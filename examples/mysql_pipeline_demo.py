# coding=utf-8
"""
阶段三 · 步骤3 —— 真实业务流接入与容错验证 (Integration Test)

用途：不依赖真实网络抓取，而是模拟真实数据流转的三路业务(热榜 / RSS / AI分析)，
通过 MySQLDataPipeline 统一入库，并专门构造"脏数据"验证逐条容错：
即使个别条目缺失字段 / 情绪分越界 / 告警级非法 / 含异常字符，也不会导致整个定时管道崩溃。

覆盖要点：
1. ingest_crawled_news  -> raw_data_feed (source_type=hotlist_news)
2. ingest_rss_feed      -> raw_data_feed (source_type=rss_feed)
3. process_ai_analysis  -> financial_sentiment (entities)
4. 容错验证：构造 None/越界/非法/超长字段，观察单条被跳过、其余正常入库
5. 健康检查 + 表统计

使用方式：
    1. 先初始化表结构：python -m trendradar.storage.mysql_init init
    2. 运行：python examples/mysql_pipeline_demo.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trendradar.storage.mysql_env import conn_params_from_env
from trendradar.storage.mysql_pipeline import init_mysql_pipeline

# 连接参数：MYSQL_* 环境变量优先（见 trendradar/storage/mysql_env.py）
MYSQL_CONN = conn_params_from_env()


def demo_hotlist(pipeline) -> int:
    """真实业务流 1：热榜抓取数据入库。"""
    print("\n==== 步骤 3.1: 热榜数据入库 ingest_crawled_news ====")
    items = [
        {
            "title": "🚀 财联社：降准政策落地，市场普涨。",
            "url": "https://cls.cn/news/1001",
            "rank": 1,
            "ranks": [1, 2, 1],
            "crawl_time": "09:30",
            "rank_timeline": [{"time": "09:00", "rank": 2}, {"time": "09:30", "rank": 1}],
        },
        {
            "title": "📉 微博热榜：新能源板块震荡加剧。",
            "url": "https://weibo.com/2002",
            "rank": 3,
            "ranks": [3, 5, 4],
            "crawl_time": "09:30",
            "rank_timeline": [{"time": "09:00", "rank": 5}, {"time": "09:30", "rank": 3}],
        },
    ]
    count = pipeline.ingest_crawled_news(items, source_id="cls-hot", source_name="财联社")
    print(f"[结果] 热榜入库 {count} 条")
    return count

def demo_rss(pipeline) -> int:
    """真实业务流 2：RSS 抓取数据入库。"""
    print("\n==== 步骤 3.2: RSS 数据入库 ingest_rss_feed ====")
    items = [
        {
            "title": "NVIDIA 数据中心营收创新高，分析师上调目标价。",
            "url": "https://example.com/rss/nvda",
            "summary": "受 AI 需求驱动，英伟达数据中心业务持续高增。",
            "published_at": "2026-08-20T08:00:00",
            "guid": "rss-1",
            "author": "Reuters",
        },
    ]
    count = pipeline.ingest_rss_feed(items, feed_id="example-finance", feed_name="财经资讯")
    print(f"[结果] RSS 入库 {count} 条")
    return count


def demo_ai_sentiment(pipeline) -> int:
    """真实业务流 3：AI 分析后的结构化股票实体入库。"""
    print("\n==== 步骤 3.3: AI 分析结果入库 process_ai_analysis ====")
    analysis_result = {
        "entities": [
            {
                "type": "STOCK",
                "name": "NVIDIA",
                "code": "NVDA",
                "sentiment_score": 0.82,
                "alert_level": "High",
                "event_summary": "AI 芯片需求强劲，市场情绪正面。",
                "confidence": 0.9,
                "context": "数据中心业务高增",
            },
            {
                "type": "STOCK",
                "name": "苹果",
                "code": "AAPL",
                "sentiment_score": -0.15,
                "alert_level": "Low",
                "event_summary": "新品发布偏中性。",
                "context": "消费电子需求平稳",
            },
        ]
    }
    count = pipeline.process_ai_analysis(analysis_result)
    print(f"[结果] AI 情感分析入库 {count} 条")
    return count


def demo_fault_tolerance(pipeline):
    """容错验证：刻意注入多种脏数据，验证单条失败不拖垮整批。"""
    print("\n==== 步骤 3.4: 容错验证（脏数据逐条隔离） ====")

    # 脏数据 1：content 为 None、source_id 为空（触发跳过/归一）
    # 脏数据 2：content 含孤立 Surrogate 代理项 \ud800（正常 utf8mb4 会编码失败）
    # 脏数据 3：正常数据（应正常入库）
    dirty_items = [
        {
            "title": None,
            "url": "https://x.com/bad",
            "rank": 9,
            "ranks": [],
            "crawl_time": "09:31",
            "rank_timeline": [],
        },
        {
            "title": "含孤立代理项 \ud800 的特殊文本（应被清洗替换为 U+FFFD）",
            "url": "https://x.com/surrogate",
            "rank": 2,
            "ranks": [2],
            "crawl_time": "09:31",
            "rank_timeline": [],
        },
        {
            "title": "✅ 正常热点：宏观数据超预期。",
            "url": "https://x.com/ok",
            "rank": 4,
            "ranks": [4],
            "crawl_time": "09:31",
            "rank_timeline": [],
        },
    ]
    count = pipeline.ingest_crawled_news(
        dirty_items, source_id="dirty-demo", source_name="容错演示"
    )
    print(f"[结果] 脏数据批量入库：共 {len(dirty_items)} 条，成功 {count} 条")
    print("       (预期：None 标题条被跳过、孤立代理项被清洗替换、正常条成功)")

    # 容错 2：sentiment_score 越界 / alert_level 非法 / 字段缺失
    dirty_sentiments = {
        "entities": [
            {
                "type": "STOCK",
                "name": "越界评分股",
                "code": "OVR",
                "sentiment_score": 99.9,   # 越界，会被裁剪到 [ -1, 1 ]
                "alert_level": "High",
                "event_summary": "评分越界测试",
            },
            {
                "type": "STOCK",
                "name": "非法告警级",
                "code": "BADLVL",
                "sentiment_score": 0.3,
                "alert_level": "SuperCritical",  # 非法，回退 Low
                "event_summary": "告警级非法测试",
            },
            {
                "type": "STOCK",               # 缺 name/code，走默认值
                "name": None,
                "code": None,
                "sentiment_score": "not-a-number",  # 转换失败回退 0.0
                "alert_level": None,
                "event_summary": None,
            },
        ]
    }
    scount = pipeline.process_ai_analysis(dirty_sentiments)
    print(f"[结果] 脏情感数据分析入库：共 3 条，成功 {scount} 条")
    return count, scount


def demo_health(pipeline):
    """健康检查与统计。"""
    print("\n==== 步骤 3.5: 健康检查与统计 ====")
    is_ok = pipeline.health_check()
    print(f"[健康] MySQL 连接: {'正常' if is_ok else '异常'}")
    try:
        stats = pipeline.get_stats()
        print(f"[统计] {stats}")
    except Exception as exc:  # pragma: no cover - 防御
        print(f"[统计] 获取统计失败: {exc}")


def main():
    print("=" * 60)
    print("阶段三 · 真实业务流接入与容错验证")
    print("=" * 60)

    pipeline = init_mysql_pipeline(**MYSQL_CONN)

    demo_hotlist(pipeline)
    demo_rss(pipeline)
    demo_ai_sentiment(pipeline)
    _, _ = demo_fault_tolerance(pipeline)
    demo_health(pipeline)

    print("\n==== 测试结束 ====")
    print("✅ 阶段三业务流与容错验证完成，见上方输出。")


if __name__ == "__main__":
    main()

