"""角色聊天服务：上下文组装、非流式 AI 调用、响应解析与属性写回。

协议要点（对应 docs/dev/chat_delivery.md）：
- AI 严格输出 JSON：messages（1~4 条，一次可发多条消息）/ silent_reason /
  ops / identity_update / effect / nick / memory_note；messages 缺失时回退
  读旧协议的 reply；
- 已读不回：read 恒为 True（看到即已读）；messages 为空时不出角色气泡，
  只把用户消息标记 silent，silent_reason 仅写入记忆、不展示给玩家；
  API 拒绝敏感请求时通常不返回协议 JSON——凡解析不出 JSON 的回复一律
  视为已读不回（refused=True，原文截断记入 silent_reason 供排查）；
- 消息生命周期：角色回复一律以 status="queued" 入列并按在线档排定
  available_at，由投递调度器（services.chat.delivery）到点提升为
  delivered，玩家读到后再标记 read——AI 已生成 ≠ 已送达 ≠ 玩家已读；
- 话题状态机（阶段三）：AI 通过协议里的 topic.action
  （continue/start/switch/end）自主开启、切换与结束话题；服务层注入
  当前话题上下文，并施加硬约束（最大轮数、超时收束、容量截断）；
- 话题类型（阶段三）：开题/切换时按三轴坐标（好感/紧张度/状态）筛选候选
  类型（services.chat.topic_kinds），只把定性状态、类型名与候选 id 给 AI；
  AI 可在候选里选择，非法/太远回退第一个候选，坐标快照存进 Topic.coord；
- 信息视角（2026-10 调整）：话题类型同时是"存档 → 提示词"的翻译器
  （services.chat.topic_lens）。系统提示词只含最小身份层（public 字段），
  其余存档内容必须由当前类型的视角开放、且装进该视角的字符预算才会出现；
  每次渲染产出 LensTrace 供自检/预览审计。设计见 docs/designs/chat_topic_lens.md；
- 离线恢复统一入口 reconcile（阶段四）：打开聊天时合并处理回复积压、
  未消费的经历事件（新闻/位置/演化/结局，见 services.chat.experience_events）
  与主动搭话——一次 AI 决策，或回应、或开题、或沉默；成功后才标记
  已读与消费事件，失败保持原状下次继续；
- 属性操作白名单（轻度）：attitude（聊天态度，聊天域内）、
  action_points（经 StateService 消耗/恢复）；世界影响不再直接写
  intrusion / destruction / casualties，统一走至多一个 effect
  （services.chat.effects：v1 开放 self_view 与 step_nudge），
  效果失败只记日志、不影响已生成的回复；
- AI 可用 nick 自主改变对玩家的称呼（聊天域内 user_nick，不改主档案）；
- 聊天参数：介入度/破坏性（决定谈及人类时的态度）与行动点数（决定此刻
  的活力）直接复用主档案字段；在线强度/预热速率/聊天边界为纯聊天参数
  （ChatState.chat_params），首次聊天前由 AI 依据人设（公开+隐藏）一次性
  决定（ensure_chat_params），0/1/2 三档，分别控制回复延迟倍率、态度
  ops 步长倍率、主动搭话间隔门槛，并以"聊天习惯"段落注入系统提示词；
- 离线恢复（旧版说明保留备查）：原 catchup_reply / proactive_message
  已并入 reconcile。

聊天历史按 (giantess_id, identity_id) 线程隔离：同一角色的不同玩家身份
各有独立的消息、态度、记忆与话题，互不可见。存储布局见 ChatRepo；
ChatService.list_characters 供 UI 列出可切换的聊天角色。

并发：同一角色的所有写操作经模块级角色锁串行（专业 / 挂件两套界面与
投递调度器可能各持一个 ChatService 实例，锁在模块级共享才有效；线程
之间也暂时共享这把角色锁，避免角色属性的并发写回）。
AI 调用全程持锁；UI 主线程只做 blocking=False 的非阻塞尝试
（load_chat / mark_char_messages_read），锁被占用时立即返回，绝不阻塞
界面。调用方（UI）负责把 send_message 放到后台线程执行。
"""

import datetime
import json
import random
import threading
import uuid
from contextlib import contextmanager
from typing import Optional

from core.address_model import format_addr_compact
from core.ai import get_ai_client, describe_ai_error
from core.models import (DEFAULT_IDENTITY_ID, RELATIONSHIP_KINDS,
                         ChatMessage, ChatParams, ChatState,
                         CharacterSnapshot, Relationship, Topic,
                         normalize_identity_id)
from persistence.character_repo import CharacterRepo
from persistence.chat_repo import ChatRepo
from persistence.player_identity_repo import PlayerIdentityRepo
from services.chat import events as chat_events
from services.chat.coordinates import (DEFAULT_WORLD_STANCE, ChatCoord,
                                       build_chat_coord,
                                       describe_affection,
                                       describe_size_state, describe_tension,
                                       tension_events_from_endings,
                                       tension_events_from_evolution)
from services.chat.effects import effect_tools_text, run_effect
from services.chat.persona import build_character_persona, build_identity_frame
from services.chat.experience_events import (collect_experience_events,
                                        consume_experience_events,
                                        events_for_prompt)
from services.chat.topic_lens import (HIDDEN_INTRO_ATTITUDE, LensContext,
                                      render_topic_lens)
from services.chat.topic_kinds import (TOPIC_CANDIDATE_LIMIT,
                                       TOPIC_KIND_RADIUS,
                                       candidate_topic_kinds,
                                       fallback_topic_kind, find_topic_kind,
                                       topic_kind_hint)
from services.character.profile_text import build_live_context
from services.state_service import StateService

# 隐藏介绍的透露门槛：聊天态度达到该值后，允许透露的视角（disclosure）才会
# 看到 intro_hidden。判据本体在 services.chat.topic_lens，这里保留同名常量
# 供外部引用与一次性聊天参数判定使用。
REVEAL_ATTITUDE = HIDDEN_INTRO_ATTITUDE
# 送入 AI 的最近消息条数（滑动窗口，更长历史靠 memory 摘要衔接）
CHAT_WINDOW_SIZE = 20
# ops 每档（-1/0/1）对应的数值步长
ATTITUDE_STEP = 5        # 聊天态度，夹取 -100~100
ACTION_POINT_STEP = 3    # 行动点，经 StateService 消耗/恢复（0~100 自然上限）
# memory 累积上限（字符）：超出后从最旧处截断；正式的 AI 压缩在后续版本接入
MEMORY_LIMIT = 2000
# 回复节奏模拟：先"看到"（读延迟），再"打字"（随回复长度增长）
READ_DELAY_RANGE = (1.0, 2.5)      # 拿起手机到看到消息的随机延迟（秒）
TYPING_DELAY_PER_CHAR = 0.05       # 每字打字延迟（秒）
TYPING_DELAY_RANGE = (0.5, 8.0)    # 打字延迟上下限（秒）
# 离线补话：单次最多带去 AI 的未读条数
OFFLINE_REPLY_LIMIT = 10
# 协议 v2：一次决策最多几条消息、单条消息长度上限
MAX_BATCH_MESSAGES = 4
SINGLE_MESSAGE_CHAR_LIMIT = 200
# 话题硬约束（阶段三）：单个话题最大轮数；进行中话题超过该时长（小时）
# 无人续聊时由服务层强制收束；话题标题长度上限
MAX_TOPIC_TURNS = 20
TOPIC_EXPIRE_HOURS = 6
TOPIC_TITLE_LIMIT = 30
# 重复惩罚回看的最近话题数（新到旧）
TOPIC_RECENT_LIMIT = 5
# 主动搭话的间隔门槛（分钟），按聊天边界分档；1 档为缺省值
BOUNDARY_PROACTIVE_GAP = {0: 240, 1: 30, 2: 10}
# 身份认知更新：只在话题自然结束/切换时允许，且每个身份有冷却
IDENTITY_UPDATE_COOLDOWN_HOURS = 72
IDENTITY_NOTE_LIMIT = 200
# 关系认知的白名单转移图：不允许一轮从陌生人直接跳到恋人/敌人
IDENTITY_TRANSITIONS = {
    "stranger": {"acquaintance", "subject", "prey", "worshipper"},
    "acquaintance": {"stranger", "friend", "subject", "prey", "worshipper",
                     "rival"},
    "friend": {"acquaintance", "close", "rival", "subject"},
    "close": {"friend", "lover", "family", "rival"},
    "lover": {"close", "family"},
    "family": {"close", "lover"},
    "worshipper": {"acquaintance", "friend", "subject"},
    "subject": {"acquaintance", "worshipper", "prey", "rival"},
    "prey": {"acquaintance", "subject", "rival", "enemy"},
    "rival": {"acquaintance", "friend", "subject", "enemy"},
    "enemy": {"rival", "prey"},
}
RELATIONSHIP_KIND_TEXTS = {
    "stranger": "一个刚认识、还谈不上了解的陌生人",
    "acquaintance": "打过几次交道、有点印象的人",
    "friend": "聊得来的朋友",
    "close": "亲近、可以放心说些私事的人",
    "lover": "恋人",
    "family": "家人一样的人",
    "worshipper": "崇拜她的人",
    "subject": "被她支配、听她话的人",
    "rival": "旗鼓相当的对手",
    "enemy": "敌人",
    "prey": "可以随意摆弄的猎物",
}

