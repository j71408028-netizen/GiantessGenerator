"""全屏 NVL（text_nvl）：全屏半透明覆盖层上堆叠全部历史（阅读模式）"""

from imgui_bundle import imgui

from dungeon.window import imgui_util as iu

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
        pad_top = max(pad, int(ctx.component_top_inset() or 0))
        wrap = max(1.0, w - 2 * pad - round(14 * s))
        return s, w, h, pad, pad_top, wrap

    def layout(self, ctx):
        self._layout = self._geometry(ctx)
        _s, w, h, _pad, _pad_top, _wrap = self._layout
        self._panel_rect = (0.0, 0.0, w, h)

    def draw(self, ctx):
        if self._layout is None:
            self.layout(ctx)
        s, w, h, pad, pad_top, wrap = self._layout
        params = self.params or {}
        bg = params.get("bg_color") or (8, 10, 18, 190)
        draw_list = imgui.get_window_draw_list()
        draw_list.add_rect_filled(iu.v2(0.0, 0.0), iu.v2(w, h), iu.u32(*bg), 0.0)
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
