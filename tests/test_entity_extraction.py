# coding=utf-8
"""
结构化实体提取（P0-①）单元测试

覆盖：
- AIAnalyzer.extract_entities：正常解析 / markdown 包裹 / 无内容跳过 /
  LLM 异常降级 / 非法 JSON 降级 / 脏实体过滤
- _extract_json_str 静态方法（从 _parse_response 重构抽出）
- _parse_response 重构后行为回归
- loader 对 enable_sentiment_extraction / sentiment_extraction_prompt_file 的加载
- config/ai_sentiment_prompt.txt 存在性与结构
"""

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from trendradar.ai.analyzer import AIAnalyzer
from trendradar.core.loader import _load_ai_analysis_config

_PROJECT_ROOT = Path(__file__).parent.parent
_SENTIMENT_PROMPT = _PROJECT_ROOT / "config" / "ai_sentiment_prompt.txt"


def _make_analyzer(**analysis_overrides) -> AIAnalyzer:
    """构造带假 Key 的 AIAnalyzer（不触网），可覆盖 analysis 配置项"""
    ai_config = {
        "MODEL": "test/test-model",
        "API_KEY": "test-key-123456",
        "TEMPERATURE": 0.5,
        "MAX_TOKENS": 100,
        "TIMEOUT": 10,
    }
    analysis_config = {
        "LANGUAGE": "Chinese",
        "PROMPT_FILE": "ai_analysis_prompt.txt",
        "SENTIMENT_PROMPT_FILE": "ai_sentiment_prompt.txt",
        "MAX_NEWS_FOR_ANALYSIS": 50,
        "INCLUDE_RSS": True,
        "INCLUDE_STANDALONE": False,
    }
    analysis_config.update(analysis_overrides)
    return AIAnalyzer(ai_config, analysis_config, lambda: datetime(2026, 8, 31, 12, 0, 0))


def _sample_stats():
    """与主流程 stats 同构的最小样例（_prepare_news_content 可消费）"""
    return [
        {
            "word": "存储芯片",
            "titles": [
                {
                    "title": "长江存储扩产 带动上游设备采购",
                    "source_name": "微博",
                    "ranks": [1, 3],
                    "first_time": "09:30",
                    "last_time": "10:00",
                    "count": 3,
                }
            ],
        }
    ]


VALID_ENTITIES_JSON = json.dumps(
    {
        "entities": [
            {
                "type": "STOCK",
                "name": "存储芯片",
                "code": "",
                "sentiment_score": 0.8,
                "alert_level": "High",
                "event_summary": "长江存储扩产带动设备采购",
                "context": "长江存储扩产 带动上游设备采购",
            }
        ]
    },
    ensure_ascii=False,
)


class TestExtractEntities:
    """AIAnalyzer.extract_entities 主路径"""

    def test_success_plain_json(self):
        analyzer = _make_analyzer()
        with patch.object(analyzer.client, "chat", return_value=VALID_ENTITIES_JSON):
            result = analyzer.extract_entities(_sample_stats())

        assert result["skipped"] is False
        assert result["error"] == ""
        assert len(result["entities"]) == 1
        entity = result["entities"][0]
        assert entity["type"] == "STOCK"
        assert entity["name"] == "存储芯片"
        assert entity["sentiment_score"] == 0.8
        assert entity["alert_level"] == "High"

    def test_success_markdown_wrapped(self):
        """AI 常见的 ```json 包裹响应必须可解析"""
        analyzer = _make_analyzer()
        wrapped = f"```json\n{VALID_ENTITIES_JSON}\n```"
        with patch.object(analyzer.client, "chat", return_value=wrapped):
            result = analyzer.extract_entities(_sample_stats())

        assert result["error"] == ""
        assert len(result["entities"]) == 1

    def test_skipped_when_no_content(self):
        """空新闻数据应跳过而不是浪费一次 LLM 调用"""
        analyzer = _make_analyzer()
        with patch.object(analyzer.client, "chat") as mock_chat:
            result = analyzer.extract_entities([])

        mock_chat.assert_not_called()
        assert result["skipped"] is True
        assert result["entities"] == []

    def test_degrade_on_llm_exception(self):
        """LLM 调用异常必须降级为空实体，绝不抛出中断主流程"""
        analyzer = _make_analyzer()
        with patch.object(analyzer.client, "chat", side_effect=RuntimeError("timeout")):
            result = analyzer.extract_entities(_sample_stats())

        assert result["entities"] == []
        assert result["skipped"] is False
        assert "timeout" in result["error"]

    def test_degrade_on_invalid_json(self):
        analyzer = _make_analyzer()
        with patch.object(analyzer.client, "chat", return_value="这不是JSON"):
            result = analyzer.extract_entities(_sample_stats())

        assert result["entities"] == []
        assert result["error"] != ""

    def test_filters_non_dict_entities(self):
        """entities 中的脏项（非字典）被过滤，合法项保留"""
        analyzer = _make_analyzer()
        dirty = json.dumps(
            {"entities": ["oops", 42, json.loads(VALID_ENTITIES_JSON)["entities"][0]]},
            ensure_ascii=False,
        )
        with patch.object(analyzer.client, "chat", return_value=dirty):
            result = analyzer.extract_entities(_sample_stats())

        assert len(result["entities"]) == 1
        assert result["entities"][0]["type"] == "STOCK"

    def test_missing_api_key_skips(self):
        analyzer = _make_analyzer()
        analyzer.client.api_key = ""
        result = analyzer.extract_entities(_sample_stats())
        assert result["skipped"] is True
        assert "API Key" in result["error"]

    def test_prompt_fill_and_message_order(self):
        """system 在前 user 在后，占位符被真实数据替换"""
        analyzer = _make_analyzer()
        captured = {}

        def fake_chat(messages, **kwargs):
            captured["messages"] = messages
            return VALID_ENTITIES_JSON

        with patch.object(analyzer.client, "chat", side_effect=fake_chat):
            analyzer.extract_entities(_sample_stats())

        messages = captured["messages"]
        assert messages[0]["role"] == "system"
        assert messages[-1]["role"] == "user"
        user_text = messages[-1]["content"]
        assert "{news_content}" not in user_text
        assert "存储芯片" in user_text
        assert "{current_time}" not in user_text


