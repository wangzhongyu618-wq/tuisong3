# coding=utf-8
"""
QMT 消费端规格文档防漂移测试（P1-④）：

- docs/QMT_CONSUMER_SPEC.md 必须覆盖 ORM 两张表的全部列名（自动遍历 metadata）；
- 必须覆盖全部索引/唯一约束名（uq_raw_dedup / uq_sentiment_event 等）；
- 必须覆盖 mcp_server/server.py 注册的全部 mysql_* MCP 工具名；
- MySQLReader.describe_schema() 自描述必须含 event_hash 等新增列（与 P1-⑤ 对齐）；
- 关键语义（枚举/评分范围/UTC/代码格式/去重键）在文档中有明确表述。

全 mock，不连接真实 MySQL。
"""
import re
from pathlib import Path

import pytest

from sqlalchemy import UniqueConstraint

from trendradar.storage.mysql_models import Base

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOC_PATH = PROJECT_ROOT / "docs" / "QMT_CONSUMER_SPEC.md"
SERVER_PATH = PROJECT_ROOT / "mcp_server" / "server.py"


@pytest.fixture(scope="module")
def doc_text():
    assert DOC_PATH.exists(), "QMT 消费端规格文档缺失: docs/QMT_CONSUMER_SPEC.md"
    text = DOC_PATH.read_text(encoding="utf-8")
    assert text.strip(), "QMT 消费端规格文档为空"
    return text


def _table(name):
    return Base.metadata.tables[name]


def test_doc_covers_all_table_columns(doc_text):
    """ORM 两张表的每一列都必须出现在文档中（含未来新增列，自动防漂移）。"""
    missing = []
    for table_name in ("raw_data_feed", "financial_sentiment"):
        for column_name in _table(table_name).columns.keys():
            if column_name not in doc_text:
                missing.append(f"{table_name}.{column_name}")
    assert not missing, f"文档缺少以下列的说明: {missing}"


def test_doc_covers_all_indexes_and_unique_keys(doc_text):
    """全部索引与唯一约束名必须出现在文档中。"""
    missing = []
    for table_name in ("raw_data_feed", "financial_sentiment"):
        table = _table(table_name)
        names = [idx.name for idx in table.indexes if idx.name]
        names += [
            c.name for c in table.constraints
            if isinstance(c, UniqueConstraint) and c.name
        ]
        for name in names:
            if name not in doc_text:
                missing.append(f"{table_name}:{name}")
    assert not missing, f"文档缺少以下索引/唯一键说明: {missing}"


def test_doc_covers_all_mysql_mcp_tools(doc_text):
    """server.py 注册的每个 mysql_* MCP 工具都必须在文档中有说明。"""
    server_src = SERVER_PATH.read_text(encoding="utf-8")
    tool_names = sorted(set(re.findall(r"async def (mysql_\w+)\s*\(", server_src)))
    assert tool_names, "未能从 mcp_server/server.py 提取到 mysql_* 工具"
    missing = [name for name in tool_names if name not in doc_text]
    assert not missing, f"文档缺少以下 MCP 工具说明: {missing}"


def test_doc_covers_enum_values_and_score_range(doc_text):
    for token in ("Low", "Medium", "High", "-1.0", "1.0", "UNKNOWN"):
        assert token in doc_text, f"文档缺少枚举/取值说明: {token}"


def test_doc_covers_timezone_and_code_format_rules(doc_text):
    for token in ("UTC", "600000", ".SH", ".SZ", "00700", "AAPL",
                  "sector_mapping.yaml"):
        assert token in doc_text, f"文档缺少时区/代码格式说明: {token}"


def test_doc_covers_event_dedup_semantics(doc_text):
    """P1-⑤ 去重键语义必须在文档中明确（含 NULL 不参与判重的边界）。"""
    for token in ("event_hash", "uq_sentiment_event", "uq_raw_dedup",
                  "content_hash", "compute_event_hash"):
        assert token in doc_text, f"文档缺少去重语义说明: {token}"
    assert "NULL" in doc_text, "文档缺少 event_hash=NULL 不参与判重的说明"


# ----------------------------------------------------------------
# describe_schema 自描述契约（MCP Agent 的查询起点）
# ----------------------------------------------------------------

class _FakeBackend:
    """describe_schema 依赖的最小后端桩。"""

    def health_check(self):
        return True

    def get_table_stats(self):
        return {"raw_data_feed": 3, "financial_sentiment": 5}


@pytest.fixture()
def reader_no_db(monkeypatch):
    import trendradar.storage.mysql_reader as reader_mod

    monkeypatch.setattr(reader_mod, "init_db_pool", lambda **kw: None)
    monkeypatch.setattr(reader_mod, "MySQLStorageBackend", lambda: _FakeBackend())
    return reader_mod.MySQLReader(host="test-host", username="u", password="p")


def test_describe_schema_includes_event_hash(reader_no_db):
    """P1-⑤ 新增列必须进入 MCP 自描述（否则 Agent 无法感知去重键）。"""
    schema = reader_no_db.describe_schema()
    sent_cols = schema["tables"]["financial_sentiment"]["columns"]
    assert "event_hash" in sent_cols
    assert "去重" in sent_cols["event_hash"] or "唯一" in sent_cols["event_hash"]


def test_describe_schema_raw_feed_columns_complete(reader_no_db):
    schema = reader_no_db.describe_schema()
    raw_cols = schema["tables"]["raw_data_feed"]["columns"]
    for col in ("related_tickers", "content_hash", "additional_data"):
        assert col in raw_cols


def test_describe_schema_mentions_sample_counts(reader_no_db):
    schema = reader_no_db.describe_schema()
    assert schema["sample_counts"] == {
        "raw_data_feed": 3, "financial_sentiment": 5,
    }


if __name__ == "__main__":
    import pytest as _pytest

    _pytest.main([__file__, "-v"])
