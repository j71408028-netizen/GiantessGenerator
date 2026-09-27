"""文本卡片（text_card）：底部卡片式（悬浮圆角半透明卡片）"""

import dearpygui.dearpygui as dpg

from .base import (_DEFAULT_LABEL_COLOR, _DEFAULT_TEXT_COLOR,
                   _HIGHLIGHT_COLOR, _TextDisplayBase)
from dungeon.window.fonts import TEXT_FONT_SIZE


class CardTextComponent(_TextDisplayBase):
    """底部卡片式文本栏：底部居中的圆角半透明卡片上显示最近几条正文。

    与底部渐变式同源的数据管线，只是呈现为一块悬浮卡片：半透明深色底 +
    圆角 + 淡边框近似 galgame 的玻璃卡片（DPG 无背景模糊，用半透明纯色
    代替）。显示最近 ``max_lines`` 个显示段落，完整历史在回放与报告中。
    """

    id = "text_card"
    label = "底部卡片式文本栏"
    description = "底部居中的圆角半透明卡片上浮现最近几句正文。"
    _BLINK_TASK = "text_card:blink"
    #: 本组件显式 tag 的统一前缀（守卫据此查命名空间冲突）
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

    _CARD = "text_card_box"           # 卡片子窗口
    _INDICATOR = "text_card_indicator"
    _TOOLBAR = "text_card_toolbar"

    def __init__(self, ctx):
        super().__init__(ctx)
        self._card_pos = [0, 0]   # 卡片几何缓存（build/layout 时更新）
        self._card_size = [0, 0]

    def _geometry(self, ctx):
        s, w, h = self._viewport(ctx)
        params = self.params or {}
        pad = round(16 * s)
        max_lines = max(1, min(5, int(params.get("max_lines") or 1)))
        percent = min(95, max(30, int(params.get("width_percent") or 70)))
        card_w = max(round(320 * s), round(w * percent / 100))
        # 标签行 + 每个显示段落预留两行正文的预算（段落折行时不溢出）；
        # 行高按字号推算并留空隙，卡片高度不超过视口的 60%
        line_h = round((TEXT_FONT_SIZE + 10) * s)
        pair_h = line_h + 2 * line_h + round(8 * s)
        card_h = min(round(h * 0.6), 2 * pad + max_lines * pair_h)
        card_x = max(0, (w - card_w) // 2)
        card_y = max(0, h - card_h - round(24 * s))
        return s, pad, card_w, card_h, card_x, card_y, max_lines

    def build(self, ctx):
        self._hide_builtin()
        s, pad, card_w, card_h, card_x, card_y, max_lines = self._geometry(ctx)
        for tag in (self._CARD, self._INDICATOR):
            if dpg.does_item_exist(tag):
                dpg.delete_item(tag)

        self._card_pos, self._card_size = [card_x, card_y], [card_w, card_h]
        dpg.add_child_window(tag=self._CARD, parent="main_window",
                             pos=[card_x, card_y], width=card_w, height=card_h,
                             border=True)
        dpg.configure_item(self._CARD, no_scrollbar=True, no_scroll_with_mouse=True)
        params = self.params or {}
        with dpg.theme() as card_theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg,
                                    params.get("bg_color") or (20, 24, 40, 190))
                dpg.add_theme_color(dpg.mvThemeCol_Border,
                                    params.get("border_color") or (255, 255, 255, 45))
                dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, round(14 * s))
                dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 0, round(8 * s))
        dpg.bind_item_theme(self._CARD, card_theme)

        # WindowPadding 对 child_window 不生效（DPG 2.3.1 怪癖）：顶部 spacer + indent
        dpg.add_spacer(height=pad, parent=self._CARD)
        self._line_tags = []
        wrap = max(1, card_w - 2 * pad)
        for i in range(max_lines):
            label_tag = dpg.add_text("", parent=self._CARD, indent=pad, show=False)
            text_tag = dpg.add_text("", parent=self._CARD, wrap=wrap, indent=pad,
                                    show=False)
            self._line_tags.append((label_tag, text_tag))
        self._bold_font = self._create_bold_font(ctx)
        self._bind_line_fonts(ctx, [t for pair in self._line_tags for t in pair])

        dpg.add_text("▼", tag=self._INDICATOR, parent="main_window",
                     color=_DEFAULT_LABEL_COLOR, show=False)
        # 功能工具条：卡片右上角（中线压在卡片上缘，一半探出卡外）
        self._build_toolbar(ctx)
        self._place_toolbar(ctx, card_x + card_w - round(10 * s),
                            card_y - self._toolbar_size[1] // 2)
        self._start_blink(ctx)
        self.refresh(ctx)

    def layout(self, ctx):
        if not dpg.does_item_exist(self._CARD):
            return
        s, pad, card_w, card_h, card_x, card_y, max_lines = self._geometry(ctx)
        self._card_pos, self._card_size = [card_x, card_y], [card_w, card_h]
        dpg.configure_item(self._CARD, pos=[card_x, card_y],
                           width=card_w, height=card_h)
        wrap = max(1, card_w - 2 * pad)
        for _, text_tag in self._line_tags:
            if dpg.does_item_exist(text_tag):
                dpg.configure_item(text_tag, wrap=wrap)
        self._place_toolbar(ctx, card_x + card_w - round(10 * s),
                            card_y - self._toolbar_size[1] // 2)
        self.refresh(ctx)

    def refresh(self, ctx):
        if not self._line_tags:
            return
        params = self.params or {}
        s = ctx.component_viewport()[0]
        pad = round(16 * s)
        items = (getattr(ctx, "story_history", None) or [])[-len(self._line_tags):]
        self._display_line_pairs(items,
                                 params.get("label_color") or _DEFAULT_LABEL_COLOR,
                                 _DEFAULT_TEXT_COLOR,
                                 params.get("highlight_color"),
                                 wrap=max(1, self._card_size[0] - 2 * pad))
        self._update_indicator_state(ctx)
        self._sync_toolbar(ctx)
        s = getattr(ctx, "_dpi_scale", 1.0) or 1.0
        self._place_indicator(self._card_pos[0] + self._card_size[0] - round(46 * s),
                              self._card_pos[1] + self._card_size[1] - round(40 * s))

    def destroy(self, ctx):
        self._destroy_toolbar()
        for tag in (self._CARD, self._INDICATOR):
            if dpg.does_item_exist(tag):
                dpg.delete_item(tag)
        super().destroy(ctx)
