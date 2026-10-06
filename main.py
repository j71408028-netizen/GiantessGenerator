import multiprocessing
import sys
import traceback

import tkinter as tk
import customtkinter as ctk

from paths import ensure_cwd
import ui.common.ctk_patch  # noqa: F401  模式切换时同步刷新 CTk 控件 Frame 底色，避免几何重排露旧色
from ui.common import fonts as ui_fonts
from services.exploration.context import ExplorationContext
from app.window_manager import MainWindowManager
from persistence import SettingsRepo, LandmarkRepo, PresetRepo, PersonalityRepo
from persistence import QuipRepo, ScenarioRepo, CharacterRepo
from services.worlds import WorldManager
from ui.common.loading import LoadingWindow
from ui.common.splash import splash_process
from ui.common.theme import DEFAULT_PALETTE, apply_palette


def _load_repos(state):
    world_state = state.get("world_state")
    state["landmark_repo"] = LandmarkRepo(world_state=world_state)
    state["preset_repo"] = PresetRepo(world_state=world_state)
    state["personality_repo"] = PersonalityRepo(world_state=world_state)
    state["quip_repo"] = QuipRepo(world_state=world_state)
    state["scenario_repo"] = ScenarioRepo(world_state=world_state)
    state["character_repo"] = CharacterRepo()


def _build_context(state, settings, settings_repo):
    state["context"] = ExplorationContext(
        settings=settings,
        landmark_repo=state["landmark_repo"],
        quip_repo=state["quip_repo"],
        preset_repo=state["preset_repo"],
        personality_repo=state["personality_repo"],
        character_repo=state["character_repo"],
        settings_repo=settings_repo,
        scenario_repo=state["scenario_repo"],
        world_state=state.get("world_state")
    )


def _build_manager(root, state, report):
    state["manager"] = MainWindowManager(
        root, state["context"], on_progress=report,
        world_manager=state.get("world_manager"))


def _build_tab(state, page_key):
    manager = state["manager"]
    getattr(manager, f"create_{page_key}_tab")(manager.pages[page_key])


def _build_settings(state):
    state["manager"].create_settings_panel()


def _launch_splash(theme_mode, color_theme, palette):
    """启动独立进程启动屏，返回 (Pipe 连接, Process)；失败时返回 (None, None)。"""
    parent_conn = child_conn = None
    try:
        parent_conn, child_conn = multiprocessing.Pipe()
        proc = multiprocessing.Process(
            target=splash_process,
            args=(child_conn, theme_mode, color_theme, "「正在初始化」", palette),
            daemon=True,
        )
        proc.start()
        child_conn.close()
        return parent_conn, proc
    except Exception as e:
        print(f"[Warning] 启动子进程启动屏失败，回退到进程内加载窗口: {e}")
        for conn in (child_conn, parent_conn):
            try:
                conn.close()
            except Exception:
                pass
        return None, None


def _request_splash_geometry(splash_conn):
    """请求启动屏当前几何信息（state, geometry），超时/失败返回 None。"""
    try:
        splash_conn.send(("request_geometry",))
        if splash_conn.poll(2.0):
            reply = splash_conn.recv()
            if reply and reply[0] == "geometry":
                return reply[1], reply[2]
    except Exception:
        pass
    return None


