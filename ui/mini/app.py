"""挂件主窗口（低像素风）。

一扇窗口就是一个「小游戏」：顶部是调查卡（掷出世界 / 地标 / 副本 / 挑战），
中间是创建参数或角色卡，底部是四个动作按钮与报告区。所有原本需要弹窗的交互
（角色列表、设置、AI 配置、消息与确认）都改成整窗换屏，见 ui/mini/screens.py。

控件数量被刻意压到最低：选择项用「点一下换一个」的循环行，弹窗一律换成换屏，
不再有下拉框、多页选项卡、滑杆与保存按钮。
"""

import ctypes
import json
import os
import sys
import uuid
from tkinter import filedialog

from core.ai import resolve_ai_config
from core.models import BodyPreset, CharacterSnapshot, Personality
from services.chat import events as chat_events
from services.chat import pending_char_messages
from services.challenge_service import ChallengeService
from core import appearance
from ui.mini import pixel as px
from ui.mini.investigation import Investigation, Investigator
from ui.mini.params_panel import MiniParamsPanel
from ui.mini.report_view import MiniReportView
from ui.mini.screens import (AIScreen, ChallengeScreen, CharactersScreen,
                             MessageScreen, SettingsScreen)
from ui.mini.state_card import MiniStateCard

DEFAULT_GEOMETRY = "360x620"
MIN_SIZE = (330, 470)


def default_preset() -> BodyPreset:
    """挑战包不含身材数据，按原版用一个标准身材补位。"""
    return BodyPreset(
        name="标准", leg_ratio=0.5, foot_length_ratio=0.15, arm_span_ratio=0.4,
        index_finger_ratio=0.05, palm_length_ratio=0.1, chest_width_ratio=0.25,
        thigh_diameter_ratio=0.12, forearm_diameter_ratio=0.08,
        knee_height_ratio=0.3, ankle_height_ratio=0.085,
        finger_gap_ratio=0.02, stride_ratio=0.8)


