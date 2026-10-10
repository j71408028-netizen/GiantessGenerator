"""应用外壳：在同一进程内切换「专业模式」与「挂件模式」两套界面。

两套界面的 Tk 根窗口类型不同（专业：``customtkinter.CTk``；挂件：``tkinter.Tk``），
配色机制、字体策略、缩放处理也各自独立。因此切换不复用任何控件，而是
**销毁旧根窗口、重建新根窗口**——控件、绑定、``after`` 回调随解释器一起消失，
不会留下跨模式的脏状态。

切换对使用者是「关掉一个窗口、打开另一个窗口」，但进程不重启：数据目录、
世界包激活状态、设置都留在内存里，重建时重新读取一遍即可。

留存下来的只有少数**模块级全局**——它们不随解释器销毁，必须由本模块清理：

- ``customtkinter`` 的 ``ScalingTracker``：按窗口登记 DPI 回调，旧根销毁后
  字典里仍留着失效窗口，DPI 轮询会持续抛 ``invalid command name``；
- 旧解释器排队的 ``after`` 定时器与待派发事件：Tcl 的 notifier 按线程共享，
  它们会在**新界面**的事件循环里到期/派发，而那时命令已随旧解释器删除，
  控制台里会出现 ``invalid command name``——见 :func:`_quiesce_root`；
- ``ui.mini.pixel`` 的模块级缓存：主题绑定表（指着已销毁控件）与字体度量缓存
  （``tkinter.font.Font`` 绑在具体解释器上，跨根复用会报
  ``application has been destroyed``）。

副本会话自 2026-10 起运行在**独立子进程**里（``ui.common.dungeon_spawner``），
父进程不再创建 DPG 上下文、不再 import dearpygui——曾长在「Tk 与 DPG 同进程
共存」上的整套补救机器（保活视口 ``park_context`` 的兜底修复、根窗口退役等）
随会话一起搬走，本模块不再过问。

用法：

    from app.shell import run_app
    run_app()                        # 按「启动界面模式」设置启动
                                     #（缺省「退出时模式」：跟随上次退出时所在界面）
"""

import os
import sys

from services.ui_mode import MODE_MINI, MODE_PRO, resolve_startup_mode, save_mode

# 注：界面模式的取值（``MODE_PRO`` / ``MODE_MINI`` / ``STARTUP_EXIT``）与「启动界面
# 模式」偏好的读写（含 ``UI_MODE_KEY`` / ``STARTUP_MODE_KEY``）住在
# ``services/ui_mode.py``——那是两套界面与外壳共用的词表，ui 层要能**直接读写**它而
# 不必 import 本模块（设置页曾为此踩过 ``ui -> app`` 越界，阶段 3.2.2 已改）。
# 本模块只保留**切换机制**：销毁根窗口、登记待处理请求、清理跨根全局状态。

# 待处理的切换请求。两套界面都通过 switch_to() 写入，由 run_app 的循环读取。
_PENDING = {"mode": None}

#: 一次清场最多派发多少个事件（防某个 <Configure> 处理器无限自续）
_MAX_EVENT_DRAIN = 2000


def request_switch(mode: str) -> None:
    """登记一次切换请求（不立即生效，等当前界面的事件循环退出后）。"""
    _PENDING["mode"] = mode


def take_request():
    """取出并清空待处理的切换请求；无请求返回 None。"""
    mode = _PENDING["mode"]
    _PENDING["mode"] = None
    return mode


# ==================== 引导 ====================

def bootstrap():
    """读出两套界面共用的启动状态：设置、世界包、世界状态。

    每次切换都会重跑一遍——另一套界面可能改过设置或激活了别的世界包，
    重建前重新读取才能保证新界面拿到的是最新数据。
    """
    from paths import ensure_cwd
    from persistence import SettingsRepo
    from services.worlds import WorldManager

    ensure_cwd()

    settings_repo = SettingsRepo()
    settings = settings_repo.load()

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

    return {
        "settings": settings,
        "settings_repo": settings_repo,
        "world_manager": world_manager,
        "world_state": world_manager.world_state,
    }


