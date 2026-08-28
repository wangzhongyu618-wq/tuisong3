# coding=utf-8
"""
MySQL 数据管道集成模块

功能：
- 从爬虫数据转换到 MySQL 原始数据表
- 从 AI 分析结果转换到 MySQL 情感分析表
- 提供统一的数据管道接口
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional, Any

from trendradar.storage.mysql_backend import MySQLStorageBackend
from trendradar.storage.mysql_pool import get_db_pool

logger = logging.getLogger(__name__)


class MySQLDataPipeline:
    """MySQL 数据管道 - 处理数据的存储流程"""

    def __init__(self, mysql_backend: Optional[MySQLStorageBackend] = None):
        """
        初始化数据管道

        Args:
            mysql_backend: MySQL 存储后端（如果为 None，将创建新实例）
        """
        self.backend = mysql_backend or MySQLStorageBackend()

    # ========================================
    # 爬虫数据处理
    # ========================================

    def ingest_crawled_news(
        self,
        news_items: List[Dict[str, Any]],
        source_id: str,
        source_name: str = "",
    ) -> int:
        """
        批量存储爬虫抓取的新闻数据

        Args:
            news_items: 新闻条目列表，每条包含：
                {
                    'title': str,          # 新闻标题
                    'url': str,            # 链接
                    'rank': int,           # 排名
                    'ranks': List[int],    # 历史排名
                    'crawl_time': str,     # 抓取时间
                    ...
                }
            source_id: 来源 ID (如 "toutiao", "baidu")
            source_name: 来源名称

        Returns:
            成功存储的条数
        """
        try:
            records = []
            for item in news_items:
                record = {
                    'source_type': 'hotlist_news',  # 热榜新闻
                    'content': item.get('title', ''),
                    'url': item.get('url', ''),
                    'source_id': source_id,
                    'source_name': source_name,
                    'additional_data': {
                        'rank': item.get('rank'),
                        'ranks': item.get('ranks', []),
                        'crawl_time': item.get('crawl_time'),
                        'rank_timeline': item.get('rank_timeline'),
                    }
                }
                records.append(record)

            count = self.backend.save_raw_data_batch(records)
            logger.info(
                f"[MySQL管道] 热榜新闻已存储: "
                f"source_id={source_id}, count={count}"
            )
            return count

        except Exception as e:
            logger.error(f"[MySQL管道] 存储爬虫数据失败: {e}", exc_info=True)
            return 0

    def ingest_rss_feed(
        self,
        rss_items: List[Dict[str, Any]],
        feed_id: str,
        feed_name: str = "",
    ) -> int:
        """
        批量存储 RSS 源数据

        Args:
            rss_items: RSS 条目列表，每条包含：
                {
                    'title': str,
                    'url': str,
                    'summary': str,
                    'published_at': str,
                    ...
                }
            feed_id: RSS 源 ID
            feed_name: RSS 源名称

        Returns:
            成功存储的条数
        """
        try:
            records = []
            for item in rss_items:
                record = {
                    'source_type': 'rss_feed',
                    'content': item.get('title', ''),
                    'url': item.get('url', ''),
                    'source_id': feed_id,
                    'source_name': feed_name,
                    'additional_data': {
                        'summary': item.get('summary', ''),
                        'author': item.get('author', ''),
                        'published_at': item.get('published_at', ''),
                        'guid': item.get('guid', ''),
                    }
                }
                records.append(record)

            count = self.backend.save_raw_data_batch(records)
            logger.info(
                f"[MySQL管道] RSS 数据已存储: "
                f"feed_id={feed_id}, count={count}"
            )
            return count

        except Exception as e:
            logger.error(f"[MySQL管道] 存储 RSS 数据失败: {e}", exc_info=True)
            return 0

    # ========================================
    # AI 分析结果处理
    # ========================================

    def process_ai_analysis(
        self,
        analysis_result: Dict[str, Any],
        raw_data_id: Optional[int] = None,
    ) -> int:
        """
        处理 AI 分析结果并存储情感分析数据

        参数格式示例（从 LLM 解析的结构化数据）：
        {
            'entities': [
                {
                    'type': 'STOCK',
                    'name': 'Apple',
                    'code': 'AAPL',
                    'sentiment_score': 0.75,  # -1.0 到 1.0
                    'alert_level': 'High',     # Low / Medium / High
                    'event_summary': '...',
                    'context': '...'
                },
                ...
            ]
        }

        Args:
            analysis_result: AI 分析结果字典
            raw_data_id: 关联的原始数据 ID（可选）

        Returns:
            成功存储的条数
        """
        try:
            records = []
            entities = analysis_result.get('entities', [])
            skipped = 0

            for entity in entities:
                # 逐条容错：单条脏数据（字段缺失、评分无法转换等）仅跳过该条，
                # 不因个别异常中断整个 AI 分析结果入库，避免拖垮定时管道。
                try:
                    if not isinstance(entity, dict) or entity.get('type') != 'STOCK':
                        continue  # 只处理股票实体

                    # 情感评分安全转换：非法值回退 0.0（后端还会再做越界裁剪）
                    try:
                        sentiment_score = float(entity.get('sentiment_score', 0.0))
                    except (TypeError, ValueError):
                        skipped += 1
                        logger.warning(
                            f"[MySQL管道] 跳过情感评分非法实体: "
                            f"sentiment_score={entity.get('sentiment_score')!r}"
                        )
                        sentiment_score = 0.0

                    record = {
                        'stock_name': entity.get('name', ''),
                        'stock_code': entity.get('code', ''),
                        'sentiment_score': sentiment_score,
                        'alert_level': entity.get('alert_level', 'Low'),
                        'summary_event': entity.get('event_summary', ''),
                        'raw_data_id': raw_data_id,
                        'analysis_metadata': {
                            'context': entity.get('context', ''),
                            'confidence': entity.get('confidence'),
                            'source_text': entity.get('source_text', ''),
                        }
                    }
                    records.append(record)

                except Exception as e:
                    skipped += 1
                    logger.warning(
                        f"[MySQL管道] 第 {len(records) + 1} 条 AI 分析实体解析失败，已跳过: {e}"
                    )

            count = self.backend.save_financial_sentiment_batch(records)
            logger.info(
                f"[MySQL管道] AI 分析结果已存储: count={count} "
                f"(解析跳过 {skipped} 条)"
            )
            return count

        except Exception as e:
            logger.error(f"[MySQL管道] 存储 AI 分析结果失败: {e}", exc_info=True)
            return 0

    def process_ai_analysis_single(
        self,
        stock_name: str,
        stock_code: str,
        sentiment_score: float,
        alert_level: str = "Low",
        summary_event: str = "",
        raw_data_id: Optional[int] = None,
    ) -> bool:
        """
        处理单条 AI 分析结果

        Args:
            stock_name: 股票名称
            stock_code: 股票代码
            sentiment_score: 情感评分
            alert_level: 告警级别
            summary_event: 事件摘要
            raw_data_id: 关联的原始数据 ID

        Returns:
            是否成功
        """
        try:
            record_id = self.backend.save_financial_sentiment(
                stock_name=stock_name,
                stock_code=stock_code,
                sentiment_score=sentiment_score,
                alert_level=alert_level,
                summary_event=summary_event,
                raw_data_id=raw_data_id,
            )
            return record_id is not None

        except Exception as e:
            logger.error(f"[MySQL管道] 存储单条分析结果失败: {e}", exc_info=True)
            return False

    # ========================================
    # 数据查询接口
    # ========================================

    def get_recent_raw_data(
        self,
        source_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        获取最近的原始数据

        Args:
            source_type: 数据源类型（可选）
            limit: 返回数量

        Returns:
            数据列表
        """
        return self.backend.query_raw_data(source_type=source_type, limit=limit)

    def get_alert_sentiments(
        self,
        alert_level: str = "High",
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        获取高告警级别的情感分析记录

        Args:
            alert_level: 告警级别（Low / Medium / High）
            limit: 返回数量

        Returns:
            数据列表
        """
        return self.backend.query_financial_sentiment(
            alert_level=alert_level,
            limit=limit
        )

    def get_sentiments_by_stock(
        self,
        stock_code: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        按股票代码查询情感分析记录

        Args:
            stock_code: 股票代码
            limit: 返回数量

        Returns:
            数据列表
        """
        return self.backend.query_financial_sentiment(
            stock_code=stock_code,
            limit=limit
        )

    # ========================================
    # 数据库管理
    # ========================================

    def get_stats(self) -> Dict[str, int]:
        """获取数据库统计信息"""
        return self.backend.get_table_stats()

    def health_check(self) -> bool:
        """健康检查"""
        return self.backend.health_check()

    def cleanup_old_data(self, retention_days: int) -> int:
        """清理超过保留期限的历史数据（按 created_at）。

        Args:
            retention_days: 保留天数（<=0 表示不清理）;若 >0 则删除更早记录。

        Returns:
            删除的记录总数。
        """
        return self.backend.cleanup_old_data(retention_days)


# 全局管道实例
_pipeline: Optional[MySQLDataPipeline] = None


def init_mysql_pipeline(
    host: str = "localhost",
    port: int = 3306,
    username: str = "root",
    password: str = "12345678",
    database: str = "trendradar",
    charset: str = "utf8mb4",
    pool_size: int = 10,
    max_overflow: int = 20,
) -> MySQLDataPipeline:
    """
    初始化 MySQL 数据管道

    Args:
        (数据库连接参数，见 MySQLDatabasePool)

    Returns:
        MySQLDataPipeline 实例
    """
    global _pipeline
    try:
        from trendradar.storage.mysql_pool import init_db_pool
        
        db_pool = init_db_pool(
            host=host,
            port=port,
            username=username,
            password=password,
            database=database,
            charset=charset,
            pool_size=pool_size,
            max_overflow=max_overflow,
        )
        backend = MySQLStorageBackend(db_pool)
        _pipeline = MySQLDataPipeline(backend)
        logger.info("[MySQL管道] 数据管道初始化完成")
        return _pipeline

    except Exception as e:
        logger.error(f"[MySQL管道] 初始化失败: {e}", exc_info=True)
        raise


def get_mysql_pipeline() -> MySQLDataPipeline:
    """获取全局 MySQL 数据管道实例"""
    global _pipeline
    if _pipeline is None:
        raise RuntimeError("MySQL 数据管道未初始化，请先调用 init_mysql_pipeline()")
    return _pipeline
