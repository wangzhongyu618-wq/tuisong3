# coding=utf-8
"""
板块→A股/ETF 映射表（P0-③ 防幻觉闸门）单元测试

覆盖：
- is_valid_security_code：A股/ETF/港股/美股 合法与非法形态
- SectorMapper：真实映射表加载 / 精确匹配 / 后缀剥离 / 包含匹配 /
  code 补全 / 非法 code 清除后继续补全 / 合法 code 优先保留 /
  未命中原样 / etf 为 null / yaml 锚点继承（芯片←半导体）/ 文件缺失降级 /
  批量与异常兜底
- loader 对 enable_sector_mapping / sector_mapping_file 的加载
- AIAnalyzer 集成：extract_entities 输出经过映射闸门
"""

import json
from datetime import datetime
from unittest.mock import patch

import pytest

from trendradar.ai.analyzer import AIAnalyzer
from trendradar.ai.sector_mapping import SectorMapper, is_valid_security_code
from trendradar.core.loader import _load_ai_analysis_config


def _make_analyzer(**analysis_overrides) -> AIAnalyzer:
    """构造带假 Key 的 AIAnalyzer（不触网），默认开启映射闸门（真实映射表）"""
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
    """与主流程 stats 同构的最小样例"""
    return [
        {
            "word": "存储芯片",
            "titles": [
                {
                    "title": "存储芯片涨价周期开启",
                    "source_name": "微博",
                    "ranks": [1],
                    "first_time": "09:30",
                    "last_time": "10:00",
                    "count": 1,
                }
            ],
        }
    ]


class TestIsValidSecurityCode:
    """证券代码基础格式校验"""

    @pytest.mark.parametrize("code", ["600519", "512480", "000002", "300308", "688256"])
    def test_a_share_and_etf(self, code):
        assert is_valid_security_code(code) is True

    @pytest.mark.parametrize("code", ["00700", "0700", "09988"])
    def test_hk_stock(self, code):
        assert is_valid_security_code(code) is True

    @pytest.mark.parametrize("code", ["AAPL", "NVDA", "TSLA"])
    def test_us_stock(self, code):
        assert is_valid_security_code(code) is True

    def test_us_stock_lowercase_normalized(self):
        """美股小写自动规整后可通过（不误杀）"""
        assert is_valid_security_code("aapl") is True

    @pytest.mark.parametrize(
        "code", ["", "   ", "中际旭创", "ABC123", "6005199", "600519.SH", "12 34", "紫金矿业", "888888"]
    )
    def test_invalid_codes(self, code):
        assert is_valid_security_code(code) is False


class TestSectorMapperLoading:
    """真实 config/sector_mapping.yaml 加载"""

    @pytest.fixture()
    def mapper(self):
        return SectorMapper("sector_mapping.yaml")

    def test_theme_count(self, mapper):
        assert len(mapper) >= 20, "映射表应至少包含 20 个主题"

    def test_storage_theme(self, mapper):
        theme = mapper._themes.get("存储芯片")
        assert theme is not None
        assert theme["etf"]["code"] == "512480"
        codes = [s["code"] for s in theme["stocks"]]
        assert "603986" in codes  # 兆易创新

    def test_yaml_anchor_merge(self, mapper):
        """yaml 锚点：芯片主题继承半导体的 stocks，覆盖 etf"""
        chip = mapper._themes.get("芯片")
        assert chip is not None
        assert chip["etf"]["code"] == "159995"
        codes = [s["code"] for s in chip["stocks"]]
        assert "002371" in codes  # 继承自 半导体 锚点（北方华创）

    def test_anchor_shared_theme(self, mapper):
        """光模块锚点被 CPO 共享"""
        assert mapper._themes["CPO"]["stocks"] == mapper._themes["光模块"]["stocks"]


