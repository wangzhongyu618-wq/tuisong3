# coding=utf-8
"""
QMT 消费端对接模板（examples/qmt_consumer_demo.py）单元测试（全 mock，不连库）：

- §3.5 代码映射：to_qmt_code 与规格映射表逐行对齐（防消费端抄错后缀规则）；
- §5.1 三条读取 SQL 的关键语义锁死（水位法 / High 订阅 / 实体时间线）；
- §6-1 水位持久化：往返 / 损坏容错 / 增量推进；
- §6-4 解析防御：analysis_metadata 坏 JSON 按 NULL 处理。
"""
import importlib.util
import json
from datetime import datetime
from pathlib import Path

import pytest

_EXAMPLE = (
    Path(__file__).resolve().parent.parent / "examples" / "qmt_consumer_demo.py"
)
_spec = importlib.util.spec_from_file_location("qmt_consumer_demo", _EXAMPLE)
demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(demo)


# ---------------- 测试替身：模拟 pymysql 连接 ----------------

class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        # 归一空白后记录（SQL 拼接自多行字符串，语义按词断言）
        self.executed.append((" ".join(sql.split()), params))

    def fetchall(self):
        return list(self.rows)


class FakeConn:
    def __init__(self, rows):
        self.rows = rows
        self.cursors = []

    def cursor(self):
        c = FakeCursor(self.rows)
        self.cursors.append(c)
        return c


# ---------------- §3.5 QMT 后缀映射 ----------------

class TestToQmtCode:
    @pytest.mark.parametrize("code,expected", [
        ("600000", "600000.SH"),   # 沪 A
        ("688981", "688981.SH"),   # 科创
        ("512480", "512480.SH"),   # 沪 ETF
        ("000001", "000001.SZ"),   # 深 A
        ("300308", "300308.SZ"),   # 创业
        ("159995", "159995.SZ"),   # 深 ETF
        ("830799", "830799.BJ"),   # 北交所
        ("430047", "430047.BJ"),   # 北交所
    ])
    def test_prefix_rules_match_spec_3_5(self, code, expected):
        assert demo.to_qmt_code(code) == expected

    def test_us_ticker_kept_as_is(self):
        assert demo.to_qmt_code("AAPL") == "AAPL"

    def test_unknown_and_blank_rejected(self):
        assert demo.to_qmt_code("UNKNOWN") is None
        assert demo.to_qmt_code("") is None
        assert demo.to_qmt_code(None) is None

    def test_suffix_idempotent_and_normalized(self):
        assert demo.to_qmt_code("600000.SH") == "600000.SH"
        assert demo.to_qmt_code(" 300308 ") == "300308.SZ"
        assert demo.to_qmt_code("aapl") == "AAPL"

    def test_illegal_shapes_rejected(self):
        assert demo.to_qmt_code("12345") is None      # 5 位
        assert demo.to_qmt_code("1234567") is None    # 7 位
        assert demo.to_qmt_code("12A456") is None     # 混合
        assert demo.to_qmt_code("900001") is None     # 前缀无映射(B股)不给后缀

    def test_name_hint_for_unknown(self):
        row = {"stock_code": "UNKNOWN", "stock_name": "存储芯片"}
        assert "按名称映射" in demo.qmt_code_or_name_hint(row)
        assert "存储芯片" in demo.qmt_code_or_name_hint(row)
        assert demo.qmt_code_or_name_hint(
            {"stock_code": "600000", "stock_name": "浦发银行"}
        ) == "600000.SH"


# ---------------- §6-1 水位持久化 ----------------

