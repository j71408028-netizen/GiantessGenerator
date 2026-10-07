"""挂件层守卫：``ui/mini/`` 是纯 tkinter 的独立界面，不得碰专业界面的那套东西。

用法：``python tests/check_mini_layering.py``

一个版本里并存两套界面（专业模式 ``ui/*`` 用 customtkinter，挂件模式 ``ui/mini/*``
用原生 tkinter）。挂件层若混进专业层的依赖，代价是：

- 引入 ``customtkinter`` —— 启动被拖慢、打包体积变大，且挂件刻意不用它的圆角与
  主题机制，混用只会让两套控件风格打架；
- 引入 ``ui.common.theme`` —— 该模块顶层 ``import ui.common.ctk_patch``，会连带
  拉起 customtkinter；配色应当走 ``core.appearance`` + ``ui.mini.pixel``；
- 引入 ``ui.common.fonts`` —— 那里的 ``ui_font()`` 给专业版用磅值，挂件要的是
  负数像素字号，两边不能共用；
- 引入 ``ui.common.widgets`` / ``dialogs`` / ``managers`` —— 都是 CTk 控件或面向
  专业界面的弹窗，挂件一律用整窗换屏代替弹窗。

允许：``ui.mini.*`` 自身、``ui.common.tk_host``（副本窗口的宿主端口适配，是挂件与
dungeon 层之间的桥）、``ui.common.x11``（纯 stdlib ctypes 的 Linux/X11 原生支持，
零 GUI 框架依赖）。外观模式住在 ``core.appearance``（零依赖，2026-10-06 从
``ui/common/`` 下移），不属于 ``ui.*``，因此不受本白名单约束。

退出码：0 = 通过；1 = 存在越界依赖。
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MINI_DIR = ROOT / "ui" / "mini"

# 顶层/次级模块名 -> 为什么不能用
FORBIDDEN_ROOTS = {
    "customtkinter": "CTk 外观库（挂件只用原生 tkinter）",
    "dearpygui": "副本窗口框架（经 ui.common.tk_host 间接进入，不在界面层直接用）",
    "ui": "见 ALLOWED_UI 白名单以外的界面模块",
}

# ui.* 里挂件可以碰的例外（外观模式已下移 core.appearance，不再需要在此登记）
ALLOWED_UI = {
    "ui.mini",
    "ui.common.tk_host",
    # 纯 stdlib ctypes 的 Linux/X11 原生支持（XInitThreads / EWMH 置顶），零 GUI
    # 框架依赖：挂件要写 _NET_WM_STATE_ABOVE 就靠它（见 ui/mini/topmost.py）。
    "ui.common.x11",
}


def _violation(module, lineno, reason):
    return {"module": module, "lineno": lineno, "reason": reason}


def _check_target(target, lineno):
    if not target:
        return []
    top = target.split(".")[0]
    if top == "ui":
        # 逐级放宽：ui.mini.xxx 也放行，比对精确模块名更稳
        parts = target.split(".")
        for depth in range(len(parts), 1, -1):
            if ".".join(parts[:depth]) in ALLOWED_UI:
                return []
        return [_violation(target, lineno,
                           f"挂件层不应依赖 {target}（白名单只有 "
                           f"{sorted(ALLOWED_UI)}）")]
    if top in FORBIDDEN_ROOTS:
        return [_violation(target, lineno,
                           f"挂件层不应依赖 FORBIDDEN：{FORBIDDEN_ROOTS[top]}")]
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
                # ``from ui.common import appearance`` 要按完整目标名判定，
                # 只看 ``ui.common`` 会把白名单里的子模块一并误杀。
                base = node.module or ""
                targets = [f"{base}.{alias.name}" for alias in node.names] or [base]
                for target in targets:
                    violations += _check_target(target, node.lineno)
            elif node.level >= 3:
                # from ...ui import x：跳出 ui/mini 两层去拿别处的模块
                violations.append(_violation(
                    "." * node.level + (node.module or ""), node.lineno,
                    "相对导入逃逸出 ui.mini 包"))
    return violations


def main() -> int:
    total = 0
    violations = []
    for path in sorted(MINI_DIR.rglob("*.py")):
        total += 1
        for item in check_module(path):
            item["file"] = path.relative_to(ROOT).as_posix()
            violations.append(item)

    print(f"[check_mini_layering] 已检查 ui/mini/ 下 {total} 个模块")
    if violations:
        for item in violations:
            print(f"  FAIL {item['file']}:{item['lineno']}  {item['module']} — {item['reason']}")
        print(f"[check_mini_layering] FAILED {len(violations)} 处越界依赖")
        return 1
    print("[check_mini_layering] PASSED：挂件层无 customtkinter / ui.common.theme "
          "依赖，只经 tk_host 与 core 层对外接触")
    return 0


if __name__ == "__main__":
    sys.exit(main())
