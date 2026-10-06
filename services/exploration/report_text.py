# -*- coding: utf-8 -*-
"""报告文本的拼装（阶段 3.1 / S2 从 ``core/context.py`` 抽出）。

**与 ``report.py`` 的分界**（最容易搞错的地方）：
``report.ReportEngine`` 产出的是**结构化数据**（quip 结果、伤亡、坐标、地址规划）；
本模块才把数据拼成**给人看的文本**——``[STRIKE]`` 标记、``QUIP_LINE:`` 前缀、
emoji、``═`` 分隔线。所以本模块是**呈现**，不是领域计算。

这两个函数是纯函数：除了 ``settings`` 与 ``data`` 之外不读任何其它状态，
因此做成模块级函数而不是类。
"""

import datetime

from core.logic import format_size


def build_report_text(data: dict, settings: dict, show_will: bool = True) -> str:
    """拼装报告纯文本。

    show_will：是否输出意愿状态行（仅免费报告为 True）。
    """
    name = data["name"]
    nick = data["nick"]
    height = data["height"]
    original_height = data["original_height"]
    will_status = data["will_status"]
    intro_visible = data["intro_visible"]
    quip_results = data["quip_results"]
    today_str = datetime.date.today().strftime("%y/%m/%d")

    report = []

    world_setting = settings.get("world_setting", "appear")
    if world_setting in ("abs_giant", "rel_giant") and original_height is not None:
        strike_text = format_size(original_height)
        height_line = f"{name}    身高：[STRIKE]{strike_text}[/STRIKE] {format_size(height)}"
    else:
        height_line = f"{name}    身高：{format_size(height)}"
    report.append(height_line)
    report.append(f"{'═' * (16 + len(name))}")

    intro_display = intro_visible.strip()
    will_msg = {
        "implemented": f"✨ {name}表示内心渴望得到了回应。",
        "failed": f"💔 {name}似乎觉得还不够...",
        "within": f"✅ 身体规模满足了{name}的预期。"
    }.get(will_status, "")

    # 分隔线与对比循环之间的内容：简介与（免费报告的）意愿状态
    between = []
    if intro_display:
        between.append("")
        for line in intro_display.splitlines():
            if line.strip():
                between.append(f"\u200b{line}")
    if show_will and will_msg:
        between.append("")
        between.append(will_msg)
    if between:
        report.extend(between)
    report.append("")

    total_casualties = data.get("total_casualties", 0.0)
    for res in quip_results:
        report.append(f"📏 {res['part']} {res['size_str']}")
        report.append(res['compare_text'])
        quip_line = f"QUIP_LINE:\"{res['quip_text']}\"\n" if res['quip_text'] else "QUIP_LINE:"
        report.append(quip_line)

    report.append(f"    {'─' * 20}")
    report.append(f"    {today_str}")
    total_cas = int(total_casualties)
    if total_cas > 999999999:
        report.append("     本报告总计伤亡：999,999,999+")
    else:
        report.append(f"    本报告总计伤亡：{total_cas:,}")

    return "\n".join(report)


def build_detail_text(body_parts: dict, height: float) -> str:
    lines = []
    for k, v in body_parts.items():
        lines.append(f"{k:<12} {format_size(v, base_size=height)}")
    return "\n".join(lines)
