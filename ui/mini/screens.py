"""覆盖式屏幕：挂件版用整窗换屏取代所有对话框。

原先的角色选择、设置、AI 配置与消息 / 确认框都是独立弹窗，在「小游戏」的观感
里既突兀又容易和置顶窗口抢层级。这里把它们统一做成同一扇窗口内的「一屏」：标题
栏 + 内容区，左上角一个 ◀ 返回，内容直接盖住主界面，不再有第二个窗口。
"""

import os
import threading
import uuid

import tkinter as tk

from core.ai import PROVIDER_DEFAULTS, create_client
from core.logic import format_size
from core.models import ChatMessage
from paths import APP_VERSION
from services.chat.delivery import ChatDeliveryController
from services.chat import should_reconcile
from ui.mini import pixel as px


# ==================== 基类 ====================
class Screen(tk.Frame):
    """整窗一屏：顶部标题栏 + ◀ 返回，内容由子类填充。"""

    TITLE = ""

    def __init__(self, parent, app):
        super().__init__(parent, bd=0, highlightthickness=0, bg=px.color("ink"))
        px.bind_colors(self, {'bg': 'ink'})
        self.app = app

        header = px.panel(self, fill="ink_alt", border="line")
        header.pack(fill='x')
        px.PixelButton(header, "◀", self.app.pop_screen, tone="text_dim",
                       width=26, height=22).pack(side='left', padx=(4, 2), pady=4)
        px.label(header, self.TITLE, tone="accent", size=14, bold=True).pack(
            side='left', padx=4)

        self.body = px.transparent(self)
        self.body.pack(fill='both', expand=True, padx=6, pady=6)
        self._build(self.body)

    def _build(self, body):
        """子类实现。"""

    def on_show(self):
        """每次显示时刷新（返回上一屏会重建，因此这里通常无需处理）。"""


# ==================== 角色档案 ====================
class CharactersScreen(Screen):
    """列出 data/archives 下的角色，点一行即载入。"""

    TITLE = "角色档案"

    def _build(self, body):
        entries = self.app.collect_characters()
        if not entries:
            px.label(body, "尚无角色档案。", tone="text_dim", size=10).pack(
                anchor='w', pady=(10, 2))
            px.label(body, "回到主界面按「创建」邂逅第一位少女。",
                     tone="text_off", size=10).pack(anchor='w')
            return

        px.label(body, f"共 {len(entries)} 位", tone="text_dim", size=10).pack(
            anchor='w', pady=(0, 4))

        listing = px.ScrollFrame(body, fill="ink_alt", border="line")
        listing.pack(fill='both', expand=True)

        current_id = self.app.current_state.giantess_id if self.app.current_state else ""
        for entry in entries:
            nick = f"（{entry['nick']}）" if entry["nick"] else ""
            text = (f"{entry['name']}{nick}　{format_size(entry['height'])}"
                    f"　{entry['updated_at'][5:10]}")
            row = px.PixelButton(
                listing.body, text, lambda eid=entry["id"]: self._pick(eid),
                tone="accent" if entry["id"] == current_id else "text_dim",
                height=24, anchor='w')
            row.pack(fill='x', pady=1)
            listing.bind_wheel(row)

    def _pick(self, giantess_id: str):
        self.app.pop_screen()
        self.app.load_character(giantess_id)


