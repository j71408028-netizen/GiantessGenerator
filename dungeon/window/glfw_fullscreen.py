"""整屏切换（F11）：直接对 GLFW 窗口调 ``glfwSetWindowMonitor``。

## 为什么不用 ``window_geometry.full_screen_mode``

P1 的 ``WindowRunner.set_fullscreen`` 走的是「改 ``window_geometry.full_screen_mode``
+ ``resize_app_window_at_next_frame``」。P5 实测（``.workbuddy/p5_probe/probe_dpi2.py``）
**这条路在 1.92.900 上是坏的**：

    frame 2: display=2240x1260            （窗口态，符合请求）
    请求 full_monitor_work_area
    frame 12: display=420x34              ← 退化成一个 420×34 的窗口
    请求 no_full_screen
    frame 18: display=420x34              ← 回不去了

评估阶段写的 ``glfw_utils.GlfwHelpers`` 在 1.92.900 不存在（要 ``pip install glfw``），
而 ``hello_imgui.change_window_size()`` 只是"改客户区尺寸"，没有边框/置顶/还原位置，
不是全屏。**但是**：``hello_imgui.get_glfw_window_address()`` 是公开 API，而
imgui_bundle 的安装目录里就带着 ``glfw3.dll``——不需要新增任何依赖，用 ctypes 绑
那几个函数就能把 hello_imgui 自己的那个 GLFW 窗口切成真全屏。

实测（``.workbuddy/p5_probe/probe_glfw_fs.py``，2560×1600@165 显示器）：

    base:       display=2240x1260  glfw size=(2240,1260) pos=(202,176)
    进全屏后:    display=2560x1600  glfw size=(2560,1600) pos=(0,0)
    还原后:      display=2240x1260  glfw size=(2240,1260) pos=(202,176)   ← 位置也回来了

## 口径

- 只调用**我们自己的** ctypes 函数，不改 GLFW 的错误回调（多线程下那会踩
  ``glfwSetErrorCallback`` 的线程局部状态）；失败靠返回值判空 + 事后尺寸复核。
- 进全屏前记下窗口位置与尺寸，退出全屏时原样 ``glfwSetWindowMonitor(None, ...)``
  还原——GLFW 自己不会帮忙记。
- 全屏期间的分辨率用当前显示器的 ``GLFWvidmode``（桌面分辨率），
  刷新率也传它，避免切到不存在的模式。
- 加载不到 ``glfw3.dll`` 时 ``available`` 为假，调用方应给出提示而**不是**
  静默假装成功（这正是评估阶段 F11 『看起来成功其实没切』的老坑）。
"""

import ctypes
import os
import sys

# 显示器：直接用 ``glfwGetPrimaryMonitor``。副本窗口一直开在主显示器上（宿主
# 客户区就是主窗口的尺寸），按"窗口所在显示器"取需要额外比对窗口位置与各显示器
# 矩形，收益为零。


class _GlfwVidMode(ctypes.Structure):
    _fields_ = [("width", ctypes.c_int), ("height", ctypes.c_int),
                ("red_bits", ctypes.c_int), ("green_bits", ctypes.c_int),
                ("blue_bits", ctypes.c_int), ("refresh_rate", ctypes.c_int)]


