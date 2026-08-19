"""窗口生命周期与基础属性。"""

import random
from collections import deque

import dearpygui.dearpygui as dpg

from ai import create_client
from dungeon.background import DungeonBackground
from dungeon.dispatcher import _dispatch
from dungeon.models import DungeonTextType, DungeonState
from dungeon.prompts import DungeonPromptBuilder
from dungeon.rules import EvolutionRules
from logic import get_size_category
from ui.common.fonts import dungeon_font_default


class DungeonWindowBase:
    def __init__(self, parent, name, nick, personality, preset, original_height,
                 intro_hidden, intro_visible, tags, uploaded_image,
                 dungeon_config, dungeon_repo,
                 merged_landmarks, merged_quips,
                 selected_styles, selected_quip_styles, detail_pools, height,
                 ai_config, greed: int,
                  is_replay=False, replay_data=None, dungeon_id=None, dungeon_font=None,
                 body_parts=None, character=None, character_repo=None, gui=None,
                 mode="explore"):
        self.parent = parent
        self.name = name
        self.nick = nick
        self.personality = personality
        self.preset = preset
        self.original_height = original_height
        self.intro_hidden = intro_hidden
        self.intro_visible = intro_visible
        self.tags = tags
        self.uploaded_image = uploaded_image
        self.dungeon_config = dungeon_config
        self.dungeon_repo = dungeon_repo
        self.merged_landmarks = merged_landmarks
        self.selected_styles = selected_styles
        self.selected_quip_styles = selected_quip_styles
        self.detail_pools = detail_pools
        self.height = height
        self.ai_config = ai_config or {}
        self.greed = greed
        self.is_replay = is_replay
        self.loaded_replay = replay_data
        self.dungeon_id = dungeon_id
        self.dungeon_font = dungeon_font or dungeon_font_default()
        self.body_parts = body_parts
        # 副本保存/回放相关
        self.character = character
        self.character_repo = character_repo
        self.gui = gui
        # 运行模式："explore"（探索模式）或 "challenge"（挑战模式）
        self.mode = mode
        self.settings = {}
        if gui is not None:
            self.settings = getattr(gui, "settings", None) or {}
        self.ending_effects = {}
        self.ending_text = ""
        # 当前结局触发器信息（图标路径、触发器下标、已写入的索引记录）
        self.ending_icon_path = ""
        self._ending_trigger_index = -1
        self._achievement_record = None
        self._replay_saved = False
        self._ending_thread = None
        self._closing = False
        self._text_update_pending = False
        self._text_item_tags = []
        self._bg_pil_full = None
        self._bg_pil_original = None
        self._bg_revision = 0
        self._bg_resize_timer = None
        self._background = DungeonBackground(self)

        # 回放模式相关
        self.current_replay_index = 0
        self.triggers = dungeon_config.get("triggers", []) if not is_replay else []
        self.triggered_names = set()   # 已触发且不可再次触发的触发器
        self.fired_triggers = set()    # 至少触发过一次的触发器（前置条件判断用）
        self.replay_data = []
        # 插入触发器触发后排队的段落（FIFO）
        self.pending_insertions = deque()
        # 选项触发器状态
        self.trigger_choices = {}      # 触发器名 -> [已选择编号...]
        self.pending_option = None     # 待弹出的选项触发器数据
        self.option_choice = None      # 最近一次选择 {"name","index","prompt","text"}
        self._option_generating = False
        # 回放记录引用：选择/结局生成完成后把结果写回对应记录
        self._last_option_record = None
        self._last_ending_record = None
        # 敏感触发器效果：{"attr": 属性名, "amount": 倍率改变量, "remaining": 剩余步数}
        self.sensitivity_effects = []
        # 结局触发器状态
        self.pending_ending = None    # 待生成结局 {"name": 结局名称}
        self.dungeon_ended = False    # 结局已生成，故事结束
        self._ending_generating = False

        if not is_replay:
            try:
                self.ai_client = create_client(
                    self.ai_config.get("provider"),
                    self.ai_config.get("api_key", ""),
                    base_url=self.ai_config.get("url") or None,
                    model=self.ai_config.get("model") or None,
                )
            except Exception as e:
                print(f"AI 客户端初始化失败: {e}")
                self.ai_client = None

            self.initial_prompt = dungeon_config.get("initial_prompt", "")
            self.section_prompts = dungeon_config.get("section_prompts", {})
            self.evolution_attrs = dungeon_config.get("evolution_attrs", [])

            init_custom = {}
            for attr in self.evolution_attrs:
                if attr["type"] == "custom":
                    init_custom[attr["name"]] = attr.get("init_value", 0.0)
            self.dungeon_state = DungeonState(
                intrusion=personality.init_intrusion if personality.init_intrusion != 0 else random.uniform(0.5, 2.5),
                destruction=personality.init_destruction if personality.init_destruction != 0 else random.uniform(0.5, 2.5),
                custom_attrs=init_custom
            )
            self.dungeon_state.total_steps = 0
            self.dungeon_state.steps_since_trigger = 0
            self.dungeon_logic = EvolutionRules()
            self.current_text_type = None
            self.last_ai_text = ""
            self._generating = False

            self.breakthrough_attempts = 0
            self.locked_coords = {(4, 4)}
            self.noticed_parts = set()
            self.prompted_parts = set()
            self.quips_working = dict(merged_quips) if merged_quips else {}
            self.used_quips = set()

            self.size_cat = get_size_category(height)
            self.selected_styles = selected_styles
            self.selected_quip_styles = selected_quip_styles
            self.detail_pools = detail_pools

            self.prompt_builder = DungeonPromptBuilder(self)
            self.system_prompt = self.prompt_builder.build_system_prompt()
            self.messages = [{"role": "system", "content": self.system_prompt}]
        else:
            # 回放数据可能以触发器记录开头（如开局即触发的背景触发器），
            # 取第一条步进记录还原初始属性；若全为触发器记录则使用默认值。
            first_step = next((e for e in self.loaded_replay if e.get("kind") != "trigger"), None)
            if first_step is None:
                self.dungeon_state = DungeonState(intrusion=0.0, destruction=0.0, custom_attrs={})
            else:
                self.dungeon_state = DungeonState(
                    intrusion=first_step["intrusion_before"],
                    destruction=first_step["destruction_before"],
                    custom_attrs=first_step.get("custom_before", {})
                )
            self.dungeon_logic = None
            self.current_text_type = None

        self.story_history = []
        # 故事视图保留全部段落，游戏视图只显示当前段落。
        configured_view_mode = (dungeon_config or {}).get("view_mode", "story")
        self.view_mode = configured_view_mode if configured_view_mode in ("story", "game") else "story"

        import platform
        self._is_windows = platform.system() == "Windows"
        self._real_title = f"副本模式 - {self.name}" if not self.is_replay else f"回放模式 - {self.name}"
        self._temp_title = f"DungeonSession" if self._is_windows else self._real_title

        # 向主窗口注册自身，以便关闭时能通知 DPG 退出
        self._register_with_parent()

        self._build_ui()

        if self.parent and hasattr(self.parent, 'withdraw'):
            self.parent.withdraw()

        # 经调度器在首帧执行标题修正，避免与调度器自身的 frame callback 链冲突
        _dispatch.enqueue(self._fix_windows_title)
        _dispatch.install()

        if not self.is_replay:
            self.check_triggers()
        dpg.start_dearpygui()

        _dispatch.stop()

        # 先恢复主窗口，便于退出提示/保存回放对话框正确显示
        if self.parent and hasattr(self.parent, 'deiconify'):
            try:
                self.parent.deiconify()
                self.parent.lift()
            except Exception:
                pass

        # 用户关闭副本窗口时进行退出处理（未触发结局则警告数据丢失，触发后询问是否保存回放）
        if self._closing:
            self._handle_exit()

        dpg.destroy_context()
        self._unregister_with_parent()

    def _register_with_parent(self):
        """在父对象上注册自身，以便关闭时协调退出。"""
        obj = self.parent
        while obj is not None:
            if hasattr(obj, '_active_dungeon_window') and hasattr(obj, '_closing'):
                obj._active_dungeon_window = self
                break
            obj = getattr(obj, 'master', None) or getattr(obj, 'parent', None)

    def _unregister_with_parent(self):
        """从父对象解除注册。"""
        obj = self.parent
        while obj is not None:
            if hasattr(obj, '_active_dungeon_window') and obj._active_dungeon_window is self:
                obj._active_dungeon_window = None
                break
            obj = getattr(obj, 'master', None) or getattr(obj, 'parent', None)

    def _fix_windows_title(self):
        if self._is_windows:
            import ctypes
            hwnd = ctypes.windll.user32.FindWindowW(None, self._temp_title)
            if hwnd:
                ctypes.windll.user32.SetWindowTextW(hwnd, self._real_title)

    def _get_initial_viewport_size(self):
        """取得主窗口客户区实际尺寸与 DPI，避免两套 GUI 框架重复缩放。

        返回 (视口初始宽, 视口初始高, dpi_scale, 主窗口客户区宽, 主窗口客户区高)。
        """
        scale = 1.0
        main_cw = main_ch = 0
        if self.parent and hasattr(self.parent, "winfo_toplevel"):
            try:
                root = self.parent.winfo_toplevel()
                root.update_idletasks()

                if self._is_windows:
                    import ctypes
                    from ctypes import wintypes

                    hwnd = ctypes.windll.user32.GetAncestor(root.winfo_id(), 2)  # GA_ROOT
                    dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
                    if dpi:
                        scale = dpi / 96.0

                    rect = wintypes.RECT()
                    if ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(rect)):
                        if rect.right > 0 and rect.bottom > 0:
                            main_cw, main_ch = rect.right, rect.bottom

                if main_cw <= 0:
                    scale = float(root.winfo_fpixels("1i")) / 96.0
                    main_cw = root.winfo_width()
                    main_ch = root.winfo_height()
            except Exception:
                pass
        elif self._is_windows:
            try:
                import ctypes
                scale = ctypes.windll.user32.GetDpiForSystem() / 96.0
            except Exception:
                pass

        scale = max(scale, 0.5)
        if main_cw <= 0 or main_ch <= 0:
            main_cw, main_ch = round(1280 * scale), round(720 * scale)

        # 先按粗略估算建窗，展示后再按实际边框偏移精确对齐（见 _correct_viewport_size_to_main）。
        return main_cw + round(16 * scale), main_ch + round(39 * scale), scale, main_cw, main_ch

    def _correct_viewport_size_to_main(self):
        """用实际窗口边框偏移把视口客户区精确对齐到主窗口客户区。"""
        try:
            delta_w = max(0, dpg.get_viewport_width() - dpg.get_viewport_client_width())
            delta_h = max(0, dpg.get_viewport_height() - dpg.get_viewport_client_height())
            dpg.set_viewport_width(self._main_client_w + delta_w)
            dpg.set_viewport_height(self._main_client_h + delta_h)
        except Exception as e:
            print(f"视口尺寸校正失败: {e}")

    @staticmethod
    def _type_prefix(text_type):
        return {
            DungeonTextType.BACKGROUND: "【环境】",
            DungeonTextType.BRANCH: "【分支】",
            DungeonTextType.DIALOG: "【对话】",
            DungeonTextType.INTERACTION: "【互动】",
            DungeonTextType.ACTION: "【行动】",
        }.get(text_type, "【未知】")
