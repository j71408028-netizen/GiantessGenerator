# -*- coding: utf-8 -*-
"""离线恢复计划自检：计划/执行分离、行为包覆盖与旧入口兼容。

用法：``python tests/check_offline_plan.py``
只跑纯逻辑，不建窗口、不联网、不碰 data/。
"""

import datetime
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_report_path = os.path.join(
    tempfile.mkdtemp(prefix="offline_plan_"), "report.txt")
_log = open(_report_path, "w", encoding="utf-8")
sys.stdout = _log
sys.stderr = _log

failures = []
total = 0


def check(name, ok, extra=""):
    global total
    total += 1
    print(("  OK   " if ok else "  FAIL ") + name
          + (f"  <{extra}>" if extra and not ok else ""))
    if not ok:
        failures.append(name)


from core.behavior_runtime import get_runtime  # noqa: E402
from core.models import (  # noqa: E402
    CharacterSnapshot, EvolutionRecord, OfflineDetail, OfflineRecoveryPlan,
)
from services.character.offline import (  # noqa: E402
    OfflineModel, OfflineService, apply_recover_plan, build_recover_plan,
)
from services.state_service import StateService  # noqa: E402

_NOW = datetime.datetime(2026, 10, 24, 12, 0, 0)
_RUNTIME = get_runtime()
_RUNTIME.reset()


def _snap(action_points=0, coords=(1.0, 1.0), hours=8.0):
    snap = CharacterSnapshot(giantess_id="g", name="t", height=100.0)
    snap.updated_at = (_NOW - datetime.timedelta(hours=hours)).isoformat()
    snap.action_points = action_points
    snap.evolution = [EvolutionRecord(
        changed_at=(_NOW - datetime.timedelta(hours=12)).isoformat(),
        step=1.0, intrusion=coords[0], destruction=coords[1],
        casualties=0.0, source="t")]
    return snap


def _detail():
    return OfflineDetail(start_at=_NOW.isoformat(), end_at=_NOW.isoformat(),
                         step=1.0, casualties=1.0, env_factor=1.0)


# ---------------- 1. 计划是纯函数 ----------------
state = _snap()
before = (state.action_points, state.intrusion, state.destruction,
          state.total_casualties, len(state.evolution),
          list(state.offline_details))
plan = build_recover_plan(state, _NOW, OfflineModel())
after = (state.action_points, state.intrusion, state.destruction,
         state.total_casualties, len(state.evolution),
         list(state.offline_details))
check("默认计划生成成功", isinstance(plan, OfflineRecoveryPlan))
check("生成计划不修改状态", before == after)
check("默认计划含明细与步进", bool(plan.details) and plan.step > 0)

# ---------------- 2. 计划执行写回 ----------------
state = _snap()
apply_recover_plan(state, OfflineRecoveryPlan(
    action_point_gain=3, intrusion=2.0, destruction=3.0,
    details=[_detail()], step=1.25, casualties=4.0))
check("执行计划写回行动点", state.action_points == 3)
check("执行计划写回坐标",
      state.intrusion == 2.0 and state.destruction == 3.0)
check("执行计划写回明细",
      len(state.offline_details) == 1
      and state.offline_details[0].env_factor == 1.0)
check("执行计划追加演化行",
      state.evolution[-1].step == 1.25
      and state.total_casualties == 4.0
      and state.evolution[-1].source == "recover_evolution")

# ---------------- 3. 省略字段时保留原值 ----------------
state = _snap(coords=(1.5, 2.5))
state.offline_details = [_detail()]
apply_recover_plan(state, OfflineRecoveryPlan(action_point_gain=1))
check("计划省略坐标时不改坐标",
      state.intrusion == 1.5 and state.destruction == 2.5)
check("计划省略明细时保留明细",
      len(state.offline_details) == 1)

# ---------------- 4. 行为包覆盖计划 ----------------
called = []


def _custom_plan(state, now, model):
    called.append((state, now, model))
    return OfflineRecoveryPlan(
        action_point_gain=7, intrusion=2.5, destruction=3.5,
        details=[_detail()], step=2.0, casualties=5.0)


