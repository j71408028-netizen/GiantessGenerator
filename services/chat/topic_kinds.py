"""话题类型注册表：坐标、体裁与信息视角的唯一一处定义（纯逻辑）。

每个类型有两重身份，写在同一条 ``TopicKind`` 里：

- **坐标**（``coord`` / ``radius``）：三轴格点，决定此刻能不能被选到、候选怎么
  排序。只用于过滤与排序，不强制选最近的一个。
- **翻译器**（``frame`` / ``prompt`` / ``focus`` / ``opens`` / ``denies`` /
  ``budget`` / ``disclosure``）：把这个类型下"她此刻该注意到什么"翻译成提示词
  片段。字段目录、渲染与预算裁剪在 ``services/chat/topic_lens.py``。

三件文案的权重依次是 ``frame`` > ``prompt`` > ``focus``：``frame`` 决定聊什么、
注意力落在哪；``prompt`` 只管体裁（句长、节奏、用词层级、标点），不承担态度；
``focus`` 是候选列表里给 AI 看的预告。话题内容（挑衅、试探、调情……）写进
``frame`` 而不是 ``prompt``——否则注意力范围与说话腔调会互相打架。

增删一个话题只需要动 ``TOPIC_KINDS`` 里的一条；``topic_lens`` 不再维护第二张
同 id 的表，也就不会出现"坐标改了、视角没跟上"的半成品类型。

面向世界包的两个入口（``topics`` 资源类型经 ``services/chat/topic_tables.py``
接线，见 ``docs/designs/chat_topic_lens.md`` §8）：

- ``topic_kind_from_dict`` / ``topic_kind_to_dict``：JSON 条目 ↔ ``TopicKind``；
- ``install_topic_kinds`` / ``reset_topic_kinds``：校验通过后整表替换或还原。

``tags`` 承担**软分类**：``relation``（由关系位置决定的交流状态）与 ``topic``
（指向某件具体的事或东西）。它只用于查阅与人工分组（``kinds_with_tag``），
不参与半径过滤、排序或校验——同一个类型同时挂两个标签也不会改变行为。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from services.chat.coordinates import ChatCoord

FALLBACK_KIND = "daily"
TOPIC_KIND_RADIUS = 0.35
TOPIC_REPEAT_PENALTY = 0.08
TOPIC_REPEAT_DECAY = 0.5
TOPIC_CANDIDATE_LIMIT = 5
# 软分类标签：只决定"查阅时归到哪一栏"，不参与筛选、排序与校验
KIND_TAG_RELATION = "relation"   # 关系状态：由两人的距离与位置决定，不指向具体事
KIND_TAG_TOPIC = "topic"         # 话题：指向某件具体的事、东西或看法
TAG_LABELS = {KIND_TAG_RELATION: "关系状态", KIND_TAG_TOPIC: "话题"}
# 候选列表里一句区别的字符上限（见 topic_kind_hint）
_HINT_CHARS = 40
# 类型视角的默认字符预算（只约束字段文本，见 topic_lens.pack_fields）
DEFAULT_TOPIC_BUDGET = 800


# ---------- 归一化 ----------
def _normalize_opens(value: Any) -> Tuple[Tuple[str, float], ...]:
    """``opens`` 接受 {字段: 权重} / [(字段, 权重)] / [字段]，统一成二元组。"""
    if isinstance(value, dict):
        items = list(value.items())
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        return ()
    result: List[Tuple[str, float]] = []
    seen = set()
    for item in items:
        if isinstance(item, str):
            field_id, weight = item, 1.0
        else:
            try:
                field_id, weight = item
            except (TypeError, ValueError):
                continue
        field_id = str(field_id or "").strip()
        if not field_id or field_id in seen:
            continue
        try:
            score = float(weight)
        except (TypeError, ValueError):
            score = 1.0
        if not math.isfinite(score) or score < 0.0:
            score = 0.0
        seen.add(field_id)
        result.append((field_id, score))
    return tuple(result)


def _normalize_ids(value: Any) -> Tuple[str, ...]:
    """``denies`` / ``tags``：去空、去重、保序。"""
    if isinstance(value, str):
        value = (value,)
    try:
        items = list(value)
    except TypeError:
        return ()
    result, seen = [], set()
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return tuple(result)


def _normalize_budget(value: Any) -> int:
    """预算非法时回退默认值；0 合法，表示"只给框架、不给字段"。"""
    try:
        budget = int(value)
    except (TypeError, ValueError):
        return DEFAULT_TOPIC_BUDGET
    return budget if budget >= 0 else DEFAULT_TOPIC_BUDGET


# ---------- 类型 ----------
@dataclass(frozen=True)
class TopicKind:
    """一个话题类型：坐标格点 + 注意力框架 + 体裁 + 信息视角。

    ``frame``     注意力框架（首要）：此刻聊什么、注意力落在哪；
    ``prompt``    体裁（次要）：句长、节奏、用词层级、标点，不管态度；
    ``focus``     候选列表里"选它会更关注什么"，只在选之前出现；
    ``opens``     (字段 id, 权重)；只有列出的字段才可能被渲染；
    ``denies``    显式关闭的字段（与 opens 冲突时按关闭处理）；
    ``budget``    字段文本的字符预算；
    ``disclosure`` 是否允许 secret 字段（仍受好感门槛约束）；
    ``virtual``   不是可选类型，只在"没有话题"时充当兜底视角。
    """

    id: str
    label: str
    coord: ChatCoord
    radius: float = TOPIC_KIND_RADIUS
    prompt: str = ""
    focus: str = ""
    frame: str = ""
    opens: Tuple[Tuple[str, float], ...] = ()
    denies: Tuple[str, ...] = ()
    budget: int = DEFAULT_TOPIC_BUDGET
    disclosure: bool = False
    tags: Tuple[str, ...] = ()
    priority: int = 0
    cooldown: float = 0.0
    virtual: bool = False

    def __post_init__(self):
        object.__setattr__(self, "id", str(self.id or "").strip())
        object.__setattr__(self, "label", str(self.label or "").strip())
        object.__setattr__(self, "coord", ChatCoord.from_values(self.coord))
        try:
            radius = float(self.radius)
        except (TypeError, ValueError):
            radius = TOPIC_KIND_RADIUS
        if not math.isfinite(radius) or radius <= 0.0:
            radius = TOPIC_KIND_RADIUS
        object.__setattr__(self, "radius", radius)
        for name in ("prompt", "focus", "frame"):
            value = getattr(self, name)
            object.__setattr__(self, name, value if isinstance(value, str) else "")
        object.__setattr__(self, "opens", _normalize_opens(self.opens))
        object.__setattr__(self, "denies", _normalize_ids(self.denies))
        object.__setattr__(self, "budget", _normalize_budget(self.budget))
        object.__setattr__(self, "disclosure", bool(self.disclosure))
        object.__setattr__(self, "tags", _normalize_ids(self.tags))
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
        object.__setattr__(self, "virtual", bool(self.virtual))

    def wants(self, field_id: str) -> bool:
        """这个视角是否申请了该字段（denies 优先）。"""
        if field_id in self.denies:
            return False
        return any(item == field_id for item, _ in self.opens)

    def weight_for(self, field_id: str) -> float:
        for item, weight in self.opens:
            if item == field_id:
                return weight
        return 0.0


# 无活跃话题时的虚拟视角：不是第十种类型，只是"还没开题"时的最小视野
NO_TOPIC_LENS = TopicKind(
    id="open",
    label="",
    coord=ChatCoord(0.5, 0.5, 0.5),
    prompt="像平时随手回消息：短句、口语，想到什么说什么。",
    focus="此刻的处境",
    frame="还没有定下来的话题。你先看看此刻自己在哪、在做什么，再决定要不要开口。",
    opens=(("self.live", 1.2), ("self.position", 1.0), ("self.size", 0.8),
           ("world.news", 0.7), ("self.rhythm", 0.5)),
    budget=520,
    virtual=True,
)


# ---------- 注册表（增删话题只动这里） ----------
# 坐标顺序：affection / tension / size_state
#
# 软分类速查（由 tags 标注，只用于查阅，不参与筛选）：
#   关系状态 relation：daily spar flirt guard aloof showoff memory elders
#   话题     topic   ：body reflect sharing talk mundane plan encounter
#                      observation hobby desire
TOPIC_KINDS: Tuple[TopicKind, ...] = (
    TopicKind(
        id="daily", label="日常", coord=ChatCoord(0.50, 0.35, 0.75),
        tags=(KIND_TAG_RELATION,),
        prompt=("短句、口语，句子松散，可以含语气词和少量网络"
                "用语；一条消息一两句话就够。"),
        focus="眼前的小事与对方的近况",
        frame=("你现在只想随便聊聊：对方在做什么、今天过得怎么样。可以顺手"
               "试探一两句，但不必一下子靠得太近。"),
        opens=(("self.live", 1.3), ("self.position", 1.0), ("self.size", 0.8),
               ("self.rhythm", 0.8), ("world.news", 0.4),
               ("private.birthday", 0.3)),
        budget=700,
    ),
    TopicKind(
        id="spar", label="博弈", coord=ChatCoord(0.25, 0.75, 0.80),
        tags=(KIND_TAG_RELATION,), 
        prompt=("句子短、收得干净，多用陈述句和命令句，少用语气词、不用表情"
                "符号；不铺垫、不解释。"),
        focus="外界的反应、规则与底线",
        frame=("你在掂量对方：他敢到哪一步、外面的世界又是怎么议论你的。可以"
               "立规矩、挑衅、拿尺寸压人，但先停在言语上，不动真的。"),
        opens=(("world.stance", 1.7), ("world.news", 1.5), ("self.edge", 1.5),
               ("self.size", 1.2), ("self.measured", 0.8)),
        denies=("private.hidden", "private.birthday"),
        budget=820,
    ),
    TopicKind(
        id="flirt", label="暧昧", coord=ChatCoord(0.80, 0.30, 0.85),
        tags=(KIND_TAG_RELATION,), 
        prompt=("短句、跳着说，常用半句和省略号，语气词多（欸、嘛、呢）；"
                "偶尔留一句没说完的钩子。"),
        focus="你自己那点没说出口的事",
        frame=("你想让他多知道一点你，但不会一次说完。可以说些平时不讲的、"
               "带点试探的话；分寸自己把握，不必变成讨好。"),
        opens=(("private.hidden", 1.9), ("self.will", 1.0), ("self.live", 1.0),
               ("private.birthday", 0.8), ("self.size", 0.6)),
        denies=("world.news", "world.stance"),
        budget=820,
        disclosure=True,
    ),
    TopicKind(
        id="reflect", label="思辨", coord=ChatCoord(0.70, 0.30, 0.30),
        tags=(KIND_TAG_TOPIC,), 
        prompt=("长句、偏书面语，可以自问自答、用破折号插入；不用网络用语，"
                "不用表情符号。"),
        focus="你怎么看自己身上发生的变化",
        frame=("你在往后退一步看自己：这种尺寸、这种变化对你意味着什么。可以"
               "借题说别的，但别变成平静地说教。"),
        opens=(("self.will", 1.5), ("self.size", 1.3), ("self.edge", 1.2),
               ("self.rhythm", 1.0), ("world.endings", 0.8)),
        denies=("world.news",),
        budget=780,
    ),
    TopicKind(
        id="body", label="身体尺寸", coord=ChatCoord(0.76, 0.55, 0.82),
        tags=(KIND_TAG_TOPIC,), 
        prompt=("句子短、分得碎，动词和具体名词多、形容词少；节奏慢，一条只"
                "说一件事。"),
        focus="你自己的尺寸与身体感受",
        frame=("注意力全落在你自己身上：多大、多重、碰到东西是什么感觉。"
               "谈的是身体本身，别人的看法先放着。"),
        opens=(("self.measured", 2.0), ("self.scale", 2.2), ("self.size", 1.8),
               ("self.edge", 1.0), ("self.live", 0.5)),
        denies=("world.news", "world.stance", "private.hidden"),
        budget=780,
    ),
    TopicKind(
        id="memory", label="回忆", coord=ChatCoord(0.75, 0.75, 0.30),
        tags=(KIND_TAG_RELATION,), 
        prompt=("句子偏长、节奏慢，常用省略号和「……后来」这类停顿；口语但"
                "克制，不带网络用语。"),
        focus="你们共同经历过的事",
        frame=("从已经发生过的事往回说：那些画面、那些没说完的话。可以停在"
               "没说完的地方，不必急着开解。"),
        opens=(("world.endings", 1.8), ("private.hidden", 1.2),
               ("self.will", 1.2), ("self.spatial", 0.8)),
        denies=("world.news", "self.measured"),
        budget=820,
        disclosure=True,
    ),
    TopicKind(
        id="guard", label="戒备", coord=ChatCoord(0.20, 0.75, 0.25),
        tags=(KIND_TAG_RELATION,),
        prompt=("极短的句子，常常只有几个字；陈述与命令为主，不用语气词，"
                "不解释理由。"),
        focus="这个人是否危险",
        frame=("你在评估这个人会不会伤到你、会不会把别人引来。可以反复试探、"
               "警告、赶人，也可以随时结束——除了威胁，别的都不重要。"),
        opens=(("world.stance", 1.8), ("world.news", 1.2), ("self.edge", 1.3),
               ("self.size", 0.8), ("self.position", 0.7)),
        denies=("private.hidden", "private.birthday"),
        budget=720,
    ),
    TopicKind(
        id="aloof", label="疏离", coord=ChatCoord(0.20, 0.25, 0.30),
        tags=(KIND_TAG_RELATION,), 
        prompt="能一个字回答就不写一句；书面、客气，不加语气词和多余标点。",
        focus="只有最基本的处境",
        frame="你没什么想说的：问什么答什么，不主动展开，私事和情绪都绕开。",
        opens=(("self.live", 0.6), ("self.size", 0.5)),
        budget=320,
    ),
    TopicKind(
        id="showoff", label="自得", coord=ChatCoord(0.25, 0.25, 0.80),
        tags=(KIND_TAG_RELATION,), 
        prompt=("短句、节奏快，可以夸张、反问；口语，允许带一点自得的口头禅。"),
        focus="你有多大、能做什么",
        frame=("你想让他看见你的尺寸和能耐；享受这种被仰望的感觉，"
               "但不必真的亲近。"),
        opens=(("self.size", 2.0), ("self.measured", 1.8), ("self.scale", 1.6),
               ("self.edge", 1.6), ("world.stance", 0.8)),
        denies=("private.hidden", "world.news"),
        budget=780,
    ),
    TopicKind(
        id="sharing", label="网络分享", coord=ChatCoord(0.66, 0.20, 0.72),
        radius=0.26,
        tags=(KIND_TAG_TOPIC,),
        prompt=("一条一两句，短、跳、没头没尾；网络用语和缩写可以用，标点能省"
                "就省，不用敬语。"),
        focus="他会不会也觉得这个好笑",
        frame=("你刷到什么就想立刻发给他。游戏推到哪了、这个梗好不好笑、刚看到"
               "的怪东西，都可以直接丢过去，不用铺垫。"),
        opens=(("self.live", 1.2), ("self.position", 0.4)),
        denies=("world.news", "world.stance", "self.size", "self.measured",
                "private.hidden", "private.birthday"),
        budget=260,
    ),
    TopicKind(
        id="talk", label="闲谈观点", coord=ChatCoord(0.56, 0.26, 0.40),
        tags=(KIND_TAG_TOPIC,), radius=0.26,
        prompt=("句子完整、节奏平，可以比喻、可以反问；不用网络烂梗，也不"
                "说教。"),
        focus="一件跟她无关、但她有看法的事",
        frame=("聊点跟你自己没直接关系的事。一段视频、一张画、一句说法，或者"
               "别人怎么看巨大娘——你是认真还是随口都行，别绕回身体。"),
        opens=(("world.news", 1.0), ("self.will", 0.8), ("world.stance", 0.4)),
        denies=("private.hidden", "private.birthday", "self.measured"),
        budget=460,
    ),
    TopicKind(
        id="mundane", label="学习/工作", coord=ChatCoord(0.50, 0.33, 0.56),
        tags=(KIND_TAG_TOPIC,), radius=0.26,
        prompt=("平铺直叙，句子完整，具体名词多；少用形容词和比喻。"),
        focus="普通人的一天怎么过",
        frame=("问他今天上课上班怎么样。也说说自己或自己熟人生活中普通的方面：通勤、作业、开会、吃"
               "什么。都是小事，不用升华，也别往尺寸上拐。"),
        opens=(("self.live", 1.4), ("self.rhythm", 1.0),
               ("self.position", 0.8), ("self.spatial", 0.5)),
        denies=("world.news", "world.stance", "private.hidden", "self.size",
                "self.measured", "self.edge"),
        budget=480,
    ),
    TopicKind(
        id="elders", label="客套应答", coord=ChatCoord(0.28, 0.20, 0.58),
        tags=(KIND_TAG_RELATION,), radius=0.26,
        prompt=("礼貌短句，「还行」「挺好的」「就那样」；不反问、不接话头、"
                "不加语气词。"),
        focus="像被长辈问话一样应付",
        frame=("他可能问你一些你不想回答的问题，如怎么处理私事、为什么要破坏"
               "——你客气地答，简短，不展开，也不真的想聊。"),
        opens=(("self.live", 0.6),),
        denies=("world.news", "world.stance", "world.endings",
                "private.hidden", "private.birthday", "self.size",
                "self.measured"),
        budget=240,
    ),
    TopicKind(
        id="plan", label="个人规划", coord=ChatCoord(0.72, 0.38, 0.60),
        tags=(KIND_TAG_TOPIC,), radius=0.26,
        prompt=("句子中等长度，陈述为主；适当开展想象力。"),
        focus="你接下来打算怎么办",
        frame=("你想说说自己的打算。接下来想去哪、还想不想再长、哪些事准备放"
               "到以后——说得具体一点，但不必许诺。"),
        opens=(("self.will", 1.6), ("self.spatial", 1.2),
               ("self.position", 0.8)),
        denies=("world.news", "world.stance", "private.hidden"),
        budget=520,
    ),
    TopicKind(
        id="encounter", label="异界见闻", coord=ChatCoord(0.42, 0.52, 0.50),
        radius=0.26,
        tags=(KIND_TAG_TOPIC,),
        prompt=("叙述为主、句子偏长，可以插一句判断；不用「系统」「设定」这类"
                "词。"),
        focus="别处传来的留言，或你碰见过的东西",
        frame=("你在别处待过，也听过别处的说法。可以讲旅居过的世界、路上碰见的"
               "人、传过来的留言，像说一件旧事。"),
        opens=(("world.travel", 2.2), ("world.news", 1.0),
               ("self.position", 0.8)),
        denies=("private.hidden", "private.birthday", "self.measured"),
        budget=560,
    ),
    TopicKind(
        id="observation", label="个人观察", coord=ChatCoord(0.48, 0.58, 0.72),
        radius=0.28,
        tags=(KIND_TAG_TOPIC,),
        prompt=("句子中等长度，以看到的东西为主，具体名词多；判断放在后面，"
                "不铺陈形容词。"),
        focus="城市与山川是怎么长的，人遇到危险会怎么做",
        frame=("你在看外面：城市怎么摊开、山脊和河道往哪走、人多的地方怎么长"
               "出来。也看人——出事时他们往哪跑、先救什么、多久恢复。只说看"
               "见的。"),
        opens=(("self.position", 1.6), ("self.spatial", 1.4),
               ("self.edge", 1.2), ("world.news", 0.8), ("self.size", 0.6)),
        denies=("private.hidden", "private.birthday", "world.stance"),
        budget=720,
    ),
    TopicKind(
        id="hobby", label="个人爱好", coord=ChatCoord(0.86, 0.24, 0.48),
        radius=0.24,
        tags=(KIND_TAG_TOPIC,),
        prompt=("句子松、口语，说细节不说意义；可以停顿、补一句「就是……」；"
                "不介绍背景。"),
        focus="你自己愿意花时间的事",
        frame=("说说你自己愿意花时间的事：常待的地方、手边摆弄的东西、看得下去"
               "的东西。用你已经说过的偏好和平时待的地方讲，别新编一个爱好。"),
        opens=(("self.spatial", 1.4), ("self.rhythm", 1.2),
               ("self.live", 1.0), ("self.position", 0.6)),
        denies=("world.news", "world.stance", "world.endings", "private.hidden",
                "private.birthday", "self.size", "self.measured"),
        budget=540,
    ),
    TopicKind(
        id="desire", label="个人欲望", coord=ChatCoord(0.82, 0.52, 0.35),
        radius=0.26,
        tags=(KIND_TAG_TOPIC,),
        prompt=("句子可以断，说到一半停住也行；少用解释性的「因为」；不承诺。"),
        focus="你真正想要、但未必说得出的事",
        frame=("说一件你想要的。不是打算，是那种说不清、也未必拿得到的东西："
               "想被怎么对待、想变成什么样、想留住什么。可以说一半就停。"),
        opens=(("private.hidden", 1.8), ("self.will", 1.6), ("self.greed", 1.2)),
        denies=("world.news", "world.stance", "self.measured", "self.size"),
        budget=700,
        disclosure=True,
    ),
)

# ---------- 生效中的类型表（世界包可整表替换） ----------
_ACTIVE_KINDS: Tuple[TopicKind, ...] = TOPIC_KINDS
_ACTIVE_NO_TOPIC: TopicKind = NO_TOPIC_LENS


def active_kinds() -> Tuple[TopicKind, ...]:
    """当前生效的类型表；默认就是 ``TOPIC_KINDS``。"""
    return _ACTIVE_KINDS


def active_no_topic() -> TopicKind:
    """当前生效的"无话题"视角。"""
    return _ACTIVE_NO_TOPIC


def install_topic_kinds(kinds: Iterable[Any],
                        no_topic: Optional[TopicKind] = None
                        ) -> Tuple[TopicKind, ...]:
    """整表替换当前话题类型（世界包加载时调用），返回生效的表。

    列表里若带 ``virtual`` 条目（通常是无话题视角），它被单独取作兜底视角，
    不参与候选筛选——所以"整表导出再整表导入"可以原样往返。校验不过抛
    ``ValueError``，且不改动现有表。别在会话进行中调用：同一轮里两次取表
    可能拿到不同的内容。
    """
    global _ACTIVE_KINDS, _ACTIVE_NO_TOPIC
    try:
        entries, virtual = _coerce_kinds(kinds)
    except ValueError as exc:
        raise ValueError(f"话题类型表无效:\n  - {exc}")
    fallback = no_topic or virtual or _ACTIVE_NO_TOPIC
    problems = validate_topic_kinds(entries, fallback) + _field_problems(
        entries, fallback)
    if problems:
        raise ValueError("话题类型表无效:\n"
                         + "\n".join("  - " + item for item in problems))
    _ACTIVE_KINDS, _ACTIVE_NO_TOPIC = entries, fallback
    return entries


def reset_topic_kinds() -> Tuple[TopicKind, ...]:
    """还原代码里的默认表（测试与卸载世界包用）。"""
    global _ACTIVE_KINDS, _ACTIVE_NO_TOPIC
    _ACTIVE_KINDS, _ACTIVE_NO_TOPIC = TOPIC_KINDS, NO_TOPIC_LENS
    return _ACTIVE_KINDS


def validate_topic_kinds(kinds: Optional[Iterable[TopicKind]] = None,
                         no_topic: Optional[TopicKind] = None) -> List[str]:
    """结构校验：返回问题清单，空表示可用。

    只管类型表自身（id、文案、opens/denies 是否自相矛盾）；``opens`` 里的字段
    有没有注册由 ``services.chat.topic_lens.lens_coverage`` 负责——字段目录在
    那边，本模块不反向依赖它。
    """
    entries = tuple(active_kinds() if kinds is None else kinds)
    fallback = no_topic if isinstance(no_topic, TopicKind) else _ACTIVE_NO_TOPIC
    problems: List[str] = []
    if not any(not kind.virtual for kind in entries
               if isinstance(kind, TopicKind)):
        problems.append("至少需要一个可选话题类型")
    seen = set()
    for kind in entries:
        if not isinstance(kind, TopicKind):
            problems.append(f"类型表混入了非 TopicKind 条目：{kind!r}")
            continue
        name = kind.id or "（空 id）"
        if not kind.id:
            problems.append("类型 id 不能为空")
        elif kind.id in seen:
            problems.append(f"类型 id 重复：{kind.id}")
        else:
            seen.add(kind.id)
        if not kind.frame.strip():
            problems.append(f"{name} 缺少 frame（注意力框架）")
        if not kind.prompt.strip():
            problems.append(f"{name} 缺少 prompt（体裁）")
        if not kind.virtual and not kind.label:
            problems.append(f"{name} 缺少 label")
        if not kind.virtual and not kind.focus.strip():
            problems.append(f"{name} 缺少 focus（候选列表里的一句区别）")
        for field_id in kind.denies:
            if any(item == field_id for item, _ in kind.opens):
                problems.append(f"{name} 同时开放又关闭字段 {field_id}")
    if not fallback.frame.strip():
        problems.append("无话题视角缺少 frame")
    if fallback.id in seen:
        problems.append(f"无话题视角的 id 与可选类型冲突：{fallback.id}")
    return problems


def _coerce_kinds(kinds: Iterable[Any]
                  ) -> Tuple[Tuple[TopicKind, ...], Optional[TopicKind]]:
    """解析条目并摘出 virtual 兜底视角；结构错误抛 ``ValueError``。"""
    entries: List[TopicKind] = []
    virtual: Optional[TopicKind] = None
    for item in kinds or ():
        kind = topic_kind_from_dict(item) if isinstance(item, dict) else item
        if not isinstance(kind, TopicKind):
            raise ValueError(f"类型表混入了非 TopicKind 条目：{item!r}")
        if kind.virtual:
            virtual = virtual or kind
            continue
        entries.append(kind)
    return tuple(entries), virtual


def _field_problems(kinds: Tuple[TopicKind, ...],
                    no_topic: TopicKind) -> List[str]:
    # 字段目录在 topic_lens；延迟导入避免 topic_kinds ↔ topic_lens 循环
    from services.chat.topic_lens import lens_coverage
    return lens_coverage(kinds, no_topic)


# ---------- JSON 条目（世界包 authoring 用） ----------
_COORD_KEYS = ("affection", "tension", "size_state")


def topic_kind_from_dict(data: Dict[str, Any]) -> TopicKind:
    """把一条 JSON 定义解析成 ``TopicKind``。

    必需 ``id`` / ``label`` / ``coord``（``[好感, 紧张, 状态]`` 或
    ``{"affection":…, "tension":…, "size_state":…}``）；其余键与 dataclass
    同名，``opens`` 接受 ``{"字段": 权重}`` 或 ``[["字段", 权重], …]``。
    """
    if not isinstance(data, dict):
        raise ValueError(f"话题类型定义必须是对象：{data!r}")
    kind_id = str(data.get("id") or "").strip()
    label = str(data.get("label") or "").strip()
    if not kind_id:
        raise ValueError(f"话题类型缺少 id：{data!r}")
    if not label and not data.get("virtual"):
        raise ValueError(f"话题类型 {kind_id} 缺少 label")
    coord = _coord_from_json(data.get("coord"))
    if coord is None:
        raise ValueError(f"话题类型 {kind_id} 的 coord 必须是三分量数值坐标")
    return TopicKind(
        id=kind_id, label=label, coord=coord,
        radius=data.get("radius", TOPIC_KIND_RADIUS),
        prompt=data.get("prompt", ""),
        focus=data.get("focus", ""),
        frame=data.get("frame", ""),
        opens=data.get("opens", ()),
        denies=data.get("denies", ()),
        budget=data.get("budget", DEFAULT_TOPIC_BUDGET),
        disclosure=data.get("disclosure", False),
        tags=data.get("tags", ()),
        priority=data.get("priority", 0),
        cooldown=data.get("cooldown", 0.0),
        virtual=data.get("virtual", False),
    )


def topic_kind_to_dict(kind: TopicKind) -> Dict[str, Any]:
    """导出为可写回 JSON 的条目（``install_topic_kinds`` 能直接吃）。"""
    return {
        "id": kind.id,
        "label": kind.label,
        "coord": kind.coord.as_list(),
        "radius": kind.radius,
        "prompt": kind.prompt,
        "focus": kind.focus,
        "frame": kind.frame,
        "opens": [[field_id, weight] for field_id, weight in kind.opens],
        "denies": list(kind.denies),
        "budget": kind.budget,
        "disclosure": kind.disclosure,
        "tags": list(kind.tags),
        "priority": kind.priority,
        "cooldown": kind.cooldown,
        "virtual": kind.virtual,
    }


def _coord_from_json(value: Any) -> Optional[ChatCoord]:
    if isinstance(value, ChatCoord):
        return value
    if isinstance(value, dict):
        if not all(key in value for key in _COORD_KEYS):
            return None
        value = [value[key] for key in _COORD_KEYS]
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    try:
        parts = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(part) for part in parts):
        return None
    return ChatCoord.from_values(parts)


# ---------- 查询与筛选 ----------
def _kind_tuple(kinds: Optional[Iterable[TopicKind]]) -> Tuple[TopicKind, ...]:
    """可参与筛选的类型（virtual 条目只做兜底视角，不进候选）。"""
    source = active_kinds() if kinds is None else tuple(kinds)
    return tuple(kind for kind in source if not kind.virtual)


def _coord(coord) -> ChatCoord:
    return ChatCoord.from_values(coord)


def find_topic_kind(kind_id: str,
                    kinds: Optional[Iterable[TopicKind]] = None
                    ) -> Optional[TopicKind]:
    """按 id 查类型；未知 id 或虚拟视角返回 None。"""
    if not isinstance(kind_id, str):
        return None
    target = kind_id.strip()
    for kind in _kind_tuple(kinds):
        if kind.id == target:
            return kind
    return None


def kinds_with_tag(tag: str,
                   kinds: Optional[Iterable[TopicKind]] = None
                   ) -> Tuple[TopicKind, ...]:
    """按软分类标签取类型（``relation`` / ``topic``），只用于查阅与人工分组。

    标签不参与半径过滤、排序或候选数量；``kinds`` 省略时读当前生效的表。
    """
    target = str(tag or "").strip()
    if not target:
        return ()
    return tuple(kind for kind in _kind_tuple(kinds) if target in kind.tags)


def lens_for(kind_id: Optional[str]) -> TopicKind:
    """按 id 取视角；空 / 未知类型回退到无话题视角。"""
    kind = find_topic_kind(kind_id) if kind_id else None
    return kind if kind is not None else _ACTIVE_NO_TOPIC


def topic_kind_distance(coord, kind: TopicKind) -> float:
    return _coord(coord).distance(kind.coord)


def _first_clause(text: str, limit: int = _HINT_CHARS) -> str:
    """取最早结束的那一小句；超长按逗号收口并补省略号。"""
    text = (text or "").strip()
    if not text:
        return ""
    cut = len(text)
    for separator in ("。", "；", "！", "？", "\n"):
        index = text.find(separator)
        if index != -1:
            cut = min(cut, index)
    clause = text[:cut].strip(" ，、：")
    if len(clause) <= limit:
        return clause
    shortened = clause[:limit]
    for separator in ("，", "、", "："):
        index = shortened.rfind(separator)
        if index >= limit // 2:
            return shortened[:index] + "…"
    return shortened + "…"


def topic_kind_hint(kind: TopicKind) -> str:
    """候选列表里的一句区别：取 frame 的第一小句（没写 frame 时退回体裁）。"""
    return _first_clause(kind.frame or kind.prompt)


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
