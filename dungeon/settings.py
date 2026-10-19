"""副本独占设置的键名与缺省（引擎侧单一真相源）。

这些设置只有副本引擎会读：音频总线电平、段落字体、记忆窗口大小、自动保存回放、
帧率上限。它们从主程序的 ``settings.json`` 独立出来，住
``persistence/dungeon_settings_repo.py`` 管的 ``data/user/dungeon_settings.json``：

- 副本会话跑在独立子进程里，会话期主窗口隐藏——独占文件同一时刻只有一个写者，
  子进程可以直接读写，不必绕父进程协议（共享的 ``settings.json`` 有"父进程持有
  整份 dict + 世界包锁键"的语义，子进程直写会竞态）；
- 引擎的配置随引擎的存储走，与 ``api_keys.json`` 同先例。

本模块只放**纯数据**（键名与缺省），不碰持久化与 UI，供 ``persistence`` 与
``dungeon/window`` 两侧共用，避免缺省值两处各写一份。
"""

from dungeon.audio.buses import DEFAULT_BUS_LEVELS

#: 帧率上限缺省（Hz）。真实节拍由 vsync 决定，这只是高刷屏上的封顶。
DEFAULT_FPS_MAX = 60

#: 对话历史保留的消息条数缺省（设置里可调）。
DEFAULT_STORY_RECENT_COUNT = 20

#: 从 ``settings.json`` 迁往 ``dungeon_settings.json`` 的键（一次性迁移，见
#: ``SettingsRepo.load``）；``fps_max`` 是新键，无旧值可迁。
LEGACY_KEYS = ("audio_levels", "dungeon_font", "story_recent_count",
               "auto_save_replay")


def defaults() -> dict:
    """副本独占设置的缺省值。

    每次返回**新的** dict（``audio_levels`` 是可变值，不能共享同一份引用）。
    """
    return {
        "audio_levels": dict(DEFAULT_BUS_LEVELS),
        "dungeon_font": "",
        "story_recent_count": DEFAULT_STORY_RECENT_COUNT,
        "auto_save_replay": False,
        "fps_max": DEFAULT_FPS_MAX,
    }