def _build_repos(world_state):
    """按世界状态构造各仓库（两套界面用的是同一组仓库类）。"""
    from persistence import (
        CharacterRepo, LandmarkRepo, PersonalityRepo, PresetRepo,
        QuipRepo, ScenarioRepo,
    )
    return {
        "landmark_repo": LandmarkRepo(world_state=world_state),
        "preset_repo": PresetRepo(world_state=world_state),
        "personality_repo": PersonalityRepo(world_state=world_state),
        "quip_repo": QuipRepo(world_state=world_state),
        "scenario_repo": ScenarioRepo(world_state=world_state),
        "character_repo": CharacterRepo(),
    }


# ==================== 切换 ====================

def switch_to(root, mode: str, *, save=None) -> None:
    """结束当前界面并请求切换到 ``mode``。

    步骤：先落盘（可选）→ 登记请求 → 摘掉关闭协议 → 摘掉 CTk 缩放登记
    → 清掉旧解释器的残留定时器与待派发事件 → 销毁根窗口。
    销毁根窗口会让 ``mainloop()`` 返回，``run_app`` 的循环接着启动新界面。

    摘掉 ``WM_DELETE_WINDOW`` 是必需的：两套界面的关闭协议都以
    ``os._exit(0)`` 收尾，销毁窗口会触发它，清理代码就再也跑不到。
    """
    if save is not None:
        try:
            save()
        except Exception as e:
            print(f"[Warning] 切换前保存失败: {e}")
    save_mode(mode)
    request_switch(mode)
    try:
        root.protocol("WM_DELETE_WINDOW", lambda: None)
    except Exception:
        pass
    _detach_ctk_window(root)
    # 销毁前把旧解释器的残留清干净（见 _quiesce_root）：不清就会在**新界面**的
    # 事件循环里刷一串 invalid command name。
    _quiesce_root(root)
    _silence_teardown_noise(root)
    try:
        root.destroy()
    except Exception:
        pass
    # 注 1：销毁后才「取消排队的 after」是没用的——那时命令已经删了，取消不取消
    # 都不会再报错；要清就得在销毁之前清，见 _quiesce_root。
    # 注 2：**不要**在这里碰 DPG——副本会话已搬进独立子进程
    # （ui.common.dungeon_spawner），父进程从不创建 DPG 上下文；对从未建
    # 上下文的 dearpygui 调用函数会直接段错误，不是异常。


# ==================== 跑过副本之后的根窗口修复 ====================
# （已随进程隔离删除：副本会话在独立子进程里跑（ui.common.dungeon_spawner），
# 父进程不再创建 DPG 上下文，GLFW 的生命周期不再影响 Tk 根窗口。曾经的
# 「保活视口 park_context + 兜底重建 + 根窗口退役」三件套见 git 历史。）


def _release_global_state() -> None:
    """清掉不随 Tk 解释器销毁的模块级全局状态（目前只有挂件层的缓存）。"""
    _flush_mini_theme_bindings()


def _quiesce_root(root) -> None:
    """销毁前，把「还会在下一套界面的事件循环里醒过来」的残留清掉。

    Tcl 的 notifier 是**按线程**共享的：旧解释器排队的 ``after`` 定时器不随根窗口
    销毁而消失，新根的 ``mainloop`` 一跑起来它们照常到期；而这时 tkinter 注册的
    那批 Tcl 命令名已经被 ``Misc.destroy`` 删掉了，于是往 stderr 刷一串

        invalid command name "2233413778176update"
            ("after" script)

    已经排进事件队列、还没来得及派发的绑定事件同理——等命令删掉之后才被派发：

        invalid command name "2233390865216<lambda>"
            (command bound to event)

    两者都是使用者在控制台里能看到的东西（打包版是 pythonw，没有控制台，但开着
    控制台调试时很扎眼）。所以在销毁之前做两件事：**先**把队列里的事件派发干净
    （此时控件都还活着），**再**撤掉排队的定时器。两步各走一轮，是因为派发事件
    可能又排上新的定时器。
    """
    for _ in range(2):
        _drain_event_queue(root)
        _cancel_pending_afters(root)