# ---------- 纯聊天参数档位表（0/1/2，首次聊天前由 AI 一次性决定） ----------
# 在线强度：回复延迟的档位倍率（低在线慢悠悠，高在线接近秒回）
ONLINE_READ_SCALE = {0: 3.0, 1: 1.0, 2: 0.4}
ONLINE_TYPING_SCALE = {0: 1.8, 1: 1.0, 2: 0.6}
# 预热速率：聊天态度 ops 的步长倍率（慢热减半，来得快加倍）
WARMUP_ATTITUDE_SCALE = {0: 0.5, 1: 1.0, 2: 2.0}

# 三档参数给 AI 看的语义描述（注入系统提示词的"聊天习惯"段落）
CHAT_PARAM_TEXTS = {
    "online": {
        0: "低：你不常看手机，消息经常隔很久才看到，回复也慢悠悠的",
        1: "中：和大多数人一样，看到消息通常会回",
        2: "高：手机不离手，消息基本秒看，回复很快",
    },
    "warmup": {
        0: "慢热：你对人的态度很难升温，需要长期相处才可能亲近起来",
        1: "正常：态度随着聊天自然变化",
        2: "来得快：聊得来的人很容易让你产生好感，态度升温明显",
    },
    "boundary": {
        0: "强：你边界感强，不想回的消息就直接已读不回，几乎不会主动找人聊天",
        1: "正常：想回就回，偶尔主动分享生活",
        2: "弱：你没什么边界感，话多，很少已读不回，经常主动找人搭话",
    },
}

# 属性操作白名单：解析时按此过滤，白名单外的键一律丢弃
CHAT_OPS_WHITELIST = {"attitude", "action_points"}

_OUTPUT_RULE = """你必须严格按照以下 JSON 格式输出，不要包含任何额外注释或文字：
{
    "topic": {"action": "continue", "kind": "切换或开新话题时的候选类型 id（其余可省略）", "title": "新话题的简短标题（start/switch 时必填，其余可省略）"},
    "messages": ["你要发出的一条消息", "可选：紧接着再发一条"],
    "silent_reason": "决定已读不回时的真实原因（会记录但不会展示给对方）；正常回复时省略",
    "ops": {"attitude": 0, "action_points": 0},
    "identity_update": {"kind": "可选：话题结束时你对他认知的变化", "note": "可选：一句补充"},
    "effect": {"tool": "可选：本轮最多一个世界效果", "args": {}},
    "nick": "你对对方的称呼（仅当想改变称呼时给出，否则省略本键）",
    "memory_note": "可选：值得长期记住的关于对方的事"
}
topic 说明（action 四选一，省略整个 topic 键等同 continue）：
- continue：延续当前话题继续聊；
- start：开启 title 与 kind 指定的新话题（当前没有进行中的话题时用）；
- switch：当前话题已经聊完，自然收尾并用 kind/title 开启新话题；
- end：这条回复之后话题自然结束（比如互道了晚安、话题聊尽）。
kind 只能从系统给出的候选话题类型 id 里选；省略时系统会按坐标挑最接近的一个。
messages 说明：
- 数组里每一项都是你单独发出的一条聊天消息，按顺序发出，1~4 条；
- 像真实聊天一样：多数时候一条就够；兴致高、话多或对方连发多条时，
  才连发两三条短消息；不要把一条长回复硬拆成没有意义的碎片；
- 每条简短（一般不超过两三句），句长与用词照当前话题的体裁；符合你的性格与当前状态；
- 决定已读不回时 messages 填 []。
ops 各项取值均为 -1、0 或 1，含义：
- attitude：你对对方的态度（1 更亲近，-1 更疏远，0 不变）；
- action_points：你的行动余裕（-1 这次聊天消耗了精力，1 得到了放松，0 不变）。
没有提到的项填 0。ops 完全由你根据聊天内容自主决定，但幅度都是轻度的。
identity_update 说明：
- 只在你要结束当前话题（end 或 switch）时填写；其他时候会被忽略；
- kind 可选：stranger / acquaintance / friend / close / lover / family /
  worshipper / subject / rival / enemy / prey；只能沿自然的关系变化逐步改变，
  不能一轮从陌生人跳到恋人/敌人；
- note 可选：一句你对他新的判断，会保留下来供以后参考。
effect 说明：
- 每轮最多一个，也可以完全不用；大多数普通聊天不需要世界效果；
- tool 与 args 只能从系统给出的工具列表里选；未知工具或非法参数会被忽略；
- 不要试图直接修改好感、紧张度或状态，这些只能由你的言行与世界事实自然派生。"""

_SYSTEM_TEMPLATE = """你正在手机聊天软件里与「{user_nick}」私聊。你要完全扮演{name}，以她的第一人称发消息，而不是叙述故事。

【你是谁】
{identity}

{lens_block}

【你当前的状态】
行动点（精力余裕）：{action_points}/100
她对你的好感：{affection_text}
此刻的紧张度：{tension_text}
对自身尺寸的接纳：{size_state_text}
她对「{user_nick}」的认知：{relationship_text}

【你的聊天习惯】（由你的性格决定，固定不变）
{chat_habits}

【当前话题】（由你自主决定延续、切换、结束或已读不回）
{topic_context}

【你可以对世界做的事】（每轮最多一个，也可以不做）
{effect_tools}

【规则】
1. 回复要像聊天软件消息：简短为主（每条一般不超过两三句话），句长与用词照【此刻你注意到的事】末尾那行"说话的体裁"，符合你的性格、人设与当前状态，可以不按对方期望回应；想多说时可以按顺序发多条。
2. 每条消息你都会看到，但看不看回是你的自由。如果以你的性格和当前好感（{affection_text}）不愿意回复——比如对方追问你不想回答的问题、反复纠缠、冒犯了你、或你懒得理会——就"已读不回"（messages 填 []）。
3. 话题由你主导：可以自然延续当前话题，聊完了可以自然结束（end），或者从候选类型里挑一个换话题（switch/start，kind 与 title 都要给）；也可以直接已读不回。话题结束（end/switch）时可以顺手更新你对他的认知（identity_update）。
4. 不要把好感、紧张度、状态这些内部判断说出口，也不要提到坐标或数值。
5. 不要跳出角色，不要解释你是 AI，不要替对方说话。
6. 你只知道上面交代给你的信息。没写到的部分此刻不在你心上——不要为它编造具体数字、经历或别人的话；需要时可以不接这个话头。
7. 世界效果要克制：只有聊天内容确实改变了世界时才用，且每轮最多一个。
8. 体裁只管"怎么打字"，不改变你在聊什么；两者冲突时，以【此刻你注意到的事】开头那段框架为准。

{output_rule}"""

