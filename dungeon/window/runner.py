"""副本窗口的宿主循环：hello_imgui runner 封装（L1）。

迁移前窗口自己手写帧循环（``base._run_frame_loop``：泵宿主 → tick 帧时钟 →
``run_callbacks`` → 落实关闭请求 → ``render_dearpygui_frame`` → 让位）。那套写法
是为了绕开 DPG 的三个进程单例（上下文 / 视口 / 回调队列）：收尾要显式
``destroy_context()``，还得额外处理「视口原生窗口不被销毁」与「Tk 根窗口失去
窗口级命令能力」两个怪癖（当时的实现见 git 历史的 ``dungeon/window/dpg_state.py``
与 window.md §5）。

换到 imgui-bundle 之后这些全部消失：``hello_imgui.run(RunnerParams)`` 把 GL 后端、
上下文、视口、帧循环一次收进库内部，返回时一并释放，不需要保活视口、不需要
清残留 ``WM_QUIT``（P0-S1 已实测）。

本模块只做一件事：**把 RunnerParams 的回调挂到窗口原有的生命周期语义上**，
使 ``base`` 侧的约定（关闭只置位、帧边界落实）保持不变。

===================================  =====================================
旧（``_run_frame_loop`` 循环体）        新（``RunnerParams.callbacks``）
===================================  =====================================
——（DPG 时代字体在 ``_build_ui``）      ``load_additional_fonts``
``self._pump_host_events()``          ``before_imgui_render``
``self._frame.tick()``                同上
``dpg.run_callbacks(...)``            —— 立即模式：回调就是绘制代码本身
落实 ``_stop_requested``              同上（本回调即"帧边界"）
``dpg.render_dearpygui_frame()``      ``show_gui``
``self.frames_rendered += 1``         ``after_swap``
跳出循环 → ``self._on_close()``        ``before_exit``
===================================  =====================================

四个必须显式改掉的 hello_imgui 默认值（都不是库的错，而是本程序的用法）：

``load_additional_fonts``
    默认 ``ImGuiDefaultSettings::LoadDefaultFont_WithFontAwesomeIcons``（只带拉丁
    字形）。**字体必须在这里加载，不能放进 ``post_init``**：库的调用顺序是
    ``post_init`` → ``load_additional_fonts``，在 ``post_init`` 里 ``load_font`` 得到
    的字体没有挂上图集，之后 ``push_font`` 会直接 access violation（0xC0000005）。
    实测矩阵见 ``.workbuddy/p1_probe/_r2_report.txt``。


``ini_disable``
    默认 False → 会在**当前工作目录**写 ``imgui.ini``。本程序是用户态桌面应用，
    不该往启动目录丢文件，因此关掉。
``default_imgui_window_type``
    默认 ``provide_full_screen_window``（库自己开一个全屏窗口）。我们要完全自绘
    （无标题栏、无内边距、自己铺背景），所以改成 ``no_default_window``。
``fps_idling.enable_idling``
    默认 True → 只有事件到达才渲染。但本窗口的帧时钟（
    :class:`~dungeon.window.frame.FrameScheduler`）是**每帧 tick** 的，静默期不渲染
    等于轮播 / 仿流式动画 / 防抖全部冻结。必须关掉，退回"每帧都画"。
"""

from imgui_bundle import hello_imgui

from dungeon.settings import DEFAULT_FPS_MAX

#: 帧率上限（Hz，``dungeon/settings.py`` 的单一真相源，玩家可在副本窗口内改）。
#: 真实节拍仍由 vsync 决定（``vsync_to_monitor``）：这里只是高刷屏上的封顶，
#: 避免把一整个核跑满——与旧帧循环让位逻辑的意图一致。

#: 本进程累计进入过多少次 :func:`hello_imgui.run`（起停一次算一次，只增不减）。
#: 语义等价于迁移前的 ``dungeon.window.dpg_state.was_created()``：给「父进程是否
#: 真的在本进程跑过副本窗口」这类进程隔离自检一个只增的判据（子进程入口见
#: ``ui.hosting.dungeon_spawner``）。产品代码不读它。
_times_started = 0


def times_started() -> int:
    """本进程启动过副本窗口的次数（只增不减）。"""
    return _times_started


