# -*- coding: utf-8 -*-
"""挂件版聊天区（低像素风）：与报告区同款的面板，内容换成气泡消息。

样式对齐 ``MiniReportView``：外层 ``ink_alt`` 面板 + 2px 描边，标题条压在更深的
``ink`` 底色上（左边标题、右边一个收起键、中间夹态度值），下面是凹进去的内容井。
区别只在内容——报告是分步正文，这里是气泡列表：玩家靠右（accent 底、ink 字）、
角色靠左（ink_soft 底、text 字）。

行为与专业版完全一致，仍由共用的 ``ChatDeliveryController`` 驱动（消息生命周期
见 docs/chat_delivery.md）：AI 回复以 queued 入列，到点重载重渲染、期间显示"对方
正在输入…"、渲染后统一标记已读；界面只画 delivered/read 的角色消息。撤回窗口 =
AI 应答完成之前（角色已"看到"即关闭，物理删除）。打开时先后台预热聊天参数再自动
补话（有未读→回复未读；间隔够久→可能主动搭话）。AI 请求都在后台线程执行。

聊天对象就是主界面当前载入的角色：换人先回角色档案屏载入另一位。
"""

import threading
import tkinter as tk

from core.models import ChatMessage
from services.chat import should_reconcile
from services.chat.delivery import ChatDeliveryController
from ui.mini import pixel as px

#: 气泡正文 / 标题 / 状态行的字号。正文取 14，与报告正文同档（旧版是 11）；挂件
#: 是窄栏，标题与状态行收到 13 / 12，在 330 宽的最小窗口里仍排得开。
BODY_SIZE = 14
TITLE_SIZE = 13
MARK_SIZE = 12
INPUT_SIZE = 13

#: 气泡让出的横向空白（像素）：靠边一侧留 2，对面留这个，避免气泡顶满整行；
#: 换行宽度也按它算（见 _on_listing_resize）。
BUBBLE_INSET = 26

#: 内容井宽度拿不到（首帧未映射）时的气泡换行宽度兜底。
DEFAULT_WRAP = 240


