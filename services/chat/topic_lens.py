"""话题类型 = 存档 → 提示词的翻译器（纯逻辑）。

设计动机见 ``docs/designs/chat_topic_lens.md``：话题类型此前只是"一句体裁
提示"，而角色存档（隐藏介绍、已测尺寸、位置、作息、新闻、结局……）无论聊
什么都会被整段塞进系统提示词。本模块把这条通路反过来：

    存档字段（带层级/来源/渲染器） →  类型视角（开放哪些、权重、预算）
                                    →  只渲染被开放且放得下的字段

三条硬规则（对应 ``tests/check_chat_topic_lens.py``）：

1. **分层**：``public`` 字段才允许进"你是谁"身份层；``contextual`` 只能由
   类型视角开放；``secret`` 还额外要求视角允许透露（``disclosure``）且达到
   门槛（好感）。没有代码路径可以无条件注入 contextual / secret 字段。
2. **预算**：每个视角有字符预算，超出按权重贪心裁剪，同组字段最多取两条，
   丢弃原因进 ``LensTrace``，不是静默截断。
3. **可审计**：``render_lens`` 返回 (文本, trace)，调试面板 / 自检据此断言
   "这个类型下她看不到哪些信息"，而不是靠人读提示词。

类型本身（坐标、注意力框架、体裁、开放的字段与预算）定义在
``services/chat/topic_kinds.py`` 的 ``TopicKind``——一个话题一条，本模块只认
字段目录与渲染，不再维护第二张同 id 的表。

本模块不读写存档、不改话题状态机；纯函数，``now`` 可注入。
"""

from __future__ import annotations

import datetime
import math
from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Optional, Tuple

from core.address_model import format_addr_compact
from core.logic import format_size
from core.models import CharacterSnapshot, ChatState, Topic
from core.scale_reference import (destruction_level_text,
                                  intrusion_level_text, size_definition_text)
from services.character.profile_text import (build_live_context, describe_rhythm,
                                             describe_spatial)
from services.character.spatial import build_spatial_profile
from services.chat.coordinates import DEFAULT_WORLD_STANCE, ChatCoord
from services.chat.topic_kinds import (TopicKind, active_kinds,
                                       active_no_topic, lens_for)

# ---------- 字段层级 ----------
TIER_PUBLIC = "public"          # 可以进身份层，任何话题下都在
TIER_CONTEXTUAL = "contextual"  # 只能由类型视角开放
TIER_SECRET = "secret"          # 视角开放 + 好感门槛，才以"想不想说"的形式给出

# 隐藏介绍的门槛（聊天态度）。与 __init__.REVEAL_ATTITUDE 同值：接线上线后
# 那边只用于一次性聊天参数判定，这里是聊天内容侧的唯一判据。
HIDDEN_INTRO_ATTITUDE = 40

# 单个视角内，同一 group 最多取几条（防止新闻/世界类字段霸占预算）
GROUP_CAP = 2
# 预算裁剪时，剩余空间小于该值就不再塞字段（避免把一句话砍成半句）
MIN_FIELD_CHARS = 40

# 世界态势的定性描述（与 coordinates.WORLD_STANCE_VALUES 的键一一对应）
WORLD_STANCE_TEXTS = {
    "worship": "外界把你当神一样供着，你的存在被当成理所当然",
    "accept": "外界大体接受你，日子照常过",
    "coexist": "世界和你保持着勉强的共存，彼此都在适应",
    "alert": "外面的气氛紧绷，人们防着你",
    "hostile": "外界把你当成威胁，敌意摆在明面上",
}

# 意志状态是存档里的枚举值，直接拼进提示词会漏出内部字符串
WILL_STATUS_TEXTS = {
    "implemented": "你想要的规模已经得到了，心里踏实",
    "failed": "还没到你想要的程度，有点不甘",
    "within": "现在的规模落在你能接受的范围内",
}


@dataclass(frozen=True)
class LensContext:
    """一次渲染需要的全部输入（只读）。"""
    state: CharacterSnapshot
    chat_state: ChatState
    coord: ChatCoord = ChatCoord()
    world_stance: str = DEFAULT_WORLD_STANCE
    now: Optional[datetime.datetime] = None
    topic: Optional[Topic] = None


@dataclass(frozen=True)
class ArchiveField:
    """一条可从存档翻译成提示词的字段。

    ``render`` 返回空串表示"此刻没有这条信息"（例如没新闻、没测量记录），
    渲染器不会为它留空行。``weight`` 是字段自身的分量，与视角给的权重相乘
    决定预算内的先后。
    """
    id: str
    tier: str
    group: str
    render: Callable[[LensContext], str]
    weight: float = 1.0

    @property
    def is_secret(self) -> bool:
        return self.tier == TIER_SECRET


