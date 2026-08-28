# coding=utf-8
"""
MySQL 数据库连接管理模块

提供：
- 数据库连接池管理
- SQLAlchemy Session 会话管理
- 连接池配置和监控
- 异常处理和重试机制
"""

import logging
from contextlib import contextmanager
from typing import Optional, Any
from sqlalchemy import create_engine, event, Engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import QueuePool
from sqlalchemy.exc import SQLAlchemyError, OperationalError
import time

logger = logging.getLogger(__name__)


class MySQLDatabasePool:
    """MySQL 数据库连接池管理器"""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 3306,
        username: str = "root",
        password: str = "12345678",
        database: str = "trendradar",
        charset: str = "utf8mb4",
        pool_size: int = 10,
        max_overflow: int = 20,
        pool_recycle: int = 3600,
        echo_sql: bool = False,
    ):
        """
        初始化 MySQL 连接池

        Args:
            host: 数据库主机地址
            port: 数据库端口
            username: 数据库用户名
            password: 数据库密码
            database: 数据库名称
            charset: 字符集
            pool_size: 连接池大小
            max_overflow: 超出 pool_size 的最大连接数
            pool_recycle: 连接回收时间（秒）
            echo_sql: 是否打印 SQL 语句（调试用）
        """
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.database = database
        self.charset = charset
        self.pool_size = pool_size
        self.max_overflow = max_overflow
        self.pool_recycle = pool_recycle
        self.echo_sql = echo_sql

        # 连接字符串 (PyMySQL 驱动)
        self.connection_string = (
            f"mysql+pymysql://{self.username}:{self.password}@{self.host}:{self.port}/{self.database}"
            f"?charset={self.charset}&autocommit=true"
        )

        self.engine: Optional[Engine] = None
        self.session_factory: Optional[sessionmaker] = None
        self._initialized = False

    def initialize(self):
        """初始化数据库引擎和会话工厂"""
        if self._initialized:
            logger.warning("数据库连接池已初始化，跳过重复初始化")
            return

        try:
            logger.info(f"[数据库] 初始化 MySQL 连接池: {self.host}:{self.port}/{self.database}")

            # 创建引擎
            self.engine = create_engine(
                self.connection_string,
                poolclass=QueuePool,
                pool_size=self.pool_size,
                max_overflow=self.max_overflow,
                pool_recycle=self.pool_recycle,
                echo=self.echo_sql,
                connect_args={
                    'connect_timeout': 10,
                    'read_timeout': 30,
                    'write_timeout': 30,
                },
            )

            # 创建会话工厂
            self.session_factory = sessionmaker(
                bind=self.engine,
                expire_on_commit=False,
                autoflush=False,
            )

            # 注册连接事件监听器
            self._register_event_listeners()

            logger.info("[数据库] 连接池初始化成功")
            self._initialized = True

        except Exception as e:
            logger.error(f"[数据库] 连接池初始化失败: {e}", exc_info=True)
            raise

    def _register_event_listeners(self):
        """注册连接事件监听器"""
        if not self.engine:
            return

        # 连接返回到池时，清理连接状态
        @event.listens_for(self.engine, "connect")
        def receive_connect(dbapi_conn, connection_record):
            """连接创建时设置会话参数"""
            try:
                cursor = dbapi_conn.cursor()
                # 设置 MySQL 会话参数
                cursor.execute("SET SESSION sql_mode='STRICT_TRANS_TABLES'")
                cursor.execute("SET SESSION innodb_lock_wait_timeout=5")
                cursor.close()
                logger.debug("[数据库] MySQL 会话参数设置成功")
            except Exception as e:
                logger.warning(f"[数据库] 无法设置会话参数: {e}")

        # 连接检出时的处理
        @event.listens_for(self.engine, "checkout")
        def receive_checkout(dbapi_conn, connection_record, connection_proxy):
            """连接检出时进行健康检查"""
            try:
                cursor = dbapi_conn.cursor()
                cursor.execute("SELECT 1")
                cursor.close()
            except (OperationalError, Exception) as e:
                logger.warning(f"[数据库] 连接健康检查失败，将重新建立: {e}")
                raise

    def get_session(self) -> Session:
        """
        获取数据库会话

        Returns:
            SQLAlchemy Session 实例
        """
        if not self._initialized:
            self.initialize()

        if not self.session_factory:
            raise RuntimeError("数据库连接池未初始化")

        return self.session_factory()

    @contextmanager
    def session_scope(self):
        """
        会话上下文管理器（自动提交/回滚）

        使用方式:
            with db_pool.session_scope() as session:
                result = session.query(MyModel).filter(...).first()

        Returns:
            SQLAlchemy Session 实例
        """
        session = self.get_session()
        try:
            yield session
            session.commit()
            logger.debug("[数据库] 事务提交成功")
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"[数据库] 事务执行失败，已回滚: {e}", exc_info=True)
            raise
        except Exception as e:
            session.rollback()
            logger.error(f"[数据库] 未知错误，事务已回滚: {e}", exc_info=True)
            raise
        finally:
            session.close()

    def execute_with_retry(
        self,
        func,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        *args,
        **kwargs
    ) -> Any:
        """
        带重试机制的数据库操作执行

        Args:
            func: 要执行的函数
            max_retries: 最大重试次数
            retry_delay: 重试延迟（秒）
            *args: 函数位置参数
            **kwargs: 函数关键字参数

        Returns:
            函数执行结果
        """
        last_error = None
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except (OperationalError, Exception) as e:
                last_error = e
                if attempt < max_retries - 1:
                    wait_time = retry_delay * (2 ** attempt)  # 指数退避
                    logger.warning(
                        f"[数据库] 操作失败 (尝试 {attempt + 1}/{max_retries})，"
                        f"{wait_time:.1f}s 后重试: {e}"
                    )
                    time.sleep(wait_time)
                else:
                    logger.error(f"[数据库] 操作失败，已达最大重试次数: {e}")

        raise last_error or RuntimeError("数据库操作失败")

    def close(self):
        """关闭数据库连接池"""
        if self.engine:
            self.engine.dispose()
            logger.info("[数据库] 连接池已关闭")

    def __enter__(self):
        """上下文管理器入口"""
        self.initialize()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口"""
        self.close()

    def __repr__(self):
        return (
            f"<MySQLDatabasePool(host={self.host}, port={self.port}, "
            f"database={self.database}, pool_size={self.pool_size})>"
        )


# 全局数据库池实例
_db_pool: Optional[MySQLDatabasePool] = None


def init_db_pool(
    host: str = "localhost",
    port: int = 3306,
    username: str = "root",
    password: str = "12345678",
    database: str = "trendradar",
    charset: str = "utf8mb4",
    pool_size: int = 10,
    max_overflow: int = 20,
    pool_recycle: int = 3600,
    echo_sql: bool = False,
) -> MySQLDatabasePool:
    """
    初始化全局数据库池

    Args:
        (见 MySQLDatabasePool.__init__)

    Returns:
        MySQLDatabasePool 实例
    """
    global _db_pool
    _db_pool = MySQLDatabasePool(
        host=host,
        port=port,
        username=username,
        password=password,
        database=database,
        charset=charset,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_recycle=pool_recycle,
        echo_sql=echo_sql,
    )
    _db_pool.initialize()
    return _db_pool


def get_db_pool() -> MySQLDatabasePool:
    """
    获取全局数据库池实例

    Returns:
        MySQLDatabasePool 实例
    """
    global _db_pool
    if _db_pool is None:
        raise RuntimeError("数据库池尚未初始化，请先调用 init_db_pool()")
    return _db_pool


def close_db_pool():
    """关闭全局数据库池"""
    global _db_pool
    if _db_pool:
        _db_pool.close()
        _db_pool = None
        logger.info("[数据库] 全局数据库池已关闭")