try:
    _RUNTIME.override("offline.recover_plan", _custom_plan)
    state = _snap()
    OfflineService().recover_offline(state, _NOW)
    check("计划覆盖被调用", len(called) == 1)
    check("计划覆盖写回行动点", state.action_points == 7)
    check("计划覆盖写回坐标",
          state.intrusion == 2.5 and state.destruction == 3.5)
    check("计划覆盖写回演化",
          state.evolution[-1].step == 2.0
          and state.total_casualties == 5.0)
finally:
    _RUNTIME.reset()

# ---------------- 5. 常量覆盖模型参数 ----------------
try:
    _RUNTIME.override("offline.model.step_per_hour", 0.02)
    svc = OfflineService()
    check("常量覆盖进入实例", svc.step_per_hour == 0.02)
    fast = build_recover_plan(_snap(), _NOW, svc._model())
    base = build_recover_plan(
        _snap(), _NOW, OfflineModel(step_per_hour=0.01))
    check("提高步进率抬高计划步进", fast.step > base.step)
finally:
    _RUNTIME.reset()

try:
    _RUNTIME.override("offline.model.recovery_points_per_minute", 1.0)
    plan = build_recover_plan(_snap(), _NOW, OfflineService()._model())
    check("提高行动点恢复率抬高恢复量",
          plan.action_point_gain == 480)
finally:
    _RUNTIME.reset()

# ---------------- 6. 旧入口兼容与优先级 ----------------
calls = []


def _legacy(state, now=None):
    calls.append("legacy")
    state.action_points = 77


def _new_plan(state, now, model):
    calls.append("new")
    return OfflineRecoveryPlan(action_point_gain=1)


try:
    _RUNTIME.override("StateService.recover_evolution", _legacy)
    state = _snap()
    StateService.recover_evolution(state, _NOW)
    check("旧 key 单独仍可整体替换", state.action_points == 77)
    check("旧 key 单独注册时被调用", calls == ["legacy"])
finally:
    _RUNTIME.reset()

calls.clear()
try:
    _RUNTIME.override("StateService.recover_evolution", _legacy)
    _RUNTIME.override("offline.recover_plan", _new_plan)
    state = _snap()
    StateService.recover_evolution(state, _NOW)
    check("旧 key 先注册时新 key 取代", state.action_points == 1)
    check("旧 key 先注册时不调用旧实现", calls == ["new"])
finally:
    _RUNTIME.reset()

calls.clear()
try:
    _RUNTIME.override("offline.recover_plan", _new_plan)
    _RUNTIME.override("StateService.recover_evolution", _legacy)
    state = _snap()
    StateService.recover_evolution(state, _NOW)
    check("旧 key 后注册时仍被忽略", state.action_points == 1)
    check("旧 key 后注册时不调用旧实现", calls == ["new"])
finally:
    _RUNTIME.reset()

# ---------------- 7. 无效时间戳不产生计划 ----------------
state = _snap()
state.updated_at = "not-a-date"
before = (state.action_points, state.intrusion, state.destruction,
          len(state.evolution), list(state.offline_details))
check("无效时间戳返回空计划",
      build_recover_plan(state, _NOW, OfflineModel()) is None)
OfflineService().recover_offline(state, _NOW)
after = (state.action_points, state.intrusion, state.destruction,
         len(state.evolution), list(state.offline_details))
check("空计划不修改状态", before == after)

# ---------------- 8. 默认路径端到端 ----------------
state = _snap()
OfflineService().recover_offline(state, _NOW)
check("默认路径恢复行动点", state.action_points > 0)
check("默认路径写入明细", bool(state.offline_details))
check("默认路径追加演化行",
      state.evolution[-1].source == "recover_evolution")

# ---------------- 结论 ----------------
_log.flush()
sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__
if failures:
    print(f"[check_offline_plan] FAILED {len(failures)}/{total}: {failures}")
else:
    print(f"[check_offline_plan] PASSED {total}/{total}")
print(f"[check_offline_plan] report: {_report_path}")
sys.exit(1 if failures else 0)
