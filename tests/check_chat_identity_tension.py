"""聊天关系认知与紧张度守卫。

覆盖设计文档 §11.4 的离线部分：
- 全局 world_stance 设置与 provider 回退；
- 紧张度对世界态势、关系认知与近期事件的响应；
- 从演化表提取事件时排除纯衰减行与窗口外记录；
- Relationship 随线程落盘、旧档回退陌生人；
- 身份更新只在 natural/switch 生效，走白名单转移图并受冷却约束；
- _finish_exchange 在话题结束时落地 identity_update。

不锁具体权重：断言只依赖单调性与模块常量。

用法：``python tests/check_chat_identity_tension.py``
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.models import (  # noqa: E402
    CharacterSnapshot, ChatState, EvolutionRecord, Relationship,
)
from persistence.character_repo import CharacterRepo  # noqa: E402
from persistence.chat_repo import ChatRepo  # noqa: E402
from persistence.settings_repo import SettingsRepo  # noqa: E402
from services.chat import (  # noqa: E402
    ChatService, IDENTITY_TRANSITIONS, IDENTITY_UPDATE_COOLDOWN_HOURS,
    apply_identity_update, apply_topic_decision, chat_coord,
    identity_update_allowed, parse_chat_response, topic_candidates,
)
from services.chat.coordinates import (  # noqa: E402
    DEFAULT_WORLD_STANCE, WORLD_STANCE_VALUES,
    tension_events_from_endings, tension_events_from_evolution,
)

NOW = datetime.datetime(2026, 10, 27, 12, 0, 0)


def _check(problems, name, ok, extra=""):
    if not ok:
        problems.append(name + (f" <{extra}>" if extra else ""))


def _state() -> CharacterSnapshot:
    state = CharacterSnapshot(giantess_id="g1", name="测试", action_points=100)
    state.record_change(intrusion=2.5, destruction=2.5)
    return state


def _chat(kind: str = "stranger") -> ChatState:
    chat = ChatState(giantess_id="g1", attitude=0)
    chat.relationship.kind = kind
    return chat


def _event_record(changed_at, source="report", intrusion=2.5,
                  destruction=2.5, casualties=0.0, step=0.0):
    return EvolutionRecord(
        changed_at=changed_at, step=step, intrusion=intrusion,
        destruction=destruction, casualties=casualties, source=source)


def check_world_stance() -> list:
    problems = []
    defaults = SettingsRepo(data_dir=tempfile.mkdtemp())._defaults
    _check(problems, "缺省 world_stance 应是合法语义值",
           defaults.get("world_stance") in WORLD_STANCE_VALUES,
           defaults.get("world_stance"))

    root = tempfile.mkdtemp(prefix="chat_stance_")
    os.makedirs(os.path.join(root, "user"), exist_ok=True)
    with open(os.path.join(root, "user", "settings.json"), "w",
              encoding="utf-8") as handle:
        json.dump({"world_stance": "hostile"}, handle)
    service = ChatService(data_dir=root,
                          character_repo=CharacterRepo(root),
                          chat_repo=ChatRepo(root))
    _check(problems, "ChatService 应读取设置里的世界态势",
           service._world_stance() == "hostile", service._world_stance())

    injected = ChatService(data_dir=root, world_stance_provider=lambda: "worship")
    _check(problems, "provider 应优先于设置",
           injected._world_stance() == "worship")

    broken = ChatService(data_dir=root,
                         world_stance_provider=lambda: 1 / 0)
    _check(problems, "provider 失败应回退 coexist",
           broken._world_stance() == DEFAULT_WORLD_STANCE)
    return problems


def check_tension() -> list:
    problems = []
    state = _state()
    coexist = chat_coord(state, _chat(), now=NOW, world_stance="coexist")
    hostile = chat_coord(state, _chat(), now=NOW, world_stance="hostile")
    _check(problems, "世界越敌对紧张度越高",
           hostile.tension > coexist.tension)

    stranger = chat_coord(state, _chat("stranger"), now=NOW,
                          world_stance="coexist")
    lover = chat_coord(state, _chat("lover"), now=NOW,
                       world_stance="coexist")
    _check(problems, "亲近关系应降低紧张度",
           lover.tension < stranger.tension)

    pressured_state = _state()
    pressured_state.record_change(intrusion=3.5, destruction=2.5,
                                  casualties=2.0, source="report")
    pressured = chat_coord(pressured_state, _chat(), world_stance="coexist")
    _check(problems, "近期事件压力应抬升紧张度",
           pressured.tension > stranger.tension,
           (pressured.tension, stranger.tension))
    return problems


def check_evolution_events() -> list:
    problems = []
    now = NOW
    long_ago = (now - datetime.timedelta(days=30)).isoformat()
    old = (now - datetime.timedelta(days=10)).isoformat()
    recent = (now - datetime.timedelta(hours=1)).isoformat()
    records = [
        _event_record(long_ago, intrusion=2.0, destruction=2.0),
        # 窗口外的伤亡与位移：权重算得出来，但不应进入事件列表
        _event_record(old, intrusion=2.5, destruction=2.5, casualties=1.0),
        _event_record(recent, intrusion=2.6, destruction=2.5),
        # 纯步长衰减行：即便坐标有变化也不计入
        _event_record(recent, intrusion=2.6, destruction=2.5,
                      source="apply_step_decay"),
        _event_record(recent, intrusion=2.6, destruction=2.5,
                      casualties=2.0),
    ]
    events = tension_events_from_evolution(records, now=now)
    _check(problems, "只保留窗口内且非衰减的事件", len(events) == 2,
           len(events))
    _check(problems, "第一行没有前值不应产生事件",
           tension_events_from_evolution(records[:1], now=now) == [])
    _check(problems, "伤亡增量的权重应高于坐标微移",
           events and events[-1].weight > events[0].weight,
           [event.weight for event in events])

    decay_only = [
        _event_record(recent, intrusion=2.5, destruction=2.5),
        _event_record(recent, intrusion=2.6, destruction=2.6,
                      source="apply_step_decay"),
    ]
    _check(problems, "只有衰减行时应没有事件",
           tension_events_from_evolution(decay_only, now=now) == [])

    report = tension_events_from_evolution(
        [_event_record(recent, intrusion=2.5, destruction=2.5),
         _event_record(recent, intrusion=2.6, destruction=2.5)], now=now)
    chat = tension_events_from_evolution(
        [_event_record(recent, intrusion=2.5, destruction=2.5),
         _event_record(recent, source="chat", intrusion=2.6,
                       destruction=2.5)], now=now)
    _check(problems, "旧聊天来源的世界事实应低权重计入",
           report and chat and chat[0].weight < report[0].weight,
           (report, chat))

    endings = [
        {"name": "旧结局", "achieved_at": old},
        {"name": "新结局", "achieved_at": recent},
        {"name": "坏时间", "achieved_at": "not-a-time"},
    ]
    ending_events = tension_events_from_endings(endings, now=now)
    _check(problems, "结局应进入事件压力且过滤窗口外/坏时间",
           len(ending_events) == 1
           and ending_events[0].weight > 0.0, ending_events)
    return problems


def check_relationship_storage() -> list:
    problems = []
    old = ChatState.from_dict({"giantess_id": "g1"})
    _check(problems, "旧线程应默认陌生人",
           old.relationship.kind == "stranger"
           and old.relationship.note == "")

    root = tempfile.mkdtemp(prefix="chat_relationship_")
    repo = ChatRepo(root)
    chat = repo.load("g1")
    chat.relationship = Relationship(kind="friend", note="聊得来")
    repo.save(chat)
    loaded = repo.load("g1")
    _check(problems, "关系认知应随线程落盘",
           loaded.relationship.kind == "friend"
           and loaded.relationship.note == "聊得来")

    bad = ChatState.from_dict({
        "giantess_id": "g1", "relationship": {"kind": "not-a-kind"}})
    _check(problems, "非法关系 kind 应回退陌生人",
           bad.relationship.kind == "stranger")
    return problems


def check_identity_update() -> list:
    problems = []
    chat = _chat("stranger")
    _check(problems, "非话题结束不应更新",
           not apply_identity_update(chat, {"kind": "acquaintance"}, "",
                                     now=NOW))
    _check(problems, "stale 强制收束不应更新",
           not apply_identity_update(chat, {"kind": "acquaintance"}, "stale",
                                     now=NOW))
    _check(problems, "跨级跳转应整条丢弃",
           not apply_identity_update(
               chat, {"kind": "lover", "note": "跳过"}, "natural", now=NOW)
           and chat.relationship.note == "")
    _check(problems, "合法转移应生效",
           apply_identity_update(chat, {"kind": "acquaintance"}, "natural",
                                 now=NOW)
           and chat.relationship.kind == "acquaintance")
    _check(problems, "更新后应进入冷却",
           not identity_update_allowed(chat, now=NOW)
           and not apply_identity_update(
               chat, {"note": "立刻再改"}, "natural", now=NOW))

    later = NOW + datetime.timedelta(hours=IDENTITY_UPDATE_COOLDOWN_HOURS + 1)
    _check(problems, "冷却结束后的 note-only 更新应生效",
           apply_identity_update(chat, {"note": "后来更熟"}, "switched",
                                 now=later)
           and chat.relationship.note == "后来更熟")

    friend = _chat("friend")
    _check(problems, "非法 kind 应连同 note 一起丢弃",
           not apply_identity_update(
               friend, {"kind": "nonsense", "note": "坏"}, "natural", now=NOW)
           and friend.relationship.kind == "friend"
           and friend.relationship.note == "")

    targets = set()
    for choices in IDENTITY_TRANSITIONS.values():
        targets |= set(choices)
    _check(problems, "转移图不应允许陌生人直达恋人/敌人",
           "lover" not in IDENTITY_TRANSITIONS["stranger"]
           and "enemy" not in IDENTITY_TRANSITIONS["stranger"])
    _check(problems, "转移图应覆盖关系枚举",
           targets >= {"acquaintance", "friend", "close", "lover", "family",
                       "worshipper", "subject", "rival", "enemy", "prey"})
    return problems


def check_parse_identity() -> list:
    problems = []
    parsed = parse_chat_response(json.dumps({
        "messages": ["晚安"],
        "topic": {"action": "end"},
        "identity_update": {"kind": "Friend", "note": " 聊得来 "},
    }, ensure_ascii=False))
    update = parsed.get("identity_update") or {}
    _check(problems, "identity_update 应解析并归一 kind",
           update.get("kind") == "friend" and update.get("note") == "聊得来")
    empty = parse_chat_response(json.dumps({
        "messages": [], "identity_update": {"kind": "", "note": ""}}))
    _check(problems, "空 identity_update 不应进入结果",
           empty.get("identity_update") == {})
    return problems


def check_finish_exchange() -> list:
    problems = []
    root = tempfile.mkdtemp(prefix="chat_identity_finish_")
    repo = ChatRepo(root)
    service = ChatService(data_dir=root,
                          character_repo=CharacterRepo(root),
                          chat_repo=repo,
                          world_stance_provider=lambda: "coexist")
    service._reenqueue = lambda *args, **kwargs: None

    state = _state()
    chat = ChatState(giantess_id="g1")
    coord = chat_coord(state, chat)
    apply_topic_decision(chat, {"action": "start", "kind": "daily",
                                "title": "近况"}, had_reply=True,
                         coord=coord, candidates=topic_candidates(chat, coord))
    service._finish_exchange(state, chat, {
        "messages": ["晚安"],
        "topic": {"action": "end"},
        "identity_update": {"kind": "acquaintance", "note": "多聊了几句"},
    })
    saved = repo.load("g1")
    _check(problems, "话题自然结束应落盘身份更新",
           saved.relationship.kind == "acquaintance"
           and saved.relationship.note == "多聊了几句",
           saved.relationship)

    chat2 = ChatState(giantess_id="g1")
    coord2 = chat_coord(state, chat2)
    apply_topic_decision(chat2, {"action": "start", "kind": "daily",
                                 "title": "近况"}, had_reply=True,
                         coord=coord2,
                         candidates=topic_candidates(chat2, coord2))
    service._finish_exchange(state, chat2, {
        "messages": ["嗯"],
        "topic": {"action": "continue"},
        "identity_update": {"kind": "friend"},
    })
    _check(problems, "未结束话题时 identity_update 应被忽略",
           chat2.relationship.kind == "stranger"
           and not chat2.relationship.updated_at)
    return problems


def main() -> int:
    problems = (check_world_stance() + check_tension()
                + check_evolution_events() + check_relationship_storage()
                + check_identity_update() + check_parse_identity()
                + check_finish_exchange())
    if problems:
        print("[check_chat_identity_tension] FAILED")
        for item in problems:
            print("  - " + item)
        return 1
    print("[check_chat_identity_tension] PASSED："
          "世界态势、紧张度与身份更新符合预期")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
