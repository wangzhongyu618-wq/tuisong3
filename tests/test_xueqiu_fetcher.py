# coding=utf-8
import pytest

import trendradar.crawler.xueqiu_fetcher as xueqiu_module
from trendradar.crawler.xueqiu_fetcher import XueqiuSeleniumFetcher

import re


class DummyDriver:
    def __init__(self):
        self.closed = False

    def get(self, url):
        self.url = url

    def execute_script(self, script, *args):
        return [
            {
                "text": "今天买入了 AAPL，长期看好 AI 产业链",
                "timestamp": "2026-08-18 09:15:30",
            }
        ]

    def quit(self):
        self.closed = True


class CookieAwareDriver(DummyDriver):
    """记录页面导航与 cookie 操作顺序的替身，用于登录态注入测试。"""

    def __init__(self):
        super().__init__()
        self.events = []  # ("get", url) / ("add_cookie", name)
        self.cookies = []

    def get(self, url):
        super().get(url)
        self.events.append(("get", url))

    def add_cookie(self, cookie):
        self.cookies.append(cookie)
        self.events.append(("add_cookie", cookie["name"]))

    def get_cookies(self):
        return list(self.cookies)

    @property
    def current_url(self):
        return "https://xueqiu.com/u/123456"


class DummyWait:
    """WebDriverWait 替身：视为页面已加载完成，避免真实等待。

    selenium 未安装时模块顶层 import 失败，WebDriverWait 为 None；
    已安装时 DummyDriver 的 readyState 不满足条件会真实等待。
    mock 掉后测试与 selenium 安装状态完全解耦。
    """

    def __init__(self, driver, timeout=None):
        self.driver = driver
        self.timeout = timeout

    def until(self, method, message=None):
        return True


def test_fetch_latest_posts_extracts_text_and_timestamp(monkeypatch):
    # selenium 可能未安装（模块符号为 None）或已安装，统一替换为测试替身
    monkeypatch.setattr(xueqiu_module, "WebDriverWait", DummyWait)
    # 清除真实环境变量，防止本机登录态影响"未配置 cookie"路径的断言
    monkeypatch.delenv("XUEQIU_COOKIES", raising=False)

    fetcher = XueqiuSeleniumFetcher(headless=True)

    monkeypatch.setattr(fetcher, "_create_driver", lambda: DummyDriver())
    # 跳过反爬随机等待（每次 2-5 秒），保证测试瞬时完成
    monkeypatch.setattr(fetcher, "_random_wait", lambda: None)
    posts = fetcher.fetch_latest_posts("https://xueqiu.com/u/123456", max_posts=5)

    assert len(posts) == 1
    assert posts[0]["content"] == "今天买入了 AAPL，长期看好 AI 产业链"
    # 绝对时间输入解析后为带本地时区的 ISO 格式（偏移后缀随机器时区，不校验）
    assert posts[0]["published_at"].startswith("2026-08-18T09:15:30")


def test_parse_relative_time_formats():
    from datetime import datetime, timedelta, timezone as tz

    anchor = datetime(2026, 9, 7, 12, 0, 0, tzinfo=tz(timedelta(hours=8)))
    parse = lambda s: XueqiuSeleniumFetcher._parse_relative_time(s, now=anchor)

    # 相对时间 → 以抓取时刻为锚的绝对时间（带时区偏移）
    assert parse("刚刚") == "2026-09-07T12:00:00+08:00"
    assert parse("5分钟前") == "2026-09-07T11:55:00+08:00"
    assert parse("3小时前") == "2026-09-07T09:00:00+08:00"

    # 今天/昨天 + 时分
    assert parse("今天 09:30") == "2026-09-07T09:30:00+08:00"
    assert parse("昨天 14:05") == "2026-09-06T14:05:00+08:00"

    # 月-日（今年内）与完整日期（兼容 / 分隔与秒）
    assert parse("09-01") == "2026-09-01T00:00:00+08:00"
    assert parse("09-01 08:15") == "2026-09-01T08:15:00+08:00"
    assert parse("2026-08-18 09:15:30") == "2026-08-18T09:15:30+08:00"
    assert parse("2026/08/18") == "2026-08-18T00:00:00+08:00"

    # 纯 HH:MM → 今天该时刻
    assert parse("10:20") == "2026-09-07T10:20:00+08:00"

    # "编辑于/修改于"前缀与"来自xx"后缀自动剥离
    assert parse("编辑于4小时前 来自iPhone") == "2026-09-07T08:00:00+08:00"
    assert parse("修改于7小时前 来自iPhone") == "2026-09-07T05:00:00+08:00"
    assert parse("编辑于 昨天 22:00 来自Android") == "2026-09-06T22:00:00+08:00"

    # 无法识别 → 原样返回（不丢信息）
    assert parse("上周三") == "上周三"
    assert parse("") == ""
    assert parse(None) == ""


