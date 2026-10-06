# -*- coding: utf-8 -*-
"""分层依赖矩阵守卫：把「谁能依赖谁」写成显式矩阵，越界即红。

用法：``python tests/check_import_graph.py``

**为什么需要**：本仓引用密度很高——``paths`` 被 33 处 import、``models`` 30 处、
``logic`` 24 处，横跨 persistence / services / dungeon / ui 全部四层。靠 grep 找
不全调用点，所以后续「把根目录模块收编进 core/」「把 ui、services 的扁平文件收进
子包」这类搬迁，必须先有一张能自动断言的依赖图，否则改漏一处只会在运行时炸。

**与其他守卫的分工**：``check_dungeon_layering`` 管 dungeon 内部的领域层/窗口层，
``check_mini_layering`` 管挂件层不得碰 CTk 那一套，本守卫管**全局的层间方向**。

层的划分（自底向上，每层只能依赖自己与更低层）::

    infra          根目录基础设施（paths）
    core           core/ 领域模型（models / address_model / logic /
                   behavior_runtime / ai）
    dungeon        副本领域定义与规则（dungeon/**，不含 window）
    persistence    持久化
    services       服务层（含 services/exploration/ 的探索编排）
    dungeon_window 副本会话窗口（dungeon/window/**）
    ui             专业界面与挂件界面（ui/**）
    app            应用壳（根目录 main / app_shell /
                   main_window_manager）

**`orchestration` 层已消失**（2026-10-06，阶段 3.1）。它曾用来给 ``core/context.py``
的 ``ExplorationContext`` 开一条全图唯一的双向豁免：那个 God object 既被 ui 大量
引用，又反向延迟导入 ``ui.exploration.creation_params_dlg``。归位不是"把类拆小"
而是"整层搬走"——``ExplorationContext`` 连同它的职责子系统一起迁进了
``services/exploration/``，于是 ``ui → services`` 与 ``services → persistence/core``
两侧都落回既有合法边，豁免不再需要。

⚠️ **回退护栏**：若有人再把 ``ExplorationContext`` 放回 ``core/``，本守卫会立刻报
越界（``core`` 只允许依赖 ``infra``）——这正是我们要的，不要再给它开例外。

已登记例外见 ``KNOWN_EXCEPTIONS``，每条注明原因与消除阶段。例外指向的 src 文件
若已不存在，按配置错误处理并要求清理，避免豁免腐化成永久债。

退出码：0 = 通过；1 = 存在未登记越界或例外配置失效。
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXCLUDE_DIRS = {
    "__pycache__", ".git", ".idea", ".workbuddy", ".venv-build", ".venv-macos",
    "data", "dist", "developer_tools", "build", "assets",
}

# --------------------------------------------------------------------------
# 一、层归属
# --------------------------------------------------------------------------
# 根目录模块 -> 层。阶段 2 已把领域模型收编进 core/，根目录只剩基础设施（paths）
# 与应用壳（app）；app_shell / main_window_manager 计划迁往 app/ 包。
# 此表同时充当「根目录已冻结」的白名单：根目录新增 .py 必须先在这里登记。
ROOT_MODULE_LAYERS = {
    "paths": "infra",
    "main": "app",
    "app_shell": "app",
    "main_window_manager": "app",
}

# 一级包 -> 层。一个特例在 _layer_from_parts 里判：
#   dungeon/window/**    -> dungeon_window
PACKAGE_LAYERS = {
    "core": "core",
    "persistence": "persistence",
    "services": "services",
    "ui": "ui",
    "dungeon": "dungeon",
}

ALL_LAYERS = {
    "infra", "core", "dungeon", "persistence", "services",
    "dungeon_window", "ui", "app",
}

# --------------------------------------------------------------------------
# 二、允许矩阵（每层可依赖的下层；自身恒允许）
# --------------------------------------------------------------------------
BASE_ALLOWED = {
    "infra": set(),
    "core": {"infra"},
    "dungeon": {"infra", "core"},
    "persistence": {"infra", "core", "dungeon"},
    "services": {"infra", "core", "dungeon", "persistence"},
    "dungeon_window": {"infra", "core", "dungeon", "persistence", "services"},
    "ui": {"infra", "core", "dungeon", "persistence", "services",
           "dungeon_window"},
    "app": set(ALL_LAYERS),
}

# --------------------------------------------------------------------------
# 三、UI 框架禁令（下层不得引入窗口框架）
# --------------------------------------------------------------------------
# dungeon/ 的领域层与窗口层由 check_dungeon_layering 负责，这里不重复。
FRAMEWORK_LAYERS = {"infra", "core", "persistence", "services"}
UI_FRAMEWORKS = {
    "tkinter", "_tkinter", "customtkinter", "dearpygui", "PIL",
}

# --------------------------------------------------------------------------
# 四、已登记例外
# --------------------------------------------------------------------------
# kind="layer"     ：src 依赖 dst（模块前缀），跨层方向不合矩阵
# kind="framework" ：src 引入了 dst（框架顶层名），下层不得碰 UI 框架
# src 是相对仓库根的文件路径（搬迁后若路径失效，守卫会报配置错误）。
KNOWN_EXCEPTIONS = [
    dict(
        kind="layer",
        src="persistence/character_repo.py",
        dst="services.image_service",
        why="仓库层要生成头像缩略图（persistence -> services）",
        plan="阶段 3 把纯图像处理下移到 core，仓库层只依赖它",
    ),
    dict(
        kind="framework",
        src="persistence/character_repo.py",
        dst="PIL",
        why="生成缩略图需要解码图片",
        plan="阶段 3 随纯图像处理一起下移到 core",
    ),
    dict(
        kind="framework",
        src="services/image_service.py",
        dst="customtkinter",
        why="服务层里混着界面逻辑（ui.common.dialogs 反向依赖它）",
        plan="阶段 3 拆出纯图像部分，界面部分上移回 ui",
    ),
    dict(
        kind="framework",
        src="services/image_service.py",
        dst="PIL",
        why="图像处理本体",
        plan="阶段 3 随拆分保留在 core 侧",
    ),
    dict(
        kind="framework",
        src="services/preview/__init__.py",
        dst="PIL",
        why="预览剪影绘制本体",
        plan="阶段 3 保留在 core 侧",
    ),
]


# --------------------------------------------------------------------------
# 实现
# --------------------------------------------------------------------------
def _layer_from_parts(parts):
    """按路径片段（目录部分 + 可选文件名）判层，返回 None 表示不参与。"""
    if not parts:
        return None
    top = parts[0]
    if len(parts) == 1:
        # 只有一段：要么是根目录文件，要么是顶层包名（``import ui``）
        if top in PACKAGE_LAYERS:
            return PACKAGE_LAYERS[top]
        return ROOT_MODULE_LAYERS.get(top)
    if top == "dungeon":
        return "dungeon_window" if parts[1] == "window" else "dungeon"
    return PACKAGE_LAYERS.get(top)


def layer_of_file(path):
    rel = path.relative_to(ROOT)
    parts = list(rel.with_suffix("").parts)
    return _layer_from_parts(parts)


def layer_of_module(mod):
    if not mod:
        return None
    parts = mod.split(".")
    if parts[0] not in PACKAGE_LAYERS and parts[0] not in ROOT_MODULE_LAYERS:
        return None
    return _layer_from_parts(parts)


def resolve_relative(path, level, module, alias):
    """把相对导入解析成绝对模块名。"""
    raw = list(path.relative_to(ROOT).with_suffix("").parts)
    pkg = raw[:-1]  # __init__.py 与普通模块都取到「所在包」
    keep = len(pkg) - (level - 1)
    if keep < 0:
        return None
    base = pkg[:keep]
    if module:
        base = base + module.split(".")
    elif alias:
        base = base + [alias]
    return ".".join(base) if base else None


def collect_imports(path):
    """返回 [(绝对模块名, 行号, 是否函数内延迟导入)]；源码不可解析时返回 None。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return None

    module_level = {
        id(node) for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
    }

    found = []
    for node in ast.walk(tree):
        lazy = id(node) not in module_level
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name, node.lineno, lazy))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if node.module:
                    target = resolve_relative(path, node.level, node.module, None)
                    if target:
                        found.append((target, node.lineno, lazy))
                else:
                    for alias in node.names:
                        target = resolve_relative(path, node.level, None, alias.name)
                        if target:
                            found.append((target, node.lineno, lazy))
            elif node.module:
                found.append((node.module, node.lineno, lazy))
    return found


