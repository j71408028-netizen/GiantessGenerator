"""文本栏（text）：底部渐变式（galgame ADV 风格）"""

import dearpygui.dearpygui as dpg

from .base import (_DEFAULT_LABEL_COLOR, _DEFAULT_TEXT_COLOR,
                   _HIGHLIGHT_COLOR, _TOOLBAR_MARGIN, _TextDisplayBase)
from dungeon.window.fonts import TEXT_FONT_SIZE


# 渐变纹理：1×N 竖向 alpha 渐变，顶透明、底接近不透明（draw_image 拉伸铺满）
_GRAD_ROWS = 64
# 渐变底部颜色（0~255 RGBA；alpha 即底部的最大不透明度，向上渐隐）
_GRAD_DEFAULT_COLOR = (10, 12, 22, 128)
_GRAD_CURVE = 1.6


class TextComponent(_TextDisplayBase):
    """底部渐变式文本栏：视口底部向上淡出的深色渐变衬底上显示最近几条正文。

    显示最近 ``max_lines`` 个显示段落；完整历史保留在回放与报告中，屏幕上
    只呈现当下。可配置参数见 param_specs。
    """

    id = "text"
    label = "底部渐变式文本栏"
    description = "视口底部向上淡出的深色渐变衬底上显示最近几句正文（ADV 风格）。"
    _BLINK_TASK = "text_gradient:blink"
    #: 本组件显式 tag 的统一前缀（守卫据此查命名空间冲突）
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

    # ---- 标签约定（build 创建，layout/refresh/destroy 按名复用） ----
    _BACK = "text_gradient_back"        # 渐变衬底子窗口
    _DRAW = "text_gradient_draw"        # 渐变 drawlist
    _IMAGE = "text_gradient_image"      # draw_image 项
    _TEXTURE = "text_gradient_texture"  # 1×64 渐变纹理
    _BOX = "text_gradient_box"          # 文本子窗口
    _INDICATOR = "text_gradient_indicator"
    _TOOLBAR = "text_gradient_toolbar"

    def __init__(self, ctx):
        super().__init__(ctx)
        self._box_pos = [0, 0]    # 文本子窗口几何缓存（build/layout 时更新）
        self._box_size = [0, 0]

    # ---- 几何 ----
    def _geometry(self, ctx):
        s, w, h = self._viewport(ctx)
        percent = int((self.params or {}).get("height_percent") or 45)
        grad_h = max(round(h * 0.15), round(h * min(80, max(15, percent)) / 100))
        margin_bottom = round(20 * s)
        pad = round(15 * s)
        text_top = h - grad_h + round(grad_h * 0.25)
        text_h = max(round(80 * s), h - text_top - margin_bottom)
        text_w = max(round(300 * s), w - 2 * round(40 * s))
        return s, w, h, grad_h, [round(40 * s), text_top], text_w, text_h, pad

    def build(self, ctx):
        self._hide_builtin()
        s, w, h, grad_h, box_pos, text_w, text_h, pad = self._geometry(ctx)

        # 幂等：重建前先清掉旧控件与旧纹理
        for tag in (self._TEXTURE, self._BACK, self._BOX, self._INDICATOR):
            if dpg.does_item_exist(tag):
                dpg.delete_item(tag)
        gc = (self.params or {}).get("gradient_color") or _GRAD_DEFAULT_COLOR
        max_alpha = max(0.0, min(1.0, gc[3] / 255.0))
        rows = []
        for r in range(_GRAD_ROWS):
            v = r / (_GRAD_ROWS - 1)          # 0 = 纹理顶部
            alpha = max_alpha * (v ** _GRAD_CURVE)
            rows += [gc[0] / 255.0, gc[1] / 255.0, gc[2] / 255.0, alpha]
        dpg.add_dynamic_texture(width=1, height=_GRAD_ROWS, default_value=rows,
                                tag=self._TEXTURE, parent="dungeon_texture_registry")

        # 渐变衬底：子窗口承载 drawlist（主窗口内 drawlist 才渲染 draw_image）
        dpg.add_child_window(tag=self._BACK, parent="main_window",
                             pos=[0, h - grad_h], width=w, height=grad_h,
                             border=False)
        dpg.configure_item(self._BACK, no_scrollbar=True, no_scroll_with_mouse=True)
        with dpg.theme() as back_theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (0, 0, 0, 0))
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 0, 0)
        dpg.bind_item_theme(self._BACK, back_theme)
        with dpg.drawlist(tag=self._DRAW, width=w, height=grad_h,
                          parent=self._BACK):
            dpg.draw_image(texture_tag=self._TEXTURE, pmin=[0, 0],
                           pmax=[w, grad_h], tag=self._IMAGE)

        # 文本子窗口：标签 + 正文行
        self._box_pos, self._box_size = list(box_pos), [text_w, text_h]
        dpg.add_child_window(tag=self._BOX, parent="main_window",
                             pos=box_pos, width=text_w, height=text_h,
                             border=False)
        dpg.configure_item(self._BOX, no_scrollbar=True, no_scroll_with_mouse=True)
        with dpg.theme() as box_theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (0, 0, 0, 0))
                dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 0, round(8 * s))
        dpg.bind_item_theme(self._BOX, box_theme)

        # WindowPadding 对 child_window 不生效（DPG 2.3.1 怪癖）：顶部用 spacer、
        # 左侧用 indent 拼出内边距
        dpg.add_spacer(height=pad, parent=self._BOX)
        self._line_tags = []
        max_lines = max(1, min(5, int((self.params or {}).get("max_lines") or 1)))
        for i in range(max_lines):
            label_tag = dpg.add_text("", parent=self._BOX, indent=pad, show=False)
            text_tag = dpg.add_text("", parent=self._BOX, indent=pad,
                                    wrap=max(1, text_w - 2 * pad), show=False)
            self._line_tags.append((label_tag, text_tag))
        self._bold_font = self._create_bold_font(ctx)
        self._bind_line_fonts(ctx, [t for pair in self._line_tags for t in pair])

        # 继续指示（右下角闪烁 ▼）
        dpg.add_text("▼", tag=self._INDICATOR, parent="main_window",
                     color=_DEFAULT_LABEL_COLOR, show=False)
        # 功能工具条：渐变区右下角（不与文本行争空间）
        self._build_toolbar(ctx)
        self._place_toolbar(ctx, w - _TOOLBAR_MARGIN * s,
                            h - self._toolbar_size[1] - round(10 * s))
        self._start_blink(ctx)
        self.refresh(ctx)

    def layout(self, ctx):
        if not dpg.does_item_exist(self._BACK):
            return
        s, w, h, grad_h, box_pos, text_w, text_h, pad = self._geometry(ctx)
        self._box_pos, self._box_size = list(box_pos), [text_w, text_h]
        dpg.configure_item(self._BACK, pos=[0, h - grad_h], width=w, height=grad_h)
        dpg.configure_item(self._DRAW, width=w, height=grad_h)
        dpg.configure_item(self._IMAGE, pmin=[0, 0], pmax=[w, grad_h])
        dpg.configure_item(self._BOX, pos=box_pos, width=text_w, height=text_h)
        for _, text_tag in self._line_tags:
            if dpg.does_item_exist(text_tag):
                dpg.configure_item(text_tag, wrap=max(1, text_w - 2 * pad))
        self._place_toolbar(ctx, w - _TOOLBAR_MARGIN * s,
                            h - self._toolbar_size[1] - round(10 * s))
        self.refresh(ctx)

    def refresh(self, ctx):
        if not self._line_tags:
            return
        params = self.params or {}
        s, w, h, grad_h, box_pos, text_w, text_h, pad = self._geometry(ctx)
        items = (getattr(ctx, "story_history", None) or [])[-len(self._line_tags):]
        self._display_line_pairs(items,
                                 params.get("label_color") or _DEFAULT_LABEL_COLOR,
                                 _DEFAULT_TEXT_COLOR,
                                 params.get("highlight_color"),
                                 wrap=max(1, text_w - 2 * pad))
        self._update_indicator_state(ctx)
        self._sync_toolbar(ctx)
        # 把文本框贴着渐变区底部放（galgame 惯例：新句把旧行上推，最新句始终
        # 落在渐变最深处保证对比度），高度不超几何预算。高度不读 item 实测
        # rect——绑定字体后 rect_size 只报字号、不含下伸部（最新句会被窗口
        # 裁掉），且流式动画期间实测值天然滞后一拍；改由文本内容估算行数，
        # 估算只偏保守（CJK 字宽≈字号、ASCII≈0.55 字号），不会裁字。
        line_h = round((TEXT_FONT_SIZE + 10) * s)
        wrap_w = max(1, text_w - 2 * pad)
        spacing = round(8 * s)
        # 上限不再用 text_h（渐变区高度小、字号大时装不下，最新句会被裁），
        # 只要求 box 顶不越过窗口顶部 60px；顶部几行落在渐变上方属正常
        # （旧行本来就降透明度，最新句始终在渐变深处）
        cap_h = h - round(60 * s)
        content = 2 * pad
        for label_tag, text_tag in self._line_tags:
            if not (dpg.does_item_exist(label_tag)
                    and dpg.does_item_exist(text_tag)):
                continue
            for tag in (label_tag, text_tag):
                conf = dpg.get_item_configuration(tag)
                if conf.get("show"):
                    content += (line_h * self._line_count(dpg.get_value(tag), wrap_w, s)
                                + spacing)
        box_h = max(round(60 * s), min(cap_h, content))
        self._box_pos = [box_pos[0], h - round(20 * s) - box_h]
        self._box_size = [text_w, box_h]
        if dpg.does_item_exist(self._BOX):
            dpg.configure_item(self._BOX, pos=self._box_pos, height=box_h)
        # 继续指示放在文本框右下角，但左移让出右下角的工具条
        self._place_indicator(
            self._box_pos[0] + self._box_size[0] - round(46 * s)
            - (self._toolbar_size[0] + round(16 * s)),
            self._box_pos[1] + self._box_size[1] - round(44 * s))

    def destroy(self, ctx):
        self._destroy_toolbar()
        for tag in (self._BACK, self._BOX, self._INDICATOR, self._TEXTURE):
            if dpg.does_item_exist(tag):
                dpg.delete_item(tag)
        super().destroy(ctx)
