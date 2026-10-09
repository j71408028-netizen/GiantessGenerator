"""文本屏蔽：屏蔽词规范与判断、标签偏好跳过、地标对比行文案。

自 ``core/logic.py`` 按职责拆出（原文件现为薄壳 ``core/logic/__init__.py``）。
``@behavior_hook`` 的 scope ``"logic"`` 是已部署行为包的公开契约，与文件位置无关，
不得改动（见 ``docs/designs/world_pack_behaviors.md``）。
"""

import random
import re
from typing import Dict, Iterable, List, Tuple, Union

from core.behavior_runtime import behavior_hook
from core.logic.sizing import format_size


def normalize_blocked_words(words: Union[str, Iterable[str], None]) -> List[str]:
    """将屏蔽词输入规范为非空字符串列表。字符串按逗号/顿号/分号/换行拆分。"""
    if not words:
        return []
    if isinstance(words, str):
        parts = re.split(r"[,，;；、\n]+", words)
        return [p.strip() for p in parts if p.strip()]
    return [str(w).strip() for w in words if str(w).strip()]


@behavior_hook("logic", "contains_blocked_word")
def contains_blocked_word(text: str, blocked_words: Union[str, Iterable[str], None]) -> bool:
    """判断文本是否包含任一屏蔽词（大小写不敏感的子串匹配）。"""
    words = normalize_blocked_words(blocked_words)
    if not text or not words:
        return False
    lowered = text.lower()
    return any(w.lower() in lowered for w in words)


@behavior_hook("logic", "should_skip_by_part_tags")
def should_skip_by_part_tags(part: str, selected_tags: List[str], p: float) -> bool:
    """按"摄影师请就位/细节控"标签偏好判断是否跳过该部位。

    摄影师请就位偏好宏观部位（PHOTOGRAPHER_PARTS）；细节控偏好手部细节
    （HANDY_PARTS）；两者同时选中时偏好二者并集。p 为单标签跳过基准概率，
    由调用方按 skip_base_prob/(标签数+3) 计算，与 get_comparisons 一致。
    """
    # "摄影师请就位/细节控"标签偏好的部位集合
    photographer_parts = {"身高", "腿长", "胸宽", "膝盖高度", "步长"}
    handy_parts = {"食指长度", "手掌长度", "食指直径", "指纹宽度", "指缝宽度"}
    if "摄影师请就位" in selected_tags and "细节控" in selected_tags:
        return part not in (photographer_parts | handy_parts) and random.random() < 2 * p
    if "摄影师请就位" in selected_tags:
        return part not in photographer_parts and random.random() < p
    if "细节控" in selected_tags:
        return part not in handy_parts and random.random() < p
    return False


def comparison_lines(comp: Dict, height: float) -> Tuple[str, str]:
    """把一条地标对比翻成报告里的两行文字，返回 ``(尺寸文本, 对比文本)``。

    报告正文与挂件版「身高对比」开场共用同一套措辞——两处各写一遍迟早会写歪。
    """
    landmark = comp["landmark"]
    size_str = format_size(comp["size"], base_size=height)
    ratio = comp["ratio"]
    suffix = "高" if landmark.dimension == "vertical" else (
        "长" if landmark.horizontal_type == "length" else "宽")
    if landmark.frequency == "unique":
        compare_text = f"    └─ 约等于{landmark.name}{suffix}度的{ratio:.2f}倍"
    elif ratio < 0.5:
        compare_text = f"    └─ 尚不足{landmark.name}的{suffix}度"
    elif ratio > 1.5:
        compare_text = f"    └─ 完全超过{landmark.name}的{suffix}度"
    else:
        compare_text = f"    └─ 相当于{landmark.name}的{suffix}度"
    return size_str, compare_text
