"""官方副本显示组件包：共享常量、工具条图标与文本组件公共基类。

组件包按组件拆分（text_gradient / text_card / text_nvl / attr_bar / proc_log），
各组件模块从本模块导入共享常量与 ``_TextDisplayBase``；``__init__.py`` 只是
组装 ``REGISTRY`` 的入口。

字体相关一律从 ``dungeon.window.fonts`` 取（字号与候选链的唯一真相源，窗口层
``ui.py`` 用的是同一份）——本模块**不再**自留字号与候选链副本。组件访问窗口则
只走 ``dungeon.window.components.ComponentHandler`` 提供的服务面
（``component_viewport`` / ``session_waiting_for_input`` / ``component_autoplay_on``
/ ``text_font_tag`` / ``bold_font_tag`` / ``schedule*`` / ``component``）。
"""

import math
import os

import dearpygui.dearpygui as dpg

from dungeon import process_log
from dungeon.window.component_registry import DungeonComponent
from dungeon.window.fonts import BOLD_FONT_PATHS, TEXT_FONT_SIZE


_HIGHLIGHT_COLOR = (255, 200, 60, 255)
_OLD_LINE_ALPHA = 0.55             # 非最新行的透明度系数
_DEFAULT_LABEL_COLOR = (255, 170, 210, 255)
_DEFAULT_TEXT_COLOR = (240, 240, 245, 255)
_BLINK_SECONDS = 0.65

# 文本组件共享的悬浮工具条：圆角半透明容器 + 三个图标按钮（纹理程序化生成，
# 无底色只有悬停色），转调窗口同名服务（快捷键等效）。容器尺寸按图标与
# 内边距推算，见 _build_toolbar。
_TOOLBAR_ACTIONS = (
    ("autoplay", "play", "自动播放（A）"),
    ("screenshot", "camera", "截图（F2）"),
    ("proclog", "log", "过程日志面板（F12）"),
)
_TOOLBAR_HANDLERS = {
    "autoplay": "toggle_autoplay",
    "screenshot": "take_screenshot",
    "proclog": "toggle_proc_log",
}
_TOOLBAR_MARGIN = 12
_TOOLBAR_ICON = 16     # 图标边长（px @ dpi=1）
_TOOLBAR_PAD_X = 4    # 按钮内边距（经工具条主题的 FramePadding 统一注入）
_TOOLBAR_PAD_Y = 3
_TOOLBAR_GAP = 6      # 按钮间距
_TOOLBAR_FRAME_X = 9   # 圆角容器内边距
_TOOLBAR_FRAME_Y = 6
_TOOLBAR_TINT = (232, 236, 244, 220)       # 图标常态着色
_TOOLBAR_TINT_ON = (255, 205, 100, 255)    # 自动播放中图标转金色


def _dim(color, factor):
    return (color[0], color[1], color[2], int(color[3] * factor))


# ---------------------------------------------------------------------------
# 工具条图标：按归一化形状函数程序化生成小纹理（无外部资源依赖）
# ---------------------------------------------------------------------------

def _shape_play(u, v):
    """右向实心三角（播放）。"""
    if not (0.15 <= v <= 0.85):
        return 0.0
    t = (u - 0.25) / 0.6          # 0 at 左缘顶点边，1 at 右侧尖角
    if not 0.0 <= t <= 1.0:
        return 0.0
    half = 0.35 * (1.0 - t)
    return 1.0 if abs(v - 0.5) <= half else 0.0


def _shape_camera(u, v):
    """相机机身 + 顶视窗 + 镜头圆孔。"""
    if 0.35 <= u <= 0.65 and 0.18 <= v <= 0.32:
        return 1.0
    if 0.12 <= u <= 0.88 and 0.32 <= v <= 0.82:
        du, dv = u - 0.5, v - 0.57
        return 0.0 if du * du + dv * dv <= 0.16 ** 2 else 1.0
    return 0.0


def _shape_log(u, v):
    """三条横线（日志列表）。"""
    for cy in (0.24, 0.5, 0.76):
        if 0.16 <= u <= 0.84 and abs(v - cy) <= 0.055:
            return 1.0
    return 0.0


