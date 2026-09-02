# coding=utf-8
"""
调度系统启用与解析行为测试（P1-③）

覆盖：
- 真实 config.yaml：schedule.enabled 必须为 True、preset 为 morning_evening
  （防漂移核心断言：调度开关一旦回退，测试立即失败）
- 真实 timeline.yaml：morning_evening 预设结构完整
  （default 全天推送 / evening_summary 晚间汇总窗口 / day_plans / week_map 全周覆盖）
- Scheduler.resolve() 行为（注入固定时间，不依赖真实时钟）：
  * 白天 → default：采集/分析/推送全开，report_mode=current，不限次
  * 晚间窗口 → evening_summary：report_mode=daily、ai_mode=daily、once 去重生效
  * 半开区间边界：命中 start、不命中 start-1min、命中 end-1min、不命中 end
  * 周末：week_map 覆盖 1-7，周六同样命中晚间汇总
- once 去重往返：record_execution → already_executed（按 date/period/action 三元组隔离）
- 调度关闭：resolve() 回退 config.yaml 的 report.mode（fallback_report_mode）
- loader 层：SCHEDULE_ENABLED / SCHEDULE_PRESET 环境变量覆盖 + load_config 全链路
"""

from datetime import datetime

import pytest
import yaml

from trendradar.core.loader import _load_schedule_config, load_config
from trendradar.core.scheduler import Scheduler

CONFIG_FILE = "config/config.yaml"
TIMELINE_FILE = "config/timeline.yaml"

# 固定基准日期：2026-08-31 是周一（isoweekday=1），周六为其 +5 天
MONDAY = datetime(2026, 8, 31)
SATURDAY = datetime(2026, 9, 5)


class _FakeStorage:
    """内存版时间段执行记录（只实现 Scheduler 用到的两个存储接口）"""

    def __init__(self):
        self._records = set()

    def has_period_executed(self, date_str, period_key, action):
        return (date_str, period_key, action) in self._records

    def record_period_execution(self, date_str, period_key, action):
        self._records.add((date_str, period_key, action))


def _make_scheduler(timeline_data, fixed_dt, enabled=True, storage=None):
    """构造注入固定时间的调度器（fallback_report_mode 对应 config.yaml report.mode）"""
    return Scheduler(
        schedule_config={"enabled": enabled, "preset": "morning_evening"},
        timeline_data=timeline_data,
        storage_backend=storage if storage is not None else _FakeStorage(),
        get_time_func=lambda: fixed_dt,
        fallback_report_mode="incremental",
    )


def _hhmm_to_dt(base_dt: datetime, hhmm: str) -> datetime:
    """把 HH:MM 应用到基准日期上"""
    h, m = map(int, hhmm.split(":"))
    return base_dt.replace(hour=h, minute=m)


def _shift_hhmm(hhmm: str, minutes: int) -> str:
    """HH:MM 偏移若干分钟（本文件仅用于 ±1 分钟，不处理跨日）"""
    h, m = map(int, hhmm.split(":"))
    total = h * 60 + m + minutes
    return f"{total // 60:02d}:{total % 60:02d}"


@pytest.fixture(scope="module")
def raw_config():
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def schedule_cfg(raw_config):
    return raw_config["schedule"]


