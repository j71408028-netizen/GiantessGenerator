import json
import os

from dungeon.audio.buses import BUS_VOICE, DEFAULT_BUS_LEVELS
from dungeon.settings import LEGACY_KEYS as _DUNGEON_SETTINGS_KEYS
from persistence.dungeon_settings_repo import DungeonSettingsRepo
from persistence.resource_settings_repo import (
    RESOURCE_SETTINGS_KEYS,
    ResourceSettingsRepo,
)


class SettingsRepo:
    def __init__(self, data_dir: str = "data", world_state=None):
        self._data_dir = data_dir
        self.world_state = world_state
        self._file = os.path.join(self._data_dir, "user", "settings.json")
        self._ai_keys_repo = AiKeysRepo(data_dir)
        self._resource_repo = ResourceSettingsRepo(data_dir)

    @property
    def _defaults(self) -> dict:
        return {
            "comparison_count": 5,
            "comparison_order": "match",
            "world_setting": "appear",
            "seed": 0,
            "theme_mode": "Light",
            "color_theme": "blue",
            "theme_palette": "Slate",
            "ai_provider": "zhipu",
            "ai_configs": {
                "zhipu": {"name": "智谱AI", "url": "https://open.bigmodel.cn/api/paas/v4/", "model": "glm-4.7-flash", "api_key": ""},
                "deepseek": {"name": "DeepSeek", "url": "https://api.deepseek.com", "model": "deepseek-chat", "api_key": ""},
                "openai": {"name": "ChatGPT", "url": "https://api.openai.com/v1", "model": "gpt-4o", "api_key": ""}
            },
            "show_casualties": True,
            "auto_save_report": False,
            # 副本独占设置（audio_levels / dungeon_font / story_recent_count /
            # auto_save_replay / fps_max）不在此表：它们住 data/user/dungeon_settings.json，
            # 见 persistence/dungeon_settings_repo.py。这里只保留与主程序共用的键。
            # 资源域键（静态表选择 / 风格选择 / active_world）同理不在此表：它们住
            # data/user/resource_settings.json，见 persistence/resource_settings_repo.py。
            # 网络代理（可留空）：语音合成与 AI 文本生成**共用**，见 core/netproxy.py。
            # 留空则回退到环境变量（DUNGEON_NET_PROXY / HTTPS_PROXY / HTTP_PROXY）。
            # 旧键 ``voice_proxy`` 会在 load 时迁移到这里。
            "net_proxy": "",
            "save_low_resolution_image": False,
            "use_preview_image_as_avatar": False,
            # 世界履历展示开关：履历始终记录（只写），关闭只影响展示/导出读取
            "world_history_enabled": True,
            # 启动界面模式偏好：pro/mini 固定其一，exit=退出时模式
            # （跟随 ui_mode 记录的上次所在界面），见 services.ui_mode.resolve_startup_mode
            "ui_startup": "exit",
            "blocked_words": []
        }

    def load(self) -> dict:
        if not os.path.exists(self._file):
            data = dict(self._defaults)
            data.update(self._resource_repo.load())
            return data
        try:
            with open(self._file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            # 旧键迁移：``voice_proxy`` → ``net_proxy``（代理扩为语音与 AI 共用后改名）。
            # 只在旧键有值、新键没值时搬运，搬完删掉旧键（免得存盘时留两条）。
            if not data.get("net_proxy") and data.get("voice_proxy"):
                data["net_proxy"] = data["voice_proxy"]
            data.pop("voice_proxy", None)
            # 旧键迁移：``dungeon_voice_enabled``（已撤掉的总开关）→ 语音总线电平。
            # 关过开关的人替他拧到 0——撤掉选项后声音不该自己回来；开着的人不动。搬完删掉旧键。
            if "dungeon_voice_enabled" in data:
                if not data.get("dungeon_voice_enabled"):
                    levels = data.get("audio_levels")
                    levels = (dict(levels) if isinstance(levels, dict)
                              else dict(DEFAULT_BUS_LEVELS))
                    levels[BUS_VOICE] = 0
                    data["audio_levels"] = levels
                data.pop("dungeon_voice_enabled", None)
            # 副本独占设置搬家：搬往 data/user/dungeon_settings.json。搬完从 settings
            # 里摘掉（本 dict 不再返回它们）；目标文件已存在的键不被覆盖。
            moved = {k: data.pop(k) for k in _DUNGEON_SETTINGS_KEYS if k in data}
            if moved:
                DungeonSettingsRepo(self._data_dir).absorb_legacy(moved)
            # 资源域设置搬家：搬往 data/user/resource_settings.json，再合并回来。
            # 对外 dict 形状不变（资源键仍在返回值里），只是持久化位置换了。
            moved = {k: data.pop(k) for k in RESOURCE_SETTINGS_KEYS if k in data}
            if moved:
                self._resource_repo.absorb_legacy(moved)
            data.update(self._resource_repo.load())
            for k, v in self._defaults.items():
                if k not in data:
                    data[k] = v
            # Load ai_configs from api_keys.json
            data["ai_configs"] = self._ai_keys_repo.load() or self._defaults.get("ai_configs", {})
            return data
        except Exception:
            data = dict(self._defaults)
            data.update(self._resource_repo.load())
            return data

    def save(self, settings: dict):
        data = dict(settings)
        # 副本独占设置不该再落回 settings.json（迁移后它们的家在 dungeon_settings.json；
        # 世界包锁键的语义也不适用于它们）。防止旧调用方把整份 dict 原样写回。
        for key in _DUNGEON_SETTINGS_KEYS:
            data.pop(key, None)
        locked = set()
        if self.world_state is not None:
            locked = self.world_state.locked_keys() or set()
        # 资源域键分流到 resource_settings.json（锁键保护在新文件上由它自己执行）。
        resource = {k: data.pop(k) for k in RESOURCE_SETTINGS_KEYS if k in data}
        if resource:
            self._resource_repo.save(resource, locked_keys=locked)
        if locked - set(RESOURCE_SETTINGS_KEYS):
            base = {}
            if os.path.exists(self._file):
                try:
                    with open(self._file, 'r', encoding='utf-8') as f:
                        base = json.load(f)
                except Exception:
                    base = {}
            for key in locked - set(RESOURCE_SETTINGS_KEYS):
                if key in base:
                    data[key] = base[key]
                else:
                    data.pop(key, None)

        # Save ai_configs to api_keys.json
        ai_configs = data.pop("ai_configs", {})
        self._ai_keys_repo.save(ai_configs)

        os.makedirs(os.path.dirname(self._file), exist_ok=True)
        with open(self._file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def save_resource_keys(self, resource_values: dict) -> None:
        """只把资源域键落盘到 resource_settings.json，不触碰 settings.json。

        资源中心页的即时保存入口：一次选择不该把设置页里尚未保存的其它暂存值
        一并写盘。传入 dict 应含全部资源键（load() 合并后的共享 dict 即完整）；
        锁键保护与 :meth:`save` 相同。
        """
        data = {k: resource_values[k] for k in RESOURCE_SETTINGS_KEYS
                if k in resource_values}
        if not data:
            return
        locked = self.world_state.locked_keys() if self.world_state is not None else set()
        self._resource_repo.save(data, locked_keys=locked)


class AiKeysRepo:
    def __init__(self, data_dir: str = "data"):
        self._data_dir = data_dir
        self._file = os.path.join(self._data_dir, "user", "api_keys.json")

    def load(self) -> dict:
        if not os.path.exists(self._file):
            return {}
        try:
            with open(self._file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def save(self, configs: dict):
        os.makedirs(os.path.dirname(self._file), exist_ok=True)
        with open(self._file, 'w', encoding='utf-8') as f:
            json.dump(configs, f, indent=2, ensure_ascii=False)
