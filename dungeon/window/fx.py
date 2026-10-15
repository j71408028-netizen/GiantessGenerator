"""窗口视效：闪光 / 振动 / 反色 / 色差分离（**P5 待迁移**）。

与「章节效果覆盖」（PIL 重采样背景滤镜）相对：这组视效操作**整个会话窗口**——
覆盖层盖住全窗、内容整体位移——并以**绝对时间**（秒）消退，与步进无关。
触发即播放：派发后立即返回，视效期间流式生成、音频播放、切换背景等一切
动作照常，不存在"等待视效结束"的路径。

## 迁移状态（P1 占位）

DPG 版的这一层靠 ``add_dynamic_texture`` + 透明子窗 + ``draw_image`` +
``output_frame_buffer`` 抓帧实现，imgui 侧没有一一对应物：

- 闪光：可以走 ``get_window_draw_list().add_rect_filled`` 的全窗矩形（简单）；
- 振动：DPG 靠移动主窗口的全部直接子项实现，imgui 没有"移动整个窗口内容"的概念，
  要么整体平移绘制坐标，要么改 viewport 位置——需要重新设计；
- 反色 / 色差：需要 ``glReadPixels`` 抓当前帧缓冲（要后端指针），再上传 ``TextureGpu``。

因此本层整体留到 **P5** 重写。P1 只保留公开 API 与收尾，保证调用链不崩：
``base.__init__`` 建实例、``ui._setup_imgui`` 调 ``build``、``ui._relayout`` 调
``relayout``、``base._finish_session`` 调 ``shutdown``、``triggers`` 调 ``start``。

``parse_fx_params`` 是纯函数，被 ``dungeon/window/triggers.py`` 与
``tests/check_trigger_timing.py`` 依赖，**原样保留**。
"""

import os
import tempfile

from dungeon.actions import WINDOW_FX_KEYS
from dungeon.window.background import PixelWorker

#: 各视效的帧任务 key（P5 会用；P1 只在收尾时统一取消，确保不留残留任务）
_FLASH_TASK = "fx:flash"
_SHAKE_TASK = "fx:shake"
_OVERLAY_TASK = "fx:overlay"
_SNAP_POLL_TASK = "fx:snapshot"


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
    """会话窗口的瞬时视效层（P1 占位：只留 API 与收尾）。

    ``__init__`` 仍然建好像素工作者与临时快照路径，``shutdown`` 仍然把它们收干净，
    因此 P1 的会话收尾与迁移前一样不留线程 / 临时文件；只是 ``build`` /
    ``relayout`` / ``start`` 在 P5 之前不做任何绘制。
    """

    def __init__(self, owner):
        self.owner = owner
        #: 视效层是否已建（P1 恒为 False：没有层可画）
        self._built = False
        #: 像素工作者：反色 / 色差的重活线程（P5 会用；P1 起好即收）
        self._worker = PixelWorker(name="dungeon-fx-worker")
        #: 进行中的视效种类（P5 用；P1 恒空）
        self._active = set()
        #: 帧快照落盘路径（临时文件，用完即删）
        self._snap_path = os.path.join(
            tempfile.gettempdir(), f"dungeon_fx_snap_{os.getpid()}.png")

    @property
    def _frame(self):
        return self.owner._frame

    # ---------- 构建 / 重排 / 收尾 ----------
    def build(self, *args, **kwargs):
        """P5 占位：imgui 侧的视效层尚未实现（P1 调用不建任何东西）。"""
        self._built = False

    def relayout(self, w, h):
        """P5 占位：视效层几何跟随（P1 没有层，无操作）。"""

    def shutdown(self):
        """会话收尾：撤掉帧任务与像素工作者，清掉临时快照。"""
        for key in (_FLASH_TASK, _SHAKE_TASK, _OVERLAY_TASK, _SNAP_POLL_TASK):
            self._frame.cancel(key)
        self._active.clear()
        self._worker.shutdown()
        self._remove_snap()

    # ---------- 启动 ----------
    def start(self, fx_key, duration, intensity):
        """启动一个窗口视效（任意线程可调）。

        P1 占位：只做合法性判定后返回，不绘制、也不排队——调用方（``triggers``）
        的契约"派发后立即返回、不阻塞"因此保持不变。P5 会把真正的绘制接进来。
        """
        if fx_key not in WINDOW_FX_KEYS:
            return
        if self.owner._closing or not self._built:
            return

    def _remove_snap(self):
        try:
            if os.path.exists(self._snap_path):
                os.remove(self._snap_path)
        except OSError:
            pass
