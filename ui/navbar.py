import customtkinter as ctk

from paths import APP_VERSION
from ui.common.theme import (
    NAV_BG, NAV_HOVER, NAV_TEXT, NAV_TITLE,
    NAV_SELECTED_BG, NAV_SELECTED_TEXT, NAV_WORLD_BG, NAV_WORLD_GREEN,
    NAV_WORLD_HOVER, NAV_WORLD_SELECTED_BG, NAV_WORLD_SELECTED_TEXT, NAV_WORLD_TEXT,
    NAV_WORLD_TITLE,
)
from ui.common import fonts as ui_fonts


class NavigationBar(ctk.CTkFrame):
    """左侧导航栏组件

    ``on_switch`` 是页面切换回调（点页面按钮）；``on_switch_ui`` 是**界面模式**
    切换回调（点底部「ME模式」）。后者不是换页而是换掉整套界面——窗口会被销毁
    重建，所以单独放一个 action 按钮，不混进页面列表。
    """

    def __init__(self, master, on_switch, on_switch_ui=None, **kwargs):
        super().__init__(master, width=200, height=60, corner_radius=0,
                         fg_color=NAV_BG, **kwargs)
        self.pack_propagate(False)
        self._on_switch = on_switch
        self._on_switch_ui = on_switch_ui
        self._buttons = {}
        self._frames = {}
        self._current_page = None
        self._world_active = False

        self._build()

    def _build(self):
        self._title_label = ctk.CTkLabel(self, text="导航菜单", font=ui_fonts.ui_font(14, "bold"),
                                         text_color=NAV_TITLE)
        self._title_label.pack(pady=(12, 6))

        pages = [
            ("  🧭  探索模式", "generator"),
            ("  🎯  挑战模式", "challenge"),
            ("  📑  文本管理", "text_mgmt"),
            ("  🎭  副本编辑", "dungeon"),
            ("  ⚙️  设置", "settings"),
        ]

        for text, key in pages:
            initial_color = NAV_SELECTED_BG if key == "generator" else "transparent"
            btn = ctk.CTkButton(self, text=text, anchor='w', height=32,
                                fg_color=initial_color,
                                text_color=NAV_SELECTED_TEXT if key == "generator" else NAV_TEXT,
                                hover_color=NAV_HOVER,
                                font=ui_fonts.ui_font(13),
                                command=lambda k=key: self._on_switch(k))
            btn.pack(fill='x', padx=9, pady=3)
            self._buttons[key] = btn

        foots = ["app_version", "worldpack_version", "packloaded"]
        for foot in foots:
            frame = ctk.CTkFrame(self, fg_color="transparent")
            frame.pack(side='bottom', fill='x')
            self._frames[foot] = frame

        # 底部「ME模式」入口：与版本页脚同属导航栏底部固定区（后 pack 的
        # bottom 排在先 pack 的上面，所以它会停在页脚正上方）。界面模式切换会
        # 重建整个窗口，因此与页面按钮分开放，避免被当成「换一页」。
        self._switch_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._switch_frame.pack(side='bottom', fill='x')
        self.ui_switch_btn = ctk.CTkButton(
            self._switch_frame, text="  ⇄  ME模式", anchor='w', height=32,
            fg_color="transparent", text_color=NAV_TEXT, hover_color=NAV_HOVER,
            font=ui_fonts.ui_font(13), command=self._on_ui_switch_clicked)
        self.ui_switch_btn.pack(fill='x', padx=9, pady=(6, 2))

        # 底部容器：版本页脚与世界包信息统一收纳，使导航栏底部更紧凑
        self.app_version_label = ctk.CTkLabel(
            self._frames["app_version"], text=f"生成器版本：v{APP_VERSION}", font=ui_fonts.ui_font(12),
            text_color=NAV_TEXT)
        self.app_version_label.pack(side="left", padx=22, pady=(0, 14))

        self.worldpack_version_label = ctk.CTkLabel(
            self._frames["worldpack_version"], text="", font=ui_fonts.ui_font(11),
            text_color=NAV_WORLD_GREEN)
        self.worldpack_version_label.pack(side="left", padx=22)

        self.packloaded_label = ctk.CTkLabel(
            self._frames["packloaded"], text="", font=ui_fonts.ui_font(11, "bold"),
            text_color=NAV_WORLD_GREEN)
        self.packloaded_label.pack(side="left", padx=14, pady=2)

    def _on_ui_switch_clicked(self):
        """「ME模式」按钮：只负责转发，确认框与拦截都在主窗口管理器里。"""
        if self._on_switch_ui is not None:
            self._on_switch_ui()

    def set_active(self, page_key):
        """高亮指定按钮，其余恢复默认"""
        self._current_page = page_key
        self._apply_world_style()

    def set_world_active(self, active, world_name="", world_version="",
                         has_behavior_pack=False):
        """世界包加载状态：导航栏整体淡绿色风格 + 底部世界包信息区。

        程序版本号始终显示在导航栏底部；加载世界包时额外展示世界包版本，
        并在包含行为包时提示行为已更改。
        """
        self._world_active = bool(active)
        if active:
            self.worldpack_version_label.configure(
                text=f"世界包版本：v{world_version}" if world_version else "世界包版本：未知")
            if has_behavior_pack:
                self.packloaded_label.configure(text=f"🌍 {world_name} （行为已更改）"
                if world_name else "🌍 世界包已加载 （行为已更改）")
            else:
                self.packloaded_label.configure(text=f"🌍 {world_name}" if world_name else "🌍 世界包已加载")
        else:
            self.worldpack_version_label.configure(text="")
            self.packloaded_label.configure(text="")
        self._apply_world_style()

    def _apply_world_style(self):
        """世界包启用时导航栏整体变为淡绿色，标题与按钮同步切换为绿色主题。"""
        if self._world_active:
            self.configure(fg_color=NAV_WORLD_BG)
            title_color = NAV_WORLD_TITLE
            sel_bg, sel_text = NAV_WORLD_SELECTED_BG, NAV_WORLD_SELECTED_TEXT
            bg, text, hover = "transparent", NAV_WORLD_TEXT, NAV_WORLD_HOVER
            footer_color = NAV_WORLD_TEXT
        else:
            self.configure(fg_color=NAV_BG)
            title_color = NAV_TITLE
            sel_bg, sel_text = NAV_SELECTED_BG, NAV_SELECTED_TEXT
            bg, text, hover = "transparent", NAV_TEXT, NAV_HOVER
            footer_color = NAV_TEXT
        self._title_label.configure(text_color=title_color)
        self.app_version_label.configure(text_color=footer_color)
        # 「ME模式」不是页面按钮，不参与高亮，只跟随整体文字/悬停配色
        self.ui_switch_btn.configure(text_color=text, hover_color=hover)
        for key, btn in self._buttons.items():
            if key == self._current_page:
                btn.configure(fg_color=sel_bg, text_color=sel_text, hover_color=hover)
            else:
                btn.configure(fg_color=bg, text_color=text, hover_color=hover)
