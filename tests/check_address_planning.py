# -*- coding: utf-8 -*-
"""地址规划自检：地标参与方式、世界集合、可达性与 stuck 选项。

P4a 起选点循环由选取仲裁器（services/exploration/arbiter.py）承担，
本锁随之重定向；断言口径不变（comparisons 兼容键）。
旧档回归锚点：中性档案（无到访历史 / 无 TAG / 无活动区域）下，
纯地址行为与原规划器逐项一致。

用法：``python tests/check_address_planning.py``
只覆盖纯逻辑，不建窗口、不联网。
"""

import os
import sys
import tempfile
from dataclasses import replace
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_report_path = os.path.join(tempfile.mkdtemp(prefix="address_planning_"), "report.txt")
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


from core.address_model import resolve_full_address  # noqa: E402
from core.models import Landmark  # noqa: E402
from core.resource_ref import Ref  # noqa: E402
from services.character.spatial import SpatialProfile  # noqa: E402
from services.exploration.address_index import AddressIndex  # noqa: E402
from services.exploration.address_plan import AddressPlanner  # noqa: E402
from services.exploration.arbiter import (  # noqa: E402
    LANE_COVER, LANE_EXACT, LANE_FREE, LANE_WEIGHTS, SelectionArbiter,
)


class FakeLandmarkRepo:
    def __init__(self, registers):
        self.registers = dict(registers)

    def load_style_registers(self, styles):
        return {s: self.registers[s] for s in styles if s in self.registers}

    def load_style_address(self, style):
        return self.registers.get(style, "")


class FakeQuipRepo:
    def __init__(self, registers):
        self.registers = dict(registers)

    def load_style_registers(self, styles):
        return {s: self.registers[s] for s in styles if s in self.registers}


class FakeCatalog:
    def __init__(self, landmarks, registers=None, quip_registers=None,
                 styles=None, quip_styles=None):
        self.settings = {"comparison_count": 5}
        self.merged_landmarks = list(landmarks)
        self.selected_styles = list(styles or [])
        self.selected_quip_styles = list(quip_styles or [])
        self.landmark_repo = FakeLandmarkRepo(registers or {})
        self.quip_repo = FakeQuipRepo(quip_registers or {})
        self.quips = {}
        # 夹具用 _style 属性模拟「地标属于哪个风格」，与目录里的 merged_refs 同构
        self.merged_refs = [Ref.scoped("landmarks", getattr(x, "_style", ""), x.name)
                            for x in self.merged_landmarks]
        self.address_index = AddressIndex(self.landmark_repo)
        self.address_index.rebuild(zip(self.merged_refs, self.merged_landmarks))


def _arbiter(catalog):
    return SelectionArbiter(catalog, AddressPlanner(catalog))


def _cand(lm):
    return {"landmark": lm, "part": "body", "ratio": 1.0, "size": 1.0}


def _state(position, durability=None):
    return SimpleNamespace(position=position, landmark_durability=durability or {})


_STYLE = "1e5_1e3_1e2@W-A"

# ---------------- 1. 地标参与方式 ----------------
_unique = Landmark("U", 100, "vertical", "unique")
_unique._style = "S"
_common = Landmark("C", 100, "vertical", "common")
_common._style = "S"
_catalog = FakeCatalog([_unique, _common], {"S": _STYLE}, styles=["S"])
_planner = AddressPlanner(_catalog)

check("独特地标空地址继承风格注册地址",
      _planner.landmark_address(_unique) == resolve_full_address(_STYLE, ""))
check("均值地标空地址默认 portable",
      _planner.landmark_address(_common) == "")

_rel_unique = Landmark("Rel", 100, "vertical", "unique", address="B-1")
_rel_unique._style = "S"
_rel_catalog = FakeCatalog([_rel_unique], {"S": _STYLE}, styles=["S"])
check("地标相对后缀可解析",
      AddressPlanner(_rel_catalog).landmark_address(_rel_unique)
      == "1e5_1e3_1e2@!W-A-B-1")

_portable = Landmark("P", 100, "vertical", "unique", address=_STYLE,
                     address_mode="portable")
_portable._style = "S"
_portable_catalog = FakeCatalog([_portable], {"S": _STYLE}, styles=["S"])
check("显式 portable 不参与地址",
      AddressPlanner(_portable_catalog).landmark_address(_portable) == "")

