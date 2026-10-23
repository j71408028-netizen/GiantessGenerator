"""风格「注册地址」伴随文件（``<风格名>.addr.json``）的统一读写。

地标风格与描述风格共用同一约定：主文件旁放一个 companion 文件，内容为
``{"address": "<地址文本>"}``（空 = 未注册）。读取目录（世界包激活时为包内
目录，否则自由目录）与写入目录（始终自由目录）由调用方决定；建 / 删 /
改名风格时的伴随处理也集中在这里，两个 repo 与世界包导出共用一份实现。
"""

import os

from .json_store import load_json_with_backup, write_json_atomic

ADDR_SUFFIX = ".addr.json"


def addr_path(folder: str, style_name: str) -> str:
    return os.path.join(folder, f"{style_name}{ADDR_SUFFIX}")


def load_address(read_dir: str, style_name: str) -> str:
    """读取风格注册地址文本；缺失 / 损坏一律视为未注册（返回空串）。"""
    data = load_json_with_backup(os.path.join(read_dir, f"{style_name}{ADDR_SUFFIX}"))
    return data.get("address", "") if isinstance(data, dict) else ""


def save_address(folder: str, style_name: str, address_text: str) -> None:
    write_json_atomic(addr_path(folder, style_name), {"address": address_text or ""})


def delete_address(folder: str, style_name: str) -> None:
    path = addr_path(folder, style_name)
    if os.path.exists(path):
        os.remove(path)


def rename_address(folder: str, old_name: str, new_name: str) -> None:
    old = addr_path(folder, old_name)
    if os.path.exists(old):
        os.rename(old, addr_path(folder, new_name))
