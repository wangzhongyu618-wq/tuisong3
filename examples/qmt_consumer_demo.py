# coding=utf-8
"""
QMT / xtquant 消费端对接模板（P1-④ 配套示例）。

对应规格：docs/QMT_CONSUMER_SPEC.md（§3.5 代码映射 / §5.1 水位增量 /
§6 消费端实现建议）。本文件**自包含**：仅依赖 pymysql 与标准库，
QMT 策略端可整文件拷入自身工程直接使用，无需安装 trendradar。

演示的三种只读读取（与规格 §5.1 的 SQL 一一对应）：
  1) pull_new_sentiments : 水位法增量拉取（幂等，重启续传）
  2) high_alerts         : 近 N 天 High 告警订阅
  3) entity_timeline     : 单实体情绪时间线

用法：
  python examples/qmt_consumer_demo.py                # 完整演示一轮
  python examples/qmt_consumer_demo.py --reset        # 重置水位后演示
  python examples/qmt_consumer_demo.py --code 300308  # 指定实体时间线

连接参数优先级：MYSQL_* 环境变量 > 文件内默认值（QMT 端可自行改为
只读账号，规格 §5.1 建议仅授 SELECT）。
"""
import argparse
import json
import os
import sys
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

try:
    import pymysql
    import pymysql.cursors
except ImportError:  # pragma: no cover - QMT 环境无 pymysql 时给出明确提示
    pymysql = None

# 连接参数（MYSQL_* 环境变量优先；与 trendradar/storage/mysql_env.py 键名一致）
DEFAULT_CONN = dict(
    host="localhost", port=3306, user="root",
    password="12345678", database="trendradar", charset="utf8mb4",
)
# 水位文件默认放 output/（运行时产物目录，不进版本库）
WATERMARK_FILENAME = os.path.join("output", "qmt_watermark.json")


def _conn_params() -> Dict[str, Any]:
    """MYSQL_* 环境变量覆盖默认连接参数（消费端也可直接硬编码）。"""
    params = dict(DEFAULT_CONN)

    def _env(key: str, key_of: str, cast=str) -> None:
        raw = os.getenv(key, "").strip()
        if raw:
            params[key_of] = cast(raw) if cast is not str else raw

    _env("MYSQL_HOST", "host")
    _env("MYSQL_PORT", "port", int)
    _env("MYSQL_USERNAME", "user")
    _env("MYSQL_PASSWORD", "password")
    _env("MYSQL_DATABASE", "database")
    return params


def connect(params: Optional[Dict[str, Any]] = None):
    """建立只读连接（DictCursor 让列名取值，贴近规格 §5.1 的 SELECT 列清单）。"""
    if pymysql is None:
        raise RuntimeError("缺少 pymysql：pip install pymysql")
    return pymysql.connect(
        cursorclass=pymysql.cursors.DictCursor, **(params or _conn_params())
    )


# ═══════════════════════════════════════════════════════════════════
# §3.5 证券代码格式：库内 6 位无后缀 → QMT/xtquant 带后缀代码
# （映射后请以 xtquant 官方代码规范核对）
# ═══════════════════════════════════════════════════════════════════

_SUFFIX_BY_PREFIX = {
    "6": ".SH",  # 沪 A / 科创
    "5": ".SH",  # 沪 ETF
    "0": ".SZ",  # 深 A
    "3": ".SZ",  # 创业
    "1": ".SZ",  # 深 ETF
    "4": ".BJ",  # 北交所
    "8": ".BJ",  # 北交所
}


def to_qmt_code(stock_code: Optional[str]) -> Optional[str]:
    """把库内 stock_code 转为 QMT 可用代码；无法映射返回 None。

    规则（docs/QMT_CONSUMER_SPEC.md §3.5）：
      - A股/ETF：6 位纯数字按首位映射后缀（6/5→.SH，0/3/1→.SZ，4/8→.BJ）
      - 美股：字母 ticker 原样返回（是否需后缀以 xtquant 规范为准）
      - UNKNOWN（或空/非法）：返回 None —— 消费端必须先按 stock_name
        走词表/人工映射（可参考 config/sector_mapping.yaml），严禁直接下单
      - 已带 .SH/.SZ/.BJ 后缀的原样返回（幂等）
    """
    if not stock_code:
        return None
    code = str(stock_code).strip().upper()
    if not code:
        return None
    if code.count(".") == 1 and code.split(".", 1)[1] in ("SH", "SZ", "BJ"):
        return code  # 已带后缀，幂等返回
    if code == "UNKNOWN":
        return None
    if code.isdigit():
        if len(code) != 6:
            return None
        suffix = _SUFFIX_BY_PREFIX.get(code[0])
        if not suffix:
            # 未列入 §3.5 映射表的前缀（如 B 股 9 开头）：拒绝半映射，
            # 交由上层按名称/官方代码表处理，严禁输出无后缀裸码
            return None
        return code + suffix
    # 美股等字母 ticker：保持原样（xtquant 是否需要后缀由券商配置决定）
    if code.isalpha():
        return code
    return None  # 其余杂格式一律拒绝，交由上层按名称处理


