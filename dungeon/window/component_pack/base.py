"""官方组件包公共基类：立即模式绘制、工具条与文本行。

P4 迁移后，本模块不再创建 DPG 控件：组件在 ``build`` / ``layout`` 里只准备
状态与几何，真正的绘制在每帧的 ``draw(ctx)`` 里由 ``imgui`` draw list 完成。
组件仍只经 ``dungeon.window.components.ComponentHandler`` 提供的服务面访问窗口。
"""

from imgui_bundle import hello_imgui, imgui

from dungeon.window import imgui_util as iu
from dungeon.window.component_registry import DungeonComponent
from dungeon.window.fonts import TEXT_FONT_SIZE


_HIGHLIGHT_COLOR = (255, 200, 60, 255)
_OLD_LINE_ALPHA = 0.55
_DEFAULT_LABEL_COLOR = (255, 170, 210, 255)
_DEFAULT_TEXT_COLOR = (240, 240, 245, 255)
_BLINK_SECONDS = 0.65

# 文本组件共享的悬浮工具条（自动播放 / 截图 / 日志面板）。
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
_TOOLBAR_ICON = 16
_TOOLBAR_PAD = 4
_TOOLBAR_GAP = 6
_TOOLBAR_FRAME_X = 9
_TOOLBAR_FRAME_Y = 6
_TOOLBAR_TINT = (232, 236, 244, 255)
_TOOLBAR_TINT_ON = (255, 205, 100, 255)


def _dim(color, factor):
    return (color[0], color[1], color[2], int(color[3] * factor))


