"""聊天话题类型选择、提示词与坐标快照守卫。

覆盖设计文档 §11.3 的离线部分：
- 协议解析 topic.kind，非法值不进入话题；
- 提示词只给三轴定性描述与候选类型，不给坐标；
- 候选按半径过滤、无候选时回退 daily；
- 开题/切换写入 kind_id 与开题时的坐标快照，非法 kind 回退第一个候选；
- 沉默轮仍然落地 end/switch，但不计轮数；
- _finish_exchange 用 ops 生效前的坐标校验并落盘。

不锁具体初始类型集合与调参值：断言只依赖注册表与模块常量。

用法：``python tests/check_chat_topic_selection.py``
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.models import CharacterSnapshot, ChatState, Topic  # noqa: E402
from persistence.character_repo import CharacterRepo  # noqa: E402
from persistence.chat_repo import ChatRepo  # noqa: E402
from services.chat import (  # noqa: E402
    ChatService, active_topic, apply_topic_decision, build_chat_system_prompt,
    chat_coord, parse_chat_response, recent_topic_kind_ids, resolve_topic_kind,
    topic_candidates, topic_context_text, topic_deviation,
)
from services.chat.coordinates import (  # noqa: E402
    ChatCoord, describe_affection, describe_size_state, describe_tension,
)
from services.chat.topic_kinds import (  # noqa: E402
    TOPIC_CANDIDATE_LIMIT, TopicKind, fallback_topic_kind,
)


def _check(problems, name, ok, extra=""):
    if not ok:
        problems.append(name + (f" <{extra}>" if extra else ""))


def _state() -> CharacterSnapshot:
    state = CharacterSnapshot(giantess_id="g1", name="测试", action_points=100)
    state.record_change(intrusion=2.5, destruction=2.5)
    return state


def _chat(attitude: int = 0) -> ChatState:
    return ChatState(giantess_id="g1", attitude=attitude)


def check_protocol() -> list:
    problems = []
    parsed = parse_chat_response(json.dumps({
        "messages": ["嗯"],
        "topic": {"action": "switch", "kind": " Flirt ", "title": "换个话题"},
    }, ensure_ascii=False))
    _check(problems, "topic.kind 应去空白并转小写",
           parsed["topic"]["kind"] == "flirt")
    _check(problems, "topic.action 仍应正常解析",
           parsed["topic"]["action"] == "switch"
           and parsed["topic"]["title"] == "换个话题")

    bad = parse_chat_response(json.dumps({
        "messages": ["嗯"], "topic": {"kind": 123},
    }))
    _check(problems, "非字符串 kind 应为空", bad["topic"]["kind"] == "")

    legacy = parse_chat_response(json.dumps({"reply": "旧协议"}))
    _check(problems, "旧 reply 协议仍应可读",
           legacy["messages"] == ["旧协议"] and legacy["topic"]["kind"] == "")
    return problems


def check_prompt() -> list:
    problems = []
    state = _state()
    chat = _chat()
    coord = chat_coord(state, chat)
    prompt = build_chat_system_prompt(state, chat)

    _check(problems, "提示词应注入好感定性描述",
           describe_affection(coord.affection) in prompt)
    _check(problems, "提示词应注入紧张度定性描述",
           describe_tension(coord.tension) in prompt)
    _check(problems, "提示词应注入状态定性描述",
           describe_size_state(coord.size_state) in prompt)
    _check(problems, "输出协议应包含 topic.kind 字段", "kind" in prompt)

    candidates = topic_candidates(chat, coord)
    _check(problems, "候选数量应在 1~上限之间",
           1 <= len(candidates) <= TOPIC_CANDIDATE_LIMIT,
           len(candidates))
    for kind in candidates:
        _check(problems, f"候选 {kind.id} 的 id 与名称应注入",
               kind.id in prompt and kind.label in prompt)
        coord_text = (f"({kind.coord.affection:.2f}, "
                      f"{kind.coord.tension:.2f}, {kind.coord.size_state:.2f})")
        _check(problems, "候选不应泄露坐标", coord_text not in prompt)

    current_kind = candidates[0]
    chat.topics.append(Topic(
        title="近况", kind_id=current_kind.id, coord=coord.as_list(),
        status="active", turn_count=3))
    chat.active_topic_id = chat.topics[-1].id
    context = topic_context_text(chat, coord, candidates)
    _check(problems, "应注入当前话题标题与类型名",
           "近况" in context and current_kind.label in context)
    _check(problems, "应注入当前话题轮数",
           str(chat.topics[-1].turn_count) in context)
    _check(problems, "坐标贴近快照时不应判为偏离",
           not topic_deviation(chat, coord))

    chat.topics[-1].coord = [0.0, 0.0, 0.0]
    _check(problems, "偏离检测应识别超出类型半径的话题",
           topic_deviation(chat, coord))
    _check(problems, "偏离时应额外注入提示",
           topic_context_text(chat, coord, candidates) != context)

    no_candidates = topic_context_text(chat, coord, [])
    _check(problems, "没有候选时的段落应与有候选时不同",
           no_candidates != topic_context_text(chat, coord, candidates))
    return problems


def check_candidates_and_snapshot() -> list:
    problems = []
    state = _state()
    chat = _chat()
    coord = chat_coord(state, chat)
    candidates = [
        TopicKind(id="alpha", label="甲", coord=ChatCoord(0.5, 0.5, 0.5)),
        TopicKind(id="beta", label="乙", coord=ChatCoord(0.5, 0.5, 0.5)),
    ]
    candidate_ids = [kind.id for kind in candidates]

    apply_topic_decision(chat, {"action": "start", "kind": "beta",
                                "title": "靠近"}, had_reply=True,
                         coord=coord, candidates=candidates)
    topic = active_topic(chat)
    _check(problems, "开题应写入候选类型 id",
           topic is not None and topic.kind_id == "beta", topic)
    _check(problems, "开题应写入坐标快照",
           topic is not None and len(topic.coord) == 3
           and abs(topic.coord[0] - coord.affection) < 1e-6)

    old_id = topic.id
    apply_topic_decision(chat, {"action": "switch", "kind": "alpha",
                                "title": "换话题"}, had_reply=True,
                         coord=coord, candidates=candidates)
    new_topic = active_topic(chat)
    old_topic = next(t for t in chat.topics if t.id == old_id)
    _check(problems, "切换应结束旧话题并标记 switched",
           old_topic.status == "ended" and old_topic.end_reason == "switched")
    _check(problems, "切换应开启带类型的新话题",
           new_topic is not None and new_topic.kind_id == "alpha")

    apply_topic_decision(chat, {"action": "switch", "kind": "not-a-kind",
                                "title": "回退"}, had_reply=True,
                         coord=coord, candidates=candidates)
    _check(problems, "非法 kind 应回退第一个候选",
           active_topic(chat).kind_id == candidate_ids[0],
           active_topic(chat).kind_id)

    apply_topic_decision(chat, {"action": "switch", "kind": "guard",
                                "title": "太远"}, had_reply=True,
                         coord=coord, candidates=candidates)
    _check(problems, "候选之外的 kind 应回退第一个候选",
           active_topic(chat).kind_id == candidate_ids[0])

    _check(problems, "无候选校验时注册类型仍可解析",
           resolve_topic_kind("daily") == "daily"
           and resolve_topic_kind("not-a-kind") == "")
    _check(problems, "有候选校验时只接受候选集合",
           resolve_topic_kind("alpha", candidates) == "alpha"
           and resolve_topic_kind("daily", candidates) == candidate_ids[0])

    far = ChatCoord(0.0, 1.0, 0.0)
    fallback = fallback_topic_kind()
    fallback_candidates = topic_candidates(_chat(), far)
    _check(problems, "所有类型都在半径外时应回退 daily 兜底",
           fallback is not None
           and [kind.id for kind in fallback_candidates] == [fallback.id])
    _check(problems, "兜底候选仍可被 AI 选中",
           resolve_topic_kind(fallback.id, fallback_candidates)
           == fallback.id)

    recent_chat = _chat()
    for kind_id in ("daily", "showoff", "daily"):
        recent_chat.topics.append(Topic(title=kind_id, kind_id=kind_id,
                                        coord=[], status="ended"))
    recent = recent_topic_kind_ids(recent_chat, limit=2)
    _check(problems, "最近类型应按新到旧取指定条数",
           recent == ["daily", "showoff"], recent)
    return problems


def check_silence_and_limits() -> list:
    problems = []
    state = _state()
    chat = _chat()
    coord = chat_coord(state, chat)
    candidates = [TopicKind(id="daily", label="日常", coord=coord)]

    apply_topic_decision(chat, {"action": "start", "kind": "daily",
                                "title": "沉默开题"}, had_reply=False,
                         coord=coord, candidates=candidates)
    topic = active_topic(chat)
    _check(problems, "沉默轮仍应落地 start 话题",
           topic is not None and topic.kind_id == "daily")

    before = topic.turn_count
    apply_topic_decision(chat, {"action": "continue"}, had_reply=False,
                         coord=coord, candidates=candidates)
    _check(problems, "沉默轮不应增加话题轮数",
           active_topic(chat).turn_count == before)

    apply_topic_decision(chat, {"action": "end"}, had_reply=False,
                         coord=coord, candidates=candidates)
    _check(problems, "沉默轮仍应落地 end",
           active_topic(chat) is None
           and chat.topics[-1].end_reason == "natural")
    return problems


def check_finish_exchange() -> list:
    problems = []
    root = tempfile.mkdtemp(prefix="chat_topic_selection_")
    repo = ChatRepo(root)
    service = ChatService(data_dir=root,
                          character_repo=CharacterRepo(root),
                          chat_repo=repo)
    service._reenqueue = lambda *args, **kwargs: None

    state = _state()
    chat = _chat()
    before = chat_coord(state, chat)
    chosen = topic_candidates(chat, before)[0].id
    result = service._finish_exchange(state, chat, {
        "messages": ["你好"],
        "topic": {"action": "start", "kind": chosen, "title": "日常"},
        "ops": {"attitude": 1},
    })
    saved = repo.load("g1")
    topic = active_topic(saved)
    _check(problems, "一次交流应落盘开题类型",
           topic is not None and topic.kind_id == chosen)
    _check(problems, "坐标快照应取 ops 生效前的值",
           topic is not None and len(topic.coord) == 3
           and abs(topic.coord[0] - before.affection) < 1e-6,
           topic.coord if topic else None)
    _check(problems, "态度 ops 仍应独立生效", saved.attitude > 0,
           saved.attitude)
    _check(problems, "回复应以 queued 入列",
           result["reply_messages"] and
           result["reply_messages"][0].status == "queued")

    far_state = _state()
    far_chat = _chat()
    service._finish_exchange(far_state, far_chat, {
        "messages": [],
        "topic": {"action": "start", "kind": "not-a-kind", "title": "太远"},
    })
    far_topic = active_topic(far_chat)
    candidates = [kind.id for kind in topic_candidates(
        far_chat, chat_coord(far_state, far_chat))]
    _check(problems, "非法/太远的 kind 不应落盘",
           far_topic is not None and far_topic.kind_id != "not-a-kind"
           and far_topic.kind_id in candidates,
           far_topic.kind_id if far_topic else None)
    return problems


def main() -> int:
    problems = (check_protocol() + check_prompt()
                + check_candidates_and_snapshot() + check_silence_and_limits()
                + check_finish_exchange())
    if problems:
        print("[check_chat_topic_selection] FAILED")
        for item in problems:
            print("  - " + item)
        return 1
    print("[check_chat_topic_selection] PASSED："
          "话题类型选择、提示词与坐标快照符合预期")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