def _exception_key(exc):
    return (exc["kind"], exc["src"], exc["dst"])


def _match_exception(kind, src_rel, target):
    """按 kind 匹配例外。layer 比前缀，framework 比顶层名。"""
    for exc in KNOWN_EXCEPTIONS:
        if exc["kind"] != kind or exc["src"] != src_rel:
            continue
        dst = exc["dst"]
        if kind == "framework":
            if target.split(".")[0] == dst:
                return exc
        else:
            if target == dst or target.startswith(dst + ".") or dst.startswith(target + "."):
                return exc
    return None


def check_config():
    """守卫自身的配置自洽性——配置写错会让检查静默失效。"""
    problems = []
    if set(BASE_ALLOWED) != ALL_LAYERS:
        problems.append(
            f"ALLOWED 覆盖的层 {sorted(BASE_ALLOWED)} 与层集合 {sorted(ALL_LAYERS)} 不一致")
    for layer, allowed in BASE_ALLOWED.items():
        unknown = allowed - ALL_LAYERS
        if unknown:
            problems.append(f"层 {layer} 的允许集含未知层：{sorted(unknown)}")
    if set(ROOT_MODULE_LAYERS.values()) - ALL_LAYERS:
        problems.append("ROOT_MODULE_LAYERS 出现未登记的层名")
    if set(PACKAGE_LAYERS.values()) - ALL_LAYERS:
        problems.append("PACKAGE_LAYERS 出现未登记的层名")
    if FRAMEWORK_LAYERS - ALL_LAYERS:
        problems.append("FRAMEWORK_LAYERS 出现未登记的层名")
    for exc in KNOWN_EXCEPTIONS:
        if not (ROOT / exc["src"]).is_file():
            problems.append(
                f"例外指向的文件已不存在（搬迁后请清理该条例外）：{exc['src']}")
    return problems