class TestResolveEntity:
    """单实体处理规则"""

    @pytest.fixture()
    def mapper(self):
        return SectorMapper("sector_mapping.yaml")

    def test_fill_on_exact_match(self, mapper):
        entity = {"type": "STOCK", "name": "存储芯片", "code": ""}
        new, note = mapper.resolve_entity(entity)
        assert new["code"] == "512480"
        assert "映射表补全" in note
        # 原实体不被就地修改
        assert entity["code"] == ""

    def test_fill_with_suffix_stripped(self, mapper):
        """'存储芯片概念' 剥离后缀后命中"""
        new, note = mapper.resolve_entity({"name": "存储芯片概念", "code": ""})
        assert new["code"] == "512480"
        assert "映射表补全" in note

    def test_theme_without_etf_keeps_empty(self, mapper):
        """命中主题但 etf 为 null → code 留空"""
        new, note = mapper.resolve_entity({"name": "算力租赁", "code": ""})
        assert new["code"] == ""
        assert "命中主题" in note

    def test_fill_by_contains_match(self, mapper):
        """'存储芯片板块走强' 包含主题名 '存储芯片' → 补全"""
        new, note = mapper.resolve_entity({"name": "存储芯片板块走强", "code": ""})
        assert new["code"] == "512480"
        assert "映射表补全" in note

    def test_valid_code_kept_over_theme(self, mapper):
        """合法 code（精确个股）优先保留，不被映射表覆盖"""
        entity = {"name": "存储芯片", "code": "603986"}
        new, note = mapper.resolve_entity(entity)
        assert new["code"] == "603986"
        assert note == ""

    def test_invalid_code_cleared_then_filled(self, mapper):
        """编造代码先清除，再按主题补全 ETF"""
        entity = {"name": "存储芯片", "code": "888888"}
        new, note = mapper.resolve_entity(entity)
        assert new["code"] == "512480"
        assert "非法代码已清除" in note and "映射表补全" in note

    def test_invalid_code_cleared_no_theme(self, mapper):
        """编造代码且主题无 ETF 时只清除"""
        entity = {"name": "算力租赁", "code": "XYZ99"}
        new, note = mapper.resolve_entity(entity)
        assert new["code"] == ""
        assert "非法代码已清除" in note

    def test_no_match_untouched(self, mapper):
        """未命中主题且无 code → 原样返回"""
        entity = {"name": "元宇宙", "code": ""}
        new, note = mapper.resolve_entity(entity)
        assert new["code"] == ""
        assert note == ""

    def test_non_chinese_us_entity(self, mapper):
        """美股实体合法 code 保留"""
        new, _ = mapper.resolve_entity({"name": "NVIDIA", "code": "NVDA"})
        assert new["code"] == "NVDA"

    def test_empty_name(self, mapper):
        new, note = mapper.resolve_entity({"code": ""})
        assert note == ""

    def test_exception_fallback(self, mapper):
        """闸门自身异常不抛出，保留原值"""
        broken = None  # 非 dict → .get 抛 AttributeError
        new, note = mapper.resolve_entity(broken)
        assert new == broken
        assert "异常" in note


class TestResolveEntities:
    """批量处理与兜底"""

    @pytest.fixture()
    def mapper(self):
        return SectorMapper("sector_mapping.yaml")

    def test_batch_mixed(self, mapper):
        entities = [
            {"name": "存储芯片", "code": ""},
            {"name": "中际旭创", "code": "300308"},
            {"name": "算力租赁", "code": "BAD1"},
            {"name": "未知主题", "code": ""},
        ]
        resolved = mapper.resolve_entities(entities)
        assert resolved[0]["code"] == "512480"
        assert resolved[1]["code"] == "300308"
        assert resolved[2]["code"] == ""
        assert resolved[3]["code"] == ""

    def test_input_list_not_mutated(self, mapper):
        entities = [{"name": "存储芯片", "code": ""}]
        mapper.resolve_entities(entities)
        assert entities[0]["code"] == ""

    def test_non_list_input_fallback(self, mapper):
        """整体异常兜底：原样返回输入，绝不抛出"""
        assert mapper.resolve_entities(None) is None


