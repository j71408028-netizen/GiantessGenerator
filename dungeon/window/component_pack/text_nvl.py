"""全屏 NVL（text_nvl）：半透明阅读覆盖层，可留百分比边距、可停靠侧边代管区"""

from imgui_bundle import imgui

from dungeon.window import imgui_util as iu
from dungeon.window.layout import (SHAPE_BAND, SHAPE_RAIL, SIDE_LEFT,
                                   SIDE_RIGHT, SURFACE_HOST, Slot, SlotStyle)

from .base import (_DEFAULT_LABEL_COLOR, _DEFAULT_TEXT_COLOR,
                   _HIGHLIGHT_COLOR, _MAX_TEXT_WIDTH, _TOOLBAR_MARGIN,
                   _TextDisplayBase)

# 侧边代管区（log 组件展开时启用）：贴着正文列的一侧、占满覆盖层高度，
# 正文列有可读宽度上限；两区共用覆盖层底纹（融合），或由日志自绘面板。
_DOCK_WIDTH = 320
_DOCK_GAP = 18
_DOCK_MIN_TEXT = 240
#: 覆盖层边距允许的最大百分比（再大就只剩一条缝，正文没地方放）
_MARGIN_MAX = 30


class NvlTextComponent(_TextDisplayBase):
    """全屏 NVL 式文本栏：半透明覆盖层上堆叠显示全部历史段落。"""

    id = "text_nvl"
    label = "全屏 NVL 文本栏"
    description = "半透明覆盖层上堆叠全部历史段落，可滚轮回看（阅读模式）。"
    _BLINK_TASK = "text_nvl:blink"
    TAG_PREFIX = "text_nvl"
    param_specs = [
        {"key": "margin_percent", "label": "窗口边距%", "type": "int",
         "default": 0, "min": 0, "max": _MARGIN_MAX,
         "note": "覆盖层四周留白占视口对应边长的百分比，0 = 全屏"},
        {"key": "bg_color", "label": "覆盖层底色", "type": "color",
         "default": (8, 10, 18, 190)},
        {"key": "label_color", "label": "标签颜色", "type": "color",
         "default": _DEFAULT_LABEL_COLOR},
        {"key": "highlight_color", "label": "高亮颜色", "type": "color",
         "default": _HIGHLIGHT_COLOR,
         "note": "highlight 段落的正文颜色"},
        {"key": "use_serif", "label": "衬线字体", "type": "bool",
         "default": True, "note": "宋体等衬线字体（找不到时回退默认字体）"},
        {"key": "show_indicator", "label": "显示继续指示", "type": "bool",
         "default": True},
    ]

    def __init__(self, ctx):
        super().__init__(ctx)
        self._layout = None

    def _overlay_rect(self, w, h):
        """覆盖层矩形：按窗口边长百分比向内收；0% 与旧版全屏完全一致。"""
        percent = int((self.params or {}).get("margin_percent") or 0)
        percent = max(0, min(_MARGIN_MAX, percent))
        margin_x = round(w * percent / 100.0)
        margin_y = round(h * percent / 100.0)
        return (float(margin_x), float(margin_y),
                max(1.0, float(w - 2 * margin_x)),
                max(1.0, float(h - 2 * margin_y))), percent

    def _geometry(self, ctx):
        s, w, h = ctx.component_viewport()
        panel, _percent = self._overlay_rect(w, h)
        px, py, pw, ph = panel
        pad = round(24 * s)
        # 顶部代管区取代旧的 top_inset 让位：高度至少覆盖状态条的自报占位，
        # 正文从这个带的下方开始，滚动内容不会压住它。没有 status 组件时
        # 完全退回旧行为，不凭空留一条空带。
        roles = tuple(ctx.component_roles() or ())
        has_status = "status" in roles
        header_h = round(36 * s) if has_status else 0
        slot_gap = round(10 * s) if has_status else 0
        # top_inset 是相对视口顶端的值；覆盖层有上边距时只补差额。
        inset = max(0, int(ctx.component_top_inset() or 0) - round(py))
        pad_top = max(pad + header_h + slot_gap, inset)
        if has_status:
            header_h = max(header_h, pad_top - pad - slot_gap)

        # 侧边代管区：占满覆盖层高度，横向贴着正文列；正文列有可读宽度上限，
        # 有日志时「日志 + 正文」作为一组在覆盖层里居中（大屏上不会分居两端）。
        side = SIDE_RIGHT if ctx.component_dock_side() == SIDE_RIGHT else SIDE_LEFT
        dock_w = round(_DOCK_WIDTH * s)
        dock_gap = round(_DOCK_GAP * s)
        text_w = float(min(pw, round(_MAX_TEXT_WIDTH * s)))
        text_x = px + (pw - text_w) / 2.0
        dock_rect = None
        if ("log" in roles and pw >= dock_w + dock_gap
                + round(_DOCK_MIN_TEXT * s)):
            text_w = float(min(round(_MAX_TEXT_WIDTH * s),
                               pw - dock_w - dock_gap))
            group_x = px + (pw - (dock_w + dock_gap + text_w)) / 2.0
            if side == SIDE_LEFT:
                dock_rect = (group_x, py, float(dock_w), ph)
                text_x = group_x + dock_w + dock_gap
            else:
                text_x = group_x
                dock_rect = (group_x + text_w + dock_gap, py,
                             float(dock_w), ph)

        header_rect = (text_x + pad, py + pad,
                       max(1.0, text_w - 2 * pad), float(header_h))
        wrap = max(1.0, text_w - 2 * pad - round(14 * s))
        return (s, w, h, panel, pad, pad_top, wrap, header_rect,
                dock_rect, side, text_x, text_w)

    def layout(self, ctx):
        self._layout = self._geometry(ctx)
        (s, _w, _h, panel, _pad, _pad_top, _wrap, header_rect,
         dock_rect, side, _text_x, _text_w) = self._layout
        self._panel_rect = panel
        params = self.params or {}
        self._slots = []
        if header_rect[3] > 0:
            self._slots.append(Slot(
                "nvl_header", SHAPE_BAND, header_rect,
                roles=("status",),
                style=SlotStyle(
                    surface=SURFACE_HOST,
                    bg=(10, 12, 20, 120),
                    separator=(255, 255, 255, 36),
                    separator_side="bottom",
                    radius=round(8 * s),
                    padding=(round(12 * s), round(8 * s),
                             round(12 * s), round(8 * s)),
                    label_color=params.get("label_color") or _DEFAULT_LABEL_COLOR,
                    text_color=_DEFAULT_TEXT_COLOR,
                    highlight_color=params.get("highlight_color") or _HIGHLIGHT_COLOR,
                ),
            ))
        if dock_rect is not None:
            # surface=host 但不给底纹 / 边框 / 分隔线：日志区与正文区共用
            # 覆盖层那一层半透明底纹，两区背景完全融合（日志仍可经自己的
            # draw_own_surface 否决，自绘独立面板）。
            self._slots.append(Slot(
                "nvl_side", SHAPE_RAIL, dock_rect,
                roles=("log",),
                style=SlotStyle(
                    surface=SURFACE_HOST,
                    padding=(round(12 * s), round(10 * s),
                             round(12 * s), round(10 * s)),
                    text_color=_DEFAULT_TEXT_COLOR,
                    highlight_color=params.get("highlight_color") or _HIGHLIGHT_COLOR,
                    side=side,
                ),
            ))

    def draw(self, ctx):
        if self._layout is None:
            self.layout(ctx)
        (s, _w, _h, panel, pad, pad_top, _wrap, _header_rect,
         _dock_rect, _side, text_x, text_w) = self._layout
        px, py, pw, ph = panel
        params = self.params or {}
        bg = params.get("bg_color") or (8, 10, 18, 190)
        draw_list = imgui.get_window_draw_list()
        # 全屏时不描圆角（贴边没有意义），留边距后给覆盖层一点圆角。
        radius = round(12 * s) if (px > 0.5 or py > 0.5) else 0.0
        draw_list.add_rect_filled(iu.v2(px, py), iu.v2(px + pw, py + ph),
                                  iu.u32(*bg), radius)
        for slot in self._slots:
            self._draw_slot_chrome(slot, s)
        imgui.set_cursor_pos(iu.v2(px, py))
        imgui.begin_child(self.TAG_PREFIX + "##scroll", iu.v2(pw, ph),
                          imgui.ChildFlags_.none.value,
                          imgui.WindowFlags_.none.value)
        items = self._visible_items(ctx)
        # 滚动子窗口的局部原点就是覆盖层左上角，正文坐标要扣掉面板偏移。
        text_local_x = text_x - px
        self._draw_text_lines(ctx, text_local_x + pad, pad_top,
                              text_local_x + text_w - pad - round(14 * s),
                              items,
                              params.get("label_color") or _DEFAULT_LABEL_COLOR,
                              _DEFAULT_TEXT_COLOR,
                              params.get("highlight_color") or _HIGHLIGHT_COLOR,
                              label_fmt="{label}")
        imgui.set_scroll_here_y(1.0)
        imgui.end_child()
        bar_w, _bar_h = self._toolbar_size(s)
        # 工具条与继续指示都居中在正文区（有侧边代管区时自动避开日志列）。
        self._draw_toolbar(ctx, text_x + text_w / 2.0 - bar_w / 2.0,
                           py + _TOOLBAR_MARGIN * s)
        self._draw_indicator(ctx, text_x + text_w / 2.0 - 80 * s,
                             py + ph - round(44 * s), "▼ 点击继续 ▼")
