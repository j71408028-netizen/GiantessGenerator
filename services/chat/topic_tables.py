"""话题表资源：磁盘上的一整张话题类型表 ↔ ``TopicKind``。

话题是**整表替换**的：一份容器就是一张完整表，内容为 JSON 数组，元素形状与
``topic_kind_to_dict`` 完全一致，因此「导出 → 改 → 导回」可以原样往返。

三条约定：

- **路径与枚举一律走资源注册表**（``persistence.resources``），本模块不拼路径；
- **内置表恒可用**：``default`` 在没有对应文件时指向代码里的 ``TOPIC_KINDS``，
  这样界面上永远有东西可打包，且不为了打包往用户目录写文件；
- **坏条目跳过**：单条解析失败只丢那一条并打印，整表是否可用交给
  ``install_topic_kinds`` 的结构校验（它会拒绝空表与自相矛盾的条目）。

世界包激活时由 :class:`services.worlds.service.WorldManager` 调 :func:`activate`，
卸载时调 :func:`restore`。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

from persistence.resources import registry
from services.chat.topic_kinds import (
    NO_TOPIC_LENS,
    TOPIC_KINDS,
    TopicKind,
    install_topic_kinds,
    reset_topic_kinds,
    topic_kind_from_dict,
    topic_kind_to_dict,
)

#: 内置表 id：无同名文件时回落到代码里的话题表
BUILTIN_TABLE = "default"


def builtin_entries() -> List[Dict[str, Any]]:
    """代码内置话题表（含兜底视角）的 JSON 条目。"""
    return [topic_kind_to_dict(kind) for kind in (*TOPIC_KINDS, NO_TOPIC_LENS)]


def list_tables(data_dir: str = "data", world_state=None) -> List[str]:
    """可用的话题表 id：内置表恒在首位，其余来自自由目录（或包内）。"""
    found = list(registry.list_ids("topics", data_dir, world_state))
    if BUILTIN_TABLE in found:
        found.remove(BUILTIN_TABLE)
    return [BUILTIN_TABLE, *found]


def table_path(table: str, data_dir: str = "data", world_state=None) -> str:
    """话题表文件路径（包内优先，其次自由目录）。"""
    return registry.read_path("topics", table, data_dir, world_state)


def load_entries(table: str, data_dir: str = "data",
                 world_state=None) -> List[Dict[str, Any]]:
    """读出原始条目数组；磁盘无此表时按内置表回落。

    根节点不是数组、JSON 无法解析时抛 ``ValueError``——整表坏了没有可以
    跳过的最小单位。
    """
    path = table_path(table, data_dir, world_state)
    if not os.path.isfile(path):
        if table == BUILTIN_TABLE:
            return builtin_entries()
        raise ValueError(f"话题表 '{table}' 不存在（{path}）")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ValueError(f"话题表 '{table}' 无法解析: {exc}")
    if not isinstance(data, list):
        raise ValueError(f"话题表 '{table}' 的根节点应为数组")
    return [item for item in data if isinstance(item, dict)]


def load_kinds(table: str, data_dir: str = "data",
               world_state=None) -> Tuple[TopicKind, ...]:
    """解析成 ``TopicKind``；单条失败只丢那一条并打印。"""
    kinds: List[TopicKind] = []
    for index, item in enumerate(load_entries(table, data_dir, world_state)):
        try:
            kinds.append(topic_kind_from_dict(item))
        except ValueError as exc:
            print(f"[Topics] {table} 第 {index} 条跳过: {exc}")
    return tuple(kinds)


def dump_kinds(kinds) -> List[Dict[str, Any]]:
    """``TopicKind`` 序列列 → 可写盘的条目数组（与 :func:`load_kinds` 互逆）。"""
    return [topic_kind_to_dict(kind) for kind in kinds]


def activate(table: str, data_dir: str = "data", world_state=None) -> Tuple[TopicKind, ...]:
    """把某张表安装为当前生效的话题类型表；校验不过抛 ``ValueError``。"""
    if not table:
        return restore()
    return install_topic_kinds(load_kinds(table, data_dir, world_state))


def restore() -> Tuple[TopicKind, ...]:
    """还原代码内置表（世界包卸载 / 解散后调用）。"""
    return reset_topic_kinds()


def declared_table(manifest) -> Optional[str]:
    """世界包声明的话题表 id（未声明返回 None）。"""
    if manifest is None or not manifest.owns("topics"):
        return None
    declared = list(manifest.resources.get("topics") or [])
    return declared[0] if declared else None