def qmt_code_or_name_hint(row: Dict[str, Any]) -> str:
    """演示用：UNKNOWN/无法映射的记录给出「按名称映射」提示（§6-4）。"""
    qmt = to_qmt_code(row.get("stock_code"))
    if qmt:
        return qmt
    name = (row.get("stock_name") or "").strip()
    return f"<UNKNOWN→按名称映射: {name or '?'}>"


# ═══════════════════════════════════════════════════════════════════
# §6-1 水位持久化：last_watermark_id 落盘 JSON，进程重启续传
# ═══════════════════════════════════════════════════════════════════


def load_watermark(path: str) -> int:
    """读取上次消费水位；文件缺失/损坏按 0 处理（全量补拉，幂等安全）。"""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        value = int(data.get("last_watermark_id", 0))
        return value if value > 0 else 0
    except (OSError, ValueError, TypeError):
        return 0


def save_watermark(path: str, last_id: int) -> None:
    """原子落盘水位（先写临时文件再替换，避免中途崩溃留半截 JSON）。"""
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"last_watermark_id": int(last_id)}, fh)
    os.replace(tmp, path)


# ═══════════════════════════════════════════════════════════════════
# 读取一（§5.1 推荐通道）：水位法增量拉取
# ═══════════════════════════════════════════════════════════════════

_SQL_INCREMENTAL = (
    "SELECT id, stock_name, stock_code, sentiment_score, alert_level, "
    "summary_event, event_hash, raw_data_id, analysis_metadata, created_at "
    "FROM financial_sentiment "
    "WHERE id > %s ORDER BY id ASC LIMIT %s"
)


def pull_new_sentiments(
    conn, watermark_file: str, limit: int = 500
) -> Tuple[List[Dict[str, Any]], int]:
    """拉取水位之后的新情感事件，并把水位推进到本批最大 id。

    水位法幂等：重复轮询无副作用；库内 uq_sentiment_event（P1-⑤）已保证
    同 (实体, 事件文本) 只有一条，消费端无需二次判重。

    Returns:
        (rows, new_watermark) —— rows 为本批新事件；无新行时 new_watermark
        保持原值。
    """
    last_id = load_watermark(watermark_file)
    with conn.cursor() as cursor:
        cursor.execute(_SQL_INCREMENTAL, (last_id, int(limit)))
        rows = cursor.fetchall() or []
    if rows:
        new_watermark = max(int(r["id"]) for r in rows)
        save_watermark(watermark_file, new_watermark)
        last_id = new_watermark
    return rows, last_id


# ═══════════════════════════════════════════════════════════════════
# 读取二（§5.1）：High 告警订阅（走 idx_alert_level_created）
# ═══════════════════════════════════════════════════════════════════

_SQL_HIGH_ALERTS = (
    "SELECT fs.id, fs.stock_code, fs.stock_name, fs.sentiment_score, "
    "fs.summary_event, fs.created_at, "
    "JSON_UNQUOTE(JSON_EXTRACT(fs.analysis_metadata, '$.context')) AS context, "
    "rd.content AS source_content, rd.url "
    "FROM financial_sentiment fs "
    "LEFT JOIN raw_data_feed rd ON rd.id = fs.raw_data_id "
    "WHERE fs.alert_level = %s "
    "AND fs.created_at >= UTC_TIMESTAMP() - INTERVAL %s DAY "
    "ORDER BY fs.created_at DESC LIMIT %s"
)


def high_alerts(conn, days: int = 1, limit: int = 50) -> List[Dict[str, Any]]:
    """近 N 天 High 告警（含原文溯源 JOIN）；时间比较用 UTC_TIMESTAMP()（§3.6）。"""
    with conn.cursor() as cursor:
        cursor.execute(_SQL_HIGH_ALERTS, ("High", int(days), int(limit)))
        return cursor.fetchall() or []


# ═══════════════════════════════════════════════════════════════════
# 读取三（§5.1）：实体情绪时间线（走 idx_stock_code_created）
# ═══════════════════════════════════════════════════════════════════