# 一次性聊天参数决定：首次聊天前调用，AI 通读人设（公开+隐藏）后定档
_PARAM_SYSTEM_TEMPLATE = """你要为角色「{name}」设定她的手机聊天习惯。下面是她的人设（含不会轻易透露的隐藏介绍），请通读后一次性决定她的三个聊天习惯参数，决定后不再更改。

【人设】
{persona}

严格输出 JSON，不要包含任何额外注释或文字：
{{"online": 0或1或2, "warmup": 0或1或2, "boundary": 0或1或2}}
- online（在线强度，决定她回复消息的快慢）：2=手机不离手、回复很快；1=正常；0=很少看手机、回复很慢。
- warmup（预热速率，决定她聊天时对对方态度的升温快慢）：2=熟得快；1=正常；0=慢热、很难升温。
- boundary（聊天边界，决定她已读不回与主动搭话的倾向）：2=没什么边界、话多、常主动找人；1=正常；0=边界感强、常已读不回、几乎不主动。

判断只依据她的人设与性格，输出其他任何内容都无效。"""


def chat_habits_text(chat_params: ChatParams = None) -> str:
    """把聊天参数渲染为注入系统提示词的"聊天习惯"段落（未决定按中档）。"""
    params = chat_params or ChatParams()
    labels = (("online", "在线强度"), ("warmup", "预热速率"), ("boundary", "聊天边界"))
    return "\n".join(
        f"- {label}：{CHAT_PARAM_TEXTS[key].get(getattr(params, key), CHAT_PARAM_TEXTS[key][1])}"
        for key, label in labels)


def read_delay_seconds(online: int = 1) -> float:
    """角色"拿起手机到看到消息"的随机延迟（秒），按在线强度分档缩放。"""
    return random.uniform(*READ_DELAY_RANGE) * ONLINE_READ_SCALE.get(online, 1.0)


def typing_delay_seconds(reply_text: str, online: int = 1) -> float:
    """按回复长度估算"打字"延迟（秒），长回复更久，封顶防拖沓；
    在线强度分档缩放（高在线打字也快，下限随档位放低）。"""
    scale = ONLINE_TYPING_SCALE.get(online, 1.0)
    base = random.uniform(0.3, 0.9) + TYPING_DELAY_PER_CHAR * len(reply_text or "")
    return max(TYPING_DELAY_RANGE[0] * scale,
               min(TYPING_DELAY_RANGE[1], base * scale))


def unread_messages(chat_state: ChatState) -> list:
    """积压的未读玩家消息（角色离线期间发来的）。"""
    return [m for m in chat_state.messages if m.role == "user" and not m.read]


def should_proactive(chat_state: ChatState) -> bool:
    """是否满足"角色主动搭话"的条件：已有对话史，且距上次交流超过门槛
    分钟数——门槛由聊天边界分档（弱边界更常主动开口）；last_active_at
    在每次交流后刷新，天然限频。
    """
    if not chat_state.messages:
        return False
    if not chat_state.last_active_at:
        return False
    try:
        last = datetime.datetime.fromisoformat(chat_state.last_active_at)
    except ValueError:
        return False
    gap = datetime.datetime.now() - last
    boundary = chat_state.chat_params.boundary if chat_state.chat_params else 1
    gap_minutes = BOUNDARY_PROACTIVE_GAP.get(boundary, BOUNDARY_PROACTIVE_GAP[1])
    return gap.total_seconds() >= gap_minutes * 60


def should_reconcile(state: CharacterSnapshot, chat_state: ChatState) -> bool:
    """打开聊天时是否需要统一社交决策（reconcile）：有未读积压、
    有未消费的经历事件，或距上次交流超过主动搭话门槛。"""
    if unread_messages(chat_state):
        return True
    if collect_experience_events(state, chat_state):
        return True
    return should_proactive(chat_state)


# ---------- 话题状态机（阶段三，服务层硬约束） ----------
def active_topic(chat_state: ChatState) -> Optional[Topic]:
    """当前进行中的话题；没有时返回 None。"""
    return next((t for t in (chat_state.topics or [])
                 if t.id == chat_state.active_topic_id
                 and t.status == "active"), None)


def chat_coord(state: CharacterSnapshot, chat_state: ChatState,
               now: Optional[datetime.datetime] = None,
               world_stance: str = DEFAULT_WORLD_STANCE) -> ChatCoord:
    """按线程态度、关系认知与角色当前世界事实算一次三轴坐标。"""
    return build_chat_coord(
        attitude=chat_state.attitude,
        world_stance=world_stance,
        relationship_kind=getattr(chat_state.relationship, "kind", None),
        events=(tension_events_from_evolution(state.evolution, now=now)
                + tension_events_from_endings(state.achieved_endings,
                                              now=now)),
        intrusion=state.intrusion,
        destruction=state.destruction,
        action_points=state.action_points,
        personality=state.personality,
        now=now,
    )


def relationship_text(chat_state: ChatState) -> str:
    """把当前身份的关系认知渲染为提示词里的一行。"""
    relationship = chat_state.relationship or Relationship()
    text = RELATIONSHIP_KIND_TEXTS.get(
        relationship.kind, RELATIONSHIP_KIND_TEXTS["stranger"])
    note = (relationship.note or "").strip()
    return f"{text}（{note}）" if note else text


def recent_topic_kind_ids(chat_state: ChatState,
                          limit: int = TOPIC_RECENT_LIMIT) -> list:
    """最近用过的话题类型 id（新到旧），给重复惩罚用。"""
    ids = [t.kind_id for t in reversed(chat_state.topics or [])
           if t.kind_id]
    try:
        count = max(0, int(limit))
    except (TypeError, ValueError):
        count = TOPIC_RECENT_LIMIT
    return ids[:count]


def topic_candidates(chat_state: ChatState, coord: ChatCoord,
                     limit: int = TOPIC_CANDIDATE_LIMIT) -> list:
    """半径内的候选类型；一个都没有时回退 daily 兜底。"""
    kinds = candidate_topic_kinds(
        coord, recent_kind_ids=recent_topic_kind_ids(chat_state), limit=limit)
    if kinds:
        return kinds
    fallback = fallback_topic_kind()
    return [fallback] if fallback is not None else []


def topic_deviation(chat_state: ChatState, coord: ChatCoord) -> bool:
    """当前话题是否已严重偏离它自己的类型半径。"""
    topic = active_topic(chat_state)
    if topic is None or not topic.coord:
        return False
    kind = find_topic_kind(topic.kind_id) if topic.kind_id else None
    radius = kind.radius if kind is not None else TOPIC_KIND_RADIUS
    return ChatCoord.from_values(topic.coord).distance(coord) > radius


def resolve_topic_kind(requested: str,
                       candidates: Optional[list] = None) -> str:
    """校验 AI 给的 kind：只按候选集合判断，不重算半径。

    ``candidates=None`` 表示不做候选校验（兼容旧调用），只要求是注册类型。
    非法/太远 → 回退候选中的第一个；没有候选时返回空串。
    """
    requested = requested.strip().lower() if isinstance(requested, str) else ""
    if candidates is None:
        kind = find_topic_kind(requested)
        return kind.id if kind is not None else ""
    ids = [kind.id for kind in candidates]
    if requested and requested in ids:
        return requested
    return ids[0] if ids else ""