# ==================== 设置 ====================
class SettingsScreen(Screen):
    """精简设置：主题、置顶、头像、AI 服务商——每项一行一个控件。"""

    TITLE = "设置"

    def _build(self, body):
        settings = self.app.settings

        theme = px.CycleRow(
            body, "主题", ["亮色", "暗色"],
            on_change=lambda _v: self.app.toggle_theme(),
            tone="accent", button_width=110)
        theme.set_value("暗色" if self.app.is_dark() else "亮色")
        theme.pack(fill='x', pady=3)

        topmost = px.CycleRow(
            body, "窗口置顶", ["开", "关"],
            on_change=lambda v: self.app.set_topmost(v == "开"),
            tone="ok", button_width=110)
        topmost.set_value("开" if settings.get("always_on_top", True) else "关")
        topmost.pack(fill='x', pady=3)
        # Linux/X11：桌面环境（窗口管理器）不支持 EWMH 置顶时把开关置灰并说明原因，
        # 而不是留一个点了没反应的开关（见 ui/mini/topmost.py）。
        if not self.app.topmost_available():
            topmost.set_disabled(True, reason="本桌面环境不支持")

        avatar = px.CycleRow(
            body, "身材预览头像", ["开", "关"],
            on_change=lambda v: self.app.set_preview_avatar(v == "开"),
            tone="ok", button_width=110)
        avatar.set_value(
            "开" if settings.get("use_preview_image_as_avatar", True) else "关")
        avatar.pack(fill='x', pady=3)

        px.divider(body).pack(fill='x', pady=8)

        self.provider_row = px.CycleRow(
            body, "AI 服务商", self.app.ai_provider_names() or ["（未配置）"],
            on_change=self.app.set_ai_provider, tone="report",
            button_width=110)
        provider = self.app.settings.get("ai_provider")
        if self.app.ai_configs():
            self.provider_row.set_value(self.app.ai_provider_name(provider))
        self.provider_row.pack(fill='x', pady=3)

        buttons = px.transparent(body)
        buttons.pack(fill='x', pady=(6, 0))
        px.PixelButton(buttons, "编辑", lambda: self.app.push_screen("ai"),
                       tone="report", height=24).pack(
            side='left', fill='x', expand=True, padx=(0, 2))
        px.PixelButton(buttons, "新建", self.app.new_ai_profile,
                       tone="text_dim", height=24).pack(
            side='left', fill='x', expand=True, padx=(2, 0))

        footer = px.transparent(body)
        footer.pack(side='bottom', fill='x')
        px.label(footer, f"生成器 v{APP_VERSION}", tone="text_off", size=10).pack(
            anchor='w')


# ==================== AI 配置 ====================
class AIScreen(Screen):
    """编辑当前 AI 配置：名称 / 接口 URL / 模型 / API Key。"""

    TITLE = "AI 配置"

    def _build(self, body):
        self.profile_id = self.app.settings.get("ai_provider") or ""
        config = dict(self.app.ai_configs().get(self.profile_id, {}))

        self.name_var = tk.StringVar(value=config.get("name", ""))
        self.url_var = tk.StringVar(value=config.get("url", ""))
        self.model_var = tk.StringVar(value=config.get("model", ""))
        self.key_var = tk.StringVar(value=config.get("api_key", ""))

        templates = px.transparent(body)
        templates.pack(fill='x', pady=(0, 4))
        px.label(templates, "模板", tone="text_dim").pack(
            side='left', padx=(0, 4))
        for provider_id, defaults in PROVIDER_DEFAULTS.items():
            px.PixelButton(
                templates, defaults.get("name", provider_id),
                lambda d=defaults: self._fill(d), tone="text_dim",
                width=62, height=22).pack(side='left', padx=1)

        for title, var, secret in (("名称", self.name_var, False),
                                   ("接口 URL", self.url_var, False),
                                   ("模型", self.model_var, False),
                                   ("API Key", self.key_var, True)):
            row = px.transparent(body)
            row.pack(fill='x', pady=2)
            # 标签列固定最小宽度，四行的输入框左边对齐（tk 的 Label 宽度以字符计，
            # 固定像素宽度只能交给 grid 的 minsize）。
            row.columnconfigure(0, minsize=56)
            row.columnconfigure(1, weight=1)
            px.label(row, title, tone="text_dim").grid(
                row=0, column=0, sticky='w')
            px.entry(row, textvariable=var, height=24,
                     show="•" if secret else None).grid(
                row=0, column=1, sticky='ew')

        self.status = px.label(body, "", tone="text_off")
        self.status.pack(anchor='w', pady=(6, 0))

        buttons = px.transparent(body)
        buttons.pack(fill='x', side='bottom', pady=(8, 0))
        px.PixelButton(buttons, "测试", self._test, tone="accent",
                       height=24).pack(side='left', fill='x', expand=True, padx=(0, 2))
        if len(self.app.ai_configs()) > 1:
            px.PixelButton(buttons, "删除", self._delete, tone="danger",
                           height=24).pack(side='left', fill='x', expand=True, padx=2)
        px.PixelButton(buttons, "保存", self._save, tone="ok",
                       height=24).pack(side='left', fill='x', expand=True,
                                       padx=(2, 0))

    # ---------- 交互 ----------
    def _fill(self, defaults):
        self.name_var.set(defaults.get("name", ""))
        self.url_var.set(defaults.get("url", ""))
        self.model_var.set(defaults.get("model", ""))

    def _current(self) -> dict:
        return {
            "name": self.name_var.get().strip(),
            "url": self.url_var.get().strip(),
            "model": self.model_var.get().strip(),
            "api_key": self.key_var.get().strip(),
        }

    def _save(self):
        config = self._current()
        if not config["name"]:
            self.status.configure(text="请填写名称", text_color="danger")
            return
        if not self.profile_id:
            self.profile_id = "profile_" + uuid.uuid4().hex[:8]
        self.app.save_ai_profile(self.profile_id, config)
        self.app.pop_screen()

    def _delete(self):
        self.app.ask("删除该 AI 配置？", self._do_delete)

    def _do_delete(self):
        self.app.delete_ai_profile(self.profile_id)
        self.app.pop_screen()

    def _test(self):
        config = self._current()
        if not config["api_key"]:
            self.status.configure(text="请先填写 API Key", text_color="danger")
            return
        self.status.configure(text="正在测试连接…", text_color="text_dim")
        threading.Thread(target=self._do_test, args=(config,), daemon=True).start()

    def _do_test(self, config):
        try:
            client = create_client(self.profile_id, config["api_key"],
                                   base_url=config["url"] or None,
                                   model=config["model"] or None)
            ok, message = client.test_connection()
        except Exception as e:
            ok, message = False, str(e)
        self.after(0, lambda: self._test_done(ok, message))

    def _test_done(self, ok, message):
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        if ok:
            self.status.configure(text="连接成功", text_color="ok")
        else:
            self.status.configure(text=f"连接失败：{message or '未知错误'}",
                                  text_color="danger")


