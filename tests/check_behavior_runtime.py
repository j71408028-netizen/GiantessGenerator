# -*- coding: utf-8 -*-
"""行为包运行时自检：注册表元数据、覆盖校验与启用诊断报告。

用法：``python tests/check_behavior_runtime.py``
只覆盖纯逻辑，不建窗口、不联网、不碰 data/（行为包目录建在系统临时目录）。

验证：
- 函数钩子：签名兼容的覆盖生效；缺参数、多出必填参数、非可调用、未知 key 的
  注册被拒绝，多出可选参数被接受；
- 常量钩子：同类型值与可调用对象可覆盖，类型不符被拒绝；可调用对象返回值
  不符时回退默认值并记入诊断；数值常量允许 int/float 互通；
- load_pack：正常包产出接管清单；坏注册逐条记入诊断不整包失效；
  缺失包 / 模块加载异常 / 无 register 模块 / 未注册任何覆盖均有诊断；
  重复注册保留最后一条并告警；reset 清覆盖、报告保留。
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.behavior_runtime import (  # noqa: E402
    BehaviorLoadReport, behavior_const, behavior_hook, get_runtime,
    reset_runtime,
)
from persistence.world_pack import WorldPackManifest  # noqa: E402

_report_path = os.path.join(
    tempfile.mkdtemp(prefix="behavior_runtime_"), "report.txt")
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


reset_runtime()
_rt = get_runtime()


# ---------------- 1. 函数钩子与签名校验 ----------------

@behavior_hook("diag", "fn")
def _fn(a, b=1):
    return (a, b)


@behavior_hook("diag", "noargs")
def _noargs():
    return 1


check("默认实现可用", _fn(2) == (2, 1))
_rt.override("diag.fn", lambda a, b=1: (a, b + 10))
check("签名兼容的覆盖生效", _fn(2) == (2, 11))
_rt.override("diag.fn", lambda *a, **kw: 7)
check("**kwargs 覆盖被接受", _fn(2) == 7)
_rt.override("diag.fn", lambda a, b=1: (a, b + 10))
_rt.override("diag.fn", lambda a: None)
check("缺参数覆盖被拒绝", _fn(2) == (2, 11))
_rt.override("diag.fn", lambda a, b=1, *, c: None)
check("多出必填参数覆盖被拒绝", _fn(2) == (2, 11))
_rt.override("diag.fn", lambda a, b=1, verbose=False: (a, b + 20))
check("多出可选参数覆盖被接受", _fn(2) == (2, 21))
_rt.override("diag.fn", "not-callable")
check("非可调用覆盖被拒绝", _fn(2) == (2, 21))
_rt.override("diag.nope", lambda: 1)
check("未知 key 注册不生效", _rt.resolve("diag.nope") is None)
_rt.override("diag.noargs", lambda x: 2)
check("无参默认实现拒绝多出必填参数", _noargs() == 1)


# ---------------- 2. 常量钩子 ----------------

_const_default = behavior_const("diag", "MY_TAGS", ["甲", "乙"])
check("behavior_const 原样返回值", _const_default == ["甲", "乙"])
check("resolve_value 回退默认值", _rt.resolve_value("diag.MY_TAGS") == ["甲", "乙"])
_rt.override("diag.MY_TAGS", ["丙"])
check("常量同类型值覆盖", _rt.resolve_value("diag.MY_TAGS") == ["丙"])
_rt.override("diag.MY_TAGS", lambda: ["丁"])
check("常量可调用对象覆盖", _rt.resolve_value("diag.MY_TAGS") == ["丁"])
_rt.override("diag.MY_TAGS", 123)
check("常量类型不符被拒绝", _rt.resolve_value("diag.MY_TAGS") == ["丁"])
_rt.override("diag.MY_TAGS", lambda: "坏类型")
check("常量返回类型不符回退默认", _rt.resolve_value("diag.MY_TAGS") == ["甲", "乙"])

_NUM = behavior_const("diag", "NUM", 5)
check("数值常量默认值", _rt.resolve_value("diag.NUM") == 5)
_rt.override("diag.NUM", 5.5)
check("数值常量允许 int/float 互通", _rt.resolve_value("diag.NUM") == 5.5)
_rt.override("diag.NUM", "5")
check("数值常量拒绝非数值", _rt.resolve_value("diag.NUM") == 5.5)


# ---------------- 3. load_pack 启用诊断 ----------------

@behavior_hook("diag", "fn2")
def _fn2(a, b=1):
    return (a, b)


_base = tempfile.mkdtemp(prefix="behavior_packs_")
_good_src = (
    "def register(runtime):\n"
    "    runtime.override('diag.fn2', lambda a, b=1: (a, b * 2))\n"
)
_bad_src = (
    "def register(runtime):\n"
    "    runtime.override('diag.fn2', lambda a, b=1: (a, b))\n"
    "    runtime.override('diag.ghost', lambda: 1)\n"          # 未知 key
    "    runtime.override('diag.fn2', lambda a: None)\n"       # 缺参数
    "    runtime.override('diag.MY_TAGS', 123)\n"              # 常量类型不符
)
_empty_src = (
    "def register(runtime):\n"
    "    pass\n"
)
_noregister_src = "X = 1\n"
_dup_src = (
    "def register(runtime):\n"
    "    runtime.override('diag.fn2', lambda a, b=1: (a, b))\n"
    "    runtime.override('diag.fn2', lambda a, b=1: (a, b * 3))\n"
)
_badload_src = "raise RuntimeError('boom')\n"

for pack, src in (("goodpack", _good_src), ("badpack", _bad_src),
                  ("emptypack", _empty_src), ("noregister", _noregister_src),
                  ("duppack", _dup_src), ("badload", _badload_src)):
    pack_dir = os.path.join(_base, "behaviors", pack)
    os.makedirs(pack_dir)
    with open(os.path.join(pack_dir, "main.py"), "w", encoding="utf-8") as f:
        f.write(src)


def _load(pack):
    manifest = WorldPackManifest(world_id="diag", name=pack,
                                 resources={"behaviors": [pack]})
    return _rt.load_pack(manifest, _base)


report = _load("goodpack")
check("报告类型", isinstance(report, BehaviorLoadReport))
check("接管清单", report.overridden == ["diag.fn2"], str(report.overridden))
check("正常包无诊断异常", not report.errors, str(report.errors))
check("模块计数", report.modules_loaded == 1)
check("last_report 记录", _rt.last_report is report)
check("包内覆盖生效", _fn2(3) == (3, 2))
check("诊断行含接管摘要", any("接管 1 项" in l for l in report.log_lines()))

_rt.override("diag.MY_TAGS", lambda: 123)
_rt.resolve_value("diag.MY_TAGS")
check("常量返回类型不符记入诊断",
      any("返回值类型不符" in e for e in _rt.last_report.errors),
      str(_rt.last_report.errors))

report = _load("badpack")
check("坏注册逐条记入诊断", len(report.errors) == 3, str(report.errors))
check("坏包仍接管可用项", report.overridden == ["diag.fn2"], str(report.overridden))
check("坏包诊断有未知 key", any("未知覆盖目标" in e for e in report.errors))
check("坏包诊断有签名拒绝", any("签名缺少" in e for e in report.errors))
check("坏包诊断有类型拒绝", any("类型不符" in e or "需要 list" in e for e in report.errors))

report = _load("ghostpack")
check("缺失包记入诊断", len(report.errors) == 1 and "缺失" in report.errors[0])

report = _load("noregister")
check("无 register 模块记入诊断",
      len(report.errors) == 1 and "register" in report.errors[0])

report = _load("emptypack")
check("空包无异常但有警告",
      not report.errors and len(report.warnings) == 1
      and "未注册任何覆盖" in report.warnings[0],
      f"e={report.errors} w={report.warnings}")

report = _load("duppack")
check("重复注册只记一次接管项", report.overridden == ["diag.fn2"],
      str(report.overridden))
check("重复注册记入警告", any("重复注册" in w for w in report.warnings),
      str(report.warnings))
check("重复注册最后一条生效", _fn2(3) == (3, 3))

report = _load("badload")
check("模块加载失败带异常原因",
      len(report.errors) == 1 and "加载失败" in report.errors[0]
      and "boom" in report.errors[0], str(report.errors))


# ---------------- 4. reset 语义 ----------------

reset_runtime()
check("reset 清空覆盖", _fn2(3) == (3, 1) and _fn(2) == (2, 1))
check("reset 保留诊断报告", _rt.last_report is not None
      and _rt.last_report.pack_name == "badload")

# ---------------- 结论 ----------------

_log.flush()
sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__
if failures:
    print(f"[check_behavior_runtime] FAILED {len(failures)}/{total}: {failures}")
else:
    print(f"[check_behavior_runtime] PASSED {total}/{total}")
print(f"[check_behavior_runtime] report: {_report_path}")
sys.exit(1 if failures else 0)