# ---------- 字段渲染器 ----------
def _size_text(ctx: LensContext) -> str:
    """只剩"我有多大"这一层；能做什么留给 self.scale，不是每次都念。"""
    state = ctx.state
    line = f"- 你现在的身高是 {format_size(state.height)}。"
    definition = size_definition_text(state.height).splitlines()
    if definition:
        line += " " + definition[0]
    return line


def _scale_text(ctx: LensContext) -> str:
    """尺寸能做什么 / 不能做什么：只有真正谈身体的话题才需要。"""
    lines = size_definition_text(ctx.state.height).splitlines()[1:]
    if not lines:
        return ""
    return "\n".join(f"- {line}" for line in lines)


def _edge_text(ctx: LensContext) -> str:
    state = ctx.state
    if not state.evolution and state.intrusion <= 0 and state.destruction <= 0:
        return ""
    return (f"- 你移动时对周围的影响：介入度方面{intrusion_level_text(state.intrusion)}；"
            f"破坏性方面{destruction_level_text(state.destruction)}")


def _measured_text(ctx: LensContext) -> str:
    measured = [(part, desc) for part, desc in ctx.state.size_unlocks.items()
                if desc and desc != "MEASURED"]
    if not measured:
        return ""
    return ("- 你已经量过、心里有数的部位尺寸："
            + "；".join(f"{part}（{desc}）" for part, desc in measured))


def _will_text(ctx: LensContext) -> str:
    raw = (ctx.state.will_status or "").strip()
    if not raw:
        return ""
    return "- 你对自己现在这个规模的感觉：" + WILL_STATUS_TEXTS.get(raw, raw)


def _greed_text(ctx: LensContext) -> str:
    """“再大一点”的念头强度（0~100 只译成三档定性），0 表示没有这条信息。"""
    try:
        greed = float(ctx.state.greed)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(greed) or greed <= 0.0:
        return ""
    if greed < 30.0:
        wording = "偶尔会冒出来，不算执念"
    elif greed < 70.0:
        wording = "经常在想，还压得住"
    else:
        wording = "几乎放不下，随时会顺着它走"
    return f"- 你想再长大的念头：{wording}"


def _rhythm_text(ctx: LensContext) -> str:
    text = describe_rhythm(ctx.state.evolution or [])
    return "- 你惯常的作息：\n  " + text.replace("\n", "\n  ") if text else ""


def _spatial_text(ctx: LensContext) -> str:
    text = describe_spatial(build_spatial_profile(ctx.state, now=ctx.now),
                            ctx.state.position)
    return "- 你常活动的范围：\n  " + text.replace("\n", "\n  ") if text else ""


def _live_text(ctx: LensContext) -> str:
    text = build_live_context(ctx.state, now=ctx.now)
    return text


def _position_text(ctx: LensContext) -> str:
    position = (ctx.state.position or "").strip()
    if not position:
        return ""
    return f"- 你此刻在：{format_addr_compact(position)}"


def _news_text(ctx: LensContext) -> str:
    news = (ctx.state.last_news or "").strip()
    if not news:
        return ""
    return f"- 最近关于你的新闻：{news[:120]}"


def _stance_text(ctx: LensContext) -> str:
    key = (ctx.world_stance or DEFAULT_WORLD_STANCE).strip().lower()
    text = WORLD_STANCE_TEXTS.get(key)
    return f"- 外界对你的态度：{text}" if text else ""


def _endings_text(ctx: LensContext) -> str:
    endings = [str(e.get("name", "")).strip()
               for e in (ctx.state.achieved_endings or []) if isinstance(e, dict)]
    endings = [name for name in endings if name][-3:]
    return "- 你身上已经发生过的大事：" + "、".join(endings) if endings else ""


def _travel_text(ctx: LensContext) -> str:
    """旅居过的世界（世界履历里的包名，最近三个）；没有履历时返回空。"""
    names: List[str] = []
    for item in ctx.state.world_history or ():
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("pack_id") or "").strip()
        if name and name not in names:
            names.append(name)
    return "- 你待过的世界：" + "、".join(names[-3:]) if names else ""


def _hidden_intro_text(ctx: LensContext) -> str:
    hidden = (ctx.state.intro_hidden or "").strip()
    if not hidden:
        return ""
    return ("- 有件事你平时不主动说（只有话题自然走到这里、或你愿意的时候，"
            "才用你自己的话讲一点，不要像念资料）：\n  " + hidden)


