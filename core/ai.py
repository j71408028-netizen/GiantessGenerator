"""OpenAI-compatible AI client and profile resolution."""

from typing import Dict, Optional

from core import netproxy
from paths import data_dir


#: 单次请求超时（秒）。请求体可能很长（整段历史 + 提示词），给宽一点。
TIMEOUT_SECONDS = 120.0


# These are starter profiles, not provider-specific client implementations.
PROVIDER_DEFAULTS: Dict[str, dict] = {
    "zhipu": {"name": "智谱AI", "url": "https://open.bigmodel.cn/api/paas/v4/", "model": "glm-4.7-flash"},
    "deepseek": {"name": "DeepSeek", "url": "https://api.deepseek.com", "model": "deepseek-v4-flash"},
    "openai": {"name": "ChatGPT", "url": "https://api.openai.com/v1", "model": "gpt-4o"},
}
PROVIDER_NAMES = {key: value["name"] for key, value in PROVIDER_DEFAULTS.items()}


def provider_defaults(provider: str) -> dict:
    return dict(PROVIDER_DEFAULTS.get(provider or "", {}))


def describe_ai_error(exc) -> str:
    """把 AI 请求异常翻成一句人能读的原因（诊断用，不暴露栈）。

    与语音侧的 ``dungeon/audio/speech.py::_describe_synth_error`` 同口径：**看
    HTTP 状态码**（各家 SDK 异常都带 ``status_code``）与异常名/文本，而不是死认
    某个具体的 SDK 类型——这样换后端、换 SDK 版本都不至于失准。原先这些异常被
    ``str(e)`` 原样透出（形如 ``Connection error.`` 或 ``Error code: 401 - ...``），
    玩家既看不懂也不知道该改什么。
    """
    name = type(exc).__name__
    text = str(exc or "")

    # 异常链：openai 会把底层 httpx 错误包成 ``APIConnectionError``，代理/DNS 等
    # 关键词常挂在 ``__cause__`` 上，只看最外层会漏掉。
    parts = []
    node, depth = exc, 0
    while node is not None and depth < 5:
        parts.append(f"{type(node).__name__}: {node}")
        node = node.__cause__ or node.__context__
        depth += 1
    low = " ".join(parts).lower()

    # 代理本身的问题（文案与 core.netproxy 一致）
    if isinstance(exc, ValueError) and "代理" in text:
        return text
    if isinstance(exc, ValueError):
        # 我们自己抛的配置类错误（如「未配置模型名称」）：原文就是最好的文案
        return text or "配置有误"
    if isinstance(exc, ImportError) and "openai" in low:
        return "未安装 openai 依赖（pip install \"openai>=2.0\"）"
    if "InvalidURL" in name or "invalid url" in low:
        return "代理地址格式不对——请写成 http://主机:端口（如 http://127.0.0.1:7890）"
    if "ProxyConnection" in name or ("proxy" in low and ("connect" in low or "refused" in low)):
        return "连不上代理——地址或端口不对，或代理软件没在运行"
    if "socks" in low:
        return "SOCKS 代理不受支持，请改填 HTTP 代理（代理软件通常另有 HTTP/mixed 端口）"

    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)

    if status == 401 or "401" in text or "unauthorized" in low \
            or "invalid api key" in low or "incorrect api key" in low \
            or "authentication" in low:
        return "API Key 无效或未授权（HTTP 401）——请检查设置里的 API Key"
    if status == 403 or "403" in text:
        return "服务端拒绝访问（HTTP 403）——Key 权限不足，或被网络/地区限制"
    if status == 404 or "404" in text or "model_not_found" in low \
            or "does not exist" in low:
        return "找不到模型或接口地址（HTTP 404）——检查「模型名称」与「接口 URL」"
    if status == 429 or "429" in text or "rate limit" in low or "quota" in low \
            or "insufficient" in low or "余额" in text:
        return "请求过于频繁或额度不足（HTTP 429）——稍等重试，或检查账户额度"
    if status == 400 or "400" in text:
        return "请求被拒绝（HTTP 400）——通常是模型名称不对或提示内容过长"
    if status and 500 <= status < 600:
        return f"服务端错误（HTTP {status}）——稍后重试"
    if "timeout" in low or "timed out" in low or name in (
            "APITimeoutError", "TimeoutError", "ReadTimeout", "ConnectTimeout"):
        return "连接超时——检查网络，或给「网络代理」填一个可用代理"
    if "connection" in low or "connect" in low or name == "APIConnectionError":
        # 「连不上」与「要么没网、要么代理挂了」在用户眼里长得一样。若当前配了
        # 代理，就把它点名——否则一个已经关掉的代理会让人以为是网络问题。
        try:
            proxy = netproxy.resolve()
        except Exception:
            proxy = ""
        if proxy:
            return (f"连不上服务器（当前经代理 {proxy}）——多半是该代理没在运行或地址"
                    f"不对；也可清空设置里的「网络代理」改用直连")
        return "连不上服务器——检查网络/接口 URL；若需代理，请在设置里填「网络代理」"
    return f"请求失败（{name}）"


