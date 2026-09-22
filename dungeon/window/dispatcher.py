import threading
import time
from collections import deque

import dearpygui.dearpygui as dpg


class DpgDispatcher:
    """将后台线程的 UI 更新安全地转发到 DPG 主线程。"""

    def __init__(self):
        self._queue = deque()
        self._lock = threading.Lock()
        self._installed = False
        self._last_processed = time.time()
        self._watchdog = None

    def enqueue(self, fn, *args, **kwargs):
        with self._lock:
            self._queue.append((fn, args, kwargs))

    def _process(self):
        if not self._installed:
            return
        while True:
            with self._lock:
                if not self._queue:
                    break
                fn, args, kwargs = self._queue.popleft()
            try:
                fn(*args, **kwargs)
            except Exception as exc:
                print(f"[DpgDispatcher] 更新异常: {exc}")

    def install(self):
        if self._installed:
            return
        self._installed = True
        self._last_processed = time.time()
        self._start_watchdog()
        self._register_next_frame()

    def stop(self):
        self._installed = False
        with self._lock:
            self._queue.clear()

    def _register_next_frame(self):
        dpg.set_frame_callback(dpg.get_frame_count() + 3, callback=self._recurse)

    def _recurse(self, *args):
        if not self._installed:
            return
        self._process()
        self._last_processed = time.time()
        self._register_next_frame()

    def _start_watchdog(self):
        def watch():
            while self._installed:
                time.sleep(1.0)
                if not self._installed:
                    break
                with self._lock:
                    stale = time.time() - self._last_processed > 1.5
                if stale:
                    try:
                        self._register_next_frame()
                        self._last_processed = time.time()
                    except Exception as exc:
                        print(f"[DpgDispatcher] 看门狗重注册失败: {exc}")

        self._watchdog = threading.Thread(target=watch, daemon=True)
        self._watchdog.start()


_dispatch = DpgDispatcher()
