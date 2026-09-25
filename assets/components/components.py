"""官方副本显示组件包：三种 galgame 式文本组件与属性条。

存放于 assets/components/（随应用分发的只读资源，经 paths.dungeon_components_dir()
定位、由 dungeon.window.component_registry 导入），暴露 ``REGISTRY``：
{组件 id: 组件类}。组件类实现 build/layout/refresh/destroy 生命周期钩子，
ctx 即副本会话窗口实例（dungeon.window.DungeonSessionWindow 的 mixin 组合）。

文本组件三选一（方案配置 ``text_component`` 字段声明，主组件层）：
- ``text``      底部渐变式：视口底部向上淡出的深色衬底上显示最近几句（ADV 风格）；
- ``text_card`` 底部卡片式：底部居中的圆角半透明卡片，浮现最近几句；
- ``text_nvl``  全屏 NVL 式：全屏半透明覆盖层上堆叠全部历史（阅读模式）。

三者都声明 ``owns_text_display = True`` 接管窗口文本显示（内置 text_container
管线被 _update_text_display 的所有权守卫跳过）；仿流式动画仍由窗口帧时钟驱动
（item["text"] 就地增长），组件 refresh 只读取最新值。完整历史一律保留在回放
与报告中，屏幕呈现因组件而异。

线程约束：所有钩子都只由主线程调用；组件内不得在后台线程操作 DPG，
刷新经窗口的 _dispatch 调度链（_schedule_text_update / _relayout）完成。
"""

import os

import dearpygui.dearpygui as dpg

from dungeon import process_log
from dungeon.window.component_registry import DungeonComponent


# ---------------------------------------------------------------------------
# 文本组件公共部分
# ---------------------------------------------------------------------------

_HIGHLIGHT_COLOR = (255, 200, 60, 255)
_OLD_LINE_ALPHA = 0.55             # 非最新行的透明度系数
_DEFAULT_LABEL_COLOR = (255, 170, 210, 255)
_DEFAULT_TEXT_COLOR = (240, 240, 245, 255)
_BLINK_SECONDS = 0.65


def _dim(color, factor):
    return (color[0], color[1], color[2], int(color[3] * factor))


