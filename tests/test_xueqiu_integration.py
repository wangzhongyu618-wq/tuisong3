# coding=utf-8
"""
雪球大V动态主流程接线（P1-①）单元测试

覆盖：
- loader._load_xueqiu_config：默认值 / yaml 读取 / env 覆盖 /
  target_urls 字符串容错 / max_posts 非法值容错
- NewsAnalyzer._crawl_xueqiu_data：开关关闭 / 未配置 URL / 未配置 Cookie /
  正常抓取（mock fetcher，验证参数与帖子规范化）/ 单 URL 失败隔离
- NewsAnalyzer._build_xueqiu_rss_entry：无帖子 / 伪词组结构 /
  长文本截断 / 渲染必备字段（formatter 直接下标访问 time_display/count）
- _run_analysis_pipeline 合入：rss_items 尾部追加「雪球大V动态」词组，
  且不覆盖已有 RSS 词组
- dispatcher.translate_content：skip_translation 标记保留原文
"""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from trendradar import __main__ as main_module
from trendradar.__main__ import NewsAnalyzer
from trendradar.ai.translator import BatchTranslationResult, TranslationResult
from trendradar.core.loader import _load_xueqiu_config
from trendradar.notification.dispatcher import NotificationDispatcher


# ========================================
# 测试脚手架
# ========================================

class _FakePipeline:
    """最小 MySQL 管道替身：仅验证 _crawl_xueqiu_data 把它传给 fetcher"""

    def __init__(self):
        self.marker = "fake-pipeline"


class _FakeFetcher:
    """XueqiuSeleniumFetcher 替身：记录构造参数，返回固定帖子"""

    instances = []

    def __init__(self, target_url=None, headless=True, cookies="", **kwargs):
        self.target_url = target_url
        self.headless = headless
        self.cookies = cookies
        _FakeFetcher.instances.append(self)

    def fetch_and_store_latest_posts(self, target_url=None, max_posts=10,
                                     mysql_pipeline=None, **kwargs):
        assert mysql_pipeline is not None and mysql_pipeline.marker == "fake-pipeline"
        assert max_posts == 7
        return {
            "stored_count": 1,
            "posts": [
                {"content": "存储芯片涨价，关注下游模组厂", "published_at": "2026-08-31 10:00", "url": ""},
                {"content": "   ", "published_at": "", "url": ""},  # 空白帖应被过滤
            ],
            "source_id": "someone",
            "source_name": "雪球大V",
        }


class _ExplodingFetcher(_FakeFetcher):
    """第一个 URL 抛异常、其余正常的 fetcher 替身"""

    def fetch_and_store_latest_posts(self, target_url=None, max_posts=10,
                                     mysql_pipeline=None, **kwargs):
        if target_url == "https://xueqiu.com/bad":
            raise RuntimeError("浏览器启动失败")
        return {
            "stored_count": 1,
            "posts": [{"content": "正常帖", "published_at": "2026-08-31 11:00", "url": ""}],
            "source_id": "good",
            "source_name": "正常大V",
        }


def _bare_analyzer(config=None, posts=None) -> NewsAnalyzer:
    """跳过重初始化构造 NewsAnalyzer（__new__ + 手动挂最小依赖）"""
    analyzer = NewsAnalyzer.__new__(NewsAnalyzer)
    analyzer.ctx = SimpleNamespace(
        config=config if config is not None else {},
        format_time=lambda: "2026-08-31 12:00:00",
        get_mysql_pipeline=lambda: _FakePipeline(),
    )
    analyzer.filter_method = None  # 默认关键词匹配分支（与真实 __init__ 一致）
    analyzer._xueqiu_posts = posts if posts is not None else []
    return analyzer


def _xueqiu_config(enabled=True, urls=None, cookies="cookie123", headless=False,
                   max_posts=7):
    return {
        "XUEQIU": {
            "ENABLED": enabled,
            "COOKIES": cookies,
            "HEADLESS": headless,
            "TARGET_URLS": urls if urls is not None else ["https://xueqiu.com/someone"],
            "MAX_POSTS": max_posts,
        }
    }


