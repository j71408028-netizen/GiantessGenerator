"""角色档案导出：把角色文件夹内容打包成单文件 HTML 维基式档案。

图片以内嵌 data URI 封装，脚本与样式均写在同一文件内，可被任意浏览器
直接打开；亮/暗双主题可平滑切换，并支持在尺寸 / 报告 / 回放中搜索定位。
视觉要点：固定高度顶栏（滚动进度条 + 滚动高亮 + 按身高分级变色的角色名）、
磨砂质感背景、低对比卡片、wiki 式紧凑尺寸表（解锁情报以悬浮弹窗展示）、
按身高对数分级的分析色组、wiki 式连贯简介栏、按修改时间排序的形象图墙与
灯箱、双栏报告阅读器（左侧时间索引、右侧正文）、按类型着色的回放时间轴。
"""

import base64
import datetime
import html
import json
import math
import os
import string

from address_model import parse_full as _parse_addr, cell_width_m as _addr_cell_width, \
    format_addr_verbose as _format_addr_verbose
from logic import ALL_PART_NAMES, format_size
from models import CharacterSnapshot
from paths import data_dir, template_dir


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


# 介入度 / 破坏性分档解读（与性格参数对话框 creation_params_dlg 的分档文案一致）
_INTRUSION_BUCKETS = [
    (0.0, "她参与人类生活全凭机缘，时而缺乏兴趣，时而混迹其中。"),
    (0.5, "她在多数时候作壁上观，只在兴趣使然时偶尔掺上一脚。"),
    (1.5, "她习惯了主动进入人们的视野，但仍为自己留足退出的余地。"),
    (2.5, "她巨大身体的存在感强烈地渗透进每场相遇，几乎从不缺席。"),
    (3.5, "少女的甜蜜支配无孔不入，时刻摆弄着人类的精神与道德观。"),
]

_DESTRUCTION_BUCKETS = [
    (0.0, "她的破坏与否全凭当下心情。随时需准备面临巨大损失。"),
    (0.5, "她的行动多克制而留有余地，极少引发真正的破坏。"),
    (1.5, "她不在意地在行动中留下或轻或重的痕迹，破坏如影随形。"),
    (2.5, "她是一场算计着最大损失的天灾，乐意见到一片崩碎。"),
    (3.5, "少女所过之处皆成废墟，毁灭已经成为她的本能与乐趣。"),
]


def _pick_level(value: float, buckets) -> str:
    """按阈值表下限取文案：返回最后一个 value >= 下限 的形容。"""
    picked = buckets[0][1]
    try:
        value = float(value)
    except (TypeError, ValueError):
        return picked
    for lo, text in buckets:
        if value + 1e-9 >= lo:
            picked = text
    return picked


def _evacuation_coef(value: float) -> int:
    """由介入度/破坏性档位给出影响范围系数（身高倍数）。"""
    try:
        d = float(value)
    except (TypeError, ValueError):
        d = 0.0
    if d < 0.5:
        return 2
    if d < 1.5:
        return 5
    if d < 2.5:
        return 10
    if d < 3.5:
        return 18
    return 30


def _build_guide_block(state: CharacterSnapshot) -> str:
    """应对指南块：当前地址（含定位精度提示）+ 无秩序/疏散双范围 + 参考数据警告。"""
    pos = (state.position or "").strip()
    addr = _parse_addr(pos) if pos else None
    if addr is not None:
        loc_text = " - ".join(p for p in (addr.world,) + addr.regions if p)
        cell = _addr_cell_width(pos)
        loc_html = (f"<span class='guide-addr' title=\"{_esc(_format_addr_verbose(pos))}\">"
                    f"{_esc(loc_text)}</span>")
    else:
        loc_html = (f"<span class='guide-addr'>"
                    f"{_esc(pos) if pos else '（尚未记录位置）'}</span>")
        cell = 0.0

    try:
        height = float(state.height or 0)
    except (TypeError, ValueError):
        height = 0.0
    try:
        intrusion_v = float(state.intrusion)
    except (TypeError, ValueError):
        intrusion_v = 0.0

    coef_d = _evacuation_coef(state.destruction)
    evac = height * coef_d
    lawless = evac * intrusion_v / 10.0   # 无秩序范围 = 疏散范围 × 介入度 / 10

    # 定位不精确警告直接拼接在地址行后
    if height > 0 and cell > height * 100:
        loc_html += " <span class='guide-imprecise'>⚠ 当前定位可能不精确</span>"

    return (
        "<div class='ana-guide'>"
        "<div class='guide-title'>应对参考</div>"
        f"<div class='guide-row'><span class='guide-k'>当前地址</span>{loc_html}</div>"
        "<div class='guide-row'><span class='guide-k'>影响范围</span>"
        "<span class='guide-v'>"
        f"疏散范围 <strong class='guide-num'>{_esc(format_size(evac))}</strong>"
        f"（无秩序范围 <strong class='guide-num'>{_esc(format_size(lawless))}</strong>内建议自行逃生）</span></div>"
        "<div class='guide-warn'>⚠ 本栏数据均由 GiantessWiki 内部模型推算，"
        "不代表真实应对建议，请以官方防灾指导为准。</div>"
        "</div>"
    )


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


