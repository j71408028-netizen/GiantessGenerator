"""小游戏运行时：会话窗口内的全屏覆盖层舞台（imgui 立即模式）。

进入方式：``mini_game`` 触发器按 manifest 后端分派——``py`` 后端走这里，
把游戏渲染成**叠在副本界面上的覆盖层**；``web`` 后端仍走宿主端口
``launch_mini_game()`` 的子进程 pywebview（兼容通道）。

画布结构（P6 起）：舞台是 ``_draw_frame`` **最后**绘制的一个顶层窗口，铺满客户区
（半透明压暗底），窗口内用 ``imgui.get_window_draw_list()`` 逐帧画游戏画面——
游戏坐标 = 画面区域（play rect）局部坐标，绘制前把 draw list 的裁剪矩形压到画面
区域上，越界绘制因此不会溢出到副本界面（与 DPG 版「canvas child 会裁剪自身
drawlist」等价，见 ``.workbuddy/imgui-migration/50-analysis-canvas-as-component.md``
§4：画布画在根窗口里会被工具条盖住，所以舞台必须是**帧末的顶层窗口**）。

驱动：不再挂帧时钟——舞台由窗口在帧末直接调用 :meth:`_MiniGameStage.render`
（``ui._draw_frame`` → ``_draw_mini_game_stage``），每帧 压暗底 → ``game.update(dt)``
→ 边框/HUD →（已结算则）收尾。输入是**轮询式**（``imgui.is_key_down`` /
``io.mouse_pos`` / ``imgui.is_mouse_down``，imgui 下这些状态每帧都新鲜，不像 DPG
手动渲染那样恒 False，因此不再需要 handler 维护状态表），与会话按键（空格 /
回车 / H / ESC / A / F2）零冲突：

- 步进挂起：舞台存续期间 :meth:`OverlayHandler.overlay_open` 返回
  ``"minigame"``（阅读模态），``_on_next_step`` 与自动播放自动停摆；
- 模态遮蔽：舞台窗口铺满客户区且没有 ``no_bring_to_front_on_focus``，帧末最后
  ``begin`` 因而压在工具条之上，鼠标也落在舞台上（工具条不会被误点）；
- ESC：会话级 ESC 回调转交 :meth:`MiniGameStageHandler._minigame_escape`
  → 中止（无结果，副本照常继续）；
- 游戏自身 ``api.finish()``：结算 → 销毁舞台 → 结果经与 web 后端同一条
  ``_on_mini_game_result`` 管线记入返回值与回放（小游戏不区分胜负，
  分支由其他触发器按返回值判定）。

离屏画布（:class:`_CanvasTexture`）：**宿主自持的一张 GL 纹理** + 按脏矩形
``glTexSubImage2D`` 增量上传（见 ``dungeon/window/gl_texture.py`` 与
``.workbuddy/imgui-migration/52-analysis-gpu-texture-native.md`` §6）。DPG 时代
的「切块 + 1 px 出血」整个删掉了：那是为绕开 ``set_value`` 的 10 ms/次开销，
而整张 1088×768 的 ``glTexSubImage2D`` 实测只要 0.294 ms，一张纹理既没有块
边界的采样缝，也不再需要逐块的重传策略。

线程约束：全部方法只在帧循环线程调用（触发器侧经 ``_frame.call`` 并入）。
"""

import os
import time
import weakref as _weakref

from imgui_bundle import hello_imgui, imgui

from dungeon import process_log
from dungeon.window import gl_texture as _gl
from dungeon.window import gpu_textures
from dungeon.window import imgui_util as _iu
from dungeon.window.ui import _panel_flags
from .base import GameAPI, clamp_dt

try:
    import numpy as _np
except ImportError:          # 无 numpy 时离屏画布上传退化为「不上传」（见 _ensure）
    _np = None

#: 舞台顶层窗口 id（``##`` 前缀不进 ini / 不与标题冲突）
_STAGE_WINDOW = "##dungeon_minigame"
_BACKDROP_COLOR = (8, 10, 18, 225)
_HUD_COLOR = (240, 240, 245, 255)
_HUD_SIZE = 20
_MAX_TEXTURE_EDGE = 2048
#: 游戏画面相对窗口边缘的留白（仅声明了 aspect 的游戏生效，随 DPI 缩放）
_PLAY_MARGIN = 64
_PLAY_FRAME_COLOR = (255, 255, 255, 30)
#: 一块画布一次累积的脏矩形上限：超过就直接标「整张脏」（再攒没有意义）
_DIRTY_BOX_LIMIT = 64

#: 键名 → imgui 键码；在首次使用时惰性构建
_KEY_MAP = None


def _stage_flags():
    """舞台窗口的标志：``ui._panel_flags()`` + 关掉滚动条。

    与浮层同一条规矩——**不带** ``no_bring_to_front_on_focus``，否则会被铺满
    客户区的根窗口盖住（P1 的入口面板就是这么"自检全绿、屏幕上看不见"的）。
    舞台在帧末最后 ``begin``，于是压在工具条 / 选项弹窗之上。
    """
    return (_panel_flags()
            | imgui.WindowFlags_.no_scrollbar.value
            | imgui.WindowFlags_.no_scroll_with_mouse.value)


