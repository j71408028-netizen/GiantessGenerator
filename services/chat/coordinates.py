"""聊天三轴坐标：好感、紧张度、状态的纯函数。

坐标只由领域事实派生（聊天态度、世界态势、关系认知、演化事件、行动点与
个性强度），不读写存档、不依赖 UI。所有时间相关函数都可注入 ``now``，
便于测试。

话题开始时调用一次 :func:`build_chat_coord`，把结果存进 ``Topic.coord``
作为快照；之后即使世界事实变化，也用快照判断当前话题是否偏离。
"""

from __future__ import annotations

import datetime
import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

ATTITUDE_MIN = -100.0
ATTITUDE_MAX = 100.0

# 状态（size_state）公式的建议常量（见 docs 中的设计文档）。
AP_LOW = 50.0
EDGE_MARGIN = 0.5
W_R = 0.4
W_E = 0.5
W_A = 0.5
NEUTRAL_RESILIENCE = 0.6

# 紧张度（tension）公式的建议常量。
TENSION_TAU_HOURS = 48.0
TENSION_W_WORLD = 0.6
TENSION_W_IDENTITY = 0.4
TENSION_W_EVENT = 0.3
DEFAULT_WORLD_STANCE = "coexist"

# 世界态势的语义值；尺度（appear / abs_giant / rel_giant）不参与映射。
WORLD_STANCE_VALUES = {
    "worship": 0.10,
    "accept": 0.25,
    "coexist": 0.45,
    "alert": 0.70,
    "hostile": 0.90,
}

# 关系认知对紧张度的偏移；未知/未建立关系按 0（不额外加压）。
RELATIONSHIP_TENSION_OFFSETS = {
    "lover": -0.15,
    "close": -0.10,
    "friend": -0.05,
    "acquaintance": 0.0,
    "stranger": 0.05,
    "family": -0.10,
    "worshipper": -0.05,
    "subject": 0.05,
    "prey": 0.15,
    "rival": 0.15,
    "enemy": 0.25,
}