def _durability_text(d) -> str:
    """把耐久值（1.0 为完好，<0.5 残破，可为负）转为展示文案。"""
    try:
        dv = float(d)
    except (TypeError, ValueError):
        dv = 1.0
    pct = max(0.0, min(1.0, dv)) * 100
    if dv <= 0:
        state = "已损毁"
    elif dv < 0.5:
        state = "残破"
    elif dv < 1.0:
        state = "受损"
    else:
        state = "完好"
    return f"当前耐久 {pct:.0f}%（{state}）"


def _landmark_popup_info(lm_dur: dict, lm_addr: dict = None) -> dict:
    """把耐久表与地址表整理为 显示名 -> 悬浮文案 的映射。

    键为"名称@完整地址"（无地址时仅名称），同名不同地址的地标各算一条；
    同名多条时合并进同一个链接的弹窗，每条一行"位于 地址 + 耐久"。
    """
    lm_addr = lm_addr or {}
    lines_by_name = {}
    for key in sorted(lm_dur):
        if key in lm_addr:
            name = key.split("@", 1)[0]
            lines_by_name.setdefault(name, []).append(
                f"位于 {lm_addr[key]}\n{_durability_text(lm_dur[key])}")
        else:
            lines_by_name.setdefault(key, []).append(_durability_text(lm_dur[key]))
    return {name: "\n".join(lines) for name, lines in lines_by_name.items()}


def _link_landmarks(html_str: str, lm_info: dict) -> str:
    """把报告文本中的独特地标名替换为可点击链接（悬浮显示地址与当前耐久）。

    长名优先替换，避免短名是长名子串时被提前截断。
    """
    for name in sorted(lm_info, key=len, reverse=True):
        esc_name = html.escape(name)
        if esc_name not in html_str:
            continue
        link = (f"<span class='lm-link' data-lm-info=\""
                f"{_esc(lm_info[name])}\">{esc_name}</span>")
        html_str = html_str.replace(esc_name, link)
    return html_str


def _render_report_text(text: str, lm_info: dict = None) -> str:
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
    html_str = "\n".join(out)
    if lm_info:
        html_str = _link_landmarks(html_str, lm_info)
    return html_str


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
    "goto": "章节跳转",
    "effect": "短暂视效",
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
    elif action_type == "goto":
        target = str(data.get("chapter") or "").strip()
        body_parts.append(
            f"<div class='rp-trigger-body'>🚪 进入章节：{html.escape(target or '离开章节')}</div>")
    elif action_type == "effect":
        filter_key = str(data.get("filter") or "").strip()
        if filter_key:
            body_parts.append(
                f"<div class='rp-trigger-body'>✨ 视效 {html.escape(filter_key)}"
                f"（{html.escape(str(data.get('duration', '')))} 步）</div>")
    if not body_parts and action_type in ("none", "", None):
        body_parts.append("<div class='rp-trigger-body muted'>（仅标记条件成立，无动作）</div>")
    return head + "".join(body_parts)


def _render_chapter_record(entry: dict) -> str:
    """章节进入记录：章节自身没有条件，进入由触发器的跳转动作执行。"""
    name = html.escape(str(entry.get("name", "")) or "无章节")
    background = entry.get("background") or {}
    image_path = str(background.get("image_path") or "")
    tag = ""
    if image_path:
        tag = f"<span class='rp-trigger-tag'>🖼 {html.escape(os.path.basename(image_path))}</span>"
    return f"<div class='rp-trigger-head'>📖 章节「{name}」{tag}</div>"


