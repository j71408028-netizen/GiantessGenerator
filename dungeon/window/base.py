"""窗口生命周期与基础属性（imgui-bundle 立即模式）。

入口阶段与正式运行阶段（Run）共享**同一次窗口生命周期（Session）**：切换副本方案发生在
同一窗口内（:class:`dungeon.window.launcher.DungeonLaunchStages` 负责入口阶段），
选择“开始副本 / 加载回放”后由 ``_enter_dungeon_phase()`` 切到运行阶段，同一 Session
内不再重建窗口。两局之间也不再需要"保活视口"之类的怪癖——每次 ``run()`` 都是
一个独立的 :func:`hello_imgui.run`，返回即全部释放。

**窗口本体由 hello_imgui 托管**（imgui-bundle 1.92.900，见
:class:`dungeon.window.runner.WindowRunner`）：GL 后端、ImGui 上下文、视口与帧循环
都收在 ``hello_imgui.run(RunnerParams)`` 内部。迁移前那套「进程单例上下文 + 保活
视口 + 显式 ``destroy_context`` + 清残留 ``WM_QUIT``」的收尾（原
``dungeon.window.dpg_state``）整体消失，本类因此只剩与后端无关的生命周期：

- ``_start_session``：藏宿主 → 取一次宿主尺寸 → ``_build_ui()`` → 进入口/运行阶段 →
  ``self._runner.run()``（阻塞到窗口关闭）；
- ``_finish_session``：停帧时钟 → 收组件/覆盖层/背景/音频 → 清残留退出消息 →
  恢复宿主 → 退出处理。

关闭请求（``_request_close``）只置位，由 runner 在**帧边界**落实成
``app_shall_exit``——绝不在回调执行途中直接退（hello_imgui 的 ``confirm_exit``
路径会在帧中途拆掉上下文）。

帧时钟（L3）：窗口自带一个 :class:`~dungeon.window.frame.FrameScheduler`
（``self._frame``），背景轮播、结局图标翻页、Ken Burns、仿流式动画与背景防抖
都挂在它上面，它是本层的唯一时间源；会话结束随之 stop。

结果（L4）：:meth:`run` 返回 :class:`~dungeon.window.result.SessionResult`，
调用方不再读窗口的私有属性。
"""

import os
import random
from collections import deque

from core.ai import create_client
from core import netproxy
from dungeon import process_log
from dungeon import settings as dungeon_settings
from dungeon.audio import BgmPlayer, buses
from dungeon.audio.speech import (SpeechDirector, VoiceCaster, normalize_voice,
                                 set_proxy as set_speech_proxy)
from dungeon.window.background import DungeonBackground
from dungeon.window.fx import WindowFx
from dungeon.audio.chapters import find_chapter, normalize_chapters
from dungeon.coupling import normalize_coupling_level
from dungeon.scene import landmarks_for_chapter
from dungeon.window.frame import FrameScheduler
from dungeon.window.host import HostPort
from dungeon.window.result import (REASON_ALREADY_RUNNING, REASON_ENTRY_CANCELLED,
                                   REASON_LAUNCH_FAILED, REASON_SESSION_ENDED,
                                   SessionResult)
from dungeon.models import DungeonTextType, DungeonState
from dungeon.prompts import DungeonPromptBuilder
from dungeon.rules import EvolutionRules
from dungeon.summary import StorySummarizer
from dungeon.admission import check_entry_requirements
from dungeon.validate import format_diagnostics, has_errors, validate_scenario_config
from core.logic import get_size_category
from services.state_service import StateService


