"""界面热切换自检：专业模式 ⇄ 挂件模式来回切，并覆盖「跑过副本之后再切换」。

验证的是「同进程内销毁旧 Tk 根窗口、重建另一套界面」这条路径——两套界面的
根窗口类型不同（``customtkinter.CTk`` / ``tkinter.Tk``），配色、字体、缩放机制
各自独立，切换不能复用任何控件，只能销毁重建。桩件验不到这些，所以这里
**真实构建两套界面**，并用 ``after`` 定时触发切换按钮的回调，走完整的
``mainloop → 销毁 → 重建`` 流程。

五轮：
  1. 专业模式 → 切到挂件（没跑过副本的常规路径）；
  2. 挂件模式 → **真实跑一局副本**（收尾会 ``dpg.destroy_context()``）→ 切回专业；
  3. 二次构建专业模式；
  4. 再切回挂件并二次构建挂件（验证 ``ui.mini.pixel`` 的跨解释器缓存已清）；
  5. 挂件模式 → **再真实跑一局副本** → 再切回专业。

第 2 轮是**回归保护的要点**：``destroy_context()`` 会终止 GLFW，此后 Tk 根窗口
失去销毁/隐藏能力，而热切换的收尾恰好是 ``root.destroy()``——没有副本侧那道
保活视口（``dungeon.window.dpg_state.park_context``）时，这一轮的 ``mainloop``
会以 0xC000041D 静默杀死进程。第 4 轮保护的是另一条：挂件层的字体度量缓存
（``tkinter.font.Font``）绑在解释器上，不清理的话切回挂件时界面建不起来。
第 5 轮保护「保活视口 → 拆保活视口（下一局开头）→ 再保活」这条往返：DPG 的
上下文与视口都是进程单例，两局之间必须一拆一建，拆错了同样会硬崩。

每轮检查：
  - 界面确实建起来了，控件可布局、可刷新；
  - 切换请求被正确登记，且模式已落盘；
  - **实时切换入口是导航栏底部的「切换界面」按钮**（点它先弹确认框，这里把
    弹框换成记录型再驱动按钮回调）；设置页的「启动界面模式」只写设置、
    **不**销毁窗口、**不**登记切换请求；
  - 挂件建窗时本线程是 **DPI 非感知**的（``ui.mini.dpi``）：挂件那套像素尺寸
    是按 96 DPI 写死的，靠系统的位图缩放才与独立挂件版一致；一旦被 CTk 设成
    感知之后没切回来，同一个窗口就只剩一半大；
  - 跑过副本之后保活视口确实补上了（热切换不需要走兜底修复）；
  - 销毁后模块级全局（CTk 的 DPI 轮询表、挂件主题绑定表与字体缓存）没有残留；
  - 挂件在二次建起后仍能正常调查（功能性验证，不只是建窗）。

会写 ``data/user/settings.json`` 与副本产物（未完成回放 / 报告），运行结束自动还原：
设置备份到系统临时区后拷回，副本产物搬到临时区，不做删除。任一步骤失败即以非零码退出。

用法：python tests/smoke_switch.py
"""

import contextlib
import os
import shutil
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from paths import data_dir, ensure_cwd

ensure_cwd()

_FAILURES = []
_CHECKS = [0]


def check(name, ok, detail=""):
    _CHECKS[0] += 1
    if ok:
        print(f"  PASS  {name}" + (f"  （{detail}）" if detail else ""))
    else:
        print(f"  FAIL  {name}" + (f"  （{detail}）" if detail else ""))
        _FAILURES.append(name)


def _discard(path):
    """把文件移出 data/ 到系统临时区（本项目禁止在 data/ 下删除）。"""
    try:
        dest = os.path.join(tempfile.mkdtemp(prefix="switch_trash_"),
                            os.path.basename(path))
        shutil.move(path, dest)
        return dest
    except OSError:
        return None


# ==================== 备份与还原 ====================

def _backup_user_files():
    user_dir = os.path.join("data", "user")
    backup = tempfile.mkdtemp(prefix="switch_backup_")
    for name in os.listdir(user_dir):
        src = os.path.join(user_dir, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(backup, name))
    return user_dir, backup