def _render_replay_entries(entries: list) -> str:
    blocks = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("kind") == "trigger":
            blocks.append(f"<div class='rp-item rp-trigger'>{_render_trigger_record(entry)}</div>")
            continue
        if entry.get("kind") == "chapter":
            blocks.append(f"<div class='rp-item rp-trigger'>{_render_chapter_record(entry)}</div>")
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
    """灾害分析：介入度/破坏性指标（数值与趋势、进度条、分档解读）、
    演化统计图、当前地址与疏散范围参考，末尾附参考数据警告。"""

    def trend_of(step: float) -> tuple:
        try:
            step = float(step)
        except (TypeError, ValueError):
            step = 0.0
        if step >= 0.25:
            return ("↗", "预计持续增强", "trend-up")
        if step <= -0.25:
            return ("↘", "预计逐步收敛", "trend-down")
        return ("→", "预计保持平稳", "trend-flat")

    def metric(name, value, step, read_text):
        try:
            val = min(4.5, max(0.0, float(value)))
        except (TypeError, ValueError):
            val = 0.0
        ratio = val / 4.5
        light, dark = _stat_gradient(ratio)
        pct = f"{ratio * 100:.0f}%"
        arrow, phrase, cls = trend_of(step)
        return (
            f"<div class='stat-item'>"
            f"<div class='stat-line'>"
            f"<span class='stat-name'>{name}</span>"
            f"<span class='stat-val'>{_fmt_num(val)} / 4.5</span>"
            f"<span class='stat-trend {cls}'><span class='trend-arrow'>{arrow}</span>{phrase}</span>"
            f"</div>"
            f"<div class='stat-track'><div class='stat-fill' "
            f"style='width:{pct};--sf1:{light};--sf2:{dark}'></div></div>"
            f"<div class='stat-read'>{_esc(read_text)}</div>"
            f"</div>"
        )

    step_i = state.current_step_intrusion
    step_d = state.current_step_destruction
    metrics_html = (
        metric("介入度", state.intrusion, step_i,
               _pick_level(state.intrusion, _INTRUSION_BUCKETS))
        + metric("破坏性", state.destruction, step_d,
                 _pick_level(state.destruction, _DESTRUCTION_BUCKETS))
    )

    left_html = f"<div class='ana-stats'>{metrics_html}</div>"
    return (
        "<div class='ana-grid'>"
        f"<div class='ana-left'>{left_html}</div>"
        f"<div class='ana-right'>{_build_evolution_chart(state)}</div>"
        "</div>"
        f"{_build_guide_block(state)}"
    )


def _fmt_compact_num(v) -> str:
    """把数值压缩为中文数量级文本（如 1万 / 350万 / 2.5亿），用于坐标轴刻度。"""
    v = float(v)
    if v >= 1e8:
        return f"{v / 1e8:g}亿"
    if v >= 1e4:
        return f"{v / 1e4:g}万"
    return f"{v:g}"


def _daytime_hours(start: datetime.datetime, end: datetime.datetime) -> float:
    """统计 [start, end) 中落在日间（6–18点）的小时数，与 StateService 同规则。"""
    day_start_hour, night_start_hour = 6, 18
    if end <= start:
        return 0.0
    total = 0.0
    t = start
    while t < end:
        next_t = min(t.replace(minute=0, second=0, microsecond=0)
                     + datetime.timedelta(hours=1), end)
        if day_start_hour <= t.hour < night_start_hour:
            total += (next_t - t).total_seconds() / 3600.0
        t = next_t
    return total


def _nice_axis(lo: float, hi: float, target: int = 4) -> tuple:
    """为线性坐标轴自动选取合适标度，返回 (下限, 上限, 刻度列表)。

    刻度步长取 1/2/5 × 10^k 中能容纳约 target 段的最小值，
    上下限向外取整到刻度倍数，保证数据点不贴边。
    """
    if hi <= lo:
        hi = lo + 1.0
    raw = (hi - lo) / target
    mag = 10.0 ** math.floor(math.log10(raw))
    step = mag * 10
    for m in (1, 2, 5, 10):
        if m * mag >= raw:
            step = m * mag
            break
    lo2 = math.floor(lo / step) * step
    hi2 = math.ceil(hi / step) * step
    count = int(round((hi2 - lo2) / step))
    return lo2, hi2, [lo2 + i * step for i in range(count + 1)]


def _linear_ticks(lo: float, hi: float, target: int = 4) -> tuple:
    """在 [lo, hi] 上取步长为 1/2/5×10^k 的刻度，返回 (上限, 刻度列表)。

    下限不取整（供不从 0 起画的纵轴使用），刻度对齐到步长倍数，
    上限向上取整到刻度倍数。
    """
    if hi <= lo:
        hi = lo + 1.0
    raw = (hi - lo) / target
    mag = 10.0 ** math.floor(math.log10(raw))
    step = mag * 10
    for m in (1, 2, 5, 10):
        if m * mag >= raw:
            step = m * mag
            break
    hi2 = math.ceil(hi / step) * step
    first = int(math.ceil(lo / step))
    last = int(round(hi2 / step))
    return hi2, [i * step for i in range(first, last + 1)]


_WINDOW_HOURS = 72
_BIN_HOURS = 2
_BIN_COUNT = _WINDOW_HOURS // _BIN_HOURS   # 36 个统计桶


