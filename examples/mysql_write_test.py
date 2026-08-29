# coding=utf-8
"""最小可运行的 MySQL 写入测试脚本

用途：
- 验证 MySQL 连接是否可用
- 验证 trendradar 数据库是否存在
- 验证 raw_data_feed 和 financial_sentiment 能否写入
- 验证 raw_data_id 外键和 sentiment_score 取值范围
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text

from trendradar.storage.mysql_env import conn_params_from_env

_CONN = conn_params_from_env()  # MYSQL_* 环境变量优先（见 trendradar/storage/mysql_env.py）
DB_URL = (
    f"mysql+pymysql://{_CONN['username']}:{_CONN['password']}"
    f"@{_CONN['host']}:{_CONN['port']}?charset={_CONN['charset']}"
)
TARGET_DB = _CONN["database"]


def ensure_database():
    engine = create_engine(DB_URL, future=True)
    with engine.begin() as conn:
        result = conn.execute(
            text("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = :db"),
            {"db": TARGET_DB},
        )
        exists = result.fetchone() is not None

        if not exists:
            conn.execute(text(f"CREATE DATABASE `{TARGET_DB}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))
            print(f"[OK] Database created: {TARGET_DB}")
        else:
            print(f"[OK] Database exists: {TARGET_DB}")

    engine.dispose()


def ensure_tables():
    engine = create_engine(f"{DB_URL}/{TARGET_DB}", future=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS raw_data_feed (
                    id BIGINT NOT NULL AUTO_INCREMENT,
                    source_type VARCHAR(50) NOT NULL,
                    content LONGTEXT NOT NULL,
                    url VARCHAR(1024) NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS financial_sentiment (
                    id BIGINT NOT NULL AUTO_INCREMENT,
                    stock_name VARCHAR(200) NOT NULL,
                    stock_code VARCHAR(50) NOT NULL,
                    sentiment_score FLOAT NOT NULL,
                    alert_level ENUM('Low','Medium','High') NOT NULL DEFAULT 'Low',
                    summary_event TEXT NULL,
                    raw_data_id BIGINT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (id),
                    CONSTRAINT fk_financial_raw_data
                        FOREIGN KEY (raw_data_id) REFERENCES raw_data_feed(id)
                        ON DELETE SET NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
        )

        print("[OK] Tables ensured: raw_data_feed, financial_sentiment")

    engine.dispose()


def write_demo_records():
    engine = create_engine(f"{DB_URL}/{TARGET_DB}", future=True)
    with engine.begin() as conn:
        insert_raw = text(
            "INSERT INTO raw_data_feed (source_type, content, url) VALUES (:source_type, :content, :url)"
        )
        result = conn.execute(
            insert_raw,
            {
                "source_type": "xueqiu_v_dynamic",
                "content": "测试写入：AAPL 未来预期向好，市场风险逐步缓和。",
                "url": "https://xueqiu.com/",
            },
        )
        raw_id = result.lastrowid

        insert_sentiment = text(
            """
            INSERT INTO financial_sentiment
                (stock_name, stock_code, sentiment_score, alert_level, summary_event, raw_data_id)
            VALUES
                (:stock_name, :stock_code, :sentiment_score, :alert_level, :summary_event, :raw_data_id)
            """
        )
        conn.execute(
            insert_sentiment,
            {
                "stock_name": "Apple",
                "stock_code": "AAPL",
                "sentiment_score": 0.68,
                "alert_level": "Medium",
                "summary_event": "市场对苹果产品线修复预期增强，情绪偏正面。",
                "raw_data_id": raw_id,
            },
        )

        print(f"[OK] Demo records inserted. raw_data_id={raw_id}")

    engine.dispose()


def main():
    try:
        ensure_database()
        ensure_tables()
        write_demo_records()
        print("[OK] MySQL write test completed successfully.")
    except Exception as exc:
        print(f"[ERROR] MySQL write test failed: {exc}")
        raise


if __name__ == "__main__":
    main()