def _drain_event_queue(root) -> int:
    """不阻塞地把本线程事件队列里剩下的东西跑完，返回处理条数。

    ``dooneevent`` 处理的是**整个线程**的事件队列，所以旧解释器里排队的
    ``<Configure>`` 也会在这里交给还活着的控件处理掉，而不是留给销毁之后。
    """
    try:
        import _tkinter
    except ImportError:      # pragma: no cover - 理论上不会发生
        return 0
    flags = _tkinter.ALL_EVENTS | _tkinter.DONT_WAIT
    processed = 0
    while processed < _MAX_EVENT_DRAIN:
        try:
            if not root.tk.dooneevent(flags):
                break
        except Exception:
            break
        processed += 1
    return processed


def _cancel_pending_afters(root) -> int:
    """撤掉本解释器上排队的 ``after`` 定时器，返回条数。

    只撤定时器，不碰别的：``after info`` 列出的就是 ``after ms script`` 建的那些，
    取消它们不会影响 mainloop 的退出（后者看的是主窗口数，与定时器无关）。
    """
    try:
        names = root.tk.call('after', 'info')
    except Exception:
        return 0
    cancelled = 0
    for name in names:
        try:
            root.tk.call('after', 'cancel', name)
            cancelled += 1
        except Exception:
            pass
    return cancelled


def _silence_teardown_noise(root) -> None:
    """把「命令已随解释器删除、事件却刚到」这一种 Tk 后台错误压掉。

    :func:`_quiesce_root` 已经把能清的都清了，但 Tk 在销毁窗口的过程中自己还会
    派发一批排队事件，时机上做不到一个不剩。这一类错误只有一种形态——
    ``invalid command name "..."``（事件绑定与 ``after`` 脚本各占一半）——
    这里只过滤这一种，其余后台错误照旧打印。此刻这个解释器马上就要整个销毁，
    压掉它的后台报错不会掩盖任何有用的信息（该解释器此后不再运行任何代码）。
    """
    try:
        root.tk.eval(
            "proc bgerror {msg} {\n"
            "    if {[string match {invalid command name*} $msg]} { return }\n"
            "    catch {puts stderr $msg}\n"
            "}\n")
    except Exception:
        pass


def _detach_ctk_window(root) -> None:
    """销毁前把窗口从 CTk 的 DPI 轮询表里摘掉。

    CTk 的 ``check_dpi_scaling`` 按 ``window_widgets_dict`` 里的窗口续排
    ``after``；窗口销毁后条目若仍在，每轮都会对死窗口调用 ``after``，持续抛
    ``invalid command name``。**在销毁之前**摘除是关键：那时窗口还活着，直接
    删字典条目即可，不必调用 ``winfo_exists()``——对已销毁的 Tcl 解释器调用
    它有段错误风险，那不是异常、拦不住。
    """
    if "customtkinter" not in sys.modules:
        return
    try:
        from customtkinter.windows.widgets.scaling.scaling_tracker import (
            ScalingTracker)
        ScalingTracker.window_widgets_dict.pop(root, None)
        ScalingTracker.window_dpi_scaling_dict.pop(root, None)
    except Exception as e:
        print(f"[Warning] 摘除 CTk 缩放回调失败: {e}")


def _flush_mini_theme_bindings() -> None:
    """清空挂件层里绑在**已销毁解释器**上的模块级缓存。

    两处都按「模块未被导入就什么都不做」处理：

    - ``_bindings``：主题重刷回调表，指着已销毁的控件；
    - ``_font_cache``：字体度量用的 ``tkinter.font.Font``。它绑在创建它的那个
      解释器上，跨根复用会 ``application has been destroyed``——不清理的话，
      「专业 → 挂件」切回去时挂件界面根本建不起来（实测：建到第一个按钮就抛）。

    字体家族名（``_family_cache``）只是字符串，跨解释器仍然有效，不必清。
    """
    pixel = sys.modules.get("ui.mini.pixel")
    if pixel is None:
        return
    try:
        pixel._bindings.clear()
        pixel._font_cache.clear()
    except Exception as e:
        print(f"[Warning] 清理挂件模块级缓存失败: {e}")


