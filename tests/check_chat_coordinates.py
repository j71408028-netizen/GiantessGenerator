"""聊天三轴坐标纯函数守卫。

覆盖设计文档 §3：好感归一化；状态对贴边、行动点不足与个性强度的响应；
紧张度对世界态势、关系认知与事件衰减的响应；以及坐标快照与距离计算。

只验证结构与单调性，不锁具体调参值：常量从模块导入，预期值按公式现算，
调整 AP_LOW / EDGE_MARGIN / W_R / W_E / W_A / TAU 不应让本自检变红。

用法：``python tests/check_chat_coordinates.py``
"""
from __future__ import annotations

import datetime
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.models import Personality  # noqa: E402
from services.chat.coordinates import (  # noqa: E402
    AP_LOW, EDGE_MARGIN, NEUTRAL_RESILIENCE, TENSION_TAU_HOURS, W_R,
    ChatCoord, TensionEvent, action_point_deficit, affection_from_attitude,
    build_chat_coord, compute_size_state, compute_tension, edge_pressure,
    event_pressure, world_stance_value,
)

NOW = datetime.datetime(2026, 10, 27, 12, 0, 0)


def _about(actual, expected, tolerance=1e-9):
    return abs(actual - expected) <= tolerance


def _personality(strength):
    return Personality(
        name="测试", init_intrusion=2.5, step_intrusion=0.0,
        init_destruction=2.5, step_destruction=0.0, sensitivity=0.0,
        skip_base_prob=strength)


def check_affection() -> list:
    problems = []
    for attitude, expected in ((-100, 0.0), (0, 0.5), (100, 1.0)):
        actual = affection_from_attitude(attitude)
        if not _about(actual, expected):
            problems.append(f"态度 {attitude} 应归一到 {expected}，实际 {actual}")
    if affection_from_attitude(-300) != 0.0:
        problems.append("低于 -100 的态度应夹到 0")
    if affection_from_attitude(300) != 1.0:
        problems.append("高于 100 的态度应夹到 1")
    if not _about(affection_from_attitude("坏值"), 0.5):
        problems.append("非法态度应回退中性 0.5")
    if not (affection_from_attitude(-1) < affection_from_attitude(0)
            < affection_from_attitude(1)):
        problems.append("好感应随态度单调上升")
    return problems


def check_size_state() -> list:
    problems = []
    baseline = 0.5 + W_R * NEUTRAL_RESILIENCE
    neutral = compute_size_state(2.5, 2.5, 100, None)
    if not _about(neutral, baseline):
        problems.append(f"中性状态应为 {baseline}，实际 {neutral}")

    strong = compute_size_state(2.5, 2.5, 100, _personality(5.0))
    weak = compute_size_state(2.5, 2.5, 100, _personality(0.0))
    if not strong > neutral > weak:
        problems.append("个性越强，状态应越高（越适应）")

    at_edge = compute_size_state(0.5, 4.5, 100, None)
    if not at_edge < neutral:
        problems.append("介入/破坏贴边应降低状态")
    if not (compute_size_state(4.5, 2.5, 100, None)
            < compute_size_state(2.5, 2.5, 100, None)):
        problems.append("只贴一侧边界也应降低状态")

    low_ap = compute_size_state(2.5, 2.5, 0, None)
    if not low_ap < neutral:
        problems.append("行动点不足应降低状态")
    if not _about(action_point_deficit(AP_LOW), 0.0):
        problems.append("行动点刚到阈值时不应有不足")
    if not _about(action_point_deficit(0), 1.0):
        problems.append("行动点耗尽时不足应为 1")
    if not _about(compute_size_state(2.5, 2.5, None, None), neutral):
        problems.append("行动点缺失时不应误判为不足")

    if not _about(edge_pressure(0.5 + EDGE_MARGIN), 0.0):
        problems.append("距边界达到 EDGE_MARGIN 时压力应为 0")
    if not _about(edge_pressure(0.5), 1.0):
        problems.append("贴住边界时压力应为 1")

    extreme = compute_size_state(0.5, 0.5, 0, _personality(0.0))
    if not 0.0 <= extreme <= 1.0:
        problems.append(f"状态必须夹在 0~1，实际 {extreme}")
    return problems