class _TextDisplayBase(DungeonComponent):
    """galgame 式文本组件的公共基类：接管显示、行对渲染、闪烁继续指示。

    行数据源是 ``ctx.story_history`` 条目（type_str / text / highlight /
    speaker），行首标签优先用说话人（Solea/Bulla 对话分支的 ``@标记`` 解析
    结果），无说话人时回退段落类型前缀。最新一行全亮，旧行降透明度。
    """

    owns_text_display = True
    _BLINK_TASK = ""   # 帧任务 key（子类设置）
    _INDICATOR = ""    # 继续指示文本项 tag（子类设置）

    def __init__(self, ctx):
        super().__init__(ctx)
        self._line_tags = []      # [(label_tag, text_tag), ...]
        self._indicator_on = True
        self._indicator_shown = False

    # ---- 内置容器接管 ----
    def _hide_builtin(self):
        for tag in ("text_container", "bg_overlay_child"):
            if dpg.does_item_exist(tag):
                dpg.hide_item(tag)

    def _restore_builtin(self):
        """恢复内置容器可见性（会话收尾后不再有组件接管显示）。"""
        for tag in ("text_container", "bg_overlay_child"):
            if dpg.does_item_exist(tag):
                dpg.show_item(tag)

    # ---- 几何 ----
    def _viewport(self, ctx):
        s = getattr(ctx, "_dpi_scale", 1.0) or 1.0
        w = getattr(ctx, "_layout_w", 0) or dpg.get_viewport_client_width()
        h = getattr(ctx, "_layout_h", 0) or dpg.get_viewport_client_height()
        return s, w, h

    # ---- 闪烁继续指示（计时走窗口帧时钟，不开线程——窗口约束 C5） ----
    def _start_blink(self, ctx):
        self._indicator_on = True
        ctx._frame.every(_BLINK_SECONDS, self._blink, key=self._BLINK_TASK)

    def _blink(self):
        self._indicator_on = not self._indicator_on
        if dpg.does_item_exist(self._INDICATOR):
            dpg.configure_item(self._INDICATOR,
                               show=self._indicator_shown and self._indicator_on)

    def _update_indicator_state(self, ctx):
        """等待点击（不在生成/动画/弹窗/结局中）时才显示继续指示。"""
        waiting = (not getattr(ctx, "_generating", False)
                   and getattr(ctx, "_text_anim_state", None) is None
                   and not getattr(ctx, "dungeon_ended", False)
                   and getattr(ctx, "pending_option", None) is None
                   and getattr(ctx, "pending_ending", None) is None)
        self._indicator_shown = bool((self.params or {}).get(
            "show_indicator", True)) and waiting

    def _place_indicator(self, x, y):
        if dpg.does_item_exist(self._INDICATOR):
            dpg.configure_item(self._INDICATOR, pos=[x, y],
                               show=self._indicator_shown and self._indicator_on)

    # ---- 行对渲染 ----
    def _display_line_pairs(self, items, label_color, text_color, wrap,
                            label_fmt="{label}"):
        """把 items 渲染到 _line_tags 的 (标签, 正文) 文本项对上。

        不足的行对隐藏；最新一行全亮，旧行降透明度；highlight 项用金色。
        """
        offset = len(self._line_tags) - len(items)
        for i, (label_tag, text_tag) in enumerate(self._line_tags):
            j = i - offset
            if not (dpg.does_item_exist(label_tag) and dpg.does_item_exist(text_tag)):
                continue
            if not 0 <= j < len(items):
                dpg.configure_item(label_tag, show=False)
                dpg.configure_item(text_tag, show=False)
                continue
            item = items[j]
            is_newest = j == len(items) - 1
            raw = (item.get("speaker") or item.get("type_str") or "").strip()
            label = label_fmt.format(label=raw) if raw else ""
            dpg.configure_item(label_tag, default_value=label,
                               color=_dim(label_color,
                                          1.0 if is_newest else _OLD_LINE_ALPHA),
                               show=bool(label))
            body = _HIGHLIGHT_COLOR if item.get("highlight") else text_color
            dpg.configure_item(text_tag, default_value=item.get("text", ""),
                               color=_dim(body,
                                          1.0 if is_newest else _OLD_LINE_ALPHA),
                               wrap=wrap, show=True)

    def destroy(self, ctx):
        if self._BLINK_TASK:
            ctx._frame.cancel(self._BLINK_TASK)
        self._line_tags = []
        self._restore_builtin()


# ---------------------------------------------------------------------------
# 文本栏（text）：底部渐变式（galgame ADV 风格）
# ---------------------------------------------------------------------------

# 渐变纹理：1×N 竖向 alpha 渐变，顶透明、底接近不透明（draw_image 拉伸铺满）
_GRAD_ROWS = 64
_GRAD_COLOR = (0.03, 0.04, 0.08)   # 深夜蓝黑
_GRAD_MAX_ALPHA = 0.92


