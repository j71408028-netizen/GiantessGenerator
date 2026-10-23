"""文本栏（text）：底部渐变式（galgame ADV 风格）"""

from imgui_bundle import imgui

from dungeon.window import imgui_util as iu
from dungeon.window.layout import (ENTER_FROM_BOTTOM, SHAPE_BAND, SHAPE_RAIL,
                                   SIDE_LEFT, SIDE_RIGHT, SURFACE_BARE,
                                   SURFACE_OWN, Slot, SlotStyle)

from .base import (_DEFAULT_LABEL_COLOR, _DEFAULT_TEXT_COLOR,
                   _HIGHLIGHT_COLOR, _MAX_TEXT_WIDTH, _TOOLBAR_MARGIN,
                   _TextDisplayBase)


_GRAD_DEFAULT_COLOR = (10, 12, 22, 128)
# 侧边代管区（log 组件展开时启用）：对齐正文列的一侧，停在渐变区上方。
_DOCK_MARGIN = 20
_DOCK_WIDTH = 320
_DOCK_MIN_HEIGHT = 300
_DOCK_MIN_USABLE = 160
_DOCK_GAP = 10


class TextComponent(_TextDisplayBase):
    """底部渐变式文本栏：底部向上淡出的深色衬底上显示最近几条正文。"""

    id = "text"
    label = "底部渐变式文本栏"
    description = "视口底部向上淡出的深色渐变衬底上显示最近几句正文（ADV 风格）。"
    _BLINK_TASK = "text_gradient:blink"
    TAG_PREFIX = "text_gradient"
    param_specs = [
        {"key": "height_percent", "label": "渐变区高度%", "type": "int",
         "default": 45, "min": 15, "max": 80, "note": "视口高度的百分比，15~80"},
        {"key": "max_lines", "label": "显示行数", "type": "int",
         "default": 1, "min": 1, "max": 5, "note": "最近 N 个显示段落，1~5"},
        {"key": "gradient_color", "label": "渐变底部颜色", "type": "color",
         "default": _GRAD_DEFAULT_COLOR,
         "note": "衬底最底处的颜色（含透明度），向上渐隐"},
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
        percent = int(params.get("height_percent") or 45)
        percent = min(80, max(15, percent))
        grad_h = max(round(h * 0.15), round(h * percent / 100.0))
        pad = round(15 * s)
        # 正文列有可读宽度上限，大屏上居中而不是铺满整条窗口。
        text_w = min(max(round(300 * s), w - 2 * round(40 * s)),
                     round(_MAX_TEXT_WIDTH * s))
        text_top = h - grad_h + round(grad_h * 0.25)
        text_h = max(round(80 * s), h - text_top - round(20 * s))
        box_x = round((w - text_w) / 2.0)
        box_y = max(0.0, h - round(20 * s) - text_h)
        # 正文上方的状态带：无底纹（bare），只借用渐变衬底上的文字颜色。
        roles = tuple(ctx.component_roles() or ())
        has_status = "status" in roles
        slot_h = round(30 * s) if has_status else 0
        slot_rect = (box_x + pad, max(0.0, box_y - round(8 * s) - slot_h),
                     max(1.0, text_w - 2 * pad), float(slot_h))
        # 停靠侧是窗口视觉设置（玩家实时改），不是方案参数。
        side = SIDE_RIGHT if ctx.component_dock_side() == SIDE_RIGHT else SIDE_LEFT
        dock_rect = self._dock_rect(s, w, h, grad_h, slot_rect, has_status,
                                    "log" in roles, side, box_x, text_w)
        return (s, w, h, grad_h, box_x, box_y, text_w, text_h, pad,
                slot_rect, dock_rect, side)

    @staticmethod
    def _dock_rect(s, w, h, grad_h, slot_rect, has_status, has_log, side,
                   box_x, text_w):
        """侧边代管区矩形：对齐正文列一侧，底端停在渐变区（含状态带）上方。

        渐变正文没有「卡片让位」那种横向余量，代管区改为向上占用正文上方
        空间，横向与正文列同侧对齐；放不下就返回 ``None``，日志回退右上角面板。
        """
        if not has_log:
            return None
        margin = round(_DOCK_MARGIN * s)
        dock_w = round(_DOCK_WIDTH * s)
        top_limit = float(h - grad_h)
        if has_status:
            top_limit = min(top_limit, float(slot_rect[1]) - round(_DOCK_GAP * s))
        available = top_limit - margin
        if side == SIDE_LEFT:
            x = float(box_x)
        else:
            x = float(box_x + text_w - dock_w)
        if (text_w < dock_w or x < 0 or x + dock_w > w + 0.5
                or available < round(_DOCK_MIN_USABLE * s)):
            return None
        dock_h = min(round(_DOCK_MIN_HEIGHT * s), available)
        return (x, float(top_limit - dock_h), float(dock_w), float(dock_h))

    def layout(self, ctx):
        self._layout = self._geometry(ctx)
        (s, _w, _h, _grad_h, _bx, _by, _tw, _th, _pad, slot_rect,
         dock_rect, dock_side) = self._layout
        self._panel_rect = (_bx, _by, _tw, _th)
        params = self.params or {}
        self._slots = []
        if slot_rect[3] > 0:
            self._slots.append(Slot(
                "scrim_band", SHAPE_BAND, slot_rect,
                roles=("status",),
                style=SlotStyle(
                    surface=SURFACE_BARE,
                    padding=(round(8 * s), round(4 * s),
                             round(8 * s), round(4 * s)),
                    label_color=params.get("label_color") or _DEFAULT_LABEL_COLOR,
                    text_color=_DEFAULT_TEXT_COLOR,
                    highlight_color=params.get("highlight_color") or _HIGHLIGHT_COLOR,
                ),
            ))
        if dock_rect is not None:
            # 日志自带面板（own）；与卡片的侧边代管区不同，它不从屏幕侧边推入，
            # 而是从代管区下方升起并淡入（见 layout.enter_transform）。
            self._slots.append(Slot(
                "scrim_side", SHAPE_RAIL, dock_rect,
                roles=("log",),
                style=SlotStyle(
                    surface=SURFACE_OWN,
                    padding=(0.0, 0.0, 0.0, 0.0),
                    side=dock_side,
                    enter_from=ENTER_FROM_BOTTOM,
                    enter_fade=True,
                    enter_seconds=0.34,
                ),
            ))

    def draw(self, ctx):
        if self._layout is None:
            self.layout(ctx)
        (s, w, h, grad_h, bx, by, tw, th, pad, _slot_rect,
         _dock_rect, _dock_side) = self._layout
        params = self.params or {}
        color = params.get("gradient_color") or _GRAD_DEFAULT_COLOR
        draw_list = imgui.get_window_draw_list()
        top = iu.u32(color[0], color[1], color[2], 0)
        bottom = iu.u32(*color)
        draw_list.add_rect_filled_multi_color(iu.v2(0.0, h - grad_h), iu.v2(w, h),
                                              top, top, bottom, bottom)
        for slot in self._slots:
            self._draw_slot_chrome(slot, s)
        max_lines = max(1, min(5, int(params.get("max_lines") or 1)))
        items = self._visible_items(ctx, max_lines)
        self._draw_text_lines(ctx, bx + pad, by + pad, bx + tw - pad, items,
                              params.get("label_color") or _DEFAULT_LABEL_COLOR,
                              _DEFAULT_TEXT_COLOR,
                              params.get("highlight_color") or _HIGHLIGHT_COLOR)
        bar_w, bar_h = self._toolbar_size(s)
        self._draw_toolbar(ctx, bx + tw - bar_w - round(_TOOLBAR_MARGIN * s),
                           h - bar_h - round(10 * s))
        self._draw_indicator(ctx,
                             bx + tw - round(46 * s) - bar_w - round(16 * s),
                             by + th - round(44 * s))