def test_parse_relative_time_output_is_tz_aware_for_downstream():
    """输出必须带时区偏移：format_iso_time_friendly 把 naive 时间当 UTC，会错 8 小时。"""
    import re as _re

    parsed = XueqiuSeleniumFetcher._parse_relative_time("3小时前")
    assert parsed
    # 带偏移的 ISO 格式（如 2026-09-07T09:00:00+08:00），与运行机器时区无关
    assert _re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}", parsed
    ), parsed



def test_parse_cookies_supports_header_string_and_json():
    # 格式 1：浏览器 Cookie 请求头串（无效片段自动忽略）
    pairs = XueqiuSeleniumFetcher._parse_cookies("xq_a_token=abc; xqat=def ;bad; =empty")
    assert pairs == [("xq_a_token", "abc"), ("xqat", "def")]

    # 格式 2：JSON 对象
    pairs2 = XueqiuSeleniumFetcher._parse_cookies('{"xq_a_token": "abc", "xqat": "def"}')
    assert pairs2 == [("xq_a_token", "abc"), ("xqat", "def")]

    # 空输入
    assert XueqiuSeleniumFetcher._parse_cookies("") == []
    assert XueqiuSeleniumFetcher._parse_cookies(None) == []


def test_cookies_resolution_env_fallback_and_param_priority(monkeypatch):
    # 未传参数时回读环境变量
    monkeypatch.setenv("XUEQIU_COOKIES", "xq_a_token=envtoken")
    fetcher = XueqiuSeleniumFetcher(headless=True)
    assert fetcher.cookies == "xq_a_token=envtoken"

    # 显式参数优先于环境变量
    fetcher2 = XueqiuSeleniumFetcher(headless=True, cookies="xq_a_token=argtoken")
    assert fetcher2.cookies == "xq_a_token=argtoken"

    # 环境变量与参数都为空时，不触发注入
    monkeypatch.delenv("XUEQIU_COOKIES", raising=False)
    fetcher3 = XueqiuSeleniumFetcher(headless=True)
    assert fetcher3.cookies == ""


def test_cookie_injection_happens_before_target_navigation(monkeypatch):
    monkeypatch.setattr(xueqiu_module, "WebDriverWait", DummyWait)
    fetcher = XueqiuSeleniumFetcher(headless=True, cookies="xq_a_token=abc; xqat=def")

    driver = CookieAwareDriver()
    monkeypatch.setattr(fetcher, "_create_driver", lambda: driver)
    monkeypatch.setattr(fetcher, "_random_wait", lambda: None)

    posts = fetcher.fetch_latest_posts("https://xueqiu.com/u/123456", max_posts=5)

    # 导航顺序：先落雪球首页（同域才能 add_cookie），再进目标页
    gets = [u for kind, u in driver.events if kind == "get"]
    assert gets[0] == "https://xueqiu.com/"
    assert gets[1] == "https://xueqiu.com/u/123456"

    # cookie 逐条注入且发生在目标页导航之前
    add_cookie_indexes = [i for i, (kind, _) in enumerate(driver.events) if kind == "add_cookie"]
    assert [n for kind, n in driver.events if kind == "add_cookie"] == ["xq_a_token", "xqat"]
    target_nav_index = next(
        i for i, (kind, u) in enumerate(driver.events) if kind == "get" and u == "https://xueqiu.com/u/123456"
    )
    assert all(i < target_nav_index for i in add_cookie_indexes)

    # 注入后抓取流程不受影响
    assert len(posts) == 1
    assert posts[0]["content"] == "今天买入了 AAPL，长期看好 AI 产业链"


