# coding=utf-8
"""
存储模块 - 支持多种存储后端

支持的存储后端:
- local: 本地 SQLite + TXT/HTML 文件
- remote: 远程云存储（S3 兼容协议：R2/OSS/COS/S3 等）
- auto: 根据环境自动选择（GitHub Actions 用 remote，其他用 local）
"""

from trendradar.storage.base import (
    StorageBackend,
    NewsItem,
    NewsData,
    RSSItem,
    RSSData,
    convert_crawl_results_to_news_data,
)
from trendradar.storage.sqlite_mixin import SQLiteStorageMixin
from trendradar.storage.local import LocalStorageBackend
from trendradar.storage.manager import StorageManager, get_storage_manager

# 远程后端可选导入（需要 boto3）
try:
    from trendradar.storage.remote import RemoteStorageBackend
    HAS_REMOTE = True
except ImportError:
    RemoteStorageBackend = None
    HAS_REMOTE = False

# MySQL 后端和相关工具
try:
    from trendradar.storage.mysql_models import (
        Base,
        RawDataFeed,
        FinancialSentiment,
        AlertLevel,
    )
    from trendradar.storage.mysql_pool import (
        MySQLDatabasePool,
        init_db_pool,
        get_db_pool,
        close_db_pool,
    )
    from trendradar.storage.mysql_backend import MySQLStorageBackend
    HAS_MYSQL = True
except ImportError:
    Base = None
    RawDataFeed = None
    FinancialSentiment = None
    AlertLevel = None
    MySQLDatabasePool = None
    init_db_pool = None
    get_db_pool = None
    close_db_pool = None
    MySQLStorageBackend = None
    HAS_MYSQL = False

__all__ = [
    # 基础类
    "StorageBackend",
    "NewsItem",
    "NewsData",
    "RSSItem",
    "RSSData",
    # Mixin
    "SQLiteStorageMixin",
    # 转换函数
    "convert_crawl_results_to_news_data",
    # 后端实现
    "LocalStorageBackend",
    "RemoteStorageBackend",
    "HAS_REMOTE",
    "MySQLStorageBackend",
    "HAS_MYSQL",
    # 管理器
    "StorageManager",
    "get_storage_manager",
    # MySQL 相关
    "Base",
    "RawDataFeed",
    "FinancialSentiment",
    "AlertLevel",
    "MySQLDatabasePool",
    "init_db_pool",
    "get_db_pool",
    "close_db_pool",
]