def _restore_user_files(user_dir, backup):
    for name in os.listdir(backup):
        shutil.copy2(os.path.join(backup, name), os.path.join(user_dir, name))
    shutil.rmtree(backup, ignore_errors=True)
    print("（设置已还原）")


# ==================== 两套界面的构建 ====================

def _build_context(boot):
    from services.exploration.context import ExplorationContext
    repos = __import__("app_shell")._build_repos(boot["world_state"])
    return ExplorationContext(
        settings=boot["settings"],
        landmark_repo=repos["landmark_repo"],
        quip_repo=repos["quip_repo"],
        preset_repo=repos["preset_repo"],
        personality_repo=repos["personality_repo"],
        character_repo=repos["character_repo"],
        settings_repo=boot["settings_repo"],
        scenario_repo=repos["scenario_repo"],
        world_state=boot["world_state"],
    )


def build_professional(boot):
    """构建专业模式界面（简化掉启动屏与分步任务，其余与真实启动一致）。"""
    import customtkinter as ctk
    from ui.common import fonts as ui_fonts
    from ui.common.theme import DEFAULT_PALETTE, apply_palette
    from main_window_manager import MainWindowManager

    settings = boot["settings"]
    ctk.set_appearance_mode(settings.get("theme_mode", "Light"))
    ctk.set_default_color_theme(settings.get("color_theme", "blue"))
    try:
        apply_palette(settings.get("theme_palette", DEFAULT_PALETTE))
    except Exception as e:
        print(f"[Warning] 应用配色失败: {e}")

    root = ctk.CTk()
    root.title("巨大娘生成器")
    root.withdraw()
    ui_fonts.align_native_scaling(root)

    manager = MainWindowManager(root, _build_context(boot),
                                world_manager=boot["world_manager"])
    manager.create_generator_tab(manager.pages["generator"])
    root.update_idletasks()
    return root, manager


def build_mini(boot):
    """构建挂件模式界面。

    ``switch_ui`` 按 ``main.run_mini`` 的接线注入——挂件标题栏的「⇄」经这个回调
    走到外壳（ui 层不直接 import ``app_shell``）。自检必须照着接线，否则第 2 轮
    「跑过副本再切回专业」会走到界面里的「未接线」分支而切不动。
    """
    import tkinter as tk
    from core import appearance
    from ui.mini.app import MiniApp

    settings = boot["settings"]
    for key, value in (("theme_mode", "Dark"), ("always_on_top", True),
                       ("use_preview_image_as_avatar", True),
                       ("auto_save_report", True)):
        settings.setdefault(key, value)
    appearance.set_mode(settings.get("theme_mode", "Light"))

    root = tk.Tk()

    def _switch_ui(mode, save=None):
        from app_shell import switch_to
        switch_to(root, mode, save=save)

    app = MiniApp(root, _build_context(boot), boot["world_manager"],
                  boot["settings_repo"], switch_ui=_switch_ui)
    root.update_idletasks()
    return root, app


@contextlib.contextmanager
def mini_phase(boot):
    """建挂件界面，并让它存续期间本线程保持 DPI 非感知。

    真实入口 ``main.run_mini`` 是把「建根窗口 + mainloop」整个包在
    ``ui.mini.dpi.virtualized_dpi()`` 里的（DPI 感知在建窗口时就定死，之后再切
    线程也改不了），自检必须照做，否则验不到那条路径。
    """
    from ui.mini import dpi
    with dpi.virtualized_dpi():
        root, app = build_mini(boot)
        yield root, app


# ==================== 真实副本会话（复用挂件自检里的桩件与限帧循环） ====================