def expire_stale_topic(chat_state: ChatState) -> bool:
    """进行中的话题超过 TOPIC_EXPIRE_HOURS 无人续聊 → 强制收束。

    服务端规则，不需要 AI 参与；返回是否有话题被收束。
    """
    active = active_topic(chat_state)
    if active is None or not active.last_activity_at:
        return False
    try:
        last = datetime.datetime.fromisoformat(active.last_activity_at)
    except ValueError:
        return False
    if (datetime.datetime.now() - last).total_seconds() < TOPIC_EXPIRE_HOURS * 3600:
        return False
    active.status = "ended"
    active.ended_at = datetime.datetime.now().isoformat()
    active.end_reason = "stale"
    chat_state.active_topic_id = ""
    return True


def apply_topic_decision(chat_state: ChatState, decision: dict,
                         had_reply: bool, source: str = "user",
                         coord: Optional[ChatCoord] = None,
                         candidates: Optional[list] = None):
    """把协议里的 topic 决策落地到话题状态机（含硬约束）。

    - end：结束当前话题（end_reason=natural）；
    - start/switch 且有 title：结束旧话题（switched）并开启新话题；
      kind 只从候选集合里取，非法/太远回退第一个候选；
    - continue / 未表态：活跃话题计数 +1；无活跃话题但有回复时，
      兜底开启一个隐式话题（粗糙实现，避免对话游离在状态机之外）；
    - 单个话题达到 MAX_TOPIC_TURNS 强制收束（stale）；列表容量由
      repo 侧截断兜底。

    ``coord`` 是本次决策生效前的坐标快照，开新话题时写入 Topic.coord；
    沉默轮（had_reply=False）不计数，但 end/switch 仍然落地。
    返回 ``{"ended_reason", "kind_id", "started"}``，供身份更新判断本次是否
    自然结束/切换了话题。
    """
    now_iso = datetime.datetime.now().isoformat()
    topics = chat_state.topics
    active = active_topic(chat_state)
    decision = decision or {}
    action = decision.get("action", "continue")
    title = decision.get("title", "")
    kind_id = resolve_topic_kind(decision.get("kind", ""), candidates)
    snapshot = coord.as_list() if isinstance(coord, ChatCoord) else []
    outcome = {"ended_reason": "", "kind_id": kind_id, "started": False}

    def end_active(reason: str):
        nonlocal active
        if active is not None:
            active.status = "ended"
            active.ended_at = now_iso
            active.end_reason = reason
            chat_state.active_topic_id = ""
            active = None
            outcome["ended_reason"] = reason

    def start_topic(topic_title: str):
        nonlocal active
        topic = Topic(title=topic_title, kind_id=kind_id, coord=snapshot,
                      status="active", source=source, started_at=now_iso,
                      last_activity_at=now_iso, turn_count=1)
        topics.append(topic)
        chat_state.active_topic_id = topic.id
        active = topic
        outcome["started"] = True

    if action == "end":
        end_active("natural")
        return outcome
    if action in ("start", "switch") and title:
        end_active("switched")
        start_topic(title)
        return outcome
    if active is None:
        if had_reply:
            # 隐式话题：AI 没有明确开题但产生了对话
            start_topic(title or "自由闲聊")
        return outcome
    if had_reply:
        active.last_activity_at = now_iso
        active.turn_count += 1
        if active.turn_count >= MAX_TOPIC_TURNS:
            end_active("stale")
    return outcome


def identity_update_allowed(chat_state: ChatState,
                            now: Optional[datetime.datetime] = None) -> bool:
    """当前线程是否已过身份认知更新冷却（按 Relationship.updated_at）。"""
    updated = (chat_state.relationship.updated_at
               if chat_state.relationship else "")
    if not updated:
        return True
    try:
        moment = datetime.datetime.fromisoformat(updated)
    except ValueError:
        return True
    current = now or datetime.datetime.now()
    try:
        elapsed = (current - moment).total_seconds()
    except TypeError:
        elapsed = (current.replace(tzinfo=None)
                   - moment.replace(tzinfo=None)).total_seconds()
    return elapsed >= IDENTITY_UPDATE_COOLDOWN_HOURS * 3600


def apply_identity_update(chat_state: ChatState, update: dict,
                          ended_reason: str,
                          now: Optional[datetime.datetime] = None) -> bool:
    """在话题自然结束/切换时更新当前身份的关系认知。

    只在 ``ended_reason`` 为 natural/switched 时生效；stale 强制收束不更新。
    kind 必须沿白名单转移图逐步变化，非法即整条丢弃；note 单独也可更新。
    每个身份受 ``IDENTITY_UPDATE_COOLDOWN_HOURS`` 冷却约束。
    """
    if not isinstance(update, dict) or not update:
        return False
    if ended_reason not in ("natural", "switched"):
        return False
    if not identity_update_allowed(chat_state, now=now):
        return False
    relationship = chat_state.relationship or Relationship()
    requested = update.get("kind", "")
    requested = (requested.strip().lower()
                 if isinstance(requested, str) else "")
    note = update.get("note", "")
    note = note.strip()[:IDENTITY_NOTE_LIMIT] if isinstance(note, str) else ""
    if requested:
        if requested not in RELATIONSHIP_KINDS:
            return False
        if (requested != relationship.kind
                and requested not in IDENTITY_TRANSITIONS.get(
                    relationship.kind, set())):
            return False
    if not requested and not note:
        return False
    now_iso = (now or datetime.datetime.now()).isoformat()
    if requested and requested != relationship.kind:
        relationship.kind = requested
        relationship.since = now_iso
    if note:
        relationship.note = note
    relationship.updated_at = now_iso
    chat_state.relationship = relationship
    return True


def schedule_batch_available_at(texts: list, online: int = 1) -> list:
    """为一批回复逐条排定 available_at（ISO 时间）。

    第一条 = 当前时间 + 读延迟 + 自己的打字延迟；之后每条在前一条的
    到达时刻上追加自己的打字延迟（长消息打字更久）。延迟函数与在线
    档倍率沿用 read_delay_seconds / typing_delay_seconds，由服务层统一
    计算，UI 不再自行模拟节奏。
    """
    moment = datetime.datetime.now() + datetime.timedelta(
        seconds=read_delay_seconds(online))
    result = []
    for text in texts:
        moment += datetime.timedelta(seconds=typing_delay_seconds(text, online))
        result.append(moment.isoformat())
    return result


def pending_char_messages(chat_state: ChatState) -> list:
    """已投递（delivered）但玩家未读的角色消息——未读徽标的唯一依据。"""
    return [m for m in chat_state.messages
            if m.role == "char" and m.status == "delivered"]


def queued_char_messages(chat_state: ChatState) -> list:
    """尚未到点投递的角色消息（"正在输入…"状态的依据）。"""
    return [m for m in chat_state.messages
            if m.role == "char" and m.status == "queued"]


# ---------- 角色级锁：跨实例共享（专业 / 挂件 / 调度器串行写同一角色） ----------
_CHAR_LOCKS = {}
_CHAR_LOCKS_GUARD = threading.Lock()


@contextmanager
def _char_lock(giantess_id: str, blocking: bool = True):
    """按 giantess_id 取进程级锁；blocking=False 时抢不到立即返回 False。"""
    with _CHAR_LOCKS_GUARD:
        lock = _CHAR_LOCKS.setdefault(giantess_id, threading.Lock())
    acquired = lock.acquire(blocking=blocking)
    try:
        yield acquired
    finally:
        if acquired:
            lock.release()


