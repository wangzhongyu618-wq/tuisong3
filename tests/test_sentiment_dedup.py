# coding=utf-8
"""
financial_sentiment 事件级去重（P1-⑤）单元测试（全 mock，不连接真实 MySQL）：

- FinancialSentiment.compute_event_hash / extract_event_text（单一事实源）
- save_financial_sentiment(_batch) 批内同事件去重与库内重复键（MySQL 1062）跳过
- mysql_init._ensure_event_dedup 迁移各步骤按状态幂等（回填/清理/建唯一键）
- reconcile_schema 对 event_hash 列走增量 ALTER（不触发 DROP 重建）
"""
import hashlib
import json
from unittest.mock import MagicMock, patch

from pymysql.err import IntegrityError as PymysqlIntegrityError
from sqlalchemy.exc import IntegrityError

from trendradar.storage.mysql_backend import MySQLStorageBackend
from trendradar.storage.mysql_init import MySQLDatabaseInitializer
from trendradar.storage.mysql_models import FinancialSentiment


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _make_backend():
    pool = MagicMock(name="db_pool")
    backend = MySQLStorageBackend(db_pool=pool)
    session = pool.session_scope.return_value.__enter__.return_value
    return backend, session


def _make_init():
    init = MySQLDatabaseInitializer()
    init.engine = MagicMock(name="engine")
    init.engine.begin.return_value.__enter__.return_value.execute.return_value.rowcount = 0
    return init


def _sentiment_record(
    stock_name="Apple",
    stock_code="AAPL",
    context=" iPhone 销量 大涨 ",
    summary="iPhone销量大涨",
    metadata=None,
    **extra,
):
    record = {
        "stock_name": stock_name,
        "stock_code": stock_code,
        "sentiment_score": 0.8,
        "alert_level": "High",
        "summary_event": summary,
        "analysis_metadata": metadata if metadata is not None else {"context": context},
    }
    record.update(extra)
    return record


# ---------- compute_event_hash（单一事实源） ----------

class TestComputeEventHash:

    def test_deterministic_and_hex64(self):
        h1 = FinancialSentiment.compute_event_hash("Apple", "AAPL", "iPhone 销量大涨")
        h2 = FinancialSentiment.compute_event_hash("Apple", "AAPL", "iPhone 销量大涨")
        assert h1 == h2
        assert len(h1) == 64
        assert h1 == _sha256("AAPL\x1fiPhone 销量大涨")

    def test_distinct_entity_or_text(self):
        base = FinancialSentiment.compute_event_hash("Apple", "AAPL", "某事件")
        assert base != FinancialSentiment.compute_event_hash("NVIDIA", "NVDA", "某事件")
        assert base != FinancialSentiment.compute_event_hash("Apple", "AAPL", "另一事件")

    def test_normalizes_code_case_and_whitespace(self):
        a = FinancialSentiment.compute_event_hash("Apple", " aapl ", "事件A")
        b = FinancialSentiment.compute_event_hash("Apple", "AAPL", "事件A")
        assert a == b
        c = FinancialSentiment.compute_event_hash("Apple", "AAPL", "多  空格\n与\t制表")
        d = FinancialSentiment.compute_event_hash("Apple", "AAPL", "多 空格 与 制表")
        assert c == d

    def test_falls_back_to_name_when_code_empty(self):
        a = FinancialSentiment.compute_event_hash("存储芯片", "", "扩产消息")
        b = FinancialSentiment.compute_event_hash("存储芯片", None, "扩产消息")
        assert a == b and a
        # code 优先于 name
        assert a != FinancialSentiment.compute_event_hash("存储芯片", "512480", "扩产消息")

    def test_none_when_key_part_missing(self):
        assert FinancialSentiment.compute_event_hash("", "", "某事件") is None
        assert FinancialSentiment.compute_event_hash(None, None, "某事件") is None
        assert FinancialSentiment.compute_event_hash("Apple", "AAPL", "") is None
        assert FinancialSentiment.compute_event_hash("Apple", "AAPL", "   ") is None
        assert FinancialSentiment.compute_event_hash("Apple", "AAPL", None) is None

    def test_separator_prevents_concatenation_ambiguity(self):
        a = FinancialSentiment.compute_event_hash(None, "AB", "C")
        b = FinancialSentiment.compute_event_hash(None, "A", "BC")
        assert a != b


# ---------- extract_event_text（事件锚点提取） ----------

