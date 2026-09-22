"""副本窗口（DearPyGui）自动驾驶自检。

不用人手点击、不联网、不碰真实 ``data/``：用脚本代替玩家操作 DPG 会话窗口，
跑完一个场景后对窗口状态与落盘结果做断言。

用法::

    python scripts/dungeon_autopilot.py                  # 全部场景（默认，每场景一个子进程）
    python scripts/dungeon_autopilot.py --scene session-close
    python scripts/dungeon_autopilot.py --repeat 10      # 连续开关窗口 10 次
    python scripts/dungeon_autopilot.py --in-process     # 当前进程内依次跑（快，但崩溃会带走全部）

场景：

===============  ==========================================================
session-close    挑战模式直进会话 → 步进若干 → 点 X 关闭（走「未完成」收尾）
entry-cancel     探索模式入口页 → 点「返回」（入口退出，不应落盘）
entry-start      探索模式入口页 → 点「开始副本」→ 步进 → 点 X 关闭
===============  ==========================================================

输出：控制台一行 ASCII 结论 + UTF-8 报告文件路径（报告在临时目录，含全部明细）。

与 ``scripts/check_dungeon_finalize.py`` 等无 GUI 脚本的关系：那些脚本覆盖纯逻辑，
本脚本覆盖**只有真窗口才能覆盖的部分**——构造 → 步进 → 关闭的完整生命周期与
DPG 上下文的创建/销毁。属于 GUI 冒烟层，需要显示器，不进无 GUI 的 CI 门禁。
"""

import argparse
import json
import os
import sys
import tempfile
import threading
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_REPORT_DIR = tempfile.mkdtemp(prefix="dungeon_autopilot_")
_REPORT_PATH = os.path.join(_REPORT_DIR, "report.txt")
_log = open(_REPORT_PATH, "w", encoding="utf-8")
sys.stdout = _log
sys.stderr = _log

failures = []
total = 0


def check(name, ok, extra=""):
    global total
    total += 1
    print(("  OK   " if ok else "  FAIL ") + name + (f"  <{extra}>" if extra and not ok else ""))
    if not ok:
        failures.append(name)


# ---------------------------------------------------------------------------
# 隔离：数据目录改到临时目录；对话框换成记录器（真实对话框需要 Tk 根窗口）
# 必须在 import dungeon.window.* 之前打补丁——那些模块用
# ``from paths import data_dir`` 在导入时绑定函数对象。
# ---------------------------------------------------------------------------
import paths  # noqa: E402

_DATA_ROOT = os.path.join(tempfile.mkdtemp(prefix="dungeon_autopilot_data_"), "data")
os.makedirs(os.path.join(_DATA_ROOT, "user"), exist_ok=True)
paths.data_dir = lambda: _DATA_ROOT

import ui.common.dialogs as dialogs  # noqa: E402

dialog_calls = []
dialogs.showinfo = lambda *a, **k: dialog_calls.append(("info", str(a)[:200]))
dialogs.showwarning = lambda *a, **k: dialog_calls.append(("warn", str(a)[:200]))
dialogs.showerror = lambda *a, **k: dialog_calls.append(("error", str(a)[:200]))
dialogs.askyesno = lambda *a, **k: False


# ---------------------------------------------------------------------------
# 假 AI：不联网、不花钱、可确定性复现
# ---------------------------------------------------------------------------
DEFAULT_PARAGRAPHS = [
    "她抬起脚，街道在脚下轻微震颤。远处的人群开始骚动。",
    "有人尖叫着后退，也有人呆立在原地仰头张望。",
    "她低头看了一眼，嘴角浮起一点笑意。",
]


class ScriptedAI:
    """按脚本返回固定 AI 响应，覆盖流式与一次性两种调用。"""

    def __init__(self, paragraphs=None):
        self.paragraphs = list(paragraphs or DEFAULT_PARAGRAPHS)
        self.index = 0
        self.stream_calls = 0
        self.generate_calls = 0

    def _next_text(self):
        text = self.paragraphs[self.index % len(self.paragraphs)]
        self.index += 1
        return text

    def generate_stream(self, messages, temperature=0.8):
        self.stream_calls += 1
        payload = json.dumps(
            {"text": self._next_text(), "direction": 1, "custom_directions": {}},
            ensure_ascii=False)
        for i in range(0, len(payload), 12):
            time.sleep(0.004)
            yield payload[i:i + 12]

    def generate(self, messages, temperature=0.8):
        self.generate_calls += 1
        return json.dumps({"summary": "概要：街道骚动", "facts": [], "questions": []},
                          ensure_ascii=False)


