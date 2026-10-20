"""过程日志组件（proc_log）：文本组件侧边代管区 / 右上角运行消息面板"""

from imgui_bundle import imgui

from dungeon import process_log
from dungeon.window import imgui_util as iu
from dungeon.window.component_registry import DungeonComponent
from dungeon.window.layout import SHAPE_RAIL, enter_direction, enter_transform


_LOG_COLOR = (232, 232, 232, 255)
_TITLE = "过程日志（F12 收起）"


def _fade(color, alpha):
    """按入场透明度缩放颜色 alpha；``alpha`` 为 1 时原样返回。"""
    if alpha >= 0.999:
        return color
    factor = max(0.0, min(1.0, float(alpha)))
    return (color[0], color[1], color[2], int(color[3] * factor))


class ProcLogComponent(DungeonComponent):
    """过程日志：优先停靠文本组件的侧边代管区，没有时回退右上角面板。

    只有面板展开时才声明 ``log`` 角色——收起状态不占用代管区，宿主也就不会
    平白偏移。展开后的呈现由 ``SlotStyle`` 的入场契约决定：卡片的 ``card_side``
    从所贴的屏幕边缘平移进入，渐变栏的 ``scrim_side`` 从代管区下方升起并淡入。
    """

    id = "proc_log"
    label = "过程日志"
    description = ("显示运行消息（F12 切换展开/收起，默认收起）；展开时优先停靠"
                   "文本组件的侧边代管区（卡片侧边滑入、渐变栏侧边升起淡入），"
                   "没有代管区时回退右上角。")
    TAG_PREFIX = "proc_log"
    shapes = (SHAPE_RAIL,)
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
        self._slot_rect = None
        self._slot_style = None
        self._enter_progress = 1.0

    @property
    def roles(self):
        """展开时参与代管区匹配；收起时为空（不预留位置）。"""
        return ("log",) if self._open else ()

    def _geometry(self, ctx):
        s, w, _h = ctx.component_viewport()
        width = int((self.params or {}).get("panel_width") or 460)
        scroll_h = int((self.params or {}).get("scroll_height") or 300)
        return s, w, round(width * s), round(scroll_h * s), round(8 * s), round(6 * s)

    def build(self, ctx):
        self._lines = []
        self._open = False
        self._slot_rect = None
        self._slot_style = None
        self._enter_progress = 1.0
        self.layout(ctx)
        backlog = process_log.subscribe(self._on_message)
        for line in backlog:
            ctx.schedule(self._append, line)

    def layout(self, ctx):
        slot = ctx.component_slot(self.id)
        rect = ctx.component_rect(self.id)
        if slot is not None and rect:
            if self._slot_rect is None:
                # 首次拿到代管区（含从回退面板切过来）：从头播放入场动画。
                self._enter_progress = 0.0
            self._slot_rect = tuple(rect)
            self._slot_style = slot.style
            self._layout = None
            self._panel_rect = tuple(rect)
            return
        self._slot_rect = None
        self._slot_style = None
        s, w, width, scroll_h, margin, pad = self._geometry(ctx)
        x = max(0.0, w - width - margin)
        y = float(margin)
        panel_h = scroll_h + round(30 * s)
        self._layout = (s, x, y, width, panel_h, scroll_h, margin, pad)
        self._panel_rect = (x, y, width, panel_h)

    def toggle(self, ctx):
        self._open = not self._open
        if self._open:
            self._enter_progress = 0.0

    def _on_message(self, text):
        self.ctx.schedule(self._append, text)

    def _append(self, text):
        self._lines.append(str(text))
        max_lines = int((self.params or {}).get("max_lines") or 300)
        if len(self._lines) > max_lines:
            del self._lines[:-max_lines]

    def _enter_state(self, rect, viewport_w, viewport_h):
        """代管区要求的入场呈现：``(水平位移, 纵向位移, 透明度)``。"""
        style = self._slot_style
        if style is None or not enter_direction(style):
            return 0.0, 0.0, 1.0
        dt = min(float(imgui.get_io().delta_time), 0.1)
        seconds = max(1e-3, float(style.enter_seconds or 0.0))
        if self._enter_progress < 1.0:
            self._enter_progress = min(1.0, self._enter_progress + dt / seconds)
        return enter_transform(style, rect, viewport_w, viewport_h,
                               self._enter_progress)

    def draw(self, ctx):
        if not self._open:
            return
        if self._slot_rect is not None:
            x, y, w, h = self._slot_rect
            _s, viewport_w, viewport_h = ctx.component_viewport()
            dx, dy, alpha = self._enter_state(self._slot_rect,
                                              viewport_w, viewport_h)
            self._draw_panel(ctx, x + dx, y + dy, w, h, alpha)
            return
        if self._layout is None:
            self.layout(ctx)
        _s, x, y, width, panel_h, _scroll_h, _margin, _pad = self._layout
        self._draw_panel(ctx, x, y, width, panel_h)

    def _draw_panel(self, ctx, x, y, w, h, alpha=1.0):
        s = ctx.component_viewport()[0]
        draw_list = imgui.get_window_draw_list()
        pad = round(8 * s)
        radius = round(10 * s)
        draw_list.add_rect_filled(iu.v2(x, y), iu.v2(x + w, y + h),
                                  iu.u32(*_fade((12, 12, 12, 175), alpha)),
                                  radius)
        draw_list.add_rect(iu.v2(x, y), iu.v2(x + w, y + h),
                           iu.u32(*_fade((255, 255, 255, 32), alpha)), radius)
        draw_list.add_text(iu.v2(x + pad, y + pad),
                           iu.u32(*_fade((200, 205, 215, 255), alpha)), _TITLE)
        title_h = float(imgui.calc_text_size(_TITLE).y) + pad
        child_w = max(1.0, w - 2 * pad)
        child_h = max(1.0, h - title_h - pad)
        imgui.set_cursor_pos(iu.v2(x + pad, y + title_h))
        imgui.begin_child(self.TAG_PREFIX + "##scroll",
                          iu.v2(child_w, child_h),
                          imgui.ChildFlags_.none.value,
                          imgui.WindowFlags_.none.value)
        imgui.push_text_wrap_pos(child_w)
        for line in self._lines:
            imgui.push_style_color(imgui.Col_.text,
                                   iu.u32(*_fade(_LOG_COLOR, alpha)))
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
        self._slot_rect = None
        self._slot_style = None
        self._enter_progress = 1.0
