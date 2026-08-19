import math
import tkinter as tk
from typing import Optional, Dict, List

import customtkinter as ctk

from PIL import Image, ImageDraw

from logic import ALL_PART_NAMES
from models import Personality, BodyPreset
from ui.common.dialogs import BaseDialog
from ui.common import fonts as ui_fonts
from ui.common.widgets import CTkScrollableDropdownFrame, CTkSegmentedControl
from ui.common.theme import (
    PARAMS_STATUS, VAL_DISABLED, VAL_GOLDEN,
    VIEW_PNL_FG, VIEW_PNL_BORDER,
    TEXT, SOFT, TITLE,
    PNL_BG, BORDER_ALT, HOVER_ALT, MENU_HOVER,
    STATUS_ERR, CLEAR_BG, CLEAR_BORDER,
    GOLD_BTN, GOLD_BTN_HOVER,
    PROGRESS_BTN, PROGRESS_BTN_HOVER, SLIDER_TRACK, TEXT_DISABLED,
    VIEW_OUTLINE, VIEW_OUTLINE_DARK, VIEW_HAIR, VIEW_HAIR_SHADE,
    VIEW_SKIN, VIEW_SKIN_SHADE, VIEW_SKIN_LINE, VIEW_CLOTH,
    VIEW_NAVY, VIEW_NAVY_LINE, VIEW_EYE, VIEW_EYE_WHITE, VIEW_EYE_WHITE_LINE,
    VIEW_PUPIL, VIEW_HIGHLIGHT, VIEW_MOUTH, VIEW_BLUSH, VIEW_SHADOW,
    VIEW_CLOTH_DARK, VIEW_CLOTH_DARK_LINE, VIEW_WHITE_LINE, VIEW_SLEEVE, VIEW_STITCH,
)

# 滑块配色（对齐面板滑块条：棕色=正常、金色=随机、灰色=禁用）
_SLIDER_BROWN = dict(
    button_color=PROGRESS_BTN,
    button_hover_color=PROGRESS_BTN_HOVER,
    progress_color=PROGRESS_BTN,
    fg_color=HOVER_ALT,
)
_SLIDER_GOLD = dict(
    button_color=GOLD_BTN,
    button_hover_color=GOLD_BTN_HOVER,
    progress_color=GOLD_BTN,
    fg_color=HOVER_ALT,
)
_SLIDER_GRAY = dict(
    button_color=TEXT_DISABLED,
    button_hover_color=TEXT_DISABLED,
    progress_color=SLIDER_TRACK,
    fg_color=SLIDER_TRACK,
)


def _step_decimals(step: float) -> int:
    """根据滑块步长推断显示/取整的小数位数。"""
    s = f"{step:.10f}".rstrip("0")
    return len(s.split(".", 1)[1]) if "." in s else 0


# ---- 性格形容文案 ----
# 各维度参数阈值表：(下限, 形容文案)，按下限升序；
# 取值时返回最后一个满足 value >= 下限 的文案。
# 介入度 / 破坏性为 0 表示随机演化（对应首项文案）。
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

_SENSITIVITY_BUCKETS = [
    (-3.0, "她对人类的情感可能是保护欲望，但不排除一种格外危险的占有表达。"),
    (-0.5, "她面对怎样的身下环境与人类请求都无动于衷，几乎不会为此改变计划。"),
    (0.5, "忽略完全不对等的交流，可以把她视作一个有正常好奇心与羞耻心的女孩。"),
    (1.5, "人类的拒绝会让患得患失的她强烈退缩，同时却经常有着一不小心有玩过头的模样。"),
]

_STRENGTH_BUCKETS = [
    (0.0, "她的身上几乎看不出个人特质，性格底色淡薄、随波逐流。"),
    (1.0, "习惯与爱好在她生活中的分量很轻，行动与破坏大多中规中矩。"),
    (2.0, "她有着声张或隐藏的个人好恶，在特定时机以巨大的形态表现。"),
    (3.0, "她的个性基本上鲜明稳固，在巨大身躯下也易于见其本色。"),
    (4.0, "她极度沉浸于自身特质，将习惯与爱好作为一切活动的原点。"),
]


def _pick_level(value: float, buckets) -> str:
    """按阈值表下限取文案：返回最后一个 value >= 下限 的形容。"""
    picked = buckets[0][1]
    for lo, text in buckets:
        if value + 1e-9 >= lo:
            picked = text
    return picked


def _trend_phrase(step: float) -> str:
    """根据步长（随时间推进的增减趋势）生成走向短语。"""
    if abs(step) < 0.25:
        return "这成为了巨大娘的常态。"
    if step >= 1.5:
        return "沉溺其中的她一发不可收拾。"
    if step >= 0.75:
        return "她似乎并未放弃更过分的可能。"
    if step > 0:
        return "她与这样的生活长期培养着感情。"
    if step <= -1.5:
        return "这样的尝试很快让她积累了痛苦。"
    if step <= -0.75:
        return "她似乎略厌烦这偏高强度的刺激。"
    if step <= -0.25:
        return "她希望以自己的方式变得“自律”。"
    return ""


def _init_step_phrase(noun: str, init: float, step: float, levels) -> str:
    """由初始值与步长共同生成介入度/破坏性的具体形容。"""
    base = _pick_level(init, levels)
    if init > 0.0:
        return f"{base}{_trend_phrase(step)}"
    else:
        return base


def _personality_summary(values: dict,
                         base_item: Optional[Personality] = None) -> list:
    """生成性格形容行：预设性格首行为其简介，其后按当前参数分述四大维度。

    介入度 / 破坏性由初始值 + 步长共同形容；
    敏感（特殊事件反应）与个性强度（习惯与爱好的分量）各自按当前值形容。
    """
    lines = []
    if base_item is not None and base_item.description:
        lines.append(base_item.description)
    lines.append(f"{_init_step_phrase('介入', values['init_intrusion'],
                                      values['step_intrusion'], _INTRUSION_BUCKETS)}")
    lines.append(f"{_init_step_phrase('破坏', values['init_destruction'],
                                      values['step_destruction'], _DESTRUCTION_BUCKETS)}")
    lines.append(f"{_pick_level(values['sensitivity'], _SENSITIVITY_BUCKETS)}")
    lines.append(f"{_pick_level(values['skip_base_prob'], _STRENGTH_BUCKETS)}")
    return lines


# 暗色模式下身材预览的颜色衰减强度（0~1，越大整体越暗）
_PREVIEW_DARK_DIM = 0.15


def _preview_dim_color(color: str) -> str:
    """暗色模式预览取色：对越亮的通道应用越大的亮度衰减（纯黑不变）。

    返回仍为规范的 #RRGGBB；仅处理 7 位十六进制颜色，其余（"" 等）原样返回。
    """
    if not (isinstance(color, str) and len(color) == 7 and color[0] == "#"):
        return color

    def channel(v: int) -> int:
        t = v / 255.0
        return round(v * (1.0 - _PREVIEW_DARK_DIM * t))

    return "#{:02X}{:02X}{:02X}".format(*(channel(int(color[i:i + 2], 16))
                                          for i in (1, 3, 5)))


