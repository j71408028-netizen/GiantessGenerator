# -*- coding: utf-8 -*-
"""聊天域行为包钩子守卫。

覆盖两批开放面：
- 第一批：提示词内容（framing / world_context / topic_lens /
  topic_candidates / topic_context / relationship_text / habits_text）；
- 第二批：节奏与坐标（coord / should_proactive / schedule_batch /
  chat.model.* 常量）。

只读注册表与纯函数，不建窗口、不联网、不碰 data/。

用法：``python tests/check_chat_hooks.py``
"""

import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.behavior_runtime import (  # noqa: E402
    BEHAVIOR_API_VERSION, get_runtime, reset_runtime,
)
from core.models import CharacterSnapshot, ChatMessage, ChatState  # noqa: E402
from services.chat import (  # noqa: E402
    WORLD_CONTEXT_LIMIT, _safe_schedule_batch, build_chat_system_prompt,
    chat_coord, read_delay_seconds, should_proactive, should_reconcile,
    typing_delay_seconds,
)
from services.chat.coordinates import (  # noqa: E402
    ChatCoord, describe_affection, describe_size_state, describe_tension,
)
from services.chat.topic_kinds import TopicKind  # noqa: E402


def _check(problems, name, ok, extra=""):
    if not ok:
        problems.append(name + (f" <{extra}>" if extra else ""))


def _state() -> CharacterSnapshot:
    state = CharacterSnapshot(giantess_id="g1", name="测试", action_points=100)
    state.record_change(intrusion=2.5, destruction=2.5)
    return state


def _chat() -> ChatState:
    return ChatState(giantess_id="g1", attitude=0)


def _prompt(state=None, chat=None) -> str:
    return build_chat_system_prompt(state or _state(), chat or _chat())


def _override(key, impl) -> None:
    """测试内替换同一 key；先摘掉旧覆盖，避免重复注册告警刷屏。"""
    runtime = get_runtime()
    runtime._overrides.pop(key, None)
    runtime.override(key, impl)


def check_defaults() -> list:
    problems = []
    prompt = _prompt()
    _check(problems, "默认提示词应保留手机聊天开场",
           "你正在手机聊天软件里" in prompt)
    _check(problems, "默认提示词不应出现世界背景块",
           "【世界背景】" not in prompt)
    _check(problems, "默认提示词仍应包含身份层",
           "【你是谁】" in prompt and "测试" in prompt)
    _check(problems, "默认提示词仍应包含输出协议",
           "你必须严格按照以下 JSON 格式输出" in prompt)
    _check(problems, "行为包 API 版本保持 2",
           BEHAVIOR_API_VERSION == 2)
    _check(problems, "默认状态无需主动搭话",
           should_proactive(_chat()) is False)
    return problems


def check_framing_and_world() -> list:
    problems = []
    try:
        _override("chat.framing",
                  lambda state, chat_state: "你们正在用传讯术交谈。")
        _override(
            "chat.world_context",
            lambda state, chat_state, world_stance="coexist": "浮空城悬在云海之上。")
        prompt = _prompt()
        _check(problems, "chat.framing 覆盖应替换开场",
               "你们正在用传讯术交谈。" in prompt
               and "你正在手机聊天软件里" not in prompt)
        _check(problems, "chat.world_context 覆盖应注入世界背景块",
               "【世界背景】" in prompt and "浮空城悬在云海之上。" in prompt)
        _check(problems, "世界背景应位于身份层之后",
               prompt.index("【你是谁】") < prompt.index("【世界背景】"))
        _check(problems, "输出协议不应被世界背景替换",
               "你必须严格按照以下 JSON 格式输出" in prompt)

        _override(
            "chat.world_context",
            lambda state, chat_state, world_stance="coexist":
            "浮" * (WORLD_CONTEXT_LIMIT + 80))
        prompt = _prompt()
        _check(problems, "世界背景应按上限截断",
               prompt.count("浮") == WORLD_CONTEXT_LIMIT,
               prompt.count("浮"))

        _override("chat.world_context",
                  lambda state, chat_state, world_stance="coexist": 123)
        prompt = _prompt()
        _check(problems, "世界背景返回非字符串不应炸",
               "【世界背景】" not in prompt)

        _override("chat.framing", lambda state, chat_state: None)
        prompt = _prompt()
        _check(problems, "开场返回非字符串应回退默认",
               "你正在手机聊天软件里" in prompt)
    finally:
        reset_runtime()
    return problems


def check_prompt_hooks() -> list:
    problems = []
    try:
        _override("chat.topic_lens",
                  lambda ctx: ("【自定义视角】\n只看星象。", None))
        prompt = _prompt()
        _check(problems, "chat.topic_lens 覆盖应替换视角段",
               "【自定义视角】" in prompt and "只看星象。" in prompt)

        _override("chat.topic_lens", lambda ctx: "bad-return")
        prompt = _prompt()
        _check(problems, "视角返回类型不符应回退默认",
               "bad-return" not in prompt
               and "【此刻你注意到的事】" in prompt)

        kind = TopicKind(id="world_only", label="界外",
                         coord=ChatCoord(0.5, 0.5, 0.5))
        _override(
            "chat.topic_candidates",
            lambda chat_state, coord, limit=6: [kind])
        prompt = _prompt()
        _check(problems, "chat.topic_candidates 覆盖应进入提示词",
               "world_only" in prompt and "界外" in prompt)

        _override(
            "chat.topic_candidates",
            lambda chat_state, coord, limit=6: [object()])
        prompt = _prompt()
        _check(problems, "候选中的坏元素应被过滤",
               "此刻没有合适的候选类型" in prompt)

        _override(
            "chat.topic_candidates",
            lambda chat_state, coord, limit=6: [])
        prompt = _prompt()
        _check(problems, "候选为空应走无候选分支",
               "此刻没有合适的候选类型" in prompt)

        _override(
            "chat.topic_context",
            lambda chat_state, coord=None, candidates=None: "自定义话题段")
        _override("chat.relationship_text",
                  lambda chat_state: "自定义关系段")
        _override("chat.habits_text",
                  lambda chat_params=None: "自定义聊天习惯")
        prompt = _prompt()
        _check(problems, "chat.topic_context 覆盖应进入提示词",
               "自定义话题段" in prompt)
        _check(problems, "chat.relationship_text 覆盖应进入提示词",
               "自定义关系段" in prompt)
        _check(problems, "chat.habits_text 覆盖应进入提示词",
               "自定义聊天习惯" in prompt)
    finally:
        reset_runtime()
    return problems


