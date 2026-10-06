"""入口与打包资源守卫：被引用的文件必须真实存在。

用法：``python tests/check_entrypoints.py``

盯两类「改了结构、忘了改引用」的静默失效——开发时不暴露，只在打包或某条界面
分支被真正走到时才炸：

**A. 构建脚本里的入口 / 图标 / add-data 源路径**

``build/`` 下的 Windows 与 macOS 构建脚本各写死一个 PyInstaller 入口文件
（目前只有 ``main.py``）、一个图标路径与若干 ``--add-data`` 源目录。
某次重构把入口并进别的文件之后，脚本本身照样能读、能跑，直到 pyinstaller
找不到入口才报错。这里把脚本里出现的这些路径逐个验证存在性。

注：``build/`` 曾经被 ``.gitignore`` 的 ``build/`` 规则整体忽略，构建脚本因此没进
版本控制，入口被删掉也没人发现（`main_mini.py` 就这样消失过）。现改为只忽略
``/build/*`` 的产物并显式放行 ``build/windows/``、``build/macos/``。本项在
``build/`` 不存在时跳过，不影响干净检出。

**B. 悬空 import**

扫描仓库里的模块，凡是 import **第一方**模块（顶层名是本仓库的包或模块），
就要求目标真实存在。典型事故：``from services.archive_export import ...``
而该模块实际在 ``services/character_service/archive_export.py``——同包内的错误
路径不会被其它自检覆盖（它们校验的是另一条路径），只有真正走到那段代码时才抛
``ModuleNotFoundError``。

判断规则（只针对第一方；stdlib / 第三方 / 可选依赖一律跳过）：

- ``import a.b`` / ``from a.b import c``：``a/b.py`` 或 ``a/b/__init__.py`` 必须存在；
- ``from a.b import c`` 里的 ``c``：``a/b/c.py`` 存在即通过；否则要求 ``a/b`` 的源码在
  **顶层**绑定过 ``c``（class / def / 赋值 / 再导出），都没有才判为悬空。

相对导入（``from . import``）由 Python 自行解析，不在此处判。

退出码：0 = 通过；1 = 存在缺失的入口 / 资源 / 模块。
"""

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD_DIR = ROOT / "build"

#: 不参与 import 扫描的目录：产物、缓存、用户数据、IDE，以及不入库的本地工具。
#: ``developer_tools/`` 是随时可弃的探针集合（.gitignore 掉），让它拖垮正式自检
#: 没有意义。
EXCLUDED_DIRS = {
    "__pycache__", ".git", ".idea", ".workbuddy", ".venv-build", ".venv-macos",
    "data", "dist", "build", "developer_tools",
}

#: 出现这些名字就认为模块在**动态**创建全局量，名字级校验对它失效
DYNAMIC_MARKERS = {"globals", "locals", "vars", "exec", "eval", "setattr"}

#: 构建脚本里的入口赋值（ps1 的 ``$Entry = "main.py"`` / sh 的 ``ENTRY="main.py"``）
ENTRY_RE = re.compile(r'^\s*\$?ENTRY\s*=\s*"([^"]+)"', re.M | re.I)
#: ``--icon assets\icons\icon.ico``
ICON_RE = re.compile(r'--icon\s+"?([^"\s]+)"?')
#: ``--add-data "assets;assets"``（Windows，分号）/ ``--add-data "assets:assets"``（POSIX，冒号）
ADDDATA_RE = re.compile(r'--add-data\s+"?([^";\s]+)[;:]')


def _norm(path: str) -> str:
    """把脚本里的路径统一成仓储相对路径（Windows 反斜杠 -> 正斜杠）。"""
    return path.replace("\\", "/").strip()


# ==================== A. 构建脚本 ====================

def check_build_scripts():
    """校验构建脚本里的入口 / 图标 / add-data 源路径。返回 (检查项数, 问题列表)。"""
    if not BUILD_DIR.is_dir():
        return 0, []
    checked = 0
    problems = []
    for script in sorted(BUILD_DIR.rglob("*")):
        if script.suffix.lower() not in (".ps1", ".sh"):
            continue
        try:
            rel = script.relative_to(ROOT).as_posix()
        except ValueError:      # BUILD_DIR 被指到仓储外：退回绝对路径，别让守卫自己崩
            rel = script.as_posix()
        text = script.read_text(encoding="utf-8")
        for pattern, label in ((ENTRY_RE, "入口文件"),
                               (ICON_RE, "图标"),
                               (ADDDATA_RE, "--add-data 源路径")):
            for match in pattern.finditer(text):
                target = _norm(match.group(1))
                checked += 1
                if not (ROOT / target).exists():
                    problems.append(f"{rel}: {label}不存在 -> {target}")
    return checked, problems