class TextComponent(_TextDisplayBase):
    """底部渐变式文本栏：视口底部向上淡出的深色渐变衬底上显示最近几条正文。

    显示最近 ``max_lines`` 个显示段落；完整历史保留在回放与报告中，屏幕上
    只呈现当下。可配置参数见 param_specs。
    """

    id = "text"
    _BLINK_TASK = "text_gradient:blink"
    param_specs = [
        {"key": "height_percent", "label": "渐变区高度%", "type": "int",
         "default": 45, "note": "视口高度的百分比，15~80"},
        {"key": "max_lines", "label": "显示行数", "type": "int",
         "default": 2, "note": "最近 N 个显示段落，1~5"},
        {"key": "label_color", "label": "标签颜色", "type": "color",
         "default": _DEFAULT_LABEL_COLOR},
        {"key": "text_color", "label": "正文颜色", "type": "color",
         "default": _DEFAULT_TEXT_COLOR},
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
        rows = []
        for r in range(_GRAD_ROWS):
            v = r / (_GRAD_ROWS - 1)          # 0 = 纹理顶部
            alpha = _GRAD_MAX_ALPHA * (v ** 1.6)
            rows += [_GRAD_COLOR[0], _GRAD_COLOR[1], _GRAD_COLOR[2], alpha]
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
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, pad, pad)
        dpg.bind_item_theme(self._BOX, box_theme)

        self._line_tags = []
        max_lines = max(1, min(5, int((self.params or {}).get("max_lines") or 2)))
        for i in range(max_lines):
            label_tag = dpg.add_text("", parent=self._BOX, show=False)
            text_tag = dpg.add_text("", parent=self._BOX, wrap=max(1, text_w - 2 * pad),
                                    show=False)
            self._line_tags.append((label_tag, text_tag))

        # 继续指示（右下角闪烁 ▼）
        dpg.add_text("▼", tag=self._INDICATOR, parent="main_window",
                     color=_DEFAULT_LABEL_COLOR, show=False)
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
        self.refresh(ctx)

    def refresh(self, ctx):
        if not self._line_tags:
            return
        params = self.params or {}
        pad = round(15 * (getattr(ctx, "_dpi_scale", 1.0) or 1.0))
        items = (getattr(ctx, "story_history", None) or [])[-len(self._line_tags):]
        self._display_line_pairs(items,
                                 params.get("label_color") or _DEFAULT_LABEL_COLOR,
                                 params.get("text_color") or _DEFAULT_TEXT_COLOR,
                                 wrap=max(1, self._box_size[0] - 2 * pad))
        self._update_indicator_state(ctx)
        s = getattr(ctx, "_dpi_scale", 1.0) or 1.0
        self._place_indicator(self._box_pos[0] + self._box_size[0] - round(46 * s),
                              self._box_pos[1] + self._box_size[1] - round(44 * s))

    def destroy(self, ctx):
        for tag in (self._BACK, self._BOX, self._INDICATOR, self._TEXTURE):
            if dpg.does_item_exist(tag):
                dpg.delete_item(tag)
        super().destroy(ctx)


# ---------------------------------------------------------------------------
# 文本卡片（text_card）：底部卡片式（悬浮圆角半透明卡片）
# ---------------------------------------------------------------------------