class OpenAICompatibleClient:
    """Client for any endpoint implementing the OpenAI chat completions API."""

    provider = "openai"

    def __init__(self, api_key, base_url: Optional[str] = None, model: Optional[str] = None):
        from openai import OpenAI

        self.model = (model or "").strip()
        if not self.model:
            raise ValueError("未配置模型名称")
        kwargs = {
            "api_key": api_key,
            "base_url": (base_url or "").strip() or None,
            "timeout": TIMEOUT_SECONDS,
        }
        # 需要联网：与语音合成共用同一份代理（设置页「网络代理」）。只在**配了**
        # 代理时才注入 httpx 客户端——没配就不传，交给 SDK 默认（照常读环境变量）。
        # 用 openai 自己导出的 ``DefaultHttpxClient``（它就是配好默认值的 httpx.Client），
        # 免得直接 import httpx 而多一条依赖声明。
        # 注意：这份解析在构造时定格，改代理后要新建客户端才生效（与语音侧一致）。
        proxy = netproxy.resolve()
        if proxy:
            from openai import DefaultHttpxClient

            kwargs["http_client"] = DefaultHttpxClient(
                proxy=proxy, timeout=TIMEOUT_SECONDS)
        self.client = OpenAI(**kwargs)

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

    def generate_once(self, messages, temperature=0.8, max_tokens: Optional[int] = None):
        """非流式生成：一次返回完整文本（聊天等不需要打字机效果的场景）。"""
        kwargs = {"model": self.model, "messages": messages,
                  "temperature": temperature, "stream": False}
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        response = self.client.chat.completions.create(**kwargs)
        choices = getattr(response, "choices", None)
        if not choices:
            return ""
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if content:
            return content
        refusal = getattr(message, "refusal", None)
        return refusal or ""

    def test_connection(self):
        try:
            self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
            )
            return True, "连接成功"
        except Exception as e:
            return False, describe_ai_error(e)


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
        settings = {}
        # Read ai_configs from api_keys.json
        keys_path = os.path.join(data_dir(), "user", "api_keys.json")
        try:
            with open(keys_path, "r", encoding="utf-8") as f:
                settings["ai_configs"] = json.load(f)
        except Exception:
            settings["ai_configs"] = {}
        # Also try to read ai_provider from settings.json
        path = os.path.join(data_dir(), "user", "settings.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                saved = json.load(f)
                settings["ai_provider"] = saved.get("ai_provider", "")
                # 网络代理（语音与 AI 共用）：主进程若没被启动装配设过，这里补一次，
                # 保证在设置页之外调用（如聊天）也走同一份代理。
                if "net_proxy" in saved:
                    netproxy.set_override(saved.get("net_proxy"))
        except Exception:
            pass
    cfg = resolve_ai_config(settings or {})
    if not cfg["api_key"]:
        return None, None
    try:
        return create_client(cfg["provider"], cfg["api_key"], cfg["url"], cfg["model"]), cfg["name"]
    except Exception as e:
        print(f"AI 客户端初始化失败: {e}")
        return None, None
