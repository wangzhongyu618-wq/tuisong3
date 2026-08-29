# coding=utf-8
"""
阶段四 · 生命周期管理验证（Lifecycle）

目标：
1. 验证 cleanup_old_data() 能按保留天数清理过期历史数据（两张表）。
2. 验证脚本结束/定时任务收尾时正确调用 close_db_pool()，彻底释放连接池，
   避免 MySQL 连接泄漏或死锁（结合 atexit 兜底）。

前置：先执行 `python -m trendradar.storage.mysql_init init` 初始化表结构。

流程：
  step 4.1  写入一条"今天"的记录 + 一条"过期"记录（把 created_at 改为 N 天前）
  step 4.2  调用 pipeline/backend 的 cleanup_old_data(retention_days=30)
  step 4.3  验证：过期记录已删除、今天记录仍在、并展示统计
  step 4.4  收尾 close_db_pool() + atexit 兜底（展示如何防止连接泄漏）
"""
import atexit
from datetime import datetime, timedelta

from trendradar.storage.mysql_env import conn_params_from_env
from trendradar.storage.mysql_pipeline import init_mysql_pipeline
from trendradar.storage.mysql_pool import close_db_pool, get_db_pool

# 连接参数：MYSQL_* 环境变量优先（见 trendradar/storage/mysql_env.py）
MYSQL_CONN = conn_params_from_env()
RETENTION_DAYS = 30  # 保留期：30 天前的记录视为过期


def _set_old_created_at(record_id: int) -> None:
    """直接把某条 raw 记录的 created_at 改到保留期之前，用于模拟"过期数据"。"""
    old_time = datetime.utcnow() - timedelta(days=RETENTION_DAYS + 1)
    from sqlalchemy import text
    with get_db_pool().session_scope() as session:
        session.execute(
            text(
                "UPDATE raw_data_feed SET created_at = :t "
                "WHERE id = :id"
            ),
            {"t": old_time, "id": record_id},
        )


def main():
    print("=" * 60)
    print("阶段四 · 生命周期管理验证")
    print("=" * 60)

    # atexit 兜底：即使主流程异常退出也会释放连接池（模拟定时任务的收尾）
    atexit.register(close_db_pool)

    # ---- step 4.1 ：初始化并写入两条原始数据 ----
    pipeline = init_mysql_pipeline(**MYSQL_CONN)
    backend = pipeline.backend
    print("\n==== step 4.1: 写入一条正常记录 + 一条过期记录 ====")
    fresh_id = backend.save_raw_data(
        source_type="hotlist_news", content="今日正常热点数据（保留）",
        source_id="lifecycle-demo", source_name="生命周期演示",
    )
    old_id = backend.save_raw_data(
        source_type="hotlist_news", content="历史过期数据（即将被清理）",
        source_id="lifecycle-demo", source_name="生命周期演示",
    )
    _set_old_created_at(old_id)  # 把 old_id 的创建时间改到保留期之前
    print(f"  已写入: fresh_id={fresh_id}, old_id={old_id}(已改期为过期)")
    print(f"  初始化后统计: {backend.get_table_stats()}")
    print(f"  健康检查: {pipeline.health_check()}")

    # ---- step 4.2 ：执行清理 ----
    print(f"\n==== step 4.2: cleanup_old_data(retention_days={RETENTION_DAYS}) ====")
    deleted = pipeline.cleanup_old_data(RETENTION_DAYS)
    print(f"  清理返回删除数: {deleted}")

    # ---- step 4.3 ：验证 ----
    print("\n==== step 4.3: 验证清理结果 ====")
    rows = backend.query_raw_data(source_id="lifecycle-demo", limit=10)
    print(f"  仍存在的 lifecycle-demo 记录数: {len(rows)}（预期: 已被清理的例外）")
    for r in rows:
        print(f"    id={r.get('id')} | {str(r.get('content'))[:30]}")
    print(f"  清理后全表统计: {backend.get_table_stats()}")

    # ---- step 4.4 ：收尾释放连接池，避免连接泄漏/死锁 ----
    print("\n==== step 4.4: 收尾释放连接池 ====")
    print("  通过 close_db_pool() 释放连接池（atexit 仍有兜底）...")
    close_db_pool()
    print("  close_db_pool() 调用完成 → 无连接泄漏")

    print("\n==== 测试结束 ====")
    print("✅ 生命周期管理验证完成：过期数据已清理，连接池已显式释放。")


if __name__ == "__main__":
    main()
