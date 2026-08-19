import multiprocessing
import sys
import traceback

import customtkinter as ctk

from paths import ensure_cwd
import ui.common.ctk_patch  # noqa: F401  模式切换时同步刷新 CTk 控件 Frame 底色，避免几何重排露旧色
from context import ExplorationContext
from main_window_manager import MainWindowManager
from persistence import SettingsRepo, LandmarkRepo, PresetRepo, PersonalityRepo
from persistence import QuipRepo, DungeonRepo, CharacterRepo
from services.world_service import WorldManager
from ui.common.loading import LoadingWindow
from ui.common.splash import splash_process


def _load_repos(state):
    world_state = state.get("world_state")
    state["landmark_repo"] = LandmarkRepo(world_state=world_state)
    state["preset_repo"] = PresetRepo(world_state=world_state)
    state["personality_repo"] = PersonalityRepo(world_state=world_state)
    state["quip_repo"] = QuipRepo(world_state=world_state)
    state["dungeon_repo"] = DungeonRepo(world_state=world_state)
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
        dungeon_repo=state["dungeon_repo"],
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


def _launch_splash(theme_mode, color_theme):
    """启动独立进程启动屏，返回 (Pipe 连接, Process)；失败时返回 (None, None)。"""
    parent_conn = child_conn = None
    try:
        parent_conn, child_conn = multiprocessing.Pipe()
        proc = multiprocessing.Process(
            target=splash_process,
            args=(child_conn, theme_mode, color_theme, "「正在初始化」"),
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


def main():
    # PyInstaller 打包 + multiprocessing 子进程必需。
    multiprocessing.freeze_support()

    # 无论从命令行、Finder 双击还是打包后的 .app / exe 启动，先把工作目录
    # 锚定到数据目录的父级，确保 data/、assets/ 等相对路径稳定可解析（macOS
    # 下从 Finder 启动时 CWD 为 “/”，不修正会找不到存档与素材）。
    ensure_cwd()


    # 先读取设置并应用主题，再创建窗口，避免暗色模式下初始化页先以亮色显示、随后再翻转，产生明显的亮暗闪动。
    settings_repo = SettingsRepo()
    settings = settings_repo.load()

    # 恢复持久化的世界包激活状态，并把包设置叠加到内存 settings。
    world_manager = WorldManager(data_dir="data")
    active_id = settings.get("active_world")
    if active_id:
        try:
            world_manager.load_active(active_id)
        except ValueError as e:
            print(f"[Warning] 世界包 '{active_id}' 无法加载，已忽略: {e}")
            settings.pop("active_world", None)
    world_manager.apply_world_settings(settings)
    settings_repo.world_state = world_manager.world_state

    theme_mode = settings.get("theme_mode", "Light")
    color_theme = settings.get("color_theme", "blue")
    ctk.set_appearance_mode(theme_mode)
    ctk.set_default_color_theme(color_theme)

    # 真实主窗口先创建但保持隐藏：初始化期间不参与拖动/缩放等窗口事件，界面在“后台”逐步构建，完成后原位替换。
    root = ctk.CTk()
    root.title("巨大娘生成器")
    root.withdraw()

    # 启动屏跑在独立子进程里，拥有自己的 Tk 事件循环——主进程无论怎么同步
    # 阻塞构建界面，都不影响它的拖动/放大流畅度。失败时回退到进程内窗口。
    splash_conn, splash_proc = _launch_splash(theme_mode, color_theme)
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


if __name__ == "__main__":
    main()
