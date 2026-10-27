"""话题类型注册表与候选筛选（纯逻辑）。

``TopicKind`` 是三轴坐标里的“格点”：每个类型有自己的坐标、半径与增量提示词。
坐标只用于过滤与排序，不强制选最近的一个；真正的话题切换由 AI 在候选里
决定。本模块不读写存档，也不拼完整系统提示词。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence, Tuple

from services.chat.coordinates import ChatCoord

FALLBACK_KIND = "daily"
TOPIC_KIND_RADIUS = 0.35
TOPIC_REPEAT_PENALTY = 0.08
TOPIC_REPEAT_DECAY = 0.5
TOPIC_CANDIDATE_LIMIT = 5


@dataclass(frozen=True)
class TopicKind:
    """一个话题类型（注册表格点）。

    ``prompt`` 是叠加在人设之上的增量片段，不是完整 system prompt。
    ``priority`` 越大越优先；``cooldown`` 预留给后续的重复/冷却实现。
    """
    id: str
    label: str
    coord: ChatCoord
    radius: float = TOPIC_KIND_RADIUS
    prompt: str = ""
    tags: Tuple[str, ...] = ()
    priority: int = 0
    cooldown: float = 0.0

    def __post_init__(self):
        object.__setattr__(self, "id", str(self.id or "").strip())
        object.__setattr__(self, "label", str(self.label or "").strip())
        object.__setattr__(self, "coord", ChatCoord.from_values(self.coord))
        radius = self.radius
        try:
            radius = float(radius)
        except (TypeError, ValueError):
            radius = TOPIC_KIND_RADIUS
        if not math.isfinite(radius) or radius <= 0.0:
            radius = TOPIC_KIND_RADIUS
        object.__setattr__(self, "radius", radius)
        object.__setattr__(self, "prompt",
                           self.prompt if isinstance(self.prompt, str) else "")
        tags = self.tags if isinstance(self.tags, (list, tuple)) else ()
        object.__setattr__(self, "tags", tuple(str(tag) for tag in tags))
        try:
            priority = int(self.priority)
        except (TypeError, ValueError):
            priority = 0
        object.__setattr__(self, "priority", priority)
        try:
            cooldown = float(self.cooldown)
        except (TypeError, ValueError):
            cooldown = 0.0
        if not math.isfinite(cooldown) or cooldown < 0.0:
            cooldown = 0.0
        object.__setattr__(self, "cooldown", cooldown)


# 初始类型集（示例值，坐标/半径可调）。坐标顺序：
#   affection / tension / size_state
TOPIC_KINDS: Tuple[TopicKind, ...] = (
    TopicKind(
        id="daily", label="初识/日常", coord=ChatCoord(0.50, 0.35, 0.75),
        prompt=("语气轻松自然，像普通朋友日常寒暄：观察对方、聊眼前的小事、"
                "轻量试探。避免一下子太亲密或太敌对。"),
    ),
    TopicKind(
        id="spar", label="博弈/施压", coord=ChatCoord(0.25, 0.75, 0.80),
        prompt=("语气带压迫感，谈规则、挑衅、试探底线，用尺寸与力量压人；"
                "先维持言语层面的张力，不要立刻真的伤害对方。"),
    ),
    TopicKind(
        id="flirt", label="暧昧/亲昵", coord=ChatCoord(0.80, 0.30, 0.85),
        prompt=("语气放松亲昵，允许调情与暧昧的玩笑；距离感近，但仍保留"
                "角色自己的分寸，不要变成无条件讨好。"),
    ),
    TopicKind(
        id="reflect", label="思辨/抽离", coord=ChatCoord(0.70, 0.30, 0.30),
        prompt=("用抽象、思辨或旁观式的讨论回避身体的不适与紧张；像在借题"
                "想别的事，而不是平静地说教。"),
    ),
    TopicKind(
        id="body", label="身体尺寸", coord=ChatCoord(0.80, 0.75, 0.85),
        prompt=("话题落在尺寸、触及、占有与强烈互动上，强调她对自己身体的"
                "掌控感；不要把坐标或数值直接说出口。"),
    ),
    TopicKind(
        id="memory", label="回忆/沉重", coord=ChatCoord(0.75, 0.75, 0.30),
        prompt=("从共同经历、创伤或反复出现的画面谈起，语气沉、节奏慢；"
                "可以停在没说完的地方，不要强行开解。"),
    ),
    TopicKind(
        id="guard", label="戒备/恐惧", coord=ChatCoord(0.20, 0.75, 0.25),
        prompt=("保持戒备与距离，警告、驱逐或反复试探对方的意图；不打算"
                "久聊，随时可能结束。"),
    ),
    TopicKind(
        id="aloof", label="疏离/客套", coord=ChatCoord(0.20, 0.25, 0.30),
        prompt=("冷淡、客套、公事公办，回避私事与情绪；回答简短，不主动"
                "延伸话题。"),
    ),
    TopicKind(
        id="showoff", label="自得/炫耀", coord=ChatCoord(0.25, 0.25, 0.80),
        prompt=("她享受自己的尺寸与力量，带一点炫耀和自得，但与你保持"
                "距离，不轻易亲近。"),
    ),
)


def _kind_tuple(kinds: Optional[Iterable[TopicKind]]) -> Tuple[TopicKind, ...]:
    return TOPIC_KINDS if kinds is None else tuple(kinds)


def _coord(coord) -> ChatCoord:
    return ChatCoord.from_values(coord)


def find_topic_kind(kind_id: str,
                    kinds: Optional[Iterable[TopicKind]] = None
                    ) -> Optional[TopicKind]:
    """按 id 查类型；未知 id 返回 None。"""
    if not isinstance(kind_id, str):
        return None
    for kind in _kind_tuple(kinds):
        if kind.id == kind_id:
            return kind
    return None


def topic_kind_distance(coord, kind: TopicKind) -> float:
    return _coord(coord).distance(kind.coord)


def topic_kind_hint(kind: TopicKind) -> str:
    """候选列表里的一句区别：取增量提示词的第一句，太长再截断。"""
    prompt = kind.prompt.strip()
    for separator in ("。", "；", "\n"):
        index = prompt.find(separator)
        if index != -1:
            prompt = prompt[:index]
            break
    return prompt[:40]


def repeat_penalty(kind_id: str,
                   recent_kind_ids: Sequence[str] = (),
                   base: float = TOPIC_REPEAT_PENALTY,
                   decay: float = TOPIC_REPEAT_DECAY) -> float:
    """最近用过的类型重复惩罚；``recent_kind_ids`` 按新到旧排列。"""
    penalty = 0.0
    factor = 1.0
    for recent in recent_kind_ids or ():
        if recent == kind_id:
            penalty += base * factor
        factor *= decay
    return penalty


def kind_is_eligible(kind_id: str, coord,
                     kinds: Optional[Iterable[TopicKind]] = None) -> bool:
    kind = find_topic_kind(kind_id, kinds)
    return bool(kind is not None
                and topic_kind_distance(coord, kind) <= kind.radius)


def eligible_topic_kinds(coord,
                         kinds: Optional[Iterable[TopicKind]] = None
                         ) -> list:
    """半径内的类型，按距离、优先级排序；没有候选时返回空列表。"""
    point = _coord(coord)
    ranked = []
    for index, kind in enumerate(_kind_tuple(kinds)):
        distance = point.distance(kind.coord)
        if distance <= kind.radius:
            ranked.append((distance, -kind.priority, index, kind))
    ranked.sort(key=lambda item: item[:3])
    return [item[3] for item in ranked]


def rank_topic_kinds(coord,
                     kinds: Optional[Iterable[TopicKind]] = None,
                     recent_kind_ids: Sequence[str] = ()) -> list:
    """半径内的类型按（距离 + 重复惩罚，优先级）排序。"""
    point = _coord(coord)
    ranked = []
    for index, kind in enumerate(_kind_tuple(kinds)):
        distance = point.distance(kind.coord)
        if distance > kind.radius:
            continue
        score = distance + repeat_penalty(kind.id, recent_kind_ids)
        ranked.append((score, -kind.priority, index, kind))
    ranked.sort(key=lambda item: item[:3])
    return [item[3] for item in ranked]


def candidate_topic_kinds(coord,
                          kinds: Optional[Iterable[TopicKind]] = None,
                          recent_kind_ids: Sequence[str] = (),
                          limit: int = TOPIC_CANDIDATE_LIMIT) -> list:
    """可注入提示词的候选类型（默认最多 5 个）；不保证非空。"""
    try:
        count = max(0, int(limit))
    except (TypeError, ValueError):
        count = TOPIC_CANDIDATE_LIMIT
    return rank_topic_kinds(coord, kinds, recent_kind_ids)[:count]


def fallback_topic_kind(kinds: Optional[Iterable[TopicKind]] = None
                        ) -> Optional[TopicKind]:
    """无候选时的兜底类型；注册表缺 daily 时返回 None。"""
    return find_topic_kind(FALLBACK_KIND, kinds)