def _key_map():
    global _KEY_MAP
    if _KEY_MAP is None:
        key = imgui.Key
        # imgui 的键常量名：字母 / 数字是小写单字符，命名键用 *_arrow / *_shift…
        _KEY_MAP = {
            "space": key.space, "spacebar": key.space,
            "enter": key.enter, "return": key.enter,
            "keypad_enter": key.keypad_enter, "escape": key.escape,
            "up": key.up_arrow, "down": key.down_arrow,
            "left": key.left_arrow, "right": key.right_arrow,
            "lshift": key.left_shift, "rshift": key.right_shift,
            "lcontrol": key.left_ctrl, "rcontrol": key.right_ctrl,
            "shift": key.left_shift, "ctrl": key.left_ctrl,
            "tab": key.tab,
        }
    return _KEY_MAP


def _resolve_key(key):
    """键名 → imgui 键码；未知键返回 None（调用方视为未按下）。"""
    key = str(key or "").lower()
    named = _key_map().get(key)
    if named is not None:
        return named
    if len(key) == 1:
        if key.isalpha():
            return getattr(imgui.Key, key, None)
        if key.isdigit():
            return getattr(imgui.Key, "_" + key, None)
    return None


def _pack(color):
    """``(r, g, b[, a])``（0~255）→ imgui 打包色。"""
    r, g, b = color[0], color[1], color[2]
    a = color[3] if len(color) > 3 else 255
    return _iu.u32(r, g, b, a)


def _draw_text_run(dl, text, pos, color, size, font, dpi):
    """往 draw list 画一行文本（字号 ``size`` 按 imgui 单位 / 物理像素）。

    ``push_font(font, base)`` 实际得到 ``base × dpi``（见 ``ui`` 模块文档），
    因此要渲染到 ``size`` 物理像素就得传 ``size / dpi``。字体缺失时退回当前字体。
    """
    pushed = False
    if font is not None:
        imgui.push_font(font, max(1.0, float(size) / max(0.5, float(dpi))))
        pushed = True
    try:
        dl.add_text(_iu.v2(pos[0], pos[1]), _pack(color), str(text))
    finally:
        if pushed:
            imgui.pop_font()