# ==================== B. 悬空 import ====================

def _first_party_tops():
    """收集本仓库可被 import 的顶层名：含 ``__init__.py`` 的目录 + 根目录 .py 文件。"""
    tops = set()
    for entry in ROOT.iterdir():
        if entry.is_dir():
            if (entry / "__init__.py").exists():
                tops.add(entry.name)
        elif entry.suffix == ".py":
            tops.add(entry.stem)
    return tops


def _module_file(name: str):
    """把点分模块名解析成文件；不存在返回 None。"""
    base = ROOT.joinpath(*name.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _top_level_names(path: Path):
    """收集模块顶层绑定的名字（含顶层 if / try / with / for 内，不含函数与类体内）。

    返回 ``(names, dynamic)``。``names`` 为 None 表示源码无法解析——此时放弃校验
    而不是误报；``dynamic`` 为真表示该模块用 ``globals().update(...)`` / ``exec``
    之类手段**动态**创建全局量（``ui.common.theme`` 就是从 ``assets/theme/*.json``
    注入那批 token 的），静态解析看不到，调用方据此跳过名字级校验。
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return None, False

    names = set()

    # 动态填充检测单独走一整棵树：``ui.common.theme`` 在**模块顶层**执行
    # ``globals().update(palette)``，而拼装那些 token 的代码在函数里，只看顶层
    # 语句会漏判。这里刻意保守——模块里只要出现过 globals() / exec 这类手段，就
    # 放弃对它的名字级校验（模块路径本身仍然校验）。
    dynamic = any(
        isinstance(node, ast.Name) and node.id in DYNAMIC_MARKERS
        for node in ast.walk(tree))

    def collect(body):
        for node in body:
            if isinstance(node, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                collect(node.body)
                collect(getattr(node, "orelse", []) or [])
                collect(getattr(node, "finalbody", []) or [])
                for handler in getattr(node, "handlers", []) or []:
                    collect(handler.body)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                if isinstance(node.target, ast.Name):
                    names.add(node.target.id)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    names.add(alias.asname or alias.name.split(".")[0])

    collect(tree.body)
    return names, dynamic


def _iter_py_files():
    for path in sorted(ROOT.rglob("*.py")):
        parts = path.relative_to(ROOT).parts[:-1]
        if any(part in EXCLUDED_DIRS for part in parts):
            continue
        yield path


def check_imports():
    """校验第一方 import 目标存在。返回 (检查项数, 问题列表)。"""
    tops = _first_party_tops()
    checked = 0
    problems = []
    for path in _iter_py_files():
        rel = path.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError) as e:
            problems.append(f"{rel}: 源码无法解析（{type(e).__name__}）")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] not in tops:
                        continue
                    checked += 1
                    if _module_file(alias.name) is None:
                        problems.append(
                            f"{rel}:{node.lineno}: import 的模块不存在 -> {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                # 相对导入交给 Python 解析；无模块名的 `from . import x` 跳过
                if node.level != 0 or not node.module:
                    continue
                if node.module.split(".")[0] not in tops:
                    continue
                checked += 1
                target = _module_file(node.module)
                if target is None:
                    problems.append(
                        f"{rel}:{node.lineno}: from 的模块不存在 -> {node.module}")
                    continue
                if any(alias.name == "*" for alias in node.names):
                    continue
                bound, dynamic = _top_level_names(target)
                if bound is None or dynamic:
                    continue
                for alias in node.names:
                    if _module_file(f"{node.module}.{alias.name}") is not None:
                        continue
                    if alias.name not in bound:
                        problems.append(
                            f"{rel}:{node.lineno}: {node.module} 里没有 '{alias.name}'")
    return checked, problems


def main() -> int:
    build_checked, build_problems = check_build_scripts()
    import_checked, import_problems = check_imports()
    problems = build_problems + import_problems

    build_note = "（build/ 不存在，已跳过）" if build_checked == 0 else ""
    print(f"[check_entrypoints] 构建脚本路径 {build_checked} 项{build_note}，"
          f"第一方 import 目标 {import_checked} 项")
    if problems:
        for item in problems:
            print(f"  FAIL {item}")
        print(f"[check_entrypoints] FAILED {len(problems)} 处引用缺失")
        return 1
    print("[check_entrypoints] PASSED：入口、图标、add-data 源路径与第一方 import 目标均存在")
    return 0


if __name__ == "__main__":
    sys.exit(main())
