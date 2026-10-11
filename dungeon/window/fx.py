"""窗口视效：闪光 / 振动 / 反色 / 色差分离。

与「短暂视效」（PIL 重采样背景滤镜）相对：这组视效操作**整个会话窗口**——
覆盖层盖住全窗、内容整体位移——并以**绝对时间**（秒）消退，与步进无关。
触发即播放：派发后立即返回，视效期间流式生成、音频播放、切换背景等一切
动作照常，不存在"等待视效结束"的路径。

实现约定与 window 层一致：

- **时机**全部挂在帧时钟（``owner._frame``）：每类视效一条 repeating 帧任务，
  按 ``time.monotonic`` 计算进度，到期由任务自己收尾——不数步数、不开线程；
- **重活**（帧快照的反相 / 通道分离）丢给像素工作者，结果经 ``_frame.call``
  回帧线程上屏；
- DPG 调用只出现在帧线程路径里（``start`` 经 ``_frame.call`` 投递，任意线程可调）。

四个视效的画法：

- ``flash`` 闪光：视效层里一块全窗白色矩形，fill 不透明度从峰值线性退到 0；
- ``shake`` 振动：主窗口全部直接子项按基线位置叠加衰减正弦偏移，整窗内容
  一起抖；
- ``invert`` 反色 / ``rgb_split`` 色差分离：先 ``dpg.output_frame_buffer`` 抓
  一帧当前画面（整窗真实像素，含文本与 UI），像素工作者加工后上到视效纹理，
  经视效层的半透明 ``draw_image`` 盖回全窗，再按时长把不透明度退到 0——
  消退期间下层画面照常活动。抓帧失败退回处理当前背景图。

层叠陷阱（帧缓冲实测，DPG 2.3.1）：嵌在窗口里的裸 drawlist **无论子项顺序**
都在子窗口之下渲染——直接盖不住文字与组件；也不能用视口级绘制列表
（``viewport_drawlist`` 在本版本不渲染 ``draw_image``，见 ui.py 同款注释）。
因此视效层是主窗口里的**透明子窗**包住绘制列表，触发时移到子项末尾（最上）
并显示，全部视效结束后隐藏（常驻透明层会挡住组件工具条的悬停判定）。
"""

import math
import os
import tempfile
import time

import numpy as np
from PIL import Image, ImageChops, ImageOps
import dearpygui.dearpygui as dpg

from dungeon import process_log
from dungeon.actions import WINDOW_FX_KEYS
from dungeon.window.background import PixelWorker

#: 各视效的帧任务 key（同类互斥：重触发顶掉旧的）
_FLASH_TASK = "fx:flash"
_SHAKE_TASK = "fx:shake"
_OVERLAY_TASK = "fx:overlay"
#: 帧快照落盘的轮询任务 key（output_frame_buffer 在帧末异步写文件）
_SNAP_POLL_TASK = "fx:snapshot"
#: 快照轮询的间隔与最多次数（超过即视为抓帧失败，退回背景图）
_SNAP_POLL_INTERVAL = 0.05
_SNAP_POLL_MAX = 20
#: 振动频率（Hz）：太低拖沓，太高在低帧率下走样
_SHAKE_HZ = 14.0


