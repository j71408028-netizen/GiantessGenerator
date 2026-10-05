"""小游戏运行时：会话窗口内的全屏覆盖层舞台（本框架唯一允许 DPG 的地方）。

进入方式：``mini_game`` 触发器按 manifest 后端分派——``py`` 后端走这里，
把游戏渲染成**叠在背景上的覆盖层**（不再是弹独立窗口）；``web`` 后端仍走
宿主端口 ``launch_mini_game()`` 的子进程 pywebview（兼容通道）。

画布结构：全窗 stage child（压暗底）内嵌一个位于画面区域的 canvas child，
游戏 drawlist 与覆盖层（边框/HUD）都在其中——child window 会把 drawlist
内容裁剪进自己的矩形（帧缓冲实测），游戏内容因此不会溢出到副本界面；
游戏坐标即画布局部坐标，``_pt`` 恒等映射。

驱动：挂窗口帧时钟（``FrameScheduler.every(EVERY_FRAME)``，key 唯一），
每帧 清屏 → ``game.update(dt)`` → HUD →（已结算则）收尾。输入是**轮询式**
（``dpg.is_key_down`` / 鼠标状态），不注册任何新 handler——与会话按键
（空格 / 回车 / H / ESC / A / F2）零冲突：

- 步进挂起：舞台存续期间 :meth:`OverlayHandler.overlay_open` 返回
  ``"minigame"``（阅读模态），``_on_next_step`` 与自动播放自动停摆；
- ESC：会话级 ESC 回调转交 :meth:`MiniGameStageHandler._minigame_escape`
  → 中止（无结果，副本照常继续）；
- 游戏自身 ``api.finish()``：结算 → 销毁舞台 → 结果经与 web 后端同一条
  ``_on_mini_game_result`` 管线记入返回值与回放（小游戏不区分胜负，
  分支由其他触发器按返回值判定）。

线程约束：全部方法只在帧循环线程调用（触发器侧经 ``_frame.call`` 并入）。
"""

import os
import time

import dearpygui.dearpygui as dpg

from dungeon import process_log
from .base import GameAPI, clamp_dt

try:
    import numpy as _np
except ImportError:          # 无 numpy 时退回纯 Python 转换（慢，仅兜底）
    _np = None

_STAGE_TAG = "minigame_stage"
_CANVAS_TAG = "minigame_canvas"
_DRAW_TAG = "minigame_stage_draw"
_OVERLAY_TAG = "minigame_stage_overlay"
_TICK_KEY = "minigame:tick"
_BACKDROP_COLOR = (8, 10, 18, 225)
_HUD_COLOR = (240, 240, 245, 255)
_HUD_SIZE = 20
_MAX_TEXTURE_EDGE = 2048
#: 游戏画面相对窗口边缘的留白（仅声明了 aspect 的游戏生效，随 DPI 缩放）
_PLAY_MARGIN = 64
_PLAY_FRAME_COLOR = (255, 255, 255, 30)
_NO_FILL = (0, 0, 0, -255)     # DPG 的「不填充」哨兵色（fill 参数期望颜色元组）

#: 键名 → DPG 键值（字母 / 数字按 VK 码 = ord）；在首次使用时惰性构建
_KEY_MAP = None


def _key_map():
    global _KEY_MAP
    if _KEY_MAP is None:
        # DPG 2.3.1 的键常量名：Spacebar / Return，Shift/Control 分左右键
        mapping = {}
        for name, const in (("spacebar", "mvKey_Spacebar"), ("return", "mvKey_Return"),
                            ("escape", "mvKey_Escape"), ("up", "mvKey_Up"),
                            ("down", "mvKey_Down"), ("left", "mvKey_Left"),
                            ("right", "mvKey_Right"), ("lshift", "mvKey_LShift"),
                            ("lcontrol", "mvKey_LControl"), ("tab", "mvKey_Tab")):
            mapping[name] = getattr(dpg, const, None)
        mapping["space"] = mapping.get("spacebar")
        mapping["enter"] = mapping.get("return")
        mapping["shift"] = mapping.get("lshift")
        mapping["ctrl"] = mapping.get("lcontrol")
        _KEY_MAP = mapping
    return _KEY_MAP


