"""聊天消息投递：后台调度线程 + 两套界面共用的节奏控制器。

职责边界（对应 docs/dev/chat_delivery.md 阶段一、二）：
- ChatDeliveryScheduler：进程内唯一的后台线程。消息以 queued 入列时
  登记最早 available_at 并唤醒；到点调用 ChatService.deliver_due 把
  queued 提升为 delivered 并广播 message_delivered 事件。应用启动时
  扫描上次会话遗留的 queued 消息并接管（延迟结算：关闭期间到期的
  消息下次启动时一次性投递，不做逐分钟后台模拟）。
- ChatDeliveryController：工具包无关的节奏控制器，专业模式与挂件模式
  界面共用。每个 tick：非阻塞重载聊天域（含到点提升）→ 触发界面重
  渲染 → 标记玩家已读 → 若仍有 queued 消息，按最早 available_at 安排
  下一次 after，并回调"正在输入"状态。attach/detach/refresh 用 token
  取消过期回调，界面切换对象/关闭时不会误投旧消息。

线程模型：调度器在自己的线程里做阻塞调用；控制器所有路径都在 UI 主
线程，对 ChatService 一律 blocking=False——角色锁被 AI 请求占用时
load_chat 返回 None，控制器保留旧状态 1 秒后重试，界面永不卡顿。
"""

import datetime
import threading

from persistence.chat_repo import ChatRepo


class ChatDeliveryScheduler:
    """后台投递线程：到点把 queued 消息提升为 delivered 并广播。"""

    def __init__(self, chat_service):
        self._chat_service = chat_service
        self._condition = threading.Condition()
        self._wake_at = {}          # giantess_id -> 最早 available_at（datetime）
        self._thread = None
        self._stopped = False

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stopped = False
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="chat-delivery")
        self._thread.start()
        self._adopt_pending()

    def stop(self):
        self._stopped = True
        with self._condition:
            self._condition.notify_all()

    def notify_queued(self, giantess_id: str, available_at: str):
        """有消息以 queued 入列：登记最早到点时间并唤醒调度线程。"""
        try:
            moment = datetime.datetime.fromisoformat(available_at)
        except (TypeError, ValueError):
            moment = datetime.datetime.now()
        with self._condition:
            current = self._wake_at.get(giantess_id)
            if current is None or moment < current:
                self._wake_at[giantess_id] = moment
            self._condition.notify_all()

    def _adopt_pending(self):
        """启动时接管上次会话遗留的 queued 消息（deliver_due 会把已到点
        的直接投递，未到点的经 _reenqueue 自动登记下一次唤醒）。"""
        try:
            repo = ChatRepo() if self._chat_service is None else \
                self._chat_service.chat_repo
            for giantess_id in repo.ids_with_queued():
                self.notify_queued(giantess_id,
                                   datetime.datetime.now().isoformat())
        except Exception:
            # 接管失败不影响本次运行：打开聊天时 load_chat 仍会兜底提升
            pass

    def _run(self):
        while not self._stopped:
            due = []
            with self._condition:
                if not self._wake_at:
                    self._condition.wait(timeout=30)
                    continue
                now = datetime.datetime.now()
                due = [gid for gid, moment in self._wake_at.items()
                       if moment <= now]
                for gid in due:
                    self._wake_at.pop(gid, None)
                if not due:
                    earliest = min(self._wake_at.values())
                    delay = max(0.0, (earliest - now).total_seconds())
                    self._condition.wait(timeout=min(delay, 30.0))
            for giantess_id in due:
                try:
                    self._chat_service.deliver_due(giantess_id)
                except Exception:
                    # 单个角色投递失败不拖垮线程；deliver_due 内部
                    # _reenqueue 会在存档仍有 queued 时重新登记
                    pass


class ChatDeliveryController:
    """两套聊天界面共用的投递节奏控制器（工具包无关）。

    界面提供 tk 风格的 after(delay_ms, fn) 与两个回调：
    - on_reload(chat_state)：聊天域已重载（含到点提升），界面据此重渲染；
    - on_status(kind)："typing"= 仍有 queued 消息（显示"正在输入…"），
      ""= 静默。
    """

    def __init__(self, chat_service, after, on_reload, on_status):
        self._chat_service = chat_service
        self._after = after
        self._on_reload = on_reload
        self._on_status = on_status
        self._giantess_id = None
        self._token = 0

    def attach(self, state):
        """开始跟随一个角色：立即重载并接管其 queued 消息的展示节奏。"""
        self._giantess_id = state.giantess_id if state is not None else None
        self.refresh()

    def detach(self):
        self._giantess_id = None
        self._token += 1

    def refresh(self):
        """外部状态变化（发送/补话返回）后立即对齐一次。"""
        if not self._giantess_id:
            return
        self._token += 1
        self._tick(self._token)

    def _tick(self, token: int):
        if token != self._token or not self._giantess_id:
            return
        giantess_id = self._giantess_id
        chat_state = self._chat_service.load_chat(giantess_id, blocking=False)
        if chat_state is None:
            # AI 请求占用角色锁：保留旧状态，稍后重试
            self._after(1000, lambda: self._tick(token))
            return
        self._on_reload(chat_state)
        self._chat_service.mark_char_messages_read(giantess_id, blocking=False)
        from services.chat import queued_char_messages
        queued = queued_char_messages(chat_state)
        if queued:
            earliest = None
            for message in queued:
                if not message.available_at:
                    earliest = datetime.datetime.now()
                    break
                try:
                    moment = datetime.datetime.fromisoformat(message.available_at)
                except ValueError:
                    moment = datetime.datetime.now()
                if earliest is None or moment < earliest:
                    earliest = moment
            delay_ms = max(0, int((earliest - datetime.datetime.now())
                                  .total_seconds() * 1000)) + 60
            self._on_status("typing")
            self._after(delay_ms, lambda: self._tick(token))
        else:
            self._on_status("")


_scheduler = None
_SCHEDULER_GUARD = threading.Lock()


def get_scheduler(chat_service=None) -> ChatDeliveryScheduler:
    """进程级调度器单例（懒创建，daemon 线程，随进程退出自动结束）。

    UI 根初始化时应传入自己的 ChatService 启动调度器（顺便接管上次
    会话遗留的 queued 消息）；服务层内部调用不传参，只取既有单例。
    """
    global _scheduler
    with _SCHEDULER_GUARD:
        if _scheduler is None:
            if chat_service is None:
                from services.chat import ChatService
                chat_service = ChatService()
            _scheduler = ChatDeliveryScheduler(chat_service)
            _scheduler.start()
        return _scheduler