def _handoff_to_main(root, splash_conn, splash_proc, fallback_loading, state):
    """按启动屏被拖动/缩放后的形态，原位替换为真实主窗口。"""
    manager = state.get("manager")
    win_state, geometry = "normal", None

    if splash_conn is not None:
        result = _request_splash_geometry(splash_conn)
        if result:
            win_state, geometry = result
    elif fallback_loading is not None:
        try:
            win_state = fallback_loading.state()
        except Exception:
            win_state = "normal"
        try:
            geometry = fallback_loading.geometry()
        except Exception:
            geometry = None

    # 先把主窗口映射在启动屏所在位置/尺寸处，再关闭启动屏，实现原位替换。
    # 最小化状态下 geometry 可能是无效的极小值，跳过，主窗口按默认尺寸显示。
    if geometry and win_state != "iconic":
        root.geometry(geometry)
    root.deiconify()
    root.update()

    if win_state == "zoomed":
        try:
            root.state("zoomed")
        except Exception:
            pass

    # 窗口真正映射后 DWM 句柄才有效，重新应用标题栏主题与图标。
    if manager is not None:
        try:
            manager._apply_titlebar_theme()
        except Exception:
            pass
        try:
            manager._apply_app_icon()
        except Exception:
            pass

    root.lift()
    root.update()

    if splash_conn is not None:
        try:
            splash_conn.send(None)
        except Exception:
            pass
        try:
            splash_proc.join(timeout=1.5)
        except Exception:
            pass
        try:
            splash_conn.close()
        except Exception:
            pass
    if fallback_loading is not None:
        try:
            fallback_loading.destroy()
        except Exception:
            pass

def run_mini(boot):
    """启动挂件模式界面。

        ``boot`` 是 ``app_shell.bootstrap()`` 的结果。窗口关闭后返回下一次要切换
        的界面模式，无切换请求则返回 None（由外壳决定重建还是结束进程）。

        窗口创建与整个事件循环都包在 :func:`ui.mini.dpi.virtualized_dpi` 里：独立
        挂件版本就是 DPI 非感知进程，被 CTk 设成感知之后再切过来的必须显式切回线程
        非感知，否则同一份像素尺寸只剩一半大（详见 ``ui.mini.dpi``）。
        """
    from core import appearance
    from ui.mini import dpi
    from app.shell import take_request

    # PyInstaller 打包 + multiprocessing 子进程必需。
    multiprocessing.freeze_support()

    # 从命令行、快捷方式或打包后的 exe 启动时都先把工作目录锚定到数据目录父级。
    ensure_cwd()

    settings = boot["settings"]
    settings_repo = boot["settings_repo"]
    world_manager = boot["world_manager"]
    world_state = boot["world_state"]

    # 挂件版的额外默认值：暗色像素主题、常驻置顶、自动用身材预览图当头像
    # （挂件不提供立绘上传），以及报告自动归档（界面已没有保存键）。
    # 只在用户没显式设过时补齐，不覆盖专业模式下改过的设置。
    mini_defaults = {
        "theme_mode": "Dark",
        "always_on_top": True,
        "use_preview_image_as_avatar": True,
        "auto_save_report": True,
    }

    for key, value in mini_defaults.items():
        if key not in settings:
            settings[key] = value

    # 主题必须在创建任何控件之前确定：纯 tkinter 的配色在控件创建时落地，
    # 首帧之后再翻转会整窗重刷一次，看起来像闪动。
    appearance.set_mode(settings.get("theme_mode", "Light"))

    def _build_repos(world_state):
        return {
            "landmark_repo": LandmarkRepo(world_state=world_state),
            "preset_repo": PresetRepo(world_state=world_state),
            "personality_repo": PersonalityRepo(world_state=world_state),
            "quip_repo": QuipRepo(world_state=world_state),
            "scenario_repo": ScenarioRepo(world_state=world_state),
            "character_repo": CharacterRepo(),
        }

    repos = _build_repos(world_state)
    context = ExplorationContext(
        settings=settings,
        landmark_repo=repos["landmark_repo"],
        quip_repo=repos["quip_repo"],
        preset_repo=repos["preset_repo"],
        personality_repo=repos["personality_repo"],
        character_repo=repos["character_repo"],
        settings_repo=settings_repo,
        scenario_repo=repos["scenario_repo"],
        world_state=world_state,
    )

    # 窗口必须在「线程非感知」的状态下创建：DPI 感知是**建窗口时**定下的，
    # 之后再切线程也改不了已存在的窗口。见 ui.mini.dpi 的说明。
    with dpi.virtualized_dpi():
        root = tk.Tk()

        def _switch_ui(mode, save=None):
            """把界面切换交给外壳：本层（app）是 ui 与外壳之间唯一的接线处。

            挂件标题栏的「⇄」经注入回调走到这里——ui 层因此不必 import
            ``app_shell``（见 ``ui.mini.app.MiniApp`` 的 ``switch_ui`` 参数）。
            """
            from app.shell import switch_to
            switch_to(root, mode, save=save)

        from ui.mini.app import MiniApp
        MiniApp(root, context, world_manager, settings_repo, switch_ui=_switch_ui)
        root.mainloop()

    # 窗口销毁后 mainloop 返回：取一次切换请求交给外壳处理。
    return take_request()