class TestExtractEventText:

    def test_prefers_source_text_then_context(self):
        meta = {"source_text": "来源原文", "context": "原文关键句"}
        assert FinancialSentiment.extract_event_text(meta, "摘要") == "来源原文"
        assert FinancialSentiment.extract_event_text({"context": "原文关键句"}, "摘要") == "原文关键句"

    def test_falls_back_to_summary(self):
        assert FinancialSentiment.extract_event_text({}, "事件摘要") == "事件摘要"
        assert FinancialSentiment.extract_event_text({"context": "   "}, "事件摘要") == "事件摘要"

    def test_accepts_json_string_metadata(self):
        meta = json.dumps({"context": "原文关键句"}, ensure_ascii=False)
        assert FinancialSentiment.extract_event_text(meta, "摘要") == "原文关键句"

    def test_dirty_metadata_is_tolerated(self):
        assert FinancialSentiment.extract_event_text("不是JSON", "摘要") == "摘要"
        assert FinancialSentiment.extract_event_text(12345, "摘要") == "摘要"
        assert FinancialSentiment.extract_event_text(None, None) == ""
        assert FinancialSentiment.extract_event_text({"context": 42}, "摘要") == "摘要"


# ---------- save_financial_sentiment_batch 批量去重 ----------

def test_batch_in_batch_event_duplicates_skipped():
    """同批内同 (实体, 事件文本) 只入库一次。"""
    backend, session = _make_backend()
    saved = backend.save_financial_sentiment_batch(
        [_sentiment_record(), _sentiment_record()]
    )
    assert saved == 1
    assert session.add.call_count == 1


def test_batch_db_event_duplicate_1062_skipped():
    """库内唯一键冲突（1062）时跳过该条并计入重复，不视为失败。"""
    backend, session = _make_backend()
    dup_error = IntegrityError(
        "INSERT INTO financial_sentiment ...",
        {},
        PymysqlIntegrityError(1062, "Duplicate entry 'x' for key 'uq_sentiment_event'"),
    )
    session.flush.side_effect = [None, dup_error]
    saved = backend.save_financial_sentiment_batch(
        [_sentiment_record(context="事件甲"), _sentiment_record(context="事件乙")]
    )
    assert saved == 1
    assert session.add.call_count == 2


def test_batch_same_context_different_stock_kept():
    """同一事件文本发生在不同实体上不算重复（event_hash 含实体键）。"""
    backend, session = _make_backend()
    saved = backend.save_financial_sentiment_batch(
        [_sentiment_record(), _sentiment_record(stock_name="NVIDIA", stock_code="NVDA")]
    )
    assert saved == 2
    assert session.add.call_count == 2


def test_batch_empty_event_key_not_deduped():
    """无有效事件键（event_hash=NULL）的记录不参与判重，全部入库。"""
    backend, session = _make_backend()
    saved = backend.save_financial_sentiment_batch(
        [_sentiment_record(metadata={}, summary=""), _sentiment_record(metadata={}, summary="")]
    )
    assert saved == 2
    assert session.add.call_count == 2
    for call in session.add.call_args_list:
        assert call.args[0].event_hash is None


def test_batch_row_event_hash_set():
    """写入行携带与 compute_event_hash 一致的事件哈希。"""
    backend, session = _make_backend()
    backend.save_financial_sentiment_batch([_sentiment_record()])
    row = session.add.call_args_list[0].args[0]
    expected = FinancialSentiment.compute_event_hash("Apple", "AAPL", "iPhone 销量 大涨")
    assert row.event_hash == expected


def test_batch_code_empty_falls_back_to_name_for_hash():
    """板块主题实体（code 为空）用原始 stock_name 参与事件键；存库 code 仍兜底 UNKNOWN。"""
    backend, session = _make_backend()
    backend.save_financial_sentiment_batch(
        [_sentiment_record(stock_name="存储芯片", stock_code="")]
    )
    row = session.add.call_args_list[0].args[0]
    assert row.stock_code == "UNKNOWN"
    expected = FinancialSentiment.compute_event_hash("存储芯片", "", "iPhone 销量 大涨")
    assert row.event_hash == expected


def test_batch_unidentifiable_entity_not_deduped():
    """实体完全缺失（name/code 均空）时事件键为 NULL，不参与判重（宁缺勿杀）。"""
    backend, session = _make_backend()
    saved = backend.save_financial_sentiment_batch(
        [
            _sentiment_record(stock_name="", stock_code=""),
            _sentiment_record(stock_name="", stock_code=""),
        ]
    )
    assert saved == 2
    for call in session.add.call_args_list:
        assert call.args[0].event_hash is None


# ---------- save_financial_sentiment 单条 ----------

