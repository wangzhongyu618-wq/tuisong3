# coding=utf-8
"""
MySQL 存储后端 - 使用 SQLAlchemy ORM

支持将原始数据和 LLM 分析结果存储到 MySQL 数据库
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from trendradar.storage.mysql_models import (
    Base,
    RawDataFeed,
    FinancialSentiment,
    AlertLevel,
)
from trendradar.storage.mysql_pool import MySQLDatabasePool, get_db_pool

logger = logging.getLogger(__name__)


class MySQLStorageBackend:
    """MySQL 存储后端 - 使用 SQLAlchemy ORM"""

    def __init__(self, db_pool: Optional[MySQLDatabasePool] = None):
        """
        初始化 MySQL 存储后端

        Args:
            db_pool: 数据库连接池（如果为 None，将使用全局池）
        """
        self.db_pool = db_pool or get_db_pool()

    @property
    def backend_name(self) -> str:
        """返回后端名称"""
        return "mysql"

    # ========================================
    # 原始数据存储操作
    # ========================================

    def save_raw_data(
        self,
        source_type: str,
        content: str,
        url: str = "",
        source_id: str = "",
        source_name: str = "",
        additional_data: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        """
        保存原始数据

        Args:
            source_type: 数据源类型
            content: 内容
            url: 链接
            source_id: 来源 ID
            source_name: 来源名称
            additional_data: 额外数据（JSON）

        Returns:
            插入的记录 ID，失败返回 None
        """
        try:
            with self.db_pool.session_scope() as session:
                record = RawDataFeed(
                    source_type=source_type,
                    content=content,
                    url=url,
                    source_id=source_id,
                    source_name=source_name,
                    additional_data=additional_data,
                )
                session.add(record)
                session.flush()  # 获取自增 ID
                record_id = record.id
                logger.debug(
                    f"[MySQL存储] 原始数据已保存: "
                    f"id={record_id}, source_type={source_type}, source_id={source_id}"
                )
                return record_id

        except SQLAlchemyError as e:
            logger.error(f"[MySQL存储] 保存原始数据失败: {e}", exc_info=True)
            return None

    def save_raw_data_batch(
        self,
        records: List[Dict[str, Any]],
    ) -> int:
        """
        批量保存原始数据

        Args:
            records: 记录列表，每条记录的格式：
                {
                    'source_type': str,
                    'content': str,
                    'url': str,
                    'source_id': str,
                    'source_name': str,
                    'additional_data': dict
                }

        Returns:
            成功插入的记录数
        """
        try:
            with self.db_pool.session_scope() as session:
                batch_records = []
                for record in records:
                    batch_records.append(
                        RawDataFeed(
                            source_type=record.get('source_type', ''),
                            content=record.get('content', ''),
                            url=record.get('url', ''),
                            source_id=record.get('source_id', ''),
                            source_name=record.get('source_name', ''),
                            additional_data=record.get('additional_data'),
                        )
                    )
                session.add_all(batch_records)
                session.flush()
                count = len(batch_records)
                logger.info(f"[MySQL存储] 批量保存原始数据: {count} 条")
                return count

        except SQLAlchemyError as e:
            logger.error(f"[MySQL存储] 批量保存原始数据失败: {e}", exc_info=True)
            return 0

    def get_raw_data(self, data_id: int) -> Optional[Dict[str, Any]]:
        """
        获取原始数据

        Args:
            data_id: 数据 ID

        Returns:
            数据字典，不存在返回 None
        """
        try:
            with self.db_pool.session_scope() as session:
                record = session.query(RawDataFeed).filter_by(id=data_id).first()
                if record:
                    return record.to_dict()
                return None

        except SQLAlchemyError as e:
            logger.error(f"[MySQL存储] 获取原始数据失败: {e}", exc_info=True)
            return None

    # ========================================
    # 金融情感分析数据存储操作
    # ========================================

    def save_financial_sentiment(
        self,
        stock_name: str,
        stock_code: str,
        sentiment_score: float,
        alert_level: str = "Low",
        summary_event: str = "",
        raw_data_id: Optional[int] = None,
        analysis_metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        """
        保存金融情感分析结果

        Args:
            stock_name: 股票名称
            stock_code: 股票代码
            sentiment_score: 情感评分（-1.0 到 1.0）
            alert_level: 告警级别（Low / Medium / High）
            summary_event: 事件摘要
            raw_data_id: 关联的原始数据 ID
            analysis_metadata: 分析元数据

        Returns:
            插入的记录 ID，失败返回 None
        """
        try:
            # 验证情感评分
            if not (-1.0 <= sentiment_score <= 1.0):
                logger.warning(
                    f"[MySQL存储] 情感评分超出范围 [{-1.0}, {1.0}]: {sentiment_score}，已裁剪"
                )
                sentiment_score = max(-1.0, min(1.0, sentiment_score))

            # 解析告警级别
            if isinstance(alert_level, str):
                try:
                    alert_level_enum = AlertLevel[alert_level.upper()]
                except KeyError:
                    logger.warning(f"[MySQL存储] 未知的告警级别: {alert_level}，使用 LOW")
                    alert_level_enum = AlertLevel.LOW
            else:
                alert_level_enum = alert_level

            with self.db_pool.session_scope() as session:
                record = FinancialSentiment(
                    stock_name=stock_name,
                    stock_code=stock_code,
                    sentiment_score=sentiment_score,
                    alert_level=alert_level_enum,
                    summary_event=summary_event,
                    raw_data_id=raw_data_id,
                    analysis_metadata=analysis_metadata,
                )
                session.add(record)
                session.flush()
                record_id = record.id
                logger.debug(
                    f"[MySQL存储] 情感分析结果已保存: "
                    f"id={record_id}, stock_code={stock_code}, "
                    f"sentiment_score={sentiment_score}, alert_level={alert_level_enum.value}"
                )
                return record_id

        except SQLAlchemyError as e:
            logger.error(f"[MySQL存储] 保存情感分析结果失败: {e}", exc_info=True)
            return None

    def save_financial_sentiment_batch(
        self,
        records: List[Dict[str, Any]],
    ) -> int:
        """
        批量保存金融情感分析结果

        Args:
            records: 记录列表，每条记录的格式：
                {
                    'stock_name': str,
                    'stock_code': str,
                    'sentiment_score': float,
                    'alert_level': str,
                    'summary_event': str,
                    'raw_data_id': int,
                    'analysis_metadata': dict
                }

        Returns:
            成功插入的记录数
        """
        try:
            with self.db_pool.session_scope() as session:
                batch_records = []
                for record in records:
                    sentiment_score = record.get('sentiment_score', 0.0)
                    # 验证情感评分
                    if not (-1.0 <= sentiment_score <= 1.0):
                        sentiment_score = max(-1.0, min(1.0, sentiment_score))

                    # 解析告警级别
                    alert_level = record.get('alert_level', 'Low')
                    if isinstance(alert_level, str):
                        try:
                            alert_level_enum = AlertLevel[alert_level.upper()]
                        except KeyError:
                            alert_level_enum = AlertLevel.LOW
                    else:
                        alert_level_enum = alert_level

                    batch_records.append(
                        FinancialSentiment(
                            stock_name=record.get('stock_name', ''),
                            stock_code=record.get('stock_code', ''),
                            sentiment_score=sentiment_score,
                            alert_level=alert_level_enum,
                            summary_event=record.get('summary_event', ''),
                            raw_data_id=record.get('raw_data_id'),
                            analysis_metadata=record.get('analysis_metadata'),
                        )
                    )
                session.add_all(batch_records)
                session.flush()
                count = len(batch_records)
                logger.info(f"[MySQL存储] 批量保存情感分析结果: {count} 条")
                return count

        except SQLAlchemyError as e:
            logger.error(f"[MySQL存储] 批量保存情感分析结果失败: {e}", exc_info=True)
            return 0

    def get_financial_sentiment(self, sentiment_id: int) -> Optional[Dict[str, Any]]:
        """
        获取金融情感分析记录

        Args:
            sentiment_id: 记录 ID

        Returns:
            数据字典，不存在返回 None
        """
        try:
            with self.db_pool.session_scope() as session:
                record = session.query(FinancialSentiment).filter_by(id=sentiment_id).first()
                if record:
                    return record.to_dict()
                return None

        except SQLAlchemyError as e:
            logger.error(f"[MySQL存储] 获取情感分析结果失败: {e}", exc_info=True)
            return None

    # ========================================
    # 查询操作
    # ========================================

    def query_raw_data(
        self,
        source_type: Optional[str] = None,
        source_id: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        查询原始数据

        Args:
            source_type: 数据源类型（可选）
            source_id: 来源 ID（可选）
            start_date: 开始时间（可选）
            end_date: 结束时间（可选）
            limit: 返回数据数量限制

        Returns:
            数据字典列表
        """
        try:
            with self.db_pool.session_scope() as session:
                query = session.query(RawDataFeed)

                if source_type:
                    query = query.filter_by(source_type=source_type)
                if source_id:
                    query = query.filter_by(source_id=source_id)
                if start_date:
                    query = query.filter(RawDataFeed.created_at >= start_date)
                if end_date:
                    query = query.filter(RawDataFeed.created_at <= end_date)

                records = query.order_by(RawDataFeed.created_at.desc()).limit(limit).all()
                return [record.to_dict() for record in records]

        except SQLAlchemyError as e:
            logger.error(f"[MySQL存储] 查询原始数据失败: {e}", exc_info=True)
            return []

    def query_financial_sentiment(
        self,
        stock_code: Optional[str] = None,
        alert_level: Optional[str] = None,
        min_sentiment: Optional[float] = None,
        max_sentiment: Optional[float] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        查询金融情感分析结果

        Args:
            stock_code: 股票代码（可选）
            alert_level: 告警级别（可选）
            min_sentiment: 最小情感评分（可选）
            max_sentiment: 最大情感评分（可选）
            start_date: 开始时间（可选）
            end_date: 结束时间（可选）
            limit: 返回数据数量限制

        Returns:
            数据字典列表
        """
        try:
            with self.db_pool.session_scope() as session:
                query = session.query(FinancialSentiment)

                if stock_code:
                    query = query.filter_by(stock_code=stock_code)
                if alert_level:
                    try:
                        alert_level_enum = AlertLevel[alert_level.upper()]
                        query = query.filter_by(alert_level=alert_level_enum)
                    except KeyError:
                        pass

                if min_sentiment is not None:
                    query = query.filter(FinancialSentiment.sentiment_score >= min_sentiment)
                if max_sentiment is not None:
                    query = query.filter(FinancialSentiment.sentiment_score <= max_sentiment)

                if start_date:
                    query = query.filter(FinancialSentiment.created_at >= start_date)
                if end_date:
                    query = query.filter(FinancialSentiment.created_at <= end_date)

                records = query.order_by(FinancialSentiment.created_at.desc()).limit(limit).all()
                return [record.to_dict() for record in records]

        except SQLAlchemyError as e:
            logger.error(f"[MySQL存储] 查询情感分析结果失败: {e}", exc_info=True)
            return []

    # ========================================
    # 数据库管理操作
    # ========================================

    def get_table_stats(self) -> Dict[str, int]:
        """
        获取表统计信息

        Returns:
            {"raw_data_feed": 123, "financial_sentiment": 456}
        """
        try:
            with self.db_pool.session_scope() as session:
                raw_data_count = session.query(RawDataFeed).count()
                sentiment_count = session.query(FinancialSentiment).count()
                return {
                    'raw_data_feed': raw_data_count,
                    'financial_sentiment': sentiment_count,
                }

        except SQLAlchemyError as e:
            logger.error(f"[MySQL存储] 获取表统计失败: {e}", exc_info=True)
            return {}

    def health_check(self) -> bool:
        """
        健康检查

        Returns:
            连接是否正常
        """
        try:
            with self.db_pool.session_scope() as session:
                session.execute("SELECT 1")
                logger.debug("[MySQL存储] 健康检查成功")
                return True

        except Exception as e:
            logger.error(f"[MySQL存储] 健康检查失败: {e}")
            return False