def run_professional(boot):
    """启动专业模式界面。

    ``boot`` 是 ``app_shell.bootstrap()`` 的结果（设置 / 世界包 / 仓库宿主）。
    窗口关闭后返回下一次要切换的界面模式，无切换请求则返回 None——由
    ``app_shell.run_app`` 决定是重建另一套界面还是结束进程。
    """
    # PyInstaller 打包 + multiprocessing 子进程必需。
    multiprocessing.freeze_support()

    # 工作目录已由 app_shell 锚定；直接运行本模块时补一次，确保从命令行、
    # Finder 双击或打包后的 .app / exe 启动时 data/、assets/ 都能解析。
    ensure_cwd()

    settings = boot["settings"]
    settings_repo = boot["settings_repo"]
    world_manager = boot["world_manager"]

    theme_mode = settings.get("theme_mode", "Light")
    color_theme = settings.get("color_theme", "blue")
    ctk.set_appearance_mode(theme_mode)
    ctk.set_default_color_theme(color_theme)

    # 界面配色必须在任何控件创建前切换：UI 模块在 import 期就把 token 绑成了
    # 模块级常量，apply_palette() 会就地改写这些对象并同步各模块的同名全局量，
    # 因此这里切换后后续构建的控件直接用新配色。
    palette = settings.get("theme_palette", DEFAULT_PALETTE)
    try:
        apply_palette(palette)
    except Exception as e:
        print(f"[Warning] 应用配色 '{palette}' 失败，回退默认配色: {e}")
        palette = DEFAULT_PALETTE

    # 真实主窗口先创建但保持隐藏：初始化期间不参与拖动/缩放等窗口事件，界面在“后台”逐步构建，完成后原位替换。
    root = ctk.CTk()
    root.title("巨大娘生成器")
    root.withdraw()

    # 原生控件（ttk.Treeview / tk.Listbox / canvas 文字 / Text tag 字体）的
    # 磅→像素换算必须与 CTk 的缩放一致，否则同字号在两套链路里大小不同。
    ui_fonts.align_native_scaling(root)

    # 启动屏跑在独立子进程里，拥有自己的 Tk 事件循环——主进程无论怎么同步
    # 阻塞构建界面，都不影响它的拖动/放大流畅度。失败时回退到进程内窗口。
    splash_conn, splash_proc = _launch_splash(theme_mode, color_theme, palette)
    fallback_loading = None
    abort = {"requested": False}

    def request_abort():
        abort["requested"] = True

    if splash_conn is None:
        fallback_loading = LoadingWindow(
            title="「正在初始化」", on_close=request_abort)

    state = {}
    state["world_manager"] = world_manager
    state["world_state"] = world_manager.world_state
    failed = {"value": False}

    def _format_traceback(exc):
        return "".join(traceback.format_exception(
            type(exc), exc, exc.__traceback__))

    def _cleanup_startup():
        tasks.clear()
        if splash_conn is not None:
            try:
                splash_conn.send(None)
            except Exception:
                pass
            try:
                splash_proc.terminate()
                splash_proc.join(timeout=1.5)
            except Exception:
                pass
            try:
                splash_conn.close()
            except Exception:
                pass
        if fallback_loading is not None:
            try:
                fallback_loading.destroy()
            except Exception:
                pass

    def _abort_startup():
        _cleanup_startup()
        try:
            root.destroy()
        except Exception:
            pass
        try:
            root.quit()
        except Exception:
            pass
        sys.exit(1 if failed["value"] else 0)

    def _poll_cancel():
        if abort["requested"]:
            _abort_startup()
            return
        if splash_conn is not None:
            try:
                while splash_conn.poll(0):
                    item = splash_conn.recv()
                    if item is not None and item[0] == "close_requested":
                        abort["requested"] = True
                        _abort_startup()
                        return
            except (EOFError, OSError):
                pass
            except Exception:
                pass
        root.after(50, _poll_cancel)

    def report(value, detail):
        if splash_conn is not None:
            try:
                splash_conn.send(("progress", value, detail))
            except Exception:
                pass
        elif fallback_loading is not None:
            fallback_loading.update_progress(value, detail)

    def report_error(message):
        """初始化失败时把完整错误信息送到加载窗口展示，避免其停滞在进度条上。"""
        print(f"[Error] 初始化失败：\n{message}", file=sys.stderr)
        if splash_conn is not None:
            try:
                splash_conn.send(("error", message))
            except Exception:
                pass
        elif fallback_loading is not None:
            try:
                fallback_loading.show_error(message)
            except Exception:
                pass
        root.update()

    # 初始化的各阶段被拆成小任务，通过 after() 逐个交给事件循环执行，
    tasks = []

    def enqueue(value, detail, fn):
        tasks.append((value, detail, fn))

    enqueue(0.10, "加载资源...", lambda: _load_repos(state))
    enqueue(0.20, "构建探索上下文...",
            lambda: _build_context(state, settings, settings_repo))
    enqueue(0.30, "构建界面框架...", lambda: _build_manager(root, state, report))

    for value, detail, page_key in (
        (0.40, "构建生成器页面...", "generator"),
        (0.50, "构建文本管理页面...", "text_mgmt"),
        (0.60, "构建副本页面...", "dungeon"),
        (0.70, "构建挑战页面...", "challenge"),
    ):
        enqueue(value, detail, lambda key=page_key: _build_tab(state, key))

    enqueue(0.90, "构建设置页面...", lambda: _build_settings(state))
    enqueue(1.00, "初始化完成！", lambda: None)

    def run_next():
        if abort["requested"]:
            _abort_startup()
            return
        if not tasks:
            # 稍作停留让“初始化完成”提示可见，再原位替换为真实主窗口。
            root.after(120, lambda: _handoff_to_main(
                root, splash_conn, splash_proc, fallback_loading, state))
            return
        value, detail, fn = tasks.pop(0)
        try:
            fn()
        except Exception as e:
            failed["value"] = True
            report_error(_format_traceback(e))
            return
        report(value, detail)
        # 同步处理一次事件，维持主进程事件循环（真实主窗口隐藏，无可见负担）。
        root.update()
        root.after(0, run_next)

    # 映射加载窗口（回退场景）或确保子进程启动屏立即可见后开始任务。
    root.update()
    root.after(0, _poll_cancel)
    root.after(0, run_next)
    root.mainloop()

    # 窗口销毁后 mainloop 返回：取一次切换请求交给外壳处理。
    from app.shell import take_request
    return take_request()


def main():
    """入口：跟随「启动界面模式」设置启动
    （见 ``services.ui_mode.resolve_startup_mode``）。
    """
    if "--mini-game-host" in sys.argv:
        # 打包模式：内置小游戏子进程经应用可执行文件路由到这里
        # （源码运行直接跑 ui/common/mini_game_host.py，不走本分支）
        from ui.common.mini_game_host import run as run_mini_game_host
        run_mini_game_host(sys.argv)
        return
    from app.shell import run_app
    run_app()


if __name__ == "__main__":
    main()
