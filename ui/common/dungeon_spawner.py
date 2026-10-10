# -*- coding: utf-8 -*-
"""把副本会话搬进独立子进程运行（父进程侧）。

调用方拿到与 ``DungeonSessionWindow(...).run()`` 完全同形的返回值::

    result = launch_dungeon_subprocess(
        host_window=self.app.root, app=self.app, dialogs=ui.common.dialogs,
        name=...,              # ……与窗口构造相同的其余参数
        settings=self.app.settings)
    if result.failed:
        ui.common.dialogs.showerror("错误", result.launch_error)

为什么搬进程：副本的 DPG/GLFW 与 Tk 同进程共存时共享线程消息队列、GLFW
单例与 X11 连接，由此长出一整套同进程补救机器（保活视口 ``park_context``、
``WM_QUIT`` 残留清理、视口僵尸窗口销毁、热切换兜底修复……）。会话独立成
子进程后，``destroy_context()`` 终止的是子进程自己的 GLFW，父进程的 Tk 从此
不再接触 DPG；子进程崩溃也不再带走整个应用。

职责（全部收在本模块，调用方无感知）：

1. **spawn**：构造参数经 ``dungeon.window.launch_payload.serialize_launch``
   序列化后，随视口尺寸一起经 ``multiprocessing.Process``（``_child_main``）
   传给子进程（``ui.common.dungeon_child_host.run_dungeon_host``）；
2. **代问**：子进程的收尾弹框（``dialog``）与回放文件选择
   （``open_replay_file``）以协议请求送回，用调用方的 ``dialogs`` 实现与
   Tk 文件框代答——子进程内因此没有任何 Tk 代码；
3. **泵循环**：等待期间反复 ``host_window.update()`` 维护主窗口事件（收尾
   弹框弹出前子进程会先发 ``show_window`` 请求，父窗口恢复后弹框才有父级）；
4. **回收**：结果 dict 还原成 :class:`~dungeon.window.result.SessionResult`；
   子进程没有送回结果（崩溃/被杀）时按启动失败兜底；
5. **登记**：会话期间把进程句柄登记到 ``app._active_dungeon_window``——
   主窗口关闭/切换界面时的「副本进行中」守卫与通知路径照旧工作，只是
   ``request_close()`` 现在变成「请子进程收尾」。

协议见 ``ui.common.dungeon_child_host`` 模块说明。
"""

import multiprocessing
import queue
import threading
import time

from dungeon.window.host import (DIALOG_ASK, DIALOG_ERROR, DIALOG_INFO,
                                 DIALOG_WARNING)
from dungeon.window.launch_payload import (result_from_dict, result_to_dict,
                                           serialize_launch)
from dungeon.window.result import (REASON_ALREADY_RUNNING, REASON_LAUNCH_FAILED,
                                   SessionResult)

#: 一次只允许一个副本子进程（对齐旧的 ``DungeonWindowBase._session_running``：
#: 等待循环会泵宿主事件，期间用户的「进入副本」回调可能重入）
_ACTIVE = None


def _child_main(conn, payload):
    """multiprocessing 拉起的子进程入口（必须模块级，供 spawn pickle）。"""
    from ui.common.dungeon_child_host import run_dungeon_host

    run_dungeon_host(conn, payload)


class DungeonProcessHandle:
    """活动副本的句柄：登记在 ``app._active_dungeon_window`` 上的东西。

    老代码在这里拿到的是窗口实例并调 ``window.request_close()``；现在换成
    本句柄，同名方法转成协议请求——主窗口关闭路径（``on_closing``）与挂件
    的退出路径因此一行不改。
    """

    def __init__(self, conn, proc, send_lock):
        self._conn = conn
        self._proc = proc
        self._send_lock = send_lock

    def request_close(self):
        """请子进程走帧边界收尾（父进程随后退出时管道断开，子进程必然终结）。"""
        try:
            with self._send_lock:
                self._conn.send({"req": "close"})
        except Exception:
            try:
                self._proc.terminate()
            except Exception:
                pass


def launch_dungeon_subprocess(host_window, app=None, dialogs=None,
                              **window_kwargs) -> SessionResult:
    """在独立子进程里跑一局副本，返回与窗口 ``run()`` 同形的结果。

    ``host_window``：主窗口（Tk/CTk 根）——spawn 前量视口尺寸并藏起、等待期
    泵事件、收尾时恢复。``app``：持有 ``_active_dungeon_window`` 的主控对象
    （``MainWindowManager`` / ``MiniApp``），仅用于会话期登记，可省略。
    ``dialogs``：收尾弹框实现（需 ``showinfo/showwarning/showerror/askyesno``），
    缺省用 ``ui.common.dialogs``。其余关键字参数与 ``DungeonSessionWindow``
    构造参数一致（``parent`` / ``host`` / ``gui`` / 两个仓库会被序列化剔除，
    子进程自行提供）。
    """
    global _ACTIVE

    if _ACTIVE is not None:
        return SessionResult(REASON_ALREADY_RUNNING)
    if dialogs is None:
        import ui.common.dialogs as dialogs

    # 视口尺寸要趁主窗口**仍可见**时量（隐藏后各平台结果不一致，见 TkHost）。
    from ui.common.tk_host import TkHost

    viewport_w, _vh, scale, main_cw, main_ch = TkHost(host_window).viewport_metrics()
    payload = {
        "window": serialize_launch(window_kwargs),
        "viewport": {"scale": scale, "main_cw": main_cw, "main_ch": main_ch},
    }

    # 藏起主窗口（对齐旧 TkHost.hide_window 的时机：子进程显示视口之前）
    _hide_window(host_window)

    send_lock = threading.Lock()
    conn_parent, conn_child = multiprocessing.Pipe(duplex=True)
    proc = multiprocessing.Process(target=_child_main, args=(conn_child, payload),
                                   daemon=True, name="dungeon-session")
    proc.start()
    conn_child.close()

    handle = DungeonProcessHandle(conn_parent, proc, send_lock)
    _ACTIVE = handle
    owner = app if app is not None else getattr(host_window, "app", None)
    registered = _register(owner, handle)
    try:
        result_data = _wait_loop(host_window, dialogs, conn_parent, proc,
                                 send_lock)
    finally:
        _ACTIVE = None
        if registered:
            _register(owner, None)
        _show_window(host_window)
        try:
            conn_parent.close()
        except Exception:
            pass

    result = result_from_dict(result_data)
    if result_data is None and proc.exitcode not in (0, None):
        result = SessionResult(
            REASON_LAUNCH_FAILED,
            launch_error=f"副本子进程异常退出（退出码 {proc.exitcode}），"
                         "详情见控制台输出。")
    return result