def test_fetch_without_cookies_keeps_legacy_behavior(monkeypatch):
    monkeypatch.setattr(xueqiu_module, "WebDriverWait", DummyWait)
    monkeypatch.delenv("XUEQIU_COOKIES", raising=False)

    fetcher = XueqiuSeleniumFetcher(headless=True)
    driver = CookieAwareDriver()
    monkeypatch.setattr(fetcher, "_create_driver", lambda: driver)
    monkeypatch.setattr(fetcher, "_random_wait", lambda: None)

    posts = fetcher.fetch_latest_posts("https://xueqiu.com/u/123456", max_posts=5)

    # 未配置 cookie：只访问目标页一次，无任何 add_cookie 调用（与旧版行为一致）
    assert driver.events == [("get", "https://xueqiu.com/u/123456")]
    assert len(posts) == 1


def test_fetch_with_invalid_cookies_degrades_to_logged_out(monkeypatch):
    """注入流程抛异常时应降级为未登录态继续抓取，而不是失败返回空。"""
    monkeypatch.setattr(xueqiu_module, "WebDriverWait", DummyWait)
    monkeypatch.delenv("XUEQIU_COOKIES", raising=False)

    fetcher = XueqiuSeleniumFetcher(headless=True, cookies="xq_a_token=abc")

    driver = DummyDriver()

    def broken_inject(_driver):
        raise RuntimeError("模拟注入环境异常")

    monkeypatch.setattr(fetcher, "_inject_cookies", broken_inject)
    monkeypatch.setattr(fetcher, "_create_driver", lambda: driver)
    monkeypatch.setattr(fetcher, "_random_wait", lambda: None)

    posts = fetcher.fetch_latest_posts("https://xueqiu.com/u/123456", max_posts=5)
    assert len(posts) == 1
    assert driver.url == "https://xueqiu.com/u/123456"


class FakePipeline:
    """记录 ingest_crawled_news 调用的管道替身。"""

    def __init__(self):
        self.calls = []

    def ingest_crawled_news(self, posts, source_id=None, source_name=None):
        self.calls.append({"posts": posts, "source_id": source_id, "source_name": source_name})
        return len(posts)


def test_fetch_and_store_normalizes_posts_with_source_type(monkeypatch):
    # source_type 在 fetch_and_store_latest_posts 的入库标准化阶段注入
    fetcher = XueqiuSeleniumFetcher(headless=True)
    monkeypatch.setattr(
        fetcher,
        "fetch_latest_posts",
        lambda url, max_posts=10: [
            {
                "content": "今天买入了 AAPL，长期看好 AI 产业链",
                "published_at": "2026-08-18 09:15:30",
            }
        ],
    )
    pipeline = FakePipeline()

    result = fetcher.fetch_and_store_latest_posts(
        "https://xueqiu.com/u/123456", max_posts=5, mysql_pipeline=pipeline,
    )

    assert result["stored_count"] == 1
    assert result["source_id"] == "123456"
    assert result["posts"][0]["source_type"] == "xueqiu_v_dynamic"

    call = pipeline.calls[0]
    assert call["source_id"] == "123456"
    assert call["posts"][0]["source_type"] == "xueqiu_v_dynamic"
    assert call["posts"][0]["title"] == "今天买入了 AAPL，长期看好 AI 产业链"
    # crawl_time 恒为抓取时刻（YYYY-MM-DD HH:MM:SS），不再借用发布时间文本
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", call["posts"][0]["crawl_time"])

    assert call["posts"][0]["published_at"] == "2026-08-18 09:15:30"


