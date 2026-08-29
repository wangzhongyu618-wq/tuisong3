# coding=utf-8
"""
MySQL 只读查询工具

将 ``trendradar.storage.mysql_reader.MySQLReader`` 的只读检索方法封装为
MCP 工具类，供 AI Agent 通过自然语言查询 MySQL 库中的原始抓取数据
（raw_data_feed）与情感分析结果（financial_sentiment）。

设计要点：
- 所有方法只读（SELECT），无副作用，可安全被 LLM 反复调用；
- 连接懒初始化：首次调用工具时才建立连接池，MCP 服务器启动不依赖 MySQL；
- 连接配置优先级：MYSQL_* 环境变量 > config/config.yaml 的 storage.mysql > 默认值；
- 连接失败转为带修复建议的 MCPError，不影响 MCP 服务器其它工具。
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from ..utils.errors import MCPError


def _parse_iso_datetime(value: Optional[str], field_name: str) -> Optional[datetime]:
    """把 ISO 风格日期字符串解析为 datetime（工具层入参校验）。

    支持 "YYYY-MM-DD"、"YYYY-MM-DD HH:MM:SS"、"YYYY-MM-DDTHH:MM:SS"
    以及带 "Z"/时区偏移的完整 ISO 时间戳；空值返回 None。
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None

    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    candidates = [text]
    # "2026-08-01T12:30:00" → fromisoformat 直接支持；补齐空格分隔写法
    if "T" in text:
        candidates.append(text.replace("T", " ", 1))

    for candidate in candidates:
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            pass

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    raise MCPError(
        message=f"参数 {field_name} 不是有效的日期格式: {value!r}",
        code="INVALID_PARAMETER",
        suggestion="请使用 ISO 格式日期，如 2026-08-01 或 2026-08-01T12:00:00",
    )


def _clamp_limit(limit: int, default: int = 20, maximum: int = 200) -> int:
    """把 limit 收敛到 [1, maximum]，非法输入回退默认值。"""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return default
    return max(1, min(limit, maximum))


