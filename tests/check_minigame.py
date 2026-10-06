# -*- coding: utf-8 -*-
"""小游戏包契约守卫（无 GUI，可进 CI 门禁）。

检查对象：``data/packs/minigames/*/``（数据包）与
``dungeon/window/minigame/games/``（未来内置游戏）。

manifest 层：
- ``manifest.json`` 存在且可解析；``id`` 与目录名一致（声明了才查）；
- ``backend`` ∈ {py, web}；``py`` → ``entry`` 存在；``web`` → ``session.html`` 存在；
- ``params`` 声明结构合法（key/label/type，min<=max）。

py 游戏代码层（AST 扫描 ``entry``）：
- **恰一个** ``MiniGame`` 子类，且 ``id`` 与包 id 一致；
- 禁 import：dearpygui / tkinter / customtkinter / threading / subprocess /
  socket / ui / services / PIL（贴图加载是运行时职责，走 ``api.draw_image``）；
- 禁调用 ``print``（输出走过程日志 / ``api.notify``）；
- 禁触碰私有面：``api._`` / ``self.api._`` / ``stage._`` 属性访问一律违规。

用法：``python tests/check_minigame.py``；有违规时退出码 1。
"""

import ast
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

_PACK_ROOT = os.path.join(_ROOT, "data", "packs", "minigames")
_BUILTIN_ROOT = os.path.join(_ROOT, "dungeon", "window", "minigame", "games")

_ALLOWED_BACKENDS = ("py", "web")
#: py 游戏代码里禁止 import 的顶层模块
FORBIDDEN_IMPORTS = {
    "dearpygui": "绘制必须走 GameAPI（运行时才允许 DPG）",
    "tkinter": "禁 GUI 宿主细节",
    "customtkinter": "禁 GUI 宿主细节",
    "threading": "帧时钟是唯一时间源，禁自开线程",
    "subprocess": "禁进程操作",
    "socket": "禁网络",
    "ui": "禁界面层（宿主能力经运行时提供）",
    "services": "禁服务层",
    "PIL": "贴图加载由运行时代劳（api.draw_image）",
}
_PARAM_TYPES = ("int", "float", "str", "bool", "color")

_violations = []


def fail(path, lineno, message):
    where = f"{os.path.relpath(path, _ROOT)}:{lineno or '?'}" if lineno else \
        os.path.relpath(path, _ROOT)
    _violations.append(f"{where}  {message}")


def check_manifest(pack_dir):
    manifest_path = os.path.join(pack_dir, "manifest.json")
    if not os.path.isfile(manifest_path):
        return None  # 历史 web 包无 manifest：由校验器兼容，此处不重复报
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, ValueError) as exc:
        fail(manifest_path, 0, f"manifest.json 解析失败: {exc}")
        return None
    if not isinstance(manifest, dict):
        fail(manifest_path, 0, "manifest.json 必须是对象")
        return None
    pack_id = os.path.basename(pack_dir)
    declared = str(manifest.get("id") or "").strip()
    if declared and declared != pack_id:
        fail(manifest_path, 0, f"manifest id「{declared}」与目录名「{pack_id}」不一致")
    backend = str(manifest.get("backend") or "").strip()
    if backend not in _ALLOWED_BACKENDS:
        fail(manifest_path, 0, f"backend「{backend}」非法（允许：{'/'.join(_ALLOWED_BACKENDS)}）")
        return manifest
    if backend == "py":
        entry = str(manifest.get("entry") or "game.py")
        if not os.path.isfile(os.path.join(pack_dir, entry)):
            fail(manifest_path, 0, f"py 后端入口「{entry}」不存在")
    elif backend == "web" and not os.path.isfile(os.path.join(pack_dir, "session.html")):
        fail(manifest_path, 0, "web 后端缺少 session.html")
    params = manifest.get("params")
    if params is not None:
        if not isinstance(params, list):
            fail(manifest_path, 0, "params 必须是数组")
        else:
            for i, param in enumerate(params):
                if not isinstance(param, dict) or not str(param.get("key") or "").strip():
                    fail(manifest_path, 0, f"params[{i}] 缺少 key")
                    continue
                ptype = str(param.get("type") or "").strip()
                if ptype and ptype not in _PARAM_TYPES:
                    fail(manifest_path, 0,
                         f"params[{i}].type「{ptype}」非法（允许：{'/'.join(_PARAM_TYPES)}）")
                if ptype in ("int", "float"):
                    try:
                        low, high = param.get("min"), param.get("max")
                        if low is not None and high is not None and float(low) > float(high):
                            fail(manifest_path, 0, f"params[{i}] min > max")
                    except (TypeError, ValueError):
                        fail(manifest_path, 0, f"params[{i}] 的 min/max 必须是数字")
    return manifest


