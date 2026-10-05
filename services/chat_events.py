"""聊天事件的极简发布/订阅（模式与 dungeon.process_log 一致）。

聊天服务在消息收发、属性写回后发布事件，pro / mini 两套界面各自订阅
以刷新未读徽标与消息列表；订阅者自行负责把界面更新投递回主线程。
"""

import threading

_lock = threading.Lock()
_subscribers = []


def publish(event):
    """发布一条聊天事件（dict，至少含 type 与 giantess_id）。

    任意线程可调；单个订阅者异常不影响其他订阅者。
    """
    with _lock:
        subscribers = tuple(_subscribers)
    for callback in subscribers:
        try:
            callback(event)
        except Exception:
            pass


def subscribe(callback):
    """登记订阅者；同一回调重复订阅只登记一次。"""
    with _lock:
        if callback not in _subscribers:
            _subscribers.append(callback)


def unsubscribe(callback):
    """解除订阅；未登记过则静默返回。"""
    with _lock:
        if callback in _subscribers:
            _subscribers.remove(callback)