class PersonalityCustomDialog(BaseDialog):
    """性格参数对话框：导入下拉框 + 状态标签 + 参数滑块 + 性格形容，底部确定 / 消除。

    下拉框可把滑块设到对应表条目并更新状态标签，但不锁定滑块；
    手动调节任一滑块后状态标签变为“自定义”。
    底部“性格形容”区在选中预设性格时首行显示其简介，
    其后的行分别按当前参数形容介入度、破坏性、敏感、个性强度四大维度。
    初始介入度/破坏性为 0 表示随机演化（相应步长无意义，会被禁用并高亮标注）。
    结果约定：result = ("ok", label, obj) 表示确定；
             ("clear", None, None) 表示消除（回到随机）；
             None 表示直接关闭（无操作）。
    """

    # attr -> (label, min, max, step)
    # 初始介入度 / 初始破坏性的滑块左端为 0.4（步长 0.1），
    # 该 0.4 只是滑块内部状态，语义上直接当作 0（随机演化）；0.5 起为真实低值，
    PARAMS = [
        ("init_intrusion", "初始介入度", 0.4, 4.5, 0.1),
        ("step_intrusion", "介入度步长", -3, 3, 0.1),
        ("init_destruction", "初始破坏性", 0.4, 4.5, 0.1),
        ("step_destruction", "破坏性步长", -3, 3, 0.1),
        ("sensitivity", "敏感值", -3, 3, 0.1),
        ("skip_base_prob", "个性强度", 0, 5, 0.1),
    ]

    _INIT_STEP_PAIRS = {
        "init_intrusion": "step_intrusion",
        "init_destruction": "step_destruction",
    }

    # 性格形容维度行（预设简介之外）所用的斜体字体
    _DESC_ITALIC_FONT = ui_fonts.ui_font(10, "italic")

    def __init__(self, parent, table_items, initial: Optional[Personality] = None):
        super().__init__(parent)
        self.title("性格")
        self.result = None
        self._table_items = table_items or []
        self._sliders: Dict[str, ctk.CTkSlider] = {}
        self._value_labels: Dict[str, ctk.CTkLabel] = {}
        self.geometry("465x450")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._base_item = None
        self._manual = False

        # ---- 顶部：导入下拉框 + 状态标签（与身材对话框一致的布局） ----
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky='ew', padx=(14, 8), pady=(12, 2))
        ctk.CTkLabel(top, text="导入性格", anchor='w', font=self.UI_FONT,
                     text_color=TITLE).pack(side="left")
        self._dropdown_btn = ctk.CTkButton(
            top, text="选择…", width=110, height=26, font=self.UI_FONT,
            fg_color=PNL_BG,
            text_color=TEXT,
            hover_color=HOVER_ALT,
            border_width=1, border_color=BORDER_ALT,
            corner_radius=8,
            state="disabled" if not self._table_items else "normal")
        self._dropdown_btn.pack(side="left", padx=(10, 85))
        self._dropdown = None
        if self._table_items:
            self._dropdown = CTkScrollableDropdownFrame(
                attach=self._dropdown_btn,
                values=[p.name for p in self._table_items],
                command=self._import_from_table,
                height=200, button_height=28,
                fg_color=PNL_BG,
                button_color=PNL_BG,
                hover_color=HOVER_ALT,
                scrollbar_button_color=HOVER_ALT,
                scrollbar_button_hover_color=MENU_HOVER,
                frame_border_color=MENU_HOVER,
                text_color=TEXT,
                frame_border_width=1,
                justify="left")
        ctk.CTkLabel(top, text="状态:", font=self.UI_FONT,
                     text_color=TITLE).pack(side="left", padx=(0, 6))
        self._status_label = ctk.CTkLabel(
            top, text="随机", anchor='w', font=self.UI_FONT_BOLD,
            text_color=PARAMS_STATUS["random"])
        self._status_label.pack(side="left", padx=3)

        # ---- 中间：参数滑块 ----
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky='nsew', padx=14, pady=4)
        for attr, label, lo, hi, step in self.PARAMS:
            row_frame = ctk.CTkFrame(body, fg_color="transparent")
            row_frame.pack(fill='x', pady=3)
            row_frame.columnconfigure(1, weight=1)
            ctk.CTkLabel(row_frame, text=label, width=110, anchor='w',
                         font=self.UI_FONT,
                         text_color=TEXT).grid(row=0, column=0, padx=(0, 6))
            slider = ctk.CTkSlider(
                row_frame, from_=lo, to=hi,
                number_of_steps=int(round((hi - lo) / step)),
                command=lambda _v, a=attr: self._on_param_change(a),
                **_SLIDER_BROWN)
            slider.grid(row=0, column=1, sticky='ew')
            val_label = ctk.CTkLabel(row_frame, text="", width=76, anchor='e',
                                     font=("Consolas", 11),
                                     text_color=SOFT)
            val_label.grid(row=0, column=2, padx=(8, 0))
            self._sliders[attr] = slider
            self._value_labels[attr] = val_label

        # ---- 性格形容（预设首行为其简介，其后按当前参数分述四大维度） ----
        summary_frame = ctk.CTkFrame(self, fg_color="transparent")
        summary_frame.grid(row=2, column=0, sticky='ew', padx=14, pady=0)
        self._summary_lines = []
        for _i in range(5):
            lbl = ctk.CTkLabel(summary_frame, text="", anchor='w', justify="left",
                               font=self.UI_FONT, wraplength=470, text_color=TEXT)
            lbl.pack(fill='x', pady=0)
            self._summary_lines.append(lbl)

        # ---- 底部：确定 / 消除 ----
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=3, column=0, pady=10)
        ctk.CTkButton(btn_frame, text="确定", width=88, height=28, font=self.UI_FONT,
                      command=self._ok).pack(side='left', padx=7)
        ctk.CTkButton(btn_frame, text="消除", width=88, height=28, font=self.UI_FONT,
                      fg_color="transparent", text_color=STATUS_ERR,
                      hover_color=CLEAR_BG,
                      border_width=1, border_color=CLEAR_BORDER,
                      command=self._clear).pack(side='left', padx=7)

        # ---- 状态初始化（默认取下拉框第一项） ----
        if initial is None:
            if self._table_items:
                self._apply_table_item(self._table_items[0])
            else:
                for attr, _label, lo, _hi, _step in self.PARAMS:
                    self._sliders[attr].set(lo)
                self._refresh_value_labels()
                self._update_special()
                self._set_status("随机", base_item=None, manual=False)
        else:
            match = next((p for p in self._table_items
                          if p.name == initial.name), None)
            if match is not None:
                self._apply_table_item(match)
            else:
                self._apply_values(initial)
                self._update_special()
                self._set_status("自定义", base_item=None, manual=True)

        self.protocol("WM_DELETE_WINDOW", self._close_noop)
        self.bind("<Escape>", self._close_noop)
        self._show_modal()

    # ---------- 取值 ----------

    def _semantic_value(self, attr: str) -> float:
        """滑块物理位置 → 语义值。

        初始介入度/破坏性滑块左端 0.4 只是内部状态，直接视作语义值 0（随机演化）；
        其余物理值（0.5 起）即语义值本身，保证 (0, 0.5) 区间仍为不可达的真实低值。
        """
        val = round(self._sliders[attr].get(), 1)
        if attr in self._INIT_STEP_PAIRS and abs(val - 0.4) < 1e-9:
            return 0.0
        return val

    def _values(self) -> dict:
        return {attr: self._semantic_value(attr) for attr, *_ in self.PARAMS}

    def _refresh_value_labels(self):
        for attr, _label, _lo, _hi, _step in self.PARAMS:
            val = self._semantic_value(attr)
            self._value_labels[attr].configure(text=f"{val:.1f}",
                                               text_color=SOFT)

    def _update_special(self):
        """表现初始介入度/破坏性为 0 的特殊性（随机演化，步长禁用并高亮标注）。"""
        for init_attr, step_attr in self._INIT_STEP_PAIRS.items():
            val = self._semantic_value(init_attr)
            zero = val == 0
            self._value_labels[init_attr].configure(
                text="（随机）" if zero else f"{val:.1f}",
                text_color=VAL_GOLDEN if zero else SOFT)
            self._sliders[init_attr].configure(
                **(_SLIDER_GOLD if zero else _SLIDER_BROWN))
            step_slider = self._sliders[step_attr]
            if zero:
                step_slider.configure(state="disabled", **_SLIDER_GRAY)
            else:
                step_slider.configure(state="normal", **_SLIDER_BROWN)
            self._value_labels[step_attr].configure(
                text_color=VAL_DISABLED if zero else SOFT)

    def _apply_values(self, item: Personality):
        for attr, _label, _lo, _hi, _step in self.PARAMS:
            self._sliders[attr].set(getattr(item, attr))
        self._refresh_value_labels()
        self._update_special()
        self._refresh_summary()

    def _apply_table_item(self, item):
        self._apply_values(item)
        self._set_status(item.name, base_item=item, manual=False)
        self._dropdown_btn.configure(text=item.name)

    def _set_status(self, text, base_item=None, manual=False):
        self._status = text
        self._base_item = base_item
        self._manual = manual
        key = "custom" if manual else ("item" if base_item is not None else "random")
        self._status_label.configure(text=text, text_color=PARAMS_STATUS[key])
        self._refresh_summary()

    def _refresh_summary(self):
        """刷新底部性格形容文本：预设性格首行为简介，其后按当前参数分述四大维度。
            使用斜体并保持紧凑行距。
        """
        lines = _personality_summary(self._values(), self._base_item)
        for idx, lbl in enumerate(self._summary_lines):
            if idx < len(lines):
                lbl.configure(text=lines[idx], text_color=SOFT, font=self._DESC_ITALIC_FONT)
                lbl.pack(fill='x', pady=0)
            else:
                lbl.pack_forget()

    def _import_from_table(self, choice):
        item = next((p for p in self._table_items if p.name == choice), None)
        if item is None:
            return
        self._apply_table_item(item)

    def _on_param_change(self, attr):
        slider = self._sliders[attr]
        val = round(slider.get(), 1)
        slider.set(val)
        self._value_labels[attr].configure(text=f"{self._semantic_value(attr):.1f}",
                                           text_color=SOFT)
        self._update_special()
        self._set_status("自定义", base_item=None, manual=True)

    # ---------- 提交 ----------

    def _ok(self):
        label = self._status
        if label == "随机":
            self.result = ("ok", "随机", None)
        elif not self._manual and self._base_item is not None:
            self.result = ("ok", label, self._base_item)
        else:
            self.result = ("ok", label,
                           Personality(name=label, **self._values()))
        self.destroy()

    def _clear(self):
        self.result = ("clear", None, None)
        self.destroy()

    def _close_noop(self, _event=None):
        self.result = None
        self.destroy()


