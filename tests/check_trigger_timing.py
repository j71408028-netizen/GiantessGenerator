"""自检：触发判定时序（位置顺序 / 同位配额 / 轮次防重发）+ 窗口视效参数。

用法：``python tests/check_trigger_timing.py``

无 GUI：视效的 DPG 上屏路径不在覆盖范围，这里只守纯逻辑——
1. 动作注册表：``fx`` 在 NEW_ACTIONS；WINDOW_FX 键齐全；位置表 ACTION_POSITIONS
   的顺序与分组（fx → 模态 → goto → effect → insert → none）；
2. ``parse_fx_params`` 夹紧：解析失败 / 越界都回到合法区间；
3. 判定顺序：一轮判定按位置顺序派发，同位置至多一个生效（配置序决定谁赢）；
4. 轮次防重发：结算重判（不 _begin_trigger_round 直接重判）共享本轮已触发
   集合——同一触发器不重发，其他触发器可接力；开新轮后恢复；
5. 旧版 ending：运行时不再执行（走退场提示路径，不启动结局生成）；
6. 回放复现：fx 记录按解析后的参数原样重放；
7. 校验器：fx 配置错误产出逐字段 warning；旧版 ending 报 LEGACY warning。
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_report_path = os.path.join(tempfile.mkdtemp(prefix="trigger_timing_"), "report.txt")
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
check("位置表顺序与分组", actions_mod.ACTION_POSITIONS
      == (("fx",), ("option", "mini_game"), ("goto",),
          ("effect",), ("insert",), ("none",)),
      str(actions_mod.ACTION_POSITIONS))

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

# ---------------- 3-4. 判定顺序 / 同位配额 / 轮次防重发 ----------------
class _State:
    steps_since_trigger = 0
    total_steps = 0


class _Dummy(TriggerHandler):
    """触发判定的最小宿主：条件恒真，动作只记录不执行。"""

    def __init__(self, triggers):
        self.triggers = triggers
        self.triggered_names = set()
        self.fired_triggers = set()
        self._round_fired = set()
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
        self.ending_calls = []

    def evaluate_condition(self, condition, state):
        return True

    def _enter_chapter(self, name, record=False, **kw):
        self.current_chapter = name
        self.goto_calls.append(name)
        return True

    def _start_ending_generation(self):
        self.ending_calls.append(True)

    def _start_option_generation(self):
        pass

    def start(self, fx_key, duration, intensity):
        self.fx_calls.append((fx_key, duration, intensity))


TRIGGERS = [
    {"name": "fx一", "action_type": "fx",
     "action_data": {"type": "fx", "fx": "flash", "duration": 0.5, "intensity": 60},
     "repeatable": True},
    {"name": "fx二", "action_type": "fx",
     "action_data": {"type": "fx", "fx": "shake", "duration": 0.5, "intensity": 60},
     "repeatable": True},
    {"name": "选项甲", "action_type": "option",
     "action_data": {"type": "option", "options": [{"id": 0, "prompt": "a"},
                                                   {"id": 1, "prompt": "b"}]},
     "repeatable": True},
    {"name": "选项乙", "action_type": "option",
     "action_data": {"type": "option", "options": [{"id": 0, "prompt": "a"},
                                                   {"id": 1, "prompt": "b"}]},
     "repeatable": True},
    {"name": "跳转", "action_type": "goto",
     "action_data": {"type": "goto", "chapter": "X"}, "repeatable": True},
    {"name": "滤镜", "action_type": "effect",
     "action_data": {"type": "effect", "filter": "blur", "duration": 2},
     "repeatable": True},
    {"name": "插入", "action_type": "insert",
     "action_data": {"type": "insert", "text": "……", "text_type": "background"},
     "repeatable": True},
    {"name": "标记", "action_type": "none", "action_data": {"type": "none"},
     "repeatable": True},
]

dummy = _Dummy(TRIGGERS)
dummy._window_fx = dummy
dummy._begin_trigger_round()
dummy.check_triggers()
check("位置顺序派发：fx → 模态 → goto → effect → insert → none",
      dummy.fx_calls and dummy.goto_calls == ["X"] and dummy.visual_effects
      and dummy.pending_insertions,
      f"fx={dummy.fx_calls} goto={dummy.goto_calls}")
check("同位置至多一个（配置序决定谁赢）",
      len(dummy.fx_calls) == 1 and dummy._round_fired == {"fx一"} | {"选项甲", "跳转", "滤镜", "插入", "标记"},
      str(dummy._round_fired))

# 结算重判（同一轮）：同一触发器不重发，其他触发器接力
dummy.pending_option = None   # 模拟选项甲结算
dummy.check_triggers()
check("结算重判：选项甲不重发，选项乙接力，fx二 顶上",
      dummy._round_fired == {"fx一", "fx二", "选项甲", "选项乙", "跳转", "滤镜", "插入", "标记"}
      and len(dummy.fx_calls) == 2 and dummy.fx_calls[1][0] == "shake",
      f"fired={dummy._round_fired} fx={dummy.fx_calls}")

# 开新轮：全体可再次触发（跳转因已处于章节 X 被跳过）
dummy.pending_option = None
dummy._begin_trigger_round()
dummy.check_triggers()
check("新轮恢复：fx一重新生效", dummy._round_fired == {"fx一"} | {"选项甲", "滤镜", "插入", "标记"}
      and dummy.fx_calls[-1][0] == "flash",
      str(dummy._round_fired))

# 不可再触发的触发器不进新一轮
dummy2 = _Dummy([{"name": "一次性", "action_type": "fx",
                  "action_data": {"type": "fx", "fx": "flash"}, "repeatable": False}])
dummy2._window_fx = dummy2
dummy2._begin_trigger_round()
dummy2.check_triggers()
dummy2._begin_trigger_round()
dummy2.check_triggers()
check("不可再触发：第二轮起跳过", len(dummy2.fx_calls) == 1
      and "一次性" in dummy2.triggered_names)

# ---------------- 5. 旧版 ending 退场 ----------------
legacy = _Dummy([{"name": "旧结局", "action_type": "ending",
                  "action_data": {"type": "ending", "name": "终局"}, "repeatable": True}])
legacy._window_fx = legacy
legacy._begin_trigger_round()
legacy.check_triggers()
check("旧版 ending 不再执行（不启动结局生成）", legacy.ending_calls == []
      and legacy.pending_ending is None and not legacy._round_fired)

# ---------------- 6. 回放复现 ----------------
replay = _Dummy([])
replay._window_fx = replay
replay._replay_trigger({"name": "窗", "action_type": "fx",
                        "action_data": {"fx": "invert", "duration": 2, "intensity": 90}})
check("回放复现窗口视效", replay.fx_calls == [("invert", 2.0, 90.0)], str(replay.fx_calls))

# ---------------- 7. 校验器 ----------------
diags = validate_scenario_config({"chapters": [], "triggers": [
    {"name": "a", "action_type": "fx",
     "action_data": {"type": "fx", "fx": "flash", "duration": 2, "intensity": 50}},
    {"name": "b", "action_type": "fx",
     "action_data": {"type": "fx", "fx": "nope", "duration": 99, "intensity": -3}},
    {"name": "c", "action_type": "ending", "action_data": {"type": "ending", "name": "终局"}},
]})
msgs = [(d.level, d.path) for d in diags]
check("正确配置零误报", not any(p.startswith("triggers[0]") for _l, p in msgs), str(msgs))
check("未知视效键有诊断", any(l == "warning" and p == "triggers[1].action_data.fx"
                             for l, p in msgs), str(msgs))
check("时长越界有诊断", any(p == "triggers[1].action_data.duration" for _l, p in msgs), str(msgs))
check("强度越界有诊断", any(p == "triggers[1].action_data.intensity" for _l, p in msgs), str(msgs))
check("旧版 ending 报 LEGACY warning", any(l == "warning" and p == "triggers[2].action"
                                          for l, p in msgs), str(msgs))

# ---------------- 汇总 ----------------
_log.close()
sys.stdout = sys.__stdout__
print(f"[check_trigger_timing] {'PASSED' if not failures else 'FAILED'} {total - len(failures)}/{total}")
print(f"[check_trigger_timing] report: {_report_path}")
sys.exit(1 if failures else 0)
