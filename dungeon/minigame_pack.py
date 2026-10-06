"""小游戏数据包读取（领域层）：manifest 解析与参数声明。

从 ``dungeon/window/minigame`` 下沉而来，供两侧共用：

- ``dungeon.validate``——校验器需要读取参数声明做诊断（领域层禁止
  反向依赖 ``dungeon.window``，见 ``tests/check_dungeon_layering.py``）；
- ``dungeon.window.minigame``——运行时解析游戏包。

这里只读 ``data/packs/minigames/<id>/`` 的文件与内置注册表，不 import DPG。
"""

import json
import os

from dungeon import process_log


def pack_root(game_id):
    from paths import data_dir
    return os.path.join(data_dir(), "packs", "minigames", str(game_id))


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
