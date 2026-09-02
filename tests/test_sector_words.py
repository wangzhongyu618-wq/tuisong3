# coding=utf-8
"""
板块与概念词组（P1-②）单元测试

覆盖：
- 板块词组加载：显示名集合完整、新增组均为 /.../ => 别名 正则格式
- 与 sector_mapping.yaml 主题双向对齐（防漂移核心断言）：
  词组显示名 ⊆ 映射表主题；映射表主题（CPO 与光模块共用 YAML 锚点，
  词组合并为"光模块"）⊆ 词组显示名
- 关键词命中行为：每板块样例标题命中对应词组；英文缩写与中文邻接
  可命中（环视写法而非 \\b）；"中央银行"不误命中"银行"词组
- 多板块命中：跨板块关键词的新闻计入多个词组
"""

import pytest

from trendradar.ai.sector_mapping import SectorMapper
from trendradar.core.frequency import _word_matches, load_frequency_words

FREQ_FILE = "config/frequency_words.txt"

# 板块词组显示名全集（必须与 config/sector_mapping.yaml 主题名一致）
SECTOR_GROUP_NAMES = {
    "存储芯片", "半导体", "芯片", "科创芯片", "光模块",
    "AI算力", "算力租赁", "人工智能",
    "新能源车", "锂电池", "光伏",
    "白酒", "券商", "银行",
    "创新药", "医疗器械",
    "黄金", "有色金属", "煤炭", "军工", "游戏", "房地产", "消费电子",
    "机器人", "恒生科技", "纳斯达克",
}

# CPO 与光模块共用 YAML 锚点（CPO: &optical / 光模块: *optical），
# 词表侧合并为一个"光模块"词组，反向对齐时豁免
ANCHOR_MERGED_THEMES = {"CPO"}


@pytest.fixture(scope="module")
def word_groups_data():
    return load_frequency_words(FREQ_FILE)


@pytest.fixture(scope="module")
def mapper():
    return SectorMapper("sector_mapping.yaml")


def _hit_group_names(title: str, groups) -> set:
    """返回标题命中的词组显示名集合（复刻 matches_word_groups 的单组判定逻辑）"""
    title_lower = title.lower()
    hits = set()
    for group in groups:
        if group["required"] and not all(
            _word_matches(w, title_lower) for w in group["required"]
        ):
            continue
        if group["normal"] and not any(
            _word_matches(w, title_lower) for w in group["normal"]
        ):
            continue
        if group["required"] or group["normal"]:
            hits.add(group["display_name"])
    return hits


class TestSectorWordGroupsLoaded:
    """板块词组加载完整性"""

    def test_all_sector_groups_present(self, word_groups_data):
        groups, _, _ = word_groups_data
        names = {g["display_name"] for g in groups if g["display_name"]}
        missing = SECTOR_GROUP_NAMES - names
        assert not missing, f"板块词组缺失: {missing}"

    def test_new_sector_groups_are_regex_with_alias(self, word_groups_data):
        """板块词组统一 /.../ => 别名 格式（每个映射表主题独立成组）"""
        groups, _, _ = word_groups_data
        by_name = {g["display_name"]: g for g in groups if g["display_name"]}
        for name in SECTOR_GROUP_NAMES:
            group = by_name[name]
            assert group["normal"], name
            for w in group["normal"]:
                assert w["is_regex"], f"{name} 组关键词应为正则格式: {w['word']}"
                assert w.get("display_name"), f"{name} 组关键词应有行别名"


class TestMappingAlignment:
    """词组 ↔ 映射表主题双向对齐（P0-③ 防幻觉闸门依赖该对齐）"""

    def test_group_names_in_mapping(self, word_groups_data, mapper):
        groups, _, _ = word_groups_data
        names = {g["display_name"] for g in groups if g["display_name"]}
        missing = SECTOR_GROUP_NAMES - set(mapper._themes.keys())
        assert not missing, f"词组名未对齐映射表主题: {missing}"

    def test_mapping_themes_have_group(self, word_groups_data, mapper):
        groups, _, _ = word_groups_data
        names = {g["display_name"] for g in groups if g["display_name"]}
        expected = set(mapper._themes.keys()) - ANCHOR_MERGED_THEMES
        missing = expected - names
        assert not missing, f"映射表主题缺少对应词组: {missing}"


class TestKeywordMatching:
    """样例标题命中对应板块词组"""

    @pytest.mark.parametrize(
        "title,group_name",
        [
            ("长江存储二期扩产获批", "存储芯片"),
            ("NAND合约价连续三周上涨", "存储芯片"),
            ("中芯国际产能利用率满载", "半导体"),
            ("光刻机出口管制升级", "半导体"),
            ("国产GPU批量交付", "AI算力"),
            ("算力租赁价格持续上调", "算力租赁"),
            ("大模型端侧部署提速", "人工智能"),
            ("固态电池量产时间表提前", "锂电池"),
            ("宁德时代发布新车型电池", "新能源车"),
            ("茅台批价企稳回升", "白酒"),
            ("券商业绩预喜", "券商"),
            ("招商银行上调存款利率", "银行"),
            ("创新药出海授权创新高", "创新药"),
            ("金价创历史新高", "黄金"),
            ("稀土出口管制收紧", "有色金属"),
            ("动力煤价格反弹", "煤炭"),
            ("国防预算稳步增长", "军工"),
            ("游戏版号常态化发放", "游戏"),
            ("楼市成交量回暖", "房地产"),
            ("折叠屏手机出货量大增", "消费电子"),
            ("恒生指数夜盘收涨", "恒生科技"),
            ("纳指期货走高", "纳斯达克"),
        ],
    )
    def test_hits_expected_group(self, word_groups_data, title, group_name):
        groups, _, _ = word_groups_data
        assert group_name in _hit_group_names(title, groups)

    def test_central_bank_not_bank(self, word_groups_data):
        """"中央银行"不误命中"银行"词组（(?<!央) 负向环视）"""
        groups, _, _ = word_groups_data
        hits = _hit_group_names("多国中央银行增持黄金储备", groups)
        assert "银行" not in hits
        assert "黄金" in hits

    def test_english_adjacent_chinese(self, word_groups_data):
        """英文缩写与汉字邻接仍可命中（环视写法；\\b 在此场景会失效）"""
        groups, _, _ = word_groups_data
        assert "存储芯片" in _hit_group_names("长鑫存储DRAM产能爬坡", groups)
        assert "AI算力" in _hit_group_names("英伟达GPU供应紧张", groups)

    def test_cpo_merged_into_optical_module(self, word_groups_data):
        """CPO 与光模块同锚点，词组统一归"光模块" """
        groups, _, _ = word_groups_data
        hits = _hit_group_names("CPO共封装光学需求爆发", groups)
        assert "光模块" in hits


class TestMultiGroupHits:
    """跨板块新闻计入多个词组"""

    def test_battery_news_hits_two_groups(self, word_groups_data):
        groups, _, _ = word_groups_data
        hits = _hit_group_names("宁德时代发布固态电池新品", groups)
        assert {"新能源车", "锂电池"} <= hits

    def test_gold_and_metal_overlap(self, word_groups_data):
        groups, _, _ = word_groups_data
        hits = _hit_group_names("紫金矿业黄金产量创新高", groups)
        assert {"黄金", "有色金属"} <= hits
