"""副本 S4 分层守卫：``dungeon/`` 根必须是纯领域层（无 GUI、无服务层依赖）。

用法：``python scripts/check_dungeon_layering.py``

扫描 ``dungeon/*.py``（**只限根目录**，``window/`` 子包是 UI 层，不在检查范围），
逐个 parse AST 收集 import，命中以下任一即失败：

- UI 框架：``dearpygui`` / ``customtkinter`` / ``tkinter``；
- 服务层与界面层：``services.*`` / ``ui.*``；
- 反向依赖自己的 UI 子包：``dungeon.window``（含 ``from .window import``）；
- 逃逸出包的相对导入（``from .. import``）。

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

# 顶层包名 -> 它属于哪一层（命中即越界）
FORBIDDEN_ROOTS = {
    "dearpygui": "UI 框架",
    "customtkinter": "UI 框架",
    "tkinter": "UI 框架",
    "PIL": "图像库（UI 层职责）",
    "services": "服务层",
    "ui": "界面层",
}
# dungeon 自己的 UI 子包：领域层不得反向依赖
DOMAIN_UI_SUBPACKAGE = "window"


def _violation(module, lineno, reason):
    return {"module": module, "lineno": lineno, "reason": reason}


def _check_target(target, lineno):
    """判断单个 import 目标是否越界。返回 0~1 条违规。"""
    top = target.split(".")[0]
    if top in FORBIDDEN_ROOTS:
        return [_violation(target, lineno,
                           f"领域层不应依赖{FORBIDDEN_ROOTS[top]}（应由调用方注入）")]
    parts = target.split(".")
    if len(parts) >= 2 and parts[0] == "dungeon" and parts[1] == DOMAIN_UI_SUBPACKAGE:
        return [_violation(target, lineno, "领域层不得反向依赖 UI 子包 dungeon.window")]
    return []


def check_module(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                violations += _check_target(alias.name, node.lineno)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                violations += _check_target(node.module or "", node.lineno)
            elif node.level >= 2:
                violations.append(_violation(
                    "." * node.level + (node.module or ""), node.lineno,
                    "相对导入逃逸出 dungeon 包（跨层依赖）"))
            elif (node.module or "").split(".")[0] == DOMAIN_UI_SUBPACKAGE:
                violations.append(_violation(
                    f"from .{node.module}", node.lineno,
                    "领域层不得反向依赖 UI 子包 dungeon.window"))
    return violations


def main() -> int:
    files = sorted(DOMAIN_DIR.glob("*.py"))
    total = 0
    violations = []
    for path in files:
        total += 1
        for item in check_module(path):
            item["file"] = path.relative_to(ROOT).as_posix()
            violations.append(item)

    print(f"[check_dungeon_layering] 已检查 dungeon/ 根 {total} 个领域模块")
    if violations:
        for item in violations:
            print(f"  FAIL {item['file']}:{item['lineno']}  {item['module']} — {item['reason']}")
        print(f"[check_dungeon_layering] FAILED {len(violations)} 处越界依赖")
        return 1
    print("[check_dungeon_layering] PASSED：无 dearpygui / services / ui 依赖")
    return 0


if __name__ == "__main__":
    sys.exit(main())
