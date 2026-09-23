"""副本窗口的宿主端口（Host Port）。

``dungeon/window/`` 不再 import ``tkinter`` / ``customtkinter`` / ``ui.*``：
窗口对宿主的全部索取都收敛到本端口，共七个能力：

=======================  ==================================================
方法                      用途
=======================  ==================================================
``viewport_metrics()``    视口初始尺寸与 DPI 缩放（以宿主客户区为基准）
``hide_window()``         副本窗口打开时先藏起宿主，避免两个窗口同时占屏
``show_window()``         副本窗口关闭后恢复宿主
``pump_events()``         帧循环里处理一次宿主事件（宿主不冻结的关键，L0）
``dialog()``              收尾时的提示/询问（替代原先的 ``ui.common.dialogs``）
``open_replay_file()``    入口页"加载回放"的文件选择（L4：回放不再需要调用方
                          重开第二个窗口，因此这个文件对话框必须由宿主提供）
``register_active_window()`` 让宿主在整体退出时能找到活动副本窗口
``unregister_active_window()``
``default_font()``        段落字体缺省家族
=======================  ==================================================

实现：

- :class:`HostPort` —— 无宿主缺省实现（自动驾驶自检、将来换框架前的占位）；
- ``ui.common.tk_host.TkHost`` —— 当前 Tk/CTk 主程序（唯一真实宿主）。

换框架时只需再写一个适配器，window 层一行不用改；具体见
``docs/Dungeon/window_host.md`` §5.1/§3（L2）。
"""

import sys

#: ``dialog()`` 的 kind 取值
DIALOG_INFO = "info"
DIALOG_WARNING = "warning"
DIALOG_ERROR = "error"
DIALOG_ASK = "ask"

#: 无 UI 框架时的中文字族兜底。权威表在 ``ui/common/fonts.py``（UI 层），
#: 真实宿主直接委托给那份，这里只保证自检脚本里字体不缺席。
_PLATFORM_FALLBACK_FONTS = {
    "win32": "Microsoft YaHei",
    "darwin": "PingFang SC",
}


def _platform_key() -> str:
    if sys.platform.startswith("darwin"):
        return "darwin"
    if sys.platform.startswith("win"):
        return "win32"
    return "linux"


class HostPort:
    """宿主端口的缺省实现：什么都不做，但保证窗口能跑完。

    自动驾驶自检（``scripts/dungeon_autopilot.py``）就用它——不需要 Tk 主窗口，
    也不需要打桩对话框：提示类弹框打到标准输出，询问类弹框一律返回 False
    （与"用户选择不保存"等价，正是自检期望的分支）。
    """

    #: 询问类弹框的缺省回答（True/False）。自检或无人值守场景可覆盖。
    default_answer = False

    # ---------------- 尺寸与 DPI ----------------
    def viewport_metrics(self):
        """返回 ``(视口宽, 视口高, dpi_scale, 宿主客户区宽, 宿主客户区高)``。

        无宿主时退回 1280×720 @ 1.0（与原先 ``parent=None`` 的兜底一致）。
        """
        scale = 1.0
        if sys.platform.startswith("win"):
            try:
                import ctypes
                scale = ctypes.windll.user32.GetDpiForSystem() / 96.0
            except Exception:
                scale = 1.0
        scale = max(0.5, scale)
        main_cw, main_ch = round(1280 * scale), round(720 * scale)
        return (main_cw + round(16 * scale), main_ch + round(39 * scale),
                scale, main_cw, main_ch)

    # ---------------- 宿主窗口显隐 ----------------
    def hide_window(self):
        """副本窗口即将显示：隐藏宿主。"""

    def show_window(self):
        """副本窗口已关闭：恢复并前置宿主。"""

    # ---------------- 事件泵 ----------------
    def pump_events(self):
        """处理一次挂起的宿主事件。手动渲染的帧循环每帧调用一次。"""

    # ---------------- 弹框 ----------------
    def dialog(self, kind, title, message):
        """提示/询问。``ask`` 返回 bool，其余返回 None。"""
        print(f"[host:{kind}] {title}: {message}")
        if kind == DIALOG_ASK:
            return bool(self.default_answer)
        return None

    # ---------------- 回放文件选择 ----------------
    def open_replay_file(self):
        """选择并读取一个回放文件，返回记录列表；取消或不支持时返回 None。

        L4 之后"加载回放"在**同一个副本窗口内**完成（入口页选完文件直接切到回放
        阶段），所以这一步必须由宿主提供——window 层不能自己去碰 ``tkinter``。
        缺省实现返回 None，等价于"用户没有选文件"：入口页原地不动。
        """
        return None

    # ---------------- 活动窗口登记 ----------------
    def register_active_window(self, window):
        """登记活动副本窗口，供宿主在整体退出时停掉它。"""

    def unregister_active_window(self, window):
        """解除登记。"""

    # ---------------- 字体 ----------------
    def default_font(self) -> str:
        """段落字体缺省家族。返回空串表示交给 DPG 缺省字体。"""
        return _PLATFORM_FALLBACK_FONTS.get(_platform_key(), "Noto Sans CJK SC")
