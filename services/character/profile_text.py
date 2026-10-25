# -*- coding: utf-8 -*-
"""角色档案派生层的文本化出口：作息与空间偏好的可读结论与实时情境提示。

四个入口都是行为包钩子，scope 为字面量 ``"profile"``（契约与模块位置
无关，已部署行为包按 ``"profile.<name>"`` 字符串覆写）：

    profile.describe_rhythm   作息历史结论（档案导出 / 聊天人设）
    profile.rhythm_now_hint   作息实时提示（聊天系统提示词）
    profile.describe_spatial  空间偏好历史结论
    profile.spatial_now_hint  空间实时提示

返回约定：纯文本多行（不掺 HTML，导出层负责转义排版）；实时提示在
样本不足 / 零证据时返回空串，消费方据此整行省略。本模块只做派生视图
的措辞：不学习、不持久化，模型一律从演化表现场学习（rhythm.py /
spatial.py 的装配入口），供档案导出与聊天等消费方共用。
"""

import datetime
from typing import Optional

from core.behavior_runtime import behavior_hook

from services.character.rhythm import RhythmService
from services.character.spatial import SpatialProfile, build_spatial_profile

# 成熟度门槛（活动样本数）：低于 _HINT_MIN_SAMPLES 实时提示整体缄默；
# 低于 _MATURE_SAMPLES 历史结论加"初步观察"措辞
_HINT_MIN_SAMPLES = 6.0
_MATURE_SAMPLES = 24.0
# 实时作息分档阈值（factor_at 围绕 1.0；中间档不产生提示以压噪声）
_HINT_ACTIVE = 1.3
_HINT_REST = 0.7

_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _bucket_label(bucket: int) -> str:
    """2 小时桶的时段文本（如 20-22 时）。"""
    return f"{bucket * 2}-{bucket * 2 + 2} 时"


def _env_preference_wording(value: float) -> str:
    """环境偏好代表值 → 措辞（值高 ≈ 偏爱人烟 / 值低 ≈ 偏爱荒僻）。"""
    if value < 0.45:
        return "偏爱荒僻、少有人烟的地带"
    if value < 0.75:
        return "偏向人烟稀疏的城郊野外"
    if value <= 1.05:
        return "对环境不挑，人烟多寡都待得住"
    return "偏爱人烟密集、热闹繁华的地带"


def _env_now_wording(value: float) -> str:
    """当前环境估计 → 措辞；寻常区间不产生提示以压噪声。"""
    if value < 0.5:
        return "周边环境颇为荒僻，人烟稀少。"
    if value < 0.9:
        return "周边人烟不算稠密。"
    if value > 1.2:
        return "周边相当热闹，人烟密集。"
    return ""


# ==================== 作息：历史结论 / 实时提示 ====================

@behavior_hook("profile", "describe_rhythm", layer="module", contract="strategy")
def describe_rhythm(evolution: list,
                    now: Optional[datetime.datetime] = None) -> str:
    """作息历史结论：整体活跃/安静时段 + 周末与工作日差异 + 成熟度措辞。

    ``now`` 不参与结论计算（结论只看历史网格），保留参数与实时提示
    同型，测试可统一注入时钟。
    """
    rhythm = RhythmService().learn_from_evolution(evolution)
    total = rhythm.total_samples()
    if total <= 0:
        return ""
    grid = rhythm.learned_grid()
    maturity = ("当前记录甚少，仅供参考" if total < _MATURE_SAMPLES
                else "已较稳定")
    bucket_mean = [sum(grid[w][b] for w in range(7)) / 7.0 for b in range(12)]
    hi = max(range(12), key=bucket_mean.__getitem__)
    lo = min(range(12), key=bucket_mean.__getitem__)
    lines = [f"作息（约 {int(total)} 次活动记录，{maturity}）："
             f"整体最活跃的时段在{_bucket_label(hi)}，"
             f"{_bucket_label(lo)}最沉静。"]
    # 周内对比只在有证据的格子上做：原始网格均值会被昼夜先验淹没
    counts = rhythm.counts_grid()

    def _side_mean(days):
        vals = [grid[w][b] for w in days for b in range(12) if counts[w][b] > 0]
        return sum(vals) / len(vals) if vals else 0.0

    weekday = _side_mean(range(5))
    weekend = _side_mean(range(5, 7))
    if weekday > 0 and weekend > 0:
        ratio = weekend / weekday
        if ratio >= 1.2:
            lines.append("在有记录的时段里，周末的强度明显更高。")
        elif ratio <= 0.8:
            lines.append("在有记录的时段里，工作日的强度明显更高。")
    return "\n".join(lines)