class WindowRunner:
    """一次窗口生命周期 = 一次 :func:`hello_imgui.run`。

    六个回调都是**可选**的，缺省即空操作，便于自检脚本只接其中一两个：

    ``on_post_init()``
        GL 后端与 ImGui 上下文就绪、首帧之前。样式与几何在这里定标，
        **字体不在这里**（见下）。
    ``on_load_fonts()``
        库加载字体的唯一时机（``load_additional_fonts``）。库保证这个回调
        **在 ``post_init`` 之后、第一帧之前**跑一次，跑完即置 None。
        在这里 ``hello_imgui.load_font`` 得到的字体才挂了图集；
        放在 ``post_init`` 里会得到一个"空"字体，``push_font`` 时 access violation。
    ``on_frame_start()``
        每帧最前（泵宿主事件、tick 帧时钟、落实关闭请求）。
    ``on_draw()``
        构建画面（立即模式，每帧重画一次）。
    ``on_frame_end()``
        换缓冲之后（计帧、自检限帧）。
    ``on_capture()``
        同样在换缓冲之后、``on_frame_end`` **之前**：此时本帧的 GL 上下文仍然
        当前，可以 ``glReadPixels`` 抓整帧（F2 截图与反色/色差视效都依赖它）。
        实现要自己在帧线程做，且必须便宜——它是每帧都会跑的空判。缺省不接。
    ``on_before_exit()``
        即将退出（库已决定收尾，但窗口与上下文仍在）。等价于旧帧循环跳出后的
        ``_on_close()``——无论关闭来自本程序还是用户点 X，都会走到这里。

    ``request_exit()`` 是唯一的程序侧关闭入口，语义与 ``base._request_close``
    一致：只置位，由下一帧的 ``on_frame_start`` 落实（``app_shall_exit = True``）。
    绝不在回调执行途中直接退出——hello_imgui 的 ``confirm_exit`` 路径会在
    帧中途拆掉上下文。
    """

    def __init__(self, title, width, height, min_width=800, min_height=500,
                 dpi_scale=1.0, on_post_init=None, on_load_fonts=None,
                 on_frame_start=None, on_draw=None, on_frame_end=None,
                 on_capture=None, on_before_exit=None, fps_max=DEFAULT_FPS_MAX):
        self.title = str(title or "")
        #: 建窗尺寸按 **96-PPI 逻辑点**给出（库内部乘 ``dpi_window_size_factor()``）。
        #: 与 ``io.display_size`` 的口径差异见模块文档与 ``ui`` 的布局换算。
        self.width = int(round(width))
        self.height = int(round(height))
        self.min_width = int(round(min_width))
        self.min_height = int(round(min_height))
        self.dpi_scale = float(dpi_scale or 1.0)
        self.fps_max = float(fps_max)

        self._on_post_init = on_post_init
        self._on_load_fonts = on_load_fonts
        self._on_frame_start = on_frame_start
        self._on_draw = on_draw
        self._on_frame_end = on_frame_end
        self._on_capture = on_capture
        self._on_before_exit = on_before_exit

        #: 关闭请求位：与 ``base._stop_requested`` 同义，由帧回调落实
        self._exit_requested = False
        #: 已经跑到 ``before_exit``：收尾钩子只放行一次
        self._exited = False
        #: 已完成帧数（自检/调试用）
        self.frames = 0

        # 回调必须是 Python 侧强引用：RunnerParams 持的是 C++ std::function，
        # nanobind 只在赋值时拷贝可调用对象，不留 Python 引用。
        self._callbacks = {}
        self.params = self._build_params()

    # ---------------- RunnerParams 组装 ----------------
    def _build_params(self):
        params = hello_imgui.RunnerParams()

        # 不写 imgui.ini（默认会往当前工作目录丢一份）
        params.ini_disable = True
        # 库不开自己的窗口：全部自绘（无标题栏 / 无内边距 / 自己铺背景）
        params.imgui_window_params.default_imgui_window_type = (
            hello_imgui.DefaultImGuiWindowType.no_default_window)
        # 静默期也要出帧：帧时钟是每帧 tick 的，停渲染等于冻结动画
        params.fps_idling.enable_idling = False
        params.fps_idling.fps_max = self.fps_max
        params.fps_idling.vsync_to_monitor = True

        window = params.app_window_params
        window.window_title = self.title
        window.resizable = True
        window.borderless = False
        # 不记忆上一次的窗口几何：自检连续开关窗口时必须每次按参数落位
        window.restore_previous_geometry = False
        geo = hello_imgui.WindowGeometry()
        geo.size = (self.width, self.height)
        geo.size_auto = False
        geo.window_size_state = hello_imgui.WindowSizeState.standard
        # 96-PPI 逻辑点：本机 200% 缩放下不会被库再乘一次（R7）
        geo.window_size_measure_mode = hello_imgui.WindowSizeMeasureMode.relative_to96_ppi
        geo.full_screen_mode = hello_imgui.FullScreenMode.no_full_screen
        window.window_geometry = geo

        # 点 X 直接退出，不弹库自带的确认框（收尾提示由本程序自己弹）。
        # 缺省即空回调，但显式置空更清楚；个别版本对 std::function 赋 None 会报错，
        # 因此容错——退回缺省行为与本意一致。
        try:
            params.callbacks.confirm_exit = None
        except Exception:
            pass
        self._install(params.callbacks, "post_init", self._run_post_init)
        # 字体：库在这里加载（post_init 之后、首帧之前）；不接就退回库的缺省拉丁字体
        if self._on_load_fonts is not None:
            self._install(params.callbacks, "load_additional_fonts",
                          self._run_load_fonts)
        self._install(params.callbacks, "before_imgui_render", self._run_frame_start)
        self._install(params.callbacks, "show_gui", self._run_draw)
        self._install(params.callbacks, "after_swap", self._run_frame_end)
        self._install(params.callbacks, "before_exit", self._run_before_exit)
        return params

    def _install(self, callbacks, name, fn):
        setattr(callbacks, name, fn)
        self._callbacks[name] = fn

    # ---------------- 回调实现 ----------------
    def _runner_params(self):
        """当前活的 RunnerParams（回调里改 ``app_shall_exit`` 用）。"""
        return hello_imgui.get_runner_params()

    def _run_post_init(self):
        if self._on_post_init is not None:
            self._on_post_init()

    def _run_load_fonts(self):
        # 库保证：post_init 之后、第一帧之前，只跑一次，然后把这个回调置 None。
        if self._on_load_fonts is not None:
            self._on_load_fonts()

    def _run_frame_start(self):
        # 帧边界：上一帧（或是宿主线程）置的关闭请求在这里落实。放在绘制**之前**，
        # 保证本帧仍完整画完再退，不会在绘制中途把上下文拆掉。
        if self._exit_requested:
            self._exit_requested = False
            self._runner_params().app_shall_exit = True
        if self._on_frame_start is not None:
            self._on_frame_start()

    def _run_draw(self):
        if self._on_draw is not None:
            self._on_draw()

    def _run_frame_end(self):
        self.frames += 1
        if self._on_capture is not None:
            # 抓帧钩子：本程序的 F2 截图与反色/色差视效都在这里读后备缓冲。
            # 时机是**换缓冲之后、本帧的 GL 上下文仍然当前**——探针实测
            # （``.workbuddy/p5_probe/probe_grab.py``）此时 ``glReadPixels`` 拿到的
            # 就是刚画完的整帧；而库自带的 ``final_app_window_screenshot()`` 在
            # 帧内返回空数组，只有退出前那个钩子里才有内容。
            # 放 ``after_swap`` 而不是 ``show_gui`` 里，是因为绘制中间读会把
            # 半成品画面上屏（反色/色差覆盖层要的是"完整的一帧"）。
            # 异常吞掉但留痕：抓帧失败不该让会话挂掉（F2 会自己给失败提示）。
            try:
                self._on_capture()
            except Exception as exc:
                from dungeon import process_log
                process_log.log(f"[Dungeon] 抓帧钩子异常: {exc}")
        if self._on_frame_end is not None:
            self._on_frame_end()

    def _run_before_exit(self):
        if self._exited:
            return
        self._exited = True
        if self._on_before_exit is not None:
            self._on_before_exit()

    # ---------------- 程序侧入口 ----------------
    def request_exit(self):
        """请求关闭窗口（任意线程、任意回调内均可安全调用）。

        只置位；由下一帧的 ``on_frame_start`` 落实成 ``app_shall_exit``。
        """
        self._exit_requested = True

    @property
    def exit_requested(self) -> bool:
        return self._exit_requested

    def set_fps_max(self, fps_max) -> None:
        """运行期改帧率上限（副本窗口内的设置面板用）。

        只改 ``fps_idling.fps_max``；真实节拍仍由 vsync 决定。解析失败时保持原值。
        """
        try:
            value = float(fps_max)
        except (TypeError, ValueError):
            return
        self.fps_max = value
        try:
            self.params.fps_idling.fps_max = value
        except Exception:
            pass

    def set_fullscreen(self, enabled: bool) -> bool:
        """（**已废弃，P5 起不再使用**）按 ``window_geometry.full_screen_mode`` 切换整屏。

        P5 实测这条路在 1.92.900 上是坏的：请求 ``full_monitor_work_area`` 会把窗口
        变成 420×34，请求还原也回不到原尺寸（``.workbuddy/p5_probe/probe_dpi2.py``，
        ``Case fs_geometry``）。真正的 F11 走 ``glfw_fullscreen.FULLSCREEN``（对
        GLFW 窗口调 ``glfwSetWindowMonitor``）。

        方法本身保留：它没有副作用（只写参数结构体），留着不占用运行期成本，
        也便于将来在别的 imgui-bundle 版本上复测这条官方路径。
        """
        try:
            # ``window_geometry`` 是嵌套结构体，nanobind 取出来是副本：改完必须
            # 整体写回，否则改动落不到库那一侧。
            geo = self.params.app_window_params.window_geometry
            geo.full_screen_mode = (
                hello_imgui.FullScreenMode.full_monitor_work_area if enabled
                else hello_imgui.FullScreenMode.no_full_screen)
            # 让库在下一帧重建窗口尺寸（与 full_screen_mode 同批写回）
            geo.resize_app_window_at_next_frame = True
            self.params.app_window_params.window_geometry = geo
            return True
        except Exception:
            return False

    def run(self):
        """阻塞直到窗口关闭。调用方负责在前后做收尾（``base._start_session``）。"""
        global _times_started
        _times_started += 1
        hello_imgui.run(self.params)