class TestWatermark:
    def test_missing_file_defaults_zero(self, tmp_path):
        assert demo.load_watermark(str(tmp_path / "none.json")) == 0

    def test_roundtrip(self, tmp_path):
        path = str(tmp_path / "wm.json")
        demo.save_watermark(path, 1234)
        assert demo.load_watermark(path) == 1234

    def test_corrupt_file_defaults_zero(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        assert demo.load_watermark(str(path)) == 0

    def test_negative_treated_as_zero(self, tmp_path):
        path = str(tmp_path / "neg.json")
        Path(path).write_text(json.dumps({"last_watermark_id": -5}),
                              encoding="utf-8")
        assert demo.load_watermark(path) == 0


# ---------------- §5.1 水位增量拉取 ----------------

def _row(rid, code="300308", **kw):
    base = {
        "id": rid, "stock_name": "芒果超媒", "stock_code": code,
        "sentiment_score": 0.7, "alert_level": "Medium",
        "summary_event": "事件", "event_hash": "h" * 64,
        "raw_data_id": 1, "analysis_metadata": None,
        "created_at": datetime(2026, 9, 1, 2, 0, 0),
    }
    base.update(kw)
    return base


class TestPullNewSentiments:
    def test_sql_semantics_locked(self, tmp_path):
        conn = FakeConn([])
        demo.pull_new_sentiments(conn, str(tmp_path / "wm.json"))
        sql, params = conn.cursors[0].executed[0]
        assert "FROM financial_sentiment" in sql
        assert "WHERE id > %s" in sql          # 水位法（§5.1）
        assert "ORDER BY id ASC" in sql        # 主键顺序，幂等
        assert "LIMIT %s" in sql
        assert params == (0, 500)              # 默认水位 0、默认 limit

    def test_advances_watermark_to_batch_max(self, tmp_path):
        wm = str(tmp_path / "wm.json")
        conn = FakeConn([_row(41), _row(42), _row(40)])
        rows, watermark = demo.pull_new_sentiments(conn, wm)
        assert [r["id"] for r in rows] == [41, 42, 40]
        assert watermark == 42
        assert demo.load_watermark(wm) == 42

    def test_uses_saved_watermark(self, tmp_path):
        wm = str(tmp_path / "wm.json")
        demo.save_watermark(wm, 41)
        conn = FakeConn([_row(42)])
        _, watermark = demo.pull_new_sentiments(conn, wm)
        assert conn.cursors[0].executed[0][1] == (41, 500)
        assert watermark == 42

    def test_empty_batch_keeps_watermark(self, tmp_path):
        wm = str(tmp_path / "wm.json")
        demo.save_watermark(wm, 42)
        conn = FakeConn([])
        rows, watermark = demo.pull_new_sentiments(conn, wm)
        assert rows == []
        assert watermark == 42
        assert demo.load_watermark(wm) == 42


# ---------------- §5.1 High 订阅 / 实体时间线 ----------------

class TestAlertAndTimelineSQL:
    def test_high_alerts_sql_semantics_locked(self):
        conn = FakeConn([_row(9, alert_level="High")])
        rows = demo.high_alerts(conn, days=2, limit=10)
        assert len(rows) == 1
        sql, params = conn.cursors[0].executed[0]
        assert "fs.alert_level = %s" in sql
        assert "UTC_TIMESTAMP() - INTERVAL %s DAY" in sql   # §3.6 UTC 比较
        assert "LEFT JOIN raw_data_feed rd ON rd.id = fs.raw_data_id" in sql
        assert "JSON_EXTRACT(fs.analysis_metadata, '$.context')" in sql
        assert params == ("High", 2, 10)

    def test_entity_timeline_sql_semantics_locked(self):
        conn = FakeConn([_row(7)])
        rows = demo.entity_timeline(conn, "300308", limit=5)
        assert len(rows) == 1
        sql, params = conn.cursors[0].executed[0]
        assert "WHERE stock_code = %s" in sql
        assert "ORDER BY created_at DESC" in sql
        assert params == ("300308", 5)


# ---------------- §6-4 解析防御 / §3.6 时区 ----------------

class TestDisplayHelpers:
    def test_parse_metadata_defense(self):
        assert demo.parse_metadata(None) is None
        assert demo.parse_metadata("") is None
        assert demo.parse_metadata("{broken") is None
        assert demo.parse_metadata('["not dict"]') is None
        assert demo.parse_metadata({"context": "x"}) == {"context": "x"}
        assert demo.parse_metadata('{"context": "x"}') == {"context": "x"}

    def test_utc_to_beijing(self):
        utc = datetime(2026, 9, 1, 2, 30, 0)
        assert demo.utc_to_beijing_str(utc) == "2026-09-01 10:30:00"
        assert demo.utc_to_beijing_str(None) == "-"
        assert demo.utc_to_beijing_str("bad") == "bad"


if __name__ == "__main__":
    import pytest as _pytest

    _pytest.main([__file__, "-v"])
