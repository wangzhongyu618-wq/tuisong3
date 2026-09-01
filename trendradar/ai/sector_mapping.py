# coding=utf-8
"""
板块/主题 → A股/ETF 映射表（P0-③ 防幻觉闸门）

对 AI 实体提取（extract_entities）的输出做后处理：
  1) code 格式校验：LLM 编造的非法代码（中文/混合/超长）强制置空
  2) 主题映射补全：code 为空的板块实体按 name 匹配映射表，补全 ETF 代码
  3) code 格式合法时保留 LLM 结果（可能是精确个股，优先级高于主题 ETF）

映射表文件：config/sector_mapping.yaml（人工维护，含"需人工核对"声明）。
文件缺失/损坏时降级为纯格式校验，不影响主流程。
"""

import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml

# 项目 config 根目录（与 prompt_loader 保持一致）
_CONFIG_ROOT = Path(__file__).parent.parent.parent / "config"

# 证券代码格式：A股/ETF 6位数字（含交易所段位校验）、
# 美股 1-5 位大写字母、港股 4-5 位数字
_A_CODE_PREFIXES = {
    "60", "68",                     # 沪市主板 / 科创板
    "00", "30",                     # 深市主板（含原中小板）/ 创业板
    "15", "16", "18",               # 深市 ETF / LOF
    "51", "56", "58", "59",         # 沪市 ETF
    "82", "83", "87", "92",         # 北交所 / 新三板
    "90", "20",                     # B股（沪 / 深）
}
_US_RE = re.compile(r"^[A-Z]{1,5}$")
_HK_RE = re.compile(r"^\d{4,5}$")

# 主题名常见后缀（匹配前剥离）
_THEME_SUFFIX_RE = re.compile(r"(概念|板块|题材|相关|产业链|指数|股)$")


def is_valid_security_code(code: str) -> bool:
    """证券代码基础格式校验（防幻觉：拒绝编造/乱码代码）

    规则：
      - 6位数字：须命中 A股/ETF 交易所段位前缀（如 60/68/00/30/51/56/15），
        段位外的纯数字（如 888888）视为编造代码
      - 4-5位数字：港股
      - 1-5位大写字母：美股（小写自动规整，不误杀）
    不做逐一代码库比对，避免过度依赖静态数据。
    """
    code = (code or "").strip()
    if not code:
        return False
    if len(code) == 6 and code.isdigit():
        return code[:2] in _A_CODE_PREFIXES
    if _HK_RE.match(code):
        return True
    # 美股：小写自动规整后再校验（aapl -> AAPL）
    return bool(_US_RE.match(code.upper()))


class SectorMapper:
    """板块/主题 → A股/ETF 映射器（防幻觉闸门）"""

    def __init__(self, mapping_file: str = "sector_mapping.yaml"):
        """
        Args:
            mapping_file: 映射表文件名（相对于 config 目录）
        """
        self._themes: Dict[str, Dict[str, Any]] = {}
        self._mapping_path = _CONFIG_ROOT / mapping_file

        try:
            if self._mapping_path.exists():
                with open(self._mapping_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                if isinstance(data, dict):
                    self._themes = {
                        str(k).strip(): v
                        for k, v in data.items()
                        if str(k or "").strip() and isinstance(v, dict)
                    }
                print(
                    f"[实体映射] 映射表加载成功: {self._mapping_path.name} "
                    f"({len(self._themes)} 个主题)"
                )
            else:
                print(
                    f"[实体映射] 映射表不存在: {self._mapping_path}，"
                    f"仅启用代码格式校验"
                )
        except Exception as e:
            self._themes = {}
            print(f"[实体映射] 映射表加载失败（降级为纯格式校验）: {e}")

    def __len__(self) -> int:
        return len(self._themes)

    # ----------------------------------------
    # 主题匹配
    # ----------------------------------------

    def _match_theme(self, name: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        """按实体名匹配主题：精确 → 去后缀 → 包含（键顺序即优先级）"""
        name = (name or "").strip()
        if not name:
            return None

        # 1) 精确匹配
        if name in self._themes:
            return name, self._themes[name]

        # 2) 剥离常见后缀后精确匹配（"存储芯片概念" -> "存储芯片"）
        base = _THEME_SUFFIX_RE.sub("", name).strip()
        if base and base != name and base in self._themes:
            return base, self._themes[base]

        # 3) 包含匹配（实体名包含主题名，如 "光模块涨价" 含 "光模块"）
        for key, theme in self._themes.items():
            if key and key in name:
                return key, theme

        return None

    # ----------------------------------------
    # 实体处理
    # ----------------------------------------

    def resolve_entity(self, entity: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
        """处理单个实体，返回 (新实体, 处理说明)

        规则：
          - code 非法（编造/乱码）→ 置空，并继续尝试映射补全
          - code 为空且 name 命中主题且有 ETF → 补全 ETF 代码
          - code 合法 → 原样保留（精确个股优先）
        任何异常返回原实体（闸门自身故障不挡数据）。
        """
        try:
            code = str(entity.get("code") or "").strip()
            name = str(entity.get("name") or "").strip()
            note = ""

            # 1) 防幻觉核心：非法代码清除（清除后继续尝试映射补全）
            if code and not is_valid_security_code(code):
                entity = dict(entity)
                entity["code"] = ""
                note = f"非法代码已清除: {code!r}"
                code = ""

            # 2) 主题映射补全（仅当 code 为空）
            if not code and name:
                matched = self._match_theme(name)
                if matched:
                    theme_key, theme = matched
                    etf = theme.get("etf") or {}
                    etf_code = str(etf.get("code") or "").strip()
                    if etf_code and is_valid_security_code(etf_code):
                        entity = dict(entity)
                        entity["code"] = etf_code
                        filled_note = f"映射表补全: {theme_key} -> {etf_code}"
                        return entity, f"{note}; {filled_note}" if note else filled_note
                    if not note:
                        return entity, f"命中主题 {theme_key}（无可用ETF代码，code留空）"

            return entity, note
        except Exception as e:
            return entity, f"映射处理异常（保留原值）: {e}"

    def resolve_entities(self, entities: list) -> list:
        """批量处理实体列表，打印摘要，返回处理后的列表

        单条异常由 resolve_entity 兜底；本方法整体再兜底：
        闸门自身故障时原样返回输入，绝不抛出。
        """
        try:
            resolved = []
            filled, cleared, kept, untouched = 0, 0, 0, 0
            for entity in entities:
                new_entity, note = self.resolve_entity(entity)
                resolved.append(new_entity)
                if "映射表补全" in note:
                    filled += 1
                    print(f"[实体映射] {entity.get('name')}: {note}")
                elif "非法代码已清除" in note:
                    cleared += 1
                    print(f"[实体映射] {entity.get('name')}: {note}")
                elif note:
                    kept += 1
                else:
                    untouched += 1

            print(
                f"[实体映射] 处理完成: 补全 {filled} / 清除非法 {cleared} / "
                f"命中主题 {kept} / 未命中 {untouched}"
            )
            return resolved
        except Exception as e:
            print(f"[实体映射] 批量处理异常（保留原始实体）: {e}")
            return entities