class CardTextComponent(_TextDisplayBase):
    """底部卡片式文本栏：底部居中的圆角半透明卡片上显示最近几条正文。

    与底部渐变式同源的数据管线，只是呈现为一块悬浮卡片：半透明深色底 +
    圆角 + 淡边框近似 galgame 的玻璃卡片（DPG 无背景模糊，用半透明纯色
    代替）。显示最近 ``max_lines`` 个显示段落，完整历史在回放与报告中。
    """

    id = "text_card"
    _BLINK_TASK = "text_card:blink"
    param_specs = [
        {"key": "width_percent", "label": "卡片宽度%", "type": "int",
         "default": 70, "note": "视口宽度的百分比，30~95"},
        {"key": "max_lines", "label": "显示行数", "type": "int",
         "default": 2, "note": "最近 N 个显示段落，1~5"},
        {"key": "bg_color", "label": "卡片底色", "type": "color",
         "default": (20, 24, 40, 190)},
        {"key": "border_color", "label": "边框颜色", "type": "color",
         "default": (255, 255, 255, 45)},
        {"key": "label_color", "label": "标签颜色", "type": "color",
         "default": _DEFAULT_LABEL_COLOR},
        {"key": "text_color", "label": "正文颜色", "type": "color",
         "default": _DEFAULT_TEXT_COLOR},
        {"key": "show_indicator", "label": "显示继续指示", "type": "bool",
         "default": True},
    ]

    _CARD = "text_card_box"           # 卡片子窗口
    _INDICATOR = "text_card_indicator"

    def __init__(self, ctx):
        super().__init__(ctx)
        self._card_pos = [0, 0]   # 卡片几何缓存（build/layout 时更新）
        self._card_size = [0, 0]

    def _geometry(self, ctx):
        s, w, h = self._viewport(ctx)
        params = self.params or {}
        pad = round(16 * s)
        max_lines = max(1, min(5, int(params.get("max_lines") or 2)))
        percent = min(95, max(30, int(params.get("width_percent") or 70)))
        card_w = max(round(320 * s), round(w * percent / 100))
        # 标签行 + 每个显示段落预留两行正文的预算（段落折行时不溢出）
        card_h = 2 * pad + round(28 * s) + max_lines * round(52 * s)
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
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, pad, pad)
        dpg.bind_item_theme(self._CARD, card_theme)

        self._line_tags = []
        wrap = max(1, card_w - 2 * pad)
        for i in range(max_lines):
            label_tag = dpg.add_text("", parent=self._CARD, show=False)
            text_tag = dpg.add_text("", parent=self._CARD, wrap=wrap, show=False)
            self._line_tags.append((label_tag, text_tag))

        dpg.add_text("▼", tag=self._INDICATOR, parent="main_window",
                     color=_DEFAULT_LABEL_COLOR, show=False)
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
        self.refresh(ctx)

    def refresh(self, ctx):
        if not self._line_tags:
            return
        params = self.params or {}
        pad = round(16 * (getattr(ctx, "_dpi_scale", 1.0) or 1.0))
        items = (getattr(ctx, "story_history", None) or [])[-len(self._line_tags):]
        self._display_line_pairs(items,
                                 params.get("label_color") or _DEFAULT_LABEL_COLOR,
                                 params.get("text_color") or _DEFAULT_TEXT_COLOR,
                                 wrap=max(1, self._card_size[0] - 2 * pad))
        self._update_indicator_state(ctx)
        s = getattr(ctx, "_dpi_scale", 1.0) or 1.0
        self._place_indicator(self._card_pos[0] + self._card_size[0] - round(46 * s),
                              self._card_pos[1] + self._card_size[1] - round(40 * s))

    def destroy(self, ctx):
        for tag in (self._CARD, self._INDICATOR):
            if dpg.does_item_exist(tag):
                dpg.delete_item(tag)
        super().destroy(ctx)


# ---------------------------------------------------------------------------
# 全屏 NVL（text_nvl）：全屏半透明覆盖层上堆叠全部历史（阅读模式）
# ---------------------------------------------------------------------------

# 衬线字体候选（仿宋/宋体优先，回退到项目常用的通用字体）
_SERIF_FONT_CANDIDATES = (
    "C:/Windows/Fonts/simsun.ttc",
    "C:/Windows/Fonts/STZHONGS.TTF",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
)


