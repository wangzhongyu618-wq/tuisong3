# coding=utf-8
"""
MySQL 数据库模型定义（SQLAlchemy ORM）

定义核心数据表：
- raw_data_feed: 存储抓取的原始数据
- financial_sentiment: 存储 LLM 解析后的结构化结果
"""

from datetime import datetime
from enum import Enum as PyEnum
from sqlalchemy import Column, Integer, String, Text, Float, DateTime, ForeignKey, Enum, DECIMAL, Index
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from sqlalchemy.types import TypeDecorator
import json

# 创建 Base 类
Base = declarative_base()


class AlertLevel(str, PyEnum):
    """告警级别枚举"""
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class JSONType(TypeDecorator):
    """JSON 数据类型（用于存储复杂数据结构）"""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            return json.dumps(value, ensure_ascii=False, default=str)
        return None

    def process_result_value(self, value, dialect):
        if value is not None:
            return json.loads(value)
        return None


class RawDataFeed(Base):
    """
    原始数据表 - 存储从各数据源抓取的原始数据

    字段说明：
    - id: 主键（自增）
    - source_type: 数据源类型（如 "news", "rss", "weibo" 等）
    - content: 原始内容（新闻标题、摘要等）
    - url: 内容链接 URL
    - source_id: 来源 ID（如 "toutiao", "baidu" 等）
    - source_name: 来源名称
    - additional_data: 额外数据（JSON格式）
    - created_at: 记录创建时间（UTC）
    - updated_at: 记录更新时间（UTC）
    """
    __tablename__ = 'raw_data_feed'

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_type = Column(String(50), nullable=False, comment='数据源类型')
    content = Column(Text, nullable=False, charset='utf8mb4', comment='原始内容')
    url = Column(String(1024), comment='内容链接')
    source_id = Column(String(100), nullable=False, comment='来源ID')
    source_name = Column(String(200), comment='来源名称')
    additional_data = Column(JSONType, comment='额外数据(JSON格式)')
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, comment='创建时间')
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment='更新时间')

    # 关系：一条原始数据可能对应多条情感分析记录
    sentiment_records = relationship('FinancialSentiment', back_populates='raw_data', cascade='all, delete-orphan')

    # 索引
    __table_args__ = (
        Index('idx_source_type_created', 'source_type', 'created_at'),
        Index('idx_source_id_created', 'source_id', 'created_at'),
        {'charset': 'utf8mb4', 'collate': 'utf8mb4_unicode_ci'},
    )

    def __repr__(self):
        return f"<RawDataFeed(id={self.id}, source_type={self.source_type}, source_id={self.source_id})>"

    def to_dict(self):
        """转换为字典"""
        return {
            'id': self.id,
            'source_type': self.source_type,
            'content': self.content,
            'url': self.url,
            'source_id': self.source_id,
            'source_name': self.source_name,
            'additional_data': self.additional_data,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class FinancialSentiment(Base):
    """
    金融情感分析表 - 存储 LLM 解析后的结构化结果

    字段说明：
    - id: 主键（自增）
    - stock_name: 股票名称（如 "Apple" "NVIDIA"）
    - stock_code: 股票代码（如 "AAPL" "NVDA"）
    - sentiment_score: 情感评分（-1.0 到 1.0，-1=极度负面，0=中立，1=极度正面）
    - alert_level: 告警级别（Low / Medium / High）
    - summary_event: 事件摘要（文本描述）
    - raw_data_id: 外键，指向 raw_data_feed 表
    - analysis_metadata: 分析元数据（JSON格式）
    - created_at: 记录创建时间（UTC）
    - updated_at: 记录更新时间（UTC）
    """
    __tablename__ = 'financial_sentiment'

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_name = Column(String(200), nullable=False, comment='股票名称')
    stock_code = Column(String(50), nullable=False, comment='股票代码')
    sentiment_score = Column(Float, nullable=False, comment='情感评分(-1.0到1.0)')
    alert_level = Column(
        Enum(AlertLevel, native_enum=False),
        default=AlertLevel.LOW,
        nullable=False,
        comment='告警级别'
    )
    summary_event = Column(Text, charset='utf8mb4', comment='事件摘要')
    raw_data_id = Column(Integer, ForeignKey('raw_data_feed.id', ondelete='SET NULL'), comment='原始数据ID')
    analysis_metadata = Column(JSONType, comment='分析元数据(JSON格式)')
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, comment='创建时间')
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment='更新时间')

    # 关系：多条情感分析记录指向一条原始数据
    raw_data = relationship('RawDataFeed', back_populates='sentiment_records')

    # 索引
    __table_args__ = (
        Index('idx_stock_code_created', 'stock_code', 'created_at'),
        Index('idx_alert_level_created', 'alert_level', 'created_at'),
        Index('idx_raw_data_id', 'raw_data_id'),
        Index('idx_sentiment_score', 'sentiment_score'),
        {'charset': 'utf8mb4', 'collate': 'utf8mb4_unicode_ci'},
    )

    def __repr__(self):
        return f"<FinancialSentiment(id={self.id}, stock_code={self.stock_code}, sentiment_score={self.sentiment_score})>"

    def to_dict(self):
        """转换为字典"""
        return {
            'id': self.id,
            'stock_name': self.stock_name,
            'stock_code': self.stock_code,
            'sentiment_score': self.sentiment_score,
            'alert_level': self.alert_level.value if self.alert_level else None,
            'summary_event': self.summary_event,
            'raw_data_id': self.raw_data_id,
            'analysis_metadata': self.analysis_metadata,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
