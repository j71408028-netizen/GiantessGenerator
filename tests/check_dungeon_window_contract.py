"""副本窗口契约守卫：``dungeon/window/`` 里的高危后端调用与线程纪律。

用法：``python tests/check_dungeon_window_contract.py``

文档（docs/Dungeon/window.md §5 约束清单）写明的规则，过去只能靠 AI/人记得；
本脚本把其中**机械可查**的几条变成 AST 检查，让误写在真窗口跑起来之前就失败：

**A. 线程纪律**

- 禁 ``threading.Timer``（C5：计时类逻辑一律用帧时钟 ``self._frame.every/after``；
  Timer 收尾要逐个 join，退出路径因此复杂化）。
- 禁零参数 ``.join()``（§4：``join`` 只允许带超时；无超时 join 会把帧循环吊死）。

**B. 后端调用（imgui-bundle）**

- ``dearpygui`` 全层禁用：它已随 imgui 迁移（P8）彻底移除，此后任何回归都是把
  一个没有依赖声明的后端又请回来。
- ``hello_imgui.run(...)`` 只允许 ``runner.py``：窗口起停收在 ``WindowRunner``
  一处（``run()`` 内部建 GL 后端 / ImGui 上下文 / 视口与帧循环）。
- 给 ``app_shall_exit`` 赋值的语句只允许出现在 ``runner.py``：关闭只置位、由
  runner 在**帧边界**落实成 ``app_shall_exit``（C1）；别处直接退出会让
  hello_imgui 在帧中途把上下文拆掉。
- ``sys.exit`` / ``os._exit`` 全层禁用：窗口生命周期不由解释器退出驱动。

说明：检查只认 AST（代码本身）——注释 / docstring 里对 DPG 时代的历史表述不算
违约。``scripts/`` 与 ``ui/`` 不在扫描范围。与 ``check_dungeon_layering.py`` 的
分工：那个脚本管「能不能 import」，本脚本管「import 之后不许怎么调」。

退出码：0 = 通过；1 = 存在违约调用。
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WINDOW_DIR = ROOT / "dungeon" / "window"

_DEARPYGUI_WHY = "dearpygui 已随 imgui 迁移移除（P8）；窗口层不得再引用它"
_RUNNER_ONLY_WHY = ("窗口起停 / 关闭只在 runner.py 落实：run() 建上下文与帧循环，"
                    "关闭由帧回调在帧边界写 app_shall_exit（C1）")
_EXIT_WHY = "窗口生命周期不由解释器退出驱动；关窗走 _request_close()（C1）"
_TIMER_WHY = "计时类逻辑用帧时钟 every/after（C5）"
_JOIN_WHY = "join 只允许带 timeout（window.md §4）"


def _is_dearpygui(target: str) -> bool:
    return target.split(".")[0] == "dearpygui"


def check_module(path: Path):
    """单个窗口模块的违约清单：[(lineno, 简述, 原因), ...]。"""
    rel = path.relative_to(WINDOW_DIR).as_posix()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations = []
    for node in ast.walk(tree):
        # --- A. 线程纪律 ---
        if isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Attribute) and func.attr == "Timer"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "threading"):
                violations.append((node.lineno, "threading.Timer", _TIMER_WHY))
            elif isinstance(func, ast.Name) and func.id == "Timer":
                # from threading import Timer 的裸名形式一并拦下
                violations.append((node.lineno, "Timer(...)", _TIMER_WHY))
            if (isinstance(func, ast.Attribute) and func.attr == "join"
                    and not node.args
                    and "timeout" not in {k.arg for k in node.keywords}):
                violations.append((node.lineno, ".join() 无超时", _JOIN_WHY))
        # --- B. 后端调用 ---
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_dearpygui(alias.name):
                    violations.append((node.lineno, f"import {alias.name}", _DEARPYGUI_WHY))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and _is_dearpygui(node.module or ""):
                violations.append((node.lineno, f"from {node.module} import ...",
                                   _DEARPYGUI_WHY))
        elif isinstance(node, ast.Call):
            func = node.func
            name = (f"{func.value.id}.{func.attr}"
                    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                    else None)
            if name in ("sys.exit", "os._exit"):
                violations.append((node.lineno, f"{name}(...)", _EXIT_WHY))
            elif name == "hello_imgui.run" and rel != "runner.py":
                violations.append((node.lineno, "hello_imgui.run(...)", _RUNNER_ONLY_WHY))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr == "app_shall_exit" \
                        and rel != "runner.py":
                    violations.append((node.lineno, "app_shall_exit = ...",
                                       _RUNNER_ONLY_WHY))
        elif isinstance(node, ast.Attribute):
            # dpg.xxx（含 dpg.foo(...) 调用）——迁移后窗口层不该再出现这个别名
            if isinstance(node.value, ast.Name) and node.value.id == "dpg":
                violations.append((node.lineno, f"dpg.{node.attr}", _DEARPYGUI_WHY))
    return violations


def main() -> int:
    total = 0
    violations = []
    for path in sorted(WINDOW_DIR.rglob("*.py")):
        total += 1
        for lineno, what, why in check_module(path):
            violations.append((path.relative_to(ROOT).as_posix(), lineno, what, why))

    print(f"[check_dungeon_window_contract] 已检查 dungeon/window/（含子包）{total} 个模块")
    if violations:
        for file, lineno, what, why in violations:
            print(f"  FAIL {file}:{lineno}  {what} — {why}")
        print(f"[check_dungeon_window_contract] FAILED：{len(violations)} 处违约调用")
        return 1
    print("[check_dungeon_window_contract] PASSED：无 threading.Timer / 无超时 join，"
          "dearpygui 已清干净，hello_imgui.run 与关闭置位只落在 runner.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