class TestExtractJsonStr:
    """_parse_response 重构抽出的 markdown 剥离逻辑"""

    def test_plain_json(self):
        raw = '{"a": 1}'
        assert AIAnalyzer._extract_json_str(raw) == '{"a": 1}'

    def test_json_code_fence(self):
        raw = '前置说明\n```json\n{"a": 1}\n```\n后缀'
        assert AIAnalyzer._extract_json_str(raw) == '{"a": 1}'

    def test_generic_code_fence(self):
        raw = '```\n{"a": 1}\n```'
        assert AIAnalyzer._extract_json_str(raw) == '{"a": 1}'

    def test_unclosed_fence(self):
        raw = '```json\n{"a": 1}'
        assert AIAnalyzer._extract_json_str(raw) == '{"a": 1}'


class TestParseResponseRegression:
    """_parse_response 重构后行为回归"""

    def _full_sections_json(self) -> str:
        return json.dumps(
            {
                "core_trends": "核心态势",
                "sentiment_controversy": "风向争议",
                "signals": "异动信号",
                "rss_insights": "RSS洞察",
                "outlook_strategy": "研判策略",
            },
            ensure_ascii=False,
        )

    def test_plain_parse(self):
        analyzer = _make_analyzer()
        result = analyzer._parse_response(self._full_sections_json())
        assert result.success is True
        assert result.error == ""
        assert result.core_trends == "核心态势"
        assert result.outlook_strategy == "研判策略"

    def test_markdown_wrapped_parse(self):
        analyzer = _make_analyzer()
        result = analyzer._parse_response(f"```json\n{self._full_sections_json()}\n```")
        assert result.success is True
        assert result.core_trends == "核心态势"

    def test_empty_response(self):
        analyzer = _make_analyzer()
        result = analyzer._parse_response("")
        assert result.success is False
        assert result.error == "AI 返回空响应"


class TestLoaderConfig:
    """loader 对新配置键的加载"""

    def test_defaults_when_absent(self):
        cfg = _load_ai_analysis_config({})
        assert cfg["ENABLE_SENTIMENT_EXTRACTION"] is True
        assert cfg["SENTIMENT_PROMPT_FILE"] == "ai_sentiment_prompt.txt"

    def test_explicit_values(self):
        cfg = _load_ai_analysis_config(
            {
                "ai_analysis": {
                    "enable_sentiment_extraction": False,
                    "sentiment_extraction_prompt_file": "custom_prompt.txt",
                }
            }
        )
        assert cfg["ENABLE_SENTIMENT_EXTRACTION"] is False
        assert cfg["SENTIMENT_PROMPT_FILE"] == "custom_prompt.txt"


class TestSentimentPromptFile:
    """config/ai_sentiment_prompt.txt 结构约定"""

    def test_file_exists_with_sections(self):
        assert _SENTIMENT_PROMPT.exists(), "实体提取提示词文件缺失"
        content = _SENTIMENT_PROMPT.read_text(encoding="utf-8")
        assert "[system]" in content
        assert "[user]" in content
        # extract_entities 依赖的占位符
        assert "{news_content}" in content
        assert "{rss_content}" in content
        assert "{current_time}" in content
        # 管道只接受 STOCK 实体
        assert '"STOCK"' in content or "'STOCK'" in content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