def _collect_evolution_events(state: CharacterSnapshot) -> list:
    """解析演化表为按时间排序的事件列表：时间、单次步进（原值）、累计伤亡、来源。"""
    events = []
    for row in (state.evolution or []):
        try:
            t = datetime.datetime.fromisoformat(str(row.changed_at))
            cas = max(0.0, float(row.casualties or 0.0))
            step = float(row.step or 0.0)
        except (TypeError, ValueError):
            continue
        events.append({"t": t, "step": step, "cas": cas,
                       "source": (getattr(row, "source", "") or "")})
    events.sort(key=lambda e: e["t"])
    return events


def _split_bin_slices(start: datetime.datetime, end: datetime.datetime,
                      t0: datetime.datetime, bin_secs: float) -> list:
    """把 [start, end] 按统计桶边界切分为 (段起点, 段终点, 桶下标) 列表。"""
    slices = []
    k = int(math.floor((start - t0).total_seconds() / bin_secs))
    while True:
        edge = t0 + datetime.timedelta(seconds=(k + 1) * bin_secs)
        seg_end = min(end, edge)
        slices.append((start, seg_end, k))
        if seg_end >= end:
            break
        start = seg_end
        k += 1
    return slices


def _path_from_points(pts: list) -> str:
    """把 [(x, y), ...] 转成 SVG 折线 path 的 d 字符串。"""
    if not pts:
        return ""
    d = f"M {pts[0][0]:.1f},{pts[0][1]:.1f}"
    for x, y in pts[1:]:
        d += f" L {x:.1f},{y:.1f}"
    return d


def _catmull_points(pts: list, subdiv: int = 8) -> list:
    """对点序列做 Catmull-Rom→Bézier 细分采样，输出平滑密集点列。"""
    n = len(pts)
    if n < 2:
        return list(pts)
    out = []
    for i in range(n - 1):
        p0, p1 = pts[i], pts[i + 1]
        pm = pts[i - 1] if i > 0 else p0
        pn = pts[i + 2] if i + 2 < n else p1
        c1 = (p0[0] + (p1[0] - pm[0]) / 6.0, p0[1] + (p1[1] - pm[1]) / 6.0)
        c2 = (p1[0] - (pn[0] - p0[0]) / 6.0, p1[1] - (pn[1] - p0[1]) / 6.0)
        for s in range(subdiv):
            t = s / subdiv
            mt = 1.0 - t
            x = (mt ** 3) * p0[0] + 3 * mt * mt * t * c1[0] \
                + 3 * mt * t * t * c2[0] + (t ** 3) * p1[0]
            y = (mt ** 3) * p0[1] + 3 * mt * mt * t * c1[1] \
                + 3 * mt * t * t * c2[1] + (t ** 3) * p1[1]
            out.append((x, y))
    out.append(pts[-1])
    return out


def _monotone_points(pts: list, subdiv: int = 8) -> list:
    """对（单调不减的）点序列做单调三次插值采样，输出平滑密集点列。

    使用 Fritsch–Carlson 斜率限制，保证中间不外溢成上下抖动
    （累计伤亡曲线必须单调，避免出现“伤亡回落”的视觉误导）。
    """
    n = len(pts)
    if n < 3:
        return list(pts)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    dx = [xs[i + 1] - xs[i] for i in range(n - 1)]
    sl = [(ys[i + 1] - ys[i]) / d if d else 0.0 for i, d in enumerate(dx)]
    m = [0.0] * n
    for i in range(1, n - 1):
        d0, d1 = sl[i - 1], sl[i]
        if d0 * d1 > 0:
            m[i] = 2.0 * d0 * d1 / (d0 + d1)
    m[0] = sl[0] if n > 1 else 0.0
    m[-1] = sl[-1] if n > 1 else 0.0
    out = []
    for i in range(n - 1):
        h = dx[i]
        if h <= 0:
            continue
        y0, y1 = ys[i], ys[i + 1]
        for s in range(subdiv):
            t = s / subdiv
            t2 = t * t
            t3 = t2 * t
            h00 = 2 * t3 - 3 * t2 + 1
            h10 = t3 - 2 * t2 + t
            h01 = -2 * t3 + 3 * t2
            h11 = t3 - t2
            y = (h00 * y0 + h10 * h * m[i]
                 + h01 * y1 + h11 * h * m[i + 1])
            out.append((xs[i] + t * h, y))
    out.append(pts[-1])
    return out