class MiniApp:
    """桌面挂件版界面管理器。"""

    def __init__(self, root, context, world_manager, settings_repo):
        self.root = root
        self.context = context
        self.settings = context.settings
        self.world_manager = world_manager

        self._settings_repo = settings_repo
        self._landmark_repo = context.landmark_repo
        self._quip_repo = context.quip_repo
        self._preset_repo = context.preset_repo
        self._personality_repo = context.personality_repo
        self._scenario_repo = context.scenario_repo
        self._character_repo = context.character_repo
        self.world_state = getattr(context, "world_state", None)

        self.challenge_service = ChallengeService(
            self._settings_repo, self._character_repo, self._landmark_repo,
            self._quip_repo, self._scenario_repo, world_state=self.world_state)

        self.current_state: CharacterSnapshot = None
        self.investigation = Investigation()
        self.investigator = Investigator(self)
        self.auto_answer = None      # 测试用：True/False 时跳过确认屏直接应答
        self._screen_stack = []
        self._screen = None
        self._message_payload = None
        self._closing = False
        self._unread_chat = {}       # giantess_id -> 未读回复数
        self._chat_service = None
        # 正在跑的副本窗口（由宿主端口登记）。挂件关闭时若它还在，要先停掉
        # DPG，否则独立视口会拖住进程。
        self._active_dungeon_window = None

        self._apply_window()
        self._build_main()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        # 聊天事件广播：后台线程发来，经 after 投递回主线程刷新未读
        chat_events.subscribe(self._on_chat_event)
        # 首帧之后再掷出第一次调查，保证窗口先出现，不会让启动显得卡顿。
        self.root.after(80, self._first_investigate)

    # ==================== 窗口 ====================
    def _apply_window(self):
        self.root.title("巨大娘生成器")
        self.root.geometry(self.settings.get("window_geometry") or DEFAULT_GEOMETRY)
        self.root.minsize(*MIN_SIZE)
        px.bind_colors(self.root, {'bg': 'ink'})
        appearance.set_mode(self.theme_mode())
        self._apply_titlebar_theme()
        # 主窗口尚在映射前，DWM 会在窗口真正显示时把标题栏恢复成系统配色，
        # 因此映射后必须再刷一次（与对话框走的是同一条路）。
        self.root.bind("<Map>", lambda _e: self._apply_titlebar_theme(), add="+")
        self.root.attributes("-topmost", bool(self.settings.get("always_on_top", True)))

    def _apply_titlebar_theme(self):
        if not sys.platform.startswith("win"):
            return
        try:
            dark = self.is_dark()
            hwnd = self._window_handle()
            if not hwnd:
                return
            value = ctypes.c_int(1 if dark else 0)
            # Win10 2004+ 使用 20，更早版本使用 19
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 20, ctypes.byref(value), ctypes.sizeof(value)) != 0:
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 19, ctypes.byref(value), ctypes.sizeof(value))
        except Exception:
            pass

    def _window_handle(self):
        """主窗口的顶层 HWND：winfo_id() 给的是客户区，需再取一层父窗口。"""
        try:
            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            if hwnd:
                return hwnd
            return self.root.winfo_id() if self.root.winfo_ismapped() else 0
        except Exception:
            return 0

    def theme_mode(self) -> str:
        return "Dark" if self.settings.get("theme_mode", "Dark") == "Dark" else "Light"

    def is_dark(self) -> bool:
        return self.theme_mode() == "Dark"

    # ==================== 主界面布局 ====================
    def _build_main(self):
        self.main = px.surface(self.root)
        self.main.pack(fill='both', expand=True)

        self._build_titlebar(self.main)
        self._build_investigation_card(self.main)

        # 参数卡与角色卡共用同一格：进入角色模式后互相顶替。
        self.stack = px.transparent(self.main)
        self.stack.pack(fill='x', padx=6, pady=(0, 4))
        self.stack.columnconfigure(0, weight=1)

        self.params_panel = MiniParamsPanel(
            self.stack, self.context, self._preset_repo, self._personality_repo)
        self.params_panel.grid(row=0, column=0, sticky='ew', pady=3 )

        self.state_card = MiniStateCard(self.stack, self)
        self.state_card.grid(row=0, column=0, sticky='nsew', pady=3)
        self.state_card.grid_remove()

        self._build_actions(self.main)

        self.report_view = MiniReportView(self.main, self, self.context)
        self.report_view.pack(fill='both', expand=True, padx=6, pady=3)

        self.status_label = px.label(self.main, "", tone="text_off", size=12)
        self.status_label.pack(fill='x', padx=8, pady=3)

        self._set_mode("params")
        self._refresh_investigation_ui()

    def _build_titlebar(self, parent):
        bar = px.panel(parent, fill="ink_alt", border="line")
        bar.pack(fill='x', padx=6, pady=(6, 3))

        px.label(bar, "▚ 巨大娘生成器", size=14, tone="accent", bold=True).pack(
            side='left', padx=6, pady=4)
        # 「⇄」切到专业模式：销毁本窗口，由外壳在同进程内重建专业界面。
        px.PixelButton(bar, "⇄", self.switch_to_professional,
                       tone="text_dim", width=26, height=22).pack(
            side='right', padx=(2, 4), pady=4)
        px.PixelButton(bar, "▤", lambda: self.push_screen("characters"),
                       tone="text_dim", width=26, height=22).pack(
            side='right', padx=(2, 4), pady=4)
        self.chat_btn = px.PixelButton(bar, "✉", self.open_chat,
                                       tone="text_dim", width=26, height=22)
        self.chat_btn.pack(side='right', padx=(2, 4), pady=4)
        px.PixelButton(bar, "☰", lambda: self.push_screen("settings"),
                       tone="text_dim", width=26, height=22).pack(
            side='right', pady=4)

    def _build_investigation_card(self, parent):
        card = px.panel(parent, fill="ink_alt", border="line", bevel=True)
        card.pack(fill='x', padx=6, pady=3)

        grid = px.transparent(card)
        grid.pack(fill='x', padx=6, pady=(6, 2))
        grid.columnconfigure(1, weight=1)
        grid.columnconfigure(3, weight=1)

        self._inv_labels = {}
        for row, (key, title) in enumerate([("world", "世界"), ("landmark", "地标")]):
            self._inv_labels[key] = self._inv_cell(grid, row, 0, title)
        for row, (key, title) in enumerate([("dungeon", "副本"), ("quip", "描述")]):
            self._inv_labels[key] = self._inv_cell(grid, row, 2, title)

        px.PixelButton(card, "▶  调查  ◀", self.investigate, tone="accent",
                       size=14, height=28).pack(
            fill='x', padx=6, pady=(2, 6))

    def _inv_cell(self, grid, row, column, title):
        px.label(grid, f"{title}▸", tone="text_off").grid(
            row=row, column=column, sticky='w', padx=(0, 2), pady=1)
        value = px.label(grid, "—", tone="text")
        value.grid(row=row, column=column + 1, sticky='ew', padx=(0, 6), pady=1)
        return value

    def _build_actions(self, parent):
        row = px.transparent(parent)
        row.pack(fill='x', padx=6, pady=3)
        # 挂件宽度有限：显式给窄宽度，四个按钮同排才不会溢出。
        size = dict(size=13, height=26, width=68)

        self.create_btn = px.PixelButton(
            row, "创建", self.on_create_clicked, tone="ok", **size)
        self.report_btn = px.PixelButton(
            row, "报告", self.generate_report, tone="report", **size)
        self.dungeon_btn = px.PixelButton(
            row, "副本", self.enter_dungeon, tone="dungeon", **size)
        self.challenge_btn = px.PixelButton(
            row, "挑战", self.enter_challenge, tone="challenge", **size)

    def _set_mode(self, mode: str):
        """参数模式（未创建角色）与角色模式（已创建）之间的切换。"""
        self.mode = mode
        for btn in (self.create_btn, self.report_btn,
                    self.dungeon_btn, self.challenge_btn):
            btn.pack_forget()

        if mode == "params":
            self.state_card.grid_remove()
            self.state_card.stop_auto_recovery()
            self.params_panel.grid()
            self.create_btn.pack(side='left', fill='x', expand=True, padx=(0, 3))
        else:
            self.params_panel.grid_remove()
            self.state_card.grid()
            self.state_card.start_auto_recovery()
        self.report_btn.pack(side='left', fill='x', expand=True, padx=3)
        self.dungeon_btn.pack(side='left', fill='x', expand=True, padx=3)
        self.challenge_btn.pack(side='left', fill='x', expand=True, padx=(3, 0))

    # ==================== 换屏 ====================
    def push_screen(self, key: str):
        """打开一屏盖住主界面；pop_screen 逐层退回。"""
        self._screen_stack.append(key)
        self._render_screen()

    def pop_screen(self):
        if self._screen_stack:
            self._screen_stack.pop()
        self._render_screen()

    def _render_screen(self):
        if self._screen is not None:
            self._screen.destroy()
            self._screen = None
        if not self._screen_stack:
            return
        key = self._screen_stack[-1]
        self._screen = self._build_screen(key)
        self._screen.place(x=0, y=0, relwidth=1, relheight=1)
        self._screen.lift()
        self._screen.on_show()

    def _build_screen(self, key: str):
        if key == "characters":
            return CharactersScreen(self.root, self)
        if key == "settings":
            return SettingsScreen(self.root, self)
        if key == "ai":
            return AIScreen(self.root, self)
        if key == "challenge":
            return ChallengeScreen(self.root, self)
        if key == "chat":
            from ui.mini.screens import ChatScreen
            return ChatScreen(self.root, self)
        if key == "message":
            text, buttons, tone, title = self._message_payload
            return MessageScreen(self.root, self, text, buttons, tone, title)
        raise ValueError(f"未知屏幕: {key}")

    # ==================== 聊天 ====================
    def chat_service(self):
        """聊天服务（懒加载；角色历史按 giantess_id 天然隔离）。"""
        if self._chat_service is None:
            from services.chat import ChatService
            self._chat_service = ChatService()
            # 启动投递调度器（接管上次会话遗留的 queued 消息；进程级单例）
            from services.chat.delivery import get_scheduler
            get_scheduler(self._chat_service)
        return self._chat_service

    def open_chat(self):
        """打开聊天屏：清掉当前角色的未读并亮/灭标题栏徽标。"""
        if self.current_state is None:
            self.notify("还没有载入角色，先创建或载入一位再来聊天。",
                        title="聊天")
            return
        self._unread_chat.pop(self.current_state.giantess_id, None)
        self._refresh_chat_badge()
        self.push_screen("chat")

    def _refresh_chat_badge(self):
        unread = sum(self._unread_chat.values())
        self.chat_btn.configure(
            text="✉●" if unread else "✉",
            text_color=px.color("accent") if unread else px.color("text_dim"))

    def _on_chat_event(self, event):
        """聊天事件（后台线程）：投递回主线程累计未读。"""
        if self._closing:
            return
        try:
            self.root.after(0, lambda: self._handle_chat_event(event))
        except Exception:
            pass

    def _handle_chat_event(self, event):
        giantess_id = event.get("giantess_id")
        if not giantess_id:
            return
        # 聊天屏正开着：消息在屏上直接渲染并由控制器标记已读，无需徽标
        if (self._screen_stack and self._screen_stack[-1] == "chat"
                and self.current_state is not None
                and giantess_id == self.current_state.giantess_id):
            return
        # 徽标按存档重算：已投递但玩家未读的角色消息数（事件只是触发器）
        chat_state = self.chat_service().load_chat(giantess_id, blocking=False)
        if chat_state is None:
            return
        unread = len(pending_char_messages(chat_state))
        if unread:
            self._unread_chat[giantess_id] = unread
        else:
            self._unread_chat.pop(giantess_id, None)
        self._refresh_chat_badge()

    # ==================== 消息 / 确认 ====================
    def notify(self, text: str, tone: str = "accent", title: str = "提示"):
        """一段提示 + 一个确定键（整窗换屏，不再弹消息框）。"""
        if self.auto_answer is not None:
            return
        self._message_payload = (text, [("确定", None)], tone, title)
        self.push_screen("message")

    def ask(self, text: str, on_yes, on_no=None, tone: str = "accent",
            title: str = "确认"):
        """二选一确认；测试时用 auto_answer 直接应答，不出现界面。"""
        if self.auto_answer is not None:
            if self.auto_answer and on_yes:
                on_yes()
            elif not self.auto_answer and on_no:
                on_no()
            return
        self._message_payload = (text, [("是", on_yes), ("否", on_no)], tone, title)
        self.push_screen("message")

    # ==================== 调查 ====================
    def _first_investigate(self):
        """启动后掷出第一次调查，让挂件一打开就有可玩的配置。"""
        self.investigate(initial=True)

    def investigate(self, initial: bool = False):
        self._set_status("正在调查…")
        self.root.update_idletasks()
        try:
            self.investigation = self.investigator.investigate()
        except Exception as e:
            self._set_status(f"调查失败：{e}")
            self.notify(f"调查失败：\n{e}", tone="danger", title="出错了")
            return

        self.context.apply_context_settings()
        self.params_panel.refresh_choices()
        self._refresh_investigation_ui()
        self._sync_world_hint()
        self._save_settings()

        inv = self.investigation
        if initial:
            self._set_status(f"调查完成：{inv.world_text()} / {inv.scenario_text()}")
        else:
            self._set_status(
                f"调查完成：{inv.world_text()} · 地标 {inv.landmark_text()}"
                f" · 副本 {inv.scenario_text()} · 描述 {inv.quip_text()}"
                + ("　★抽中挑战包" if inv.has_challenge else ""))

    def _refresh_investigation_ui(self):
        inv = self.investigation
        values = {
            "world": inv.world_text(),
            "landmark": inv.landmark_text(),
            "dungeon": inv.scenario_text(),
            "quip": inv.quip_text(),
        }
        for key, label in self._inv_labels.items():
            label.configure(text=self._shorten(values.get(key, "—")))

        self.challenge_btn.configure(
            state='normal' if inv.has_challenge else 'disabled',
            text_color=px.color("challenge") if inv.has_challenge
            else px.color("text_off"),
            border_color=px.color("challenge") if inv.has_challenge
            else px.color("line"))
        self.dungeon_btn.configure(
            state='normal' if inv.has_scenario else 'disabled',
            text_color=px.color("dungeon") if inv.has_scenario
            else px.color("text_off"),
            border_color=px.color("dungeon") if inv.has_scenario
            else px.color("line"))

    @staticmethod
    def _shorten(text: str, limit: int = 12) -> str:
        text = text or "—"
        return text if len(text) <= limit else text[:limit - 1] + "…"

    def _sync_world_hint(self):
        state = self.world_state
        self._world_suffix = (f"▚{state.pack_name}"
                              if state is not None and state.active else "")

    # ==================== 探索模式 ====================
    def on_create_clicked(self):
        """创建角色：优先复用当前报告数据，与探索模式原行为一致。"""
        if self.report_view.last_report is not None:
            self.ask("用当前报告的数据创建角色？", self._create_from_report,
                     self.create_character)
        else:
            self.create_character()

    def _create_from_report(self):
        self.create_character(reuse_last_report=True)

    def create_character(self, reuse_last_report: bool = False):
        report = self.report_view.last_report
        try:
            if reuse_last_report and report is not None:
                snapshot = self.context.character_from_core_or_report(report)
            else:
                core = self._core_from_params()
                if core is None:
                    return
                snapshot = self.context.character_from_core_or_report(core)
        except Exception as e:
            self.notify(f"创建失败：\n{e}", tone="danger", title="出错了")
            return

        self.current_state = snapshot
        self.state_card.update_state(snapshot)
        self._set_mode("state")
        self._set_status(f"邂逅了 {snapshot.name}　身高 {snapshot.height:.1f} 米")

    def generate_report(self):
        if self.current_state is not None:
            report = self.context.report_from_core_or_character(
                self.current_state,
                self.context.selected_styles,
                self.context.selected_quip_styles,
                consume_points=self.current_state.report_generated,
                state_service=self.context.state_service)
            if report is not None:
                self.current_state.report_generated = True
                self._character_repo.save(self.current_state)
        else:
            core = self._core_from_params()
            if core is None:
                return
            try:
                report = self.context.report_from_core_or_character(
                    core, self.context.selected_styles,
                    self.context.selected_quip_styles, consume_points=False)
            except Exception as e:
                self.notify(f"生成失败：\n{e}", tone="danger", title="出错了")
                return

        if report is None:
            self.notify("行动点数不足以生成报告。", tone="danger", title="点数不足")
            return

        self.report_view.render_report(report)
        if self.current_state is not None:
            self.state_card.update_state(self.current_state)
            # 挂件版没有保存按钮：有角色时报告一律直接归档。
            self.report_view.archive_report(
                self.current_state.giantess_id, self.current_state.name)
        self._set_status(f"报告完成　{report.height:.1f} 米　"
                         f"{self._shorten(report.name, 8)}")

    def _core_from_params(self):
        """把当前参数字面交给创建服务，随机性格 / 身材也在这里完成。"""
        params = self.params_panel.get_params()
        try:
            return self.context.creation_service.core_from_params(
                params, self.settings, self._preset_repo, self._personality_repo)
        except Exception as e:
            self.notify(f"参数无效：\n{e}", tone="danger", title="出错了")
            return None

    # ==================== 角色管理 ====================
    def collect_characters(self):
        """读取档案目录下全部角色的摘要信息（按更新时间倒序）。"""
        states_dir = self._character_repo.states_dir
        if not os.path.exists(states_dir):
            return []
        entries = []
        for folder in sorted(os.listdir(states_dir)):
            info_path = os.path.join(states_dir, folder, "info.json")
            if not os.path.isfile(info_path):
                continue
            try:
                with open(info_path, 'r', encoding='utf-8') as handle:
                    data = json.load(handle)
            except Exception:
                continue
            entries.append({
                "id": data.get("giantess_id", folder),
                "name": data.get("name", ""),
                "nick": data.get("nick", ""),
                "height": data.get("height", 0.0),
                "updated_at": data.get("updated_at", ""),
            })
        entries.sort(key=lambda item: item["updated_at"], reverse=True)
        return entries

    def load_character(self, giantess_id: str):
        state = self.context.load_character_state(giantess_id)
        if state is None:
            self.notify(f"无法加载角色「{giantess_id}」", tone="danger", title="出错了")
            return
        self.current_state = state
        self.report_view.clear()
        self.state_card.update_state(state)
        self._set_mode("state")
        # 离线积压的已投递未读回复：点亮标题栏 ✉ 徽标（按存档重算）
        chat_state = self.chat_service().load_chat(giantess_id, blocking=False)
        self._unread_chat[giantess_id] = (
            len(pending_char_messages(chat_state))
            if chat_state is not None else 0)
        self._refresh_chat_badge()
        self._set_status(f"载入 {state.name}　{state.height:.1f} 米　"
                         f"行动点 {state.action_points}")

    def export_character(self):
        state = self.current_state
        if state is None:
            return
        file_path = filedialog.asksaveasfilename(
            title="导出角色卡或档案",
            initialfile=state.name or "角色",
            defaultextension=".html",
            filetypes=[("角色档案", "*.html"), ("角色卡", "*.json"),
                       ("所有文件", "*.*")])
        if not file_path:
            return
        lower = file_path.lower()
        if lower.endswith((".mhtml", ".mht", ".htm")):
            file_path = file_path.rsplit(".", 1)[0] + ".html"
            lower = file_path.lower()
        if lower.endswith(".html"):
            from services.character_service.archive_export import export_character_mhtml
            try:
                export_character_mhtml(
                    state, file_path,
                    show_casualties=bool(self.settings.get("show_casualties", True)))
            except Exception as e:
                self.notify(f"导出档案失败：\n{e}", tone="danger", title="出错了")
                return
            self._set_status(f"档案已导出　{os.path.basename(file_path)}")
            return
        if lower.endswith(".chara.json"):
            card_path = file_path
        elif lower.endswith(".json"):
            card_path = file_path[:-5] + ".chara.json"
        else:
            card_path = file_path + ".chara.json"
        self._export_card(state, card_path)

    def _export_card(self, state, card_path: str):
        try:
            data = self.context.build_export_card_from_state(state)
        except ValueError as e:
            self.notify(str(e), tone="danger", title="出错了")
            return
        try:
            with open(card_path, 'w', encoding='utf-8') as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
        except OSError as e:
            self.notify(f"保存失败：\n{e}", tone="danger", title="出错了")
            return
        self._set_status(f"角色卡已导出　{os.path.basename(card_path)}")

    def delete_character(self):
        state = self.current_state
        if state is None:
            return
        self.ask(f"永久删除「{state.name}」？\n此操作不可恢复。", self._do_delete,
                 tone="danger", title="删除角色")

    def _do_delete(self):
        state = self.current_state
        if state is None:
            return
        self._character_repo.delete(state.giantess_id)
        self._unread_chat.pop(state.giantess_id, None)
        self._refresh_chat_badge()
        name = state.name
        self.unload_character(confirm=False)
        self._set_status(f"已删除 {name}")

    def unload_character(self, confirm: bool = True):
        if self.current_state is None:
            return
        if confirm:
            self.ask("卸下当前角色？\n未保存的修改会丢失。", self._do_unload)
            return
        self._do_unload()

    def _do_unload(self):
        self.state_card.stop_auto_recovery()
        self.current_state = None
        self.state_card.update_state(None)
        self.report_view.clear()
        self.params_panel.reset()
        self._set_mode("params")
        self._set_status("已卸下角色，可重新掷骰")

    # ==================== 副本 ====================
    def _dungeon_payload(self):
        """返回 (副本数据, 角色状态)；无角色时用当前参数即时构造一份临时数据。"""
        if self.current_state is not None:
            return self.context.dungeon_data_from_any(self.current_state), self.current_state
        core = self._core_from_params()
        if core is None:
            return None, None
        return self.context.dungeon_data_from_any(core), None

    def enter_dungeon(self):
        inv = self.investigation
        if not inv.has_scenario:
            self.notify("本次调查没有可用副本方案，请再调查一次。",
                        tone="danger", title="无法进入")
            return
        config = self._scenario_repo.load_config(inv.scenario_id)
        if config is None:
            self.notify(f"无法加载副本配置「{inv.scenario_id}」", tone="danger",
                        title="出错了")
            return

        # 挂件跳过了副本窗口的入口选择阶段，也就跳过了入口阶段那道方案校验，
        # 坏方案会在会话中途才炸。这里补上同一套校验：错误级问题直接拦下。
        # 延迟导入：dungeon 包不参与挂件的日常启动路径。
        from dungeon.validate import (format_diagnostics, has_errors,
                                      validate_scenario_config)
        diagnostics = validate_scenario_config(
            config, scenario_dir=self._scenario_repo.scenario_dir(inv.scenario_id))
        if has_errors(diagnostics):
            self.notify(
                f"副本方案「{inv.scenario_id}」存在无法运行的问题：\n\n"
                f"{format_diagnostics(diagnostics)}",
                tone="danger", title="无法进入")
            return

        payload, character = self._dungeon_payload()
        if payload is None:
            return

        # 进入消耗：探索模式且已加载角色时按副本配置扣除行动点数
        if character is not None:
            try:
                entry_cost = max(0, int(config.get("entry_action_cost", 0) or 0))
            except (TypeError, ValueError):
                entry_cost = 0
            if entry_cost > 0:
                if not self.context.state_service.consume_action_points(
                        character, entry_cost):
                    self.notify(
                        f"进入该副本需要 {entry_cost} 行动点，"
                        f"当前仅剩 {character.action_points} 点。",
                        tone="danger", title="行动点不足")
                    return
                self._character_repo.save(character)
                self.state_card.update_state(character)

        self._launch_dungeon(
            payload=payload, config=config, scenario_id=inv.scenario_id,
            character=character, mode="explore")

    def enter_challenge(self):
        """「挑战」键只负责亮起时看一眼挑战包：确认屏之后才真正进入。"""
        inv = self.investigation
        if not inv.has_challenge:
            self.notify("本次调查没有抽到挑战包，请再调查一次。",
                        tone="danger", title="无法进入")
            return
        if self.challenge_meta() is None:
            self.notify("无法读取挑战包内容。", tone="danger", title="出错了")
            return
        self.push_screen("challenge")

    def challenge_meta(self):
        """当前抽中的挑战包元数据（供确认屏展示）；读不到返回 None。"""
        base = self.investigation.challenge_base
        for meta in self.challenge_service.get_all_metas():
            if meta.get("pack_base") == base:
                return meta
        return None

    def launch_challenge(self):
        """确认屏点了「进入」之后才启动挑战副本。"""
        inv = self.investigation
        try:
            data = self.challenge_service.open_challenge_by_name(inv.challenge_base)
        except Exception as e:
            self.notify(f"打开挑战包失败：\n{e}", tone="danger", title="出错了")
            return
        if not data:
            self.notify("无法读取挑战包内容。", tone="danger", title="出错了")
            return

        payload, config, scenario_id = self.challenge_payload(data)
        if payload is None:
            return

        # 挑战包自带地标 / 描述数据，临时接管风格选择，副本窗口返回后还原。
        saved_styles = list(self.context.selected_styles)
        saved_quips = list(self.context.selected_quip_styles)
        self.context.update_styles(
            list(data.get("landmark_styles") or saved_styles),
            list(data.get("quip_styles") or saved_quips))
        try:
            self._launch_dungeon(payload=payload, config=config,
                                 scenario_id=scenario_id, character=None,
                                 mode="challenge")
        finally:
            self.context.update_styles(saved_styles, saved_quips)

    def challenge_payload(self, data: dict):
        """把挑战包内容翻译成副本窗口参数：返回 (payload, config, scenario_id)。

        挑战包不含身材数据，按原版用标准身材补位；性格缺失则视为包损坏。
        """
        char_data = data.get("character_data", {}) or {}
        raw_personality = char_data.get("personality")
        if not raw_personality:
            self.notify("挑战包中缺少性格数据。", tone="danger", title="出错了")
            return None, None, ""
        return (
            {
                "name": char_data.get("name", "未知"),
                "nick": char_data.get("nick", ""),
                "height": char_data.get("height", 1.6),
                "original_height": char_data.get("original_height", 1.6),
                "personality_obj": Personality.from_dict(raw_personality),
                "preset_obj": default_preset(),
                "body_parts": char_data.get("body_parts", {}),
                "intro_hidden": char_data.get("intro_hidden", ""),
                "intro_visible": char_data.get("intro_visible", ""),
                "selected_tags": char_data.get("selected_tags", []),
                "greed": char_data.get("greed", 0),
                "uploaded_image": None,
            },
            data.get("scenario_config", {}) or {},
            data.get("scenario_id", "") or "",
        )

    def _launch_dungeon(self, *, payload, config, scenario_id, character, mode):
        from dungeon.window import DungeonSessionWindow
        from ui.common.tk_host import TkHost
        from ui.mini.dialogs import MiniDialogs
        ai_config = resolve_ai_config(self.settings)
        dungeon_font = self.settings.get("dungeon_font") or "Microsoft YaHei"
        # 宿主端口：副本窗口只经它取尺寸/DPI、显隐宿主、弹收尾提示。
        # owner=self —— 挂件主控不是 Tk 控件，不显式指名就登记不到活动窗口，
        # 挂件关闭时停不掉副本视口。
        # dialogs=MiniDialogs —— 不用 CTk 那套弹窗（纯 Tk 根下会卡住）。
        host = TkHost(self.root, owner=self, dialogs=MiniDialogs(self.root))
        # 不传 scenario_ids —— 挂件版已由「调查」掷好方案，跳过入口选择阶段。
        window = DungeonSessionWindow(
            self.root,
            name=payload["name"], nick=payload.get("nick", ""),
            height=payload["height"], personality=payload["personality_obj"],
            preset=payload.get("preset_obj"), greed=payload.get("greed", 0),
            original_height=payload.get("original_height", 1.6),
            intro_hidden=payload.get("intro_hidden", ""),
            intro_visible=payload.get("intro_visible", ""),
            tags=payload.get("selected_tags", []),
            uploaded_image=payload.get("uploaded_image"),
            scenario_config=config, scenario_repo=self._scenario_repo,
            merged_landmarks=self.context.merged_landmarks,
            merged_quips=self.context.quips,
            selected_styles=self.context.selected_styles,
            selected_quip_styles=self.context.selected_quip_styles,
            detail_pools=self.context.detail_pools,
            ai_config=ai_config, is_replay=False, replay_data=None,
            scenario_id=scenario_id, dungeon_font=dungeon_font,
            body_parts=payload.get("body_parts", {}),
            character=character, character_repo=self._character_repo,
            gui=self, mode=mode, host=host)
        result = window.run()
        # 入口阶段失败（配置缺失/行动点数不足/校验错误）：窗口已关闭，在主线程提示
        if result.failed:
            self.notify(result.launch_error, tone="danger", title="无法进入")
            return
        # 副本窗口期间主窗口被隐藏，返回后重新贴回并同步行动点数。
        self.root.deiconify()
        self.root.lift()
        if self.current_state is not None:
            self.state_card.update_state(self.current_state)

    # ==================== 设置（由设置屏调用） ====================
    def toggle_theme(self):
        theme = "Light" if self.is_dark() else "Dark"
        self.settings["theme_mode"] = theme
        # 纯 tkinter 不会自己重刷：切模式后由 appearance 广播，pixel 按登记表重绘。
        appearance.set_mode(theme)
        self._apply_titlebar_theme()
        self.report_view.refresh_theme()
        self.state_card.refresh_theme()
        self.params_panel.refresh_theme()
        self._save_settings()

    def set_topmost(self, enabled: bool):
        self.settings["always_on_top"] = bool(enabled)
        self.root.attributes("-topmost", bool(enabled))
        self._save_settings()

    def set_preview_avatar(self, enabled: bool):
        self.settings["use_preview_image_as_avatar"] = bool(enabled)
        self._save_settings()

    # ---------- AI 配置 ----------
    def ai_configs(self) -> dict:
        return self.settings.get("ai_configs") or {}

    def ai_provider_names(self):
        return [cfg.get("name", pid) for pid, cfg in self.ai_configs().items()]

    def ai_provider_name(self, provider_id: str) -> str:
        return (self.ai_configs().get(provider_id) or {}).get("name", provider_id or "")

    def set_ai_provider(self, name: str):
        for pid, cfg in self.ai_configs().items():
            if cfg.get("name", pid) == name:
                self.settings["ai_provider"] = pid
                break
        self._save_settings()

    def new_ai_profile(self):
        profile_id = "profile_" + uuid.uuid4().hex[:8]
        self.settings["ai_provider"] = profile_id
        self.settings.setdefault("ai_configs", {})[profile_id] = {
            "name": "新配置", "url": "", "model": "", "api_key": ""}
        self._save_settings()
        self.push_screen("ai")

    def save_ai_profile(self, profile_id: str, config: dict):
        self.settings.setdefault("ai_configs", {})[profile_id] = config
        self.settings["ai_provider"] = profile_id
        self._save_settings()

    def delete_ai_profile(self, profile_id: str):
        self.settings.get("ai_configs", {}).pop(profile_id, None)
        remaining = self.ai_configs()
        self.settings["ai_provider"] = next(iter(remaining), "")
        self._save_settings()

    # ==================== 生命周期 ====================
    def _save_settings(self):
        try:
            self.settings["selected_styles"] = list(self.context.selected_styles)
            self.settings["selected_quip_styles"] = list(self.context.selected_quip_styles)
            self.settings["window_geometry"] = self.root.geometry()
            self._settings_repo.save(self.settings)
        except Exception as e:
            print(f"[Warning] 保存设置失败: {e}")

    def _set_status(self, text: str):
        suffix = getattr(self, "_world_suffix", "")
        self.status_label.configure(text=f"{text}　{suffix}" if suffix else text)

    def switch_to_professional(self):
        """切到专业模式：停掉挂件的自动恢复计时器，再让外壳销毁本窗口。

        外壳随后会重建一个 ``customtkinter`` 根窗口并启动专业界面——同进程、
        同一份 ``data/``，只是界面层换掉。
        """
        from app_shell import MODE_PRO, switch_to

        # 副本视口是独立顶层窗口，切换界面会把它连同 Tk 根一起带走，
        # 会话结果无从回收，因此进行中直接拒绝切换。
        if self._active_dungeon_window is not None:
            self.notify("副本进行中，请先结束副本再切换界面。",
                        tone="danger", title="无法切换")
            return
        self._closing = True
        self.state_card.stop_auto_recovery()
        switch_to(self.root, MODE_PRO, save=self._save_settings)

    def on_closing(self):
        if self._closing:
            return
        self._closing = True
        self.state_card.stop_auto_recovery()
        try:
            self._save_settings()
        except Exception as e:
            print(f"[Warning] 退出前保存失败: {e}")
        # 副本视口是独立顶层窗口：还开着就先让它停下，否则进程退不干净。
        # 走窗口自己的公开入口，挂件层因此不必 import dearpygui。
        if self._active_dungeon_window is not None:
            try:
                self._active_dungeon_window.request_close()
            except Exception:
                pass
        os._exit(0)
