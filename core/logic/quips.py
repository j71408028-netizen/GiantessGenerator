"""quip 标签与选取：预定义标签、占位符替换、地标对比挑选与预算选句。

自 ``core/logic.py`` 按职责拆出（原文件现为薄壳 ``core/logic/__init__.py``）。
``@behavior_hook`` 的 scope ``"logic"`` 是已部署行为包的公开契约，与文件位置无关，
不得改动（见 ``docs/world_pack_behaviors.md``）。
"""

import math
import random
import re
from typing import Dict, List, Optional, Set, Tuple

from core.behavior_runtime import behavior_hook, get_runtime
from core.models import Landmark
from core.logic.text import contains_blocked_word, should_skip_by_part_tags

PREDEFINED_TAGS = [
    "涩涩", "裙装", "制服", "旅行", "地理测量",
    "能躺着不站着", "四处走走", "摄影师请就位",
    "细节控", "尺寸焦虑症",
]


def get_predefined_tags() -> List[str]:
    """返回预定义标签列表（可被行为包通过覆盖 ``logic.PREDEFINED_TAGS`` 定制）。

    行为包既可注册一个返回标签列表的可调用对象，也可直接注册列表本身。
    """
    impl = get_runtime().resolve("logic.PREDEFINED_TAGS")
    if impl is not None:
        return impl() if callable(impl) else impl
    return list(PREDEFINED_TAGS)


@behavior_hook("logic", "replace_quip_tags")
def replace_quip_tags(
    quip_text: str,
    style: str,
    size_cat: str,
    detail_pools: Dict,
    quips_working: Dict = None,
    intr: float = None,
    dest: float = None,
    enable_confusion: bool = False,
    allow_confusion_map: Dict[str, bool] = None,
) -> str:
    """根据内容池替换描述中的 [类型:编号:文本] 标记。"""
    if not detail_pools or size_cat not in detail_pools:
        return quip_text

    pool_cat = detail_pools[size_cat].get(style, {})

    def replacer(match):
        letter = match.group(1)
        num = match.group(2)
        orig_text = match.group(3)
        if orig_text.strip().upper() == "MARK":
            return ""

        use_confusion = (
            enable_confusion
            and quips_working is not None
            and intr is not None
            and dest is not None
            and allow_confusion_map is not None
            and allow_confusion_map.get(letter, False)
        )

        if use_confusion:
            matrix = quips_working.get(size_cat, {})
            if not matrix:
                candidates = pool_cat.get(letter, {}).get(num, [])
                return random.choice(candidates) if candidates else orig_text

            coords_with_dist = []
            for (i, d), quip_list in matrix.items():
                if not quip_list:
                    continue
                dist = math.hypot(i - intr, d - dest)
                if dist <= 2.0:
                    coords_with_dist.append(((i, d), dist))

            if not coords_with_dist:
                candidates = pool_cat.get(letter, {}).get(num, [])
                return random.choice(candidates) if candidates else orig_text

            num_weight = {}
            for (i, d), dist in coords_with_dist:
                weight = math.exp(-dist)
                quip_list = matrix.get((i, d), [])
                for qdict in quip_list:
                    if qdict.get("style") != style:
                        continue
                    text = qdict.get("text", "")
                    pattern = r'\[{}:(\d+):[^\]]*\]'.format(letter)
                    for m in re.finditer(pattern, text):
                        n = m.group(1)
                        num_weight[n] = num_weight.get(n, 0.0) + weight

            if not num_weight:
                candidates = pool_cat.get(letter, {}).get(num, [])
                return random.choice(candidates) if candidates else orig_text

            total = sum(num_weight.values())
            if total == 0:
                selected_num = num
            else:
                r = random.random() * total
                cum = 0.0
                selected_num = num
                for n, w in num_weight.items():
                    cum += w
                    if r <= cum:
                        selected_num = n
                        break

            candidates = pool_cat.get(letter, {}).get(selected_num, [])
            return random.choice(candidates) if candidates else orig_text
        else:
            candidates = pool_cat.get(letter, {}).get(num, [])
            return random.choice(candidates) if candidates else orig_text

    return re.sub(r'\[([a-e]):(\d+):([^\]]+)\]', replacer, quip_text)


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

        if not skip:
            if "旅行" in selected_tags and "地理测量" in selected_tags:
                pass
            else:
                lm = cand["landmark"]
                if "旅行" in selected_tags and lm.frequency != "unique":
                    if random.random() < P:
                        skip = True
                if not skip and "地理测量" in selected_tags and lm.frequency == "average":
                    if random.random() < 0.5 * P:
                        skip = True

        if not skip:
            collected.append(cand)

    if order == "size_asc":
        collected.sort(key=lambda x: x["size"])
    elif order == "size_desc":
        collected.sort(key=lambda x: x["size"], reverse=True)

    return collected


