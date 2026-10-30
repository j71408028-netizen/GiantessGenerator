"""副本进入准入自检：地址距离、身高区间、校验器诊断与仓库迁移。

用法：``python tests/check_scenario_admission.py``
输出：控制台一行 ASCII 结论 + UTF-8 报告文件路径。

只覆盖纯逻辑（不建窗口、不联网）。地址文本使用 ``core.address_model`` 的
规范格式：``<规模>@<维度>-<一级>-<二级>-<三级>``。
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_report_path = os.path.join(tempfile.mkdtemp(prefix="scenario_admission_"), "report.txt")
_log = open(_report_path, "w", encoding="utf-8")
sys.stdout = _log
sys.stderr = _log

failures = []
total = 0
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check(name, ok, extra=""):
    global total
    total += 1
    print(("  OK   " if ok else "  FAIL ") + name + (f"  <{extra}>" if extra and not ok else ""))
    if not ok:
        failures.append(name)


from dungeon import admission, schema  # noqa: E402
from dungeon.validate import validate_scenario_config  # noqa: E402

# ---------------- 1. 空模板与字段声明 ----------------
_empty = schema.empty_scenario_config()
check("空模板含四项准入字段",
      all(key in _empty for key in admission.ADMISSION_FIELDS),
      set(admission.ADMISSION_FIELDS) - set(_empty))
check("四项准入字段都在 schema 声明内",
      set(admission.ADMISSION_FIELDS) <= set(schema.known_scenario_keys()),
      set(admission.ADMISSION_FIELDS) - set(schema.known_scenario_keys()))
check("准入字段默认不限",
      _empty["entry_address"] == ""
      and _empty["entry_address_max_distance_m"] is None
      and _empty["entry_height_min_m"] is None
      and _empty["entry_height_max_m"] is None)

# ---------------- 2. 地址距离 ----------------
_ADDR_1 = "1e4_1e3_1e2@TEST-A-B-1"
_ADDR_2 = "1e4_1e3_1e2@TEST-A-B-2"
_FAR_WORLD = "1e4_1e3_1e2@OTHER-A-B-1"

_config = {
    admission.FIELD_ADDRESS: _ADDR_1,
    admission.FIELD_MAX_DISTANCE: 50,
}
check("地址相同距离 0，可进入",
      admission.check_entry_requirements(_config, position=_ADDR_1) == "")
check("同维度、分歧级距离 100m > 上限 50m，拒绝",
      "不超过" in admission.check_entry_requirements(_config, position=_ADDR_2))
check("上限 100m 时距离 100m 可进入",
      admission.check_entry_requirements(
          {admission.FIELD_ADDRESS: _ADDR_1,
           admission.FIELD_MAX_DISTANCE: 100},
          position=_ADDR_2) == "")
check("不同维度拒绝",
      "同一维度" in admission.check_entry_requirements(
          {admission.FIELD_ADDRESS: _ADDR_1,
           admission.FIELD_MAX_DISTANCE: 1000},
          position=_FAR_WORLD))
check("角色没有地址时跳过地址准入",
      admission.check_entry_requirements(_config, position="") == "")
check("角色地址无法解析时跳过地址准入",
      admission.check_entry_requirements(_config, position="坏地址") == "")
check("方案地址写坏时按未配置处理（不拦人）",
      admission.check_entry_requirements(
          {admission.FIELD_ADDRESS: "坏地址",
           admission.FIELD_MAX_DISTANCE: 0},
          position=_ADDR_2) == "")
check("距离上限 0 表示必须同址",
      "不超过" in admission.check_entry_requirements(
          {admission.FIELD_ADDRESS: _ADDR_1,
           admission.FIELD_MAX_DISTANCE: 0},
          position=_ADDR_2))
check("只配地址不配距离 = 不限制",
      admission.check_entry_requirements(
          {admission.FIELD_ADDRESS: _ADDR_1},
          position=_FAR_WORLD) == "")

_COARSE = "1e4_1e3_1e2@TEST-A"
_FINE = "1e4_1e3_1e2@TEST-A-B"
check("位置比方案粗时用距离上界保守拒绝",
      "精度" in admission.check_entry_requirements(
          {admission.FIELD_ADDRESS: _FINE,
           admission.FIELD_MAX_DISTANCE: 50},
          position=_COARSE))
check("位置粗但上界仍在上限内时允许",
      admission.check_entry_requirements(
          {admission.FIELD_ADDRESS: _FINE,
           admission.FIELD_MAX_DISTANCE: 20000},
          position=_COARSE) == "")
check("位置比方案细时目标区域按 0 距离计",
      admission.check_entry_requirements(
          {admission.FIELD_ADDRESS: _COARSE,
           admission.FIELD_MAX_DISTANCE: 0},
          position=_FINE) == "")

# ---------------- 3. 身高区间 ----------------
_height_config = {
    admission.FIELD_HEIGHT_MIN: 50,
    admission.FIELD_HEIGHT_MAX: 300,
}
check("身高落在区间内可进入",
      admission.check_entry_requirements(_height_config, height=100) == "")
check("身高低于下限拒绝",
      "不低于" in admission.check_entry_requirements(_height_config, height=10))
check("身高高于上限拒绝",
      "不高于" in admission.check_entry_requirements(_height_config, height=500))
check("只设下限：上限不限",
      admission.check_entry_requirements(
          {admission.FIELD_HEIGHT_MIN: 50}, height=100000) == "")
check("只设上限：下限不限",
      admission.check_entry_requirements(
          {admission.FIELD_HEIGHT_MAX: 100}, height=1) == "")
check("身高未知时不拦人",
      admission.check_entry_requirements(_height_config, height=None) == "")
check("地址与身高同时满足才可进入",
      admission.check_entry_requirements(
          {**_config, **_height_config}, position=_ADDR_1, height=100) == "")
check("地址满足但身高不满足仍拒绝",
      "身高" in admission.check_entry_requirements(
          {**_config, **_height_config}, position=_ADDR_1, height=10))

# ---------------- 4. 校验器诊断 ----------------
_diags = validate_scenario_config({
    admission.FIELD_ADDRESS: "坏地址",
    admission.FIELD_MAX_DISTANCE: -1,
    admission.FIELD_HEIGHT_MIN: 300,
    admission.FIELD_HEIGHT_MAX: 50,
})
check("非法地址报 warning",
      any(d.level == "warning" and d.path == admission.FIELD_ADDRESS
          for d in _diags))
check("负距离报 warning",
      any(d.level == "warning" and d.path == admission.FIELD_MAX_DISTANCE
          for d in _diags))
check("身高下限大于上限报 warning",
      any(d.level == "warning" and d.path == admission.FIELD_HEIGHT_MIN
          for d in _diags))

_no_address = validate_scenario_config({admission.FIELD_MAX_DISTANCE: 100})
check("配了距离没配地址报 warning",
      any(d.level == "warning" and d.path == admission.FIELD_MAX_DISTANCE
          for d in _no_address))

_no_distance = validate_scenario_config({admission.FIELD_ADDRESS: _ADDR_1})
check("配了地址没配距离报 info",
      any(d.level == "info" and d.path == admission.FIELD_MAX_DISTANCE
          for d in _no_distance))

_clean = schema.empty_scenario_config()
_clean["chapters"] = [{"name": "开场", "start": True},
                      {"name": "终幕", "ending": True}]
_clean["entry_address"] = _ADDR_1
_clean["entry_address_max_distance_m"] = 100
_clean["entry_height_min_m"] = 7.5
_clean["entry_height_max_m"] = 150000
_clean_diags = validate_scenario_config(_clean)
check("合法准入配置没有 error/warning",
      not [d for d in _clean_diags if d.level in ("error", "warning")],
      "\n".join(str(d) for d in _clean_diags))

# ---------------- 5. 仓库迁移 ----------------
from persistence.scenario_repo import ScenarioRepo  # noqa: E402

_tmp = tempfile.mkdtemp(prefix="scenario_admission_repo_")
_repo = ScenarioRepo(data_dir=_tmp)
_repo.save_config("default", {
    admission.FIELD_ADDRESS: "  ",
    admission.FIELD_MAX_DISTANCE: "-5",
    admission.FIELD_HEIGHT_MIN: "10",
    admission.FIELD_HEIGHT_MAX: 100,
})
_loaded = _repo.load_config("default")
check("仓库迁移：地址去空白、非法距离归为不限",
      _loaded[admission.FIELD_ADDRESS] == ""
      and _loaded[admission.FIELD_MAX_DISTANCE] is None)
check("仓库迁移：身高区间保留合法值",
      _loaded[admission.FIELD_HEIGHT_MIN] == 10.0
      and _loaded[admission.FIELD_HEIGHT_MAX] == 100.0)

# ---------------- 6. 入口接线（AST/源码级） ----------------
def _source(rel_path):
    with open(os.path.join(_PROJECT_ROOT, rel_path), encoding="utf-8") as fh:
        return fh.read()


for _rel in ("dungeon/window/base.py", "ui/mini/app.py", "ui/challenge/__init__.py"):
    check(f"{_rel} 调用了进入准入检查",
          "check_entry_requirements" in _source(_rel))
check("窗口入口在扣行动点之前检查准入",
      _source("dungeon/window/base.py").index("check_entry_requirements")
      < _source("dungeon/window/base.py").index("entry_cost = max(0"))

# ---------------- 结论 ----------------
_log.flush()
sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__
if failures:
    print(f"[check_scenario_admission] FAILED {len(failures)}/{total}: {failures}")
else:
    print(f"[check_scenario_admission] PASSED {total}/{total}")
print(f"[check_scenario_admission] report: {_report_path}")
sys.exit(1 if failures else 0)