class DungeonWindowBase:
    """副本窗口的生命周期：构造（``__init__``）与运行（``run``）分离。

    驱动方式：把窗口交给 :class:`~dungeon.window.runner.WindowRunner`
    （内部 ``hello_imgui.run(RunnerParams)``），帧回调挂在
    ``RunnerParams.callbacks`` 上（见 ``dungeon.window.ui`` 的五个钩子）。由此：

    - 关闭只需 runner 置 ``app_shall_exit``（跨平台），不再有按窗口标题
      ``FindWindowW`` + ``WM_CLOSE`` 的平台 hack，也不再需要 ``stop_dearpygui``；
    - 用户点 X 与程序侧关闭走同一条 ``before_exit`` 钩子（见
      :meth:`_finalize_close_once`），不再靠 ``is_dearpygui_running()`` 轮询；
    - 帧节奏（vsync + ``fps_max`` 封顶）交给 RunnerParams，本类不再手写让位逻辑。

    帧时钟（L3）：窗口自带一个 :class:`~dungeon.window.frame.FrameScheduler`
    （``self._frame``），背景轮播、结局图标翻页、Ken Burns、仿流式动画与背景防抖
    都挂在它上面，它是本层的唯一时间源；会话结束随之 stop。

    结果（L4）：:meth:`run` 返回 :class:`~dungeon.window.result.SessionResult`，
    调用方不再读窗口的私有属性。
    """

    #: 帧循环期间是否泵宿主事件（Tk 的 ``update()``）：开启后主窗口在副本
    #: 运行期间仍可响应/重绘，不再"假死"。宿主不提供 ``update()`` 时自动跳过。
    PUMP_HOST_EVENTS = True
    #: 同一进程内只允许一个会话在跑。宿主事件泵会把调用方的回调重新放回
    #: 主线程，若不挡住就可能嵌套创建第二个窗口会话（GL 上下文/窗口是进程级资源）。
    _session_running = False

    def __init__(self, parent, name, nick, personality, preset, original_height,
                 intro_hidden, intro_visible, tags, uploaded_image,
                 scenario_config, scenario_repo,
                 merged_landmarks, merged_quips,
                 selected_styles, selected_quip_styles, detail_pools, height,
                 ai_config, greed: int,
                 is_replay=False, replay_data=None, scenario_id=None, dungeon_font=None,
                 body_parts=None, character=None, character_repo=None, gui=None,
                 mode="explore", scenario_ids=None, host=None, settings=None,
                 dungeon_settings=None, dungeon_settings_repo=None):
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
        # 副本独占设置（音量 / 段落字体 / 记忆窗口 / 自动保存回放 / 帧率上限）：
        # 与主程序共用的 settings 分开，由 ``dungeon_settings_repo`` 注入（子进程里
        # 由 ChildHost 侧按数据目录自建并读取，见 dungeon/settings.py）。缺省空表
        # 时各处退回缺省常量。
        self.dungeon_settings = dict(dungeon_settings or {})
        self.dungeon_settings_repo = dungeon_settings_repo
        self.dungeon_font = (dungeon_font
                             or self.dungeon_settings.get("dungeon_font")
                             or self.host.default_font())
        self.body_parts = body_parts
        # 副本保存/回放相关
        self.character = character
        self.character_repo = character_repo
        self.gui = gui
        # 运行模式："explore"（探索模式）或 "challenge"（挑战模式）
        self.mode = mode
        # 设置：``settings`` 参数优先（子进程没有 gui 可问，父进程随载荷传入）；
        # 同进程调用未传时回退 gui.settings，两者都缺省为空表（自检替身等）。
        # 只装与主程序共用的键（net_proxy / info_update_rate / blocked_words …），
        # 副本独占设置在 self.dungeon_settings 里。
        if settings is not None:
            self.settings = dict(settings)
        elif gui is not None:
            self.settings = getattr(gui, "settings", None) or {}
        else:
            self.settings = {}
        # 玩家侧音频总线电平（副本独占设置）装进混音层：三条播放路径（BGM /
        # 朗读 / 语音效果渲染）都从 buses 取，不必逐处透传。
        buses.set_bus_levels(buses.levels_from_settings(self.dungeon_settings))
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
        # 收尾钩子一次性闸门：``before_exit`` 与自检限帧都会请求收尾，由
        # :meth:`_finalize_close_once` 保证 ``_on_close()`` 只跑一次。
        self._close_hook_done = False
        self._text_update_pending = False
        self._text_item_tags = []
        # 帧时钟（L3）：时机在这里，重活在外面。取代原先的模块级单例 _dispatch：
        # 每帧 tick 一次（先跑到期任务，再 drain 主线程更新队列），会话结束 stop。
        self._frame = FrameScheduler()
        self._bg_pil_full = None
        self._bg_pil_original = None
        self._bg_revision = 0
        self._background = DungeonBackground(self)
        # 窗口视效（闪光/振动/反色/色差分离）：触发即播、按时长自消，不阻塞任何动作
        self._window_fx = WindowFx(self)
        # 章节背景音乐：一个人一条（bgm 与 background 共用同一套素材路径解析，
        # 因此把 background 的解析器注入进去，省得多写一份相对路径兜底）
        self.current_bgm_path = ""
        # 章节对话语音效果（她 / 其他人两档），进章时由 triggers 填。
        # current_voice_fx 是**当前生效值**（可能被「章节效果短暂覆盖」改写），
        # 章节默认值留在 chapter_voice_fx，覆盖到期后据此回退。
        self.current_voice_fx = {}
        self.chapter_voice_fx = {}
        self._applied_voice_fx = {}
        self._bgm = BgmPlayer(resolver=self._background.resolve_path,
                              logger=process_log.log)
        # 对话语音：Solea/Bulla 的带说话人台词由它朗读（音色在会话初始化时
        # 按方案配置填进去，见 _configure_voice）
        self._speech = SpeechDirector(logger=process_log.log, enabled=False)

        # 入口阶段（dungeon.window.launcher.DungeonLaunchStages）与运行阶段共用同一 Session 生命周期
        self.scenario_ids = list(scenario_ids) if scenario_ids else []
        self._is_entry_phase = False

        # 回放模式相关
        self.current_replay_index = 0
        self.triggers = (scenario_config or {}).get("triggers", []) if not is_replay else []
        self.chapters = normalize_chapters((scenario_config or {}).get("chapters", []))
        # 当前章节的尺寸换算候选池：章节名 -> 池子（切章失效，见 chapter_landmarks）
        self._chapter_landmark_cache = None
        # 章节运行时状态：当前章节、章节持续敏感效果、章节效果的短暂覆盖
        self.current_chapter = None
        self.chapter_sensitivity_effects = []
        # 短暂覆盖条目：[{remaining, overrides}]，见 dungeon/overrides.py；
        # 三个 _applied_* 记录各维度已落地值，避免每步重复刷背景 / 挪音量 / 重设语音
        self.effect_overrides = []
        self._applied_visual_filter = None
        self._applied_bgm_level = None
        self.current_background_path = ""
        self.triggered_names = set()   # 已触发且不可再次触发的触发器
        self.fired_triggers = set()    # 至少触发过一次的触发器（前置条件判断用）
        self._round_fired = set()      # 本轮判定已生效的触发器（结算重判不重发，见 triggers._begin_trigger_round）
        self.replay_data = []
        # 插入触发器触发后排队的段落（FIFO）
        self.pending_insertions = deque()
        # 当前逻辑段落（一次 AI 输出）内待揭示的显示段落（内置分句器切分）
        self._pending_units = []
        # 固定文本章节（章节字段 fixed_text）的脚本文本与推进进度：
        # _script 为解析后的 Script，_script_key 是 (章节名, 路径) 缓存键，
        # _script_index 是下一段下标；换章时整体重置（见 triggers._enter_chapter）
        self._script = None
        self._script_key = None
        self._script_index = 0
        self._script_exhausted = False
        # 上屏游标（限速 30 字/秒的仿流式输出）：当前显示段落的逐字状态，
        # 真流式下目标文本随 chunk 增长；同时为自动播放记录停留到期时刻。
        self._reveal = None
        self._advance_due = 0.0
        # 细节探究：AI 在后台提出的想了解细节，下次生成前检索解答并消费
        self._detail_queries = []
        self._detail_querying = False
        # 预演化：下一步的预生成结果（玩家点击前提前发出的生成调用）。
        # _pregen = {"user_prompt", "messages", "response", "text_type"}
        self._pregen = None
        self._pregen_inflight = False
        # 选项触发器状态
        self.trigger_choices = {}      # 触发器名 -> [已选择编号...]
        self.pending_option = None     # 待弹出的选项触发器数据
        self.option_choice = None      # 最近一次选择 {"name","index","prompt","text"}
        self._option_generating = False
        # 回放记录引用：选择/结局生成完成后把结果写回对应记录
        self._last_option_record = None
        self._last_ending_record = None
        # 小游戏触发器状态：pending_mini_game 非空期间步进挂起，等结果回传
        self.pending_mini_game = None
        self._last_mini_game_record = None
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
        # 显示组件（见 component_pack/ 与 dungeon/window/components.py）。
        # 这里先给入口阶段用的空默认值，运行阶段进入时由 _init_components 复位重建。
        self._components = []
        self._components_built = False

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
        # 窗口标题在 runner 建窗时一次性给出（GLFW 的 glfwSetWindowTitle 收 UTF-8，
        # 中文标题直接生效，不再需要 DPG 时代"先建临时标题、首帧再 FindWindowW
        # + SetWindowTextW 改回来"的绕行）。
        self._real_title = f"副本模式 - {self.name}" if not self.is_replay else f"回放模式 - {self.name}"

        # 帧循环统计（自检/调试用）
        self.frames_rendered = 0
        # 自检钩子：副本会话在独立子进程里跑（ui.hosting.dungeon_spawner），
        # 冒烟自检经环境变量限制帧数，到帧按「用户点关闭键」收尾，避免
        # 无人值守的会话挂在帧循环里。正常使用不设置该变量，钩子不生效。
        try:
            self._test_frame_limit = int(
                os.environ.get("GIANTESS_DUNGEON_TEST_FRAMES") or 0)
        except (TypeError, ValueError):
            self._test_frame_limit = 0
        if self._test_frame_limit:
            self._test_frame_limit = max(1, self._test_frame_limit)

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
            process_log.log("[Dungeon] 已有副本会话在运行，忽略本次启动")
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
        # 向宿主登记自身，以便宿主整体退出时能协调停掉本窗口
        self._register_with_parent()
        try:
            # 宿主尺寸/DPI 要在它**仍可见**时取下（下一步就藏起宿主；隐藏状态下取
            # 客户区在不同平台上结果不一致）。见 _get_initial_viewport_size。
            self._host_metrics = self.host.viewport_metrics()

            # 藏起宿主：副本窗口是独立顶层窗口，不藏会两个窗口同时占屏。
            self.host.hide_window()

            self._build_ui()

            # 入口阶段：与正式运行阶段共享同一个窗口，
            # 用户在入口页选择副本方案后由 _enter_dungeon_phase() 切换到运行阶段。
            if self.scenario_ids:
                self._enter_entry_phase()
            else:
                # 无入口阶段（挑战模式/直接指定配置）：会话配置已在 __init__ 初始化，
                # 立即构建显示组件并进入起始章节。
                self._init_components()
                self._build_components()
                self._enter_start_chapter()

            # 托管帧循环：阻塞到窗口关闭（无论来自本程序还是用户点 X）。
            # 收尾统一落在 finally 的 _finish_session()。
            self._runner.run()
        finally:
            self._finish_session()

    def _finalize_close_once(self):
        """收尾钩子的一次性闸门：``_on_close()`` 只允许跑一次。

        两条路径都会请求收尾——runner 的 ``before_exit``（用户点 X，或本程序置了
        ``app_shall_exit``）与自检限帧（``ui._on_frame_end``）。入口页"返回"会先
        把 ``_closing`` 置真再请求关闭，因此**不能**用 ``_closing`` 当"是否已收尾"
        的判据（那样会漏掉 ``_exit_from_entry`` 标记，结果被误判成会话结束而非
        入口取消）；改用独立闸门。
        """
        if getattr(self, "_close_hook_done", False):
            return
        self._close_hook_done = True
        self._on_close()

    def _pump_host_events(self):
        """让宿主处理一次挂起事件，避免主窗口在副本运行期间"假死"。

        经宿主端口调用（``TkHost`` 映射到 ``widget.update()``）；没有宿主或
        宿主不支持时静默跳过。重入由 ``_session_running`` 兜住：宿主回调里
        再次启动副本会被 :meth:`run` 忽略，不会嵌套出第二个窗口会话。
        """
        if not self.PUMP_HOST_EVENTS:
            return
        try:
            self.host.pump_events()
        except Exception:
            pass

    def _finish_session(self):
        """窗口生命周期结束后的收尾（无论正常关闭还是异常都执行）。

        到达这里时 ``hello_imgui.run()`` **已经返回**：GL 后端、ImGui 上下文与
        视口都已随库内部收尾释放。因此本方法的顺序与 DPG 时代不同——不再需要
        "赶在 destroy_context 之前"清控件，也不必清残留视口原生窗口；剩下的只是
        本程序自己的资源（帧时钟、组件、音频、AI 线程）。
        """
        # 帧时钟先停：此后后台线程投递的界面更新与未跑的帧任务一律丢弃，
        # 不会再碰到即将消失的窗口。
        self._frame.stop()
        # GPU 纹理句柄兜底：本方法跑在 ``hello_imgui.run()`` **返回之后**，此时
        # ``TextureGpu`` 的 RAII 析构会去碰已释放的 GL 后端而段错误——下面
        # ``_destroy_components`` / ``_destroy_overlays`` / ``_window_fx.shutdown``
        # 清空的容器里**不许**再有句柄。正常路径它们在 ``before_exit``
        # （``ui._release_gpu_textures``，GL 仍存活）里就释放干净了；这里兜住
        # 「钩子没被调用」的异常收尾，必须排在那几步之前。
        # 口径与理由见 dungeon/window/gpu_textures.py。
        self._retire_gpu_textures()
        # 显示组件销毁：丢弃实例缓存（P1 无组件，P4 起才有实体）
        self._destroy_components()
        # 覆盖层（对话记录等）同批清理，并复位阅读模态标记
        self._destroy_overlays()
        # 背景像素工作者：会话内的全部重采样/混合任务都在这里排队，收工时一并结束
        self._background.shutdown()
        # 窗口视效：帧任务已随 stop() 清空，这里收掉像素线程、丢掉覆盖层纹理句柄
        self._window_fx.shutdown()
        # 截图落盘线程（P5）：可能正在写 PNG，给它 join 的机会再往下走
        shutdown_shots = getattr(self, "_shutdown_screenshot_worker", None)
        if callable(shutdown_shots):
            shutdown_shots()
        # 章节背景音乐：会话一结束就该静音（帧循环已停，渐变任务不会再被调用方刷新）
        self._bgm.shutdown()
        # 对话语音：同样必须立刻收声，顺手清掉合成的临时音频
        self._speech.shutdown()

        # 用户点关闭键（X）退出时，GLFW 销毁原生窗口会往本线程队列里留一条
        # WM_QUIT；它不会危害本程序，却会让 Tk 的计时器与模态对话框（tkwait）
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

        # 兜底再清一次残留退出消息：万一将来 GLFW 改了行为，漏清就会重演
        # §5-C12 的「Tk 计时器/模态框全停摆」。
        self._discard_pending_quit()

        # 解除在宿主上的登记（恢复宿主时可能用得到活动窗口列表，故放在最后）
        self._unregister_with_parent()

    # ---------------- 会话访问器 ----------------
    def chapter_landmarks(self) -> list:
        """本章的尺寸换算候选池（提示词与窗口共用）。

        章节没写地址也没取清单时返回 ``merged_landmarks`` 全量——旧方案因此与
        改动前逐字一致；写了地址就只留地址相配的全局地标，取清单则以清单为准
        （组装规则见 ``dungeon/scene.py``）。按章节名缓存，切章失效。
        """
        key = self.current_chapter or ""
        cached = self._chapter_landmark_cache
        if cached is not None and cached[0] == key:
            return cached[1]
        chapter = find_chapter(self.chapters, self.current_chapter)
        pool = landmarks_for_chapter(self.scenario_config, chapter,
                                     self.merged_landmarks)
        self._chapter_landmark_cache = (key, pool)
        return pool

    # ---------------- 会话内容初始化 ----------------
    def _current_action_points(self):
        """当前角色的行动点数；挑战模式等无角色场景返回 None（策略值按缺省因子计算）。"""
        return getattr(self.character, "action_points", None)

    def _load_session_config(self, scenario_id):
        """入口阶段选择副本方案后加载配置并初始化会话。

        在探索模式入口页用户点击“开始副本”时由 _enter_dungeon_phase() 调用：
        加载副本配置、扣 AP（经 on_entry_selected 钩子）、初始化 AI 客户端与提示词。
        返回 False 表示配置不可用，此时不应进入运行阶段。
        """
        config = None
        if self.scenario_repo is not None:
            try:
                config = self.scenario_repo.load_config(scenario_id)
            except Exception as e:
                process_log.log(f"副本配置加载失败: {e}")
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
                process_log.log(f"[Scenario] 方案「{scenario_id}」校验提示：\n"
                      + format_diagnostics(self.scenario_diagnostics))

        # 进入准入：地址（有地址时）与身高。放在扣点之前——被拦下不应消耗行动点。
        character = self.character
        admission_error = check_entry_requirements(
            config,
            position=getattr(character, "position", "") or "",
            height=(getattr(character, "height", None) if character is not None
                    else self.height),
        )
        if admission_error:
            self._launch_error = (
                f"无法进入副本方案「{scenario_id}」：\n\n{admission_error}")
            return False

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

        返回 False 表示行动点数不足或其它原因，不应进入运行阶段。
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
                        process_log.log(f"[Dungeon] 角色保存失败: {e}")
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
            process_log.log(f"[Dungeon] 主界面状态同步失败: {e}")

    def _init_session(self, scenario_config):
        """新开副本：初始化 AI 客户端、提示词、演化状态与尺寸类别。"""
        scenario_config = scenario_config or {}
        # 网络代理：语音与 AI 共用（见 core/netproxy.py）。**必须在创建 AI 客户端
        # 之前设**——代理在客户端构造时定格；副本跑在独立子进程里，主进程设的代理
        # 带不过来，这里按本进程读到的 settings 再设一次。
        netproxy.set_override((self.settings or {}).get("net_proxy"))
        try:
            self.ai_client = create_client(
                self.ai_config.get("provider"),
                self.ai_config.get("api_key", ""),
                base_url=self.ai_config.get("url") or None,
                model=self.ai_config.get("model") or None,
            )
        except Exception as e:
            process_log.log(f"AI 客户端初始化失败: {e}")
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

        # 剧情压缩器：提示词携带全部压缩概要 + 事实卡 + 关键事件
        # （近期原文由对话历史携带，窗口大小见 _message_window_size）
        self.story_summary = StorySummarizer(
            coupling_level=self.coupling_level)

        self.prompt_builder = DungeonPromptBuilder(self)
        self.system_prompt = self.prompt_builder.build_system_prompt()
        self.messages = [{"role": "system", "content": self.system_prompt}]

        self._configure_voice(scenario_config)

    def _configure_voice(self, scenario_config=None):
        """按方案配置装配对话语音（Solea/Bulla 对话句朗读）。

        「玩家不想听」不再有独立开关——把设置页「音量」里的**语音总线**拧到 0
        即可：既不看方案配置，也不合成（见 ``dungeon/audio/speech.py`` 的总线
        判空）。章节级的物理效果（``current_voice_fx``）随它一起装配：语音不出声
        时自然不会有效果。
        """
        cfg = normalize_voice((scenario_config or {}).get("voice"))
        # 网络代理：语音与 AI 共用，副本子进程里按本进程读到的 settings 再设一次
        # （主进程设的带不过来，见 core/netproxy.py）。
        set_speech_proxy((self.settings or {}).get("net_proxy"))
        caster = VoiceCaster(
            her_names=(self.name, self.nick),
            # 注意逗号：单元素元组写成 ``(x)`` 会得到字符串，遍历时按字符拆开
            protagonist_names=((getattr(self, "protagonist_title", "") or "主角"),),
            voice_her=cfg["voice_her"],
            voice_protagonist=cfg["voice_protagonist"],
            other_voices=cfg["voice_other"],
        )
        self._speech.configure(caster=caster, rate=cfg["rate"],
                               level=cfg["level"], enabled=cfg["enabled"],
                               effect=getattr(self, "current_voice_fx", None) or {})

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
        self.story_summary = StorySummarizer()
        # 回放也照方案配置朗读（音色可能只有默认值——回放数据里没有主角称呼）
        self._configure_voice(getattr(self, "scenario_config", None))

    def _message_window_size(self) -> int:
        """对话历史保留的消息条数（不含系统提示；可在设置中调整，默认 20）。

        ``story_recent_count``（「副本记忆窗口大小」，副本独占设置）原先控制提示词
        里的最近段落原文数；近期原文改由对话历史携带后，该设置转义为历史窗口
        大小，语义不变——仍决定 AI 能逐字看到多少近期剧情。
        """
        try:
            default = dungeon_settings.DEFAULT_STORY_RECENT_COUNT
            return max(2, int((self.dungeon_settings or {}).get(
                "story_recent_count", default)))
        except (TypeError, ValueError):
            return dungeon_settings.DEFAULT_STORY_RECENT_COUNT

    def set_dungeon_settings(self, patch: dict, *, persist: bool = True) -> dict:
        """副本窗口内改独占设置（音量 / 帧率 / 组件代管区停靠侧…）：就地生效并落盘。

        落盘走注入的 ``dungeon_settings_repo``（会话在子进程里，独占文件同一时刻
        只有一个写者，可直接写）。没有仓库时（自检替身）只改内存。音频电平立即
        生效；帧率上限同步给运行器；窗口视觉设置（如代管区停靠侧）变更后立即重排
        组件，玩家改完马上看到新布局。
        """
        patch = patch or {}
        self.dungeon_settings.update(patch)
        buses.set_bus_levels(buses.levels_from_settings(self.dungeon_settings))
        if "fps_max" in patch:
            runner = getattr(self, "_runner", None)
            if runner is not None and hasattr(runner, "set_fps_max"):
                runner.set_fps_max(self.dungeon_settings.get("fps_max"))
        if "dock_side" in patch and getattr(self, "_components_built", False):
            relayout = getattr(self, "_relayout_components", None)
            if callable(relayout):
                relayout()
        if persist and self.dungeon_settings_repo is not None:
            try:
                self.dungeon_settings = self.dungeon_settings_repo.update(
                    dict(self.dungeon_settings))
            except Exception as e:
                process_log.log(f"[Dungeon] 副本设置保存失败: {e}")
        return self.dungeon_settings

    # ---------------- 入口阶段进入（由 dungeon.window.launcher.DungeonLaunchStages 提供） ----------------
    # 子类 mixin 会覆盖 _enter_entry_phase / _init_entry_materials：
    # base 只负责在 _build_ui() 后调用统一的 enter 钩子进入入口阶段。
    def _request_close(self):
        """请求关闭副本窗口（可在任意回调内安全调用）。

        只置位 runner 的退出请求，由下一帧的帧回调在**帧边界**落实成
        ``app_shall_exit``（见 :class:`dungeon.window.runner.WindowRunner`）。

        **不要**在这里直接退出：hello_imgui 一旦决定收尾就会在帧中途拆掉上下文，
        从回调内部直接触发会让收尾不干净。DPG 时代同样踩过这个坑——2026-09-13
        「窗口关闭后进程在 Tk 主循环中于 _dearpygui.pyd 内崩溃」、2026-10-07
        「入口页返回后延迟崩溃 / 视口僵尸残留」；结论一致：停止必须落在回调批
        之外的帧边界。见 window.md §2.2 / §5-C1。
        """
        runner = getattr(self, "_runner", None)
        if runner is not None:
            runner.request_exit()

    def request_close(self):
        """公开版 :meth:`_request_close`：宿主整体退出时停掉本窗口。

        宿主（如挂件界面）只持有登记进来的窗口实例，用这个入口即可，
        不必自己碰私有方法。
        """
        self._request_close()

    def _close_loop(self):
        """入口阶段选择“返回”时关闭窗口。

        只置标记并请求关闭；``_closing`` / ``_exit_from_entry`` 等收尾由
        :meth:`_finalize_close_once` 里的 ``_on_close()`` 完成，后台线程 join
        与退出处理由 :meth:`_finish_session` 完成。
        """
        self._closing = True
        # 挂起的重采样帧任务不再需要：窗口正在退出，让它跑只会白算一张新背景
        self._background.cancel_pending_refresh()
        self._request_close()

    # ---------- 视口尺寸（与正式运行阶段窗口一致） ----------
    def _get_initial_viewport_size(self):
        """返回 (视口初始宽, 高, dpi_scale, 主窗口客户区宽, 高)。

        尺寸与 DPI 全部来自宿主端口：Tk 的 ``winfo_*`` 与 Windows 的
        ``GetDpiForWindow``/``GetClientRect`` 都在 ``ui.hosting.tk_host.TkHost``
        里，window 层不直接碰宿主 API。

        ``_start_session`` 会在藏起宿主**之前**取一次并缓存在 ``_host_metrics``：
        宿主隐藏后取到的客户区在不同平台上结果不一致。
        """
        metrics = getattr(self, "_host_metrics", None)
        if metrics is not None:
            return metrics
        return self.host.viewport_metrics()

    def _register_with_parent(self):
        """在宿主上登记自身，以便宿主整体退出时能协调停止本窗口。

        原先沿 ``master/parent`` 链用 ``hasattr`` 摸宿主（Tk 结构知识），
        现在收进宿主适配器（``TkHost.register_active_window``）。
        """
        try:
            self.host.register_active_window(self)
        except Exception as e:
            process_log.log(f"[Dungeon] 活动窗口登记失败: {e}")

    def _unregister_with_parent(self):
        """解除在宿主上的登记。"""
        try:
            self.host.unregister_active_window(self)
        except Exception as e:
            process_log.log(f"[Dungeon] 活动窗口注销失败: {e}")

    def _discard_pending_quit(self):
        """让宿主丢掉「原生关闭键关掉视口」留下的退出类残留消息（见 §5-C12）。

        实现全在宿主适配器里（Tk 侧清的是线程队列里的 ``WM_QUIT``）：window 层
        不认识 Win32，只按端口索要这一个能力。
        """
        try:
            removed = self.host.discard_pending_quit()
        except Exception as e:
            process_log.log(f"[Dungeon] 清理宿主残留退出消息失败: {e}")
            return
        if removed:
            process_log.log(f"[Dungeon] 已丢弃宿主队列里 {removed} 条残留退出消息（原生关闭）")

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