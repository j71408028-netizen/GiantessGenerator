"""全屏 NVL（text_nvl）：全屏半透明覆盖层上堆叠全部历史（阅读模式）"""

from imgui_bundle import imgui

from dungeon.window import imgui_util as iu
from dungeon.window.layout import SHAPE_BAND, SURFACE_HOST, Slot, SlotStyle

from .base import (_DEFAULT_LABEL_COLOR, _DEFAULT_TEXT_COLOR,
                   _HIGHLIGHT_COLOR, _TOOLBAR_MARGIN, _TextDisplayBase)


class NvlTextComponent(_TextDisplayBase):
    """全屏 NVL 式文本栏：全屏半透明覆盖层上堆叠显示全部历史段落。"""

    id = "text_nvl"
    label = "全屏 NVL 文本栏"
    description = "全屏半透明覆盖层上堆叠全部历史段落，可滚轮回看（阅读模式）。"
    _BLINK_TASK = "text_nvl:blink"
    TAG_PREFIX = "text_nvl"
    param_specs = [
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

    def _geometry(self, ctx):
        s, w, h = ctx.component_viewport()
        pad = round(24 * s)
        # 顶部代管区取代旧的 top_inset 让位：高度至少覆盖状态条的自报占位，
        # 正文从这个带的下方开始，滚动内容不会压住它。没有 status 组件时
        # 完全退回旧行为，不凭空留一条空带。
        has_status = "status" in tuple(ctx.component_roles() or ())
        header_h = round(36 * s) if has_status else 0
        slot_gap = round(10 * s) if has_status else 0
        pad_top = max(pad + header_h + slot_gap,
                      int(ctx.component_top_inset() or 0))
        if has_status:
            header_h = max(header_h, pad_top - pad - slot_gap)
        header_rect = (pad, pad, max(1.0, w - 2 * pad), float(header_h))
        wrap = max(1.0, w - 2 * pad - round(14 * s))
        return s, w, h, pad, pad_top, wrap, header_rect

    def layout(self, ctx):
        self._layout = self._geometry(ctx)
        s, w, h, _pad, _pad_top, _wrap, header_rect = self._layout
        self._panel_rect = (0.0, 0.0, w, h)
        params = self.params or {}
        self._slots = []
        if header_rect[3] > 0:
            self._slots = [Slot(
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
            )]

    def draw(self, ctx):
        if self._layout is None:
            self.layout(ctx)
        s, w, h, pad, pad_top, wrap, _header_rect = self._layout
        params = self.params or {}
        bg = params.get("bg_color") or (8, 10, 18, 190)
        draw_list = imgui.get_window_draw_list()
        draw_list.add_rect_filled(iu.v2(0.0, 0.0), iu.v2(w, h), iu.u32(*bg), 0.0)
        for slot in self._slots:
            self._draw_slot_chrome(slot, s)
        imgui.set_cursor_pos(iu.v2(0.0, 0.0))
        imgui.begin_child(self.TAG_PREFIX + "##scroll", iu.v2(w, h),
                          imgui.ChildFlags_.none.value,
                          imgui.WindowFlags_.none.value)
        items = self._visible_items(ctx)
        self._draw_text_lines(ctx, pad, pad_top, w - pad - round(14 * s), items,
                              params.get("label_color") or _DEFAULT_LABEL_COLOR,
                              _DEFAULT_TEXT_COLOR,
                              params.get("highlight_color") or _HIGHLIGHT_COLOR,
                              label_fmt="{label}")
        imgui.set_scroll_here_y(1.0)
        imgui.end_child()
        bar_w, _bar_h = self._toolbar_size(s)
        self._draw_toolbar(ctx, w / 2.0 - bar_w / 2.0, _TOOLBAR_MARGIN * s)
        self._draw_indicator(ctx, round(w / 2.0 - 80 * s), h - round(44 * s),
                             "▼ 点击继续 ▼")
