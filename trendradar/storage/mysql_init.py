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
import argparse
import logging
from pathlib import Path
from sqlalchemy import create_engine, text, inspect, MetaData
from sqlalchemy.exc import OperationalError, ProgrammingError

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from trendradar.storage.mysql_models import Base, RawDataFeed, FinancialSentiment

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class MySQLDatabaseInitializer:
    """MySQL 数据库初始化器"""

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
            f"?charset={self.charset}&autocommit=false"
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

    def create_tables(self) -> bool:
        """创建所有数据表"""
        if not self.engine:
            logger.error("[初始化] 引擎未初始化")
            return False

        try:
            # 创建所有表
            Base.metadata.create_all(self.engine)
            logger.info("[初始化] 数据表创建成功")

            # 打印创建的表信息
            inspector = inspect(self.engine)
            tables = inspector.get_table_names()
            logger.info(f"[初始化] 数据库中的表: {tables}")

            return True

        except Exception as e:
            logger.error(f"[初始化] 创建数据表失败: {e}", exc_info=True)
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
                    required_cols = ['id', 'source_type', 'content', 'url', 'source_id', 'created_at']
                elif table_name == 'financial_sentiment':
                    required_cols = ['id', 'stock_name', 'stock_code', 'sentiment_score', 'alert_level', 'raw_data_id', 'created_at']
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
            ("创建数据表", self.create_tables),
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
