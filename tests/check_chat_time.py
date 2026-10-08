"""聊天时间标记守卫：间隔长短 → 补不补、补到什么精度。

背景：专业模式的角色聊天面板会按相邻消息的间隔自动补时间标记
（``ui/common/chat_time.py``）。这套"分级"是最容易被顺手改坏的地方——
把跨天的日期去掉、把 5 分钟门槛调没、或者误用实际投递时间（应用重启后
离线到期的回复会被结算成启动时刻，几天的间隔会被压成一瞬），界面都不会
报错，只会显示成一段没有时间概念的历史。

本脚本把这几条断言成回归：

1. **门槛**：间隔短于 :data:`TIME_MARKER_GAP` 不补；达到门槛必须补；
   这段历史的开头（没有上一条）必然补；
2. **精度分级**：今天 ``HH:MM`` / 昨天 ``昨天 HH:MM`` / 今年 ``M月D日 HH:MM``
   / 跨年 ``YYYY年M月D日 HH:MM``；未来时间（时钟偏差、计划时间未到）不退化成
   带日期的怪相；
3. **时间坐标**：角色消息优先计划展示时间 ``available_at``（而不是被延迟
   结算改写的 ``delivered_at``），玩家消息取发送时间 ``created_at``；缺字段、
   时间串损坏时逐级回退，全都没有时返回 None 而不是抛异常。

纯 ``datetime`` 断言，不需要显示器 / 窗口环境。

用法：``python tests/check_chat_time.py``
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.models import ChatMessage  # noqa: E402
from ui.common.chat_time import (  # noqa: E402
    TIME_MARKER_GAP, format_time_marker, message_moment, time_marker_text,
)

#: 断言用的"现在"：固定值，避免脚本跨零点时自己抖。
NOW = datetime.datetime(2026, 3, 5, 20, 0, 0)


def _at(year, month, day, hour=0, minute=0):
    return datetime.datetime(year, month, day, hour, minute)


def check_gap_threshold() -> list:
    """门槛：短间隔不补、达到门槛补、历史开头必补。"""
    problems = []
    previous = _at(2026, 3, 5, 14, 0)
    just_under = previous + TIME_MARKER_GAP - datetime.timedelta(seconds=1)
    at_threshold = previous + TIME_MARKER_GAP

    if time_marker_text(previous, just_under, NOW) != "":
        problems.append(f"间隔 {TIME_MARKER_GAP} 以内不该补标记，实际补了")
    if time_marker_text(previous, at_threshold, NOW) == "":
        problems.append(f"间隔达到 {TIME_MARKER_GAP} 必须补标记，实际没补")
    if time_marker_text(None, previous, NOW) == "":
        problems.append("历史的第一条消息必须带标记，实际没补")
    if time_marker_text(previous, None, NOW) != "":
        problems.append("当前消息取不到时间时不该补标记")
    return problems


def check_precision_levels() -> list:
    """精度分级：今天的只给时分，跨天补日期，跨年再补年份。"""
    problems = []
    cases = [
        (_at(2026, 3, 5, 14, 32), "14:32", "今天"),
        (_at(2026, 3, 4, 9, 5), "昨天 09:05", "昨天"),
        (_at(2026, 1, 2, 8, 0), "1月2日 08:00", "今年更早"),
        (_at(2024, 12, 31, 23, 59), "2024年12月31日 23:59", "跨年"),
    ]
    for moment, expected, label in cases:
        actual = format_time_marker(moment, NOW)
        if actual != expected:
            problems.append(f"{label}的标记应为 {expected!r}，实际 {actual!r}")

    # 未来时间（时钟偏差 / available_at 还没到）：按今天处理，不出现"负几天"
    future = _at(2026, 3, 5, 21, 30)
    if format_time_marker(future, NOW) != "21:30":
        problems.append(
            f"今天的未来时间标记应为 '21:30'，实际 {format_time_marker(future, NOW)!r}")
    if format_time_marker(None, NOW) != "":
        problems.append("时间坐标为 None 时应返回空串")
    return problems


def check_message_moment() -> list:
    """时间坐标：角色消息认计划时间，玩家消息认发送时间，坏数据逐级回退。"""
    problems = []
    planned = _at(2026, 3, 2, 10, 0).isoformat()
    delivered = _at(2026, 3, 5, 19, 0).isoformat()
    created = _at(2026, 3, 1, 10, 0).isoformat()

    # 离线到期的回复：投递时间被结算成"现在"，标记必须仍按计划时间算
    char_msg = ChatMessage(role="char", text="在", status="delivered",
                           created_at=created, available_at=planned,
                           delivered_at=delivered)
    if message_moment(char_msg) != _at(2026, 3, 2, 10, 0):
        problems.append(
            f"角色消息应优先取 available_at，实际 {message_moment(char_msg)!r}")

    # 没有 available_at（旧档 / 已投递过的）→ 实际投递时间
    older = ChatMessage(role="char", text="嗯", status="read",
                        created_at=created, delivered_at=delivered)
    if message_moment(older) != _at(2026, 3, 5, 19, 0):
        problems.append(
            f"角色消息缺 available_at 时应取 delivered_at，实际 {message_moment(older)!r}")

    # 玩家消息：created_at 是发送时刻，不让 delivered_at 抢先
    user_msg = ChatMessage(role="user", text="你好", created_at=created,
                           delivered_at=delivered)
    if message_moment(user_msg) != _at(2026, 3, 1, 10, 0):
        problems.append(
            f"玩家消息应取 created_at，实际 {message_moment(user_msg)!r}")

    for broken, label in ((ChatMessage(role="user", text="x", created_at="坏时间"),
                           "时间串损坏"),
                          (None, "消息为 None")):
        try:
            moment = message_moment(broken)
        except Exception as e:      # noqa: BLE001 - 守卫要把异常变成失败项
            problems.append(f"{label}时不该抛异常：{e!r}")
            continue
        if moment is not None:
            problems.append(f"{label}时应返回 None，实际 {moment!r}")
    return problems


def main() -> int:
    problems = (check_gap_threshold() + check_precision_levels()
                + check_message_moment())
    if problems:
        print("[check_chat_time] FAILED")
        for item in problems:
            print("  - " + item)
        return 1
    print("[check_chat_time] PASSED：门槛、精度分级与时间坐标回退都符合预期")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
