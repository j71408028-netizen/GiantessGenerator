"""窗口视效：闪光 / 振动 / 反色 / 色差分离。

与「章节效果覆盖」（PIL 重采样背景滤镜）相对：这组视效操作**整个会话窗口**——
覆盖层盖住全窗、内容整体位移——并以**绝对时间**（秒）消退，与步进无关。
触发即播放：派发后立即返回，视效期间流式生成、音频播放、切换背景等一切
动作照常，不存在"等待视效结束"的路径。

实现约定与 window 层一致：

- **时机**全部挂在帧时钟（``owner._frame``）：每类视效一条 repeating 帧任务，
  按 ``time.monotonic`` 计算进度，到期由任务自己收尾——不数步数、不开线程；
- **重活**（整帧像素的反相 / 通道分离）丢给像素工作者，结果经 ``_frame.call``
  回帧线程上屏；
- 触发（``start``）可能来自任意线程，一律 ``_frame.call`` 投递给帧线程。

## imgui 立即模式的画法（P5 重写）

DPG 版的这一层靠「主窗口里的透明子窗 + 绘制列表 + 动态纹理 + 抓帧落盘」实现，
imgui 侧四个视效各走各的路（见 ``.workbuddy/imgui-migration/40-status-p5.md``）：

============  ==================================================================
视效           实现
============  ==================================================================
``flash``     根窗口 draw list 上一块全窗白色矩形，不透明度从峰值线性退到 0
``shake``     根窗口原点与尺寸每帧按衰减正弦偏移（``ui._draw_frame`` 落实），
              **整窗内容一起抖**，与 DPG 版"移动主窗口全部直接子项"等价
``invert``    ``after_swap`` 里 ``glReadPixels`` 抓整帧 → 工作者反相 → 上
``rgb_split`` ``TextureGpu`` 当全窗覆盖层，不透明度按时长退到 0
============  ==================================================================

三点与 DPG 版**故意不同**：

1. **抓帧不落盘**。旧版靠 ``dpg.output_frame_buffer(file=...)`` 写临时 PNG 再
   轮询读回（因为 DPG 的抓帧是帧末异步写文件）。这里直接读后备缓冲得到
   ``numpy`` 数组，省掉一轮 PNG 编解码与磁盘往返。
2. **抓一次，不每帧抓**。反色/色差是"把这一瞬间的画面染色"，消退靠覆盖层的
   不透明度；每帧都抓会白白多花一次全窗回读（720p 约 2 ms）且画面会随着下层
   动画不停变化，反而比旧版更花。
3. **闪光与覆盖层画在根窗口自己的 draw list 上**，不再需要"透明子窗 + 移动子项
   到最上层"那套层级体操——立即模式里后画的就在上面。
"""

import math
import time

import numpy as np
from PIL import Image, ImageChops, ImageOps

from dungeon import process_log
from dungeon.actions import WINDOW_FX_KEYS
from dungeon.window import gl_util
from dungeon.window import imgui_util as iu
from dungeon.window.background import PixelWorker

#: 各视效的帧任务 key（同类互斥：重触发顶掉旧的）
_FLASH_TASK = "fx:flash"
_SHAKE_TASK = "fx:shake"
_OVERLAY_TASK = "fx:overlay"
#: 覆盖层「等像素结果」的轮询任务 key（P5 起只是在等工作者线程，不再等落盘）
_SNAP_POLL_TASK = "fx:snapshot"
#: 抓帧后等工作者线程处理的间隔与最多次数（超过即视为失败，放弃这次视效）
_SNAP_POLL_INTERVAL = 0.02
_SNAP_POLL_MAX = 25
#: 振动频率（Hz）：太低拖沓，太高在低帧率下走样
_SHAKE_HZ = 14.0