def topic_context_text(chat_state: ChatState,
                       coord: Optional[ChatCoord] = None,
                       candidates: Optional[list] = None) -> str:
    """把话题状态机与候选类型渲染为注入系统提示词的"当前话题"段落。

    只给类型名、轮数、候选 id 与两句区别（会关注什么 / 聊起来的姿态）；
    不给坐标、半径等可优化数值。
    """
    topics = chat_state.topics or []
    active = active_topic(chat_state)
    lines = []
    if active is not None:
        kind = find_topic_kind(active.kind_id) if active.kind_id else None
        label = f"（类型：{kind.label}）" if kind is not None else ""
        lines.append(f"- 进行中的话题：{active.title}{label}"
                     f"（已聊 {active.turn_count} 轮）")
        if coord is not None and topic_deviation(chat_state, coord):
            lines.append("- 注意：现在的状态已经明显偏离这个话题类型的适用"
                         "范围，可以自然结束它，或从候选里换一个更合适的类型。")
    else:
        lines.append("- 当前没有进行中的话题；聊得起来就可以开启一个"
                     "（action=start，kind 从候选里选，并给 title）")
    recent = [t for t in topics if t.id != chat_state.active_topic_id][-3:]
    if recent:
        lines.append("- 最近结束的话题："
                     + "、".join(t.title for t in recent if t.title)
                     + "（避免重复聊同样的内容）")
    if candidates:
        lines.append("- 可选话题类型（topic.kind 只能填这些 id；前半是会关注的"
                     "信息，后半是聊起来的姿态——换类型等于换一套信息和写法）：")
        for kind in candidates:
            brief = "｜".join(part for part in (kind.focus.strip(),
                                               topic_kind_hint(kind)) if part)
            lines.append(f"  - {kind.id}（{kind.label}）：{brief}" if brief
                         else f"  - {kind.id}（{kind.label}）")
    else:
        lines.append("- 此刻没有合适的候选类型；可以继续当前话题、结束它，"
                     "或者已读不回。")
    return "\n".join(lines)


def build_chat_system_prompt(state: CharacterSnapshot, chat_state: ChatState,
                             now: Optional[datetime.datetime] = None,
                             world_stance: str = DEFAULT_WORLD_STANCE) -> str:
    """组装聊天系统提示词：最小身份层 + 话题视角开放的存档字段 + 状态 +
    聊天习惯 + 话题上下文 + 输出协议。

    存档内容不再整段注入：只有当前话题类型的视角开放且放得下的字段才会
    出现在【此刻你注意到的事】里（见 services.chat.topic_lens）。``now``
    供测试注入固定时钟；``world_stance`` 由调用方（ChatService）从设置读取。
    """
    coord = chat_coord(state, chat_state, now=now, world_stance=world_stance)
    lens_block, _trace = render_topic_lens(LensContext(
        state=state, chat_state=chat_state, coord=coord,
        world_stance=world_stance, now=now, topic=active_topic(chat_state)))
    return _SYSTEM_TEMPLATE.format(
        user_nick=chat_state.user_nick or "一个普通人类",
        name=state.name,
        identity=build_identity_frame(state),
        lens_block=lens_block,
        action_points=state.action_points,
        affection_text=describe_affection(coord.affection),
        tension_text=describe_tension(coord.tension),
        size_state_text=describe_size_state(coord.size_state),
        relationship_text=relationship_text(chat_state),
        chat_habits=chat_habits_text(chat_state.chat_params),
        topic_context=topic_context_text(
            chat_state, coord, topic_candidates(chat_state, coord)),
        effect_tools=effect_tools_text(),
        output_rule=_OUTPUT_RULE,
    )


def live_context_block(state: CharacterSnapshot,
                       now: Optional[datetime.datetime] = None) -> str:
    """实时情境段（作息/空间的 now 提示，见 profile_text）；有内容时补
    一个尾空行分隔版面，无内容时留空（模板行自然退化为空行）。"""
    ctx = build_live_context(state, now=now)
    return f"{ctx}\n" if ctx else ""


# 收尾 JSON 提醒：弱模型对长系统提示词末尾的输出协议服从率低
# （实测 DeepSeek flash 系会直接输出角色扮演纯文本），把它紧跟在
# 最后一条 user 消息之后再强调一次，实测可稳定恢复协议输出。
_OUTPUT_REMINDER = ("输出提醒：你的下一条回复必须是且仅是一个 JSON 对象"
                    "（按上面规定的字段），不要输出任何 JSON 之外的文字；"
                    "决定已读不回时 messages 填 []。")


def build_chat_messages(state: CharacterSnapshot, chat_state: ChatState,
                        pending_text: str,
                        world_stance: str = DEFAULT_WORLD_STANCE) -> list:
    """组装完整的对话消息列表：system + 记忆摘要 + 滑动窗口 + 待发消息
    + 收尾 JSON 提醒（见 _OUTPUT_REMINDER）。"""
    messages = [{"role": "system",
                 "content": build_chat_system_prompt(
                     state, chat_state, world_stance=world_stance)}]
    if chat_state.memory:
        messages.append({"role": "system",
                         "content": f"更早的聊天留下的记忆摘要：\n{chat_state.memory}"})
    for m in chat_state.messages[-CHAT_WINDOW_SIZE:]:
        messages.append({"role": "user" if m.role == "user" else "assistant",
                         "content": m.text})
    messages.append({"role": "user", "content": pending_text})
    messages.append({"role": "system", "content": _OUTPUT_REMINDER})
    return messages


def parse_chat_response(response_text: str) -> dict:
    """解析聊天协议响应，返回统一结构：

    {"messages": [str, ...], "topic": {"action", "kind", "title"},
     "silent_reason": str, "refused": bool,
     "ops": {属性: -1/0/1}, "nick": str|None, "memory_note": str,
     "identity_update": {"kind", "note"}, "effect": {"tool", "args"}}

    协议要求严格 JSON，messages 为 1~4 条消息（一次决策可发多条）；
    缺失时回退读旧协议的 reply 字段。topic.action 夹取到
    continue/start/switch/end 之一（缺省 continue），kind 只做去空白与
    长度截断（候选校验在服务层落地时进行），title 截断保留。
    identity_update 只保留 kind/note，是否允许更新由服务层按话题结束方式
    与冷却判断。effect 最多一个；给成数组视为多个，整条丢弃；tool/args
    的合法性由 effects 注册表在落地时判断。
    解析不出 JSON 对象的回复（典型为
    API 拒绝敏感请求时的自然语言道歉/拒绝文本）一律视为已读不回：
    messages=[]、refused=True，原文截断后记入 silent_reason 供排查，
    不展示给玩家。messages 为空/缺失 同样是已读不回（refused=False，
    角色主观不想回）。
    """
    result = {"messages": [],
              "topic": {"action": "continue", "kind": "", "title": ""},
              "silent_reason": "", "refused": False,
              "ops": {}, "nick": None, "memory_note": "",
              "identity_update": {}, "effect": {}}
    cleaned = (response_text or "").strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()
    if not cleaned:
        return result

    start = cleaned.find("{")
    data = None
    if start != -1:
        try:
            data, _ = json.JSONDecoder().raw_decode(cleaned[start:])
        except json.JSONDecodeError:
            data = None
    if not isinstance(data, dict):
        # 非 JSON 回复：视为 API 拒绝（已读不回），原文留档排查
        result["refused"] = True
        result["silent_reason"] = cleaned[:100]
        return result

    raw_messages = data.get("messages")
    if not isinstance(raw_messages, list):
        # 旧协议回退：reply（或更早的 text）单条
        raw_messages = [data.get("reply", data.get("text"))]
    texts = []
    for item in raw_messages:
        if not isinstance(item, str):
            continue
        text = item.strip()[:SINGLE_MESSAGE_CHAR_LIMIT]
        if text:
            texts.append(text)
        if len(texts) >= MAX_BATCH_MESSAGES:
            break
    result["messages"] = texts
    topic = data.get("topic")
    if isinstance(topic, dict):
        action = topic.get("action", "continue")
        if action not in ("continue", "start", "switch", "end"):
            action = "continue"
        title = topic.get("title")
        kind = topic.get("kind")
        result["topic"] = {
            "action": action,
            "kind": (kind.strip().lower()[:32]
                     if isinstance(kind, str) else ""),
            "title": (title.strip()[:TOPIC_TITLE_LIMIT]
                      if isinstance(title, str) else ""),
        }
    reason = data.get("silent_reason")
    if isinstance(reason, str):
        result["silent_reason"] = reason.strip()
    ops = data.get("ops")
    if isinstance(ops, dict):
        for key, value in ops.items():
            if key not in CHAT_OPS_WHITELIST:
                continue
            try:
                result["ops"][key] = max(-1, min(1, int(value)))
            except (TypeError, ValueError):
                continue
    nick = data.get("nick")
    if isinstance(nick, str) and nick.strip():
        result["nick"] = nick.strip()[:24]
    note = data.get("memory_note")
    if isinstance(note, str) and note.strip():
        result["memory_note"] = note.strip()
    update = data.get("identity_update")
    if isinstance(update, dict):
        kind = update.get("kind")
        note = update.get("note")
        clean_kind = (kind.strip().lower()[:32]
                      if isinstance(kind, str) else "")
        clean_note = (note.strip()[:IDENTITY_NOTE_LIMIT]
                      if isinstance(note, str) else "")
        if clean_kind or clean_note:
            result["identity_update"] = {"kind": clean_kind,
                                         "note": clean_note}
    effect = data.get("effect")
    if isinstance(effect, dict):
        tool = effect.get("tool")
        args = effect.get("args")
        tool = tool.strip().lower()[:32] if isinstance(tool, str) else ""
        if tool:
            result["effect"] = {
                "tool": tool,
                "args": args if isinstance(args, dict) else {},
            }
    return result


