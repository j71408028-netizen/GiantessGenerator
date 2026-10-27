"""聊天世界效果层守卫。

覆盖设计文档 §11.5 的离线部分：
- effect 协议解析：单个对象有效，数组/缺 tool 丢弃；
- self_view 追加而非覆盖公开介绍，单条截断，隐藏介绍不动；
- step_nudge 走 StateService，单次幅度小且接近上限时衰减；
- 未知工具/非法参数只记日志，不改世界；
- applied_effects 随线程落盘并保留最近 50 条；
- _finish_exchange 的落地顺序：ops 不再写世界，effect 在话题前生效；
- 沉默轮同样应用 effect。

不锁具体提示词全文，只验证工具清单与旧世界 ops 已移除。

用法：``python tests/check_chat_effects.py``
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.models import (  # noqa: E402
    APPLIED_EFFECT_LIMIT, CharacterSnapshot, ChatState,
)
from persistence.character_repo import CharacterRepo  # noqa: E402
from persistence.chat_repo import ChatRepo  # noqa: E402
from services.chat import (  # noqa: E402
    ChatService, apply_chat_ops, build_chat_system_prompt,
    parse_chat_response,
)
from services.chat.effects import (  # noqa: E402
    SELF_VIEW_CHAR_LIMIT, STEP_NUDGE_AMOUNT, effect_tools_text, run_effect,
)


def _check(problems, name, ok, extra=""):
    if not ok:
        problems.append(name + (f" <{extra}>" if extra else ""))


def _state() -> CharacterSnapshot:
    state = CharacterSnapshot(giantess_id="g1", name="测试", action_points=100,
                              intro_visible="原本的公开介绍",
                              intro_hidden="不该被改的隐藏介绍")
    state.record_change(intrusion=2.5, destruction=2.5)
    state.step_intrusion = 1.0
    state.step_destruction = 1.0
    return state


def check_parse_effect() -> list:
    problems = []
    parsed = parse_chat_response(json.dumps({
        "messages": ["嗯"],
        "effect": {"tool": " Self_View ", "args": {"text": "喜欢雨"}},
    }, ensure_ascii=False))
    effect = parsed.get("effect") or {}
    _check(problems, "单个 effect 应解析并归一工具名",
           effect.get("tool") == "self_view"
           and effect.get("args", {}).get("text") == "喜欢雨", effect)

    multiple = parse_chat_response(json.dumps({
        "messages": ["嗯"],
        "effect": [{"tool": "self_view", "args": {"text": "a"}},
                   {"tool": "step_nudge", "args": {}}],
    }))
    _check(problems, "多个 effect 应整条丢弃", multiple.get("effect") == {},
           multiple.get("effect"))

    missing = parse_chat_response(json.dumps({
        "messages": ["嗯"], "effect": {"args": {"text": "x"}}}))
    _check(problems, "缺 tool 的 effect 不应进入结果",
           missing.get("effect") == {})

    bad_args = parse_chat_response(json.dumps({
        "messages": ["嗯"],
        "effect": {"tool": "self_view", "args": "not-a-dict"}}))
    _check(problems, "非对象 args 应回退空对象",
           bad_args.get("effect", {}).get("args") == {},
           bad_args.get("effect"))
    return problems


def check_self_view() -> list:
    problems = []
    state = _state()
    chat = ChatState(giantess_id="g1")
    record, changed = run_effect(
        state, chat, {"tool": "self_view", "args": {"text": "我喜欢雨。"}})
    _check(problems, "self_view 应成功并标记主档案变化",
           record is not None and changed, record)
    _check(problems, "应保留原公开介绍并追加新内容",
           state.intro_visible.startswith("原本的公开介绍")
           and "我喜欢雨。" in state.intro_visible,
           state.intro_visible)
    _check(problems, "不应改写隐藏介绍",
           state.intro_hidden == "不该被改的隐藏介绍")

    before = state.intro_visible
    run_effect(state, chat,
               {"tool": "self_view", "args": {"text": "我喜欢雨。"}})
    _check(problems, "重复内容不应重复追加", state.intro_visible == before)

    long_text = "长" * (SELF_VIEW_CHAR_LIMIT + 80)
    run_effect(state, chat, {"tool": "self_view",
                             "args": {"text": long_text}})
    _check(problems, "单条追加应截断到上限",
           state.intro_visible.count("长") == SELF_VIEW_CHAR_LIMIT,
           state.intro_visible.count("长"))

    failure, changed = run_effect(
        state, chat, {"tool": "self_view", "args": {"text": "  "}})
    _check(problems, "空文本应失败且不改主档案",
           failure is not None and not changed
           and "需要非空" in failure.result, failure)
    return problems


def check_step_nudge() -> list:
    problems = []
    state = _state()
    chat = ChatState(giantess_id="g1")
    record, changed = run_effect(
        state, chat,
        {"tool": "step_nudge",
         "args": {"axis": "intrusion", "direction": 1}})
    _check(problems, "step_nudge 应小幅增加步长",
           record is not None and changed
           and state.step_intrusion > 1.0
           and state.step_intrusion - 1.0 < STEP_NUDGE_AMOUNT,
           state.step_intrusion)

    state.step_intrusion = 4.99
    old = state.step_intrusion
    run_effect(state, chat,
               {"tool": "step_nudge",
                "args": {"axis": "intrusion", "direction": 1}})
    _check(problems, "接近上限时应衰减",
           0.0 < state.step_intrusion - old < STEP_NUDGE_AMOUNT * 0.1,
           state.step_intrusion - old)

    state.step_destruction = 2.0
    record, changed = run_effect(
        state, chat,
        {"tool": "step_nudge",
         "args": {"axis": "destruction", "direction": 0}})
    _check(problems, "direction=0 应是合法空操作",
           record is not None and not changed
           and state.step_destruction == 2.0)

    before = (state.step_intrusion, state.step_destruction)
    record, changed = run_effect(
        state, chat,
        {"tool": "step_nudge",
         "args": {"axis": "gravity", "direction": 1}})
    _check(problems, "非法轴应失败且不改步长",
           record is not None and not changed
           and (state.step_intrusion, state.step_destruction) == before,
           record)
    return problems


def check_log_and_persistence() -> list:
    problems = []
    state = _state()
    chat = ChatState(giantess_id="g1")
    record, changed = run_effect(
        state, chat, {"tool": "move", "args": {"target_id": "x"}})
    _check(problems, "未知工具应记失败日志且不改世界",
           record is not None and not changed
           and "未知工具" in record.result, record)

    for index in range(APPLIED_EFFECT_LIMIT + 5):
        run_effect(state, chat,
                   {"tool": "self_view",
                    "args": {"text": f"第{index}条自我描述"}})
    _check(problems, "效果日志应只保留最近上限",
           len(chat.applied_effects) == APPLIED_EFFECT_LIMIT,
           len(chat.applied_effects))

    root = tempfile.mkdtemp(prefix="chat_effects_")
    repo = ChatRepo(root)
    thread = repo.load("g1")
    run_effect(state, thread,
               {"tool": "self_view", "args": {"text": "落盘用"}})
    repo.save(thread)
    loaded = repo.load("g1")
    _check(problems, "效果日志应随线程落盘",
           loaded.applied_effects
           and loaded.applied_effects[-1].tool == "self_view"
           and "落盘用" in loaded.applied_effects[-1].args.get("text", ""),
           loaded.applied_effects[-1] if loaded.applied_effects else None)
    return problems


def check_prompt() -> list:
    problems = []
    state = _state()
    chat = ChatState(giantess_id="g1")
    prompt = build_chat_system_prompt(state, chat)
    _check(problems, "提示词应列出可用世界效果",
           "self_view" in prompt and "step_nudge" in prompt)
    _check(problems, "提示词应说明 effect 协议",
           '"effect"' in prompt and "每轮最多一个" in prompt)
    _check(problems, "旧 ops 的世界字段应已移除",
           "casualties" not in prompt
           and "仅当这次聊天内容涉及你造成人员伤亡" not in prompt)
    _check(problems, "工具清单只暴露已注册工具",
           effect_tools_text().count("- ") == 2, effect_tools_text())
    return problems


def check_finish_exchange() -> list:
    problems = []
    root = tempfile.mkdtemp(prefix="chat_effects_finish_")
    repo = ChatRepo(root)
    characters = CharacterRepo(root)
    service = ChatService(data_dir=root, character_repo=characters,
                          chat_repo=repo,
                          world_stance_provider=lambda: "coexist")
    service._reenqueue = lambda *args, **kwargs: None

    state = _state()
    chat = ChatState(giantess_id="g1")
    parsed = {
        "messages": ["嗯，今天下雨了"],
        "topic": {"action": "start", "kind": "daily", "title": "天气"},
        "ops": {"attitude": 1, "intrusion": 1},
        "effect": {"tool": "self_view", "args": {"text": "我喜欢雨天。"}},
    }
    result = service._finish_exchange(state, chat, parsed)
    _check(problems, "旧 ops 的 intrusion 应被忽略",
           state.intrusion == 2.5, state.intrusion)
    _check(problems, "self_view 应写入主档案",
           "我喜欢雨天。" in state.intro_visible)
    _check(problems, "结果应带效果摘要",
           result["effect_applied"]
           and result["effect_applied"]["tool"] == "self_view",
           result["effect_applied"])

    saved_chat = repo.load("g1")
    _check(problems, "效果日志应写入聊天存档",
           saved_chat.applied_effects
           and saved_chat.applied_effects[-1].tool == "self_view")
    saved_state = characters.load("g1")
    _check(problems, "效果改动应写入角色档案",
           saved_state is not None
           and "我喜欢雨天。" in (saved_state.intro_visible or ""))

    silent_state = _state()
    silent_chat = ChatState(giantess_id="g1")
    old_step = silent_state.step_destruction
    service._finish_exchange(silent_state, silent_chat, {
        "messages": [],
        "silent_reason": "不想回",
        "topic": {"action": "continue"},
        "effect": {"tool": "step_nudge",
                   "args": {"axis": "destruction", "direction": 1}},
    })
    _check(problems, "沉默轮也应应用 effect",
           silent_state.step_destruction > old_step,
           silent_state.step_destruction)
    _check(problems, "沉默轮不应排队消息",
           not silent_chat.messages)

    fail_state = _state()
    fail_chat = ChatState(giantess_id="g1")
    service._finish_exchange(fail_state, fail_chat, {
        "messages": ["好"],
        "effect": {"tool": "not_a_tool", "args": {}},
    })
    _check(problems, "失败效果也应留下日志",
           fail_chat.applied_effects
           and "未知工具" in fail_chat.applied_effects[-1].result)
    _check(problems, "失败效果不应改主档案",
           fail_state.intro_visible == "原本的公开介绍"
           and fail_state.step_intrusion == 1.0)
    return problems


def check_apply_ops_only_chat_domain() -> list:
    problems = []
    state = _state()
    chat = ChatState(giantess_id="g1")
    before = list(state.evolution)
    applied = apply_chat_ops(state, chat, {
        "ops": {"attitude": 1, "intrusion": 1, "destruction": -1,
                "casualties": 1},
    })
    _check(problems, "apply_chat_ops 只应处理聊天域字段",
           set(applied) <= {"attitude", "action_points", "user_nick"},
           applied)
    _check(problems, "旧世界 ops 不应改坐标或演化表",
           state.intrusion == 2.5 and state.destruction == 2.5
           and state.total_casualties == 0.0
           and len(state.evolution) == len(before))
    return problems


def main() -> int:
    problems = (check_parse_effect() + check_self_view() + check_step_nudge()
                + check_log_and_persistence() + check_prompt()
                + check_finish_exchange() + check_apply_ops_only_chat_domain())
    if problems:
        print("[check_chat_effects] FAILED")
        for item in problems:
            print("  - " + item)
        return 1
    print("[check_chat_effects] PASSED：效果解析、执行、日志与落地顺序符合预期")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
