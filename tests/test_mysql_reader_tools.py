# coding=utf-8
"""MySQL 只读层单元测试（全部 mock，不依赖真实 MySQL 服务）。

覆盖：
- trendradar/storage/mysql_env.py 的 MYSQL_* 环境变量优先级
- trendradar/storage/mysql_reader.py 的日期解析/过滤/聚合逻辑
- mcp_server/tools/mysql_reader.py 工具层的参数收敛与错误转换
"""

import os
from datetime import datetime

import pytest

from trendradar.storage.mysql_env import conn_params_from_env
from trendradar.storage.mysql_reader import MySQLReader
from mcp_server.tools.mysql_reader import (
    MySQLReaderTools,
    _clamp_limit,
    _parse_iso_datetime,
)
from mcp_server.utils.errors import MCPError


# ----------------------------------------------------------------
# 测试替身
# ----------------------------------------------------------------

class FakeBackend:
    """按预设行数据响应查询的假存储后端。"""

    def __init__(self, raw_rows=None, sentiment_rows=None, healthy=True,
                 table_stats=None):
        self.raw_rows = raw_rows or []
        self.sentiment_rows = sentiment_rows or []
        self.healthy = healthy
        self.table_stats = table_stats or {}
        self.calls = []

    def health_check(self):
        return self.healthy

    def get_table_stats(self):
        return dict(self.table_stats)

    def query_raw_data(self, source_type=None, source_id=None,
                       start_date=None, end_date=None, limit=20):
        self.calls.append(("raw", source_type, source_id, limit))
        return list(self.raw_rows)

    def query_financial_sentiment(self, stock_code=None, alert_level=None,
                                  min_sentiment=None, max_sentiment=None,
                                  start_date=None, end_date=None, limit=20):
        self.calls.append(("sent", stock_code, alert_level, limit))
        return list(self.sentiment_rows)


class FakeReader:
    """预注入 MySQLReaderTools._reader 的假读取器（绕过真实连接）。"""

    def __init__(self):
        self.health_ok = True
        self.raw_rows = []
        self.sentiment_rows = []

    def health_check(self):
        return self.health_ok

    def search_raw_data(self, source_type=None, source_id=None, keyword=None,
                        start_date=None, end_date=None, limit=20):
        return [{"echo": dict(source_type=source_type, source_id=source_id,
                              keyword=keyword, start_date=start_date,
                              end_date=end_date, limit=limit)}]

    def recent_news(self, source_type=None, limit=10):
        return list(self.raw_rows)

    def search_sentiments(self, **kwargs):
        return list(self.sentiment_rows)

    def top_stocks(self, limit=5, horizon_days=7):
        return [{"stock_code": "NVDA", "avg_score": 0.9, "count": 2}]

    def get_sentiment_by_id(self, sentiment_id):
        return {"id": sentiment_id, "stock_name": "NVIDIA"} if sentiment_id else None

    def describe_schema(self):
        return {"tables": {"raw_data_feed": {}}, "sample_counts": {}}


# ----------------------------------------------------------------
# mysql_env.conn_params_from_env
# ----------------------------------------------------------------

def test_conn_params_defaults_without_env(monkeypatch):
    for key in ("MYSQL_HOST", "MYSQL_PORT", "MYSQL_USERNAME", "MYSQL_PASSWORD",
                "MYSQL_DATABASE", "MYSQL_CHARSET"):
        monkeypatch.delenv(key, raising=False)

    params = conn_params_from_env()

    assert params == {
        "host": "localhost", "port": 3306, "username": "root",
        "password": "12345678", "database": "trendradar", "charset": "utf8mb4",
    }


def test_conn_params_env_overrides_everything(monkeypatch):
    monkeypatch.setenv("MYSQL_HOST", "db.internal")
    monkeypatch.setenv("MYSQL_PORT", "3307")
    monkeypatch.setenv("MYSQL_USERNAME", "readonly")
    monkeypatch.setenv("MYSQL_PASSWORD", "s3cret")
    monkeypatch.setenv("MYSQL_DATABASE", "other_db")
    monkeypatch.setenv("MYSQL_CHARSET", "utf8")

    params = conn_params_from_env()

    assert params == {
        "host": "db.internal", "port": 3307, "username": "readonly",
        "password": "s3cret", "database": "other_db", "charset": "utf8",
    }