# ---------------- 2. 均值地标显式地址不再无条件绕过 ----------------
_far_common = Landmark("FarCommon", 100, "vertical", "common",
                       address="1e5_1e3_1e2@W-A-B-1")
_far_common._style = "S"
_catalog = FakeCatalog([_far_common], {"S": _STYLE}, styles=["S"])
_plan = _arbiter(_catalog).arbitrate(
    [_cand(_far_common)], "1e5_1e3_1e2@W-A-C", height=1.0,
    durability={}, skip_base_prob=0.0)
check("均值地标显式地址参与距离筛选",
      _plan["engaged"] and not _plan["comparisons"]
      and _plan["stuck"]["reason"] == "no_reachable")

_portable_common = Landmark("Portable", 100, "vertical", "common")
_portable_common._style = "S"
_catalog = FakeCatalog([_portable_common], {"S": _STYLE}, styles=["S"])
_plan = _arbiter(_catalog).arbitrate(
    [_cand(_portable_common)], "1e5_1e3_1e2@W-A-C", height=1.0,
    durability={}, skip_base_prob=0.0)
check("均值地标空地址仍 portable",
      not _plan["engaged"] and _plan["comparisons"])

# ---------------- 3. 世界集合来自所有地址内容 ----------------
_other = Landmark("Other", 100, "vertical", "unique",
                  address="1e3_1e2_1e1@V-X")
_catalog = FakeCatalog([_other], {}, styles=[])
_plan = _arbiter(_catalog).arbitrate(
    [_cand(_other)], "1e3_1e2_1e1@W-Y", height=1.0,
    durability={}, skip_base_prob=0.0)
check("未注册风格的地标也能触发维度不匹配",
      _plan["stuck"]["reason"] == "world_mismatch"
      and "V" in _plan["stuck"]["worlds"])

# ---------------- 4. 坏位置 / 坏地址 ----------------
_bad_pos = Landmark("U", 100, "vertical", "unique")
_bad_pos._style = "S"
_catalog = FakeCatalog([_bad_pos], {"S": _STYLE}, styles=["S"])
_plan = _arbiter(_catalog).arbitrate(
    [_cand(_bad_pos)], "W-A", height=1.0, durability={}, skip_base_prob=0.0)
check("坏位置给出 invalid_position",
      _plan["stuck"]["reason"] == "invalid_position")

_bad_addr = Landmark("Bad", 100, "vertical", "unique",
                     address="1e3_1e2_1e1@V-X")
_bad_addr._style = "S"
_catalog = FakeCatalog([_bad_addr], {"S": _STYLE}, styles=["S"])
_plan = _arbiter(_catalog).arbitrate(
    [_cand(_bad_addr)], "", height=1.0, durability={}, skip_base_prob=0.0)
check("无法组合的地标地址不再退化为 portable",
      not _plan["engaged"] and not _plan["comparisons"]
      and _plan["stuck"]["reason"] == "invalid_address"
      and len(_plan["invalid"]) == 1)

# ---------------- 5. 粗定位保留可选性并标记不确定 ----------------
_uncertain = Landmark("Uncertain", 100, "vertical", "unique", address="B-1")
_uncertain._style = "S"
_catalog = FakeCatalog([_uncertain], {"S": _STYLE}, styles=["S"])
_plan = _arbiter(_catalog).arbitrate(
    [_cand(_uncertain)], "1e5_1e3_1e2@W-A", height=10.0,
    durability={}, skip_base_prob=0.0)
check("粗定位仍可选但标记 address_uncertain",
      _plan["comparisons"]
      and _plan["comparisons"][0].get("address_uncertain") is True)

# ---------------- 6. 描述车道分层（Q4） ----------------
_qregs = {
    "exact": "1e5_1e3_1e2@W-A-B-1",
    "coarse": "1e5_1e3_1e2@W-A",
    "other": "1e5_1e3_1e2@V-A",
    "portable": "",
}
_catalog = FakeCatalog([], {"S": _STYLE}, quip_registers=_qregs,
                       styles=["S"], quip_styles=list(_qregs))
_lanes = dict(_arbiter(_catalog).quip_lanes("1e5_1e3_1e2@W-A-B-1", _qregs))
check("同址车道为 exact、覆盖为 cover、未注册为 free",
      _lanes["exact"] == LANE_WEIGHTS[LANE_EXACT]
      and _lanes["coarse"] == LANE_WEIGHTS[LANE_COVER]
      and _lanes["portable"] == LANE_WEIGHTS[LANE_FREE])