def _run_real_dungeon(app, root):
    """真实拉起一局副本：AI 指向不可达地址、帧循环限帧、弹框换成记录型。

    ``smoke_mini`` 里已有这套桩件（参数与签名一致性也由它保证），这里直接用，
    免得两处各写一份；它跑完会把新增的副本产物搬出 ``data/``。

    可重复调用（第 2、5 轮各一次）：桩只要装一次，重复装会把真类也替换成桩。
    """
    import smoke_mini as mini_smoke
    from app_shell import _root_needs_repair
    from dungeon.window import dpg_state

    if not mini_smoke._REAL_WINDOW.get("cls"):
        mini_smoke._install_dungeon_stub()
    # 行动点会被上一局扣掉，补满再掷方案，否则这一局进不去
    if getattr(app, "current_state", None) is not None:
        app.current_state.action_points = 99
        app._character_repo.save(app.current_state)
    while not app.investigation.has_scenario:
        app.investigate()
        root.update_idletasks()
        if getattr(app, "current_state", None) is not None:
            app.current_state.action_points = 99
            app._character_repo.save(app.current_state)
    cleanup = [os.path.join(data_dir(), "archives"),
               os.path.join(data_dir(), "user", "replays"),
               os.path.join(data_dir(), "user", "reports")]
    outcome = mini_smoke._real_dungeon_run(app, root, cleanup)
    # 收尾应已补上保活视口：这一步不成立，热切换就只剩兜底修复可走
    outcome["park"] = dpg_state.is_alive()
    outcome["repair_needed"] = _root_needs_repair()
    return outcome


# ==================== 弹框与按钮桩件 ====================

def _stub_ui_dialogs(confirm=True):
    """把专业模式的弹框换成记录型，返回调用记录。

    导航栏「切换界面」会先弹确认框——自检不能真弹（模态窗口会把事件循环堵死），
    所以按记录型替换，顺便可以断言「确实问过」。
    """
    import ui.common.dialogs as dlg
    calls = []

    def askyesno(title, message, *a, **k):
        calls.append(("askyesno", title))
        return confirm

    def showwarning(title, message, *a, **k):
        calls.append(("showwarning", title, message))

    dlg.askyesno = askyesno
    dlg.showwarning = showwarning
    return calls


def _button_text(button):
    """取 CTk 按钮的文本，兼容 cget 不支持 "text" 的版本。"""
    try:
        return str(button.cget("text"))
    except Exception:
        return str(getattr(button, "_text", ""))


# ==================== 全局残留检查 ====================

def _ctk_tracked_windows():
    """CTk DPI 轮询表里挂着的窗口条目数。

    只数条目、不调用 ``winfo_exists()``：对已销毁的 Tcl 解释器调用它是段错误，
    不是异常。销毁前摘除已能保证这里为 0。
    """
    if "customtkinter" not in sys.modules:
        return 0
    try:
        from customtkinter.windows.widgets.scaling.scaling_tracker import (
            ScalingTracker)
        return len(ScalingTracker.window_widgets_dict)
    except Exception:
        return -1


def _is_destroyed(root):
    """窗口是否已销毁。解释器销毁后再调 winfo_exists 会抛 TclError，一并视为已销毁。"""
    try:
        return not root.winfo_exists()
    except Exception:
        return True


def _mini_bindings():
    pixel = sys.modules.get("ui.mini.pixel")
    if pixel is None:
        return 0
    return len(getattr(pixel, "_bindings", []))


def _mini_font_cache():
    pixel = sys.modules.get("ui.mini.pixel")
    if pixel is None:
        return 0
    return len(getattr(pixel, "_font_cache", {}))


def _mini_style(app):
    """地标风格（世界包未激活时 world_id 合法为空，所以看风格而不是 world）。"""
    return getattr(app.investigation, "landmark_style", "")


# ==================== 主流程 ====================