class _PILCanvas:
    """:class:`~.base.MiniCanvas` 的 PIL 实现（本模块私有，作者不 import）。

    半透明形状先画进包围盒临时层再 ``alpha_composite`` 回画布
    （ImageDraw 在 RGBA 画布上写半透明色会直接替换像素含 alpha，
    与 canvas 2D / imgui 的混合语义不一致，见 :meth:`_blend_shape`）。
    每次绘制 ``_version`` 自增，同时把包围盒记进 ``_dirty``：运行时据此
    只重传内容变化的纹理矩形（见 :class:`_CanvasTexture`）；``_dirty_all``
    表示「整张都脏」（脏矩形攒太多、或首帧），此时按整张处理。
    """

    _UID = 0

    def __init__(self, width, height, bg=None, root=None, font_family=""):
        from PIL import Image, ImageDraw
        _PILCanvas._UID += 1
        self._uid = _PILCanvas._UID     # 纹理缓存键（id() 会被对象复用，不可靠）
        self._size = (max(1, int(width)), max(1, int(height)))
        self._img = Image.new("RGBA", self._size, tuple(bg) if bg else (0, 0, 0, 0))
        self._draw = ImageDraw.Draw(self._img, "RGBA")
        self._root = root            # 相对路径基准（小游戏包目录）
        self._font_family = font_family or ""   # 与会话同源的字体家族（可空）
        self._version = 0
        # 脏区域（画布坐标，半开区间）与「整张脏」标记；供运行时增量上传
        self._dirty = []
        self._dirty_all = False

    def _bump(self, bbox):
        """记录一次内容变化：``_version`` 自增 + 累积脏矩形（半开区间 ``bbox``）。

        ``bbox`` 必须是**覆盖本次绘制像素的闭包**：宁可多报（多传一块）也不能
        漏报，漏报就是画面残留旧像素。传 ``None`` 表示范围不可知 → 整张脏。
        """
        self._version += 1
        if self._dirty_all or bbox is None:
            self._dirty_all = True
            return
        w, h = self._size
        # int() 对负数是向零截断；上下界都会夹到 [0, w] / [0, h]，仍是安全超集
        x0 = max(0, min(w, int(bbox[0])))
        y0 = max(0, min(h, int(bbox[1])))
        x1 = max(0, min(w, int(bbox[2]) + 1))
        y1 = max(0, min(h, int(bbox[3]) + 1))
        if x1 <= x0 or y1 <= y0:
            return                       # 完全落在画布外
        self._dirty.append((x0, y0, x1, y1))
        if len(self._dirty) >= _DIRTY_BOX_LIMIT:
            # 攒够上限还没被消费（往往是一次关卡预渲染）：整张传一次更划算
            self._dirty_all = True
            self._dirty.clear()

    def _alpha(self, color):
        return color[3] if len(color) > 3 else 255

    def _blend_shape(self, bbox, render):
        """把 alpha<255 的形状混合进画布（render 在临时层局部坐标里作画）。"""
        from PIL import Image, ImageDraw
        x0, y0, x1, y1 = bbox
        cx0, cy0 = max(0, x0), max(0, y0)
        cx1, cy1 = min(self._size[0] - 1, x1), min(self._size[1] - 1, y1)
        if cx1 < cx0 or cy1 < cy0:
            return
        tmp = Image.new("RGBA", (cx1 - cx0 + 1, cy1 - cy0 + 1), (0, 0, 0, 0))
        render(ImageDraw.Draw(tmp), -cx0, -cy0)
        self._img.alpha_composite(tmp, (cx0, cy0))

    @property
    def width(self):
        return self._size[0]

    @property
    def height(self):
        return self._size[1]

    def _resolve_image(self, source):
        """draw_image 的源：路径（相对包目录）或另一块画布 → PIL 图。"""
        if isinstance(source, _PILCanvas):
            return source._img
        path = str(source or "")
        if path and not os.path.isabs(path) and self._root:
            path = os.path.join(self._root, path)
        if not path or not os.path.isfile(path):
            return None
        try:
            from PIL import Image
            with Image.open(path) as img:
                return img.convert("RGBA")
        except Exception:
            return None

    def draw_rect(self, pmin, pmax, color, fill=True, thickness=1.0):
        x0, y0 = round(pmin[0]), round(pmin[1])
        x1, y1 = round(pmax[0]) - 1, round(pmax[1]) - 1
        pad = max(1, round(thickness))
        self._bump((x0 - pad, y0 - pad, x1 + pad, y1 + pad))
        if self._alpha(color) < 255:
            def render(d, ox, oy):
                if fill:
                    d.rectangle([x0 + ox, y0 + oy, x1 + ox, y1 + oy],
                                fill=tuple(color))
                if (not fill) or thickness > 1:
                    d.rectangle([x0 + ox, y0 + oy, x1 + ox, y1 + oy],
                                outline=tuple(color),
                                width=max(1, round(thickness)))
            self._blend_shape((x0, y0, x1, y1), render)
            return
        if fill:
            self._draw.rectangle([x0, y0, x1, y1], fill=tuple(color))
        if (not fill) or thickness > 1:
            self._draw.rectangle([x0, y0, x1, y1], outline=tuple(color),
                                 width=max(1, round(thickness)))

    def draw_round_rect(self, pmin, pmax, radius, color, fill=True, thickness=1.0):
        x0, y0 = round(pmin[0]), round(pmin[1])
        x1, y1 = round(pmax[0]) - 1, round(pmax[1]) - 1
        pad = max(1, round(thickness))
        self._bump((x0 - pad, y0 - pad, x1 + pad, y1 + pad))
        if fill:
            self._draw.rounded_rectangle([x0, y0, x1, y1], radius=max(0, round(radius)),
                                         fill=tuple(color))
        if not fill or thickness > 1:
            self._draw.rounded_rectangle([x0, y0, x1, y1], radius=max(0, round(radius)),
                                         outline=tuple(color),
                                         width=max(1, round(thickness)))

    def draw_circle(self, center, radius, color, fill=True, thickness=1.0):
        cx, cy, r = center[0], center[1], radius
        bbox = [round(cx - r), round(cy - r), round(cx + r) - 1, round(cy + r) - 1]
        pad = max(1, round(thickness))
        self._bump((bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad))
        if self._alpha(color) < 255:
            def render(d, ox, oy):
                box = [bbox[0] + ox, bbox[1] + oy, bbox[2] + ox, bbox[3] + oy]
                if fill:
                    d.ellipse(box, fill=tuple(color))
                if (not fill) or thickness > 1:
                    d.ellipse(box, outline=tuple(color),
                              width=max(1, round(thickness)))
            self._blend_shape((bbox[0], bbox[1], bbox[2], bbox[3]), render)
            return
        if fill:
            self._draw.ellipse(bbox, fill=tuple(color))
        if (not fill) or thickness > 1:
            self._draw.ellipse(bbox, outline=tuple(color),
                               width=max(1, round(thickness)))

    def draw_line(self, p1, p2, color, thickness=1.0):
        x0, y0 = round(p1[0]), round(p1[1])
        x1, y1 = round(p2[0]), round(p2[1])
        width = max(1, round(thickness))
        self._bump((min(x0, x1) - width, min(y0, y1) - width,
                    max(x0, x1) + width, max(y0, y1) + width))
        if self._alpha(color) < 255:
            pad = width + 1

            def render(d, ox, oy):
                d.line([x0 + ox, y0 + oy, x1 + ox, y1 + oy],
                       fill=tuple(color), width=width)
            self._blend_shape((min(x0, x1) - pad, min(y0, y1) - pad,
                               max(x0, x1) + pad, max(y0, y1) + pad), render)
            return
        self._draw.line([x0, y0, x1, y1], fill=tuple(color), width=width)

    def draw_text(self, text, pos, color, size=18):
        font = _pil_font(size, self._font_family)
        # 不量字形（要读字体度量，代价与调用频次成正比）：按「每字符一个字宽 +
        # 上下各一个字高」给足上界。宁可多传一块，也不能漏掉笔画。
        size = max(1, int(size))
        self._bump((pos[0] - size, pos[1] - size,
                    pos[0] + (len(str(text)) + 1) * size, pos[1] + size * 2))
        self._draw.text((pos[0], pos[1]), str(text), font=font, fill=tuple(color))

    def draw_image(self, source, pmin, pmax, uv=None):
        img = self._resolve_image(source)
        if img is None:
            return
        if uv:
            u0, v0, u1, v1 = uv
            box = (round(u0 * img.width), round(v0 * img.height),
                   round(u1 * img.width), round(v1 * img.height))
            img = img.crop(box)
        w = max(1, round(pmax[0] - pmin[0]))
        h = max(1, round(pmax[1] - pmin[1]))
        if (img.width, img.height) != (w, h):
            img = img.resize((w, h))
        dx, dy = round(pmin[0]), round(pmin[1])
        self._bump((dx, dy, dx + w, dy + h))
        self._img.paste(img, (dx, dy), img)


