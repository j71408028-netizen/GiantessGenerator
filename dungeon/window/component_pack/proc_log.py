"""过程日志组件（proc_log）：右上角运行消息面板"""

import dearpygui.dearpygui as dpg

from dungeon import process_log
from dungeon.window.component_registry import DungeonComponent


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
    label = "过程日志"
    description = "右上角面板显示运行消息（F12 切换展开/收起，默认收起）。"
    #: 本组件显式 tag 的统一前缀（守卫据此查命名空间冲突）
    TAG_PREFIX = "proc_log"
    param_specs = [
        {"key": "panel_width", "label": "面板宽度", "type": "int",
         "default": 460, "min": 240, "max": 960},
        {"key": "scroll_height", "label": "日志区高度", "type": "int",
         "default": 300, "min": 60, "max": 800},
        {"key": "max_lines", "label": "最大行数", "type": "int",
         "default": 300, "min": 50, "max": 2000},
    ]

    def __init__(self, ctx):
        super().__init__(ctx)
        self._tags = []      # 日志行 text 控件 tag（FIFO，超上限删最旧）
        self._open = False   # 当前是否展开
        self._wrap = 0

    def _geometry(self, ctx):
        """返回 (dpi_scale, 面板宽, 日志区高, 外边距, 内边距)，均按 scale 换算。"""
        s = ctx.component_viewport()[0]
        width = int((self.params or {}).get("panel_width") or 460)
        scroll_h = int((self.params or {}).get("scroll_height") or 300)
        return (s, round(width * s), round(scroll_h * s), round(8 * s), round(6 * s))

    def build(self, ctx):
        if dpg.does_item_exist(_PROC_LOG_PANEL_TAG):
            dpg.delete_item(_PROC_LOG_PANEL_TAG)
        self._tags = []
        self._open = False
        s, width, scroll_h, margin, pad = self._geometry(ctx)
        vw = ctx.component_viewport()[1]
        self._wrap = max(1, width - 2 * pad - round(18 * s))

        with dpg.child_window(
                tag=_PROC_LOG_PANEL_TAG, parent="main_window",
                pos=[max(0, vw - width - margin), margin],
                width=width, height=scroll_h + round(30 * s),
                show=False, no_scrollbar=True, no_scroll_with_mouse=True,
                border=False):
            dpg.add_spacer(height=pad)
            dpg.add_text("过程日志（F12 收起）", tag=_PROC_LOG_TITLE_TAG,
                         parent=_PROC_LOG_PANEL_TAG, indent=pad,
                         color=(200, 205, 215, 255))
            with dpg.child_window(tag=_PROC_LOG_SCROLL_TAG, height=scroll_h,
                                  indent=pad, border=True):
                pass

        with dpg.theme() as theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (12, 12, 12, 175))
                dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, round(10 * s))
        dpg.bind_item_theme(_PROC_LOG_PANEL_TAG, theme)

        # 订阅过程日志：缓冲积压（构造 / 会话初始化阶段的消息）一并补显
        backlog = process_log.subscribe(self._on_message)
        for line in backlog:
            ctx.schedule(self._append, line)

    def toggle(self, ctx):
        """F12 调用：展开 / 收起面板（主线程）。"""
        self._open = not self._open
        if dpg.does_item_exist(_PROC_LOG_PANEL_TAG):
            dpg.configure_item(_PROC_LOG_PANEL_TAG, show=self._open)

    def _on_message(self, text):
        """process_log 订阅回调（任意线程）：把日志投递回主线程显示。"""
        self.ctx.schedule(self._append, text)

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
        vw = ctx.component_viewport()[1]
        dpg.configure_item(_PROC_LOG_PANEL_TAG,
                           pos=[max(0, vw - width - margin), margin],
                           width=width, height=scroll_h + round(30 * s))

    def destroy(self, ctx):
        process_log.unsubscribe(self._on_message)
        if dpg.does_item_exist(_PROC_LOG_PANEL_TAG):
            dpg.delete_item(_PROC_LOG_PANEL_TAG)
        self._tags = []