# 部位名 -> (attr, 最小值, 最大值, 步长)；键与 ALL_PART_NAMES 一一对应（身高不在滑块中）
_PART_PARAMS = {
    "步长": ("stride_ratio", 0.50, 1.50, 0.005),
    "腿长": ("leg_ratio", 0.40, 0.60, 0.005),
    "臂长": ("arm_span_ratio", 0.30, 0.50, 0.005),
    "胸宽": ("chest_width_ratio", 0.20, 0.40, 0.005),
    "脚长": ("foot_length_ratio", 0.10, 0.20, 0.005),
    "脚踝高度": ("ankle_height_ratio", 0.03, 0.16, 0.005),
    "膝盖高度": ("knee_height_ratio", 0.20, 0.35, 0.005),
    "大腿直径": ("thigh_diameter_ratio", 0.10, 0.20, 0.005),
    "小臂直径": ("forearm_diameter_ratio", 0.03, 0.08, 0.005),
    "手掌长度": ("palm_length_ratio", 0.05, 0.16, 0.005),
    "食指长度": ("index_finger_ratio", 0.02, 0.09, 0.005),
    "食指直径": ("index_finger_diameter_ratio", 0.002, 0.020, 0.0001),
    "指缝宽度": ("finger_gap_ratio", 0.0005, 0.0100, 0.00005),
    "指纹宽度": ("fingerprint_width_ratio", 0.0001, 0.0020, 0.00001),
}


# (attr, 部位名, 最小值, 最大值, 步长)，顺序跟随 ALL_PART_NAMES；无滑块的部位（如身高）跳过
def _preset_params() -> list:
    return [(_PART_PARAMS[n][0], n, *_PART_PARAMS[n][1:])
            for n in ALL_PART_NAMES if n in _PART_PARAMS]


class PresetCustomDialog(BaseDialog):
    """身材参数对话框：导入下拉框 + 状态标签 + 分页参数滑块 + 身材预览，底部确定 / 消除。

    左侧滑块按 ALL_PART_NAMES 顺序创建，分成两页展示（每页 PAGE_SIZE 个以内）；
    右侧预览画布按实际绘制内容的长宽紧凑适配，画布颜色与右侧预览面板保持一致。
    """

    PARAMS = _preset_params()

    PAGE_SIZE = 9      # 每页最多滑块数（页1：步长~小臂直径；页2：手掌长度~指纹宽度）
    CANVAS_W = 225     # 预览画布设计宽（100% 缩放下的物理像素），略放宽预览区
    CANVAS_H = 400     # 预览画布设计高（100% 缩放下的物理像素）

    def __init__(self, parent, table_items, initial: Optional[BodyPreset] = None):
        super().__init__(parent)
        self.title("身材")
        self.result = None
        self._table_items = table_items or []
        self._sliders: Dict[str, ctk.CTkSlider] = {}
        self._value_labels: Dict[str, ctk.CTkLabel] = {}
        self._page = 0
        self._page_frames: list = []
        self._page_labels = [f"部位 {i + 1}"
                             for i in range((len(self.PARAMS) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)]
        self.geometry("600x450")
        self.resizable(False, False)
        self.grid_columnconfigure(0, weight=4)
        self.grid_columnconfigure(1, weight=3)
        self.grid_rowconfigure(1, weight=1)

        # ---- 顶部：导入下拉框 + 状态标签（置于左侧） ----
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky='ew', padx=(14, 8), pady=(12, 2))
        top.columnconfigure(1, weight=1)
        ctk.CTkLabel(top, text="导入身材", anchor='w', font=self.UI_FONT,
                     text_color=TITLE).pack(side="left")

        self._dropdown_btn = ctk.CTkButton(
            top, text="选择…", width=110, height=26, font=self.UI_FONT,
            fg_color=PNL_BG, text_color=TEXT,
            hover_color=HOVER_ALT, border_width=1, border_color=BORDER_ALT,
            corner_radius=8, state="disabled" if not self._table_items else "normal")
        self._dropdown_btn.pack(side="left", padx=(10, 85))

        self._dropdown = None
        if self._table_items:
            self._dropdown = CTkScrollableDropdownFrame(
                attach=self._dropdown_btn, values=[p.name for p in self._table_items],
                command=self._import_from_table, height=160, button_height=28,
                fg_color=PNL_BG, button_color=PNL_BG,
                hover_color=HOVER_ALT, scrollbar_button_color=HOVER_ALT,
                scrollbar_button_hover_color=MENU_HOVER, frame_border_color=MENU_HOVER,
                text_color=TEXT, frame_border_width=1, justify="left")

        ctk.CTkLabel(top, text="状态:", font=self.UI_FONT, text_color=TITLE).pack(side="left", padx=(0, 6))
        self._status_label = ctk.CTkLabel(top, text="随机", anchor='w', font=self.UI_FONT_BOLD,
                                          text_color=PARAMS_STATUS["random"])
        self._status_label.pack(side="left", padx=3)

        # ---- 中间左侧：按 ALL_PART_NAMES 顺序分页的参数滑块 ----
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky='nsew', padx=(14, 8), pady=4)

        # 分页切换（部位 1 / 部位 2）
        self._page_seg = CTkSegmentedControl(
            body,
            values=self._page_labels,
            command=lambda label: self._switch_page(self._page_labels.index(label)),
            height=23, corner_radius=5,
            font=self.UI_FONT
        )
        self._page_seg.pack(anchor='w', pady=5)

        # 各页滑块（按 ALL_PART_NAMES 顺序截段创建）
        for start in range(0, len(self.PARAMS), self.PAGE_SIZE):
            page_frame = ctk.CTkFrame(body, fg_color="transparent")
            self._page_frames.append(page_frame)
            for attr, label, lo, hi, step in self.PARAMS[start:start + self.PAGE_SIZE]:
                row_frame = ctk.CTkFrame(page_frame, fg_color="transparent")
                row_frame.pack(fill='x', pady=3)
                row_frame.columnconfigure(1, weight=1)
                ctk.CTkLabel(row_frame, text=label, width=110, anchor='w', font=self.UI_FONT,
                             text_color=TEXT).grid(row=0, column=0, padx=(0, 6))

                slider = ctk.CTkSlider(row_frame, from_=lo, to=hi, number_of_steps=int(round((hi - lo) / step)),
                                       command=lambda _v, a=attr: self._on_param_change(a),
                                       **_SLIDER_BROWN)
                slider.grid(row=0, column=1, sticky='ew')

                val_label = ctk.CTkLabel(row_frame, text="", width=50, anchor='w', font=("Consolas", 11),
                                         text_color=SOFT)
                val_label.grid(row=0, column=2, padx=10, sticky='w')
                self._sliders[attr] = slider
                self._value_labels[attr] = val_label
        self._switch_page(0)

        # ---- 右侧：身材比例预览（画布尺寸按实际绘制长宽紧凑适配，颜色与面板一致） ----
        s = self._get_window_scaling()
        preview_panel = ctk.CTkFrame(self, border_color=VIEW_PNL_BORDER,
                                     fg_color=VIEW_PNL_FG, border_width=0, corner_radius=8)
        preview_panel.grid(row=0, column=1, rowspan=3, sticky='nsew', padx=(8, 10), pady=(6, 9))
        ctk.CTkLabel(preview_panel, text="身材比例预览", font=self.UI_FONT_BOLD,
                     text_color=TITLE).pack(pady=(8, 0))

        canvas_bg = (VIEW_PNL_FG[0] if ctk.get_appearance_mode().lower() == "light"
                     else VIEW_PNL_FG[1])
        self._preview_canvas = BodyPreviewCanvas(preview_panel,
                                                 width=round(self.CANVAS_W * s),
                                                 height=round(self.CANVAS_H * s),
                                                 bg=canvas_bg)
        self._preview_canvas.pack(padx=8, pady=(0, 75), fill='both', expand=True)

        # ---- 底部：确定 / 消除（置于左侧） ----
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=2, column=0, padx=(14, 8), pady=10)
        ctk.CTkButton(btn_frame, text="确定", width=88, height=28, font=self.UI_FONT, command=self._ok).pack(
            side='left', padx=7)
        ctk.CTkButton(btn_frame, text="消除", width=88, height=28, font=self.UI_FONT, fg_color="transparent",
                      text_color=STATUS_ERR, hover_color=CLEAR_BG,
                      border_width=1, border_color=CLEAR_BORDER, command=self._clear).pack(side='left',
                                                                                           padx=7)

        # ---- 状态初始化 ----
        if initial is None:
            if self._table_items:
                self._apply_table_item(self._table_items[0])
            else:
                for attr, _label, lo, _hi, _step in self.PARAMS:
                    self._sliders[attr].set(lo)
                self._refresh_value_labels()
                self._set_status("随机", base_item=None, manual=False)
                self._preview_canvas.update_values(self._values())
        else:
            match = next((p for p in self._table_items if p.name == initial.name), None)
            if match is not None:
                self._apply_table_item(match)
            else:
                self._apply_values(initial)
                self._set_status("自定义", base_item=None, manual=True)

        self.protocol("WM_DELETE_WINDOW", self._close_noop)
        self.bind("<Escape>", self._close_noop)
        self._show_modal()

    # ---------- 分页 ----------
    def _switch_page(self, page: int):
        """切换左侧参数页面（0 起），并刷新分页切换控件的选中样式。"""
        self._page = page
        for i, page_frame in enumerate(self._page_frames):
            if i == page:
                page_frame.pack(fill='x')
            else:
                page_frame.pack_forget()
        self._page_seg.set(self._page_labels[page])

    def _values(self) -> dict:
        return {attr: self._sliders[attr].get() for attr, *_ in self.PARAMS}

    def _refresh_value_labels(self):
        for attr, _label, _lo, _hi, step in self.PARAMS:
            val = self._sliders[attr].get()
            dec = _step_decimals(step)
            self._value_labels[attr].configure(text=f"{val:.{dec}f}", text_color=SOFT)

    def _apply_values(self, item: BodyPreset):
        for attr, _label, _lo, _hi, _step in self.PARAMS:
            self._sliders[attr].set(getattr(item, attr))
        self._refresh_value_labels()
        self._preview_canvas.update_values(self._values())

    def _apply_table_item(self, item):
        self._apply_values(item)
        self._set_status(item.name, base_item=item, manual=False)
        self._dropdown_btn.configure(text=item.name)

    def _set_status(self, text, base_item=None, manual=False):
        self._status = text
        self._base_item = base_item
        self._manual = manual
        key = "custom" if manual else ("item" if base_item is not None else "random")
        self._status_label.configure(text=text, text_color=PARAMS_STATUS[key])

    def _import_from_table(self, choice):
        item = next((p for p in self._table_items if p.name == choice), None)
        if item is None:
            return
        self._apply_table_item(item)

    def _on_param_change(self, attr):
        slider = self._sliders[attr]
        step = next((s for a, _l, _lo, _hi, s in self.PARAMS if a == attr), 0.001)
        dec = _step_decimals(step)
        val = round(slider.get(), dec)
        slider.set(val)
        self._value_labels[attr].configure(text=f"{val:.{dec}f}", text_color=SOFT)
        self._set_status("自定义", base_item=None, manual=True)
        self._preview_canvas.update_values(self._values())

    def _ok(self):
        label = self._status
        if label == "随机":
            self.result = ("ok", "随机", None)
        elif not self._manual and self._base_item is not None:
            self.result = ("ok", label, self._base_item)
        else:
            self.result = ("ok", label, BodyPreset(name=label, **self._values()))
        self.destroy()

    def _clear(self):
        self.result = ("clear", None, None)
        self.destroy()

    def _close_noop(self, _event=None):
        self.result = None
        self.destroy()


