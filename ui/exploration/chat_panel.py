"""角色聊天面板（专业模式）。

挂在左栏介绍面板（IntroPanel）的展开区里，与编辑模式同级：介绍卡上的
「💬 聊天」按钮让介绍卡让位、聊天区在状态面板下方展开（右栏报告面板不再
被占用）。面板顶部沿用编辑模式的排布——36px 紧凑头像框（与介绍卡共用同一
份头像母本）+ 对象名 + 关闭；消息渲染为气泡列表：玩家消息靠右、角色消息
靠左，并按相邻消息的间隔长短自动补时间标记（见 ui.common.chat_time）；"已读
不回"只保留为内部状态，界面上不做任何标注——角色沉默本身就是回应。

回复节奏由服务层与投递控制器负责（docs/dev/chat_delivery.md）：AI 决策后回复
以 queued 入列并排定 available_at；本面板通过共用的 ChatDeliveryController
到点重载聊天域、重渲染，期间显示"对方正在输入…"，渲染后统一标记玩家已读。
界面只画 delivered/read 的角色消息，queued 消息不会提前出现。撤回窗口 =
AI 应答完成之前（角色已"看到"即关闭），撤回后消息从历史中物理删除（视为
没看到），到达中的回复一并丢弃。打开聊天时自动触发统一社交决策
（reconcile）：回复积压、未消费的经历事件与主动搭话一次合并处理。

AI 请求在后台线程执行（ChatService 同步完成解析与写回），完成后经 ``after``
投递回主线程；属性操作生效后通过 host 回调通知探索页刷新状态面板。
未读徽标由 host（ExplorationPanel）统一管理（按存档重算）。
"""

import threading

import customtkinter as ctk

from core.models import ChatMessage, CharacterSnapshot
from services.chat.delivery import ChatDeliveryController
from services.chat import (ChatService, should_reconcile,
                                   unread_messages)
from ui.common.avatar import AvatarFrame, COMPACT_AVATAR_SIZE
from ui.common.chat_time import message_moment, time_marker_text
from ui.common.theme import (
    FB_BLUE, FB_BTN, FB_BTN_HOVER, FB_CARD_BG, FB_CHIP_BG, FB_CHIP_HOVER,
    FB_MUTED, INTRO_BORDER,
)
from ui.common import fonts as ui_fonts