@pytest.fixture(autouse=True)
def _reset_fake_fetcher():
    _FakeFetcher.instances = []
    yield
    _FakeFetcher.instances = []


# ========================================
# loader._load_xueqiu_config
# ========================================

class TestLoadXueqiuConfig:
    def test_defaults_when_section_missing(self):
        cfg = _load_xueqiu_config({})
        assert cfg == {
            "ENABLED": False,
            "COOKIES": "",
            "HEADLESS": True,
            "TARGET_URLS": [],
            "MAX_POSTS": 10,
            "EXECUTABLE_PATH": "",
            "INTERVAL_MINUTES": 120,
        }

    def test_yaml_values_read(self):
        cfg = _load_xueqiu_config({
            "xueqiu": {
                "enabled": True,
                "cookies": "abc",
                "headless": False,
                "target_urls": [" https://xueqiu.com/1 ", ""],
                "max_posts": 5,
            }
        })
        assert cfg["ENABLED"] is True
        assert cfg["COOKIES"] == "abc"
        assert cfg["HEADLESS"] is False
        assert cfg["TARGET_URLS"] == ["https://xueqiu.com/1"]  # strip + 空项过滤
        assert cfg["MAX_POSTS"] == 5

    def test_target_urls_string_tolerance(self):
        cfg = _load_xueqiu_config({"xueqiu": {"target_urls": "https://xueqiu.com/2"}})
        assert cfg["TARGET_URLS"] == ["https://xueqiu.com/2"]

        cfg = _load_xueqiu_config({"xueqiu": {"target_urls": "   "}})
        assert cfg["TARGET_URLS"] == []

    def test_env_overrides_yaml(self, monkeypatch):
        monkeypatch.setenv("XUEQIU_ENABLED", "false")
        monkeypatch.setenv("XUEQIU_COOKIES", "env-cookie")
        monkeypatch.setenv("XUEQIU_MAX_POSTS", "3")
        cfg = _load_xueqiu_config({
            "xueqiu": {"enabled": True, "cookies": "yaml-cookie", "max_posts": 99}
        })
        assert cfg["ENABLED"] is False
        assert cfg["COOKIES"] == "env-cookie"
        assert cfg["MAX_POSTS"] == 3

    def test_max_posts_invalid_falls_back(self, monkeypatch):
        monkeypatch.delenv("XUEQIU_MAX_POSTS", raising=False)
        cfg = _load_xueqiu_config({"xueqiu": {"max_posts": "abc"}})
        assert cfg["MAX_POSTS"] == 10

    def test_executable_path_yaml_and_env(self, monkeypatch):
        cfg = _load_xueqiu_config({"xueqiu": {"executable_path": "D:/drv/chromedriver.exe"}})
        assert cfg["EXECUTABLE_PATH"] == "D:/drv/chromedriver.exe"

        monkeypatch.setenv("XUEQIU_EXECUTABLE_PATH", "C:/drv/chromedriver.exe")
        cfg = _load_xueqiu_config({"xueqiu": {"executable_path": "D:/drv/chromedriver.exe"}})
        assert cfg["EXECUTABLE_PATH"] == "C:/drv/chromedriver.exe"

    def test_interval_minutes_yaml_env_and_invalid(self, monkeypatch):
        cfg = _load_xueqiu_config({"xueqiu": {"interval_minutes": 30}})
        assert cfg["INTERVAL_MINUTES"] == 30

        # 环境变量优先
        monkeypatch.setenv("XUEQIU_INTERVAL_MINUTES", "45")
        cfg = _load_xueqiu_config({"xueqiu": {"interval_minutes": 30}})
        assert cfg["INTERVAL_MINUTES"] == 45

        # 非法值回退默认 120
        monkeypatch.delenv("XUEQIU_INTERVAL_MINUTES", raising=False)
        cfg = _load_xueqiu_config({"xueqiu": {"interval_minutes": "abc"}})
        assert cfg["INTERVAL_MINUTES"] == 120

        # 负数 clamp 到 0（禁用频控）
        cfg = _load_xueqiu_config({"xueqiu": {"interval_minutes": -5}})
        assert cfg["INTERVAL_MINUTES"] == 0