def _birthday_text(ctx: LensContext) -> str:
    birthday = (ctx.state.birthday or "").strip()
    return f"- 你的生日：{birthday}" if birthday else ""


ARCHIVE_FIELDS: Tuple[ArchiveField, ...] = (
    ArchiveField("pub.name", TIER_PUBLIC, "identity", lambda c: "", 0.0),
    ArchiveField("pub.personality", TIER_PUBLIC, "identity", lambda c: "", 0.0),
    ArchiveField("pub.intro", TIER_PUBLIC, "identity", lambda c: "", 0.0),
    ArchiveField("pub.tags", TIER_PUBLIC, "identity", lambda c: "", 0.0),
    ArchiveField("self.size", TIER_CONTEXTUAL, "self", _size_text, 3.0),
    ArchiveField("self.scale", TIER_CONTEXTUAL, "body", _scale_text, 1.4),
    ArchiveField("self.edge", TIER_CONTEXTUAL, "self", _edge_text, 1.5),
    ArchiveField("self.measured", TIER_CONTEXTUAL, "body", _measured_text, 2.0),
    ArchiveField("self.will", TIER_CONTEXTUAL, "self", _will_text, 1.0),
    ArchiveField("self.greed", TIER_CONTEXTUAL, "self", _greed_text, 1.0),
    ArchiveField("self.rhythm", TIER_CONTEXTUAL, "life", _rhythm_text, 1.0),
    ArchiveField("self.spatial", TIER_CONTEXTUAL, "life", _spatial_text, 1.0),
    ArchiveField("self.live", TIER_CONTEXTUAL, "life", _live_text, 1.2),
    ArchiveField("self.position", TIER_CONTEXTUAL, "place", _position_text, 1.0),
    ArchiveField("world.news", TIER_CONTEXTUAL, "world", _news_text, 2.0),
    ArchiveField("world.stance", TIER_CONTEXTUAL, "world", _stance_text, 1.5),
    ArchiveField("world.endings", TIER_CONTEXTUAL, "world", _endings_text, 1.2),
    ArchiveField("world.travel", TIER_CONTEXTUAL, "world", _travel_text, 1.0),
    ArchiveField("private.hidden", TIER_SECRET, "private", _hidden_intro_text, 2.5),
    ArchiveField("private.birthday", TIER_SECRET, "private", _birthday_text, 0.6),
)

FIELD_INDEX = {item.id: item for item in ARCHIVE_FIELDS}


