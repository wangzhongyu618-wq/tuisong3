# coding=utf-8
"""
MySQL 集成示例

演示如何将 MySQL 存储集成到 TrendRadar 的主流水线中
包括：
- 爬虫数据存储
- AI 分析结果存储
- 数据查询和统计

使用方式：
    python examples/mysql_integration_example.py
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def example_1_basic_setup():
    """示例 1: 基础设置 - 初始化数据库连接"""
    logger.info("=" * 60)
    logger.info("示例 1: 基础设置")
    logger.info("=" * 60)

    from trendradar.storage import init_db_pool, MySQLStorageBackend
    from trendradar.storage.mysql_env import conn_params_from_env

    # 初始化连接池（MYSQL_* 环境变量优先）
    db_pool = init_db_pool(
        **conn_params_from_env(),
        pool_size=10,
        max_overflow=20,
    )

    # 创建存储后端
    backend = MySQLStorageBackend(db_pool)

    # 健康检查
    if backend.health_check():
        logger.info("✓ MySQL 连接成功")
        stats = backend.get_table_stats()
        logger.info(f"表统计: raw_data_feed={stats.get('raw_data_feed', 0)}, "
                    f"financial_sentiment={stats.get('financial_sentiment', 0)}")
    else:
        logger.error("✗ MySQL 连接失败")
        return False

    return backend


def example_2_save_crawled_news(backend):
    """示例 2: 保存爬虫抓取的新闻数据"""
    logger.info("\n" + "=" * 60)
    logger.info("示例 2: 保存爬虫新闻数据")
    logger.info("=" * 60)

    # 模拟爬虫数据
    news_records = [
        {
            'source_type': 'hotlist_news',
            'content': 'AI 芯片新突破，性能提升 30%',
            'url': 'https://example.com/news/ai-chip-1',
            'source_id': 'toutiao',
            'source_name': '头条新闻',
            'additional_data': {
                'rank': 1,
                'ranks': [5, 3, 1],
                'crawl_time': '14:30',
                'crawl_date': datetime.now().isoformat()
            }
        },
        {
            'source_type': 'hotlist_news',
            'content': '科技巨头争夺市场，投资并购频繁',
            'url': 'https://example.com/news/tech-merger-1',
            'source_id': 'baidu',
            'source_name': '百度热搜',
            'additional_data': {
                'rank': 2,
                'ranks': [10, 5, 2],
                'crawl_time': '14:30'
            }
        },
        {
            'source_type': 'hotlist_news',
            'content': '股市行情：沪深 300 上涨 2.5%',
            'url': 'https://example.com/news/stock-market-1',
            'source_id': 'weibo',
            'source_name': '微博热搜',
            'additional_data': {
                'rank': 3,
                'ranks': [15, 10, 3],
                'crawl_time': '14:30'
            }
        }
    ]

    # 批量保存
    count = backend.save_raw_data_batch(news_records)
    logger.info(f"✓ 已保存 {count} 条新闻数据")

    return news_records


def example_3_save_rss_data(backend):
    """示例 3: 保存 RSS 源数据"""
    logger.info("\n" + "=" * 60)
    logger.info("示例 3: 保存 RSS 数据")
    logger.info("=" * 60)

    rss_records = [
        {
            'source_type': 'rss_feed',
            'content': 'Breakthrough in Quantum Computing',
            'url': 'https://example.com/article/quantum-1',
            'source_id': 'hacker-news',
            'source_name': 'Hacker News',
            'additional_data': {
                'summary': 'Researchers at MIT announce major breakthrough...',
                'author': 'John Doe',
                'published_at': datetime.now().isoformat(),
                'guid': 'hn-12345'
            }
        },
        {
            'source_type': 'rss_feed',
            'content': 'Market Analysis: Tech Stocks Rally',
            'url': 'https://example.com/article/market-1',
            'source_id': 'reuters-tech',
            'source_name': 'Reuters Technology',
            'additional_data': {
                'summary': 'Technology stocks surge on positive earnings reports...',
                'author': 'Jane Smith',
                'published_at': datetime.now().isoformat(),
                'guid': 'reuters-12345'
            }
        }
    ]

    count = backend.save_raw_data_batch(rss_records)
    logger.info(f"✓ 已保存 {count} 条 RSS 数据")


def example_4_ai_analysis_results(backend):
    """示例 4: 保存 AI 分析结果"""
    logger.info("\n" + "=" * 60)
    logger.info("示例 4: 保存 AI 分析结果")
    logger.info("=" * 60)

    # 模拟 AI 分析结果（从 LLM 提取的结构化数据）
    analysis_results = [
        {
            'stock_name': 'Apple Inc.',
            'stock_code': 'AAPL',
            'sentiment_score': 0.85,
            'alert_level': 'High',
            'summary_event': 'AI 芯片新突破利好 Apple 股价，市场反应积极',
            'raw_data_id': None,  # 如果需要关联可设置
            'analysis_metadata': {
                'confidence': 0.92,
                'entity_type': 'STOCK',
                'source': 'multiple_news',
                'keywords': ['AI', '芯片', 'Apple', '性能提升']
            }
        },
        {
            'stock_name': 'Microsoft Corporation',
            'stock_code': 'MSFT',
            'sentiment_score': 0.65,
            'alert_level': 'Medium',
            'summary_event': '科技并购活动增加，Microsoft 投资意向明显',
            'raw_data_id': None,
            'analysis_metadata': {
                'confidence': 0.88,
                'entity_type': 'STOCK',
                'source': 'market_news',
                'keywords': ['并购', '投资', 'Microsoft']
            }
        },
        {
            'stock_name': 'NVIDIA Corporation',
            'stock_code': 'NVDA',
            'sentiment_score': 0.75,
            'alert_level': 'High',
            'summary_event': 'AI 芯片需求旺盛，NVIDIA 受益明显',
            'raw_data_id': None,
            'analysis_metadata': {
                'confidence': 0.90,
                'entity_type': 'STOCK',
                'source': 'tech_analysis',
                'keywords': ['AI', '芯片', 'GPU', 'NVIDIA']
            }
        }
    ]

    count = backend.save_financial_sentiment_batch(analysis_results)
    logger.info(f"✓ 已保存 {count} 条情感分析结果")


def example_5_query_operations(backend):
    """示例 5: 数据查询操作"""
    logger.info("\n" + "=" * 60)
    logger.info("示例 5: 数据查询")
    logger.info("=" * 60)

    # 查询原始数据
    logger.info("\n[查询] 最近的新闻数据（来源：toutiao）")
    news = backend.query_raw_data(source_id="toutiao", limit=10)
    for item in news[:3]:
        logger.info(f"  - {item['content'][:50]}... (URL: {item['url']})")

    # 查询高告警级别的情感分析
    logger.info("\n[查询] 高告警级别的股票分析（alert_level=High）")
    high_alerts = backend.query_financial_sentiment(alert_level="High", limit=10)
    for item in high_alerts[:5]:
        logger.info(
            f"  - {item['stock_code']}: {item['sentiment_score']:.2f} "
            f"({item['alert_level']}) - {item['summary_event'][:40]}..."
        )

    # 查询特定股票的情感趋势
    logger.info("\n[查询] AAPL 股票的情感分析记录")
    aapl_sentiments = backend.query_financial_sentiment(stock_code="AAPL", limit=10)
    if aapl_sentiments:
        avg_sentiment = sum(s['sentiment_score'] for s in aapl_sentiments) / len(aapl_sentiments)
        logger.info(f"  - 记录数: {len(aapl_sentiments)}")
        logger.info(f"  - 平均情感分: {avg_sentiment:.2f}")
        logger.info(f"  - 最新分析: {aapl_sentiments[0]['summary_event']}")

    # 查询正面评价的股票
    logger.info("\n[查询] 正面评价的股票（sentiment_score > 0.5）")
    positive = backend.query_financial_sentiment(min_sentiment=0.5, limit=10)
    logger.info(f"  - 找到 {len(positive)} 条正面评价")
    for item in positive[:3]:
        logger.info(f"    {item['stock_code']}: {item['sentiment_score']:.2f}")


def example_6_using_pipeline(backend=None):
    """示例 6: 使用 MySQL 数据管道（高级）"""
    logger.info("\n" + "=" * 60)
    logger.info("示例 6: MySQL 数据管道")
    logger.info("=" * 60)

    from trendradar.storage.mysql_env import conn_params_from_env
    from trendradar.storage.mysql_pipeline import init_mysql_pipeline

    # 初始化管道（MYSQL_* 环境变量优先）
    pipeline = init_mysql_pipeline(**conn_params_from_env())

    # 使用管道处理爬虫数据
    logger.info("\n[管道] 处理爬虫数据...")
    crawled_news = [
        {'title': '新闻标题 1', 'url': 'https://example.com/1', 'rank': 1},
        {'title': '新闻标题 2', 'url': 'https://example.com/2', 'rank': 2},
    ]
    count = pipeline.ingest_crawled_news(crawled_news, "toutiao", "头条")
    logger.info(f"✓ 已处理 {count} 条新闻")

    # 使用管道处理 RSS 数据
    logger.info("\n[管道] 处理 RSS 数据...")
    rss_items = [
        {'title': 'RSS 文章 1', 'url': 'https://example.com/a1', 'summary': '摘要 1'},
    ]
    count = pipeline.ingest_rss_feed(rss_items, "hacker-news", "Hacker News")
    logger.info(f"✓ 已处理 {count} 条 RSS")

    # 获取统计信息
    logger.info("\n[管道] 数据库统计信息...")
    stats = pipeline.get_stats()
    logger.info(f"✓ 统计结果: {stats}")


def example_7_error_handling(backend):
    """示例 7: 错误处理和异常恢复"""
    logger.info("\n" + "=" * 60)
    logger.info("示例 7: 错误处理")
    logger.info("=" * 60)

    # 测试无效的情感分数（应该被裁剪）
    logger.info("\n[测试] 无效情感分数处理...")
    invalid_sentiment_id = backend.save_financial_sentiment(
        stock_name="Test Corp",
        stock_code="TEST",
        sentiment_score=2.5,  # 超出范围，会被裁剪到 1.0
        alert_level="Medium",
        summary_event="测试数据"
    )
    if invalid_sentiment_id:
        result = backend.get_financial_sentiment(invalid_sentiment_id)
        logger.info(f"✓ 情感分数已裁剪: {result['sentiment_score']}")

    # 测试无效的告警级别
    logger.info("\n[测试] 无效告警级别处理...")
    invalid_alert_id = backend.save_financial_sentiment(
        stock_name="Test Corp 2",
        stock_code="TEST2",
        sentiment_score=0.5,
        alert_level="INVALID",  # 无效级别，会降级为 Low
        summary_event="测试数据"
    )
    if invalid_alert_id:
        result = backend.get_financial_sentiment(invalid_alert_id)
        logger.info(f"✓ 告警级别已修正: {result['alert_level']}")

    # 测试连接池重试
    logger.info("\n[测试] 连接池健康检查...")
    is_healthy = backend.health_check()
    logger.info(f"✓ 连接池状态: {'健康' if is_healthy else '异常'}")


def example_8_performance_test(backend):
    """示例 8: 性能测试"""
    logger.info("\n" + "=" * 60)
    logger.info("示例 8: 性能测试")
    logger.info("=" * 60)

    import time

    # 批量插入性能测试
    logger.info("\n[性能] 批量插入 1000 条原始数据...")
    start_time = time.time()

    records = [
        {
            'source_type': 'hotlist_news',
            'content': f'测试新闻 {i}',
            'url': f'https://example.com/test/{i}',
            'source_id': f'source_{i % 5}',
            'source_name': f'来源 {i % 5}',
            'additional_data': {'index': i}
        }
        for i in range(1000)
    ]

    count = backend.save_raw_data_batch(records)
    elapsed = time.time() - start_time

    logger.info(f"✓ 插入完成: {count} 条，耗时 {elapsed:.2f} 秒")
    logger.info(f"  - 平均速度: {count / elapsed:.0f} 条/秒")

    # 查询性能测试
    logger.info("\n[性能] 查询性能测试...")
    start_time = time.time()
    results = backend.query_raw_data(limit=1000)
    elapsed = time.time() - start_time
    logger.info(f"✓ 查询完成: {len(results)} 条，耗时 {elapsed:.3f} 秒")


def main():
    """主函数 - 运行所有示例"""
    try:
        # 示例 1: 基础设置
        backend = example_1_basic_setup()
        if not backend:
            logger.error("基础设置失败，退出")
            return

        # 示例 2: 保存爬虫数据
        example_2_save_crawled_news(backend)

        # 示例 3: 保存 RSS 数据
        example_3_save_rss_data(backend)

        # 示例 4: 保存 AI 分析结果
        example_4_ai_analysis_results(backend)

        # 示例 5: 查询操作
        example_5_query_operations(backend)

        # 示例 6: 数据管道
        example_6_using_pipeline(backend)

        # 示例 7: 错误处理
        example_7_error_handling(backend)

        # 示例 8: 性能测试
        example_8_performance_test(backend)

        logger.info("\n" + "=" * 60)
        logger.info("✓ 所有示例执行完成！")
        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"执行出错: {e}", exc_info=True)
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