# ========================================
# NewsAnalyzer._crawl_xueqiu_data
# ========================================

class TestCrawlXueqiuData:
    def test_disabled_returns_empty(self):
        analyzer = _bare_analyzer(config=_xueqiu_config(enabled=False))
        assert analyzer._crawl_xueqiu_data() == []

    def test_enabled_without_urls_skips(self):
        analyzer = _bare_analyzer(config=_xueqiu_config(urls=[]))
        assert analyzer._crawl_xueqiu_data() == []

    def test_enabled_without_cookies_skips(self):
        analyzer = _bare_analyzer(config=_xueqiu_config(cookies=""))
        assert analyzer._crawl_xueqiu_data() == []
        assert _FakeFetcher.instances == []

    def test_normal_fetch_normalizes_posts(self):
        analyzer = _bare_analyzer(config=_xueqiu_config())
        with patch.object(main_module, "XueqiuSeleniumFetcher", _FakeFetcher):
            posts = analyzer._crawl_xueqiu_data()

        # 空白帖被过滤，只留 1 条；url 回退到主页 URL；author 取 source_name
        assert len(posts) == 1
        assert posts[0]["content"] == "存储芯片涨价，关注下游模组厂"
        assert posts[0]["url"] == "https://xueqiu.com/someone"
        assert posts[0]["author"] == "雪球大V"
        assert posts[0]["published_at"] == "2026-08-31 10:00"

        # 构造参数正确传递
        assert len(_FakeFetcher.instances) == 1
        fetcher = _FakeFetcher.instances[0]
        assert fetcher.target_url == "https://xueqiu.com/someone"
        assert fetcher.headless is False
        assert fetcher.cookies == "cookie123"

    def test_single_url_failure_isolated(self):
        analyzer = _bare_analyzer(config=_xueqiu_config(
            urls=["https://xueqiu.com/bad", "https://xueqiu.com/good"]
        ))
        with patch.object(main_module, "XueqiuSeleniumFetcher", _ExplodingFetcher):
            posts = analyzer._crawl_xueqiu_data()

        # 第一个 URL 失败不影响第二个
        assert len(posts) == 1
        assert posts[0]["content"] == "正常帖"
        assert posts[0]["author"] == "正常大V"


# ========================================
# 登录墙检测与告警（P0-② 后续：cookie 失效监控）
# ========================================

class _WallFetcher(_FakeFetcher):
    """命中登录墙的 fetcher 替身：返回 login_state=login_wall、无帖子"""

    def fetch_and_store_latest_posts(self, target_url=None, max_posts=10,
                                     mysql_pipeline=None, **kwargs):
        return {
            "stored_count": 0,
            "posts": [],
            "source_id": "someone",
            "source_name": "雪球大V",
            "login_state": "login_wall",
        }


