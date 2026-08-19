import ctypes
import json
import os
import sys
import tkinter as tk
from typing import Dict, List

import customtkinter as ctk

import ui.common.dialogs
import ui.common.ctk_patch  # noqa: F401  模式切换时同步刷新 CTk 控件 Frame 底色，避免几何重排露旧色
from context import ExplorationContext
from paths import assets_dir
from persistence import QuipRepo, DungeonRepo, CharacterRepo
from persistence import SettingsRepo, LandmarkRepo, PresetRepo, PersonalityRepo
from services.challenge_service import ChallengeService
from services.world_service import WorldManager
from ui.challenge import ChallengeModePanel
from ui.exploration.exp_frame import ExplorationPanel
from ui.landmark import LandmarkCardManager
from ui.navbar import NavigationBar
from ui.quip_mgr import QuipCardManager
from ui.scenario.sc_frame import ScenarioEditor
from ui.settings import SettingsPanel
from ui.exploration.news_dlg import NewsDialog
from ui.common.loading import LoadingPage
from ui.common.theme import BASE, SOFT, HOVER_ALT, BORDER_ALT
from ui.common import fonts as ui_fonts


class MainWindowManager:
    """界面管理器 - 负责窗口布局、页面切换、UI 回调"""

    def __init__(self, root, context: ExplorationContext, on_progress=None,
                 world_manager=None):
        self.root = root
        self.root.title("巨大娘生成器")
        self.root.geometry("1280x720")
        self._closing = False
        self._active_dungeon_window = None
        self._on_progress = on_progress

        self.context = context

        # 世界包管理（与 main.py 启动时恢复的 world_manager 保持一致）
        self.world_manager: WorldManager = world_manager or WorldManager(data_dir="data")
        self.world_state = getattr(context, "world_state", None) \
            or self.world_manager.world_state

        # 从 context 获取 repo 引用（保持与旧 gui_ref 兼容）
        self._settings_repo: SettingsRepo = context.settings_repo
        self._landmark_repo: LandmarkRepo = context.landmark_repo
        self._preset_repo: PresetRepo = context.preset_repo
        self._personality_repo: PersonalityRepo = context.personality_repo
        self._quip_repo: QuipRepo = context.quip_repo
        self._dungeon_repo: DungeonRepo = context.dungeon_repo
        self._character_repo: CharacterRepo = context.character_repo

        self.settings = context.settings
        self.current_style = self._landmark_repo.default_style
        self.current_quip_style = self._quip_repo.default_style

        # 主题在 main() 创建任何控件前已经应用。这里仅读取当前值，避免重复切换
        # 全局外观模式导致已创建控件再次重绘。
        self.theme_mode = self.settings.get("theme_mode", "Light")
        self.color_theme = self.settings.get("color_theme", "blue")

        # Windows 下禁用 customtkinter 的标题栏切换（withdraw/deiconify 会导致整窗闪烁、
        # 并可能丢失窗口图标），改为直接调用 DWM API，避免浅色/深色切换时的加载闪烁。
        # layered 窗口样式让 DWM 在最小化/还原期间保留最后一帧合成画面。
        if sys.platform.startswith("win"):
            self.root._deactivate_windows_window_header_manipulation = True
            self._apply_titlebar_theme()
            self._enable_layered_window()
            self._install_restore_cover()
        self._apply_app_icon()

        # 快捷引用
        self.world_setting = self.context.world_setting
        self.selected_styles = self.context.selected_styles
        self.selected_quip_styles = self.context.selected_quip_styles

        # ── 构建主框架 ──
        self.main_frame = ctk.CTkFrame(root, corner_radius=0, fg_color=BASE)
        self.main_frame.pack(fill='both', expand=True)

        self.nav_bar = NavigationBar(self.main_frame, on_switch=self.show_page)
        self.nav_bar.pack(side='left', fill='y')

        self.content_frame = ctk.CTkFrame(self.main_frame, corner_radius=0,
                                           fg_color=BASE)
        self.content_frame.pack(side='left', fill='both', expand=True)

        # 创建各页面框架
        self.pages = {}
        for key in ["generator", "text_mgmt", "dungeon", "challenge", "settings"]:
            frame = ctk.CTkFrame(self.content_frame, corner_radius=0,
                                  fg_color=BASE)
            frame.place(relwidth=1, relheight=1)
            self.pages[key] = frame

        # ── 各标签页不在 __init__ 中一次性同步构建，避免加载期间窗口长时间无响应、无法拖动或最大化。

        self.show_page("generator")
        self.refresh_world_ui()

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _report_progress(self, value, detail):
        if self._on_progress:
            self._on_progress(value, detail)

    # ==================== 生命周期 ====================
    def on_closing(self):
        if self._closing:
            return
        self._closing = True

        try:
            if self.context is not None:
                self.settings["selected_styles"] = self.context.selected_styles
                self.settings["selected_quip_styles"] = self.context.selected_quip_styles
                self.settings["world_setting"] = self.context.world_setting
            self._settings_repo.save(self.settings)
        except Exception as e:
            print(f"[Warning] 保存设置失败: {e}")

        if self._active_dungeon_window is not None:
            try:
                import dearpygui.dearpygui as dpg
                if dpg.is_dearpygui_running():
                    dpg.stop_dearpygui()
            except Exception:
                pass

        os._exit(0)

    # ==================== 导航 ====================
    def _world_locks_resource(self, resource_type) -> bool:
        """世界包激活且打包了该资源时返回 True（附带类型的资源编辑器直接锁定）。"""
        if self.world_state is None or not self.world_state.active:
            return False
        return self.world_state.owns(resource_type)

    def _locked_pages(self) -> Dict[str, str]:
        """返回被世界包锁定而不可进入的管理页及其提示。"""
        locked = {}
        if self._world_locks_resource("dungeons"):
            locked["dungeon"] = "世界包附带了副本方案，编辑器已锁定"
        return locked

    def show_page(self, page_key):
        if page_key == "settings" and hasattr(self, 'settings_panel'):
            self.settings_panel.sync_saved_theme()
        locked = self._locked_pages()
        if page_key in locked:
            ui.common.dialogs.showwarning(
                "提示",
                f"{locked[page_key]}，无法进入该管理页。\n如需编辑请先卸载或解散世界包。")
            return
        self.pages[page_key].lift()
        self.nav_bar.set_active(page_key)

    # ==================== 生成器标签页 ====================
    def create_generator_tab(self, parent):
        self.generator_panel = ExplorationPanel(parent, self, self.context)
        self.generator_panel.pack(fill='both', expand=True)
        self.generator_panel.set_world_active(
            self.world_state is not None and self.world_state.active)

    # ==================== 文本管理标签页 ====================
    def create_text_mgmt_tab(self, parent):
        self.landmark_mgr = LandmarkCardManager(parent, self._landmark_repo, self._settings_repo, self)
        self.landmark_mgr.pack(fill='both', expand=True)

        self.quip_card_mgr = QuipCardManager(parent, self._quip_repo, self._settings_repo, self)
        self.quip_card_mgr.pack_forget()
        self.show_landmark_manager()

    def show_landmark_manager(self):
        self.quip_card_mgr.pack_forget()
        self.landmark_mgr.pack(fill='both', expand=True)
        self.landmark_mgr.switch_btn.set("地标管理")
        self.quip_card_mgr.switch_btn.set("地标管理")

    def show_quip_manager(self):
        self.landmark_mgr.pack_forget()
        self.quip_card_mgr.pack(fill='both', expand=True)
        self.quip_card_mgr.switch_btn.set("描述管理")
        self.landmark_mgr.switch_btn.set("描述管理")

    # ==================== 数据库管理标签页 ====================
    def refresh_personality_and_preset_combos(self):
        if hasattr(self, 'generator_panel'):
            self.generator_panel.refresh_dropdowns()

    def load_combined_landmarks(self):
        self.context.reload_merged_data()
        if hasattr(self, 'generator_panel'):
            self.generator_panel.refresh_style_hint()

    def load_combined_quips(self):
        self.context.reload_merged_data()
        if hasattr(self, 'generator_panel'):
            self.generator_panel.refresh_style_hint()

    def sync_landmark_styles_state(self):
        pass

    # ==================== 副本编辑标签页 ====================
    def create_dungeon_tab(self, parent):
        self.dungeon_editor = ScenarioEditor(
            parent, self._dungeon_repo, self,
            challenge_mgr=ChallengeService(self._settings_repo, self._character_repo,
                                           self._landmark_repo, self._quip_repo, self._dungeon_repo,
                                           world_state=self.world_state)
        )
        self.dungeon_editor.pack(fill='both', expand=True)

    def refresh_challenge_pack_dropdowns(self):
        if hasattr(self, 'landmark_mgr'):
            self.landmark_mgr._rebuild_dropdown()
        if hasattr(self, 'quip_card_mgr'):
            self.quip_card_mgr._rebuild_dropdown()
        if hasattr(self, 'dungeon_editor'):
            self.dungeon_editor._rebuild_dropdown()

    # ==================== 挑战模式标签页 ====================
    def create_challenge_tab(self, parent):
        self.challenge_panel = ChallengeModePanel(parent, self)
        self.challenge_panel.pack(fill='both', expand=True)
        self.challenge_panel.set_world_active(
            self.world_state is not None and self.world_state.active)

    # ==================== 设置面板 ====================
    def create_settings_panel(self):
        parent = self.pages["settings"]
        for widget in parent.winfo_children():
            widget.destroy()

        main_container = ctk.CTkFrame(parent, fg_color="transparent")
        main_container.pack(fill='both', expand=True)

        self.settings_panel = SettingsPanel(
            main_container,
            self._settings_repo,
            self._landmark_repo,
            self._quip_repo,
            self.settings,
            on_styles_changed=self._on_styles_changed,
            on_world_setting_changed=self.on_world_setting_changed,
            on_name_table_changed=self.on_name_table_changed,
            on_news_table_changed=self.on_news_table_changed,
            on_preset_table_changed=self.on_preset_table_changed,
            on_personality_table_changed=self.on_personality_table_changed,
            on_return_callback=lambda: self.show_page("generator"),
            gui_ref=self,
            world_manager=self.world_manager,
            name_repo=self.context.name_repo,
            preset_repo=self._preset_repo,
            personality_repo=self._personality_repo
        )
        self.settings_panel.pack(fill='both', expand=True)

        return_btn = ctk.CTkButton(
            main_container, text="保存并返回主页面",
            command=self.settings_panel._save_and_return,
            fg_color="transparent", height=30,
            text_color=SOFT,
            hover_color=HOVER_ALT,
            border_width=1, border_color=BORDER_ALT,
            corner_radius=8, font=ui_fonts.ui_font(12, "bold")
        )
        return_btn.pack(side='bottom', fill='x', padx=18, pady=9)

    # ---------- 设置回调 ----------
    def _get_landmark_count(self, style: str) -> int:
        return self.context.get_landmark_count(style)

    def _get_quip_counts_by_size(self, style: str) -> List[int]:
        return self.context.get_quip_counts_by_size(style)

    def _on_styles_changed(self):
        self.context.update_styles(
            self.settings_panel.selected_styles,
            self.settings_panel.selected_quip_styles
        )
        self.selected_styles = self.context.selected_styles
        self.selected_quip_styles = self.context.selected_quip_styles
        if hasattr(self, 'generator_panel'):
            self.generator_panel.refresh_style_hint()

    def on_world_setting_changed(self, world_setting):
        self.context.update_world_setting(world_setting)
        self.world_setting = world_setting
        if hasattr(self, 'generator_panel'):
            self.generator_panel.update_world_setting(world_setting)

    def on_name_table_changed(self, table_name):
        self.context.update_name_table(table_name)

    def on_news_table_changed(self, table_name):
        self.context.update_news_table(table_name)

    def on_preset_table_changed(self, table_name):
        self.context.update_preset_table(table_name)
        self.refresh_personality_and_preset_combos()

    def on_personality_table_changed(self, table_name):
        self.context.update_personality_table(table_name)
        self.refresh_personality_and_preset_combos()

    def refresh_world_ui(self):
        """世界包状态变化后，刷新导航栏、模式页绿色指示、挑战下拉框与管理页下拉框。"""
        active = self.world_state is not None and self.world_state.active
        world_name = self.world_state.pack_name if active else ""
        manifest = self.world_state.manifest if active else None
        world_version = manifest.version if manifest is not None else ""
        has_behavior_pack = bool(manifest is not None and manifest.owns("behaviors"))
        self.nav_bar.set_world_active(
            active, world_name, world_version, has_behavior_pack)
        if hasattr(self, 'generator_panel'):
            self.generator_panel.set_world_active(active)
        if hasattr(self, 'challenge_panel'):
            self.challenge_panel.set_world_active(active)
            self.challenge_panel.refresh_world_resources()
        if hasattr(self, 'landmark_mgr'):
            self.landmark_mgr.set_world_locked(
                self._world_locks_resource("landmarks"))
            self.landmark_mgr.refresh_styles()
        if hasattr(self, 'quip_card_mgr'):
            self.quip_card_mgr.set_world_locked(
                self._world_locks_resource("quips"))
            self.quip_card_mgr.refresh_styles()
        if hasattr(self, 'dungeon_editor'):
            self.dungeon_editor._refresh_scenario_list()
        if hasattr(self, 'settings_panel'):
            self.settings_panel._refresh_world_pack_ui()

    def refresh_style_listboxes(self):
        """风格新建/重命名/删除后，刷新所有 StyleListBox（挑战面板与设置面板）。"""
        if hasattr(self, 'challenge_panel'):
            self.challenge_panel._sync_landmark_styles()
            self.challenge_panel._sync_quip_styles()
        if hasattr(self, 'settings_panel'):
            self.settings_panel._sync_all_states()

    def apply_settings(self):
        # SettingsPanel 在保存前只修改控件变量；保存完成后从 settings 读取
        # 唯一有效值，再一次性切换全局外观并刷新原生 Tk 控件。
        loading_page = LoadingPage(self.root, title="「正在切换主题」")
        loading_page.place(relwidth=1, relheight=1)
        loading_page.lift()
        loading_page.update_progress(0.15, "读取主题设置...")

        saved_theme_mode = self.settings.get("theme_mode", "Light")
        self.theme_mode = saved_theme_mode
        ctk.set_appearance_mode(saved_theme_mode)
        loading_page.update_progress(0.40, "应用界面主题...")
        self.context.apply_context_settings()
        loading_page.update_progress(0.60, "刷新页面控件...")
        self.update_all_managers_theme()
        if hasattr(self, 'generator_panel'):
            self.generator_panel.update_world_setting(self.world_setting)
            self.generator_panel.refresh_style_hint()
            if self.generator_panel.current_state:
                self.generator_panel.state_panel.update_state(self.generator_panel.current_state)
        self.root.update_idletasks()
        loading_page.update_progress(1, "主题切换完成!")
        # 整批主题重绘已在上方提交，短暂保留遮罩只需覆盖首个原生合成帧。
        self.root.after(80, loading_page.destroy)

    def _apply_app_icon(self):
        """设置主窗口应用图标（assets/icon.ico），缺失时回退到 customtkinter 图标。"""
        if not sys.platform.startswith("win"):
            return
        try:
            icon_path = os.path.join(assets_dir(), "icon.ico")
            if not os.path.exists(icon_path):
                icon_path = os.path.join(os.path.dirname(ctk.__file__), "assets", "icons",
                                         "CustomTkinter_icon_Windows.ico")
            if os.path.exists(icon_path):
                self.root.iconbitmap(icon_path)
        except Exception:
            pass

    def _apply_titlebar_theme(self):
        """按当前外观模式直接设置 Windows 标题栏颜色（不走 withdraw/deiconify，避免闪烁）。"""
        if not sys.platform.startswith("win"):
            return
        try:
            dark = ctk.get_appearance_mode().lower() == "dark"
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            if not hwnd:
                return
            value = ctypes.c_int(1 if dark else 0)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 20, ctypes.byref(value), ctypes.sizeof(value)) != 0:
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 19, ctypes.byref(value), ctypes.sizeof(value))
        except Exception:
            pass

    def _enable_layered_window(self):
        """Windows 下把主窗口置为 WS_EX_LAYERED（全不透明），让 DWM 在最小化/还原期间
        保留最后一帧合成画面，避免还原瞬间控件区闪黑（CustomTkinter #1597/#2664 已知缺陷）。"""
        if not sys.platform.startswith("win"):
            return
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            if not hwnd:
                return
            user32 = ctypes.windll.user32
            try:
                get_style = user32.GetWindowLongPtrW
                set_style = user32.SetWindowLongPtrW
            except AttributeError:
                get_style = user32.GetWindowLongW
                set_style = user32.SetWindowLongW
            get_style.argtypes = [ctypes.c_void_p, ctypes.c_int]
            get_style.restype = ctypes.c_ssize_t
            set_style.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
            set_style.restype = ctypes.c_ssize_t
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            LWA_ALPHA = 0x2
            style = get_style(hwnd, GWL_EXSTYLE)
            if not style & WS_EX_LAYERED:
                set_style(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED)
            user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)
        except Exception:
            pass

    # 遮罩保持时长：给 Tk 一个原生重绘周期后立即撤掉。
    _RESTORE_REVEAL_DELAY_MS = 125

    def _install_restore_cover(self):
        """主题色遮罩常驻主窗口底层，还原时立即升到顶层盖住重绘。

        与旧方案的区别：遮罩只在初始化时创建一次，之后每个还原周期只是
        tkraise()/lower() 切换层级，不再反复 place/销毁、不动主框架布局，
        因此从最小化还原多次后行为保持一致。"""
        self._window_was_minimized = False
        self._restore_cover_active = False
        self._restore_cover = tk.Frame(self.root)
        self._restore_cover.place(x=0, y=0, relwidth=1, relheight=1)
        self._restore_cover.lower()
        self.root.bind("<Unmap>", self._on_window_unmap, add="+")
        self.root.bind("<Map>", self._on_window_map, add="+")

    def _on_window_unmap(self, _event=None):
        # 子控件也会经由 bindtags 触发根窗口的 <Unmap>，只处理根窗口自身。
        if _event is not None and _event.widget is not self.root:
            return
        # 运行期间根窗口整体 Unmap 只发生在最小化（关闭走 os._exit），
        # 因此下一次 <Map> 必然是还原。
        self._window_was_minimized = True

    def _on_window_map(self, _event=None):
        # 子控件也会经由 bindtags 触发根窗口的 <Map>，只处理根窗口自身。
        if _event is not None and _event.widget is not self.root:
            return
        if not self._window_was_minimized or self._restore_cover_active:
            return
        self._window_was_minimized = False
        self._restore_cover_active = True
        try:
            self._restore_cover.configure(bg=self._theme_bg_color())
            self._restore_cover.tkraise()
            # 不主动递归调用每个 CTk 控件的私有 _draw()，也不在 Map
            # 回调中嵌套 root.update()；回调返回后由 Tk 原生处理首轮重绘。
        except Exception:
            pass
        # 给 Tk 一个原生重绘周期后撤掉遮罩：内容已在遮罩下重绘，
        # 撤除只是一次从纯色块到完整界面的单帧切换。
        self.root.after(self._RESTORE_REVEAL_DELAY_MS, self._drop_restore_cover)

    def _drop_restore_cover(self):
        try:
            if self._restore_cover.winfo_exists():
                self._restore_cover.lower()
        except Exception:
            pass
        self._restore_cover_active = False

    def _theme_bg_color(self):
        """当前主题的主界面底色（BASE），遮罩用它保证闪烁色与界面无缝。"""
        try:
            dark = ctk.get_appearance_mode().lower() == "dark"
            return BASE[1] if dark else BASE[0]
        except Exception:
            return self.root.cget("bg")

    def update_all_managers_theme(self):
        mode = ctk.get_appearance_mode()
        if sys.platform.startswith("win"):
            self._apply_titlebar_theme()
            self._apply_app_icon()
        if hasattr(self, 'dungeon_editor'):
            self.dungeon_editor.evolution_panel.update_theme(mode)
            self.dungeon_editor.trigger_panel.update_theme(mode)
        if hasattr(self, 'landmark_mgr'):
            self.landmark_mgr.update_theme(mode)
        if hasattr(self, 'quip_card_mgr'):
            self.quip_card_mgr.update_theme(mode)
        if hasattr(self, 'generator_panel'):
            self.generator_panel.update_theme(mode)
        if hasattr(self, 'settings_panel'):
            self.settings_panel.update_theme(mode)

    # ==================== 角色加载与导出 ====================
    def load_character_by_id(self, giantess_id: str):
        state = self.context.load_character_state(giantess_id)
        if state is None:
            ui.common.dialogs.showerror("错误", f"无法加载角色 '{giantess_id}'")
            return

        if hasattr(self, 'generator_panel'):
            self.generator_panel.load_character_by_id(state)
            ui.common.dialogs.showinfo("成功", f"已加载角色：{state.name}")
            article = self.context.prepare_news_for_character_load(state)
            if article is not None:
                self.root.after(
                    1000,
                    lambda state_id=state.giantess_id: self._show_news_dialog(
                        article, state_id))

    def _show_news_dialog(self, article, state_id):
        if self._closing or not self.root.winfo_exists():
            return
        if (not hasattr(self, "generator_panel")
                or self.generator_panel.current_state is None
                or self.generator_panel.current_state.giantess_id != state_id):
            return
        NewsDialog(self.root, article)

    def export_character_card_to_path(self, file_path: str):
        if not hasattr(self, 'generator_panel'):
            ui.common.dialogs.showerror("错误", "生成器面板未初始化")
            return
        params = self.generator_panel.params_panel.get_params()
        personality_obj = params.get("current_personality_obj")
        preset_obj = params.get("current_preset_obj")

        if personality_obj is None:
            ui.common.dialogs.showerror("错误", "请选择一个具体的性格")
            return
        if preset_obj is None:
            ui.common.dialogs.showerror("错误", "请选择一个具体的身材")
            return

        data = self.context.build_export_card_data(
            name=params["name"],
            nick=params["nick"],
            original_height=params["original_height"],
            personality_obj=personality_obj,
            preset_obj=preset_obj,
            intro_hidden=params.get("intro_hidden", ""),
            intro_visible=params.get("intro_visible", ""),
            selected_tags=params.get("selected_tags", []),
            birthday=params.get("birthday", ""),
            uploaded_image_path=params.get("uploaded_image_path")
        )

        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            ui.common.dialogs.showinfo("成功", f"角色卡已导出到：{file_path}")
        except Exception as e:
            ui.common.dialogs.showerror("错误", f"保存失败：{e}")
