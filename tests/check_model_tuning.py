# -*- coding: utf-8 -*-
"""角色出身调制烙印（ModelTuning）自检：派生 / 烙印 / 兼容行为锁。

用法：``python tests/check_model_tuning.py``
只跑纯逻辑，不建窗口、不联网；档案读写走系统临时目录，不碰 data/。

行为锁（对应 .workbuddy/behavior-pack-baseline-v2.md 阶段 A）：
- 默认派生 = 现行常量默认（无包时数值不变）；
- 常量 / TAG 表 / derive 钩子的覆盖都能进入烙印；
- 烙印终身携带，世界包停用（runtime.reset）不触及已烙印的角色；
- 离谱调制被 sanitize 夹取（烙印与 from_dict 两端）；
- 旧档（无 tuning 字段）回退全默认，离线结算结果与改动前一致；
- 副本 payload 不携带 tuning。
"""

import datetime
import json
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_report_path = os.path.join(
    tempfile.mkdtemp(prefix="model_tuning_"), "report.txt")
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
    CharacterSnapshot, ModelTuning, SpatialPriorsParams,
)
from persistence.character_repo import CharacterRepo  # noqa: E402
from services.character.offline import OfflineModel  # noqa: E402
from services.character.spatial import (  # noqa: E402
    SpatialPriors, env_priors_for_tags, priors_for_tags,
)
from services.creation_service import CreationService  # noqa: E402
from services.exploration.character import CharacterAssembler  # noqa: E402

_RUNTIME = get_runtime()
_RUNTIME.reset()


def _personality():
    from core.models import Personality
    return Personality(name="温和", init_intrusion=1.0, step_intrusion=0.1,
                       init_destruction=1.0, step_destruction=0.1,
                       sensitivity=1.0, gravity=0.5)


def _core(name="测试娘", tags=None):
    return {
        "name": name, "nick": "", "original_height": 1.6,
        "height": 30.0, "body_parts": {"身高": 30.0},
        "personality_obj": _personality(),
        "base_intrusion": 1.0, "base_destruction": 1.0,
        "greed": 0.0, "will": False, "will_status": None,
        "selected_tags": list(tags or []), "intro_hidden": "",
        "intro_visible": "", "birthday": "", "uploaded_image": None,
        "total_casualties": 0.0, "position": "",
    }


def _assembler(world_state=None):
    repo = CharacterRepo(tempfile.mkdtemp(prefix="tuning_repo_"))
    return repo, CharacterAssembler(repo, {}, world_state=world_state)


_ACTIVE_WORLD = SimpleNamespace(
    active=True,
    manifest=SimpleNamespace(world_id="w1", name="测试世界", version="2.1"),
)

# ---------------- 1. 默认派生 = 现行常量默认 ----------------

tuning = CreationService.derive_model_tuning(None, [], None)
model = OfflineModel()
check("默认派生离线 8 项与现行常量一致", (
    tuning.step_per_hour == model.step_per_hour
    and tuning.exit_amp == model.exit_amp
    and tuning.exit_tau == model.exit_tau
    and tuning.recovery_points_per_minute == model.recovery_points_per_minute
    and tuning.recovery_decay_per_minute == model.recovery_decay_per_minute
    and tuning.noise_scale == model.noise_scale
    and tuning.old_step_divisor == model.old_step_divisor
    and tuning.rhythm_swing == model.rhythm_swing))
check("默认派生无标签时先验中性、份额均匀兜底", (
    tuning.spatial_priors == SpatialPriorsParams()
    and tuning.env_prior_shares is None
    and tuning.origin_world is None))

tuning_travel = CreationService.derive_model_tuning(
    None, ["旅行"], None)
expect = priors_for_tags(["旅行"])
check("默认派生按标签合并空间先验", (
    tuning_travel.spatial_priors.range_factor == expect.range_factor
    and tuning_travel.spatial_priors.stickiness == expect.stickiness
    and tuning_travel.spatial_priors.novelty_bonus == expect.novelty_bonus
    and tuning_travel.spatial_priors.anchored_bias == expect.anchored_bias))