def test_single_save_sets_event_hash():
    backend, session = _make_backend()
    backend.save_financial_sentiment(
        stock_name="Apple",
        stock_code="AAPL",
        sentiment_score=0.8,
        alert_level="High",
        summary_event="iPhone销量大涨",
        analysis_metadata={"context": " iPhone 销量 大涨 "},
    )
    record = session.add.call_args_list[0].args[0]
    expected = FinancialSentiment.compute_event_hash("Apple", "AAPL", "iPhone 销量 大涨")
    assert record.event_hash == expected


def test_single_save_1062_returns_none():
    """单条写入遇库内重复键时返回 None（跳过而非失败）。"""
    backend, session = _make_backend()
    dup_error = IntegrityError(
        "INSERT INTO financial_sentiment ...",
        {},
        PymysqlIntegrityError(1062, "Duplicate entry 'x' for key 'uq_sentiment_event'"),
    )
    session.flush.side_effect = dup_error
    result = backend.save_financial_sentiment(
        stock_name="Apple",
        stock_code="AAPL",
        sentiment_score=0.8,
        summary_event="iPhone销量大涨",
    )
    assert result is None
    assert session.add.call_count == 1


# ---------- _ensure_event_dedup 迁移 ----------

def _patch_event_inspector(indexes, columns=None, tables=("financial_sentiment",)):
    inspector = MagicMock(name="inspector")
    inspector.get_table_names.return_value = list(tables)
    if columns is None:
        columns = [
            {"name": n} for n in
            ["id", "stock_name", "stock_code", "sentiment_score", "alert_level",
             "summary_event", "event_hash", "raw_data_id", "analysis_metadata",
             "created_at", "updated_at"]
        ]
    inspector.get_columns.return_value = columns
    inspector.get_indexes.return_value = indexes
    return inspector


def _executed_calls(init):
    conn = init.engine.begin.return_value.__enter__.return_value
    return conn.execute.call_args_list


def test_ensure_event_dedup_creates_unique_when_missing():
    """唯一键缺失：按 回填 → 清理重复 → 建唯一键 顺序执行，回填值与模型一致。"""
    init = _make_init()
    conn = init.engine.begin.return_value.__enter__.return_value
    rows = [
        # (id, stock_name, stock_code, analysis_metadata, summary_event)
        (1, "Apple", "AAPL", json.dumps({"context": "iPhone  销量\n大涨"}), None),
        (2, "Apple", "aapl", None, "iPhone 销量 大涨"),  # 归一后与第 1 行同 hash
        (3, "某主题", "", json.dumps({"context": "  "}), ""),  # 无有效事件键 → NULL
    ]
    select_result = MagicMock(name="select_result")
    select_result.fetchall.return_value = rows
    conn.execute.side_effect = [
        select_result,
        MagicMock(rowcount=1),
        MagicMock(rowcount=1),
        MagicMock(rowcount=2),
        MagicMock(rowcount=0),
    ]
    with patch(
        "trendradar.storage.mysql_init.inspect",
        return_value=_patch_event_inspector([]),
    ):
        assert init._ensure_event_dedup() is True

    calls = _executed_calls(init)
    stmts = [str(call.args[0]) for call in calls]
    # 1) 先 SELECT 待回填行
    assert "SELECT" in stmts[0] and "event_hash" in stmts[0]
    # 2) 第 1/2 行回填为同一事件哈希；第 3 行保持 NULL（无 UPDATE）
    expected = FinancialSentiment.compute_event_hash("Apple", "AAPL", "iPhone 销量 大涨")
    assert calls[1].args[1] == {"h": expected, "id": 1}
    assert calls[2].args[1] == {"h": expected, "id": 2}
    assert len(calls) == 5
    joined = "\n".join(stmts)
    # 3) 清理重复旧行（保留最早 id）
    assert "DELETE r1 FROM" in joined
    # 4) 建唯一键
    assert "ADD UNIQUE KEY `uq_sentiment_event`" in joined
    # 顺序：清理在建键之前
    delete_pos = next(i for i, s in enumerate(stmts) if "DELETE r1 FROM" in s)
    alter_pos = next(i for i, s in enumerate(stmts) if "ADD UNIQUE KEY" in s)
    assert delete_pos < alter_pos


def test_ensure_event_dedup_idempotent_when_unique_exists():
    """唯一键已存在且无待回填行：零写操作（幂等）。"""
    init = _make_init()
    conn = init.engine.begin.return_value.__enter__.return_value
    select_result = MagicMock(name="select_result")
    select_result.fetchall.return_value = []
    conn.execute.return_value = select_result
    indexes = [{
        "name": "uq_sentiment_event",
        "column_names": ["event_hash"],
        "unique": True,
    }]
    with patch(
        "trendradar.storage.mysql_init.inspect",
        return_value=_patch_event_inspector(indexes),
    ):
        assert init._ensure_event_dedup() is True
    stmts = [str(call.args[0]) for call in _executed_calls(init)]
    assert len(stmts) == 1 and "SELECT" in stmts[0]
    assert "DELETE r1 FROM" not in "\n".join(stmts)
    assert "ADD UNIQUE KEY" not in "\n".join(stmts)


