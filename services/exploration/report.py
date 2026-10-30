# -*- coding: utf-8 -*-
"""报告引擎：把角色状态演化为一份**结构化**报告数据。

由 ``core/context.py`` 的 ``ExplorationContext`` 按职责抽出（阶段 3.1 / S2）。

它和 ``report_text`` 的分界：本模块产出结构化数据（对比项、quip 结果、伤亡、
坐标、地址规划），**不做**任何面向人的排版；``report_text`` 才拼文本。

``generate()`` 开头把 ``catalog`` 上的易变字段（``merged_landmarks`` / ``quips`` /
``detail_pools``）取成局部变量——它们会被 ``update_styles()`` 整体替换，每次调用
都重新取一次即可，绝不要在 ``__init__`` 里缓存。地标地址与耐久键一律向
``AddressPlanner`` 取（其背后是 ``AddressIndex`` 的单一解析结果）。
"""

import copy
import random
import re
from typing import List

from core.behavior_runtime import behavior_const, get_runtime
from core.logic import (
    ALL_PART_NAMES, format_size, get_size_category, replace_quip_tags,
    get_comparisons, compute_casualty, compute_environment_factor,
    select_quip_with_budget, comparison_lines,
)
from services.character.spatial import SpatialProfile, priors_for_tags

# 报告引擎的伤亡 / 耐久模型参数（常量钩子的 key 是公开契约；消费一律走
# resolve_value，此处不保留模块级名字）。终局坐标锁 (4, 4)、突破的 will
# 门控与直通语义不开放（核心不变式 / 策略契约）。
behavior_const("report", "model.breakthrough_scale", 1.0,
               layer="infra", contract="data")
behavior_const("report", "model.durability_damage_scale", 0.1,
               layer="infra", contract="data")
behavior_const("report", "model.worn_threshold", 0.5,
               layer="infra", contract="data")
behavior_const("report", "model.step_fallback", 0.05,
               layer="infra", contract="data")
behavior_const("report", "model.env_factor_fallback", 0.3,
               layer="infra", contract="data")


def _model_value(key: str) -> float:
    """report.model.* 常量钩子的有效值（覆盖优先于注册默认值）。"""
    return float(get_runtime().resolve_value(key))


def breakthrough_prob(greed: float, will: bool, attempts: int) -> float:
    """终局坐标突破概率：仅意愿角色参与，贪婪与已尝试次数线性抬升。"""
    if not will:
        return 0.0
    return min(1.0, _model_value("report.model.breakthrough_scale")
               * (greed / 100.0) * (attempts + 1))


def durability_damage(ratio: float, height_ratio: float,
                      destruction: float) -> float:
    """独特地标单次耐久伤害：对比倍率 × 身高比² × 破坏性 × 伤害系数。"""
    return (ratio * (height_ratio ** 2) * destruction
            * _model_value("report.model.durability_damage_scale"))


def is_worn(durability: float) -> bool:
    """耐久是否低到标注「（残破的）」。"""
    return durability < _model_value("report.model.worn_threshold")