def check_coord_and_proactive() -> list:
    problems = []
    try:
        _override("chat.coord",
                  lambda state, chat_state, now=None,
                  world_stance="coexist": [0.0, 0.0, 1.0])
        coord = ChatCoord.from_values(chat_coord(_state(), _chat()))
        _check(problems, "chat.coord 覆盖应直接生效",
               isinstance(coord, ChatCoord)
               and coord.affection == 0.0 and coord.size_state == 1.0)
        prompt = _prompt()
        _check(problems, "覆盖坐标应改变提示词里的定性描述",
               describe_affection(0.0) in prompt
               and describe_tension(0.0) in prompt
               and describe_size_state(1.0) in prompt)

        _override("chat.coord", lambda state, chat_state, now=None,
                  world_stance="coexist": [1.0])
        prompt = _prompt()
        _check(problems, "非法坐标应回退 ChatCoord 默认值而非崩溃",
               describe_affection(0.5) in prompt)

        _override("chat.should_proactive", lambda chat_state: True)
        _check(problems, "chat.should_proactive 覆盖为 True 应影响 reconcile",
               should_reconcile(_state(), _chat()) is True)
        _override("chat.should_proactive", lambda chat_state: False)
        _check(problems, "chat.should_proactive 覆盖为 False 应影响 reconcile",
               should_reconcile(_state(), _chat()) is False)
    finally:
        reset_runtime()
    return problems


def check_schedule_and_model_constants() -> list:
    problems = []
    try:
        fixed = ["2000-01-01T00:00:01", "2000-01-01T00:00:02"]
        _override("chat.schedule_batch",
                  lambda texts, online=1: fixed[:len(texts)])
        _check(problems, "chat.schedule_batch 覆盖应直接生效",
               _safe_schedule_batch(["a", "b"], 1) == fixed)

        _override("chat.schedule_batch",
                  lambda texts, online=1: fixed[:1])
        fallback = _safe_schedule_batch(["a", "b"], 1)
        _check(problems, "排程长度不符应回退默认实现",
               len(fallback) == 2 and fallback != fixed[:1])

        _override("chat.schedule_batch",
                  lambda texts, online=1: ["不是时间"] * len(texts))
        fallback = _safe_schedule_batch(["a"], 1)
        _check(problems, "排程元素非法应回退默认实现",
               len(fallback) == 1 and fallback != ["不是时间"])

        _override("chat.model.read_delay_range", (0.0, 0.0))
        _override("chat.model.online_read_scale", {0: 0.0, 1: 0.0, 2: 0.0})
        _check(problems, "读延迟常量覆盖应生效",
               read_delay_seconds(1) == 0.0)

        _override("chat.model.typing_delay_per_char", 0.0)
        _override("chat.model.typing_delay_range", (0.0, 0.0))
        _override("chat.model.online_typing_scale",
                  {0: 0.0, 1: 0.0, 2: 0.0})
        _check(problems, "打字延迟常量覆盖应生效",
               typing_delay_seconds("很长的一段回复", 1) == 0.0)

        _override("chat.model.boundary_proactive_gap",
                  {0: 0, 1: 0, 2: 0})
        old_chat = _chat()
        old_chat.messages.append(
            ChatMessage(role="user", text="在吗", read=True))
        old_chat.last_active_at = (
            datetime.datetime.now() - datetime.timedelta(minutes=1)).isoformat()
        _check(problems, "主动搭话门槛常量覆盖应生效",
               should_proactive(old_chat) is True)

        _override("chat.model.read_delay_range", lambda: "bad")
        value = read_delay_seconds(1)
        _check(problems, "常量返回类型不符应回退默认值",
               0.0 <= value <= 5.0, value)

        _override("chat.model.online_read_scale", {1: "bad"})
        value = read_delay_seconds(1)
        _check(problems, "常量表内坏值应安全回退",
               0.0 <= value <= 10.0, value)

        _override("chat.model.boundary_proactive_gap", {1: "bad"})
        _check(problems, "搭话门槛坏值应安全回退",
               should_proactive(old_chat) is False)
    finally:
        reset_runtime()
        _check(problems, "reset 后应恢复默认开场",
               "你正在手机聊天软件里" in _prompt())
        _check(problems, "reset 只清覆盖不清目录",
               get_runtime().resolve("chat.framing") is None
               and any(entry["key"] == "chat.framing"
                       for entry in get_runtime().catalog()))
    return problems


def main() -> int:
    problems = (check_defaults() + check_framing_and_world()
                + check_prompt_hooks() + check_coord_and_proactive()
                + check_schedule_and_model_constants())
    if problems:
        print("[check_chat_hooks] FAILED")
        for item in problems:
            print("  - " + item)
        return 1
    print("[check_chat_hooks] PASSED：聊天行为包钩子与兜底符合预期")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