class TestDegradedMode:
    """映射表缺失/损坏时降级为纯格式校验"""

    def test_missing_file(self):
        mapper = SectorMapper("no_such_mapping_file.yaml")
        assert len(mapper) == 0

    def test_still_validates_code_format(self):
        mapper = SectorMapper("no_such_mapping_file.yaml")
        new, note = mapper.resolve_entity({"name": "存储芯片", "code": "BAD1"})
        assert new["code"] == ""
        assert "非法代码已清除" in note

    def test_no_fill_without_table(self):
        mapper = SectorMapper("no_such_mapping_file.yaml")
        new, note = mapper.resolve_entity({"name": "存储芯片", "code": ""})
        assert new["code"] == ""
        assert note == ""

    def test_corrupted_file_fallback(self):
        """损坏的 yaml → 空映射，不抛异常"""
        mapper = SectorMapper("__init__.py")  # 存在但不是合法 yaml
        assert len(mapper) == 0


class TestLoaderSectorMappingConfig:
    """loader 对 P0-③ 配置键的加载"""

    def test_defaults_when_absent(self):
        cfg = _load_ai_analysis_config({})
        assert cfg["ENABLE_SECTOR_MAPPING"] is True
        assert cfg["SECTOR_MAPPING_FILE"] == "sector_mapping.yaml"

    def test_explicit_values(self):
        cfg = _load_ai_analysis_config(
            {
                "ai_analysis": {
                    "enable_sector_mapping": False,
                    "sector_mapping_file": "custom_mapping.yaml",
                }
            }
        )
        assert cfg["ENABLE_SECTOR_MAPPING"] is False
        assert cfg["SECTOR_MAPPING_FILE"] == "custom_mapping.yaml"


class TestAnalyzerIntegration:
    """extract_entities 输出经过映射闸门"""

    def test_entities_resolved_by_gate(self):
        """空 code 补全 / 合法保留 / 非法清除"""
        analyzer = _make_analyzer()  # 默认开启闸门（真实映射表）
        payload = json.dumps(
            {
                "entities": [
                    {"type": "STOCK", "name": "存储芯片", "code": "",
                     "sentiment_score": 0.8, "alert_level": "High",
                     "event_summary": "涨价", "context": "存储芯片涨价"},
                    {"type": "STOCK", "name": "中际旭创", "code": "300308",
                     "sentiment_score": 0.6, "alert_level": "Medium",
                     "event_summary": "订单", "context": "光模块订单"},
                    {"type": "STOCK", "name": "算力租赁", "code": "FAKE99",
                     "sentiment_score": 0.5, "alert_level": "Low",
                     "event_summary": "扩容", "context": "算力扩容"},
                ]
            },
            ensure_ascii=False,
        )
        with patch.object(analyzer.client, "chat", return_value=payload):
            result = analyzer.extract_entities(_sample_stats())

        assert result["error"] == ""
        codes = [e["code"] for e in result["entities"]]
        assert codes == ["512480", "300308", ""]  # 补全 / 保留 / 清除

    def test_gate_disabled(self):
        """关闭开关后输出原样（code 不补全）"""
        analyzer = _make_analyzer(ENABLE_SECTOR_MAPPING=False)
        assert analyzer.sector_mapper is None
        payload = json.dumps(
            {"entities": [{"type": "STOCK", "name": "存储芯片", "code": "",
                           "sentiment_score": 0.8, "alert_level": "High",
                           "event_summary": "涨价", "context": "存储芯片涨价"}]},
            ensure_ascii=False,
        )
        with patch.object(analyzer.client, "chat", return_value=payload):
            result = analyzer.extract_entities(_sample_stats())

        assert result["entities"][0]["code"] == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