@behavior_hook("logic", "select_quip_with_budget")
def select_quip_with_budget(
        size_cat: str,
        intrusion_val: float,
        destruction_val: float,
        quips_working: Dict,
        locked_coords: Set,
        cumulative_actual: float,
        cumulative_base: float,
        rate_factor: float = 1.0,
        step_index: int = 0,
        selected_tags: Optional[List[str]] = None,
        skip_base_prob: float = 0.0,
        posture_list: Optional[List[int]] = None,
        blocked_words: Optional[List[str]] = None,
) -> Tuple[Optional[str], Optional[str], Optional[Tuple[int, int]], float, float, float]:
    matrix = quips_working.get(size_cat, {})
    if not matrix:
        return None, None, None, 0.0, cumulative_actual, cumulative_base

    nearest_coord = (round(intrusion_val), round(destruction_val))
    nearest_list = matrix.get(nearest_coord)
    use_only_nearest = (
            nearest_coord not in locked_coords
            and nearest_list is not None
            and len(nearest_list) > 15
    )

    def _is_usable_quip(q) -> bool:
        if not isinstance(q, dict):
            return False
        return not contains_blocked_word(q.get("text", ""), blocked_words)

    candidates = []
    if use_only_nearest:
        dist = math.hypot(nearest_coord[0] - intrusion_val, nearest_coord[1] - destruction_val)
        for q in nearest_list:
            if _is_usable_quip(q):
                candidates.append((nearest_coord, q, dist))
    if not use_only_nearest or not candidates:
        for (i, d), quip_list in matrix.items():
            if (i, d) in locked_coords:
                continue
            dist = math.hypot(i - intrusion_val, d - destruction_val)
            if dist <= 2.0:
                for q in quip_list:
                    if _is_usable_quip(q):
                        candidates.append(((i, d), q, dist))

    if not candidates:
        return None, None, None, 0.0, cumulative_actual, cumulative_base

    if selected_tags and skip_base_prob > 0:
        has_dress = "裙装" in selected_tags
        has_uniform = "制服" in selected_tags
        if has_dress or has_uniform:
            N = len(selected_tags)
            P = skip_base_prob / (N + 3)
            filtered = []
            for coord, q, dist in candidates:
                text = q['text']
                pattern = r'\[([a-e]):(\d+):[^\]]*\]'
                marks = re.findall(pattern, text)
                a_marks = [(letter, num) for letter, num, _ in marks if letter == 'a']
                if not a_marks:
                    filtered.append((coord, q, dist))
                else:
                    a_nums = {num for _, num in a_marks}
                    skip = False
                    if has_dress and has_uniform:
                        if '1' not in a_nums and '2' not in a_nums:
                            if random.random() < 2 * P:
                                skip = True
                    elif has_dress:
                        if '1' not in a_nums:
                            if random.random() < P:
                                skip = True
                    elif has_uniform:
                        if '2' not in a_nums:
                            if random.random() < P:
                                skip = True
                    if not skip:
                        filtered.append((coord, q, dist))
            candidates = filtered

    if not candidates:
        return None, None, None, 0.0, cumulative_actual, cumulative_base

    if posture_list is not None and len(posture_list) > 0:
        filtered2 = []
        posture_set = set(posture_list)
        for coord, q, dist in candidates:
            text = q['text']
            pattern_b = r'\[b:(\d+):[^\]]*\]'
            b_nums = {int(num) for num in re.findall(pattern_b, text)}
            if b_nums and not (posture_set & b_nums):
                continue
            filtered2.append((coord, q, dist))
        candidates = filtered2

    if not candidates:
        return None, None, None, 0.0, cumulative_actual, cumulative_base

    steps = [q['step'] for _, q, _ in candidates]
    base_step = sorted(steps)[len(steps) // 2]

    budget = cumulative_actual - cumulative_base * rate_factor
    norm = max(1.0, step_index + 1)
    clamp_val = max(-0.4, min(0.4, budget / (norm * 0.2)))
    p = 0.5 - clamp_val
    candidates_sorted = sorted(candidates, key=lambda x: x[1]['step'])
    target_idx = int(p * len(candidates_sorted))
    target_idx = max(0, min(len(candidates_sorted) - 1, target_idx))
    target_step = candidates_sorted[target_idx][1]['step']

    scored = []
    for coord, q, dist in candidates:
        step_diff = abs(q['step'] - target_step)
        step_score = step_diff / 1.0
        score = dist * 2.0 + step_score
        scored.append((score, coord, q, dist))

    scored.sort(key=lambda x: x[0])
    top_n = scored[:5]

    min_score = top_n[0][0]
    weights = [min_score - s[0] + 1e-6 for s in top_n]
    total_weight = sum(weights)
    if total_weight <= 0:
        chosen = random.choice(top_n)
    else:
        chosen = random.choices(top_n, weights=weights, k=1)[0]

    _, coord, selected_q, dist = chosen
    actual_step = selected_q['step']

    quip_list = matrix.get(coord, [])
    for idx, item in enumerate(quip_list):
        if item is selected_q:
            quip_list.pop(idx)
            break

    new_cumulative_actual = cumulative_actual + actual_step
    new_cumulative_base = cumulative_base + base_step

    return selected_q['text'], selected_q.get('style',
                                              ''), coord, actual_step, new_cumulative_actual, new_cumulative_base
