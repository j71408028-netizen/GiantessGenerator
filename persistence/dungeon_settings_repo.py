"""副本独占设置的仓库（音量 / 段落字体 / 记忆窗口 / 自动保存回放 / 帧率上限 /
组件代管区停靠侧）。

这些键只有副本引擎会用，与主程序无关（见 ``dungeon/settings.py`` 的说明），因此
不再住 ``data/user/settings.json``，而是独立成 ``data/user/dungeon_settings.json``：

- 副本会话跑在独立子进程里，会话期主窗口隐藏——独占文件同一时刻只有一个写者，
  子进程可直接读写（共享的 settings.json 做不到，必须绕父进程协议）；
- 引擎的配置随引擎的存储走，与 ``api_keys.json`` 同先例。

旧值由 ``SettingsRepo.load`` 经 :meth:`absorb_legacy` 一次性搬过来，旧用户无感。
"""

import os

from dungeon import settings as dungeon_settings
from dungeon.audio import buses
from persistence.json_store import load_json_with_backup, write_json_atomic


class DungeonSettingsRepo:
    def __init__(self, data_dir: str = "data"):
        self._data_dir = data_dir
        self._file = os.path.join(self._data_dir, "user", "dungeon_settings.json")

    # ---------------- 读 ----------------
    def load(self) -> dict:
        """读全部副本独占设置；缺项补缺省、越界夹取、结构坏掉整体回退缺省。"""
        raw = load_json_with_backup(self._file)
        data = dungeon_settings.defaults()
        if isinstance(raw, dict):
            data.update({k: v for k, v in raw.items() if k in data})
        return self._normalize(data)

    # ---------------- 写 ----------------
    def save(self, values: dict) -> None:
        """原子写（半截设置文件不会被读到）。"""
        write_json_atomic(self._file, self._normalize(dict(values)))

    def update(self, patch: dict) -> dict:
        """把若干键合并进现有设置并落盘，返回合并后的完整设置。

        副本窗口在会话中改音量 / 帧率走这里：读—改—写一次完成，调用方不必自己
        持有整份 dict（独占文件在同一时刻只有一个写者）。
        """
        data = self.load()
        data.update(patch or {})
        data = self._normalize(data)
        self.save(data)
        return data

    def absorb_legacy(self, legacy: dict) -> dict:
        """旧键迁移：把 ``settings.json`` 里的副本设置搬进本文件（幂等）。

        只在**本文件尚无该键**时写入，因此即使 ``settings.json`` 里还残留旧值
        （用户此后不再打开设置页保存、旧键没被 save 清掉），也不会覆盖副本窗口
        后来改的现值。
        """
        raw = load_json_with_backup(self._file)
        raw = dict(raw) if isinstance(raw, dict) else {}
        data = dungeon_settings.defaults()
        data.update({k: v for k, v in raw.items() if k in data})
        changed = False
        for key, value in (legacy or {}).items():
            if key in data and key not in raw:
                data[key] = value
                changed = True
        data = self._normalize(data)
        if changed or not os.path.exists(self._file):
            self.save(data)
        return data

    # ---------------- 归一化 ----------------
    @staticmethod
    def _normalize(data: dict) -> dict:
        # 音量走总线单一真相源（夹取 0-100、结构坏掉整体回退缺省）
        data["audio_levels"] = buses.levels_from_settings(data)
        data["dungeon_font"] = str(data.get("dungeon_font") or "")
        try:
            data["story_recent_count"] = max(1, min(200, int(
                data.get("story_recent_count",
                         dungeon_settings.DEFAULT_STORY_RECENT_COUNT))))
        except (TypeError, ValueError):
            data["story_recent_count"] = dungeon_settings.DEFAULT_STORY_RECENT_COUNT
        data["auto_save_replay"] = bool(data.get("auto_save_replay", False))
        try:
            fps = float(data.get("fps_max", dungeon_settings.DEFAULT_FPS_MAX))
        except (TypeError, ValueError):
            fps = float(dungeon_settings.DEFAULT_FPS_MAX)
        data["fps_max"] = max(20.0, min(240.0, fps))
        data["dock_side"] = dungeon_settings.normalize_dock_side(
            data.get("dock_side"))
        return data


__all__ = ["DungeonSettingsRepo"]