# ----------------------------------------------------------------
# 工具层参数校验
# ----------------------------------------------------------------

def test_parse_iso_datetime_accepts_common_formats():
    assert _parse_iso_datetime("2026-08-01", "x") == datetime(2026, 8, 1)
    assert _parse_iso_datetime("2026-08-01T09:30:00", "x") == datetime(2026, 8, 1, 9, 30)
    assert _parse_iso_datetime("2026-08-01 09:30:00", "x") == datetime(2026, 8, 1, 9, 30)
    assert _parse_iso_datetime("2026-08-01T09:30:00Z", "x") is not None
    assert _parse_iso_datetime("2026/08/01", "x") == datetime(2026, 8, 1)
    assert _parse_iso_datetime(None, "x") is None
    assert _parse_iso_datetime("", "x") is None


def test_parse_iso_datetime_rejects_garbage():
    with pytest.raises(MCPError) as excinfo:
        _parse_iso_datetime("not-a-date", "start_date")
    assert excinfo.value.code == "INVALID_PARAMETER"
    assert "start_date" in excinfo.value.message


def test_clamp_limit_bounds():
    assert _clamp_limit(0) == 1
    assert _clamp_limit(-5) == 1
    assert _clamp_limit(200) == 200
    assert _clamp_limit(999) == 200
    assert _clamp_limit("abc") == 20
    assert _clamp_limit(None) == 20
    assert _clamp_limit(7, default=10) == 7


# ----------------------------------------------------------------
# MySQLReader（storage 层，fake backend）
# ----------------------------------------------------------------

def make_reader(monkeypatch, backend):
    """构造不连真实数据库的 MySQLReader（连接池与后端均被替换）。"""
    monkeypatch.setattr(
        "trendradar.storage.mysql_reader.init_db_pool", lambda **kw: None
    )
    monkeypatch.setattr(
        "trendradar.storage.mysql_reader.MySQLStorageBackend", lambda: backend
    )
    return MySQLReader(host="test-host", username="u", password="p")


def test_reader_search_raw_data_filters_by_keyword(monkeypatch):
    backend = FakeBackend(raw_rows=[
        {"id": 1, "content": "英伟达发布新芯片"},
        {"id": 2, "content": "苹果财报超预期"},
    ])
    reader = make_reader(monkeypatch, backend)

    rows = reader.search_raw_data(source_type="rss_feed", keyword="英伟达")

    assert [r["id"] for r in rows] == [1]
    assert backend.calls == [("raw", "rss_feed", None, 20)]


def test_reader_search_sentiments_filters_by_stock_name(monkeypatch):
    backend = FakeBackend(sentiment_rows=[
        {"id": 1, "stock_name": "NVIDIA 英伟达", "sentiment_score": 0.8},
        {"id": 2, "stock_name": "Apple 苹果", "sentiment_score": 0.5},
    ])
    reader = make_reader(monkeypatch, backend)

    rows = reader.search_sentiments(stock_name="英伟达")

    assert [r["id"] for r in rows] == [1]


def test_reader_top_stocks_aggregates_and_sorts(monkeypatch):
    backend = FakeBackend(sentiment_rows=[
        {"stock_code": "AAPL", "stock_name": "苹果", "sentiment_score": 0.9},
        {"stock_code": "NVDA", "stock_name": "英伟达", "sentiment_score": 0.4},
        {"stock_code": "NVDA", "stock_name": "英伟达", "sentiment_score": 0.8},
        {"stock_code": "TSLA", "stock_name": "特斯拉", "sentiment_score": -0.2},
    ])
    reader = make_reader(monkeypatch, backend)

    top = reader.top_stocks(limit=3, horizon_days=7)

    # 按平均评分降序：AAPL(0.9) > NVDA((0.4+0.8)/2=0.6) > TSLA(-0.2)
    assert [r["stock_code"] for r in top] == ["AAPL", "NVDA", "TSLA"]
    assert top[0]["avg_score"] == 0.9
    assert top[1]["avg_score"] == 0.6
    assert top[1]["count"] == 2


