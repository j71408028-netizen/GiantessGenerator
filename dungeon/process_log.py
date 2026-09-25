"""副本过程日志：收集副本运行中的状态与异常消息（print 的替代通道）。

副本各处的过程消息（章节进出、触发器判定、预生成、收尾落盘……）不再直接
``print`` 到控制台，而是经 :func:`log` 进入进程级环形缓冲；窗口层订阅后经
帧时钟（``FrameScheduler.call``）投递到副本窗口内的「过程日志」折叠面板显示。

- **线程安全**：AI 线程 / 像素工作者 / 帧主线程都可调用 :func:`log`；
- **无订阅者不丢**：订阅前产生的消息留存在缓冲里，订阅时作为积压一次性补显
  （并清空缓冲，避免串进上一局的旧消息）；
- **分层安全**：本模块是纯领域层（不依赖 DPG / Tk / services），领域模块
  （如 ``summary.py``）与窗口层都能 import，由 ``check_dungeon_layering.py`` 守卫。
"""

import threading
import time
from collections import deque

#: 环形缓冲上限：订阅前积压的最大消息条数（防止无人订阅时无限增长）
BUFFER_LIMIT = 500

_lock = threading.Lock()
_buffer = deque(maxlen=BUFFER_LIMIT)
_subscribers = []


def log(message):
    """记录一条过程消息（任意线程可调）。

    消息带 ``[时:分:秒]`` 前缀后进入环形缓冲，并同步通知全部订阅者；订阅者
    （窗口侧）自行负责把显示投递回主线程。单个订阅者异常不影响其他订阅者。
    """
    text = time.strftime("[%H:%M:%S] ") + str(message)
    with _lock:
        _buffer.append(text)
        subscribers = tuple(_subscribers)
    for callback in subscribers:
        try:
            callback(text)
        except Exception:
            pass


def subscribe(callback) -> list:
    """登记订阅者，返回缓冲中的积压消息并清空缓冲。

    积压消息供订阅者建立界面后一次性补显——窗口订阅时缓冲里可能已有构造 /
    会话初始化阶段产生的消息。同一回调重复订阅只登记一次。
    """
    with _lock:
        if callback not in _subscribers:
            _subscribers.append(callback)
        backlog = list(_buffer)
        _buffer.clear()
        return backlog


def unsubscribe(callback):
    """解除订阅；未登记过则静默返回。"""
    with _lock:
        if callback in _subscribers:
            _subscribers.remove(callback)


__all__ = ["log", "subscribe", "unsubscribe"]
