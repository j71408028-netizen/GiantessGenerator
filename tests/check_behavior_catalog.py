# -*- coding: utf-8 -*-
"""行为包 hook 目录自检（第 0 步）：语义元数据与文档覆盖。

用法：``python tests/check_behavior_catalog.py``
只读注册表与文档，不建窗口、不联网、不碰 data/。

验证：
- 每个 hook 都带 layer / contract / since / deprecated 元数据；
- 层级与契约组合合法，scope 的默认归属符合登记表；
- 已知例外（profile、内容常量）分类正确；
- 已删 key（offline.model.* / spatial.TAG_* / recover_evolution）不回潮；
- 目录按 key 排序，且每个 key 都能在 designs/ 组文档中找到。
"""

import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.behavior_runtime import (  # noqa: E402
    CONTRACT_DATA, CONTRACT_POINT, CONTRACT_STRATEGY,
    KIND_CONST, KIND_FUNCTION,
    LAYER_CONTENT, LAYER_INFRA, LAYER_MODULE, get_runtime,
)
import core.logic  # noqa: E402,F401  注册 logic 函数与常量
import services.chat  # noqa: E402,F401  注册 chat 提示词 / 节奏钩子
import services.character.offline  # noqa: E402,F401
import services.character.profile_text  # noqa: E402,F401
import services.exploration.arbiter  # noqa: E402,F401
import services.exploration.report  # noqa: E402,F401  注册 report 引擎常量
import services.creation_service  # noqa: E402,F401
import services.state_service  # noqa: E402,F401

_report_path = os.path.join(
    tempfile.mkdtemp(prefix="behavior_catalog_"), "report.txt")
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


# scope 的默认语义归属。新 scope 必须先在这里登记，再写钩子；
# 特例在 scope 内按 key 单独断言。
SCOPE_LAYERS = {
    "CreationService": {LAYER_INFRA, LAYER_MODULE},
    "StateService": {LAYER_INFRA, LAYER_MODULE},
    "chat": {LAYER_INFRA, LAYER_MODULE},
    "logic": {LAYER_INFRA, LAYER_CONTENT},
    "offline": {LAYER_MODULE},
    "profile": {LAYER_MODULE},
    "report": {LAYER_INFRA, LAYER_MODULE},
}
LAYER_CONTRACTS = {
    LAYER_INFRA: {CONTRACT_POINT, CONTRACT_DATA},
    LAYER_MODULE: {CONTRACT_STRATEGY, CONTRACT_DATA},
    LAYER_CONTENT: {CONTRACT_DATA},
}

_catalog = get_runtime().catalog()
_by_key = {entry["key"]: entry for entry in _catalog}


def _classify(key, layer, contract, kind):
    entry = _by_key.get(key)
    check(
        f"{key} 分类为 {layer}/{contract}",
        entry is not None
        and entry["layer"] == layer
        and entry["contract"] == contract
        and entry["kind"] == kind,
        str(entry),
    )


# ---------------- 1. 元数据完整性 ----------------

check("目录非空", bool(_catalog))
check("目录按 key 排序",
      [entry["key"] for entry in _catalog]
      == sorted(entry["key"] for entry in _catalog))
check("目录 key 不重复",
      len(_by_key) == len(_catalog))

_bad_meta = []
_bad_scope = []
_bad_pair = []
for entry in _catalog:
    key = entry["key"]
    scope = entry["scope"]
    layer = entry["layer"]
    contract = entry["contract"]
    if key.split(".", 1)[0] != scope:
        _bad_meta.append(f"{key}: scope={scope!r}")
    if entry["kind"] not in (KIND_FUNCTION, KIND_CONST):
        _bad_meta.append(f"{key}: kind={entry['kind']!r}")
    if not entry["since"]:
        _bad_meta.append(f"{key}: since 为空")
    if not isinstance(entry["deprecated"], bool):
        _bad_meta.append(f"{key}: deprecated 不是 bool")
    if not isinstance(entry["params"], list):
        _bad_meta.append(f"{key}: params 不是 list")
    if scope not in SCOPE_LAYERS:
        _bad_scope.append(scope)
    elif layer not in SCOPE_LAYERS[scope]:
        _bad_meta.append(f"{key}: {scope} 不应为 {layer}")
    if layer not in LAYER_CONTRACTS or contract not in LAYER_CONTRACTS[layer]:
        _bad_pair.append(f"{key}: {layer}/{contract}")

check("元数据字段完整", not _bad_meta, str(_bad_meta))
check("所有 scope 已登记语义归属", not _bad_scope, str(sorted(set(_bad_scope))))
check("层级 / 契约组合合法", not _bad_pair, str(_bad_pair))

