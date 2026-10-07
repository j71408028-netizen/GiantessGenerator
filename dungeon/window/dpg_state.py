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

#: X11 错误码 → 人类可读文本。``XErrorEvent.error_code`` 在结构体首几个字段里，
#: ctypes 只需声明到它即可安全读取。
_X11_ERROR_CODES = {3: "BadWindow", 4: "BadPixmap", 8: "BadMatch",
                    9: "BadDrawable", 10: "BadAccess", 11: "BadAlloc"}

#: 预期内的陈旧窗口错误：只有这两个码走「忽略 + 记日志」。
#: ``BadMatch(8)`` 也曾出现在 GLFW 反复 destroy/create 的路径上，但它同样可能意味着
#: 真实配置错误，因此**故意不列入**预期集合——收窄的意义就在这里：不掩盖真实问题。
_EXPECTED_X11_ERRORS = (3, 9)  # BadWindow / BadDrawable


def install_x11_error_guard() -> bool:
    """安装一个 X11 同步错误处理器（仅 Linux/X11）。

    Dear PyGui/GLFW 与 Tk 共用 X server 时，宿主 Tk 的事件泵会收到并处理
    其它顶层窗口的旧事件；当这些窗口已经销毁，Tk 内部查询会触发 BadWindow，
    而 Xlib 默认处理器会直接 ``exit(1)``——表现为冒烟测试里的 X Error 硬崩。

    处理策略（2026-10-06 收窄）：
    - ``BadWindow`` / ``BadDrawable``：预期内的陈旧窗口错误，忽略并记入
      ``process_log``（前 3 次完整记录，之后只累加计数）；
    - 其它错误码：**照样返回 0（不退出进程），但每次大声记录**。这里不转发给
      Xlib 默认处理器——那个处理器是 ``_XError``，转发等于让进程退出，而段错误
      压测的价值恰在于「跑完并留下证据」。要恢复默认行为就删掉本函数的所有
      调用点（风险登记见 docs/linux.md 附录A §5）。

    重复调用会重新覆盖当前处理器，防止后续 GLFW/DPG 生命周期把它换掉。
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

            class XErrorEvent(ctypes.Structure):
                """Xlib ``XErrorEvent``：只需读到 ``error_code`` / ``request_code``。"""

                _fields_ = [
                    ("type", ctypes.c_int),
                    ("display", ctypes.c_void_p),
                    ("resourceid", ctypes.c_ulong),
                    ("serial", ctypes.c_ulong),
                    ("error_code", ctypes.c_ubyte),
                    ("request_code", ctypes.c_ubyte),
                    ("minor_code", ctypes.c_ubyte),
                ]

            handler_type = ctypes.CFUNCTYPE(
                ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(XErrorEvent))
            state = {"count": 0, "unexpected": 0}

            def _handle(display, event):
                code = int(event.contents.error_code) if event else 0
                request = int(event.contents.request_code) if event else 0
                if code in _EXPECTED_X11_ERRORS:
                    state["count"] += 1
                    if state["count"] <= 3:
                        _log_x11(f"已忽略预期的陈旧窗口错误（{_code_label(code)}，"
                                 f"request={request}，第 {state['count']} 次）")
                    return 0
                state["unexpected"] += 1
                if state["unexpected"] <= 3:
                    _log_x11(f"未预期的 X11 错误：{_code_label(code)}"
                             f"（request={request}）——若在冒烟中反复出现请上报",
                             error=True)
                else:
                    _log_x11(f"未预期的 X11 错误累计 {state['unexpected']} 次"
                             f"（最近：{_code_label(code)}）", error=True)
                return 0

            handler = handler_type(_handle)
            _X11_ERROR_GUARD = (x11, handler, state, XErrorEvent)

        x11, handler, _state, _event_type = _X11_ERROR_GUARD
        x11.XSetErrorHandler.argtypes = [ctypes.c_void_p]
        x11.XSetErrorHandler.restype = ctypes.c_void_p
        x11.XSetErrorHandler(ctypes.cast(handler, ctypes.c_void_p))
        return True
    except Exception as e:
        print(f"[Warning] 安装 X11 错误兼容处理器失败: {e}")
        return False


def _code_label(code: int) -> str:
    """错误码 → ``BadWindow(3)`` 这样的标签。"""
    return f"{_X11_ERROR_CODES.get(code, 'XError')}({code})"


def _log_x11(message: str, error: bool = False) -> None:
    """把 X11 兼容层的事件记入过程日志（不可用则退回 print）。"""
    try:
        from dungeon import process_log
        process_log.log("[X11] " + message)
    except Exception:
        if error:
            print("[X11] " + message)


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
