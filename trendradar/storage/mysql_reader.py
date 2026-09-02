# coding=utf-8
"""
MySQL 读取封装模块 - 面向 MCP / 大模型自然语言查库（阶段五）。

设计目标：
- 不暴露原始 SQL，提供"命名好了的、类型化、幂等"的只读方法，方便
  Cursor / Claude 等 Agent 通过自然语言直接调用检索库内新表数据。
- 所有方法只读（SELECT），无副作用，可安全被 LLM 反复调用。
- 输出统一为 JSON 友好结构（datetime 转 isoformat，枚举转字符串）。

典型用法::

    from trendradar.storage.mysql_reader import MySQLReader

    reader = MySQLReader(host="localhost", port=3306, username="root",
                         password="12345678", database="trendradar")
    schema = reader.describe_schema()            # 表/字段自描述
    rows    = reader.search_sentiments(stock_code="NVDA", limit=5)
    top     = reader.top_stocks(limit=3)
    reader.close()                               # 收尾释放连接池
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from trendradar.storage.mysql_pool import init_db_pool, close_db_pool
from trendradar.storage.mysql_backend import MySQLStorageBackend

import logging

logger = logging.getLogger(__name__)


class MySQLReader:
    """MySQL 只读查询器，为 LLM/MCP 提供自然语言友好的检索接口。"""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 3306,
        username: str = "root",
        password: str = "12345678",
        database: str = "trendradar",
        charset: str = "utf8mb4",
    ):
        self._conn_params = dict(
            host=host, port=port, username=username,
            password=password, database=database, charset=charset,
        )
        # 初始化全局连接池；若已有则复用（幂等）。
        init_db_pool(**self._conn_params)
        self._backend = MySQLStorageBackend()
        logger.info("[MySQLReader] 初始化完成: %s/%s", host, database)

    # ----------------------------------------------------------------
    # 自描述：让大模型理解库结构
    # ----------------------------------------------------------------

    def health_check(self) -> bool:
        """数据库健康检查（只读）；供调用方在初始化后确认连接可用。"""
        return self._backend.health_check()

    def describe_schema(self) -> Dict[str, Any]:
        """返回两张表的字段与查询维度说明，供大模型理解可查内容。

        Returns:
            {"tables": {...}, "sample_counts": {...}}
        """
        stats = self._backend.get_table_stats() if self._backend.health_check() else {}
        return {
            "tables": {
                "raw_data_feed": {
                    "comment": "抓取的原始新闻/热点数据",
                    "columns": {
                        "id": "自增主键",
                        "source_type": "数据源类型: hotlist_news / rss_feed 等",
                        "content": "原始内容(标题/摘要)",
                        "url": "链接",
                        "source_id": "来源ID(如 cls-hot, wallstreetcn-hot)",
                        "source_name": "来源名称",
                        "related_tickers": "关联股票代码列表(JSON数组, 可为NULL)",
                        "additional_data": "附加JSON(rank/ranks/crawl_time 等)",
                        "content_hash": "内容哈希(sha256), (source_type, source_id, content_hash) 唯一去重",
                        "created_at": "创建时间(UTC)",
                        "updated_at": "更新时间(UTC)",
                    },
                    "查询维度": "source_type / source_id / 时间范围 / 关键词",
                },
                "financial_sentiment": {
                    "comment": "LLM 情感分析后的结构化结果",
                    "columns": {
                        "id": "自增主键",
                        "stock_name": "股票名称(如 NVIDIA 英伟达)",
                        "stock_code": "股票代码(如 NVDA)",
                        "sentiment_score": "情感评分, -1(极负)~1(极正)",
                        "alert_level": "告警级别: Low / Medium / High",
                        "summary_event": "事件摘要",
                        "event_hash": "事件哈希(sha256), 同(实体,事件文本)唯一防跨轮重复; NULL=无有效事件键(不参与去重)",
                        "raw_data_id": "关联 raw_data_feed.id(可NULL)",
                        "analysis_metadata": "分析元数据JSON",
                        "created_at": "创建时间(UTC)",
                        "updated_at": "更新时间(UTC)",
                    },
                    "查询维度": "stock_code / alert_level / 评分范围 / 时间范围",
                },
            },
            "sample_counts": stats,
        }

    # ----------------------------------------------------------------
    # 原始新闻数据检索
    # ----------------------------------------------------------------

    def search_raw_data(
        self,
        source_type: Optional[str] = None,
        source_id: Optional[str] = None,
        keyword: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """按条件检索原始新闻数据（只读）。

        Args:
            source_type: 数据源类型（如 "hotlist_news", "rss_feed"）
            source_id: 来源 ID
            keyword: 内容关键词（content 模糊匹配）
            start_date/end_date: 创建时间范围
            limit: 返回条数上限（默认 20）
        """
        rows = self._backend.query_raw_data(
            source_type=source_type,
            source_id=source_id,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )
        if keyword:
            low = keyword.lower()
            rows = [r for r in rows if low in (r.get("content") or "").lower()]
        return rows

    def recent_news(self, source_type: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
        """最近抓取的新闻（按创建时间倒序）。"""
        return self._backend.query_raw_data(source_type=source_type, limit=limit)

    # ----------------------------------------------------------------
    # 情感分析检索
    # ----------------------------------------------------------------

    def search_sentiments(
        self,
        stock_code: Optional[str] = None,
        stock_name: Optional[str] = None,
        alert_level: Optional[str] = None,
        min_sentiment: Optional[float] = None,
        max_sentiment: Optional[float] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """按条件检索情感分析结果（只读）。

        Args:
            stock_code: 股票代码（如 "NVDA"）
            stock_name: 股票名称（如 "英伟达"，模糊匹配）
            alert_level: 告警级别（Low/Medium/High）
            min_sentiment/max_sentiment: 评分范围 [-1, 1]
            start_date/end_date: 创建时间范围
        """
        rows = self._backend.query_financial_sentiment(
            stock_code=stock_code,
            alert_level=alert_level,
            min_sentiment=min_sentiment,
            max_sentiment=max_sentiment,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )
        if stock_name:
            low = stock_name.lower()
            rows = [r for r in rows if low in (r.get("stock_name") or "").lower()]
        return rows

    def top_stocks(self, limit: int = 5, horizon_days: int = 7) -> List[Dict[str, Any]]:
        """近 N 天情感最正面的股票（按平均评分排序）。

        Returns:
            [{stock_code, stock_name, avg_score, count}]
        """
        end = datetime.utcnow()
        start = end - timedelta(days=horizon_days)
        rows = self._backend.query_financial_sentiment(
            start_date=start, end_date=end, limit=100000
        )
        agg: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            code = r.get("stock_code") or "?"
            cand = agg.setdefault(code, {
                "stock_code": code,
                "stock_name": r.get("stock_name") or "",
                "score_sum": 0.0,
                "count": 0,
            })
            cand["score_sum"] += r.get("sentiment_score") or 0.0
            cand["count"] += 1
        results = [
            {
                "stock_code": code,
                "stock_name": cand["stock_name"],
                "avg_score": round(cand["score_sum"] / cand["count"], 4)
                if cand["count"] else 0.0,
                "count": cand["count"],
            }
            for code, cand in agg.items()
        ]
        results.sort(key=lambda x: x["avg_score"], reverse=True)
        return results[:limit]

    def get_sentiment_by_id(self, sentiment_id: int) -> Optional[Dict[str, Any]]:
        """按 ID 获取单条情感分析详情。"""
        return self._backend.get_financial_sentiment(sentiment_id)

    # ----------------------------------------------------------------
    # 收尾：彻底释放连接池（避免连接泄漏）
    # ----------------------------------------------------------------

    def close(self) -> None:
        """释放连接池。调用后可再次 init/查询（幂等）。"""
        close_db_pool()
        logger.info("[MySQLReader] 连接池已关闭")