class BodyPreviewCanvas(tk.Canvas):
    """轻量级正面身材比例预览（已迁移角色画法，修正鞋子绘制与腿长计算方法）"""

    def __init__(self, parent, width: int = 300, height: int = 410, **kwargs):
        self.preview_width = width
        self.preview_height = height
        self._current_values: Optional[dict] = None
        self._redraw_job = None
        self._dark = ctk.get_appearance_mode().lower() == "dark"

        super().__init__(
            parent,
            width=width,
            height=height,
            highlightthickness=0,
            borderwidth=0,
            **kwargs,
        )
        self.bind("<Configure>", self._on_configure)

    def update_values(self, values: dict):
        """更新参数并请求重绘。"""
        self._current_values = dict(values)
        self.refresh()

    def _on_configure(self, _event=None):
        if self._redraw_job is not None:
            try:
                self.after_cancel(self._redraw_job)
            except tk.TclError:
                pass
        self._redraw_job = self.after_idle(self.refresh)

    def poly(self, points, fill, outline=VIEW_OUTLINE, width=1.5, **kwargs):
        return self.create_polygon(points, fill=fill, outline=outline, width=width,
                                   joinstyle="round", **kwargs)

    def line(self, points, fill, width, **kwargs):
        return self.create_line(points, fill=fill, width=width, smooth=True,
                                capstyle="round", joinstyle="round", **kwargs)

    # ---- 暗色模式曝光衰减：所有绘制颜色统一经此折算（越亮衰减越大） ----
    def _dim_kwargs(self, kwargs):
        if not self._dark:
            return kwargs
        for key in ("fill", "outline"):
            color = kwargs.get(key)
            if color and _preview_dim_color(color) != color:
                kwargs[key] = _preview_dim_color(color)
        return kwargs

    def create_oval(self, *args, **kwargs):
        return super().create_oval(*args, **self._dim_kwargs(kwargs))

    def create_polygon(self, *args, **kwargs):
        return super().create_polygon(*args, **self._dim_kwargs(kwargs))

    def create_line(self, *args, **kwargs):
        return super().create_line(*args, **self._dim_kwargs(kwargs))

    # ==================== 绘制拆分主入口与子方法 ====================
    def refresh(self):
        """主绘制逻辑：负责计算全局坐标系并调用各部位绘制子方法。"""
        if not self._current_values:
            return

        self.delete("all")
        w = max(self.winfo_width(), 100)
        h = max(self.winfo_height(), 100)

        # 1. 地面基准线 (ground) 与固定总身高 (total_height)
        cx = w * 0.5
        ground = h - 30
        total_height = h * 0.82
        v = self._current_values

        # 2. 关键 y 坐标计算
        head_top = ground - total_height
        leg = v.get("leg_ratio", 0.5) * total_height
        hip_y = ground - leg

        upper_h = total_height - leg
        min_head_h = total_height / 8.5
        max_head_h = total_height / 6.5

        head_h = max(min_head_h, min(upper_h * 0.27, max_head_h))

        # 胸宽对头部宽度的微调联动
        chest_ratio = v.get("chest_width_ratio", 0.2)
        head_w_scale = 0.77 + (chest_ratio - 0.2) * 0.15
        head_w = head_h * head_w_scale

        neck_h = max((upper_h - head_h) * 0.05, 4.0)
        neck_y = head_top + head_h
        shoulder_y = neck_y + neck_h

        foot = v.get("foot_length_ratio", 0.1) * total_height * 0.6
        shoe_h = 18.0 + foot * 0.25
        ankle_y = ground - shoe_h

        knee_y = ground - (v.get("knee_height_ratio", 0.25) * total_height)
        knee_y = max(hip_y + leg * .32, min(knee_y, ankle_y - leg * .16))

        # 3. 关键宽度与构件长短计算
        thigh_ratio = v.get("thigh_diameter_ratio", 0.1)
        thigh_w = thigh_ratio * total_height * .72
        calf_w = thigh_w * .72
        bust_w = chest_ratio * total_height * .58

        # 使裙子基础宽度增加
        weakened_chest_ratio = 0.22 + (chest_ratio - 0.2) * 0.28

        # 当增大大腿直径时，下半身基准宽度会同步被略微撑大，从而放大裙子下摆
        thigh_expansion = max(0.0, (thigh_ratio - 0.1) * 0.35)
        lower_bust_w = (weakened_chest_ratio + thigh_expansion) * total_height * .64

        arm = v.get("arm_span_ratio", 0.3) * total_height

        # 配色定义
        colors = {
            "hair_base": VIEW_HAIR,
            "hair_shade": VIEW_HAIR_SHADE,
            "skin": VIEW_SKIN,
            "skin_shade": VIEW_SKIN_SHADE,
            "cloth_white": VIEW_CLOTH,
            "navy_base": VIEW_NAVY,
            "navy_line": VIEW_NAVY_LINE,
            "eye": VIEW_EYE
        }

        # 依次调用部件绘制函数
        self._draw_background(cx, ground)
        self._draw_back_hair(cx, head_top, head_h, head_w, neck_y, shoulder_y, colors)

        self._draw_arm_back_styled(cx, shoulder_y, bust_w, arm, colors, direction=-1)
        self._draw_arm_back_styled(cx, shoulder_y, bust_w, arm, colors, direction=1)

        self._draw_legs_and_shoes(cx, hip_y, knee_y, ankle_y, ground, lower_bust_w, thigh_w, calf_w, foot, colors)
        self._draw_outfit(cx, shoulder_y, hip_y, bust_w, lower_bust_w, colors)
        self._draw_head_and_face(cx, head_top, head_h, head_w, neck_y, shoulder_y, colors)
        self._draw_front_hair(cx, head_top, head_h, head_w, colors)

    def _draw_head_and_face(self, cx, head_top, head_h, head_w, neck_y, shoulder_y, colors):
        """绘制头部轮廓、脖子、眼睛、嘴巴与腮红（精简脖子基础宽度）。"""
        skull_top = head_top + head_h * 0.08

        # 减少脖子基础宽度
        neck_w_half = head_w * 0.21
        self.poly([cx - neck_w_half, neck_y - 5, cx + neck_w_half, neck_y - 5,
                   cx + neck_w_half * 1.05, shoulder_y + 18, cx - neck_w_half * 1.05, shoulder_y + 18], colors["skin"])
        self.create_oval(cx - head_w * .5, skull_top, cx + head_w * .5, skull_top + head_h * 1.0,
                         fill=colors["skin"], outline=VIEW_OUTLINE_DARK, width=1.5)

        eye_y = head_top + head_h * .62
        eye_w = max(9, head_w * .20)
        eye_h = max(6, head_h * .13)
        eye_offset = head_w * .21

        # 睫毛
        self.line([cx - eye_offset - eye_w, eye_y - eye_h * .46, cx - eye_offset + eye_w, eye_y - eye_h * .62],
                  colors["hair_base"], 1.5)
        self.line([cx + eye_offset - eye_w, eye_y - eye_h * .62, cx + eye_offset + eye_w, eye_y - eye_h * .46],
                  colors["hair_base"], 1.5)

        # 眼白
        self.create_oval(cx - eye_offset - eye_w / 2, eye_y - eye_h / 2, cx - eye_offset + eye_w / 2, eye_y + eye_h / 2,
                         fill=VIEW_EYE_WHITE, outline=VIEW_EYE_WHITE_LINE, width=1.5)
        self.create_oval(cx + eye_offset - eye_w / 2, eye_y - eye_h / 2, cx + eye_offset + eye_w / 2, eye_y + eye_h / 2,
                         fill=VIEW_EYE_WHITE, outline=VIEW_EYE_WHITE_LINE, width=1.5)

        # 虹膜
        pupil_w = eye_w * 0.56
        pupil_h = eye_h * 0.84
        self.create_oval(cx - eye_offset - pupil_w / 2, eye_y - pupil_h / 2, cx - eye_offset + pupil_w / 2, eye_y + pupil_h / 2,
                         fill=colors["eye"], outline="")
        self.create_oval(cx + eye_offset - pupil_w / 2, eye_y - pupil_h / 2, cx + eye_offset + pupil_w / 2, eye_y + pupil_h / 2,
                         fill=colors["eye"], outline="")

        # 瞳孔中心（加深）
        center_w = pupil_w * 0.6
        center_h = pupil_h * 0.6
        self.create_oval(cx - eye_offset - center_w / 2, eye_y - center_h / 2, cx - eye_offset + center_w / 2, eye_y + center_h / 2,
                         fill=VIEW_PUPIL, outline="")
        self.create_oval(cx + eye_offset - center_w / 2, eye_y - center_h / 2, cx + eye_offset + center_w / 2, eye_y + center_h / 2,
                         fill=VIEW_PUPIL, outline="")

        # 高光
        shine_size = 2.5
        self.create_oval(cx - eye_offset - pupil_w / 2, eye_y - pupil_h / 2, cx - eye_offset - pupil_w / 2 + shine_size, eye_y - pupil_h / 2 + shine_size,
                         fill=VIEW_HIGHLIGHT, outline="white", width=0.5)
        self.create_oval(cx + eye_offset - pupil_w / 2, eye_y - pupil_h / 2, cx + eye_offset - pupil_w / 2 + shine_size, eye_y - pupil_h / 2 + shine_size,
                         fill=VIEW_HIGHLIGHT, outline="white", width=0.5)

        # 嘴巴与腮红
        mouth_y = head_top + head_h * .87
        self.line([cx - head_w * .07, mouth_y, cx, mouth_y + 4.5, cx + head_w * .07, mouth_y], VIEW_MOUTH, 1.5)
        self.create_oval(cx - head_w * .43, head_top + head_h * .78, cx - head_w * .28, head_top + head_h * .87,
                         fill=VIEW_BLUSH, outline="")
        self.create_oval(cx + head_w * .28, head_top + head_h * .78, cx + head_w * .43, head_top + head_h * .87,
                         fill=VIEW_BLUSH, outline="")

    # ---------------- 拆分的具体部件绘制方法 ----------------

    def _draw_background(self, cx: float, ground: float):
        """绘制阴影地面背景。"""
        self.create_oval(cx - 90, ground - 15, cx + 90, ground + 10, fill=VIEW_SHADOW, outline="")

    def _draw_back_hair(self, cx, head_top, head_h, head_w, neck_y, shoulder_y, colors):
        """绘制后发部分。"""
        self.create_polygon([
            cx - head_w * .48, head_top + head_h * .60,
            cx - head_w * .65, head_top + head_h * .88,
            cx - head_w * .72, neck_y + 20,
            cx - head_w * .68, shoulder_y + 30,
            cx - head_w * .62, shoulder_y + 58,
            cx - head_w * .45, shoulder_y + 53,
            cx - head_w * .29, shoulder_y + 47,
            cx, neck_y + 38,
            cx + head_w * .29, shoulder_y + 47,
            cx + head_w * .45, shoulder_y + 53,
            cx + head_w * .62, shoulder_y + 58,
            cx + head_w * .68, shoulder_y + 30,
            cx + head_w * .72, neck_y + 20,
            cx + head_w * .65, head_top + head_h * .88,
            cx + head_w * .48, head_top + head_h * .60
        ], fill=colors["hair_base"], outline="", smooth=True, splinesteps=16)

        for side in (-1, 1):
            self.create_polygon([
                cx + side * head_w * .48, head_top + head_h * .60,
                cx + side * head_w * .58, head_top + head_h * .92,
                cx + side * head_w * .62, neck_y + 23,
                cx + side * head_w * .55, shoulder_y + 10,
                cx + side * head_w * .48, shoulder_y + 35,
                cx + side * head_w * .38, shoulder_y + 33,
                cx + side * head_w * .29, shoulder_y + 32,
                cx + side * head_w * .31, neck_y + 22
            ], fill=colors["hair_shade"], outline="", smooth=True, splinesteps=16)

    def _draw_arm_back_styled(self, cx, shoulder_y, bust_w, arm, colors, direction):
        """通用方法：绘制背到身后的手臂，强化肘关节外侧轮廓感，精准对称且无变型相交。

        :param direction: -1 代表图像左臂，1 代表图像右臂
        """
        # 1. 骨骼基准关键点（肩部作为原点锚点，起点向内向上移动）
        rs = (cx + direction * bust_w * 0.60, shoulder_y + 28)

        # 比例与臂长设定
        len_upper = arm * 0.40   # 大臂实际几何长度
        len_fore = arm * 0.34    # 小臂实际几何长度

        # 大臂方向与小臂方向的单位化向量（保留原有自然的背手姿势角度）
        dir_u_len = math.hypot(0.22, 0.35)
        u_dx = direction * (0.22 / dir_u_len) * len_upper
        u_dy = (0.35 / dir_u_len) * len_upper
        re = (rs[0] + u_dx, rs[1] + u_dy)  # 肘部坐标

        dir_f_len = math.hypot(-0.6, 0.28)
        f_dx = direction * (-0.6 / dir_f_len) * len_fore
        f_dy = (0.28 / dir_f_len) * len_fore
        rw = (re[0] + f_dx, re[1] + f_dy)  # 手腕坐标

        v = self._current_values or {}
        forearm_ratio = v.get("forearm_diameter_ratio", 0.05)
        h = max(self.winfo_height(), 100)
        total_height = h * 0.82

        w_avg = forearm_ratio * total_height * 0.65
        w_elbow = w_avg / 0.85
        w_wrist = 0.65 * w_elbow
        w_shoulder = 1.25 * w_elbow

        # 偏移辅助函数：根据向量 (p1->p2) 计算外侧(+1)和内侧(-1)的点
        def get_offset_pt(p1, p2, width, side_sign):
            dx, dy = p2[0] - p1[0], p2[1] - p1[1]
            length = math.hypot(dx, dy) or 1.0
            nx = -dy / length * (width / 2.0) * direction * side_sign
            ny = dx / length * (width / 2.0) * direction * side_sign
            return (p1[0] + nx, p1[1] + ny)

        # 2. 独立计算大臂段与小臂段两侧的边界点 (+1 为外侧，-1 为内侧)
        s_outer = get_offset_pt(rs, re, w_shoulder, 1)
        s_inner = get_offset_pt(rs, re, w_shoulder, -1)

        # 肘部外侧关键点：计算一个略微向外凸出的“肘尖”控制点
        e_outer_u = get_offset_pt(re, rs, w_elbow, -1)
        e_outer_l = get_offset_pt(re, rw, w_elbow, 1)
        # 结合两段法线方向，向外侧额外延伸 1.5 像素，形成明确的肘尖结构
        elbow_tip_x = (e_outer_u[0] + e_outer_l[0]) * 0.5 + direction * 1.5
        elbow_tip_y = (e_outer_u[1] + e_outer_l[1]) * 0.5
        elbow_tip = (elbow_tip_x, elbow_tip_y)

        # 肘部内侧关键点（保持平滑过渡）
        e_inner_u = get_offset_pt(re, rs, w_elbow, 1)
        e_inner_l = get_offset_pt(re, rw, w_elbow, -1)

        # 手腕
        w_outer = get_offset_pt(rw, re, w_wrist, -1)
        w_inner = get_offset_pt(rw, re, w_wrist, 1)

        # 3. 排列控制点：外侧直接使用 elbow_tip 集中转折，减少过度插值；内侧保留中点插值
        arm_pts = [
            s_outer[0], s_outer[1],
            (s_outer[0] + e_outer_u[0]) * 0.5, (s_outer[1] + e_outer_u[1]) * 0.5,
            # 外侧肘尖
            elbow_tip[0], elbow_tip[1],
            (e_outer_l[0] + w_outer[0]) * 0.5, (e_outer_l[1] + w_outer[1]) * 0.5,
            w_outer[0], w_outer[1],
            # 手腕末端
            rw[0], rw[1] + 2,
            w_inner[0], w_inner[1],
            (e_inner_l[0] + w_inner[0]) * 0.5, (e_inner_l[1] + w_inner[1]) * 0.5,
            (e_inner_u[0] + e_inner_l[0]) * 0.5, (e_inner_u[1] + e_inner_l[1]) * 0.5,
            (s_inner[0] + e_inner_u[0]) * 0.5, (s_inner[1] + e_inner_u[1]) * 0.5,
            s_inner[0], s_inner[1]
        ]

        # 4. 绘制手臂
        self.create_polygon(
            arm_pts,
            fill=colors["skin_shade"],
            outline=VIEW_SKIN_LINE,
            width=1.2,
            smooth=True,
            splinesteps=16
        )

    def _draw_legs_and_shoes(self, cx, hip_y, knee_y, ankle_y, ground, bust_w, thigh_w, calf_w, foot, colors):
        """绘制双腿、膝盖细节与鞋子（配置统一的比肤色略暗腿部外轮廓线条）。"""
        lh, rh = cx - bust_w * .31, cx + bust_w * .31
        lk, rk = cx - bust_w * .39, cx + bust_w * .36
        la, ra = cx - bust_w * .30, cx + bust_w * .29

        for hx, kx, ax, direction in ((lh, lk, la, -1), (rh, rk, ra, 1)):
            calf_top_y = knee_y + (ankle_y - knee_y) * 0.35
            ankle_transition_y = ankle_y - (ankle_y - knee_y) * 0.15

            # 1. 腿部基础轮廓与外描边
            self.create_polygon([
                hx - direction * thigh_w * 0.5, hip_y - 5,
                hx - direction * thigh_w * 0.54, (hip_y + knee_y) * 0.45,
                kx - direction * thigh_w * 0.45, knee_y - 10,
                kx - direction * calf_w * 0.42, knee_y,
                kx - direction * calf_w * 0.52, calf_top_y,
                ax - direction * calf_w * 0.30, ankle_transition_y,
                ax - direction * calf_w * 0.22, ankle_y,
                ax + direction * calf_w * 0.22, ankle_y,
                ax + direction * calf_w * 0.28, ankle_transition_y,
                kx + direction * calf_w * 0.42, calf_top_y + (ankle_y - knee_y) * 0.08,
                kx + direction * calf_w * 0.38, knee_y,
                hx + direction * thigh_w * 0.45, (hip_y + knee_y) * 0.5,
                hx + direction * thigh_w * 0.5, hip_y - 5,
            ], fill=colors["skin"],
               outline=VIEW_SKIN_LINE, width=1.2,  # 统一修改：比 skin (#f8eade) 略暗柔和的肤色描边
               smooth=True, splinesteps=24)

            # 内部阴影区域保持无描边，避免线条叠加重影
            self.create_polygon([
                hx + direction * thigh_w * .12, hip_y + 4,
                hx + direction * thigh_w * .45, hip_y + 3,
                kx + direction * thigh_w * .41, (hip_y + knee_y) / 2,
                kx + direction * thigh_w * .38, knee_y,
                kx + direction * calf_w * .38, calf_top_y,
                kx + direction * calf_w * .25, ankle_transition_y,
                ax + direction * calf_w * .13, ankle_y,
                kx + direction * calf_w * .12, knee_y
            ], fill=colors["skin_shade"], outline="", smooth=True, splinesteps=12)

            self.create_oval(
                kx - thigh_w * .23, knee_y - thigh_w * .16,
                kx + thigh_w * .23, knee_y + thigh_w * .19,
                fill=colors["skin_shade"], outline=VIEW_SKIN_LINE, width=1  # 同步改用略暗肤线
            )
            self.create_line(
                [ax - calf_w * .22, ankle_y + 2, ax + calf_w * .22, ankle_y + 1],
                fill=VIEW_SKIN_LINE, width=1, smooth=True  # 同步改用略暗肤线
            )

            # 2. 鞋子绘制
            shoe_h = ground - ankle_y
            toe_x = ax + direction * (foot * 0.85)
            heel_x = ax - direction * (calf_w * 0.38)
            top_opening_y = ankle_y + shoe_h * 0.15

            sole_y = ground - 1
            sole_pts = [
                heel_x - direction * 1, sole_y - 2,
                toe_x + direction * 2, sole_y - 2,
                toe_x + direction * 1, ground,
                heel_x, ground
            ]
            self.create_polygon(
                sole_pts,
                fill=VIEW_CLOTH_DARK, outline=VIEW_CLOTH_DARK_LINE, width=1,
                smooth=True, splinesteps=8
            )

            shoe_upper_pts = [
                heel_x, top_opening_y,
                ax - direction * (calf_w * 0.1), top_opening_y + shoe_h * 0.25,
                ax + direction * (calf_w * 0.2), top_opening_y + shoe_h * 0.2,
                ax + direction * (foot * 0.5), top_opening_y + shoe_h * 0.35,
                toe_x, sole_y - 1,
                heel_x, sole_y - 1
            ]
            self.create_polygon(
                shoe_upper_pts,
                fill=colors["cloth_white"], outline=VIEW_OUTLINE_DARK, width=1.2,
                smooth=True, splinesteps=16
            )

            strap_x1 = ax - direction * (calf_w * 0.15)
            strap_x2 = ax + direction * (calf_w * 0.25)
            strap_y = top_opening_y + shoe_h * 0.22
            self.create_line(
                [strap_x1, strap_y, strap_x2, strap_y + 1],
                fill=colors["navy_base"], width=2, capstyle="round"
            )

    def _draw_outfit(self, cx, shoulder_y, hip_y, bust_w, lower_bust_w, colors):
        """绘制水手服（采用增宽后的 lower_bust_w 绘制裙子）。"""
        v = self._current_values or {}
        h = max(self.winfo_height(), 100)
        total_height = h * 0.82
        ground = h - 30

        # 白色部位的统一较暗边线与短袖变暗填充色
        dark_white_outline = VIEW_WHITE_LINE
        sleeve_fill = VIEW_SLEEVE

        # 1. 计算膝盖位置与裙子范围
        knee_y = ground - (v.get("knee_height_ratio", 0.25) * total_height)
        torso_h = max(hip_y - shoulder_y, 10.0)
        skirt_top = hip_y - torso_h * 0.33
        thigh_mid_y = (hip_y + knee_y) * 0.5

        skirt_bot_mid = thigh_mid_y + 4
        skirt_bot_side = thigh_mid_y - 6

        # 2. 绘制裙子主体与裙褶
        self.create_polygon([
            cx - lower_bust_w * .56, skirt_top,
            cx, skirt_top + 2,
            cx + lower_bust_w * .56, skirt_top,
            cx + lower_bust_w * .75, (skirt_top + skirt_bot_side) / 2,
            cx + lower_bust_w * .82, skirt_bot_side,
            cx + lower_bust_w * .50, skirt_bot_mid,
            cx, skirt_bot_mid + 3,
            cx - lower_bust_w * .50, skirt_bot_mid,
            cx - lower_bust_w * .82, skirt_bot_side,
            cx - lower_bust_w * .75, (skirt_top + skirt_bot_side) / 2
        ], fill=colors["navy_base"], outline="", smooth=True, splinesteps=16)

        for offset in (-.44, -.15, .15, .44):
            self.create_line(
                [cx + lower_bust_w * offset, skirt_top + 4, cx + lower_bust_w * offset * 1.25, skirt_bot_mid - 6],
                fill=colors["navy_line"], width=2, smooth=True)

        # 3. 短袖绘制（彻底解决自交叉沙漏问题，精准对齐几何轮廓）
        arm_span = v.get("arm_span_ratio", 0.3) * total_height
        forearm_ratio = v.get("forearm_diameter_ratio", 0.05)

        # 1:1 计算大臂粗细
        arm_w_avg = forearm_ratio * total_height * 0.65
        arm_w_elbow = arm_w_avg / 0.85
        w_shoulder = 1.25 * arm_w_elbow

        # 大臂骨骼朝向与单位法线
        len_upper = arm_span * 0.38
        dir_u_len = math.hypot(0.22, 0.35)
        u_dx = 0.22 / dir_u_len
        u_dy = 0.35 / dir_u_len

        sleeve_len = len_upper * 0.48
        sleeve_w = w_shoulder * 1.15  # 袖宽与大臂直径增幅保持 1:1

        for side in (-1, 1):
            # 1. 微调：更靠外侧、高度稍低的肩膀顶点
            s_outer = (cx + side * bust_w * 0.78, shoulder_y + 16)

            # 2. 腋下内侧连接点
            s_inner = (cx + side * bust_w * 0.45, shoulder_y + 50)

            # 3. 大臂中轴线延伸出的袖口中心
            rs = (cx + side * bust_w * 0.60, shoulder_y + 32)
            c_center = (rs[0] + side * u_dx * sleeve_len, rs[1] + u_dy * sleeve_len)

            # 4. 显式计算向上/向外的法线偏移（保证 c_outer 绝对在大臂外上方，c_inner 在内下方）
            perp_x = side * u_dy
            perp_y = -u_dx

            # 袖口外侧点与内侧点
            c_outer = (c_center[0] + perp_x * (sleeve_w * 0.5), c_center[1] + perp_y * (sleeve_w * 0.5))
            c_inner = (c_center[0] - perp_x * (sleeve_w * 0.5), c_center[1] - perp_y * (sleeve_w * 0.5))

            # 5. 根据左右侧(side)显式调整多边形点集的排列顺序，保证无论左右都是凸多边形无交叉
            if side == -1:  # 左臂
                sleeve_pts = [
                    s_outer[0], s_outer[1],
                    c_outer[0], c_outer[1],
                    c_inner[0], c_inner[1],
                    s_inner[0], s_inner[1]
                ]
            else:  # 右臂
                sleeve_pts = [
                    s_outer[0], s_outer[1],
                    s_inner[0], s_inner[1],
                    c_inner[0], c_inner[1],
                    c_outer[0], c_outer[1]
                ]

            # 绘制凸多边形短袖，填充颜色变暗，采用统一较暗的白色边线
            self.create_polygon(sleeve_pts, fill=sleeve_fill, outline=dark_white_outline, width=1.0)

            # 绘制平行于大臂截面的袖口水手线
            self.create_line([c_outer[0], c_outer[1], c_inner[0], c_inner[1]],
                             fill=VIEW_STITCH, width=2, capstyle="round")

        # 4. 上衣躯干主体（边线采用统一较暗的白色边线）
        self.create_polygon([
            cx - bust_w * .60, shoulder_y + 22,
            cx - bust_w * .45, shoulder_y + 16,
            cx, shoulder_y + 20,
            cx + bust_w * .45, shoulder_y + 16,
            cx + bust_w * .60, shoulder_y + 22,
            cx + lower_bust_w * .53, skirt_top + 5,
            cx, skirt_top + 13,
            cx - lower_bust_w * .53, skirt_top + 5
        ], fill=colors["cloth_white"], outline=dark_white_outline, width=1.0, smooth=True)

        # 5. 肩部遮罩
        # 覆盖短袖与水手领之间的肩部连接区域
        mask_fill = VIEW_SLEEVE
        mask_outline = dark_white_outline

        for side in (-1, 1):
            if side == -1:
                pts = [
                    # 外侧肩头
                    (cx - bust_w * 0.78, shoulder_y + 16),
                    # 肩部上缘
                    (cx - bust_w * 0.55, shoulder_y + 17),
                    # 向下覆盖肩部
                    (cx - bust_w * 0.36, shoulder_y + 55),
                    # 回到外侧下方
                    (cx - bust_w * 0.57, shoulder_y + 38),
                ]
            else:
                pts = [
                    # 外侧肩头
                    (cx + bust_w * 0.78, shoulder_y + 16),
                    # 肩部上缘
                    (cx + bust_w * 0.55, shoulder_y + 17),
                    # 向下覆盖肩部
                    (cx + bust_w * 0.36, shoulder_y + 57),
                    # 回到外侧下方
                    (cx + bust_w * 0.57, shoulder_y + 39),
                ]

            self.create_polygon(
                pts,
                fill=mask_fill,
                outline=mask_outline,
                width=1.0,
                joinstyle="round",
            )

        # 6. 水手领与蝴蝶结
        self.poly([cx - bust_w * .63, shoulder_y + 18, cx - bust_w * .19, shoulder_y + 10,
                   cx, shoulder_y + 51, cx - bust_w * .15, shoulder_y + 82, cx - bust_w * .50, shoulder_y + 30],
                  colors["navy_base"])
        self.poly([cx + bust_w * .63, shoulder_y + 18, cx + bust_w * .19, shoulder_y + 10,
                   cx, shoulder_y + 51, cx + bust_w * .15, shoulder_y + 82, cx + bust_w * .50, shoulder_y + 30],
                  colors["navy_base"])
        self.poly([cx - 16, shoulder_y + 45, cx, shoulder_y + 64, cx + 16, shoulder_y + 45,
                   cx + 10, shoulder_y + 90, cx, shoulder_y + 101, cx - 10, shoulder_y + 90], colors["navy_base"])


    def _draw_front_hair(self, cx, head_top, head_h, head_w, colors):
        """绘制前发与刘海（最顶层）。"""
        eye_y = head_top + head_h * .58
        eye_h = max(5.0, head_h * .15)

        self.create_polygon([
            cx - head_w * .58, head_top + head_h * .55,
            cx - head_w * .50, head_top + head_h * .22,
            cx - head_w * .22, head_top + head_h * .02,
            cx, head_top,
            cx + head_w * .22, head_top + head_h * .02,
            cx + head_w * .45, head_top + head_h * .22,
            cx + head_w * .58, head_top + head_h * .55,
            cx + head_w * .35, eye_y + eye_h * .20,
            cx + head_w * .22, head_top + head_h * .48,
            cx + head_w * .03, eye_y + eye_h * .35,
            cx - head_w * .20, head_top + head_h * .50,
            cx - head_w * .38, eye_y + eye_h * .20
        ], fill=colors["hair_base"], outline="", smooth=True)

        for side in (-1, 1):
            self.create_polygon([
                cx + side * head_w * .58, head_top + head_h * .48,
                cx + side * head_w * .65, head_top + head_h * .70,
                cx + side * head_w * .62, head_top + head_h * .82,
                cx + side * head_w * .55, head_top + head_h * .90,
                cx + side * head_w * .44, head_top + head_h * .77,
                cx + side * head_w * .35, head_top + head_h * .65,
                cx + side * head_w * .22, head_top + head_h * .46
            ], fill=colors["hair_shade"], outline="", smooth=True)