check("不覆盖的维度与反向细地址不入窗口",
      "other" not in _lanes)
check("无地址地标全部风格 free",
      dict(_arbiter(_catalog).quip_lanes("", _qregs))
      == {s: LANE_WEIGHTS[LANE_FREE] for s in _qregs})
_chunk_regs = {"ck": "1e5_1e3_1e2@A!W-A"}
check("私有名冲突的风格不入窗口",
      _arbiter(_catalog).quip_lanes("1e5_1e3_1e2@B!W-A-B-1", _chunk_regs) == [])

# ---------------- 7. stuck_options 只给出可落地的其它维度 ----------------
_u1 = Landmark("U1", 100, "vertical", "unique")
_u1._style = "S"
_u2 = Landmark("U2", 100, "vertical", "unique",
               address="1e3_1e2_1e1@V-B")
_catalog = FakeCatalog([_u1, _u2], {"S": "1e3_1e2_1e1@W-A"}, styles=["S"])
_planner = AddressPlanner(_catalog)
_options = _planner.stuck_options(
    _state("1e3_1e2_1e1@W-A"),
    {"reason": "world_mismatch", "worlds": ["W", "V"]})
check("stuck_options 过滤当前世界并保留其它世界",
      _options["worlds"] == ["V"]
      and {x["name"] for x in _options["addresses"]} == {"U1", "U2"})

# ---------------- 8. 私有名约束参与可达与损毁判定 ----------------
_chunked = Landmark("Chunked", 100, "vertical", "unique", address="B-1")
_chunked._style = "S"
_catalog = FakeCatalog([_chunked], {"S": "1e5_1e3_1e2@A!W-A"}, styles=["S"])
_plan = _arbiter(_catalog).arbitrate(
    [_cand(_chunked)], "1e5_1e3_1e2@B!W-A", height=1.0,
    durability={}, skip_base_prob=0.0)
check("私有名冲突的地标视为不可达",
      _plan["engaged"] and not _plan["comparisons"]
      and _plan["stuck"]["reason"] == "no_reachable")
_plan = _arbiter(_catalog).arbitrate(
    [_cand(_chunked)], "1e5_1e3_1e2@A!W-A", height=1.0,
    durability={}, skip_base_prob=0.0)
check("私有名一致时正常可达", bool(_plan["comparisons"]))

# 附近存在私有名冲突的地标时不算“可用”，不应阻断 all_damaged 判定
_damaged = Landmark("NearDmg", 100, "vertical", "unique", address="B-2")
_damaged._style = "S"
_shuttle = Landmark("Shuttle", 100, "vertical", "common", address="B-2")
_shuttle._style = "S"
_other_chunk = Landmark("OtherChunk", 100, "vertical", "unique", address="B-2")
_other_chunk._style = "T"
_catalog = FakeCatalog([_damaged, _shuttle, _other_chunk],
                       {"S": "1e4_1e3_6e1@A!W-A", "T": "1e4_1e3_6e1@B!W-A"},
                       styles=["S", "T"])
_plan = _arbiter(_catalog).arbitrate(
    [_cand(_damaged), _cand(_shuttle), _cand(_other_chunk)],
    "1e4_1e3_6e1@A!W-A-B-1", height=10.0,
    durability={"landmarks:S/NearDmg": 0.3}, skip_base_prob=0.1)
check("私有名冲突的地标不阻断 all_damaged",
      _plan["comparisons"] and _plan["stuck"]["reason"] == "all_damaged")

# ---------------- 9. 活动区域软偏好（经空间档案） ----------------
_lm_east = Landmark("East1", 100, "vertical", "unique", address="B-1")
_lm_east._style = "S"
_lm_west = Landmark("West1", 100, "vertical", "unique", address="C-1")
_lm_west._style = "S"
_catalog = FakeCatalog([_lm_east, _lm_west], {"S": "1e5_1e3_1e2@W-A"}, styles=["S"])
_arb = _arbiter(_catalog)
_pos_west_hub = "1e5_1e3_1e2@W-A-M"
_plan = _arb.arbitrate(
    [_cand(_lm_east), _cand(_lm_west)], _pos_west_hub, height=1000.0,
    durability={}, skip_base_prob=0.0)
check("无活动区域时保持原顺序",
      [c["landmark"].name for c in _plan["comparisons"]] == ["East1", "West1"])
