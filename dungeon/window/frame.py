"""会话内唯一的帧时钟：定时任务 + 主线程更新队列（L3）。

分工只有一句话：**时机在这里，重活在外面**。

- **时机**：轮播换一张图、结局图标翻页、Ken Burns 每帧微调几何、仿流式动画每
  30ms 推出两个字、背景重采样的防抖延迟——以前各自用一条线程 ``sleep`` 轮询
  ``_closing``，现在都是挂在帧循环上的任务（:meth:`FrameScheduler.every` /
  :meth:`FrameScheduler.after`），跟着帧循环一起生一起死，不必再逐个 join。
- **重活**：AI 流式/细节提问/选项文案/结局生成，以及背景的像素重采样与混合，
  仍然留在后台线程里做；做完只把结果 :meth:`FrameScheduler.call` 回主线程。

帧循环（``base.DungeonWindowBase._run_frame_loop``）每帧只调用一次
:meth:`FrameScheduler.tick`：先跑到期的定时任务，再清空主线程更新队列——所以任务
里 ``call()`` 出来的界面更新会在**同一帧**的末尾执行，不需要额外等一帧。

与旧的 ``dispatcher._dispatch`` 的区别：

- 它是窗口的**实例成员**（``self._frame``），不再是模块级单例：两个会话互不干扰，
  也根除了"上一个会话 stop 过，新会话忘了 install 导致 UI 更新被静默丢弃"这个坑；
- 它同时是**唯一的定时源**：``dpg.set_frame_callback`` 这条并行的帧回调链随之
  取消，整个 window 层只剩帧循环一个时间源。
"""

import threading
import time
from collections import deque
from itertools import count

from dungeon import process_log


class FrameTask:
    """一条挂在帧时钟上的任务（每 N 秒一次 / 延迟 N 秒一次）。"""

    __slots__ = ("id", "key", "fn", "args", "kwargs", "interval", "repeat",
                 "due_at", "cancelled")

    def __init__(self, task_id, key, fn, args, kwargs, interval, repeat, due_at):
        self.id = task_id
        self.key = key
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        #: 重复间隔（秒）；``repeat`` 为 False 时无意义
        self.interval = interval
        self.repeat = repeat
        self.due_at = due_at
        self.cancelled = False


class FrameScheduler:
    """窗口的帧时钟。**只有主线程**能调 :meth:`tick` / :meth:`drain`；
    后台线程一律走 :meth:`call`（投递），两者都由锁保护。
    """

    #: 传给 :meth:`every` 表示"每帧执行一次"（等价于 0）
    EVERY_FRAME = 0.0

    def __init__(self):
        self._lock = threading.Lock()
        self._queue = deque()      # [(fn, args, kwargs), ...] 主线程队列
        self._tasks = []           # [FrameTask, ...] 定时任务
        self._running = True
        self._ids = count(1)
        #: 统计（自检/调试用）
        self.ticks = 0
        self.task_runs = 0

    # ---------------- 生命周期 ----------------
    def stop(self):
        """会话结束：丢弃尚未执行的更新与任务，后续 :meth:`call` 一律失效。

        在 ``base._finish_session`` 里紧跟"跳出帧循环"之后调用；此后 background
        像素工作者投递的更新会被静默丢弃，不会再碰到已经/即将销毁的 DPG 上下文。
        """
        with self._lock:
            self._running = False
            self._queue.clear()
            self._tasks.clear()

    @property
    def running(self) -> bool:
        return self._running

    def task_count(self) -> int:
        """当前仍挂着的任务数（自检用：会话结束后应为 0）。"""
        with self._lock:
            return sum(1 for task in self._tasks if not task.cancelled)

    # ---------------- 主线程更新队列 ----------------
    def call(self, fn, *args, **kwargs) -> bool:
        """把一次界面更新排到下一帧的主线程执行（后台线程的唯一入口）。

        会话已结束（:meth:`stop` 之后）返回 False——调用方不必各自判 ``_closing``。
        """
        with self._lock:
            if not self._running:
                return False
            self._queue.append((fn, args, kwargs))
            return True

    def drain(self):
        """主线程执行当前排队的全部更新（由 :meth:`tick` 调用）。"""
        while True:
            with self._lock:
                if not self._queue:
                    return
                fn, args, kwargs = self._queue.popleft()
            try:
                fn(*args, **kwargs)
            except Exception as exc:
                process_log.log(f"[FrameScheduler] 更新异常: {exc}")

    # ---------------- 定时任务 ----------------
    def every(self, interval, fn, *args, key=None, **kwargs) -> int:
        """每 ``interval`` 秒执行一次；``interval <= 0`` 表示每帧执行一次。

        ``key`` 用于取消（见 :meth:`cancel`）。返回任务 id。
        """
        return self._schedule(float(interval), True, fn, args, kwargs, key, 0.0)
    def after(self, delay, fn, *args, key=None, **kwargs) -> int:
        """``delay`` 秒后执行一次（防抖/延迟用）。返回任务 id。"""
        return self._schedule(max(0.0, float(delay)), False, fn, args, kwargs, key,
                              max(0.0, float(delay)))

    def cancel(self, key) -> int:
        """取消所有 ``key`` 相同的任务，返回取消的条数。

        同名任务天然互斥（重采样防抖、仿流式动画只保留最新一条），所以 schedules
        之前先 cancel 一次即可，不必再持有/记录上一条 timer。
        """
        if key is None:
            return 0
        removed = 0
        with self._lock:
            for task in self._tasks:
                if task.key == key and not task.cancelled:
                    task.cancelled = True
                    removed += 1
            self._tasks = [t for t in self._tasks if not t.cancelled]
        return removed

    # ---------------- 帧循环入口 ----------------
    def tick(self, now=None) -> int:
        """帧循环每帧调用一次：跑到期任务 → 清空更新队列。返回执行的任务数。"""
        self.ticks += 1
        now = time.monotonic() if now is None else now
        with self._lock:
            if not self._running:
                return 0
            due = []
            alive = []
            for task in self._tasks:
                if task.cancelled:
                    continue
                if task.due_at <= now:
                    due.append(task)
                    if task.repeat:
                        # 掉帧不补跑：下一轮以当前时刻重排，避免卡顿后出现爆发
                        task.due_at = now + max(0.0, task.interval)
                        alive.append(task)
                    continue
                alive.append(task)
            self._tasks = alive

        for task in due:
            self.task_runs += 1
            try:
                task.fn(*task.args, **task.kwargs)
            except Exception as exc:
                process_log.log(f"[FrameScheduler] 帧任务异常（{task.key}）: {exc}")
        # 排在后面的 drain：任务里 call() 出来的界面更新同帧就能上屏
        self.drain()
        return len(due)

    # ---------------- 内部 ----------------
    def _schedule(self, interval, repeat, fn, args, kwargs, key, delay) -> int:
        task_id = next(self._ids)
        with self._lock:
            if key is not None:
                for task in self._tasks:
                    if task.key == key:
                        task.cancelled = True
                self._tasks = [t for t in self._tasks if not t.cancelled]
            if not self._running:
                return 0
            task = FrameTask(task_id, key, fn, args, kwargs, interval, repeat,
                             time.monotonic() + delay)
            self._tasks.append(task)
        return task_id


__all__ = ["FrameScheduler", "FrameTask", "EVERY_FRAME"]