_PIL_FONTS = {}


def _pil_font(size, family=""):
    """按 (字号, 家族) 缓存 PIL 字体：与会话字体走同一条候选链。

    PIL 能指定集合（.ttc）内的 face 序号，所以用 :func:`resolve_font` 拿到的
    序号就用（Noto CJK 因此是简中 face，而不是只能取到的第 0 个 JP face）；
    链上一个文件都没有时回退内置位图字体（没有中文字形）。
    """
    key = (max(6, round(size)), str(family or ""))
    font = _PIL_FONTS.get(key)
    if font is not None:
        return font
    from PIL import ImageFont
    from dungeon.window.fonts import resolve_font
    font = None
    for bold in (True, False):
        entry = resolve_font(family, bold=bold)
        if not entry:
            continue
        path, index = entry
        try:
            font = ImageFont.truetype(path, key[0], index=index)
            break
        except Exception as exc:
            process_log.log(f"[MiniGame] 字体加载失败 {path}: {exc}")
    if font is None:
        font = ImageFont.load_default()
    _PIL_FONTS[key] = font
    return font


class _StageGameAPI(GameAPI):
    """:class:`~.base.GameAPI` 的运行时实现：薄包装，持有舞台引用。

    契约脚本（check_minigame.py）禁止游戏触碰 ``api._``——私有面随时重构。

    游戏坐标系 = 画面区域（play rect）局部坐标：``width`` / ``height`` 返回
    画面区域尺寸，绘制指令平移进位于该区域的屏幕矩形并压在 play rect 裁剪矩形
    之内（声明了 ``aspect`` 的游戏区域不铺满窗口，其余部分露出压暗的副本界面；
    越界绘制不外溢）。
    """

    def __init__(self, stage):
        self._stage = stage

    # ---- 几何 ----
    @property
    def _origin(self):
        rect = self._stage._play_rect
        return rect[0], rect[1]

    def _pt(self, x, y):
        # 游戏坐标 → 客户区坐标（舞台窗口在 (0,0)，窗口局部即客户区）
        return self._stage._to_screen(x, y)

    @property
    def width(self):
        return self._stage._play_rect[2]

    @property
    def height(self):
        return self._stage._play_rect[3]

    # ---- 绘制（全部画进舞台窗口的 draw list；裁剪在 play rect 内） ----
    # 注意与 PIL 画布的 fill 语义差别：这里 fill 是布尔，颜色是 (r,g,b,a)。
    def draw_rect(self, pmin, pmax, color, fill=True, thickness=1.0):
        dl = self._stage._draw_list
        if dl is None:
            return
        a, b = _iu.v2(*self._pt(*pmin)), _iu.v2(*self._pt(*pmax))
        col = _pack(color)
        if fill:
            dl.add_rect_filled(a, b, col)
        if (not fill) or thickness > 1:
            dl.add_rect(a, b, col, 0.0, max(1.0, float(thickness)))

    def draw_round_rect(self, pmin, pmax, radius, color, fill=True, thickness=1.0):
        dl = self._stage._draw_list
        if dl is None:
            return
        a, b = _iu.v2(*self._pt(*pmin)), _iu.v2(*self._pt(*pmax))
        col = _pack(color)
        rounding = max(0.0, float(radius))
        if fill:
            dl.add_rect_filled(a, b, col, rounding)
        if (not fill) or thickness > 1:
            dl.add_rect(a, b, col, rounding, max(1.0, float(thickness)))

    def draw_circle(self, center, radius, color, fill=True, segments=0,
                    thickness=1.0):
        dl = self._stage._draw_list
        if dl is None:
            return
        cen = _iu.v2(*self._pt(*center))
        col = _pack(color)
        seg = max(0, int(segments))
        if fill:
            dl.add_circle_filled(cen, float(radius), col, seg)
        if (not fill) or thickness > 1:
            dl.add_circle(cen, float(radius), col, seg,
                          max(1.0, float(thickness)))

    def draw_line(self, p1, p2, color, thickness=1.0):
        dl = self._stage._draw_list
        if dl is None:
            return
        dl.add_line(_iu.v2(*self._pt(*p1)), _iu.v2(*self._pt(*p2)),
                    _pack(color), max(1.0, float(thickness)))

    def draw_polygon(self, points, color):
        dl = self._stage._draw_list
        if dl is None or not points:
            return
        pts = [_iu.v2(*self._pt(*point)) for point in points]
        dl.add_concave_poly_filled(pts, _pack(color))

    def draw_text(self, text, pos, color, size=18):
        dl = self._stage._draw_list
        if dl is None:
            return
        _draw_text_run(dl, text, self._pt(*pos), color, size,
                       self._stage._text_font(), self._stage._dpi)

    def draw_image(self, source, pmin, pmax, uv=None):
        dl = self._stage._draw_list
        if dl is None:
            return
        if isinstance(source, _PILCanvas):
            self._stage._draw_canvas(source, pmin, pmax, uv)
            return
        texture = self._stage._ensure_texture(source)
        if texture is None:
            return
        ref = imgui.ImTextureRef(int(texture.texture_id()))
        uv_min = _iu.v2(uv[0], uv[1]) if uv else None
        uv_max = _iu.v2(uv[2], uv[3]) if uv else None
        dl.add_image(ref, _iu.v2(*self._pt(*pmin)), _iu.v2(*self._pt(*pmax)),
                     uv_min, uv_max)

    def offscreen(self, width, height, bg=None):
        return _PILCanvas(width, height, bg=bg, root=self._stage._root,
                          font_family=self._stage.font_family)

    # ---- 输入（轮询式：imgui 的键鼠状态每帧新鲜，不需要状态表） ----
    def key_down(self, key):
        code = _resolve_key(key)
        if code is None:
            return False
        try:
            return bool(imgui.is_key_down(code))
        except Exception:
            return False

    def mouse_pos(self):
        try:
            io = imgui.get_io()
            ox, oy = self._origin
            return (float(io.mouse_pos.x) - ox, float(io.mouse_pos.y) - oy)
        except Exception:
            return (0.0, 0.0)

    def mouse_down(self, button=0):
        buttons = (imgui.MouseButton_.left, imgui.MouseButton_.right,
                   imgui.MouseButton_.middle)
        if not 0 <= int(button) < len(buttons):
            return False
        try:
            return bool(imgui.is_mouse_down(buttons[int(button)]))
        except Exception:
            return False

    # ---- 服务 ----
    def finish(self, result=None):
        self._stage._finish(result)

    def hud(self, text):
        self._stage._hud = str(text or "")

    def notify(self, message):
        try:
            self._stage._win._notify(message)
        except Exception:
            pass

    def read_state(self, key):
        win = self._stage._win
        key = str(key or "")
        if key == "章节":
            return getattr(win, "current_chapter", None)
        if key == "身高":
            return getattr(win, "height", None)
        state = getattr(win, "dungeon_state", None)
        if state is None:
            return None
        values = {"介入度": state.intrusion, "破坏性": state.destruction,
                  "总伤亡": state.total_casualties, "总步数": state.total_steps}
        return values.get(key)


