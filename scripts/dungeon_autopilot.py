"""副本窗口（DearPyGui）自动驾驶自检。

不用人手点击、不联网、不碰真实 ``data/``：用脚本代替玩家操作 DPG 会话窗口，
跑完一个场景后对窗口状态与落盘结果做断言。

用法::

    python scripts/dungeon_autopilot.py                  # 全部场景（默认，每场景一个子进程）
    python scripts/dungeon_autopilot.py --scene session-close --isolate
    python scripts/dungeon_autopilot.py --repeat 10 --isolate   # 连续开关窗口 10 次
    python scripts/dungeon_autopilot.py --in-process     # 当前进程内依次跑（快，但崩溃会带走全部）
    python scripts/dungeon_autopilot.py --require-clean-exit    # 退出挂死也判失败（环境复测用）

场景：

===============  ==========================================================
callback-thread  真实按键回调（SendMessage 直投 WM_KEYDOWN）的执行线程 == 帧循环线程
                 ——DPG 2.3.1 默认把回调派发到工作线程，须由手动回调管理收拢
                 （见 ui._build_ui 的 configure_app 与 base._run_frame_loop）
chapter-bgm      章节背景音乐接线：起始章节起播 → 跳到无音乐章节停播 → 收尾收工
                 （用替身后端，自检不出声；真实后端由 check_scenario_schema 探）
chapter-voice-fx 章节对话语音效果（物理尺度，不是语气）：她那一档开分拍回声 →
                 其他人那一档缓抖 → 叙述句不念 → 换章清效果 → 回放带配置 → 收尾
                 （假合成器不联网、替身后端不出声）
dialogue-voice   Solea/Bulla 对话语音接线：她/主角/配角分嗓子 → 叙述句不念 →
                 全局开关关掉一声不吭 → 收尾收工（假合成器不联网、替身后端不出声）
session-close    挑战模式直进会话 → 步进若干 → 点 X 关闭（走「未完成」收尾）
entry-cancel     探索模式入口页 → 点「返回」（入口退出，不应落盘）
entry-start      探索模式入口页 → 点「开始副本」→ 步进 → 点 X 关闭
entry-replay     入口页点「加载回放」→ 先取消一次 → 再选文件 → 在**同一窗口内**切回放
tk-host          parent 换成真 Tk 根窗口 → 心跳计数证明宿主事件循环未被冻结
native-close     用原生关闭键（WM_CLOSE）关视口 → 队列里不得残留 WM_QUIT（§5-C12）
===============  ==========================================================

``entry-replay`` 覆盖 L4：回放不再让调用方 ``new`` 第二个窗口，而是在同一个 DPG
生命周期里切 ``is_replay``——因此本场景断言"整段回放只跑了一次窗口生命周期"。

L3 的验收是帧时钟：每个场景结束后，窗口自有的 ``FrameScheduler`` 应已停止、
``DungeonBackground`` 的像素工作者应收工，会话里不该留下自己的线程。

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
# 隔离：数据目录改到临时目录（真实数据不动）、宿主换成脚本宿主
# 必须在 import dungeon.window.* 之前打补丁——那些模块用
# ``from paths import data_dir`` 在导入时绑定函数对象。
# ---------------------------------------------------------------------------
import paths  # noqa: E402

_DATA_ROOT = os.path.join(tempfile.mkdtemp(prefix="dungeon_autopilot_data_"), "data")
os.makedirs(os.path.join(_DATA_ROOT, "user"), exist_ok=True)
paths.data_dir = lambda: _DATA_ROOT

# 官方组件包是窗口层常驻 Python 包（dungeon/window/component_pack），随 registry
# 正常导入，与 data_dir 重定向无关——这里不再需要任何注入。

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
# 自检宿主：记录收尾弹框，不做任何窗口操作
#    L2 之后 window 层只经宿主端口要东西（不再 import ui.common.dialogs），
#    所以自检不需要再打桩对话框模块——换掉宿主就够了。
# ---------------------------------------------------------------------------
from dungeon.window.host import DIALOG_ASK, HostPort  # noqa: E402


class ScriptedHost(HostPort):
    """把弹框收进 ``calls``，询问类一律按 ``answer`` 回答（默认「否」）。

    另外提供 ``replay_payload``：给 :meth:`open_replay_file` 用。置 None 表示
    "用户在文件选择框里点了取消"（L4 自检要覆盖这条分支）。
    """

    def __init__(self, widget=None, answer=False, replay_payload=None):
        self.widget = widget
        self.answer = answer
        self.calls = []
        self.replay_payload = replay_payload
        self.replay_calls = 0

    def dialog(self, kind, title, message):
        self.calls.append((kind, title, message))   # [(kind, title, message), ...]
        if kind == DIALOG_ASK:
            return bool(self.answer)
        return None

    def open_replay_file(self):
        self.replay_calls += 1
        return self.replay_payload


_HOST = ScriptedHost()

#: 结论行出现后等待子进程自行退出的宽限（秒）。正常子进程约 0.1s 退完；
#: 超过这个时间还在才算"退出阶段挂死"（见 _run_isolated 的说明）。
EXIT_GRACE = 5.0

#: 真 Tk 宿主：尺寸/DPI/显隐/事件泵全走适配器，只有弹框被换成记录器
#: ——自检里弹真模态框会一直等用户点确定（`scene_tk_host` 曾因此挂死）。
from ui.common.tk_host import TkHost  # noqa: E402


class RecordingTkHost(TkHost):
    def __init__(self, widget):
        super().__init__(widget)
        self.calls = []

    def dialog(self, kind, title, message):
        self.calls.append((kind, title, message))
        return False if kind == DIALOG_ASK else None


# ---------------------------------------------------------------------------
# 自动驾驶窗口：在 UI 建好后起一个驾驶员线程，所有操作都经窗口自己的帧时钟回到主线程
# ---------------------------------------------------------------------------
from dungeon.window import DungeonSessionWindow  # noqa: E402
from dungeon.window.launcher import REPLAY_MARK  # noqa: E402

CLICK_GAP = 0.6      # 与 _on_next_step 的 0.4s 节流保持一致
_MINIMIZE = False    # 是否最小化视口（默认否，见 _build_ui 注释）
#: 自检里创建的 Tk 根窗口（不销毁：会话结束后碰 Tk 会崩，见 scene_tk_host）
_KEEP_ALIVE_ROOTS = []

_WM_CLOSE = 0x0010   #: 投给视口原生窗口 = 用户点关闭键
_WM_QUIT = 0x0012    #: GLFW 销毁原生窗口后 Windows 记下的「退出进程」消息


def _post_viewport_native_close() -> bool:
    """向 DPG 原生视口窗口投一条 ``WM_CLOSE``（= 用户点关闭键），成功返回 True。

    这是唯一能触发「原生关闭」路径的手段，也只有它会往本线程队列里留一条
    ``WM_QUIT``（见窗口文档 §5-C12）。按**窗口类名**找：DPG 用创建视口时的标题
    （``DungeonSession``）注册窗口类，之后 ``_fix_windows_title`` 改标题不影响类名。
    """
    if not sys.platform.startswith("win"):
        return False
    import ctypes
    hwnd = ctypes.windll.user32.FindWindowW("DungeonSession", None)
    if not hwnd:
        return False
    ctypes.windll.user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0)
    return True


def _post_viewport_key() -> bool:
    """向自检视口投一次 K 键按下/抬起（SendMessage 直达窗口过程，不依赖焦点）。"""
    if not sys.platform.startswith("win"):
        return False
    import ctypes
    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW("DungeonSession", None)
    if not hwnd:
        return False
    vk_k = 0x4B
    lparam = 0x00250001   # scancode=0x25 扩展位0 重复1
    user32.SendMessageW(hwnd, 0x0100, vk_k, lparam)   # WM_KEYDOWN
    time.sleep(0.05)
    user32.SendMessageW(hwnd, 0x0101, vk_k, lparam | 0xC0000000)   # WM_KEYUP
    return True


def _pending_quit() -> int:
    """线程消息队列里是否压着 ``WM_QUIT``（1/0；非 Windows 恒为 0）。

    **只看不取**：它留在队列里才是症状（Tk 与 Windows 都不管它，却让 ``WM_TIMER``
    不再合成 → 计时器与模态对话框全停摆）。
    """
    if not sys.platform.startswith("win"):
        return 0
    import ctypes
    from ctypes import wintypes
    msg = wintypes.MSG()
    found = ctypes.windll.user32.PeekMessageW(
        ctypes.byref(msg), None, _WM_QUIT, _WM_QUIT, 0)
    return 1 if found else 0


class AutopilotWindow(DungeonSessionWindow):
    """按脚本自动操作的会话窗口。

    脚本动作（二元组）：
      ("sleep", 秒)     等待
      ("click", 次数)   等价于玩家点击/空格（每次间隔 CLICK_GAP）
      ("enter", None)   入口页点「开始副本」
      ("cancel", None)  入口页点「返回」
      ("replay", 值)    入口页点「加载回放」：值为 "cancel" 时宿主不返回文件
      ("check", fn)     会话中途在主线程（DPG 存活时）执行 fn(win)，断言失败记入
                        _check_results（run() 结束后场景据此判定）
      ("close", None)   关闭窗口（走 _close_loop → dpg.stop_dearpygui）
      ("native-close", None)  向视口投 WM_CLOSE（模拟用户点关闭键）
      ("native-key", None)    向视口投 K 键按下/抬起（回调线程探针用）
    """

    def __init__(self, *args, script=None, **kwargs):
        self._script = list(script or [])
        #: 每个脚本动作执行后的状态快照，供场景断言"那一刻"的情形
        self._snapshots = {}
        #: ("check", fn) 动作的断言结果 [("ok"|"fail", 说明), ...]
        self._check_results = []
        #: ``native-close`` 动作是否真的把 WM_CLOSE 投给了视口原生窗口
        self._native_close_posted = False
        self._callback_thread = None
        self._callback_received = threading.Event()
        self._native_key_posted = False
        super().__init__(*args, **kwargs)

    def _build_ui(self):
        super()._build_ui()
        if getattr(self, "_probe_callback", False):
            import dearpygui.dearpygui as dpg
            dpg.add_button(label="Callback probe", tag="autopilot_callback_probe",
                           parent="main_window", pos=[24, 24], width=160,
                           height=42, callback=self._record_callback_thread)
            with dpg.handler_registry():
                dpg.add_key_press_handler(key=dpg.mvKey_K,
                                          callback=self._record_callback_thread)
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
                        self._frame.call(self._on_next_step)
                        time.sleep(CLICK_GAP)
                elif action == "enter":
                    self._frame.call(self._enter_start)
                    time.sleep(1.0)
                elif action == "cancel":
                    self._frame.call(self._on_entry_cancel)
                    time.sleep(1.0)
                elif action == "replay":
                    # L4：取消时应留在入口页；返回数据时应切到回放（仍在同一窗口）
                    self.host.replay_payload = (
                        None if value == "cancel" else _replay_fixture())
                    self._frame.call(self._on_entry_replay)
                    time.sleep(1.0)
                elif action == "check":
                    # 断言必须在主线程执行（DPG 单线程约束）；Event 等它真正跑完，
                    # 后续动作与快照才有意义
                    done = threading.Event()

                    def _run_check(fn=value, win_ref=self, done=done):
                        name = getattr(fn, "__name__", "?")
                        try:
                            fn(win_ref)
                            win_ref._check_results.append(("ok", name))
                        except Exception as exc:
                            win_ref._check_results.append(
                                ("fail", f"{name}: {type(exc).__name__}: {exc}"))
                        finally:
                            done.set()

                    self._frame.call(_run_check)
                    done.wait(timeout=10.0)
                elif action == "close":
                    self._frame.call(self._close_loop)
                    time.sleep(1.0)
                elif action == "native-close":
                    # 模拟用户点视口关闭键（X）：只有这条路径会在队列里留下 WM_QUIT
                    self._native_close_posted = _post_viewport_native_close()
                    time.sleep(1.0)
                elif action == "native-key":
                    self._native_key_posted = _post_viewport_key()
                    self._callback_received.wait(timeout=3.0)
                # 每个动作之后留一张快照，场景可以断言"那一刻"的情形
                self._snapshots[(action, str(value))] = {
                    "closing": bool(self._closing),
                    "replay": bool(self.is_replay),
                    "entry": bool(getattr(self, "_is_entry_phase", False)),
                    "history": len(self.story_history),
                }
        except Exception as exc:
            print(f"[autopilot] 脚本执行异常: {exc}")
            traceback.print_exc()
            self._frame.call(self._close_loop)

    def _enter_start(self):
        """入口页点「开始副本」：经真实控件取值，顺便覆盖 combo 读取路径。"""
        try:
            import dearpygui.dearpygui as dpg
            if dpg.does_item_exist("ctl_dungeon") and self.scenario_ids:
                dpg.set_value("ctl_dungeon", self.scenario_ids[0])
        except Exception as exc:
            print(f"[autopilot] 选择方案失败: {exc}")
        self._on_entry_start()

    def _record_callback_thread(self, sender, app_data):
        self._callback_thread = threading.get_ident()
        self._callback_received.set()


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


def _replay_fixture():
    """回放 fixture：两条步进记录。

    字段按窗口回放路径的需要给全：``_init_replay`` 读第一条的 *_before，
    ``_replay_next_step`` 读每条的 type/text/*_after。
    """
    return [
        {"step": 1, "kind": None, "type": "background",
         "text": "回放第一段：街道在脚下震颤。",
         "intrusion_before": 1.0, "destruction_before": 1.0,
         "intrusion_after": 1.2, "destruction_after": 1.1,
         "casualty_increase": 0.0, "total_casualties_after": 0.0},
        {"step": 2, "kind": None, "type": "dialog",
         "text": "回放第二段：有人尖叫着后退。",
         "intrusion_before": 1.2, "destruction_before": 1.1,
         "intrusion_after": 1.4, "destruction_after": 1.2,
         "casualty_increase": 0.0, "total_casualties_after": 0.0},
    ]


def _scenario_repo():
    from persistence.scenario_repo import ScenarioRepo
    repo = ScenarioRepo(data_dir=_DATA_ROOT)
    repo.save_config("_default", _scenario_config())
    return repo


def _make_window(script, explore=False, parent=None, host=None,
                 config_overrides=None):
    """构造并运行一个自动驾驶窗口（默认用 ScriptedHost，不需要 Tk 主窗口）。

    L1 之后构造与运行分离；L4 之后 ``run()`` 返回 :class:`SessionResult` 而不是
    ``self``——需要检查窗口内部状态时把实例一起返回（``win, result``）。
    L2 之后宿主能力全部经 ``host`` 端口注入，自检因此不再打桩对话框模块。
    ``config_overrides`` 直接覆盖 ``_scenario_config()`` 的字段（组件冒烟用）；
    值为 ``None`` 表示删除该键（构造"text_component 缺失"的旧写法配置）。"""
    repo = _scenario_repo() if explore else None
    cfg = _scenario_config()
    for key, value in (config_overrides or {}).items():
        if value is None:
            cfg.pop(key, None)
        else:
            cfg[key] = value
    win = AutopilotWindow(
        parent, name="自检角色", nick="", height=100.0, personality=_Personality(),
        preset=None, greed=0, original_height=1.6, intro_hidden="", intro_visible="",
        tags=[], uploaded_image=None,
        scenario_config=None if explore else cfg,
        scenario_repo=repo,
        merged_landmarks=[], merged_quips={},
        selected_styles=[], selected_quip_styles=[], detail_pools={},
        ai_config={"provider": "fake", "api_key": "", "model": "fake"},
        is_replay=False, replay_data=None,
        body_parts={}, character=None, character_repo=None, gui=None,
        mode="explore" if explore else "challenge",
        scenario_ids=["_default"] if explore else None,
        host=host if host is not None else _HOST,
        script=script,
    )
    return win, win.run()


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
    win, result = _make_window([("click", 4), ("close", None)], explore=False)
    check("会话：宿主为脚本宿主", win.host is _HOST)
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
    check("会话：退出时给出提示", any(c[0] == "info" for c in _HOST.calls), _HOST.calls)
    # ---- L4：结果对象 ----
    check("会话：结果为 session-ended", result.reason == "session-ended", result)
    check("会话：结果 succeeded", result.succeeded and not result.failed
          and not result.cancelled, result)
    check("会话：结果带回访/报告路径", bool(result.replay_path and result.report_path),
          (result.replay_path, result.report_path))
    check("会话：结果带了渲染帧数", result.frames_rendered > 0, result.frames_rendered)
    # ---- L3：帧时钟随会话收摊 ----
    check("会话：帧时钟已停止", win._frame.running is False)
    check("会话：帧任务已清空", win._frame.task_count() == 0, win._frame.task_count())
    check("会话：帧时钟跑过任务", win._frame.task_runs > 0, win._frame.task_runs)
    check("会话：像素工作者已收工", win._background.worker_alive is False)
    # 章节背景音乐：会话收尾必须静音且线程不再挂着（作者没配音乐也要成立）
    check("会话：背景音乐已收工", win._bgm.worker_alive is False)


def _write_silent_wav(path, seconds=0.2):
    """写一个真实存在的静音 wav：替身后端也只认"文件在不在"。"""
    import struct
    import wave
    rate = 8000
    frames = int(rate * seconds)
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(struct.pack("<h", 0) * frames)
    return path


class _FakeTrack:
    """自检替身后端：只记录指令，不碰音频设备（因此自检全程不出声）。"""

    name = "fake"
    #: 收到的指令 [(动作, 路径), ...]，场景据此断言"真的起了播/真的关了"
    events = []
    #: set_volume 调用次数：语音效果的「缓抖」靠音量调制，据此断言它真的动了
    volume_calls = 0

    def __init__(self, path):
        self._path = path
        self.volume = None
        self.loop = None

    @classmethod
    def supported(cls):
        return True

    @classmethod
    def open(cls, path):
        return cls(path) if os.path.isfile(path) else None

    def start(self, loop, volume):
        self.loop = loop
        self.volume = volume
        self.events.append(("start", self._path))
        return True

    def set_volume(self, volume):
        self.volume = volume
        _FakeTrack.volume_calls += 1
        self.events.append(("volume", self._path))

    def stop(self):
        self.events.append(("stop", self._path))

    def close(self):
        self.events.append(("close", self._path))

    def finished(self):
        """语音层靠它回收播完的音轨；替身没真在放，一律算播完。"""
        return True

    def tick(self):
        """背景音乐层靠它续播；替身说自己不需要（返回 False = 后端自带循环）。"""
        return False


def _fake_synth(dest_dir=None):
    """假合成器：不联网、不出声，只留一份"合成产物"在临时目录里。

    返回 ``(synth, calls)``——``calls`` 里是 ``(音色, 语速, 台词)``，场景据此
    断言「她/主角/配角各用自己的嗓子」。
    """
    calls = []

    def _synth(voice, rate, text, directory):
        calls.append((voice, rate, text))
        path = os.path.join(directory, f"voice_{len(calls)}.mp3")
        with open(path, "wb") as handle:
            handle.write(b"fake")
        return path

    return _synth, calls


def scene_dialogue_voice():
    """Solea/Bulla 对话语音：谁说话谁出声，关掉就安静，收尾收干净。

    与 ``chapter-bgm`` 同款替身策略——真实合成要联网、真实播放会出声，自检
    只盯**接线**：方案音色有没有传到播放器、说话人有没有分对嗓子、全局开关
    关掉时是不是一声不吭、会话收尾后线程与临时文件有没有留下。
    """
    from dungeon import audio as audio_mod
    _FakeTrack.events = []
    synth, calls = _fake_synth()
    previous = audio_mod.set_backends((_FakeTrack,))
    try:
        voice_cfg = {"enabled": True, "voice_her": "V_HER",
                     "voice_protagonist": "V_PRO",
                     "voice_other": ["A", "B", "C"], "rate": "+0%",
                     "volume": 70}
        observed = {}

        def _speak(win):
            # 换掉合成实现：真合成要联网，自检不该依赖外网也不该出声
            win._speech._synth = synth
            # 走窗口层的同一条路径（带说话人的显示单元定稿 → 朗读）。
            # 一句一句等完：新台词本来就会打断旧台词（翻页时不该念上一页的），
            # 连发三句只会留下最后一句。
            for speaker, text in (("自检角色", "你跑不掉的"),
                                  ("主角", "等等！"),
                                  ("军官", "全体撤退"),
                                  ("", "一阵尘土落下。")):
                win._speak_item({"speaker": speaker, "text": text})
                win._speech.wait_idle(5.0)
            observed["calls"] = list(calls)
            observed["last_voice"] = win._speech.last_voice
            observed["tmp_dir"] = win._speech._tmp_dir

        def _mute(win):
            # 全局开关关掉：不看方案配置，一句都不该念
            win.settings["dungeon_voice_enabled"] = False
            win._configure_voice(win.scenario_config)
            calls.clear()
            win._speak_item({"speaker": "自检角色", "text": "关掉后不该出声"})
            win._speech.wait_idle(2.0)
            observed["muted_calls"] = list(calls)
            observed["muted_enabled"] = win._speech.enabled

        win, _result = _make_window(
            [("check", _speak), ("check", _mute), ("close", None)],
            config_overrides={"voice": voice_cfg, "coupling_level": "solea",
                              "protagonist_title": "主角"})
        check("语音：故障不在脚本里",
              all(s == "ok" for s, _ in win._check_results), win._check_results)
        voices = [call[0] for call in observed.get("calls") or []]
        check("语音：只有带说话人的台词念出来", len(voices) == 3, voices)
        check("语音：她用自己的音色", voices and voices[0] == "V_HER", voices)
        check("语音：主角用自己的音色", len(voices) > 1 and voices[1] == "V_PRO",
              voices)
        check("语音：配角从音色池里拿嗓子",
              len(voices) > 2 and voices[2] in ("A", "B", "C"), voices)
        check("语音：合成产物真的起了播",
              any(kind == "start" for kind, _ in _FakeTrack.events),
              _FakeTrack.events)
        check("语音：全局开关关掉后一声不吭",
              observed.get("muted_enabled") is False
              and not observed.get("muted_calls"), observed.get("muted_calls"))
        check("语音：会话收尾后线程已收工", win._speech.worker_alive is False)
        check("语音：临时音频已清理",
              not observed.get("tmp_dir")
              or not os.path.isdir(observed["tmp_dir"]),
              observed.get("tmp_dir"))
    finally:
        audio_mod.set_backends(previous)


def scene_chapter_voice_fx():
    """章节对话语音效果：她那一档开分拍回声，其他人那一档缓抖，换章即清。

    与 ``dialogue-voice`` 同款替身策略——假合成器不联网、替身后端不出声，自检
    只盯**接线**：章节配置有没有真的传到语音导演、两档有没有按说话人分对、
    回放记录有没有带上配置、走到没配效果的章节会不会串味、收尾干不干净。

    注意：假合成器产出的不是真音频，解码必然失败 → 这条链走的是**零依赖的
    播放侧**（分拍 + 音量调制），正好把「解不开也不能拖垮朗读」一起验了。
    """
    from dungeon import audio as audio_mod
    _FakeTrack.events = []
    _FakeTrack.volume_calls = 0
    synth, calls = _fake_synth()
    previous = audio_mod.set_backends((_FakeTrack,))
    try:
        cfg = _scenario_config()
        cfg["chapters"] = [
            {"name": "回声章", "start": True,
             "voice_fx": {"her": {"preset": "open_valley", "intensity": 80},
                          "others": {"preset": "trembling_ground",
                                     "intensity": 70}}},
            {"name": "静默章", "voice_fx": {}},
        ]
        observed = {}

        def _snapshot(events):
            return {"starts": sum(1 for kind, _p in events if kind == "start"),
                    "volumes": sum(1 for kind, _p in events if kind == "volume")}

        def _speak(win):
            win._speech._synth = synth
            observed["effect"] = dict(win._speech.effect)
            observed["replay_fx"] = [r.get("voice_fx") for r in win.replay_data
                                     if r.get("kind") == "chapter"]
            # 她 → 「她」那一档（旷野回声：主声 + 两拍分拍）
            _FakeTrack.events = []
            win._speak_item({"speaker": "自检角色", "text": "你跑不掉的"})
            win._speech.wait_idle(6.0)
            observed["her"] = _snapshot(_FakeTrack.events)
            observed["her_engine"] = win._speech.last_engine
            observed["her_starts"] = [p for kind, p in _FakeTrack.events
                                      if kind == "start"]
            # 配角 → 「其他人」那一档（震地余波：缓抖，不动分拍）
            _FakeTrack.events = []
            win._speak_item({"speaker": "军官", "text": "全体撤退"})
            win._speech.wait_idle(4.0)
            observed["other"] = _snapshot(_FakeTrack.events)
            observed["other_engine"] = win._speech.last_engine
            # 叙述句（无说话人）不该出声，也不该进合成队列
            _FakeTrack.events = []
            before = len(calls)
            win._speak_item({"speaker": "", "text": "一阵尘土落下。"})
            win._speech.wait_idle(2.0)
            observed["narration"] = _snapshot(_FakeTrack.events)
            observed["narration_calls"] = len(calls) - before
            observed["calls"] = len(calls)

        def _leave_chapter(win):
            _FakeTrack.events = []
            win._enter_chapter("静默章", record=True)
            observed["silent_effect"] = dict(win._speech.effect)
            win._speak_item({"speaker": "自检角色", "text": "安静下来"})
            win._speech.wait_idle(3.0)
            observed["silent"] = _snapshot(_FakeTrack.events)
            observed["silent_engine"] = win._speech.last_engine
            observed["replay_fx_after"] = [r.get("voice_fx")
                                           for r in win.replay_data
                                           if r.get("kind") == "chapter"]

        def _finale(win):
            observed["tmp_dir"] = win._speech._tmp_dir

        win, _result = _make_window(
            [("check", _speak), ("check", _leave_chapter), ("check", _finale),
             ("close", None)],
            config_overrides={"chapters": cfg["chapters"],
                              "voice": {"enabled": True, "volume": 80},
                              "coupling_level": "solea",
                              "protagonist_title": "主角"})
        check("语音效果：故障不在脚本里",
              all(s == "ok" for s, _ in win._check_results), win._check_results)
        expected = {"her": {"preset": "open_valley", "intensity": 80},
                    "others": {"preset": "trembling_ground", "intensity": 70}}
        check("语音效果：起始章节把两档装进语音导演",
              observed.get("effect") == expected, observed.get("effect"))
        check("语音效果：回放记录带上章节的 voice_fx",
              observed.get("replay_fx") and all(
                  fx and fx.get("her") for fx in observed["replay_fx"]),
              observed.get("replay_fx"))
        check("语音效果：她那一档开了分拍回声（主声 + 2 拍）",
              observed.get("her_engine") == "playback"
              and observed.get("her", {}).get("starts") == 3, observed.get("her"))
        check("语音效果：分拍用的是同一份合成音频",
              len(observed.get("her_starts") or []) == 3
              and len(set(observed["her_starts"])) == 1
              and all(p.endswith((".mp3", ".wav")) for p in observed["her_starts"]),
              observed.get("her_starts"))
        check("语音效果：其他人那一档走缓抖（1 起播 + 音量调制）",
              observed.get("other_engine") == "playback"
              and observed.get("other", {}).get("starts") == 1
              and observed.get("other", {}).get("volumes", 0) >= 1,
              observed.get("other"))
        check("语音效果：叙述句不念也不进合成队列",
              observed.get("narration", {}).get("starts") == 0
              and observed.get("narration_calls") == 0,
              observed.get("narration"))
        check("语音效果：跳到没配效果的章节后清空",
              observed.get("silent_effect") == {}
              and observed.get("silent_engine") == "none"
              and observed.get("silent", {}).get("starts") == 1
              and observed.get("silent", {}).get("volumes") == 0,
              (observed.get("silent_effect"), observed.get("silent")))
        check("语音效果：新章节的回放记录也是空效果",
              observed.get("replay_fx_after")
              and observed["replay_fx_after"][-1] == {},
              observed.get("replay_fx_after"))
        check("语音效果：会话收尾后语音线程已收工",
              win._speech.worker_alive is False)
        check("语音效果：临时音频已清理",
              not observed.get("tmp_dir")
              or not os.path.isdir(observed["tmp_dir"]), observed.get("tmp_dir"))
    finally:
        audio_mod.set_backends(previous)


def scene_chapter_bgm():
    """章节背景音乐：进配了音乐的章节起播，跳到没配音乐的章节停播。

    真实后端只在``check_scenario_schema``里探一次（那台机器上有没有可用后端
    不该决定自检成败），这里换成替身后端——要盯的是**接线**：章节配置有没有
    真的传到播放器、离开音乐章节有没有静下来、会话收尾有没有收干净。
    """
    from dungeon import audio as audio_mod
    _FakeTrack.events = []
    previous = audio_mod.set_backends((_FakeTrack,))
    wav = _write_silent_wav(os.path.join(_REPORT_DIR, "bgm_probe.wav"))
    try:
        cfg = _scenario_config()
        cfg["chapters"] = [
            {"name": "音乐章", "start": True,
             "bgm": {"path": wav, "volume": 30, "loop": True, "fade_seconds": 0}},
            {"name": "静默章", "bgm": {}},
        ]
        observed = {}

        def _probe_playing(win):
            win._bgm.wait_idle()
            observed["path"] = win.current_bgm_path
            observed["track"] = win._bgm.track_path
            observed["playing"] = win._bgm.playing
            observed["replay_bgm"] = [
                r.get("bgm") for r in win.replay_data if r.get("kind") == "chapter"]

        def _leave_chapter(win):
            win._enter_chapter("静默章", record=True)
            win._bgm.wait_idle()
            observed["silent_path"] = win.current_bgm_path
            observed["silent_playing"] = win._bgm.playing

        win, _result = _make_window(
            [("check", _probe_playing), ("check", _leave_chapter),
             ("close", None)], config_overrides={"chapters": cfg["chapters"]})
        check("音乐：故障不在脚本里",
              all(s == "ok" for s, _ in win._check_results), win._check_results)
        check("音乐：起始章节自动带出曲目", observed.get("path") == wav, observed)
        check("音乐：播放器真的起了播",
              observed.get("playing") is True and observed.get("track") == wav,
              observed)
        check("音乐：回放记录带上 bgm 配置",
              any(b and b.get("path") == wav for b in observed.get("replay_bgm") or []),
              observed.get("replay_bgm"))
        check("音乐：跳到无音乐章节后停播",
              observed.get("silent_playing") is False
              and observed.get("silent_path") == "", observed)
        check("音乐：曲目被关闭（不留设备句柄）",
              ("close", wav) in _FakeTrack.events, _FakeTrack.events)
        check("音乐：会话收尾后播放线程已收工", win._bgm.worker_alive is False)
    finally:
        audio_mod.set_backends(previous)


def scene_callback_thread():
    """真实 DPG 回调必须与手动渲染循环运行在同一线程。

    回调经由「SendMessage 直投 WM_KEYDOWN」触发的真实按键 handler 触发
    （悬停判定的按钮对合成鼠标输入不可靠）；断言其执行线程就是跑 ``run()``
    帧循环的线程。此前 DPG 默认把回调派发到工作线程，与 window 层线程约定
    冲突，靠 ui._build_ui 的 manual_callback_management + 帧循环
    run_callbacks 收拢（见 window.md §2.2 / §4）。
    """
    win = AutopilotWindow(
        None, name="自检角色", nick="", height=100.0, personality=_Personality(),
        preset=None, greed=0, original_height=1.6, intro_hidden="", intro_visible="",
        tags=[], uploaded_image=None, scenario_config=_scenario_config(),
        scenario_repo=None, merged_landmarks=[], merged_quips={}, selected_styles=[],
        selected_quip_styles=[], detail_pools={}, ai_config={"provider": "fake"},
        body_parts={}, character=None, character_repo=None, gui=None,
        mode="challenge", host=_HOST,
        script=[("native-key", None), ("close", None)])
    win._probe_callback = True
    render_thread = threading.get_ident()
    win.run()
    check("回调：原生按键已投递", win._native_key_posted)
    check("回调：真实按钮回调已触发", win._callback_received.is_set())
    check("回调：按钮在帧循环线程执行", win._callback_thread == render_thread,
          (win._callback_thread, render_thread))


def scene_entry_cancel():
    """入口页点「返回」：入口退出，不应落任何盘。"""
    files_before = _written_files()
    win, result = _make_window([("cancel", None)], explore=True)
    check("入口返回：窗口已关闭", win._closing is True)
    check("入口返回：标记为入口退出", win._exit_from_entry is True)
    check("入口返回：未生成内容", len(win.story_history) == 0, len(win.story_history))
    check("入口返回：不落盘", _written_files() == files_before,
          set(_written_files()) - set(files_before))
    check("入口返回：结果为 entry-cancelled",
          result.reason == "entry-cancelled" and result.cancelled, result)
    check("入口返回：帧时钟已停止", win._frame.running is False)
    check("入口返回：像素工作者已收工", win._background.worker_alive is False)


def scene_entry_start():
    """入口页点「开始副本」→ 步进 → 关闭：应进入会话并落盘。"""
    win, result = _make_window([("enter", None), ("click", 3), ("close", None)],
                               explore=True)
    check("入口进入：无启动错误", not result.launch_error, result.launch_error)
    check("入口进入：已初始化会话", win._session_initialized is True)
    check("入口进入：生成了段落", len(win.story_history) > 0, len(win.story_history))
    check("入口进入：窗口已关闭", win._closing is True)
    files = _written_files()
    replays = [f for f in files if f.startswith(os.path.join("user", "replays"))]
    check("入口进入：落盘了回放", bool(replays), files)
    check("入口进入：结果 scenario_id 正确", result.scenario_id == "_default",
          result.scenario_id)
    check("入口进入：结果为 session-ended", result.succeeded, result)
    check("入口进入：像素工作者已收工", win._background.worker_alive is False)


def scene_entry_replay():
    """入口页「加载回放」：取消一次不应关窗，选到文件应在**同一窗口内**切回放。

    L4 之前这段控制流在调用方（关掉窗口 → 由 ``ui/exploration/exp_frame.py``
    new 出第二个 ``DungeonSessionWindow(is_replay=True)``）。现在窗口自己通过
    宿主端口的 ``open_replay_file()`` 拿到数据后原地切换，因此：

    - 取消选择时**不关窗**（那一刻 ``_closing`` 仍为 False，入口页继续可用）；
    - 拿到数据后 ``is_replay`` 为真，回放到尾部时自行关闭；
    - 全程只有一个窗口生命周期，也就不该产生回放/报告输出（回放不落盘）。
    """
    files_before = _written_files()
    _HOST.replay_payload = None
    script = [("replay", "cancel"), ("sleep", 0.5), ("replay", "ok"), ("click", 4)]
    win, result = _make_window(script, explore=True)

    check("回放：宿主被问过两次文件", _HOST.replay_calls == 2, _HOST.replay_calls)
    # 「取消」那一刻：仍停在入口页，没关窗、没切回放
    snap = win._snapshots.get(("replay", "cancel"), {})
    check("回放：取消选择时不关窗", snap.get("closing") is False, snap)
    check("回放：取消选择时不切回放", snap.get("replay") is False, snap)
    check("回放：窗口已切到回放模式", win.is_replay is True)
    check("回放：入口选择记录为回放标记", win._launch_choice == REPLAY_MARK,
          win._launch_choice)
    check("回放：渲染过帧", win.frames_rendered > 0, win.frames_rendered)
    texts = "".join(item.get("text", "") for item in win.story_history)
    check("回放：放出了 fixture 的正文", "回放第一段" in texts, texts[:120])
    check("回放：结果为 session-ended",
          result.reason == "session-ended" and result.is_replay, result)
    check("回放：不落盘（回放不是新的副本）", _written_files() == files_before,
          set(_written_files()) - set(files_before))
    check("回放：像素工作者已收工", win._background.worker_alive is False)


def scene_tk_host():
    """最小 Tk 宿主：副本运行期间宿主事件循环仍在跑（L0 的核心收益）。

    手动渲染后帧由窗口自己的循环驱动，每帧顺带泵一次宿主事件；因此副本跑
    在 Tk 主窗口里时，主窗口不再"假死"。用 100ms 心跳计数验证：心跳只在
    宿主的事件循环被处理过之后才会增长。

    **故意不在会话结束后碰 Tk**：``destroy_context()`` 之后现场那个 Tk 根窗口的
    窗口级命令（``destroy()`` / ``withdraw()``）会以 0xC0000005 崩掉进程；会话收尾
    会立刻补一个隐藏保活视口把它修回来（见 ``dungeon/window/dpg_state.py`` 与
    ``base._finish_session``），这里仍保持「创建了就不销毁」的老做法，省得自检
    自己踩在修复链路上。真应用的 Tk 对话框也都在 ``destroy_context()`` 之前
    （见 base._finish_session 的顺序）。
    """
    import tkinter as tk
    root = tk.Tk()
    root.geometry("2x2+-4000+-4000")   # 挪到屏幕外，避免测试时闪窗
    root.withdraw()
    try:
        root.attributes("-alpha", 0.0)
    except Exception:
        pass

    beats = {"n": 0}

    def beat():
        beats["n"] += 1
        root.after(100, beat)

    root.after(100, beat)

    # 真宿主适配器：尺寸/DPI/显隐/事件泵全部走 ui.common.tk_host.TkHost，
    # 只有弹框换成记录器——自检里弹真模态框会一直等人点（挂死）。
    host = RecordingTkHost(root)
    win, _result = _make_window([("click", 3), ("close", None)], explore=False,
                                parent=root, host=host)

    check("Tk宿主：窗口已关闭", win._closing is True)
    check("Tk宿主：渲染过帧", win.frames_rendered > 0, win.frames_rendered)
    check("Tk宿主：帧时钟跑过任务的帧", win._frame.ticks > 0, win._frame.ticks)
    check("Tk宿主：宿主事件循环未被冻结", beats["n"] > 0, beats["n"])
    check("Tk宿主：视口尺寸取自宿主", win._main_client_w > 0, win._main_client_w)
    check("Tk宿主：宿主就是注入的适配器", win.host is host)
    check("Tk宿主：收尾提示经宿主端口", bool(host.calls), host.calls)
    # 留给 GC：主动销毁 Tk 根窗口会触发上面那条 0xC0000005
    _KEEP_ALIVE_ROOTS.append(root)


class NativeCloseTkHost(RecordingTkHost):
    """真 Tk 宿主，弹框换成记录器；额外记录「弹框时队列里是否压着 WM_QUIT」。"""

    def __init__(self, widget):
        super().__init__(widget)
        self.pending_quit_at_dialog = []

    def dialog(self, kind, title, message):
        self.pending_quit_at_dialog.append(_pending_quit())
        return super().dialog(kind, title, message)


def scene_native_close():
    """视口用**原生关闭键（X）**退出：收尾提示与宿主计时器都不得被拖死（§5-C12）。

    这是唯一一条会往主线程队列里留 ``WM_QUIT`` 的关闭路径——GLFW 销毁自己的原生
    窗口时，Windows 的 ``DefWindowProc(WM_DESTROY)`` 记下这条「退出进程」消息。
    Tk 不会吃掉它，但 Windows 从此不再合成 ``WM_TIMER``：收尾提示框的 ``tkwait``
    与主窗口的 ``after`` 计时器全部停摆，表现为「提示框弹出来，主窗口卡死」。
    三条断言锁住它：弹框那一刻队列里没有残留、会话结束后宿主计时器仍在跑、
    未完成回放照常落盘。
    """
    if not sys.platform.startswith("win"):
        print("  SKIP native-close：WM_QUIT 是 Win32 专有，非 Windows 不适用")
        return
    import tkinter as tk
    root = tk.Tk()
    root.geometry("2x2+-4000+-4000")   # 挪到屏幕外，避免测试时闪窗
    root.withdraw()
    try:
        root.attributes("-alpha", 0.0)
    except Exception:
        pass

    beats = {"n": 0}

    def beat():
        beats["n"] += 1
        root.after(100, beat)

    root.after(100, beat)

    host = NativeCloseTkHost(root)
    win, result = _make_window([("click", 3), ("native-close", None)], explore=False,
                               parent=root, host=host)

    check("原生关闭：确实把 WM_CLOSE 投给了视口", win._native_close_posted is True)
    check("原生关闭：窗口已关闭", win._closing is True)
    check("原生关闭：收尾提示弹出时队列里没有残留 WM_QUIT",
          bool(host.pending_quit_at_dialog) and not any(host.pending_quit_at_dialog),
          host.pending_quit_at_dialog)
    check("原生关闭：会话结束后队列里也没有 WM_QUIT", _pending_quit() == 0)
    files = _written_files()
    replays = [f for f in files if f.startswith(os.path.join("user", "replays"))]
    check("原生关闭：落盘了未完成回放", any("_未完成" in f for f in replays), replays)
    check("原生关闭：结果为 session-ended 且 succeeded",
          result.reason == "session-ended" and result.succeeded, result)
    # 用户可见的症状就是「宿主计时器不再跳」：这里真的泵一段事件循环去验证
    before = beats["n"]
    deadline = time.time() + 0.8
    while time.time() < deadline:
        root.update()
        time.sleep(0.02)
    check("原生关闭：会话结束后宿主计时器仍在跑", beats["n"] > before,
          (before, beats["n"]))
    # 留给 GC：主动销毁 Tk 根窗口会触发 destroy_context 之后的 0xC0000005
    _KEEP_ALIVE_ROOTS.append(root)


def _check_owned_display(cid):
    """会话中途（主线程）验证组件接管文本显示的可见状态。

    DPG 上下文在 run() 返回后销毁，接管/隐藏这类「那一帧」的状态只能在这里断言。
    child_window 的 item state 不含 ``visible``，用 configuration 的 ``show`` 判断。
    """
    _OWN_TAG = {"text": "text_gradient_back", "text_card": "text_card_box",
                "text_nvl": "text_nvl_overlay"}

    def _shown(tag):
        import dearpygui.dearpygui as dpg
        return dpg.get_item_configuration(tag).get("show", True)

    def _probe(win):
        import dearpygui.dearpygui as dpg
        comps = {c.id: c for c in win._components}
        assert cid in comps, f"组件未构建: {list(comps)}"
        assert getattr(comps[cid], "owns_text_display", False), "应声明接管文本显示"
        assert not _shown("text_container"), "内置文本容器应被接管隐藏"
        tag = _OWN_TAG[cid]
        assert dpg.does_item_exist(tag) and _shown(tag), \
            f"接管容器 {tag} 应存在且可见"
        comp = comps[cid]
        if cid == "text_nvl":
            # 全屏 NVL 全历史堆叠：行对池应与历史条目一一对应
            assert len(comp._line_tags) == len(win.story_history), \
                (len(comp._line_tags), len(win.story_history))
        else:
            assert len(comp._line_tags) >= 1, "行对池未创建"

    _probe.__name__ = f"owned-display:{cid}"
    return _probe


def _check_text_component(cid, extra_ids=()):
    """会话中途（主线程）验证组件构建顺序：文本主组件在前、其余组件在后。"""
    def _probe(win):
        ids = [c.id for c in win._components]
        assert ids == [cid] + list(extra_ids), f"组件构建结果: {ids}"

    _probe.__name__ = f"text-component:{cid}"
    return _probe


def _check_overlay_service(win):
    """服务面探针：覆盖层调出 → 阅读模态挂起推进 → 关闭恢复 → 兄弟组件只读。"""
    import dearpygui.dearpygui as dpg
    from dungeon.splitter import DisplayUnit

    def _shown(tag):
        return dpg.get_item_configuration(tag).get("show", True)

    assert win.overlay_open() is None, "初始不应有覆盖层"
    win.toggle_overlay("log")
    assert win.overlay_open() == "log", win.overlay_open()
    assert dpg.does_item_exist("overlay_log") and _shown("overlay_log"), \
        "对话记录覆盖层应存在且可见"
    # 阅读模态：未揭示句在覆盖层打开时不被消费（点击/空格都挂起）
    win._pending_units = [DisplayUnit(speaker=None, text="覆盖层挂起验证。")]
    win._on_next_step()
    assert len(win._pending_units) == 1, "覆盖层打开时推进应被挂起"
    # 关闭后恢复推进，覆盖层控件删除
    win.close_overlay()
    assert win.overlay_open() is None and not dpg.does_item_exist("overlay_log")
    win._on_next_step()
    assert len(win._pending_units) == 0, "覆盖层关闭后应恢复逐句揭示"
    # 兄弟组件只读访问（服务面 component(cid)）
    assert win.component("attr_bar") is not None, "attr_bar 实例应可读"
    assert win.component("ghost") is None, "未知组件应返回 None"
    # 再开关一轮确认 toggle 幂等
    win.toggle_overlay("log")
    win.toggle_overlay("log")
    assert win.overlay_open() is None, "toggle 两次应回到关闭态"

_check_overlay_service.__name__ = "overlay-service"


def scene_text_components():
    """文本组件家族冒烟：text_card / text_nvl 接管显示 + text_component 字段。"""
    for cid in ("text_card", "text_nvl"):
        win, result = _make_window(
            [("click", 3), ("sleep", 0.5), ("check", _check_owned_display(cid)),
             ("close", None)],
            explore=False,
            config_overrides={"text_component": cid, "components": [],
                              "components_params": {}})
        check(f"{cid}：结果 succeeded", result.succeeded and not result.failed, result)
        check(f"{cid}：退出时组件已销毁", win._components == [], win._components)
        failures = [d for s, d in win._check_results if s != "ok"]
        check(f"{cid}：会话中途接管断言通过", bool(win._check_results) and not failures,
              failures)

    # text_component 字段（三选一）：配置 text_card 时按它构建
    win, result = _make_window(
        [("click", 2), ("check", _check_text_component("text_card")), ("close", None)],
        explore=False,
        config_overrides={"text_component": "text_card", "components": [],
                          "components_params": {}})
    check("字段：text_card 三选一生效",
          bool(win._check_results) and all(s == "ok" for s, _ in win._check_results),
          win._check_results)

    # 旧写法兼容：text_component 缺失时，components 列表里的家族成员被提升
    win, result = _make_window(
        [("click", 2),
         ("check", _check_text_component("text_nvl", ["attr_bar"])), ("close", None)],
        explore=False,
        config_overrides={"text_component": None,
                          "components": ["attr_bar", "text_nvl"],
                          "components_params": {}})
    check("兼容：旧写法提升 text_nvl",
          bool(win._check_results) and all(s == "ok" for s, _ in win._check_results),
          win._check_results)

    # 服务面：覆盖层调出/阅读模态挂起/关闭恢复 + 兄弟组件只读访问
    win, result = _make_window(
        [("click", 2), ("sleep", 0.5), ("check", _check_overlay_service),
         ("close", None)],
        explore=False,
        config_overrides={"text_component": "text",
                          "components": ["attr_bar"],
                          "components_params": {}})
    check("服务面：覆盖层与挂起断言通过",
          bool(win._check_results) and all(s == "ok" for s, _ in win._check_results),
          win._check_results)


SCENES = {
    "callback-thread": scene_callback_thread,
    "chapter-bgm": scene_chapter_bgm,
    "chapter-voice-fx": scene_chapter_voice_fx,
    "dialogue-voice": scene_dialogue_voice,
    "session-close": scene_session_close,
    "entry-cancel": scene_entry_cancel,
    "entry-start": scene_entry_start,
    "entry-replay": scene_entry_replay,
    "tk-host": scene_tk_host,
    "native-close": scene_native_close,
    "text-components": scene_text_components,
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


def _drain_output(proc, sink):
    """在独立线程里把子进程输出逐行收进 ``sink``。

    ``readline`` 在管道上没有超时：一旦子进程停止出字（如 DPG 渲染占住 GIL），
    主循环里再怎么检查 deadline 都会被它无限期吊住——正是文档要求「父进程兜
    超时」要防的事。读操作挪到 daemon 线程后，主线程的等待才是真正有界的。
    """
    try:
        for line in iter(proc.stdout.readline, ""):
            sink.append(line)
    except Exception:
        pass


def _run_isolated(names, timeout, repeat=1, require_clean_exit=False):
    """每个场景一个子进程：崩溃/挂死只影响子进程，主进程能定位到具体场景。

    判定用子进程打印的结论行而不是退出码——历史上确实出现过"跑完会话后进程在
    **退出阶段**挂死"（`os._exit` 都绕不过，见 window_host.md
    §5.3/§5.4：那是长时会话累积出来的 OS/驱动级状态问题，重启即消失）。
    但**不要**看到结论行就立刻断定挂死：实测正常子进程在结论行之后 0.1s 内就
    自行退出，立即 `poll()` 会把正常退出误判成挂死（2026-09-23 修正为宽限
    ``EXIT_GRACE`` 秒）。只有宽限过后仍未退出才算挂死，此时按结论判定成败。

    三件事分开汇报，不混成一个「通过」：场景断言（上面的两个 check）、
    子进程退出码、退出方式（自行退出 / 超时未出结论 / 结论已出但退出挂死）。
    默认退出码与挂死只汇报不判败（文档 §6 认定退出挂死是环境现象）；
    ``require_clean_exit=True`` 时把「未自行干净退出」也判为失败，供环境
    复测（重启系统后）确认退出路径真的修好。

    ``repeat > 1`` 时把重复次数交给子进程内联执行——同一进程里连续开关窗口
    才是"重开副本"的回归形状（上一轮 stop 掉的调度队列必须重新启用）。
    """
    import subprocess
    for name in names:
        cmd = [sys.executable, os.path.abspath(__file__), "--scene", name]
        if repeat > 1:
            cmd += ["--repeat", str(repeat)]
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace")
        buf = []
        reader = threading.Thread(
            target=_drain_output, args=(proc, buf), daemon=True)
        reader.start()
        deadline = time.time() + timeout * (repeat if repeat > 1 else 1)
        while time.time() < deadline:
            if any("report:" in b for b in buf):   # 结论已输出
                break
            if proc.poll() is not None and not reader.is_alive():
                break
            time.sleep(0.1)
        report_seen = any("report:" in b for b in buf)
        # 结论行出现后给一小段退出宽限：正常子进程 0.1s 内就退出了，
        # 立即 poll 会把正常退出误判成挂死（2026-09-23 修正）。
        grace_deadline = time.time() + EXIT_GRACE
        while time.time() < grace_deadline and proc.poll() is None:
            time.sleep(0.05)
        hung = proc.poll() is None
        if hung:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
        out = "".join(buf)
        print(f"  [{name}] 输出: {out.strip()[-400:]}")
        if not report_seen and hung:
            print(f"  [{name}] 超时（{timeout}s）未给出结论，已强制结束")
        elif not report_seen:
            print(f"  [{name}] 子进程未见结论即退出 rc={proc.returncode}")
        elif hung:
            print(f"  [{name}] 结论已出，但进程在退出阶段挂死"
                  f"（已知环境现象，见 window_host.md §6），已强制结束")
        else:
            print(f"  [{name}] 子进程自行退出 rc={proc.returncode}")
        check(f"场景 {name} 在 {timeout}s 内给出结论", report_seen, out[-300:])
        check(f"场景 {name} 断言全过", "[dungeon_autopilot] PASSED" in out, out[-300:])
        if require_clean_exit:
            check(f"场景 {name} 自行干净退出（rc={proc.returncode}）",
                  report_seen and not hung and proc.returncode == 0, out[-300:])


def main():
    parser = argparse.ArgumentParser(description="副本窗口自动驾驶自检")
    parser.add_argument("--scene", choices=sorted(SCENES) + ["all"], default="all")
    parser.add_argument("--repeat", type=int, default=1,
                        help="每个场景重复次数（>1 用于回归开关窗口的关闭路径）")
    parser.add_argument("--in-process", action="store_true",
                        help="不开子进程、在当前进程内依次跑场景（默认：全部场景时才开子进程）")
    parser.add_argument("--isolate", action="store_true",
                        help="强制在子进程里跑（单场景也能用）：进程退出挂死时由父进程超时 kill")
    parser.add_argument("--timeout", type=int, default=40,
                        help="硬看门狗秒数：超时即自杀，避免窗口卡在屏幕上")
    parser.add_argument("--require-clean-exit", action="store_true",
                        help="把「未自行干净退出」（超时 / 退出挂死 / 非零退出码）也判为失败；"
                             "默认只汇报，因为退出挂死按 window_host.md §6 属于环境现象，"
                             "不作失败信号。适用于重启系统后复测退出路径")
    args = parser.parse_args()

    names = sorted(SCENES) if args.scene == "all" else [args.scene]
    # 全部场景默认开子进程；单场景想在子进程里跑（推荐：退出挂死由父进程兜底）用 --isolate
    if args.isolate or (args.scene == "all" and not args.in_process):
        _run_isolated(names, args.timeout, args.repeat,
                      require_clean_exit=args.require_clean_exit)
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
    # 结论行当场冲出缓冲：父进程按这行判定成败，而管道 stdout 是块缓冲，默认的
    # flush 排在解释器退出收尾的最后——若 DPG/GLFW 的退出清理卡住（文档 §6 的
    # 「退出挂死」），结论行会困在缓冲里，父进程就会把「已通过但退出挂死」
    # 误判成「超时未出结论」。
    sys.__stdout__.flush()
    sys.__stderr__.flush()
    # 走正常解释器退出（sys.exit），让 DPG 有机会完成自己的收尾；
    # 若仍有残留的 native 线程导致退出挂死，由外层 --isolate 的超时兜底。
    _log.flush()
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