@pytest.fixture(scope="module")
def timeline_data():
    with open(TIMELINE_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def morning_evening(timeline_data):
    return timeline_data["presets"]["morning_evening"]


# ──────────────────────────────────────────────────────────────
# 1. 真实 config.yaml / timeline.yaml 防漂移
# ──────────────────────────────────────────────────────────────

def test_schedule_enabled_in_config(schedule_cfg):
    """P1-③ 核心验收：调度系统已启用（禁止回退为 false）"""
    assert schedule_cfg["enabled"] is True


def test_schedule_preset_is_morning_evening(schedule_cfg):
    """预设模板为早晚汇总"""
    assert schedule_cfg["preset"] == "morning_evening"


def test_morning_evening_structure(morning_evening):
    """morning_evening 预设结构完整：default 全天推送 + evening_summary 晚间窗口"""
    # 默认配置：全天采集/分析/推送当前榜单，不限次
    default = morning_evening["default"]
    assert default["collect"] is True
    assert default["analyze"] is True
    assert default["push"] is True
    assert default["report_mode"] == "current"
    assert default["once"]["analyze"] is False
    assert default["once"]["push"] is False

    # 晚间汇总窗口：当日汇总 + 仅一次
    period = morning_evening["periods"]["evening_summary"]
    assert period["start"] == "20:00"
    assert period["end"] == "22:00"
    assert period["analyze"] is True
    assert period["report_mode"] == "daily"
    assert period["ai_mode"] == "daily"
    assert period["once"]["analyze"] is True
    assert period["once"]["push"] is True

    # 日计划与周映射：全周 7 天都使用同一日计划
    assert morning_evening["day_plans"]["all_day"]["periods"] == ["evening_summary"]
    assert set(morning_evening["week_map"].keys()) == {1, 2, 3, 4, 5, 6, 7}
    assert set(morning_evening["week_map"].values()) == {"all_day"}


def test_report_mode_fallback_value(raw_config):
    """调度关闭时的回退报告模式（incremental），供 disabled 用例对照"""
    assert raw_config["report"]["mode"] == "incremental"


# ──────────────────────────────────────────────────────────────
# 2. resolve() 行为：白天默认 / 晚间窗口 / 边界 / 周末
# ──────────────────────────────────────────────────────────────

def test_resolve_daytime_uses_default(timeline_data):
    """周一白天：走 default 配置，全开且不限次"""
    resolved = _make_scheduler(timeline_data, _hhmm_to_dt(MONDAY, "10:00")).resolve()

    assert resolved.period_key is None
    assert resolved.period_name is None
    assert resolved.day_plan == "all_day"
    assert resolved.collect is True
    assert resolved.analyze is True
    assert resolved.push is True
    assert resolved.report_mode == "current"
    assert resolved.ai_mode == "current"
    assert resolved.once_analyze is False
    assert resolved.once_push is False


def test_resolve_evening_summary_window(timeline_data):
    """周一晚间窗口内：命中 evening_summary，切换为当日汇总且仅一次"""
    resolved = _make_scheduler(timeline_data, _hhmm_to_dt(MONDAY, "20:30")).resolve()

    assert resolved.period_key == "evening_summary"
    assert resolved.period_name == "晚间汇总"
    assert resolved.day_plan == "all_day"
    assert resolved.collect is True
    assert resolved.analyze is True
    assert resolved.push is True
    assert resolved.report_mode == "daily"
    assert resolved.ai_mode == "daily"
    assert resolved.once_analyze is True
    assert resolved.once_push is True


def test_window_boundaries_half_open(timeline_data, morning_evening):
    """窗口为半开区间 [start, end)：命中 start / 不命中 start-1min /
    命中 end-1min / 不命中 end"""
    period = morning_evening["periods"]["evening_summary"]
    cases = [
        (period["start"], True),                    # 20:00 命中
        (_shift_hhmm(period["start"], -1), False),  # 19:59 不命中
        (_shift_hhmm(period["end"], -1), True),     # 21:59 命中
        (period["end"], False),                     # 22:00 不命中
    ]
    for hhmm, expect_hit in cases:
        resolved = _make_scheduler(
            timeline_data, _hhmm_to_dt(MONDAY, hhmm)
        ).resolve()
        assert (resolved.period_key == "evening_summary") is expect_hit, (
            f"{hhmm} 应{'命中' if expect_hit else '不命中'}晚间汇总窗口"
        )


def test_weekend_uses_same_day_plan(timeline_data):
    """周六：week_map 覆盖 7 天，晚间汇总同样生效"""
    resolved = _make_scheduler(timeline_data, _hhmm_to_dt(SATURDAY, "20:30")).resolve()

    assert resolved.period_key == "evening_summary"
    assert resolved.day_plan == "all_day"


def test_once_dedup_roundtrip(timeline_data):
    """record_execution 后 already_executed 为 True；date/period/action 三元组隔离"""
    storage = _FakeStorage()
    scheduler = _make_scheduler(
        timeline_data, _hhmm_to_dt(MONDAY, "20:30"), storage=storage
    )

    assert scheduler.already_executed("evening_summary", "push", "2026-08-31") is False

    scheduler.record_execution("evening_summary", "push", "2026-08-31")

    assert scheduler.already_executed("evening_summary", "push", "2026-08-31") is True
    # 同 period 同日不同 action → 未执行
    assert scheduler.already_executed("evening_summary", "analyze", "2026-08-31") is False
    # 同 action 不同日 → 未执行
    assert scheduler.already_executed("evening_summary", "push", "2026-09-01") is False


# ──────────────────────────────────────────────────────────────
# 3. 调度关闭时的回退行为
# ──────────────────────────────────────────────────────────────

def test_disabled_falls_back_to_report_mode(timeline_data):
    """enabled=False：返回默认全功能配置，report_mode 回退 config.yaml 的 report.mode"""
    resolved = _make_scheduler(
        timeline_data, _hhmm_to_dt(MONDAY, "20:30"), enabled=False
    ).resolve()

    assert resolved.period_key is None
    assert resolved.day_plan == "disabled"
    assert resolved.collect is True
    assert resolved.analyze is True
    assert resolved.push is True
    assert resolved.report_mode == "incremental"   # 来自 fallback_report_mode
    assert resolved.ai_mode == "follow_report"
    assert resolved.once_analyze is False
    assert resolved.once_push is False


# ──────────────────────────────────────────────────────────────
# 4. loader 层：环境变量覆盖 + load_config 全链路
# ──────────────────────────────────────────────────────────────

def test_loader_env_override(monkeypatch):
    """SCHEDULE_ENABLED / SCHEDULE_PRESET 环境变量优先于 yaml 值"""
    yaml_schedule = {"enabled": True, "preset": "morning_evening"}

    monkeypatch.setenv("SCHEDULE_ENABLED", "false")
    monkeypatch.setenv("SCHEDULE_PRESET", "always_on")
    cfg = _load_schedule_config({"schedule": yaml_schedule})
    assert cfg["enabled"] is False
    assert cfg["preset"] == "always_on"

    monkeypatch.delenv("SCHEDULE_ENABLED", raising=False)
    monkeypatch.delenv("SCHEDULE_PRESET", raising=False)
    cfg = _load_schedule_config({"schedule": yaml_schedule})
    assert cfg["enabled"] is True
    assert cfg["preset"] == "morning_evening"


def test_load_config_end_to_end(monkeypatch):
    """全链路：load_config() 读真实 config.yaml + timeline.yaml，
    SCHEDULE.enabled=True 且 _TIMELINE_DATA 含 morning_evening 预设"""
    monkeypatch.delenv("SCHEDULE_ENABLED", raising=False)
    monkeypatch.delenv("SCHEDULE_PRESET", raising=False)

    config = load_config()

    assert config["SCHEDULE"] == {"enabled": True, "preset": "morning_evening"}
    assert "morning_evening" in config["_TIMELINE_DATA"]["presets"]


