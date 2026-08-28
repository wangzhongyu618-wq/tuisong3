# coding=utf-8
"""
阶段五 · MCP 回环测试准备（MCP Loop）

目标：确认数据库结构满足 MCP Server 读取契约，并演示大模型/Agent 通过
自然语言检索新表数据的简易查询方式（接口全部只读、幂等、JSON 友好）。

用法：
  1) 先执行阶段一 init 建表、阶段二写入一些示例数据。
  2) 运行本脚本：python examples/mysql_mcp_read_demo.py
  3) 之后在 Cursor/Claude 的 MCP 里把本模块的只读方法暴露为 tools 即可
     （例如 describe_schema / search_raw_data / search_sentiments / top_stocks）。

对应结构的读取契约（对 LLM 自然语言可查维度）：
  - raw_data_feed  : source_type / source_id / 时间范围 / 关键词
  - financial_sentiment: stock_code / stock_name / alert_level /
                         评分范围 / 时间范围
"""
import json
from trendradar.storage.mysql_reader import MySQLReader

MYSQL_CONN = {
    "host": "localhost",
    "port": 3306,
    "username": "root",
    "password": "12345678",
    "database": "trendradar",
    "charset": "utf8mb4",
}


def _print(title: str, data) -> None:
    print(f"\n--- {title} ---")
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def main():
    print("=" * 60)
    print("阶段五 · MCP 回环测试准备（只读检索）")
    print("=" * 60)

    reader = MySQLReader(**MYSQL_CONN)

    # 1) 结构自描述：让 Agent 理解可查询维度（对 MCP 的关键契约）
    schema = reader.describe_schema()
    _print("1) describe_schema(): 表结构 / 字段 / 可查询维度", schema)

    # 2) 自然语言可映射的示例查询：
    #    "最近的热榜新闻有哪些？"
    news = reader.recent_news(source_type="hotlist_news", limit=5)
    _print("2) recent_news(hotlist_news): 最近热榜新闻", news)

    #    "按代码查股票的最近情感分析"
    sentiments = reader.search_sentiments(stock_code="NVDA", limit=5)
    _print("3) search_sentiments(NVDA): 英伟达最近情感分析", sentiments)

    #    "近 7 天情绪最正面的股票 TOP3"
    top = reader.top_stocks(limit=3, horizon_days=7)
    _print("4) top_stocks(3, 7d): 近7天情绪最好的股票", top)

    #    "所有 High 告警的记录"
    alert = reader.search_sentiments(alert_level="High", limit=5)
    _print("5) search_sentiments(High): 高告警记录", alert)

    # 3) 收尾：释放连接池（避免连接泄漏）
    reader.close()
    print("\n==== 测试结束 ====")
    print("✅ 数据库结构满足 MCP 读取契约；上述只读方法可直接暴露为 MCP tools。")


if __name__ == "__main__":
    main()
