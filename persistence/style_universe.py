"""风格素材的读取目录解析：世界包命名空间合并（P3b）。

地标风格与描述风格（``landmarks`` / ``quips``，即带 ``.addr.json`` 伴随
文件的两类）共用。规则：

- 激活包**未声明** worlds：整类型接管（包目录即全部素材，旧行为）；
- 激活包**声明** worlds：包只接管其声明的维度——自由目录中注册地址
  落在**其它**维度、或未注册地址的风格保持可用；注册地址在声明世界
  内的自由风格被包遮蔽（命名空间冲突，包赢）；
- 同名冲突一律包优先：自由目录与包内同名的风格直接遮蔽，避免同名歧义；
- 未激活包：全部来自自由目录。

其余资源类型（性格表 / 身材表 / 副本等）不参与地址命名空间，维持整类型
接管，不经本模块。
"""

import os
from typing import List, Optional, Set

from core.address_model import world_of
from .resources import registry
from .style_addr import load_address


def _list_styles(rtype: str, folder: str) -> List[str]:
    """列出目录下的风格名（主文件形状由资源声明决定，伴随文件与备份排除）。"""
    styles = []
    if os.path.isdir(folder):
        for name in os.listdir(folder):
            if registry.is_resource_entry(rtype, folder, name):
                styles.append(registry.resource_id_from_entry(rtype, name))
    return styles


def _pack_dir(world_state, rtype: str) -> Optional[str]:
    """包内该资源类型的目录；未拥有时 None。"""
    if world_state is not None and world_state.owns(rtype):
        return world_state.pack_path(rtype)
    return None


def namespace_merge(world_state, rtype: str) -> bool:
    """该类型是否启用命名空间合并：拥有该类型且声明了 worlds。"""
    return (world_state is not None
            and bool(world_state.declared_worlds())
            and world_state.owns(rtype))


def merged_styles(world_state, rtype: str, free_dir: str) -> List[str]:
    """该类型的风格全集（排序）。未激活 = 自由目录；未声明 worlds = 仅包内。"""
    pack = _pack_dir(world_state, rtype)
    if pack is None:
        return sorted(_list_styles(rtype, free_dir))
    pack_styles = _list_styles(rtype, pack)
    if not namespace_merge(world_state, rtype):
        return sorted(pack_styles)
    declared: Set[str] = world_state.declared_worlds()
    pack_set = set(pack_styles)
    merged = list(pack_styles)
    for style in _list_styles(rtype, free_dir):
        if style in pack_set:
            continue
        reg_world = world_of(load_address(free_dir, style))
        if reg_world and reg_world in declared:
            continue
        merged.append(style)
    return sorted(merged)


def style_read_dir(world_state, rtype: str, free_dir: str, style_name: str) -> str:
    """单个风格的读取目录（主文件与 .addr.json 同目录）。

    包内有该风格主文件 → 包目录；包未拥有或（合并模式下）包内没有该
    风格 → 自由目录；未声明 worlds 的包即使缺该风格也指向包目录（保持
    整类型接管时"包外风格一律不可见"的旧行为）。
    """
    pack = _pack_dir(world_state, rtype)
    if pack is not None:
        primary = registry.primary_relpath(rtype, style_name).replace("/", os.sep)
        if os.path.isfile(os.path.join(pack, primary)):
            return pack
        if not namespace_merge(world_state, rtype):
            return pack
    return free_dir
