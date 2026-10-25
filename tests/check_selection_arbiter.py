# -*- coding: utf-8 -*-
"""选取仲裁器自检（P4a）：车道窗口、近匹配提拔、覆盖报告与兼容判定。

用法：``python tests/check_selection_arbiter.py``
只覆盖纯逻辑，不建窗口、不联网。断言锁语义（窗口排除、容差带内提拔、
覆盖计数），不锁权重数值——调参不应误报。
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_report_path = os.path.join(tempfile.mkdtemp(prefix="arbiter_"), "report.txt")
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


from core.logic import select_quip_with_budget  # noqa: E402
from core.models import Landmark  # noqa: E402
from services.character.spatial import SpatialProfile, priors_for_tags  # noqa: E402
from services.exploration.address_plan import AddressPlanner  # noqa: E402


# ---------------- 1. select 的车道窗口（裁定级） ----------------
def _matrix(entries):
    """{(i,d): [(style, text), ...]} → quips_working 形状。"""
    return {"large": {coord: [{"text": t, "style": s, "step": 0.5}
                              for s, t in items]
                      for coord, items in entries.items()}}


_qw = _matrix({(2, 2): [("X", "X的描述")], (3, 3): [("X", "X的远处描述")]})

_text, _style, _coord, _step, _a, _b = select_quip_with_budget(
    "large", 2, 2, _qw, set(), 0.0, 0.0, 1.0)
check("无窗口时旧语义不变", _text == "X的描述" and _style == "X")
check("选中条目仍被消费（pop）",
      all(q["text"] != "X的描述" for qlist in _qw["large"].values() for q in qlist))

_qw = _matrix({(2, 2): [("X", "X的描述")]})
_text, _style, _coord, _step, _a, _b = select_quip_with_budget(
    "large", 2, 2, _qw, set(), 0.0, 0.0, 1.0, lanes=[("Y", 1.0)])
check("窗口外风格不可选（返回 None）", _text is None)
_qw = _matrix({(2, 2): [("X", "X的描述"), ("Y", "Y的描述")]})
_seen = set()
for _ in range(8):   # 加权采样下窗口外风格一次都不该出现
    _text, _style, _coord, _step, _a, _b = select_quip_with_budget(
        "large", 2, 2, _qw, set(), 0.0, 0.0, 1.0, lanes=[("X", 1.0), ("Y", 0.5)])
    if _text is None:
        continue
    _seen.add(_style)
check("窗口内只出现允许风格", _seen <= {"X", "Y"} and _seen)

# ---------------- 仲裁测试目录 ----------------
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
    def __init__(self, landmarks, registers, quip_registers, quips,
                 styles, quip_styles, comparison_order="match"):
        self.settings = {"comparison_count": 5,
                         "comparison_order": comparison_order}
        self.merged_landmarks = list(landmarks)
        self.selected_styles = list(styles)
        self.selected_quip_styles = list(quip_styles)
        self.landmark_repo = FakeLandmarkRepo(registers)
        self.quip_repo = FakeQuipRepo(quip_registers)
        self.quips = quips
        self._landmark_styles = {
            id(lm): style for lm, style in
            ((x, getattr(x, "_style", "")) for x in landmarks) if style
        }


def _arbiter(catalog):
    return SelectionArbiter(catalog, AddressPlanner(catalog))


def _cand(lm, ratio, size):
    return {"landmark": lm, "part": "身高", "ratio": ratio,
            "size": size, "match_score": abs(1 - ratio), "posture": [1]}


from services.exploration.arbiter import SelectionArbiter  # noqa: E402

_SIZE_CAT = "large"
_COORDS = (2, 2)
_POOL = {_SIZE_CAT: {(2, 2): [{"text": f"{s}的描述", "style": s, "step": 0.5}
                              for s in ("RICH", "RICH", "RICH", "RICH",
                                        "RICH", "RICH", "IDLE", "IDLE")]}}


def _scene(order_mode="size_desc"):
    """星槽（好匹配无覆盖）× 覆盖槽（近匹配有池）的场景目录。"""
    starved = Landmark("星槽地标", 200, "vertical", "unique", address="B-1")
    starved._style = "S"
    covered = Landmark("覆盖地标", 100, "vertical", "unique", address="C-1")
    covered._style = "S"
    quip_regs = {"RICH": "1e5_1e3_1e2@W-A-C", "IDLE": "1e5_1e3_1e2@W-Z"}
    catalog = FakeCatalog(
        [starved, covered],
        {"S": "1e5_1e3_1e2@W-A"},
        quip_regs, dict(_POOL), ["S"], list(quip_regs),
        comparison_order=order_mode)
    return catalog, starved, covered


# ---------------- 2. 近匹配提拔（裁定级） ----------------
_catalog, _starved, _covered = _scene()
_plan = _arbiter(_catalog).arbitrate(
    [_cand(_starved, 1.0, 200), _cand(_covered, 1.25, 100)],
    "1e5_1e3_1e2@W-A-B", height=1000.0, durability={},
    skip_base_prob=0.0, coords=_COORDS, size_cat=_SIZE_CAT)
check("星槽在容差带内让位给有覆盖的近匹配",
      [c["landmark"].name for c in _plan["comparisons"]][0] == "覆盖地标",
      str([c["landmark"].name for c in _plan["comparisons"]]))
check("提拔记录可解释",
      len(_plan["promotions"]) == 1
      and _plan["promotions"][0]["starved"] == "星槽地标"
      and _plan["promotions"][0]["promoted"] == "覆盖地标")
check("被提拔槽位有车道窗口且充裕",
      _plan["chosen"][0].lanes and _plan["chosen"][0].affordance > 0
      and _plan["chosen"][0].promoted)
check("星槽标记 starved 且保留在余量中",
      any(not r.promoted and r.cand["landmark"].name == "星槽地标"
          for r in _plan["chosen"]))

# 容差带外不提拔
_catalog2, _s2, _c2 = _scene()
_far = _cand(_c2, 3.0, 100)   # |log 3| ≈ 1.1 > τ
_plan2 = _arbiter(_catalog2).arbitrate(
    [_cand(_s2, 1.0, 200), _far],
    "1e5_1e3_1e2@W-A-B", height=1000.0, durability={},
    skip_base_prob=0.0, coords=_COORDS, size_cat=_SIZE_CAT)
check("容差带外的匹配差不被提拔",
      _plan2["promotions"] == []
      and [c["landmark"].name for c in _plan2["comparisons"]][0] == "星槽地标")

# 提拔上限（limit/2）
_s3 = [Landmark(f"星槽{i}", 200, "vertical", "unique", address="B-1") for i in range(3)]
for lm in _s3:
    lm._style = "S"
_cov2 = [Landmark(f"覆盖{i}", 100, "vertical", "unique", address="C-1")
         for i in range(2)]
for lm in _cov2:
    lm._style = "S"
_quip_regs = {"RICH": "1e5_1e3_1e2@W-A-C"}
_catalog3 = FakeCatalog(_s3 + _cov2, {"S": "1e5_1e3_1e2@W-A"},
                        _quip_regs, dict(_POOL), ["S"], list(_quip_regs),
                        comparison_order="size_desc")
_plan3 = _arbiter(_catalog3).arbitrate(
    [_cand(lm, 1.0, 200) for lm in _s3] + [_cand(lm, 1.2, 100) for lm in _cov2],
    "1e5_1e3_1e2@W-A-B", height=1000.0, durability={},
    skip_base_prob=0.0, coords=_COORDS, size_cat=_SIZE_CAT)
check("提拔数受上限约束", len(_plan3["promotions"]) == 2,
      str(_plan3["promotions"]))

# ---------------- 3. 覆盖报告 ----------------
check("浪费风格进入 coverage",
      "IDLE" in _plan["coverage"]["wasted_styles"])
check("星槽数计入 coverage",
      _plan["coverage"]["starved_slots"] >= 1)
check("fired_styles 初始为空", _plan["coverage"]["fired_styles"] == [])

# ---------------- 4. anchored_bias（TAG 迁移） ----------------
_portable_lm = Landmark("流动地标", 100, "vertical", "common")
_portable_lm._style = "S"
_anchored_lm = Landmark("锚定地标", 100, "vertical", "unique", address="B")
_anchored_lm._style = "S"
_catalog4 = FakeCatalog([_portable_lm, _anchored_lm],
                        {"S": "1e5_1e3_1e2@W-A"}, {}, {}, ["S"], [])
_arb4 = _arbiter(_catalog4)
_pos_fine = "1e5_1e3_1e2@W-A-B-1"
_plan4 = _arb4.arbitrate(
    [_cand(_portable_lm, 1.0, 100), _cand(_anchored_lm, 1.0, 100)],
    _pos_fine, height=1000.0, durability={}, skip_base_prob=0.0)
check("中性档案下等分槽位保持原序（旧档锚点）",
      [c["landmark"].name for c in _plan4["comparisons"]] == ["流动地标", "锚定地标"])
_plan4 = _arb4.arbitrate(
    [_cand(_portable_lm, 1.0, 100), _cand(_anchored_lm, 1.0, 100)],
    _pos_fine, height=1000.0, durability={}, skip_base_prob=0.0,
    profile=SpatialProfile(priors=priors_for_tags(["旅行"])))
check("旅行先验把锚定内容排到 portable 之前",
      [c["landmark"].name for c in _plan4["comparisons"]][0] == "锚定地标")

# ---------------- 结论 ----------------
_log.flush()
sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__
if failures:
    print(f"[check_selection_arbiter] FAILED {len(failures)}/{total}: {failures}")
else:
    print(f"[check_selection_arbiter] PASSED {total}/{total}")
print(f"[check_selection_arbiter] report: {_report_path}")
sys.exit(1 if failures else 0)
