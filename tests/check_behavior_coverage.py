# -*- coding: utf-8 -*-
"""行为包覆盖基线自检：目录 key 的文档行与默认 / 覆盖链路全覆盖。

用法：``python tests/check_behavior_coverage.py``
与 ``check_behavior_catalog``（元数据与文档覆盖）互补，遍历 ``catalog()``：

- 每个 key 在 designs/ 组文档中有**专属表格行**（首列），仅在正文提及不算——
  防止「加了钩子没写文档」；
- 函数 key 的默认实现可解析，且用默认实现本身注册覆盖能走通（签名校验、
  resolve 链路对全目录可用）；常量 key 的 ``resolve_value`` 默认值与同值
  覆盖注册可走通——防止「目录条目在运行时不可用」。

断言锁结构不锁数值：默认值与签名的语义由各域自检负责。
"""

import os
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_report_path = os.path.join(
    tempfile.mkdtemp(prefix="behavior_coverage_"), "report.txt")
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


import core.logic  # noqa: E402,F401  注册全部钩子模块
import services.chat  # noqa: E402,F401
import services.character.offline  # noqa: E402,F401
import services.character.profile_text  # noqa: E402,F401
import services.exploration.arbiter  # noqa: E402,F401
import services.exploration.report  # noqa: E402,F401
import services.creation_service  # noqa: E402,F401
import services.state_service  # noqa: E402,F401
from core.behavior_runtime import get_runtime  # noqa: E402

_rt = get_runtime()
_catalog = _rt.catalog()

# ---------------- 1. 文档行覆盖 ----------------

# 行为包钩子文档可分布在 designs/ 组内任意页面（机制可拆为目录多页），
# 拼接全组 .md 后校验；表格行正则锚定钩子表首列，扫宽不会被正文提及误满足。
_designs_dir = pathlib.Path(__file__).resolve().parents[1] / "docs" / "designs"
_docs_paths = sorted(_designs_dir.rglob("*.md"))
_docs = "\n\n".join(p.read_text(encoding="utf-8") for p in _docs_paths)
check("行为包文档源非空", bool(_docs_paths), str(_designs_dir))
print(f"  行为包文档源：{len(_docs_paths)} 篇（docs/designs/ 全组）")

_missing_rows = []
for entry in _catalog:
    key = entry["key"]
    name = key.split(".", 1)[1]
    # 钩子表格首列：`<key>` 或无 scope 前缀的 `<name>`（logic / StateService 组）
    if not re.search(rf"^\| *`(?:{re.escape(key)}|{re.escape(name)})` *\|",
                     _docs, re.M):
        _missing_rows.append(key)
check("每个 key 在文档中有专属表格行", not _missing_rows, str(_missing_rows))

# ---------------- 2. 默认 / 覆盖链路全覆盖 ----------------

_plumbing = []
try:
    for entry in _catalog:
        key = entry["key"]
        if entry["kind"] == "function":
            impl = _rt.default(key)
            if not callable(impl):
                _plumbing.append(f"{key}: 默认实现不可调用")
                continue
            _rt.override(key, impl)
            if _rt.resolve(key) is not impl:
                _plumbing.append(f"{key}: 覆盖注册后 resolve 未命中")
        else:
            value = _rt.resolve_value(key)
            _rt.override(key, value)
            if _rt.resolve_value(key) != value:
                _plumbing.append(f"{key}: 常量覆盖后取值不一致")
finally:
    _rt.reset()
check("全目录默认实现 / 覆盖链路可用", not _plumbing, str(_plumbing))
check("覆盖链路试完后已复位", not _rt.resolve(
    _catalog[0]["key"]) and _rt.resolve_value(_catalog[0]["key"]) is not None)

# ---------------- 3. 目录规模记录（防无意识回退） ----------------

check("目录非空且含函数与常量两类",
      bool(_catalog)
      and {e["kind"] for e in _catalog} == {"function", "const"})
print(f"  目录现状：{len(_catalog)} 项（"
      f"{sum(1 for e in _catalog if e['kind'] == 'function')} 函数 + "
      f"{sum(1 for e in _catalog if e['kind'] == 'const')} 常量，"
      f"{len({e['scope'] for e in _catalog})} 个 scope）")

# ---------------- 结论 ----------------

_log.flush()
sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__
if failures:
    print(f"[check_behavior_coverage] FAILED {len(failures)}/{total}: {failures}")
else:
    print(f"[check_behavior_coverage] PASSED {total}/{total}")
print(f"[check_behavior_coverage] report: {_report_path}")
sys.exit(1 if failures else 0)
