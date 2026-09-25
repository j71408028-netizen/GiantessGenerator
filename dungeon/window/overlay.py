"""窗口级覆盖层服务：文本主组件（及键位）可调出的模态浮层。

服务面（组件经 ctx 调用，见 component_registry.DungeonComponent 契约）：
- ``ctx.toggle_overlay(name)``  调出/关闭覆盖层（当前支持 ``"log"`` 对话记录）
- ``ctx.close_overlay()``       关闭当前覆盖层
- ``ctx.overlay_open()``        查询当前覆盖层名（None = 无覆盖层）
- ``ctx.component(cid)``        只读访问兄弟组件实例（不接管其生命周期）

覆盖层打开期间窗口进入「阅读模态」：点击只关闭覆盖层、不推进剧情，空格 /
回车的推进也一并挂起（`_on_next_step` 入口判定；H / ESC 随时可用）。覆盖层
在所有组件之后创建（z 序最上），随 ``_relayout`` 重排、随 ``_finish_session`` 销毁。

线程约束：全部方法只在主线程调用（组件钩子与 DPG 回调均为主线程）。
"""

import dearpygui.dearpygui as dpg

from dungeon import process_log

_HIGHLIGHT_COLOR = (255, 200, 60, 255)
_LABEL_COLOR = (255, 170, 210, 255)
_TEXT_COLOR = (240, 240, 245, 255)
_DIM_COLOR = (150, 155, 170, 255)


class OverlayHandler:
    """覆盖层服务：模态浮层的构建、挂起语义与生命周期。"""

    # ---- 服务面（组件经 ctx 调用） ----
    def overlay_open(self):
        """当前打开的覆盖层名；None 表示无覆盖层。"""
        return getattr(self, "_overlay_name", None)

    def toggle_overlay(self, name="log"):
        """调出 / 关闭指定覆盖层（同名校正：打开时再次调用即关闭）。"""
        if self.overlay_open() == name:
            self.close_overlay()
            return
        self.close_overlay()
        if name == "log":
            self._build_log_overlay()
            self._overlay_name = name
        else:
            self._notify(f"未知覆盖层：{name}")

    def close_overlay(self):
        """关闭当前覆盖层（未打开时为无操作）。"""
        name = getattr(self, "_overlay_name", None)
        if not name:
            return
        self._overlay_name = None
        if dpg.does_item_exist("overlay_log"):
            dpg.delete_item("overlay_log")

    def component(self, cid):
        """只读访问兄弟组件实例（未知或未启用时返回 None）。

        仅用于读取参数与展示状态；组件生命周期（build/layout/destroy）仍由
        窗口集中管理，调用方不得自行构建或销毁组件。
        """
        for comp in getattr(self, "_components", []):
            if comp.id == cid:
                return comp
        return None

    # ---- 键位（ui._build_ui 注册） ----
    def _on_log_key(self, sender=None, app_data=None):
        """H 键：调出 / 关闭对话记录（入口阶段忽略）。"""
        if getattr(self, "_is_entry_phase", False) or self._closing:
            return
        self.toggle_overlay("log")

    def _on_escape_key(self, sender=None, app_data=None):
        """ESC 键：关闭已打开的覆盖层。"""
        if self.overlay_open():
            self.close_overlay()

    # ---- 覆盖层实现 ----
    def _build_log_overlay(self):
        """对话记录（Backlog）：居中半透明面板，快照展示全部 story_history。"""
        if dpg.does_item_exist("overlay_log"):
            dpg.delete_item("overlay_log")
        s = getattr(self, "_dpi_scale", 1.0) or 1.0
        w = getattr(self, "_layout_w", 0) or dpg.get_viewport_client_width()
        h = getattr(self, "_layout_h", 0) or dpg.get_viewport_client_height()
        pad = round(20 * s)
        panel_w, panel_h = max(round(400 * s), round(w * 0.8)), max(round(300 * s), round(h * 0.84))
        pos = [max(round(8 * s), (w - panel_w) // 2), max(round(8 * s), (h - panel_h) // 2)]
        header_h = round(34 * s)
        scroll_h = panel_h - 2 * pad - header_h
        wrap = max(1, panel_w - 2 * pad - round(14 * s))

        dpg.add_child_window(tag="overlay_log", parent="main_window",
                             pos=pos, width=panel_w, height=panel_h, border=True)
        dpg.configure_item("overlay_log", no_scrollbar=True, no_scroll_with_mouse=True)
        with dpg.theme() as panel_theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (10, 12, 22, 235))
                dpg.add_theme_color(dpg.mvThemeCol_Border, (255, 255, 255, 50))
                dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, round(12 * s))
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, pad, pad)
        dpg.bind_item_theme("overlay_log", panel_theme)

        # C6 怪癖：容器块之后创建的控件推断不出父级，全部显式指定 parent
        dpg.add_text("对话记录", color=_LABEL_COLOR, parent="overlay_log")
        dpg.add_text("点击 / H / ESC 关闭", color=_DIM_COLOR, parent="overlay_log")

        with dpg.child_window(tag="overlay_log_scroll", border=False,
                              height=scroll_h, parent="overlay_log"):
            for item in getattr(self, "story_history", []):
                raw = (item.get("speaker") or item.get("type_str") or "").strip()
                if raw:
                    dpg.add_text(f"[ {raw} ]", color=_LABEL_COLOR, wrap=wrap,
                                 parent="overlay_log_scroll")
                body = _HIGHLIGHT_COLOR if item.get("highlight") else _TEXT_COLOR
                dpg.add_text(item.get("text", ""), color=body, wrap=wrap,
                             parent="overlay_log_scroll")
        state = dpg.get_item_state("overlay_log_scroll") or {}
        max_scroll = state.get("y_scroll_max")
        if max_scroll:
            dpg.set_y_scroll("overlay_log_scroll", max_scroll)

    def _relayout_overlays(self):
        """视口变化时重排覆盖层（重建最简单：内容快照无需迁移）。"""
        if self.overlay_open() == "log":
            self._build_log_overlay()

    def _destroy_overlays(self):
        """会话收尾时清理覆盖层控件与模态标记。"""
        self._overlay_name = None
        if dpg.does_item_exist("overlay_log"):
            dpg.delete_item("overlay_log")
