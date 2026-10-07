"""挂件窗口「窗口置顶」的跨平台落地。

Windows / macOS 上 Tk 的 ``root.attributes("-topmost", ...)`` 就是正路；Linux/X11
上它**在部分桌面环境（实测 GNOME Shell）写不进 ``_NET_WM_STATE_ABOVE``，而且不报错**——
读回 ``attributes("-topmost")`` 也一直是 0，静默失效。EWMH 的 ClientMessage 才是这套
协议的正路，实现见 ``ui.common.x11``。

本模块把两件事收在一处：

- :func:`apply`：尽力把窗口置顶/取消置顶，并**回读确认**（X11 上读真实属性，
  其余平台以 Tk 自己报的状态为准——没有独立的读回通道）；
- :func:`available`：这套窗口管理能力不支持置顶时返回 False，挂件设置页据此把
  开关置灰并说明原因（见 ``ui/mini/screens.py``），而不是让用户点一个永远无效的开关。

设计约束：任何失败都静默降级（返回 ``applied=False``），置顶不是主链路功能。
"""

import sys

#: X11 置顶能力缓存：``None`` = 还没测出结论；``True`` = 实机确认过这套 WM 支持。
#: 不缓存 False——"这次没生效"可能只是窗口还没映射，别把结论钉死成"不支持"。
_X11_SUPPORTED = {"value": None}


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def apply(window, enabled: bool) -> bool:
    """把 ``window``（Tk 根窗口）置顶/取消置顶，返回**是否真的生效**。

    - Linux/X11：先按 Tk 的 ``-topmost`` 试（对 KWin 这类 WM 有效），再上 EWMH
      的 ``_NET_WM_STATE_ABOVE``，最后回读 ``_NET_WM_STATE`` 判断；
    - 窗口尚未映射（``winfo_ismapped()`` 为假）时无法回读，一律返回 False：调用方
      应在 ``<Map>`` 之后再调一次（``ui/mini/app.py`` 就是这么接的）。
    """
    if window is None:
        return False
    try:
        window.attributes("-topmost", bool(enabled))
    except Exception:
        pass

    if not _is_linux():
        # Windows / macOS：Tk 的实现是可信的，没有独立的回读通道。
        try:
            return bool(window.attributes("-topmost")) == bool(enabled)
        except Exception:
            return False

    try:
        from ui.common import x11
    except Exception:
        return False
    if not x11.x11_enabled():
        return False
    try:
        if not window.winfo_ismapped():
            return False
        window_id = x11.top_level_window(window.winfo_id())
        if not window_id:
            return False
        applied = x11.state_above(window_id, bool(enabled))
        if applied:
            _X11_SUPPORTED["value"] = True
        return applied
    except Exception:
        return False


def available() -> bool:
    """当前桌面环境是否具备置顶能力（供 UI 决定是否置灰开关）。

    - 非 Linux：True（Windows / macOS 一直可用）；
    - Linux/X11：以 EWMH ``_NET_SUPPORTED`` 里有没有 ``_NET_WM_STATE_ABOVE`` 为准。
      GNOME Shell + XWayland 实测支持；不支持时返回 False，设置页把开关置灰。
    """
    if not _is_linux():
        return True
    try:
        from ui.common import x11
    except Exception:
        return False
    if not x11.x11_enabled():
        return False
    return bool(x11.supports_above())