def check_tension() -> list:
    problems = []
    worship = compute_tension("worship", None, (), now=NOW)
    coexist = compute_tension("coexist", None, (), now=NOW)
    hostile = compute_tension("hostile", None, (), now=NOW)
    if not worship < coexist < hostile:
        problems.append("世界态势越敌对，紧张度应越高")

    lover = compute_tension("coexist", "lover", (), now=NOW)
    stranger = compute_tension("coexist", "stranger", (), now=NOW)
    enemy = compute_tension("coexist", "enemy", (), now=NOW)
    if not lover < stranger < enemy:
        problems.append("关系认知偏移方向不对（敌人应高于恋人）")

    if not _about(world_stance_value("worship"), 0.10):
        problems.append("world_stance 语义值未正确映射")
    if not _about(world_stance_value(0.8), 0.8):
        problems.append("world_stance 数值应直接夹取")
    if world_stance_value("unknown") != world_stance_value(None):
        problems.append("未知 world_stance 应回退同一默认值")
    return problems


def check_event_decay() -> list:
    problems = []
    recent = event_pressure([TensionEvent(1.0, NOW)], now=NOW)
    if not _about(recent, 1.0):
        problems.append(f"刚发生的事件权重应为 1，实际 {recent}")

    tau_hours = TENSION_TAU_HOURS
    aged = event_pressure(
        [TensionEvent(1.0, NOW - datetime.timedelta(hours=tau_hours))],
        now=NOW)
    if not _about(aged, math.exp(-1.0), 1e-9):
        problems.append(f"经过 TAU 的事件应衰减到 exp(-1)，实际 {aged}")

    older = event_pressure(
        [TensionEvent(1.0, NOW - datetime.timedelta(hours=tau_hours * 2))],
        now=NOW)
    if not older < aged < recent:
        problems.append("事件压力应随时间单调衰减")

    total = event_pressure(
        [{"weight": 0.5, "changed_at": NOW},
         {"weight": 0.25, "changed_at": NOW}], now=NOW)
    if not _about(total, 0.75):
        problems.append(f"多条事件应求和，实际 {total}")

    skipped = event_pressure(
        [TensionEvent(0.0, NOW), TensionEvent(-1.0, NOW),
         TensionEvent(1.0, "坏时间"), TensionEvent(1.0, "")], now=NOW)
    if skipped != 0.0:
        problems.append("非正权重或坏时间的事件应被跳过")

    future = event_pressure(
        [TensionEvent(1.0, NOW + datetime.timedelta(hours=1))], now=NOW)
    if future != 1.0:
        problems.append("未来时间应按时钟偏差处理为 0 年龄，而不是负衰减")
    return problems


def check_build_coord() -> list:
    problems = []
    coord = build_chat_coord(
        100, "hostile", "enemy",
        [TensionEvent(1.0, NOW)], intrusion=0.5, destruction=4.5,
        action_points=0, personality=_personality(0.0), now=NOW)
    if not isinstance(coord, ChatCoord):
        problems.append("build_chat_coord 应返回 ChatCoord")
        return problems
    if not _about(coord.affection, 1.0):
        problems.append("坐标好感分量未接上态度归一")
    if not _about(coord.tension,
                  compute_tension("hostile", "enemy",
                                  [TensionEvent(1.0, NOW)], now=NOW)):
        problems.append("坐标紧张度分量未接上公式")
    if not _about(coord.size_state,
                  compute_size_state(0.5, 4.5, 0, _personality(0.0))):
        problems.append("坐标状态分量未接上公式")
    if not all(0.0 <= value <= 1.0 for value in coord.as_list()):
        problems.append("坐标快照必须全部分量夹在 0~1")

    origin = ChatCoord(0.0, 0.0, 0.0)
    corner = ChatCoord(1.0, 1.0, 1.0)
    if not _about(origin.distance(corner), math.sqrt(3.0)):
        problems.append("三维欧氏距离计算错误")
    if origin.distance(origin) != 0.0:
        problems.append("同一点距离应为 0")
    if ChatCoord(2.0, -1.0, 0.5).as_list() != [1.0, 0.0, 0.5]:
        problems.append("坐标分量应夹取到 0~1")
    if ChatCoord.from_values([0.1, 0.2]).as_list() != [0.5, 0.5, 0.5]:
        problems.append("长度不为 3 的坐标应回退中性值")
    return problems


def main() -> int:
    problems = (check_affection() + check_size_state() + check_tension()
                + check_event_decay() + check_build_coord())
    if problems:
        print("[check_chat_coordinates] FAILED")
        for item in problems:
            print("  - " + item)
        return 1
    print("[check_chat_coordinates] PASSED：三轴公式、事件衰减与坐标快照符合预期")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
