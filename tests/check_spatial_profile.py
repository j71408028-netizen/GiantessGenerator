# -*- coding: utf-8 -*-
"""角色空间档案自检（P3a）：到访日志、区域权重树学习、TAG 先验与档案装配。

用法：``python tests/check_spatial_profile.py``
只覆盖纯逻辑，不建窗口、不联网。断言的是语义序关系与边界（常去 > 均匀 >
未到访、时间衰减、夹取区间），不锁具体数值——调参不应误报。
"""

import datetime
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_report_path = os.path.join(tempfile.mkdtemp(prefix="spatial_"), "report.txt")
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


from core.models import (  # noqa: E402
    CharacterSnapshot, EvolutionRecord, ModelTuning, SpatialPriorsParams,
)
from services.character.spatial import (  # noqa: E402
    TAG_SPATIAL_PRIORS, SpatialProfileService, build_spatial_profile,
    env_priors_for_tags, priors_for_tags,
)

_NOW = datetime.datetime(2026, 10, 24, 12, 0, 0)
_ADDR = "1e4_1e3_1e2@*!TH-GSK1-East-3"

# ---------------- 1. 到访日志（EvolutionRecord.position） ----------------
_rec = EvolutionRecord(changed_at=_NOW.isoformat(), step=1.0,
                       intrusion=1.0, destruction=1.0, casualties=0.0,
                       source="t", position=_ADDR)
check("演化行携带到访地址", _rec.position == _ADDR)
_legacy = EvolutionRecord(changed_at=_NOW.isoformat(), step=1.0,
                          intrusion=1.0, destruction=1.0, casualties=0.0)
check("旧档演化行 position 默认为空", _legacy.position == "")

_snap = CharacterSnapshot(giantess_id="g1", name="t")
_snap.record_change(step=2.0, source="t", position=_ADDR)
check("record_change 落点到访地址",
      _snap.evolution[-1].position == _ADDR)
_snap.record_change(step=1.0, source="t")
check("record_change 不传位置时不作为到访证据",
      _snap.evolution[-1].position == "")

# ---------------- 2. 学习层：区域权重树 ----------------
def _rows(n, addr, days_ago=0.0):
    ts = _NOW - datetime.timedelta(days=days_ago)
    return [EvolutionRecord(changed_at=ts.isoformat(), step=1.0,
                            intrusion=1.0, destruction=1.0, casualties=0.0,
                            source="t", position=addr) for _ in range(n)]


_hot = "1e4_1e3_1e2@*!TH-East-1"          # 常去分支
_cold = "1e4_1e3_1e2@*!TH-West-1"         # 少去分支（同维度）
_other = "1e4_1e3_1e2@*!RW-CN1-2"         # 另一维度
_rows_hist = _rows(8, _hot) + _rows(2, _cold)

svc = SpatialProfileService().learn_from_evolution(_rows_hist, now=_NOW)
check("空学习器对任何地址中性",
      SpatialProfileService().region_factor(_hot) == 1.0
      and SpatialProfileService().region_factor("坏地址") == 1.0)
f_hot = svc.region_factor(_hot)
f_cold = svc.region_factor(_cold)
f_fresh_branch = svc.region_factor("1e4_1e3_1e2@*!TH-North-1")
check("常去分支因子高于少去分支", f_hot > f_cold,
      f"{f_hot} vs {f_cold}")
check("未到访分支因子被压低", f_fresh_branch < 1.0, f"{f_fresh_branch}")
check("因子不越夹取区间", 0.0 < f_fresh_branch and f_hot <= 2.5)

# 均匀分布 → 中性（各分支因子接近 1，不偏向任何一支）
_even = _rows(4, _hot) + _rows(4, _cold)
svc_even = SpatialProfileService().learn_from_evolution(_even, now=_NOW)
fe_hot = svc_even.region_factor(_hot)
fe_cold = svc_even.region_factor(_cold)
check("均匀到访时两分支因子接近中性",
      abs(fe_hot - 1.0) < 0.3 and abs(fe_cold - 1.0) < 0.3,
      f"{fe_hot} / {fe_cold}")

# 时间衰减：同样的到访量，全部久远 → 偏好向中性回落
svc_old = SpatialProfileService().learn_from_evolution(
    _rows(8, _hot, days_ago=180.0) + _rows(2, _cold, days_ago=180.0), now=_NOW)
fo_hot = svc_old.region_factor(_hot)
fo_cold = svc_old.region_factor(_cold)
check("久远历史偏好向中性衰减", abs(fo_hot - 1.0) < abs(f_hot - 1.0)
      and abs(fo_cold - 1.0) < abs(f_cold - 1.0),
      f"old {fo_hot}/{fo_cold} vs fresh {f_hot}/{f_cold}")

# 更近的记录权重更高：新近分支反超久远分支
svc_mix = SpatialProfileService().learn_from_evolution(
    _rows(8, _hot, days_ago=60.0) + _rows(8, _cold, days_ago=0.0), now=_NOW)
check("新近到访的分支反超久远分支",
      svc_mix.region_factor(_cold) > svc_mix.region_factor(_hot))

# 坏数据：无法解析的 position / 无时间戳行不参与，不崩溃
svc_bad = SpatialProfileService().learn_from_evolution(
    [EvolutionRecord(changed_at="", step=1.0, intrusion=0, destruction=0,
                     casualties=0, source="t", position=_ADDR),
     EvolutionRecord(changed_at=_NOW.isoformat(), step=1.0, intrusion=0,
                     destruction=0, casualties=0, source="t", position="坏地址")],
    now=_NOW)