@dataclass
class LensTrace:
    """一次渲染的账本：哪些字段进了提示词、哪些被什么理由挡掉。"""
    lens_id: str
    budget: int = 0
    chars: int = 0          # 整段渲染结果（含标题 / 框架 / 体裁行）
    field_chars: int = 0    # 只有字段文本，预算约束的是这个
    included: List[Tuple[str, float, int]] = field(default_factory=list)
    dropped: List[Tuple[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        kept = "、".join(f"{fid}({w:.2f})" for fid, w, _ in self.included) or "无"
        gone = "、".join(f"{fid}:{why}" for fid, why in self.dropped) or "无"
        return (f"[{self.lens_id}] 字段 {self.field_chars}/{self.budget} 字"
                f"（整段 {self.chars}）| 取用：{kept} | 挡掉：{gone}")


def _secret_allowed(ctx: LensContext, kind: TopicKind) -> bool:
    """secret 字段的总闸：视角允许透露 + 好感达到门槛。"""
    if not kind.disclosure:
        return False
    return ctx.chat_state.attitude >= HIDDEN_INTRO_ATTITUDE


def _field_text(field_id: str, ctx: LensContext) -> str:
    item = FIELD_INDEX.get(field_id)
    if item is None or item.tier == TIER_PUBLIC:
        return ""
    try:
        text = item.render(ctx)
    except Exception:
        return ""
    return text.strip() if isinstance(text, str) else ""


def collect_fields(kind: TopicKind, ctx: LensContext
                   ) -> Tuple[List[Tuple[float, int, str, str]], List[Tuple[str, str]]]:
    """按视角挑出可用字段，返回 [(分数, 顺序, 字段 id, 文本)] 与挡掉清单。"""
    picked, dropped = [], []
    for order, (field_id, weight) in enumerate(kind.opens):
        item = FIELD_INDEX.get(field_id)
        if item is None:
            dropped.append((field_id, "unregistered"))
            continue
        if item.tier == TIER_PUBLIC:
            dropped.append((field_id, "identity-only"))
            continue
        if field_id in kind.denies:
            dropped.append((field_id, "denied"))
            continue
        if item.is_secret and not _secret_allowed(ctx, kind):
            dropped.append((field_id, "gated"))
            continue
        text = _field_text(field_id, ctx)
        if not text:
            dropped.append((field_id, "empty"))
            continue
        picked.append((float(weight) * item.weight, order, field_id, text))
    picked.sort(key=lambda entry: (-entry[0], entry[1]))
    return picked, dropped


def pack_fields(picked: Iterable[Tuple[float, int, str, str]], kind: TopicKind,
                trace: LensTrace) -> List[str]:
    """按权重贪心装进预算；同组最多 GROUP_CAP 条。"""
    remaining = max(0, int(kind.budget))
    groups = {}
    texts = []
    for score, _, field_id, text in picked:
        group = FIELD_INDEX[field_id].group
        if groups.get(group, 0) >= GROUP_CAP:
            trace.dropped.append((field_id, "group-cap"))
            continue
        if len(text) > remaining or remaining < MIN_FIELD_CHARS:
            trace.dropped.append((field_id, "budget"))
            continue
        remaining -= len(text) + 1
        groups[group] = groups.get(group, 0) + 1
        trace.included.append((field_id, score, len(text)))
        texts.append(text)
    return texts


def render_lens(kind: TopicKind, ctx: LensContext) -> Tuple[str, LensTrace]:
    """渲染一个话题类型的提示词段落，返回 (文本, trace)。

    顺序即权重：框架（聊什么、注意什么）→ 字段（知道什么）→ 体裁（怎么打字）。
    框架行与体裁行都不受预算裁剪——它们约束的是写法，不是信息量。
    """
    head = (f"【此刻你注意到的事】（{kind.label}）" if kind.label
            else "【此刻你注意到的事】")
    lines = [head, kind.frame]
    picked, dropped = collect_fields(kind, ctx)
    trace = LensTrace(lens_id=kind.id, budget=int(kind.budget),
                      dropped=list(dropped))
    texts = pack_fields(picked, kind, trace)
    if texts:
        lines.append("")
        lines.extend(texts)
    genre = kind.prompt.strip()
    if genre:
        lines.append("")
        lines.append("说话的体裁：" + genre)
    body = "\n".join(lines)
    trace.chars = len(body)
    trace.field_chars = sum(len(text) for text in texts)
    return body, trace


def render_topic_lens(ctx: LensContext) -> Tuple[str, LensTrace]:
    """按当前活跃话题渲染（无话题时用无话题视角）。"""
    kind_id = getattr(ctx.topic, "kind_id", "") if ctx.topic is not None else ""
    return render_lens(lens_for(kind_id), ctx)


def identity_frame(state: CharacterSnapshot) -> str:
    """聊天用的最小身份层：只允许 public 层字段。

    存档里的其他内容一律进不了这一段——它们必须由话题类型的视角开放，
    见 ``render_lens``。这是"信息不经过话题不能到达 AI"的代码体现。
    """
    personality = state.personality
    nick = state.nick or ""
    lines = [f"名字：{state.name}" + (f"（昵称：{nick}）" if nick else "")]
    if personality is not None:
        lines.append(f"性格：{personality.description or '她有着独特的性格。'}")
    lines.append(f"公开介绍：{state.intro_visible or '（无）'}")
    if state.selected_tags:
        lines.append("个性标签：" + " ".join(f"#{t}" for t in state.selected_tags))
    return "\n".join(lines)


def public_field_ids() -> List[str]:
    """public 层字段 id（身份层可用的全集）。"""
    return [item.id for item in ARCHIVE_FIELDS if item.tier == TIER_PUBLIC]


def lens_coverage(kinds: Optional[Iterable[TopicKind]] = None,
                  no_topic: Optional[TopicKind] = None) -> List[str]:
    """注册表自检：返回所有视角里指向未注册/越层字段的 (类型, 字段) 描述。"""
    entries = list(active_kinds() if kinds is None else kinds)
    entries.append(no_topic if isinstance(no_topic, TopicKind)
                   else active_no_topic())
    problems = []
    for kind in entries:
        name = getattr(kind, "id", "") or "?"
        for field_id, _ in getattr(kind, "opens", ()):
            if field_id not in FIELD_INDEX:
                problems.append(f"{name}:{field_id}")
            elif FIELD_INDEX[field_id].tier == TIER_PUBLIC:
                problems.append(f"{name}:{field_id}(public)")
        for field_id in getattr(kind, "denies", ()):
            if field_id not in FIELD_INDEX:
                problems.append(f"{name}:{field_id}(deny)")
    return problems