def parse_fx_params(action_data):
    """从动作参数解析 ``(fx_key, duration, intensity)``，越界一律夹紧。

    ``duration`` 是绝对时间（秒），与章节效果覆盖的步数语义不同；解析失败或越界
    都夹回合法区间，不让视效因配置笔误变成常驻画面。
    """
    data = action_data or {}
    fx_key = str(data.get("fx") or "").strip()
    try:
        duration = float(data.get("duration", 0.8))
    except (TypeError, ValueError):
        duration = 0.8
    duration = min(10.0, max(0.1, duration))
    try:
        intensity = float(data.get("intensity", 60))
    except (TypeError, ValueError):
        intensity = 60.0
    intensity = min(100.0, max(0.0, intensity))
    return fx_key, duration, intensity


class WindowFx:
    """会话窗口的瞬时视效层：构建一次，触发即播，按时长自消。

    对窗口层的可见面只有四个：``build`` / ``relayout`` / ``shutdown`` /
    ``start``；绘制相关的三个查询（``flash_color`` / ``pixel_overlay`` /
    ``shake_offset``）供 ``ui._draw_frame`` 每帧取用。
    """

    def __init__(self, owner):
        self.owner = owner
        #: 视效层是否已建（``build`` 之后为真；``post_init`` 时调用）
        self._built = False
        #: 布局尺寸（客户区物理像素，``relayout`` 每帧/每次重排同步）
        self._w = 1.0
        self._h = 1.0
        #: 像素工作者：反色 / 色差的重活线程
        self._worker = PixelWorker(name="dungeon-fx-worker")
        #: 进行中的视效种类（"flash" / "shake" / "overlay"）
        self._active = set()

        # ---- 闪光 ----
        #: 闪光峰值不透明度（0~255）；0 表示当前没有闪光
        self._flash_alpha = 0.0

        # ---- 振动 ----
        #: 根窗口内容偏移 ``(dx, dy)``；``(0.0, 0.0)`` 表示不振
        self._shake = (0.0, 0.0)

        # ---- 反色 / 色差覆盖层 ----
        #: 覆盖层纹理句柄（``TextureGpu``）；换图时先建后丢
        self._pixel_texture = None
        #: 覆盖层不透明度（0~255）；0 表示当前没有覆盖层
        self._pixel_alpha = 0.0
        #: 覆盖层的像素处理代数：重触发会让旧结果作废
        self._pixel_gen = 0
        #: 已抓到的整帧 ``(rgba, w, h, gen)``，等工作者线程取走
        self._pixel_pending = None
        #: 待上屏的覆盖层像素 ``(rgba, gen)``，由工作者线程经 ``_frame.call`` 投递
        self._pixel_ready = None
        #: 覆盖层的色差分离像素数（``_begin_overlay`` 算好后交给 ``_process_pixels``）
        self._split_px = 2
        #: 覆盖层种类（"invert" / "rgb_split"）：工作者线程按它选变换
        self._overlay_key = "invert"

    @property
    def _frame(self):
        return self.owner._frame

    # ==================== 构建 / 重排 / 收尾 ====================
    def build(self, *args, **kwargs):
        """会话建 UI 时定标（``post_init``，GL 已就绪）。

        立即模式没有视效层控件要建——闪光/覆盖层是每帧现画的 draw list 矩形与
        图片，振动是根窗口原点偏移。这里只记下布局尺寸并置位。

        兼容旧签名：DPG 版是 ``build(viewport_w, viewport_h, tex_w, tex_h)``，
        多出来的参数（视口尺寸）按前两个取用。
        """
        if len(args) >= 2:
            self._w, self._h = float(args[0]), float(args[1])
        self._built = True

    def relayout(self, w, h):
        """视口重排：闪光/覆盖层覆盖的矩形跟随客户区（仅帧线程）。"""
        self._w, self._h = float(max(1.0, w)), float(max(1.0, h))
        if self._shake != (0.0, 0.0):
            # 振动中遇到重排：基线换了，继续用旧振幅会把新布局抖回错位。
            # 与 DPG 版同一个处置——立刻收场，交给 `_relayout` 后的静态布局。
            self._frame.cancel(_SHAKE_TASK)
            self._shake = (0.0, 0.0)
            self._end_effect("shake")

    def shutdown(self):
        """会话收尾：撤掉帧任务与像素工作者，丢掉纹理句柄。"""
        for key in (_FLASH_TASK, _SHAKE_TASK, _OVERLAY_TASK, _SNAP_POLL_TASK):
            self._frame.cancel(key)
        self._active.clear()
        self._flash_alpha = 0.0
        self._shake = (0.0, 0.0)
        self._pixel_alpha = 0.0
        self._pixel_texture = None          # RAII：丢最后一个引用即释放 GPU 资源
        self._pixel_pending = None
        self._pixel_ready = None
        self._worker.shutdown()

    # ==================== 绘制查询（ui._draw_frame 每帧调用） ====================
    def flash_color(self):
        """当前闪光矩形的打包色；没有闪光时返回 ``None``。"""
        if self._flash_alpha <= 0.0 or "flash" not in self._active:
            return None
        return iu.u32(255, 255, 255, int(round(self._flash_alpha)))

    def shake_offset(self):
        """根窗口内容的当前偏移 ``(dx, dy)``（振幅 0 时为 ``(0.0, 0.0)``）。"""
        return self._shake

    def pixel_overlay(self):
        """当前覆盖层 ``(texture, alpha)``；没有覆盖层时返回 ``None``。

        ``texture`` 是 ``hello_imgui.TextureGpu`` 句柄，调用方用
        ``texture.texture_id()`` 取 GL 名字。
        """
        if self._pixel_texture is None or self._pixel_alpha <= 0.0:
            return None
        return (self._pixel_texture, int(round(self._pixel_alpha)))

    # ==================== 启动 ====================
    def start(self, fx_key, duration, intensity):
        """启动一个窗口视效（任意线程可调）：投递给帧线程，不阻塞调用方。"""
        if fx_key not in WINDOW_FX_KEYS:
            return
        if getattr(self.owner, "_closing", False) or not self._built:
            return
        self._frame.call(self._begin, fx_key, duration, intensity)

    def _begin(self, fx_key, duration, intensity):
        if getattr(self.owner, "_closing", False) or not self._built:
            return
        if fx_key == "flash":
            self._active.add("flash")
            self._begin_flash(duration, intensity)
        elif fx_key == "shake":
            self._active.add("shake")
            self._begin_shake(duration, intensity)
        else:
            self._active.add("overlay")
            self._begin_overlay(fx_key, duration, intensity)

    @staticmethod
    def _peak_alpha(intensity):
        """强度 0~100 → 峰值不透明度：低强度保底可见，100 即完全不透明。"""
        return min(255, max(64, round(255 * (0.25 + 0.75 * intensity / 100.0))))

    def _end_effect(self, kind):
        self._active.discard(kind)

    # ==================== 闪光 ====================
    def _begin_flash(self, duration, intensity):
        """全窗白色矩形，不透明度从峰值线性退到 0（每帧重画，故只需记峰值）。"""
        peak = self._peak_alpha(intensity)
        frame = self._frame
        started = time.monotonic()
        self._flash_alpha = float(peak)

        def tick():
            if getattr(self.owner, "_closing", False):
                self._flash_alpha = 0.0
                frame.cancel(_FLASH_TASK)
                self._end_effect("flash")
                return
            progress = (time.monotonic() - started) / max(0.01, duration)
            if progress >= 1.0:
                self._flash_alpha = 0.0
                frame.cancel(_FLASH_TASK)
                self._end_effect("flash")
                return
            self._flash_alpha = peak * (1.0 - progress)

        frame.every(0.0, tick, key=_FLASH_TASK)

    # ==================== 振动 ====================
    def _begin_shake(self, duration, intensity):
        """整窗内容按衰减正弦偏移：``ui._draw_frame`` 每帧读 :meth:`shake_offset`。

        与 DPG 版的差别：那边要遍历主窗口的直接子项逐个 ``set_item_pos`` 并记基线，
        这边只挪**根窗口原点**——内容坐标完全不动，等价于"整窗一起抖"，而且
        组件、浮层、背景全部自动跟随（它们都画在根窗口坐标系里）。
        """
        scale = float(getattr(self.owner, "_dpi_scale", 1.0) or 1.0)
        amp = (4.0 + 36.0 * intensity / 100.0) * scale
        frame = self._frame
        started = time.monotonic()

        def tick():
            if getattr(self.owner, "_closing", False):
                self._shake = (0.0, 0.0)
                frame.cancel(_SHAKE_TASK)
                self._end_effect("shake")
                return
            elapsed = time.monotonic() - started
            progress = elapsed / max(0.01, duration)
            if progress >= 1.0:
                self._shake = (0.0, 0.0)
                frame.cancel(_SHAKE_TASK)
                self._end_effect("shake")
                return
            decay = 1.0 - progress
            phase = 2.0 * math.pi * _SHAKE_HZ * elapsed
            dx = amp * decay * math.sin(phase)
            dy = 0.6 * amp * decay * math.cos(phase * 0.9)
            self._shake = (dx, dy)

        frame.every(0.0, tick, key=_SHAKE_TASK)

    # ==================== 反色 / 色差分离：整帧覆盖层 ====================
    def _begin_overlay(self, fx_key, duration, intensity):
        """抓一帧当前画面 → 工作者加工 → 当覆盖层压回，不透明度按时长退到 0。

        抓手在 ``after_swap``（``ui._capture_frame`` → :meth:`capture`），因为只有
        那时 GL 后备缓冲里才是刚画完的整帧；本方法只登记"要抓"，然后等结果。
        """
        peak = self._peak_alpha(intensity)
        scale = float(getattr(self.owner, "_dpi_scale", 1.0) or 1.0)
        self._overlay_key = "invert" if fx_key == "invert" else "rgb_split"
        self._split_px = max(1, round((2.0 + 28.0 * intensity / 100.0) * scale))
        self._pixel_gen += 1
        gen = self._pixel_gen
        self._pixel_pending = None
        self._pixel_ready = None
        self._pixel_alpha = 0.0
        # 旧覆盖层立刻撤掉：新的一帧还没抓到，留着旧的会闪一下上一帧的残影
        self._pixel_texture = None

        frame = self._frame
        started = time.monotonic()
        state = {"tries": 0}

        def tick():
            if getattr(self.owner, "_closing", False):
                frame.cancel(_OVERLAY_TASK)
                self._end_effect("overlay")
                return
            # 换代：本任务属于上一代，直接退场（新一代有自己的任务）
            if self._pixel_gen != gen:
                frame.cancel(_OVERLAY_TASK)
                return
            progress = (time.monotonic() - started) / max(0.01, duration)
            if progress >= 1.0:
                frame.cancel(_OVERLAY_TASK)
                self._pixel_alpha = 0.0
                self._pixel_texture = None
                self._end_effect("overlay")
                return
            if self._pixel_texture is not None:
                # 覆盖层已上屏：只推进不透明度（下层画面照常活动）
                self._pixel_alpha = peak * (1.0 - progress)
                return
            # 工作者刚交付的像素：本帧上传成纹理（`capture` 里不会再抓第二张，
            # 因为 `_pixel_ready` 的判据把它挡住了）
            ready = self._pixel_ready
            if ready is not None and ready[1] == gen:
                self._apply_pixel_texture(ready[0], gen)
                if self._pixel_texture is not None:
                    self._pixel_ready = None
                    self._pixel_alpha = peak * (1.0 - progress)
                    return
            # 还没有可用画面：等像素工作者（抓帧到上屏通常 1~3 帧）
            state["tries"] += 1
            if state["tries"] > _SNAP_POLL_MAX:
                process_log.log("[Fx] 视效覆盖层已放弃（抓帧/处理未完成）")
                frame.cancel(_OVERLAY_TASK)
                self._end_effect("overlay")

        frame.every(_SNAP_POLL_INTERVAL, tick, key=_OVERLAY_TASK)

    def capture(self):
        """（帧线程，``after_swap``）把刚画完的一帧交给像素工作者反相/分离通道。

        四条早退合起来保证**每一代只抓一次**：没有覆盖层在播、上一张还在工作者手里
        （``_pixel_pending``）、本代结果已交付、或者本代已经上屏
        （``_pixel_texture`` 即为这一代的产物）。漏掉最后一条会让每一帧都重新抓、
        重新上传同一张覆盖层——P5 探针实测到的正是这个（同一个 ``gen`` 的
        ``_apply_pixel_texture`` 被调了几十次）。
        """
        if "overlay" not in self._active:
            return
        if self._pixel_pending is not None:
            return
        if (self._pixel_ready is not None
                and self._pixel_ready[1] == self._pixel_gen):
            return
        if self._pixel_texture is not None:
            return
        rgba = self._read_framebuffer()
        if rgba is None:
            return
        gen = self._pixel_gen
        self._pixel_pending = (rgba, gen)
        self._worker.submit(lambda: self._process_pixels(gen))

    def _read_framebuffer(self):
        """从当前 GL 后备缓冲读一帧，返回 ``(h, w, 4)`` 的 ``uint8``（行序已翻正）。"""
        size = self._framebuffer_size()
        if size is None:
            return None
        w, h = size
        rgba = gl_util.read_pixels(w, h)
        if rgba is None:
            process_log.log("[Fx] 抓帧失败：GL 回调不可用（见 gl_util）")
        return rgba

    def _framebuffer_size(self):
        """当前帧缓冲的**像素**尺寸 ``(w, h)``（= 客户区 × framebuffer_scale）。"""
        try:
            from imgui_bundle import imgui
            io = imgui.get_io()
            w = int(round(io.display_size.x * io.display_framebuffer_scale.x))
            h = int(round(io.display_size.y * io.display_framebuffer_scale.y))
        except Exception:
            return None
        if w <= 1 or h <= 1:
            return None
        return (w, h)

    # ---------- 工作者线程 ----------
    def _process_pixels(self, gen):
        """（工作者线程）把抓到的整帧加工成覆盖层图像，交回帧线程上屏。"""
        job = self._pixel_pending
        self._pixel_pending = None
        if job is None or job[1] != gen:
            return
        rgba = job[0]
        try:
            out = self._transform(rgba)
        except Exception as exc:
            process_log.log(f"[Fx] 视效图像处理失败: {exc}")
            return
        self._frame.call(self._pixel_processed, out, gen)

    def _transform(self, rgba):
        """反色 / 色差分离：纯像素变换（工作者线程，不碰 GL）。"""
        img = Image.fromarray(np.ascontiguousarray(rgba), "RGBA").convert("RGB")
        if self._overlay_key == "invert":
            out = ImageOps.invert(img)
        else:
            # 色差分离：R 左移、G 原位、B 右移，通道相加合成一张（与 DPG 版同款）
            r, g, b = img.split()
            px = max(0, int(self._split_px))
            out = ImageChops.add(ImageChops.add(
                ImageChops.offset(r, -px, 0), g), ImageChops.offset(b, px, 0))
        return np.asarray(out.convert("RGBA"), dtype=np.uint8)

    def _pixel_processed(self, rgba, gen):
        """（帧线程）工作者交付结果：登记待上屏。"""
        if gen != self._pixel_gen or getattr(self.owner, "_closing", False):
            return
        self._pixel_ready = (rgba, gen)

    def _apply_pixel_texture(self, rgba, gen):
        """（帧线程，GL 就绪）把覆盖层像素上传成 ``TextureGpu``。"""
        if gen != self._pixel_gen or getattr(self.owner, "_closing", False):
            return
        try:
            from imgui_bundle import hello_imgui
            texture = hello_imgui.create_texture_gpu_from_rgba_data(rgba)
        except Exception as exc:
            process_log.log(f"[Fx] 覆盖层纹理上传失败: {exc}")
            return
        # 先建后丢：中间不留"没有覆盖层"的帧
        self._pixel_texture = texture

