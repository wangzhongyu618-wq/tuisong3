# coding=utf-8
"""
mysql_init._ensure_indexes 幂等索引补建逻辑的单元测试（全 mock，不连接真实 MySQL）。
"""
from unittest.mock import MagicMock, patch

from trendradar.storage.mysql_init import MySQLDatabaseInitializer
from trendradar.storage.mysql_models import Base

TABLES = ("raw_data_feed", "financial_sentiment")


def _make_initializer():
    """构造不连库的初始化器，engine 用 MagicMock 替身。"""
    init = MySQLDatabaseInitializer()
    init.engine = MagicMock(name="engine")
    return init


def _orm_index_map(table_name):
    """从 ORM 元数据读取 (索引名 -> 列序列) 映射，作为期望事实源。"""
    return {
        i.name: [c.name for c in i.columns]
        for i in Base.metadata.tables[table_name].indexes
    }


def _existing_for(table_name):
    """把 ORM 索引转换成 SQLAlchemy inspector.get_indexes 的返回结构。"""
    return [
        {"name": name, "column_names": cols, "unique": False}
        for name, cols in _orm_index_map(table_name).items()
    ]


def _patch_inspector(existing_raw, existing_sent, tables=TABLES):
    """构造 mock inspector，按表名返回已有索引。"""
    inspector = MagicMock(name="inspector")
    inspector.get_table_names.return_value = list(tables)
    by_table = {"raw_data_feed": existing_raw, "financial_sentiment": existing_sent}

    def get_indexes(table):
        return by_table[table]

    inspector.get_indexes.side_effect = get_indexes
    return inspector


def _executed_sql(init):
    """收集 engine.begin() 事务内执行过的 SQL 文本。"""
    conn = init.engine.begin.return_value.__enter__.return_value
    return [str(call.args[0]) for call in conn.execute.call_args_list]


def test_missing_indexes_are_created():
    """库中无任何二级索引时，按 ORM 定义逐个 CREATE INDEX。"""
    init = _make_initializer()
    inspector = _patch_inspector([], [])
    with patch("trendradar.storage.mysql_init.inspect", return_value=inspector):
        assert init._ensure_indexes() is True

    expected = set()
    for t in TABLES:
        for name, cols in _orm_index_map(t).items():
            col_sql = ", ".join(f"`{c}`" for c in cols)
            expected.add(f"CREATE INDEX `{name}` ON `{t}` ({col_sql})")
    assert set(_executed_sql(init)) == expected


def test_all_indexes_present_is_noop():
    """索引均已存在时不产生任何 DDL（幂等）。"""
    init = _make_initializer()
    inspector = _patch_inspector(
        _existing_for("raw_data_feed"), _existing_for("financial_sentiment")
    )
    with patch("trendradar.storage.mysql_init.inspect", return_value=inspector):
        assert init._ensure_indexes() is True
    assert _executed_sql(init) == []


def test_partial_missing_creates_only_gaps():
    """部分缺失时只补缺失的索引（raw 全有、sentiment 全缺）。"""
    init = _make_initializer()
    inspector = _patch_inspector(_existing_for("raw_data_feed"), [])
    with patch("trendradar.storage.mysql_init.inspect", return_value=inspector):
        assert init._ensure_indexes() is True

    sqls = _executed_sql(init)
    assert sqls and all("ON `financial_sentiment`" in s for s in sqls)
    expected = {
        f"CREATE INDEX `{name}` ON `financial_sentiment` "
        f"({', '.join(f'`{c}`' for c in cols)})"
        for name, cols in _orm_index_map("financial_sentiment").items()
    }
    assert set(sqls) == expected


def test_same_name_different_columns_is_skipped():
    """同名但列不一致的索引只告警、不重建，其余缺失索引照常补建。"""
    init = _make_initializer()
    existing_sent = [{"name": "idx_created_at", "column_names": ["stock_code"], "unique": False}]
    inspector = _patch_inspector([], existing_sent)
    with patch("trendradar.storage.mysql_init.inspect", return_value=inspector):
        assert init._ensure_indexes() is True

    sqls = _executed_sql(init)
    assert not any("idx_created_at" in s and "financial_sentiment" in s for s in sqls)
    assert any("idx_stock_code_created" in s for s in sqls)


def test_missing_table_is_skipped():
    """表尚未创建时跳过索引补建且不报错（由 create_tables 负责）。"""
    init = _make_initializer()
    inspector = _patch_inspector([], [], tables=[])
    with patch("trendradar.storage.mysql_init.inspect", return_value=inspector):
        assert init._ensure_indexes() is True
    assert _executed_sql(init) == []


def test_no_engine_fails_fast():
    """引擎未初始化时返回 False，不做任何查询。"""
    init = MySQLDatabaseInitializer()
    init.engine = None
    assert init._ensure_indexes() is False