class _TextDisplayBase(DungeonComponent):
    """文本组件公共基类：接管显示、行渲染、继续指示与悬浮工具条。"""

    owns_text_display = True
    _BLINK_TASK = ""
    TAG_PREFIX = ""

    def __init__(self, ctx):
        super().__init__(ctx)
        self._indicator_on = True
        self._toolbar_textures = {}
        self._toolbar_hovered = False
        self._toolbar_rect = (0.0, 0.0, 0.0, 0.0)
        self._drawn_line_count = 0
        self._panel_rect = (0.0, 0.0, 0.0, 0.0)

    # ---- 生命周期 ----
    def build(self, ctx):
        self._indicator_on = True
        if self._BLINK_TASK:
            ctx.schedule_every(_BLINK_SECONDS, self._blink, self._BLINK_TASK)
        self.layout(ctx)

    def layout(self, ctx):
        """子类覆盖：只计算几何/缓存，不调 imgui。"""

    def refresh(self, ctx):
        self.layout(ctx)

    def _blink(self):
        self._indicator_on = not self._indicator_on

    def drain_gpu_textures(self):
        """交出自己的 ``TextureGpu`` 句柄（窗口在 GL 存活时统一释放）。

        ``destroy`` 在 ``_finish_session`` 里跑，那时 ``hello_imgui.run()`` 已经
        返回、GL 后端已释放；在那里丢句柄会让 RAII 析构去碰失效后端而段错误
        （见 :mod:`dungeon.window.gpu_textures`）。所以句柄一律提前经这里交出去，
        由窗口的 ``_release_gpu_textures``（``before_exit``，GL 仍存活）真正释放。

        离屏自检（``check_component_pack``）里没有 GL，工具条纹理恒为空表，
        本方法返回空列表。
        """
        handles = list(self._toolbar_textures.values())
        self._toolbar_textures = {}
        return handles

    def destroy(self, ctx):
        # 工具条纹理是 GPU 资源，正常路径已在 before_exit 里经 drain_gpu_textures
        # 交出去释放；这里只兜底清状态（若组件是在会话中途被销毁、GL 仍在，清空
        # 即释放，也是对的）。**别把这一步当成"资源释放点"**——收尾期 GL 已没了。
        if self._BLINK_TASK:
            ctx.cancel_task(self._BLINK_TASK)
        self._toolbar_textures = {}

    def toolbar_hovered(self):
        return bool(self._toolbar_hovered)

    # ---- 数据 ----
    def _visible_items(self, ctx, max_lines=None):
        history = list(getattr(ctx, "story_history", None) or [])
        items = [it for it in history if (it.get("text") or str(it.get("speaker") or "").strip())]
        if max_lines is not None:
            return items[-max(1, int(max_lines)):]
        return items

    # ---- 文本绘制：调用方先算好绝对坐标（根窗口局部坐标） ----
    def _draw_text_lines(self, ctx, x, y, wrap_x, items,
                         label_color=None, text_color=None, highlight_color=None,
                         label_fmt="{label}"):
        label_color = label_color or _DEFAULT_LABEL_COLOR
        text_color = text_color or _DEFAULT_TEXT_COLOR
        highlight_color = highlight_color or _HIGHLIGHT_COLOR
        if not items:
            self._drawn_line_count = 0
            return
        font = ctx.bold_font_tag() or ctx.text_font_tag()
        pushed = False
        if font is not None:
            imgui.push_font(font, TEXT_FONT_SIZE)
            pushed = True
        try:
            imgui.set_cursor_pos(iu.v2(x, y))
            total = len(items)
            for idx, item in enumerate(items):
                newest = idx == total - 1
                alpha = 1.0 if newest else _OLD_LINE_ALPHA
                label = str(item.get("speaker") or "").strip()
                if label:
                    label = label_fmt.format(label=label) if label_fmt else label
                    imgui.push_style_color(imgui.Col_.text, iu.u32(*_dim(label_color, alpha)))
                    imgui.text(label)
                    imgui.pop_style_color()
                body = highlight_color if item.get("highlight") else text_color
                imgui.push_style_color(imgui.Col_.text, iu.u32(*_dim(body, alpha)))
                imgui.push_text_wrap_pos(float(wrap_x))
                imgui.text_wrapped(str(item.get("text") or ""))
                imgui.pop_text_wrap_pos()
                imgui.pop_style_color()
                imgui.dummy(iu.v2(0.0, 8.0))
            self._drawn_line_count = total
        finally:
            if pushed:
                imgui.pop_font()

    def _draw_indicator(self, ctx, x, y, text=None):
        if not bool((self.params or {}).get("show_indicator", True)):
            return
        if not self._indicator_on or not ctx.session_waiting_for_input():
            return
        draw_list = imgui.get_window_draw_list()
        draw_list.add_text(iu.v2(x, y), iu.u32(*_DEFAULT_LABEL_COLOR),
                           text or "▼")

    # ---- 工具条（draw list + 自定义按钮，不建额外窗口） ----
    def _toolbar_size(self, s):
        icon = max(6, round(_TOOLBAR_ICON * s))
        pad = round(_TOOLBAR_PAD * s)
        gap = round(_TOOLBAR_GAP * s)
        frame_x = round(_TOOLBAR_FRAME_X * s)
        frame_y = round(_TOOLBAR_FRAME_Y * s)
        w = len(_TOOLBAR_ACTIONS) * (icon + 2 * pad) + (len(_TOOLBAR_ACTIONS) - 1) * gap + 2 * frame_x
        h = icon + 2 * pad + 2 * frame_y
        return float(w), float(h)

    def _ensure_toolbar_textures(self, s):
        if self._toolbar_textures:
            return
        icon = max(6, round(_TOOLBAR_ICON * s))
        for key, icon_name, _tip in _TOOLBAR_ACTIONS:
            rgba = iu.icon_rgba(iu.ICON_SHAPES[icon_name], icon)
            self._toolbar_textures[key] = hello_imgui.create_texture_gpu_from_rgba_data(rgba)

    def _draw_toolbar(self, ctx, x, y):
        s = float(ctx.component_viewport()[0])
        self._ensure_toolbar_textures(s)
        icon = max(6, round(_TOOLBAR_ICON * s))
        pad = round(_TOOLBAR_PAD * s)
        gap = round(_TOOLBAR_GAP * s)
        frame_x = round(_TOOLBAR_FRAME_X * s)
        frame_y = round(_TOOLBAR_FRAME_Y * s)
        bw, bh = self._toolbar_size(s)
        x = max(0.0, float(x))
        y = max(0.0, float(y))
        self._toolbar_rect = (x, y, bw, bh)
        draw_list = imgui.get_window_draw_list()
        draw_list.add_rect_filled(iu.v2(x, y), iu.v2(x + bw, y + bh),
                                  iu.u32(10, 12, 20, 100), round(8 * s))
        draw_list.add_rect(iu.v2(x, y), iu.v2(x + bw, y + bh),
                           iu.u32(255, 255, 255, 32), round(8 * s))
        self._toolbar_hovered = bool(
            imgui.is_mouse_hovering_rect(iu.v2(x, y), iu.v2(x + bw, y + bh)))
        autoplay_on = bool(ctx.component_autoplay_on())
        for index, (key, _icon_name, tip) in enumerate(_TOOLBAR_ACTIONS):
            bx = x + frame_x + index * (icon + 2 * pad + gap)
            by = y + frame_y
            hover = imgui.is_mouse_hovering_rect(
                iu.v2(bx, by), iu.v2(bx + icon, by + icon))
            if hover:
                draw_list.add_rect_filled(
                    iu.v2(bx - pad, by - pad), iu.v2(bx + icon + pad, by + icon + pad),
                    iu.u32(255, 255, 255, 46), round(5 * s))
            texture = self._toolbar_textures.get(key)
            if texture is not None:
                tint = _TOOLBAR_TINT_ON if (key == "autoplay" and autoplay_on) else _TOOLBAR_TINT
                ref = imgui.ImTextureRef(int(texture.texture_id()))
                draw_list.add_image(ref, iu.v2(bx, by), iu.v2(bx + icon, by + icon),
                                    iu.v2(0.0, 0.0), iu.v2(1.0, 1.0), iu.u32(*tint))
            if hover:
                self._toolbar_hovered = True
                if imgui.is_mouse_clicked(imgui.MouseButton_.left):
                    handler = getattr(ctx, _TOOLBAR_HANDLERS.get(key, ""), None)
                    if callable(handler):
                        handler()


__all__ = ["_TextDisplayBase", "_dim", "_DEFAULT_LABEL_COLOR",
           "_DEFAULT_TEXT_COLOR", "_HIGHLIGHT_COLOR", "_TOOLBAR_MARGIN",
           "_TOOLBAR_TINT", "_TOOLBAR_TINT_ON"]