def parse_chat_params(response_text: str) -> ChatParams:
    """解析一次性聊天参数决定结果；解析失败或档位非法的项回退中档。"""
    params = ChatParams()
    cleaned = (response_text or "").strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()
    start = cleaned.find("{")
    if start == -1:
        return params
    try:
        data, _ = json.JSONDecoder().raw_decode(cleaned[start:])
    except json.JSONDecodeError:
        return params
    if not isinstance(data, dict):
        return params
    for key in ("online", "warmup", "boundary"):
        try:
            setattr(params, key, max(0, min(2, int(data.get(key, 1)))))
        except (TypeError, ValueError):
            continue
    return params


def apply_chat_ops(state: CharacterSnapshot, chat_state: ChatState,
                   parsed: dict) -> dict:
    """把白名单内的属性操作写回聊天域与角色快照，返回实际生效的变化。

    attitude / user_nick 写聊天域；attitude 的步长按预热速率分档缩放
    （慢热减半、来得快加倍）；action_points 经 StateService 消耗/恢复。
    世界影响不在这里写：intrusion/destruction/casualties 已从 ops 移除，
    统一走 effect（services.chat.effects）。返回 {"attitude": 新值, ...}。
    """
    applied = {}
    ops = parsed.get("ops") or {}
    direction = ops.get("attitude", 0)
    if direction:
        warmup = chat_state.chat_params.warmup if chat_state.chat_params else 1
        step = ATTITUDE_STEP * WARMUP_ATTITUDE_SCALE.get(warmup, 1.0)
        chat_state.attitude = max(-100, min(
            100, int(chat_state.attitude + direction * step)))
        applied["attitude"] = chat_state.attitude
    direction = ops.get("action_points", 0)
    if direction > 0:
        if StateService.receive_action_points(state, ACTION_POINT_STEP):
            applied["action_points"] = state.action_points
    elif direction < 0:
        if StateService.consume_action_points(state, ACTION_POINT_STEP):
            applied["action_points"] = state.action_points
    if parsed.get("nick"):
        chat_state.user_nick = parsed["nick"]
        applied["user_nick"] = chat_state.user_nick
    return applied


