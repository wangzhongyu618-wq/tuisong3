# coding=utf-8
"""
MySQL 数据库模型定义（SQLAlchemy ORM）

定义核心数据表：
- raw_data_feed: 存储抓取的原始数据
- financial_sentiment: 存储 LLM 解析后的结构化结果
"""

from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional
from sqlalchemy import Column, Integer, String, Text, Float, DateTime, ForeignKey, Enum, DECIMAL, Index, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, validates
from sqlalchemy.types import TypeDecorator
import hashlib
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
     - content_hash: 内容哈希（sha256 hex），(source_type, source_id, content_hash) 唯一，防止重复入库
    - created_at: 记录创建时间（UTC）
    - updated_at: 记录更新时间（UTC）
    """
    __tablename__ = 'raw_data_feed'

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_type = Column(String(50), nullable=False, comment='数据源类型')
    content = Column(Text, nullable=False, comment='原始内容')
    url = Column(String(1024), comment='内容链接')
    source_id = Column(String(100), nullable=False, comment='来源ID')
    source_name = Column(String(200), comment='来源名称')
    related_tickers = Column(JSONType, comment='关联股票代码列表(JSON数组)')
    additional_data = Column(JSONType, comment='额外数据(JSON格式)')
    content_hash = Column(String(64), nullable=False, comment='内容哈希(sha256)，用于同来源内去重')
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, comment='创建时间')
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment='更新时间')

    sentiment_records = relationship('FinancialSentiment', back_populates='raw_data', cascade='all, delete-orphan')

    @validates('content')
    def _hash_content(self, key, value):
        """content 赋值时自动计算 sha256 哈希，供 (source_type, source_id, content_hash) 唯一键去重。"""
        cleaned = value if isinstance(value, str) else ("" if value is None else str(value))
        self.content_hash = hashlib.sha256(cleaned.encode("utf-8", errors="replace")).hexdigest()
        return value

    __table_args__ = (
        Index('idx_source_type_created', 'source_type', 'created_at'),
        Index('idx_source_id_created', 'source_id', 'created_at'),
        # 支撑无 source_type 过滤、仅按时间范围过滤/倒序排序的查询（如 MCP mysql_recent_news）
        Index('idx_created_at', 'created_at'),
        # 内容去重：同一来源下相同标题只保留一条（跨来源允许重复）
        UniqueConstraint('source_type', 'source_id', 'content_hash', name='uq_raw_dedup'),
        {'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_unicode_ci'},
    )

    def __repr__(self):
        return f"<RawDataFeed(id={self.id}, source_type={self.source_type}, source_id={self.source_id})>"

    def to_dict(self):
        return {
            'id': self.id,
            'source_type': self.source_type,
            'content': self.content,
            'url': self.url,
            'source_id': self.source_id,
                        'source_name': self.source_name,
            'related_tickers': self.related_tickers,
            'additional_data': self.additional_data,
            'content_hash': self.content_hash,
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
    - event_hash: 事件哈希（sha256），(实体, 事件文本) 唯一，防止同一事件跨轮次重复入库（P1-⑤）
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
    summary_event = Column(Text, comment='事件摘要')
    event_hash = Column(String(64), nullable=True, comment='事件哈希(sha256)，用于同事件跨轮次去重；无有效事件键时为 NULL（不参与判重）')
    raw_data_id = Column(Integer, ForeignKey('raw_data_feed.id', ondelete='SET NULL'), comment='原始数据ID')
    analysis_metadata = Column(JSONType, comment='分析元数据(JSON格式)')
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, comment='创建时间')
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment='更新时间')

    raw_data = relationship('RawDataFeed', back_populates='sentiment_records')

    __table_args__ = (
        Index('idx_stock_code_created', 'stock_code', 'created_at'),
        Index('idx_alert_level_created', 'alert_level', 'created_at'),
        Index('idx_raw_data_id', 'raw_data_id'),
        Index('idx_sentiment_score', 'sentiment_score'),
        # 支撑仅按时间范围过滤的聚合/倒序查询（如 MCP mysql_top_stocks、按时间取最新情感）
        Index('idx_created_at', 'created_at'),
        # 事件级去重：同一 (实体, 事件文本) 跨轮次只保留一条（event_hash 已含实体键；
        # NULL 不参与唯一键判重，MySQL 对 NULL 天然放行）
        UniqueConstraint('event_hash', name='uq_sentiment_event'),
        {'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_unicode_ci'},
    )

    @staticmethod
    def extract_event_text(metadata=None, summary_event=None) -> str:
        """按 source_text → context → summary_event 顺序提取事件锚点文本。

        - source_text/context 来自 analysis_metadata（提示词约定 context 为
          "原文关键句摘录"，同一新闻跨轮次摘录高度稳定，是判重锚点首选）；
        - metadata 兼容 dict 与 JSON 字符串两种形态（后者用于数据库回填场景）；
        - 全部缺失/脏数据时返回 ''。
        """
        candidates: list = []
        if isinstance(metadata, dict):
            candidates = [metadata.get('source_text'), metadata.get('context')]
        elif isinstance(metadata, str) and metadata.strip():
            try:
                parsed = json.loads(metadata)
            except (ValueError, TypeError):
                parsed = None
            if isinstance(parsed, dict):
                candidates = [parsed.get('source_text'), parsed.get('context')]
        for value in candidates:
            if isinstance(value, str) and value.strip():
                return value
        return summary_event if isinstance(summary_event, str) else ''

    @staticmethod
    def compute_event_hash(stock_name, stock_code, event_text) -> Optional[str]:
        """计算事件级去重哈希（写入与迁移回填共用的单一事实源）。

        事件键 = (实体标识, 归一化事件文本)：
        - 实体标识：stock_code（去首尾空白后大写）优先，为空时回退 stock_name
          （板块/主题实体常无 code，提示词允许 code 为空串）；
        - 事件文本：连续空白（含换行/制表）压缩归一；
        - 分隔符 \\x1f 防止拼接歧义（"AB"+"C" 与 "A"+"BC" 不同键）。

        任一部分为空返回 None（NULL 不参与唯一键判重，保持旧行为不误杀）。
        """
        entity_key = (stock_code or '').strip().upper() or (stock_name or '').strip().upper()
        normalized_text = ' '.join(str(event_text).split()) if event_text else ''
        if not entity_key or not normalized_text:
            return None
        raw = f"{entity_key}\x1f{normalized_text}"
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()

    def __repr__(self):
        return f"<FinancialSentiment(id={self.id}, stock_code={self.stock_code}, sentiment_score={self.sentiment_score})>"

    def to_dict(self):
        return {
            'id': self.id,
            'stock_name': self.stock_name,
            'stock_code': self.stock_code,
            'sentiment_score': self.sentiment_score,
            'alert_level': self.alert_level.value if self.alert_level else None,
            'summary_event': self.summary_event,
            'event_hash': self.event_hash,
            'raw_data_id': self.raw_data_id,
            'analysis_metadata': self.analysis_metadata,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
