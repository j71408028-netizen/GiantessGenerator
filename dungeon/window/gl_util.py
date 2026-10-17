"""整帧像素抓取：``glReadPixels`` 的最小 ctypes 绑定。

为什么不用 ``hello_imgui.final_app_window_screenshot()``：P5 实测
（``.workbuddy/p5_probe/probe_grab.py``）它在**帧内**返回空数组 ``(0, 0, 3)``，
只有 ``before_exit`` 那个钩子里才有内容——那是"退出时留一张遗照"的接口，不是
「F2 截图」要的东西。

为什么不用 PyOpenGL：本仓库的依赖表里没有它，而这里只需要一个函数。ctypes 绑
``opengl32.dll`` / ``libGL`` 是零新增依赖的做法，且只在**换缓冲之后、GL 上下文
仍然当前**时调用（``RunnerParams.callbacks.after_swap``），实测拿到的是刚画完的
整帧（同一次探针里 ``glGetString(GL_VERSION)`` 也是有效的）。

读出的像素是 GL 口径：原点在**左下**、通道序 **RGBA**（``GL_RGBA`` + 后端
``display_framebuffer_scale`` 已由 hello_imgui 处理，见 P0-S3 §2）。因此这里
返回前统一翻成图像行序（左上原点），调用方直接当 PIL/numpy 图用。
"""

import ctypes
import sys

#: GL 枚举
GL_RGBA = 0x1908
GL_UNSIGNED_BYTE = 0x1401
GL_VERSION = 0x1F02

_lib = None
_read_pixels = None
_get_string = None
_load_failed = False


def _load():
    """按平台加载 GL 函数表（只做一次；失败后不再重试）。"""
    global _lib, _read_pixels, _get_string, _load_failed
    if _read_pixels is not None or _load_failed:
        return _read_pixels is not None
    try:
        if sys.platform.startswith("win"):
            _lib = ctypes.WinDLL("opengl32.dll")
            proto = ctypes.WINFUNCTYPE
        else:
            # 注意：这里**不能**写 ``import ctypes.util``——那会把函数作用域里的
            # ``ctypes`` 变成一个局部名字，后面每处 ``ctypes.c_int`` 都会
            # ``UnboundLocalError``（这个坑在本模块身上真踩过一次）。
            import ctypes.util as _ctypes_util
            name = ("libGL.so.1" if sys.platform.startswith("linux")
                    else (_ctypes_util.find_library("OpenGL") or "libGL.dylib"))
            _lib = ctypes.CDLL(name)
            proto = ctypes.CFUNCTYPE
        _read_pixels = proto(
            None, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p)(
            ("glReadPixels", _lib))
        _get_string = proto(ctypes.c_char_p, ctypes.c_uint)(("glGetString", _lib))
        return True
    except Exception:
        _lib = None
        _load_failed = True
        return False


def available() -> bool:
    """GL 函数表是否可用（自检/探针用；不可用时抓帧一律返回 ``None``）。"""
    return _load()


def gl_version() -> str:
    """当前上下文的 ``GL_VERSION``（诊断用；无上下文时返回空串）。"""
    if not _load() or _get_string is None:
        return ""
    try:
        raw = _get_string(GL_VERSION)
    except Exception:
        return ""
    return raw.decode("ascii", "replace") if raw else ""


def read_pixels(width, height):
    """读当前后备缓冲的 ``width × height`` 区域。

    返回 ``(height, width, 4)`` 的 ``numpy.uint8``（行序已翻正），失败返回
    ``None``。**只能在 GL 上下文当前的线程上调用**（本程序 = 帧线程的
    ``after_swap``）。``numpy`` 延迟导入：本模块要能在没有 numpy 的离线守卫里被
    导入。
    """
    width, height = int(width), int(height)
    if width <= 0 or height <= 0 or not _load():
        return None
    import numpy as np
    buf = np.empty((height, width, 4), dtype=np.uint8)
    _read_pixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE,
                 buf.ctypes.data_as(ctypes.c_void_p))
    return buf[::-1].copy()


__all__ = ["available", "gl_version", "read_pixels"]
