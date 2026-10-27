"""聊天世界效果：AI 只表达意图，服务层校验、执行并留审计日志。

设计约束（见设计文档 §7）：

- 每轮最多一个 effect；未知工具或非法参数一律丢弃，不重试；
- 不提供直接改 tension / size_state 的工具，它们只能由世界事实派生；
- 每次执行（含失败）都追加一条 :class:`ChatEffect`，保留最近
  ``APPLIED_EFFECT_LIMIT`` 条；
- v1 只开放 ``self_view`` 与 ``step_nudge``；``move`` / ``casualty`` 后置。

本模块不读写存档；调用方负责在效果后保存 chat 与 character。
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Optional

from core.models import (APPLIED_EFFECT_LIMIT, CharacterSnapshot, ChatEffect,
                         ChatState)
from services.state_service import StateService

# self_view 单条追加长度上限
SELF_VIEW_CHAR_LIMIT = 500
# 单次步长微调幅度
STEP_NUDGE_AMOUNT = 0.02
# 提示词里每个工具的一句话说明（只暴露可用工具，不给内部常量）
EFFECT_TOOL_HINTS = {
    "self_view": (
        '{"text": "..."}：补充一段你愿意公开的自我描述；追加在已有介绍后，'
        f"不改写旧内容，单条不超过 {SELF_VIEW_CHAR_LIMIT} 字"
    ),
    "step_nudge": (
        '{"axis": "intrusion|destruction", "direction": -1|0|1}：'
        "轻微调整你接下来的行为步长（幅度很小，接近极限时会衰减）"
    ),
}


@dataclass(frozen=True)
class EffectResult:
    """单个 handler 的执行结果。"""
    ok: bool
    detail: str = ""
    character_changed: bool = False


def _fail(detail: str) -> EffectResult:
    return EffectResult(False, detail)


def _apply_self_view(state: CharacterSnapshot, args: dict) -> EffectResult:
    text = args.get("text")
    if not isinstance(text, str) or not text.strip():
        return _fail("self_view 需要非空 text")
    text = text.strip()[:SELF_VIEW_CHAR_LIMIT]
    current = state.intro_visible or ""
    if text in current:
        return EffectResult(True, "内容已存在，未重复追加")
    if current.strip():
        state.intro_visible = current.rstrip() + "\n\n" + text
    else:
        state.intro_visible = text
    return EffectResult(True, "已追加到公开自我介绍", True)


def _apply_step_nudge(state: CharacterSnapshot, args: dict) -> EffectResult:
    axis = args.get("axis")
    direction = args.get("direction")
    if axis not in ("intrusion", "destruction"):
        return _fail("step_nudge.axis 必须是 intrusion/destruction")
    try:
        direction = int(direction)
    except (TypeError, ValueError):
        return _fail("step_nudge.direction 必须是 -1/0/1")
    if direction not in (-1, 0, 1):
        return _fail("step_nudge.direction 必须是 -1/0/1")
    changed = StateService.nudge_step_rates(
        state, axis, direction, amount=STEP_NUDGE_AMOUNT)
    if changed is None:
        return EffectResult(True, "步长未变化")
    old, new = changed
    return EffectResult(
        True, f"{axis} 步长 {old:.3f} → {new:.3f}", True)


EFFECT_HANDLERS: dict[str, Callable[[CharacterSnapshot, dict],
                                     EffectResult]] = {
    "self_view": _apply_self_view,
    "step_nudge": _apply_step_nudge,
}


def effect_tools_text() -> str:
    """给系统提示词用的工具清单；按注册表顺序生成。"""
    lines = []
    for tool in EFFECT_HANDLERS:
        hint = EFFECT_TOOL_HINTS.get(tool, "")
        lines.append(f"- {tool} {hint}".rstrip())
    return "\n".join(lines)


def run_effect(state: CharacterSnapshot, chat_state: ChatState,
               effect: Any,
               now: Optional[datetime.datetime] = None
               ) -> tuple[Optional[ChatEffect], bool]:
    """执行至多一个世界效果，返回 ``(日志, 是否改了主档案)``。

    没有 effect 时返回 ``(None, False)``；非法/未知工具只记一条失败日志。
    """
    if not isinstance(effect, dict) or not effect:
        return None, False
    moment = now or datetime.datetime.now()
    applied_at = moment.isoformat()
    tool = effect.get("tool")
    tool = tool.strip().lower()[:32] if isinstance(tool, str) else ""
    args = effect.get("args")
    if not isinstance(args, dict):
        args = {}
    if not tool:
        record = ChatEffect(tool="", args=args, applied_at=applied_at,
                            result="缺少 effect.tool")
        _append(chat_state, record)
        return record, False
    handler = EFFECT_HANDLERS.get(tool)
    if handler is None:
        record = ChatEffect(tool=tool, args=args, applied_at=applied_at,
                            result="未知工具，已忽略")
        _append(chat_state, record)
        return record, False
    try:
        result = handler(state, args)
    except Exception as exc:  # 效果失败不影响已生成的回复
        result = _fail(f"执行失败：{type(exc).__name__}: {exc}")
    record = ChatEffect(id=uuid.uuid4().hex, tool=tool, args=dict(args),
                        applied_at=applied_at,
                        result=result.detail or ("成功" if result.ok else "失败"))
    _append(chat_state, record)
    return record, bool(result.ok and result.character_changed)


def _append(chat_state: ChatState, record: ChatEffect):
    chat_state.applied_effects = (
        list(chat_state.applied_effects or []) + [record]
    )[-APPLIED_EFFECT_LIMIT:]
