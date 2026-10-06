"""小游戏框架：包解析与注册表（作者契约见 ``base.py``，运行时见 ``stage.py``）。

游戏来源两级，解析顺序（:func:`resolve_mini_game`）：

1. **内置注册表**（``REGISTRY``：id → :class:`~.base.MiniGame` 子类）——
   常驻框架包，随应用发布；
2. **数据包** ``data/packs/minigames/<id>/``——``manifest.json`` 声明后端：
   - ``"py"``：``entry`` 指向的 Python 文件里**恰一个** MiniGame 子类，
     运行时动态加载（契约由 ``tests/check_minigame.py`` 守卫）；
   - ``"web"``：``session.html``，交给宿主端口 ``launch_mini_game()``
     用子进程 pywebview 打开（兼容通道，见 ui/common/mini_game_host.py）。

无 manifest 但存在 ``session.html`` 的目录按 web 兼容（历史包）。

本模块**不 import DPG**（解析发生在触发器线程，渲染只在 stage）；游戏文件
里禁止出现 dearpygui / tkinter / threading 等，一律由契约脚本扫出来。
"""

import importlib.util
import os

from dungeon import process_log
from dungeon.actions import MINI_GAME_DEFAULT_ID
from dungeon.minigame_pack import (load_manifest as _load_manifest,
                                   mini_game_params as _mini_game_params,
                                   pack_root as _pack_root)
from .base import GameAPI, MiniGame, clamp_dt  # noqa: F401  （作者只 import 本包）

#: 支持的后端
BACKEND_PY = "py"
BACKEND_WEB = "web"
BACKENDS = (BACKEND_PY, BACKEND_WEB)

#: 内置游戏注册表（id → MiniGame 子类）。当前内置游戏都在数据包里，
#: 留空作为框架扩展点：这里登记的游戏优先于数据包同名 id。
REGISTRY = {}


class ResolvedMiniGame:
    """一次解析结果：后端 + 加载入口（惰性，触发时才真正 import）。"""

    __slots__ = ("game_id", "backend", "label", "root", "entry", "cls")

    def __init__(self, game_id, backend, label="", root=None, entry=None, cls=None):
        self.game_id = game_id
        self.backend = backend
        self.label = label or game_id
        #: py 后端：包目录（贴图等资源的相对路径基准）
        self.root = root
        #: py 后端：entry 的绝对路径
        self.entry = entry
        #: 内置游戏：直接是类对象
        self.cls = cls

    def load(self):
        """返回 MiniGame 子类。py 后端首次调用时从 ``entry`` 动态加载。"""
        if self.cls is not None:
            return self.cls
        spec = importlib.util.spec_from_file_location(
            f"_minigame_{self.game_id}", self.entry)
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            raise ImportError(f"小游戏「{self.game_id}」加载失败: {exc}") from exc
        classes = [
            obj for obj in vars(module).values()
            if isinstance(obj, type) and issubclass(obj, MiniGame)
            and obj is not MiniGame and getattr(obj, "id", "") == self.game_id
        ]
        if len(classes) != 1:
            raise ImportError(
                f"小游戏「{self.game_id}」的 {self.entry} 必须定义恰一个 "
                f"id 匹配的 MiniGame 子类（实际 {len(classes)} 个）")
        self.cls = classes[0]
        return self.cls


def resolve_mini_game(game_id):
    """按 id 解析小游戏；找不到返回 None（调用方打日志并跳过触发器）。"""
    game_id = str(game_id or "").strip()
    if not game_id or os.sep in game_id or "/" in game_id:
        return None
    if game_id in REGISTRY:
        cls = REGISTRY[game_id]
        return ResolvedMiniGame(game_id, BACKEND_PY,
                                label=getattr(cls, "label", game_id), cls=cls)
    root = _pack_root(game_id)
    if not os.path.isdir(root):
        return None
    manifest = _load_manifest(root) or {}
    backend = str(manifest.get("backend") or "").strip()
    label = str(manifest.get("name") or game_id)
    if backend == BACKEND_PY:
        entry = str(manifest.get("entry") or "game.py")
        entry_path = os.path.join(root, entry)
        if not os.path.isfile(entry_path):
            process_log.log(f"[MiniGame] 小游戏「{game_id}」的入口不存在：{entry_path}")
            return None
        return ResolvedMiniGame(game_id, BACKEND_PY, label=label,
                                root=root, entry=entry_path)
    if backend == BACKEND_WEB:
        if not os.path.isfile(os.path.join(root, "session.html")):
            process_log.log(f"[MiniGame] web 小游戏「{game_id}」缺少 session.html")
            return None
        return ResolvedMiniGame(game_id, BACKEND_WEB, label=label, root=root)
    # 无 manifest / 未声明后端：session.html 存在即按 web 兼容（历史包）
    if os.path.isfile(os.path.join(root, "session.html")):
        return ResolvedMiniGame(game_id, BACKEND_WEB, label=label, root=root)
    process_log.log(f"[MiniGame] 小游戏「{game_id}」既无有效 manifest 也无 session.html")
    return None


def mini_game_params(game_id):
    """小游戏的参数声明（``manifest.params`` / 内置类 ``params``），编辑器
    据此动态生成表单。读取逻辑在 ``dungeon.minigame_pack``（领域层）。"""
    return _mini_game_params(game_id, REGISTRY)


def list_mini_games():
    """枚举可用小游戏：``[(id, label, backend), ...]``（编辑器候选用）。

    数据包目录名即 id；解析失败的目录静默跳过（诊断由校验器负责）。
    """
    from paths import data_dir
    found = {}
    for game_id, cls in REGISTRY.items():
        found[game_id] = (game_id, getattr(cls, "label", game_id), BACKEND_PY)
    packs_root = os.path.join(data_dir(), "packs", "minigames")
    if os.path.isdir(packs_root):
        for name in sorted(os.listdir(packs_root)):
            if name in found:
                continue
            resolved = resolve_mini_game(name)
            if resolved is not None:
                found[name] = (name, resolved.label, resolved.backend)
    return sorted(found.values())


__all__ = ["GameAPI", "MiniGame", "ResolvedMiniGame", "REGISTRY",
           "MINI_GAME_DEFAULT_ID", "BACKEND_PY", "BACKEND_WEB", "BACKENDS",
           "clamp_dt", "resolve_mini_game", "list_mini_games",
           "mini_game_params"]