def main() -> int:
    config_problems = check_config()

    modules = []
    for path in sorted(ROOT.rglob("*.py")):
        rel_parts = path.relative_to(ROOT).parts
        if any(part in EXCLUDE_DIRS for part in rel_parts):
            continue
        layer = layer_of_file(path)
        if layer is None:
            continue
        imports = collect_imports(path)
        if imports is None:
            print(f"  WARN 源码无法解析，跳过：{path.relative_to(ROOT).as_posix()}")
            continue
        modules.append((path, layer, imports))

    # 根目录冻结：新增 .py 必须先在 ROOT_MODULE_LAYERS 登记并归层
    unregistered = [
        p.name for p in sorted(ROOT.glob("*.py"))
        if p.stem not in ROOT_MODULE_LAYERS
    ]

    violations = []
    hits = {_exception_key(e): 0 for e in KNOWN_EXCEPTIONS}
    edge_count = 0

    for path, src_layer, imports in modules:
        rel = path.relative_to(ROOT).as_posix()
        allowed = BASE_ALLOWED[src_layer] | {src_layer}
        for target, lineno, lazy in imports:
            dst_layer = layer_of_module(target)
            if dst_layer is not None:
                edge_count += 1
                if dst_layer in allowed:
                    continue
                exc = _match_exception("layer", rel, target)
                if exc:
                    hits[_exception_key(exc)] += 1
                    continue
                violations.append({
                    "file": rel, "lineno": lineno, "target": target,
                    "src": src_layer, "dst": dst_layer, "lazy": lazy,
                })
                continue
            # 非第一方：只看下层有没有偷用 UI 框架
            top = target.split(".")[0]
            if src_layer in FRAMEWORK_LAYERS and top in UI_FRAMEWORKS:
                exc = _match_exception("framework", rel, target)
                if exc:
                    hits[_exception_key(exc)] += 1
                    continue
                violations.append({
                    "file": rel, "lineno": lineno, "target": target,
                    "src": src_layer, "dst": f"UI 框架 {top}", "lazy": lazy,
                })

    stale = [e for e in KNOWN_EXCEPTIONS if hits[_exception_key(e)] == 0]

    print(f"[check_import_graph] 已检查 {len(modules)} 个模块，"
          f"{edge_count} 条第一方依赖边")
    print(f"[check_import_graph] 允许矩阵："
          + "；".join(f"{k}<-{'/'.join(sorted(v)) or '∅'}"
                      for k, v in sorted(BASE_ALLOWED.items())))

    if violations:
        for v in violations:
            tag = "延迟" if v["lazy"] else "顶层"
            print(f"  FAIL {v['file']}:{v['lineno']} [{tag}] {v['target']}"
                  f" — 越界：{v['src']} 不得依赖 {v['dst']}")
    if unregistered:
        for name in unregistered:
            print(f"  FAIL 根目录模块未登记归层：{name}"
                  f" — 根目录已冻结，请移入对应包或更新 ROOT_MODULE_LAYERS")
    for problem in config_problems:
        print(f"  FAIL 守卫配置：{problem}")

    if hits and any(hits.values()):
        print(f"[check_import_graph] 已登记例外 {len(KNOWN_EXCEPTIONS)} 条，"
              f"本次命中 {sum(1 for v in hits.values() if v)} 条：")
        for exc in KNOWN_EXCEPTIONS:
            mark = "命中" if hits[_exception_key(exc)] else "未触发"
            print(f"  · [{exc['kind']}] {exc['src']} -> {exc['dst']}"
                  f"（{mark}，{exc['why']}；{exc['plan']}）")
    if stale:
        print(f"  WARN 以下例外本次未被触发，确认是否已可删除：")
        for exc in stale:
            print(f"    - [{exc['kind']}] {exc['src']} -> {exc['dst']}")

    if violations or unregistered or config_problems:
        total = len(violations) + len(unregistered) + len(config_problems)
        print(f"[check_import_graph] FAILED {total} 项")
        return 1

    print("[check_import_graph] PASSED：层间依赖方向与 UI 框架禁令均无未登记越界")
    return 0


if __name__ == "__main__":
    sys.exit(main())
