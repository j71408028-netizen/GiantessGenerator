"""文本卡片（text_card）：底部卡片式（悬浮圆角半透明卡片）"""

import math

from imgui_bundle import imgui

from dungeon.window import imgui_util as iu
from dungeon.window.fonts import TEXT_FONT_SIZE
from dungeon.window.layout import (SIDE_LEFT, SIDE_RIGHT, SHAPE_BAND,
                                   SHAPE_RAIL, SURFACE_HOST, SURFACE_OWN,
                                   Slot, SlotStyle)

from .base import (_DEFAULT_LABEL_COLOR, _DEFAULT_TEXT_COLOR,
                   _HIGHLIGHT_COLOR, _TextDisplayBase)

# 侧边代管区（log 组件展开时启用）：贴视口一侧，卡片往另一侧让位。
_DOCK_MARGIN = 20
_DOCK_WIDTH = 320
_DOCK_GAP = 14
_DOCK_MIN_HEIGHT = 300
_INDICATOR_WIDTH = 34
# 卡片让位 / 归位的平滑时间常数（秒）：约 4 个时间常数后到位。
_SHIFT_TAU = 0.06


class CardTextComponent(_TextDisplayBase):
    """底部卡片式文本栏：底部居中的圆角半透明卡片上显示最近几条正文。"""

    id = "text_card"
    label = "底部卡片式文本栏"
    description = "底部居中的圆角半透明卡片上浮现最近几句正文。"
    _BLINK_TASK = "text_card:blink"
    TAG_PREFIX = "text_card"
    param_specs = [
        {"key": "width_percent", "label": "卡片宽度%", "type": "int",
         "default": 70, "min": 30, "max": 95, "note": "视口宽度的百分比，30~95"},
        {"key": "max_lines", "label": "显示行数", "type": "int",
         "default": 1, "min": 1, "max": 5, "note": "最近 N 个显示段落，1~5"},
        {"key": "bg_color", "label": "卡片底色", "type": "color",
         "default": (20, 24, 40, 190)},
        {"key": "border_color", "label": "边框颜色", "type": "color",
         "default": (255, 255, 255, 45)},
        {"key": "label_color", "label": "标签颜色", "type": "color",
         "default": _DEFAULT_LABEL_COLOR},
        {"key": "highlight_color", "label": "高亮颜色", "type": "color",
         "default": _HIGHLIGHT_COLOR,
         "note": "highlight 段落的正文颜色"},
        {"key": "show_indicator", "label": "显示继续指示", "type": "bool",
         "default": True},
        {"key": "dock_on_right", "label": "代管区改到右侧", "type": "bool",
         "default": False,
         "note": "反转左右布局：日志面板停靠右侧，卡片改为左移让位"},
    ]

    def __init__(self, ctx):
        super().__init__(ctx)
        self._layout = None
        self._shift_current = 0.0

    def _geometry(self, ctx):
        s, w, h = ctx.component_viewport()
        params = self.params or {}
        pad = round(16 * s)
        max_lines = max(1, min(5, int(params.get("max_lines") or 1)))
        percent = min(95, max(30, int(params.get("width_percent") or 70)))
        base_w = max(round(320 * s), round(w * percent / 100.0))
        line_h = round((TEXT_FONT_SIZE + 10) * s)
        roles = tuple(ctx.component_roles() or ())
        # 只有本会话真的有对应组件时才留代管区，否则不凭空长高 / 偏移。
        has_status = "status" in roles
        has_log = "log" in roles
        slot_h = round(34 * s) if has_status else 0
        slot_gap = round(10 * s) if has_status else 0
        card_h = min(round(h * 0.6),
                     2 * pad + max_lines * (3 * line_h + round(8 * s))
                     + slot_h + slot_gap)
        base_x = max(0.0, (w - base_w) / 2.0)
        card_y = max(0.0, h - card_h - round(24 * s))

        margin = round(_DOCK_MARGIN * s)
        dock_w = round(_DOCK_WIDTH * s)
        dock_gap = round(_DOCK_GAP * s)
        dock_rect = None
        # 左右反转：代管区始终贴视口一侧，卡片往另一侧让位。
        side = SIDE_RIGHT if bool(params.get("dock_on_right")) else SIDE_LEFT
        # 视口放不下贴边代管区时不声明它，日志组件回退自己的右上角面板。
        use_dock = (has_log and w - 2 * margin - dock_w - dock_gap
                    >= round(200 * s))
        if use_dock:
            # 卡片让出代管区后仍要留在另一侧边界内；窗口太窄时先缩卡片。
            card_w = min(base_w, max(1.0, w - 2 * margin - dock_w - dock_gap))
            if side == SIDE_LEFT:
                card_x = min(base_x + dock_w + dock_gap, w - margin - card_w)
                card_x = max(card_x, margin + dock_w + dock_gap)
                dock_x = float(margin)
            else:
                card_x = max(base_x - dock_w - dock_gap, margin)
                card_x = min(card_x,
                             w - margin - dock_w - dock_gap - card_w)
                dock_x = float(w - margin - dock_w)
            # 代管区底端与卡片对齐，短卡片时向上补到最小高度。
            dock_h = min(max(card_h, round(_DOCK_MIN_HEIGHT * s)),
                         max(1.0, card_y + card_h - margin))
            dock_rect = (dock_x, float(card_y + card_h - dock_h),
                         float(dock_w), float(dock_h))
        else:
            card_w, card_x = base_w, base_x
        # 右下角继续指示占用代管区右端，状态行内容宽度相应让位。
        slot_w = max(1.0, card_w - 2 * pad
                     - (round(_INDICATOR_WIDTH * s) if has_status else 0))
        footer_rect = (card_x + pad, card_y + card_h - pad - slot_h,
                       slot_w, float(slot_h))
        return (s, pad, card_w, card_h, card_x, card_y, max_lines,
                footer_rect, dock_rect, card_x - base_x)

    def layout(self, ctx):
        self._layout = self._geometry(ctx)
        (s, _pad, card_w, card_h, card_x, card_y, _max_lines,
         footer_rect, dock_rect, _shift) = self._layout
        self._panel_rect = (card_x, card_y, card_w, card_h)
        params = self.params or {}
        # 代管区对象跨 layout 复用：refresh 只重算几何、不重新分配时，
        # 子组件手里的 Slot 引用仍然指向同一对象，让位动画的 offset_x 才不会断。
        previous = {slot.id: slot for slot in self._slots}
        self._slots = []
        if footer_rect[3] > 0:
            style = SlotStyle(
                surface=SURFACE_HOST,
                separator=(255, 255, 255, 30),
                separator_side="top",
                padding=(round(10 * s), round(6 * s),
                         round(10 * s), round(6 * s)),
                label_color=params.get("label_color") or _DEFAULT_LABEL_COLOR,
                text_color=_DEFAULT_TEXT_COLOR,
                highlight_color=params.get("highlight_color") or _HIGHLIGHT_COLOR,
            )
            self._slots.append(self._reuse_slot(
                previous, "card_footer", SHAPE_BAND, footer_rect,
                roles=("status",), style=style))
        if dock_rect is not None:
            # log 组件自带面板（own），卡片只提供外框；从代管区所贴的屏幕边缘滑入。
            side = SIDE_RIGHT if bool(params.get("dock_on_right")) else SIDE_LEFT
            self._slots.append(self._reuse_slot(
                previous, "card_side", SHAPE_RAIL, dock_rect,
                roles=("log",),
                style=SlotStyle(surface=SURFACE_OWN,
                                padding=(0.0, 0.0, 0.0, 0.0),
                                side=side)))

    @staticmethod
    def _reuse_slot(previous, sid, shape, rect, roles=(), style=None):
        slot = previous.pop(sid, None)
        if slot is None:
            return Slot(sid, shape, rect, roles=roles, style=style)
        slot.shape = shape
        slot.rect = tuple(float(v) for v in rect)
        slot.roles = tuple(roles or ())
        slot.style = style or SlotStyle()
        slot.used = 0
        slot.offset_x = 0.0
        return slot

    def draw(self, ctx):
        if self._layout is None:
            self.layout(ctx)
        (s, pad, card_w, card_h, card_x, card_y, max_lines,
         footer_rect, _dock_rect, shift) = self._layout
        # 让位 / 归位都走同一个平滑量：坐标以 layout 的最终位置为基准，
        # dx 是当前帧相对最终位置的偏移（开场为负、收起为正）。
        dt = min(float(imgui.get_io().delta_time), 0.1)
        self._shift_current += ((shift - self._shift_current)
                                * (1.0 - math.exp(-dt / _SHIFT_TAU)))
        if abs(shift - self._shift_current) < 0.5:
            self._shift_current = shift
        dx = self._shift_current - shift
        card_x += dx
        for slot in self._slots:
            # 卡片里的代管区跟着卡片走；贴屏幕边缘的侧边代管区不动。
            slot.offset_x = dx if slot.id == "card_footer" else 0.0
        params = self.params or {}
        pmin, pmax = iu.v2(card_x, card_y), iu.v2(card_x + card_w, card_y + card_h)
        draw_list = imgui.get_window_draw_list()
        radius = round(14 * s)
        draw_list.add_rect_filled(pmin, pmax, iu.u32(*(params.get("bg_color") or (20, 24, 40, 190))), radius)
        draw_list.add_rect(pmin, pmax, iu.u32(*(params.get("border_color") or (255, 255, 255, 45))), radius)
        for slot in self._slots:
            self._draw_slot_chrome(slot, s, slot.offset_x)
        items = self._visible_items(ctx, max_lines)
        self._draw_text_lines(ctx, card_x + pad, card_y + pad,
                              card_x + card_w - pad, items,
                              params.get("label_color") or _DEFAULT_LABEL_COLOR,
                              _DEFAULT_TEXT_COLOR,
                              params.get("highlight_color") or _HIGHLIGHT_COLOR)
        bar_w, bar_h = self._toolbar_size(s)
        self._draw_toolbar(ctx, card_x + card_w - round(10 * s) - bar_w,
                           card_y - bar_h / 2.0)
        if footer_rect[3] > 0:
            self._draw_indicator(ctx,
                                 card_x + card_w - round(28 * s),
                                 footer_rect[1] + round(8 * s))
        else:
            self._draw_indicator(ctx,
                                 card_x + card_w - round(46 * s),
                                 card_y + card_h - round(40 * s))