# ==================== 等待循环 ====================

def _wait_loop(host_window, dialogs, conn, proc, send_lock):
    """泵宿主事件 + 代答协议请求，直到拿到结果 / 子进程退出 / 宿主窗口销毁。

    返回结果 dict；子进程没送回结果时返回 ``None``（由调用方按退出码兜底）。
    """
    messages = queue.Queue()

    def _reader():
        try:
            while True:
                messages.put(conn.recv())
        except Exception:
            messages.put(None)   # 管道断开哨兵（父进程退出 / 子进程死亡）

    threading.Thread(target=_reader, daemon=True,
                     name="dungeon-spawn-reader").start()

    result_data = None
    pipe_open = True
    while True:
        # 泵一次宿主事件：主窗口在副本运行期间保持可响应（收尾弹框的父级、
        # 会话期登记的关闭守卫都依赖这条循环活着）。
        try:
            host_window.update()
        except Exception:
            # 宿主窗口被销毁（正常流程不会走到：会话期有守卫挡住切换/关闭）。
            try:
                proc.terminate()
            except Exception:
                pass
            return None
        time.sleep(0.01)

        while True:
            try:
                msg = messages.get_nowait()
            except queue.Empty:
                break
            if msg is None:
                pipe_open = False
                break
            if not isinstance(msg, dict):
                continue
            if "result" in msg:
                return msg["result"]
            if msg.get("req") == "show_window":
                _show_window(host_window)
                continue
            if msg.get("req") == "dialog":
                _answer_dialog(conn, send_lock, dialogs, msg)
                continue
            if msg.get("req") == "open_replay_file":
                data = _read_replay_file(host_window, dialogs)
                _send(conn, send_lock, {"reply": msg.get("id"), "answer": data})
                continue

        if result_data is not None or not pipe_open:
            # 管道断开后最后捞一遍队列：结果可能与 EOF 挨着到达
            try:
                msg = messages.get_nowait()
            except queue.Empty:
                msg = None
            if isinstance(msg, dict) and "result" in msg:
                return msg["result"]
            if not pipe_open:
                proc.join(timeout=2.0)
                return None


def _answer_dialog(conn, send_lock, dialogs, msg):
    """用调用方的弹框实现代答一次 ``dialog`` 请求。

    弹框是模态的：期间本循环停住、副本画面冻结——与旧架构里收尾弹框
    （``tkwait``）的表现一致。
    """
    kind = msg.get("kind")
    title = msg.get("title", "")
    message = msg.get("message", "")
    answer = None
    try:
        if kind == DIALOG_ASK:
            answer = bool(dialogs.askyesno(title, message))
        elif kind == DIALOG_WARNING:
            dialogs.showwarning(title, message)
        elif kind == DIALOG_ERROR:
            dialogs.showerror(title, message)
        elif kind == DIALOG_INFO:
            dialogs.showinfo(title, message)
    except Exception:
        answer = False if kind == DIALOG_ASK else None
    _send(conn, send_lock, {"reply": msg.get("id"), "answer": answer})


def _send(conn, send_lock, msg):
    try:
        with send_lock:
            conn.send(msg)
    except Exception:
        pass


def _read_replay_file(host_window, dialogs):
    """回放文件选择的父进程实现（对齐 ``TkHost.open_replay_file``）。"""
    import json
    from tkinter import filedialog

    try:
        file_path = filedialog.askopenfilename(
            parent=host_window, title="选择回放文件",
            filetypes=[("副本回放", "*.replay.json"), ("所有文件", "*.*")])
    except Exception as exc:
        dialogs.showerror("错误", f"打开文件选择框失败：{exc}")
        return None
    if not file_path:
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:
        dialogs.showerror("错误", f"加载回放失败：{exc}")
        return None
    if not isinstance(data, list) or not data:
        dialogs.showerror("错误", "回放文件格式错误")
        return None
    return data


# ==================== 窗口显隐与登记 ====================

def _hide_window(host_window):
    try:
        host_window.withdraw()
    except Exception:
        pass


def _show_window(host_window):
    try:
        host_window.deiconify()
        host_window.lift()
    except Exception:
        pass


def _register(owner, handle) -> bool:
    """把句柄登记到主控对象上（会话期「副本进行中」守卫的依据）。"""
    if owner is None or not hasattr(owner, "_active_dungeon_window"):
        return False
    try:
        owner._active_dungeon_window = handle
        return True
    except Exception:
        return False


__all__ = ["launch_dungeon_subprocess", "DungeonProcessHandle",
           "result_to_dict"]