class NvlTextComponent(_TextDisplayBase):
    """全屏 NVL 式文本栏：全屏半透明覆盖层上堆叠显示全部历史段落。

    Fate/魔法使之夜式的阅读模式：正文按时间顺序纵向堆叠，旧行降透明度、
    当前（动画中）行全亮，可选衬线字体，覆盖层内可滚轮回看，新段落上屏后
    自动跟随到底部（仅当此前已在本就位于底部时跟随，避免打断回看）。
    """

    id = "text_nvl"
    _BLINK_TASK = "text_nvl:blink"
    param_specs = [
        {"key": "bg_color", "label": "覆盖层底色", "type": "color",
         "default": (8, 10, 18, 225)},
        {"key": "label_color", "label": "标签颜色", "type": "color",
         "default": _DEFAULT_LABEL_COLOR},
        {"key": "text_color", "label": "正文颜色", "type": "color",
         "default": _DEFAULT_TEXT_COLOR},
        {"key": "use_serif", "label": "衬线字体", "type": "bool",
         "default": True, "note": "宋体等衬线字体（找不到时回退默认字体）"},
        {"key": "show_indicator", "label": "显示继续指示", "type": "bool",
         "default": True},
    ]

    _OVERLAY = "text_nvl_overlay"     # 全屏覆盖层子窗口
    _INDICATOR = "text_nvl_indicator"

    def __init__(self, ctx):
        super().__init__(ctx)
        self._font_tag = None
        self._wrap = 800

    def _create_font(self, ctx):
        """衬线字体只绑到 NVL 文本项，不动全局字体（找不到候选时返回 None）。"""
        if not bool((self.params or {}).get("use_serif", True)):
            return None
        s = getattr(ctx, "_dpi_scale", 1.0) or 1.0
        for path in _SERIF_FONT_CANDIDATES:
            if os.path.exists(path):
                try:
                    return dpg.add_font(path, round(20 * s))
                except Exception as exc:
                    print(f"[Components] 衬线字体加载失败 {path}: {exc}")
        return None

    def build(self, ctx):
        self._hide_builtin()
        s, w, h = self._viewport(ctx)
        pad = round(24 * s)
        for tag in (self._OVERLAY, self._INDICATOR):
            if dpg.does_item_exist(tag):
                dpg.delete_item(tag)
        if self._font_tag and dpg.does_item_exist(self._font_tag):
            dpg.delete_item(self._font_tag)
        self._font_tag = None

        self._wrap = max(1, w - 2 * pad - round(14 * s))   # 右侧留滚动条宽
        dpg.add_child_window(tag=self._OVERLAY, parent="main_window",
                             pos=[0, 0], width=w, height=h, border=False)
        with dpg.theme() as overlay_theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(
                    dpg.mvThemeCol_ChildBg,
                    (self.params or {}).get("bg_color") or (8, 10, 18, 225))
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, pad, pad)
        dpg.bind_item_theme(self._OVERLAY, overlay_theme)

        self._font_tag = self._create_font(ctx)
        self._line_tags = []

        dpg.add_text("▼ 点击继续 ▼", tag=self._INDICATOR, parent="main_window",
                     color=_dim(_DEFAULT_TEXT_COLOR, 0.8), show=False)
        self._start_blink(ctx)
        self.refresh(ctx)

    def layout(self, ctx):
        if not dpg.does_item_exist(self._OVERLAY):
            return
        s, w, h = self._viewport(ctx)
        pad = round(24 * s)
        self._wrap = max(1, w - 2 * pad - round(14 * s))
        dpg.configure_item(self._OVERLAY, pos=[0, 0], width=w, height=h)
        for _, text_tag in self._line_tags:
            if dpg.does_item_exist(text_tag):
                dpg.configure_item(text_tag, wrap=self._wrap)
        self.refresh(ctx)

    def refresh(self, ctx):
        if not dpg.does_item_exist(self._OVERLAY):
            return
        items = getattr(ctx, "story_history", None) or []

        # 跟随判定取自更新前的滚动状态：此前已在底部才自动跟随，回看不被打断
        state = dpg.get_item_state(self._OVERLAY) or {}
        pos = state.get("y_scroll_pos")
        max_scroll = state.get("y_scroll_max")
        follow = pos is None or max_scroll is None or pos >= max_scroll - 6

        # 标签池按需增删（与内置 text_container 同样的池化管理，不每帧重建）
        while len(self._line_tags) < len(items):
            label_tag = dpg.add_text("", parent=self._OVERLAY, show=False)
            text_tag = dpg.add_text("", parent=self._OVERLAY,
                                    wrap=self._wrap, show=False)
            if self._font_tag:
                dpg.bind_item_font(label_tag, self._font_tag)
                dpg.bind_item_font(text_tag, self._font_tag)
            self._line_tags.append((label_tag, text_tag))
        while len(self._line_tags) > len(items):
            label_tag, text_tag = self._line_tags.pop()
            for tag in (label_tag, text_tag):
                if dpg.does_item_exist(tag):
                    dpg.delete_item(tag)

        params = self.params or {}
        self._display_line_pairs(items,
                                 params.get("label_color") or _DEFAULT_LABEL_COLOR,
                                 params.get("text_color") or _DEFAULT_TEXT_COLOR,
                                 wrap=self._wrap, label_fmt="[ {label} ]")

        self._update_indicator_state(ctx)
        s, w, h = self._viewport(ctx)
        self._place_indicator(round(w / 2 - 80 * s), h - round(44 * s))

        if follow:
            state = dpg.get_item_state(self._OVERLAY) or {}
            max_scroll = state.get("y_scroll_max")
            if max_scroll:
                dpg.set_y_scroll(self._OVERLAY, max_scroll)

    def destroy(self, ctx):
        for tag in (self._OVERLAY, self._INDICATOR):
            if dpg.does_item_exist(tag):
                dpg.delete_item(tag)
        if self._font_tag and dpg.does_item_exist(self._font_tag):
            dpg.delete_item(self._font_tag)
        self._font_tag = None
        super().destroy(ctx)