check("默认派生按标签合并环境份额", (
    tuning_travel.env_prior_shares
    == env_priors_for_tags(["旅行"])))

# ---------------- 2. 世界包覆盖进入烙印 ----------------

_RUNTIME.override("offline.model.step_per_hour", 0.05)
_RUNTIME.override("spatial.TAG_SPATIAL_PRIORS", {
    "旅行": SpatialPriors(range_factor=1.4, novelty_bonus=0.2)})
_RUNTIME.override("spatial.TAG_ENV_PRIORS", {"旅行": (0.3,) * 7})
tuning_pack = CreationService.derive_model_tuning(None, ["旅行"], None)
check("常量与 TAG 表覆盖进入派生结果", (
    tuning_pack.step_per_hour == 0.05
    and tuning_pack.spatial_priors.range_factor == 1.4
    and tuning_pack.env_prior_shares is not None
    and abs(sum(tuning_pack.env_prior_shares) - 1.0) < 1e-9))
_RUNTIME.reset()
check("停用后派生回落默认",
      CreationService.derive_model_tuning(None, [], None).step_per_hour
      == model.step_per_hour)

# ---------------- 3. derive 钩子整体覆盖 + 烙印终身携带 ----------------


def _custom_derive(personality, selected_tags, height, core=None):
    return ModelTuning(step_per_hour=-5.0, rhythm_swing=99.0,
                       extra={"mood": "calm"})


_RUNTIME.override(CreationService.derive_model_tuning, _custom_derive)
repo, assembler = _assembler(world_state=_ACTIVE_WORLD)
state = assembler.character_from_core_or_report(_core(name="烙印娘"))
_RUNTIME.reset()
imprinted = state.model_tuning
check("derive 覆盖被烙印且经 sanitize 夹取", (
    imprinted.step_per_hour == 0.0 and imprinted.rhythm_swing == 3.0
    and imprinted.extra == {"mood": "calm"}))
check("origin_world 记录激活世界包", imprinted.origin_world == {
    "pack_id": "w1", "name": "测试世界", "version": "2.1"})
check("停用世界包后已烙角色不变", (
    state.model_tuning.step_per_hour == 0.0
    and state.model_tuning.rhythm_swing == 3.0))
state2 = assembler.character_from_core_or_report(_core(name="回落娘"))
check("停用后新角色按默认派生", (
    state2.model_tuning.step_per_hour == model.step_per_hour
    and state2.model_tuning.rhythm_swing == model.rhythm_swing
    and state2.model_tuning.origin_world == {
        "pack_id": "w1", "name": "测试世界", "version": "2.1"}))

repo_none, assembler_none = _assembler(world_state=None)
state3 = assembler_none.character_from_core_or_report(_core(name="无包娘"))
check("未激活世界包时 origin_world 为 None",
      state3.model_tuning.origin_world is None)

# ---------------- 4. sanitize 两端夹取 ----------------

bad = ModelTuning(
    step_per_hour=float("nan"), exit_tau=-5.0, noise_scale=-1.0,
    rhythm_swing=99.0,
    spatial_priors=SpatialPriorsParams(range_factor=10.0, stickiness=5.0,
                                       novelty_bonus=-2.0, anchored_bias=3.0),
    env_prior_shares=[0.5, 0.5, 0.5], origin_world="不是字典",
    extra=None).sanitize()
check("sanitize 非有限回默认、负值归零", (
    bad.step_per_hour == ModelTuning().step_per_hour
    and bad.exit_tau == 1e-9 and bad.noise_scale == 0.0))
check("sanitize 夹取 rhythm_swing 到 0~3", bad.rhythm_swing == 3.0)
check("sanitize 夹取空间先验四标量", (
    bad.spatial_priors.range_factor == 2.0
    and bad.spatial_priors.stickiness == 0.8
    and bad.spatial_priors.novelty_bonus == 0.0
    and bad.spatial_priors.anchored_bias == 0.5))