class ReportEngine:
    """报告生成引擎（无编排、无持久化）。"""

    def __init__(self, catalog, addresses, arbiter=None):
        self.catalog = catalog
        self.addresses = addresses
        self.arbiter = arbiter

    def generate(self, core_state: dict, selected_quip_styles: List[str]) -> dict:
        settings = self.catalog.settings
        quip_repo = self.catalog.quip_repo
        merged_landmarks = self.catalog.merged_landmarks
        quips = self.catalog.quips
        detail_pools = self.catalog.detail_pools
        state_service = self.catalog.state_service

        curr_intrusion = core_state["base_intrusion"]
        curr_destruction = core_state["base_destruction"]
        name = core_state["name"]
        nick = core_state["nick"]
        height = core_state["height"]
        body_parts = core_state["body_parts"]
        personality_obj = core_state["personality_obj"]
        preset_obj = core_state["preset_obj"]
        selected_tags = core_state["selected_tags"]
        greed = core_state["greed"]
        will = core_state["will"]
        # 当前介入度/破坏性步长：未在 core_state 提供时沿用性格初始步长
        curr_step_intrusion = core_state.get("step_intrusion",
                                             personality_obj.step_intrusion)
        curr_step_destruction = core_state.get("step_destruction",
                                               personality_obj.step_destruction)

        comparison_order = settings.get("comparison_order", "match")
        comparison_count = settings.get("comparison_count", 5)
        selected_parts = settings.get("selected_parts", ALL_PART_NAMES.copy())

        blocked_words = settings.get("blocked_words", [])
        candidates = get_comparisons(
            merged_landmarks,
            body_parts,
            order=comparison_order,
            limit=max(comparison_count * 8, 100),
            selected_tags=selected_tags,
            skip_base_prob=personality_obj.skip_base_prob,
            selected_parts=selected_parts,
            blocked_words=blocked_words,
        )

        landmark_durability = core_state.get("landmark_durability", {})
        landmark_addresses = dict(core_state.get("landmark_addresses") or {})
        # 选取仲裁：车道窗口 / 近匹配提拔 / 档案权重（硬闸口径同原规划器）
        profile = core_state.get("spatial_profile") or SpatialProfile(
            priors=priors_for_tags(selected_tags or []))
        size_cat = get_size_category(height)
        arbitration = self.arbiter.arbitrate(
            candidates,
            core_state.get("position") or "",
            height,
            landmark_durability,
            profile=profile,
            skip_base_prob=personality_obj.skip_base_prob,
            coords=(curr_intrusion, curr_destruction),
            size_cat=size_cat,
        )
        position_now = arbitration["position"] or core_state.get("position") or ""
        stuck = arbitration["stuck"]
        if stuck is not None:
            return {
                **core_state,
                "comparisons": [],
                "quip_results": [],
                "total_casualties": 0.0,
                "curr_intrusion": curr_intrusion,
                "curr_destruction": curr_destruction,
                "step_intrusion": curr_step_intrusion,
                "step_destruction": curr_step_destruction,
                "size_cat": size_cat,
                "position": position_now,
                "engaged": arbitration["engaged"],
                "stuck": stuck,
                "coverage": arbitration["coverage"],
            }
        chosen = arbitration["chosen"]
        comparisons = [r.cand for r in chosen]

        quips_working = copy.deepcopy(quips)
        locked_coords = {(4, 4)}
        breakthrough_attempts = 0
        previous_frequency = None   # 上一次匹配地标的风貌（unique/common）；首次匹配视为切换

        enable_confusion = settings.get("enable_confusion", False)
        rate_factor = settings.get("quip_rate_factor", 1.0)
        cumulative_actual = 0.0
        cumulative_base = 0.0

        style_meta_cache = {}
        for st in selected_quip_styles:
            style_meta_cache[st] = quip_repo.load_meta(st)

        quip_results = []
        total_casualties = 0.0
        has_quips = bool(quips)

        for idx, ruling in enumerate(chosen):
            comp = ruling.cand
            if curr_intrusion == 0:
                curr_intrusion = random.randint(1, 4)
            if curr_destruction == 0:
                curr_destruction = random.randint(1, 4)

            # 地标风貌切换（首次匹配视为切换到该风貌）时按性格敏感值调整坐标
            frequency = comp["landmark"].frequency
            if frequency != previous_frequency:
                curr_intrusion, curr_destruction = state_service.apply_landmark_switch(
                    personality_obj, curr_intrusion, curr_destruction, frequency)
                previous_frequency = frequency

            part = comp["part"]
            ratio = comp["ratio"]
            if comp["landmark"].frequency == "unique":
                lm = comp["landmark"]
                suffix = "高" if lm.dimension == "vertical" else (
                    "长" if lm.horizontal_type == "length" else "宽")
                size_str = format_size(comp['size'], base_size=height)
                lm_key = self.addresses.durability_key(lm)
                lm_addr = self.addresses.landmark_address(lm)
                if lm_addr:
                    landmark_addresses[lm_key] = lm_addr
                durability = landmark_durability.get(lm_key, 1.0)
                height_ratio = height / lm.size
                damage = durability_damage(ratio, height_ratio, curr_destruction)
                durability -= damage
                landmark_durability[lm_key] = durability
                durability_suffix = "（残破的）" if is_worn(durability) else ""
                compare_text = f"    └─ 约等于{lm.name}{durability_suffix}{suffix}度的{ratio:.2f}倍"
            else:
                # 非唯一地标的措辞与挂件版共用一份，见 logic.comparison_lines
                size_str, compare_text = comparison_lines(comp, height)

            quip_text = ""
            quip_style = ""
            coord = None
            actual_step = 0

            if has_quips:
                _lane_kwargs = {"lanes": ruling.lanes}
                if ruling.env_estimate is not None:
                    _lane_kwargs["env_estimate"] = ruling.env_estimate
                quip_text, quip_style, coord, actual_step, cumulative_actual, cumulative_base = select_quip_with_budget(
                    size_cat, curr_intrusion, curr_destruction,
                    quips_working, locked_coords,
                    cumulative_actual, cumulative_base, rate_factor,
                    step_index=idx,
                    selected_tags=selected_tags,
                    skip_base_prob=personality_obj.skip_base_prob,
                    posture_list=comp["posture"],
                    blocked_words=blocked_words,
                    **_lane_kwargs
                )
                if quip_text is None:
                    quip_text = "尺寸通过远程测量取得，尚未收集到事件记录。"
                    quip_style = ""
                else:
                    if quip_style:
                        arbitration["coverage"].setdefault(
                            "fired_styles", []).append(quip_style)
                    quip_text = quip_text.replace('{name}', name).replace('{nick}', nick)
                    allow_confusion_map = {}
                    if quip_style and quip_style in style_meta_cache:
                        meta = style_meta_cache[quip_style]
                        custom_types = meta.get("custom_types", {})
                        for letter in ("c", "d", "e"):
                            if letter in custom_types:
                                allow_confusion_map[letter] = custom_types[letter].get("allow_confusion", False)
                    quip_text = replace_quip_tags(
                        quip_text, quip_style, size_cat,
                        detail_pools, quips_working=quips_working,
                        intr=curr_intrusion, dest=curr_destruction,
                        enable_confusion=enable_confusion,
                        allow_confusion_map=allow_confusion_map
                    )
                    quip_text = quip_text.replace('{name}', name).replace('{nick}', nick)
                    quip_text = re.sub(r'\s*\[summary:.*?\]', '', quip_text)

            if quip_text is not None:
                curr_intrusion, curr_destruction = state_service.advance_coordinates(
                    personality_obj, curr_intrusion, curr_destruction, actual_step,
                    step_intrusion=curr_step_intrusion,
                    step_destruction=curr_step_destruction)
                # 步长演化（演化坐标后）：先按步进扣除 步长 -= 步进 × 敏感/重力，
                # 再向初始值恢复 0.2*个性强度 的比例差值，并叠加不适应性衰减
                # （坐标达到 0.5/4.5 后步长向 0 收敛）
                curr_step_intrusion, curr_step_destruction = \
                    state_service.evolve_step_rates(
                        personality_obj, curr_step_intrusion, curr_step_destruction,
                        actual_step,
                        intrusion=curr_intrusion, destruction=curr_destruction)

                if (4, 4) in locked_coords and curr_intrusion >= 4.0 and curr_destruction >= 4.0:
                    if "涩涩" in selected_tags:
                        locked_coords.remove((4, 4))
                    else:
                        prob = breakthrough_prob(greed, will, breakthrough_attempts)
                        if random.random() < prob:
                            locked_coords.remove((4, 4))
                        else:
                            breakthrough_attempts += 1

            effective_step = (actual_step if actual_step > 0
                              else _model_value("report.model.step_fallback"))
            env_factor = (compute_environment_factor(quip_text) if quip_text
                          else _model_value("report.model.env_factor_fallback"))
            casualty_increase = compute_casualty(
                height, effective_step, curr_destruction, quip_text or "", env_factor)
            total_casualties += casualty_increase

            quip_results.append({
                "part": part,
                "size_str": size_str,
                "compare_text": compare_text,
                "quip_text": quip_text,
                "quip_style": quip_style,
                "intrusion": curr_intrusion,
                "destruction": curr_destruction,
                "coord": coord,
                "environment_factor": env_factor,
                "step": effective_step,
                "casualty_increase": casualty_increase
            })

        return {
            **core_state,
            "comparisons": comparisons,
            "quip_results": quip_results,
            "curr_intrusion": curr_intrusion,
            "curr_destruction": curr_destruction,
            "step_intrusion": curr_step_intrusion,
            "step_destruction": curr_step_destruction,
            "total_casualties": total_casualties,
            "size_cat": size_cat,
            "style_meta_cache": style_meta_cache,
            "landmark_durability": landmark_durability,
            "landmark_addresses": landmark_addresses,
            "position": position_now,
            "engaged": arbitration["engaged"],
            "stuck": None,
            # 仲裁可解释性数据（P4a 只落数据不落 UI）
            "coverage": arbitration["coverage"],
            "promotions": arbitration["promotions"],
        }