class _CanvasTexture:
    """一块离屏画布的**单张宿主 GL 纹理**：按需创建、只重传脏矩形。

    为什么是「一张」而不是 DPG 时代的分块纹理集：``glTexSubImage2D`` 更新整张
    1088×768 实测只要 0.294 ms（``.workbuddy/p6_probe/probe_gltex.py``），而
    分块原本是为了绕开 ``set_value`` 的 10 ms 开销。一张纹理既没有块边界采样缝，
    也不必维护「脏块 + 出血 + uv 内缩」那套几何，代码量与出错面都小一个量级。

    生命周期：由 :class:`_MiniGameStage` 持有，舞台销毁时删纹理。持有画布
    **弱引用**：小游戏换关常常整块换掉离屏画布（``self._terrain = ...``），
    旧画布随即无人引用——弱引用一断，纹理就能被识别为孤儿并回收，不必等整个
    小游戏结束（否则一局 30 关会攒三十份大纹理）。
    """

    def __init__(self, canvas):
        self._canvas_ref = _weakref.ref(canvas)
        self._size = (canvas.width, canvas.height)
        self._tex = None            # GL 纹理名；None = 尚未创建
        self._stale = []            # 内容已变、尚未重传的矩形（半开区间）
        self._stale_all = True      # 首帧 / 攒够脏矩形 → 整张重传一次

    @property
    def canvas(self):
        """当前画布；已被回收时返回 None（该纹理随即由舞台清掉）。"""
        return self._canvas_ref()

    @property
    def texture(self):
        """GL 纹理名（未创建 / 不可用时为 None）。"""
        return self._tex

    def _harvest(self):
        """把画布累积的脏矩形收进待传队列（同帧多次绘制只折算一次）。"""
        canvas = self.canvas
        if canvas is None:
            return
        if canvas._dirty_all:
            canvas._dirty_all = False
            canvas._dirty.clear()
            self._stale_all = True
            self._stale = []
            return
        if not canvas._dirty:
            return
        self._stale.extend(canvas._dirty)
        canvas._dirty = []

    def _ensure(self):
        """取纹理（首次创建并整张上传，之后按脏矩形增量上传）；失败返回 None。"""
        if _np is None or not _gl.available():
            return None
        canvas = self.canvas
        if canvas is None:
            return None
        w, h = self._size
        if self._tex is None:
            try:
                # ravel 不需要：PIL 的 RGBA 缓冲是 (h, w, 4) 连续的 uint8，
                # 行 0 = 图像上边 = 纹理 v=0 = 绘制矩形上边（imgui uv (0,0) 在 p_min）
                arr = _np.asarray(canvas._img, dtype=_np.uint8)
            except Exception as exc:
                process_log.log(f"[MiniGame] 离屏画布读取失败: {exc}")
                return None
            self._tex = _gl.create_texture(w, h, arr)
            if self._tex is None:
                return None
            self._stale = []
            self._stale_all = False
            return self._tex
        try:
            arr = _np.asarray(canvas._img, dtype=_np.uint8)
        except Exception as exc:
            process_log.log(f"[MiniGame] 离屏画布读取失败: {exc}")
            return None
        if self._stale_all:
            if _gl.upload(self._tex, 0, 0, w, h, arr):
                self._stale_all = False
                self._stale = []
            return self._tex
        if not self._stale:
            return self._tex
        pending, self._stale = self._stale, []
        for x0, y0, x1, y1 in pending:
            x0 = max(0, min(w, int(x0)))
            y0 = max(0, min(h, int(y0)))
            x1 = max(0, min(w, int(x1)))
            y1 = max(0, min(h, int(y1)))
            if x1 <= x0 or y1 <= y0:
                continue
            # glTexSubImage2D 要求连续数据：PIL 缓冲切片必须 ascontiguousarray
            sub = _np.ascontiguousarray(arr[y0:y1, x0:x1])
            if not _gl.upload(self._tex, x0, y0, x1 - x0, y1 - y0, sub):
                self._stale.append((x0, y0, x1, y1))    # 失败留待下帧
        return self._tex

    def draw(self, dl, pmin, pmax, uv=None):
        """把画布（``uv`` 指定的归一化子区域）画进 ``dl``；``pmin``/``pmax`` 是屏幕坐标。"""
        if dl is None:
            return
        self._harvest()
        texture = self._ensure()
        if texture is None:         # 画布已被回收 / 无 GL：静默跳过
            return
        u0, v0, u1, v1 = uv if uv else (0.0, 0.0, 1.0, 1.0)
        dl.add_image(imgui.ImTextureRef(int(texture)),
                     _iu.v2(pmin[0], pmin[1]), _iu.v2(pmax[0], pmax[1]),
                     _iu.v2(u0, v0), _iu.v2(u1, v1))

    def destroy(self, delete_gl=True):
        """删掉 GL 纹理（``delete_gl`` 为假时只丢句柄——GL 上下文已随窗口消失）。"""
        if delete_gl and self._tex is not None:
            _gl.delete_texture(self._tex)
        self._tex = None
        self._stale = []
        self._stale_all = False


