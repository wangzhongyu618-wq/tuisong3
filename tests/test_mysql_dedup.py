# coding=utf-8
"""
raw_data_feed 内容去重机制的单元测试（全 mock，不连接真实 MySQL）：
- content_hash 由 @validates('content') 自动计算
- save_raw_data_batch 批内去重与库内重复键（MySQL 1062）跳过
- mysql_init._ensure_dedup 迁移各步骤按状态幂等
"""
import hashlib
from unittest.mock import MagicMock, patch

from pymysql.err import IntegrityError as PymysqlIntegrityError
from sqlalchemy.exc import IntegrityError

from trendradar.storage.mysql_backend import MySQLStorageBackend
from trendradar.storage.mysql_init import MySQLDatabaseInitializer
from trendradar.storage.mysql_models import RawDataFeed


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _make_backend():
    pool = MagicMock(name="db_pool")
    backend = MySQLStorageBackend(db_pool=pool)
    session = pool.session_scope.return_value.__enter__.return_value
    return backend, session


# ---------- content_hash 自动计算 ----------

def test_content_hash_auto_computed():
    row = RawDataFeed(source_type='rss_feed', content='AI 芯片需求激增', source_id='hn')
    assert row.content_hash == _sha256('AI 芯片需求激增')


def test_content_hash_deterministic_and_distinct():
    a = RawDataFeed(content='same title')
    b = RawDataFeed(content='same title')
    c = RawDataFeed(content='other title')
    assert a.content_hash == b.content_hash
    assert a.content_hash != c.content_hash


def test_content_hash_handles_non_string():
    row = RawDataFeed(content=12345)
    assert row.content_hash == _sha256('12345')


# ---------- save_raw_data_batch 去重 ----------

def _record(title, source_id='hn'):
    return {
        'source_type': 'hotlist_news',
        'content': title,
        'url': 'https://example.com',
        'source_id': source_id,
        'source_name': 'demo',
    }


def test_batch_in_batch_duplicates_skipped():
    """同批内 (source_type, source_id, content) 完全相同的记录只入库一次。"""
    backend, session = _make_backend()
    saved = backend.save_raw_data_batch([_record('同一标题'), _record('同一标题')])
    assert saved == 1
    assert session.add.call_count == 1


def test_batch_db_duplicate_1062_skipped():
    """库内唯一键冲突（1062）时跳过该条并计入重复，不视为失败。"""
    backend, session = _make_backend()
    dup_error = IntegrityError(
        "INSERT INTO raw_data_feed ...",
        {},
        PymysqlIntegrityError(1062, "Duplicate entry 'x' for key 'uq_raw_dedup'"),
    )
    session.flush.side_effect = [None, dup_error]
    saved = backend.save_raw_data_batch([_record('标题A'), _record('标题B')])
    assert saved == 1
    assert session.add.call_count == 2


def test_batch_same_title_different_source_kept():
    """同一标题来自不同来源不算重复（唯一键含 source_id）。"""
    backend, session = _make_backend()
    saved = backend.save_raw_data_batch([_record('标题X', 'hn'), _record('标题X', 'baidu')])
    assert saved == 2


def test_duplicate_key_error_detector():
    err_1062 = IntegrityError("stmt", {}, PymysqlIntegrityError(1062, "Duplicate entry 'x'"))
    err_other = IntegrityError("stmt", {}, PymysqlIntegrityError(1452, "Cannot add or update a child row"))
    assert MySQLStorageBackend._is_duplicate_key_error(err_1062) is True
    assert MySQLStorageBackend._is_duplicate_key_error(err_other) is False


# ---------- _ensure_dedup 迁移 ----------

def _patch_inspector(indexes):
    inspector = MagicMock(name="inspector")
    inspector.get_table_names.return_value = ["raw_data_feed", "financial_sentiment"]
    inspector.get_columns.return_value = [
        {"name": n} for n in
        ["id", "source_type", "content", "url", "source_id", "source_name",
         "related_tickers", "additional_data", "content_hash", "created_at", "updated_at"]
    ]
    inspector.get_indexes.return_value = indexes
    return inspector


def _executed_sql(engine):
    conn = engine.begin.return_value.__enter__.return_value
    return [str(call.args[0]) for call in conn.execute.call_args_list]


def _make_init():
    init = MySQLDatabaseInitializer()
    init.engine = MagicMock(name="engine")
    init.engine.begin.return_value.__enter__.return_value.execute.return_value.rowcount = 0
    return init


def test_ensure_dedup_creates_unique_key_when_missing():
    """唯一键缺失：按 回填 → 清理重复 → NOT NULL → 建唯一键 顺序执行。"""
    init = _make_init()
    with patch("trendradar.storage.mysql_init.inspect", return_value=_patch_inspector([])):
        assert init._ensure_dedup() is True
    sqls = "\n".join(_executed_sql(init.engine))
    assert "UPDATE" in sqls and "SHA2" in sqls
    assert "DELETE r1 FROM" in sqls
    assert "MODIFY COLUMN" in sqls
    assert "ADD UNIQUE KEY `uq_raw_dedup`" in sqls


def test_ensure_dedup_idempotent_when_unique_exists():
    """唯一键已存在：不再执行 DELETE/NOT NULL/ADD UNIQUE，仅跑幂等回填。"""
    init = _make_init()
    indexes = [{
        "name": "uq_raw_dedup",
        "column_names": ["source_type", "source_id", "content_hash"],
        "unique": True,
    }]
    with patch("trendradar.storage.mysql_init.inspect", return_value=_patch_inspector(indexes)):
        assert init._ensure_dedup() is True
    sqls = "\n".join(_executed_sql(init.engine))
    assert "DELETE r1 FROM" not in sqls
    assert "ADD UNIQUE KEY" not in sqls
    assert "MODIFY COLUMN" not in sqls


def test_ensure_dedup_skips_when_column_missing():
    """content_hash 列尚未迁移时跳过（由修正表结构步骤负责加列）。"""
    init = _make_init()
    inspector = _patch_inspector([])
    inspector.get_columns.return_value = [{"name": "id"}, {"name": "content"}]
    with patch("trendradar.storage.mysql_init.inspect", return_value=inspector):
        assert init._ensure_dedup() is True
    assert _executed_sql(init.engine) == []


def test_ensure_dedup_skips_when_table_missing():
    """表尚未创建时跳过且不报错（由 create_tables 负责）。"""
    init = _make_init()
    inspector = _patch_inspector([])
    inspector.get_table_names.return_value = []
    with patch("trendradar.storage.mysql_init.inspect", return_value=inspector):
        assert init._ensure_dedup() is True
    assert _executed_sql(init.engine) == []
