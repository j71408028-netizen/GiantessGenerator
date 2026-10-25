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
    EnvPreferenceProfile, EnvironmentProfile, SpatialProfile,
    build_spatial_profile, env_priors_for_tags,
)
from services.exploration.address_plan import AddressPlanner  # noqa: E402
from services.exploration.arbiter import (  # noqa: E402
    SelectionArbiter, SlotRuling,
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

# ---------------- 5. 环境偏好学习（第三轴：交点） ----------------
def _vote_rows(env, n, step=1.0, days_ago=0.0, addr=_LM_ADDR):
    ts = _NOW - datetime.timedelta(days=days_ago)
    return [EvolutionRecord(changed_at=ts.isoformat(), step=step, intrusion=1.0,
                            destruction=1.0, casualties=0.0, source="t",
                            position=addr, env_factor=env)
            for _ in range(n)]


_empty_pref = EnvPreferenceProfile()
check("零证据零先验 → 偏好全中性",
      _empty_pref.preference_factor(0.2) == 1.0
      and _empty_pref.preference_factor(1.2) == 1.0)

_wild = EnvPreferenceProfile().learn_from_evolution(_vote_rows(0.15, 20), now=_NOW)
_urban = EnvPreferenceProfile().learn_from_evolution(_vote_rows(1.4, 20), now=_NOW)
check("学习方向：荒野证据抬高低环境值偏好",
      _wild.preference_factor(0.15) > _wild.preference_factor(1.4))
check("学习方向：城市证据反向",
      _urban.preference_factor(1.4) > _urban.preference_factor(0.15))

_no_vote = EnvPreferenceProfile().learn_from_evolution(
    _vote_rows(1.2, 5, step=0.0), now=_NOW)
check("step≤0 的行不投票", not _no_vote.has_data()
      and _no_vote.preference_factor(1.2) == 1.0)

_stale = EnvPreferenceProfile().learn_from_evolution(
    _vote_rows(0.15, 20, days_ago=90.0), now=_NOW)
check("久远投票向中性回落",
      abs(_stale.preference_factor(0.15) - 1.0)
      < abs(_wild.preference_factor(0.15) - 1.0))

# ---------------- 6. TAG 先验：方向 + 权重压低 ----------------
_travel = env_priors_for_tags(["旅行"])
check("旅行先验偏向低环境值", _travel is not None and _travel[0] > _travel[-1])
check("无命中标签返回 None（均匀兜底）", env_priors_for_tags(["涩涩"]) is None)

_prior_only = EnvPreferenceProfile().learn_from_evolution(
    [], prior_shares=_travel, now=_NOW)
check("先验单独在场：方向生效且为温和扰动",
      1.0 < _prior_only.preference_factor(0.15) < _wild.preference_factor(0.15))
check("先验方向的反侧被压低",
      _prior_only.preference_factor(1.4) < 1.0)
check("先验随证据让位（先验 + 反向证据 → 反向偏好）",
      EnvPreferenceProfile().learn_from_evolution(
          _vote_rows(1.4, 40), prior_shares=_travel, now=_NOW
      ).preference_factor(1.4) > 1.0)

# ---------------- 7. 档案装配与仲裁器消费 ----------------
def _pref_state(rows, tags):
    snap = CharacterSnapshot(giantess_id="g", name="t")
    snap.evolution = list(rows)
    snap.selected_tags = list(tags)
    return snap


_A = "1e5_1e3_1e2@W-A-C"    # C / D 为同父兄弟：区域权重对称，只剩偏好分辨
_D = "1e5_1e3_1e2@W-A-D"
_prof_pref = build_spatial_profile(
    _pref_state(_vote_rows(0.15, 10, addr=_A) + _vote_rows(1.4, 10, addr=_D),
                ["旅行"]), now=_NOW)
check("档案装配第三轴", _prof_pref.env_preference is not None
      and _prof_pref.env_preference_factor(_A) > _prof_pref.env_preference_factor(_D))

check("无偏好数据 → 因子恒 1.0",
      build_spatial_profile(_pref_state([], []), now=_NOW)
      .env_preference_factor(_A) == 1.0)
check("坏地址 → 因子 1.0", _prof_pref.env_preference_factor("") == 1.0
      and _prof_pref.env_preference_factor("坏文本") == 1.0)


def _ruling_at(addr):
    lm = Landmark(f"地标{addr[-1]}", 100, "vertical", "unique", address=addr)
    return SlotRuling(cand={"landmark": lm, "ratio": 1.0}, addr=addr)


_arb_score = SelectionArbiter(None, None)
_s_pref = _arb_score._joint_score(_ruling_at(_A), "", 1000.0, _prof_pref)
_s_anti = _arb_score._joint_score(_ruling_at(_D), "", 1000.0, _prof_pref)
check("仲裁联合分：偏好环境的地标排前", _s_pref > _s_anti)
_prof_neutral = SpatialProfile()
check("中性档案下联合分不受环境偏好影响",
      _arb_score._joint_score(_ruling_at(_A), "", 1000.0, _prof_neutral)
      == _arb_score._joint_score(_ruling_at(_D), "", 1000.0, _prof_neutral))

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