# ---------------------------------------------------------------------------
# 属性条（attr_bar）：演化属性状态栏
# ---------------------------------------------------------------------------

_ATTR_TYPE_LABELS = {
    "intrusion": "介入度",
    "destruction": "破坏性",
    "casualty": "总伤亡",
}

_PANEL_TAG = "attr_bar_panel"


def _format_value(attr_type, value):
    if attr_type == "casualty":
        return f"{int(round(value)):,}"
    return f"{value:.2f}"


class AttrBarComponent(DungeonComponent):
    """属性条：按 evolution_attrs 中 display_state == "show" 的属性实时显示。

    渲染 dungeon_state 的介入度/破坏性/自定义属性/总伤亡；未显式启用的
    属性（collapse/internal）不显示。布局为窗口左上角窄栏。

    可配置参数见 param_specs。
    """

    id = "attr_bar"
    param_specs = [
        {"key": "title_text", "label": "标题文字", "type": "text",
         "default": "属性状态"},
        {"key": "title_color", "label": "标题颜色", "type": "color",
         "default": (255, 225, 150, 255)},
        {"key": "text_color", "label": "数值颜色", "type": "color",
         "default": (230, 235, 245, 255)},
        {"key": "panel_width", "label": "面板宽度", "type": "int",
         "default": 300},
        {"key": "show_title", "label": "显示标题", "type": "bool",
         "default": True},
    ]

    def __init__(self, ctx):
        super().__init__(ctx)
        self._items = []   # [(属性名, 类型, tag)]
        self._title_tag = None

    def _resolved_attrs(self, ctx):
        """按配置顺序返回要展示的属性定义（display_state == "show"）。"""
        attrs = ctx.evolution_attrs if hasattr(ctx, "evolution_attrs") else []
        resolved = []
        for attr in attrs or []:
            if not isinstance(attr, dict):
                continue
            if attr.get("display_state") != "show":
                continue
            resolved.append(attr)
        return resolved

    def build(self, ctx):
        if dpg.does_item_exist(_PANEL_TAG):
            dpg.delete_item(_PANEL_TAG)
        self._items = []
        self._title_tag = None
        s = ctx._dpi_scale if hasattr(ctx, "_dpi_scale") else 1.0
        margin = round(20 * s)
        params = self.params or {}
        panel_width = int(params.get("panel_width") or 300)
        title_text = str(params.get("title_text") or "属性状态")
        title_color = params.get("title_color") or (255, 225, 150, 255)
        text_color = params.get("text_color") or (230, 235, 245, 255)
        show_title = bool(params.get("show_title", True))

        # 面板：左上角
        dpg.add_child_window(
            tag=_PANEL_TAG, parent="main_window",
            pos=[margin, margin], width=round(panel_width * s), height=round(120 * s),
            no_scrollbar=True, no_scroll_with_mouse=True, border=False)

        if show_title:
            self._title_tag = dpg.add_text(title_text, parent=_PANEL_TAG, color=title_color)

        attrs = self._resolved_attrs(ctx)
        if not attrs:
            dpg.add_text("（未启用展示属性）", parent=_PANEL_TAG,
                         color=(160, 170, 185, 255), wrap=round((panel_width - 40) * s))
            return

        for attr in attrs:
            label = attr.get("name") or _ATTR_TYPE_LABELS.get(attr.get("type"), "属性")
            tag = dpg.add_text("", parent=_PANEL_TAG, color=text_color)
            self._items.append((label, attr.get("type", "custom"), tag))
        self.refresh(ctx)

    def refresh(self, ctx):
        if not self._items:
            return
        state = getattr(ctx, "dungeon_state", None)
        text_color = (self.params or {}).get("text_color") or (230, 235, 245, 255)
        for label, attr_type, tag in self._items:
            if attr_type == "intrusion":
                value = state.intrusion if state else 0.0
            elif attr_type == "destruction":
                value = state.destruction if state else 0.0
            elif attr_type == "casualty":
                value = state.total_casualties if state else 0.0
            else:
                custom = state.custom_attrs if state else {}
                value = custom.get(label, 0.0)
            if dpg.does_item_exist(tag):
                dpg.configure_item(tag, default_value=f"{label}：{_format_value(attr_type, value)}",
                                   color=text_color)

    def layout(self, ctx):
        if not dpg.does_item_exist(_PANEL_TAG):
            return
        s = ctx._dpi_scale if hasattr(ctx, "_dpi_scale") else 1.0
        margin = round(20 * s)
        panel_width = int((self.params or {}).get("panel_width") or 300)
        dpg.configure_item(_PANEL_TAG, pos=[margin, margin],
                           width=round(panel_width * s), height=round(120 * s))

    def destroy(self, ctx):
        if dpg.does_item_exist(_PANEL_TAG):
            dpg.delete_item(_PANEL_TAG)
        self._items = []
        self._title_tag = None


