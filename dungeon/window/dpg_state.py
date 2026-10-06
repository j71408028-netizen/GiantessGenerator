"""dearpygui 全局上下文的状态标志与「保活视口」生命周期。

DPG 的上下文是进程级单例，且它的 C 扩展在**从未创建过上下文**时调用
``is_dearpygui_running()`` 会直接让进程段错误——不是抛异常，``try/except``
挡不住。而界面层（``ui.exploration`` 等）为了让副本窗口能随取随用，会在
import 期就把 dearpygui 拉进 ``sys.modules``，于是「模块已导入」完全不能
当作「上下文已创建」来判断。

因此这里单独记住上下文的真实状态：``dungeon/window/ui.py`` 建上下文后置位，
``dungeon/window/base.py`` 销毁后清除。需要清理上下文的调用方先看这个标志，
为假就一个 DPG 函数都不碰。

``was_created()`` 是**只增不减**的补充：本进程是否建过上下文。销毁上下文会
顺带终止 GLFW，而实测那会让 Tk 根窗口失去窗口级命令能力（详见下）。

## 保活视口（park）——为什么上下文不能就这么销毁了事

实测（2026-09-28，详见 ``.workbuddy/memory/2026-09-28.md``）：

- ``dpg.destroy_context()`` 会终止 GLFW。此后**当时存在的那个** Tk 根窗口就再也
  不能销毁/隐藏——``root.destroy()`` / ``root.withdraw()`` 与原生 ``ShowWindow``
  一律硬崩（0xC0000005 / 0xC000041D），没有 traceback；``quit()`` / ``geometry()``
  / ``attributes('-alpha')`` / ``winfo_*()`` / ``update()`` 仍正常。
- 更麻烦的是**之后新建**的 Tk 根窗口也不干净：纯 ``tk.Tk`` 还能用，但
  ``customtkinter.CTk`` 根的首次显示（``deiconify()``）同样硬崩——热切换界面
  正好要「销毁旧根 → 新建另一套根」，于是既切不过去，切过去也建不起来。
- 反过来，只要进程里存在一个**活的 DPG 上下文 + 视口**（哪怕从不
  ``show_viewport``、对使用者完全不可见），上面两条都不发生：Tk 根窗口一切正常。

所以会话收尾在 ``destroy_context()`` 之后立刻 :func:`park_context`，把进程留在这个
「健康」状态；下一次会话开头（宿主隐藏之后）用 :func:`unpark_context` 把它拆掉
让位——DPG 的上下文与视口都是单例，两局之间必须一拆一建。
"""

import sys

#: 保活视口的标题（从不显示，仅用于人工排查时辨认，如按标题 FindWindowW）
PARK_TITLE = "GiantessKeepAlive"

_STATE = {"alive": False, "ever_created": False}

#: Linux/X11 兼容处理器：Tk 与 DPG/GLFW 同进程时，Tk 可能在事件循环里
#: 处理到别家已销毁窗口的事件，Xlib 默认错误处理器会直接把进程结束。
#: 这里只用于抑制这类预期内的陈旧窗口错误；处理器本身必须保持强引用。
_X11_ERROR_GUARD = None


def install_x11_error_guard() -> bool:
    """安装一个宽松的 X11 同步错误处理器（仅 Linux/X11）。

    Dear PyGui/GLFW 与 Tk 共用 X server 时，宿主 Tk 的事件泵会收到并处理
    其它顶层窗口的旧事件；当这些窗口已经销毁，Tk 内部查询会触发 BadWindow，
    而 Xlib 默认处理器会直接 ``exit(1)``——表现为冒烟测试里的 X Error 硬崩。
    这是一个兼容性保护，不是业务错误处理；重复调用会重新覆盖当前处理器，
    防止后续 GLFW/DPG 生命周期把它换掉。
    """
    global _X11_ERROR_GUARD
    if not sys.platform.startswith("linux"):
        return False
    try:
        import ctypes
        import ctypes.util

        if _X11_ERROR_GUARD is None:
            lib_name = ctypes.util.find_library("X11") or "libX11.so.6"
            x11 = ctypes.CDLL(lib_name)
            handler_type = ctypes.CFUNCTYPE(
                ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
            state = {"count": 0}

            def _handle(display, event):
                state["count"] += 1
                if state["count"] <= 3:
                    try:
                        from dungeon import process_log
                        process_log.log(
                            "[X11] 已忽略预期的陈旧窗口错误"
                            f"（第 {state['count']} 次）")
                    except Exception:
                        pass
                return 0

            handler = handler_type(_handle)
            _X11_ERROR_GUARD = (x11, handler, state)

        x11, handler, _state = _X11_ERROR_GUARD
        x11.XSetErrorHandler.argtypes = [ctypes.c_void_p]
        x11.XSetErrorHandler.restype = ctypes.c_void_p
        x11.XSetErrorHandler(handler)
        return True
    except Exception as e:
        print(f"[Warning] 安装 X11 错误兼容处理器失败: {e}")
        return False


def is_alive() -> bool:
    """当前是否存在已创建的 DPG 上下文（含保活视口）。"""
    return _STATE["alive"]


def was_created() -> bool:
    """本进程是否建过 DPG 上下文（上下文销毁后仍为真）。

    为真意味着：GLFW 已被初始化过、也可能已被收尾终止过；此时 Tk 根窗口是否
    健康取决于当前有没有活着的上下文（见 :func:`park_context`）。
    """
    return _STATE["ever_created"]


def mark_created() -> None:
    """``dpg.create_context()`` 成功后调用。"""
    _STATE["alive"] = True
    _STATE["ever_created"] = True


def mark_destroyed() -> None:
    """``dpg.destroy_context()`` 之后调用（无论成功与否）。"""
    _STATE["alive"] = False


def park_context() -> bool:
    """建一个**从不显示**的保活视口，把进程留在「Tk 根窗口健康」的状态。

    调用点：副本会话收尾 ``destroy_context()`` 之后（``base._finish_session``），
    以及热切换的兜底修复之后（``app_shell._revive_dpg_for_teardown``）。

    已经有活着的上下文时直接返回 True（幂等）。返回 False 表示这次没修好——
    调用方若正要销毁/切换界面，得走自己的兜底路径。
    """
    if is_alive():
        return True
    try:
        import dearpygui.dearpygui as dpg
        dpg.create_context()
        # 只建视口、不 show_viewport：原生窗口在 show 之前根本不会被创建，
        # 所以对使用者完全不可见，也不占桌面。
        dpg.create_viewport(title=PARK_TITLE, width=64, height=48)
        dpg.setup_dearpygui()
        install_x11_error_guard()
    except Exception as e:
        print(f"[Warning] 建立 DPG 保活视口失败: {e}")
        return False
    mark_created()
    return True


def unpark_context() -> bool:
    """拆掉保活视口（若有），为下一局副本让出 DPG 单例。

    调用点：副本会话开头、**宿主隐藏之后**（``base._start_session``）。顺序不能反：
    拆它会终止 GLFW，此前可见的那个 Tk 根窗口随即失去窗口级命令能力，
    ``host.hide_window()``（``withdraw``）会硬崩。

    返回是否真的拆掉了一个（本来就没有活着的上下文时返回 False）。
    """
    if not is_alive():
        return False
    try:
        import dearpygui.dearpygui as dpg
        if dpg.is_dearpygui_running():
            dpg.stop_dearpygui()
        dpg.destroy_context()
    except Exception as e:
        print(f"[Warning] 拆除 DPG 保活视口失败: {e}")
    finally:
        mark_destroyed()
    return True