def _game_key_codes():
    """小游戏可用的全部键位码：命名键 + 字母 + 数字（handler 逐码注册）。

    注意：DPG 的字母 / 数字键常量**不是 ASCII 码**（mvKey_A=546 而非 65），
    必须经 mvKey_* 常量取值——用 ord() 注册会错位到别的键（实测按一个键
    触发一串无关 handler，状态表错乱）。
    """
    codes = [code for code in _key_map().values() if code is not None]
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
        code = getattr(dpg, f"mvKey_{ch}", None)
        if code is not None:
            codes.append(code)
    return codes


def _resolve_key(key):
    """键名 → DPG 键值；未知键返回 None（调用方视为未按下）。"""
    key = str(key or "").lower()
    named = _key_map().get(key)
    if named is not None:
        return named
    if len(key) == 1:
        upper = key.upper()
        if upper.isalpha() or upper.isdigit():
            return getattr(dpg, f"mvKey_{upper}", None)
    return None


def _image_to_texture_data(image):
    """PIL RGBA 图 → DPG 纹理数据（float32 平铺 0~1；numpy 快路径 + 兜底）。"""
    if _np is not None:
        arr = _np.frombuffer(image.tobytes(), dtype=_np.uint8).astype(_np.float32)
        arr /= 255.0
        return arr
    return [value / 255.0 for value in image.tobytes()]


class _PILCanvas:
    """:class:`~.base.MiniCanvas` 的 PIL 实现（本模块私有，作者不 import）。

    半透明形状先画进包围盒临时层再 ``alpha_composite`` 回画布
    （ImageDraw 在 RGBA 画布上写半透明色会直接替换像素含 alpha，
    与 canvas 2D / DPG 的混合语义不一致，见 :meth:`_blend_shape`）。
    每次绘制 ``_version`` 自增，供纹理上传按内容版本判定是否重传。
    """

    _UID = 0

    def __init__(self, width, height, bg=None, root=None):
        from PIL import Image, ImageDraw
        _PILCanvas._UID += 1
        self._uid = _PILCanvas._UID     # 纹理缓存键（id() 会被对象复用，不可靠）
        self._size = (max(1, int(width)), max(1, int(height)))
        self._img = Image.new("RGBA", self._size, tuple(bg) if bg else (0, 0, 0, 0))
        self._draw = ImageDraw.Draw(self._img, "RGBA")
        self._root = root            # 相对路径基准（小游戏包目录）
        self._version = 0

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
        self._version += 1
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
        self._version += 1
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
        self._version += 1
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
        self._version += 1
        x0, y0 = round(p1[0]), round(p1[1])
        x1, y1 = round(p2[0]), round(p2[1])
        width = max(1, round(thickness))
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
        font = _pil_font(size)
        self._version += 1
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
        self._version += 1
        self._img.paste(img, (round(pmin[0]), round(pmin[1])), img)


_PIL_FONTS = {}


def _pil_font(size):
    """按字号缓存 PIL 字体（与会话字体同一候选链；找不到回退内置字体）。"""
    size = max(6, round(size))
    font = _PIL_FONTS.get(size)
    if font is not None:
        return font
    from PIL import ImageFont
    font = None
    try:
        from dungeon.window.fonts import BOLD_FONT_PATHS
        for path in BOLD_FONT_PATHS:
            if os.path.exists(path):
                try:
                    font = ImageFont.truetype(path, size)
                    break
                except Exception:
                    continue
    except Exception:
        font = None
    if font is None:
        font = ImageFont.load_default()
    _PIL_FONTS[size] = font
    return font


