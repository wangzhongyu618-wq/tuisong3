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


def test_fetch_latest_posts_extracts_text_and_timestamp(monkeypatch):
    fetcher = XueqiuSeleniumFetcher(headless=True)

    monkeypatch.setattr(fetcher, "_create_driver", lambda: DummyDriver())
    posts = fetcher.fetch_latest_posts("https://xueqiu.com/u/123456", max_posts=5)

    assert len(posts) == 1
    assert posts[0]["content"] == "今天买入了 AAPL，长期看好 AI 产业链"
    assert posts[0]["published_at"] == "2026-08-18 09:15:30"
    assert posts[0]["source_type"] == "xueqiu_v_dynamic"
