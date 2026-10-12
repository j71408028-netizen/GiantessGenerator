"""联网请求的代理设置：语音合成（edge-tts）与 AI 文本生成共用同一份。

**为什么单独成模块**：两处都需要「显式设置 > 环境变量 > 直连」这一条口径，也都
需要把用户随手填的 ``127.0.0.1:7890`` 整理成库能吃的 ``http://127.0.0.1:7890``。
与其各写一份、各错一处，不如把规则收在这里，两处都只是调用方。

**层次**：``core`` 只依赖 ``infra``，本模块零第三方依赖（只用 ``os``）——这样
``dungeon``（语音）与 ``core``（AI）都能安全引用，不制造反向依赖。

**两个坑，两处都一样**（教训来自 2026-10 的语音 403 排查）：

- *环境变量代理对 WebSocket 不生效*：aiohttp 的 ``trust_env`` 只认 ``http(s)://``，
  对 ``wss://`` 不认环境变量；所以语音侧必须把解析结果**显式**传给库，光靠环境变量
  会静默走直连。AI 侧（httpx）虽认环境变量，但为口径统一也走同一条 ``resolve``。
- *VPN「系统代理」(WinINET) Python 看不见*：那种设置不会进环境变量，必须由用户在
  设置里显式填。
"""

import os

#: 通用代理环境变量：都设了就按顺序取第一个非空。故意不含任何功能专有键——专有键
#: 由调用方按 ``extra_env`` 补（如语音的 ``DUNGEON_TTS_PROXY``），免得 AI 跟着语音
#: 的专用键走。
ENV_KEYS = ("DUNGEON_NET_PROXY", "HTTPS_PROXY", "HTTP_PROXY")

#: 只认 http/https：edge-tts 底层的 aiohttp 与 AI 侧的 httpx 都没装 SOCKS 支持，
#: 放行只会换来一个看不懂的连接错误，不如在 :func:`normalize_proxy` 就拦下来说清楚。
SUPPORTED_SCHEMES = ("http", "https")
SOCKS_SCHEMES = ("socks", "socks4", "socks4a", "socks5", "socks5h")

#: 显式设置的代理（窗口层按 ``settings["net_proxy"]`` 填进来）；空 = 交还环境变量
_override = ""


def set_override(proxy=None) -> None:
    """显式设置代理（``""`` / ``None`` = 交还给环境变量）。"""
    global _override
    _override = str(proxy or "").strip()


def normalize_proxy(proxy) -> str:
    """把用户填的代理整理成库真能用的形式。

    - 空串 = 直连；
    - 缺 scheme 就补 ``http://``——``127.0.0.1:7890`` 直接丢给 aiohttp 只会抛
      ``InvalidURL``，用户等于白填；
    - SOCKS / 未知协议一律拒绝。

    不合法时抛 :class:`ValueError`，文案就是用户可见的原因。
    """
    text = str(proxy or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "http://" + text
    scheme = text.split("://", 1)[0].lower()
    if scheme in SOCKS_SCHEMES:
        raise ValueError("SOCKS 代理不受支持，请改填 HTTP 代理"
                         "（代理软件通常另有一个 HTTP/mixed 端口）")
    if scheme not in SUPPORTED_SCHEMES:
        raise ValueError(f"不支持的代理协议「{scheme}://」，请填 http:// 或 https://")
    return text


def resolve(extra_env=()) -> str:
    """当前生效的代理：显式设置 > ``extra_env`` > :data:`ENV_KEYS` > 空串（直连）。

    返回值已经过 :func:`normalize_proxy` 整理；不合法时抛 :class:`ValueError`。
    """
    raw = _override
    if not raw:
        for key in tuple(extra_env) + ENV_KEYS:
            value = os.environ.get(key, "").strip()
            if value:
                raw = value
                break
    return normalize_proxy(raw)
