# coding=utf-8
"""MySQLDataPipeline.ingest_crawled_news 的入库标准化测试。

重点回归 P0-② 引入的 source_type 透传与 additional_data 字段
（author/published_at 由雪球等调用方提供，热榜调用方缺省为空）。
"""
import pytest

from trendradar.storage.mysql_pipeline import MySQLDataPipeline


class RecordingBackend:
    """记录 save_raw_data_batch 入参的存储后端替身。"""

    def __init__(self):
        self.saved = []

    def save_raw_data_batch(self, records):
        self.saved.extend(records)
        return len(records)


@pytest.fixture()
def pipeline():
    return MySQLDataPipeline(mysql_backend=RecordingBackend())


def test_ingest_crawled_news_passes_through_xueqiu_fields(pipeline):
    posts = [{
        "title": "存储芯片涨价，关注下游模组厂",
        "url": "https://xueqiu.com/u/123456/123",
        "rank": 1,
        "ranks": [1],
        "crawl_time": "2026-09-07 12:00:00",
        "source_type": "xueqiu_v_dynamic",
        "author": "雪球大V",
        "published_at": "2026-09-07T10:30:00+08:00",
    }]

    count = pipeline.ingest_crawled_news(
        posts, source_id="xueqiu_home", source_name="雪球主页",
    )

    assert count == 1
    record = pipeline.backend.saved[0]
    assert record["source_type"] == "xueqiu_v_dynamic"
    assert record["source_id"] == "xueqiu_home"
    assert record["url"] == "https://xueqiu.com/u/123456/123"
    additional = record["additional_data"]
    assert additional["author"] == "雪球大V"
    assert additional["published_at"] == "2026-09-07T10:30:00+08:00"
    assert additional["crawl_time"] == "2026-09-07 12:00:00"
    assert additional["rank"] == 1


def test_ingest_crawled_news_defaults_for_hotlist_callers(pipeline):
    """热榜调用方不提供 source_type/author/published_at 时保持历史默认。"""
    count = pipeline.ingest_crawled_news(
        [{"title": "某热点话题", "url": "https://example.com/hot/1", "rank": 3, "ranks": [3]}],
        source_id="toutiao",
    )

    assert count == 1
    record = pipeline.backend.saved[0]
    assert record["source_type"] == "hotlist_news"
    additional = record["additional_data"]
    assert additional["author"] == ""
    assert additional["published_at"] == ""
    assert additional["crawl_time"] is None
