"""资源索引缓存：``<数据目录>/user/catalog.json`` 的读写。

索引是**缓存不是权威**：删掉可以从文件系统重建。因此这里只做三件事——

1. :func:`load_catalog` 读缓存，缺失或结构不对就重建并写回；
2. :func:`refresh_catalog` 与磁盘比对，**内容没变不写盘**（避免每次启动都
   改文件时间戳、触发备份）；
3. :func:`save_catalog` 原子写。

``rev`` 在本地写时永远是空串：接服务端后它才作为远端游标使用，本地从不
凭空造一个版本号。形状见规范 §4.3。
"""

import os
from typing import Optional

from ..json_store import load_json_with_backup, write_json_atomic
from . import registry

CATALOG_SCHEMA = "gg.catalog/1"

#: 相对数据目录的缓存位置
CATALOG_RELPATH = ("user", "catalog.json")


def catalog_path(data_dir: str = "data") -> str:
    """索引缓存的文件路径。"""
    return os.path.join(data_dir, *CATALOG_RELPATH)


def _read(data_dir: str) -> Optional[dict]:
    """读缓存；缺失、损坏或 schema 不符都返回 None。"""
    data = load_json_with_backup(catalog_path(data_dir))
    if not isinstance(data, dict) or data.get("schema") != CATALOG_SCHEMA:
        return None
    if not isinstance(data.get("entries"), dict):
        return None
    return data


def save_catalog(catalog: dict, data_dir: str = "data") -> str:
    """原子写缓存（不保留 ``.bak``：缓存随时可重建）。"""
    path = catalog_path(data_dir)
    write_json_atomic(path, catalog, backup=False)
    return path


def refresh_catalog(data_dir: str = "data", world_state=None,
                    force: bool = False) -> dict:
    """从磁盘重建索引；与现有缓存一致时不写盘。

    ``world_state`` 只影响「包内接管」的读取目录；索引缓存本身记的是
    自由目录 + 当前生效包合成后的可见资源。
    """
    built = registry.build_catalog(data_dir, world_state)
    if not force and _read(data_dir) == built:
        return built
    save_catalog(built, data_dir)
    return built


def load_catalog(data_dir: str = "data", world_state=None) -> dict:
    """读缓存；没有可用缓存时重建（并写盘）。"""
    cached = _read(data_dir)
    if cached is not None:
        return cached
    return refresh_catalog(data_dir, world_state, force=True)


def catalog_ids(catalog: dict, type_id: str) -> list:
    """索引里某类型的全部资源 id（保持索引里的顺序）。"""
    entries = (catalog or {}).get("entries") or {}
    return [entry.get("id", "") for entry in entries.get(type_id, [])
            if isinstance(entry, dict)]
