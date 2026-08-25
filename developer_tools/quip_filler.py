#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
from ui.common import fonts as ui_fonts
描述填充开发者工具
- 复用 QuipRepo / AI 客户端 / QuipCardManager（"描述管理"面板，支持查看/编辑/移动/复制等操作）
- 顶部两个面板可切换（默认显示"描述管理"）：
    * 描述管理：选择 风格 / 体型 / 介入度 / 破坏性，并可浏览、编辑、移动、复制既有条目
    * 填充工具：读取描述管理当前选中的目标坐标，配合风格定义/场景提示调用 AI 快速填充
- 将"体型与互动尺度（含适合/不适合案例）、类型标记、风格定义、场景提示"拼入 AI 提示词
- AI 一次返回 3 条候选描述（text + summary），
  在简化初审对话框中逐条快速编辑并保存（保存到当前 介入度/破坏性 坐标）
- 填充工具顶部提供 Temperature 输入框，可调节生成随机性

用法：在项目根目录执行
    python -m developer_tools.quip_filler
（脚本内已自动把项目根目录加入 sys.path，直接运行脚本亦可。）
"""

import base64
import io
import json
import os
import re
import sys
import tkinter as tk
from tkinter import filedialog

from PIL import Image as PILImage

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

import customtkinter as ctk

import ui.common.dialogs
from ai import get_ai_client as _get_ai_client_global
from persistence import QuipRepo, SettingsRepo
from persistence.quip_repo import DEFAULT_QUIP_STYLE
from ui.common.dialogs import BaseDialog
from ui.quip_mgr import QuipCardManager
from ui.common import fonts as ui_fonts

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "desc_filler_config.json")

SIZE_CATEGORIES = ["small", "medium", "large", "huge", "colossal"]
SIZE_DISPLAY = {
    "small": "7.5~50m", "medium": "50~300m",
    "large": "300~1800m", "huge": "1800~10000m",
    "colossal": "10~150km",
}

# 体型与互动尺度（依据 Events.json 各体型条目与体型区间归纳，硬编码进提示词）
# 每个体型给出准确描述 + 适合的互动案例 + 不适合（尺寸过大或过小）的互动案例
SIZE_DEFINITIONS = {
    "small": {
        "desc": "她的高度近似于中低层居民楼，但仍低于部分高楼。步幅足以跨过道路，坐下可覆盖公园草坪等区域；可以随意触动路标、广告牌等设施",
        "suitable": "躯干与单栋建筑、小型社区、口袋公园、公路、路口互动；手指拨弄天线与招牌。",
        "unsuitable": "不适合用手托起整栋摩天大楼、把体育场当坐垫（尺寸不够大）。",
    },
    "medium": {
        "desc": "她高于市内几乎所有建筑；身体范围可以涵盖社区，并在街区产生尺度压迫感。",
        "suitable": "躯干与社区、大楼、体育场、港口、中型公园互动。",
        "unsuitable": "不适合拨弄屋顶天线、触碰招牌等小设施（尺寸过大）；也不适合把整座大城市当舞台、伸手够到山峦（尺寸仍不够大）。",
    },
    "large": {
        "desc": "全市最高的摩天大楼也不及她的腰部；手掌能覆盖学校、商场等大面积建筑，整个港口或街区可以被一些身体姿势包裹。",
        "suitable": "适合：躯干与城区、港口、摩天大楼互动；手掌托起学校/商场等整栋建筑、身体部位之间夹住街区。",
        "unsuitable": "不适合在单条街道上精耕细作、手指拨弄小招牌（尺寸过大）；也不适合把数座城市同时踩在脚下、伸手触及山脉（尺寸不够大）。",
    },
    "huge": {
        "desc": "她以公里计的高度俯视一切，大部分摩天大楼也仅到小腿高度，一步可以跨过小镇、河流等。",
        "suitable": "适合：在中大城市活动、躯干与少数地标互动；脚掌踩瘪街区、手掌碾碎摩天大楼。",
        "unsuitable": "不适合在单栋建筑或街区内精细互动（尺寸过大）；也不适合把整个国家、山脉群或海洋当床（尺寸仍不够大）。",
    },
    "colossal": {
        "desc": "她的身躯以数十上百公里计，一座城市不过是她身边的玩物，甚至可以颠覆局部大气层。",
        "suitable": "适合：躯干与城市及城市群、山脉、盆地、海域互动；手指毫不费力地抹平数个街道、脚掌覆盖县城、冲击波及方圆百里。",
        "unsuitable": "不适合在单个中小型城市、街区或单栋建筑尺度上互动（尺寸过大）；也不适合涉及国家、大陆，以及更大尺度（尺寸过小）。",
    },
}

BUILTIN_TYPES = {
    "a": ("衣着", ["裙子", "制服", "夏季", "冬季"]),
    "b": ("姿势", ["站立", "坐下", "躺下", "蹲跪"]),
}

# 介入度 / 破坏性分级定义（仍作为坐标背景写入提示词，供 AI 把握尺度，但不再要求输出）
INTRUSION_LEVELS = {
    1: "被动存在：她仅仅作为庞大存在，被在意的是影响而非她本身（如被新闻报告、被科学解析、被远远注视）。描述着重于轮廓、精神气质、社会影响，不涉及具体的少女形象",
    2: "无意的日常动作：她的行走、跳跃、转身、撩发、坐下、躺下等普通日常动作被观察。描述着重于少女的神态、动作，不涉及身体细节。",
    3: "主动的城市互动：人们被她身体与城市设施的互动所冲击（用手脚包裹地标、挑逗人类等）。描述着重于身体细节、精神改变，但保持基本克制。",
    4: "占据与玩弄：她已经暴露贪婪或色情的一面，以身体占据、封锁或玩弄城市，建筑与人群彻底沦为玩物。描述完全表现这种少女私密身体的支配。",
}

DESTRUCTION_LEVELS = {
    1: "避免破坏：她小心地对待身边一切，但客观上可能有无人员伤亡的环境改变，或感官上的压迫。",
    2: "可能破坏：她采取无所谓态度，活动近似略谨慎的一般少女，可能导致结构受损或人员伤亡。",
    3: "尝试破坏：她带有明确目的的动作导致建筑结构性受损或变形、街区或城市被大面积波及。",
    4: "享受毁灭：她以身体接触和摧毁为唯一目的，短时间便使建筑结构化为废墟或齑粉，甚至街区或城市被完全抹除。",
}

SYSTEM_TEMPLATE = """你是一位"巨女"（巨型少女）题材的事件描述撰写助手，负责为事件描述数据库填充高质量条目。