def _build_evolution_chart(state: CharacterSnapshot) -> str:
    """分析卡片内的演化统计图（内联 SVG）。

    左栏展示累计伤亡总数、图例与图注；右栏为统计图（矮版）。
    只统计最近 72 小时，每 2 小时合并为一个统计点：左轴为累计伤亡
    （线性刻度，起点自动选取在数据下限附近、不从 0 开始），右轴为
    单次步进增量（线性刻度，标度至少 0~0.5，桶内求和，不累计）。
    离线恢复（source 以 recover 开头）一行的增长，优先采用该角色状态
    上附加的 offline_details（2 小时精度明细，偶数整点对齐），按明细的
    绝对时间区间直接并入各统计桶；无明细时回退到昼间（6–18 点）均摊。
    累计伤亡用单调插值平滑，步进强度用 Catmull-Rom 平滑成曲线。
    """
    events = _collect_evolution_events(state)
    if not events:
        return "<p class='empty-hint'>暂无演化数据。</p>"

    w, h = 650.0, 240.0
    m_left, m_right, m_top, m_bottom = 62.0, 54.0, 18.0, 28.0
    plot_w = w - m_left - m_right
    plot_h = h - m_top - m_bottom
    base_y = m_top + plot_h
    bin_secs = _BIN_HOURS * 3600.0

    # 桶边界对齐到偶数整点：向上取整到下一个偶数小时作为窗口终点，
    # 使统计桶与横轴标签稳定（不随渲染时刻的分钟数漂移）
    now = datetime.datetime.now()
    t1 = now.replace(minute=0, second=0, microsecond=0)
    if t1 < now or t1.hour % 2:
        t1 += datetime.timedelta(hours=1)
    if t1.hour % 2:
        t1 += datetime.timedelta(hours=1)
    t0 = t1 - datetime.timedelta(hours=_WINDOW_HOURS)

    # 离线恢复明细（角色最近一次恢复所附，覆盖最后 72h）
    offline_details = getattr(state, "offline_details", None) or []
    detail_bins = []
    for d in offline_details:
        try:
            s = datetime.datetime.fromisoformat(str(d.start_at))
            e = datetime.datetime.fromisoformat(str(d.end_at))
        except (TypeError, ValueError):
            continue
        detail_bins.append((s, e, float(d.step or 0.0),
                            float(d.casualties or 0.0)))
    detail_bins.sort(key=lambda x: x[0])

    # 明细属于“最近一次”recover 行：离线明细只保留最后一次恢复产生的
    recover_indexes = [i for i, ev in enumerate(events)
                       if str(ev["source"]).startswith("recover")]
    target_recover = recover_indexes[-1] if recover_indexes else None

    # 展开为累计伤亡断点序列 + 各桶步进增量
    bps = []                       # (时间, 累计伤亡)
    step_bins = [0.0] * _BIN_COUNT
    prev_t = None
    prev_cas = 0.0
    cum = 0.0
    for i, ev in enumerate(events):
        cas_delta = ev["cas"] - prev_cas
        if ev["source"].startswith("recover") and prev_t is not None \
                and ev["t"] > prev_t:
            if detail_bins and i == target_recover:
                # 明细之外的部分（离线早于最近72h窗口的汇总伤亡）：在其起点
                # 前一次性并入累计值（不影响窗口内的曲线形状）
                sum_detail_cas = sum(d[3] for d in detail_bins)
                leftover = max(0.0, cas_delta - sum_detail_cas)
                first_start = detail_bins[0][0]
                if leftover > 0:
                    cum += leftover
                    bps.append((first_start, cum))
                for s, e, dstep, dcas in detail_bins:
                    dur_total = (e - s).total_seconds()
                    if dur_total <= 0:
                        continue
                    for a, b, k in _split_bin_slices(s, e, t0, bin_secs):
                        share = (b - a).total_seconds() / dur_total
                        cum += dcas * share
                        bps.append((b, cum))
                        if 0 <= k < _BIN_COUNT:
                            step_bins[k] += dstep * share
            else:
                # 无明细的离线行（旧数据 / 更早的一次恢复）：按昼间均摊
                slices = _split_bin_slices(prev_t, ev["t"], t0, bin_secs)
                day_total = sum(_daytime_hours(a, b) for a, b, _ in slices)
                dur_total = (ev["t"] - prev_t).total_seconds()
                for a, b, k in slices:
                    if day_total > 0:
                        share = _daytime_hours(a, b) / day_total
                    else:
                        share = (b - a).total_seconds() / dur_total
                    cum += cas_delta * share
                    bps.append((b, cum))
                    if 0 <= k < _BIN_COUNT:
                        step_bins[k] += ev["step"] * share
        else:
            cum = ev["cas"]
            bps.append((ev["t"], cum))
            k = int((ev["t"] - t0).total_seconds() // bin_secs)
            if 0 <= k < _BIN_COUNT:
                step_bins[k] += ev["step"]
        prev_t = ev["t"]
        prev_cas = ev["cas"]

    def cum_at(t):
        val = 0.0
        for bt, bv in bps:
            if bt > t:
                break
            val = bv
        return val

    def x_of(t):
        return m_left + (t - t0).total_seconds() / (_WINDOW_HOURS * 3600.0) * plot_w

    # 左轴（累计伤亡，线性，起点取数据下限附近、不从 0 开始）的自动标度
    cas_points = [cum_at(t0 + datetime.timedelta(seconds=(i + 1) * bin_secs))
                  for i in range(_BIN_COUNT)]
    cas_vals = [cum_at(t0)] + cas_points
    cmin, cmax = min(cas_vals), max(cas_vals)
    span = cmax - cmin
    if span > 0:
        pad = span * 0.12
    else:
        pad = max(cmax * 0.06, 1.0)
    if cmin > 0:
        pad = min(pad, cmin * 0.5)   # 下限不穿过 0
    cas_lo = max(0.0, cmin - pad)
    cas_hi, cas_ticks = _linear_ticks(cas_lo, cmax + pad)
    # 右轴（步进增量）：标度至少 0~0.5
    step_lo, step_hi, step_ticks = _nice_axis(
        min(0.0, min(step_bins)), max(0.5, max(step_bins)))

    def y_cas(v):
        return m_top + plot_h * (1.0 - (v - cas_lo) / (cas_hi - cas_lo))

    def y_step(v):
        return m_top + plot_h * (1.0 - (v - step_lo) / (step_hi - step_lo))

    # 纵向网格与左轴刻度（虚线淡格，更轻盈）
    grid = "".join(
        f"<line x1='{m_left:g}' y1='{y_cas(v):.1f}' x2='{w - m_right:g}' "
        f"y2='{y_cas(v):.1f}' stroke='var(--border-soft)' stroke-width='1' "
        f"stroke-dasharray='2 5' stroke-linecap='round'/>"
        f"<text x='{m_left - 6:g}' y='{y_cas(v) + 3:.1f}' text-anchor='end' "
        f"font-size='12.5' fill='var(--muted)'>{_esc(_fmt_compact_num(v))}</text>"
        for v in cas_ticks)

    # 右轴刻度与横轴时间刻度（每 12 小时一个标签）
    right_axis = "".join(
        f"<text x='{w - m_right + 6:g}' y='{y_step(v) + 3:.1f}' "
        f"font-size='12.5' fill='var(--muted)'>{_esc(_fmt_compact_num(v))}</text>"
        for v in step_ticks)
    x_labels = "".join(
        f"<text x='{x_of(t):.1f}' y='{h - 8:g}' text-anchor='middle' "
        f"font-size='12.5' fill='var(--muted)'>{t:%m-%d %H:%M}</text>"
        for t in (t0 + datetime.timedelta(hours=12 * i)
                  for i in range(_WINDOW_HOURS // 12 + 1)))
    axis_lines = (
        f"<line x1='{m_left:g}' y1='{base_y:g}' x2='{w - m_right:g}' y2='{base_y:g}' "
        f"stroke='var(--border)' stroke-width='1'/>")

    # 曲线：伤亡取各桶末累计值（单调平滑），步进取各桶中心的桶内增量（Catmull-Rom 平滑）
    cas_pts = [(x_of(t0 + datetime.timedelta(seconds=i * bin_secs)),
                y_cas(cum_at(t0 + datetime.timedelta(seconds=i * bin_secs))))
               for i in range(_BIN_COUNT + 1)]
    step_pts = [(x_of(t0 + datetime.timedelta(seconds=(i + 0.5) * bin_secs)),
                 y_step(step_bins[i])) for i in range(_BIN_COUNT)]

    cas_smooth = _monotone_points(cas_pts)
    step_smooth = _catmull_points(step_pts)
    # 步进平滑可能与 Catmull-Rom 越过 0/上边界：夹回绘图区
    step_smooth = [(x, max(m_top, min(base_y, y))) for x, y in step_smooth]

    cas_line = _path_from_points(cas_smooth)
    step_line = _path_from_points(step_smooth)
    area_d = (f"{cas_line} "
              f"L {cas_smooth[-1][0]:.1f},{base_y:.1f} "
              f"L {cas_smooth[0][0]:.1f},{base_y:.1f} Z")
    # 线端圆点：曲线末端的当前位置标记
    cas_end, step_end = cas_smooth[-1], step_smooth[-1]
    end_dots = (
        f"<circle cx='{step_end[0]:.1f}' cy='{step_end[1]:.1f}' r='2.6' "
        f"fill='var(--stat-blue)' stroke='var(--card)' stroke-width='1.4'/>"
        f"<circle cx='{cas_end[0]:.1f}' cy='{cas_end[1]:.1f}' r='3.2' "
        f"fill='var(--casualty)' stroke='var(--card)' stroke-width='1.5'/>")
    series = (
        f"<defs><linearGradient id='evoArea' x1='0' y1='0' x2='0' y2='1'>"
        f"<stop offset='0' style='stop-color:var(--casualty);stop-opacity:0.20'/>"
        f"<stop offset='1' style='stop-color:var(--casualty);stop-opacity:0.02'/>"
        f"</linearGradient></defs>"
        f"<path d='{area_d}' fill='url(#evoArea)'/>"
        f"<path d='{cas_line}' fill='none' stroke='var(--casualty)' "
        f"stroke-width='2' stroke-linejoin='round' stroke-linecap='round'/>"
        f"<path d='{step_line}' fill='none' stroke='var(--stat-blue)' "
        f"stroke-width='1.6' stroke-linejoin='round' stroke-linecap='round' "
        f"stroke-dasharray='4 3'/>"
        f"{end_dots}")

    try:
        casualties = float(state.total_casualties or 0)
    except (TypeError, ValueError):
        casualties = 0.0
    if casualties > 999999999:
        casualty_str = "999,999,999+"
    else:
        casualty_str = f"{int(casualties):,}"

    legend = (
        "<div class='evo-legend'>"
        "<span><span class='legend-swatch' style='background:var(--casualty)'></span>"
        "伤亡统计</span>"
        "<span><span class='legend-swatch' style='background:var(--stat-blue)'></span>"
        "活动强度</span></div>")

    svg = (f"<svg viewBox='0 0 {w:g} {h:g}' role='img' aria-label='伤亡与步进演化统计图'>"
           f"{grid}{right_axis}{x_labels}{axis_lines}{series}</svg>")
    head_html = (
        "<div class='evo-head'>"
        "<div class='evo-cas'><span class='stat-name'>☠ 累计伤亡</span>"
        f"<span class='casualty-num'>{casualty_str}</span></div>"
        f"{legend}</div>")
    return f"<div class='evo-chart'>{head_html}{svg}</div>"


def _build_sizes_section(state: CharacterSnapshot) -> str:
    """wiki 式紧凑表格：部位与尺寸一一对应；有解锁情报的部位点击弹出悬浮情报框。"""
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
            name_html = (f"<button type='button' class='size-item has-note' "
                         f"data-note=\"{_esc(note)}\" title=\"查看解锁情报\">{_esc(part)}</button>")
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


def _build_reports_section(report_files: list, show_casualties: bool,
                           lm_info: dict = None) -> str:
    """双栏报告阅读器：左侧时间索引，右侧单篇正文（切换显示，节约纵向空间）。

    `lm_info` 为独特地标 显示名 -> 悬浮文案（地址+耐久）映射，
    正文中的地标名会转为可点击链接。
    """
    if not report_files:
        return "<p class='empty-hint'>暂无报告记录。</p>"
    lm_info = lm_info or {}
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
        body = _render_report_text(text, lm_info)
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
        steps = sum(1 for e in entries
                    if isinstance(e, dict) and not e.get("kind"))
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


def _build_bio_section(state: CharacterSnapshot,
                       gallery_html: str = "", gallery_count: int = 0) -> str:
    """wiki 式人物简介栏：以连贯文本讲述个人简介、生成信息与达成的重要事件。

    末尾附“内部设定”折叠卡（隐藏简介）与人物形象图，仅当有内容时渲染。
    """
    name_part = _esc(state.name)
    if state.nick:
        name_part += f"（通称「{_esc(state.nick)}」）"
    paras = []

    if state.intro_visible:
        paras.append(f"<p class='bio-p'>{_esc(state.intro_visible)}</p>")

    created = _esc(str(state.created_at)[:19].replace("T", " "))
    updated = _esc(str(state.updated_at)[:19].replace("T", " "))
    info_bits = [
        f"{name_part}的档案生成于 <span class='bio-date'>{created}</span>，"
        f"最近更新于 <span class='bio-date'>{updated}</span>。"]
    tags = [t for t in (state.selected_tags or []) if str(t).strip()]
    if tags:
        info_bits.append("角色倾向标签：" + "、".join(f"「{_esc(t)}」" for t in tags) + "。")
    info_bits.append(f"基准身高为 {_esc(format_size(state.height))}。")
    p = state.personality
    if p is not None:
        seg = f"性格表现为「{_esc(p.name)}」"
        if p.description:
            desc = _esc(p.description).rstrip("。")
            seg += f"——{desc}"
        info_bits.append(seg + "。")
    paras.append("<p class='bio-p'>" + "".join(info_bits) + "</p>")

    endings = state.achieved_endings or []
    if endings:
        parts = [f"在已记录的探索历程中，{name_part}共达成 {len(endings)} 起重要事件："]
        for e in endings:
            ename = _esc(e.get("name", "未命名事件"))
            etext = _esc(e.get("ending_text", "")).rstrip("。")
            at = _esc(str(e.get("achieved_at", ""))[:19].replace("T", " "))
            sentence = (f"<span class='bio-date'>{at}</span>，"
                        f"达成<strong class='bio-event'>「{ename}」</strong>")
            if etext:
                sentence += f"——{etext}"
            parts.append(sentence + "。")
        paras.append("<p class='bio-p'>" + "".join(parts) + "</p>")
    else:
        paras.append(
            f"<p class='bio-p'>截至最近一次更新，{name_part}尚未达成任何重要事件。</p>")

    if state.intro_hidden:
        paras.append(
            "<details class='record-card secret bio-secret'><summary>"
            "<span class='file-icon'>🔒</span>内部设定（隐藏简介）</summary>"
            f"<div class='intro-text bio-secret-text'>{_esc(state.intro_hidden)}</div></details>"
        )

    gallery_html = (gallery_html or "").strip()
    if gallery_html and "empty-hint" not in gallery_html:
        paras.append(f"<h3 class='bio-gal-title'>形象图 · 共 {gallery_count} 张</h3>"
                     f"<div class='gallery'>{gallery_html}</div>")
    return "\n".join(paras)



# -----------------------------------------------------------------
# 档案模板（框架部分）
#
# HTML 档案的静态框架——骨架、样式与脚本——分别放在 assets/templates/ 下的
# archive_template.html / .css / .js 模板文件中，导出时读取并内嵌进
# 单文件 HTML；本文件只负责用角色数据填充动态内容，不改动框架结构。
# 模板文件缺失时回退为空串，仅提示一次，避免渲染不可用。
# -----------------------------------------------------------------

_template_cache = {}
_template_missing_logged = set()


def _load_template(name: str) -> str:
    """读取 assets/templates/archive_template.{html,css,js}，带进程内缓存；失败回退空串。"""
    cached = _template_cache.get(name)
    if cached is not None:
        return cached
    text = ""
    path = os.path.join(template_dir(), f"archive_template.{name}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        if name not in _template_missing_logged:
            print(f"[ArchiveExport] 模板读取失败 {path}: {e}")
            _template_missing_logged.add(name)
    _template_cache[name] = text
    return text


def _archive_css() -> str:
    return _load_template("css")


def _archive_js() -> str:
    return _load_template("js")


def _archive_html() -> str:
    return _load_template("html")


def _render_template(**ctx: str) -> str:
    """把 HTML 骨架模板里的 $占位符 替换为传入的片段。

    用 string.Template 而非 str.format，因为 CSS 内含大量花括号，
    format 的 {} 语法会与之冲突；模板内使用 $name 变量引用。
    """
    return string.Template(_archive_html()).substitute(**ctx)


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

    bio_html = _build_bio_section(state, gallery_html, len(avatar_files))

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    nav_items = [("analysis", "灾害分析"), ("bio", "人物简介"),
                 ("sizes", "物理信息")]
    if report_files:
        nav_items.append(("reports", f"测量报告（{len(report_files)}）"))
    if replay_files:
        nav_items.append(("replays", f"事件记录（{len(replay_files)}）"))
    nav_html = "".join(f"<a href='#{anchor}'>{label}</a>" for anchor, label in nav_items)

    doc = _render_template(
        page_title=_esc(state.name),
        rank_tier=_rank_tier(state.height),
        css=_archive_css(),
        js=_archive_js(),
        nav=nav_html,
        hero_name=_esc(state.name),
        infobox=_build_infobox(state, img_srcs, latest_date),
        analysis=_build_analysis_section(state),
        # “显示伤亡统计”关闭时，灾害分析栏默认隐藏（可经顶部导航再次打开）
        analysis_hidden="" if show_casualties else " sec-hidden",
        bio=bio_html,
        base_height=_esc(format_size(state.height)),
        sizes=_build_sizes_section(state),
        reports=_build_reports_section(
            report_files, show_casualties,
            # 兼容旧构造路径：快照对象可能没有地标字段（AttributeError）
            _landmark_popup_info(getattr(state, "landmark_durability", None) or {},
                                 getattr(state, "landmark_addresses", None) or {})),
        replays=_build_replays_section(replay_files),
        exported_at=now_str,
    )

    os.makedirs(os.path.dirname(os.path.abspath(file_path)) or ".", exist_ok=True)
    with open(file_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(doc)
    return file_path
