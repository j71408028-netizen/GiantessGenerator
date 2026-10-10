# -*- coding: utf-8 -*-
"""副本子进程的宿主端口实现与子进程主体。

副本会话运行在**独立子进程**里（父进程侧见 ``ui.common.dungeon_spawner``）：
DPG / GLFW 从此独占子进程，与 Tk 主程序不再共享线程消息队列、GLFW 单例与
X11 连接——``dpg.destroy_context()`` 终止的是子进程的 GLFW，父进程的 Tk 根
窗口因此不再需要保活视口（``dpg_state.park_context``）这类同进程共存补救。

本模块在子进程内充当 ``HostPort`` 的实现，宿主能力按这样映射：

====================  ====================================================
端口方法              子进程实现
====================  ====================================================
``viewport_metrics``  父进程在 spawn 前量好主窗口客户区与 DPI，随载荷传入
``hide_window``       空操作（父进程在 spawn 前自己藏起主窗口）
``show_window``       协议请求，父进程恢复并前置主窗口
``pump_events``       空操作（子进程不再泵宿主事件，见下）
``discard_pending_quit``  空操作（本进程只有 GLFW 一个 GUI，消息归自己）
``dialog``            协议请求，父进程用界面自己的弹框实现代问
``open_replay_file``  协议请求，父进程弹文件框并回传回放数据
``launch_mini_game``  与 ``TkHost`` 同款：孙进程跑 ``mini_game_host``，
                      结果写文件、watch 线程回调（子进程没有 Tk 弹框，
                      失败只记过程日志）
``register_active_window``  空操作（宿主整体退出靠管道断开，见下）
``default_font``      空串（字体家族随构造参数传入，不依赖宿主）
====================  ====================================================

**DPI 感知**：子进程的感知档位镜像父进程——CTk 主进程（专业模式）是感知的，
独立挂件版（纯 Tk，见 ``ui.mini.dpi``）刻意无感知。子进程若在父进程感知时保持
无感知，Windows 会把视口再放大一次（窗口与字体巨大，2026-10-11 实测）。

协议（``multiprocessing.Pipe`` 双向，JSON 安全 dict）：

- 子 → 父：``{"req": "show_window"}``、``{"req": "dialog", "id", "kind",
  "title", "message"}``、``{"req": "open_replay_file", "id"}``、
  ``{"result": {...}}``（最后一局的结果，发完即收）；
- 父 → 子：``{"reply": id, "answer": ...}``、``{"req": "close"}``（父进程
  整体退出前请子进程走帧边界收尾，:meth:`DungeonSessionWindow.request_close`）。

**父进程死亡检测**：管道由一个专职读线程独占（``recv`` 不能多线程并发），
``recv`` 抛 EOF/OSError 即父进程已不在——直接 ``os._exit``。这保证父进程
无论怎么退（正常退出、``os._exit``、崩溃），子进程都不会变成孤儿继续写
``data/``。
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

from dungeon.window.host import DIALOG_ASK, HostPort

_IS_WINDOWS = sys.platform.startswith("win")


class ChildHost(HostPort):
    """子进程侧的宿主端口：能力走父进程，其余按无宿主缺省。"""

    def __init__(self, conn, viewport_spec=None):
        self._conn = conn
        self._viewport = dict(viewport_spec or {})
        self._send_lock = threading.Lock()
        self._pending = {}          # 请求 id -> {"event": Event, "answer": ...}
        self._request_seq = 0
        self._closed = threading.Event()
        #: 父进程请求关闭时经此转交（run_dungeon_host 接到窗口后设置）
        self.on_close_request = None
        self._reader = threading.Thread(target=self._reader_loop,
                                        daemon=True, name="dungeon-child-host")
        self._reader.start()

    # ---------------- 协议 ----------------
    def _reader_loop(self):
        """管道的**唯一**读方：分发应答与父进程请求；管道断开即退出进程。"""
        while True:
            try:
                msg = self._conn.recv()
            except Exception:
                break
            if not isinstance(msg, dict):
                continue
            reply_id = msg.get("reply")
            if reply_id is not None:
                box = self._pending.pop(reply_id, None)
                if box is not None:
                    box["answer"] = msg.get("answer")
                    box["event"].set()
                continue
            if msg.get("req") == "close":
                callback = self.on_close_request
                if callable(callback):
                    try:
                        callback()
                    except Exception:
                        pass
        # 父进程已不在（正常退出 / os._exit / 崩溃）：立即结束子进程，
        # 不给"孤儿副本继续写 data/"留任何窗口。
        self._closed.set()
        os._exit(0)

    def _request(self, msg: dict):
        """发一条协议请求并等待父进程应答；父进程死亡时进程直接终结。"""
        with self._send_lock:
            self._request_seq += 1
            req_id = self._request_seq
            box = {"event": threading.Event(), "answer": None}
            self._pending[req_id] = box
            try:
                self._conn.send({"id": req_id, **msg})
            except Exception:
                self._pending.pop(req_id, None)
                return None
        box["event"].wait()
        return box["answer"]

    # ---------------- 尺寸与 DPI ----------------
    def viewport_metrics(self):
        """父进程量好的主窗口客户区 + DPI（与 ``TkHost.viewport_metrics`` 同构）。"""
        scale = max(0.5, float(self._viewport.get("scale") or 1.0))
        main_cw = int(self._viewport.get("main_cw") or 0)
        main_ch = int(self._viewport.get("main_ch") or 0)
        if main_cw <= 0 or main_ch <= 0:
            main_cw, main_ch = round(1280 * scale), round(720 * scale)
        return (main_cw + round(16 * scale), main_ch + round(39 * scale),
                scale, main_cw, main_ch)

    # ---------------- 宿主窗口显隐 ----------------
    def hide_window(self):
        pass  # 父进程在 spawn 前已自行藏起主窗口

    def show_window(self):
        try:
            self._conn.send({"req": "show_window"})
        except Exception:
            pass

    # ---------------- 事件泵 ----------------
    def pump_events(self):
        pass  # 子进程不泵宿主事件；Tk 主窗口由父进程自己的等待循环维护

    # ---------------- 弹框 ----------------
    def dialog(self, kind, title, message):
        answer = self._request({"req": "dialog", "kind": kind,
                                "title": str(title), "message": str(message)})
        if kind == DIALOG_ASK:
            return bool(answer)
        return None

    # ---------------- 回放文件选择 ----------------
    def open_replay_file(self):
        data = self._request({"req": "open_replay_file"})
        return data if isinstance(data, list) else None

    # ---------------- 内置小游戏 ----------------
    def launch_mini_game(self, game_id, config=None, on_result=None) -> bool:
        """孙进程跑 ``mini_game_host``（与 ``TkHost.launch_mini_game`` 同款）。

        差异只有失败提示：子进程没有 Tk 弹框可用，一律记入过程日志。
        """
        from dungeon import process_log
        from paths import data_dir

        if importlib.util.find_spec("webview") is None:
            process_log.log("[MiniGame] 未安装 pywebview，小游戏窗口不可用"
                            "（pip install pywebview）")
            return False
        entry = os.path.join(data_dir(), "packs", "minigames", str(game_id),
                             "session.html")
        if not os.path.isfile(entry):
            process_log.log(f"[MiniGame] 找不到小游戏「{game_id}」的入口文件：{entry}")
            return False

        config = config if isinstance(config, dict) else {}
        try:
            target = max(1, int(float(config.get("target_level", 3) or 3)))
        except (TypeError, ValueError):
            target = 3
        out_path = os.path.join(
            tempfile.gettempdir(),
            f"giantessgenerator_minigame_{os.getpid()}_{time.time_ns()}.json")

        def _emit(result):
            if callable(on_result):
                try:
                    on_result(result)
                except Exception as exc:
                    process_log.log(f"[MiniGame] 结果回调异常: {exc}")

        def _watch():
            try:
                if getattr(sys, "frozen", False):
                    cmd = [sys.executable, "--mini-game-host", entry,
                           str(target), out_path]
                else:
                    host_script = os.path.join(
                        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "common", "mini_game_host.py")
                    cmd = [sys.executable, host_script, entry, str(target), out_path]
                flags = (subprocess.CREATE_NO_WINDOW if _IS_WINDOWS else 0)
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL,
                                        creationflags=flags)
                code = proc.wait()
                if code != 0:
                    process_log.log(f"[MiniGame] 小游戏进程异常退出（退出码 {code}）")
            except Exception as exc:
                process_log.log(f"[MiniGame] 小游戏进程启动失败: {exc}")
                _emit(None)
                return
            result = None
            try:
                if os.path.isfile(out_path):
                    with open(out_path, "r", encoding="utf-8") as fh:
                        loaded = json.load(fh)
                    result = loaded if isinstance(loaded, dict) and loaded else None
            except (OSError, ValueError):
                result = None
            try:
                os.remove(out_path)
            except OSError:
                pass
            _emit(result)

        threading.Thread(target=_watch, daemon=True,
                         name="mini-game-watch").start()
        return True


# ==================== 子进程主体 ====================

def _ensure_dpi_awareness():
    """开启本进程的 DPI 感知（仅 Windows；必须在任何窗口创建之前调用）。

    子进程是全新的 Python 进程：主进程里由 CTk 在建根窗口时开启的 DPI 感知
    **不会**被继承。子进程若停留在无感知状态，Windows 会把 DPG 视口的
    96-DPI 逻辑坐标按系统缩放再放大一次（实测 175% 缩放的机器上窗口与字体
    整体巨大约 3 倍，2026-10-11）。

    档位与 CTk 主程序一致（``shcore.SetProcessDpiAwareness(2)``，Per-Monitor
    Aware）——视口尺寸与缩放是父进程按真实 DPI 量好随载荷传入的，子进程必须
    同样感知，坐标才能对上。调用失败（已设置过 / 旧系统无 shcore）退回
    ``SetProcessDPIAware`` 兜底，再失败就只能保持无感知。
    """
    if not _IS_WINDOWS:
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def run_dungeon_host(conn, payload):
    """子进程入口：由 ``dungeon_spawner`` 经 ``multiprocessing.Process`` 拉起。

    跑完一局（或子进程侧任何异常）后把结果 dict 发回父进程，然后由父进程
    关闭管道 / 自行退出（读线程的 EOF 分支保证进程一定终结）。
    """
    from dungeon.window.result import REASON_LAUNCH_FAILED

    from paths import ensure_cwd

    # DPI 感知镜像父进程：父进程感知（专业模式的 CTk 进程）则子进程也要感知，
    # 否则 Windows 会把视口再放大一次；父进程本身无感知（独立挂件版的设计，
    # 见 ui.mini.dpi）则子进程同样保持无感知——Windows 整体位图拉伸，
    # 与旧「DPG 与 Tk 同进程」时代的表现一致（尺寸正确、位图偏糊）。
    if payload.get("dpi_aware"):
        _ensure_dpi_awareness()
    ensure_cwd()
    try:
        result = _run_session(conn, payload)
    except Exception:
        import traceback
        result = {"reason": REASON_LAUNCH_FAILED,
                  "launch_error": "副本子进程异常退出：\n"
                                  + traceback.format_exc()}
    try:
        conn.send({"result": result})
    except Exception:
        pass


def _run_session(conn, payload) -> dict:
    """构建仓库与窗口，跑完一局，返回结果 dict。"""
    from dungeon.window.launch_payload import (deserialize_launch,
                                               result_to_dict)
    from persistence import CharacterRepo, ScenarioRepo
    from services.worlds import WorldManager

    kwargs = deserialize_launch(payload.get("window") or {})
    settings = kwargs.get("settings") or {}

    # 仓库在子进程自建：与世界包状态保持一致（对齐 app.shell.bootstrap 的
    # 世界包加载路径；激活失败回退自由副本根，语义与外壳一致）。
    world_manager = WorldManager(data_dir="data")
    active_id = settings.get("active_world")
    if active_id:
        try:
            world_manager.load_active(active_id)
        except ValueError:
            pass
    try:
        world_manager.apply_world_settings(settings)
    except Exception:
        pass
    kwargs["scenario_repo"] = ScenarioRepo(world_state=world_manager.world_state)
    kwargs["character_repo"] = CharacterRepo()
    kwargs.setdefault("parent", None)

    from dungeon.window import DungeonSessionWindow

    host = ChildHost(conn, payload.get("viewport") or {})
    window = DungeonSessionWindow(host=host, **kwargs)
    host.on_close_request = window.request_close
    result = window.run()
    return result_to_dict(result)
