"""JSON / 文本文件的原子写入与备份读取。

副本配置、回放、报告与结局索引此前都用 ``open(path, 'w')`` 直接截断再写：
写到一半崩溃或断电会留下半截文件，作者辛苦编好的副本方案被写坏且无从回退。

统一改为「同目录临时文件 → fsync → 覆盖前留一份最近的 .bak → ``os.replace``」。
``os.replace`` 在 Windows / POSIX 上都是原子替换，因此任何时刻读取到的要么是
旧文件、要么是完整新文件。
"""

import json
import os
import shutil

BACKUP_SUFFIX = ".bak"
TEMP_SUFFIX = ".tmp"


def _backup_existing(path: str) -> None:
    """覆盖前把现有文件另存为 ``<name>.bak``（只保留最近一份）。"""
    try:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            shutil.copy2(path, path + BACKUP_SUFFIX)
    except OSError as e:
        print(f"[AtomicIO] 备份失败（继续写入）: {path} - {e}")


def write_text_atomic(path: str, text: str, *, backup: bool = True,
                      encoding: str = "utf-8") -> None:
    """原子写入文本文件；写入失败抛异常，由调用方决定如何提示。"""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    temp_path = path + TEMP_SUFFIX
    try:
        with open(temp_path, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError as e:
                # 少数文件系统/网络盘不支持 fsync：降级为普通写入，不阻断保存
                print(f"[AtomicIO] fsync 失败（降级写入）: {temp_path} - {e}")
    except Exception:
        # 写入中途失败：清掉半截临时文件，主文件保持原样
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise
    if backup:
        _backup_existing(path)
    os.replace(temp_path, path)


def write_json_atomic(path: str, data, *, backup: bool = True, indent: int = 2,
                      encoding: str = "utf-8") -> None:
    """原子写入 JSON 文件（``ensure_ascii=False``，与本仓库其余存档一致）。"""
    write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=indent),
                      backup=backup, encoding=encoding)


def load_json_with_backup(path: str, *, encoding: str = "utf-8"):
    """读取 JSON；主文件缺失或损坏时回退到 ``.bak``，都不可用返回 None。"""
    for candidate in (path, path + BACKUP_SUFFIX):
        if not os.path.exists(candidate):
            continue
        try:
            with open(candidate, "r", encoding=encoding) as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            print(f"[AtomicIO] 读取失败: {candidate} - {e}")
            continue
        if candidate != path:
            print(f"[AtomicIO] {path} 不可用，已回退到备份: {candidate}")
        return data
    return None