@behavior_hook("profile", "rhythm_now_hint", layer="module", contract="strategy")
def rhythm_now_hint(evolution: list,
                    now: Optional[datetime.datetime] = None) -> str:
    """作息实时提示：当前时刻落在活跃期 / 休整期才产生一行提示。

    措辞不带主语，聊天（第二人称语境）与档案（观察者语境）均可直接
    使用；样本不足返回空串。
    """
    rhythm = RhythmService().learn_from_evolution(evolution)
    if rhythm.total_samples() < _HINT_MIN_SAMPLES:
        return ""
    now = now or datetime.datetime.now()
    factor = rhythm.factor_at(now)
    stamp = f"{_WEEKDAYS[now.weekday()]} {_bucket_label(now.hour // 2)}"
    if factor >= _HINT_ACTIVE:
        return f"现在（{stamp}）正值惯常活跃时段，精力充沛，更可能外出活动。"
    if factor <= _HINT_REST:
        return f"现在（{stamp}）是惯常的歇息时段，行动和回应都会少一些。"
    return ""


# ==================== 空间：历史结论 / 实时提示 ====================

@behavior_hook("profile", "describe_spatial", layer="module", contract="strategy")
def describe_spatial(profile: SpatialProfile, position: str = "") -> str:
    """空间偏好历史结论：常活动地带 + 环境偏好 + 手工活动区域。

    ``profile`` 是 ``build_spatial_profile`` 的装配结果；``position``
    当前未参与结论（留给覆盖实现做"此处即常活动地带"类措辞）。
    """
    del position
    lines = []
    learned = profile.learned
    if learned is not None and learned.has_data():
        tops = learned.top_regions(2)
        if tops:
            names = "、".join("·".join(k) for k, _ in tops)
            lead = ("初步看来" if learned.total_weight() < _MATURE_SAMPLES
                    else "")
            label = f"常活动地带（{lead}）" if lead else "常活动地带"
            lines.append(f"{label}：{names}。")
    pref = profile.env_preference
    pref_env = pref.preferred_env() if pref is not None else None
    if pref_env is not None:
        value, confidence = pref_env
        wording = _env_preference_wording(value)
        lead = "初步看来" if confidence < 0.5 else ""
        lines.append(f"对环境的偏好{f'（{lead}）' if lead else ''}：{wording}。")
    regions = [r.strip() for r in (profile.activity_regions or []) if r.strip()]
    if regions:
        lines.append("指定的活动区域：" + "、".join(regions) + "。")
    return "\n".join(lines)


@behavior_hook("profile", "spatial_now_hint", layer="module", contract="strategy")
def spatial_now_hint(profile: SpatialProfile, position: str = "") -> str:
    """空间实时提示：当前位置相对偏好的评价 + 环境口语化描述。

    手工活动区域、区域树、环境估计三路均零证据时返回空串。
    """
    pos = (position or "").strip()
    if not pos:
        return ""
    lines = []
    if profile.in_activity_region(pos):
        lines.append("当前所在位置属于指定的活动区域。")
    learned = profile.learned
    if learned is not None and learned.has_data() and not lines:
        factor = profile.region_factor(pos)
        if factor >= _HINT_ACTIVE:
            lines.append("当前这一带是常来常往的地方。")
        elif factor <= _HINT_REST:
            lines.append("当前这一带较少涉足，还比较陌生。")
    env_est = profile.env_estimate(pos)
    if env_est is not None:
        wording = _env_now_wording(env_est)
        if wording:
            lines.append(wording)
    return "\n".join(lines)


# ==================== 便捷装配（非钩子） ====================

def build_live_context(state, now: Optional[datetime.datetime] = None) -> str:
    """两路实时提示的合并入口（聊天系统提示词 / 主动搭话上下文共用）。

    ``state`` 只需 evolution / position 属性；两路都空时返回空串。
    """
    profile = build_spatial_profile(state, now=now)
    parts = [rhythm_now_hint(getattr(state, "evolution", None) or [], now),
             spatial_now_hint(profile, getattr(state, "position", "") or "")]
    return "\n".join(p for p in parts if p)