# ==================== 挑战包确认 ====================
class ChallengeScreen(Screen):
    """抽中挑战包后的确认屏：先看清包里是什么，再决定进不进。

    挑战用的是挑战包自带的角色、地标与描述，不和当前进度共用资源，因此进入前
    值得先看一眼：角色是谁、走哪个副本、带了哪些风格、以及一段简介。
    """

    TITLE = "挑战包"

    def _build(self, body):
        meta = self.app.challenge_meta() or {}
        inv = self.app.investigation

        info = px.ScrollFrame(body, fill="ink_alt", border="line")
        info.pack(fill='both', expand=True)
        grid = px.transparent(info.body)
        grid.pack(fill='x', padx=8, pady=8)
        grid.columnconfigure(1, weight=1)

        rows = [
            ("名称", inv.challenge_title or meta.get("pack_base", "—")),
            ("角色", meta.get("character_name", "—")),
            ("副本", meta.get("scenario_id", "") or "—"),
            ("地标", "、".join(meta.get("landmark_styles") or []) or "—"),
            ("描述", "、".join(meta.get("quip_styles") or []) or "—"),
        ]
        for row, (title, value) in enumerate(rows):
            px.label(grid, f"{title}▸", tone="text_off").grid(
                row=row, column=0, sticky='nw', padx=(0, 4), pady=1)
            px.label(grid, value, tone="text", wraplength=230,
                     justify='left').grid(row=row, column=1, sticky='w', pady=1)

        intro = (meta.get("intro") or "").strip()
        if intro:
            px.divider(grid).grid(row=len(rows), column=0, columnspan=2,
                                  sticky='ew', pady=(6, 4))
            px.label(grid, intro, tone="text_dim", size=11, wraplength=290,
                     justify='left').grid(row=len(rows) + 1, column=0,
                                          columnspan=2, sticky='w')
            tip_row = len(rows) + 2
        else:
            tip_row = len(rows)
        px.label(grid, "挑战使用包内自带的角色与数据，不消耗行动点。",
                 tone="text_off", wraplength=290, justify='left').grid(
            row=tip_row, column=0, columnspan=2, sticky='w', pady=(8, 0))
        info.bind_wheel(grid)

        buttons = px.transparent(body)
        buttons.pack(fill='x', pady=(6, 0))
        self.enter_btn = px.PixelButton(buttons, "进入挑战", self._enter,
                                        tone="challenge", height=26)
        self.enter_btn.pack(side='left', fill='x', expand=True, padx=(2, 0))
        px.PixelButton(buttons, "取消", self.app.pop_screen, tone="text_dim",
                       height=26).pack(side='left', fill='x', expand=True,
                                       padx=(0, 2))

    def _enter(self):
        # 先退掉确认屏再启动副本窗口，副本结束返回时直接回到主界面。
        self.app.pop_screen()
        self.app.launch_challenge()