class ChatPanel(ctk.CTkFrame):
    """单角色的聊天窗口（气泡列表 + 补话条 + 输入行）。"""

    def __init__(self, parent, app, host=None, chat_service: ChatService = None,
                 embedded: bool = False):
        # embedded：嵌入 IntroPanel 展开区时去掉自身卡片描边与底色，
        # 直接融进介绍面板，避免"卡片套卡片"的双层边框。
        super().__init__(parent,
                         fg_color="transparent" if embedded else FB_CARD_BG,
                         border_width=0 if embedded else 1,
                         border_color=INTRO_BORDER,
                         corner_radius=10)
        self.app = app
        self.host = host
        self.state: CharacterSnapshot = None
        self.chat_state = None
        self._pending = False
        self._recall_requested = False
        self._pending_row = None          # 待撤回的玩家消息气泡行
        self._recall_btn = None
        self._last_moment = None          # 最后一条已渲染消息的时间坐标

        self._chat_service = chat_service or ChatService()
        # 投递节奏控制器：到点重载/重渲染/"正在输入"/标记已读，界面不自算延迟
        self._delivery = ChatDeliveryController(
            self._chat_service, after=self.after,
            on_reload=self._on_delivery_reload,
            on_status=self._on_delivery_status)
        self._build_ui()

    # ---------- UI 构建 ----------
    def _build_ui(self):
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # 顶栏：与介绍条编辑模式同款——左侧紧凑头像框，右侧对象名与关闭。
        # 头像走 IntroPanel 的母本缓存（见 _refresh_avatar），框自己定尺寸。
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky='ew', padx=10, pady=(8, 2))
        self.avatar_frame = AvatarFrame(header, size=COMPACT_AVATAR_SIZE)
        self.avatar_frame.pack(side='left', padx=(0, 10))
        ctk.CTkButton(header, text="✕", width=28, height=24,
                      font=ui_fonts.ui_font(11),
                      fg_color=FB_CHIP_BG, text_color=FB_MUTED,
                      hover_color=FB_CHIP_HOVER, corner_radius=12,
                      command=self._close).pack(side='right')
        self.title_label = ctk.CTkLabel(header, text="💬 聊天",
                                        font=ui_fonts.ui_font(14, "bold"),
                                        text_color=FB_BLUE, anchor="w")
        self.title_label.pack(side='left', fill='x', expand=True, padx=(0, 6))

        # 消息气泡区
        self.message_scroll = ctk.CTkScrollableFrame(
            self, fg_color="transparent", scrollbar_button_color=INTRO_BORDER)
        self.message_scroll.grid(row=1, column=0, sticky='nsew', padx=8, pady=4)

        # 状态行（对方正在输入… / 错误提示）
        self.status_label = ctk.CTkLabel(self, text="", height=16,
                                         font=ui_fonts.ui_font(10),
                                         text_color=FB_MUTED, anchor="w")
        self.status_label.grid(row=2, column=0, sticky='ew', padx=12)

        # 输入行
        input_row = ctk.CTkFrame(self, fg_color="transparent")
        input_row.grid(row=3, column=0, sticky='ew', padx=10, pady=(2, 10))
        input_row.grid_columnconfigure(0, weight=1)
        self.input_entry = ctk.CTkEntry(input_row, height=32,
                                        font=ui_fonts.ui_font(12),
                                        border_color=INTRO_BORDER,
                                        fg_color=FB_CARD_BG,
                                        placeholder_text="说点什么…")
        self.input_entry.grid(row=0, column=0, sticky='ew')
        self.input_entry.bind("<Return>", lambda _e: self._send())
        self.send_btn = ctk.CTkButton(input_row, text="发送", width=70,
                                      height=32, font=ui_fonts.ui_font(12),
                                      fg_color=FB_BTN, text_color="white",
                                      hover_color=FB_BTN_HOVER,
                                      corner_radius=8, command=self._send)
        self.send_btn.grid(row=0, column=1, padx=(6, 0))

    # ---------- 数据加载 ----------
    def set_state(self, state: CharacterSnapshot):
        """切换聊天对象：经投递控制器重载该角色的聊天历史并重绘。"""
        self.state = state
        if state is None:
            self.title_label.configure(text="💬 聊天")
        else:
            nick = f"（{state.nick}）" if state.nick else ""
            self.title_label.configure(text=f"💬 与 {state.name} 聊天{nick}")
        self._refresh_avatar()
        self._set_status("")
        if state is None:
            self._delivery.detach()
            self.chat_state = None
            self._render_messages()
            return
        # attach 会立即 load_chat（含到点提升）→ on_reload 渲染并标记已读
        self._delivery.attach(state)
        # 打开聊天时先后台预热聊天参数（首次聊天前由 AI 一次性决定，
        # 失败静默——发送时会正式报错），再自动补话：
        # 有未读→回复未读；无未读且间隔够久→可能主动搭话
        self.after(80, self._prewarm_then_catchup)

    def _refresh_avatar(self):
        """顶部紧凑头像框：取介绍卡解析好的同一份头像母本，按本框尺寸显示。

        头像的解析、缩略与"用预览图当头像"的回退都在 IntroPanel 里（含读盘
        缓存），这里只取母本位图；没有角色或没有头像时框自己退回占位字形。
        """
        pil_img = None
        intro = getattr(self.host, "intro_panel", None)
        if intro is not None and self.state is not None:
            try:
                pil_img = intro.current_avatar_pil(self.state)
            except Exception:
                pil_img = None
        self.avatar_frame.set_image(pil_img)

    def unread_count(self) -> int:
        """当前角色的未读玩家消息数（角色尚未看到的积压）。"""
        if self.state is None:
            return 0
        return len(unread_messages(
            self._chat_service.load_chat(self.state.giantess_id)))

    # ---------- 投递节奏（与挂件模式共用 ChatDeliveryController） ----------
    def _on_delivery_reload(self, chat_state):
        """控制器重载聊天域后：换用最新状态并重绘（queued 消息不出现）。"""
        if not self.winfo_exists():
            return
        self.chat_state = chat_state
        self._render_messages()

    def _on_delivery_status(self, kind: str):
        if self.winfo_exists():
            self._set_status("对方正在输入…" if kind == "typing" else "")

    # ---------- 自动补话 ----------
    def _prewarm_then_catchup(self):
        if self.state is None or self.chat_state is None:
            return
        if self.chat_state.chat_params is not None:
            self._auto_catchup()
            return
        state, chat_state = self.state, self.chat_state

        def worker():
            self._chat_service.ensure_chat_params(state, chat_state)
            self.after(0, self._auto_catchup)

        threading.Thread(target=worker, daemon=True).start()

    def _auto_catchup(self):
        """打开聊天后的统一社交决策判断（reconcile）：回复积压、经历
        事件与主动搭话，一次合并处理；无事可做则静默。"""
        if self._pending or self.state is None or self.chat_state is None:
            return
        if should_reconcile(self.state, self.chat_state):
            self._run_reconcile()

    def _run_reconcile(self):
        self._pending = True
        self.send_btn.configure(state='disabled', text="…")
        self._set_status("对方正在输入…")
        state, chat_state = self.state, self.chat_state

        def worker():
            result = self._chat_service.reconcile(state, chat_state)
            self.after(0, lambda: self._catchup_done(result))

        threading.Thread(target=worker, daemon=True).start()

    def _catchup_done(self, result: dict):
        self._pending = False
        self.send_btn.configure(state='normal', text="发送")
        if self.state is None:
            return
        if result.get("error"):
            self._set_status(result["error"])
            return
        if result.get("ops_applied") and self.host is not None:
            self.host.on_chat_ops_applied()
        # 回复已 queued 入列：交给投递控制器接管展示节奏
        self._delivery.refresh()

    # ---------- 渲染 ----------
    def _render_messages(self):
        for widget in self.message_scroll.winfo_children():
            widget.destroy()
        self._pending_row = None
        self._recall_btn = None
        self._last_moment = None
        if self.chat_state is None:
            return
        for message in self.chat_state.messages:
            # 角色消息只画已投递/已读的：queued 由控制器到点后再出现
            if message.role == "char" and message.status not in ("delivered", "read"):
                continue
            self._append_timed_bubble(message)
        self._scroll_to_bottom()

    def _append_timed_bubble(self, message: ChatMessage, recallable: bool = False):
        """按与上一条的间隔补时间标记，再画气泡（间隔太短就不补）。

        ``_last_moment`` 只认**已渲染**的消息：恢复一段历史时它是 None，
        于是第一条必然带标记，交代这段对话从什么时候开始；跳过 queued
        角色消息也不会把时间线算歪。
        """
        moment = message_moment(message)
        marker = time_marker_text(self._last_moment, moment)
        if marker:
            self._append_time_marker(marker)
        self._append_bubble(message, recallable=recallable)
        if moment is not None:
            self._last_moment = moment

    def _append_time_marker(self, text: str):
        """消息之间的时间标记：居中、弱化，只交代"隔了多久"。"""
        row = ctk.CTkFrame(self.message_scroll, fg_color="transparent")
        row.pack(fill='x', pady=(6, 2))
        ctk.CTkLabel(row, text=text, font=ui_fonts.ui_font(9),
                     text_color=FB_MUTED).pack()

    def _append_bubble(self, message: ChatMessage, recallable: bool = False):
        is_user = message.role == "user"
        row = ctk.CTkFrame(self.message_scroll, fg_color="transparent")
        row.pack(fill='x', pady=2, anchor='e' if is_user else 'w')

        bubble = ctk.CTkLabel(
            row, text=message.text, justify='left', wraplength=360,
            font=ui_fonts.ui_font(12),
            fg_color=FB_BLUE if is_user else FB_CHIP_BG,
            text_color="white" if is_user else FB_BLUE,
            corner_radius=10)
        bubble.pack(anchor='e' if is_user else 'w',
                    padx=((40, 0) if is_user else (0, 40)),
                    ipadx=8, ipady=4)
        if recallable:
            # 撤回窗口：角色"看到"之前，最后一条玩家消息可撤回
            self._pending_row = row
            self._recall_btn = ctk.CTkButton(
                row, text="撤回", width=36, height=20,
                font=ui_fonts.ui_font(9),
                fg_color="transparent", text_color=FB_MUTED,
                hover_color=FB_CHIP_HOVER, border_width=0, corner_radius=8,
                command=self._recall)
            self._recall_btn.pack(side='right', padx=(6, 0))
        self._scroll_to_bottom()

    def _scroll_to_bottom(self):
        def scroll():
            if not self.winfo_exists():
                return
            canvas = getattr(self.message_scroll, "_parent_canvas", None)
            if canvas is not None:
                canvas.yview_moveto(1.0)
        self.after(30, scroll)

    def _set_status(self, text: str):
        self.status_label.configure(text=text)

    # ---------- 收发 ----------
    def _send(self):
        if self._pending or self.state is None or self.chat_state is None:
            return
        text = self.input_entry.get().strip()
        if not text:
            return
        self.input_entry.delete(0, 'end')
        self._pending = True
        self._recall_requested = False
        self.send_btn.configure(state='disabled', text="…")
        self._set_status("")
        # 玩家消息同样先补标记：上一条已经是很久以前时，不能等回复到了才报时
        self._append_timed_bubble(ChatMessage(role="user", text=text),
                                  recallable=True)

        state, chat_state = self.state, self.chat_state

        def worker():
            try:
                result = self._chat_service.send_message(state, chat_state, text)
            except Exception as e:  # 网络层之外的意外异常也兜住
                result = {"user_message": None, "reply_messages": [],
                          "ops_applied": {}, "refused": False, "error": str(e)}
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

    def _send_done(self, result: dict):
        self._pending = False
        self.send_btn.configure(state='normal', text="发送")
        if self.state is None:
            return
        if self._recall_requested:
            # 角色尚未"看到"：整段对话从历史中移除（含到达中的回复）
            self._recall_requested = False
            self._chat_service.recall_exchange(
                self.chat_state, result.get("user_message"),
                result.get("reply_message"))
            self._delivery.refresh()
            self._set_status("已撤回")
            self.after(2000, lambda: self._set_status(""))
            return
        if result["error"]:
            self._set_status(result["error"])
            return
        # AI 已应答 = 角色"看到"，撤回窗口关闭；回复（可能多条）已 queued
        # 入列，展示节奏交给投递控制器
        self._expire_recall()
        if result.get("ops_applied") and self.host is not None:
            self.host.on_chat_ops_applied()
        self._delivery.refresh()

    def _expire_recall(self):
        if self._recall_btn is not None:
            self._recall_btn.destroy()
            self._recall_btn = None
        self._pending_row = None

    def _close(self):
        if self.host is not None:
            self.host.close_chat_panel()