# ========================================
# 登录态监控（last_login_state）
# ========================================

class LoginWallDriver(CookieAwareDriver):
    """被重定向到登录页的驱动替身（cookie 过期场景）。"""

    @property
    def current_url(self):
        return "https://xueqiu.com/S/signin"


def test_login_state_no_cookies(monkeypatch):
    """未配置 cookie 时标记 no_cookies（而非误报登录墙）。"""
    monkeypatch.setattr(xueqiu_module, "WebDriverWait", DummyWait)
    monkeypatch.delenv("XUEQIU_COOKIES", raising=False)

    fetcher = XueqiuSeleniumFetcher(headless=True)
    monkeypatch.setattr(fetcher, "_create_driver", lambda: DummyDriver())
    monkeypatch.setattr(fetcher, "_random_wait", lambda: None)

    fetcher.fetch_latest_posts("https://xueqiu.com/u/123456")
    assert fetcher.last_login_state == "no_cookies"


def test_login_state_login_wall_detected(monkeypatch):
    """cookie 失效（重定向登录页）必须标记 login_wall，不允许静默降级。"""
    monkeypatch.setattr(xueqiu_module, "WebDriverWait", DummyWait)
    monkeypatch.delenv("XUEQIU_COOKIES", raising=False)

    fetcher = XueqiuSeleniumFetcher(headless=True, cookies="xq_a_token=expired")
    monkeypatch.setattr(fetcher, "_create_driver", lambda: LoginWallDriver())
    monkeypatch.setattr(fetcher, "_random_wait", lambda: None)

    fetcher.fetch_latest_posts("https://xueqiu.com/u/123456")
    assert fetcher.last_login_state == "login_wall"


def test_login_state_ok_when_page_normal(monkeypatch):
    """页面未被重定向时标记 ok。"""
    monkeypatch.setattr(xueqiu_module, "WebDriverWait", DummyWait)
    monkeypatch.delenv("XUEQIU_COOKIES", raising=False)

    fetcher = XueqiuSeleniumFetcher(headless=True, cookies="xq_a_token=fresh")
    monkeypatch.setattr(fetcher, "_create_driver", lambda: CookieAwareDriver())
    monkeypatch.setattr(fetcher, "_random_wait", lambda: None)

    fetcher.fetch_latest_posts("https://xueqiu.com/u/123456")
    assert fetcher.last_login_state == "ok"


def test_fetch_and_store_result_includes_login_state(monkeypatch):
    """fetch_and_store_latest_posts 的两个返回路径都要携带 login_state。"""
    fetcher = XueqiuSeleniumFetcher(headless=True)
    monkeypatch.setattr(
        fetcher, "fetch_latest_posts",
        lambda url, max_posts=10: [{"content": "帖", "published_at": ""}],
    )
    result = fetcher.fetch_and_store_latest_posts(
        "https://xueqiu.com/u/123456", max_posts=5, mysql_pipeline=FakePipeline(),
    )
    assert result["login_state"] == "unknown"  # 未真实抓取，保持初始值

    # posts 为空的早退路径同样携带 login_state
    monkeypatch.setattr(fetcher, "fetch_latest_posts", lambda url, max_posts=10: [])
    empty = fetcher.fetch_and_store_latest_posts(
        "https://xueqiu.com/u/123456", max_posts=5, mysql_pipeline=FakePipeline(),
    )
    assert empty["stored_count"] == 0
    assert empty["login_state"] == "unknown"


# ========================================
# 驱动启动错误提示（后续项③：版本漂移明确报错）
# ========================================

class _ExplodingChrome:
    """webdriver 替身：Chrome 构造时抛版本不匹配异常"""

    @staticmethod
    def Chrome(options=None, service=None):
        raise RuntimeError(
            "Message: session not created: This version of ChromeDriver "
            "only supports Chrome version 139"
        )


