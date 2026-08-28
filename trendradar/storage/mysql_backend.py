# coding=utf-8
"""
MySQL 存储后端 - 使用 SQLAlchemy ORM

支持将原始数据和 LLM 分析结果存储到 MySQL 数据库
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import text

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
        related_tickers: Optional[List[str]] = None,
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
            related_tickers: 关联股票代码列表（如 ["AAPL", "NVDA"]）
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
                    related_tickers=related_tickers,
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

    def _clean_text(self, value: Any, max_len: int = 0) -> str:
        """
        将任意值安全清洗为可入库的字符串，避免 None/特殊类型/超长触发数据库错误。

        场景：网络抓取/AI 返回的内容可能夹杂 None、bytes、异常 Unicode 或超长文本。
        """
        if value is None:
            return ""
        if isinstance(value, bytes):
            try:
                value = value.decode("utf-8", errors="replace")
            except Exception:
                value = repr(value)
        text = str(value)
        # 去除 Surrogate 字符（JSON/MySQL 无法存储）
        try:
            # 替换孤立代理项，防止 utf8mb4 编码失败
            text = text.encode("utf-8", errors="replace").decode("utf-8")
        except Exception:
            text = text.replace("\ud800", "\ufffd")
        if max_len > 0 and len(text) > max_len:
            text = text[:max_len]
        return text

    def save_raw_data_batch(
        self,
        records: List[Dict[str, Any]],
    ) -> int:
        """
        批量保存原始数据（逐条容错，单条脏数据不会拖垮整批）。

                Args:
            records: 记录列表，每条记录的格式：
                {
                    'source_type': str,
                    'content': str,
                    'url': str,
                    'source_id': str,
                    'source_name': str,
                    'related_tickers': List[str],  # 关联股票代码，如 ["AAPL"]
                    'additional_data': dict
                }

        Returns:
            成功插入的记录数
        """
        saved = 0
        with self.db_pool.session_scope() as session:
            for idx, record in enumerate(records, start=1):
                try:
                    # 字段清洗：列宽以截断方式防御超长与非法类型
                    source_type = self._clean_text(record.get('source_type'), 50)
                    content = self._clean_text(record.get('content'), 10000)
                    url = self._clean_text(record.get('url'), 1024)
                    source_id = self._clean_text(record.get('source_id'), 100)
                    source_name = self._clean_text(record.get('source_name'), 200)
                    additional = record.get('additional_data')

                    # related_tickers 清洗：保证是合法的股票代码字符串列表
                    raw_tickers = record.get('related_tickers') or []
                    related_tickers = None
                    if isinstance(raw_tickers, (list, tuple)):
                        cleaned = []
                        for tk in raw_tickers:
                            if isinstance(tk, str) and tk.strip():
                                tk_clean = tk.strip().upper()[:50]
                                if tk_clean and tk_clean not in cleaned:
                                    cleaned.append(tk_clean)
                        related_tickers = cleaned[:20] if cleaned else None

                    if not content or not source_id:
                        logger.warning(
                            f"[MySQL存储] 跳过无内容/无来源ID的第 {idx} 条记录"
                        )
                        continue

                    row = RawDataFeed(
                        source_type=source_type or 'hotlist_news',
                        content=content,
                        url=url,
                        source_id=source_id or 'unknown',
                        source_name=source_name,
                        related_tickers=related_tickers,
                        additional_data=additional,
                    )
                    session.add(row)
                    session.flush()  # 触发约束校验与自增
                    saved += 1
                except SQLAlchemyError as e:
                    session.rollback()
                    logger.warning(
                        f"[MySQL存储] 第 {idx} 条原始数据入库失败，已跳过: {e}"
                    )
                except (TypeError, ValueError, OverflowError) as e:
                    session.rollback()
                    logger.warning(
                        f"[MySQL存储] 第 {idx} 条原始数据内容非法，已跳过: {e}"
                    )
                except Exception as e:  # 兜底，防止单条异常中断整个定时管道
                    session.rollback()
                    logger.warning(
                        f"[MySQL存储] 第 {idx} 条原始数据未知异常，已跳过: {e}",
                        exc_info=True,
                    )

        logger.info(f"[MySQL存储] 批量保存原始数据: 成功 {saved}/{len(records)} 条")
        return saved

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
        批量保存金融情感分析结果（逐条容错，单条脏数据不会拖垮整批）。

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
        saved = 0
        with self.db_pool.session_scope() as session:
            for idx, record in enumerate(records, start=1):
                try:
                    # 字段清洗与长度防御
                    stock_name = self._clean_text(record.get('stock_name'), 200) or '未知'
                    stock_code = self._clean_text(record.get('stock_code'), 50) or 'UNKNOWN'
                    summary_event = self._clean_text(record.get('summary_event'), 10000)

                    # 情感评分校验/裁剪
                    try:
                        sentiment_score = float(record.get('sentiment_score', 0.0))
                    except (TypeError, ValueError):
                        sentiment_score = 0.0
                    if not (-1.0 <= sentiment_score <= 1.0):
                        sentiment_score = max(-1.0, min(1.0, sentiment_score))

                    # 告警级别解析（非法值回退 LOW）
                    alert_level_in = record.get('alert_level', 'Low')
                    if isinstance(alert_level_in, str):
                        try:
                            alert_level_enum = AlertLevel[alert_level_in.upper()]
                        except KeyError:
                            alert_level_enum = AlertLevel.LOW
                    else:
                        alert_level_enum = alert_level_in if alert_level_in in (
                            AlertLevel.LOW, AlertLevel.MEDIUM, AlertLevel.HIGH
                        ) else AlertLevel.LOW

                    raw_data_id = record.get('raw_data_id')
                    if raw_data_id is not None:
                        try:
                            raw_data_id = int(raw_data_id)
                        except (TypeError, ValueError):
                            raw_data_id = None

                    row = FinancialSentiment(
                        stock_name=stock_name,
                        stock_code=stock_code,
                        sentiment_score=sentiment_score,
                        alert_level=alert_level_enum,
                        summary_event=summary_event,
                        raw_data_id=raw_data_id,
                        analysis_metadata=record.get('analysis_metadata'),
                    )
                    session.add(row)
                    session.flush()
                    saved += 1
                except SQLAlchemyError as e:
                    session.rollback()
                    logger.warning(
                        f"[MySQL存储] 第 {idx} 条情感分析入库失败，已跳过: {e}"
                    )
                except (TypeError, ValueError, OverflowError) as e:
                    session.rollback()
                    logger.warning(
                        f"[MySQL存储] 第 {idx} 条情感分析内容非法，已跳过: {e}"
                    )
                except Exception as e:
                    session.rollback()
                    logger.warning(
                        f"[MySQL存储] 第 {idx} 条情感分析未知异常，已跳过: {e}",
                        exc_info=True,
                    )

        logger.info(f"[MySQL存储] 批量保存情感分析结果: 成功 {saved}/{len(records)} 条")
        return saved

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
                session.execute(text("SELECT 1"))
                logger.debug("[MySQL存储] 健康检查成功")
                return True

        except Exception as e:
            logger.error(f"[MySQL存储] 健康检查失败: {e}")
            return False

    def cleanup_old_data(self, retention_days: int) -> int:
        """
        清理超过保留期限的历史数据（按记录创建时间 created_at）。

        Args:
            retention_days: 保留天数（<=0 表示不清理，直接返回 0）

        Returns:
            删除的记录总数（raw_data_feed 与 financial_sentiment 之和）。
            注意：删除 raw_data_feed 时，其外键 `ON DELETE SET NULL`
            会将其关联的 financial_sentiment.raw_data_id 置空，属预期行为。
        """
        if retention_days <= 0:
            logger.info("[MySQL存储] retention_days<=0，跳过过期数据清理")
            return 0

        try:
            cutoff = datetime.utcnow() - timedelta(days=retention_days)
            with self.db_pool.session_scope() as session:
                # 1) 先清理过期的情感分析记录（自身创建时间早于保留期）
                sentiment_deleted = (
                    session.query(FinancialSentiment)
                    .filter(FinancialSentiment.created_at < cutoff)
                    .delete(synchronize_session=False)
                )
                # 2) 清理过期的原始数据记录
                raw_deleted = (
                    session.query(RawDataFeed)
                    .filter(RawDataFeed.created_at < cutoff)
                    .delete(synchronize_session=False)
                )

                total = int(sentiment_deleted) + int(raw_deleted)
                logger.info(
                    f"[MySQL存储] 清理过期数据完成: "
                    f"情感分析 {int(sentiment_deleted)} 条, "
                    f"原始数据 {int(raw_deleted)} 条"
                )
                return total

        except SQLAlchemyError as e:
            logger.error(f"[MySQL存储] 清理过期数据失败: {e}", exc_info=True)
            return 0

