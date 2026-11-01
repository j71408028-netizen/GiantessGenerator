"""报告侧的描述（quip）机制：预定义标签、标记替换、预算选句、解锁描述与对比行文案。

描述（quip）原是基础能力，副本移除描述注入后（见
``docs/designs/dungeon_scene.md`` §5 的校准块）只剩报告管线消费，
自 ``core/logic`` 迁入本模块。

``@behavior_hook`` 的 scope ``"logic"`` 是已部署行为包的公开契约，与文件位置无关，
不得改动（见 ``docs/designs/behaviors/hooks-logic.md``）。共用的底座（
``get_comparisons`` / ``contains_blocked_word`` / ``compute_environment_factor``）
仍在 ``core/logic``，此处按需引用。
"""

import math
import random
import re
from typing import Dict, List, Optional, Set, Tuple

from core.behavior_runtime import behavior_const, behavior_hook, get_runtime
from core.logic.simulation import compute_environment_factor
from core.logic.sizing import format_size
from core.logic.text import contains_blocked_word

PREDEFINED_TAGS = behavior_const("logic", "PREDEFINED_TAGS", [
    "涩涩", "裙装", "制服", "旅行", "地理测量",
    "能躺着不站着", "四处走走", "摄影师请就位",
    "细节控", "尺寸焦虑症",
], layer="content", contract="data")


def get_predefined_tags() -> List[str]:
    """返回预定义标签列表（可被行为包通过覆盖 ``logic.PREDEFINED_TAGS`` 定制）。

    行为包既可注册一个返回标签列表的可调用对象，也可直接注册列表本身。
    """
    return list(get_runtime().resolve_value("logic.PREDEFINED_TAGS"))


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


# 环境一致性软权重（选取仲裁器传入槽位估计，Q1 剩余①）：
#   加分 = 权重 × (1 - |文本因子 - 槽位估计| / 值域全宽)，偏差越大越趋零；
#   零证据时槽位估计为 None，软权重整体缺席（零证据零权重）。
# 两参数是 report.model.* 常量钩子（行为包可覆盖，消费时读取有效值）
behavior_const("report", "model.env_consistency", 0.5,
               layer="infra", contract="data")
behavior_const("report", "model.env_dev_range", 1.4,
               layer="infra", contract="data")   # 因子值域 [0.1, 1.5] 全宽


def _report_model(key: str) -> float:
    """report.model.* 常量钩子的有效值（float）。"""
    return float(get_runtime().resolve_value(key))


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
        lanes: Optional[List] = None,
        env_estimate: Optional[float] = None,
) -> Tuple[Optional[str], Optional[str], Optional[Tuple[int, int]], float, float, float]:
    """按坐标邻域与步进预算选一条描述。

    lanes：选取仲裁器的资格窗口 ``[(风格, 车道权重)]``（裁定级）。None =
    不分层（仅限未走仲裁的直接调用）。窗口外风格不参与选取；窗口内风格
    按车道权重获得排序加分（只影响优先序，不排除低权重者）。消费语义不
    变：选中的条目仍从 ``quips_working`` 中移除。行为包覆盖实现必须接受
    这两个参数并遵循其语义，否则与报告引擎的契约不匹配。

    env_estimate：槽位地址的环境估计（档案环境轴）。None = 零证据，
    软权重中性缺席；非空时对每条候选文本单次采样 ``compute_environment_
    factor``，与估计偏差越小排序加分越高——软权重，不做硬闸，矛盾文本
    仍可被选（池耗尽降级不受影响）。
    """
    lane_weights = None
    if lanes is not None:
        lane_weights = {s: w for s, w in lanes}
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
        if lane_weights is not None and q.get("style", "") not in lane_weights:
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
        # 车道加分：exact > cover > free（仲裁分层），只在同池内调整优先序
        lane_bonus = lane_weights.get(q.get("style", ""), 0.0) if lane_weights else 0.0
        # 环境一致性加分：文本因子与槽位估计偏差越小加分越高（单次采样，
        # 容忍 hook 随机性）；与车道加分同走减分-夹取非负路径
        env_bonus = 0.0
        if env_estimate is not None:
            dev = abs(compute_environment_factor(q.get("text", "")) - env_estimate)
            env_bonus = (_report_model("report.model.env_consistency")
                         * max(0.0, 1.0 - dev / _report_model(
                             "report.model.env_dev_range")))
        score = dist * 2.0 + step_score - lane_bonus - env_bonus
        scored.append((score, coord, q, dist))

    scored.sort(key=lambda x: x[0])
    top_n = scored[:5]

    min_score = top_n[0][0]
    # 车道加分可能把分数拉成负值，权重一律夹取非负——否则总权重 ≤ 0 时
    # 会落入 random.choice 分支，加权的确定性失效
    weights = [max(0.0, min_score - s[0] + 1e-6) for s in top_n]
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


@behavior_hook("logic", "build_size_description")
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
