import math
import random
import re
from typing import List, Dict, Optional, Set, Tuple, Iterable, Union

from models import Landmark
from behavior_runtime import behavior_hook, get_runtime

ALL_PART_NAMES = [
    "身高", "步长", "腿长", "臂长", "胸宽", "脚长",
    "脚踝高度", "膝盖高度", "大腿直径", "小臂直径",
    "手掌长度", "食指长度", "食指直径", "指缝宽度", "指纹宽度"
]

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


SIZE_CATEGORIES = ["small", "medium", "large", "huge", "colossal"]
SIZE_DISPLAY = {
    "small": "7.5~50m", "medium": "50~300m",
    "large": "300~1800m", "huge": "1800~10000m",
    "colossal": "10~150km",
}


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


@behavior_hook("logic", "apply_size_unlock_updates")
def apply_size_unlock_updates(unlocks: Dict[str, str], updates: Dict[str, str],
                              info_update_rate: float = 0.5) -> Dict[str, str]:
    """按报告正文描述规则写入部位解锁信息，返回新的解锁字典。

    原解锁为空或 MEASURED 时直接写入新描述；原为详细文本且新描述非空时，
    以 info_update_rate 概率覆写。
    """
    unlocks = dict(unlocks or {})
    for part, new_desc in (updates or {}).items():
        new_desc = (new_desc or "").strip()
        if not new_desc:
            continue
        old = unlocks.get(part, "")
        if old in ("", "MEASURED"):
            unlocks[part] = new_desc
        elif random.random() < info_update_rate:
            unlocks[part] = new_desc
    return unlocks


@behavior_hook("logic", "compute_environment_factor")
def compute_environment_factor(text: str) -> float:
    """按文本中的环境关键词给出伤亡环境系数。"""
    _HIGH_RISK_WORDS = ["城市", "街道", "楼", "建筑", "住宅", "市中心", "广场",
                        "马路", "公路", "桥梁", "车站", "机场", "港口", "城镇",
                        "村庄", "居民", "人群", "交通", "地铁", "铁路", "商场"]
    _LOW_RISK_WORDS = ["野外", "森林", "山", "山脉", "海", "海洋", "湖", "河",
                       "沙漠", "草原", "荒野", "丛林", "岛屿", "海岸", "山谷",
                       "田野", "农田", "自然", "无人区"]
    has_high = any(kw in text for kw in _HIGH_RISK_WORDS)
    has_low = any(kw in text for kw in _LOW_RISK_WORDS)
    if has_high and not has_low:
        return 1.0 + 0.5 * random.random()
    elif has_low and not has_high:
        return 0.1 + 0.1 * random.random()
    else:
        return 0.3 + 0.3 * random.random()


@behavior_hook("logic", "compute_casualty")
def compute_casualty(height: float, step: float, destruction: float, text: str,
                     env_factor: Optional[float] = None) -> float:
    """计算一段文本的伤亡增量：0.015 × 身高² × 故事步长 × 破坏性 × 环境系数 × 碰撞系数。"""
    if env_factor is None:
        env_factor = compute_environment_factor(text)
    collision_factor = max(0.0, math.log10(height))
    return 0.01 * height * height * step * destruction * env_factor * collision_factor


@behavior_hook("logic", "format_size")
def format_size(size: float, base_size: float = None) -> str:
    """根据基准身高(base_size)对齐小数位数的逻辑"""
    ref_val = base_size if base_size is not None else size
    if ref_val >= 10000:
        km_ref = ref_val / 1000
        if km_ref >= 100:
            decimals = 1
        elif km_ref >= 10:
            decimals = 2
        else:
            decimals = 3
        display_val = size / 1000
        unit = "千米"
    else:
        if ref_val >= 1000:
            decimals = 0
        elif ref_val >= 100:
            decimals = 1
        else:
            decimals = 2
        display_val = size
        unit = "米"
    return f"{display_val:.{decimals}f} {unit}"


@behavior_hook("logic", "length_unit_label")
def length_unit_label() -> str:
    """返回当前基础长度单位标签（默认“米”）。

    行为包可覆盖此函数以全流程更换长度单位的显示名称，
    例如返回“英尺”或幻想世界中的“里”等。界面输入/标签
    （如创建参数面板的身高单位标签）会调用它来显示单位。
    """
    return "米"


def get_size_category(height: float) -> str:
    """根据身高返回大小类别"""
    if height < 7.5 or height > 150000:
        return ""
    if height >= 10000:
        return "colossal"
    elif height >= 1800:
        return "huge"
    elif height >= 300:
        return "large"
    elif height >= 50:
        return "medium"
    else:
        return "small"


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


@behavior_hook("logic", "_build_size_description")
def build_size_description(quip_result: dict) -> str:
    """由报告正文中的一条尺寸对比结果构造解锁描述（使用对应的事件描述 quip）。"""
    quip_text = (quip_result.get("quip_text") or "").strip()
    if quip_text:
        return quip_text
    size_str = quip_result.get("size_str", "")
    compare_text = quip_result.get("compare_text", "")
    compare_text = compare_text.strip().lstrip("└─ ").strip()
    if compare_text:
        return f"{size_str}，{compare_text}"
    return size_str


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
