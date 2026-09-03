"""角色档案导出：把角色文件夹内容打包成单文件 HTML 维基式档案。

图片以内嵌 data URI 封装，脚本与样式均写在同一文件内，可被任意浏览器
直接打开；亮/暗双主题可平滑切换，并支持在尺寸 / 报告 / 回放中搜索定位。
视觉要点：固定高度顶栏（滚动进度条 + 滚动高亮）、磨砂质感背景、低对比卡片、
按身高对数分级的概览/分析色组、wiki 式紧凑尺寸表、按修改时间排序的形象图墙与
灯箱、双栏报告阅读器（左侧时间索引、右侧正文）、按类型着色的回放时间轴。
"""

import base64
import datetime
import html
import json
import math
import os

from logic import ALL_PART_NAMES, format_size
from models import CharacterSnapshot
from paths import data_dir


# -----------------------------------------------------------------
# 数据收集
# -----------------------------------------------------------------

def _char_dir(state: CharacterSnapshot) -> str:
    return os.path.join(data_dir(), "archives", state.giantess_id)


def _list_dir(path: str, exts) -> list:
    if not os.path.isdir(path):
        return []
    files = [os.path.join(path, f) for f in os.listdir(path)
             if f.lower().endswith(exts)]
    return sorted(files)


def _list_images(path: str) -> list:
    """列出图片文件，按修改时间从新到旧排序。"""
    if not os.path.isdir(path):
        return []
    files = [os.path.join(path, f) for f in os.listdir(path)
             if f.lower().endswith(tuple(_MIME_BY_EXT))]
    files.sort(key=lambda p: (os.path.getmtime(p) if os.path.exists(p) else 0),
               reverse=True)
    return files


def _fmt_mtime(path: str) -> str:
    """文件的修改时间，形如 2026-08-30 12:00；失败返回空串。"""
    try:
        ts = os.path.getmtime(path)
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except OSError:
        return ""


_MIME_BY_EXT = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".bmp": "image/bmp",
}


def _load_image_part(path: str) -> tuple:
    """读取图片原始字节，返回 (字节, MIME类型, 文件名)；失败返回 None。"""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as f:
            raw = f.read()
        ext = os.path.splitext(path)[1].lower()
        mime = _MIME_BY_EXT.get(ext, "application/octet-stream")
        return raw, mime, os.path.basename(path)
    except Exception as e:
        print(f"[ArchiveExport] 图片读取失败 {path}: {e}")
        return None


