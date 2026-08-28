# coding=utf-8
"""
MySQL 存储适配层 - 对齐 StorageManager.save() 统一存储接口
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

from trendradar.storage.base import (
    StorageBackend,
    NewsData,
    RSSData,
    NewsItem,
)

logger = logging.getLogger(__name__)

try:  # pragma: no cover - 环境探测
    from trendradar.storage.mysql_backend import MySQLStorageBackend
    from trendradar.storage.mysql_pool import init_db_pool, get_db_pool, close_db_pool
    HAS_MYSQL_BACKEND = True
except ImportError:  # pragma: no cover
    MySQLStorageBackend = None
    init_db_pool = None
    get_db_pool = None
    close_db_pool = None
    HAS_MYSQL_BACKEND = False


class MySQLStorageBackendAdapter(StorageBackend):
    """
    与 StorageManager 对齐的 MySQL 存储后端。

    用法::

        from trendradar.storage import get_storage_manager
        storage_manager = get_storage_manager(backend_type="mysql")
        storage_manager.save_news_data(news_data)   # -> raw_data_feed
        storage_manager.save_sentiment(...)          # -> financial_sentiment
    """

    def __init__(self, conn_params: Optional[Dict[str, Any]] = None):
        """
        Args:
            conn_params: MySQL 连接参数（host/port/username/password/database/charset
                         /pool_size/max_overflow）。为 None 时使用全局连接池，
                         若全局池未初始化则按默认参数初始化。
        """
        self._conn_params = conn_params or {}
        self._backend: Optional[MySQLStorageBackend] = None
        self._owns_pool = False

    @property
    def mysql_backend(self) -> "MySQLStorageBackend":
        """懒加载底层 MySQL 存储后端，同时确保数据库连接池就绪。"""
        if self._backend is not None:
            return self._backend

        if not HAS_MYSQL_BACKEND or MySQLStorageBackend is None:
            raise RuntimeError(
                "MySQL 后端不可用：缺少 SQLAlchemy/PyMySQL，或未安装 MySQL 依赖。"
            )

        db_pool = None
        try:
            db_pool = get_db_pool()
        except RuntimeError:
            if init_db_pool:
                host = self._conn_params.get("host", "localhost")
                port = int(self._conn_params.get("port", 3306))
                username = self._conn_params.get("username", "root")
                password = self._conn_params.get("password", "12345678")
                database = self._conn_params.get("database", "trendradar")
                charset = self._conn_params.get("charset", "utf8mb4")
                db_pool = init_db_pool(
                    host=host,
                    port=port,
                    username=username,
                    password=password,
                    database=database,
                    charset=charset,
                    pool_size=int(self._conn_params.get("pool_size", 10)),
                    max_overflow=int(self._conn_params.get("max_overflow", 20)),
                )
                self._owns_pool = True

        if db_pool is None:
            raise RuntimeError("MySQL 连接池初始化失败")

        self._backend = MySQLStorageBackend(db_pool)
        return self._backend

    # ========================================
    # 必须有实现（StorageBackend 抽象方法）
    # ========================================

    @property
    def backend_name(self) -> str:
        """后端名称"""
        return "mysql"

    @property
    def supports_txt(self) -> bool:
        """MySQL 不产出 TXT 快照文件"""
        return False

    def save_news_data(self, data: NewsData) -> bool:
        """保存新闻数据到 raw_data_feed 表。

        news_data.items: {source_id: [NewsItem, ...]}
        """
        backend = self.mysql_backend

        if not data or data.get_total_count() == 0:
            logger.info("[MySQL适配] 无新闻数据可保存")
            return True

        records: List[Dict[str, Any]] = []
        for source_id, news_list in (data.items or {}).items():
            if not news_list:
                continue
            source_name = data.id_to_name.get(source_id, source_id)
            for item in news_list:
                records.append(
                    {
                        "source_type": "hotlist_news",
                        "content": (item.title or "").strip(),
                        "url": item.url or "",
                        "source_id": source_id,
                        "source_name": (item.source_name or source_name),
                        "additional_data": {
                            "rank": item.rank,
                            "ranks": item.ranks or [],
                            "crawl_time": item.crawl_time,
                            "mobile_url": item.mobile_url,
                            "first_time": item.first_time,
                            "last_time": item.last_time,
                            "count": item.count,
                            "rank_timeline": item.rank_timeline or [],
                        },
                    }
                )

        try:
            saved = backend.save_raw_data_batch(records)
            logger.info(
                f"[MySQL适配] save_news_data 完成: 计划 {len(records)} 条，"
                f"成功 {saved} 条"
            )
            return saved > 0 or len(records) == 0
        except Exception as e:  # pragma: no cover - 防御
            logger.error(f"[MySQL适配] save_news_data 失败: {e}", exc_info=True)
            return False

    def save_rss_data(self, data: RSSData) -> bool:
        """保存 RSS 数据到 raw_data_feed 表（source_type=rss_feed）。"""
        backend = self.mysql_backend

        if not data or data.get_total_count() == 0:
            logger.info("[MySQL适配] 无 RSS 数据可保存")
            return True

        records: List[Dict[str, Any]] = []
        for feed_id, rss_list in (data.items or {}).items():
            if not rss_list:
                continue
            feed_name = data.id_to_name.get(feed_id, feed_id)
            for item in rss_list:
                records.append(
                    {
                        "source_type": "rss_feed",
                        "content": item.title or "",
                        "url": item.url or "",
                        "source_id": feed_id,
                        "source_name": (item.feed_name or feed_name),
                        "additional_data": {
                            "guid": item.guid,
                            "summary": item.summary,
                            "author": item.author,
                            "published_at": item.published_at,
                            "crawl_time": item.crawl_time,
                        },
                    }
                )

        try:
            saved = backend.save_raw_data_batch(records)
            logger.info(
                f"[MySQL适配] save_rss_data 完成: 计划 {len(records)} 条，"
                f"成功 {saved} 条"
            )
            return saved > 0 or len(records) == 0
        except Exception as e:  # pragma: no cover - 防御
            logger.error(f"[MySQL适配] save_rss_data 失败: {e}", exc_info=True)
            return False

    def get_today_all_data(self, date: Optional[str] = None) -> Optional[NewsData]:
        """从 raw_data_feed 读取热榜新闻，尽量组织回 NewsData。"""
        backend = self.mysql_backend
        return self._build_news_data(backend, date=date)

    def get_latest_crawl_data(self, date: Optional[str] = None) -> Optional[NewsData]:
        """别名：读取最新(按时间倒序)的 raw_data_feed 热榜新闻。"""
        return self.get_today_all_data(date)

    def _build_news_data(
        self, backend: "MySQLStorageBackend", date: Optional[str] = None
    ) -> Optional[NewsData]:
        """按日期查询 hotlist_news 并组装成 NewsData。"""
        try:
            start_dt, end_dt = self._day_range(date)
            rows = backend.query_raw_data(
                source_type="hotlist_news",
                start_date=start_dt,
                end_date=end_dt,
                limit=10000,
            )
            if not rows:
                return None

            items: Dict[str, List[NewsItem]] = {}
            id_to_name: Dict[str, str] = {}
            for row in rows:
                sid = row.get("source_id") or "unknown"
                additional = row.get("additional_data") or {}
                item = NewsItem(
                    title=row.get("content", ""),
                    source_id=sid,
                    source_name=row.get("source_name", ""),
                    rank=additional.get("rank", 0),
                    url=row.get("url", ""),
                    mobile_url=additional.get("mobile_url", ""),
                    crawl_time=additional.get("crawl_time", ""),
                    ranks=additional.get("ranks", []) or [],
                    first_time=additional.get("first_time", ""),
                    last_time=additional.get("last_time", ""),
                    count=additional.get("count", 1),
                    rank_timeline=additional.get("rank_timeline", []) or [],
                )
                items.setdefault(sid, []).append(item)
                if row.get("source_name"):
                    id_to_name[sid] = row["source_name"]

            return NewsData(
                date=(date or self._today_str()),
                crawl_time="",
                items=items,
                id_to_name=id_to_name,
                failed_ids=[],
            )
        except Exception as e:  # pragma: no cover - 防御
            logger.error(f"[MySQL适配] 组装 NewsData 失败: {e}", exc_info=True)
            return None

    def detect_new_titles(self, current_data: NewsData) -> Dict[str, Dict]:
        """对比现有数据库，返回新增标题（尚未入库的）。"""
        backend = self.mysql_backend
        if not current_data:
            return {}

        start_dt, end_dt = self._day_range(None)
        rows = backend.query_raw_data(
            source_type="hotlist_news",
            start_date=start_dt,
            end_date=end_dt,
            limit=100000,
        )
        seen: Dict[str, Set[str]] = {}
        for row in rows:
            sid = row.get("source_id") or "unknown"
            seen.setdefault(sid, set()).add(row.get("content", ""))

        new_titles: Dict[str, Dict] = {}
        for source_id, news_list in (current_data.items or {}).items():
            existing = seen.get(source_id, set())
            for item in news_list:
                if item.title and item.title not in existing:
                    new_titles.setdefault(source_id, {})
                    new_titles[source_id][item.title] = {
                        "ranks": item.ranks or [],
                        "url": item.url,
                        "mobileUrl": item.mobile_url,
                        "rank": item.rank,
                    }
        return new_titles

    def save_txt_snapshot(self, data: NewsData) -> Optional[str]:
        """MySQL 不产出 TXT 快照。"""
        return None

    def save_html_report(self, html_content: str, filename: str) -> Optional[str]:
        """MySQL 不产出 HTML 报告文件。"""
        return None

    def is_first_crawl_today(self, date: Optional[str] = None) -> bool:
        """MySQL 中当天无已入库热榜新闻即为首次抓取。"""
        backend = self.mysql_backend
        try:
            start_dt, end_dt = self._day_range(date)
            rows = backend.query_raw_data(
                source_type="hotlist_news",
                start_date=start_dt,
                end_date=end_dt,
                limit=1,
            )
            return not rows
        except Exception:
            return True

    def cleanup(self) -> None:
        """关闭自建连接池（复用全局池则交由全局清理）。"""
        if self._backend is not None:
            self._backend = None
        if self._owns_pool and close_db_pool is not None:
            close_db_pool()
            self._owns_pool = False
        logger.info("[MySQL适配] 资源已清理")

    def cleanup_old_data(self, retention_days: int) -> int:
        """按保留期清理 MySQL 中的过期历史数据。

        对齐 StorageBackend.cleanup_old_data 抽象语义：删除超过保留期限的行，
        返回被删除的记录行数（返回 0 表示无可清理或未启用）。
        """
        if retention_days <= 0:
            logger.info("[MySQL适配] retention_days<=0，跳过过期数据清理")
            return 0
        backend = self.mysql_backend
        return backend.cleanup_old_data(retention_days)


    # ========================================
    # 便捷扩展方法
    # ========================================

    def save_sentiment(
        self,
        stock_name: str,
        stock_code: str,
        sentiment_score: float,
        alert_level: str = "Low",
        summary_event: str = "",
        raw_data_id: Optional[int] = None,
        analysis_metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        """保存单条金融情感分析结果到 financial_sentiment 表。"""
        backend = self.mysql_backend
        return backend.save_financial_sentiment(
            stock_name=stock_name,
            stock_code=stock_code,
            sentiment_score=sentiment_score,
            alert_level=alert_level,
            summary_event=summary_event,
            raw_data_id=raw_data_id,
            analysis_metadata=analysis_metadata,
        )

    def save_sentiment_batch(self, records: List[Dict[str, Any]]) -> int:
        """批量保存金融情感分析结果。"""
        backend = self.mysql_backend
        return backend.save_financial_sentiment_batch(records)

    def get_stats(self) -> Dict[str, int]:
        """获取数据库统计信息。"""
        return self.mysql_backend.get_table_stats()

    def health_check(self) -> bool:
        """数据库健康检查。"""
        backend = self.mysql_backend
        return bool(backend.health_check())

    # ========================================
    # 内部工具
    # ========================================

    @staticmethod
    def _today_str() -> str:
        return datetime.now().strftime("%Y-%m-%d")

    @staticmethod
    def _day_range(date: Optional[str] = None) -> tuple:
        """返回 [date 00:00:00, date 23:59:59]（本地时间）。"""
        if date:
            start = datetime.strptime(date, "%Y-%m-%d")
        else:
            start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1) - timedelta(microseconds=1)
        return start, end
