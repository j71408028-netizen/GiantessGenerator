"""覆盖式屏幕：挂件版用整窗换屏取代所有对话框。

原先的角色选择、设置、AI 配置与消息 / 确认框都是独立弹窗，在「小游戏」的观感
里既突兀又容易和置顶窗口抢层级。这里把它们统一做成同一扇窗口内的「一屏」：标题
栏 + 内容区，左上角一个 ◀ 返回，内容直接盖住主界面，不再有第二个窗口。
"""

import os
import threading
import uuid

import tkinter as tk

from ai import PROVIDER_DEFAULTS, create_client
from logic import format_size
from paths import APP_VERSION
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