class TestLoginWallAlert:
    def test_login_wall_triggers_notify(self):
        analyzer = _bare_analyzer(config=_xueqiu_config())
        analyzer.ctx.format_date = lambda: "2026-08-31"
        notified = []
        analyzer._notify_xueqiu_login_expired = lambda urls, **kw: notified.append(list(urls)) or True

        with patch.object(main_module, "XueqiuSeleniumFetcher", _WallFetcher):
            analyzer._crawl_xueqiu_data()

        assert notified == [["https://xueqiu.com/someone"]]

    def test_normal_fetch_does_not_notify(self):
        analyzer = _bare_analyzer(config=_xueqiu_config())
        notified = []
        analyzer._notify_xueqiu_login_expired = lambda urls, **kw: notified.append(list(urls))

        with patch.object(main_module, "XueqiuSeleniumFetcher", _FakeFetcher):
            analyzer._crawl_xueqiu_data()

        assert notified == []

    def test_notify_sends_alert_and_dedups_same_day(self, tmp_path, monkeypatch):
        state_path = str(tmp_path / "alert_state.json")
        calls = []

        def fake_send_alert(title, message, config, timeout=30):
            calls.append({"title": title, "message": message})
            return {"feishu": True}

        monkeypatch.setattr("trendradar.notification.alert.send_alert", fake_send_alert)
        analyzer = _bare_analyzer(config=_xueqiu_config())
        analyzer.ctx.format_date = lambda: "2026-08-31"

        # 首次告警：发送且写入状态文件
        assert analyzer._notify_xueqiu_login_expired(
            ["https://xueqiu.com/a"], state_path=state_path
        ) is True
        assert len(calls) == 1
        assert "雪球登录态失效" in calls[0]["title"]
        assert "https://xueqiu.com/a" in calls[0]["message"]

        # 同日再次告警：去重跳过，不再发送
        assert analyzer._notify_xueqiu_login_expired(
            ["https://xueqiu.com/a"], state_path=state_path
        ) is False
        assert len(calls) == 1

        # 状态文件损坏 → 视为今日未告警，可再次发送
        with open(state_path, "w", encoding="utf-8") as f:
            f.write("{broken")
        assert analyzer._notify_xueqiu_login_expired(
            ["https://xueqiu.com/a"], state_path=state_path
        ) is True
        assert len(calls) == 2


# ========================================
# 雪球抓取频控（后续项②：interval_minutes）
# ========================================