def _check_forbidden_imports(tree, path):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in FORBIDDEN_IMPORTS:
                    fail(path, node.lineno,
                         f"禁 import {alias.name}（{FORBIDDEN_IMPORTS[top]}）")
        elif isinstance(node, ast.ImportFrom):
            top = (node.module or "").split(".")[0]
            if top in FORBIDDEN_IMPORTS:
                fail(path, node.lineno,
                     f"禁 from {node.module} import …（{FORBIDDEN_IMPORTS[top]}）")


def _check_private_access(tree, path):
    """禁 api._ / self.api._ / stage._ 形态的私有面访问。"""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or not node.attr.startswith("_"):
            continue
        value = node.value
        if isinstance(value, ast.Name) and value.id == "api":
            fail(path, node.lineno, f"禁触碰私有面 api.{node.attr}")
        elif (isinstance(value, ast.Attribute) and value.attr == "api"
              and isinstance(value.value, ast.Name) and value.value.id == "self"):
            fail(path, node.lineno, f"禁触碰私有面 self.api.{node.attr}")
        elif isinstance(value, ast.Name) and value.id == "stage":
            fail(path, node.lineno, f"禁触碰运行时私有面 stage.{node.attr}")


def _check_print(tree, path):
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "print"):
            fail(path, node.lineno, "禁 print（输出走 api.notify / 过程日志）")


def check_py_game(entry_path, pack_id):
    try:
        with open(entry_path, "r", encoding="utf-8") as fh:
            source = fh.read()
    except OSError as exc:
        fail(entry_path, 0, f"读取失败: {exc}")
        return
    try:
        tree = ast.parse(source, filename=entry_path)
    except SyntaxError as exc:
        fail(entry_path, exc.lineno or 0, f"语法错误: {exc.msg}")
        return
    subclasses = []
    for node in tree.body:   # 只看模块顶层定义
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                name = getattr(base, "id", None) or getattr(base, "attr", None)
                if name in ("MiniGame",):
                    subclasses.append(node)
                    if node.name == "MiniGame":
                        fail(entry_path, node.lineno, "不得把类命名为 MiniGame 本身")
    if len(subclasses) != 1:
        fail(entry_path, 0, f"必须定义恰一个 MiniGame 子类（实际 {len(subclasses)} 个）")
        return
    cls = subclasses[0]
    declared_id = None
    for stmt in cls.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 \
                and isinstance(stmt.targets[0], ast.Name) \
                and stmt.targets[0].id == "id":
            declared_id = getattr(stmt.value, "value", None)
    if declared_id != pack_id:
        fail(entry_path, cls.lineno,
             f"类属性 id={declared_id!r} 与包 id「{pack_id}」不一致")
    _check_forbidden_imports(tree, entry_path)
    _check_print(tree, entry_path)
    _check_private_access(tree, entry_path)


def main():
    if not os.path.isdir(_PACK_ROOT):
        print("[check_minigame] 没有 data/packs/minigames/ 目录，无事可查")
        return 0
    for name in sorted(os.listdir(_PACK_ROOT)):
        pack_dir = os.path.join(_PACK_ROOT, name)
        if not os.path.isdir(pack_dir):
            continue
        manifest = check_manifest(pack_dir)
        if manifest and str(manifest.get("backend") or "") == "py":
            entry = str(manifest.get("entry") or "game.py")
            entry_path = os.path.join(pack_dir, entry)
            if os.path.isfile(entry_path):
                check_py_game(entry_path, name)
    if os.path.isdir(_BUILTIN_ROOT):  # 未来内置游戏（REGISTRY 登记）
        for name in sorted(os.listdir(_BUILTIN_ROOT)):
            if name.endswith(".py") and name != "__init__.py":
                check_py_game(os.path.join(_BUILTIN_ROOT, name), name[:-3])

    if _violations:
        print(f"[check_minigame] FAILED，{len(_violations)} 处违规：")
        for item in _violations:
            print(f"  - {item}")
        return 1
    print("[check_minigame] PASSED：小游戏包契约全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