class MySQLReaderTools:
    """MySQL 只读查询工具类"""

    def __init__(self, project_root: str = None):
        """
        初始化 MySQL 只读查询工具

        Args:
            project_root: 项目根目录（用于定位 config/config.yaml）
        """
        if project_root:
            self.project_root = Path(project_root)
        else:
            current_file = Path(__file__)
            self.project_root = current_file.parent.parent.parent

        self._reader = None  # 懒初始化，首次调用时才连接
        self._conn_summary: Dict[str, Any] = {}

    # ----------------------------------------------------------------
    # 配置与连接
    # ----------------------------------------------------------------

    def _load_mysql_config(self) -> Dict[str, Any]:
        """组装连接参数：MYSQL_* 环境变量 > config.yaml storage.mysql > 默认值。"""
        cfg: Dict[str, Any] = {}
        config_file = self.project_root / "config" / "config.yaml"
        if config_file.exists():
            try:
                from trendradar.core.loader import load_config

                cfg = load_config(str(config_file)).get("STORAGE", {}).get("MYSQL", {}) or {}
            except Exception:
                cfg = {}  # 配置缺失/损坏时不阻断，退回环境变量与默认值

        def _pick(env_key: str, cfg_key: str, default: Any) -> Any:
            value = os.getenv(env_key, "").strip()
            if value:
                return value
            cfg_value = cfg.get(cfg_key)
            if cfg_value not in (None, ""):
                return cfg_value
            return default

        return {
            "host": str(_pick("MYSQL_HOST", "HOST", "localhost")),
            "port": int(_pick("MYSQL_PORT", "PORT", 3306)),
            "username": str(_pick("MYSQL_USERNAME", "USERNAME", "root")),
            "password": str(_pick("MYSQL_PASSWORD", "PASSWORD", "")),
            "database": str(_pick("MYSQL_DATABASE", "DATABASE", "trendradar")),
            "charset": str(_pick("MYSQL_CHARSET", "CHARSET", "utf8mb4")),
        }

    def _get_reader(self):
        """懒初始化 MySQLReader；失败时转为带修复建议的 MCPError。"""
        if self._reader is None:
            try:
                from trendradar.storage.mysql_reader import MySQLReader

                conn = self._load_mysql_config()
                self._reader = MySQLReader(**conn)
                # 连接/认证失败在 backend 层会被吞掉（返回空结果），
                # 这里显式体检一次，确保"库不可达"以明确错误暴露给 Agent。
                if not self._reader.health_check():
                    raise RuntimeError("MySQL 健康检查未通过（连接或认证失败）")
                self._conn_summary = {
                    k: v for k, v in conn.items() if k != "password"
                }
            except Exception as exc:
                raise MCPError(
                    message=f"MySQL 连接/初始化失败: {exc}",
                    code="MYSQL_UNAVAILABLE",
                    suggestion=(
                        "请确认 MySQL 服务已启动、已执行 "
                        "python -m trendradar.storage.mysql_init 初始化表结构，"
                        "并检查 MYSQL_HOST/MYSQL_PORT/MYSQL_USERNAME/MYSQL_PASSWORD/"
                        "MYSQL_DATABASE 环境变量或 config.yaml 的 storage.mysql 配置"
                    ),
                )
        return self._reader

    # ----------------------------------------------------------------
    # 只读查询方法（供 server.py 的 @mcp.tool 调用）
    # ----------------------------------------------------------------

    def describe_schema(self) -> Dict:
        """获取两张表的字段与可查询维度说明（Agent 查询起点）。"""
        reader = self._get_reader()
        try:
            schema = reader.describe_schema()
        except MCPError:
            raise
        except Exception as exc:
            raise MCPError(
                message=f"读取 MySQL 表结构失败: {exc}",
                code="MYSQL_QUERY_ERROR",
                suggestion="请检查数据库连接与表是否已初始化",
            )
        return {"success": True, "connection": self._conn_summary, **schema}

    def search_raw_data(
        self,
        source_type: Optional[str] = None,
        source_id: Optional[str] = None,
        keyword: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 20,
    ) -> Dict:
        """按条件检索原始新闻数据（raw_data_feed，只读）。"""
        reader = self._get_reader()
        try:
            rows = reader.search_raw_data(
                source_type=source_type or None,
                source_id=source_id or None,
                keyword=keyword or None,
                start_date=_parse_iso_datetime(start_date, "start_date"),
                end_date=_parse_iso_datetime(end_date, "end_date"),
                limit=_clamp_limit(limit),
            )
        except MCPError:
            raise
        except Exception as exc:
            raise MCPError(
                message=f"查询 raw_data_feed 失败: {exc}",
                code="MYSQL_QUERY_ERROR",
                suggestion="请检查筛选参数（source_type/source_id/时间范围）后重试",
            )
        return {"success": True, "total": len(rows), "items": rows}

    def recent_news(
        self,
        source_type: Optional[str] = None,
        limit: int = 10,
    ) -> Dict:
        """获取最近抓取的新闻（raw_data_feed，按创建时间倒序，只读）。"""
        reader = self._get_reader()
        try:
            rows = reader.recent_news(
                source_type=source_type or None,
                limit=_clamp_limit(limit, default=10),
            )
        except MCPError:
            raise
        except Exception as exc:
            raise MCPError(
                message=f"查询最近新闻失败: {exc}",
                code="MYSQL_QUERY_ERROR",
                suggestion="请检查 source_type 取值（如 hotlist_news / rss_feed）后重试",
            )
        return {"success": True, "total": len(rows), "items": rows}

    def search_sentiments(
        self,
        stock_code: Optional[str] = None,
        stock_name: Optional[str] = None,
        alert_level: Optional[str] = None,
        min_sentiment: Optional[float] = None,
        max_sentiment: Optional[float] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 20,
    ) -> Dict:
        """按条件检索情感分析结果（financial_sentiment，只读）。"""
        reader = self._get_reader()
        try:
            rows = reader.search_sentiments(
                stock_code=stock_code or None,
                stock_name=stock_name or None,
                alert_level=alert_level or None,
                min_sentiment=min_sentiment,
                max_sentiment=max_sentiment,
                start_date=_parse_iso_datetime(start_date, "start_date"),
                end_date=_parse_iso_datetime(end_date, "end_date"),
                limit=_clamp_limit(limit),
            )
        except MCPError:
            raise
        except Exception as exc:
            raise MCPError(
                message=f"查询 financial_sentiment 失败: {exc}",
                code="MYSQL_QUERY_ERROR",
                suggestion="请检查筛选参数（stock_code/alert_level/评分范围/时间范围）后重试",
            )
        return {"success": True, "total": len(rows), "items": rows}

    def top_stocks(self, limit: int = 5, horizon_days: int = 7) -> Dict:
        """统计近 N 天情感评分最正面的股票 TOP（financial_sentiment 聚合，只读）。"""
        reader = self._get_reader()
        try:
            horizon_days = int(horizon_days) if horizon_days else 7
            horizon_days = max(1, min(horizon_days, 365))
            rows = reader.top_stocks(
                limit=_clamp_limit(limit, default=5),
                horizon_days=horizon_days,
            )
        except MCPError:
            raise
        except Exception as exc:
            raise MCPError(
                message=f"统计 top_stocks 失败: {exc}",
                code="MYSQL_QUERY_ERROR",
                suggestion="请稍后重试或检查数据库连接",
            )
        return {
            "success": True,
            "horizon_days": horizon_days,
            "total": len(rows),
            "items": rows,
        }

    def get_sentiment_by_id(self, sentiment_id: int) -> Dict:
        """按 ID 获取单条情感分析详情（financial_sentiment，只读）。"""
        reader = self._get_reader()
        try:
            row = reader.get_sentiment_by_id(int(sentiment_id))
        except (TypeError, ValueError):
            raise MCPError(
                message=f"参数 sentiment_id 不是有效整数: {sentiment_id!r}",
                code="INVALID_PARAMETER",
            )
        except MCPError:
            raise
        except Exception as exc:
            raise MCPError(
                message=f"查询情感分析详情失败: {exc}",
                code="MYSQL_QUERY_ERROR",
            )
        if not row:
            return {
                "success": True,
                "found": False,
                "message": f"未找到 id={sentiment_id} 的情感分析记录",
            }
        return {"success": True, "found": True, "item": row}
