# coding=utf-8
"""雪球抓取 + MySQL 写入 + Sentiment 记录演示脚本

使用方式：
    D:/python/python.exe examples/xueqiu_mysql_pipeline_demo.py

说明：
- 先初始化 MySQL 数据库池
- 抓取雪球主页最新动态
- 转成 raw_data_feed 记录
- 生成一条示例 sentiment 结果并写入 financial_sentiment
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trendradar.crawler import XueqiuSeleniumFetcher
from trendradar.storage.mysql_env import conn_params_from_env
from trendradar.storage.mysql_pipeline import init_mysql_pipeline


def main():
    # 1) 初始化 MySQL 管道（MYSQL_* 环境变量优先）
    pipeline = init_mysql_pipeline(**conn_params_from_env())

    # 2) 创建抓取器
    fetcher = XueqiuSeleniumFetcher(
        target_url="https://xueqiu.com/",
        headless=True,
        wait_min=2,
        wait_max=5,
    )

    # 3) 抓取页面最新动态
    result = fetcher.fetch_and_store_latest_posts(
        target_url="https://xueqiu.com/",
        max_posts=5,
        source_name="雪球主页",
        source_id="xueqiu_home",
        mysql_pipeline=pipeline,
    )

    print("[抓取结果]", result)

    # 4) 仅做一个最小 sentiment 例子：如果抓到了内容，则插入 sentiment 记录
    if result.get("stored_count", 0) > 0:
        first_post = result["posts"][0]
        sentiment_ok = pipeline.process_ai_analysis_single(
            stock_name="AAPL",
            stock_code="AAPL",
            sentiment_score=0.72,
            alert_level="High",
            summary_event=f"雪球动态：{first_post.get('content', '')[:80]}",
            raw_data_id=1,
        )
        print("[sentiment] 写入结果:", sentiment_ok)

    stats = pipeline.get_stats()
    print("[stats]", stats)


if __name__ == "__main__":
    main()
