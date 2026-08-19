"""OpenAI-compatible AI client and profile resolution."""

from typing import Dict, Optional

from paths import data_dir


# These are starter profiles, not provider-specific client implementations.
PROVIDER_DEFAULTS: Dict[str, dict] = {
    "zhipu": {"name": "智谱AI", "url": "https://open.bigmodel.cn/api/paas/v4/", "model": "glm-4.7-flash"},
    "deepseek": {"name": "DeepSeek", "url": "https://api.deepseek.com", "model": "deepseek-v4-flash"},
    "openai": {"name": "ChatGPT", "url": "https://api.openai.com/v1", "model": "gpt-4o"},
}
PROVIDER_NAMES = {key: value["name"] for key, value in PROVIDER_DEFAULTS.items()}


def provider_defaults(provider: str) -> dict:
    return dict(PROVIDER_DEFAULTS.get(provider or "", {}))


class OpenAICompatibleClient:
    """Client for any endpoint implementing the OpenAI chat completions API."""

    provider = "openai"

    def __init__(self, api_key, base_url: Optional[str] = None, model: Optional[str] = None):
        from openai import OpenAI

        self.model = (model or "").strip()
        if not self.model:
            raise ValueError("未配置模型名称")
        self.client = OpenAI(
            api_key=api_key,
            base_url=(base_url or "").strip() or None,
            timeout=120.0,
        )

    def generate_stream(self, messages, temperature=0.8):
        response = self.client.chat.completions.create(
            model=self.model, messages=messages, temperature=temperature, stream=True
        )
        for chunk in response:
            choices = getattr(chunk, "choices", None)
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            if delta is None:
                continue
            content = getattr(delta, "content", None)
            if content:
                yield content
            else:
                refusal = getattr(delta, "refusal", None)
                if refusal:
                    yield refusal

    def generate(self, messages, temperature=0.8):
        return "".join(c for c in self.generate_stream(messages, temperature) if c)

    def test_connection(self):
        try:
            self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
            )
            return True, "连接成功"
        except Exception as e:
            return False, str(e)


def create_client(provider: Optional[str], api_key: str,
                  base_url: Optional[str] = None, model: Optional[str] = None):
    """Create a client for a named profile. ``provider`` is the profile id."""
    return OpenAICompatibleClient(api_key, base_url=base_url, model=model)


def resolve_ai_config(settings: dict, provider: Optional[str] = None) -> dict:
    """Resolve the selected named profile to ``provider/url/model/api_key``."""
    configs = settings.get("ai_configs") or {}
    profile_id = provider or settings.get("ai_provider")
    if not profile_id or profile_id not in configs:
        profile_id = next(iter(configs), "")
    cfg = configs.get(profile_id) or {}
    return {
        "provider": profile_id,
        "name": cfg.get("name", profile_id),
        "url": (cfg.get("url") or "").strip(),
        "model": (cfg.get("model") or "").strip(),
        "api_key": cfg.get("api_key", "") or "",
    }


def get_ai_client(settings: Optional[dict] = None):
    if settings is None:
        import json
        import os
        path = os.path.join(data_dir(), "user", "settings.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                settings = json.load(f)
        except Exception:
            settings = {}
    cfg = resolve_ai_config(settings or {})
    if not cfg["api_key"]:
        return None, None
    try:
        return create_client(cfg["provider"], cfg["api_key"], cfg["url"], cfg["model"]), cfg["name"]
    except Exception as e:
        print(f"AI 客户端初始化失败: {e}")
        return None, None