import dungeon.window.base as wbase  # noqa: E402

_AI = ScriptedAI()
wbase.create_client = lambda *a, **k: _AI


# ---------------------------------------------------------------------------
# 自动驾驶窗口：在 UI 建好后起一个驾驶员线程，所有操作都经 _dispatch 回到主线程
# ---------------------------------------------------------------------------
from dungeon.window import DungeonSessionWindow  # noqa: E402
from dungeon.window.dispatcher import _dispatch  # noqa: E402

CLICK_GAP = 0.6      # 与 _on_next_step 的 0.4s 节流保持一致
_MINIMIZE = False    # 是否最小化视口（默认否，见 _build_ui 注释）


class AutopilotWindow(DungeonSessionWindow):
    """按脚本自动操作的会话窗口。

    脚本动作（二元组）：
      ("sleep", 秒)     等待
      ("click", 次数)   等价于玩家点击/空格（每次间隔 CLICK_GAP）
      ("enter", None)   入口页点「开始副本」
      ("cancel", None)  入口页点「返回」
      ("close", None)   关闭窗口（走 _close_loop → WM_CLOSE）
    """

    def __init__(self, *args, script=None, **kwargs):
        self._script = list(script or [])
        super().__init__(*args, **kwargs)

    def _build_ui(self):
        super()._build_ui()
        if _MINIMIZE:
            try:
                import dearpygui.dearpygui as dpg
                if hasattr(dpg, "minimize_viewport"):
                    # 默认不启用：最小化后 DPG 可能不再跑帧回调，调度队列里的动作
                    # （含关闭）就再也执行不到，窗口会卡住。
                    dpg.minimize_viewport()
            except Exception as exc:
                print(f"[autopilot] 最小化视口失败: {exc}")
        threading.Thread(target=self._run_script, daemon=True).start()

    # ---- 驾驶员 ----
    def _run_script(self):
        try:
            time.sleep(1.0)   # 等首帧与入口/会话初始化稳定
            for action, value in self._script:
                if action == "sleep":
                    time.sleep(value)
                elif action == "click":
                    for _ in range(int(value)):
                        _dispatch.enqueue(self._on_next_step)
                        time.sleep(CLICK_GAP)
                elif action == "enter":
                    _dispatch.enqueue(self._enter_start)
                    time.sleep(1.0)
                elif action == "cancel":
                    _dispatch.enqueue(self._on_entry_cancel)
                    time.sleep(1.0)
                elif action == "close":
                    _dispatch.enqueue(self._close_loop)
                    time.sleep(1.0)
        except Exception as exc:
            print(f"[autopilot] 脚本执行异常: {exc}")
            traceback.print_exc()
            _dispatch.enqueue(self._close_loop)

    def _enter_start(self):
        """入口页点「开始副本」：经真实控件取值，顺便覆盖 combo 读取路径。"""
        try:
            import dearpygui.dearpygui as dpg
            if dpg.does_item_exist("ctl_dungeon") and self.scenario_ids:
                dpg.set_value("ctl_dungeon", self.scenario_ids[0])
        except Exception as exc:
            print(f"[autopilot] 选择方案失败: {exc}")
        self._on_entry_start()


# ---------------------------------------------------------------------------
# 构造参数
# ---------------------------------------------------------------------------
class _Personality:
    description = "冷静而好奇"
    init_intrusion = 1.0
    init_destruction = 1.0
    step_intrusion = 0.1
    step_destruction = 0.1
    sensitivity = 1.0
    normalized_strength = 0.5
    skip_base_prob = 0.0

    def strategy_value(self, action_points=None):
        return 1.0