class MiniChatView(px.Panel):
    """聊天面板：标题条 + 气泡井 + 状态行 + 输入行。"""

    def __init__(self, parent, app):
        super().__init__(parent, fill="ink_alt", border="line")
        self.app = app
        self.state = None
        self.chat_state = None
        self._pending = False
        self._recall_requested = False
        self._pending_row = None
        self._recall_btn = None
        self._bubble_labels = []
        self._wrap = DEFAULT_WRAP
        self._build_ui()
        self._attach()

    # ==================== UI ====================
    def _build_ui(self):
        # 标题条压在比面板更深的底色上，与报告区同款。
        header = px.surface(self, fill="ink")
        header.pack(fill='x', padx=4, pady=(4, 0))

        self._title_label = px.label(header, "✉ 聊天", tone="accent",
                                     size=TITLE_SIZE, bold=True)
        self._title_label.pack(side='left', padx=4, pady=2)

        px.PixelButton(header, "◀ 收起", self.app.pop_screen, tone="text_dim",
                       width=62, height=20).pack(side='right', padx=2, pady=2)
        self._attitude_label = px.label(header, "", tone="text_dim",
                                        size=MARK_SIZE)
        self._attitude_label.pack(side='right', padx=4)

        container = px.transparent(self)
        container.pack(fill='both', expand=True, padx=4, pady=(0, 2))

        self.listing = px.ScrollFrame(container, fill="ink", border="line")
        self.listing.pack(fill='both', expand=True)
        # 窗口可缩放：井宽变了要重算气泡换行宽度，否则窄窗溢出、宽窗早换行。
        self.listing.canvas.bind("<Configure>", self._on_listing_resize, add="+")

        self.status = px.label(self, "", tone="text_off", size=MARK_SIZE)
        self.status.pack(anchor='w', padx=6)

        row = px.transparent(self)
        row.pack(fill='x', padx=4, pady=(2, 4))
        row.columnconfigure(0, weight=1)
        self.input_var = tk.StringVar()
        entry = px.entry(row, textvariable=self.input_var, height=26,
                         size=INPUT_SIZE)
        entry.grid(row=0, column=0, sticky='ew')
        entry.bind("<Return>", lambda _e: self._send())
        self.send_btn = px.PixelButton(row, "发送", self._send, tone="ok",
                                       size=BODY_SIZE, width=58, height=26)
        self.send_btn.grid(row=0, column=1, padx=(4, 0))

    def _on_listing_resize(self, event):
        wrap = max(150, event.width - 2 * BUBBLE_INSET - 16)
        if wrap == self._wrap:
            return
        self._wrap = wrap
        for label in self._bubble_labels:
            label.configure(wraplength=wrap)

    # ==================== 装载 ====================
    def _attach(self):
        state = self.app.current_state
        self.state = state
        if state is None:
            # 主界面没有角色时不提供入口，这里只兜住"直接进屏"的异常路径。
            self._write_hint()
            return

        nick = f"（{state.nick}）" if state.nick else ""
        self.chat_state = self.app.chat_service().load_chat(state.giantess_id)
        self._title_label.configure(text=f"✉ 与 {state.name} 聊天{nick}")
        self._attitude_label.configure(text=f"态度 {self.chat_state.attitude:+d}")
        self._render_messages()

        # 投递节奏控制器：到点重载/重渲染/"正在输入"/标记已读
        self._delivery = ChatDeliveryController(
            self.app.chat_service(), after=self.after,
            on_reload=self._on_delivery_reload,
            on_status=self._on_delivery_status)
        self._delivery.attach(state)
        # 打开聊天时先后台预热聊天参数（首次聊天前由 AI 一次性决定，失败
        # 静默——发送时会正式报错），再自动补话：
        # 有未读→回复未读；无未读且间隔够久→可能主动搭话
        self.after(80, self._prewarm_then_catchup)

    def _write_hint(self):
        px.label(self.listing.body, "还没有载入角色。", tone="text_dim",
                 size=BODY_SIZE).pack(anchor='w', padx=6, pady=(8, 2))
        px.label(self.listing.body, "先创建或载入一位少女，再来找她聊天。",
                 tone="text_off", size=MARK_SIZE).pack(anchor='w', padx=6)

    # ---------- 投递节奏（与专业模式共用 ChatDeliveryController） ----------
    def _on_delivery_reload(self, chat_state):
        if not self.winfo_exists():
            return
        self.chat_state = chat_state
        self._render_messages()
        # 已投递消息已在本屏渲染：清掉角色卡上的未读点
        self.app._unread_chat.pop(self.state.giantess_id, None)
        self.app._refresh_chat_badge()

    def _on_delivery_status(self, kind: str):
        if not self.winfo_exists():
            return
        self._set_status("对方正在输入…" if kind == "typing" else "")

    # ---------- 自动补话（统一社交决策） ----------
    def _prewarm_then_catchup(self):
        if self.state is None or self.chat_state is None:
            return
        if self.chat_state.chat_params is not None:
            self._auto_catchup()
            return
        state, chat_state = self.state, self.chat_state

        def worker():
            self.app.chat_service().ensure_chat_params(state, chat_state)
            self.after(0, self._auto_catchup)

        threading.Thread(target=worker, daemon=True).start()

    def _auto_catchup(self):
        if self._pending:
            return
        if should_reconcile(self.state, self.chat_state):
            self._run_reconcile()

    def _run_reconcile(self):
        self._pending = True
        self.send_btn.configure(state='disabled')
        self._set_status("对方正在输入…")
        state, chat_state = self.state, self.chat_state

        def worker():
            result = self.app.chat_service().reconcile(state, chat_state)
            self.after(0, lambda: self._catchup_done(result))

        threading.Thread(target=worker, daemon=True).start()

    def _catchup_done(self, result):
        self._pending = False
        if not self.winfo_exists():
            return
        self.send_btn.configure(state='normal')
        if result.get("error"):
            self._set_status(result["error"], tone="danger")
            return
        if result.get("ops_applied") and self.app.current_state is not None:
            self.app.state_card.update_state(self.app.current_state)
        # 回复已 queued 入列：交给投递控制器接管展示节奏
        self._delivery.refresh()

    # ==================== 渲染 ====================
    def _render_messages(self):
        for widget in self.listing.body.winfo_children():
            widget.destroy()
        self._bubble_labels = []
        self._pending_row = None
        self._recall_btn = None
        for message in self.chat_state.messages:
            # 角色消息只画已投递/已读的：queued 由控制器到点后再出现
            if message.role == "char" and message.status not in ("delivered", "read"):
                continue
            self._append_bubble(message)
        self.listing.body.after(30, self._scroll_bottom)

    def _append_bubble(self, message, recallable: bool = False):
        """一条消息一个气泡：玩家靠右、角色靠左，靠边一侧贴边不顶满。"""
        is_user = message.role == "user"
        row = px.transparent(self.listing.body)
        row.pack(fill='x', pady=2)

        if recallable:
            # 撤回窗口：角色"看到"之前，最后一条玩家消息可撤回（键挂在气泡左侧）
            self._pending_row = row
            self._recall_btn = px.small_button(row, "撤回", self._recall,
                                               tone="text_dim", width=40)
            self._recall_btn.pack(side='right', padx=(0, 4), pady=(6, 0))
            self.listing.bind_wheel(self._recall_btn)

        bubble = px.panel(row, fill="accent" if is_user else "ink_soft",
                          border="accent_hi" if is_user else "line")
        bubble.pack(side='right' if is_user else 'left',
                    padx=((0, 2) if is_user else (2, 0)))
        text = px.label(bubble, message.text,
                        tone="ink" if is_user else "text", size=BODY_SIZE,
                        wraplength=self._wrap, justify='left', anchor='w')
        text.pack(padx=8, pady=5)
        self._bubble_labels.append(text)
        # tk 不会把滚轮事件冒泡给父控件：气泡里的每个控件都要接一次
        self.listing.bind_wheel(bubble)
        self.listing.bind_wheel(text)

    def _scroll_bottom(self):
        if self.winfo_exists():
            self.listing.canvas.yview_moveto(1.0)

    def _set_status(self, text: str, tone: str = "text_off"):
        if self.winfo_exists():
            self.status.configure(text=text, text_color=px.color(tone))

    # ==================== 收发 ====================
    def _send(self):
        if self._pending or self.state is None or self.chat_state is None:
            return
        text = self.input_var.get().strip()
        if not text:
            return
        self.input_var.set("")
        self._pending = True
        self._recall_requested = False
        self.send_btn.configure(state='disabled')
        self._set_status("")
        self._append_bubble(ChatMessage(role="user", text=text), recallable=True)
        self._scroll_bottom()

        state, chat_state = self.state, self.chat_state

        def worker():
            try:
                result = self.app.chat_service().send_message(
                    state, chat_state, text)
            except Exception as e:
                result = {"error": str(e), "reply_messages": [],
                          "ops_applied": {}}
            self.after(0, lambda: self._send_done(result))

        threading.Thread(target=worker, daemon=True).start()

    def _recall(self):
        """撤回最后一条玩家消息（仅角色看到之前有效）。"""
        if not self._pending:
            return
        self._recall_requested = True
        if self._pending_row is not None:
            self._pending_row.destroy()
            self._pending_row = None
        self._set_status("撤回中…")

    def _send_done(self, result):
        self._pending = False
        if not self.winfo_exists():
            return
        self.send_btn.configure(state='normal')
        if self._recall_requested:
            # 角色尚未"看到"：整段对话从历史中移除（含到达中的回复）
            self._recall_requested = False
            self.app.chat_service().recall_exchange(
                self.chat_state, result.get("user_message"),
                result.get("reply_message"))
            self._delivery.refresh()
            self._set_status("已撤回")
            self.after(2000, lambda: self._set_status(""))
            return
        if result.get("error"):
            self._set_status(result["error"], tone="danger")
            return
        # AI 已应答 = 角色"看到"，撤回窗口关闭；回复（可能多条）已 queued
        # 入列，展示节奏交给投递控制器
        self._expire_recall()
        if result.get("ops_applied") and self.app.current_state is not None:
            self.app.state_card.update_state(self.app.current_state)
        self._delivery.refresh()

    # ---------- 回复节奏 ----------
    def _expire_recall(self):
        if not self.winfo_exists():
            return
        if self._recall_btn is not None:
            self._recall_btn.destroy()
            self._recall_btn = None
        self._pending_row = None