def parse_fx_params(action_data):
    """从动作参数解析 ``(fx_key, duration, intensity)``，越界一律夹紧。

    ``duration`` 是绝对时间（秒），与短暂视效的步数语义不同；解析失败或越界
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
    """会话窗口的瞬时视效层：构建一次，触发即播，按时长自消。"""

    def __init__(self, owner):
        self.owner = owner
        self._built = False
        #: 视效层 = 主窗口里的透明子窗（tag fx_layer）包住绘制列表。**不能直接用
        #: 裸 drawlist**：DPG 2.3.1 里嵌在窗口里的 drawlist 无论子项顺序都在
        #: 子窗口之下渲染（帧缓冲实测），只有再包一层子窗才能盖到文字与组件上。
        self._layer = "fx_layer"
        self._drawlist = "fx_drawlist"
        self._flash_rect = "fx_flash_rect"
        self._overlay_image = "fx_overlay_image"
        self._texture = "fx_texture"
        self._tex_w = 0
        self._tex_h = 0
        #: 视效纹理的组合缓冲（数据小于纹理时贴进左上角，与背景 apply_data 同理）
        self._compose_buf = None
        self._worker = PixelWorker(name="dungeon-fx-worker")
        #: 进行中的视效种类（"flash" / "shake" / "overlay"）：全空时隐藏视效层，
        #: 避免常驻透明层挡住组件工具条的悬停判定
        self._active = set()
        #: 振动基线：开始时主窗口直接子项的 pos（收尾/重排时还原）
        self._shake_base = {}
        #: 帧快照落盘路径（临时文件，用完即删）
        self._snap_path = os.path.join(
            tempfile.gettempdir(), f"dungeon_fx_snap_{os.getpid()}.png")

    @property
    def _frame(self):
        return self.owner._frame

    # ---------- 构建 / 重排 / 收尾 ----------
    def build(self, viewport_w, viewport_h, tex_w, tex_h):
        """会话建 UI 时创建视效层（仅帧线程）：透明子窗 + 绘制列表 + 视效纹理。

        纹理与背景纹理同规格（客户区 ∨ 主显示器物理尺寸），一次建好之后
        每次视效只走 set_value 快速路径，不重建纹理。视效层平时隐藏。
        """
        self._tex_w, self._tex_h = tex_w, tex_h
        dpg.add_dynamic_texture(
            width=tex_w, height=tex_h,
            default_value=[0.0] * (tex_w * tex_h * 4),
            tag=self._texture, parent="dungeon_texture_registry")
        with dpg.child_window(tag=self._layer, parent="main_window",
                              pos=[0, 0], width=viewport_w, height=viewport_h,
                              no_scrollbar=True, no_scroll_with_mouse=True,
                              border=False, show=False):
            with dpg.drawlist(tag=self._drawlist, width=viewport_w,
                              height=viewport_h):
                dpg.draw_rectangle(tag=self._flash_rect, pmin=[0, 0],
                                   pmax=[viewport_w, viewport_h],
                                   color=(0, 0, 0, 0), fill=(255, 255, 255, 0))
                dpg.draw_image(tag=self._overlay_image, texture_tag=self._texture,
                               pmin=[0, 0], pmax=[viewport_w, viewport_h],
                               color=(255, 255, 255, 0), show=False)
        with dpg.theme() as layer_theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (0, 0, 0, 0))
        dpg.bind_item_theme(self._layer, layer_theme)
        self._built = True

    def relayout(self, w, h):
        """视口重排：覆盖层几何跟随（仅帧线程；``_relayout`` 末尾调用）。"""
        if not self._built or self.owner._closing:
            return
        if dpg.does_item_exist(self._layer):
            dpg.configure_item(self._layer, width=w, height=h)
        if dpg.does_item_exist(self._drawlist):
            dpg.configure_item(self._drawlist, width=w, height=h)
        if dpg.does_item_exist(self._flash_rect):
            dpg.configure_item(self._flash_rect, pmin=[0, 0], pmax=[w, h])
        if dpg.does_item_exist(self._overlay_image):
            dpg.configure_item(self._overlay_image, pmin=[0, 0], pmax=[w, h],
                               uv_min=[0.0, 0.0],
                               uv_max=[w / max(1, self._tex_w), h / max(1, self._tex_h)])
        if self._shake_base:
            # 振动中遇到重排：_relayout 刚把子项摆回正确基线，振动立即收场
            # （继续用旧基线会把 resize 后的布局抖回错位）
            self._frame.cancel(_SHAKE_TASK)
            self._shake_base = {}
            self._end_effect("shake")

    def shutdown(self):
        """会话收尾：撤掉帧任务与像素工作者，清掉临时快照。"""
        for key in (_FLASH_TASK, _SHAKE_TASK, _OVERLAY_TASK, _SNAP_POLL_TASK):
            self._frame.cancel(key)
        self._active.clear()
        self._shake_base = {}
        self._worker.shutdown()
        self._remove_snap()

    # ---------- 视效层显隐 ----------
    def _show_layer(self):
        """把视效层提到最前并显示（move_item 必须显式给 parent，否则 DPG 报
        [1005]；不带 before 即追加到子项末尾 = 最上层）。"""
        dpg.move_item(self._layer, parent="main_window")
        dpg.configure_item(self._layer, show=True)

    def _end_effect(self, kind):
        self._active.discard(kind)
        if not self._active and dpg.does_item_exist(self._layer):
            dpg.configure_item(self._layer, show=False)

    # ---------- 启动 ----------
    def start(self, fx_key, duration, intensity):
        """启动一个窗口视效（任意线程可调）：经帧队列投递，不阻塞调用方。"""
        if fx_key not in WINDOW_FX_KEYS:
            return
        self._frame.call(self._begin, fx_key, duration, intensity)

    def _begin(self, fx_key, duration, intensity):
        if self.owner._closing or not self._built:
            return
        if fx_key == "flash":
            self._active.add("flash")
            self._show_layer()
            self._begin_flash(duration, intensity)
        elif fx_key == "shake":
            self._active.add("shake")
            self._show_layer()
            self._begin_shake(duration, intensity)
        else:
            self._active.add("overlay")
            self._show_layer()
            self._begin_overlay(fx_key, duration, intensity)

    @staticmethod
    def _peak_alpha(intensity):
        """强度 0~100 → 峰值不透明度：低强度保底可见，100 即完全不透明。"""
        return min(255, max(64, round(255 * (0.25 + 0.75 * intensity / 100.0))))

    # ---------- 闪光 ----------
    def _begin_flash(self, duration, intensity):
        peak = self._peak_alpha(intensity)
        frame = self._frame
        started = time.monotonic()

        def tick():
            if self.owner._closing:
                frame.cancel(_FLASH_TASK)
                return
            progress = (time.monotonic() - started) / max(0.01, duration)
            if progress >= 1.0:
                frame.cancel(_FLASH_TASK)
                dpg.configure_item(self._flash_rect, fill=(255, 255, 255, 0))
                self._end_effect("flash")
                return
            alpha = round(peak * (1.0 - progress))
            dpg.configure_item(self._flash_rect, pmin=[0, 0],
                               pmax=[self.owner._layout_w, self.owner._layout_h],
                               fill=(255, 255, 255, alpha))

        frame.every(0.0, tick, key=_FLASH_TASK)

    # ---------- 振动 ----------
    def _begin_shake(self, duration, intensity):
        s = getattr(self.owner, "_dpi_scale", 1.0)
        amp = (4.0 + 36.0 * intensity / 100.0) * s
        frame = self._frame
        started = time.monotonic()
        if not self._shake_base:
            # 上一次振动还没收尾时沿用旧基线：不能把「已偏移的位置」存成基线
            self._shake_base = {
                item: dpg.get_item_pos(item)
                for item in (dpg.get_item_children("main_window", 1) or [])
            }

        def tick():
            if self.owner._closing:
                frame.cancel(_SHAKE_TASK)
                self._shake_base = {}
                return
            elapsed = time.monotonic() - started
            progress = elapsed / max(0.01, duration)
            if progress >= 1.0:
                frame.cancel(_SHAKE_TASK)
                self._restore_shake()
                self._end_effect("shake")
                return
            decay = 1.0 - progress
            phase = 2.0 * math.pi * _SHAKE_HZ * elapsed
            dx = amp * decay * math.sin(phase)
            dy = 0.6 * amp * decay * math.cos(phase * 0.9)
            for item, pos in self._shake_base.items():
                dpg.set_item_pos(item, [round(pos[0] + dx), round(pos[1] + dy)])

        frame.every(0.0, tick, key=_SHAKE_TASK)

    def _restore_shake(self):
        for item, pos in self._shake_base.items():
            try:
                if dpg.does_item_exist(item):
                    dpg.set_item_pos(item, pos)
            except Exception:
                pass
        self._shake_base = {}

    # ---------- 反色 / 色差分离：帧快照覆盖层 ----------
    def _begin_overlay(self, fx_key, duration, intensity):
        peak = self._peak_alpha(intensity)
        s = getattr(self.owner, "_dpi_scale", 1.0)
        split_px = max(1, round((2.0 + 28.0 * intensity / 100.0) * s))
        # 清掉旧快照再抓帧：output_frame_buffer 的落盘时机是帧末，轮询等文件出现
        self._remove_snap()
        dpg.output_frame_buffer(file=self._snap_path)
        state = {"tries": 0}
        frame = self._frame

        def poll():
            if self.owner._closing:
                return
            if not os.path.exists(self._snap_path):
                state["tries"] += 1
                if state["tries"] > _SNAP_POLL_MAX:
                    process_log.log("[Fx] 帧快照抓取失败，退回处理当前背景")
                    self._worker.submit(lambda: self._prepare_overlay_image(
                        None, fx_key, duration, peak, split_px))
                    return
                frame.after(_SNAP_POLL_INTERVAL, poll, key=_SNAP_POLL_TASK)
                return
            self._worker.submit(lambda: self._prepare_overlay_image(
                self._load_snap(), fx_key, duration, peak, split_px))

        frame.after(_SNAP_POLL_INTERVAL, poll, key=_SNAP_POLL_TASK)

    def _load_snap(self):
        try:
            return Image.open(self._snap_path).convert("RGB")
        except Exception as exc:
            process_log.log(f"[Fx] 帧快照读取失败: {exc}")
            return None

    def _remove_snap(self):
        try:
            if os.path.exists(self._snap_path):
                os.remove(self._snap_path)
        except OSError:
            pass

    def _prepare_overlay_image(self, source, fx_key, duration, peak, split_px):
        """（工作者线程）把快照 / 背景加工成覆盖层图像并交回主线程上屏。"""
        try:
            img = self._process_fx_image(source, fx_key, split_px)
        except Exception as exc:
            process_log.log(f"[Fx] 视效图像处理失败: {exc}")
            self._frame.call(self._overlay_aborted)
            return
        self._remove_snap()
        if img is None:
            self._frame.call(self._overlay_aborted)
            return
        w, h = img.size
        data = np.frombuffer(img.tobytes(), dtype=np.uint8).astype(np.float32) / 255.0
        self._frame.call(self._show_overlay, data, w, h, duration, peak)

    def _process_fx_image(self, source, fx_key, split_px):
        layout_w = max(2, self.owner._layout_w)
        layout_h = max(2, self.owner._layout_h)
        if source is not None:
            img = source
        else:
            # 抓帧失败退回背景图；连背景都没有就放弃这次视效
            base = getattr(self.owner, "_bg_pil_original", None)
            if base is None:
                return None
            img = base.convert("RGB")
        if img.size != (layout_w, layout_h):
            img = img.resize((layout_w, layout_h), Image.Resampling.LANCZOS)
        if fx_key == "invert":
            out = ImageOps.invert(img)
        else:
            # 色差分离：R 左移、G 原位、B 右移，通道相加合成一张
            r, g, b = img.split()
            out = ImageChops.add(ImageChops.add(
                ImageChops.offset(r, -split_px, 0), g),
                ImageChops.offset(b, split_px, 0))
        return out.convert("RGBA")

    def _show_overlay(self, data, w, h, duration, peak):
        if self.owner._closing or not self._built:
            return
        if self._compose_buf is None:
            self._compose_buf = np.zeros(self._tex_w * self._tex_h * 4,
                                         dtype=np.float32)
        self._compose_buf.reshape(self._tex_h, self._tex_w, 4)[:h, :w] = \
            data.reshape(h, w, 4)
        dpg.set_value(self._texture, self._compose_buf)
        dpg.configure_item(self._overlay_image, show=True, pmin=[0, 0], pmax=[w, h],
                           uv_min=[0.0, 0.0],
                           uv_max=[w / max(1, self._tex_w), h / max(1, self._tex_h)],
                           color=(255, 255, 255, peak))
        frame = self._frame
        started = time.monotonic()

        def tick():
            if self.owner._closing:
                frame.cancel(_OVERLAY_TASK)
                return
            progress = (time.monotonic() - started) / max(0.01, duration)
            if progress >= 1.0:
                frame.cancel(_OVERLAY_TASK)
                dpg.configure_item(self._overlay_image, show=False,
                                   color=(255, 255, 255, 0))
                self._end_effect("overlay")
                return
            alpha = round(peak * (1.0 - progress))
            dpg.configure_item(self._overlay_image, color=(255, 255, 255, alpha))

        frame.every(0.0, tick, key=_OVERLAY_TASK)

    def _overlay_aborted(self):
        process_log.log("[Fx] 视效覆盖层已放弃（无可用画面）")
        self._end_effect("overlay")