def _safe_float(value: Any, fallback: float) -> float:
    """不可解析 / 非有限数值回退到 ``fallback``。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if math.isfinite(number) else fallback


def _clamp01(value: Any, fallback: float = 0.0) -> float:
    return max(0.0, min(1.0, _safe_float(value, fallback)))


def _now(now: Optional[datetime.datetime]) -> datetime.datetime:
    return now if isinstance(now, datetime.datetime) else datetime.datetime.now()


def _as_datetime(value: Any) -> Optional[datetime.datetime]:
    if isinstance(value, datetime.datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.datetime.fromisoformat(value.strip())
    except ValueError:
        return None


def _age_seconds(changed_at: datetime.datetime,
                 now: datetime.datetime) -> float:
    """事件年龄（秒）；时区不一致时按本地墙上时间相减，未来事件按 0 计。"""
    try:
        delta = now - changed_at
    except TypeError:
        delta = now.replace(tzinfo=None) - changed_at.replace(tzinfo=None)
    return max(0.0, delta.total_seconds())


@dataclass(frozen=True)
class ChatCoord:
    """三轴坐标；所有分量夹在 0~1。"""
    affection: float = 0.5
    tension: float = 0.5
    size_state: float = 0.5

    def __post_init__(self):
        object.__setattr__(self, "affection", _clamp01(self.affection, 0.5))
        object.__setattr__(self, "tension", _clamp01(self.tension, 0.5))
        object.__setattr__(self, "size_state", _clamp01(self.size_state, 0.5))

    @classmethod
    def from_values(cls, values: Any,
                    fallback: Optional["ChatCoord"] = None) -> "ChatCoord":
        """从 ``[affection, tension, size_state]`` 或同类对象构造。"""
        if isinstance(values, cls):
            return values
        default = fallback if isinstance(fallback, cls) else cls()
        try:
            items = list(values)
        except TypeError:
            return default
        if len(items) != 3:
            return default
        return cls(items[0], items[1], items[2])

    def as_list(self) -> list:
        return [self.affection, self.tension, self.size_state]

    def distance(self, other: Any) -> float:
        """欧氏距离；用于半径过滤与同距离优先级比较。"""
        target = ChatCoord.from_values(other)
        return math.sqrt(
            (self.affection - target.affection) ** 2
            + (self.tension - target.tension) ** 2
            + (self.size_state - target.size_state) ** 2)


@dataclass(frozen=True)
class TensionEvent:
    """一条会影响紧张度的世界事件（权重 + 发生时间）。

    权重由调用方按事件类型给出（例如伤亡、结局、演化坐标变化）；本模块
    只负责按时间衰减求和。``changed_at`` 接受 datetime 或 ISO 字符串。
    """
    weight: float
    changed_at: Any


def affection_from_attitude(attitude: Any) -> float:
    """好感 = (attitude + 100) / 200，夹取 0~1。"""
    value = _safe_float(attitude, 0.0)
    return _clamp01((value - ATTITUDE_MIN) / (ATTITUDE_MAX - ATTITUDE_MIN), 0.5)


def world_stance_value(stance: Any) -> float:
    """世界态势转 0~1；语义字符串未知或数值非法时回退 coexist。"""
    fallback = WORLD_STANCE_VALUES[DEFAULT_WORLD_STANCE]
    if isinstance(stance, str):
        return WORLD_STANCE_VALUES.get(stance.strip().lower(), fallback)
    return _clamp01(stance, fallback)


def identity_tension_offset(kind: Any) -> float:
    """关系认知对紧张度的偏移；未知 kind 与未建立关系都返回 0。"""
    if isinstance(kind, str):
        return RELATIONSHIP_TENSION_OFFSETS.get(kind.strip().lower(), 0.0)
    return 0.0


def _event_parts(event: Any):
    if isinstance(event, TensionEvent):
        return event.weight, event.changed_at
    if isinstance(event, Mapping):
        return event.get("weight", 0.0), event.get("changed_at")
    return (getattr(event, "weight", 0.0),
            getattr(event, "changed_at", None))


def event_pressure(events: Iterable[Any],
                   now: Optional[datetime.datetime] = None,
                   tau_hours: float = TENSION_TAU_HOURS) -> float:
    """近期事件压力 = Σ weight × exp(-age / TAU)。

    调用方应只传入扫描窗口内的事件（例如最近 50 行演化记录或 7 天窗口），
    本函数不再做窗口裁剪。权重非正、时间/权重非法的条目静默跳过。
    """
    moment = _now(now)
    tau = _safe_float(tau_hours, TENSION_TAU_HOURS)
    if tau <= 0.0:
        tau = TENSION_TAU_HOURS
    total = 0.0
    for event in events or ():
        weight, changed_at = _event_parts(event)
        weight = _safe_float(weight, 0.0)
        if weight <= 0.0:
            continue
        changed = _as_datetime(changed_at)
        if changed is None:
            continue
        age = _age_seconds(changed, moment)
        total += weight * math.exp(-age / (tau * 3600.0))
    return total


def compute_tension(world_stance: Any = DEFAULT_WORLD_STANCE,
                    relationship_kind: Any = None,
                    events: Iterable[Any] = (),
                    now: Optional[datetime.datetime] = None,
                    *,
                    world_weight: float = TENSION_W_WORLD,
                    identity_weight: float = TENSION_W_IDENTITY,
                    event_weight: float = TENSION_W_EVENT) -> float:
    """按世界态势、身份认知与近期事件求紧张度，夹取 0~1。"""
    return _clamp01(
        _safe_float(world_weight, TENSION_W_WORLD)
        * world_stance_value(world_stance)
        + _safe_float(identity_weight, TENSION_W_IDENTITY)
        * identity_tension_offset(relationship_kind)
        + _safe_float(event_weight, TENSION_W_EVENT)
        * event_pressure(events, now=now))


def edge_pressure(x: Any, margin: float = EDGE_MARGIN) -> float:
    """演化坐标离 0.5 / 4.5 边界的软压力；越贴边越接近 1。"""
    value = _safe_float(x, 2.5)
    width = _safe_float(margin, EDGE_MARGIN)
    if width <= 0.0:
        return 0.0
    nearest_edge = min(value - 0.5, 4.5 - value)
    return _clamp01((width - nearest_edge) / width)


def action_point_deficit(action_points: Any, low: float = AP_LOW) -> float:
    """行动点不足的程度：0 = 余裕充足，1 = 耗尽。"""
    threshold = _safe_float(low, AP_LOW)
    if threshold <= 0.0:
        return 0.0
    value = _safe_float(action_points, 100.0)
    return _clamp01((threshold - value) / threshold)


def resilience(personality: Any) -> float:
    """个性强度对巨大化的适应力；没有性格时用中性值。"""
    if personality is None:
        return NEUTRAL_RESILIENCE
    return _clamp01(getattr(personality, "normalized_strength",
                            NEUTRAL_RESILIENCE), NEUTRAL_RESILIENCE)


def compute_size_state(intrusion: Any, destruction: Any, action_points: Any,
                       personality: Any = None) -> float:
    """状态 = 0.5 + 适应力 - 贴边压力 - 行动点不足，夹取 0~1。

    高值表示接纳/享受当前尺寸，低值表示不适或危机。
    """
    edge = max(edge_pressure(intrusion), edge_pressure(destruction))
    return _clamp01(
        0.5
        + W_R * resilience(personality)
        - W_E * edge
        - W_A * action_point_deficit(action_points))


def build_chat_coord(attitude: Any,
                     world_stance: Any = DEFAULT_WORLD_STANCE,
                     relationship_kind: Any = None,
                     events: Iterable[Any] = (),
                     intrusion: Any = 2.5,
                     destruction: Any = 2.5,
                     action_points: Any = 100,
                     personality: Any = None,
                     now: Optional[datetime.datetime] = None) -> ChatCoord:
    """组装三轴坐标；话题开始时应把结果作为快照保存。"""
    return ChatCoord(
        affection=affection_from_attitude(attitude),
        tension=compute_tension(world_stance, relationship_kind, events,
                                now=now),
        size_state=compute_size_state(intrusion, destruction, action_points,
                                      personality),
    )
