import json
import os


class SettingsRepo:
    def __init__(self, data_dir: str = "data", world_state=None):
        self._data_dir = data_dir
        self.world_state = world_state
        self._file = os.path.join(self._data_dir, "user", "settings.json")
        self._ai_keys_repo = AiKeysRepo(data_dir)

    @property
    def _defaults(self) -> dict:
        return {
            "comparison_count": 5,
            "comparison_order": "match",
            "world_setting": "appear",
            "name_table": "default",
            "news_table": "default",
            "seed": 0,
            "selected_styles": ["ChineseMix"],
            "selected_quip_styles": ["Events"],
            "theme_mode": "Light",
            "color_theme": "blue",
            "ai_provider": "zhipu",
            "ai_configs": {
                "zhipu": {"name": "智谱AI", "url": "https://open.bigmodel.cn/api/paas/v4/", "model": "glm-4.7-flash", "api_key": ""},
                "deepseek": {"name": "DeepSeek", "url": "https://api.deepseek.com", "model": "deepseek-chat", "api_key": ""},
                "openai": {"name": "ChatGPT", "url": "https://api.openai.com/v1", "model": "gpt-4o", "api_key": ""}
            },
            "show_casualties": True,
            "auto_save_report": False,
            "auto_save_replay": False,
            "save_low_resolution_image": False,
            "use_preview_image_as_avatar": False,
            "blocked_words": []
        }

    def load(self) -> dict:
        if not os.path.exists(self._file):
            return dict(self._defaults)
        try:
            with open(self._file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for k, v in self._defaults.items():
                if k not in data:
                    data[k] = v
            # Load ai_configs from api_keys.json
            data["ai_configs"] = self._ai_keys_repo.load() or self._defaults.get("ai_configs", {})
            return data
        except Exception:
            return dict(self._defaults)

    def save(self, settings: dict):
        data = dict(settings)
        if self.world_state is not None:
            locked = self.world_state.locked_keys()
            if locked:
                base = {}
                if os.path.exists(self._file):
                    try:
                        with open(self._file, 'r', encoding='utf-8') as f:
                            base = json.load(f)
                    except Exception:
                        base = {}
                for key in locked:
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