def test_reader_describe_schema_reports_counts(monkeypatch):
    backend = FakeBackend(healthy=True, table_stats={"raw_data_feed": 13})
    reader = make_reader(monkeypatch, backend)

    schema = reader.describe_schema()

    assert schema["sample_counts"] == {"raw_data_feed": 13}
    assert set(schema["tables"]) == {"raw_data_feed", "financial_sentiment"}


# ----------------------------------------------------------------
# MySQLReaderTools（MCP 工具层，注入 FakeReader）
# ----------------------------------------------------------------

def _tool_with_fake_reader(fake):
    tools = MySQLReaderTools(project_root=os.getcwd())
    tools._reader = fake  # 绕过真实连接
    return tools


def test_tools_search_raw_data_parses_dates_and_forwards():
    fake = FakeReader()
    tools = _tool_with_fake_reader(fake)

    result = tools.search_raw_data(
        source_type="rss_feed", keyword="AI",
        start_date="2026-08-01", end_date="2026-08-10T23:59:59", limit=500,
    )

    assert result["success"] is True
    echo = result["items"][0]["echo"]
    assert echo["source_type"] == "rss_feed"
    assert echo["start_date"] == datetime(2026, 8, 1)
    assert echo["end_date"] == datetime(2026, 8, 10, 23, 59, 59)
    assert echo["limit"] == 200  # 收敛到上限


def test_tools_get_sentiment_by_id_missing_returns_not_found():
    tools = _tool_with_fake_reader(FakeReader())

    result = tools.get_sentiment_by_id(0)

    assert result == {"success": True, "found": False,
                      "message": "未找到 id=0 的情感分析记录"}


def test_tools_get_sentiment_by_id_found():
    tools = _tool_with_fake_reader(FakeReader())

    result = tools.get_sentiment_by_id(9)

    assert result["found"] is True
    assert result["item"]["stock_name"] == "NVIDIA"


def test_tools_describe_schema_hides_password(monkeypatch):
    # 走真实懒初始化路径：_get_reader 构建的 connection 摘要必须不含密码
    monkeypatch.setattr(
        "trendradar.storage.mysql_reader.MySQLReader",
        lambda **conn: FakeReader(),
    )
    monkeypatch.setattr(
        MySQLReaderTools, "_load_mysql_config",
        lambda self: {"host": "h", "port": 3306, "username": "u",
                      "password": "s3cret", "database": "d",
                      "charset": "utf8mb4"},
    )
    tools = MySQLReaderTools(project_root=os.getcwd())

    result = tools.describe_schema()

    assert result["success"] is True
    assert "password" not in result["connection"]
    assert result["connection"]["username"] == "u"
    assert result["connection"]["database"] == "d"


def test_tools_get_reader_wraps_connection_failure(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("boom: access denied")

    monkeypatch.setattr("trendradar.storage.mysql_reader.MySQLReader", _boom)
    tools = MySQLReaderTools(project_root=os.getcwd())
    monkeypatch.setattr(
        tools, "_load_mysql_config",
        lambda: {"host": "h", "port": 3306, "username": "u",
                 "password": "p", "database": "d", "charset": "utf8mb4"},
    )

    with pytest.raises(MCPError) as excinfo:
        tools.describe_schema()

    assert excinfo.value.code == "MYSQL_UNAVAILABLE"
    assert excinfo.value.suggestion  # 带修复建议


def test_tools_get_reader_rejects_unhealthy_backend(monkeypatch):
    fake = FakeReader()
    fake.health_ok = False

    monkeypatch.setattr(
        "trendradar.storage.mysql_reader.MySQLReader",
        lambda **conn: fake,
    )
    monkeypatch.setattr(
        MySQLReaderTools, "_load_mysql_config",
        lambda self: {"host": "h", "port": 3306, "username": "u",
                      "password": "p", "database": "d", "charset": "utf8mb4"},
    )

    tools = MySQLReaderTools(project_root=os.getcwd())
    with pytest.raises(MCPError) as excinfo:
        tools._get_reader()

    assert excinfo.value.code == "MYSQL_UNAVAILABLE"