def main():
    import app_shell
    from app_shell import _release_global_state
    from services.ui_mode import MODE_MINI, MODE_PRO, load_mode

    user_dir, backup = _backup_user_files()
    try:
        # ---------- 第 1 轮：专业模式（本进程还没跑过副本） ----------
        print("\n[1] 构建专业模式（第 1 次，未跑过副本）")
        boot = app_shell.bootstrap()
        root, manager = build_professional(boot)
        check("专业界面建起", root.winfo_exists())
        check("生成器页已构建", hasattr(manager, "generator_panel"))

        # 设置页的「启动界面模式」只记录下次启动用哪套界面：写设置，但不重建
        # 窗口、也不登记切换请求（实时切换是导航栏那个按钮的事）。
        try:
            manager.create_settings_panel()
            panel_ok, panel_detail = True, ""
        except Exception as e:
            panel_ok, panel_detail = False, f"{type(e).__name__}: {e}"
        check("设置页可构建", panel_ok, panel_detail)
        if panel_ok:
            manager.settings_panel._on_ui_mode_changed("挂件模式")
            check("设置里选挂件模式会写进设置",
                  load_mode() == MODE_MINI, str(load_mode()))
            check("设置里选挂件模式不销毁窗口（当前仍是专业界面）",
                  bool(root.winfo_exists()))
            check("设置里选挂件模式不登记切换请求",
                  app_shell.take_request() is None)
            manager.settings_panel._on_ui_mode_changed("专业模式")
            check("设置里能改回专业模式",
                  load_mode() == MODE_PRO, str(load_mode()))

        # 实时切换走导航栏底部的「切换界面」按钮：点它先弹确认框，
        # 再用 after 定时触发，走真实的 mainloop → 销毁 → 返回路径。
        dialog_calls = _stub_ui_dialogs(True)
        switch_btn = getattr(manager.nav_bar, "ui_switch_btn", None)
        check("导航栏带切换界面按钮", switch_btn is not None)
        if switch_btn is not None:
            check("切换按钮文案标明是换界面",
                  "切换界面" in _button_text(switch_btn), _button_text(switch_btn))
        root.after(300, manager.nav_bar._on_ui_switch_clicked)
        root.deiconify()
        root.mainloop()
        check("切换后根窗口已销毁", _is_destroyed(root))
        check("切换前弹过确认框",
              any(c[0] == "askyesno" for c in dialog_calls), str(dialog_calls))
        check("切换过程没有多余警告",
              not any(c[0] == "showwarning" for c in dialog_calls))

        request = app_shell.take_request()
        check("切换请求指向挂件模式", request == MODE_MINI, str(request))
        saved = load_mode()
        check("界面模式已落盘", saved == MODE_MINI, str(saved))

        # ---------- 第 2 轮：挂件模式 → 跑一局真实副本 → 切回专业 ----------
        print("\n[2] 切换到挂件模式，并真实跑一局副本后再切换")
        _release_global_state()
        check("CTk 轮询表无残留条目", _ctk_tracked_windows() == 0,
              f"残留 {_ctk_tracked_windows()}")

        boot = app_shell.bootstrap()
        with mini_phase(boot) as (root, app):
            from ui.mini import dpi
            check("挂件界面建起", root.winfo_exists())
            awareness = dpi.current_awareness()
            # None = 非 Windows / 系统没这个 API，此时本项不适用，按通过算
            check("挂件建窗时线程非 DPI 感知（否则窗口只有一半大）",
                  awareness in (0, None), f"感知={awareness}")
            app.auto_answer = True

            # 功能性验证：切换后挂件仍能正常调查
            try:
                app.investigate()
                app.root.update_idletasks()
                check("挂件调查可用", bool(_mini_style(app)),
                      _mini_style(app) or "（未掷出风格）")
            except Exception as e:
                check("挂件调查可用", False, f"{type(e).__name__}: {e}")

            # 跑一局真实副本：收尾会 destroy_context()，之后靠保活视口把 Tk 根维持住
            outcome = _run_real_dungeon(app, root)
            check("副本真实跑通（未抛异常并取回结果）", bool(outcome["ran"]))
            check("副本未被入口校验拦下", not outcome["failed"], outcome["error"])
            check("副本运行期间挂件已隐藏", outcome["hidden"] == "withdrawn",
                  str(outcome["hidden"]))
            check("副本结束后挂件恢复显示", outcome["restored"] == "normal",
                  str(outcome["restored"]))
            check("副本收尾已补上 DPG 保活视口", bool(outcome["park"]))
            check("切换不需要走兜底修复", not outcome["repair_needed"])

            print("    切换（修复逻辑失效时，这一步会静默杀死进程）")
            root.after(300, app.switch_to_professional)
            root.deiconify()
            root.mainloop()
            check("跑过副本后仍能切换：挂件根窗口已销毁", _is_destroyed(root))

        from ui.mini import dpi as mini_dpi
        restored = mini_dpi.current_awareness()
        # None = 非 Windows，本项不适用；Windows 上必须回到「感知」（1 或 2）
        check("挂件退出后线程 DPI 感知已还原（否则专业界面也会被位图缩放）",
              restored in (None, 1, 2), f"感知={restored}")

        request = app_shell.take_request()
        check("切换请求指回专业模式", request == MODE_PRO, str(request))
        check("界面模式已落盘", load_mode() == MODE_PRO)

        # ---------- 第 3 轮：切回专业模式（第 2 次构建） ----------
        print("\n[3] 切回专业模式（第 2 次构建）")
        _release_global_state()
        check("挂件主题绑定表已清空", _mini_bindings() == 0,
              f"残留 {_mini_bindings()}")
        check("挂件字体度量缓存已清空", _mini_font_cache() == 0,
              f"残留 {_mini_font_cache()}")

        boot = app_shell.bootstrap()
        root2, manager2 = build_professional(boot)
        check("专业界面二次建起", root2.winfo_exists())
        check("生成器页二次构建成功", hasattr(manager2, "generator_panel"))
        root2.update_idletasks()
        check("二次界面可刷新", root2.winfo_exists())

        # ---------- 第 4 轮：再切回挂件（保护跨解释器的挂件缓存） ----------
        print("\n[4] 再切回挂件模式（第 2 次构建挂件）")
        dialog_calls = _stub_ui_dialogs(True)
        root2.after(300, manager2.nav_bar._on_ui_switch_clicked)
        root2.deiconify()
        root2.mainloop()
        check("专业根窗口已销毁（第 2 次切换）", _is_destroyed(root2))
        check("二次切换也走导航栏按钮（弹过确认框）",
              any(c[0] == "askyesno" for c in dialog_calls), str(dialog_calls))
        check("切换请求指向挂件模式",
              app_shell.take_request() == MODE_MINI)

        _release_global_state()
        boot = app_shell.bootstrap()
        with mini_phase(boot) as (root3, app3):
            from ui.mini import dpi
            check("挂件界面二次建起（跨解释器缓存已清）", root3.winfo_exists())
            awareness = dpi.current_awareness()
            check("二次建挂件时线程仍非 DPI 感知",
                  awareness in (0, None), f"感知={awareness}")
            app3.auto_answer = True
            try:
                app3.investigate()
                root3.update_idletasks()
                check("挂件二次可用", bool(_mini_style(app3)),
                      _mini_style(app3) or "（未掷出风格）")
            except Exception as e:
                check("挂件二次可用", False, f"{type(e).__name__}: {e}")

            # ---------- 第 5 轮：再跑一局副本（保活视口一拆一建） ----------
            print("\n[5] 挂件模式再跑一局副本后切回专业（保活视口一拆一建）")
            from dungeon.window import dpg_state
            check("上一轮留下的保活视口在场", dpg_state.is_alive(),
                  str(dpg_state.is_alive()))

            outcome = _run_real_dungeon(app3, root3)
            check("第二局副本真实跑通", bool(outcome["ran"]))
            check("第二局未被入口校验拦下", not outcome["failed"], outcome["error"])
            check("第二局后挂件恢复显示", outcome["restored"] == "normal",
                  str(outcome["restored"]))
            check("第二局收尾又补上保活视口", bool(outcome["park"]))
            check("第二局后切换不需要兜底修复", not outcome["repair_needed"])

            root3.after(300, app3.switch_to_professional)
            root3.deiconify()
            root3.mainloop()
            check("第二局后仍能切换：挂件根窗口已销毁", _is_destroyed(root3))
            check("切换请求指回专业模式", app_shell.take_request() == MODE_PRO)

        _release_global_state()
        boot = app_shell.bootstrap()
        root4, manager4 = build_professional(boot)
        check("专业界面三次建起", root4.winfo_exists())
        check("生成器页三次构建成功", hasattr(manager4, "generator_panel"))

        print(f"\n结果：{'全部通过' if not _FAILURES else str(len(_FAILURES)) + ' 项失败'}"
              f"（共 {_CHECKS[0]} 项）")
        code = 1 if _FAILURES else 0
    except Exception:
        traceback.print_exc()
        code = 1
    finally:
        _restore_user_files(user_dir, backup)
        # 还原完成后直接退出，不再碰任何 Tk 调用：销毁根窗口会卡住进程（见下）。
        #
        # 这套界面反复销毁重建过好几个 Tk 根，收尾再 destroy 一个已经反复拆装过的
        # 根窗口时进程会停在 Tcl 内部不返回，try/except 也拦不住。自检关心的断言
        # 与数据还原都在这之前完成了，直接退出的结果与正常退出一致。
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)


if __name__ == "__main__":
    sys.exit(main())