# ==================== 聊天 ====================
class ChatScreen(Screen):
    """与当前角色私聊的整窗聊天屏。

    消息行靠左（角色）/ 靠右（玩家）；"已读不回"只保留为内部状态，界面不标注。
    回复节奏由服务层与投递控制器负责（docs/chat_delivery.md）：AI 决策后回复
    以 queued 入列并排定 available_at，本屏通过共用的 ChatDeliveryController
    到点重载、重渲染，期间显示"对方正在输入…"，渲染后统一标记玩家已读；界面
    只画 delivered/read 的角色消息。撤回窗口 = AI 应答完成之前（角色已"看到"
    即关闭，物理删除，视为没看到）。打开聊天时自动触发离线补话（有未读→
    回复未读；间隔够久→允许主动搭话），无补话条 UI。聊天对象是主界面当前
    载入的角色，换对象先回角色档案屏载入另一位。AI 请求在后台线程执行。
    """

    TITLE = "聊天"

    def _build(self, body):
        state = self.app.current_state
        if state is None:
            px.label(body, "还没有载入角色。", tone="text_dim", size=11).pack(
                anchor='w', pady=(10, 2))
            px.label(body, "先创建或载入一位少女，再来找她聊天。",
                     tone="text_off", size=10).pack(anchor='w')
            return

        self.state = state
        self.chat_state = self.app.chat_service().load_chat(state.giantess_id)
        self._pending = False
        self._recall_requested = False
        self._pending_row = None
        self._recall_btn = None

        nick = f"（{state.nick}）" if state.nick else ""
        px.label(body, f"{state.name}{nick}　态度 {self.chat_state.attitude:+d}",
                 tone="text_dim", size=10).pack(anchor='w', pady=(0, 4))

        self.listing = px.ScrollFrame(body, fill="ink_alt", border="line")
        self.listing.pack(fill='both', expand=True)
        self._render_messages()

        self.status = px.label(body, "", tone="text_off", size=10)
        self.status.pack(anchor='w', pady=(4, 0))

        row = px.transparent(body)
        row.pack(fill='x', pady=(2, 0))
        row.columnconfigure(0, weight=1)
        self.input_var = tk.StringVar()
        entry = px.entry(row, textvariable=self.input_var, height=26)
        entry.grid(row=0, column=0, sticky='ew')
        entry.bind("<Return>", lambda _e: self._send())
        self.send_btn = px.PixelButton(row, "发送", self._send, tone="ok",
                                       size=12, width=58, height=26)
        self.send_btn.grid(row=0, column=1, padx=(4, 0))

        # 打开聊天时先后台预热聊天参数（首次聊天前由 AI 一次性决定，失败
        # 静默——发送时会正式报错），再自动补话：
        # 有未读→回复未读；无未读且间隔够久→可能主动搭话
        # 投递节奏控制器：到点重载/重渲染/"正在输入"/标记已读
        self._delivery = ChatDeliveryController(
            self.app.chat_service(), after=self.after,
            on_reload=self._on_delivery_reload,
            on_status=self._on_delivery_status)
        self._delivery.attach(state)
        self.after(80, self._prewarm_then_catchup)

    # ---------- 投递节奏（与专业模式共用 ChatDeliveryController） ----------
    def _on_delivery_reload(self, chat_state):
        if not self.winfo_exists():
            return
        self.chat_state = chat_state
        self._render_messages()
        # 已投递消息已在本屏渲染：清标题栏徽标
        self.app._unread_chat.pop(self.state.giantess_id, None)
        self.app._refresh_chat_badge()

    def _on_delivery_status(self, kind: str):
        if not self.winfo_exists():
            return
        self._set_status("对方正在输入…" if kind == "typing" else "")

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

    # ---------- 自动补话（统一社交决策） ----------
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

    # ---------- 渲染 ----------
    def _render_messages(self):
        for widget in self.listing.body.winfo_children():
            widget.destroy()
        self._pending_row = None
        self._recall_btn = None
        for message in self.chat_state.messages:
            # 角色消息只画已投递/已读的：queued 由控制器到点后再出现
            if message.role == "char" and message.status not in ("delivered", "read"):
                continue
            self._append_row(message)
        self.listing.body.after(30, self._scroll_bottom)

    def _append_row(self, message, recallable: bool = False):
        is_user = message.role == "user"
        row = px.transparent(self.listing.body)
        row.pack(fill='x', pady=1)
        line = px.label(row, message.text,
                        tone="text" if is_user else "accent",
                        size=11, wraplength=250, justify='left', anchor='w')
        if is_user:
            line.pack(anchor='e', padx=(30, 2))
            if recallable:
                # 撤回窗口：角色"看到"之前，最后一条玩家消息可撤回
                self._pending_row = row
                self._recall_btn = px.PixelButton(
                    row, "撤回", self._recall, tone="text_dim",
                    size=10, width=34, height=18)
                self._recall_btn.pack(anchor='e', padx=(0, 2))
        else:
            line.pack(anchor='w', padx=(2, 30))
        self.listing.bind_wheel(row)

    def _scroll_bottom(self):
        if self.winfo_exists():
            self.listing.canvas.yview_moveto(1.0)

    def _set_status(self, text: str, tone: str = "text_off"):
        if self.winfo_exists():
            self.status.configure(text=text, text_color=px.color(tone))

    # ---------- 收发 ----------
    def _send(self):
        if self._pending:
            return
        text = self.input_var.get().strip()
        if not text:
            return
        self.input_var.set("")
        self._pending = True
        self._recall_requested = False
        self.send_btn.configure(state='disabled')
        self._set_status("")
        self._append_row(ChatMessage(role="user", text=text), recallable=True)
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


