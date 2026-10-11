"""自检：窗口视效（fx 触发器动作，dungeon/window/fx.py）。

用法：``python tests/check_dungeon_fx.py``

无 GUI：视效的 DPG 上屏路径不在覆盖范围，这里只守纯逻辑——
1. 动作注册表：``fx`` 在 NEW_ACTIONS / ACTION_LABELS，WINDOW_FX 键与展示名齐全；
2. ``parse_fx_params`` 夹紧：解析失败 / 越界都回到合法区间；
3. 派发优先级：check_triggers 中视效类（effect / fx）先于其余动作派发；
4. 无效视效：不派发、不记入已触发集合（可再次触发语义不受影响）；
5. 回放复现：fx 记录按解析后的参数原样重放；
6. 校验器：fx 配置错误产出逐字段 warning，正确配置零误报。
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_report_path = os.path.join(tempfile.mkdtemp(prefix="dungeon_fx_"), "report.txt")
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


import dungeon.actions as actions_mod  # noqa: E402
from dungeon.validate import validate_scenario_config  # noqa: E402
from dungeon.window.fx import parse_fx_params  # noqa: E402
from dungeon.window.triggers import TriggerHandler  # noqa: E402

# ---------------- 1. 动作注册表 ----------------
check("fx 在运行时动作清单内", "fx" in actions_mod.NEW_ACTIONS)
check("fx 展示名", actions_mod.action_label("fx") == "窗口视效")
check("窗口视效清单", actions_mod.WINDOW_FX_KEYS
      == ("flash", "shake", "invert", "rgb_split"), str(actions_mod.WINDOW_FX_KEYS))
check("视效类动作清单", actions_mod.VISUAL_ACTION_TYPES == ("effect", "fx"))

# ---------------- 2. parse_fx_params 夹紧 ----------------
check("正常解析（字符串数字也接受）",
      parse_fx_params({"fx": "shake", "duration": "1.5", "intensity": 80})
      == ("shake", 1.5, 80.0))
check("缺省值", parse_fx_params({}) == ("", 0.8, 60.0))
check("解析失败回默认", parse_fx_params({"duration": "x", "intensity": None})
      == ("", 0.8, 60.0))
check("越界夹紧", parse_fx_params({"duration": 99, "intensity": -3})
      == ("", 10.0, 0.0))
check("下界夹紧", parse_fx_params({"duration": 0, "intensity": 999})
      == ("", 0.1, 100.0))

# ---------------- 3-5. 派发优先级 / 无效跳过 / 回放 ----------------
class _State:
    steps_since_trigger = 0
    total_steps = 0


class _Dummy(TriggerHandler):
    """触发判定的最小宿主：条件恒真，动作只记录不执行。"""

    def __init__(self, triggers):
        self.triggers = triggers
        self.triggered_names = set()
        self.fired_triggers = set()
        self.dungeon_state = _State()
        self.current_chapter = ""
        self.chapters = [{"name": "X"}]
        self.pending_insertions = []
        self.pending_option = None
        self.pending_mini_game = None
        self.pending_ending = None
        self.visual_effects = []
        self.replay_data = []
        self._applied_visual_filter = None
        self.current_background_path = ""
        self.trigger_choices = {}
        self.fx_calls = []
        self.goto_calls = []

    def evaluate_condition(self, condition, state):
        return True

    def _enter_chapter(self, name, record=False, **kw):
        self.goto_calls.append(name)
        return True

    def start(self, fx_key, duration, intensity):
        self.fx_calls.append((fx_key, duration, intensity))


dummy = _Dummy([
    {"name": "跳", "action_type": "goto",
     "action_data": {"type": "goto", "chapter": "X"}, "repeatable": True},
    {"name": "滤", "action_type": "effect",
     "action_data": {"type": "effect", "filter": "blur", "duration": 2}, "repeatable": True},
    {"name": "窗", "action_type": "fx",
     "action_data": {"type": "fx", "fx": "shake", "duration": "1.5", "intensity": 80},
     "repeatable": True},
])
dummy._window_fx = dummy
dummy.check_triggers()
check("短暂视效入队", dummy.visual_effects
      and dummy.visual_effects[0]["filter"] == "blur")
check("窗口视效按解析参数启动", dummy.fx_calls == [("shake", 1.5, 80.0)], str(dummy.fx_calls))
check("视效先于 goto 派发且 goto 仍执行", dummy.goto_calls == ["X"])
check("触发记录落回放", {r["name"] for r in dummy.replay_data} == {"跳", "滤", "窗"})

bad = _Dummy([{"name": "坏", "action_type": "fx",
               "action_data": {"type": "fx", "fx": "nope"}, "repeatable": True}])
bad._window_fx = bad
bad.check_triggers()
check("无效视效不启动", bad.fx_calls == [])
check("无效视效不计入已触发集合", not bad.triggered_names and not bad.fired_triggers)

replay = _Dummy([])
replay._window_fx = replay
replay._replay_trigger({"name": "窗", "action_type": "fx",
                        "action_data": {"fx": "invert", "duration": 2, "intensity": 90}})
check("回放复现窗口视效", replay.fx_calls == [("invert", 2.0, 90.0)], str(replay.fx_calls))

# ---------------- 6. 校验器 ----------------
diags = validate_scenario_config({"chapters": [], "triggers": [
    {"name": "a", "action_type": "fx",
     "action_data": {"type": "fx", "fx": "flash", "duration": 2, "intensity": 50}},
    {"name": "b", "action_type": "fx",
     "action_data": {"type": "fx", "fx": "nope", "duration": 99, "intensity": -3}},
]})
msgs = [(d.level, d.path) for d in diags]
check("正确配置零误报", not any(p.startswith("triggers[0]") for _l, p in msgs), str(msgs))
check("未知视效键有诊断", any(l == "warning" and p == "triggers[1].action_data.fx"
                             for l, p in msgs), str(msgs))
check("时长越界有诊断", any(p == "triggers[1].action_data.duration" for _l, p in msgs), str(msgs))
check("强度越界有诊断", any(p == "triggers[1].action_data.intensity" for _l, p in msgs), str(msgs))

# ---------------- 汇总 ----------------
_log.close()
sys.stdout = sys.__stdout__
print(f"[check_dungeon_fx] {'PASSED' if not failures else 'FAILED'} {total - len(failures)}/{total}")
print(f"[check_dungeon_fx] report: {_report_path}")
sys.exit(1 if failures else 0)
