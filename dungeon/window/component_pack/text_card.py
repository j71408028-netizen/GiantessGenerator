"""文本卡片（text_card）：底部卡片式（悬浮圆角半透明卡片）"""

from imgui_bundle import imgui

from dungeon.window import imgui_util as iu
from dungeon.window.fonts import TEXT_FONT_SIZE
from dungeon.window.layout import SHAPE_BAND, SURFACE_HOST, Slot, SlotStyle

from .base import (_DEFAULT_LABEL_COLOR, _DEFAULT_TEXT_COLOR,
                   _HIGHLIGHT_COLOR, _TextDisplayBase)


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
    ]

    def __init__(self, ctx):
        super().__init__(ctx)
        self._layout = None

    def _geometry(self, ctx):
        s, w, h = ctx.component_viewport()
        params = self.params or {}
        pad = round(16 * s)
        max_lines = max(1, min(5, int(params.get("max_lines") or 1)))
        percent = min(95, max(30, int(params.get("width_percent") or 70)))
        card_w = max(round(320 * s), round(w * percent / 100.0))
        line_h = round((TEXT_FONT_SIZE + 10) * s)
        # 只有本会话真的有 status 组件时才留底部代管区；否则不凭空长高。
        has_status = "status" in tuple(ctx.component_roles() or ())
        slot_h = round(34 * s) if has_status else 0
        slot_gap = round(10 * s) if has_status else 0
        card_h = min(round(h * 0.6),
                     2 * pad + max_lines * (3 * line_h + round(8 * s))
                     + slot_h + slot_gap)
        card_x = max(0.0, (w - card_w) / 2.0)
        card_y = max(0.0, h - card_h - round(24 * s))
        # 右下角继续指示占用代管区右端，状态行内容宽度相应让位。
        slot_w = max(1.0, card_w - 2 * pad - (round(34 * s) if has_status else 0))
        slot_rect = (card_x + pad, card_y + card_h - pad - slot_h,
                     slot_w, float(slot_h))
        return s, pad, card_w, card_h, card_x, card_y, max_lines, slot_rect

    def layout(self, ctx):
        self._layout = self._geometry(ctx)
        (s, _pad, card_w, card_h, card_x, card_y, _max_lines,
         slot_rect) = self._layout
        self._panel_rect = (card_x, card_y, card_w, card_h)
        params = self.params or {}
        self._slots = []
        if slot_rect[3] > 0:
            self._slots = [Slot(
                "card_footer", SHAPE_BAND, slot_rect,
                roles=("status",),
                style=SlotStyle(
                    surface=SURFACE_HOST,
                    separator=(255, 255, 255, 30),
                    separator_side="top",
                    padding=(round(10 * s), round(6 * s),
                             round(10 * s), round(6 * s)),
                    label_color=params.get("label_color") or _DEFAULT_LABEL_COLOR,
                    text_color=_DEFAULT_TEXT_COLOR,
                    highlight_color=params.get("highlight_color") or _HIGHLIGHT_COLOR,
                ),
            )]

    def draw(self, ctx):
        if self._layout is None:
            self.layout(ctx)
        (s, pad, card_w, card_h, card_x, card_y, max_lines,
         _slot_rect) = self._layout
        params = self.params or {}
        pmin, pmax = iu.v2(card_x, card_y), iu.v2(card_x + card_w, card_y + card_h)
        draw_list = imgui.get_window_draw_list()
        radius = round(14 * s)
        draw_list.add_rect_filled(pmin, pmax, iu.u32(*(params.get("bg_color") or (20, 24, 40, 190))), radius)
        draw_list.add_rect(pmin, pmax, iu.u32(*(params.get("border_color") or (255, 255, 255, 45))), radius)
        for slot in self._slots:
            self._draw_slot_chrome(slot, s)
        items = self._visible_items(ctx, max_lines)
        self._draw_text_lines(ctx, card_x + pad, card_y + pad,
                              card_x + card_w - pad, items,
                              params.get("label_color") or _DEFAULT_LABEL_COLOR,
                              _DEFAULT_TEXT_COLOR,
                              params.get("highlight_color") or _HIGHLIGHT_COLOR)
        bar_w, bar_h = self._toolbar_size(s)
        self._draw_toolbar(ctx, card_x + card_w - round(10 * s) - bar_w,
                           card_y - bar_h / 2.0)
        if _slot_rect[3] > 0:
            self._draw_indicator(ctx,
                                 card_x + card_w - round(28 * s),
                                 _slot_rect[1] + round(8 * s))
        else:
            self._draw_indicator(ctx,
                                 card_x + card_w - round(46 * s),
                                 card_y + card_h - round(40 * s))
