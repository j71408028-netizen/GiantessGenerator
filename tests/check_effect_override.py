"""自检：章节效果短暂覆盖（``effect`` 动作）——解析 / 合成 / 恢复 / 校验 / 编辑器接线。

用法：``python tests/check_effect_override.py``

无 GUI：DPG 上屏路径不在覆盖范围，用替身承接「重刷背景 / 挪音量 / 重设语音」三个
副作用，只守纯逻辑：

1. ``dungeon/overrides.py``：``parse_overrides`` 只收**显式写出**的维度，显式清除
   （``""`` / ``{}`` / ``[]``）与「不覆盖」区分开；``invalid_filter``；
   ``active_override`` 后触发优先；``describe`` 不炸。
2. 运行时（``TriggerHandler``）：触发入队 → 每步递减 → 到期把四个维度交还章节默认；
   同名维度多条并存时后触发优先、到期逐条回退；换章清空全部覆盖。
3. 派发：多维度 ``effect`` 触发入队；未知滤镜跳过；一个维度都没写跳过。
4. 回放：按记录原样复现覆盖。
5. 校验器：无维度 / 未知滤镜 / 档位越界 / 语音预设写错 / 时长非法 各有诊断，
   正确配置与显式清除零误报。
6. 编辑器接线：滤镜标签 ↔ 键（含「清除滤镜」）、覆盖维度收集与解析口径一致。
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_report_path = os.path.join(tempfile.mkdtemp(prefix="effect_override_"), "report.txt")
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
from dungeon.overrides import (active_override, describe, invalid_filter,  # noqa: E402
                               parse_overrides)
from dungeon.validate import validate_scenario_config  # noqa: E402
from dungeon.window.ending import EndingHandler  # noqa: E402
from dungeon.window.triggers import TriggerHandler  # noqa: E402

# ---------------- 1. 覆盖模型 ----------------
_regacy = parse_overrides({"filter": "blur", "duration": 2})
check("旧配置（只写 filter）向后兼容", _regacy == {"filter": "blur"}, str(_regacy))

_full = parse_overrides({
    "filter": "smooth", "bgm_level": "2",
    "voice_fx": {"her": {"preset": "giant_boom", "intensity": 80}},
    "sensitivity": [{"attr": "破坏性", "strength": 2, "objective": 0.5}],
    "duration": 3,
})
check("多维解析（duration 不属于覆盖维度）",
      _full == {"filter": "smooth", "bgm_level": 2,
                "voice_fx": {"her": {"preset": "giant_boom", "intensity": 80}},
                "sensitivity": [{"attr": "破坏性", "strength": 2.0, "objective": 0.5}]},
      str(_full))

_clear = parse_overrides({"filter": "", "voice_fx": {}, "sensitivity": []})
check("显式清除保留空值（与不覆盖区分）",
      _clear == {"filter": "", "voice_fx": {}, "sensitivity": []}, str(_clear))
check("无字段 → 空覆盖", parse_overrides({}) == {})
check("档位越界夹取", parse_overrides({"bgm_level": 9}).get("bgm_level") == 4)
check("档位非数字忽略该维度", "bgm_level" not in parse_overrides({"bgm_level": "x"}))
check("filter=none 归一为清除", parse_overrides({"filter": "none"}) == {"filter": ""})

check("未知滤镜可识别", invalid_filter({"filter": "nope"}) == "nope")
check("空串是合法清除", invalid_filter({"filter": ""}) == "")
check("后触发优先", active_override(
    [{"remaining": 5, "overrides": {"filter": "blur"}},
     {"remaining": 2, "overrides": {"filter": "smooth"}}], "filter") == "smooth")
check("未覆盖的维度返回 None", active_override(
    [{"remaining": 2, "overrides": {"filter": "smooth"}}], "bgm_level") is None)
check("逐维度独立：下层条目仍可提供另一维度", active_override(
    [{"remaining": 5, "overrides": {"bgm_level": 2}},
     {"remaining": 2, "overrides": {"filter": "smooth"}}], "bgm_level") == 2)
check("describe 不炸", isinstance(describe(_full), str) and describe({}) == "（无）")


# ---------------- 2-4. 运行时合成 / 恢复 ----------------
class _State:
    total_steps = 0
    steps_since_trigger = 0
    chapter_steps = 0


class _Bgm:
    def __init__(self):
        self.calls = []

    def play(self, spec):
        self.calls.append(dict(spec or {}))
        self.last = dict(spec or {})


class _Speech:
    def __init__(self):
        self.effects = []

    def set_effect(self, spec=None):
        self.effects.append(dict(spec or {}))


class _Host(TriggerHandler, EndingHandler):
    """最小宿主：替身承接副作用，条件恒真，章节 A/B 各自带环境。"""

    def __init__(self, triggers=None):
        self.triggers = triggers or []
        self.triggered_names = set()
        self.fired_triggers = set()
        self._round_fired = set()
        self.dungeon_state = _State()
        self.chapters = [
            {"name": "A",
             "background": {"image_path": "a.png", "filter_effect": "blur"},
             "bgm": {"path": "a.mp3", "level": 3},
             "voice_fx": {"her": {"preset": "giant_boom", "intensity": 60}}},
            {"name": "B"},
        ]
        self.current_chapter = "A"
        self.chapter_sensitivity_effects = [{"attr": "介入度", "amount": 1.0}]
        self.chapter_voice_fx = {"her": {"preset": "giant_boom", "intensity": 60}}
        self.current_voice_fx = dict(self.chapter_voice_fx)
        self.effect_overrides = []
        self._applied_visual_filter = "blur"
        self._applied_bgm_level = 3
        self._applied_voice_fx = dict(self.chapter_voice_fx)
        self.current_background_path = "a.png"
        self.personality = None
        self.character = None
        self.replay_data = []
        self.pending_insertions = []
        self.pending_option = None
        self.pending_mini_game = None
        self.pending_ending = None
        self.trigger_choices = {}
        self.bg_calls = []
        self.fx_calls = []
        self._bgm = _Bgm()
        self._speech = _Speech()

    # --- 替身 ---
    def change_background(self, image_path, smooth, filter_effect):
        self.bg_calls.append((image_path, smooth, filter_effect))

    def evaluate_condition(self, condition, state):
        return True

    def _current_action_points(self):
        # 真实宿主在 window/base.py 提供；挑战模式无角色时返回 None
        return getattr(self.character, "action_points", None)

    def _start_option_generation(self):
        pass


host = _Host()
# 滤镜：覆盖 → 恢复
host._push_effect_override(parse_overrides({"filter": "smooth", "duration": 2}), 2)
check("滤镜覆盖立即落地",
      host._applied_visual_filter == "smooth"
      and host.bg_calls[-1] == ("a.png", False, "smooth"), str(host.bg_calls))
# 音乐档位：同曲只挪档位
host._push_effect_override(parse_overrides({"bgm_level": 2, "duration": 2}), 2)
check("档位覆盖落到 BgmPlayer（同曲不换轨）",
      host._applied_bgm_level == 2 and host._bgm.calls[-1].get("path") == "a.mp3"
      and host._bgm.calls[-1].get("level") == 2, str(host._bgm.calls[-1]))
# 语音效果：覆盖生效值，章节默认留在 chapter_voice_fx
host._push_effect_override(parse_overrides(
    {"voice_fx": {"others": {"preset": "room_echo", "intensity": 50}}, "duration": 2}), 2)
check("语音覆盖改写生效值但不动章节默认",
      host._speech.effects[-1] == {"others": {"preset": "room_echo", "intensity": 50}}
      and host.current_voice_fx == {"others": {"preset": "room_echo", "intensity": 50}}
      and host.chapter_voice_fx == {"her": {"preset": "giant_boom", "intensity": 60}},
      str(host.current_voice_fx))
# 敏感效果：覆盖替换章节默认
host._push_effect_override(parse_overrides(
    {"sensitivity": [{"attr": "破坏性", "strength": 2.0, "objective": 0.5}], "duration": 2}), 2)
check("敏感覆盖替换章节默认（性格缺省时 = 强度×客观影响）",
      host._apply_sensitivity_mods() == {"破坏性": 1.0},
      str(host._apply_sensitivity_mods()))

# 第 1 步：四条线都还在
host._apply_effect_overrides()
check("第 1 步后仍生效", host._applied_visual_filter == "smooth"
      and host._applied_bgm_level == 2
      and host._apply_sensitivity_mods() == {"破坏性": 1.0})
# 第 2 步：全部到期，交还章节默认
host._apply_effect_overrides()
check("到期后覆盖清空", host.effect_overrides == [])
check("到期后滤镜回章节默认",
      host._applied_visual_filter == "blur" and host.bg_calls[-1] == ("a.png", False, "blur"),
      str(host.bg_calls[-1]))
check("到期后档位回章节默认",
      host._applied_bgm_level == 3 and host._bgm.calls[-1].get("level") == 3,
      str(host._bgm.calls[-1]))
check("到期后语音回章节默认",
      host._speech.effects[-1] == {"her": {"preset": "giant_boom", "intensity": 60}},
      str(host._speech.effects[-1]))
check("到期后敏感回章节默认", host._apply_sensitivity_mods() == {"介入度": 1.0},
      str(host._apply_sensitivity_mods()))

# 叠加：后触发优先，到期逐条回退
host._push_effect_override(parse_overrides({"filter": "emboss", "duration": 3}), 3)
host._push_effect_override(parse_overrides({"filter": "contour", "duration": 1}), 1)
check("叠加时后触发优先", host._applied_visual_filter == "contour")
host._apply_effect_overrides()
check("上一条到期后露出下一条", host._applied_visual_filter == "emboss",
      str(host._applied_visual_filter))

# 换章：立即清除未到期覆盖，并回到新章默认
host._enter_chapter("B")
check("换章清空覆盖", host.effect_overrides == [])
check("换章后落到新章默认（B 无背景/无音乐/无语音/无敏感）",
      host._applied_visual_filter is None and host._applied_bgm_level is None
      and host.current_voice_fx == {} and host._apply_sensitivity_mods() == {})

# 回放：按记录原样复现
replay = _Host()
replay._enter_chapter("A")
replay._replay_trigger({"name": "覆盖", "action_type": "effect",
                        "action_data": {"filter": "detail", "bgm_level": 1, "duration": 3}})
check("回放复现覆盖",
      replay.effect_overrides
      and replay.effect_overrides[0]["overrides"] == {"filter": "detail", "bgm_level": 1}
      and replay._applied_visual_filter == "detail"
      and replay._applied_bgm_level == 1,
      str(replay.effect_overrides))


# ---------------- 3. 派发 ----------------
def _dispatch_host(action_data, repeatable=True):
    h = _Host([{"name": "E", "action_type": "effect", "action_data": action_data,
                "repeatable": repeatable}])
    h._begin_trigger_round()
    h.check_triggers()
    return h


ok_host = _dispatch_host({"type": "effect", "filter": "emboss", "bgm_level": 1, "duration": 2})
check("多维 effect 触发入队", len(ok_host.effect_overrides) == 1
      and ok_host.effect_overrides[0]["overrides"] == {"filter": "emboss", "bgm_level": 1}
      and ok_host.effect_overrides[0]["remaining"] == 2, str(ok_host.effect_overrides))
check("触发生效即记入本轮已触发 + 回放记录",
      "E" in ok_host._round_fired and any(r.get("action_type") == "effect"
                                          and r.get("kind") == "trigger"
                                          for r in ok_host.replay_data))

bad = _dispatch_host({"type": "effect", "filter": "nope", "duration": 2})
check("未知滤镜跳过（不入队）", bad.effect_overrides == [] and not bad._round_fired)
empty = _dispatch_host({"type": "effect", "duration": 2})
check("没有任何维度跳过", empty.effect_overrides == [] and not empty._round_fired)


# ---------------- 5. 校验器 ----------------
def _paths(config):
    return [(d.level, d.path) for d in validate_scenario_config(config)]


_bad = _paths({"chapters": [], "triggers": [
    {"name": "a", "action_type": "effect",
     "action_data": {"type": "effect", "filter": "nope", "duration": 0,
                     "bgm_level": 9,
                     "voice_fx": {"her": {"preset": "bogus"}},
                     "sensitivity": [{"strength": "x"}]}},
]})
check("未知滤镜有诊断", any(p == "triggers[0].action_data.filter" for _l, p in _bad), str(_bad))
check("时长非法有诊断", any(p == "triggers[0].action_data.duration" for _l, p in _bad), str(_bad))
check("档位越界有诊断", any(p == "triggers[0].action_data.bgm_level" for _l, p in _bad), str(_bad))
check("语音预设写错有诊断",
      any(p == "triggers[0].action_data.voice_fx.her.preset" for _l, p in _bad), str(_bad))
check("敏感条目类型错有诊断",
      any(p == "triggers[0].action_data.sensitivity[0].strength" for _l, p in _bad), str(_bad))

_empty = _paths({"chapters": [], "triggers": [
    {"name": "e", "action_type": "effect", "action_data": {"type": "effect", "duration": 2}}]})
check("缺维度有诊断", any(p == "triggers[0].action_data" for _l, p in _empty), str(_empty))

_clean = _paths({"chapters": [], "triggers": [
    {"name": "ok", "action_type": "effect",
     "action_data": {"type": "effect", "filter": "blur", "bgm_level": 2,
                     "voice_fx": {"others": {"preset": "room_echo"}}, "duration": 2}},
    {"name": "clear", "action_type": "effect",
     "action_data": {"type": "effect", "filter": "", "voice_fx": {}, "sensitivity": [],
                     "duration": 2}},
]})
check("正确配置零误报", not any(p.startswith("triggers[0]") for _l, p in _clean), str(_clean))
check("显式清除零误报", not any(p.startswith("triggers[1]") for _l, p in _clean), str(_clean))


# ---------------- 6. 编辑器接线 ----------------
from dungeon.audio.voice_fx import PRESET_NONE, label_of, preset_keys_for, SLOTS  # noqa: E402
from ui.scenario.trigger_dlg import TriggerEditDialog  # noqa: E402


class _Var:
    """tk 变量的最小替身（只要 get/set）。"""

    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


_dialog = object.__new__(TriggerEditDialog)   # 跳过 __init__：不建 Tk 窗口
check("滤镜标签 → 键", TriggerEditDialog._effect_filter_label_to_key(_dialog, "模糊") == "blur")
check("清除滤镜标签 → 空串",
      TriggerEditDialog._effect_filter_label_to_key(_dialog, "（清除滤镜）") == "")
check("键 → 标签", TriggerEditDialog._effect_filter_key_to_label(_dialog, "blur") == "模糊")
check("空串 / 未知键 → 清除滤镜标签",
      TriggerEditDialog._effect_filter_key_to_label(_dialog, "") == "（清除滤镜）"
      and TriggerEditDialog._effect_filter_key_to_label(_dialog, "nope") == "（清除滤镜）")

_dialog.effect_filter_on = _Var(True)
_dialog.effect_filter_var = _Var("浮雕")
_dialog.effect_bgm_on = _Var(True)
_dialog.effect_bgm_level_var = _Var("2 · 轻")
_dialog.effect_voice_on = _Var(True)
_dialog._effect_fx_label_keys = {slot: {label_of(key): key for key in preset_keys_for(slot)}
                                 for slot in SLOTS}
_dialog._effect_fx_vars = {
    "her": (_Var(label_of("giant_boom")), _Var(80)),
    "others": (_Var(label_of(PRESET_NONE)), _Var(0)),
}
_dialog.effect_sens_on = _Var(True)
_dialog.effect_sens_rows = [{"attr_var": _Var("破坏性"),
                            "strength_var": _Var("2"),
                            "objective_var": _Var("0.5")}]
collected = TriggerEditDialog._collect_effect_overrides(_dialog)
check("编辑器收集覆盖维度", collected == {
    "filter": "emboss", "bgm_level": 2,
    "voice_fx": {"her": {"preset": "giant_boom", "intensity": 80}},
    "sensitivity": [{"attr": "破坏性", "strength": 2.0, "objective": 0.5}]}, str(collected))
check("编辑器产出可被解析器原样接受（口径一致）",
      parse_overrides(dict(collected, duration=1)) == collected)

_dialog.effect_voice_on = _Var(True)
_dialog._effect_fx_vars = {"her": (_Var(label_of(PRESET_NONE)), _Var(0)),
                           "others": (_Var(label_of(PRESET_NONE)), _Var(0))}
check("两档都「不加效果」收集为空对象（显式清除而非不覆盖）",
      TriggerEditDialog._collect_effect_overrides(_dialog).get("voice_fx") == {})
check("effect 展示名与运行时一致",
      actions_mod.action_label("effect") == "效果覆盖")

# ---------------- 汇总 ----------------
_log.close()
sys.stdout = sys.__stdout__
print(f"[check_effect_override] {'PASSED' if not failures else 'FAILED'} "
      f"{total - len(failures)}/{total}")
print(f"[check_effect_override] report: {_report_path}")
sys.exit(1 if failures else 0)
