"""小游戏数据包读取（领域层）：manifest 解析与参数声明。

从 ``dungeon/window/minigame`` 下沉而来，供两侧共用：

- ``dungeon.validate``——校验器需要读取参数声明做诊断（领域层禁止
  反向依赖 ``dungeon.window``，见 ``tests/check_dungeon_layering.py``）；
- ``dungeon.window.minigame``——运行时解析游戏包。

这里只读 ``data/packs/minigames/<id>/`` 的文件与内置注册表，不 import 渲染后端。
"""

import json
import os

from dungeon import process_log


def pack_roots(game_id):
    """小游戏数据包的候选目录：自由目录优先，其次是激活世界包内的同名目录。"""
    from paths import active_world_id, data_dir
    root = data_dir()
    roots = [os.path.join(root, "packs", "minigames", str(game_id))]
    world_id = active_world_id()
    if world_id:
        roots.append(os.path.join(root, "worlds", world_id, "minigames", str(game_id)))
    return roots


def pack_root(game_id):
    """解析到的小游戏包目录；都不存在时返回自由目录下的位置（写路径）。"""
    roots = pack_roots(game_id)
    for root in roots:
        if os.path.isdir(root):
            return root
    return roots[0]


def pack_ids():
    """两处数据包目录下的小游戏 id（自由目录优先，去重排序）。"""
    from paths import active_world_id, data_dir
    roots = [os.path.join(data_dir(), "packs", "minigames")]
    world_id = active_world_id()
    if world_id:
        roots.append(os.path.join(data_dir(), "worlds", world_id, "minigames"))
    found = set()
    for root in roots:
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            if name.startswith(".") or name == "__pycache__":
                continue
            if os.path.isdir(os.path.join(root, name)):
                found.add(name)
    return sorted(found)


def load_manifest(root):
    """读包目录的 manifest.json；不存在 / 解析失败返回 None（诊断交给校验器）。"""
    path = os.path.join(root, "manifest.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, ValueError) as exc:
        process_log.log(f"[MiniGame] manifest 解析失败 {path}: {exc}")
        return None
    return manifest if isinstance(manifest, dict) else None


def mini_game_params(game_id, registry=None):
    """小游戏的参数声明（``manifest.params`` / 内置类 ``params``），编辑器
    据此动态生成表单。返回 ``[{key, label, type, default, min, max}, ...]``；
    未知游戏返回 ``[]``。

    ``registry`` 是 window 层的内置游戏注册表（当前为空扩展点），由调用方
    传入——领域层不反向依赖 ``dungeon.window``。
    """
    game_id = str(game_id or "").strip()
    if registry and game_id in registry:
        return [dict(p) for p in (getattr(registry[game_id], "params", ()) or ())]
    root = pack_root(game_id)
    if not os.path.isdir(root):
        return []
    manifest = load_manifest(root) or {}
    params = manifest.get("params")
    if not isinstance(params, list):
        return []
    return [p for p in params if isinstance(p, dict) and p.get("key")]
