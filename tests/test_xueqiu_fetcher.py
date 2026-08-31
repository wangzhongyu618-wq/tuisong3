# coding=utf-8
import trendradar.crawler.xueqiu_fetcher as xueqiu_module
from trendradar.crawler.xueqiu_fetcher import XueqiuSeleniumFetcher


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
    assert posts[0]["published_at"] == "2026-08-18 09:15:30"


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
    assert call["posts"][0]["published_at"] == "2026-08-18 09:15:30"

