"""话题类型的"存档 → 提示词翻译器"守卫（docs/designs/chat_topic_lens.md）。

验证五件事：

1. 一处定义：坐标、语气与视角同属一条 ``TopicKind``，字段引用都能对上目录。
2. 分层：只有 public 字段能进身份层；contextual / secret 必须经过话题视角。
3. 侧重：高紧张度类型看得到新闻与世界态度、看不到隐藏介绍；允许透露的
   类型才拿得到隐藏介绍，且仍受好感门槛约束。
4. 预算：字段总量受视角预算约束，被挡掉的字段会在 trace 里写明原因；
   同一 group 不会霸占整个预算。
5. 可替换：整表安装 / 还原、JSON 往返、坏表被拒绝——世界包将来走这条路
   增删话题。

只锁机制，不锁文案：改 frame / focus / 权重 / 预算不应让本自检变红，
但"某类信息在某个视角下不可见"这类断言必须成立。

用法：``python tests/check_chat_topic_lens.py``
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.models import (  # noqa: E402
    ChatState, CharacterSnapshot, Personality, Topic,
)
from services.chat.coordinates import ChatCoord  # noqa: E402
from services.chat.topic_kinds import (  # noqa: E402
    NO_TOPIC_LENS, TOPIC_KINDS, TopicKind, active_kinds, candidate_topic_kinds,
    find_topic_kind, install_topic_kinds, lens_for, reset_topic_kinds,
    topic_kind_from_dict, topic_kind_to_dict, validate_topic_kinds,
)
from services.chat.topic_lens import (  # noqa: E402
    FIELD_INDEX, GROUP_CAP, HIDDEN_INTRO_ATTITUDE, TIER_PUBLIC, LensContext,
    LensTrace, identity_frame, lens_coverage, pack_fields, render_lens,
)

PROBLEMS = []


def check(name, ok, extra=""):
    if not ok:
        PROBLEMS.append(name + (f" <{extra}>" if extra else ""))


def _personality() -> Personality:
    return Personality(name="冷", init_intrusion=2.0, step_intrusion=0.1,
                       init_destruction=2.0, step_destruction=0.1,
                       sensitivity=1.0, gravity=1.0,
                       description="说话短，慢热，不轻易信人")


def rich_state() -> CharacterSnapshot:
    """一份"什么都有"的存档：用来验证信息是按视角过滤的，而不是全给。"""
    state = CharacterSnapshot(giantess_id="lens-1", name="林砚",
                              nick="砚", height=200.0, action_points=80)
    state.personality = _personality()
    state.intro_visible = "公开介绍：常年在城北活动。"
    state.intro_hidden = "隐藏介绍：她其实怕被人看见自己走路的样子。"
    state.selected_tags = ["寡言", "记仇"]
    state.birthday = "11 月 3 日"
    state.will_status = "意志清醒，但懒得解释"
    state.position = "城北·旧水塔"
    state.last_news = "城北出现身高两百米的女性，市政厅暂未回应。"
    state.achieved_endings = [{"name": "第一次被拍到", "achieved_at": "2026-10-01T10:00:00"},
                              {"name": "旧水塔倒塌", "achieved_at": "2026-10-20T10:00:00"}]
    state.size_unlocks = {"脚掌": "一条街那么长", "手掌": "能盖住一栋楼"}
    state.record_change(intrusion=2.6, destruction=2.2)
    return state


def ctx_for(kind_id: str, attitude: int = 0, world_stance: str = "hostile"):
    state = rich_state()
    chat = ChatState(giantess_id=state.giantess_id, attitude=attitude)
    topic = None
    if kind_id:
        topic = Topic(title="当前话题", kind_id=kind_id, status="active",
                      turn_count=2)
        chat.topics.append(topic)
        chat.active_topic_id = topic.id
    return LensContext(state=state, chat_state=chat,
                       coord=ChatCoord(0.5, 0.5, 0.5),
                       world_stance=world_stance, topic=topic)


def render(kind_id: str, attitude: int = 0):
    return render_lens(lens_for(kind_id), ctx_for(kind_id, attitude))


# ---------------- 1. 注册表 ----------------
def check_registry() -> list:
    problems = [f"类型表结构问题：{item}" for item in
                validate_topic_kinds(TOPIC_KINDS, NO_TOPIC_LENS)]
    problems += [f"视角引用了未注册/越层字段：{item}" for item in lens_coverage()]
    ids = [kind.id for kind in TOPIC_KINDS]
    if len(ids) != len(set(ids)):
        problems.append("话题类型 id 必须唯一")
    for kind in TOPIC_KINDS:
        if find_topic_kind(kind.id) is not kind:
            problems.append(f"{kind.id} 查表取回的不是同一条定义")
        if lens_for(kind.id) is not kind:
            problems.append(f"{kind.id} 的视角与定义不是同一条")
        if not kind.focus.strip():
            problems.append(f"{kind.id} 缺 focus：候选列表里没有信息提示")
        if kind.budget <= 0:
            problems.append(f"{kind.id} 的字段预算必须为正")
        for field_id in kind.denies:
            if field_id not in FIELD_INDEX:
                problems.append(f"{kind.id} 的 denies 指向未注册字段 {field_id}")
    if lens_for("not-a-kind") is not NO_TOPIC_LENS:
        problems.append("未知类型应回退到无话题视角")
    if lens_for("") is not NO_TOPIC_LENS:
        problems.append("空类型应回退到无话题视角")
    if not NO_TOPIC_LENS.virtual:
        problems.append("无话题视角应标记为 virtual")
    if find_topic_kind(NO_TOPIC_LENS.id) is not None:
        problems.append("无话题视角不应被当作可选类型查到")
    return problems


# ---------------- 2. 分层 ----------------
def check_tiers() -> list:
    problems = []
    state = rich_state()
    frame = identity_frame(state)
    for secret in (state.intro_hidden, state.will_status, state.last_news,
                   "一条街那么长", "城北·旧水塔"):
        if secret and secret in frame:
            problems.append(f"身份层不应包含存档字段：{secret[:12]}")
    for public in (state.name, state.intro_visible, "寡言"):
        if public and public not in frame:
            problems.append(f"身份层应保留 public 字段：{public[:12]}")
    if identity_frame(CharacterSnapshot(giantess_id="e", name="空")) == "":
        problems.append("空快照的身份层也不应为空")
    if [fid for fid in FIELD_INDEX.values() if fid.tier == TIER_PUBLIC] == []:
        problems.append("注册表应声明 public 层字段")
    return problems


# ---------------- 3. 侧重（信息经话题过滤） ----------------
def check_focus() -> list:
    problems = []
    spar_text, spar_trace = render("spar", attitude=100)
    if rich_state().last_news[:20] not in spar_text:
        problems.append("高紧张度视角应拿得到新闻")
    if "怕被人看见" in spar_text:
        problems.append("不允许透露的视角不应看到隐藏介绍")

    flirt_low, flirt_low_trace = render("flirt", attitude=0)
    if "怕被人看见" in flirt_low:
        problems.append("好感不足时即便视角允许也不应看到隐藏介绍")
    if "gated" not in [why for _, why in flirt_low_trace.dropped]:
        problems.append("隐藏介绍被好感门槛挡掉时应留下原因")
    flirt_high, _ = render("flirt", attitude=HIDDEN_INTRO_ATTITUDE)
    if "怕被人看见" not in flirt_high:
        problems.append("允许透露 + 好感达标时应看到隐藏介绍")
    if "市政厅" in flirt_high:
        problems.append("高好感视角不该顺带塞新闻")

    body_text, _ = render("body", attitude=100)
    if "一条街那么长" not in body_text:
        problems.append("身体视角应拿得到已测尺寸")
    if "市政厅" in body_text:
        problems.append("身体视角不该拿新闻")

    aloof_text, aloof_trace = render("aloof", attitude=100)
    if len(aloof_trace.included) > 2:
        problems.append(f"疏离视角应只开放极少量字段，实际 {len(aloof_trace.included)}")
    for other in ("spar", "body", "showoff"):
        other_text, other_trace = render(other, attitude=100)
        if aloof_trace.chars >= other_trace.chars:
            problems.append(f"疏离视角应比其他视角信息更少（对比 {other}）")
        if len(aloof_text) > len(other_text):
            problems.append(f"疏离视角文本应更短（对比 {other}）")
    return problems


# ---------------- 4. 预算与分组 ----------------
def check_budget() -> list:
    problems = []
    for kind in TOPIC_KINDS + (NO_TOPIC_LENS,):
        ctx = ctx_for(kind.id, attitude=100)
        _, trace = render_lens(kind, ctx)
        field_chars = sum(chars for _, _, chars in trace.included)
        if field_chars > kind.budget:
            problems.append(f"{kind.id} 字段总量 {field_chars} 超出预算 {kind.budget}")
        groups = {}
        for field_id, _, _ in trace.included:
            group = FIELD_INDEX[field_id].group
            groups[group] = groups.get(group, 0) + 1
        for group, count in groups.items():
            if count > GROUP_CAP:
                problems.append(f"{kind.id} 的 {group} 组取了 {count} 条，超过上限")

    # 放不下就挡掉，而不是把字段砍成半句
    ctx = ctx_for("spar", attitude=100)
    cramped = TopicKind(id="cramped", label="紧", coord=ChatCoord(0.5, 0.5, 0.5),
                        prompt="紧", focus="紧", frame="紧", budget=60,
                        opens=(("self.size", 1.0), ("world.news", 1.0)))
    _, trace = render_lens(cramped, ctx)
    reasons = [why for _, why in trace.dropped]
    if "budget" not in reasons:
        problems.append("超预算字段应被挡掉并记原因")
    if sum(chars for _, _, chars in trace.included) > cramped.budget:
        problems.append("挡掉后仍超出预算")

    # 打包函数本身：预算耗尽后不再塞字段
    picked = [(10.0, 0, "self.size", "x" * 400), (5.0, 1, "world.news", "y" * 400)]
    tiny = TopicKind(id="unit", label="单元", coord=ChatCoord(0.5, 0.5, 0.5),
                     prompt="单元", focus="单元", frame="单元", budget=500)
    unit_trace = LensTrace(lens_id="unit", budget=tiny.budget)
    kept = pack_fields(picked, tiny, unit_trace)
    if len(kept) != 1 or sum(len(t) for t in kept) > tiny.budget:
        problems.append("打包应受预算约束")
    return problems


# ---------------- 5. 不泄数值 / 空态 ----------------
def check_no_leak() -> list:
    problems = []
    text, _ = render("spar", attitude=100)
    for leak in ("0.50", "半径", "affection"):
        if leak in text:
            problems.append(f"渲染结果不应出现内部数值：{leak}")
    if "【此刻你注意到的事】" not in text:
        problems.append("渲染块应有固定标题，便于审计")
    empty = LensContext(state=CharacterSnapshot(giantess_id="e", name="空"),
                        chat_state=ChatState(giantess_id="e"))
    text_empty, trace_empty = render_lens(lens_for("memory"), empty)
    if not text_empty.strip():
        problems.append("空快照也应渲染出标题与框架")
    if trace_empty.included:
        problems.append("空快照不应凭空取到字段")
    return problems


# ---------------- 6. 整表替换（世界包入口） ----------------
def check_catalog() -> list:
    problems = []
    daily = find_topic_kind("daily")
    if topic_kind_from_dict(topic_kind_to_dict(daily)) != daily:
        problems.append("JSON 进出后类型定义应保持不变")

    every = [topic_kind_to_dict(kind) for kind in TOPIC_KINDS]
    every.append(topic_kind_to_dict(NO_TOPIC_LENS))
    try:
        restored = install_topic_kinds(every)
        if [kind.id for kind in restored] != [kind.id for kind in TOPIC_KINDS]:
            problems.append("整表导出再导入后不该丢类型")
        if lens_for("") != NO_TOPIC_LENS:
            problems.append("JSON 里的 virtual 条目应被取作无话题视角")
    finally:
        reset_topic_kinds()

    custom = TopicKind(id="pact", label="缔约", coord=ChatCoord(0.4, 0.6, 0.6),
                       prompt="语气慎重。", focus="双方能给出什么",
                       frame="你在算这笔交易值不值。",
                       opens=(("world.stance", 1.0),), budget=400)
    try:
        install_topic_kinds([custom])
        if [kind.id for kind in active_kinds()] != ["pact"]:
            problems.append("整表替换后生效表不正确")
        if find_topic_kind("daily") is not None:
            problems.append("整表替换后旧类型不应再被查到")
        if lens_for("").focus != NO_TOPIC_LENS.focus:
            problems.append("整表替换后无话题视角应保持默认")
        if [k.id for k in candidate_topic_kinds(ChatCoord(0.4, 0.6, 0.6))] != ["pact"]:
            problems.append("替换后的类型应参与候选筛选")
        ctx = LensContext(state=rich_state(),
                          chat_state=ChatState(giantess_id="lens-1"),
                          world_stance="hostile")
        text, _ = render_lens(custom, ctx)
        if "外界对你的态度" not in text:
            problems.append("替换后的类型应能按自己的视角渲染")
    finally:
        reset_topic_kinds()
    if find_topic_kind("daily") is None:
        problems.append("重置后应恢复默认表")
    if lens_for("") is not NO_TOPIC_LENS:
        problems.append("重置后无话题视角应恢复默认")

    bad = TopicKind(id="bad", label="坏", coord=ChatCoord(0.5, 0.5, 0.5),
                    prompt="坏", focus="坏", frame="坏",
                    opens=(("self.nonexistent", 1.0),))
    for entry, why in ((bad, "引用了未注册字段"),
                       (TopicKind(id="bad", label="坏", coord=ChatCoord(0.5, 0.5, 0.5),
                                  prompt="坏", focus="", frame="坏"), "缺 focus")):
        try:
            install_topic_kinds([entry])
        except ValueError:
            pass
        else:
            problems.append(f"{why}的类型表应被拒绝")
    if find_topic_kind("daily") is None:
        problems.append("校验失败不应改动生效表")

    for broken in ({"id": "x", "label": "x", "coord": [0.1, 0.2]},
                   {"id": "", "label": "x", "coord": [0.1, 0.2, 0.3]},
                   {"id": "x", "label": "x",
                    "coord": [0.1, float("nan"), 0.3]}):
        try:
            topic_kind_from_dict(broken)
        except ValueError:
            pass
        else:
            problems.append(f"坏条目应被拒绝：{broken!r}")
    return problems


def main() -> int:
    problems = (check_registry() + check_tiers() + check_focus()
                + check_budget() + check_no_leak() + check_catalog())
    if problems:
        print("[check_chat_topic_lens] FAILED")
        for item in problems:
            print("  - " + item)
        return 1
    print("[check_chat_topic_lens] PASSED：一处定义、按视角分层限量、可整体替换")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