def _data_uri(raw: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def _fmt_num(v, nd=2) -> str:
    try:
        return f"{float(v):,.{nd}f}"
    except (TypeError, ValueError):
        return str(v)


def _stat_gradient(ratio) -> tuple:
    """复刻状态面板的 绿→黄→红 渐变，返回 (亮色, 暗色) 十六进制对。"""
    try:
        ratio = min(1.0, max(0.0, float(ratio)))
    except (TypeError, ValueError):
        ratio = 0.0
    if ratio <= 0.5:
        t = ratio / 0.5
        r = 76 + int((255 - 76) * t)
        g = 175 + int((193 - 175) * t)
        b = 80 + int((7 - 80) * t)
    else:
        t = (ratio - 0.5) / 0.5
        r = 255 + int((244 - 255) * t)
        g = 193 + int((67 - 193) * t)
        b = 7 + int((54 - 7) * t)
    return (f"#{r:02x}{g:02x}{b:02x}",
            f"#{max(0, r - 30):02x}{max(0, g - 30):02x}{max(0, b - 30):02x}")


# -----------------------------------------------------------------
# 报告文本渲染（与报告面板的行分类规则一致）
# -----------------------------------------------------------------

def _strike_span(text: str) -> str:
    """把 [STRIKE]..[/STRIKE] 转成 <del>，其余转义。"""
    parts = []
    pos = 0
    while True:
        start = text.find("[STRIKE]", pos)
        if start == -1:
            if text[pos:]:
                parts.append(html.escape(text[pos:]))
            break
        if text[pos:start]:
            parts.append(html.escape(text[pos:start]))
        end = text.find("[/STRIKE]", start + 8)
        if end == -1:
            parts.append('<del>' + html.escape(text[start + 8:]) + '</del>')
            break
        parts.append('<del>' + html.escape(text[start + 8:end]) + '</del>')
        pos = end + 9
    return "".join(parts)


def _render_report_text(text: str) -> str:
    """按报告面板同一套行规则，把报告纯文本转成带样式的 HTML。"""
    out = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped == "":
            out.append("<span class='ln-blank'></span>")
        elif line.startswith("QUIP_LINE:"):
            content = line.replace("QUIP_LINE:", "").replace('"', "").strip()
            cls = "ln-quip" if content else "ln-compare"
            content = content or "（暂无事件记录）"
            out.append(f"<span class='{cls}'>{_strike_span(content)}</span>")
        elif "身高：" in line:
            out.append(f"<span class='ln-title'>{_strike_span(line)}</span>")
        elif line.startswith("═"):
            out.append(f"<span class='ln-sep'>{html.escape(line)}</span>")
        elif line.startswith("\u200b"):
            out.append(f"<span class='ln-intro'>{_strike_span(line.lstrip(chr(0x200b)))}</span>")
        elif any(m in line for m in ("✨", "💔", "✅")):
            out.append(f"<span class='ln-will'>{_strike_span(line)}</span>")
        elif "📏" in line:
            out.append(f"<span class='ln-measure'>{_strike_span(line)}</span>")
        elif "└─" in line:
            out.append(f"<span class='ln-compare'>{_strike_span(line)}</span>")
        elif stripped.startswith("─") and ("─" * 10) in stripped:
            out.append(f"<span class='ln-casualty-sep'>{html.escape(line)}</span>")
        elif "本报告总计" in line:
            out.append(f"<span class='ln-casualty'>{_strike_span(line)}</span>")
        else:
            out.append(f"<span class='ln-body'>{_strike_span(line)}</span>")
    return "\n".join(out)


# -----------------------------------------------------------------
# 回放渲染
# -----------------------------------------------------------------

_TYPE_BADGE = {
    "background": ("环境", "t-bg"),
    "branch": ("分支", "t-branch"),
    "dialog": ("对话", "t-dialog"),
    "interaction": ("互动", "t-inter"),
    "action": ("行动", "t-action"),
}

_ACTION_LABEL = {
    "option": "选项",
    "insert": "插入段落",
    "background": "场景切换",
    "ending": "结局",
    "sensitivity": "敏感效果",
    "none": "条件标记",
}


def _render_trigger_record(entry: dict) -> str:
    name = html.escape(str(entry.get("name", "")))
    action_type = entry.get("action_type") or ""
    label = _ACTION_LABEL.get(action_type, action_type or "未知")
    head = f"<div class='rp-trigger-head'>⚡ 触发器「{name}」<span class='rp-trigger-tag'>{html.escape(label)}</span></div>"
    body_parts = []
    data = entry.get("action_data") or {}
    if action_type == "option":
        prompt = str(data.get("prompt", "")).strip()
        if prompt:
            body_parts.append(f"<div class='rp-trigger-body'>{html.escape(prompt)}</div>")
        chosen = entry.get("choice_text")
        if chosen is None:
            chosen = entry.get("choice", "")
        options = data.get("options") or []
        if options:
            items = []
            for i, opt in enumerate(options):
                text = str(opt.get("text", "") or opt.get("prompt", ""))
                mark = ""
                try:
                    if entry.get("choice_index") is not None and int(entry["choice_index"]) == i:
                        mark = " ✔"
                except (TypeError, ValueError):
                    pass
                items.append(f"<li{' class=chosen' if mark else ''}>{html.escape(text)}{mark}</li>")
            body_parts.append("<ul class='rp-options'>" + "".join(items) + "</ul>")
        if chosen and not options:
            body_parts.append(f"<div class='rp-trigger-body chosen'>玩家选择：{html.escape(str(chosen))}</div>")
    elif action_type == "insert":
        text = str(data.get("text", "")).strip()
        if text:
            body_parts.append(f"<div class='rp-trigger-body'>{html.escape(text)}</div>")
    elif action_type == "ending":
        end_name = str(data.get("name", "") or data.get("ending_text", "")).strip()
        if end_name:
            body_parts.append(f"<div class='rp-trigger-body rp-ending'>🏁 结局：{html.escape(end_name)}</div>")
    elif action_type == "background":
        img = entry.get("image_path_resolved") or data.get("image_path", "")
        if img:
            body_parts.append(f"<div class='rp-trigger-body'>🖼 背景切换为 {html.escape(os.path.basename(str(img)))}</div>")
    elif action_type == "sensitivity":
        attr = data.get("attr", "")
        strength = data.get("strength", "")
        if attr or strength != "":
            body_parts.append(
                f"<div class='rp-trigger-body'>属性 {html.escape(str(attr))} × 强度 {html.escape(str(strength))}</div>")
    if not body_parts and action_type in ("none", "", None):
        body_parts.append("<div class='rp-trigger-body muted'>（仅标记条件成立，无动作）</div>")
    return head + "".join(body_parts)


def _render_replay_entries(entries: list) -> str:
    blocks = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("kind") == "trigger":
            blocks.append(f"<div class='rp-item rp-trigger'>{_render_trigger_record(entry)}</div>")
            continue
        t = str(entry.get("type", ""))
        label, cls = _TYPE_BADGE.get(t, ("未知", "t-unknown"))
        text = str(entry.get("text", "") or "")
        chips = []
        step = entry.get("step")
        if step is not None:
            chips.append(f"<span class='rp-chip'>第 {html.escape(str(step))} 步</span>")
        for key, name in (("intrusion_after", "介入度"), ("destruction_after", "破坏性")):
            if key in entry:
                before = entry.get(key.replace("_after", "_before"))
                chips.append(
                    f"<span class='rp-chip'>{name} {_fmt_num(before)} → {_fmt_num(entry[key])}</span>")
        for k, v in dict(entry.get("custom_after") or {}).items():
            b = dict(entry.get("custom_before") or {}).get(k)
            chips.append(f"<span class='rp-chip'>{html.escape(str(k))} {_fmt_num(b)} → {_fmt_num(v)}</span>")
        if entry.get("casualty_increase"):
            chips.append(f"<span class='rp-chip chip-casualty'>☠ +{_fmt_num(entry['casualty_increase'])}</span>")
        if "total_casualties_after" in entry:
            chips.append(
                f"<span class='rp-chip'>累计伤亡 {_fmt_num(entry['total_casualties_after'])}</span>")
        chips_html = ""
        if chips:
            chips_html = "<div class='rp-chips'>" + "".join(chips) + "</div>"
        blocks.append(
            f"<div class='rp-item rp-step {cls}'>"
            f"<span class='rp-badge {cls}'>【{label}】</span>"
            f"<div class='rp-text'>{html.escape(text)}</div>"
            f"{chips_html}</div>"
        )
    return "\n".join(blocks)


# -----------------------------------------------------------------
# HTML 组装
# -----------------------------------------------------------------

def _esc(v) -> str:
    return html.escape(str(v if v is not None else ""))


def _build_infobox(state: CharacterSnapshot, img_srcs: list, latest_date: str) -> str:
    rows = []
    if state.nick:
        rows.append(("昵称", _esc(state.nick)))
    rows.append(("身高", _esc(format_size(state.height))))
    if state.birthday:
        rows.append(("生日", _esc(state.birthday)))
    rows.append(("创建时间", _esc(str(state.created_at)[:19].replace("T", " "))))
    rows.append(("更新时间", _esc(str(state.updated_at)[:19].replace("T", " "))))

    rows_html = "".join(
        f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in rows)
    tags = " ".join(f"<span class='tag'>{_esc(t)}</span>" for t in (state.selected_tags or []))
    avatar_html = ""
    if img_srcs:
        date_html = (f"<div class='infobox-image-date'>🕑 {_esc(latest_date)}</div>"
                     if latest_date else "")
        avatar_html = (f"<div class='infobox-image'><img src='{img_srcs[0]}' alt='{_esc(state.name)}'>"
                       f"{date_html}</div>")
    tags_html = f"<div class='infobox-tags'>{tags}</div>" if tags else ""
    return (
        f"{avatar_html}"
        f"<div class='infobox-caption'>{_esc(state.name)}</div>"
        f"{tags_html}"
        f"<table class='infobox-table'>{rows_html}</table>"
    )


def _build_analysis_section(state: CharacterSnapshot) -> str:
    """性格 + 介入度/破坏性渐变指标条 + 累计伤亡。"""
    p = state.personality
    char_html = ""
    if p is not None:
        desc = ""
        if p.description:
            desc = f"<div class='ana-desc'>{_esc(p.description)}</div>"
        char_html = (
            f"<div class='ana-char'><span class='ana-badge'>性格</span>"
            f"<span class='ana-char-name'>{_esc(p.name)}</span>{desc}</div>"
        )
    else:
        char_html = "<div class='ana-char muted'>(未记录性格)</div>"

    def bar(name, value):
        try:
            val = min(4.5, max(0.0, float(value)))
        except (TypeError, ValueError):
            val = 0.0
        ratio = val / 4.5
        light, dark = _stat_gradient(ratio)
        pct = f"{ratio * 100:.0f}%"
        return (
            f"<div class='stat-item'>"
            f"<div class='stat-head'><span class='stat-name'>{name}</span>"
            f"<span class='stat-val'>{_fmt_num(val)} / 4.5</span></div>"
            f"<div class='stat-track'><div class='stat-fill' "
            f"style='width:{pct};--sf1:{light};--sf2:{dark}'></div></div>"
            f"</div>"
        )

    try:
        casualties = float(state.total_casualties or 0)
    except (TypeError, ValueError):
        casualties = 0.0
    if casualties > 999999999:
        casualty_str = "999,999,999+"
    else:
        casualty_str = f"{int(casualties):,}"
    casualty_html = (
        f"<div class='stat-item casualty'><div class='stat-head'>"
        f"<span class='stat-name'>☠ 累计伤亡</span></div>"
        f"<div class='casualty-num'>{casualty_str}</div></div>"
    )

    return (
        f"{char_html}"
        f"<div class='ana-stats'>{bar('介入度', state.intrusion)}"
        f"{bar('破坏性', state.destruction)}{casualty_html}</div>"
        f"{_build_evolution_chart(state)}"
    )


def _fmt_compact_num(v) -> str:
    """把数值压缩为中文数量级文本（如 1万 / 350万 / 2.5亿），用于坐标轴刻度。"""
    v = float(v)
    if v >= 1e8:
        return f"{v / 1e8:g}亿"
    if v >= 1e4:
        return f"{v / 1e4:g}万"
    return f"{v:g}"


def _collect_evolution_series(state: CharacterSnapshot) -> list:
    """把演化表汇总为统计图数据点：时间、累计伤亡、累计|步进|与更改标签。

    步进按需求取绝对值后累加；伤亡直接采用行内记录的累计值。
    """
    points = []
    cum_step = 0.0
    for row in (state.evolution or []):
        try:
            t = datetime.datetime.fromisoformat(str(row.changed_at))
            cas = max(0.0, float(row.casualties or 0.0))
            step = abs(float(row.step or 0.0))
        except (TypeError, ValueError):
            continue
        cum_step += step
        points.append({"t": t, "cas": cas, "step": cum_step,
                       "source": (getattr(row, "source", "") or "")})
    return points


def _build_evolution_chart(state: CharacterSnapshot) -> str:
    """分析卡片内的演化统计图（内联 SVG）。

    横轴为时间，左轴（对数刻度）为累计伤亡，右轴为累计步进（取绝对值）。
    加载时恢复（recover_evolution）一行的增长绘制为自上一行起的线性爬升，
    即增长被摊入加载之前的离线区间，而不是集中在加载当天。
    """
    points = _collect_evolution_series(state)
    if not points:
        return "<p class='empty-hint'>暂无演化数据。</p>"

    w, h = 720.0, 260.0
    m_left, m_right, m_top, m_bottom = 62.0, 56.0, 30.0, 26.0
    plot_w = w - m_left - m_right
    plot_h = h - m_top - m_bottom
    base_y = m_top + plot_h

    t0, t1 = points[0]["t"], points[-1]["t"]
    if t1 <= t0:
        t0 = t0.replace(hour=0, minute=0, second=0, microsecond=0)
        t1 = t0 + datetime.timedelta(days=1)
    span = (t1 - t0).total_seconds()

    def x_of(t):
        return m_left + (t - t0).total_seconds() / span * plot_w

    cas_max = max(p["cas"] for p in points)
    cas_hi = max(1, math.ceil(math.log10(cas_max))) if cas_max >= 1 else 1

    def y_cas(cas):
        return m_top + plot_h * (1.0 - math.log10(max(1.0, cas)) / cas_hi)

    step_max = max(p["step"] for p in points)
    step_hi = step_max * 1.08 if step_max > 0 else 1.0

    def y_step(step):
        return m_top + plot_h * (1.0 - step / step_hi)

    # 纵向网格与左轴刻度（伤亡，按数量级取整刻度，过多时隔一取一）
    decades = list(range(0, cas_hi + 1))
    if len(decades) > 7:
        kept = [d for d in decades if d % 2 == 0]
        if kept[-1] != decades[-1]:
            kept.append(decades[-1])
        decades = kept
    grid = "".join(
        f"<line x1='{m_left:g}' y1='{y_cas(10.0 ** d):.1f}' x2='{w - m_right:g}' "
        f"y2='{y_cas(10.0 ** d):.1f}' stroke='var(--border-soft)' stroke-width='1'/>"
        f"<text x='{m_left - 6:g}' y='{y_cas(10.0 ** d) + 3:.1f}' text-anchor='end' "
        f"font-size='10' fill='var(--muted)'>{_esc(_fmt_compact_num(10.0 ** d))}</text>"
        for d in decades)

    # 右轴刻度（累计步进，线性）与横轴时间刻度
    right_axis = "".join(
        f"<text x='{w - m_right + 6:g}' y='{y_step(step_hi * k / 3) + 3:.1f}' "
        f"font-size='10' fill='var(--muted)'>{step_hi * k / 3:.2f}</text>"
        for k in range(4))
    date_fmt = "%Y-%m-%d" if span > 320 * 86400 else "%m-%d"
    x_labels = "".join(
        f"<text x='{x_of(t0 + datetime.timedelta(seconds=span * i / 4)):.1f}' "
        f"y='{h - 8:g}' text-anchor='middle' font-size='10' fill='var(--muted)'"
        f">{(t0 + datetime.timedelta(seconds=span * i / 4)).strftime(date_fmt)}</text>"
        for i in range(5))
    axis_lines = (
        f"<line x1='{m_left:g}' y1='{m_top:g}' x2='{m_left:g}' y2='{base_y:g}' "
        f"stroke='var(--border)' stroke-width='1'/>"
        f"<line x1='{m_left:g}' y1='{base_y:g}' x2='{w - m_right:g}' y2='{base_y:g}' "
        f"stroke='var(--border)' stroke-width='1'/>"
        f"<line x1='{w - m_right:g}' y1='{m_top:g}' x2='{w - m_right:g}' y2='{base_y:g}' "
        f"stroke='var(--border)' stroke-width='1'/>")

    series = ""
    if len(points) >= 2:
        cas_pts = " ".join(f"{x_of(p['t']):.1f},{y_cas(p['cas']):.1f}" for p in points)
        step_pts = " ".join(f"{x_of(p['t']):.1f},{y_step(p['step']):.1f}" for p in points)
        area_d = (f"M{x_of(points[0]['t']):.1f},{base_y:.1f} "
                  + " ".join(f"L{x_of(p['t']):.1f},{y_cas(p['cas']):.1f}" for p in points)
                  + f" L{x_of(points[-1]['t']):.1f},{base_y:.1f} Z")
        series = (
            f"<path d='{area_d}' fill='var(--casualty)' fill-opacity='0.08'/>"
            f"<polyline points='{cas_pts}' fill='none' stroke='var(--casualty)' "
            f"stroke-width='2' stroke-linejoin='round'/>"
            f"<polyline points='{step_pts}' fill='none' stroke='var(--stat-blue)' "
            f"stroke-width='1.6' stroke-linejoin='round' stroke-dasharray='4 3'/>")

    dots = []
    for p in points:
        title = (f"{p['t']:%Y/%m/%d %H:%M}\n累计伤亡 {p['cas']:,.0f}"
                 f"\n累计步进 {p['step']:.2f}")
        if p["source"]:
            title += f"\n来源 {p['source']}"
        dots.append(
            f"<circle cx='{x_of(p['t']):.1f}' cy='{y_cas(p['cas']):.1f}' r='3' "
            f"fill='var(--casualty)'><title>{_esc(title)}</title></circle>"
            f"<circle cx='{x_of(p['t']):.1f}' cy='{y_step(p['step']):.1f}' r='2.4' "
            f"fill='var(--stat-blue)'><title>{_esc(title)}</title></circle>")

    legend = (
        "<div class='evo-chart-legend'>"
        "<span><span class='legend-swatch' style='background:var(--casualty)'></span>"
        "累计伤亡（左轴，对数）</span>"
        "<span><span class='legend-swatch' style='background:var(--stat-blue)'></span>"
        "累计步进（右轴，取绝对值）</span></div>")
    caption = ("<p class='chart-caption'>离线恢复（加载时结算）的增长按线性摊入"
               "加载之前的离线区间，而非集中在加载当天；悬停数据点可查看来源。</p>")

    svg = (f"<svg viewBox='0 0 {w:g} {h:g}' role='img' aria-label='伤亡与步进演化统计图'>"
           f"{grid}{right_axis}{x_labels}{axis_lines}{series}{''.join(dots)}</svg>")
    return f"<div class='evo-chart'>{legend}{svg}{caption}</div>"


def _build_sizes_section(state: CharacterSnapshot) -> str:
    """wiki 式紧凑表格：部位与尺寸一一对应；有解锁情报的部位以链接样式展开。"""
    unlocks = state.size_unlocks or {}
    pairs = []
    for part in ALL_PART_NAMES:
        if part not in state.body_parts:
            continue
        if part != "身高" and unlocks.get(part, "") == "":
            continue
        val = state.body_parts.get(part)
        size_str = format_size(val, base_size=state.height)
        info = unlocks.get(part, "")
        note = info if info and info != "MEASURED" else ""
        pairs.append((part, size_str, note))
    if not pairs:
        return "<p class='empty-hint'>暂无已解锁的身体尺寸。</p>"
    cells = []
    for part, size_str, note in pairs:
        if note:
            name_html = (f"<details class='size-item'><summary>{_esc(part)}</summary>"
                         f"<div class='unlock-note'>{_esc(note)}</div></details>")
        else:
            name_html = f"<span class='sz-plain'>{_esc(part)}</span>"
        cells.append(f"<td class='sz-name'>{name_html}</td>"
                     f"<td class='sz-val'>{_esc(size_str)}</td>")
    rows = []
    for i in range(0, len(cells), 4):
        row = cells[i:i + 4]
        row += ["<td></td><td></td>"] * ((4 - len(row)) // 2)
        rows.append("<tr>" + "".join(row) + "</tr>")
    return f"<table class='size-table'>{''.join(rows)}</table>"


def _file_caption(path: str) -> tuple:
    """从文件名提取显示标题与时间（形如 名字_报告_20260827020645.txt）。"""
    base = os.path.splitext(os.path.basename(path))[0]
    parts = base.split("_")
    date_part = ""
    if parts and parts[-1].isdigit() and len(parts[-1]) >= 14:
        d = parts.pop()
        try:
            date_part = datetime.datetime.strptime(d[:14], "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            date_part = d
    title = "_".join(parts) if parts else base
    return title, date_part


def _build_reports_section(report_files: list, show_casualties: bool) -> str:
    """双栏报告阅读器：左侧时间索引，右侧单篇正文（切换显示，节约纵向空间）。"""
    if not report_files:
        return "<p class='empty-hint'>暂无报告记录。</p>"
    entries = []
    panes = []
    made = False
    for i, path in enumerate(report_files):
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception as e:
            print(f"[ArchiveExport] 报告读取失败 {path}: {e}")
            continue
        title, date_part = _file_caption(path)
        if not show_casualties:
            text = "\n".join(
                ln for ln in text.split("\n") if "本报告总计" not in ln)
        body = _render_report_text(text)
        pid = f"rpt-{i}"
        act = " active" if not made else ""
        made = True
        entries.append(
            f"<button type='button' class='report-entry{act}' data-target='{pid}'>"
            f"<span class='re-date'>{_esc(date_part or '—')}</span>"
            f"<span class='re-name'>{_esc(title)}</span></button>"
        )
        panes.append(
            f"<div class='report-pane{act}' id='{pid}'>"
            f"<div class='report-text'>{body}</div></div>"
        )
    if not panes:
        return "<p class='empty-hint'>暂无报告记录。</p>"
    return (
        "<div class='report-split'>"
        f"<div class='report-rail'>{''.join(entries)}</div>"
        f"<div class='report-panes'>{''.join(panes)}</div>"
        "</div>"
    )


def _build_replays_section(replay_files: list) -> str:
    if not replay_files:
        return "<p class='empty-hint'>暂无副本回放记录。</p>"
    out = []
    for path in replay_files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                entries = json.load(f)
        except Exception as e:
            print(f"[ArchiveExport] 回放读取失败 {path}: {e}")
            continue
        if not isinstance(entries, list):
            continue
        title, date_part = _file_caption(path.replace(".replay.json", ".json"))
        date_html = f"<span class='file-date'>{_esc(date_part)}</span>" if date_part else ""
        steps = sum(1 for e in entries if isinstance(e, dict) and e.get("kind") != "trigger")
        triggers = len(entries) - steps
        meta = (f"<span class='file-meta'>{steps} 段" +
                (f" · {triggers} 次触发" if triggers else "") + "</span>")
        body = _render_replay_entries(entries)
        out.append(
            f"<details class='record-card replay-card'>"
            f"<summary><span class='file-icon'>🏰</span>{_esc(title)}{meta}{date_html}</summary>"
            f"<div class='replay-text'>{body}</div></details>"
        )
    return "\n".join(out)


def _build_endings_section(state: CharacterSnapshot) -> str:
    endings = state.achieved_endings or []
    if not endings:
        return "<p class='empty-hint'>尚未达成任何重要结局。</p>"
    items = []
    for e in endings:
        name = _esc(e.get("name", "未命名结局"))
        text = _esc(e.get("ending_text", ""))
        at = _esc(str(e.get("achieved_at", ""))[:19].replace("T", " "))
        text_html = f"<div class='ending-text'>{text}</div>" if text else ""
        items.append(
            f"<div class='ending-card'><div class='ending-head'>🏁 {name}"
            f"<span class='file-date'>{at}</span></div>{text_html}</div>"
        )
    return "\n".join(items)


_CSS = """
:root{
  --bg1:#FAFAFA; --bg2:#E9E7E3; --card:#F8F7F4; --card2:#F1F0EC;
  --border:#D5D2CC; --border-soft:#E2E0DB;
  --text:#3A392B; --muted:#918C84; --soft:#7D776C;
  --title:#6B5F47; --gold:#5D4037; --gold-line:#B8860B; --gold-hi:#E8C56A;
  --topbar-bg:rgba(80,78,72,.96); --topbar-text:#F9F3F2; --chip-bg:#EFEEE9;
  --stat-blue:#1976D2; --casualty:#C62828;
  --shadow:rgba(90,82,64,.07); --shadow-hi:rgba(90,82,64,.12);
  --shadow-deep:rgba(58,57,43,.10); --edge-hi:rgba(255,255,255,.5);
  --row-alt:rgba(139,122,96,.05);
  --rank-tint:rgba(184,134,11,.05);
  --hero-veil:rgba(247,241,233,.8);
  --hero-grad1:#5D4037; --hero-grad2:#B8860B;
  --ln-quip-bg:rgba(106,27,182,.06); --ln-casualty-bg:rgba(183,28,28,.05);
  --ln-title:#5D4037; --ln-sep:#B8860B; --ln-intro:#4E342E; --ln-will:#2E7D32;
  --ln-measure:#00695C; --ln-compare:#795548; --ln-quip:#6A1B9A; --ln-casualty:#B71C1C;
  --ln-body:#424242; --t-bg:#55705A; --t-branch:#795548; --t-dialog:#1976D2;
  --t-inter:#C2410C; --t-action:#B71C1C; --t-unknown:#9E9E9E;
  --grain:url("data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' width='220' height='220'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/><feColorMatrix type='saturate' values='0'/><feComponentTransfer><feFuncA type='linear' slope='0.05' intercept='0'/></feComponentTransfer></filter><rect width='100%25' height='100%25' filter='url(%23n)'/></svg>");
}
html[data-theme="dark"]{
  --bg1:#1E1E22; --bg2:#202024; --card:#1D2024; --card2:#23262B;
  --border:#37393E; --border-soft:#2C2E33;
  --text:#E0E0E0; --muted:#9E9E9E; --soft:#B5B0A8;
  --title:#BCAAA4; --gold:#FFD54F; --gold-line:#8D6E63; --gold-hi:#FFD54F;
  --topbar-bg:rgba(28,29,32,.94); --topbar-text:#F9F3F2; --chip-bg:#26282D;
  --stat-blue:#42A5F5; --casualty:#EF5350;
  --shadow:rgba(0,0,0,.26); --shadow-hi:rgba(0,0,0,.38);
  --shadow-deep:rgba(0,0,0,.42); --edge-hi:rgba(255,255,255,.04);
  --row-alt:rgba(255,255,255,.03);
  --rank-tint:rgba(255,213,79,.08);
  --hero-veil:rgba(22,24,30,.74);
  --hero-grad1:#FFD54F; --hero-grad2:#FFB74D;
  --ln-quip-bg:rgba(206,147,216,.10); --ln-casualty-bg:rgba(255,82,82,.08);
  --ln-title:#FFD54F; --ln-sep:#8D6E63; --ln-intro:#BCAAA4; --ln-will:#A5D6A7;
  --ln-measure:#4DB6AC; --ln-compare:#8D6E63; --ln-quip:#CE93D8; --ln-casualty:#FF5252;
  --ln-body:#E0E0E0; --t-bg:#9DB39E; --t-branch:#A1887F; --t-dialog:#64B5F6;
  --t-inter:#FB923C; --t-action:#EF9A9A; --t-unknown:#757575;
  --grain:url("data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' width='220' height='220'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/><feColorMatrix type='saturate' values='0'/><feComponentTransfer><feFuncA type='linear' slope='0.07' intercept='0'/></feComponentTransfer></filter><rect width='100%25' height='100%25' filter='url(%23n)'/></svg>");
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
/* 概览/分析卡片的色组随身高对数分级（data-rank 由导出时按 log10(身高米) 计算） */
html[data-rank="0"]{--hero-grad1:#01579B;--hero-grad2:#29B6F6;--rank-tint:rgba(2,119,189,.055)}
html[data-rank="1"]{--hero-grad1:#1B5E20;--hero-grad2:#8BC34A;--rank-tint:rgba(27,94,32,.05)}
html[data-rank="3"]{--hero-grad1:#BF360C;--hero-grad2:#FF9800;--rank-tint:rgba(191,54,12,.05)}
html[data-rank="4"]{--hero-grad1:#7F0000;--hero-grad2:#E53935;--rank-tint:rgba(127,0,0,.05)}
html[data-rank="5"]{--hero-grad1:#311B92;--hero-grad2:#AB47BC;--rank-tint:rgba(49,27,146,.055)}
html[data-theme="dark"][data-rank="0"]{--hero-grad1:#4FC3F7;--hero-grad2:#B3E5FC;--rank-tint:rgba(79,195,247,.08)}
html[data-theme="dark"][data-rank="1"]{--hero-grad1:#81C784;--hero-grad2:#C5E1A5;--rank-tint:rgba(129,199,132,.08)}
html[data-theme="dark"][data-rank="3"]{--hero-grad1:#FF8A65;--hero-grad2:#FFCC80;--rank-tint:rgba(255,138,101,.08)}
html[data-theme="dark"][data-rank="4"]{--hero-grad1:#EF5350;--hero-grad2:#FF8A80;--rank-tint:rgba(239,83,80,.09)}
html[data-theme="dark"][data-rank="5"]{--hero-grad1:#B39DDB;--hero-grad2:#F48FB1;--rank-tint:rgba(179,157,219,.09)}
body{margin:0;color:var(--text);
  font-family:"Segoe UI","Microsoft YaHei","PingFang SC",sans-serif;line-height:1.7;
  background:
    var(--grain),
    radial-gradient(1100px 520px at 88% -8%, rgba(184,134,11,.045), transparent 62%),
    linear-gradient(175deg, var(--bg1), var(--bg2));
  background-attachment:fixed}
a{color:var(--stat-blue);text-decoration:none}
img{max-width:100%}
::selection{background:rgba(184,134,11,.28)}
::-webkit-scrollbar{width:11px;height:11px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--border);border-radius:6px;
  border:3px solid transparent;background-clip:content-box}
::-webkit-scrollbar-thumb:hover{background-color:var(--muted)}

/* 主题切换平滑过渡（仅在切换瞬间启用，避免干扰日常 hover 动画） */
html.theme-anim *,html.theme-anim *::before,html.theme-anim *::after{
  transition:background-color .4s ease,color .4s ease,border-color .4s ease,
    box-shadow .4s ease,fill .4s ease,opacity .35s ease!important}

/* ---------- 顶部导航 ---------- */
.topbar{position:sticky;top:0;z-index:100;color:var(--topbar-text);
  background:var(--topbar-bg);
  backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);
  box-shadow:0 2px 14px var(--shadow-deep)}
.topbar-inner{width:80%;max-width:none;margin:0 auto;display:flex;align-items:center;gap:18px;
  height:46px;padding:0 28px;flex-wrap:wrap}
.logo{font-weight:700;font-size:17px;letter-spacing:1px;white-space:nowrap;
  text-shadow:0 1px 2px rgba(0,0,0,.25)}
.topnav{display:flex;gap:2px;flex-wrap:wrap;flex:1}
.topnav a{color:var(--topbar-text);opacity:.78;font-size:13.5px;padding:5px 13px;
  border-radius:8px;position:relative;
  transition:background .15s,transform .15s}
.topnav a:hover{opacity:1;background:rgba(255,255,255,.13);transform:translateY(-1px)}
.topnav a.active{opacity:1;background:rgba(255,255,255,.18);
  box-shadow:inset 0 -2px 0 var(--gold-hi)}
.search-box{display:flex;align-items:center;gap:4px;flex:0 1 300px;min-width:180px}
.search-box input[type="search"]{flex:1;min-width:0;height:30px;
  background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.32);
  color:var(--topbar-text);border-radius:8px;padding:0 11px;font-size:13px;
  outline:none;font-family:inherit;
  transition:background .15s,border-color .15s}
.search-box input[type="search"]::placeholder{color:rgba(249,243,242,.5)}
.search-box input[type="search"]:focus{background:rgba(255,255,255,.18);
  border-color:rgba(255,255,255,.55)}
.search-count{font-size:11px;opacity:.8;white-space:nowrap;min-width:3.2em;text-align:right;
  font-variant-numeric:tabular-nums}
.search-box button{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.35);
  color:var(--topbar-text);border-radius:6px;width:26px;height:26px;padding:0;cursor:pointer;
  font-size:10px;line-height:1;
  transition:background .15s}
.search-box button:hover:not(:disabled){background:rgba(255,255,255,.18)}
.search-box button:disabled{opacity:.35;cursor:default}
#themeBtn{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.35);
  color:var(--topbar-text);border-radius:8px;padding:3px 11px;cursor:pointer;
  font-size:13px;
  transition:background .15s}
#themeBtn:hover{background:rgba(255,255,255,.18)}
.progress{position:absolute;left:0;bottom:-2px;height:2px;width:0;
  background:linear-gradient(90deg,var(--gold-line),var(--gold-hi));
  box-shadow:0 0 8px rgba(232,197,106,.65)}

/* ---------- 布局 ---------- */
.layout{width:80%;max-width:none;margin:26px auto 60px;padding:0 28px;display:grid;
  grid-template-columns:280px minmax(0,1fr);gap:26px;align-items:start}
@media(max-width:980px){.layout{grid-template-columns:1fr}
  .sidebar{position:static;max-width:440px;width:100%;margin:0 auto 6px}}
.sidebar{position:sticky;top:60px}
main{min-width:0}

/* ---------- 通用卡片 ---------- */
.section,.infobox{background:var(--card);border:1px solid var(--border);
  border-radius:6px;box-shadow:0 1px 2px var(--shadow)}
.section{position:relative;padding:16px 24px;margin-bottom:14px;scroll-margin-top:60px}
/* 各章节统一底色；仅分析卡片以身高对应的色组着色 */
#analysis{background:linear-gradient(0deg,var(--rank-tint),var(--rank-tint)),var(--card)}
#analysis h2::before{background:linear-gradient(180deg,var(--hero-grad2),var(--hero-grad1))}
.section::before,.section::after{content:'';position:absolute;width:16px;height:16px;
  pointer-events:none;opacity:.45;border:0 solid var(--gold-line)}
.section::before{top:9px;left:9px;border-top-width:2px;border-left-width:2px;
  border-top-left-radius:4px}
.section::after{bottom:9px;right:9px;border-bottom-width:2px;border-right-width:2px;
  border-bottom-right-radius:4px}
.section h2{margin:0 0 4px;font-size:16px;color:var(--title);display:flex;
  align-items:center;gap:9px;padding-bottom:7px;border-bottom:1px solid transparent;
  border-image:linear-gradient(90deg,var(--gold-line),rgba(184,134,11,0)) 1;
  letter-spacing:.5px}
.section h2::before{content:'';flex:none;width:3px;height:15px;border-radius:2px;
  background:linear-gradient(180deg,var(--gold-line),var(--gold))}
.h2-ico{font-size:14px;transform:translateY(1px)}
.section .section-sub{font-size:11.5px;color:var(--muted);margin:0 0 10px;letter-spacing:.5px;
  display:flex;align-items:center;gap:8px}
.section-sub::before{content:'';width:18px;height:1px;flex:none;
  background:linear-gradient(90deg,var(--gold-line),transparent)}
.section h3{font-size:14.5px;color:var(--gold);margin:18px 0 8px}
.intro-text{white-space:pre-wrap;font-size:14.5px;max-width:72em}
.empty-hint{color:var(--muted);font-size:13px}
.muted{color:var(--muted)}

/* 入场动画（仅当 JS 可用时启用，避免无脚本环境内容不可见） */
.js main .section{opacity:0;transform:translateY(16px)}
.js main .section.in{opacity:1;transform:none;
  transition:opacity .55s ease,transform .55s ease}

/* ---------- 侧栏信息框 ---------- */
.infobox{padding:16px 16px 14px;overflow:hidden}
.infobox-image{position:relative}
.infobox-image img{width:100%;border-radius:4px;display:block;
  box-shadow:0 1px 4px var(--shadow)}
.infobox-image::after{content:'';position:absolute;inset:0;border-radius:4px;
  box-shadow:inset 0 0 0 1px rgba(255,255,255,.22);pointer-events:none}
.infobox-image-date{text-align:center;font-size:11px;color:var(--muted);
  font-family:Consolas,monospace;margin-top:7px;letter-spacing:.5px}
.infobox-caption{text-align:center;font-weight:700;font-size:17px;color:var(--title);
  margin:10px 0 4px;letter-spacing:.5px;position:relative;padding-bottom:9px}
.infobox-caption::after{content:'';position:absolute;left:50%;bottom:0;transform:translateX(-50%);
  width:46px;height:2px;border-radius:1px;
  background:linear-gradient(90deg,transparent,var(--gold-line),transparent)}
.infobox-tags{text-align:center;margin:7px 0 2px}
.tag{display:inline-block;background:var(--chip-bg);border:1px solid var(--border);
  border-radius:999px;padding:1px 10px;font-size:11px;color:var(--soft);margin:2px;
  transition:all .15s}
.tag:hover{border-color:var(--gold-line);color:var(--gold);transform:translateY(-1px)}
.infobox-table{width:100%;border-collapse:collapse;margin-top:9px;font-size:12.5px}
.infobox-table tbody tr:nth-child(even){background:var(--row-alt)}
.infobox-table th{color:var(--muted);font-weight:400;text-align:left;padding:4.5px 7px;
  border-top:1px solid var(--border-soft);white-space:nowrap;vertical-align:top}
.infobox-table td{padding:4.5px 7px;border-top:1px solid var(--border-soft)}

/* ---------- Hero 概览 ---------- */
.hero{overflow:hidden;padding:0}
.hero-bg{position:absolute;inset:-30px;background-size:cover;background-position:center 30%;
  filter:blur(22px) saturate(.78);opacity:.3;animation:heroDrift 26s ease-in-out infinite}
html[data-theme="dark"] .hero-bg{opacity:.2}
@keyframes heroDrift{0%,100%{transform:scale(1)}50%{transform:scale(1.055)}}
.hero-veil{position:absolute;inset:0;
  background:linear-gradient(180deg,var(--hero-veil),var(--card))}
.hero-content{position:relative;padding:24px 30px 0;display:flex;
  align-items:center;gap:20px}
.hero-avatar{flex:none;width:78px;height:78px;border-radius:50%;padding:3px;
  background:linear-gradient(135deg,var(--hero-grad1),var(--hero-grad2));
  box-shadow:0 2px 8px var(--shadow-hi)}
.hero-avatar img{width:100%;height:100%;object-fit:cover;border-radius:50%;
  display:block;border:2px solid var(--card)}
.hero-id{min-width:0}
.hero-content h2{font-size:26px;margin:0;border:none;padding:0;
  letter-spacing:1px;line-height:1.25;
  background:linear-gradient(115deg,var(--hero-grad1) 20%,var(--hero-grad2));
  -webkit-background-clip:text;background-clip:text;color:transparent}
.hero-content h2::before{display:none}
.hero-nick{color:var(--muted);font-size:13px;margin-top:4px}
.hero-nick::before{content:'✦ ';color:var(--hero-grad2)}
.hero-nick::after{content:' ✦';color:var(--hero-grad2)}
.hero-tags{margin-top:8px}
/* 简介位于头像与名称右侧，不再单独占行 */
.hero-intro{flex:1;min-width:240px;align-self:center;padding-left:20px;
  border-left:1px solid var(--border-soft)}
.hero-intro .intro-text{font-size:13px;line-height:1.65;max-width:none;margin:0}
@media(max-width:900px){.hero-content{flex-wrap:wrap}
  .hero-intro{flex-basis:100%;border-left:none;padding-left:0}}
.hero-body{position:relative;padding:12px 30px 18px}

/* ---------- 分析 ---------- */
.ana-badge{display:inline-block;font-size:11px;font-weight:700;color:var(--hero-grad1);
  border:1px solid var(--hero-grad2);border-radius:999px;padding:1px 10px;
  letter-spacing:2px;margin-right:10px;vertical-align:2px}
.ana-char-name{font-size:16.5px;font-weight:700;color:var(--title)}
.ana-desc{font-size:13.5px;color:var(--soft);margin-top:8px;max-width:72em}
.ana-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));
  gap:16px;margin-top:14px;padding-top:14px;border-top:1px dashed var(--border-soft)}
.stat-head{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:7px}
.stat-name{font-size:12.5px;color:var(--soft);letter-spacing:1px}
.stat-val{font-family:Consolas,monospace;font-size:12.5px;color:var(--muted)}
.stat-track{height:10px;border-radius:5px;
  background:linear-gradient(180deg,var(--row-alt),var(--chip-bg));
  border:1px solid var(--border-soft);overflow:hidden;
  box-shadow:inset 0 1px 3px rgba(0,0,0,.08)}
.stat-fill{height:100%;border-radius:5px;
  background:linear-gradient(90deg,var(--sf1),var(--sf2));
  box-shadow:0 0 6px var(--sf1);position:relative}
.stat-fill::after{content:'';position:absolute;inset:0;
  background:linear-gradient(180deg,rgba(255,255,255,.26),transparent 55%)}
html[data-theme="dark"] .stat-fill{filter:brightness(1.12)}
.casualty-num{font-family:Consolas,monospace;font-weight:700;font-size:28px;
  letter-spacing:1px;color:var(--casualty);line-height:1.25;
  text-shadow:0 1px 0 var(--edge-hi)}
.stat-item.casualty .stat-name{color:var(--casualty)}

/* ---------- 演化统计图 ---------- */
.evo-chart{margin-top:14px;padding-top:14px;border-top:1px dashed var(--border-soft)}
.evo-chart-legend{display:flex;flex-wrap:wrap;gap:14px;font-size:12px;
  color:var(--text);margin-bottom:6px}
.legend-swatch{display:inline-block;width:10px;height:10px;border-radius:2px;
  margin-right:5px;vertical-align:-1px}
.evo-chart svg{width:100%;height:auto;display:block}
.evo-chart .chart-caption{font-size:11px;color:var(--muted);margin:6px 0 0}

/* ---------- 形象图 ---------- */
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px}
.gallery figure{margin:0;background:var(--card2);border:1px solid var(--border-soft);
  border-radius:5px;padding:9px;text-align:center;overflow:hidden;
  transition:transform .18s,box-shadow .18s,border-color .18s}
.gallery figure:hover{transform:translateY(-3px);border-color:var(--gold-line);
  box-shadow:0 3px 8px var(--shadow)}
.img-wrap{position:relative;border-radius:3px;overflow:hidden}
.img-wrap img{display:block;width:100%;transition:transform .35s ease;cursor:zoom-in}
.gallery figure:hover .img-wrap img{transform:scale(1.045)}
.img-wrap::after{content:'⤢';position:absolute;right:8px;bottom:8px;width:26px;height:26px;
  border-radius:50%;background:rgba(20,18,14,.55);color:#fff;display:flex;
  align-items:center;justify-content:center;font-size:13px;opacity:0;transition:opacity .2s}
.gallery figure:hover .img-wrap::after{opacity:1}
.gallery figcaption{font-size:11px;color:var(--muted);margin-top:6px;
  letter-spacing:.5px;font-family:Consolas,monospace}

/* 灯箱 */
.lightbox{position:fixed;inset:0;z-index:300;display:flex;align-items:center;
  justify-content:center;background:rgba(18,16,12,.84);
  backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px);
  opacity:0;pointer-events:none;transition:opacity .22s ease}
.lightbox.open{opacity:1;pointer-events:auto}
.lightbox figure{margin:0;max-width:min(92vw,1100px);display:flex;
  flex-direction:column;align-items:center;gap:12px}
.lightbox img{max-width:100%;max-height:80vh;border-radius:5px;
  box-shadow:0 18px 60px rgba(0,0,0,.55);cursor:default}
.lightbox figcaption{color:rgba(255,255,255,.85);font-size:13px;letter-spacing:.5px}
.lb-btn{position:fixed;background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.3);
  color:#fff;border-radius:50%;cursor:pointer;font-size:18px;line-height:1;
  width:42px;height:42px;display:flex;align-items:center;justify-content:center;
  transition:background .15s,transform .15s}
.lb-btn:hover{background:rgba(255,255,255,.24);transform:scale(1.06)}
.lb-close{top:20px;right:22px;font-size:15px}
.lb-prev{left:22px;top:50%;margin-top:-21px}
.lb-next{right:22px;top:50%;margin-top:-21px}

/* ---------- 身体尺寸：wiki 式紧凑表格 ---------- */
.size-table{width:100%;border-collapse:collapse;font-size:12px;line-height:1.5}
.size-table tr:nth-child(even){background:var(--row-alt)}
.size-table td{padding:2.5px 8px;border-bottom:1px solid var(--border-soft);
  vertical-align:top}
.size-table tr:last-child td{border-bottom:none}
.size-table .sz-val{width:1%;white-space:nowrap;text-align:right;
  font-family:Consolas,monospace;font-variant-numeric:tabular-nums}
.size-item{display:inline}
.size-item summary{display:inline;list-style:none;cursor:pointer;
  color:var(--stat-blue);text-decoration:underline dotted rgba(25,118,210,.45);
  text-underline-offset:2px}
html[data-theme="dark"] .size-item summary{
  text-decoration-color:rgba(66,165,245,.5)}
.size-item summary::-webkit-details-marker{display:none}
.size-item summary::marker{content:""}
.size-item summary:hover{text-decoration:underline}
.size-item[open] summary{font-weight:600}
.size-item.has-hit{background:rgba(255,213,79,.30);border-radius:2px;
  box-shadow:0 0 0 1px rgba(184,134,11,.35)}
.unlock-note{display:block;font-size:11.5px;color:var(--muted);padding:3px 2px 1px;
  white-space:pre-wrap}
mark.search-hit{background:rgba(255,213,79,.55);color:inherit;border-radius:2px;
  padding:0 1px;box-shadow:0 0 0 1px rgba(184,134,11,.35);scroll-margin-top:60px}
mark.search-hit.current{background:#FFD54F;color:#3A392B;box-shadow:0 0 0 2px #B8860B}

/* ---------- 报告双栏阅读器 ---------- */
.report-split{display:grid;grid-template-columns:200px minmax(0,1fr);gap:14px;
  height:clamp(420px,68vh,760px);align-items:stretch}
.report-rail{display:flex;flex-direction:column;gap:6px;overflow-y:auto;
  min-height:0;padding-right:2px}
.report-entry{appearance:none;text-align:left;font-family:inherit;color:var(--text);
  background:var(--chip-bg);border:1px solid var(--border-soft);border-radius:5px;
  padding:7px 12px;cursor:pointer;position:relative;
  transition:border-color .15s,background .15s,transform .15s}
.report-entry:hover{border-color:var(--gold-line);transform:translateX(2px)}
.report-entry.active{border-color:var(--gold-line);background:var(--card2);
  box-shadow:inset 3px 0 0 var(--gold-line)}
.re-date{display:block;font-family:Consolas,monospace;font-size:12px;font-weight:700;
  color:var(--gold);font-variant-numeric:tabular-nums}
.re-name{display:block;font-size:11px;color:var(--muted);margin-top:2px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.report-entry.has-hit::after{content:'';position:absolute;top:9px;right:9px;width:7px;
  height:7px;border-radius:50%;background:var(--gold-hi);
  box-shadow:0 0 0 2px rgba(255,213,79,.35)}
.report-panes{min-width:0;overflow-y:auto;min-height:0;padding-right:4px}
.report-pane{display:none}
.report-pane.active{display:block;animation:paneIn .25s ease}
@keyframes paneIn{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:none}}
.report-pane.has-hit{outline:1px dashed var(--gold-line);outline-offset:6px;border-radius:8px}

/* 报告正文：排版紧凑 */
.report-text{padding:4px 2px;white-space:pre-wrap;
  font-family:"FangSong","STFangsong","FangSong_GB2312","SimSun",serif;font-size:12.5px;
  line-height:1.55}
.report-text span{display:inline}
.report-text .ln-blank{display:inline}
.ln-title{display:block;color:var(--ln-title);font-weight:700;font-size:15px;
  text-align:center;letter-spacing:2px;padding:2px 0 3px}
.ln-sep{display:block;color:var(--ln-sep);font-size:12.5px;text-align:center;
  letter-spacing:2px;opacity:.85;overflow:hidden}
.ln-intro{color:var(--ln-intro);font-size:12.5px}
.ln-will{color:var(--ln-will);font-weight:700;font-size:12.5px}
.ln-measure{color:var(--ln-measure);font-weight:700;font-size:12.5px}
.ln-compare{color:var(--ln-compare);font-size:12.5px}
.ln-quip{display:block;color:var(--ln-quip);font-style:italic;
  font-family:Consolas,monospace;font-size:12px;background:var(--ln-quip-bg);
  border-radius:4px;padding:1px 8px;margin:2px 0}
.ln-casualty-sep{display:block;color:var(--muted);font-size:12px;overflow:hidden}
.ln-casualty{display:block;color:var(--ln-casualty);font-weight:700;font-size:12.5px;
  border-radius:4px;padding:1px 8px;margin:2px 0}
.ln-body{color:var(--ln-body);font-size:12.5px}

/* ---------- 回放卡片 ---------- */
.record-card{background:var(--card2);border:1px solid var(--border-soft);border-radius:5px;
  margin-bottom:10px;overflow:hidden;transition:box-shadow .18s,border-color .18s}
.record-card:hover{box-shadow:0 2px 5px var(--shadow)}
.record-card>summary{cursor:pointer;padding:10px 16px;font-weight:600;font-size:13.5px;
  color:var(--title);display:flex;align-items:center;gap:9px;flex-wrap:wrap;
  transition:background .15s;list-style:none}
.record-card>summary::-webkit-details-marker{display:none}
.record-card>summary:hover{background:var(--chip-bg)}
.record-card>summary::after{content:'▾';margin-left:4px;color:var(--muted);
  font-size:11px;transition:transform .2s}
.record-card[open]>summary::after{transform:rotate(180deg)}
.record-card[open]>summary{border-bottom:1px solid var(--border-soft)}
.record-card.has-hit,.record-card[open].has-hit{border-color:var(--gold-line)}
.record-card.has-hit{box-shadow:0 0 0 1px var(--gold-line),0 2px 6px var(--shadow)}
.file-icon{display:inline-flex;align-items:center;justify-content:center;width:26px;
  height:26px;border-radius:8px;background:var(--chip-bg);font-size:13.5px;
  border:1px solid var(--border-soft)}
.file-date{margin-left:auto;font-size:11px;color:var(--muted);font-weight:400;
  font-family:Consolas,monospace}
.file-meta{font-size:11px;color:var(--muted);font-weight:400}
.record-card.secret{border-style:dashed}
.record-card.secret>summary{color:var(--soft)}

/* ---------- 回放时间轴 ---------- */
.replay-text{padding:20px 24px 20px 46px;position:relative}
.replay-text::before{content:'';position:absolute;left:16px;top:26px;bottom:22px;
  width:2px;border-radius:2px;
  background:linear-gradient(180deg,var(--gold-line),var(--border-soft))}
.rp-item{position:relative;margin-bottom:18px}
.rp-item::before{content:'';position:absolute;left:-34px;top:5px;width:10px;height:10px;
  border-radius:50%;background:var(--dotc,var(--card2));
  border:2px solid var(--dotc,var(--gold-line));
  box-shadow:0 0 0 3px var(--card)}
.rp-item.t-bg{--dotc:var(--t-bg)}
.rp-item.t-branch{--dotc:var(--t-branch)}
.rp-item.t-dialog{--dotc:var(--t-dialog)}
.rp-item.t-inter{--dotc:var(--t-inter)}
.rp-item.t-action{--dotc:var(--t-action)}
.rp-item.t-unknown{--dotc:var(--t-unknown)}
.rp-trigger::before{border-radius:2px;transform:rotate(45deg);
  background:var(--card2);border-color:var(--gold-line)}
.rp-badge{display:inline-block;font-size:11.5px;font-weight:700;color:var(--card);
  background:var(--t-unknown);border-radius:5px;padding:1px 7px;margin-right:6px;
  vertical-align:2px;box-shadow:0 1px 2px var(--shadow)}
.rp-badge.t-bg{background:var(--t-bg)}.rp-badge.t-branch{background:var(--t-branch)}
.rp-badge.t-dialog{background:var(--t-dialog)}.rp-badge.t-inter{background:var(--t-inter)}
.rp-badge.t-action{background:var(--t-action)}
.rp-text{white-space:pre-wrap;font-size:14.5px;margin-top:5px}
.rp-chips{margin-top:7px;display:flex;flex-wrap:wrap;gap:6px}
.rp-chip{font-size:10.5px;color:var(--muted);background:var(--chip-bg);
  border:1px solid var(--border-soft);border-radius:999px;padding:1px 9px;
  font-family:Consolas,monospace;font-variant-numeric:tabular-nums}
.rp-chip.chip-casualty{color:var(--casualty);border-color:var(--casualty)}
.rp-trigger{border-left:3px solid var(--gold-line);background:var(--chip-bg);
  border-radius:0 5px 5px 0;padding:10px 15px;box-shadow:0 1px 2px var(--shadow)}
.rp-trigger-head{font-size:13px;font-weight:700;color:var(--gold)}
.rp-trigger-tag{font-size:10.5px;font-weight:400;border:1px solid var(--gold-line);
  border-radius:999px;padding:0 8px;margin-left:8px;color:var(--gold)}
.rp-trigger-body{font-size:13.5px;white-space:pre-wrap;margin-top:5px}
.rp-trigger-body.muted{color:var(--muted)}
.rp-ending{color:var(--gold);font-weight:700}
.rp-options{margin:5px 0 0;padding-left:20px;font-size:13.5px}
.rp-options li.chosen{color:var(--gold);font-weight:700}

/* ---------- 结局 ---------- */
.ending-card{position:relative;border:1px solid var(--border-soft);background:var(--card2);
  border-radius:5px;padding:11px 16px 10px;margin-bottom:10px;overflow:hidden;
  transition:transform .15s,box-shadow .15s,border-color .15s}
.ending-card::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;
  background:linear-gradient(90deg,var(--gold-line),rgba(184,134,11,.12))}
.ending-card:hover{transform:translateY(-2px);border-color:var(--gold-line);
  box-shadow:0 2px 6px var(--shadow)}
.ending-head{font-weight:700;font-size:14px;color:var(--gold);display:flex;
  align-items:baseline;gap:10px;flex-wrap:wrap;letter-spacing:.5px}
.ending-text{font-size:13.5px;margin-top:6px;white-space:pre-wrap}

/* ---------- 返回顶部 ---------- */
#backTop{position:fixed;right:26px;bottom:26px;z-index:150;width:42px;height:42px;
  border-radius:50%;border:1px solid var(--gold-line);background:var(--card);
  color:var(--gold);font-size:17px;line-height:1;cursor:pointer;
  box-shadow:0 2px 8px var(--shadow-hi);opacity:0;pointer-events:none;
  transform:translateY(8px);transition:opacity .25s,transform .25s,background .15s,color .15s}
#backTop.show{opacity:1;pointer-events:auto;transform:none}
#backTop:hover{background:var(--gold-line);color:var(--card)}

.footer{width:80%;max-width:none;margin:0 auto 32px;padding:0 28px;font-size:11.5px;
  color:var(--muted);text-align:center;letter-spacing:.5px}

@media(prefers-reduced-motion:reduce){
  *,*::before,*::after{animation:none!important;transition:none!important}
  html{scroll-behavior:auto}
  .js main .section{opacity:1;transform:none}
}
@media print{
  .topbar,#backTop,.lightbox,.img-wrap::after,.report-rail{display:none!important}
  .report-pane{display:block!important}
  .report-split{height:auto}
  .report-panes{overflow:visible;height:auto}
  .layout{display:block;max-width:none}
  .section,.infobox{box-shadow:none;break-inside:avoid}
  .js main .section{opacity:1;transform:none}
  body{background:#fff}
}
"""

_JS = """
(function(){
  var root=document.documentElement;
  root.classList.add('js');
  var btn=document.getElementById('themeBtn');
  var animTimer=null;
  btn.addEventListener('click',function(){
    root.classList.add('theme-anim');
    var dark=root.getAttribute('data-theme')==='dark';
    root.setAttribute('data-theme',dark?'light':'dark');
    btn.textContent=dark?'🌙':'☀️';
    clearTimeout(animTimer);
    animTimer=setTimeout(function(){root.classList.remove('theme-anim')},480);
  });
  document.querySelectorAll('a[href^="#"]').forEach(function(a){
    a.addEventListener('click',function(e){
      var id=this.getAttribute('href').slice(1);
      var el=id&&document.getElementById(id);
      if(!el) return;
      e.preventDefault();
      el.scrollIntoView({behavior:'smooth',block:'start'});
      if(history.replaceState) history.replaceState(null,'', '#'+id);
    });
  });

  /* 章节入场动画 */
  var sections=[].slice.call(document.querySelectorAll('main .section'));
  if('IntersectionObserver' in window){
    var io=new IntersectionObserver(function(es){
      es.forEach(function(en){
        if(en.isIntersecting){en.target.classList.add('in');io.unobserve(en.target);}
      });
    },{threshold:.06,rootMargin:'0px 0px -30px 0px'});
    sections.forEach(function(s){io.observe(s);});
  }else{
    sections.forEach(function(s){s.classList.add('in');});
  }

  /* 滚动：进度条 / 目录高亮 / 返回顶部 */
  var navLinks=[].slice.call(document.querySelectorAll('.topnav a'));
  var prog=document.getElementById('scrollProgress');
  var backTop=document.getElementById('backTop');
  var ticking=false;
  function onScroll(){
    if(ticking) return; ticking=true;
    requestAnimationFrame(function(){
      ticking=false;
      var st=window.scrollY||document.documentElement.scrollTop;
      var dh=document.documentElement.scrollHeight-window.innerHeight;
      if(prog) prog.style.width=(dh>0?(st/dh*100):0)+'%';
      if(backTop) backTop.classList.toggle('show',st>560);
      var cur='';
      for(var i=0;i<sections.length;i++){
        if(sections[i].getBoundingClientRect().top<=80) cur=sections[i].id;
      }
      navLinks.forEach(function(a){
        a.classList.toggle('active',a.getAttribute('href')==='#'+cur);
      });
    });
  }
  window.addEventListener('scroll',onScroll,{passive:true});
  window.addEventListener('resize',onScroll);
  onScroll();
  if(backTop) backTop.addEventListener('click',function(){
    window.scrollTo({top:0,behavior:'smooth'});
  });

  /* 报告双栏切换 */
  function activatePane(pane){
    document.querySelectorAll('.report-pane.active').forEach(function(p){
      if(p!==pane) p.classList.remove('active');
    });
    pane.classList.add('active');
    document.querySelectorAll('.report-entry').forEach(function(e){
      e.classList.toggle('active',e.getAttribute('data-target')===pane.id);
    });
  }
  document.querySelectorAll('.report-entry').forEach(function(e){
    e.addEventListener('click',function(){
      var p=document.getElementById(e.getAttribute('data-target'));
      if(p) activatePane(p);
    });
  });

  /* 画廊灯箱 */
  var thumbs=[].slice.call(document.querySelectorAll('#gallery .img-wrap img'));
  if(thumbs.length){
    var lb=document.createElement('div');
    lb.className='lightbox';
    lb.innerHTML='<button type="button" class="lb-btn lb-close" title="关闭 (Esc)">✕</button>'+
      '<button type="button" class="lb-btn lb-prev" title="上一张 (←)">‹</button>'+
      '<button type="button" class="lb-btn lb-next" title="下一张 (→)">›</button>'+
      '<figure><img alt=""><figcaption></figcaption></figure>';
    document.body.appendChild(lb);
    var lbImg=lb.querySelector('img'),lbCap=lb.querySelector('figcaption'),lbIdx=0;
    function showLb(i){
      lbIdx=((i%thumbs.length)+thumbs.length)%thumbs.length;
      var t=thumbs[lbIdx];
      lbImg.src=t.src;
      var fig=t.closest('figure');
      var cap=fig?fig.querySelector('figcaption'):null;
      lbCap.textContent=(cap?cap.textContent:'')+'　'+(lbIdx+1)+' / '+thumbs.length;
      lb.classList.add('open');
    }
    function hideLb(){lb.classList.remove('open');}
    thumbs.forEach(function(t,i){
      t.addEventListener('click',function(){showLb(i);});
    });
    lb.addEventListener('click',function(e){
      if(e.target===lb||e.target.tagName==='FIGURE') hideLb();
    });
    lb.querySelector('.lb-close').addEventListener('click',hideLb);
    lb.querySelector('.lb-prev').addEventListener('click',function(){showLb(lbIdx-1);});
    lb.querySelector('.lb-next').addEventListener('click',function(){showLb(lbIdx+1);});
    document.addEventListener('keydown',function(e){
      if(!lb.classList.contains('open')) return;
      if(e.key==='Escape') hideLb();
      else if(e.key==='ArrowLeft') showLb(lbIdx-1);
      else if(e.key==='ArrowRight') showLb(lbIdx+1);
    });
  }

  var input=document.getElementById('archiveSearch');
  var countEl=document.getElementById('searchCount');
  var prevBtn=document.getElementById('searchPrev');
  var nextBtn=document.getElementById('searchNext');
  if(!input) return;
  var hits=[],idx=-1,lastQ='',composing=false,timer=null;
  var SCOPE='#sizes .size-item, #reports .report-text span, #replays .rp-item';

  function clearHits(){
    document.querySelectorAll('mark.search-hit').forEach(function(m){
      var p=m.parentNode;
      p.replaceChild(document.createTextNode(m.textContent),m);
      p.normalize();
    });
    document.querySelectorAll('.has-hit').forEach(function(el){
      el.classList.remove('has-hit');
    });
    hits=[];idx=-1;
  }
  function wrapMatches(el,q){
    var qLow=q.toLowerCase();
    var walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT,null);
    var nodes=[];
    while(walker.nextNode()){
      if(walker.currentNode.nodeValue.toLowerCase().indexOf(qLow)!==-1)
        nodes.push(walker.currentNode);
    }
    nodes.forEach(function(node){
      var text=node.nodeValue,low=text.toLowerCase();
      var frag=document.createDocumentFragment();
      var i=0,j;
      while((j=low.indexOf(qLow,i))!==-1){
        if(j>i) frag.appendChild(document.createTextNode(text.slice(i,j)));
        var mark=document.createElement('mark');
        mark.className='search-hit';
        mark.textContent=text.slice(j,j+q.length);
        frag.appendChild(mark);
        i=j+q.length;
        if(!q.length) break;
      }
      if(i<text.length) frag.appendChild(document.createTextNode(text.slice(i)));
      node.parentNode.replaceChild(frag,node);
    });
  }
  function reveal(el){
    var p=el;
    while(p){
      if(p.tagName==='DETAILS') p.open=true;
      if(p.classList&&p.classList.contains('report-pane')&&!p.classList.contains('active'))
        activatePane(p);
      p=p.parentElement;
    }
  }
  function updateChrome(){
    var n=hits.length,q=input.value.trim();
    countEl.textContent=q?(n?(idx+1)+'/'+n:'无匹配'):'';
    prevBtn.disabled=!n;nextBtn.disabled=!n;
  }
  function goTo(i){
    if(!hits.length) return;
    hits.forEach(function(h){h.classList.remove('current')});
    idx=((i%hits.length)+hits.length)%hits.length;
    var cur=hits[idx];
    cur.classList.add('current');
    reveal(cur);
    cur.scrollIntoView({behavior:'smooth',block:'center'});
    updateChrome();
  }
  function runSearch(){
    clearHits();
    var q=input.value.trim();
    lastQ=q;
    if(!q){updateChrome();return;}
    var qLow=q.toLowerCase();
    document.querySelectorAll(SCOPE).forEach(function(el){
      if(el.textContent.toLowerCase().indexOf(qLow)===-1) return;
      wrapMatches(el,q);
      var card=el.closest('.size-item,.record-card,.report-pane');
      if(card) card.classList.add('has-hit');
    });
    hits=Array.prototype.slice.call(document.querySelectorAll('mark.search-hit'));
    document.querySelectorAll('.report-pane').forEach(function(p){
      var e=document.querySelector('.report-entry[data-target="'+p.id+'"]');
      if(e) e.classList.toggle('has-hit',!!p.querySelector('mark.search-hit'));
    });
    if(hits.length) goTo(0);
    else updateChrome();
  }
  function schedule(){
    clearTimeout(timer);
    timer=setTimeout(runSearch,120);
  }
  input.addEventListener('compositionstart',function(){composing=true});
  input.addEventListener('compositionend',function(){composing=false;runSearch()});
  input.addEventListener('input',function(){if(!composing) schedule()});
  input.addEventListener('keydown',function(e){
    if(e.key==='Enter'){
      e.preventDefault();
      clearTimeout(timer);
      var q=input.value.trim();
      if(q!==lastQ) runSearch();
      else if(hits.length) goTo(idx+(e.shiftKey?-1:1));
    }else if(e.key==='Escape'){
      input.value='';runSearch();input.blur();
    }
  });
  prevBtn.addEventListener('click',function(){goTo(idx-1)});
  nextBtn.addEventListener('click',function(){goTo(idx+1)});
  document.addEventListener('keydown',function(e){
    if(e.key!=='/'||e.ctrlKey||e.metaKey||e.altKey) return;
    if(document.activeElement===input) return;
    var t=e.target&&e.target.tagName;
    if(t==='INPUT'||t==='TEXTAREA') return;
    e.preventDefault();
    input.focus();input.select();
  });
})();
"""


def _rank_tier(height) -> int:
    """按身高(米)的对数分级：0:<10m，1:<100m … 5:≥100km；用于概览/分析色组。"""
    try:
        h = float(height)
    except (TypeError, ValueError):
        return 2
    if h <= 0:
        return 0
    return min(5, max(0, int(math.log10(h))))


def export_character_mhtml(state: CharacterSnapshot, file_path: str,
                           show_casualties: bool = True,
                           _base_uri: str = None) -> str:
    """把角色导出为单文件 HTML 档案（图片以 data URI 内嵌），返回写入路径。

    `_base_uri` 保留以兼容旧调用，当前不再使用。
    """
    char_dir = _char_dir(state)
    avatar_dir = os.path.join(char_dir, "avatar")
    report_dir = os.path.join(char_dir, "报告")
    replay_dir = os.path.join(char_dir, "回放")

    avatar_files = _list_images(avatar_dir)
    report_files = _list_dir(report_dir, (".txt",))
    replay_files = _list_dir(replay_dir, (".replay.json",))

    gallery_items = []
    img_entries = []  # (src, 修改时间文本)
    for path in avatar_files:
        part = _load_image_part(path)
        if not part:
            continue
        raw, mime, fname = part
        src = _data_uri(raw, mime)
        mtime_str = _fmt_mtime(path)
        img_entries.append((src, mtime_str))
        gallery_items.append(
            f"<figure><div class='img-wrap'><img src='{src}' alt='{_esc(fname)}'></div>"
            f"<figcaption>{_esc(mtime_str)}</figcaption></figure>")
    img_srcs = [e[0] for e in img_entries]
    latest_date = img_entries[0][1] if img_entries else ""
    gallery_html = ("".join(gallery_items) if gallery_items
                    else "<p class='empty-hint'>暂无形象图。</p>")

    intro_html = (f"<div class='hero-intro'>"
                  f"<div class='intro-text'>{_esc(state.intro_visible)}</div></div>"
                  if state.intro_visible else "")
    hidden_html = ""
    if state.intro_hidden:
        hidden_html = (
            "<details class='record-card secret'><summary><span class='file-icon'>🔒</span>"
            "内部设定（隐藏简介）</summary>"
            f"<div class='intro-text' style='padding:12px 16px'>{_esc(state.intro_hidden)}</div></details>"
        )

    hero_style = ""
    if img_srcs:
        hero_style = f"<div class='hero-bg' style=\"background-image:url('{img_srcs[0]}')\"></div>"
    hero_avatar = (f"<div class='hero-avatar'><img src='{img_srcs[0]}' alt='{_esc(state.name)}'></div>"
                   if img_srcs else "")
    hero_body_html = f"<div class='hero-body'>{hidden_html}</div>" if hidden_html else ""
    nick_html = f"<div class='hero-nick'>{_esc(state.nick)}</div>" if state.nick else ""

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    nav_items = [("overview", "概览"), ("analysis", "分析"), ("endings", "重要结局"),
                 ("sizes", "身体尺寸"), ("gallery", "形象图")]
    if report_files:
        nav_items.append(("reports", f"报告（{len(report_files)}）"))
    if replay_files:
        nav_items.append(("replays", f"副本回放（{len(replay_files)}）"))
    nav_html = "".join(f"<a href='#{anchor}'>{label}</a>" for anchor, label in nav_items)

    tags_hero = " ".join(f"<span class='tag'>{_esc(t)}</span>"
                         for t in (state.selected_tags or []))
    tags_html = f"<div class='hero-tags'>{tags_hero}</div>" if tags_hero else ""

    doc = f"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="light" data-rank="{_rank_tier(state.height)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(state.name)} - 角色档案</title>
<style>{_CSS}</style>
</head>
<body>
<header class="topbar"><div class="topbar-inner">
  <span class="logo">📜 角色档案</span>
  <nav class="topnav">{nav_html}</nav>
  <div class="search-box" title="搜索身体尺寸、报告与副本回放（按 / 聚焦）">
    <input id="archiveSearch" type="search" placeholder="搜索尺寸 / 报告 / 回放" autocomplete="off" spellcheck="false">
    <span id="searchCount" class="search-count"></span>
    <button type="button" id="searchPrev" title="上一个（Shift+Enter）" disabled>▲</button>
    <button type="button" id="searchNext" title="下一个（Enter）" disabled>▼</button>
  </div>
  <button type="button" id="themeBtn" title="切换亮/暗主题">🌙</button>
</div><div class="progress" id="scrollProgress"></div></header>
<div class="layout">
<aside class="sidebar">
  <div class="infobox">{_build_infobox(state, img_srcs, latest_date)}</div>
</aside>
<main>
  <section class="section hero" id="overview">
    {hero_style}<div class="hero-veil"></div>
    <div class="hero-content">
      {hero_avatar}
      <div class="hero-id">
        <h2>{_esc(state.name)}</h2>
        {nick_html}
        {tags_html}
      </div>
      {intro_html}
    </div>
    {hero_body_html}
  </section>
  <section class="section" id="analysis">
    <h2><span class="h2-ico">📊</span>分析</h2>
    <div class="section-sub">性格倾向与探索态势</div>
    {_build_analysis_section(state)}
  </section>
  <section class="section" id="endings">
    <h2><span class="h2-ico">🏁</span>重要结局</h2>
    <div class="section-sub">角色达成过的重要结局索引</div>
    {_build_endings_section(state)}
  </section>
  <section class="section" id="sizes">
    <h2><span class="h2-ico">📏</span>身体尺寸</h2>
    <div class="section-sub">基准身高 {_esc(format_size(state.height))} · 蓝色部位名可展开解锁情报</div>
    {_build_sizes_section(state)}
  </section>
  <section class="section" id="gallery">
    <h2><span class="h2-ico">🖼️</span>形象图</h2>
    <div class="section-sub">共 {len(avatar_files)} 张</div>
    <div class="gallery">{gallery_html}</div>
  </section>
  <section class="section" id="reports">
    <h2><span class="h2-ico">📜</span>报告</h2>
    <div class="section-sub">共 {len(report_files)} 份</div>
    {_build_reports_section(report_files, show_casualties)}
  </section>
  <section class="section" id="replays">
    <h2><span class="h2-ico">🏰</span>副本回放</h2>
    <div class="section-sub">以文本形式回顾副本探索全程</div>
    {_build_replays_section(replay_files)}
  </section>
</main>
</div>
<button type="button" id="backTop" title="回到顶部">↑</button>
<footer class="footer">由 GiantessWiki 导出于 {now_str}。可能含有未公开信息。</footer>
<script>{_JS}</script>
</body>
</html>"""

    os.makedirs(os.path.dirname(os.path.abspath(file_path)) or ".", exist_ok=True)
    with open(file_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(doc)
    return file_path
