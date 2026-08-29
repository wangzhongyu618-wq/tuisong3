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

    fetcher = XueqiuSeleniumFetcher(headless=True)

    monkeypatch.setattr(fetcher, "_create_driver", lambda: DummyDriver())
    # 跳过反爬随机等待（每次 2-5 秒），保证测试瞬时完成
    monkeypatch.setattr(fetcher, "_random_wait", lambda: None)
    posts = fetcher.fetch_latest_posts("https://xueqiu.com/u/123456", max_posts=5)

    assert len(posts) == 1
    assert posts[0]["content"] == "今天买入了 AAPL，长期看好 AI 产业链"
    assert posts[0]["published_at"] == "2026-08-18 09:15:30"


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

