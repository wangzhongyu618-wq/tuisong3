# coding=utf-8
"""
阶段二 · 步骤2 —— Mock 单链路入库测试

用途：不经过复杂网络抓取，直接构造一条模拟热点数据，
通过 StorageManager.save() 统一存储接口写入刚建好的两张极简新表
（raw_data_feed / financial_sentiment），验证能否无报错完成入库。

覆盖要点：
1. storage_manager.save(news_data)      -> raw_data_feed（走 StorageManager 统一接口）
2. MySQLStorageBackend.save_financial_sentiment(...) -> financial_sentiment
3. Emoji 与特殊字符写入（MySQL utf8mb4 支持）
4. 回读验证并打印统计

使用方式：
    1. 先初始化表结构（阶段一）：
       python -m trendradar.storage.mysql_init init
       python -m trendradar.storage.mysql_init verify
    2. 运行本脚本：
       python examples/mysql_mock_save_demo.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trendradar.storage import (
    get_storage_manager,
    init_db_pool,
    MySQLStorageBackend,
)
from trendradar.storage.base import NewsData, NewsItem

from trendradar.storage.mysql_env import conn_params_from_env

# MySQL 连接参数：MYSQL_* 环境变量优先（见 trendradar/storage/mysql_env.py）
MYSQL_CONN = conn_params_from_env()


def build_mock_news_data() -> NewsData:
    """构造一条含 Emoji 与特殊字符的 Mock 热点数据（无需网络抓取）。"""
    mock_item = NewsItem(
        title="🌟 测试热点：英伟达发布新一代 AI 芯片，市场反应热烈！\n（含换行与引号\"、单引号'测试）",
        source_id="wallstreetcn-hot",
        source_name="华尔街见闻",
        rank=1,
        url="https://wallstreetcn.com/news/12345",
        crawl_time="09:30",
        ranks=[1, 2, 3],
        rank_timeline=[
            {"time": "09:00", "rank": 3},
            {"time": "09:30", "rank": 1},
        ],
    )
    return NewsData(
        date="2026-08-20",
        crawl_time="09:30",
        items={"wallstreetcn-hot": [mock_item]},
        id_to_name={"wallstreetcn-hot": "华尔街见闻"},
        failed_ids=[],
    )


def demo_save_news_data(sm) -> bool:
    """通过 StorageManager.save() 统一接口写入 raw_data_feed。"""
    print("\n==== 步骤 2.1: storage_manager.save(news_data) -> raw_data_feed ====")
    news_data = build_mock_news_data()
    try:
        ok = sm.save(news_data)
        print(f"[结果] save(news_data) 返回值: {ok}")
        return bool(ok)
    except Exception as exc:  # pragma: no cover
        print(f"[失败] save(news_data) 抛出异常: {exc}")
        return False


def demo_save_sentiment(raw_id: int) -> int:
    """写入一条模拟 AI 分析结果到 financial_sentiment。"""
    print("\n==== 步骤 2.2: save_financial_sentiment -> financial_sentiment ====")
    from trendradar.storage import get_db_pool
    backend = MySQLStorageBackend(get_db_pool())
    try:
        record_id = backend.save_financial_sentiment(
            stock_name="NVIDIA 英伟达 🚀",
            stock_code="NVDA",
            sentiment_score=0.85,
            alert_level="High",
            summary_event="新一代 AI 芯片发布，市场情绪偏正面。",
            raw_data_id=raw_id,
            analysis_metadata={"source": "mock_demo"},
        )
        print(f"[结果] financial_sentiment 写入成功, id={record_id}")
        return record_id
    except Exception as exc:  # pragma: no cover
        print(f"[失败] save_financial_sentiment 抛出异常: {exc}")
        return 0


def demo_readback():
    """回读验证：查询刚写入的数据。"""
    print("\n==== 步骤 2.3: 回读验证 ====")
    from trendradar.storage import get_db_pool
    backend = MySQLStorageBackend(get_db_pool())
    try:
        stats = backend.get_table_stats()
        print(f"[统计] 表行数: {stats}")

        recent = backend.query_raw_data(source_type="hotlist_news", limit=3)
        print(f"[回读] 最近 raw_data_feed ({len(recent)} 条):")
        for row in recent:
            print(
                f"   id={row.get('id')} | {row.get('source_id')} | "
                f"{str(row.get('content'))[:40]}"
            )
        return True
    except Exception as exc:  # pragma: no cover
        print(f"[失败] 回读验证异常: {exc}")
        return False


def main():
    print("=" * 60)
    print("阶段二 · Mock 单链路入库测试")
    print("=" * 60)

    # 使用 StorageManager 统一接口（backend_type="mysql" 时走 MySQL 适配层）
    sm = get_storage_manager(
        backend_type="mysql",
        mysql_conn_params=MYSQL_CONN,
    )

    # 显式初始化全局连接池（供下游 sentiment/backends 复用，避免顺序依赖）
    from trendradar.storage.mysql_pool import init_db_pool, close_db_pool
    init_db_pool(**MYSQL_CONN)

    try:
        ok_news = demo_save_news_data(sm)
    except Exception as exc:  # pragma: no cover - 连接异常时回退到裸后端重试
        print(f"[提示] StorageManager 路径异常: {exc}，改用 init_db_pool 裸后端")
        init_db_pool(**MYSQL_CONN)
        backend = MySQLStorageBackend()
        try:
            ok_news = True
            sm.save(build_mock_news_data())
        except Exception as exc2:  # pragma: no cover
            print(f"[失败] 裸后端写入也失败: {exc2}")
            ok_news = False

    if not ok_news:
        # 即使统一接口失败，也继续尝试第二张表验证，方便定位
        print("[提示] raw_data_feed 写入失败，继续尝试 financial_sentiment")

    # 写入第二张表（financial_sentiment）
    sentiment_id = demo_save_sentiment(raw_id=None)

    # 回读验证
    demo_readback()

    print("\n==== 测试结束 ====")
    if ok_news and sentiment_id:
        print("✅ 两张表均写入成功，Mock 单链路测试通过！")
    else:
        print("⚠️ 存在失败项，请检查上方输出与 MySQL 连接配置。")


if __name__ == "__main__":
    main()