## 介入度（Intrusion）
介入度描述巨型少女对人类环境的客观介入程度及报道口径，共 4 级：
{intrusion_block}

## 破坏性（Destruction）
破坏性描述少女对物理破坏的主观想法和客观后果程度，共 4 级：
{destruction_block}

## 体型与互动尺度（必须严格遵守适合/不适合范围）
{size_block}

## 类型标记
描述文本使用方括号标记嵌入"类型"内容，格式为 [字母:序号:内容]，序号从 1 开始对应细分列表：
{types_block}
（标记内容由你按场景自由填写，例如 [c:1:少女]只是静静站着、人们仰望[d:2:横贯天际的]曲线、那条[a:1:夏季短裙]随风摆动；若该处应嵌入名字占位符则写 [c:1:{{name}}]。）

称呼占位符：正文中称呼她时可以使用 {{name}}（名字）、{{nick}}（昵称）。类型标记和占位符两端不能有空格。

## 风格定义
{style_definition}

## 场景提示
{scene_prompt}"""


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def load_settings():
    settings = {}
    settings_path = os.path.join(_BASE_DIR, "data", "user", "settings.json")
    if os.path.exists(settings_path):
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                settings = json.load(f)
        except Exception:
            pass
    # Read ai_configs from api_keys.json
    keys_path = os.path.join(_BASE_DIR, "data", "user", "api_keys.json")
    if os.path.exists(keys_path):
        try:
            with open(keys_path, "r", encoding="utf-8") as f:
                settings["ai_configs"] = json.load(f)
        except Exception:
            pass
    return settings


def get_ai_client():
    settings = load_settings()
    return _get_ai_client_global(settings)


def complete(client, messages, temperature=0.8):
    """把流式 AI 调用收集为完整字符串。"""
    chunks = []
    for chunk in client.generate_stream(messages, temperature=temperature):
        if chunk:
            chunks.append(chunk)
    return "".join(chunks)


def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {"style_definition": "", "scene_prompt": ""}


def save_config(config):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def _to_level(value, default):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(4, n))


def _format_size_block():
    """把体型定义（描述 + 适合/不适合案例）排版为提示词文本块。"""
    lines = []
    for k in SIZE_CATEGORIES:
        info = SIZE_DEFINITIONS[k]
        lines.append(f"{k}（{SIZE_DISPLAY.get(k, k)}） - {info['desc']}")
        lines.append(f"  适合的互动：{info['suitable']}")
        lines.append(f"  不适合的互动：{info['unsuitable']}")
    return "\n".join(lines)


def parse_response(response):
    """从 AI 响应中解析描述列表。返回 [{text, summary}, ...]（最多 3 条）。

    无法解析出 JSON 数组（请求被拒 / 拒绝词 / 非预期输出）时，
    返回原始输出作为单条候选，避免解析失败导致生成流程中断。
    """
    text = response.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return [{"text": text, "summary": ""}] if text else []
    raw = text[start:end + 1]
    try:
        data = json.loads(raw)
    except Exception:
        raw = re.sub(r",\s*([}\]])", r"\1", raw)
        try:
            data = json.loads(raw)
        except Exception:
            return [{"text": text, "summary": ""}] if text else []
    items = []
    for d in data:
        if isinstance(d, dict):
            text_val = str(d.get("text", "")).strip()
            if not text_val:
                continue
            items.append({
                "text": text_val,
                "summary": str(d.get("summary", "")).strip(),
            })
        elif isinstance(d, str) and d.strip():
            items.append({"text": d.strip(), "summary": ""})
    if not items:
        return [{"text": text, "summary": ""}] if text else []
    return items[:3]


def build_types_block(quip_repo, style):
    meta = quip_repo.load_meta(style)
    custom_types = meta.get("custom_types", {})
    lines = ["[内置类型]"]
    for letter in ("a", "b"):
        name, subtypes = BUILTIN_TYPES[letter]
        lines.append(f"{letter} {name}: " + "  ".join(f"{i + 1}{s}" for i, s in enumerate(subtypes)))
    lines.append("[自定义类型]")
    for letter in ("c", "d", "e"):
        info = custom_types.get(letter, {})
        name = info.get("name", f"自定义{letter.upper()}")
        subtypes = info.get("subtypes", ["细分1", "细分2", "细分3", "细分4"])
        lines.append(f"{letter} {name}: " + "  ".join(f"{i + 1}{s}" for i, s in enumerate(subtypes)))
    return "\n".join(lines)


def build_messages(quip_repo, style, size, intr, dest, config, image_data_url=None):
    size_display = SIZE_DISPLAY.get(size, size)
    size_info = SIZE_DEFINITIONS.get(size, {})
    scene_prompt = config.get("scene_prompt", "").strip()
    if not scene_prompt:
        scene_prompt = "（请依据参考图片中的场景、构图与氛围进行描述）" if image_data_url else "（未填写，请自行设计合理场景）"
    system_prompt = SYSTEM_TEMPLATE.format(
        intrusion_block="\n".join(f"{k} - {v}" for k, v in INTRUSION_LEVELS.items()),
        destruction_block="\n".join(f"{k} - {v}" for k, v in DESTRUCTION_LEVELS.items()),
        size_block=_format_size_block(),
        types_block=build_types_block(quip_repo, style),
        style_definition=config.get("style_definition", "").strip() or "（未填写，请采用自然、文学化的巨女题材叙事风格）",
        scene_prompt=scene_prompt,
    )
    user_prompt = (
        f"当前目标：体型 {size}（{size_display}），介入度 {intr}，破坏性 {dest}。\n"
        f"【体型 {size}】{size_info.get('desc', size_display)}\n"
        f"【适合的互动】{size_info.get('suitable', '')}\n"
        f"【不适合的互动】{size_info.get('unsuitable', '')}\n"
        f"【介入度 {intr}】{INTRUSION_LEVELS[intr]}\n"
        f"【破坏性 {dest}】{DESTRUCTION_LEVELS[dest]}\n\n"
        f"请围绕上述体型与介入度/破坏性，生成 3 条互不重复的事件描述。要求：\n"
        "1. 每条 60~150 字，中文，场景形容准确、语言有画面感，贴合风格定义与场景提示。\n"
        "2. 互动尺度必须严格符合所选体型：优先采用“适合的互动”，明确避开“不适合的互动”。\n"
        "3. 按需使用类型标记（可嵌入 {name} / {nick} 占位符），标记序号必须符合细分列表。\n"
        "4. 为每条提供不超过 8 个字的简介（summary）。\n"
        "5. 3 条内容应覆盖不同切入点，避免与既有条目雷同。\n"
        "6. 只输出一个 JSON 数组（含 3 个对象），每个对象格式：\n"
        '   {"text": "描述正文", "summary": "简介"}\n'
        "不要输出任何多余文字、解释或代码块标记。"
    )
    user_message = {"role": "user", "content": user_prompt}
    if image_data_url:
        user_message["content"] = [
            {"type": "text", "text": user_prompt},
            {"type": "image_url", "image_url": {"url": image_data_url}},
        ]
    return [
        {"role": "system", "content": system_prompt},
        user_message,
    ]


def image_to_data_url(path, max_side=1024):
    """把本地图片编码为 data URL（供 ChatGPT 视觉输入）。大图按最长边缩放到 max_side。"""
    mime_map = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    }
    ext = os.path.splitext(path)[1].lower()
    mime = mime_map.get(ext, "image/png")
    img = PILImage.open(path)
    img.load()
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
    else:
        img = img.convert("RGB")
    if max(img.size) > max_side:
        ratio = max_side / float(max(img.size))
        img = img.resize((max(1, int(img.width * ratio)), max(1, int(img.height * ratio))),
                         PILImage.Resampling.LANCZOS)
    buf = io.BytesIO()
    save_format = {"image/png": "PNG", "image/jpeg": "JPEG", "image/gif": "GIF",
                   "image/webp": "WEBP", "image/bmp": "BMP"}.get(mime, "PNG")
    img.save(buf, format=save_format)
    return f"data:{mime};base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"


# ---------------------------------------------------------------------------
# 初审对话框：无超文本，快速逐条编辑 + 保存（介入度/破坏性由主界面坐标决定）
# ---------------------------------------------------------------------------

class ReviewDialog(BaseDialog):
    def __init__(self, parent, entries, quip_repo, style, size, intrusion, destruction):
        super().__init__(parent)
        self.title("描述初审")
        self.entries = entries
        self.quip_repo = quip_repo
        self.style = style
        self.size = size
        self.intrusion = intrusion
        self.destruction = destruction
        self._cards = []
        self.geometry("860x700")
        self._build_ui()
        self._show_modal()

    def _build_ui(self):
        hint = ctk.CTkLabel(
            self,
            text=("AI 返回的候选描述：逐条编辑后点击“保存”，将快速填充到 "
                  f"{self.size}（介入 {self.intrusion} / 破坏 {self.destruction}）坐标。\n"
                  "精细编辑、移动或复制可在主界面切换到“描述管理”面板完成。"),
            font=ui_fonts.ui_font(12),
            text_color=("#8D6E63", "#BCAAA4"),
            justify="left",
        )
        hint.pack(padx=12, pady=(10, 4), anchor="w")

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll.pack(fill="both", expand=True, padx=10, pady=4)

        for i, entry in enumerate(self.entries):
            frame, card = self._build_card(i, entry)
            frame.pack(fill="x", padx=4, pady=5)
            self._cards.append(card)

        ctk.CTkButton(self, text="关闭", width=100, command=self._close).pack(pady=8)

    def _build_card(self, idx, entry):
        frame = ctk.CTkFrame(self.scroll, border_width=1, corner_radius=8)

        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.pack(fill="x", padx=10, pady=(6, 0))
        ctk.CTkLabel(header, text=f"描述 #{idx + 1}", font=ui_fonts.ui_font(13, "bold")).pack(side="left")

        status = ctk.CTkLabel(header, text="未保存", font=ui_fonts.ui_font(12),
                              text_color=("#8D6E63", "#BCAAA4"))
        status.pack(side="right")

        summary_row = ctk.CTkFrame(frame, fg_color="transparent")
        summary_row.pack(fill="x", padx=10, pady=(4, 0))
        ctk.CTkLabel(summary_row, text="简介:", font=ui_fonts.ui_font(12)).pack(side="left")
        summary_var = tk.StringVar(value=entry.get("summary", ""))
        ctk.CTkEntry(summary_row, textvariable=summary_var, width=520,
                     font=ui_fonts.ui_font(12)).pack(side="left", padx=6, fill="x", expand=True)

        textbox = ctk.CTkTextbox(frame, wrap="word", height=104, font=ui_fonts.ui_font(12))
        textbox.pack(fill="x", padx=10, pady=6)
        textbox.insert("1.0", entry.get("text", ""))

        btn_row = ctk.CTkFrame(frame, fg_color="transparent")
        btn_row.pack(fill="x", padx=10, pady=(0, 8))
        save_btn = ctk.CTkButton(btn_row, text="保存", width=80,
                                 fg_color=("#2E7D32", "#81C784"), text_color="#FFFFFF",
                                 hover_color=("#1B5E20", "#66BB6A"),
                                 command=lambda i=idx: self._on_save(i))
        save_btn.pack(side="left")

        card = {
            "textbox": textbox, "summary_var": summary_var,
            "status": status,
        }
        return frame, card

    # ---------- 保存逻辑 ----------

    @staticmethod
    def _full_text(entry):
        clean = entry["text"].strip()
        summary = entry.get("summary", "").strip()
        return f"{clean}[summary:{summary}]" if summary else clean

    def _persist(self, entry):
        clean = entry["text"].strip()
        if not clean:
            return None
        full = self._full_text(entry)
        quips = self.quip_repo.load(self.style)
        old = entry.get("saved_text")
        if old:
            for matrix in quips.values():
                for lst in matrix.values():
                    lst[:] = [q for q in lst if q.get("text") != old]
        matrix = quips.setdefault(self.size, {})
        lst = matrix.setdefault((self.intrusion, self.destruction), [])
        if any(q.get("text") == full for q in lst):
            return "dup"
        lst.append({"text": full, "style": self.style,
                    "step": QuipRepo.default_step(self.intrusion, self.destruction)})
        self.quip_repo.save(self.style, quips)
        entry["saved_text"] = full
        return "ok"

    def _on_save(self, idx):
        card = self._cards[idx]
        entry = self.entries[idx]
        entry["text"] = card["textbox"].get("1.0", "end-1c").strip()
        entry["summary"] = card["summary_var"].get().strip()
        if not entry["text"]:
            ui.common.dialogs.showwarning("警告", f"第 {idx + 1} 条描述为空，无法保存。", parent=self)
            return
        result = self._persist(entry)
        if result == "dup":
            if not entry.get("saved_text"):
                entry["saved_text"] = self._full_text(entry)
            ui.common.dialogs.showinfo("提示", f"第 {idx + 1} 条描述已存在于该坐标，未重复保存。", parent=self)
        elif result == "ok":
            ui.common.dialogs.showinfo("成功",
                f"第 {idx + 1} 条描述已保存至 {self.size}（介入{self.intrusion}/破坏{self.destruction}）。",
                parent=self)
        self._update_card_state(idx)

    def _update_card_state(self, idx):
        entry = self.entries[idx]
        card = self._cards[idx]
        if entry.get("saved_text"):
            card["status"].configure(text="已保存", text_color=("#2E7D32", "#81C784"))
        else:
            card["status"].configure(text="未保存", text_color=("#8D6E63", "#BCAAA4"))


class TargetDialog(BaseDialog):
    """复制/移动描述时的目标坐标选择对话框（供 QuipCardManager 的 gui_ref 使用）。"""

    def __init__(self, parent, src_intr, src_dest, src_size):
        super().__init__(parent)
        self.title("选择目标位置")
        self.result = None
        self.geometry("360x230")

        self.size_var = tk.StringVar(value=src_size)
        self.intr_var = tk.StringVar(value=str(src_intr))
        self.dest_var = tk.StringVar(value=str(src_dest))

        form = ctk.CTkFrame(self, fg_color="transparent")
        form.pack(fill="x", padx=20, pady=(20, 6))

        ctk.CTkLabel(form, text="目标体型:", font=ui_fonts.ui_font(12)).grid(row=0, column=0, pady=6, sticky="e")
        ctk.CTkOptionMenu(form, variable=self.size_var, values=SIZE_CATEGORIES, width=120,
                          font=ui_fonts.ui_font(12)).grid(row=0, column=1, pady=6, padx=8)

        ctk.CTkLabel(form, text="介入度:", font=ui_fonts.ui_font(12)).grid(row=1, column=0, pady=6, sticky="e")
        ctk.CTkOptionMenu(form, variable=self.intr_var, values=["1", "2", "3", "4"], width=60,
                          font=ui_fonts.ui_font(12)).grid(row=1, column=1, pady=6, padx=8, sticky="w")

        ctk.CTkLabel(form, text="破坏性:", font=ui_fonts.ui_font(12)).grid(row=2, column=0, pady=6, sticky="e")
        ctk.CTkOptionMenu(form, variable=self.dest_var, values=["1", "2", "3", "4"], width=60,
                          font=ui_fonts.ui_font(12)).grid(row=2, column=1, pady=6, padx=8, sticky="w")

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(pady=(6, 16))
        ctk.CTkButton(btn_row, text="确定", width=90, command=self._ok).pack(side="left", padx=8)
        ctk.CTkButton(btn_row, text="取消", width=90, command=self._close).pack(side="left", padx=8)

        self._show_modal()

    def _ok(self):
        self.result = (
            self.size_var.get(),
            _to_level(self.intr_var.get(), 1),
            _to_level(self.dest_var.get(), 1),
        )
        self._close()


class PromptPreviewDialog(BaseDialog):
    def __init__(self, parent, messages):
        super().__init__(parent)
        self.title("提示词预览")
        self.geometry("820x680")
        box = ctk.CTkTextbox(self, wrap="word", font=("Consolas", 12))
        box.pack(fill="both", expand=True, padx=12, pady=(12, 6))
        for msg in messages:
            role = "SYSTEM" if msg["role"] == "system" else "USER"
            content = msg["content"]
            if isinstance(content, list):
                text = "".join(
                    p.get("text", "") for p in content
                    if isinstance(p, dict) and p.get("type") == "text")
                has_img = any(
                    isinstance(p, dict) and p.get("type") == "image_url" for p in content)
                extra = "\n[附加图片场景提示]" if has_img else ""
                box.insert("end", f"========== {role} ==========\n{text}{extra}\n\n")
            else:
                box.insert("end", f"========== {role} ==========\n{content}\n\n")
        box.configure(state="disabled")
        ctk.CTkButton(self, text="关闭", width=100, command=self._close).pack(pady=8)
        self._show_modal()


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------

class QuipFillerApp:
    def __init__(self, root):
        self.root = root
        root.title("描述填充工具（开发者）")
        root.geometry("1120x760")
        root.minsize(820, 680)

        self.quip_repo = QuipRepo(data_dir=os.path.join(_BASE_DIR, "data"))
        self.settings_repo = SettingsRepo(data_dir=os.path.join(_BASE_DIR, "data"))
        self.client, self.provider_name = get_ai_client()
        self.provider = getattr(self.client, "provider", "unknown") if self.client else None
        self.config = load_config()
        self.scene_image_path = None
        self.scene_image_tk = None

        styles = self.quip_repo.get_styles()
        current_style = styles[0] if styles else DEFAULT_QUIP_STYLE

        # 作为 QuipCardManager 的 gui_ref 所需的属性
        self.current_quip_style = current_style

        self.style_var = tk.StringVar(value=current_style)
        self.size_var = tk.StringVar(value="small")
        self.intrusion_var = tk.StringVar(value="1")
        self.destruction_var = tk.StringVar(value="1")
        self.temperature_var = tk.StringVar(value="0.9")

        self._setup_panels()
        self._build_ui()
        self._build_manager()
        self._refresh_provider_status()
        self.on_panel_switch("描述管理")

    # ---------- 面板切换 ----------

    def _setup_panels(self):
        self.switch = ctk.CTkSegmentedButton(
            self.root,
            values=["描述管理", "填充工具"],
            command=self.on_panel_switch,
            fg_color=("#D7CCC8", "#424242"),
            selected_color=("#A1887F", "#D7CCC8"),
            selected_hover_color=("#8D6E63", "#BCAAA4"),
            unselected_color=("#D7CCC8", "#424242"),
            unselected_hover_color=("#EFEBE9", "#2D2D2D"),
            text_color=("#3E2723", "#E0E0E0"),
            font=ui_fonts.ui_font(12),
        )
        self.switch.pack(fill="x", padx=12, pady=(8, 0))
        self.switch.set("描述管理")

        self.content = ctk.CTkFrame(self.root, fg_color="transparent")
        self.content.pack(fill="both", expand=True)

        self.filler_frame = ctk.CTkFrame(self.content, fg_color="transparent")
        self.filler_frame.pack_forget()

        self.manager_frame = ctk.CTkFrame(self.content, fg_color="transparent")
        self.manager_frame.pack(fill="both", expand=True)

        self.status_bar = ctk.CTkLabel(self.root, text="就绪", anchor="w",
                                       font=ui_fonts.ui_font(12), height=24,
                                       text_color=("#5D4037", "#E0E0E0"))
        self.status_bar.pack(fill="x", side="bottom", padx=12, pady=(2, 8))

    def on_panel_switch(self, value):
        if value == "描述管理":
            self._sync_to_manager()
            self.filler_frame.pack_forget()
            self.manager_frame.pack(fill="both", expand=True)
            if hasattr(self, "quip_mgr"):
                self.quip_mgr.refresh_ui()
        else:
            self._sync_from_manager()
            self.manager_frame.pack_forget()
            self.filler_frame.pack(fill="both", expand=True)

    def _sync_from_manager(self):
        """切换到填充工具时，读取描述管理面板当前选中的风格/体型/介入度/破坏性。"""
        if not hasattr(self, "quip_mgr"):
            return
        self.style_var.set(self.quip_mgr.style_var.get())
        self.size_var.set(self.quip_mgr.size_var.get())
        self.intrusion_var.set(str(self.quip_mgr.current_intrusion))
        self.destruction_var.set(str(self.quip_mgr.current_destruction))
        self._update_target_label()

    def _update_target_label(self):
        """刷新填充工具顶部的目标坐标提示（源自描述管理面板）。"""
        if not hasattr(self, "target_label"):
            return
        size = self.size_var.get()
        size_display = SIZE_DISPLAY.get(size, size)
        self.target_label.configure(
            text=f"目标：{self.style_var.get()} / {size}（{size_display}）/ "
                 f"介入 {_to_level(self.intrusion_var.get(), 1)} / "
                 f"破坏 {_to_level(self.destruction_var.get(), 1)}")

    def _sync_to_manager(self):
        """切换到描述管理时，将填充工具当前的风格/体型同步给描述管理面板。"""
        if not hasattr(self, "quip_mgr"):
            return
        self.current_quip_style = self.style_var.get()
        self.quip_mgr.style_var.set(self.style_var.get())
        self.quip_mgr.size_var.set(self.size_var.get())

    def _build_manager(self):
        self.quip_mgr = QuipCardManager(
            self.manager_frame, self.quip_repo, self.settings_repo, self)
        self.quip_mgr.pack(fill="both", expand=True)
        # 隐藏 QuipCardManager 内部的模块切换按钮（由顶部面板切换统一管理）
        self.quip_mgr.switch_btn.pack_forget()

    # ---------- gui_ref 适配 ----------

    def load_combined_quips(self):
        if hasattr(self, "quip_mgr"):
            self.quip_mgr.refresh_ui()

    def show_landmark_manager(self):
        self.switch.set("填充工具")
        self.on_panel_switch("填充工具")

    def _select_target_dialog(self, src_intr, src_dest, src_size):
        dlg = TargetDialog(self.root, src_intr, src_dest, src_size)
        return dlg.result

    # ---------- 填充工具界面 ----------

    def _build_ui(self):
        parent = self.filler_frame

        top = ctk.CTkFrame(parent, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(10, 2))
        self.provider_label = ctk.CTkLabel(top, text="", font=ui_fonts.ui_font(12),
                                           text_color=("#8D6E63", "#BCAAA4"))
        self.provider_label.pack(anchor="w")

        cfg = ctk.CTkFrame(parent, fg_color="transparent")
        cfg.pack(fill="x", padx=12, pady=(2, 6))

        # 风格/体型/介入度/破坏性 在"描述管理"面板中设置，此处仅显示当前目标
        self.target_label = ctk.CTkLabel(cfg, text="", font=ui_fonts.ui_font(13, "bold"),
                                         text_color=("#5D4037", "#E0E0E0"))
        self.target_label.pack(side="left", padx=(0, 16))

        ctk.CTkLabel(cfg, text="温度:", font=ui_fonts.ui_font(12)).pack(side="left")
        self.temperature_entry = ctk.CTkEntry(cfg, textvariable=self.temperature_var, width=70,
                                              font=ui_fonts.ui_font(12))
        self.temperature_entry.pack(side="left", padx=(4, 16))

        ctk.CTkButton(cfg, text="查看提示词", width=104, font=ui_fonts.ui_font(12),
                      fg_color="transparent", border_width=1,
                      text_color=("#5D4037", "#E0E0E0"),
                      hover_color=("#EFEBE9", "#2D2D2D"),
                      border_color=("#D7CCC8", "#424242"),
                      command=self.show_prompt_preview).pack(side="left", padx=4)
        ctk.CTkButton(cfg, text="生成描述", width=112, font=ui_fonts.ui_font(13, "bold"),
                      fg_color=("#2E7D32", "#81C784"), text_color="#FFFFFF",
                      hover_color=("#1B5E20", "#66BB6A"),
                      command=self.generate).pack(side="left", padx=4)

        self._update_target_label()

        sdef = ctk.CTkFrame(parent, border_width=1, corner_radius=10)
        sdef.pack(fill="both", expand=True, padx=12, pady=6)
        sdef_hdr = ctk.CTkFrame(sdef, fg_color="transparent")
        sdef_hdr.pack(fill="x", padx=10, pady=(6, 0))
        ctk.CTkLabel(sdef_hdr, text="风格定义（写入 AI 提示词，保存到同目录 JSON）",
                     font=ui_fonts.ui_font(13, "bold")).pack(side="left")
        ctk.CTkButton(sdef_hdr, text="保存", width=70, height=26, font=ui_fonts.ui_font(12),
                      fg_color="transparent", border_width=1,
                      text_color=("#2E7D32", "#81C784"),
                      border_color=("#2E7D32", "#81C784"),
                      command=self.save_style_definition).pack(side="right")
        self.style_def_box = ctk.CTkTextbox(sdef, wrap="word", font=ui_fonts.ui_font(12))
        self.style_def_box.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        self.style_def_box.insert("1.0", self.config.get("style_definition", ""))

        scen = ctk.CTkFrame(parent, border_width=1, corner_radius=10)
        scen.pack(fill="both", expand=True, padx=12, pady=6)
        scen_hdr = ctk.CTkFrame(scen, fg_color="transparent")
        scen_hdr.pack(fill="x", padx=10, pady=(6, 0))
        ctk.CTkLabel(scen_hdr, text="场景提示（手动输入 / 可附加参考图片）",
                     font=ui_fonts.ui_font(13, "bold")).pack(side="left")
        ctk.CTkButton(scen_hdr, text="选择图片", width=70, height=26, font=ui_fonts.ui_font(12),
                      fg_color="transparent", border_width=1,
                      text_color=("#5D4037", "#E0E0E0"),
                      hover_color=("#EFEBE9", "#2D2D2D"),
                      border_color=("#D7CCC8", "#424242"),
                      command=self.select_scene_image).pack(side="right", padx=(0, 6))
        ctk.CTkButton(scen_hdr, text="保存", width=70, height=26, font=ui_fonts.ui_font(12),
                      fg_color="transparent", border_width=1,
                      text_color=("#2E7D32", "#81C784"),
                      border_color=("#2E7D32", "#81C784"),
                      command=self.save_scene_prompt).pack(side="right")

        self.scene_image_row = ctk.CTkFrame(scen, fg_color="transparent")
        self.scene_image_row.pack(fill="x", padx=10, pady=(2, 0))
        self.scene_image_thumb = ctk.CTkLabel(self.scene_image_row, text="",
                                              font=ui_fonts.ui_font(11))
        self.scene_image_thumb.pack(side="left")
        self.scene_image_name = ctk.CTkLabel(self.scene_image_row, text="未选择参考图片（仅 ChatGPT 支持）",
                                             font=ui_fonts.ui_font(11),
                                             text_color=("#8D6E63", "#BCAAA4"))
        self.scene_image_name.pack(side="left", padx=(8, 8))
        self.scene_image_clear_btn = ctk.CTkButton(
            self.scene_image_row, text="清除图片", width=70, height=24, font=ui_fonts.ui_font(11),
            fg_color="transparent", border_width=1,
            text_color=("#C62828", "#EF9A9A"),
            border_color=("#C62828", "#EF9A9A"),
            command=self.clear_scene_image)
        self.scene_image_clear_btn.pack(side="left")

        self.scene_box = ctk.CTkTextbox(scen, wrap="word", font=ui_fonts.ui_font(12))
        self.scene_box.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        self.scene_box.insert("1.0", self.config.get("scene_prompt", ""))
        self._update_scene_image_preview()

    def _refresh_provider_status(self):
        text = f"已连接 AI：{self.provider_name}" if self.client else "未连接 AI（请先在设置中配置 API Key 后重启）"
        self.provider_label.configure(text=text)

    def set_status(self, text):
        self.status_bar.configure(text=text)
        self.root.update()

    def save_style_definition(self):
        self.config["style_definition"] = self.style_def_box.get("1.0", "end-1c").strip()
        save_config(self.config)
        self.set_status("风格定义已保存到 " + CONFIG_PATH)

    def save_scene_prompt(self):
        self.config["scene_prompt"] = self.scene_box.get("1.0", "end-1c").strip()
        save_config(self.config)
        self.set_status("场景提示已保存到 " + CONFIG_PATH)

    def select_scene_image(self):
        path = filedialog.askopenfilename(
            parent=self.root,
            title="选择场景提示参考图片",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.gif *.webp *.bmp"), ("所有文件", "*.*")])
        if not path:
            return
        if self.provider != "openai" and "chatgpt" not in self.provider_name.lower():
            ui.common.dialogs.showwarning(
                "提示",
                "图片场景提示目前仅支持 ChatGPT。图片仅保存在本次会话中，切换到其他 AI 厂商时将不会被发送。",
                parent=self.root)
        self.scene_image_path = path
        self._update_scene_image_preview()
        self.set_status(f"已选择场景图片：{os.path.basename(path)}（不保存，仅本次生成使用）")

    def clear_scene_image(self):
        self._clear_scene_image_safe()
        self.scene_image_path = None
        self.scene_image_tk = None
        self._update_scene_image_preview()
        self.set_status("已清除场景图片")

    def _clear_scene_image_safe(self):
        """按 CTkImage 生命周期文档安全清除缩略图：先清内部 Tcl Label 的图像引用，
        避免 CTkImage 被 GC 后 tkinter Label 仍持有已销毁的 Tcl 图像名导致 TclError。"""
        try:
            if hasattr(self.scene_image_thumb, '_label'):
                self.scene_image_thumb._label.configure(image="")
            self.scene_image_thumb.configure(image=None)
        except Exception:
            pass

    def _update_scene_image_preview(self):
        if not self.scene_image_path or not os.path.exists(self.scene_image_path):
            self._clear_scene_image_safe()
            self.scene_image_tk = None
            self.scene_image_thumb.configure(text="")
            self.scene_image_name.configure(
                text="未选择参考图片（仅 ChatGPT 支持）",
                text_color=("#8D6E63", "#BCAAA4"))
            self.scene_image_clear_btn.pack_forget()
            return
        try:
            img = PILImage.open(self.scene_image_path)
            img.load()
            img.thumbnail((56, 56))
            photo = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
            self.scene_image_tk = photo
            self.scene_image_thumb.configure(image=photo, text="")
            self.scene_image_name.configure(
                text=os.path.basename(self.scene_image_path),
                text_color=("#5D4037", "#E0E0E0"))
            self.scene_image_clear_btn.pack(side="left")
        except Exception as e:
            self._clear_scene_image_safe()
            self.scene_image_tk = None
            self.scene_image_thumb.configure(text="")
            self.scene_image_name.configure(
                text=f"图片预览失败：{e}",
                text_color=("#C62828", "#EF9A9A"))
            self.scene_image_clear_btn.pack_forget()

    def _sync_config(self):
        self.config["style_definition"] = self.style_def_box.get("1.0", "end-1c").strip()
        self.config["scene_prompt"] = self.scene_box.get("1.0", "end-1c").strip()

    def show_prompt_preview(self):
        self._sync_from_manager()
        self._sync_config()
        image_data_url = self._scene_image_data_url()
        PromptPreviewDialog(self.root, self._build_messages(image_data_url=image_data_url))

    def _build_messages(self, image_data_url=None):
        style = self.style_var.get()
        size = self.size_var.get()
        intr = _to_level(self.intrusion_var.get(), 1)
        dest = _to_level(self.destruction_var.get(), 1)
        return build_messages(self.quip_repo, style, size, intr, dest, self.config,
                              image_data_url=image_data_url)

    def _scene_image_data_url(self):
        """将当前所选场景图片编码为 data URL；仅 ChatGPT 支持时返回。"""
        if not self.scene_image_path or not os.path.exists(self.scene_image_path):
            return None
        if self.provider != "openai" and "chatgpt" not in self.provider_name.lower():
            return None
        try:
            return image_to_data_url(self.scene_image_path)
        except Exception as e:
            ui.common.dialogs.showerror("图片读取失败",
                f"无法读取场景图片：{self.scene_image_path}\n{e}")
            return None

    def generate(self):
        if not self.client:
            ui.common.dialogs.showerror("未连接 AI", "未检测到可用的 AI 客户端，请先在设置中配置 API Key。")
            return
        self._sync_from_manager()
        try:
            temperature = float(self.temperature_var.get())
        except ValueError:
            ui.common.dialogs.showerror("错误", "温度（Temperature）必须是数字。")
            return
        temperature = max(0.0, min(2.0, temperature))
        self._sync_config()
        image_data_url = self._scene_image_data_url()
        if self.scene_image_path and self.provider != "openai" and "chatgpt" not in self.provider_name.lower():
            ui.common.dialogs.showwarning(
                "提示",
                "图片场景提示仅支持 ChatGPT，本次生成将忽略所选图片。",
                parent=self.root)
        default_i = _to_level(self.intrusion_var.get(), 1)
        default_d = _to_level(self.destruction_var.get(), 1)
        self.set_status(f"正在调用 AI 生成 3 条描述（温度 {temperature}），请稍候 ...")
        try:
            response = complete(self.client, self._build_messages(image_data_url=image_data_url),
                                temperature=temperature)
        except Exception as e:
            self.set_status("AI 调用失败")
            ui.common.dialogs.showerror("AI 调用失败", str(e))
            return
        entries = parse_response(response)
        if not entries:
            self.set_status("解析失败")
            ui.common.dialogs.showerror("解析失败",
                "未能从 AI 响应中解析出描述。\n\n原始响应（前 600 字）：\n" + response[:600])
            return
        while len(entries) < 3:
            entries.append({"text": "", "summary": ""})
        self.set_status(f"AI 返回 {len(entries)} 条候选描述，正在打开初审对话框 ...")
        ReviewDialog(self.root, entries, self.quip_repo,
                     self.style_var.get(), self.size_var.get(), default_i, default_d)
        if hasattr(self, "quip_mgr"):
            self.quip_mgr.refresh_ui()
        self.set_status("就绪")


def main():
    settings = load_settings()
    ctk.set_appearance_mode(settings.get("theme_mode", "Light"))
    ctk.set_default_color_theme(settings.get("color_theme", "blue"))
    root = ctk.CTk()
    QuipFillerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