def _scenario_config():
    from dungeon.schema import empty_scenario_config
    cfg = empty_scenario_config()
    cfg["initial_prompt"] = "自动驾驶自检用副本"
    return cfg


def _scenario_repo():
    from persistence.scenario_repo import ScenarioRepo
    repo = ScenarioRepo(data_dir=_DATA_ROOT)
    repo.save_config("_default", _scenario_config())
    return repo


def _make_window(script, explore=False):
    """构造一个自动驾驶窗口；parent=None（不需要 Tk 主窗口）。"""
    repo = _scenario_repo() if explore else None
    return AutopilotWindow(
        None, name="自检角色", nick="", height=100.0, personality=_Personality(),
        preset=None, greed=0, original_height=1.6, intro_hidden="", intro_visible="",
        tags=[], uploaded_image=None,
        scenario_config=None if explore else _scenario_config(),
        scenario_repo=repo,
        merged_landmarks=[], merged_quips={},
        selected_styles=[], selected_quip_styles=[], detail_pools={},
        ai_config={"provider": "fake", "api_key": "", "model": "fake"},
        is_replay=False, replay_data=None,
        body_parts={}, character=None, character_repo=None, gui=None,
        mode="explore" if explore else "challenge",
        scenario_ids=["_default"] if explore else None,
        script=script,
    )


def _written_files():
    """会话产出文件（排除入口构造方案仓库时写下的 packs/ 配置本身）。"""
    found = []
    for base, _dirs, files in os.walk(_DATA_ROOT):
        for name in files:
            rel = os.path.relpath(os.path.join(base, name), _DATA_ROOT)
            if not rel.startswith("packs" + os.sep):
                found.append(rel)
    return sorted(found)


# ---------------------------------------------------------------------------
# 场景
# ---------------------------------------------------------------------------
def scene_session_close():
    """挑战模式直进会话 → 步进 → 关闭：应落「未完成」回放与报告。"""
    win = _make_window([("click", 4), ("close", None)], explore=False)
    check("会话：窗口已关闭", win._closing is True)
    check("会话：生成了段落", len(win.story_history) > 0, len(win.story_history))
    check("会话：调用了 AI", _AI.stream_calls > 0, _AI.stream_calls)
    files = _written_files()
    replays = [f for f in files if f.startswith(os.path.join("user", "replays"))]
    reports = [f for f in files if f.startswith(os.path.join("user", "reports"))]
    check("会话：落盘了未完成回放", any("_未完成" in f for f in replays), replays)
    check("会话：落盘了报告", bool(reports), reports)
    check("会话：未写 endings.json",
          not os.path.exists(os.path.join(_DATA_ROOT, "user", "endings.json")))
    check("会话：退出时给出提示", any(c[0] == "info" for c in dialog_calls), dialog_calls)


def scene_entry_cancel():
    """入口页点「返回」：入口退出，不应落任何盘。"""
    files_before = _written_files()
    win = _make_window([("cancel", None)], explore=True)
    check("入口返回：窗口已关闭", win._closing is True)
    check("入口返回：标记为入口退出", win._exit_from_entry is True)
    check("入口返回：未生成内容", len(win.story_history) == 0, len(win.story_history))
    check("入口返回：不落盘", _written_files() == files_before,
          set(_written_files()) - set(files_before))


def scene_entry_start():
    """入口页点「开始副本」→ 步进 → 关闭：应进入会话并落盘。"""
    win = _make_window([("enter", None), ("click", 3), ("close", None)], explore=True)
    check("入口进入：无启动错误", not getattr(win, "_launch_error", ""),
          getattr(win, "_launch_error", ""))
    check("入口进入：已初始化会话", win._session_initialized is True)
    check("入口进入：生成了段落", len(win.story_history) > 0, len(win.story_history))
    check("入口进入：窗口已关闭", win._closing is True)
    files = _written_files()
    replays = [f for f in files if f.startswith(os.path.join("user", "replays"))]
    check("入口进入：落盘了回放", bool(replays), files)


SCENES = {
    "session-close": scene_session_close,
    "entry-cancel": scene_entry_cancel,
    "entry-start": scene_entry_start,
}