_SQL_ENTITY_TIMELINE = (
    "SELECT stock_code, stock_name, sentiment_score, summary_event, created_at "
    "FROM financial_sentiment "
    "WHERE stock_code = %s ORDER BY created_at DESC LIMIT %s"
)


def entity_timeline(conn, stock_code: str, limit: int = 50) -> List[Dict[str, Any]]:
    """单实体（库内 6 位无后缀代码）的最近情绪事件时间线。"""
    with conn.cursor() as cursor:
        cursor.execute(_SQL_ENTITY_TIMELINE, (stock_code, int(limit)))
        return cursor.fetchall() or []


# ═══════════════════════════════════════════════════════════════════
# 展示辅助（§3.6 时区 / §6-4 解析防御）
# ═══════════════════════════════════════════════════════════════════


def utc_to_beijing_str(value) -> str:
    """UTC 裸时间 → 北京时间字符串展示（库内 DATETIME 无时区，按 UTC+8 换算）。"""
    if value is None:
        return "-"
    try:
        return (value + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
    except TypeError:
        return str(value)


def parse_metadata(value) -> Optional[Dict[str, Any]]:
    """analysis_metadata 解析防御：TEXT 存 JSON，解析失败按 None（§6-4）。"""
    if not value:
        return None
    if isinstance(value, dict):
        return value
    try:
        data = json.loads(value)
        return data if isinstance(data, dict) else None
    except (ValueError, TypeError):
        return None


# ═══════════════════════════════════════════════════════════════════
# 演示主流程
# ═══════════════════════════════════════════════════════════════════


def _print_event(row: Dict[str, Any]) -> None:
    meta = parse_metadata(row.get("analysis_metadata"))
    context = (meta or {}).get("context") or (row.get("summary_event") or "")[:60]
    print(
        f"  #{row['id']} [{row.get('alert_level')}] "
        f"{qmt_code_or_name_hint(row)} "
        f"score={row.get('sentiment_score')} "
        f"bj_time={utc_to_beijing_str(row.get('created_at'))}\n"
        f"      event: {context}\n"
        f"      event_hash: {row.get('event_hash') or '(NULL, 不参与判重)'}"
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="QMT 消费端对接演示（只读）")
    parser.add_argument("--reset", action="store_true", help="重置水位后演示")
    parser.add_argument("--code", default="300308", help="实体时间线演示代码")
    parser.add_argument(
        "--watermark-file", default=WATERMARK_FILENAME,
        help=f"水位文件路径（默认 {WATERMARK_FILENAME}）",
    )
    args = parser.parse_args(argv)

    watermark_file = args.watermark_file
    if args.reset and os.path.exists(watermark_file):
        os.remove(watermark_file)
        print(f"[水位] 已重置: {watermark_file}")

    print("=" * 60)
    print("QMT 消费端对接演示（只读，docs/QMT_CONSUMER_SPEC.md）")
    print("=" * 60)

    conn = connect()
    try:
        # 1) 水位法增量拉取（§5.1 通道一，QMT 策略端推荐）
        rows, watermark = pull_new_sentiments(conn, watermark_file)
        print(f"\n[1] 水位增量拉取: 本次新事件 {len(rows)} 条, "
              f"当前水位 id={watermark} (文件: {watermark_file})")
        for row in rows[:10]:
            _print_event(row)
        if len(rows) > 10:
            print(f"  ... 其余 {len(rows) - 10} 条略")

        # 2) High 告警订阅（§5.1）
        alerts = high_alerts(conn, days=1, limit=10)
        print(f"\n[2] 近 1 天 High 告警: {len(alerts)} 条")
        for row in alerts:
            print(
                f"  #{row['id']} {qmt_code_or_name_hint(row)} "
                f"score={row.get('sentiment_score')} "
                f"context={(row.get('context') or '')[:60]}"
            )

        # 3) 实体情绪时间线（§5.1）
        timeline = entity_timeline(conn, args.code, limit=5)
        print(f"\n[3] 实体时间线 {args.code}: 最近 {len(timeline)} 条")
        for row in timeline:
            print(
                f"  {utc_to_beijing_str(row.get('created_at'))} "
                f"score={row.get('sentiment_score')} "
                f"{(row.get('summary_event') or '')[:50]}"
            )
    finally:
        conn.close()

    print("\n提示: 再次运行本脚本——水位法保证增量幂等，已消费事件不会重复拉取。")
    print("提醒: 情感数据仅作参考信号（LLM 提取有非确定性），下单前请叠加行情侧确认；")
    print("      本数据不构成投资建议（规格 §6-5）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