class ChatService:
    """聊天功能的门面：加载存档、收发消息、写回与事件广播。"""

    def __init__(self, data_dir: str = None,
                 character_repo: CharacterRepo = None, chat_repo: ChatRepo = None,
                 world_stance_provider=None,
                 identity_repo: PlayerIdentityRepo = None):
        if data_dir is None:
            from paths import data_dir as _data_dir
            data_dir = _data_dir()
        self.data_dir = data_dir
        self.character_repo = character_repo or CharacterRepo(data_dir)
        self.chat_repo = chat_repo or ChatRepo(data_dir)
        self.identity_repo = identity_repo or PlayerIdentityRepo(data_dir)
        # 世界态势来源：优先用注入的 provider（测试/世界包），否则读全局设置
        self.world_stance_provider = world_stance_provider

    def _world_stance(self) -> str:
        """当前世界态势的语义值；读设置在失败时回退 coexist。"""
        if callable(self.world_stance_provider):
            try:
                return self.world_stance_provider()
            except Exception:
                return DEFAULT_WORLD_STANCE
        try:
            from persistence.settings_repo import SettingsRepo
            settings = SettingsRepo(self.data_dir).load()
            return settings.get("world_stance", DEFAULT_WORLD_STANCE)
        except Exception:
            return DEFAULT_WORLD_STANCE

    def load_chat(self, giantess_id: str, blocking: bool = True,
                  identity_id: str = DEFAULT_IDENTITY_ID
                  ) -> Optional[ChatState]:
        """加载一条聊天线程，并把已到点的 queued 消息提升为 delivered。

        blocking=False 供 UI 主线程使用：角色锁被 AI 请求占用时返回
        None（而非等待），调用方保留旧状态稍后重试即可。
        """
        identity_id = normalize_identity_id(identity_id)
        with _char_lock(giantess_id, blocking) as acquired:
            if not acquired:
                return None
            chat_state = self.chat_repo.load(giantess_id, identity_id)
            promoted = self._promote_due(chat_state)
            if promoted:
                self.chat_repo.save(chat_state)
                self._publish_delivered(giantess_id, identity_id, promoted)
            return chat_state

    def deliver_due(self, giantess_id: str,
                    identity_id: str = DEFAULT_IDENTITY_ID) -> list:
        """把所有到点的 queued 消息提升为 delivered（调度器到点入口）。

        返回本次提升的消息列表；若仍有未到点的 queued 消息，自动向
        调度器登记下一次唤醒。幂等：重复调用只处理未迁移的消息。
        """
        identity_id = normalize_identity_id(identity_id)
        with _char_lock(giantess_id) as acquired:
            if not acquired:
                return []
            chat_state = self.chat_repo.load(giantess_id, identity_id)
            promoted = self._promote_due(chat_state)
            if promoted:
                self.chat_repo.save(chat_state)
                self._publish_delivered(giantess_id, identity_id, promoted)
            self._reenqueue(giantess_id, chat_state)
            return promoted

    def mark_char_messages_read(self, giantess_id: str,
                                blocking: bool = True,
                                identity_id: str = DEFAULT_IDENTITY_ID) -> int:
        """把一条线程里已投递（delivered）的角色消息标记为玩家已读。

        打开聊天并渲染后由界面调用；锁被占用时（blocking=False）静默
        跳过，下次渲染周期会重新尝试。返回本次标记的条数。
        """
        identity_id = normalize_identity_id(identity_id)
        with _char_lock(giantess_id, blocking) as acquired:
            if not acquired:
                return 0
            chat_state = self.chat_repo.load(giantess_id, identity_id)
            now = datetime.datetime.now().isoformat()
            changed = []
            for message in chat_state.messages:
                if message.role == "char" and message.status == "delivered":
                    message.status = "read"
                    message.read_at = now
                    changed.append(message.id)
            if changed:
                self.chat_repo.save(chat_state)
                chat_events.publish({"type": "chat_updated",
                                     "giantess_id": giantess_id,
                                     "identity_id": identity_id})
            return len(changed)

    # ---------- 玩家身份 ----------
    def identities(self) -> list:
        """全部玩家身份（默认身份恒在且排第一）。"""
        return self.identity_repo.load()

    def active_identity_id(self) -> str:
        """界面打开聊天时应绑定的身份：上次选中的那个。"""
        return self.identity_repo.active_id()

    def set_active_identity(self, identity_id: str) -> str:
        """记住当前身份；未知 id 保持原值，返回实际生效的 id。"""
        return self.identity_repo.set_active(identity_id)

    def create_identity(self, label: str, description: str = ""):
        """新建身份并设为当前身份；数量超限或名字全空返回 None。"""
        return self.identity_repo.create(label, description)

    def unread_by_identity(self, giantess_id: str) -> dict:
        """该角色各身份线程的未读数；0 条的线程不出现在结果里。

        只统计已落盘的线程——从没打开过的身份还没有存档，也就没有未读。
        界面用它给身份选择器点小红点，角色卡上的徽标取总和。
        """
        counts = {}
        for identity_id in self.chat_repo.thread_ids(giantess_id):
            chat_state = self.chat_repo.load(giantess_id, identity_id)
            count = len(pending_char_messages(chat_state))
            if count:
                counts[identity_id] = count
        return counts

    def unread_total(self, giantess_id: str) -> int:
        """角色卡徽标：所有身份线程的未读之和（跨身份聚合只用于显示）。"""
        return sum(self.unread_by_identity(giantess_id).values())

    # ---------- 投递内部 ----------
    @staticmethod
    def _promote_due(chat_state: ChatState) -> list:
        """把 available_at 已到的 queued 消息迁移为 delivered（不落盘）。"""
        now = datetime.datetime.now()
        promoted = []
        for message in chat_state.messages:
            if message.role != "char" or message.status != "queued":
                continue
            due = True
            if message.available_at:
                try:
                    due = datetime.datetime.fromisoformat(
                        message.available_at) <= now
                except ValueError:
                    due = True
            if due:
                message.status = "delivered"
                message.delivered_at = now.isoformat()
                promoted.append(message)
        return promoted

    @staticmethod
    def _publish_delivered(giantess_id: str, identity_id: str, promoted: list):
        chat_events.publish({
            "type": "message_delivered", "giantess_id": giantess_id,
            "identity_id": identity_id,
            "message_ids": [m.id for m in promoted],
        })

    @staticmethod
    def _reenqueue(giantess_id: str, chat_state: ChatState):
        """仍有未到点的 queued 消息时，向调度器登记下一次唤醒。"""
        queued = queued_char_messages(chat_state)
        if not queued:
            return
        earliest = min(m.available_at for m in queued if m.available_at)
        from services.chat.delivery import get_scheduler
        get_scheduler().notify_queued(giantess_id, chat_state.identity_id,
                                      earliest)

    def list_characters(self) -> list:
        """列出全部可聊天角色（快照列表），供 UI 做聊天对象切换。

        每个角色的对话历史按 (giantess_id, identity_id) 线程隔离；切换对象
        = 重新 load 对应快照 + load_chat，切换身份另传 identity_id。
        """
        characters = []
        for giantess_id in self.character_repo.list_ids():
            state = self.character_repo.load(giantess_id)
            if state is not None:
                characters.append(state)
        return characters

    def send_message(self, state: CharacterSnapshot, chat_state: ChatState,
                     text: str) -> dict:
        """发送一条用户消息并同步等待角色的 AI 决策（调用方负责放后台线程）。

        返回 {"user_message", "reply_messages", "reply_message",
        "ops_applied", "refused", "error"}；reply_messages 为空列表且
        user_message.silent=True 表示已读不回（refused=True 时为 API
        拒绝所致），error 非 None 表示 AI 未配置或调用失败（消息保留、
        不计已读）。角色回复以 queued 入列，到点由调度器投递。
        """
        result = {"user_message": None, "reply_messages": [],
                  "reply_message": None, "ops_applied": {},
                  "refused": False, "error": None}
        with _char_lock(state.giantess_id):
            user_message = ChatMessage(role="user", text=text)
            chat_state.messages.append(user_message)
            result["user_message"] = user_message
            self.chat_repo.save(chat_state)
            chat_events.publish({"type": "chat_updated",
                                 "giantess_id": state.giantess_id,
                                 "identity_id": chat_state.identity_id})

            parsed = self._request_parsed(state, chat_state, text)
            if isinstance(parsed, dict) and parsed.get("_error"):
                result["error"] = parsed["_error"]
                return result

            # AI 已看到消息：无论是否回复，一律计为已读
            user_message.read = True
            user_message.seen_by_char_at = datetime.datetime.now().isoformat()
            if isinstance(parsed, dict) and parsed.get("silent_reason"):
                user_message.silent_reason_internal = parsed["silent_reason"]
            result.update(self._finish_exchange(state, chat_state, parsed))
            if not result["reply_messages"]:
                # 已读不回（角色主观沉默或 API 拒绝）：与离线补话路径一致，
                # 把本条用户消息标记为 silent——否则该状态在档案里恒为 False
                user_message.silent = True
                self.chat_repo.save(chat_state)
        return result

    def recall_exchange(self, chat_state: ChatState, user_message: ChatMessage,
                        reply_message: ChatMessage = None) -> bool:
        """撤回一次尚未被"看到"的对话：把消息从历史中移除并落盘。

        按 id 匹配（界面重载后消息对象会换新，按身份匹配会静默失效）。
        仅供 UI 在"角色还没看到"的窗口期内调用；消息物理删除后不会进入
        之后的任何 AI 上下文，即视为没看到。已投递/已读的角色消息与已被
        看到的玩家消息拒绝删除。返回是否确有删除。
        """
        targets = {m.id for m in (user_message, reply_message) if m is not None}
        if not targets:
            return False
        removed = False
        kept = []
        for message in chat_state.messages:
            if message.id in targets:
                too_late = ((message.role == "char"
                             and message.status in ("delivered", "read"))
                            or (message.role == "user" and message.read))
                if too_late:
                    kept.append(message)
                    continue
                removed = True
                continue
            kept.append(message)
        if removed:
            chat_state.messages = kept
            self.chat_repo.save(chat_state)
            chat_events.publish({"type": "chat_updated",
                                 "giantess_id": chat_state.giantess_id,
                                 "identity_id": chat_state.identity_id})
        return removed

    def reconcile(self, state: CharacterSnapshot, chat_state: ChatState) -> dict:
        """统一社交决策入口（阶段四）：合并处理回复积压、未消费的
        经历事件与主动搭话，一次 AI 决策——或回应、或开题、或沉默。

        决策成功后积压消息才计已读、经历事件才计已消费；AI 调用失败
        两者都保持原状，下次打开继续。无可处理事项时 nothing=True。
        """
        empty = {"user_message": None, "reply_messages": [],
                 "reply_message": None, "ops_applied": {},
                 "refused": False, "error": None}
        with _char_lock(state.giantess_id):
            expired = expire_stale_topic(chat_state)
            pending = unread_messages(chat_state)
            events = collect_experience_events(state, chat_state)
            if not pending and not events and not should_proactive(chat_state):
                if expired:
                    self.chat_repo.save(chat_state)
                return {**empty, "nothing": True}
            parts = []
            if pending:
                lines = "\n".join(f"- {m.text}"
                                  for m in pending[-OFFLINE_REPLY_LIMIT:])
                parts.append(
                    f"你有一段时间没看手机了。现在刚拿起手机，看到 "
                    f"{len(pending)} 条未读消息：\n{lines}\n"
                    "请针对这些消息作出回应：可以合并、逐条或一次发多条；"
                    "以你的性格不想理就 messages 填 []。")
            if events:
                shown = events_for_prompt(events)
                lines = "\n".join(f"- {e['summary']}" for e in shown)
                parts.append(
                    "你最近经历了这些事：\n" + lines +
                    "\n如果你愿意，可以把其中某件事当作新话题主动聊起来"
                    "（topic.action=start 并给 title）；不想提也完全可以。")
            if not pending and not events:
                context_lines = []
                if state.last_news:
                    context_lines.append(
                        f"最近关于你的新闻：{state.last_news[:120]}")
                if state.position:
                    context_lines.append(
                        f"你目前在：{format_addr_compact(state.position)}")
                live_ctx = build_live_context(state)
                if live_ctx:
                    context_lines.append(live_ctx)
                context = ("\n".join(context_lines) + "\n") if context_lines else ""
                parts.append(
                    "你隔了一段时间拿起手机，没有新消息。\n" + context +
                    "结合你们之前的聊天与上面的近况，如果你有想说的，就主动发"
                    "一条消息（像随手分享生活，而不是刻意）；不想主动就 "
                    "messages 填 []。")
            parts.append(
                "话题处理：延续当前话题（continue）、自然结束它（end）、"
                "或换个新话题（switch/start 并给 title），由你自主决定。")
            instruction = "\n\n".join(parts)
            parsed = self._request_parsed(state, chat_state, instruction)
            if isinstance(parsed, dict) and parsed.get("_error"):
                return {**empty, "error": parsed["_error"]}
            now_iso = datetime.datetime.now().isoformat()
            for message in pending:
                message.read = True
                message.seen_by_char_at = now_iso
            consume_experience_events(chat_state, events)
            topic_source = ("user" if pending
                            else "event" if events else "character")
            result = self._finish_exchange(state, chat_state, parsed,
                                           topic_source=topic_source)
            if not result["reply_messages"] and pending:
                # 角色看完未读仍选择沉默：与单条发送路径一致，标记已读不回
                for message in pending:
                    message.silent = True
            self.chat_repo.save(chat_state)
            return result

    # ---------- 内部 ----------
    def ensure_chat_params(self, state: CharacterSnapshot,
                           chat_state: ChatState) -> Optional[str]:
        """首次聊天前由 AI 依据人设（公开+隐藏）一次性决定聊天参数并落盘。

        返回 None 表示参数已就绪（含早已决定过的情形）；返回错误文本表示
        本次无法决定（AI 未配置/调用失败），调用方应中止本次交流。
        """
        if chat_state.chat_params is not None:
            return None
        client, _ = get_ai_client()
        if client is None:
            return "未配置 AI 客户端，请在设置中填写 API 信息"
        try:
            raw = client.generate_once(
                [{"role": "user", "content": _PARAM_SYSTEM_TEMPLATE.format(
                    name=state.name,
                    persona=build_character_persona(state, include_hidden=True))}],
                temperature=0.4)
            chat_state.chat_params = parse_chat_params(raw)
            self.chat_repo.save(chat_state)
            return None
        except Exception as e:
            return f"AI 调用失败：{describe_ai_error(e)}"

    def _request_parsed(self, state: CharacterSnapshot, chat_state: ChatState,
                        pending_text: str):
        """调 AI 并解析；出错时返回 {"_error": 文本}，成功返回协议 dict。
        参数未决定时先走一次性的聊天参数决定（再进行本次对话请求）。"""
        error = self.ensure_chat_params(state, chat_state)
        if error:
            return {"_error": error}
        client, _ = get_ai_client()
        if client is None:
            return {"_error": "未配置 AI 客户端，请在设置中填写 API 信息"}
        try:
            world_stance = self._world_stance()
            raw = client.generate_once(
                build_chat_messages(state, chat_state, pending_text,
                                    world_stance=world_stance),
                temperature=0.8)
            parsed = parse_chat_response(raw)
            if parsed.get("refused"):
                # 非 JSON 未必是 API 拒绝：弱模型偶发跑格式（直接输出
                # 角色扮演纯文本）也会落到这里。重试一次，仍是非 JSON
                # 才按已读不回处理。
                raw = client.generate_once(
                    build_chat_messages(state, chat_state, pending_text,
                                        world_stance=world_stance),
                    temperature=0.8)
                parsed = parse_chat_response(raw)
            return parsed
        except Exception as e:
            return {"_error": f"AI 调用失败：{describe_ai_error(e)}"}

    def _finish_exchange(self, state: CharacterSnapshot, chat_state: ChatState,
                         parsed: dict, topic_source: str = "user") -> dict:
        """协议解析结果的统一落地：ops 写回、回复批量入列（queued）、
        世界效果、话题状态机落地、记忆与存档、事件、调度器登记。

        落地顺序：ops → memory → effect → topic → identity_update → 保存。
        坐标快照在 ops/effect 生效前取得，效果从下一轮才影响坐标。

        角色回复不直接可见：每条以 status="queued" 入列并按在线档排定
        available_at（batch_id = 本次决策的 request_id），由投递调度器
        到点提升为 delivered 后才允许出现在界面上。

        返回 dict 不含 user_message（本方法不接触用户消息，调用方自行
        填充；此前混入的 user_message=None 会在 send_message 的
        result.update 时把真实用户消息覆盖掉，导致 silent 标记与撤回
        都拿到 None）。
        """
        request_id = uuid.uuid4().hex
        result = {"reply_messages": [], "reply_message": None,
                  "ops_applied": {}, "effect_applied": None,
                  "refused": bool(parsed.get("refused")),
                  "error": None, "batch_id": request_id}
        # 坐标在 ops 生效前快照：AI 是按看到的状态选的话题类型，
        # 校验与落盘都用同一份，效果从下一轮才影响坐标。
        coord = chat_coord(state, chat_state,
                           world_stance=self._world_stance())
        candidates = topic_candidates(chat_state, coord)
        result["ops_applied"] = apply_chat_ops(state, chat_state, parsed)
        texts = parsed.get("messages") or []
        if texts:
            online = (chat_state.chat_params.online
                      if chat_state.chat_params else 1)
            available = schedule_batch_available_at(texts, online)
            for sequence, (text, available_at) in enumerate(zip(texts, available)):
                reply_message = ChatMessage(
                    role="char", text=text, batch_id=request_id,
                    sequence=sequence, status="queued",
                    available_at=available_at)
                chat_state.messages.append(reply_message)
                result["reply_messages"].append(reply_message)
            result["reply_message"] = result["reply_messages"][0]
        if parsed.get("memory_note"):
            self._append_memory(chat_state, parsed["memory_note"])
        effect_record, effect_changed = run_effect(
            state, chat_state, parsed.get("effect"))
        if effect_record is not None:
            result["effect_applied"] = {
                "tool": effect_record.tool,
                "result": effect_record.result,
            }
        outcome = apply_topic_decision(
            chat_state, parsed.get("topic"), had_reply=bool(texts),
            source=topic_source, coord=coord, candidates=candidates)
        if parsed.get("identity_update"):
            apply_identity_update(chat_state, parsed["identity_update"],
                                  outcome.get("ended_reason", ""))
        chat_state.last_active_at = datetime.datetime.now().isoformat()
        self.chat_repo.save(chat_state)
        if result["ops_applied"] or effect_changed:
            # 行动点/世界效果写到了主档案，需要一并落盘
            self.character_repo.save(state)
        chat_events.publish({
            "type": "chat_updated", "giantess_id": state.giantess_id,
            "identity_id": chat_state.identity_id,
            "replied": bool(result["reply_messages"]),
            "ops_applied": result["ops_applied"],
            "effect_applied": result["effect_applied"],
        })
        if result["reply_messages"]:
            # 登记最早到点时间，调度器到点自动投递
            self._reenqueue(state.giantess_id, chat_state)
        return result

    @staticmethod
    def _append_memory(chat_state: ChatState, note: str):
        chat_state.memory = (chat_state.memory + "\n" + note).strip()
        if len(chat_state.memory) > MEMORY_LIMIT:
            # 从最旧处截断，保留最近 MEMORY_LIMIT 字符（按行边界对齐）
            chat_state.memory = chat_state.memory[-MEMORY_LIMIT:].lstrip("\n")
