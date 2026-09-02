#!/usr/bin/env python3
# coding=utf-8
"""
MySQL 数据库初始化和迁移脚本

功能：
- 创建/检查数据库
- 创建/更新数据表
- 执行数据库迁移
- 验证表结构

使用方式：
    python -m trendradar.storage.mysql_init --host localhost --port 3306 --user root --password 12345678 --database trendradar
    或
    python -m trendradar.storage.mysql_init init    # 创建表
    python -m trendradar.storage.mysql_init verify  # 验证表结构
"""

import sys
import json
import argparse
import logging
from pathlib import Path
from sqlalchemy import create_engine, text, inspect, MetaData
from sqlalchemy.exc import OperationalError, ProgrammingError

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# ORM 模型：导入 Base 即注册全部模型表，供 create_tables 使用
from trendradar.storage.mysql_models import Base, FinancialSentiment  # noqa: E402


# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class MySQLDatabaseInitializer:
    """MySQL 数据库初始化器"""

    # 需要按 ORM 定义补建二级索引的表（索引事实源 = mysql_models.py 的 __table_args__）
    INDEXED_TABLES = ('raw_data_feed', 'financial_sentiment')

    def __init__(
        self,
        host: str = "localhost",
        port: int = 3306,
        username: str = "root",
        password: str = "12345678",
        database: str = "trendradar",
        charset: str = "utf8mb4",
    ):
        """初始化数据库初始化器"""
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.database = database
        self.charset = charset
        self.engine = None
        self.admin_engine = None

    def _create_admin_engine(self):
        """创建管理员引擎（连接到 MySQL 服务器）"""
        connection_string = f"mysql+pymysql://{self.username}:{self.password}@{self.host}:{self.port}"
        try:
            self.admin_engine = create_engine(
                connection_string,
                connect_args={'connect_timeout': 10},
            )
            logger.info(f"[初始化] 管理员引擎已创建: {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"[初始化] 无法创建管理员引擎: {e}")
            return False

    def _create_database(self) -> bool:
        """创建数据库（如果不存在）"""
        if not self.admin_engine:
            return False

        try:
            with self.admin_engine.connect() as conn:
                # 检查数据库是否存在
                result = conn.execute(
                    text("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = :db_name"),
                    {"db_name": self.database}
                )
                if result.fetchone():
                    logger.info(f"[初始化] 数据库已存在: {self.database}")
                    return True

                # 创建数据库
                conn.execute(text(f"CREATE DATABASE `{self.database}` CHARACTER SET {self.charset}"))
                conn.commit()
                logger.info(f"[初始化] 数据库创建成功: {self.database}")
                return True

        except Exception as e:
            logger.error(f"[初始化] 创建数据库失败: {e}")
            return False

    def _create_application_engine(self):
        """创建应用程序引擎（连接到指定数据库）"""
        connection_string = (
            f"mysql+pymysql://{self.username}:{self.password}@{self.host}:{self.port}/{self.database}"
            f"?charset={self.charset}"
        )
        try:
            self.engine = create_engine(
                connection_string,
                connect_args={'connect_timeout': 10},
            )
            logger.info(f"[初始化] 应用程序引擎已创建: {self.database}")
            return True
        except Exception as e:
            logger.error(f"[初始化] 无法创建应用程序引擎: {e}")
            return False

    def _ensure_utf8mb4(self) -> bool:
        """
        确保会话连接与建表使用 utf8mb4，以支持 Emoji 等四字节字符。

        措施：
        1. 会话级执行 SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci；
        2. 校验数据库默认字符集是否为 utf8mb4。
        """
        if not self.engine:
            logger.error("[初始化] 引擎未初始化，无法校验字符集")
            return False

        try:
            with self.engine.connect() as conn:
                conn.execute(text("SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci"))
                conn.execute(text("SET CHARACTER SET utf8mb4"))

                # 校验数据库默认字符集
                result = conn.execute(
                    text(
                        "SELECT DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME "
                        "FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = :db"
                    ),
                    {"db": self.database},
                )
                row = result.fetchone()
                if row:
                    charset, collation = row[0], row[1]
                    if charset not in ("utf8mb4",):
                        logger.warning(
                            f"[初始化] 数据库默认字符集为 {charset}({collation})，"
                            f"建议使用 utf8mb4 以支持 Emoji"
                        )
                    else:
                        logger.info(
                            f"[初始化] 数据库字符集校验通过: {charset}({collation})"
                        )
                else:
                    logger.warning("[初始化] 未查询到数据库字符集信息")
            return True
        except Exception as e:
            logger.error(f"[初始化] 设置/校验 utf8mb4 失败: {e}", exc_info=True)
            return False

    def create_tables(self) -> bool:
        """创建所有数据表"""
        if not self.engine:
            logger.error("[初始化] 引擎未初始化")
            return False

        try:
            # 建表前确保会话与库均使用 utf8mb4（支持 Emoji 写入）
            self._ensure_utf8mb4()

            # 创建所有表（模型已通过 mysql_charset 显式指定 utf8mb4、utf8mb4_unicode_ci）
            Base.metadata.create_all(self.engine)
            logger.info("[初始化] 数据表创建成功")

            # 打印创建的表信息
            inspector = inspect(self.engine)
            tables = inspector.get_table_names()
            logger.info(f"[初始化] 数据库中的表: {tables}")

            # 打印每张表的字符集（确认 Emoji 兼容）
            for table_name in tables:
                if table_name not in ('raw_data_feed', 'financial_sentiment'):
                    continue
                try:
                    t_info = inspector.get_table_options(table_name)
                    charset = t_info.get('mysql_charset', 'N/A')
                    collate = t_info.get('mysql_collate', 'N/A')
                    logger.info(
                        f"[初始化] 表 {table_name} 字符集: {charset} / 排序规则: {collate}"
                    )
                except Exception:
                    pass

            return True

        except Exception as e:
            logger.error(f"[初始化] 创建数据表失败: {e}", exc_info=True)
            return False

        # 已知的新增列迁移（旧表平滑升级，不丢数据）
    COLUMN_MIGRATIONS = {
        'raw_data_feed': {
            # 列名: (ALTER TABLE 子句, 说明)
            'related_tickers': (
                "ADD COLUMN `related_tickers` TEXT NULL COMMENT '关联股票代码列表(JSON数组)' AFTER `source_name`",
                '关联股票代码列表'
            ),
            'content_hash': (
                "ADD COLUMN `content_hash` VARCHAR(64) NULL COMMENT '内容哈希(sha256)，用于同来源内去重' AFTER `additional_data`",
                '内容哈希去重列'
            ),
        },
        'financial_sentiment': {
            'event_hash': (
                "ADD COLUMN `event_hash` VARCHAR(64) NULL COMMENT '事件哈希(sha256)，用于同事件跨轮次去重' AFTER `summary_event`",
                '事件级去重列'
            ),
        },
    }

    def reconcile_schema(self) -> bool:
        """修复旧表结构与 ORM 定义不一致的问题。

        策略：
        1. 缺失表 → 走 create_tables 全新建表；
        2. 表存在但缺列 → 先尝试 ALTER TABLE 增量迁移（保留数据），
           迁移失败（如表损坏）才回退到 DROP + 重建；
        """
        if not self.engine:
            logger.error("[初始化] 引擎未初始化")
            return False

        try:
            inspector = inspect(self.engine)
            tables = set(inspector.get_table_names())
            required = {
                'raw_data_feed': ['id', 'source_type', 'content', 'url', 'source_id', 'source_name', 'related_tickers', 'additional_data', 'content_hash', 'created_at', 'updated_at'],
                'financial_sentiment': ['id', 'stock_name', 'stock_code', 'sentiment_score', 'alert_level', 'summary_event', 'event_hash', 'raw_data_id', 'analysis_metadata', 'created_at', 'updated_at'],
            }

            missing_tables = [t for t in required if t not in tables]
            if missing_tables:
                logger.warning(f"[初始化] 缺失表: {missing_tables}，将执行建表")
                return self.create_tables()

            # 收集所有缺失列
            missing_columns = {}
            for table_name, required_cols in required.items():
                columns = [col['name'] for col in inspector.get_columns(table_name)]
                missing = [c for c in required_cols if c not in columns]
                if missing:
                    missing_columns[table_name] = missing

            if not missing_columns:
                logger.info("[初始化] 表结构已满足 ORM 定义，无需迁移")
                return True

            # 第一优先：已知列的增量 ALTER 迁移（不丢数据）
            all_missing = [c for cols in missing_columns.values() for c in cols]
            known_migrations = {
                (t, c): stmt
                for t, cols in self.COLUMN_MIGRATIONS.items()
                for c, (stmt, _) in cols.items()
            }
            unknown_missing = [
                c for c in all_missing
                if not any((t, c) in known_migrations for t in missing_columns)
            ]

            migrated_any = False
            for table_name, cols in missing_columns.items():
                for col in cols:
                    key = (table_name, col)
                    if key in known_migrations:
                        stmt, desc = self.COLUMN_MIGRATIONS[table_name][col]
                        try:
                            with self.engine.begin() as conn:
                                conn.execute(text(f"ALTER TABLE `{table_name}` {stmt}"))
                            logger.info(f"[初始化] 增量迁移成功: {table_name}.{col} ({desc})，已有数据保留")
                            migrated_any = True
                        except Exception as e:
                            logger.warning(f"[初始化] 增量迁移失败 {table_name}.{col}: {e}")

            # 全部缺失列都有已知迁移且执行成功 → 完成
            if migrated_any and not unknown_missing:
                # 重新验证
                inspector2 = inspect(self.engine)
                all_ok = True
                for table_name, required_cols in required.items():
                    columns = [c['name'] for c in inspector2.get_columns(table_name)]
                    if any(c not in columns for c in required_cols):
                        all_ok = False
                if all_ok:
                    logger.info("[初始化] 增量迁移完成，表结构已满足 ORM 定义")
                    return True

            # 兜底：存在无法增量迁移的缺失列 → 重建（丢数据，仅作最后手段）
            logger.warning(
                f"[初始化] 存在无法增量迁移的缺失列: {unknown_missing or '迁移后仍缺失'}，回退到重建表（数据将丢失）"
            )
            with self.engine.begin() as conn:
                for table_name in ['financial_sentiment', 'raw_data_feed']:
                    if table_name in inspector.get_table_names():
                        conn.execute(text(f"DROP TABLE IF EXISTS `{table_name}`"))
                        logger.warning(f"[初始化] 已删除旧表: {table_name}")

            logger.info("[初始化] 开始重建表结构")
            return self.create_tables()

        except Exception as e:
            logger.error(f"[初始化] 修复表结构失败: {e}", exc_info=True)
            return False

    def _ensure_indexes(self) -> bool:
        """
        幂等补建 ORM 模型中定义的二级查询索引。

        背景：Base.metadata.create_all 只在创建新表时建立索引，
        对已存在的旧表不会补建。此方法以 mysql_models.py 的
        __table_args__ 为唯一事实源，对旧库逐个检查并补齐缺失索引；
        重复执行时索引均已存在、不会产生任何 DDL。
        """
        if not self.engine:
            logger.error("[初始化] 引擎未初始化，无法补建索引")
            return False

        try:
            inspector = inspect(self.engine)
            tables = inspector.get_table_names()
            all_ok = True
            created_any = False

            for table_name in self.INDEXED_TABLES:
                if table_name not in tables:
                    logger.warning(
                        f"[初始化] 表 {table_name} 不存在，跳过索引补建（将由 create_tables 创建）"
                    )
                    continue

                table = Base.metadata.tables[table_name]
                existing = {idx['name']: idx for idx in inspector.get_indexes(table_name)}

                for orm_idx in table.indexes:
                    idx_name = orm_idx.name
                    cols = [c.name for c in orm_idx.columns]

                    if idx_name in existing:
                        # 同名索引已存在；列不一致仅告警，不自动重建（避免误删旧索引）
                        if list(existing[idx_name].get('column_names') or []) != cols:
                            logger.warning(
                                f"[初始化] 索引 {idx_name} 已存在但列不一致: "
                                f"库={existing[idx_name].get('column_names')} 模型={cols}，跳过"
                            )
                        continue

                    col_sql = ", ".join(f"`{c}`" for c in cols)
                    stmt = f"CREATE INDEX `{idx_name}` ON `{table_name}` ({col_sql})"
                    try:
                        with self.engine.begin() as conn:
                            conn.execute(text(stmt))
                        logger.info(
                            f"[初始化] 索引补建成功: {idx_name} ON {table_name} ({', '.join(cols)})"
                        )
                        created_any = True
                    except Exception as e:
                        logger.warning(f"[初始化] 索引补建失败 {idx_name} ON {table_name}: {e}")
                        all_ok = False

            if not created_any:
                logger.info("[初始化] 查询索引均已存在，无需补建")
            return all_ok

        except Exception as e:
            logger.error(f"[初始化] 补建索引失败: {e}", exc_info=True)
            return False

    def _ensure_dedup(self) -> bool:
        """
        幂等启用 raw_data_feed 的内容去重：

        1. 回填 content_hash（MySQL SHA2(content,256) 与 Python sha256 对 utf8 文本结果一致）；
        2. 唯一键缺失时先清理 (source_type, source_id, content_hash) 重复旧行（保留最早 id；
           financial_sentiment.raw_data_id 为 ondelete=SET NULL，不会级联删除分析记录）；
        3. 收紧 content_hash 为 NOT NULL 并建立唯一键 uq_raw_dedup。

        新库由 ORM UniqueConstraint 建表时直接携带，此方法按状态逐项检查、重复执行零副作用。
        """
        if not self.engine:
            logger.error("[初始化] 引擎未初始化，无法启用去重")
            return False

        try:
            inspector = inspect(self.engine)
            if 'raw_data_feed' not in inspector.get_table_names():
                logger.warning("[初始化] 表 raw_data_feed 不存在，跳过去重启用")
                return True

            columns = [c['name'] for c in inspector.get_columns('raw_data_feed')]
            if 'content_hash' not in columns:
                logger.warning(
                    "[初始化] 表 raw_data_feed 缺少 content_hash 列（应由修正表结构步骤迁移），跳过去重启用"
                )
                return True

            has_unique = any(
                idx.get('name') == 'uq_raw_dedup' and idx.get('unique')
                for idx in inspector.get_indexes('raw_data_feed')
            )

            with self.engine.begin() as conn:
                # 1) 回填哈希（无匹配行时 rowcount=0，幂等）
                result = conn.execute(text(
                    "UPDATE `raw_data_feed` SET `content_hash` = SHA2(`content`, 256) "
                    "WHERE `content_hash` IS NULL OR `content_hash` = ''"
                ))
                if result.rowcount:
                    logger.info(f"[初始化] 已回填 content_hash: {result.rowcount} 行")

                if not has_unique:
                    # 2) 先清理存量重复，否则 ADD UNIQUE KEY 会失败
                    result = conn.execute(text(
                        "DELETE r1 FROM `raw_data_feed` r1 "
                        "JOIN `raw_data_feed` r2 "
                        "ON r1.`source_type` = r2.`source_type` "
                        "AND r1.`source_id` = r2.`source_id` "
                        "AND r1.`content_hash` = r2.`content_hash` "
                        "AND r1.`id` > r2.`id`"
                    ))
                    if result.rowcount:
                        logger.info(
                            f"[初始化] 已清理重复原始数据: {result.rowcount} 行（每组保留最早记录）"
                        )

                    # 3) 收紧 NOT NULL（仅首次启用时执行）
                    conn.execute(text(
                        "ALTER TABLE `raw_data_feed` "
                        "MODIFY COLUMN `content_hash` VARCHAR(64) NOT NULL "
                        "COMMENT '内容哈希(sha256)，用于同来源内去重'"
                    ))

                    # 4) 建唯一键
                    try:
                        conn.execute(text(
                            "ALTER TABLE `raw_data_feed` "
                            "ADD UNIQUE KEY `uq_raw_dedup` (`source_type`, `source_id`, `content_hash`)"
                        ))
                        logger.info(
                            "[初始化] 唯一键 uq_raw_dedup 创建成功（同来源同标题只保留一条）"
                        )
                    except Exception as e:
                        logger.warning(f"[初始化] 唯一键 uq_raw_dedup 创建失败: {e}")
                        return False

            return True

        except Exception as e:
            logger.error(f"[初始化] 启用去重失败: {e}", exc_info=True)
            return False

    def _ensure_event_dedup(self) -> bool:
        """
        幂等启用 financial_sentiment 的事件级去重（P1-⑤）：

        1. 幂等回填 event_hash：历史行直接复用 ORM 模型的
           FinancialSentiment.compute_event_hash（与写入路径同一实现，单一事实源，
           避免 SQL 端复刻归一化规则造成漂移）；
        2. 唯一键缺失时先清理同事件重复旧行（保留最早 id），再建唯一键
           uq_sentiment_event；
        3. 无有效事件键的行 event_hash 保持 NULL，MySQL 唯一键对 NULL 天然放行，
           不会误杀无法判重的记录。

        新库由 ORM UniqueConstraint 建表时直接携带，本方法按状态逐项检查、
        重复执行零副作用。
        """
        if not self.engine:
            logger.error("[初始化] 引擎未初始化，无法启用事件去重")
            return False

        try:
            inspector = inspect(self.engine)
            if 'financial_sentiment' not in inspector.get_table_names():
                logger.warning(
                    "[初始化] 表 financial_sentiment 不存在，跳过事件去重启用"
                    "（将由 create_tables 创建）"
                )
                return True

            columns = [c['name'] for c in inspector.get_columns('financial_sentiment')]
            if 'event_hash' not in columns:
                logger.warning(
                    "[初始化] 表 financial_sentiment 缺少 event_hash 列"
                    "（应由修正表结构步骤迁移），跳过事件去重启用"
                )
                return True

            has_unique = any(
                idx.get('name') == 'uq_sentiment_event' and idx.get('unique')
                for idx in inspector.get_indexes('financial_sentiment')
            )

            with self.engine.begin() as conn:
                # 1) 幂等回填：历史行用 Python 端 compute_event_hash 计算
                rows = conn.execute(text(
                    "SELECT `id`, `stock_name`, `stock_code`, `analysis_metadata`, "
                    "`summary_event` FROM `financial_sentiment` "
                    "WHERE `event_hash` IS NULL OR `event_hash` = ''"
                )).fetchall()
                if rows:
                    backfilled = 0
                    for row in rows:
                        row_id, stock_name, stock_code = row[0], row[1], row[2]
                        metadata, summary_event = row[3], row[4]
                        event_text = FinancialSentiment.extract_event_text(
                            metadata, summary_event
                        )
                        event_hash = FinancialSentiment.compute_event_hash(
                            stock_name, stock_code, event_text
                        )
                        if not event_hash:
                            continue  # 无有效事件键，保持 NULL，不参与判重
                        conn.execute(
                            text(
                                "UPDATE `financial_sentiment` SET `event_hash` = :h "
                                "WHERE `id` = :id"
                            ),
                            {"h": event_hash, "id": row_id},
                        )
                        backfilled += 1
                    if backfilled:
                        logger.info(f"[初始化] 已回填 event_hash: {backfilled} 行")

                if not has_unique:
                    # 2) 先清理同事件重复旧行，否则 ADD UNIQUE KEY 会失败
                    #    （event_hash 已含实体键，同 hash 即同事件；NULL 不参与）
                    result = conn.execute(text(
                        "DELETE r1 FROM `financial_sentiment` r1 "
                        "JOIN `financial_sentiment` r2 "
                        "ON r1.`event_hash` = r2.`event_hash` "
                        "AND r1.`id` > r2.`id` "
                        "WHERE r1.`event_hash` IS NOT NULL"
                    ))
                    if result.rowcount:
                        logger.info(
                            f"[初始化] 已清理重复情感记录: {result.rowcount} 行"
                            "（每组保留最早记录）"
                        )

                    # 3) 建唯一键（同事件跨轮次只保留一条）
                    try:
                        conn.execute(text(
                            "ALTER TABLE `financial_sentiment` "
                            "ADD UNIQUE KEY `uq_sentiment_event` (`event_hash`)"
                        ))
                        logger.info(
                            "[初始化] 唯一键 uq_sentiment_event 创建成功"
                            "（同实体同事件文本只保留一条）"
                        )
                    except Exception as e:
                        logger.warning(
                            f"[初始化] 唯一键 uq_sentiment_event 创建失败: {e}"
                        )
                        return False

            return True

        except Exception as e:
            logger.error(f"[初始化] 启用事件去重失败: {e}", exc_info=True)
            return False

    def verify_tables(self) -> bool:
        """验证表结构"""
        if not self.engine:
            logger.error("[初始化] 引擎未初始化")
            return False

        try:
            inspector = inspect(self.engine)
            tables = inspector.get_table_names()

            # 检查必要的表
            required_tables = ['raw_data_feed', 'financial_sentiment']
            missing_tables = [t for t in required_tables if t not in tables]

            if missing_tables:
                logger.error(f"[初始化] 缺失的表: {missing_tables}")
                return False

            logger.info("[初始化] 所有必要表都存在")

            # 验证表结构
            for table_name in required_tables:
                columns = inspector.get_columns(table_name)
                column_names = [col['name'] for col in columns]
                logger.info(f"[初始化] 表 {table_name} 的列: {column_names}")

                # 检查主要列
                if table_name == 'raw_data_feed':
                    required_cols = ['id', 'source_type', 'content', 'url', 'source_id', 'source_name', 'related_tickers', 'additional_data', 'created_at', 'updated_at']
                elif table_name == 'financial_sentiment':
                    required_cols = ['id', 'stock_name', 'stock_code', 'sentiment_score', 'alert_level', 'summary_event', 'event_hash', 'raw_data_id', 'analysis_metadata', 'created_at', 'updated_at']
                else:
                    required_cols = []

                missing_cols = [c for c in required_cols if c not in column_names]
                if missing_cols:
                    logger.error(f"[初始化] 表 {table_name} 缺失的列: {missing_cols}")
                    return False

            logger.info("[初始化] 表结构验证成功")
            return True

        except Exception as e:
            logger.error(f"[初始化] 验证表结构失败: {e}", exc_info=True)
            return False

    def initialize(self) -> bool:
        """完整的初始化流程"""
        logger.info("=" * 60)
        logger.info("开始初始化 MySQL 数据库")
        logger.info("=" * 60)

        steps = [
            ("创建管理员引擎", self._create_admin_engine),
            ("创建数据库", self._create_database),
            ("创建应用程序引擎", self._create_application_engine),
            ("修正表结构", self.reconcile_schema),
            ("创建数据表", self.create_tables),
            ("启用内容去重", self._ensure_dedup),
            ("启用事件去重", self._ensure_event_dedup),
            ("补建查询索引", self._ensure_indexes),
            ("验证表结构", self.verify_tables),
        ]

        for step_name, step_func in steps:
            logger.info(f"\n[步骤] {step_name}...")
            if not step_func():
                logger.error(f"[步骤] {step_name} 失败")
                return False

        logger.info("\n" + "=" * 60)
        logger.info("MySQL 数据库初始化完成！")
        logger.info("=" * 60)
        return True

    def cleanup(self):
        """清理资源"""
        if self.engine:
            self.engine.dispose()
        if self.admin_engine:
            self.admin_engine.dispose()
        logger.info("[初始化] 资源清理完成")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description='MySQL 数据库初始化脚本',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  # 使用默认参数初始化
  python -m trendradar.storage.mysql_init

  # 指定服务器参数
  python -m trendradar.storage.mysql_init --host 192.168.1.10 --port 3306 --user admin

  # 仅验证表结构
  python -m trendradar.storage.mysql_init verify

  # 创建表
  python -m trendradar.storage.mysql_init init
        """
    )

    parser.add_argument(
        'command',
        nargs='?',
        choices=['init', 'verify'],
        default='init',
        help='执行的命令：init（创建表）或 verify（验证表结构）'
    )
    parser.add_argument('--host', default='localhost', help='数据库主机地址（默认: localhost）')
    parser.add_argument('--port', type=int, default=3306, help='数据库端口（默认: 3306）')
    parser.add_argument('--user', dest='username', default='root', help='数据库用户名（默认: root）')
    parser.add_argument('--password', default='12345678', help='数据库密码（默认: 12345678）')
    parser.add_argument('--database', default='trendradar', help='数据库名称（默认: trendradar）')
    parser.add_argument('--charset', default='utf8mb4', help='字符集（默认: utf8mb4）')

    args = parser.parse_args()

    # 创建初始化器
    initializer = MySQLDatabaseInitializer(
        host=args.host,
        port=args.port,
        username=args.username,
        password=args.password,
        database=args.database,
        charset=args.charset,
    )

    try:
        if args.command == 'verify':
            # 仅验证
            success = (
                initializer._create_admin_engine() and
                initializer._create_application_engine() and
                initializer.verify_tables()
            )
        else:
            # 完整初始化（默认）
            success = initializer.initialize()

        sys.exit(0 if success else 1)

    except KeyboardInterrupt:
        logger.info("\n[初始化] 用户中断")
        sys.exit(1)
    except Exception as e:
        logger.error(f"\n[初始化] 发生未知错误: {e}", exc_info=True)
        sys.exit(1)
    finally:
        initializer.cleanup()


if __name__ == '__main__':
    main()