# ---------------------------------------------------------------------------
# 过程日志组件
# ---------------------------------------------------------------------------

_PROC_LOG_PANEL_TAG = "proc_log_panel"
_PROC_LOG_TITLE_TAG = "proc_log_title"
_PROC_LOG_SCROLL_TAG = "proc_log_scroll"
_PROC_LOG_COLOR = (232, 232, 232, 255)


class ProcLogComponent(DungeonComponent):
    """过程日志：右上角面板，显示 dungeon.process_log 收集的运行消息。

    方案在 ``components`` 里声明 ``"proc_log"`` 才启用；窗口的 F12 快捷键切换
    展开 / 收起（默认收起；未配置时 F12 由窗口弹「日志查看已禁用」通知）。

    消息源是领域模块 dungeon.process_log（线程安全环形缓冲）：build 时订阅并
    补显积压消息；此后任意线程的消息经订阅回调 → 窗口帧时钟投递回主线程追加。
    """

    id = "proc_log"
    param_specs = [
        {"key": "panel_width", "label": "面板宽度", "type": "int", "default": 460},
        {"key": "scroll_height", "label": "日志区高度", "type": "int", "default": 300},
        {"key": "max_lines", "label": "最大行数", "type": "int", "default": 300},
    ]

    def __init__(self, ctx):
        super().__init__(ctx)
        self._tags = []      # 日志行 text 控件 tag（FIFO，超上限删最旧）
        self._open = False   # 当前是否展开
        self._wrap = 0

    def _geometry(self, ctx):
        """返回 (dpi_scale, 面板宽, 日志区高, 外边距, 内边距)，均按 scale 换算。"""
        s = ctx._dpi_scale if hasattr(ctx, "_dpi_scale") else 1.0
        width = int((self.params or {}).get("panel_width") or 460)
        scroll_h = int((self.params or {}).get("scroll_height") or 300)
        return (s, round(width * s), round(scroll_h * s), round(8 * s), round(6 * s))

    def build(self, ctx):
        if dpg.does_item_exist(_PROC_LOG_PANEL_TAG):
            dpg.delete_item(_PROC_LOG_PANEL_TAG)
        self._tags = []
        self._open = False
        s, width, scroll_h, margin, pad = self._geometry(ctx)
        vw = dpg.get_viewport_client_width()
        self._wrap = max(1, width - 2 * pad - round(18 * s))

        with dpg.child_window(
                tag=_PROC_LOG_PANEL_TAG, parent="main_window",
                pos=[max(0, vw - width - margin), margin],
                width=width, height=scroll_h + round(30 * s),
                show=False, no_scrollbar=True, no_scroll_with_mouse=True,
                border=False):
            dpg.add_text("过程日志（F12 收起）", tag=_PROC_LOG_TITLE_TAG,
                         parent=_PROC_LOG_PANEL_TAG, color=(200, 205, 215, 255))
            with dpg.child_window(tag=_PROC_LOG_SCROLL_TAG, height=scroll_h,
                                  border=True):
                pass

        with dpg.theme() as theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (12, 12, 12, 175))
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, pad, pad)
        dpg.bind_item_theme(_PROC_LOG_PANEL_TAG, theme)

        # 订阅过程日志：缓冲积压（构造 / 会话初始化阶段的消息）一并补显
        backlog = process_log.subscribe(self._on_message)
        for line in backlog:
            ctx._frame.call(self._append, line)

    def toggle(self, ctx):
        """F12 调用：展开 / 收起面板（主线程）。"""
        self._open = not self._open
        if dpg.does_item_exist(_PROC_LOG_PANEL_TAG):
            dpg.configure_item(_PROC_LOG_PANEL_TAG, show=self._open)

    def _on_message(self, text):
        """process_log 订阅回调（任意线程）：把日志投递回主线程显示。"""
        self.ctx._frame.call(self._append, text)

    def _append(self, text):
        """追加一条日志（仅主线程）；超出上限删最旧，自动滚底。"""
        if not dpg.does_item_exist(_PROC_LOG_SCROLL_TAG):
            return
        self._tags.append(dpg.add_text(parent=_PROC_LOG_SCROLL_TAG,
                                       default_value=text, wrap=self._wrap,
                                       color=_PROC_LOG_COLOR))
        max_lines = int((self.params or {}).get("max_lines") or 300)
        if len(self._tags) > max_lines:
            for tag in self._tags[:-max_lines]:
                dpg.delete_item(tag)
            del self._tags[:-max_lines]
        state = dpg.get_item_state(_PROC_LOG_SCROLL_TAG)
        max_scroll = state.get("y_scroll_max") if state else None
        if max_scroll is not None:
            dpg.set_y_scroll(_PROC_LOG_SCROLL_TAG, max_scroll)

    def layout(self, ctx):
        if not dpg.does_item_exist(_PROC_LOG_PANEL_TAG):
            return
        s, width, scroll_h, margin, pad = self._geometry(ctx)
        vw = dpg.get_viewport_client_width()
        dpg.configure_item(_PROC_LOG_PANEL_TAG,
                           pos=[max(0, vw - width - margin), margin],
                           width=width, height=scroll_h + round(30 * s))

    def destroy(self, ctx):
        process_log.unsubscribe(self._on_message)
        if dpg.does_item_exist(_PROC_LOG_PANEL_TAG):
            dpg.delete_item(_PROC_LOG_PANEL_TAG)
        self._tags = []


REGISTRY = {
    TextComponent.id: TextComponent,
    CardTextComponent.id: CardTextComponent,
    NvlTextComponent.id: NvlTextComponent,
    AttrBarComponent.id: AttrBarComponent,
    ProcLogComponent.id: ProcLogComponent,
}

__all__ = ["REGISTRY", "TextComponent", "CardTextComponent", "NvlTextComponent",
           "AttrBarComponent", "ProcLogComponent"]
