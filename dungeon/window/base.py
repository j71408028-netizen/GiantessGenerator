"""窗口生命周期与基础属性。

进入副本界面与正式副本会话界面共享同一个 DPG context / viewport / 主窗口：
切换副本方案发生在同一视口内（dungeon.window.launcher.DungeonLaunchStages 负责入口阶段），
选择“开始副本 / 加载回放”后由 _enter_dungeon_phase() 切换到会话阶段，
同一生命周期内不再重建整个 DPG 上下文。
"""

import random
import time
from collections import deque

import dearpygui.dearpygui as dpg

from ai import create_client
from dungeon.window.background import DungeonBackground
from dungeon.chapters import normalize_chapters
from dungeon.coupling import normalize_coupling_level
from dungeon.window.frame import FrameScheduler
from dungeon.window.host import HostPort
from dungeon.window.result import (REASON_ALREADY_RUNNING, REASON_ENTRY_CANCELLED,
                                   REASON_LAUNCH_FAILED, REASON_SESSION_ENDED,
                                   SessionResult)
from dungeon.models import DungeonTextType, DungeonState
from dungeon.prompts import DungeonPromptBuilder
from dungeon.rules import EvolutionRules
from dungeon.summary import DEFAULT_RECENT_COUNT, StorySummarizer
from dungeon.validate import format_diagnostics, has_errors, validate_scenario_config
from logic import get_size_category
from services.state_service import StateService

# 文本区布局样式：旧的「副本窗口视图」设置已移除，统一用保留全历史的故事布局
LAYOUT_STYLE = "story"


