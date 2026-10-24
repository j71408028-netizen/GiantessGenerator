# -*- coding: utf-8 -*-
"""环境一致性选取接入自检（Q1 剩余①）：零证据零权重、软权重不硬闸。

用法：``python tests/check_env_selection.py``
只覆盖纯逻辑，不建窗口、不联网。断言锁语义（零证据中性、一致文本被
偏好、矛盾文本仍可选），不锁权重数值——调参不应误报。
"""

import datetime
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_report_path = os.path.join(tempfile.mkdtemp(prefix="env_selection_"), "report.txt")
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


import random  # noqa: E402

from core.logic import select_quip_with_budget  # noqa: E402
from core.models import CharacterSnapshot, EvolutionRecord, Landmark  # noqa: E402
from services.character.spatial import (  # noqa: E402
    EnvironmentProfile, SpatialProfile, build_spatial_profile,
)
from services.exploration.address_plan import AddressPlanner  # noqa: E402
from services.exploration.arbiter import (  # noqa: E402
    SelectionArbiter, hook_supports_env,
)

_NOW = datetime.datetime(2026, 10, 25, 12, 0, 0)
_LM_ADDR = "1e5_1e3_1e2@W-A-B"
_URBAN = "城市街道高楼的描述"
_FOREST = "森林荒野的描述"


def _pool(entries):
    """[(text, style)] → quips_working 形状（同坐标同 step，只有文本不同）。"""
    return {"large": {(2, 2): [{"text": t, "style": s, "step": 0.5}
                               for t, s in entries]}}


def _pick(qw, env_estimate=None, seed=0):
    random.seed(seed)
    qw = {k: {c: [dict(q) for q in l] for c, l in v.items()}
          for k, v in qw.items()}
    return select_quip_with_budget("large", 2, 2, qw, set(),
                                   0.0, 0.0, 1.0, env_estimate=env_estimate)[0]


# ---------------- 1. select 级：环境一致性软权重 ----------------
_qw = _pool([(_URBAN, "X"), (_FOREST, "X")])
# 槽位环境 1.2（城市证据）：文本因子偏差决定 argmin——一致文本必胜
_urban_wins = sum(1 for i in range(50) if _pick(_qw, 1.2, seed=i) == _URBAN)
check("环境一致的文本被偏好（argmin）", _urban_wins == 50, f"{_urban_wins}/50")
# 零证据（None）：软权重整体缺席，退回同分等概率——两文本都出现
random.seed(0)
_both = set()
for i in range(100):
    _both.add(_pick(_qw, None, seed=i))
check("零证据零权重（中性退化为旧语义）", _both == {_URBAN, _FOREST}, str(_both))
# 软权重不做硬闸：全池矛盾时仍正常选取（池耗尽降级不受影响）
check("矛盾文本仍可选（不硬闸）",
      _pick(_pool([(_FOREST, "X")]), 1.2, seed=0) == _FOREST)

# ---------------- 2. 档案级：env_estimate 与零证据 ----------------
check("空档案 env_estimate 为 None",
      SpatialProfile().env_estimate(_LM_ADDR) is None)
check("空档案对坏文本也为 None",
      SpatialProfile().env_estimate("") is None)


def _state(rows):
    snap = CharacterSnapshot(giantess_id="g", name="t")
    snap.evolution = list(rows)
    return snap


_envless = build_spatial_profile(_state([
    EvolutionRecord(changed_at=_NOW.isoformat(), step=1.0, intrusion=1.0,
                    destruction=1.0, casualties=0.0, source="t",
                    position=_LM_ADDR)          # 有 position 无 env_factor
]), now=_NOW)
check("无环境沉淀的到访行不产生环境估计",
      _envless.env_estimate(_LM_ADDR) is None and _envless.env.has_data() is False)

_profed = build_spatial_profile(_state([
    EvolutionRecord(changed_at=_NOW.isoformat(), step=1.0, intrusion=1.0,
                    destruction=1.0, casualties=0.0, source="t",
                    position=_LM_ADDR, env_factor=1.2)
]), now=_NOW)
_est = _profed.env_estimate(_LM_ADDR)
check("有沉淀时环境估计在统一值域内",
      _est is not None and 0.1 <= _est <= 1.5, str(_est))
check("估计即 EnvironmentProfile 查询值",
      _est == _profed.env.env_factor(_LM_ADDR))

# ---------------- 3. 仲裁器级：槽位装配携带环境估计 ----------------
class FakeLandmarkRepo:
    def __init__(self, registers):
        self.registers = dict(registers)

    def load_style_registers(self, styles):
        return {s: self.registers[s] for s in styles if s in self.registers}


class FakeQuipRepo:
    def load_style_registers(self, styles):
        return {}


class FakeCatalog:
    def __init__(self, landmarks, registers):
        self.settings = {"comparison_count": 5, "comparison_order": "match"}
        self.merged_landmarks = list(landmarks)
        self.selected_styles = list(registers)
        self.selected_quip_styles = []
        self.landmark_repo = FakeLandmarkRepo(registers)
        self.quip_repo = FakeQuipRepo()
        self.quips = {}
        self._landmark_styles = {
            id(lm): style for lm, style in
            ((x, getattr(x, "_style", "")) for x in landmarks) if style
        }


def _cand(lm, ratio, size):
    return {"landmark": lm, "part": "身高", "ratio": ratio,
            "size": size, "match_score": abs(1 - ratio), "posture": [1]}


_lm = Landmark("锚定地标", 100, "vertical", "unique", address=_LM_ADDR)
_lm._style = "S"
_arb = SelectionArbiter(FakeCatalog([_lm], {"S": "1e5_1e3_1e2@W-A"}),
                        AddressPlanner(FakeCatalog([_lm], {"S": "1e5_1e3_1e2@W-A"})))
_plan = _arb.arbitrate([_cand(_lm, 1.0, 100)], _LM_ADDR, height=100.0,
                       durability={}, profile=_profed, skip_base_prob=0.0)
check("仲裁槽位携带环境估计",
      _plan["chosen"] and _plan["chosen"][0].env_estimate == _est)
_plan_neutral = _arb.arbitrate([_cand(_lm, 1.0, 100)], _LM_ADDR, height=100.0,
                               durability={}, profile=None, skip_base_prob=0.0)
check("无档案时槽位环境估计为 None",
      _plan_neutral["chosen"] and _plan_neutral["chosen"][0].env_estimate is None)

# ---------------- 4. hook 兼容判定 ----------------
check("无覆盖时支持 env_estimate", hook_supports_env(None))
check("旧签名覆盖不支持 env_estimate",
      not hook_supports_env(lambda size_cat, lanes=None: None))
check("带 env_estimate 参数的覆盖支持",
      hook_supports_env(lambda size_cat, env_estimate=None: None))
check("**kwargs 覆盖支持", hook_supports_env(lambda **kw: None))

# ---------------- 结论 ----------------
_log.flush()
sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__
if failures:
    print(f"[check_env_selection] FAILED {len(failures)}/{total}: {failures}")
else:
    print(f"[check_env_selection] PASSED {total}/{total}")
print(f"[check_env_selection] report: {_report_path}")
sys.exit(1 if failures else 0)