class _StageGameAPI(GameAPI):
    """:class:`~.base.GameAPI` 的运行时实现：薄包装，持有舞台引用。

    契约脚本（check_minigame.py）禁止游戏触碰 ``api._``——私有面随时重构。

    游戏坐标系 = 画面区域（play rect）局部坐标：``width`` / ``height`` 返回
    画面区域尺寸，绘制指令进入位于该区域的 canvas child（声明了 ``aspect``
    的游戏区域不铺满窗口，其余部分露出压暗的副本界面；内容由 child window
    裁剪在区域内，越界绘制不外溢）。
    """

    def __init__(self, stage):
        self._stage = stage

    # ---- 几何 ----
    @property
    def _origin(self):
        rect = self._stage._play_rect
        return rect[0], rect[1]

    def _pt(self, x, y):
        # 游戏坐标即画布局部坐标：画布位于画面区域的 canvas child 内，
        # 裁剪与定位由 child window 的剪裁矩形承担（实测，见探针）
        return (x, y)

    @property
    def width(self):
        return self._stage._play_rect[2]

    @property
    def height(self):
        return self._stage._play_rect[3]

    # ---- 绘制（全部 parent 到舞台 drawlist，帧初清屏） ----
    # 注意 DPG 的 fill 语义：fill 是**颜色元组**（(0,0,0,-255) = 不填充），
    # color 是描边色——与 canvas 的 fill=True 布尔完全不同，必须转换。

    def draw_rect(self, pmin, pmax, color, fill=True, thickness=1.0):
        if fill:
            dpg.draw_rectangle(self._pt(*pmin), self._pt(*pmax), fill=color,
                               color=(0, 0, 0, 0), parent=_DRAW_TAG)
        if (not fill) or thickness > 1:
            dpg.draw_rectangle(self._pt(*pmin), self._pt(*pmax), color=color,
                               fill=_NO_FILL,
                               thickness=max(1.0, float(thickness)), parent=_DRAW_TAG)

    def draw_round_rect(self, pmin, pmax, radius, color, fill=True, thickness=1.0):
        if fill:
            dpg.draw_rectangle(self._pt(*pmin), self._pt(*pmax), rounding=float(radius),
                               fill=color, color=(0, 0, 0, 0), parent=_DRAW_TAG)
        if (not fill) or thickness > 1:
            dpg.draw_rectangle(self._pt(*pmin), self._pt(*pmax), rounding=float(radius),
                               color=color, fill=_NO_FILL,
                               thickness=max(1.0, float(thickness)), parent=_DRAW_TAG)

    def draw_circle(self, center, radius, color, fill=True, segments=0,
                    thickness=1.0):
        seg = int(segments) if segments else -1
        if fill:
            dpg.draw_circle(self._pt(*center), radius, fill=color,
                            color=(0, 0, 0, 0), segments=seg, parent=_DRAW_TAG)
        if (not fill) or thickness > 1:
            dpg.draw_circle(self._pt(*center), radius, color=color, fill=_NO_FILL,
                            segments=seg,
                            thickness=max(1.0, float(thickness)), parent=_DRAW_TAG)

    def draw_line(self, p1, p2, color, thickness=1.0):
        dpg.draw_line(self._pt(*p1), self._pt(*p2), color=color,
                      thickness=max(1.0, float(thickness)), parent=_DRAW_TAG)

    def draw_polygon(self, points, color):
        dpg.draw_polygon([self._pt(*point) for point in points],
                         fill=color, color=(0, 0, 0, 0), parent=_DRAW_TAG)

    def draw_text(self, text, pos, color, size=18):
        dpg.draw_text(self._pt(*pos), str(text), color=color, size=float(size),
                      parent=_DRAW_TAG)

    def draw_image(self, source, pmin, pmax, uv=None):
        tag = self._stage._ensure_texture(source)
        if tag is None:
            return
        kwargs = {}
        if uv:
            kwargs["uv_min"] = [uv[0], uv[1]]
            kwargs["uv_max"] = [uv[2], uv[3]]
        dpg.draw_image(tag, self._pt(*pmin), self._pt(*pmax), parent=_DRAW_TAG,
                       **kwargs)

    def offscreen(self, width, height, bg=None):
        return _PILCanvas(width, height, bg=bg, root=self._stage._root)

    # ---- 输入（handler 维护的状态表；DPG 的 is_key_down /
    #      is_mouse_button_down 在手动渲染下不更新，实测不可用） ----
    def key_down(self, key):
        code = _resolve_key(key)
        return code is not None and code in self._stage._keys_down

    def mouse_pos(self):
        try:
            gx, gy = self._stage._mouse_pos
            ox, oy = dpg.get_item_rect_min(_DRAW_TAG)
            px, py = self._origin
            return (gx - ox - px, gy - oy - py)
        except Exception:
            return (0.0, 0.0)

    def mouse_down(self, button=0):
        buttons = (dpg.mvMouseButton_Left, dpg.mvMouseButton_Right,
                   dpg.mvMouseButton_Middle)
        if not 0 <= int(button) < len(buttons):
            return False
        return buttons[int(button)] in self._stage._buttons_down

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