check("坏数据行被跳过且不崩溃", not svc_bad.has_data())

# visit_weight：分支级判定
svc_v = SpatialProfileService().learn_from_evolution(_rows_hist, now=_NOW)
check("已到访分支权重为正", svc_v.visit_weight(_hot) > 0.0)
check("未到访分支权重为零",
      svc_v.visit_weight("1e4_1e3_1e2@*!TH-North-1") == 0.0)
check("另一维度未到访", svc_v.visit_weight(_other) == 0.0)
check("已到访维度集合正确", svc_v.visited_worlds() == {"TH"})

# ---------------- 3. 先验层：TAG 默认表 → 创建期烙印 ----------------
for tag, priors in TAG_SPATIAL_PRIORS.items():
    got = priors_for_tags([tag])
    check(f"单标签 {tag} 可取先验", got == priors)

p_travel = priors_for_tags(["旅行"])
check("旅行先验含新颖与锚定偏好",
      p_travel.novelty_bonus > 0 and p_travel.anchored_bias > 0)
check("内容类标签不入先验表", "涩涩" not in TAG_SPATIAL_PRIORS
      and priors_for_tags(["涩涩", "裙装"]) == SpatialPriorsParams())
p_home = priors_for_tags(["能躺着不站着"])
check("恋家型标签粘性为正、半径收窄",
      p_home.stickiness > 0 and p_home.range_factor < 1.0)
p_both = priors_for_tags(["旅行", "四处走走"])
check("多标签半径倍率相乘且受夹取",
      p_both.range_factor > priors_for_tags(["四处走走"]).range_factor)

# ---------------- 4. 档案装配（先验读出身烙印） ----------------
_snap2 = CharacterSnapshot(giantess_id="g2", name="t",
                           selected_tags=["旅行"],
                           activity_regions=["1e4_1e3_1e2@!TH-East"])
_snap2.evolution = _rows_hist
_snap2.model_tuning = ModelTuning(
    spatial_priors=priors_for_tags(["旅行"]),
    env_prior_shares=env_priors_for_tags(["旅行"]))
profile = build_spatial_profile(_snap2, now=_NOW)
check("档案透传活动区域", profile.activity_regions == ["1e4_1e3_1e2@!TH-East"])
check("档案装配先验读烙印", profile.priors == priors_for_tags(["旅行"]))
check("档案学习层有数据", profile.learned is not None and profile.learned.has_data())
check("环境偏好先验来自烙印", profile.env_preference.has_data())

check("活动区域命中用覆盖口径",
      profile.in_activity_region(_hot)          # East-1 在 East 之内
      and not profile.in_activity_region(_cold)
      and not profile.in_activity_region(""))
pf_hot = profile.region_factor(_hot)
pf_north = profile.region_factor("1e4_1e3_1e2@!TH-North-1")
check("新颖加成只作用于未到访分支",
      pf_north > svc.region_factor("1e4_1e3_1e2@!TH-North-1")
      and abs(pf_hot - svc.region_factor(_hot)) < 1e-9,
      f"{pf_north} vs north, {pf_hot} vs hot")

# 粘性抹平：能躺着不站着 的档案学习层偏离更小
_snap3 = CharacterSnapshot(giantess_id="g3", name="t",
                           selected_tags=["能躺着不站着"])
_snap3.evolution = _rows_hist
_snap3.model_tuning = ModelTuning(
    spatial_priors=priors_for_tags(["能躺着不站着"]))
profile_flat = build_spatial_profile(_snap3)
base_hot = svc.region_factor(_hot)
flat_hot = profile_flat.region_factor(_hot)
check("正粘性放大已到访分支偏好", flat_hot > base_hot,
      f"{flat_hot} vs {base_hot}")

# 鸭子类型装配（无 CharacterSnapshot 也可；无烙印属性 → 先验全中性）
class _Duck:
    evolution = _rows_hist
    activity_regions = []
_duck_profile = build_spatial_profile(_Duck())
check("鸭子类型可装配", _duck_profile.learned.has_data())
check("鸭子类型无烙印时先验中性",
      _duck_profile.priors == SpatialPriorsParams())

# 运行时不再按标签现算：标签在而烙印默认（未烙印/旧档）→ 先验中性
_snap_tags_only = CharacterSnapshot(giantess_id="g4", name="t",
                                    selected_tags=["旅行"])
_snap_tags_only.evolution = _rows_hist
check("装配不读 selected_tags（烙印缺省全中性）",
      build_spatial_profile(_snap_tags_only).priors
      == SpatialPriorsParams())

# ---------------- 5. 环境份额先验的烙印路径 ----------------
# TAG 两表不再是运行时钩子：包调制先验的唯一入口是覆盖
# CreationService.derive_model_tuning（行为锁见 tests/check_model_tuning.py）；
# 这里只锁默认合并的形状契约。
_shares = env_priors_for_tags(["地理测量"])
check("环境先验默认合并归一",
      _shares is not None and abs(sum(_shares) - 1.0) < 1e-9, str(_shares))
check("未入表标签无环境先验",
      env_priors_for_tags(["四处走走"]) is None)
_merged = env_priors_for_tags(["旅行", "地理测量"])
check("多标签环境份额逐箱相加后归一",
      _merged is not None and abs(sum(_merged) - 1.0) < 1e-9)

# ---------------- 结论 ----------------
_log.flush()
sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__
if failures:
    print(f"[check_spatial_profile] FAILED {len(failures)}/{total}: {failures}")
else:
    print(f"[check_spatial_profile] PASSED {total}/{total}")
print(f"[check_spatial_profile] report: {_report_path}")
sys.exit(1 if failures else 0)