def test_ensure_event_dedup_backfill_only_when_unique_exists():
    """唯一键已存在：仅跑幂等回填，不再清理/建键。"""
    init = _make_init()
    conn = init.engine.begin.return_value.__enter__.return_value
    select_result = MagicMock(name="select_result")
    select_result.fetchall.return_value = [
        (7, "Apple", "AAPL", json.dumps({"context": " iPhone 销量 大涨 "}), None),
    ]
    conn.execute.side_effect = [select_result, MagicMock(rowcount=1)]
    indexes = [{
        "name": "uq_sentiment_event",
        "column_names": ["event_hash"],
        "unique": True,
    }]
    with patch(
        "trendradar.storage.mysql_init.inspect",
        return_value=_patch_event_inspector(indexes),
    ):
        assert init._ensure_event_dedup() is True
    calls = _executed_calls(init)
    assert len(calls) == 2
    expected = FinancialSentiment.compute_event_hash("Apple", "AAPL", "iPhone 销量 大涨")
    assert calls[1].args[1] == {"h": expected, "id": 7}


def test_ensure_event_dedup_skips_when_table_missing():
    """表尚未创建时跳过且不报错（由 create_tables 负责）。"""
    init = _make_init()
    with patch(
        "trendradar.storage.mysql_init.inspect",
        return_value=_patch_event_inspector([], tables=()),
    ):
        assert init._ensure_event_dedup() is True
    assert _executed_calls(init) == []


def test_ensure_event_dedup_skips_when_column_missing():
    """event_hash 列尚未迁移时跳过（由修正表结构步骤负责加列）。"""
    init = _make_init()
    columns = [
        {"name": n} for n in
        ["id", "stock_name", "stock_code", "sentiment_score", "alert_level",
         "summary_event", "raw_data_id", "analysis_metadata", "created_at", "updated_at"]
    ]
    with patch(
        "trendradar.storage.mysql_init.inspect",
        return_value=_patch_event_inspector([], columns=columns),
    ):
        assert init._ensure_event_dedup() is True
    assert _executed_calls(init) == []


def test_ensure_event_dedup_fails_without_engine():
    init = MySQLDatabaseInitializer()
    init.engine = None
    assert init._ensure_event_dedup() is False


# ---------- reconcile_schema 增量迁移 ----------

def _columns_for(table, include_event_hash):
    base_sent = ["id", "stock_name", "stock_code", "sentiment_score", "alert_level",
                 "summary_event", "raw_data_id", "analysis_metadata", "created_at",
                 "updated_at"]
    base_raw = ["id", "source_type", "content", "url", "source_id", "source_name",
                "related_tickers", "additional_data", "content_hash", "created_at",
                "updated_at"]
    if table == "financial_sentiment":
        names = base_sent + (["event_hash"] if include_event_hash else [])
    else:
        names = base_raw
    return [{"name": n} for n in names]


def test_reconcile_adds_event_hash_column_without_rebuild():
    """旧表缺 event_hash 列时走增量 ALTER（不触发 DROP 重建兜底）。"""
    init = _make_init()
    inspector1 = MagicMock(name="inspector_before")
    inspector1.get_table_names.return_value = ["raw_data_feed", "financial_sentiment"]
    inspector1.get_columns.side_effect = lambda t: _columns_for(t, include_event_hash=False)
    inspector2 = MagicMock(name="inspector_after")
    inspector2.get_table_names.return_value = ["raw_data_feed", "financial_sentiment"]
    inspector2.get_columns.side_effect = lambda t: _columns_for(t, include_event_hash=True)

    with patch(
        "trendradar.storage.mysql_init.inspect",
        side_effect=[inspector1, inspector2],
    ):
        assert init.reconcile_schema() is True

    conn = init.engine.begin.return_value.__enter__.return_value
    stmts = [str(call.args[0]) for call in conn.execute.call_args_list]
    assert any("ALTER TABLE `financial_sentiment`" in s and "ADD COLUMN `event_hash`" in s
               for s in stmts)
    assert not any("DROP TABLE" in s for s in stmts)


def test_column_migration_registry_covers_event_hash():
    """COLUMN_MIGRATIONS 必须覆盖 financial_sentiment.event_hash（防回退到重建）。"""
    migrations = MySQLDatabaseInitializer.COLUMN_MIGRATIONS
    assert "event_hash" in migrations.get("financial_sentiment", {})


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
