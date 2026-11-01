"""取材表组的路径解析：组目录、成员文件定位与枚举。

取材表是**一个目录即一份资源**的资源类型，目录内四个 CSV 成员各自被不同的
消费者读取（姓名 → 角色创建，身材 → 体型比例，性格 → 行为参数，新闻 → 近况
动态）。成员文件名固定，由 ``core.resource_spec.MATERIAL_MEMBERS`` 声明。

两级回落：

1. 世界包接管取材表时，包内同名成员优先；
2. 组内缺少某个成员时，回落到内置缺省组（``default``）的同名成员。

第 2 条让「只覆盖其中一两张表」不必复制整套文件，也让以后新增成员类型不需要
迁移存量组。调用方拿到的永远是**一个可读路径**（文件可能不存在，此时按空表
处理，与旧行为一致）。
"""

import os
from typing import List, Optional

from core.resource_spec import MATERIAL_MEMBERS, spec_of
from paths import data_dir as _default_data_dir
from persistence.resources.registry import free_dir as _free_dir_of

TYPE_ID = "materials"

#: 缺省组名：内置素材住在这里，也是成员缺席时的回落目标。
DEFAULT_MATERIAL_TABLE = "default"

#: 成员键（顺序即界面展示顺序）。
MEMBER_KEYS = tuple(member.key for member in MATERIAL_MEMBERS)


def member_label(key: str) -> str:
    """成员键的界面显示名；未知键原样返回。"""
    spec = spec_of(TYPE_ID)
    member = spec.member(key) if spec is not None else None
    return member.label if member is not None else key


def _data_dir(data_dir: Optional[str]) -> str:
    return data_dir or _default_data_dir()


def free_root(data_dir: Optional[str] = None) -> str:
    """自由层的取材表根目录（其下每个子目录是一份取材表）。"""
    return _free_dir_of(TYPE_ID, _data_dir(data_dir))


def root(data_dir: Optional[str] = None, world_state=None) -> str:
    """读取用的取材表根目录：包接管时用包内目录，否则用自由层。"""
    if (world_state is not None and world_state.active
            and world_state.owns(TYPE_ID)):
        pack_root = world_state.pack_path(TYPE_ID)
        if pack_root:
            return pack_root
    return free_root(data_dir)


def group_dir(group: Optional[str] = None, data_dir: Optional[str] = None,
              world_state=None) -> str:
    """组的读取目录（可能不存在）。"""
    return os.path.join(root(data_dir, world_state),
                        group or DEFAULT_MATERIAL_TABLE)


def member_path(group: Optional[str], member: str,
                data_dir: Optional[str] = None, world_state=None) -> str:
    """成员文件的读取路径，按「包内 → 自由组 → 内置组」顺序回落。"""
    group = group or DEFAULT_MATERIAL_TABLE
    spec = spec_of(TYPE_ID)
    declared = spec.member(member) if spec is not None else None
    filename = declared.file_name() if declared is not None else f"{member}.csv"

    candidates = []
    if (world_state is not None and world_state.active
            and world_state.owns(TYPE_ID)):
        pack_root = world_state.pack_path(TYPE_ID)
        if pack_root:
            candidates.append(os.path.join(pack_root, group, filename))
    free = os.path.join(free_root(data_dir), group, filename)
    candidates.append(free)
    if group != DEFAULT_MATERIAL_TABLE:
        candidates.append(os.path.join(free_root(data_dir),
                                       DEFAULT_MATERIAL_TABLE, filename))
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    # 一个都没有：返回自由层目标路径，读取侧按缺表处理（不抛异常）
    return free


def present_members(group: Optional[str] = None,
                    data_dir: Optional[str] = None,
                    world_state=None) -> List[str]:
    """组内实际存在的成员键（不含回落部分），供界面标注缺省成员。"""
    directory = group_dir(group, data_dir, world_state)
    present = []
    for member in MATERIAL_MEMBERS:
        if os.path.isfile(os.path.join(directory, member.file_name())):
            present.append(member.key)
    return present


def list_groups(data_dir: Optional[str] = None,
                world_state=None) -> List[str]:
    """可用的取材表组名（包内 + 自由层去重排序）。"""
    found = set()
    roots = [root(data_dir, world_state), free_root(data_dir)]
    for base in roots:
        try:
            entries = os.listdir(base)
        except OSError:
            continue
        for name in entries:
            if name.startswith(".") or name == "__pycache__":
                continue
            if os.path.isdir(os.path.join(base, name)):
                found.add(name)
    return sorted(found)


__all__ = [
    "TYPE_ID",
    "DEFAULT_MATERIAL_TABLE",
    "MEMBER_KEYS",
    "member_label",
    "free_root",
    "root",
    "group_dir",
    "member_path",
    "present_members",
    "list_groups",
]
