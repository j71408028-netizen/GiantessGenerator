"""尺寸对比挑选：地标对比候选的生成与筛选。

自 ``core/logic.py`` 按职责拆出（原文件现为薄壳 ``core/logic/__init__.py``）。
副本提示词（换算句 / 在场对比 / 部位对比）与报告引擎共用，留在 core。
描述（quip）专属的标签与选句机制已迁 ``services/exploration/quips.py``。

``@behavior_hook`` 的 scope ``"logic"`` 是已部署行为包的公开契约，与文件位置无关，
不得改动（见 ``docs/designs/behaviors/hooks-logic.md``）。
"""

import random
from typing import Dict, List, Optional

from core.behavior_runtime import behavior_hook
from core.models import Landmark
from core.logic.text import contains_blocked_word, should_skip_by_part_tags


@behavior_hook("logic", "get_comparisons")
def get_comparisons(
    landmarks: List[Landmark],
    body_parts: Dict[str, float],
    order: str = "match",
    limit: int = 5,
    selected_tags: List[str] = None,
    skip_base_prob: float = 0.0,
    selected_parts: Optional[List[str]] = None,
    blocked_words: Optional[List[str]] = None,
) -> List[Dict]:
    """寻找最接近的地标对比，支持根据标签概率跳过特定姿势或身体部位的候选。

    名称含屏蔽词的地标会被跳过。
    """
    if selected_parts is None:
        selected_parts = list(body_parts.keys())

    if selected_tags is None:
        selected_tags = []

    N = sum(1 for tag in selected_tags)
    P = skip_base_prob / (N + 3)

    dim_map = {
        "膝盖高度": ["vertical"],
        "脚踝高度": ["vertical"],
        "步长": ["horizontal"],
    }
    posture_rules = {
        "身高": {"vertical": [1], "horizontal": [3]},
        "腿长": {"vertical": [1], "horizontal": [2, 3]},
        "脚长": {"vertical": [2, 3], "horizontal": [1, 2, 4]},
        "臂长": {"vertical": [1, 2, 4], "horizontal": [3]},
        "胸宽": {"vertical": [3], "horizontal": [1, 2, 4]},
        "大腿直径": {"vertical": [2, 3, 4], "horizontal": [1, 2, 3]},
        "小臂直径": {"vertical": [2, 3], "horizontal": [1, 2, 3, 4]},
        "手掌长度": {"vertical": [2, 3, 4], "horizontal": [2, 3, 4]},
        "食指长度": {"vertical": [2, 3, 4], "horizontal": [2, 3, 4]},
        "食指直径": {"vertical": [2, 3, 4], "horizontal": [2, 3, 4]},
        "指纹宽度": {"vertical": [2, 3, 4], "horizontal": [2, 3, 4]},
        "指缝宽度": {"vertical": [2, 3, 4], "horizontal": [2, 3, 4]},
        "膝盖高度": {"vertical": [1], "horizontal": [1]},
        "脚踝高度": {"vertical": [1, 4], "horizontal": [1, 4]},
        "步长": {"vertical": [1], "horizontal": [1]},
    }

    all_candidates = []
    for part, size in body_parts.items():
        if part not in selected_parts:
            continue
        allowed = dim_map.get(part, ["vertical", "horizontal"])
        for landmark in landmarks:
            if contains_blocked_word(landmark.name, blocked_words):
                continue
            if landmark.dimension in allowed:
                ratio = size / landmark.size
                if ratio < 0.1 or ratio > 50:
                    continue
                posture = posture_rules.get(part, {}).get(landmark.dimension, [])
                all_candidates.append({
                    "part": part,
                    "size": size,
                    "landmark": landmark,
                    "ratio": ratio,
                    "match_score": abs(1 - ratio),
                    "posture": posture,
                })

    if selected_tags and "尺寸焦虑症" in selected_tags:
        random.shuffle(all_candidates)
    else:
        all_candidates.sort(key=lambda x: x["match_score"])

    collected = []
    for cand in all_candidates:
        if len(collected) >= limit:
            break
        skip = False

        if P > 0:
            posture_set = set(cand["posture"])
            if "能躺着不站着" in selected_tags and "四处走走" in selected_tags:
                if 1 not in posture_set and 3 not in posture_set:
                    if random.random() < 2 * P:
                        skip = True
            elif "能躺着不站着" in selected_tags:
                if 3 not in posture_set:
                    if random.random() < P:
                        skip = True
            elif "四处走走" in selected_tags:
                if 1 not in posture_set:
                    if random.random() < P:
                        skip = True

        if not skip:
            if should_skip_by_part_tags(cand["part"], selected_tags, P):
                skip = True

        # 旅行 / 地理测量的地标风貌偏好已迁入选取仲裁器（anchored_bias，
        # services/exploration/arbiter.py）：无地址内容时不产生相对倾斜。

        if not skip:
            collected.append(cand)

    if order == "size_asc":
        collected.sort(key=lambda x: x["size"])
    elif order == "size_desc":
        collected.sort(key=lambda x: x["size"], reverse=True)

    return collected