class _FakeOptions:
    """ChromeOptions 替身：仅记录参数"""

    def __init__(self):
        self.args = []
        self.binary_location = ""

    def add_argument(self, arg):
        self.args.append(arg)

    def add_experimental_option(self, name, value):
        pass


def test_driver_error_hint_version_mismatch():
    hint = XueqiuSeleniumFetcher._driver_error_hint(RuntimeError(
        "Message: session not created: This version of ChromeDriver "
        "only supports Chrome version 139"
    ))
    assert "版本不匹配" in hint
    assert "XUEQIU_EXECUTABLE_PATH" in hint  # 给出修复路径
    assert "session not created" in hint  # 保留原始错误


def test_driver_error_hint_chrome_missing():
    hint = XueqiuSeleniumFetcher._driver_error_hint(
        RuntimeError("unknown error: cannot find Chrome binary"))
    assert "Chrome" in hint
    assert "安装" in hint


def test_driver_error_hint_driver_missing():
    hint = XueqiuSeleniumFetcher._driver_error_hint(
        RuntimeError("'chromedriver' executable needs to be in PATH"))
    assert "chromedriver" in hint
    assert "路径" in hint


def test_driver_error_hint_selenium_manager_network():
    hint = XueqiuSeleniumFetcher._driver_error_hint(RuntimeError(
        "There was an error managing chromedriver (cannot be downloaded)"))
    assert "selenium manager" in hint
    assert "网络受限" in hint


def test_driver_error_hint_unknown_keeps_original():
    hint = XueqiuSeleniumFetcher._driver_error_hint(ValueError("some other failure"))
    assert "some other failure" in hint


def test_create_driver_wraps_version_mismatch(monkeypatch):
    """驱动启动失败时抛出带中文修复提示的 RuntimeError，且保留原因链。"""
    monkeypatch.setattr(xueqiu_module, "webdriver", _ExplodingChrome)
    monkeypatch.setattr(xueqiu_module, "ChromeOptions", _FakeOptions)

    fetcher = XueqiuSeleniumFetcher(headless=True)
    with pytest.raises(RuntimeError, match="版本不匹配") as excinfo:
        fetcher._create_driver()
    assert isinstance(excinfo.value.__cause__, RuntimeError)
    assert "only supports Chrome version 139" in str(excinfo.value.__cause__)


class _CapturingChrome:
    """webdriver 模块替身：Chrome 属性捕获传入的 options 供断言"""

    last_options = None

    class Chrome:
        def __init__(self, options=None, service=None):
            _CapturingChrome.last_options = options


def test_create_driver_probes_chromium_on_linux(monkeypatch):
    """Linux 容器场景：浏览器不在 PATH 常规位置时按常见命名兜底定位。"""
    monkeypatch.setattr(xueqiu_module, "webdriver", _CapturingChrome)
    monkeypatch.setattr(xueqiu_module, "ChromeOptions", _FakeOptions)
    monkeypatch.setattr(
        xueqiu_module.shutil,
        "which",
        lambda name: "/usr/bin/chromium" if name == "chromium" else None,
    )

    fetcher = XueqiuSeleniumFetcher(headless=True)
    fetcher._create_driver()
    assert _CapturingChrome.last_options.binary_location == "/usr/bin/chromium"


def test_create_driver_keeps_selenium_manager_when_no_browser(monkeypatch):
    """找不到任何浏览器 binary 时不设置 binary_location，交给 selenium manager。"""
    monkeypatch.setattr(xueqiu_module, "webdriver", _CapturingChrome)
    monkeypatch.setattr(xueqiu_module, "ChromeOptions", _FakeOptions)
    monkeypatch.setattr(xueqiu_module.shutil, "which", lambda name: None)

    fetcher = XueqiuSeleniumFetcher(headless=True)
    fetcher._create_driver()
    assert _CapturingChrome.last_options.binary_location == ""