# ---------------- 2. 已知例外与代表项 ----------------

_classify("StateService.clamp_coordinates", LAYER_INFRA, CONTRACT_POINT,
          KIND_FUNCTION)
check("StateService.recover_evolution 已随出身烙印基线删除",
      "StateService.recover_evolution" not in _by_key)
_classify("CreationService.derive_model_tuning", LAYER_MODULE,
          CONTRACT_STRATEGY, KIND_FUNCTION)
check("CreationService.derive_model_tuning since 2.0",
      _by_key["CreationService.derive_model_tuning"]["since"] == "2.0")
_classify("offline.recover_plan", LAYER_MODULE, CONTRACT_STRATEGY,
          KIND_FUNCTION)
_classify("offline.report_text", LAYER_MODULE, CONTRACT_STRATEGY,
          KIND_FUNCTION)
check("offline.model.* / spatial.TAG_* 已随出身烙印基线删除", not [
    key for key in _by_key
    if key.startswith("offline.model.") or key.startswith("spatial.")])
_classify("report.lanes", LAYER_MODULE, CONTRACT_STRATEGY, KIND_FUNCTION)
_classify("report.joint_score", LAYER_MODULE, CONTRACT_STRATEGY,
          KIND_FUNCTION)
_classify("report.promotions", LAYER_MODULE, CONTRACT_STRATEGY,
          KIND_FUNCTION)
_classify("report.model.lane_weights", LAYER_INFRA, CONTRACT_DATA,
          KIND_CONST)
_classify("report.model.near_match_tau", LAYER_INFRA, CONTRACT_DATA,
          KIND_CONST)
_classify("report.model.breakthrough_scale", LAYER_INFRA, CONTRACT_DATA,
          KIND_CONST)
_classify("logic.compute_casualty", LAYER_INFRA, CONTRACT_POINT,
          KIND_FUNCTION)
_classify("logic.PREDEFINED_TAGS", LAYER_CONTENT, CONTRACT_DATA,
          KIND_CONST)
_classify("profile.describe_rhythm", LAYER_MODULE, CONTRACT_STRATEGY,
          KIND_FUNCTION)
_classify("chat.framing", LAYER_MODULE, CONTRACT_STRATEGY, KIND_FUNCTION)
_classify("chat.world_context", LAYER_MODULE, CONTRACT_STRATEGY,
          KIND_FUNCTION)
_classify("chat.topic_lens", LAYER_MODULE, CONTRACT_STRATEGY,
          KIND_FUNCTION)
_classify("chat.coord", LAYER_MODULE, CONTRACT_STRATEGY, KIND_FUNCTION)
_classify("chat.model.read_delay_range", LAYER_INFRA, CONTRACT_DATA,
          KIND_CONST)

_state_module = [entry["key"] for entry in _catalog
                 if entry["scope"] == "StateService"
                 and entry["layer"] != LAYER_INFRA]
check("StateService 全部为基础设施层",
      _state_module == [], str(_state_module))

_since_bad = [entry["key"] for entry in _catalog if entry["since"] != "2.0"]
check("全目录 since 以 2.0 为基线", not _since_bad, str(_since_bad))

# ---------------- 3. 文档覆盖 ----------------

# 行为包钩子文档可分布在 designs/ 组内任意页面（机制可拆为目录多页），
# 拼接全组 .md 后做 key 级覆盖校验。
_designs_dir = pathlib.Path(__file__).resolve().parents[1] / "docs" / "designs"
_docs_paths = sorted(_designs_dir.rglob("*.md"))
try:
    _docs = "\n\n".join(p.read_text(encoding="utf-8") for p in _docs_paths)
except OSError as e:
    _docs = ""
    check("行为包文档源可读", False, str(e))
else:
    check("行为包文档源可读", bool(_docs_paths), str(_designs_dir))
    print(f"  行为包文档源：{len(_docs_paths)} 篇（docs/designs/ 全组）")

_missing_docs = [
    entry["key"] for entry in _catalog
    if entry["key"] not in _docs
    and entry["key"].split(".", 1)[1] not in _docs
]
check("目录中的 key 都能在行为包文档中找到",
      not _missing_docs, str(_missing_docs))

# ---------------- 结论 ----------------

_log.flush()
sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__
if failures:
    print(f"[check_behavior_catalog] FAILED {len(failures)}/{total}: {failures}")
else:
    print(f"[check_behavior_catalog] PASSED {total}/{total}")
print(f"[check_behavior_catalog] report: {_report_path}")
sys.exit(1 if failures else 0)