# ==================== 身材预览 PNG 渲染（未上传形象时替代角色形象） ====================

_PREVIEW_RENDER_W = 450     # 渲染宽（预览图导出用，放大 2 倍）
_PREVIEW_RENDER_H = 800     # 渲染高
_PREVIEW_RENDER_BG = (0, 0, 0, 0)   # 透明背景（RGBA）
_PREVIEW_AA = 4             # 超采样倍数：放大渲染后缩回目标尺寸以获得抗锯齿边缘
_PREVIEW_DESIGN_W = PresetCustomDialog.CANVAS_W   # 与身材对话框预览画布一致的设计宽，线宽按它等比放大


def _flatten_points(points: list) -> List[tuple]:
    """把 Tk 多边形/折线的点列表（扁平行或[(x,y),...]）规范为 [(x, y), ...]。"""
    if not points:
        return []
    if isinstance(points[0], (tuple, list)):
        return [(float(x), float(y)) for x, y in points]
    out = []
    for i in range(0, len(points) - 1, 2):
        out.append((float(points[i]), float(points[i + 1])))
    return out


def _tk_spline_points(pts: List[tuple], closed: bool = False,
                      steps: int = 16) -> List[tuple]:
    """Tk 画布 smooth=True 的样条精确复刻（经实测 Tk 8.6 输出验证）。

    Tk 以相邻顶点对中点为端点、顶点为控制点构造三次贝塞尔等价曲线：
    - 闭合（多边形，n 段）：从 p[i]~p[i+1] 中点 到 p[i+1]~p[i+2] 中点，控制点 p[i+1]（下标取模）；
    - 开放（折线，n-1 段）：首段 p0 -> p1~p2 中点（控制点 p1），
      末段 p[n-2]~p[n-1] 中点 -> p[n-1]（控制点 p[n-1]），中间段同闭合规则。
    返回展平后的折线点列（每段 steps 个采样点，端点不重复）。"""
    n = len(pts)
    if n < 2:
        return pts
    if n == 2:
        return [pts[0], pts[1]]

    def mid(a, b) -> tuple:
        return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)

    segs: List[tuple] = []
    if closed:
        for i in range(n):
            p0, p1, p2 = pts[i], pts[(i + 1) % n], pts[(i + 2) % n]
            segs.append((mid(p0, p1), p1, mid(p1, p2)))
    else:
        if n == 3:
            # Tk 特例：三点线为一条经过中间点的二次贝塞尔
            segs.append((pts[0], pts[1], pts[2]))
        else:
            segs.append((pts[0], pts[1], mid(pts[1], pts[2])))
            for i in range(1, n - 2):
                segs.append((mid(pts[i], pts[i + 1]), pts[i + 1], mid(pts[i + 1], pts[i + 2])))
            segs.append((mid(pts[n - 2], pts[n - 1]), pts[n - 1], pts[n - 1]))

    out: List[tuple] = []
    for s, c, e in segs:
        cx1 = s[0] + (c[0] - s[0]) * (2.0 / 3.0)
        cy1 = s[1] + (c[1] - s[1]) * (2.0 / 3.0)
        cx2 = e[0] + (c[0] - e[0]) * (2.0 / 3.0)
        cy2 = e[1] + (c[1] - e[1]) * (2.0 / 3.0)
        for k in range(steps):
            t = k / steps
            mt = 1.0 - t
            out.append((
                mt * mt * mt * s[0] + 3 * mt * mt * t * cx1
                + 3 * mt * t * t * cx2 + t * t * t * e[0],
                mt * mt * mt * s[1] + 3 * mt * mt * t * cy1
                + 3 * mt * t * t * cy2 + t * t * t * e[1],
            ))
    out.append((segs[-1][2][0], segs[-1][2][1]))
    return out


