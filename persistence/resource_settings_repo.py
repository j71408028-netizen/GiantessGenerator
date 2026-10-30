"""资源设置仓库（静态表选择 / 风格选择 / 激活世界包）。

这些键属于「资源中心」域：改动即时生效、由资源中心页面直接写盘，与主设置页的
「保存并返回」节奏无关，因此独立成 ``data/user/resource_settings.json``（与
``dungeon_settings.json`` 同先例：配置随其归属域走）。

- 静态表：``name_table`` / ``news_table`` / ``preset_table`` / ``personality_table``
- 风格：``selected_styles`` / ``selected_quip_styles``
- 世界包激活态：``active_world``

旧值由 ``SettingsRepo.load`` 经 :meth:`absorb_legacy` 一次性搬过来，旧用户无感。
世界包锁定键的保护由 :meth:`save` 在本文件上执行：锁定键落盘时保持磁盘现值。
"""

import os

from persistence.json_store import load_json_with_backup, write_json_atomic
# 资源域独占的设置键（不再住 settings.json，由 SettingsRepo 在读时合并、写时分流）；
# 键集派生自类型声明，新增一类资源不必改这里。
from persistence.resources.registry import RESOURCE_SETTINGS_KEYS


# 缺省值（preset_table / personality_table / active_world 无缺省：读取方各自回退）
RESOURCE_SETTING_DEFAULTS = {
    "name_table": "default",
    "news_table": "default",
    "selected_styles": ["ChineseMix"],
    "selected_quip_styles": ["Events"],
}


class ResourceSettingsRepo:
    def __init__(self, data_dir: str = "data"):
        self._data_dir = data_dir
        self._file = os.path.join(self._data_dir, "user", "resource_settings.json")

    # ---------------- 读 ----------------
    def load(self) -> dict:
        """读全部资源设置；只保留已知键、缺项补缺省、文件坏掉整体回退缺省。"""
        raw = load_json_with_backup(self._file)
        data = ({k: v for k, v in raw.items() if k in RESOURCE_SETTINGS_KEYS}
                if isinstance(raw, dict) else {})
        for key, value in RESOURCE_SETTING_DEFAULTS.items():
            data.setdefault(key, value)
        return data

    # ---------------- 写 ----------------
    def save(self, values: dict, locked_keys=()) -> None:
        """原子写；锁定键保持磁盘现值（世界包激活期间这些键不可改）。

        传空 ``values`` 时不落盘也不清空文件——调用方传来的应是含全部资源键的
        完整 dict（``SettingsRepo.load`` 保证合并过），空 dict 视为异常调用。
        """
        data = {k: v for k, v in dict(values).items()
                if k in RESOURCE_SETTINGS_KEYS}
        if not data:
            return
        locked = set(locked_keys or ())
        if locked:
            base = load_json_with_backup(self._file)
            base = base if isinstance(base, dict) else {}
            for key in locked:
                if key in base:
                    data[key] = base[key]
                else:
                    data.pop(key, None)
        write_json_atomic(self._file, data)

    def absorb_legacy(self, legacy: dict) -> dict:
        """旧键迁移：把 ``settings.json`` 里的资源键搬进本文件（幂等）。

        只在**本文件尚无该键**时写入，不覆盖现值（与 DungeonSettingsRepo 同语义）。
        """
        raw = load_json_with_backup(self._file)
        raw = dict(raw) if isinstance(raw, dict) else {}
        changed = False
        for key, value in (legacy or {}).items():
            if key in RESOURCE_SETTINGS_KEYS and key not in raw:
                raw[key] = value
                changed = True
        if changed or not os.path.exists(self._file):
            known = {k: v for k, v in raw.items() if k in RESOURCE_SETTINGS_KEYS}
            for key, value in RESOURCE_SETTING_DEFAULTS.items():
                known.setdefault(key, value)
            write_json_atomic(self._file, known)
        return self.load()


__all__ = ["ResourceSettingsRepo", "RESOURCE_SETTINGS_KEYS",
           "RESOURCE_SETTING_DEFAULTS"]