check("sanitize 环境份额形状不符回 None、非法字段丢弃", (
    bad.env_prior_shares is None and bad.origin_world is None
    and bad.extra == {}))
shares = ModelTuning(
    env_prior_shares=[-1.0, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2]).sanitize()
check("sanitize 环境份额负值归零后归一", (
    shares.env_prior_shares is not None
    and shares.env_prior_shares[0] == 0.0
    and abs(sum(shares.env_prior_shares) - 1.0) < 1e-9))

garbage = CharacterSnapshot.from_dict({
    "giantess_id": "g", "name": "t",
    "model_tuning": {"step_per_hour": "abc", "rhythm_swing": None,
                     "env_prior_shares": [1, 2],
                     "spatial_priors": {"range_factor": "x", "未知": 1}},
}).model_tuning
check("from_dict 端同样夹取（垃圾输入回默认）", (
    garbage.step_per_hour == ModelTuning().step_per_hour
    and garbage.rhythm_swing == ModelTuning().rhythm_swing
    and garbage.env_prior_shares is None
    and garbage.spatial_priors == SpatialPriorsParams()))

# ---------------- 5. 旧档回退默认 + 持久化 round-trip ----------------

repo.save(state)
info_path = os.path.join(repo.states_dir, state.giantess_id, "info.json")
with open(info_path, "r", encoding="utf-8") as f:
    saved = json.load(f)
check("持久化携带 model_tuning 与 origin_world", (
    isinstance(saved.get("model_tuning"), dict)
    and saved["model_tuning"].get("origin_world") == imprinted.origin_world))
reloaded = CharacterRepo(
    os.path.dirname(repo.states_dir)).load(state.giantess_id)
check("读档还原烙印", (
    reloaded.model_tuning.step_per_hour == 0.0
    and reloaded.model_tuning.rhythm_swing == 3.0
    and reloaded.model_tuning.extra == {"mood": "calm"}
    and reloaded.model_tuning.origin_world == imprinted.origin_world))

old_style = {k: v for k, v in saved.items() if k != "model_tuning"}
legacy = CharacterSnapshot.from_dict(old_style)
check("旧档无 tuning 字段回退全默认", (
    legacy.model_tuning == ModelTuning()))

# ---------------- 6. 旧档 / 新档的离线结算一致 ----------------

from services.character.offline import build_recover_plan  # noqa: E402

_NOW = datetime.datetime(2026, 10, 26, 12, 0, 0)
legacy.updated_at = (_NOW - datetime.timedelta(hours=8)).isoformat()
fresh = CharacterSnapshot.from_dict(saved)
fresh.updated_at = legacy.updated_at
plan_legacy = build_recover_plan(legacy, _NOW, OfflineModel(noise_scale=0.0))
plan_fresh = build_recover_plan(fresh, _NOW, OfflineModel(noise_scale=0.0))
check("旧档与新档（默认烙印）离线结算一致", (
    plan_legacy is not None and plan_fresh is not None
    and plan_legacy.step == plan_fresh.step
    and plan_legacy.casualties == plan_fresh.casualties
    and plan_legacy.action_point_gain == plan_fresh.action_point_gain))

# ---------------- 7. 副本 payload 不携带 tuning ----------------

payload = assembler.dungeon_data_from_any(state)
check("副本 payload 不携带 model_tuning",
      "model_tuning" not in (payload or {}))

# ---------------- 结论 ----------------

_RUNTIME.reset()
_log.flush()
sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__
if failures:
    print(f"[check_model_tuning] FAILED {len(failures)}/{total}: {failures}")
else:
    print(f"[check_model_tuning] PASSED {total}/{total}")
print(f"[check_model_tuning] report: {_report_path}")
sys.exit(1 if failures else 0)