def run_scene(name, timeout):
    print(f"[scene] {name}")
    _watchdog(timeout)
    SCENES[name]()
    _log.flush()


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
_watchdog_timer = None


def _watchdog(seconds):
    """（重）置看门狗：单个场景超时即自杀，避免窗口卡在屏幕上。"""
    global _watchdog_timer
    if _watchdog_timer is not None:
        _watchdog_timer.cancel()

    def suicide():
        print(f"[watchdog] 场景超过 {seconds}s 未结束，强制退出")
        _log.flush()
        os._exit(9)

    _watchdog_timer = threading.Timer(seconds, suicide)
    _watchdog_timer.daemon = True
    _watchdog_timer.start()


def _run_isolated(names, timeout):
    """每个场景一个子进程：崩溃/挂死只影响子进程，主进程能定位到具体场景。

    判定用子进程打印的结论行而不是退出码——实测跑完会话后进程会在**退出阶段**
    挂死（DPG 上下文销毁后的 native 收尾），此时结论已经打印完毕；外层在看到
    结论行后直接 kill，按结论判定成败。
    """
    import subprocess
    for name in names:
        proc = subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "--scene", name],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        buf = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = proc.stdout.readline()
            if line:
                buf.append(line)
            if not line and proc.poll() is not None:
                break
            if any("report:" in b for b in buf):   # 结论已输出
                break
        out = "".join(buf)
        hung = proc.poll() is None
        if hung:
            proc.kill()
        print(f"  [{name}] 输出: {out.strip()[-400:]}")
        if hung:
            print(f"  [{name}] 进程在退出阶段挂死（已知现象），已强制结束")
        check(f"场景 {name} 在 {timeout}s 内给出结论", "[dungeon_autopilot] report:" in out,
              out[-300:])
        check(f"场景 {name} 断言全过", "[dungeon_autopilot] PASSED" in out, out[-300:])


def main():
    parser = argparse.ArgumentParser(description="副本窗口自动驾驶自检")
    parser.add_argument("--scene", choices=sorted(SCENES) + ["all"], default="all")
    parser.add_argument("--repeat", type=int, default=1,
                        help="每个场景重复次数（>1 用于回归开关窗口的关闭路径）")
    parser.add_argument("--in-process", action="store_true",
                        help="不开子进程、在当前进程内依次跑场景（默认每个场景一个子进程）")
    parser.add_argument("--timeout", type=int, default=40,
                        help="硬看门狗秒数：超时即自杀，避免窗口卡在屏幕上")
    args = parser.parse_args()

    names = sorted(SCENES) if args.scene == "all" else [args.scene]
    # 只有跑全部场景时才默认开子进程；显式指定单个场景就在本进程跑（否则会递归）
    if args.scene == "all" and not args.in_process:
        _run_isolated(names, args.timeout)
    else:
        for _ in range(args.repeat):
            for name in names:
                run_scene(name, args.timeout)
        if _watchdog_timer is not None:
            _watchdog_timer.cancel()

    # 残留的非 daemon 线程（如背景重采样 Timer）会让解释器在 sys.exit 后挂住，
    # 这里记录下来便于定位；脚本自身用 os._exit 收尾，保证一定会退出。
    leftover = [t.name for t in threading.enumerate()
                if t is not threading.main_thread() and not t.daemon]
    print(f"[threads] 残留非 daemon 线程: {leftover}")

    print("[exit] 场景全部结束")
    _log.flush()
    sys.stdout = sys.__stdout__
    sys.stderr = sys.__stderr__
    if failures:
        print(f"[dungeon_autopilot] FAILED {len(failures)}/{total}: {failures}")
    else:
        print(f"[dungeon_autopilot] PASSED {total}/{total}"
              + (f" (repeat={args.repeat})" if args.repeat > 1 else ""))
    if leftover:
        print(f"[dungeon_autopilot] 残留非 daemon 线程: {leftover}")
    print(f"[dungeon_autopilot] report: {_REPORT_PATH}")
    # 走正常解释器退出（sys.exit），让 DPG 有机会完成自己的收尾；
    # 若仍有残留的 native 线程导致退出挂死，由外层 --isolate 的超时兜底。
    _log.flush()
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