_plan = _arb.arbitrate(
    [_cand(_lm_east), _cand(_lm_west)], _pos_west_hub, height=1000.0,
    durability={}, skip_base_prob=0.0,
    profile=SpatialProfile(activity_regions=["1e5_1e3_1e2@W-A-C"]))
check("活动区域内地标前移（联合分增益）",
      [c["landmark"].name for c in _plan["comparisons"]] == ["West1", "East1"])
_plan = _arb.arbitrate(
    [_cand(_lm_east), _cand(_lm_west)], _pos_west_hub, height=1000.0,
    durability={}, skip_base_prob=0.0,
    profile=SpatialProfile(activity_regions=["坏地址", ""]))
check("活动区域全部无法解析时不影响顺序",
      [c["landmark"].name for c in _plan["comparisons"]] == ["East1", "West1"])

# ---------------- 10. 仲裁扩展字段 ----------------
check("仲裁结果携带覆盖报告与提拔记录",
      isinstance(_plan.get("coverage"), dict)
      and isinstance(_plan.get("promotions"), list)
      and isinstance(_plan.get("chosen"), list))
check("chosen 槽位与 comparisons 对齐",
      [r.cand for r in _plan["chosen"]] == _plan["comparisons"])

# ---------------- 11. 地址索引（S3）：身份、单次解析、双向查询 ----------------
_tower = Landmark("IndexTower", 200, "vertical", "unique")      # 继承风格地址
_tower._style = "S"
_near = Landmark("IndexNear", 120, "vertical", "unique", address="B-1")
_near._style = "S"
_far = Landmark("IndexFar", 900, "vertical", "unique", address="1e5_1e3_1e2@V-Q")
_split = Landmark("IndexSplit", 300, "vertical", "unique", address="1e5_1e3_1e2@W-Z")
_split._style = "S"
_idx_catalog = FakeCatalog([_tower, _near, _far, _split], {"S": "1e5_1e3_1e2@W-A"},
                           styles=["S"])
_idx_planner = AddressPlanner(_idx_catalog)
_idx = _idx_catalog.address_index

check("地标身份来自声明（风格 + 名称）",
      _idx_planner.ref(_tower).text == "landmarks:S/IndexTower")
check("耐久键与地址无关",
      _idx_planner.durability_key(_tower) == "landmarks:S/IndexTower")
check("改址不改身份（耐久不清零）",
      _idx_planner.durability_key(replace(_tower, address="1e5_1e3_1e2@W-Z"))
      == "landmarks:S/IndexTower")

_exported_lm = _idx_planner.landmarks_for_export()[0]
check("导出副本仍解析出同一完整地址",
      _idx_planner.landmark_address(_exported_lm)
      == _idx_planner.landmark_address(_tower),
      _idx_planner.landmark_address(_exported_lm))
check("导出副本的身份仍算得出来", _idx_planner.ref(_exported_lm) is not None)

check("反查：地域前缀命中该区域的地标",
      _idx.referencing("1e5_1e3_1e2@W-A")
      == sorted([_idx_planner.ref(_tower), _idx_planner.ref(_near)]),
      [str(r) for r in _idx.referencing("1e5_1e3_1e2@W-A")])
check("反查：其它维度只命中自己维度的地标",
      [str(r) for r in _idx.referencing("1e5_1e3_1e2@V-Q")]
      == [str(_idx_planner.ref(_far))],
      [str(r) for r in _idx.referencing("1e5_1e3_1e2@V-Q")])
check("反查：无地址的地标不进倒排", _idx.referencing("") == [])
check("空间候选：邻近地标可达",
      _idx_planner.ref(_near) in _idx.nearby("1e5_1e3_1e2@W-A-B", 1e6))
check("空间候选：超出可达半径的地标被过滤",
      set(_idx.nearby("1e5_1e3_1e2@W-A-B", 1e3))
      == {_idx_planner.ref(_tower), _idx_planner.ref(_near)},
      [str(r) for r in _idx.nearby("1e5_1e3_1e2@W-A-B", 1e3)])
check("索引重建后条目数与命中一致",
      len(_idx) == 4 and _idx.revision >= 1)

# ---------------- 结论 ----------------
_log.flush()
sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__
if failures:
    print(f"[check_address_planning] FAILED {len(failures)}/{total}: {failures}")
else:
    print(f"[check_address_planning] PASSED {total}/{total}")
print(f"[check_address_planning] report: {_report_path}")
sys.exit(1 if failures else 0)