class BodyPreviewPilCanvas(BodyPreviewCanvas):
    """把 BodyPreviewCanvas 的绘制逻辑重定向到 PIL 图片，用于把身材预览生成为 PNG。

    复用父类全部部件绘制方法（refresh 及各 _draw_* 方法），仅把画布图元
    create_oval / create_polygon / create_line 改写为绘制到 PIL ImageDraw。
    """

    def __init__(self, width: int, height: int, background=_PREVIEW_RENDER_BG):
        self.preview_width = width
        self.preview_height = height
        self._current_values: Optional[dict] = None
        self._dark = ctk.get_appearance_mode().lower() == "dark"
        self._canvas_width = width
        self._canvas_height = height
        self._background = background
        # 线宽按设计宽等比放大，使 PNG（放大的画布）与对话框预览的线型粗细一致
        self._stroke_scale = width / float(_PREVIEW_DESIGN_W)
        self._image = None
        self._draw = None

    # ---- 让父类渲染逻辑按固定尺寸工作，而非查询真实 Tk 控件 ----
    def winfo_width(self) -> int:
        return self._canvas_width

    def winfo_height(self) -> int:
        return self._canvas_height

    def delete(self, *_args):
        pass

    def _dim_kwargs(self, kwargs):
        """与身材对话框预览一致：暗色模式下对亮色通道做曝光衰减。"""
        if not self._dark:
            return kwargs
        for key in ("fill", "outline"):
            color = kwargs.get(key)
            if color and _preview_dim_color(color) != color:
                kwargs[key] = _preview_dim_color(color)
        return kwargs

    @staticmethod
    def _pil_color(color):
        if not color or color == "":
            return None
        return color

    def _stroke_width(self, width) -> int:
        return max(1, int(round((width or 1) * self._stroke_scale)))

    # ---- 图元绘制到 PIL（等价复刻 Tk 画布的线宽/圆角/平滑规则） ----
    def create_oval(self, x1, y1, x2, y2, fill=None, outline=None, width=1, **kwargs):
        kwargs = self._dim_kwargs({"fill": fill, "outline": outline, "width": width})
        fill = kwargs["fill"]
        outline = kwargs["outline"]
        width = kwargs["width"]
        w = self._stroke_width(width)
        if fill:
            self._draw.ellipse([x1, y1, x2, y2], fill=self._pil_color(fill), outline=None)
        if outline and w > 0:
            # Tk 描边以边界线居中（内外各一半），PIL 描边在包围盒内侧，扩大包围盒模拟居中
            hw = w / 2.0
            self._draw.ellipse([x1 - hw, y1 - hw, x2 + hw, y2 + hw],
                               fill=None, outline=self._pil_color(outline), width=w)

    def create_polygon(self, points, fill=None, outline=None, width=1,
                       smooth=False, splinesteps=None, joinstyle="round", **kwargs):
        kwargs = self._dim_kwargs({"fill": fill, "outline": outline, "width": width})
        fill = kwargs["fill"]
        outline = kwargs["outline"]
        width = kwargs["width"]
        pts = _flatten_points(points)
        if smooth:
            pts = _tk_spline_points(pts, closed=True)
        w = self._stroke_width(width)
        if len(pts) < 3:
            self._draw.line(
                pts, fill=self._pil_color(fill) or "black",
                width=w, joint="curve")
            return None
        self._draw.polygon(
            pts, fill=self._pil_color(fill),
            outline=self._pil_color(outline), width=w)
        return None

    def create_line(self, points, fill=None, width=1, smooth=False,
                    capstyle="butt", joinstyle="round", **kwargs):
        kwargs = self._dim_kwargs({"fill": fill, "width": width, "capstyle": capstyle})
        fill = kwargs["fill"]
        width = kwargs["width"]
        capstyle = kwargs["capstyle"]
        pts = _flatten_points(points)
        if smooth and len(pts) >= 3:
            pts = _tk_spline_points(pts, closed=False)
        color = self._pil_color(fill) or "black"
        w = self._stroke_width(width)
        if len(pts) < 2:
            if pts:
                self._draw.point(pts, fill=color)
            return None
        self._draw.line(pts, fill=color, width=w, joint="curve")
        if capstyle == "round" and w > 1:
            # PIL 线条端点平头，Tk round 端点用端点圆补上圆头
            r = w / 2.0
            for p in (pts[0], pts[-1]):
                self._draw.ellipse(
                    [p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=color)
        return None

    def refresh(self):
        if not self._current_values:
            return
        self._image = Image.new("RGBA", (self._canvas_width, self._canvas_height),
                                self._background)
        self._draw = ImageDraw.Draw(self._image, "RGBA")
        BodyPreviewCanvas.refresh(self)


def body_ratio_values(body_parts: dict, height: float) -> dict:
    """把按身高换算的绝对部位值还原为预览画布所需的比例值（身高等同缩放）。

    与 CreationService.get_body_parts 互为逆运算：part / height 即预设比例，
    进阶后身材比例不变。
    """
    values = {}
    safe = height or 1.6
    for cn, (attr, *_) in _PART_PARAMS.items():
        part = body_parts.get(cn)
        if part:
            values[attr] = part / safe
    return values


def render_body_preview_image(body_parts: dict, height: float = 1.6,
                              width: int = _PREVIEW_RENDER_W,
                              height_px: int = _PREVIEW_RENDER_H) -> Optional[Image.Image]:
    """根据身材部位数据渲染身材比例预览图（RGBA PIL 图片，背景透明）。

    仅需“与身高成正比”的部位值，身高只用于把绝对尺度还原为比例。
    以超采样倍数放大绘制后 Lanczos 缩回目标尺寸，得到与对话框预览一致的抗锯齿边缘。
    """
    if not body_parts:
        return None
    aw = width * _PREVIEW_AA
    ah = height_px * _PREVIEW_AA
    canvas = BodyPreviewPilCanvas(aw, ah, _PREVIEW_RENDER_BG)
    canvas.update_values(body_ratio_values(body_parts, height))
    img = canvas._image
    if img is None:
        return None
    if _PREVIEW_AA > 1 and (aw, ah) != (width, height_px):
        img = img.resize((width, height_px), Image.Resampling.LANCZOS)
    return img


def render_body_preview_to_file(body_parts: dict, height: float, out_path: str) -> str:
    """将身材比例预览渲染为 PNG 写入 out_path，返回路径；失败返回 ''。"""
    img = render_body_preview_image(body_parts, height)
    if img is None:
        return ""
    try:
        img.save(out_path, "PNG")
        return out_path
    except Exception:
        return ""