class _MiniGameStage:
    """一次小游戏会话的舞台：帧内绘制、纹理缓存与结算。"""

    def __init__(self, window, game_cls, config, on_result, root=None):
        self._win = window
        self._game_cls = game_cls
        self._config = dict(config or {})
        self._root = root
        self._on_result = on_result
        self._hud = ""
        self._finished = False
        self._result = None        # None = 未结算 / 中止
        self._last_t = 0.0
        self._size = [0, 0]
        self._textures = {}        # 绝对路径 → hello_imgui.TextureGpu
        self._canvas_texes = {}    # 画布 _uid → _CanvasTexture（单张 GL 纹理）
        self._play_rect = [0, 0, 0, 0]   # 游戏画面区域（窗口内居中）
        self._draw_list = None     # 本帧的 draw list（游戏绘制入口；帧外为 None）
        self._game = None
        self._api = _StageGameAPI(self)

    @property
    def font_family(self):
        """会话窗口的字体家族名（离屏画布与文本用同一份设置，可空）。"""
        return getattr(self._win, "dungeon_font", "") or ""

    @property
    def _dpi(self) -> float:
        return max(0.5, float(getattr(self._win, "_dpi_scale", 1.0) or 1.0))

    def _text_font(self):
        """draw list 文本用的字体（会话正文档字体，可能是 None）。"""
        return getattr(self._win, "dungeon_text_font", None)

    def _to_screen(self, x, y):
        """游戏坐标（play rect 局部）→ 客户区坐标。"""
        rect = self._play_rect
        return (float(rect[0]) + float(x), float(rect[1]) + float(y))

    def _gl_ops_ok(self) -> bool:
        """收尾期是否还能碰 GL：帧时钟仍在跑 = 仍在帧线程且上下文当前。

        ``_finish_session`` 在 ``_frame.stop()`` 之后才 ``_destroy_overlays()``
        （此时 ``hello_imgui.run()`` 已返回、上下文已释放），因此那里不能删纹理。
        """
        return bool(getattr(getattr(self._win, "_frame", None), "running", False))

    # ---------------- 生命周期 ----------------
    def _compute_play_rect(self, win_w, win_h):
        """按游戏声明的长宽比计算画面区域：等比缩放进「窗口 - 边缘留白」
        内并居中；未声明 aspect 的游戏沿用旧行为（铺满窗口）。游戏绘制统一
        裁剪进该区域（见 :meth:`render` 的 push_clip_rect）。"""
        game = self._game
        aspect = getattr(game, "aspect", None) if game is not None else None
        try:
            aspect = float(aspect) if aspect else 0.0
        except (TypeError, ValueError):
            aspect = 0.0
        if aspect <= 0:
            return [0, 0, win_w, win_h]
        margin = round(_PLAY_MARGIN * (getattr(self._win, "_dpi_scale", 1.0) or 1.0))
        avail_w = max(1, win_w - 2 * margin)
        avail_h = max(1, win_h - 2 * margin)
        if avail_w / avail_h > aspect:
            h = avail_h
            w = round(h * aspect)
        else:
            w = avail_w
            h = round(w / aspect)
        return [(win_w - w) // 2, (win_h - h) // 2, w, h]

    def build(self) -> bool:
        """进入游戏（不建任何 GPU 资源 / draw list）。失败返回 False。"""
        try:
            game = self._game_cls()
            game.api = self._api
            game.setup(self._config)
        except Exception as exc:
            process_log.log(f"[MiniGame] 游戏「{getattr(self._game_cls, 'id', '?')}」"
                            f"初始化失败: {exc}")
            return False
        self._game = game
        # 客户区尺寸取窗口每帧维护的布局尺寸（build 总发生在帧内，值是最新的）。
        # 不在 build 里碰 imgui：触发器可能与 draw list 无关的时机构建舞台。
        w = int(getattr(self._win, "_layout_w", 0) or 0)
        h = int(getattr(self._win, "_layout_h", 0) or 0)
        self._size = [w, h]
        self._play_rect = self._compute_play_rect(w, h)
        self._last_t = time.monotonic()
        process_log.log(f"[MiniGame] 小游戏「{game.id}」开始（imgui 覆盖层舞台）")
        return True

    def render(self, w: float, h: float):
        """帧末绘制（``ui._draw_frame`` → ``_draw_mini_game_stage``）。

        顺序：压暗底（窗口背景）→ 裁剪到 play rect → ``game.update(dt)`` →
        边框（画面区域不满窗时）/ HUD → 已结算则收尾。异常按「无结果」收场，
        不拖垮副本会话。
        """
        if self._finished:
            # 结算沿帧任务到达（``api.finish`` 可在 update 之外调用），这里兜底销毁
            self._win._destroy_mini_game_stage()
            return
        if getattr(self._win, "_closing", False):
            return
        if abs(w - self._size[0]) > 0.5 or abs(h - self._size[1]) > 0.5:
            self._size = [w, h]
            self._play_rect = self._compute_play_rect(w, h)
        x0, y0, pw, ph = self._play_rect

        imgui.set_next_window_pos(_iu.v2(0.0, 0.0))
        imgui.set_next_window_size(_iu.v2(float(w), float(h)))
        imgui.push_style_var(imgui.StyleVar_.window_padding, _iu.v2(0.0, 0.0))
        imgui.push_style_var(imgui.StyleVar_.window_border_size, 0.0)
        imgui.push_style_var(imgui.StyleVar_.window_rounding, 0.0)
        imgui.push_style_color(imgui.Col_.window_bg, _iu.u32(*_BACKDROP_COLOR))
        imgui.begin(_STAGE_WINDOW, None, _stage_flags())

        dl = imgui.get_window_draw_list()
        self._draw_list = dl
        dl.push_clip_rect(_iu.v2(float(x0), float(y0)),
                          _iu.v2(float(x0 + pw), float(y0 + ph)), True)
        now = time.monotonic()
        dt = clamp_dt(now - self._last_t)
        self._last_t = now
        try:
            self._game.update(dt)
        except Exception as exc:
            process_log.log(f"[MiniGame] 游戏「{self._game.id}」帧异常，"
                            f"按无结果收场: {exc}")
            self._abort()
        self._draw_overlay(dl, w, h)
        dl.pop_clip_rect()
        self._draw_list = None
        imgui.end()
        imgui.pop_style_color()
        imgui.pop_style_var(3)
        if self._finished:
            self._win._destroy_mini_game_stage()

    def _draw_overlay(self, dl, w, h):
        """覆盖层（画布局部坐标，盖在游戏内容上）：画面区域不满窗时画一圈细边框
        与压暗的副本界面分开；HUD 固定在画面区域左上角。"""
        pw, ph = self._play_rect[2], self._play_rect[3]
        if [pw, ph] != [w, h]:
            dl.add_rect(_iu.v2(0.0, 0.0), _iu.v2(float(pw), float(ph)),
                        _pack(_PLAY_FRAME_COLOR), 0.0, 2.0)
        if self._hud:
            dpi = self._dpi
            pos = (round(16 * dpi), round(12 * dpi))
            _draw_text_run(dl, self._hud, pos, _HUD_COLOR, _HUD_SIZE,
                           self._text_font(), dpi)

    def drain_gpu_textures(self):
        """交出静态贴图句柄（窗口在 ``before_exit``、GL 仍存活时统一释放）。

        见 :mod:`dungeon.window.gpu_textures`：收尾期丢 ``TextureGpu`` 引用会段
        错误，所以句柄一律提前交出去。离屏画布那张纹理是宿主自持的 GL 名字
        （``gl_texture``），不走这里，仍由 :meth:`destroy` 按 ``_gl_ops_ok`` 处置。
        """
        handles = list(self._textures.values())
        self._textures.clear()
        return handles

    def destroy(self):
        """销毁 GPU 资源并回传结果（会话收尾路径帧时钟已停，回传自然失效）。"""
        gl_ok = self._gl_ops_ok()
        if self._game is not None:
            try:
                self._game.teardown()
            except Exception as exc:
                process_log.log(f"[MiniGame] 游戏「{self._game.id}」收尾异常: {exc}")
            self._game = None
        # 静态贴图是 hello_imgui.TextureGpu（RAII）：GL 还在时丢引用即释放；
        # 收尾期（窗口在游戏进行中被关掉，走 _destroy_overlays）上下文已释放，
        # 丢引用会段错误，只能退役。正常路径已由 drain_gpu_textures 收走，
        # 这里拿到的是空表。
        handles = list(self._textures.values())
        self._textures.clear()
        if gl_ok:
            gpu_textures.release(handles)
        else:
            gpu_textures.retire(handles)
        for texture in self._canvas_texes.values():
            texture.destroy(gl_ok)
        self._canvas_texes.clear()
        on_result = self._on_result
        self._on_result = None
        if on_result is not None:
            # 结果必回传一次：正常结算给结果字典；中止 / 异常给 None
            # （与 web 后端「用户关窗 = 无结果」同一语义，接收方据此清挂起态）
            on_result(self._result)

    # ---------------- 结算 ----------------
    def _finish(self, result):
        """游戏侧结算（api.finish）：记录结果，帧末统一销毁。

        小游戏不区分胜负：``result`` 为 dict 时原样回传（返回值取
        ``level`` / ``value`` 键），标量包装成 ``{"value": ...}``。"""
        if self._finished:
            return
        self._finished = True
        if isinstance(result, dict):
            self._result = dict(result)
        else:
            # 无参 / 标量：包装成返回值字典（中止路径走 _abort，_result 恒为 None）
            self._result = {"value": 1} if result is None else {"value": result}

    def _abort(self):
        """中止（ESC / 帧异常）：无结果收场，不记返回值。"""
        self._finished = True
        self._result = None

    # ---------------- 资源 ----------------
    def _ensure_texture(self, source):
        """文件路径源 → 静态 ``TextureGpu``（按绝对路径缓存）。

        离屏画布不走这里（见 :meth:`_draw_canvas`）：它要按脏矩形增量重传，
        必须由宿主自持 GL 纹理。失败返回 None（调用方静默跳过，不拖垮帧）。
        """
        if not source:
            return None
        path = str(source)
        if not os.path.isabs(path) and self._root:
            path = os.path.join(self._root, path)
        cached = self._textures.get(path)
        if cached is not None:
            return cached
        if not os.path.isfile(path):
            return None
        try:
            from PIL import Image
            with Image.open(path) as img:
                img = img.convert("RGBA")
                edge = max(img.size)
                if edge > _MAX_TEXTURE_EDGE:
                    scale = _MAX_TEXTURE_EDGE / edge
                    img = img.resize((max(1, round(img.width * scale)),
                                      max(1, round(img.height * scale))))
                rgba = _np.asarray(img, dtype=_np.uint8) if _np is not None else None
                if rgba is None:
                    return None
            texture = hello_imgui.create_texture_gpu_from_rgba_data(rgba)
        except Exception as exc:
            process_log.log(f"[MiniGame] 贴图加载失败 {path}: {exc}")
            return None
        self._textures[path] = texture
        return texture

    def _draw_canvas(self, canvas, pmin, pmax, uv=None):
        """把离屏画布 ``uv`` 指定的子区域画进 draw list（单张纹理增量上传）。

        顺手回收孤儿纹理：小游戏换关常整块换掉离屏画布，旧画布连同纹理一起作废
        （弱引用已断），不回收的话一局多关会一直往上叠纹理。
        """
        dl = self._draw_list
        if dl is None:
            return
        for uid in [u for u, t in self._canvas_texes.items() if t.canvas is None]:
            self._canvas_texes.pop(uid).destroy(self._gl_ops_ok())
        texture = self._canvas_texes.get(canvas._uid)
        if texture is None:
            texture = _CanvasTexture(canvas)
            self._canvas_texes[canvas._uid] = texture
        texture.draw(dl, self._to_screen(*pmin), self._to_screen(*pmax), uv)


class MiniGameStageHandler:
    """会话窗口 mixin：py 后端小游戏的打开 / 帧末绘制 / ESC 中止 / 销毁。"""

    #: 当前舞台（无小游戏时为 None；类属性默认值免去 init 次序问题）
    _mini_game_stage = None

    def _open_mini_game_stage(self, resolved, config, on_result) -> bool:
        """打开 py 后端小游戏舞台；已在进行中或初始化失败返回 False。"""
        if self._mini_game_stage is not None:
            process_log.log("[MiniGame] 已有小游戏进行中，忽略本次打开")
            return False
        try:
            game_cls = resolved.load()
        except ImportError as exc:
            process_log.log(f"[MiniGame] {exc}")
            return False
        # 贴图相对路径基准随包目录带给舞台
        stage = _MiniGameStage(self, game_cls, config, on_result, root=resolved.root)
        if not stage.build():
            return False
        self._mini_game_stage = stage
        return True

    def _draw_mini_game_stage(self, w: float, h: float):
        """帧末绘制 py 后端小游戏舞台（顶层窗口，压在工具条之上）。

        由 ``ui._draw_frame`` 在所有顶层浮层之后调用；无舞台时是空判。
        """
        stage = self._mini_game_stage
        if stage is not None:
            stage.render(w, h)

    def _minigame_active(self) -> bool:
        return self._mini_game_stage is not None

    def _minigame_escape(self) -> bool:
        """ESC：中止小游戏。返回 True 表示本次按键已被小游戏消费。"""
        stage = self._mini_game_stage
        if stage is None:
            return False
        process_log.log("[MiniGame] ESC 中止小游戏（无结果）")
        stage._abort()
        self._destroy_mini_game_stage()
        return True

    def _destroy_mini_game_stage(self):
        stage = self._mini_game_stage
        if stage is None:
            return
        self._mini_game_stage = None
        stage.destroy()
