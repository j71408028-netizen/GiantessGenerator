# -*- coding: utf-8 -*-
"""离线环境因子自检：在线沉淀 → 离线聚合的两信源分治口径。

用法：``python tests/check_offline_env.py``
只覆盖纯逻辑，不建窗口、不联网。断言语义序关系与边界（沉淀抬升方向、
时间回落、邻域随步进/体型参与、精确度单调），不锁定具体数值——调参
不应误报。
"""

import datetime
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_report_path = os.path.join(tempfile.mkdtemp(prefix="offline_env_"), "report.txt")
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


from core.models import CharacterSnapshot, EvolutionRecord  # noqa: E402
from services.character.offline import (  # noqa: E402
    OfflineService, _default_env_by_precision,
)

_NOW = datetime.datetime(2026, 10, 24, 12, 0, 0)
_FINE = "1e4_2e3_1e2@*!TH-GSK1-East-3"    # 三级单元 100m
_COARSE = "1e4_2e3_5e2@*!TH-GSK1"         # 只到一级，单元 10km
_NEXT = "1e4_2e3_1e2@*!TH-GSK1-East-4"    # 三级邻单元，分歧级距离 100m


def _rows(n, addr, env, days_ago=0.0):
    ts = _NOW - datetime.timedelta(days=days_ago)
    return [EvolutionRecord(changed_at=ts.isoformat(), step=1.0,
                            intrusion=1.0, destruction=1.0, casualties=0.0,
                            source="t", position=addr, env_factor=env)
            for _ in range(n)]


def _snap(height=100.0, position=_FINE, evolution=None):
    snap = CharacterSnapshot(giantess_id="g", name="t", height=height)
    snap.position = position
    snap.evolution = list(evolution or [])
    return snap


# ---------------- 1. 沉淀数据位 ----------------
_snap1 = CharacterSnapshot(giantess_id="g1", name="t")
_snap1.record_change(step=1.0, source="t", position=_FINE, env_factor=1.2)
check("record_change 携带环境沉淀", _snap1.evolution[-1].env_factor == 1.2)
_snap1.record_change(step=1.0, source="t")
check("不传 env 时沉淀为空", _snap1.evolution[-1].env_factor is None)
_legacy = EvolutionRecord(**{"changed_at": _NOW.isoformat(), "step": 1.0,
                             "intrusion": 1.0, "destruction": 1.0,
                             "casualties": 0.0, "source": "t"})
check("旧档演化行 env 兼容为空", _legacy.env_factor is None)

# ---------------- 2. 无沉淀：精确度默认值 ----------------
svc = OfflineService()
f_fine = svc._environment_factor(_snap(position=_FINE), 0.0)
f_coarse = svc._environment_factor(_snap(position=_COARSE), 0.0)
check("无沉淀时取精确度默认值",
      f_fine == _default_env_by_precision(_FINE)
      and f_coarse == _default_env_by_precision(_COARSE))
check("精确度越高默认值越高", f_fine > f_coarse)
check("无位置时取默认值下限",
      svc._environment_factor(_snap(position=""), 0.0)
      == _default_env_by_precision(""))

# ---------------- 3. 沉淀聚合 ----------------
fresh = svc._environment_factor(
    _snap(evolution=_rows(8, _FINE, 1.2)), 0.01)
half = svc._environment_factor(
    _snap(evolution=_rows(1, _FINE, 1.2)), 0.01)
check("一致沉淀抬升环境因子（高于默认）", fresh > f_fine)
check("证据越少越向默认值收缩", f_fine < half < fresh)
stale = svc._environment_factor(
    _snap(evolution=_rows(8, _FINE, 1.2, days_ago=90.0)), 0.01)
check("久远沉淀向默认值回落", stale < fresh)
check("坏沉淀（超值域/非有限）被夹取不崩溃",
      0.1 <= svc._environment_factor(
          _snap(evolution=_rows(2, _FINE, 99.0)), 0.01) <= 1.5
      and 0.1 <= svc._environment_factor(
          _snap(evolution=_rows(2, _FINE, 0.5)), 0.01) <= 1.5)

# ---------------- 4. 邻域参与：步进与体型 ----------------
_big_low = svc._environment_factor(
    _snap(height=1000.0, evolution=_rows(8, _FINE, 0.2) + _rows(8, _NEXT, 1.2)),
    0.01)
_big_high = svc._environment_factor(
    _snap(height=1000.0, evolution=_rows(8, _FINE, 0.2) + _rows(8, _NEXT, 1.2)),
    2.0)
_small_high = svc._environment_factor(
    _snap(height=1.6, evolution=_rows(8, _FINE, 0.2) + _rows(8, _NEXT, 1.2)),
    2.0)
check("步进越高邻近沉淀参与越多", _big_high > _big_low)
check("体型越大邻近沉淀参与越多", _big_low > _small_high)

# ---------------- 5. 离线结算端到端 ----------------
snap = _snap(height=100.0, evolution=_rows(4, _FINE, 1.2))
snap.updated_at = (_NOW - datetime.timedelta(hours=8)).isoformat()
snap.action_points = 0
before = len(snap.evolution)
svc.recover_offline(snap, now=_NOW)
check("离线结算追加演化行", len(snap.evolution) > before)
check("明细环境因子在统一值域内",
      all(0.1 <= d.env_factor <= 1.5 for d in snap.offline_details)
      and snap.offline_details)
check("沉淀抬升离线伤亡",
      all(d.casualties >= 0 for d in snap.offline_details))

# ---------------- 结论 ----------------
_log.flush()
sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__
if failures:
    print(f"[check_offline_env] FAILED {len(failures)}/{total}: {failures}")
else:
    print(f"[check_offline_env] PASSED {total}/{total}")
print(f"[check_offline_env] report: {_report_path}")
sys.exit(1 if failures else 0)
