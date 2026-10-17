"""过程日志组件（proc_log）：右上角运行消息面板"""

from imgui_bundle import imgui

from dungeon import process_log
from dungeon.window import imgui_util as iu
from dungeon.window.component_registry import DungeonComponent


_LOG_COLOR = (232, 232, 232, 255)


class ProcLogComponent(DungeonComponent):
    """过程日志：右上角面板，显示 dungeon.process_log 收集的运行消息。"""

    id = "proc_log"
    label = "过程日志"
    description = "右上角面板显示运行消息（F12 切换展开/收起，默认收起）。"
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
        self._lines = []
        self._open = False
        self._layout = None

    def _geometry(self, ctx):
        s, w, _h = ctx.component_viewport()
        width = int((self.params or {}).get("panel_width") or 460)
        scroll_h = int((self.params or {}).get("scroll_height") or 300)
        return s, w, round(width * s), round(scroll_h * s), round(8 * s), round(6 * s)

    def build(self, ctx):
        self._lines = []
        self._open = False
        self.layout(ctx)
        backlog = process_log.subscribe(self._on_message)
        for line in backlog:
            ctx.schedule(self._append, line)

    def layout(self, ctx):
        s, w, width, scroll_h, margin, pad = self._geometry(ctx)
        x = max(0.0, w - width - margin)
        y = float(margin)
        panel_h = scroll_h + round(30 * s)
        self._layout = (s, x, y, width, panel_h, scroll_h, margin, pad)
        self._panel_rect = (x, y, width, panel_h)

    def toggle(self, ctx):
        self._open = not self._open

    def _on_message(self, text):
        self.ctx.schedule(self._append, text)

    def _append(self, text):
        self._lines.append(str(text))
        max_lines = int((self.params or {}).get("max_lines") or 300)
        if len(self._lines) > max_lines:
            del self._lines[:-max_lines]

    def draw(self, ctx):
        if not self._open:
            return
        if self._layout is None:
            self.layout(ctx)
        s, x, y, width, panel_h, scroll_h, _margin, pad = self._layout
        draw_list = imgui.get_window_draw_list()
        pmin = iu.v2(x, y)
        pmax = iu.v2(x + width, y + panel_h)
        radius = round(10 * s)
        draw_list.add_rect_filled(pmin, pmax, iu.u32(12, 12, 12, 175), radius)
        draw_list.add_rect(pmin, pmax, iu.u32(255, 255, 255, 32), radius)
        title = "过程日志（F12 收起）"
        draw_list.add_text(iu.v2(x + pad, y + pad), iu.u32(200, 205, 215, 255), title)
        title_h = float(imgui.calc_text_size(title).y) + pad
        imgui.set_cursor_pos(iu.v2(x + pad, y + title_h))
        imgui.begin_child(self.TAG_PREFIX + "##scroll",
                          iu.v2(max(1.0, width - 2 * pad), float(scroll_h)),
                          imgui.ChildFlags_.none.value,
                          imgui.WindowFlags_.none.value)
        imgui.push_text_wrap_pos(max(1.0, width - 2 * pad))
        for line in self._lines:
            imgui.push_style_color(imgui.Col_.text, iu.u32(*_LOG_COLOR))
            imgui.text_wrapped(line)
            imgui.pop_style_color()
        imgui.pop_text_wrap_pos()
        imgui.set_scroll_here_y(1.0)
        imgui.end_child()

    def refresh(self, ctx):
        self.layout(ctx)

    def destroy(self, ctx):
        process_log.unsubscribe(self._on_message)
        self._lines = []
        self._layout = None
