"""副本 S1 收尾路径与原子写自检（无 GUI，不触碰真实 data/ 目录）。

用法：``python scripts/check_dungeon_finalize.py``
输出：控制台一行 ASCII 结论 + UTF-8 报告文件路径（报告在临时目录，含全部明细）。

覆盖：
1. ``persistence/json_store``：原子写、覆盖前留 .bak、损坏回退、无残留 .tmp；
2. ``persistence/scenario_repo``：正常读写 + 主文件损坏时自愈读 .bak；
3. ``DungeonPersistence._finalize``：未完成路径（落盘「未完成」回放与报告、
   不写 endings.json、不记结局索引、幂等、异常汇总）与完成路径（结算 + 结局索引）。
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_report_path = os.path.join(tempfile.mkdtemp(prefix="dungeon_finalize_check_"), "report.txt")
_log = open(_report_path, "w", encoding="utf-8")
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


# ---------------- 1. 原子写 + .bak ----------------
from persistence.json_store import (  # noqa: E402
    load_json_with_backup, write_json_atomic, write_text_atomic)

tmp = tempfile.mkdtemp(prefix="dungeon_finalize_")
p = os.path.join(tmp, "config.json")
write_json_atomic(p, {"a": 1})
check("首次写入内容正确", json.load(open(p, encoding="utf-8")) == {"a": 1})
check("无残留 .tmp", not os.path.exists(p + ".tmp"))
write_json_atomic(p, {"a": 2})
check("覆盖后主文件为新内容", json.load(open(p, encoding="utf-8")) == {"a": 2})
check("覆盖前留了一份 .bak", json.load(open(p + ".bak", encoding="utf-8")) == {"a": 1})

with open(p, "w", encoding="utf-8") as f:
    f.write("{\u534a\u622a\u574f\u6587\u4ef6")
check("主文件损坏时回退到 .bak", load_json_with_backup(p) == {"a": 1})
check("两者都不可用时返回 None",
      load_json_with_backup(os.path.join(tmp, "\u4e0d\u5b58\u5728.json")) is None)

tp = os.path.join(tmp, "report.txt")
write_text_atomic(tp, "\u4f60\u597d")
check("文本原子写", open(tp, encoding="utf-8").read() == "\u4f60\u597d")

# ---------------- 2. scenario_repo 读写 ----------------
from persistence.scenario_repo import ScenarioRepo  # noqa: E402

repo_dir = os.path.join(tmp, "data")
repo = ScenarioRepo(data_dir=repo_dir)
repo.save_config("_default", {"initial_prompt": "v1"})
repo.save_config("_default", {"initial_prompt": "v2"})
check("repo 正常读取新内容", repo.load_config("_default")["initial_prompt"] == "v2")
config_path = os.path.join(repo_dir, "packs", "scenarios", "_default", "config.json")
with open(config_path, "w", encoding="utf-8") as f:
    f.write("{\u5199\u574f\u4e86")
check("repo 自愈读取 .bak", repo.load_config("_default")["initial_prompt"] == "v1")

# ---------------- 3. _finalize 三态 ----------------
import dungeon.window.persistence as persp  # noqa: E402
from dungeon.window.host import DIALOG_ASK, HostPort  # noqa: E402


class RecordingHost(HostPort):
    """记录收尾弹框；询问类一律回答「否」。

    L2 之后 window 层只经宿主端口弹框（``dungeon/window/host.py``），
    自检因此改为注入一个记录宿主，不再打桩 ``ui.common.dialogs`` 模块。
    """

    def __init__(self):
        self.calls = []   # [(kind, title, message), ...]

    def dialog(self, kind, title, message):
        self.calls.append((kind, title, message))
        return False if kind == DIALOG_ASK else None


HOST = RecordingHost()
dialog_calls = HOST.calls

# 所有落盘都改到临时目录，绝不碰真实 data/
session_root = os.path.join(tmp, "session_data")
persp.data_dir = lambda: session_root


class FakeCharacter:
    giantess_id = "测试角色_unit"
    name = "测试角色"


class FakeState:
    intrusion = 1.5
    destruction = 2.0
    custom_attrs = {"测试属性": 3.0}
    total_steps = 7
    total_casualties = 1234.0


class StubSession(persp.DungeonPersistence):
    """最小会话替身：只补齐 _finalize 依赖的状态字段。"""

    def __init__(self, with_character=True, mode="explore", errors=()):
        self.host = HOST
        self.is_replay = False
        self.mode = mode
        self.scenario_id = "unit_test"
        self.name = "测试角色"
        self.settings = {}
        self.height = 100.0
        self.ending_text = ""
        self.ending_effects = {}
        self.ending_icon_path = ""
        self._ending_trigger_index = -1
        self._achievement_record = None
        self._replay_saved = False
        self._finalized = False
        self._session_errors = list(errors)
        self._last_ending_record = None
        self._ending_thread = None
        self.pending_ending = None
        self.dungeon_ended = False
        self.character = FakeCharacter() if with_character else None
        self.character_repo = None
        self.dungeon_state = FakeState()
        self.story_history = [{"type_str": "【对话】", "text": "你好"}]
        self.replay_data = [{"step": 1, "type": "dialog", "text": "你好"}]


# 3.1 未完成 + 有角色（探索模式）→ 写角色档案目录的「未完成」回放与报告
stub = StubSession()
stub._finalize(completed=False, reason="未触发结局就退出")
replay_dir = os.path.join(session_root, "archives", "测试角色_unit", "回放")
files = os.listdir(replay_dir) if os.path.exists(replay_dir) else []
check("未完成回放已落盘", len(files) == 1, files)
check("回放文件名带「未完成」且后缀不变",
      bool(files) and "_未完成" in files[0] and files[0].endswith(".replay.json"), files)
replay_payload = json.load(open(os.path.join(replay_dir, files[0]), encoding="utf-8"))
check("回放内容仍是记录数组", replay_payload == stub.replay_data)
report_dir = os.path.join(session_root, "archives", "测试角色_unit", "报告")
reports = os.listdir(report_dir) if os.path.exists(report_dir) else []
report_text = open(os.path.join(report_dir, reports[0]), encoding="utf-8").read() if reports else ""
check("未完成报告已落盘且标注未完成",
      bool(reports) and "【未完成】" in report_text and "未触发结局就退出" in report_text, reports)
check("未完成不写 endings.json",
      not os.path.exists(os.path.join(session_root, "user", "endings.json")))
check("未完成不记结局索引", stub._achievement_record is None)
check("未完成退出有提示", any(c[0] == "info" and "未完成" in c[1] for c in dialog_calls))
check("未完成标记已记录（避免重复落盘）", stub._replay_saved and stub._finalized)

# 3.2 幂等：再次调用不产生第二份文件
stub._finalize(completed=False, reason="重复调用")
check("_finalize 幂等", len(os.listdir(replay_dir)) == 1, os.listdir(replay_dir))

# 3.3 未完成 + 无角色 → 落 data/user
dialog_calls.clear()
stub2 = StubSession(with_character=False)
stub2._finalize(completed=False, reason="未触发结局就退出")
user_replays = os.path.join(session_root, "user", "replays")
user_reports = os.path.join(session_root, "user", "reports")
check("无角色时回放写 data/user/replays",
      os.path.exists(user_replays) and any("_未完成" in f for f in os.listdir(user_replays)))
check("无角色时报告写 data/user/reports",
      os.path.exists(user_reports) and any("_未完成" in f for f in os.listdir(user_reports)))

# 3.4 完全没有内容 → 不落盘、只提示
dialog_calls.clear()
stub3 = StubSession()
stub3.replay_data = []
stub3.story_history = []
stub3._finalize(completed=False, reason="未触发结局就退出")
check("空会话不落盘", len(os.listdir(replay_dir)) == 1, os.listdir(replay_dir))
check("空会话有提示", any(c[0] == "info" and "没有生成任何内容" in c[2] for c in dialog_calls))

# 3.5 会话异常汇总进未完成报告
stub4 = StubSession(errors=["ValueError: 模型返回空", "TimeoutError: 超时"])
stub4._finalize(completed=False, reason="未触发结局就退出")
reports = sorted(os.listdir(report_dir))
latest = open(os.path.join(report_dir, reports[-1]), encoding="utf-8").read()
check("异常汇总进报告头", "2 次生成异常" in latest and "TimeoutError" in latest)

# 3.6 正常结局：结算 + 结局索引 + 不自动存回放（开关关闭）
stub5 = StubSession(mode="challenge")
calls = []
stub5._apply_ending_effects = lambda: calls.append("effects")
stub5._record_ending_achievement = lambda: calls.append("achievement")
stub5._finalize(completed=True, reason="触发结局")
check("完成路径执行结算与结局索引", calls == ["effects", "achievement"], calls)
check("完成路径置 dungeon_ended", stub5.dungeon_ended is True)
check("自动保存关闭时不写回放", stub5._replay_saved is False)

# 3.7 完成路径幂等
stub5._finalize(completed=True)
check("完成路径幂等", calls == ["effects", "achievement"], calls)

# 3.8 _handle_exit 分流（不真正落盘：只记录 _finalize 的调用参数）
class ExitStub(StubSession):
    def _finalize(self, completed, reason=""):
        self.finalize_calls.append((completed, reason))


def exit_stub(dungeon_ended=False, pending_ending=None, is_replay=False):
    stub = ExitStub()
    stub.dungeon_ended = dungeon_ended
    stub.pending_ending = pending_ending
    stub.is_replay = is_replay
    stub.finalize_calls = []
    return stub


ex = exit_stub(pending_ending=None)
ex._handle_exit()
check("退出：未触发结局 → completed=False",
      ex.finalize_calls == [(False, "未触发结局就退出")], ex.finalize_calls)

ex = exit_stub(pending_ending={"name": "真结局"})
ex._handle_exit()
check("退出：结局生成中 → 按 completed=True 收尾",
      len(ex.finalize_calls) == 1 and ex.finalize_calls[0][0] is True, ex.finalize_calls)
check("退出：结局生成中补兜底结局文本", ex.ending_text == "结局：真结局", ex.ending_text)

ex = exit_stub(dungeon_ended=True)
ex._handle_exit()
check("退出：已达成结局不重复收尾", ex.finalize_calls == [], ex.finalize_calls)

ex = exit_stub(pending_ending=None, is_replay=True)
ex._handle_exit()
check("退出：回放模式跳过收尾", ex.finalize_calls == [], ex.finalize_calls)

# ---------------- 结论 ----------------
_log.flush()
sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__
if failures:
    print(f"[check_dungeon_finalize] FAILED {len(failures)}/{total}: {failures}")
else:
    print(f"[check_dungeon_finalize] PASSED {total}/{total}")
print(f"[check_dungeon_finalize] report: {_report_path}")
sys.exit(1 if failures else 0)