class TestXueqiuIntervalGate:
    """频控闸门 / 状态记录 / _crawl_xueqiu_data 接线"""

    NOW = datetime(2026, 9, 8, 10, 0, 0, tzinfo=timezone.utc)

    def _analyzer(self):
        analyzer = _bare_analyzer(config=_xueqiu_config())
        old = analyzer.ctx
        analyzer.ctx = SimpleNamespace(
            config=old.config,
            format_time=old.format_time,
            get_time=lambda: self.NOW,
            get_mysql_pipeline=old.get_mysql_pipeline,
        )
        return analyzer

    @staticmethod
    def _write_state(path: str, minutes_ago: int, naive: bool = False) -> None:
        last = TestXueqiuIntervalGate.NOW - timedelta(minutes=minutes_ago)
        if naive:
            last = last.replace(tzinfo=None)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"last_crawl": last.isoformat()}, f)

    def test_gate_blocks_within_interval(self, tmp_path):
        analyzer = self._analyzer()
        state = str(tmp_path / "gate.json")
        self._write_state(state, minutes_ago=30)  # 30 分钟前刚抓过，间隔 120 分钟
        assert analyzer._xueqiu_crawl_gate(120, state_path=state) is False

    def test_gate_allows_after_interval(self, tmp_path):
        analyzer = self._analyzer()
        state = str(tmp_path / "gate.json")
        self._write_state(state, minutes_ago=180)  # 3 小时前，间隔 120 分钟
        assert analyzer._xueqiu_crawl_gate(120, state_path=state) is True

    def test_gate_tolerates_missing_or_corrupt_state(self, tmp_path):
        analyzer = self._analyzer()
        # 状态文件不存在 → 从未抓取，放行
        assert analyzer._xueqiu_crawl_gate(
            120, state_path=str(tmp_path / "missing.json")
        ) is True
        # 状态文件损坏 → 放行
        corrupt = tmp_path / "corrupt.json"
        corrupt.write_text("{broken", encoding="utf-8")
        assert analyzer._xueqiu_crawl_gate(120, state_path=str(corrupt)) is True

    def test_gate_handles_naive_timestamp(self, tmp_path):
        """状态文件里是 naive 时间（历史版本）时按当前时区解释，不应崩溃。"""
        analyzer = self._analyzer()
        state = str(tmp_path / "gate.json")
        self._write_state(state, minutes_ago=30, naive=True)
        assert analyzer._xueqiu_crawl_gate(120, state_path=state) is False

    def test_gate_disabled_when_zero(self):
        analyzer = self._analyzer()
        # interval<=0 直接放行，不读状态文件（路径随意，不产生 IO）
        assert analyzer._xueqiu_crawl_gate(0, state_path="Z:/no/such/x.json") is True
        assert analyzer._xueqiu_crawl_gate(-1) is True

    def test_mark_xueqiu_crawled_writes_state(self, tmp_path):
        analyzer = self._analyzer()
        state = str(tmp_path / "gate.json")
        analyzer._mark_xueqiu_crawled(state_path=state)
        with open(state, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["last_crawl"].startswith("2026-09-08T10:00:00")

    def test_crawl_skipped_within_interval(self):
        """间隔未到时 _crawl_xueqiu_data 直接跳过，不构造 fetcher。"""
        analyzer = self._analyzer()
        analyzer.ctx.config = {
            "XUEQIU": {**_xueqiu_config()["XUEQIU"], "INTERVAL_MINUTES": 120}
        }
        skipped = []
        analyzer._xueqiu_crawl_gate = (
            lambda interval, **kw: skipped.append(interval) or False
        )
        with patch.object(main_module, "XueqiuSeleniumFetcher", _FakeFetcher):
            posts = analyzer._crawl_xueqiu_data()
        assert posts == []
        assert skipped == [120]
        assert _FakeFetcher.instances == []  # 频控拦截，fetcher 未被构造

    def test_crawl_calls_mark_after_run(self):
        """interval=0（禁用频控）时正常抓取，跑完后记录抓取时间。"""
        analyzer = self._analyzer()
        analyzer.ctx.config = {
            "XUEQIU": {**_xueqiu_config()["XUEQIU"], "INTERVAL_MINUTES": 0}
        }
        marked = []
        analyzer._mark_xueqiu_crawled = lambda state_path=None: marked.append(state_path)
        with patch.object(main_module, "XueqiuSeleniumFetcher", _FakeFetcher):
            posts = analyzer._crawl_xueqiu_data()
        assert len(posts) == 1
        assert len(marked) == 1


# ========================================
# NewsAnalyzer._build_xueqiu_rss_entry
# ========================================

class TestBuildXueqiuRssEntry:
    def test_no_posts_returns_none(self):
        analyzer = _bare_analyzer()
        assert analyzer._build_xueqiu_rss_entry() is None

    def test_entry_structure_and_fields(self):
        analyzer = _bare_analyzer(posts=[{
            "content": "CPO 板块大涨",
            "published_at": "2026-08-31 10:30",
            "url": "https://xueqiu.com/someone",
            "author": "雪球大V",
        }])
        entry = analyzer._build_xueqiu_rss_entry()
        assert entry is not None
        assert entry["word"] == "雪球大V动态"
        assert entry["count"] == 1

        title = entry["titles"][0]
        # formatter 直接下标访问的字段必须存在
        assert title["time_display"] == "2026-08-31 10:30"
        assert title["count"] == 1
        # 渲染/AI 消费的其余字段
        assert title["title"] == "CPO 板块大涨"
        assert title["source_name"] == "雪球大V"
        assert title["url"] == "https://xueqiu.com/someone"
        assert title["mobile_url"] == ""
        assert title["rank"] == 99 and title["ranks"] == [99]
        assert title["first_time"] == title["last_time"] == "2026-08-31 10:30"
        assert title["is_new"] is True
        assert title["matched_keyword"] == "雪球大V动态"
        assert title["skip_translation"] is True

    def test_long_content_truncated(self):
        analyzer = _bare_analyzer(posts=[{
            "content": "涨" * 100,
            "published_at": "2026-08-31 10:30",
            "url": "https://xueqiu.com/someone",
            "author": "雪球大V",
        }])
        entry = analyzer._build_xueqiu_rss_entry(max_title_len=80)
        title = entry["titles"][0]["title"]
        assert len(title) == 81  # 80 字 + 省略号
        assert title.endswith("…")

    def test_missing_publish_time_falls_back_to_now(self):
        analyzer = _bare_analyzer(posts=[{
            "content": "没有时间戳的帖",
            "published_at": "",
            "url": "https://xueqiu.com/someone",
            "author": "雪球大V",
        }])
        entry = analyzer._build_xueqiu_rss_entry()
        assert entry["titles"][0]["time_display"] == "2026-08-31 12:00:00"

    def test_blank_posts_only_returns_none(self):
        analyzer = _bare_analyzer(posts=[{"content": "  ", "published_at": "", "url": "", "author": "x"}])
        assert analyzer._build_xueqiu_rss_entry() is None


# ========================================
# _run_analysis_pipeline 合入
# ========================================

class TestPipelineMerge:
    def _run_pipeline(self, analyzer, rss_items):
        analyzer.ctx.display_mode = "keyword"
        analyzer.ctx.count_frequency = lambda *args, **kwargs: ([], 0)
        analyzer.ctx.config.update({
            "AI_ANALYSIS": {"ENABLED": False},
            "AI_TRANSLATION": {"ENABLED": False},
            "STORAGE": {"FORMATS": {"HTML": False}},
        })
        return analyzer._run_analysis_pipeline(
            data_source={},
            mode="incremental",
            title_info={},
            new_titles={},
            word_groups=[],
            filter_words=[],
            id_to_name={},
            failed_ids=None,
            global_filters=None,
            quiet=True,
            rss_items=rss_items,
            rss_new_items=None,
            standalone_data=None,
            schedule=None,
            rss_new_urls=set(),
        )

    def test_merges_xueqiu_entry_into_empty_rss_items(self):
        analyzer = _bare_analyzer(posts=[{
            "content": "存储芯片涨价",
            "published_at": "2026-08-31 10:00",
            "url": "https://xueqiu.com/someone",
            "author": "雪球大V",
        }])
        _, _, _, rss_items, _, _ = self._run_pipeline(analyzer, rss_items=None)

        assert rss_items is not None
        assert rss_items[-1]["word"] == "雪球大V动态"
        assert rss_items[-1]["count"] == 1

    def test_merges_without_overwriting_existing_groups(self):
        analyzer = _bare_analyzer(posts=[{
            "content": "存储芯片涨价",
            "published_at": "2026-08-31 10:00",
            "url": "https://xueqiu.com/someone",
            "author": "雪球大V",
        }])
        existing = [{"word": "已有词组", "count": 2, "titles": []}]
        _, _, _, rss_items, _, _ = self._run_pipeline(analyzer, rss_items=existing)

        assert [s["word"] for s in rss_items] == ["已有词组", "雪球大V动态"]

    def test_no_xueqiu_posts_keeps_rss_items_untouched(self):
        analyzer = _bare_analyzer(posts=[])
        existing = [{"word": "已有词组", "count": 2, "titles": []}]
        _, _, _, rss_items, _, _ = self._run_pipeline(analyzer, rss_items=existing)

        assert rss_items == existing


# ========================================
# dispatcher.translate_content 的 skip_translation 标记
# ========================================

class _FakeTranslator:
    enabled = True
    target_language = "English"
    scope = {"HOTLIST": True, "RSS": True, "STANDALONE": True}

    def translate_batch(self, texts):
        return BatchTranslationResult(
            total_count=len(texts),
            results=[
                TranslationResult(translated_text=f"{t}_EN", original_text=t, success=True)
                for t in texts
            ],
            success_count=len(texts),
            fail_count=0,
            parsed_count=len(texts),
        )


class TestSkipTranslation:
    def test_marked_titles_keep_original(self):
        dispatcher = NotificationDispatcher(
            config={},
            get_time_func=lambda: None,
            split_content_func=lambda content: [content],
            translator=_FakeTranslator(),
        )
        rss_items = [
            {"word": "雪球大V动态", "count": 1, "titles": [{"title": "中文财经帖", "skip_translation": True}]},
            {"word": "普通RSS", "count": 1, "titles": [{"title": "hello"}]},
        ]
        _, out_items, _, _ = dispatcher.translate_content(
            report_data={},
            rss_items=rss_items,
            rss_new_items=None,
            standalone_data=None,
            display_regions={"RSS": True},
        )
        # 带标记的雪球帖保留原文；普通 RSS 标题正常翻译
        assert out_items[0]["titles"][0]["title"] == "中文财经帖"
        assert out_items[1]["titles"][0]["title"] == "hello_EN"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
