"""窗口级覆盖层服务：文本主组件（及键位）可调出的模态浮层。

服务面（组件经 ctx 调用）：
- ``ctx.toggle_overlay(name)``  调出/关闭覆盖层（当前支持 ``"log"`` 对话记录）
- ``ctx.close_overlay()``       关闭当前覆盖层
- ``ctx.overlay_open()``        查询当前覆盖层名（None = 无覆盖层）
- ``ctx.component(cid)``        只读访问兄弟组件实例

覆盖层打开期间窗口进入「阅读模态」：点击只关闭覆盖层、不推进剧情。
P4 起面板由 ``ui._draw_frame`` 每帧调用 :meth:`_draw_log_overlay` 立即绘制。
"""

from imgui_bundle import imgui

from dungeon.window import imgui_util as iu


_HIGHLIGHT_COLOR = (255, 200, 60, 255)
_LABEL_COLOR = (255, 170, 210, 255)
_TEXT_COLOR = (240, 240, 245, 255)


class OverlayHandler:
    """覆盖层服务：模态浮层的状态与立即模式绘制。"""

    # ---- 服务面（组件经 ctx 调用） ----
    def overlay_open(self):
        if getattr(self, "_mini_game_stage", None) is not None:
            return "minigame"
        return getattr(self, "_overlay_name", None)

    def toggle_overlay(self, name="log"):
        if self.overlay_open() == name:
            self.close_overlay()
            return
        self.close_overlay()
        if name == "log":
            self._overlay_name = name
        else:
            self._notify(f"未知覆盖层：{name}")

    def close_overlay(self):
        if getattr(self, "_overlay_name", None):
            self._overlay_name = None

    def component(self, cid):
        for comp in getattr(self, "_components", []):
            if comp.id == cid:
                return comp
        return None

    # ---- 键位 ----
    def _on_log_key(self, sender=None, app_data=None):
        if getattr(self, "_is_entry_phase", False) or self._closing:
            return
        self.toggle_overlay("log")

    def _on_escape_key(self, sender=None, app_data=None):
        if getattr(self, "_mini_game_stage", None) is not None:
            self._minigame_escape()
            return
        if self.overlay_open():
            self.close_overlay()

    # ---- 立即模式绘制 ----
    def _draw_log_overlay(self, w, h):
        """对话记录覆盖层：半透明遮罩 + 居中面板 + 历史段落。"""
        if self.overlay_open() != "log":
            self._overlay_drawn = False
            return
        s = max(0.5, float(getattr(self, "_dpi_scale", 1.0) or 1.0))
        pad = round(16 * s)
        panel_w = min(max(320.0, w - 80 * s), 900 * s)
        panel_h = min(max(240.0, h - 120 * s), 600 * s)
        x = max(0.0, (w - panel_w) / 2.0)
        y = max(0.0, (h - panel_h) / 2.0)
        draw_list = imgui.get_window_draw_list()
        draw_list.add_rect_filled(iu.v2(0.0, 0.0), iu.v2(w, h), iu.u32(0, 0, 0, 120))
        radius = round(10 * s)
        draw_list.add_rect_filled(iu.v2(x, y), iu.v2(x + panel_w, y + panel_h),
                                  iu.u32(18, 20, 30, 245), radius)
        draw_list.add_rect(iu.v2(x, y), iu.v2(x + panel_w, y + panel_h),
                           iu.u32(255, 255, 255, 50), radius)
        draw_list.add_text(iu.v2(x + pad, y + pad), iu.u32(230, 235, 245, 255),
                           "对话记录（H / ESC 关闭）")
        title_h = float(imgui.calc_text_size("对话记录").y) + pad
        imgui.set_cursor_pos(iu.v2(x + pad, y + title_h))
        imgui.begin_child("##overlay_log_scroll",
                          iu.v2(max(1.0, panel_w - 2 * pad),
                                max(1.0, panel_h - title_h - pad)),
                          imgui.ChildFlags_.none.value,
                          imgui.WindowFlags_.none.value)
        imgui.push_text_wrap_pos(max(1.0, panel_w - 2 * pad))
        for item in (getattr(self, "story_history", None) or []):
            label = str(item.get("speaker") or "").strip()
            if label:
                imgui.push_style_color(imgui.Col_.text, iu.u32(*_LABEL_COLOR))
                imgui.text(label)
                imgui.pop_style_color()
            body = _HIGHLIGHT_COLOR if item.get("highlight") else _TEXT_COLOR
            imgui.push_style_color(imgui.Col_.text, iu.u32(*body))
            imgui.text_wrapped(str(item.get("text") or ""))
            imgui.pop_style_color()
            imgui.dummy(iu.v2(0.0, 6.0))
        imgui.pop_text_wrap_pos()
        imgui.set_scroll_here_y(1.0)
        imgui.end_child()
        self._overlay_rect = (x, y, panel_w, panel_h)
        self._overlay_drawn = True

    def _build_log_overlay(self):
        """P4：无 retained 控件可建；状态由 :meth:`_draw_log_overlay` 每帧读取。"""

    def _relayout_overlays(self):
        """立即模式覆盖层几何每帧按客户区现算，无需重排。"""

    def _destroy_overlays(self):
        self._overlay_name = None
        self._overlay_drawn = False
        self._overlay_rect = None
        if getattr(self, "_mini_game_stage", None) is not None:
            self._destroy_mini_game_stage()