# ==================== 调度循环 ====================

#: 卡死转储文件的句柄（必须保持强引用，否则 faulthandler 写向已回收的文件）
_HANG_DUMP_FILE = None
#: 环境变量缺省值：多少秒没有进展就转储一次线程栈
_HANG_DUMP_DEFAULT_SECONDS = 60.0


def _install_hang_watchdog() -> bool:
    """按环境变量开启 faulthandler 定时转储：卡死时留下那一刻的线程栈。

    本项目真出过「代码里复现不了、只在长时会话里偶发」的挂死，结论是
    OS/驱动级环境现象（见 ``docs/Dungeon/history/exit_hang_investigation.md``）。
    这类问题**唯一有价值的证据就是卡住那一刻的线程栈**，而它平时拿不到
    （DPG 帧循环期间 Python 层看门狗常常拿不到 GIL，``threading.Timer``
    实测根本不触发）。所以这里挂一个可选的原生转储：

    - ``GIANTESS_HANG_DUMP=30``：30 秒无进展即把**全部线程栈**写进
      数据区 ``user/hang_dump.txt``（默认关闭，避免日常使用被反复写文件）；
    - ``GIANTESS_HANG_DUMP_REPEAT=1``：反复转储（默认只转储一次）；
    - ``GIANTESS_HANG_DUMP_EXIT=1``：转储后直接结束进程（默认继续运行，
      便于继续操作/观察）。

    返回是否真的开启了。注意它是**尽力而为**：转储依赖 faulthandler 的
    看门狗线程拿到 GIL，主线程死锁在持有 GIL 的 C 调用里时可能拿不到。
    """
    global _HANG_DUMP_FILE
    raw = (os.environ.get("GIANTESS_HANG_DUMP") or "").strip()
    if not raw:
        return False
    try:
        seconds = max(5.0, float(raw))
    except ValueError:
        seconds = _HANG_DUMP_DEFAULT_SECONDS
    try:
        import faulthandler
        import time

        from paths import data_dir

        path = os.path.join(data_dir(), "user", "hang_dump.txt")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _HANG_DUMP_FILE = open(path, "a", buffering=1, encoding="utf-8")
        _HANG_DUMP_FILE.write(
            f"\n===== 会话开始 {time.strftime('%Y-%m-%d %H:%M:%S')} "
            f"（{seconds:.0f}s 无进展即转储）=====\n")
        faulthandler.enable(file=_HANG_DUMP_FILE)
        faulthandler.dump_traceback_later(
            seconds,
            repeat=bool(os.environ.get("GIANTESS_HANG_DUMP_REPEAT")),
            file=_HANG_DUMP_FILE,
            exit=bool(os.environ.get("GIANTESS_HANG_DUMP_EXIT")))
    except Exception as e:
        print(f"[Warning] 开启卡死转储失败: {e}")
        return False
    print(f"[Hang] 卡死转储已开启：{seconds:.0f}s 无进展写入 {path}")
    return True


def run_app(initial_mode: str = None, default_mode: str = MODE_PRO) -> None:
    """按界面模式启动，并在两套界面之间来回切换，直到使用者关闭窗口。

    ``initial_mode`` 非空时**固定**首次启动用它。为空时按设置里的启动
    偏好解析（见 :func:`services.ui_mode.resolve_startup_mode`），即跟随设置项的
    ``main.py``。每套界面返回后读取一次切换请求：有就重建另一套，没有就结束进程。
    """
    _install_hang_watchdog()
    mode = initial_mode or resolve_startup_mode(default_mode)

    while True:
        boot = bootstrap()
        if mode == MODE_MINI:
            from main import run_mini
            request = run_mini(boot)
        else:
            from main import run_professional
            request = run_professional(boot)

        # 界面正常关闭：没有切换请求，进程结束。
        if request not in (MODE_PRO, MODE_MINI) or request == mode:
            return

        # 销毁旧根窗口后，模块级全局不会自己消失，这里统一清一遍再重建。
        _release_global_state()
        mode = request
