# -*- coding: utf-8 -*-
"""报告引擎参数自检：``report.model.*`` 常量钩子的默认与覆盖。

用法：``python tests/check_report_engine.py``
只覆盖纯公式（突破概率 / 耐久伤害 / 残破阈值 / 无事件兜底），不建窗口、
不联网、不跑完整报告生成。断言锁公式形状，不锁世界观调参——改默认值
不应误报，改公式形状必须先改这里的预期。
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_report_path = os.path.join(
    tempfile.mkdtemp(prefix="report_engine_"), "report.txt")
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
import services.exploration.report as report_engine  # noqa: E402,F401

_RUNTIME = get_runtime()


def _close(a, b):
    return abs(a - b) < 1e-9


# ---------------- 1. 默认公式（无覆盖时与内置数值一致） ----------------
check("突破概率：无意愿恒为 0",
      report_engine.breakthrough_prob(80.0, False, 3) == 0.0)
check("突破概率：首次尝试 = 贪婪百分数",
      _close(report_engine.breakthrough_prob(50.0, True, 0), 0.5))
check("突破概率：尝试次数线性抬升并夹取 1.0",
      _close(report_engine.breakthrough_prob(50.0, True, 1), 1.0))
check("耐久伤害 = 倍率 × 身高比² × 破坏性 × 系数",
      _close(report_engine.durability_damage(1.0, 2.0, 3.0), 1.2))
check("残破阈值严格小于（默认 0.5 不算残破）",
      report_engine.is_worn(0.5) is False
      and report_engine.is_worn(0.49) is True)
check("无事件兜底默认 0.05 / 0.3",
      _close(_RUNTIME.resolve_value("report.model.step_fallback"), 0.05)
      and _close(_RUNTIME.resolve_value("report.model.env_factor_fallback"),
                 0.3))

# ---------------- 2. 常量覆盖生效 ----------------
try:
    _RUNTIME.override("report.model.breakthrough_scale", 0.0)
    check("突破缩放 0 = 永不突破",
          report_engine.breakthrough_prob(90.0, True, 5) == 0.0)
    _RUNTIME.override("report.model.breakthrough_scale", 2.0)
    check("突破缩放 2 抬升概率",
          _close(report_engine.breakthrough_prob(20.0, True, 0), 0.4))
finally:
    _RUNTIME.reset()

try:
    _RUNTIME.override("report.model.durability_damage_scale", 1.0)
    check("耐久伤害系数覆盖生效",
          _close(report_engine.durability_damage(1.0, 2.0, 3.0), 12.0))
finally:
    _RUNTIME.reset()

try:
    _RUNTIME.override("report.model.worn_threshold", 0.9)
    check("残破阈值覆盖生效", report_engine.is_worn(0.7) is True)
finally:
    _RUNTIME.reset()

try:
    _RUNTIME.override("report.model.step_fallback", 0.2)
    _RUNTIME.override("report.model.env_factor_fallback", 1.0)
    check("兜底常量覆盖生效",
          _close(_RUNTIME.resolve_value("report.model.step_fallback"), 0.2)
          and _close(_RUNTIME.resolve_value("report.model.env_factor_fallback"),
                     1.0))
finally:
    _RUNTIME.reset()

# ---------------- 结论 ----------------
_log.flush()
sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__
if failures:
    print(f"[check_report_engine] FAILED {len(failures)}/{total}: {failures}")
else:
    print(f"[check_report_engine] PASSED {total}/{total}")
print(f"[check_report_engine] report: {_report_path}")
sys.exit(1 if failures else 0)