# ==================== 消息 / 确认 ====================
class MessageScreen(Screen):
    """整窗消息屏：一段文字加一到两个按钮，取代消息框与确认框。"""

    TITLE = "提示"

    def __init__(self, parent, app, text: str, buttons, tone: str = "accent",
                 title: str = "提示"):
        # Screen.__init__ 会读取 self.TITLE，这里先用实例属性覆盖类属性。
        self.TITLE = title
        self._text = text
        self._buttons = tuple(buttons)
        self._tone = tone
        super().__init__(parent, app)

    def _build(self, body):
        # 上下各放一个可伸缩空框，把面板挤到中间，像游戏里的对话窗。
        px.transparent(body).pack(fill='both', expand=True)
        panel = px.panel(body, fill="ink_alt", border=self._tone, bevel=True)
        panel.pack(fill='x')
        px.label(panel, self._text, tone="text", size=12, anchor='w',
                 justify='left', wraplength=300).pack(
            anchor='w', padx=8, pady=(8, 6))

        row = px.transparent(panel)
        row.pack(fill='x', padx=8, pady=(0, 8))
        self.buttons = []
        for index, (text, callback) in enumerate(self._buttons):
            tone = "ok" if index == 0 else "text_dim"
            button = px.PixelButton(row, text, self._resolve(callback), tone=tone,
                                    height=24)
            button.pack(side='left', fill='x', expand=True,
                        padx=(0 if index == 0 else 2, 0))
            self.buttons.append(button)
        px.transparent(body).pack(fill='both', expand=True)

    def _resolve(self, callback):
        """按钮先退出本屏、再执行回调。

        消息屏是盖在主界面之上的一屏：没带回调的按钮（确定 / 否 / 取消）到此为止，
        带了回调的（例如「继续生成下一步？」）则先让路，回调再推新屏或改主界面，
        否则会停在提示上不动。
        """
        def run():
            self.app.pop_screen()
            if callback is not None:
                callback()
        return run