class GlfwFullscreen:
    """对 hello_imgui 的 GLFW 窗口做整屏切换。进程内单例即可（``FULLSCREEN``）。"""

    def __init__(self):
        self._lib = None
        self._win = None
        #: 进全屏前记下的 ``(x, y, w, h)``，退出全屏时还原
        self._saved = None
        self._is_fullscreen = False
        self._load()

    # ---------------- 加载 ----------------
    def _load(self):
        lib = None
        for cand in self._candidates():
            try:
                lib = (ctypes.WinDLL(cand) if sys.platform.startswith("win")
                       else ctypes.CDLL(cand))
                break
            except OSError:
                continue
        if lib is None:
            return
        try:
            lib.glfwGetPrimaryMonitor.restype = ctypes.c_void_p
            lib.glfwGetPrimaryMonitor.argtypes = []
            lib.glfwGetVideoMode.restype = ctypes.POINTER(_GlfwVidMode)
            lib.glfwGetVideoMode.argtypes = [ctypes.c_void_p]
            lib.glfwGetWindowPos.argtypes = [ctypes.c_void_p,
                                             ctypes.POINTER(ctypes.c_int),
                                             ctypes.POINTER(ctypes.c_int)]
            lib.glfwGetWindowSize.argtypes = lib.glfwGetWindowPos.argtypes
            lib.glfwSetWindowMonitor.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                                 ctypes.c_int, ctypes.c_int,
                                                 ctypes.c_int, ctypes.c_int,
                                                 ctypes.c_int]
        except AttributeError:
            return
        self._lib = lib

    @staticmethod
    def _candidates():
        """``glfw3.dll`` 的位置：imgui_bundle 安装目录（随 wheel 一起发布）。"""
        names = []
        try:
            import imgui_bundle
            pkg = os.path.dirname(os.path.abspath(imgui_bundle.__file__))
            names.extend([os.path.join(pkg, "glfw3.dll"),
                          os.path.join(pkg, "libglfw.so.3"),
                          os.path.join(pkg, "libglfw.3.dylib")])
        except Exception:
            pass
        names.extend(["glfw3.dll", "glfw.dll", "libglfw.so.3", "libglfw.3.dylib"])
        return names

    @property
    def available(self) -> bool:
        """``glfw3.dll`` 与 GLFW 窗口都拿得到时才为真。"""
        return self._lib is not None and self._window() is not None

    def _window(self):
        """当前 GLFW 窗口指针（每次现取：窗口在 ``hello_imgui.run()`` 里才创建）。"""
        if self._lib is None:
            return None
        try:
            from imgui_bundle import hello_imgui
            addr = hello_imgui.get_glfw_window_address()
        except Exception:
            return None
        if not addr:
            return None
        return ctypes.c_void_p(addr)

    # ---------------- 状态 ----------------
    @property
    def is_fullscreen(self) -> bool:
        return self._is_fullscreen

    def _window_rect(self):
        win = self._window()
        if win is None:
            return None
        x, y = ctypes.c_int(), ctypes.c_int()
        w, h = ctypes.c_int(), ctypes.c_int()
        try:
            self._lib.glfwGetWindowPos(win, ctypes.byref(x), ctypes.byref(y))
            self._lib.glfwGetWindowSize(win, ctypes.byref(w), ctypes.byref(h))
        except Exception:
            return None
        return (x.value, y.value, w.value, h.value)

    # ---------------- 切换 ----------------
    def toggle(self):
        """切换整屏，返回新的整屏状态（不可用时返回 ``None``）。

        只在帧回调里调用（GLFW 窗口函数与窗口系统同线程）。
        """
        return self.set_fullscreen(not self._is_fullscreen)

    def set_fullscreen(self, enabled):
        """请求整屏开/关；返回**实际生效**的状态，不可用时返回 ``None``。"""
        win = self._window()
        if win is None:
            return None
        if bool(enabled) == self._is_fullscreen:
            return self._is_fullscreen
        rect = self._window_rect()
        if rect is None:
            return None
        if enabled:
            monitor = self._lib.glfwGetPrimaryMonitor()
            if not monitor:
                return None
            mode = self._lib.glfwGetVideoMode(monitor)
            if not mode:
                return None
            mode = mode.contents
            self._saved = rect
            try:
                self._lib.glfwSetWindowMonitor(win, monitor, 0, 0, mode.width,
                                               mode.height, mode.refresh_rate)
            except Exception:
                self._saved = None
                return None
        else:
            x, y, w, h = self._saved or rect
            try:
                self._lib.glfwSetWindowMonitor(win, None, x, y, w, h, 0)
            except Exception:
                return None
        # 复核：GLFW 不抛异常也不代表窗口系统接受了这次切换（P1 的 geometry
        # 路径就是这样"成功"地留下了一个 420×34 的窗口）。
        now = self._window_rect()
        if now is None:
            return None
        self._is_fullscreen = bool(enabled)
        if bool(enabled) and now[2] <= 1:
            self._is_fullscreen = False
        return self._is_fullscreen


#: 进程内单例：窗口系统只有一个，状态也只有一份。
FULLSCREEN = GlfwFullscreen()

__all__ = ["FULLSCREEN", "GlfwFullscreen"]