class _MiniGameStage:
    """一次小游戏会话的舞台：控件、帧驱动、纹理缓存与结算。"""

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
        self._textures = {}        # 绝对路径 → (tag, w, h)
        self._canvas_texes = {}    # id(canvas) → [tag, 已上传的内容版本]
        self._keys_down = set()    # handler 维护的按下键码集合
        self._buttons_down = set()  # handler 维护的按下鼠标键集合
        self._mouse_pos = (0.0, 0.0)
        self._input_tags = []      # 本舞台注册进全局 handler registry 的项
        self._game = None
        self._play_rect = [0, 0, 0, 0]   # 游戏画面区域（窗口内居中）
        self._api = _StageGameAPI(self)

    # ---------------- 生命周期 ----------------
    def _compute_play_rect(self, win_w, win_h):
        """按游戏声明的长宽比计算画面区域：等比缩放进「窗口 - 边缘留白」
        内并居中；未声明 aspect 的游戏沿用旧行为（铺满窗口）。游戏绘制统一
        裁剪进该区域（见 ``tick`` 的 push/pop_clip_rect）。"""
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
        """构建覆盖层控件并进入游戏。失败（游戏 setup 抛错等）返回 False。"""
        try:
            game = self._game_cls()
            game.api = self._api
            game.setup(self._config)
        except Exception as exc:
            process_log.log(f"[MiniGame] 游戏「{getattr(self._game_cls, 'id', '?')}」"
                            f"初始化失败: {exc}")
            return False
        self._game = game

        s = getattr(self._win, "_dpi_scale", 1.0) or 1.0
        w = dpg.get_viewport_client_width()
        h = dpg.get_viewport_client_height()
        self._size = [w, h]
        self._play_rect = self._compute_play_rect(w, h)
        x0, y0, pw, ph = self._play_rect
        if dpg.does_item_exist(_STAGE_TAG):
            dpg.delete_item(_STAGE_TAG)
        dpg.add_child_window(tag=_STAGE_TAG, parent="main_window",
                             pos=[0, 0], width=w, height=h, border=False)
        with dpg.theme() as theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, _BACKDROP_COLOR)
                dpg.add_theme_color(dpg.mvThemeCol_Border, (0, 0, 0, 0))
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 0, 0)
        dpg.bind_item_theme(_STAGE_TAG, theme)
        # 画面区域 child：DPG 的 child window 会把其 drawlist 内容裁剪进
        # 自己的矩形（帧缓冲实测），游戏内容因此不会溢出到副本界面——
        # 这才是「画布」与坐标平移的真正分界，游戏坐标即画布局部坐标。
        dpg.add_child_window(tag=_CANVAS_TAG, parent=_STAGE_TAG,
                             pos=[x0, y0], width=pw, height=ph, border=False,
                             no_scrollbar=True)
        with dpg.theme() as canvas_theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (0, 0, 0, 0))
                dpg.add_theme_color(dpg.mvThemeCol_Border, (0, 0, 0, 0))
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 0, 0)
        dpg.bind_item_theme(_CANVAS_TAG, canvas_theme)
        # C6 怪癖：容器块之后创建的控件推断不出父级，drawlist 显式 parent。
        # 覆盖层（边框/HUD）建在游戏画布之后，渲染在后才能盖在游戏内容上。
        dpg.add_drawlist(tag=_DRAW_TAG, width=pw, height=ph, parent=_CANVAS_TAG)
        dpg.add_drawlist(tag=_OVERLAY_TAG, width=pw, height=ph, parent=_CANVAS_TAG)
        self._last_t = time.monotonic()
        self._install_input()
        self._win._frame.every(self._win._frame.EVERY_FRAME,
                               self._win._minigame_tick, key=_TICK_KEY)
        process_log.log(f"[MiniGame] 小游戏「{game.id}」开始（覆盖层舞台）")
        return True

    # ---------------- 输入（handler 维护状态，见 _StageGameAPI） ----------------
    def _install_input(self):
        """把键鼠状态 handler 注册进**全局** handler registry。

        DPG 的 ``is_key_down`` / ``is_mouse_button_down`` 在本应用的手动渲染
        配置下不更新（实测恒 False），但 handler 回调可靠（会话按键同机制），
        因此这里用 down/up 回调维护状态表；teardown 时逐项删除。
        """
        self._input_tags = []
        with dpg.handler_registry():
            for code in _game_key_codes():
                self._input_tags.append(dpg.add_key_down_handler(
                    key=code, callback=self._key_event, user_data=(code, True)))
                self._input_tags.append(dpg.add_key_release_handler(
                    key=code, callback=self._key_event, user_data=(code, False)))
            for button in (dpg.mvMouseButton_Left, dpg.mvMouseButton_Right,
                           dpg.mvMouseButton_Middle):
                self._input_tags.append(dpg.add_mouse_down_handler(
                    button=button, callback=self._button_event,
                    user_data=(button, True)))
                self._input_tags.append(dpg.add_mouse_release_handler(
                    button=button, callback=self._button_event,
                    user_data=(button, False)))
            self._input_tags.append(dpg.add_mouse_move_handler(
                callback=self._mouse_move_event))

    def _uninstall_input(self):
        for tag in self._input_tags:
            try:
                if dpg.does_item_exist(tag):
                    dpg.delete_item(tag)
            except Exception:
                pass
        self._input_tags = []
        self._keys_down.clear()
        self._buttons_down.clear()

    def _key_event(self, sender, app_data, user_data):
        code, down = user_data
        if down:
            self._keys_down.add(code)
        else:
            self._keys_down.discard(code)

    def _button_event(self, sender, app_data, user_data):
        button, down = user_data
        if down:
            self._buttons_down.add(button)
        else:
            self._buttons_down.discard(button)

    def _mouse_move_event(self, sender, app_data):
        try:
            self._mouse_pos = (float(app_data[0]), float(app_data[1]))
        except Exception:
            pass

    def tick(self):
        """帧驱动：清屏 → 游戏逻辑 → HUD →（已结算）收尾。只由帧任务调用。"""
        if self._finished:
            # 结算可能发生在两次 tick 之间（api.finish 在帧任务外的帧线程调用），
            # 这里兜底销毁——正常路径（update 内 finish）也会再次走到这
            self._win._destroy_mini_game_stage()
            return
        if self._win._closing:
            return
        w, h = dpg.get_viewport_client_width(), dpg.get_viewport_client_height()
        if (w, h) != tuple(self._size):
            self._size = [w, h]
            self._play_rect = self._compute_play_rect(w, h)
            x0, y0, pw, ph = self._play_rect
            if dpg.does_item_exist(_STAGE_TAG):
                dpg.configure_item(_STAGE_TAG, width=w, height=h)
            if dpg.does_item_exist(_CANVAS_TAG):
                dpg.configure_item(_CANVAS_TAG, pos=[x0, y0], width=pw, height=ph)
            for tag in (_DRAW_TAG, _OVERLAY_TAG):
                if dpg.does_item_exist(tag):
                    dpg.configure_item(tag, width=pw, height=ph)
        now = time.monotonic()
        dt = clamp_dt(now - self._last_t)
        self._last_t = now
        if dpg.does_item_exist(_DRAW_TAG):
            dpg.delete_item(_DRAW_TAG, children_only=True)
        try:
            self._game.update(dt)
        except Exception as exc:
            process_log.log(f"[MiniGame] 游戏「{self._game.id}」帧异常，按无结果收场: {exc}")
            self._abort()
            self._win._destroy_mini_game_stage()
            return
        # 覆盖层（画布局部坐标，盖在游戏内容上）：画面区域不满窗时画一圈
        # 细边框与压暗的副本界面分开；HUD 固定在画面区域左上角
        if dpg.does_item_exist(_OVERLAY_TAG):
            dpg.delete_item(_OVERLAY_TAG, children_only=True)
            pw, ph = self._play_rect[2], self._play_rect[3]
            if self._play_rect[2:] != [w, h]:
                dpg.draw_rectangle([0, 0], [pw, ph],
                                   color=_PLAY_FRAME_COLOR, thickness=2.0,
                                   parent=_OVERLAY_TAG)
            if self._hud:
                dpi = getattr(self._win, "_dpi_scale", 1.0) or 1.0
                dpg.draw_text([round(16 * dpi), round(12 * dpi)],
                              self._hud, color=_HUD_COLOR, size=_HUD_SIZE,
                              parent=_OVERLAY_TAG)
        if self._finished:
            self._win._destroy_mini_game_stage()

    def destroy(self):
        """销毁控件与资源并回传结果（会话收尾路径帧时钟已停，回传自然失效）。"""
        self._win._frame.cancel(_TICK_KEY)
        self._uninstall_input()
        if self._game is not None:
            try:
                self._game.teardown()
            except Exception as exc:
                process_log.log(f"[MiniGame] 游戏「{self._game.id}」收尾异常: {exc}")
            self._game = None
        for tag, _w, _h in self._textures.values():
            try:
                if dpg.does_item_exist(tag):
                    dpg.delete_item(tag)
            except Exception:
                pass
        self._textures.clear()
        for entry in self._canvas_texes.values():
            try:
                if dpg.does_item_exist(entry[0]):
                    dpg.delete_item(entry[0])
            except Exception:
                pass
        self._canvas_texes.clear()
        for tag in (_OVERLAY_TAG, _DRAW_TAG, _CANVAS_TAG, _STAGE_TAG):
            if dpg.does_item_exist(tag):
                dpg.delete_item(tag)
        on_result = self._on_result
        self._on_result = None
        if on_result is not None:
            # 结果必回传一次：正常结算给结果字典；中止 / 异常给 None
            # （与 web 后端「用户关窗 = 无结果」同一语义，接收方据此清挂起态）
            on_result(self._result)

    # ---------------- 结算 ----------------
    def _finish(self, result):
        """游戏侧结算（api.finish）：记录结果，帧末统一销毁。

        小游戏不再区分胜负：``result`` 为 dict 时原样回传（返回值取
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
        """draw_image 的源 → DPG 纹理 tag。

        - 文件路径：静态纹理，按绝对路径缓存；
        - 离屏画布：动态纹理，按内容版本判定是否重传（numpy 快路径）。
        失败返回 None（调用方静默跳过，不拖垮帧）。
        """
        if isinstance(source, _PILCanvas):
            return self._canvas_texture(source)
        if not source:
            return None
        path = str(source)
        if not os.path.isabs(path) and self._root:
            path = os.path.join(self._root, path)
        cached = self._textures.get(path)
        if cached is not None:
            return cached[0]
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
                tw, th = img.width, img.height
                data = _image_to_texture_data(img)
            tag = f"minigame_tex_{len(self._textures)}"
            dpg.add_static_texture(tw, th, data, tag=tag,
                                   parent="dungeon_texture_registry")
        except Exception as exc:
            process_log.log(f"[MiniGame] 贴图加载失败 {path}: {exc}")
            return None
        self._textures[path] = (tag, tw, th)
        return tag

    def _canvas_texture(self, canvas):
        key = canvas._uid
        entry = self._canvas_texes.get(key)
        if entry is None:
            try:
                data = _image_to_texture_data(canvas._img)
                tag = f"minigame_canvas_tex_{len(self._canvas_texes)}"
                dpg.add_dynamic_texture(canvas.width, canvas.height, data,
                                        tag=tag, parent="dungeon_texture_registry")
            except Exception as exc:
                process_log.log(f"[MiniGame] 离屏画布上传失败: {exc}")
                return None
            entry = [tag, canvas._version]
            self._canvas_texes[key] = entry
            return tag
        tag, uploaded_version = entry
        if uploaded_version != canvas._version:
            try:
                dpg.set_value(tag, _image_to_texture_data(canvas._img))
            except Exception as exc:
                process_log.log(f"[MiniGame] 离屏画布重传失败: {exc}")
                return tag
            entry[1] = canvas._version
        return tag


class MiniGameStageHandler:
    """会话窗口 mixin：py 后端小游戏的打开 / 帧驱动代理 / ESC 中止 / 销毁。"""

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

    def _minigame_tick(self):
        """帧任务代理（帧任务挂在窗口的调度器上，key 归属统一为 _TICK_KEY）。"""
        stage = self._mini_game_stage
        if stage is not None:
            stage.tick()

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
