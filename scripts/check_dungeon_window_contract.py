"""副本窗口契约守卫：``dungeon/window/`` 里的高危 DPG 生命周期与线程调用。

用法：``python scripts/check_dungeon_window_contract.py``

文档（docs/Dungeon/window.md §5 约束清单）写明的规则，过去只能靠 AI/人记得；
本脚本把其中**机械可查**的几条变成 AST 检查，让误写在真窗口跑起来之前就失败：

**A. 线程纪律**

- 禁 ``threading.Timer``（C5：计时类逻辑一律用帧时钟 ``self._frame.every/after``；
  Timer 收尾要逐个 join，退出路径因此复杂化）。
- 禁零参数 ``.join()``（§4：``join`` 只允许带超时；无超时 join 会把帧循环吊死）。

**B. DPG 生命周期调用（按「生命周期属主」文件白名单）**

- ``dpg.stop_dearpygui`` 仅 ``base.py``（``_request_close``）与 ``dpg_state.py``
  （保活视口拆装）允许——业务代码一律走 ``base._request_close()``（C1）。
- ``dpg.start_dearpygui`` 全层禁用（L0：手动渲染帧循环，``start_dearpygui``
  会把宿主主循环堵死）。
- ``dpg.set_exit_callback`` 全层禁用（C6：手动渲染下它只在 ``destroy_context()``
  内部才触发，对清理太晚；关闭统一由帧循环 ``is_dearpygui_running()`` 判定）。
- ``dpg.create_context`` / ``dpg.destroy_context`` 仅 ``base.py`` / ``ui.py`` /
  ``dpg_state.py`` 允许（上下文是进程单例，建/毁收在生命周期属主手里）。
- ``dpg.minimize_viewport`` 全层禁用（最小化后不再有新帧，帧时钟上排队的任务
  含关闭永远执行不到，窗口卡死——见 window_automation.md §5 坑清单）。

说明：检查按 ``dpg.`` 别名匹配（窗口层统一 ``import dearpygui.dearpygui as dpg``）；
``scripts/`` 与 ``ui/`` 不在扫描范围。与 ``check_dungeon_layering.py`` 的分工：
那个脚本管「能不能 import」，本脚本管「import 之后不许怎么调」。

退出码：0 = 通过；1 = 存在违约调用。
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WINDOW_DIR = ROOT / "dungeon" / "window"

#: dpg 生命周期调用 -> 允许出现的文件（相对 dungeon/window/）
DPG_ALLOWED = {
    "stop_dearpygui": {"base.py", "dpg_state.py"},
    "create_context": {"base.py", "ui.py", "dpg_state.py"},
    "destroy_context": {"base.py", "ui.py", "dpg_state.py"},
}
#: dpg 全层禁用的调用 -> 原因
DPG_BANNED = {
    "start_dearpygui": "L0 手动渲染：start_dearpygui 会堵死宿主主循环，"
                       "帧由 base._run_frame_loop 驱动",
    "set_exit_callback": "手动渲染下它只在 destroy_context() 内部触发，对清理太晚；"
                         "关闭由帧循环 is_dearpygui_running() 判定（C6）",
    "minimize_viewport": "最小化后不再有新帧，帧时钟上的任务（含关闭）永远执行不到，"
                         "窗口卡死",
}


def _dpg_call_name(node: ast.Call):
    """``dpg.xxx(...)`` 的方法名；非该形状返回 None。"""
    func = node.func
    return func.attr if (isinstance(func, ast.Attribute)
                         and isinstance(func.value, ast.Name)
                         and func.value.id == "dpg") else None


def check_module(path: Path):
    """单个窗口模块的违约清单：[(lineno, 简述, 原因), ...]。"""
    rel = path.relative_to(WINDOW_DIR).as_posix()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # --- A. 线程纪律 ---
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "Timer" \
                and isinstance(func.value, ast.Name) and func.value.id == "threading":
            violations.append((node.lineno, "threading.Timer",
                               "计时类逻辑用帧时钟 every/after（C5）"))
        elif isinstance(func, ast.Name) and func.id == "Timer" \
                and isinstance(node.func, ast.Name):
            # from threading import Timer 的裸名形式一并拦下
            violations.append((node.lineno, "Timer(...)",
                               "计时类逻辑用帧时钟 every/after（C5）"))
        if isinstance(func, ast.Attribute) and func.attr == "join" \
                and not node.args and "timeout" not in {k.arg for k in node.keywords}:
            violations.append((node.lineno, ".join() 无超时",
                               "join 只允许带 timeout（window.md §4）"))
        # --- B. dpg 生命周期 ---
        name = _dpg_call_name(node)
        if name is None:
            continue
        if name in DPG_BANNED:
            violations.append((node.lineno, f"dpg.{name}", DPG_BANNED[name]))
        elif name in DPG_ALLOWED and rel not in DPG_ALLOWED[name]:
            owners = ", ".join(sorted(DPG_ALLOWED[name]))
            violations.append((node.lineno, f"dpg.{name}",
                               f"只在 {owners} 允许；业务关闭一律走 base._request_close()（C1）"
                               if name == "stop_dearpygui"
                               else f"上下文建/毁收在生命周期属主：{owners}"))
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
        print(f"[check_dungeon_window_contract] FAILED {len(violations)} 处违约调用")
        return 1
    print("[check_dungeon_window_contract] PASSED：无 threading.Timer / 无超时 join，"
          "DPG 生命周期调用均在属主文件内")
    return 0


if __name__ == "__main__":
    sys.exit(main())