_ICON_SHAPES = {
    "play": _shape_play,
    "camera": _shape_camera,
    "log": _shape_log,
}


def _add_icon_texture(tag, size, shape):
    """生成 size×size 图标纹理（alpha 通道承载形状，颜色由按钮 tint 注入）。"""
    rows = []
    for y in range(size):
        v = (y + 0.5) / size
        for x in range(size):
            u = (x + 0.5) / size
            rows += [1.0, 1.0, 1.0, max(0.0, min(1.0, shape(u, v)))]
    dpg.add_dynamic_texture(width=size, height=size, default_value=rows,
                            tag=tag, parent="dungeon_texture_registry")


class _TextDisplayBase(DungeonComponent):
    """galgame 式文本组件的公共基类：接管显示、行对渲染、闪烁继续指示。

    行数据源是 ``ctx.story_history`` 条目（type_str / text / highlight /
    speaker），行首标签只显示说话人（Solea/Bulla 对话分支的 ``@标记``
    解析结果）；类型前缀不上屏。最新一行全亮，旧行降透明度。
    """

    owns_text_display = True
    _BLINK_TASK = ""   # 帧任务 key（子类设置）
    _INDICATOR = ""    # 继续指示文本项 tag（子类设置）

    def __init__(self, ctx):
        super().__init__(ctx)
        self._line_tags = []      # [(label_tag, text_tag), ...]
        self._bold_font = None    # 正文粗体字体（build 时创建，找不到为 None）
        self._indicator_on = True
        self._indicator_shown = False
        self._toolbar_btn_tags = []   # [按钮 tag, ...]（[0] 是自动播放按钮）
        self._toolbar_autoplay_on = False
        self._toolbar_size = [110, 34]  # 工具条容器实际尺寸（_build_toolbar 推算）

    # ---- 内置容器接管 ----
    def _hide_builtin(self):
        for tag in ("text_container", "bg_overlay_child"):
            if dpg.does_item_exist(tag):
                dpg.hide_item(tag)

    def _text_font(self, ctx):
        """文本行专用字体（24 号，比全局 UI 字号大一号；窗口构建时创建）。"""
        return ctx.text_font_tag()

    def _create_bold_font(self, ctx):
        """正文粗体字体（与文本字号同号；找不到候选时返回 None 回退常规）。

        ``add_font`` 必须建在字体注册表容器内，否则 DPG 直接抛错。
        候选链与字号来自 ``dungeon/window/fonts.py``（与窗口侧同一份）。
        """
        if self._bold_font and dpg.does_item_exist(self._bold_font):
            dpg.delete_item(self._bold_font)
        self._bold_font = None
        s = ctx.component_viewport()[0]
        for path in BOLD_FONT_PATHS:
            if os.path.exists(path):
                try:
                    with dpg.font_registry():
                        return dpg.add_font(path, round(TEXT_FONT_SIZE * s))
                except Exception as exc:
                    process_log.log(f"[Components] 粗体字体加载失败 {path}: {exc}")
        return None

    def _bold_or_text_font(self, ctx):
        """文本行绑定用字体：窗口解析的粗体优先，再组件兜底，缺字体保持全局字号。"""
        font = ctx.bold_font_tag()
        if font:
            return font
        if self._bold_font and dpg.does_item_exist(self._bold_font):
            return self._bold_font
        return self._text_font(ctx)

    def _bind_line_fonts(self, ctx, tags):
        """把文本行字体绑到 (标签, 正文) 文本项上（缺字体时保持全局字号）。"""
        font = self._bold_or_text_font(ctx)
        if not font:
            return
        for tag in tags:
            if dpg.does_item_exist(tag):
                dpg.bind_item_font(tag, font)

    @staticmethod
    def _line_count(text, wrap_w, s):
        """估算文本在 wrap_w 宽度内折行后的行数（只偏保守，不裁字）。

        CJK 字宽 ≈ 字号，ASCII ≈ 0.55 字号；估算略高于实际渲染宽度。
        """
        if not text:
            return 0
        fs = TEXT_FONT_SIZE * s
        width = sum(fs if ord(ch) > 0x2E80 else 0.55 * fs for ch in text)
        return max(1, math.ceil(width / max(1.0, wrap_w)))

    def _restore_builtin(self):
        """恢复内置容器可见性（会话收尾后不再有组件接管显示）。"""
        for tag in ("text_container", "bg_overlay_child"):
            if dpg.does_item_exist(tag):
                dpg.show_item(tag)

    # ---- 几何 ----
    def _viewport(self, ctx):
        """(dpi_scale, 宽, 高)：几何只经服务面取，不读窗口私有属性。"""
        return ctx.component_viewport()

    # ---- 闪烁继续指示（计时走窗口帧时钟，不开线程——窗口约束 C5） ----
    def _start_blink(self, ctx):
        self._indicator_on = True
        ctx.schedule_every(_BLINK_SECONDS, self._blink, self._BLINK_TASK)

    def _blink(self):
        self._indicator_on = not self._indicator_on
        if dpg.does_item_exist(self._INDICATOR):
            dpg.configure_item(self._INDICATOR,
                               show=self._indicator_shown and self._indicator_on)

    def _update_indicator_state(self, ctx):
        """等待点击（不在生成/动画/弹窗/结局中）时才显示继续指示。

        等待判定由窗口集中提供（``session_waiting_for_input``），与自动播放共用。
        """
        self._indicator_shown = bool((self.params or {}).get(
            "show_indicator", True)) and ctx.session_waiting_for_input()

    def _place_indicator(self, x, y):
        if dpg.does_item_exist(self._INDICATOR):
            dpg.configure_item(self._INDICATOR, pos=[x, y],
                               show=self._indicator_shown and self._indicator_on)

    # ---- 行对渲染 ----
    def _display_line_pairs(self, items, label_color, text_color, highlight_color,
                            wrap, label_fmt="{label}"):
        """把 items 渲染到 _line_tags 的 (标签, 正文) 文本项对上。

        不足的行对隐藏；最新一行全亮，旧行降透明度；highlight 项用高亮色。
        行首标签只显示说话人（Solea/Bulla 对话分支的 ``@标记`` 解析结果）；
        类型前缀（【对话】等）是内部属性，不上屏（过程日志面板可见）。
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
            label = str(item.get("speaker") or "").strip()
            label = label_fmt.format(label=label) if label else ""
            dpg.configure_item(label_tag, default_value=label,
                               color=_dim(label_color,
                                          1.0 if is_newest else _OLD_LINE_ALPHA),
                               show=bool(label))
            if item.get("highlight"):
                body = highlight_color or _HIGHLIGHT_COLOR
            else:
                body = text_color
            dpg.configure_item(text_tag, default_value=item.get("text", ""),
                               color=_dim(body,
                                          1.0 if is_newest else _OLD_LINE_ALPHA),
                               wrap=wrap, show=True)

    # ---- 悬浮工具条（自动播放 / 截图 / 日志面板） ----
    # _TOOLBAR tag 由子类设置；按钮只转调窗口服务（toggle_autoplay 等），
    # 组件自身不推进剧情。工具条 parent 到 main_window 绝对定位，不随内容滚动。
    # DPG 2.3.1 怪癖：child_window 的主题 WindowPadding 不生效（ChildBg/ItemSpacing
    # 正常），容器内边距用 spacer + 按钮自身 FramePadding 拼出来。
    def _build_toolbar(self, ctx):
        """创建圆角容器工具条与三个图标按钮（子类在 build/layout 里定位）。

        容器尺寸按图标边长与内边距推算，推算结果缓存到 _toolbar_size 供定位用。
        图标纹理一次性生成（白色 + alpha 形状），颜色由 image_button 的 tint 注入。
        """
        if dpg.does_item_exist(self._TOOLBAR):
            dpg.delete_item(self._TOOLBAR)
        self._toolbar_btn_tags = []
        s = getattr(ctx, "_dpi_scale", 1.0) or 1.0
        icon = max(6, round(_TOOLBAR_ICON * s))
        pad_x, pad_y = round(_TOOLBAR_PAD_X * s), round(_TOOLBAR_PAD_Y * s)
        frame_x, frame_y = round(_TOOLBAR_FRAME_X * s), round(_TOOLBAR_FRAME_Y * s)
        gap = round(_TOOLBAR_GAP * s)
        w = (len(_TOOLBAR_ACTIONS) * (icon + 2 * pad_x)
             + (len(_TOOLBAR_ACTIONS) - 1) * gap + 2 * frame_x)
        h = icon + 2 * pad_y + 2 * frame_y
        self._toolbar_size = [w, h]
        with dpg.child_window(
                tag=self._TOOLBAR, parent="main_window",
                width=w, height=h, border=True):
            dpg.add_spacer(height=frame_y)
            with dpg.group(horizontal=True):
                dpg.add_spacer(width=frame_x)
                for key, icon_name, tip in _TOOLBAR_ACTIONS:
                    tex_tag = f"{self._TOOLBAR}_icon_{key}"
                    if dpg.does_item_exist(tex_tag):
                        dpg.delete_item(tex_tag)
                    _add_icon_texture(tex_tag, icon, _ICON_SHAPES[icon_name])
                    tag = dpg.add_image_button(
                        tex_tag, width=icon, height=icon, tint_color=_TOOLBAR_TINT,
                        user_data=key, callback=self._on_toolbar_action)
                    self._toolbar_btn_tags.append(tag)
                    with dpg.tooltip(tag):
                        dpg.add_text(tip)
        with dpg.theme() as theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (10, 12, 20, 100))
                dpg.add_theme_color(dpg.mvThemeCol_Border, (255, 255, 255, 32))
                dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, round(8 * s))
                dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, gap, 0)
        dpg.bind_item_theme(self._TOOLBAR, theme)
        # 按钮本身无色，只有悬停 / 按下色（galgame 式轻量功能按钮）；
        # 自动播放中由 _sync_toolbar 把自动播放图标 tint 转金色
        with dpg.theme() as btn_theme:
            with dpg.theme_component(dpg.mvImageButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (0, 0, 0, 0))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,
                                    (255, 255, 255, 46))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,
                                    (255, 255, 255, 72))
                dpg.add_theme_style(dpg.mvStyleVar_FramePadding, pad_x, pad_y)
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, round(5 * s))
        for tag in self._toolbar_btn_tags:
            dpg.bind_item_theme(tag, btn_theme)

    def _place_toolbar(self, ctx, right_x, y):
        """把工具条右缘对齐 right_x、顶缘对齐 y（子类按各自几何换算后调用）。"""
        if not dpg.does_item_exist(self._TOOLBAR):
            return
        bar_w = self._toolbar_size[0]
        dpg.configure_item(self._TOOLBAR, pos=[max(0, round(right_x - bar_w)),
                                               max(0, round(y))])

    def _sync_toolbar(self, ctx):
        """把自动播放开关状态同步到图标着色（refresh 链反复调用，状态不变即跳过）。"""
        on = ctx.component_autoplay_on()
        if on == self._toolbar_autoplay_on or not self._toolbar_btn_tags:
            return
        self._toolbar_autoplay_on = on
        tag = self._toolbar_btn_tags[0]
        if dpg.does_item_exist(tag):
            dpg.configure_item(tag, tint_color=_TOOLBAR_TINT_ON if on else _TOOLBAR_TINT)

    def toolbar_hovered(self):
        """光标是否悬停在工具条上（窗口 _on_mouse_click 据此不推进剧情）。"""
        if not dpg.does_item_exist(self._TOOLBAR):
            return False
        if dpg.is_item_hovered(self._TOOLBAR):
            return True
        return any(dpg.does_item_exist(tag) and dpg.is_item_hovered(tag)
                   for tag in self._toolbar_btn_tags)

    def _on_toolbar_action(self, sender, app_data, user_data):
        handler = getattr(self.ctx, _TOOLBAR_HANDLERS.get(user_data, ""), None)
        if callable(handler):
            handler()

    def _destroy_toolbar(self):
        if dpg.does_item_exist(self._TOOLBAR):
            dpg.delete_item(self._TOOLBAR)
        self._toolbar_btn_tags = []

    def destroy(self, ctx):
        if self._BLINK_TASK:
            ctx.cancel_task(self._BLINK_TASK)
        self._line_tags = []
        if self._bold_font and dpg.does_item_exist(self._bold_font):
            dpg.delete_item(self._bold_font)
        self._bold_font = None
        self._restore_builtin()