class DungeonWindowBase:
    """副本窗口的生命周期：构造（``__init__``）与运行（``run``）分离。

    驱动方式（L0）：不再调用 ``dpg.start_dearpygui()`` 把调用方的主循环堵死，
    改由 :meth:`_run_frame_loop` 手动渲染——每帧 drain 一次调度队列、渲染一帧、
    再让宿主处理一次事件。由此带来三点：

    - 关闭只需 ``dpg.stop_dearpygui()``（跨平台），不再需要按窗口标题
      ``FindWindowW`` + ``WM_CLOSE`` 的平台 hack；
    - 关闭用 ``dpg.is_dearpygui_running()`` 检测（``is_viewport_ok()`` 关闭后
      仍返回 True，不能用来判定）；
    - ``set_exit_callback`` 在手动模式下只在 ``destroy_context()`` 内部触发，
      太晚，因此清理前移到帧循环"已停止"分支（见 ``engine.DungeonStoryEngine._on_close``）。

    帧时钟（L3）：窗口自带一个 :class:`~dungeon.window.frame.FrameScheduler`
    （``self._frame``），背景轮播、结局图标翻页、Ken Burns、仿流式动画与背景防抖
    都挂在它上面，它是本层的唯一时间源；会话结束随之 stop。

    结果（L4）：:meth:`run` 返回 :class:`~dungeon.window.result.SessionResult`，
    调用方不再读窗口的私有属性。
    """

    #: 手动渲染的帧间隔（秒）。渲染本身不阻塞，间隔决定上限帧率。
    FRAME_INTERVAL = 1.0 / 60.0
    #: 帧循环期间是否泵宿主事件（Tk 的 ``update()``）：开启后主窗口在副本
    #: 运行期间仍可响应/重绘，不再"假死"。宿主不提供 ``update()`` 时自动跳过。
    PUMP_HOST_EVENTS = True
    #: 同一进程内只允许一个会话在跑。宿主事件泵会把调用方的回调重新放回
    #: 主线程，若不挡住就可能嵌套创建第二个 DPG context（DPG 上下文是全局单例）。
    _session_running = False

    def __init__(self, parent, name, nick, personality, preset, original_height,
                 intro_hidden, intro_visible, tags, uploaded_image,
                 scenario_config, scenario_repo,
                 merged_landmarks, merged_quips,
                 selected_styles, selected_quip_styles, detail_pools, height,
                 ai_config, greed: int,
                 is_replay=False, replay_data=None, scenario_id=None, dungeon_font=None,
                 body_parts=None, character=None, character_repo=None, gui=None,
                 mode="explore", scenario_ids=None, host=None):
        # 宿主端口（L2）：尺寸/DPI、显隐、事件泵、弹框、活动窗口登记、字体
        # 全部经此取得。未传时用无宿主缺省实现（自检脚本、无人值守）。
        self.host = host if host is not None else HostPort()
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
        self.scenario_config = scenario_config
        self.scenario_repo = scenario_repo
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
        self.scenario_id = scenario_id
        self.dungeon_font = dungeon_font or self.host.default_font()
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
        # 本局落盘的产物路径（L4：由 SessionResult 交给调用方）
        self.replay_path = ""
        self.report_path = ""
        # 统一收尾标记：_finalize() 只允许执行一次（正常结局/用户中断/生成异常共用）
        self._finalized = False
        # 会话内的生成异常（不再只 print：收尾时汇总进「未完成」报告）
        self._session_errors = []
        self._ending_thread = None
        self._closing = False
        self._text_update_pending = False
        self._text_item_tags = []
        # 帧时钟（L3）：时机在这里，重活在外面。取代原先的模块级单例 _dispatch：
        # 每帧 tick 一次（先跑到期任务，再 drain 主线程更新队列），会话结束 stop。
        self._frame = FrameScheduler()
        self._bg_pil_full = None
        self._bg_pil_original = None
        self._bg_revision = 0
        self._background = DungeonBackground(self)

        # 入口阶段（dungeon.window.launcher.DungeonLaunchStages）与会话阶段共用同一 DPG 生命周期
        self.scenario_ids = list(scenario_ids) if scenario_ids else []
        self._is_entry_phase = False

        # 回放模式相关
        self.current_replay_index = 0
        self.triggers = (scenario_config or {}).get("triggers", []) if not is_replay else []
        self.chapters = normalize_chapters((scenario_config or {}).get("chapters", []))
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
        # 当前逻辑段落（一次 AI 输出）内待揭示的显示段落（内置分句器切分）
        self._pending_units = []
        # 进行中的仿流式输出动画状态（揭示句/插入段的逐字上屏）
        self._text_anim_state = None
        # 细节探究：AI 在后台提出的想了解细节，下次生成前检索解答并消费
        self._detail_queries = []
        self._detail_querying = False
        # 选项触发器状态
        self.trigger_choices = {}      # 触发器名 -> [已选择编号...]
        self.pending_option = None     # 待弹出的选项触发器数据
        self.option_choice = None      # 最近一次选择 {"name","index","prompt","text"}
        self._option_generating = False
        # 回放记录引用：选择/结局生成完成后把结果写回对应记录
        self._last_option_record = None
        self._last_ending_record = None
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
        # 最近一次方案校验的诊断（入口加载时填充；为空表示干净）
        self.scenario_diagnostics = []

        self.story_history = []
        # 耦合等级：决定副本执行时采用的硬编码 AI 提示词（见 dungeon/coupling.py）。
        # 必须在会话初始化之前就绪——_init_session 会立刻据此构建系统提示。
        self.coupling_level = normalize_coupling_level(
            (scenario_config or {}).get("coupling_level"))
        # 显示组件（官方组件库，见 dungeon/window/components.py）
        self._components = []
        self._components_built = False
        self.layout_style = LAYOUT_STYLE

        # 会话内容初始化分为“新开”（可能延迟到入口选择后）与“回放”两种
        if is_replay:
            self._init_replay(replay_data)
        elif not scenario_ids:
            # 无入口阶段（挑战模式/直接指定配置）：立即初始化会话
            self._init_session(scenario_config)
        # 有 scenario_ids 的探索模式：入口阶段内由 _enter_dungeon_phase() 调用
        # _load_session_config() 延迟初始化会话，避免在用户选择副本方案前创建
        # AI 客户端/提示词

        import platform
        self._is_windows = platform.system() == "Windows"
        self._real_title = f"副本模式 - {self.name}" if not self.is_replay else f"回放模式 - {self.name}"
        self._temp_title = f"DungeonSession" if self._is_windows else self._real_title

        # 帧循环统计（自检/调试用）
        self.frames_rendered = 0

    # ---------------- 生命周期：构造之后显式 run() ----------------
    def run(self) -> SessionResult:
        """建 UI → 驱动帧循环 → 收尾，返回这一局的结果对象（L4）。

        调用方只看返回值就知道发生了什么（``result.failed`` / ``result.cancelled``
        / ``result.succeeded``），不必再摸 ``window._launch_error`` 这类私有属性；
        需要细看窗口状态时也可以自己留着实例引用（自检脚本就是这么做的）。

        重入由 ``_session_running`` 挡住：宿主事件泵会把调用方的回调重新放回
        主线程，若不挡住就可能嵌套创建第二个 DPG 上下文。
        """
        cls = type(self)
        if cls._session_running:
            print("[Dungeon] 已有副本会话在运行，忽略本次启动")
            return SessionResult(REASON_ALREADY_RUNNING,
                                 scenario_id=self.scenario_id or "")
        cls._session_running = True
        try:
            self._start_session()
        finally:
            cls._session_running = False
        return self._build_result()

    def _build_result(self) -> SessionResult:
        """把这一局的结果打包成 :class:`SessionResult`。"""
        frames = self.frames_rendered
        # 入口选择失败：窗口已经关了，错误由调用方在主线程提示
        if self._launch_error:
            return SessionResult(REASON_LAUNCH_FAILED,
                                 launch_error=self._launch_error,
                                 launch_choice=self._launch_choice,
                                 scenario_id=self.scenario_id or "",
                                 frames_rendered=frames)
        # 入口页直接返回：没进过会话，不应有任何内容
        if getattr(self, "_exit_from_entry", False):
            return SessionResult(REASON_ENTRY_CANCELLED,
                                 launch_choice=self._launch_choice,
                                 scenario_id=self.scenario_id or "",
                                 frames_rendered=frames)
        return SessionResult(REASON_SESSION_ENDED,
                             launch_choice=self._launch_choice,
                             scenario_id=self.scenario_id or "",
                             ended=bool(self.dungeon_ended),
                             is_replay=bool(self.is_replay),
                             replay_path=getattr(self, "replay_path", "") or "",
                             report_path=getattr(self, "report_path", "") or "",
                             frames_rendered=frames)

    def _start_session(self):
        # 向主窗口注册自身，以便关闭时能通知 DPG 退出
        self._register_with_parent()
        try:
            self._build_ui()

            # 入口阶段：与正式副本会话界面共享同一个 viewport，
            # 用户在入口页选择副本方案后由 _enter_dungeon_phase() 切换到会话阶段。
            if self.scenario_ids:
                self._enter_entry_phase()
            else:
                # 无入口阶段（挑战模式/直接指定配置）：会话配置已在 __init__ 初始化，
                # 立即构建显示组件。
                self._init_components()
                self._build_components()
                self._enter_start_chapter()

            # 藏起宿主：DPG 视口是独立顶层窗口，不藏会两个窗口同时占屏
            self.host.hide_window()

            # 标题修正放到首帧：此时原生窗口已创建，按标题 FindWindowW 才找得到
            self._frame.call(self._fix_windows_title)

            self._run_frame_loop()
        finally:
            self._finish_session()

    def _run_frame_loop(self):
        """手动驱动 DPG 渲染：泵宿主 → tick 帧时钟 → 检测关闭 → 渲染一帧 → 让位。

        每帧的顺序很关键：先 tick（跑到期的帧任务，再执行后台线程投递的 UI 更新），
        再判定是否已停止，最后渲染；反过来会让"关闭前最后一次更新"永远执行不到。

        ``self._frame.tick()`` 是整层的**唯一时间源**（L3）：轮播、图标翻页、Ken
        Burns、仿流式动画、背景防抖都挂在它上面，因此不再有并行的时间线。
        """
        while True:
            self._pump_host_events()
            self._frame.tick()
            if not dpg.is_dearpygui_running():
                # 用户点 X 或程序 stop：走与退出回调等价的清理
                self._on_close()
                break
            dpg.render_dearpygui_frame()
            self.frames_rendered += 1
            time.sleep(self.FRAME_INTERVAL)

    def _pump_host_events(self):
        """让宿主处理一次挂起事件，避免主窗口在副本运行期间"假死"。

        经宿主端口调用（``TkHost`` 映射到 ``widget.update()``）；没有宿主或
        宿主不支持时静默跳过。重入由 ``_session_running`` 兜住：宿主回调里
        再次启动副本会被 :meth:`run` 忽略，不会嵌套出第二个 DPG 上下文。
        """
        if not self.PUMP_HOST_EVENTS:
            return
        try:
            self.host.pump_events()
        except Exception:
            pass

    def _finish_session(self):
        """帧循环结束后的收尾（无论正常关闭还是异常都执行）。"""
        # 帧时钟先停：此后后台线程投递的界面更新与未跑的帧任务一律丢弃，
        # 不会再碰到即将销毁的 DPG 上下文（L3 之后它由会话独占，无需再 install）。
        self._frame.stop()
        # 显示组件销毁：删各自的 DPG 控件并丢弃实例缓存（DPG 上下文仍存活，
        # 必须赶在 destroy_context 之前）；帧任务已随 stop() 清空，组件 destroy
        # 里的 cancel 只是幂等兜底
        self._destroy_components()
        # 背景像素工作者：会话内的全部重采样/混合任务都在这里排队，收工时一并结束
        self._background.shutdown()

        # 用户点视口关闭键（X）退出时，GLFW 销毁原生窗口会往本线程队列里留一条
        # WM_QUIT；它不会危害 DPG，却会让 Tk 的计时器与模态对话框（tkwait）
        # 从此收不到事件——收尾提示框弹出来就"卡死"。所以任何 Tk 交互之前先清掉，
        # 详见 window.md §5-C12 与 host.HostPort.discard_pending_quit()。
        self._discard_pending_quit()

        # 先恢复主窗口，便于退出提示/保存回放对话框正确显示
        self.host.show_window()

        # 等待后台 AI 线程退出（结局生成；未启动时跳过）
        thread = getattr(self, "_ending_thread", None)
        if thread is not None:
            try:
                thread.join(timeout=0.5)
            except Exception:
                pass

        # 用户关闭副本窗口时进行退出处理：未触发结局走 _finalize(False) 把已生成内容
        # 落盘为「未完成」回放；已触发结局则询问是否保存正式回放
        # 入口阶段点“返回”直接关闭窗口，不视为副本会话结束，跳过退出处理
        if self._closing and not getattr(self, "_exit_from_entry", False):
            self._handle_exit()

        try:
            dpg.destroy_context()
        except Exception as e:
            # 建 UI 阶段就抛异常时上下文可能根本没建起来，这里不能连累收尾
            print(f"[Dungeon] 销毁 DPG 上下文失败: {e}")
        finally:
            self._unregister_with_parent()

    # ---------------- 会话内容初始化 ----------------
    def _current_action_points(self):
        """当前角色的行动点数；挑战模式等无角色场景返回 None（策略值按缺省因子计算）。"""
        return getattr(self.character, "action_points", None)

    def _load_session_config(self, scenario_id):
        """入口阶段选择副本方案后加载配置并初始化会话。

        在探索模式入口页用户点击“开始副本”时由 _enter_dungeon_phase() 调用：
        加载副本配置、扣 AP（经 on_entry_selected 钩子）、初始化 AI 客户端与提示词。
        返回 False 表示配置不可用，此时不应进入会话阶段。
        """
        config = None
        if self.scenario_repo is not None:
            try:
                config = self.scenario_repo.load_config(scenario_id)
            except Exception as e:
                print(f"副本配置加载失败: {e}")
        if config is None:
            # 不在 DPG 循环内弹 Tk 对话框：记录错误，关闭窗口后由调用方提示
            self._launch_error = f"无法加载副本配置 '{scenario_id}'"
            return False

        # S2：启动前校验——错误级问题阻止进入，诊断经 _launch_error 交给调用方提示
        if self.scenario_repo is not None:
            self.scenario_diagnostics = validate_scenario_config(
                config, scenario_dir=self.scenario_repo.scenario_dir(scenario_id))
            if has_errors(self.scenario_diagnostics):
                self._launch_error = (
                    f"副本方案「{scenario_id}」存在无法运行的问题，已阻止进入：\n\n"
                    + format_diagnostics(self.scenario_diagnostics))
                return False
            if self.scenario_diagnostics:
                print(f"[Scenario] 方案「{scenario_id}」校验提示：\n"
                      + format_diagnostics(self.scenario_diagnostics))

        # 扣 AP / 状态刷新：探索模式且有角色时按副本配置扣除行动点数
        on_selected = getattr(self, "_on_dungeon_selected", None)
        if callable(on_selected):
            if not on_selected(scenario_id, config):
                return False

        self.scenario_config = config
        self.scenario_id = scenario_id
        self.triggers = config.get("triggers", [])
        self.chapters = normalize_chapters(config.get("chapters", []))
        self.coupling_level = normalize_coupling_level(config.get("coupling_level"))
        # 迟到初始化：此时才创建 AI 客户端与提示词
        self._init_session(config)
        self._session_initialized = True
        return True

    def _on_dungeon_selected(self, scenario_id, config) -> bool:
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

    def _init_session(self, scenario_config):
        """新开副本：初始化 AI 客户端、提示词、演化状态与尺寸类别。"""
        scenario_config = scenario_config or {}
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

        self.initial_prompt = scenario_config.get("initial_prompt", "")
        # 主角称呼：Solea/Bulla 对话分支里主角台词的说话人标注
        # （提示词构建器读取，留空回退「主角」；Velum 不使用）
        self.protagonist_title = str(
            scenario_config.get("protagonist_title", "") or "").strip()
        self.section_prompts = scenario_config.get("section_prompts", {})
        self.evolution_attrs = scenario_config.get("evolution_attrs", [])

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
        # 段落演化规则取自副本方案配置的转移矩阵与分节步长（未配置的部分
        # 沿用内置默认）；不适应性衰减由服务层注入（dungeon 领域层不依赖 services）
        self.dungeon_logic = EvolutionRules(
            transition_matrix=scenario_config.get("transition_matrix"),
            step_overrides=scenario_config.get("section_steps"),
            step_decay=StateService.decay_step_rates)
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

        # 剧情压缩器：提示词始终携带全部压缩概要 + 最近 N 段原文
        self.story_summary = StorySummarizer(
            recent_count=self._story_recent_count(),
            coupling_level=self.coupling_level)

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
        # 回放模式同样维护剧情压缩器（不调用 AI，仅走内部算法压缩）
        self.story_summary = StorySummarizer(
            recent_count=self._story_recent_count())

    def _story_recent_count(self) -> int:
        """提示词中保留的最近段落数（可在设置中调整，默认 20）。"""
        try:
            return max(1, int((self.settings or {}).get(
                "story_recent_count", DEFAULT_RECENT_COUNT)))
        except (TypeError, ValueError):
            return DEFAULT_RECENT_COUNT

    # ---------------- 入口阶段进入（由 dungeon.window.launcher.DungeonLaunchStages 提供） ----------------
    # 子类 mixin 会覆盖 _enter_entry_phase / _init_entry_materials：
    # base 只负责在 _build_ui() 后调用统一的 enter 钩子进入入口阶段。
    def _request_close(self):
        """请求退出帧循环（可在任意回调内安全调用）。

        L0 之后渲染由 :meth:`_run_frame_loop` 手动驱动，一帧只在两帧之间
        的间隙被调用，因此直接 ``stop_dearpygui()`` 不会再打断渲染帧——
        帧循环下一轮检测到 ``is_dearpygui_running() == False`` 就走正常收尾。
        旧的 ``FindWindowW(标题) + PostMessageW(WM_CLOSE)`` 是 Windows 专有
        hack（靠临时/真实两套窗口标题找句柄），已随手动渲染一并删除。
        """
        try:
            dpg.stop_dearpygui()
        except Exception:
            pass

    def _close_loop(self):
        """入口阶段选择“返回”时关闭窗口。

        只置标记并请求停止；清理（_closing/_exit_from_entry、停调度器）由
        帧循环退出分支的 _on_close 完成，后台线程 join 与退出处理由
        _finish_session 完成。
        """
        self._closing = True
        # 挂起的重采样帧任务不再需要：窗口正在退出，让它跑只会白算一张新背景
        self._background.cancel_pending_refresh()
        self._request_close()

    # ---------- 视口尺寸（与正式副本会话窗口一致） ----------
    def _get_initial_viewport_size(self):
        """返回 (视口初始宽, 高, dpi_scale, 主窗口客户区宽, 高)。

        尺寸与 DPI 全部来自宿主端口：Tk 的 ``winfo_*`` 与 Windows 的
        ``GetDpiForWindow``/``GetClientRect`` 都在 ``ui.common.tk_host.TkHost``
        里，window 层不直接碰宿主 API。
        """
        return self.host.viewport_metrics()

    def _correct_viewport_size_to_main(self):
        try:
            delta_w = max(0, dpg.get_viewport_width() - dpg.get_viewport_client_width())
            delta_h = max(0, dpg.get_viewport_height() - dpg.get_viewport_client_height())
            dpg.set_viewport_width(self._main_client_w + delta_w)
            dpg.set_viewport_height(self._main_client_h + delta_h)
        except Exception as e:
            print(f"视口尺寸校正失败: {e}")

    def _register_with_parent(self):
        """在宿主上登记自身，以便宿主整体退出时能协调停止本窗口。

        原先沿 ``master/parent`` 链用 ``hasattr`` 摸宿主（Tk 结构知识），
        现在收进宿主适配器（``TkHost.register_active_window``）。
        """
        try:
            self.host.register_active_window(self)
        except Exception as e:
            print(f"[Dungeon] 活动窗口登记失败: {e}")

    def _unregister_with_parent(self):
        """解除在宿主上的登记。"""
        try:
            self.host.unregister_active_window(self)
        except Exception as e:
            print(f"[Dungeon] 活动窗口注销失败: {e}")

    def _discard_pending_quit(self):
        """让宿主丢掉「原生关闭键关掉视口」留下的退出类残留消息（见 §5-C12）。

        实现全在宿主适配器里（Tk 侧清的是线程队列里的 ``WM_QUIT``）：window 层
        不认识 Win32，只按端口索要这一个能力。
        """
        try:
            removed = self.host.discard_pending_quit()
        except Exception as e:
            print(f"[Dungeon] 清理宿主残留退出消息失败: {e}")
            return
        if removed:
            print(f"[Dungeon] 已丢弃宿主队列里 {removed} 条残留退出消息（原生关闭）")

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

    def _display_type_prefix(self, text_type) -> str:
        """段落前缀：结束章节内所有段落一律显示为「结局」。"""
        try:
            if self._in_terminating_chapter():
                return "【结局】"
        except Exception:
            pass
        return self._type_prefix(text_type)