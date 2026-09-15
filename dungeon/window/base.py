"""窗口生命周期与基础属性。

进入副本界面与正式副本会话界面共享同一个 DPG context / viewport / 主窗口：
切换副本方案发生在同一视口内（dungeon.launcher.DungeonLaunchStages 负责入口阶段），
选择“开始副本 / 加载回放”后由 _enter_dungeon_phase() 切换到会话阶段，
同一生命周期内不再重建整个 DPG 上下文。
"""

import random
from collections import deque

import dearpygui.dearpygui as dpg

from ai import create_client
from dungeon.background import DungeonBackground
from dungeon.chapters import normalize_chapters
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
                 mode="explore", dungeon_ids=None):
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
        self.merged_quips = merged_quips
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

        # 入口阶段（dungeon.launcher.DungeonLaunchStages）与会话阶段共用同一 DPG 生命周期
        self.dungeon_ids = list(dungeon_ids) if dungeon_ids else []
        self._is_entry_phase = False

        # 回放模式相关
        self.current_replay_index = 0
        self.triggers = (dungeon_config or {}).get("triggers", []) if not is_replay else []
        self.chapters = normalize_chapters((dungeon_config or {}).get("chapters", []))
        # 章节运行时状态：当前章节、章节持续敏感效果、短暂视效
        self.current_chapter = None
        self.chapter_sensitivity_effects = []
        self.visual_effects = []
        self._applied_visual_filter = None
        self.current_background_path = ""
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
        # 探索模式：入口阶段选择副本方案后才初始化会话（延迟初始化标记）
        self._session_initialized = False
        self._entry_started = False
        self._exit_from_entry = False
        self._launch_error = ""      # 入口选择失败原因，窗口关闭后由调用方提示
        self._launch_choice = None   # 入口阶段的选择结果

        # 会话内容初始化分为“新开”（可能延迟到入口选择后）与“回放”两种
        if is_replay:
            self._init_replay(replay_data)
        elif not dungeon_ids:
            # 无入口阶段（挑战模式/直接指定配置）：立即初始化会话
            self._init_session(dungeon_config)
        # 有 dungeon_ids 的探索模式：入口阶段内由 _enter_dungeon_phase() 调用
        # _load_session_config() 延迟初始化会话，避免在用户选择副本方案前创建
        # AI 客户端/提示词

        self.story_history = []
        # 故事视图保留全部段落，游戏视图只显示当前段落。
        configured_view_mode = (dungeon_config or {}).get("view_mode", "story")
        self.view_mode = configured_view_mode if configured_view_mode in ("story", "game") else "story"
        # 显示组件（官方组件库，见 dungeon/components.py）
        self._components = []
        self._components_built = False
        self.layout_style = configured_view_mode if configured_view_mode in ("story", "game", "bottom") else "story"

        import platform
        self._is_windows = platform.system() == "Windows"
        self._real_title = f"副本模式 - {self.name}" if not self.is_replay else f"回放模式 - {self.name}"
        self._temp_title = f"DungeonSession" if self._is_windows else self._real_title

        # 向主窗口注册自身，以便关闭时能通知 DPG 退出
        self._register_with_parent()

        self._build_ui()

        # 入口阶段：与正式副本会话界面共享同一个 viewport，
        # 用户在入口页选择副本方案后由 _enter_dungeon_phase() 切换到会话阶段。
        if self.dungeon_ids:
            self._enter_entry_phase()
        else:
            # 无入口阶段（挑战模式/直接指定配置）：会话配置已在 __init__ 初始化，
            # 立即构建显示组件。
            self._init_components()
            self._build_components()
            self._enter_start_chapter()

        if self.parent and hasattr(self.parent, 'withdraw'):
            self.parent.withdraw()

        # 经调度器在首帧执行标题修正，避免与调度器自身的 frame callback 链冲突
        _dispatch.enqueue(self._fix_windows_title)
        _dispatch.install()

        dpg.start_dearpygui()

        _dispatch.stop()

        # 先恢复主窗口，便于退出提示/保存回放对话框正确显示
        if self.parent and hasattr(self.parent, 'deiconify'):
            try:
                self.parent.deiconify()
                self.parent.lift()
            except Exception:
                pass

        # 等待入口阶段后台线程退出（未启动时跳过）
        for attr in ("_bg_thread", "_ending_thread"):
            thread = getattr(self, attr, None)
            if thread is not None:
                try:
                    thread.join(timeout=0.5)
                except Exception:
                    pass

        # 用户关闭副本窗口时进行退出处理（未触发结局则警告数据丢失，触发后询问是否保存回放）
        # 入口阶段点“返回”直接关闭窗口，不视为副本会话结束，跳过退出处理
        if self._closing and not getattr(self, "_exit_from_entry", False):
            self._handle_exit()

        dpg.destroy_context()
        self._unregister_with_parent()

    # ---------------- 会话内容初始化 ----------------
    def _load_session_config(self, dungeon_id):
        """入口阶段选择副本方案后加载配置并初始化会话。

        在探索模式入口页用户点击“开始副本”时由 _enter_dungeon_phase() 调用：
        加载副本配置、扣 AP（经 on_entry_selected 钩子）、初始化 AI 客户端与提示词。
        返回 False 表示配置不可用，此时不应进入会话阶段。
        """
        config = None
        if self.dungeon_repo is not None:
            try:
                config = self.dungeon_repo.load_config(dungeon_id)
            except Exception as e:
                print(f"副本配置加载失败: {e}")
        if config is None:
            # 不在 DPG 循环内弹 Tk 对话框：记录错误，关闭窗口后由调用方提示
            self._launch_error = f"无法加载副本配置 '{dungeon_id}'"
            return False

        # 扣 AP / 状态刷新：探索模式且有角色时按副本配置扣除行动点数
        on_selected = getattr(self, "_on_dungeon_selected", None)
        if callable(on_selected):
            if not on_selected(dungeon_id, config):
                return False

        self.dungeon_config = config
        self.dungeon_id = dungeon_id
        self.triggers = config.get("triggers", [])
        self.chapters = normalize_chapters(config.get("chapters", []))
        self.view_mode = config.get("view_mode", "story")
        if self.view_mode not in ("story", "game"):
            self.view_mode = "story"
        # 迟到初始化：此时才创建 AI 客户端与提示词
        self._init_session(config)
        self._session_initialized = True
        return True

    def _on_dungeon_selected(self, dungeon_id, config) -> bool:
        """入口阶段选择副本后扣除行动点数（探索模式且有角色时）。

        返回 False 表示行动点数不足或其它原因，不应进入会话阶段。
        """
        character = self.character
        if self.mode == "explore" and character is not None:
            try:
                entry_cost = max(0, int(config.get("entry_action_cost", 0) or 0))
            except (TypeError, ValueError):
                entry_cost = 0
            if entry_cost > 0:
                state_service = getattr(self.gui, "context", None)
                state_service = getattr(state_service, "state_service", None) if state_service else None
                from services.state_service import StateService
                consume = (state_service.consume_action_points(character, entry_cost)
                           if state_service is not None
                           else StateService.consume_action_points(character, entry_cost))
                if not consume:
                    # 不在 DPG 循环内弹 Tk 对话框：记录错误，关闭窗口后由调用方提示
                    self._launch_error = (
                        f"进入该副本需要 {entry_cost} 行动点数，"
                        f"当前仅剩 {character.action_points} 点。")
                    return False
                if self.character_repo is not None:
                    try:
                        self.character_repo.save(character)
                    except Exception as e:
                        print(f"[Dungeon] 角色保存失败: {e}")
                # 同步刷新主界面状态面板
                self._refresh_external_state()
        return True

    def _refresh_external_state(self):
        """扣减行动点数后同步主界面（探索模式可用）。"""
        gui = self.gui
        if gui is None:
            return
        try:
            exploration = getattr(gui, "generator_panel", None)
            if exploration is None:
                for attr in ("exploration_panel", "exploration"):
                    exploration = getattr(gui, attr, None)
                    if exploration is not None:
                        break
            if exploration is not None and hasattr(exploration, "state_panel"):
                exploration.state_panel.update_state(self.character)
            if exploration is not None and hasattr(exploration, "_update_report_cost_label"):
                exploration._update_report_cost_label()
        except Exception as e:
            print(f"[Dungeon] 主界面状态同步失败: {e}")

    def _init_session(self, dungeon_config):
        """新开副本：初始化 AI 客户端、提示词、演化状态与尺寸类别。"""
        dungeon_config = dungeon_config or {}
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
        personality = self.personality
        character = self.character
        self.dungeon_state = DungeonState(
            intrusion=personality.init_intrusion if personality.init_intrusion != 0 else random.uniform(0.5, 2.5),
            destruction=personality.init_destruction if personality.init_destruction != 0 else random.uniform(0.5, 2.5),
            custom_attrs=init_custom,
            step_intrusion=(character.current_step_intrusion
                            if character is not None
                            else personality.step_intrusion),
            step_destruction=(character.current_step_destruction
                              if character is not None
                              else personality.step_destruction),
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
        self.keyword_match_given = set()
        self.quips_working = dict(self.merged_quips) if self.merged_quips else {}
        self.used_quips = set()

        self.size_cat = get_size_category(self.height)

        self.prompt_builder = DungeonPromptBuilder(self)
        self.system_prompt = self.prompt_builder.build_system_prompt()
        self.messages = [{"role": "system", "content": self.system_prompt}]

    def _init_replay(self, replay_data):
        """回放模式：仅重放数据，不创建 AI 客户端与提示词构建器。"""
        replay_data = replay_data or []
        # 回放数据可能以触发器记录开头（如开局即触发的背景触发器），
        # 取第一条步进记录还原初始属性；若全为触发器记录则使用默认值。
        first_step = next((e for e in replay_data if not e.get("kind")), None)
        if first_step is None:
            self.dungeon_state = DungeonState(intrusion=0.0, destruction=0.0, custom_attrs={})
        else:
            self.dungeon_state = DungeonState(
                intrusion=first_step["intrusion_before"],
                destruction=first_step["destruction_before"],
                custom_attrs=first_step.get("custom_before", {}),
                step_intrusion=first_step.get("step_intrusion_before"),
                step_destruction=first_step.get("step_destruction_before"),
            )
        self.dungeon_logic = None
        self.current_text_type = None

    # ---------------- 入口阶段进入（由 dungeon.launcher.DungeonLaunchStages 提供） ----------------
    # 子类 mixin 会覆盖 _enter_entry_phase / _init_entry_materials：
    # base 只负责在 _build_ui() 后调用统一的 enter 钩子进入入口阶段。
    def _request_close(self):
        """请求退出 DPG 渲染循环（可在任意回调内安全调用）。

        在控件/帧回调内直接调用 dpg.stop_dearpygui() 会让渲染帧中途停止，
        随后的 destroy_context() 因 DPG 内部状态未正常收尾而破坏堆——
        Windows 上表现为窗口关闭后进程在 Tk 主循环中于 _dearpygui.pyd
        内崩溃退出。改为向视口窗口投递 WM_CLOSE，让 DPG 走与用户点窗口
        X 按钮相同的原生关闭路径（在消息轮询阶段帧间停止），实测稳定。
        非 Windows 平台回退为直接 stop。
        """
        if self._is_windows:
            try:
                import ctypes
                hwnd = ctypes.windll.user32.FindWindowW(None, self._temp_title)
                if not hwnd:
                    hwnd = ctypes.windll.user32.FindWindowW(None, self._real_title)
                if hwnd:
                    ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
                    return
            except Exception as e:
                print(f"副本窗口关闭请求失败: {e}")
        try:
            dpg.stop_dearpygui()
        except Exception:
            pass

    def _close_loop(self):
        """入口阶段选择“返回”时关闭窗口。

        不在此处直接 stop 渲染循环（见 _request_close 的说明）；退出回调
        _on_close 会在最后一帧完成标记清理（_closing/_exit_from_entry、
        停调度器），后台线程的 join 由 __init__ 在循环退出后执行。
        """
        self._closing = True
        if self._bg_resize_timer is not None:
            try:
                self._bg_resize_timer.cancel()
            except Exception:
                pass
        self._request_close()

    # ---------- 视口尺寸（与正式副本会话窗口一致） ----------
    def _get_initial_viewport_size(self):
        """返回 (视口初始宽, 高, dpi_scale, 主窗口客户区宽, 高)。"""
        scale = 1.0
        main_cw = main_ch = 0
        if self.parent is not None and hasattr(self.parent, "winfo_toplevel"):
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
        scale = max(0.5, scale)
        if main_cw <= 0 or main_ch <= 0:
            main_cw, main_ch = round(1280 * scale), round(720 * scale)
        return (main_cw + round(16 * scale), main_ch + round(39 * scale),
                scale, main_cw, main_ch)

    def _correct_viewport_size_to_main(self):
        try:
            delta_w = max(0, dpg.get_viewport_width() - dpg.get_viewport_client_width())
            delta_h = max(0, dpg.get_viewport_height() - dpg.get_viewport_client_height())
            dpg.set_viewport_width(self._main_client_w + delta_w)
            dpg.set_viewport_height(self._main_client_h + delta_h)
        except Exception as e:
            print(f"视口尺寸校正失败: {e}")

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

    @staticmethod
    def _type_prefix(text_type):
        return {
            DungeonTextType.BACKGROUND: "【环境】",
            DungeonTextType.BRANCH: "【分支】",
            DungeonTextType.DIALOG: "【对话】",
            DungeonTextType.INTERACTION: "【互动】",
            DungeonTextType.ACTION: "【行动】",
        }.get(text_type, "【未知】")