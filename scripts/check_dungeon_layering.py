"""副本分层守卫：``dungeon/`` 根是纯领域层，``dungeon/window/`` 是自洽的 UI 层。

用法：``python scripts/check_dungeon_layering.py``

两层扫描，逐个 parse AST 收集 import：

**A. ``dungeon/*.py``（领域层）** —— 命中以下任一即失败：

- UI 框架：``dearpygui`` / ``customtkinter`` / ``tkinter``；
- 服务层与界面层：``services.*`` / ``ui.*``；
- 反向依赖自己的 UI 子包：``dungeon.window``（含 ``from .window import``）；
- 逃逸出包的相对导入（``from .. import``）。

**B. ``dungeon/window/*.py``（窗口层）** —— 允许 ``dearpygui`` / ``services``，
但**不允许** ``tkinter`` / ``customtkinter`` / ``ui.*``：宿主能力（尺寸/DPI、
显隐、事件泵、弹框、字体、活动窗口登记）一律经 ``dungeon.window.host.HostPort``
端口取得，Tk 相关实现只存在于 ``ui/common/tk_host.py``（L2，见
docs/Dungeon/window_host.md §3）。

领域层需要的外部行为一律由调用方注入，典型例子见 ``dungeon/rules.py``：
``EvolutionRules(step_decay=...)`` 由 ``window/base.py`` 传入
``StateService.decay_step_rates``，而不是领域层自己去 import services。

退出码：0 = 通过；1 = 存在越界依赖。
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOMAIN_DIR = ROOT / "dungeon"
WINDOW_DIR = DOMAIN_DIR / "window"

# 顶层包名 -> 它属于哪一层（命中即越界）
FORBIDDEN_ROOTS = {
    "dearpygui": "UI 框架",
    "customtkinter": "UI 框架",
    "tkinter": "UI 框架",
    "PIL": "图像库（UI 层职责）",
    "services": "服务层",
    "ui": "界面层",
}
# 窗口层同样不得依赖的东西：宿主细节必须收在宿主适配器里（L2 宿主端口）
WINDOW_FORBIDDEN_ROOTS = {
    "tkinter": "Tk 宿主细节（应由 dungeon.window.host.HostPort 端口提供）",
    "customtkinter": "Tk 宿主细节（应由 dungeon.window.host.HostPort 端口提供）",
    "ui": "界面层（宿主适配器见 ui/common/tk_host.py）",
}
# dungeon 自己的 UI 子包：领域层不得反向依赖
DOMAIN_UI_SUBPACKAGE = "window"


def _violation(module, lineno, reason):
    return {"module": module, "lineno": lineno, "reason": reason}


def _check_target(target, lineno, forbidden=FORBIDDEN_ROOTS, label="领域层",
                  check_ui_subpackage=True):
    """判断单个 import 目标是否越界。返回 0~1 条违规。"""
    top = target.split(".")[0]
    if top in forbidden:
        return [_violation(target, lineno,
                           f"{label}不应依赖{forbidden[top]}（应由调用方注入）")]
    parts = target.split(".")
    if (check_ui_subpackage and len(parts) >= 2 and parts[0] == "dungeon"
            and parts[1] == DOMAIN_UI_SUBPACKAGE):
        return [_violation(target, lineno, "领域层不得反向依赖 UI 子包 dungeon.window")]
    return []


def check_module(path: Path, forbidden=FORBIDDEN_ROOTS, label="领域层",
                 check_ui_subpackage=True):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                violations += _check_target(alias.name, node.lineno, forbidden, label,
                                            check_ui_subpackage)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                violations += _check_target(node.module or "", node.lineno, forbidden, label,
                                            check_ui_subpackage)
            elif node.level >= 2:
                violations.append(_violation(
                    "." * node.level + (node.module or ""), node.lineno,
                    "相对导入逃逸出 dungeon 包（跨层依赖）"))
            elif (check_ui_subpackage
                  and (node.module or "").split(".")[0] == DOMAIN_UI_SUBPACKAGE):
                violations.append(_violation(
                    f"from .{node.module}", node.lineno,
                    "领域层不得反向依赖 UI 子包 dungeon.window"))
    return violations


def _scan(directory: Path, forbidden, label, check_ui_subpackage=True):
    """扫描目录下的顶层模块，返回 (文件数, 违规列表)。"""
    total = 0
    violations = []
    for path in sorted(directory.glob("*.py")):
        total += 1
        for item in check_module(path, forbidden, label, check_ui_subpackage):
            item["file"] = path.relative_to(ROOT).as_posix()
            violations.append(item)
    return total, violations


def main() -> int:
    domain_total, domain_bad = _scan(DOMAIN_DIR, FORBIDDEN_ROOTS, "领域层")
    window_total, window_bad = _scan(
        WINDOW_DIR, WINDOW_FORBIDDEN_ROOTS, "窗口层", check_ui_subpackage=False)
    violations = domain_bad + window_bad

    print(f"[check_dungeon_layering] 已检查 dungeon/ 根 {domain_total} 个领域模块，"
          f"dungeon/window/ {window_total} 个窗口模块")
    if violations:
        for item in violations:
            print(f"  FAIL {item['file']}:{item['lineno']}  {item['module']} — {item['reason']}")
        print(f"[check_dungeon_layering] FAILED {len(violations)} 处越界依赖")
        return 1
    print("[check_dungeon_layering] PASSED：领域层无 dearpygui / services / ui 依赖，"
          "窗口层无 tkinter / ui 依赖")
    return 0


if __name__ == "__main__":
    sys.exit(main())
