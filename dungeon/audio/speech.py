"""Solea/Bulla 对话的合成语音（纯领域模块：不碰任何 UI 框架）。

**读什么**由窗口层决定：显示单元定稿时若带说话人（Solea/Bulla 对话分支的
``@说话人@`` 标记，解析见 ``dungeon/splitter.py``）就把台词交给
:meth:`SpeechDirector.speak`；叙述句没有说话人，不念。

**谁来念**由 :class:`VoiceCaster` 决定：

- 她（巨大娘，按本局的名字/昵称认）→ 专有音色；
- 主角（方案 ``protagonist_title``，留空回退「主角」）→ 专有音色；
- 其余角色 → 一组备用音色按**名字稳定映射**：同一个配角每局、每次都是同一把
  嗓子。映射用 ``zlib.crc32`` 而不是内置 ``hash()``——后者对字符串是随机化的，
  重启进程就会全体换嗓子。

合成走 edge-tts（微软神经网络语音，**需要联网**）。它是可选依赖：没装或网络
失败只在日志里留一条，**不出声不该拖垮一局副本**——与背景音乐
（``dungeon/audio`` 包）的降级策略一致；播放同样复用 ``audio.open_track``
（Windows MCI / pygame / 静音占位）。

线程模型沿用 ``dungeon/audio``：唯一一条后台线程串行处理「合成 → 播放」，
``speak()`` 只入队就返回；**新台词打断旧台词**（按生成号丢弃迟到的一步），
会话收尾 :meth:`SpeechDirector.shutdown` 收工。

**物理效果**：章节可以按「她 / 其他人」两档给对话句配物理声学效果（她作为巨大娘
的尺度、距离与所处空间的后果，不是语气）——见 ``dungeon/audio/voice_fx.py``。效果在
本模块的播放环节施加：有解码器就离线渲染成 WAV（音量烘进样本），没有就走零依赖的
播放侧近似（延迟分拍轨 + 音量包络），再没有就退回原声。
"""

import os
import queue
import re
import shutil
import tempfile
import threading
import zlib

from dungeon.audio import buses
from dungeon.audio import voice_fx
from dungeon.audio import open_track

# ---------------- 默认值与推荐音色 ----------------

#: 她的默认音色（女声·温暖）
DEFAULT_VOICE_HER = "zh-CN-XiaoxiaoNeural"
#: 主角的默认音色（男声·阳光）；与她区分开，对话才有对答感
DEFAULT_VOICE_PROTAGONIST = "zh-CN-YunxiNeural"
#: 其余角色的默认备用音色池（按名字稳定分配）
DEFAULT_OTHER_VOICES = ("zh-CN-YunjianNeural", "zh-CN-YunyangNeural",
                        "zh-CN-XiaoyiNeural")
#: 语速（edge-tts 的 ``rate``：``+0%`` 为常速）
DEFAULT_RATE = "+0%"
#: 默认音量**档位**（0-4，见 ``dungeon.audio.buses.STEP_LABELS``）：缺省「中」。
#: 这是**缺省值**，方案里显式填的 ``level`` 优先。
DEFAULT_VOICE_LEVEL = buses.DEFAULT_STEP
#: 音量档位上限
MAX_VOICE_LEVEL = buses.STEP_MAX
#: 单句上限：超长台词不合成（又慢又容易失败，念出来也没人听）
MAX_SPEECH_CHARS = 300

#: 编辑器下拉里的推荐中文音色 ``(显示名, voice id)``
RECOMMENDED_VOICES = (
    ("晓晓（女·温暖）", "zh-CN-XiaoxiaoNeural"),
    ("晓伊（女·活泼）", "zh-CN-XiaoyiNeural"),
    ("云希（男·阳光）", "zh-CN-YunxiNeural"),
    ("云健（男·激昂）", "zh-CN-YunjianNeural"),
    ("云扬（男·沉稳）", "zh-CN-YunyangNeural"),
    ("云夏（男·稚嫩）", "zh-CN-YunxiaNeural"),
    ("晓北（女·辽宁）", "zh-CN-liaoning-XiaobeiNeural"),
    ("晓妮（女·陕西）", "zh-CN-shaanxi-XiaoniNeural"),
    ("曉臻（女·台湾）", "zh-TW-HsiaoChenNeural"),
    ("雲哲（男·台湾）", "zh-TW-YunJheNeural"),
    ("曉佳（女·粤语）", "zh-HK-HiuGaaiNeural"),
    ("雲龍（男·粤语）", "zh-HK-WanLungNeural"),
)

#: 语速写法：``+0%`` / ``-20%`` / ``+15%``
_RATE_RE = re.compile(r"^[+-]?\d{1,3}%$")


def edge_tts_available() -> bool:
    """当前环境装没装 edge-tts（编辑器提示与自检用）。"""
    try:
        import edge_tts  # noqa: F401
    except Exception:
        return False
    return True


def normalize_rate(raw) -> str:
    """语速归一为 edge-tts 认的写法；非法值回退常速。"""
    text = str(raw or "").strip()
    if not _RATE_RE.match(text):
        return DEFAULT_RATE
    return text


def voice_level(raw) -> int:
    """取朗读音量档位：优先新键 ``level``，回退旧 0-100 ``volume``（按响度就近折算）。"""
    raw = raw if isinstance(raw, dict) else {}
    if "level" in raw:
        return buses.clamp_step(raw.get("level"), DEFAULT_VOICE_LEVEL)
    if "volume" in raw:
        return buses.step_from_level(raw.get("volume"), DEFAULT_VOICE_LEVEL)
    return DEFAULT_VOICE_LEVEL


def normalize_voice(raw) -> dict:
    """把方案里的对话语音配置补全为完整字典。

    其它角色音色是**列表**（按顺序给不同配角，同一名字固定一把嗓子）；
    空列表回退默认池。``enabled`` 缺省为 True——功能默认开，作者在编辑器里关。
    ``level`` 是音量**档位**（0-4）；仍接受早期的 0-100 ``volume``，但产出只写
    ``level``。
    """
    raw = raw if isinstance(raw, dict) else {}
    others = raw.get("voice_other")
    if not isinstance(others, (list, tuple)):
        others = []
    others = [str(item).strip() for item in others if str(item or "").strip()]
    return {
        "enabled": bool(raw.get("enabled", True)),
        "voice_her": str(raw.get("voice_her") or "").strip() or DEFAULT_VOICE_HER,
        "voice_protagonist": (str(raw.get("voice_protagonist") or "").strip()
                              or DEFAULT_VOICE_PROTAGONIST),
        "voice_other": tuple(others) or DEFAULT_OTHER_VOICES,
        "rate": normalize_rate(raw.get("rate")),
        "level": voice_level(raw),
    }


# ---------------- 说话人 → 音色 ----------------

def _clean_name(value) -> str:
    return str(value or "").strip().strip("「」『』“”‘’\"'")


class VoiceCaster:
    """把说话人名字映射到一个音色。

    ``her_names`` / ``protagonist_names`` 都是**集合语义**的可迭代对象
    （她可能同时有名字与昵称；主角称呼也可能有多个写法）。
    """

    ROLE_HER = "her"
    ROLE_PROTAGONIST = "protagonist"
    ROLE_OTHER = "other"

    def __init__(self, her_names=(), protagonist_names=(),
                 voice_her=DEFAULT_VOICE_HER,
                 voice_protagonist=DEFAULT_VOICE_PROTAGONIST,
                 other_voices=DEFAULT_OTHER_VOICES):
        self._her = {_clean_name(n) for n in her_names or () if _clean_name(n)}
        self._protagonist = {_clean_name(n) for n in protagonist_names or ()
                             if _clean_name(n)}
        self.voice_her = voice_her or DEFAULT_VOICE_HER
        self.voice_protagonist = voice_protagonist or DEFAULT_VOICE_PROTAGONIST
        self.other_voices = tuple(other_voices) or DEFAULT_OTHER_VOICES

    def role_of(self, speaker) -> str:
        name = _clean_name(speaker)
        if name and name in self._her:
            return self.ROLE_HER
        if name and name in self._protagonist:
            return self.ROLE_PROTAGONIST
        return self.ROLE_OTHER

    def voice_for(self, speaker) -> str:
        role = self.role_of(speaker)
        if role == self.ROLE_HER:
            return self.voice_her
        if role == self.ROLE_PROTAGONIST:
            return self.voice_protagonist
        # 同一个配角固定一把嗓子：crc32 而不是 hash()（后者对字符串随机化，
        # 重启进程就会全体换嗓子）
        pool = self.other_voices
        index = zlib.crc32(_clean_name(speaker).encode("utf-8")) % len(pool)
        return pool[index]


# ---------------- 合成 ----------------

#: 代理的读取顺序（都不设就是直连）。前两个是本项目专用键，后两个是通用约定——
#: 用户在系统里挂过代理，往往就期望所有联网功能都跟着走。
_PROXY_ENV_KEYS = ("DUNGEON_TTS_PROXY", "EDGE_TTS_PROXY", "HTTPS_PROXY", "HTTP_PROXY")

#: edge-tts 只认 http/https 代理：底层 aiohttp 不装 ``aiohttp-socks`` 走不了 SOCKS，
#: 放行只会换来一个看不懂的连接错误，不如在 :func:`normalize_proxy` 就拦下来说清楚。
_SUPPORTED_PROXY_SCHEMES = ("http", "https")
_SOCKS_SCHEMES = ("socks", "socks4", "socks4a", "socks5", "socks5h")

#: 旧合成主机。2026-10 起微软逐步对它回 403，全球分批铺开（edge-tts issue #490）。
_LEGACY_WSS_HOST = "speech.platform.bing.com"

#: 新合成主机：微软 Edge 155 走这里，**沿用同一个旧令牌**，只是改挂
#: ``Ocp-Apim-Subscription-Key``。上游修法见 edge-tts PR #491（7.2.8 尚未并入），
#: 所以在本地补上——免得用户手上那版 edge-tts 一出新就集体哑掉。
_V2_WSS_URL = ("wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1"
               "?Ocp-Apim-Subscription-Key={token}")

#: 显式设置的代理（窗口层按 ``settings["voice_proxy"]`` 填进来）；空 = 交还环境变量
_proxy = ""

#: 上次成功的合成端点，下次优先复用（否则每次都要先撞一次已经不通的那个）。
_working_wss_url = ""
#: 串行化「换端点 → 合成」这一段：``WSS_URL`` 是 edge-tts 的模块级全局，
#: 并发改写会串台。合成本身也是串行的，代价可忽略。
_endpoint_lock = threading.Lock()
#: 最近一次合成失败的**可读原因**（成功或还没试过时是空串）。
#: 只作诊断用：副本的朗读线程与编辑器的预听线程可能并发写它，取到的可能是
#: 「另一条路上刚发生的那次失败」——可读性够用，不追求严格归属。
_last_synth_error = ""


def set_proxy(proxy=None) -> None:
    """显式设置合成代理（``""`` / ``None`` = 交还给环境变量）。"""
    global _proxy
    _proxy = str(proxy or "").strip()


def normalize_proxy(proxy) -> str:
    """把用户填的代理整理成 edge-tts 真能用的形式。

    - 空串 = 直连；
    - 缺 scheme 就补 ``http://``——``127.0.0.1:7890`` 直接丢给 aiohttp 只会抛
      ``InvalidURL``，用户等于白填；
    - SOCKS 一律拒绝（见 ``_SOCKS_SCHEMES``）：aiohttp 不装 ``aiohttp-socks``
      根本走不了 SOCKS。

    不合法时抛 :class:`ValueError`，文案就是用户可见的原因。
    """
    text = str(proxy or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "http://" + text
    scheme = text.split("://", 1)[0].lower()
    if scheme in _SOCKS_SCHEMES:
        raise ValueError("SOCKS 代理不受支持，请改填 HTTP 代理"
                         "（代理软件通常另有一个 HTTP/mixed 端口）")
    if scheme not in _SUPPORTED_PROXY_SCHEMES:
        raise ValueError(f"不支持的代理协议「{scheme}://」，请填 http:// 或 https://")
    return text


def resolve_proxy() -> str:
    """当前生效的代理：显式设置 > 环境变量 > 空串（直连）。

    返回值已经过 :func:`normalize_proxy` 整理。**注意**：这里读到的代理会被
    *显式* 传给 ``edge_tts.Communicate(proxy=...)``——这是必需的，不是多此一举：
    aiohttp 的 ``trust_env`` 只对 ``http(s)://`` 生效，对 ``wss://`` 不认环境变量，
    所以光靠环境变量代理，WebSocket 握手仍会走直连。
    """
    raw = ""
    if _proxy:
        raw = _proxy
    else:
        for key in _PROXY_ENV_KEYS:
            value = os.environ.get(key, "").strip()
            if value:
                raw = value
                break
    return normalize_proxy(raw)


def last_synth_error() -> str:
    """最近一次合成失败的可读原因（成功或还没试过时是空串）。"""
    return _last_synth_error


def _describe_synth_error(exc) -> str:
    """把底层异常翻成一句人能读的原因（诊断用，不暴露栈）。"""
    text = str(exc or "")
    name = type(exc).__name__
    low = text.lower()
    if isinstance(exc, TypeError) and "proxy" in low:
        return "当前 edge-tts 版本不认识代理参数，请升级（pip install -U edge-tts）"
    if "InvalidURL" in name or "invalid url" in low:
        return "代理地址格式不对——请写成 http://主机:端口（如 http://127.0.0.1:7890）"
    if "ProxyConnection" in name:
        return "连不上代理——地址或端口不对，或代理软件没在运行"
    if "socks" in low:
        return "SOCKS 代理不受支持，请改填 HTTP 代理（代理软件通常另有 HTTP/mixed 端口）"
    if "403" in text:
        return ("服务端拒绝连接（HTTP 403）——微软已把朗读合成迁到新主机，旧版 edge-tts"
                "会普遍 403（上游 issue #490）；本程序已自动切到新主机，若仍 403，"
                "请更新 edge-tts 或换网络/代理")
    if "401" in text:
        return "服务端拒绝连接（HTTP 401）——令牌或音色名可能不对"
    if "SkewAdjustment" in name:
        return "服务端时间校验未通过——请校准系统时间后重试"
    if "timeout" in low or name == "TimeoutError":
        return "连接超时——检查网络，或给「语音合成代理」填一个可用代理"
    return f"网络或服务异常（{name}）"


def _endpoint_candidates(edge_tts) -> list:
    """按优先级给出要尝试的合成端点（首个为优先项）。

    只在已装版本**仍指向旧主机**时才补上新主机——上游哪天把 PR #491 并进发行版，
    这里就自动让位，不去覆盖人家的正确值。
    """
    current = ""
    try:
        current = str(getattr(edge_tts.communicate, "WSS_URL", "") or "")
    except Exception:
        current = ""
    token = ""
    try:
        token = str(getattr(edge_tts.constants, "TRUSTED_CLIENT_TOKEN", "") or "")
    except Exception:
        token = ""
    candidates = []
    if _LEGACY_WSS_HOST in current and token:
        candidates.append(_V2_WSS_URL.format(token=token))
    if current:
        candidates.append(current)
    if _working_wss_url in candidates:
        candidates.remove(_working_wss_url)
        candidates.insert(0, _working_wss_url)
    return candidates


def synthesis_endpoints() -> list:
    """当前会尝试的合成端点（首个优先）；供诊断与自检使用。"""
    try:
        import edge_tts
    except Exception:
        return []
    return _endpoint_candidates(edge_tts)


def _stream_once(edge_tts, url: str, text: str, voice: str, rate: str, proxy: str):
    """在指定端点上跑一次合成，返回音频 bytes（没收到音频返回 ``b""``）。

    ``url`` 为空 = 不插手，用库自己的端点。
    """
    import asyncio

    module = edge_tts.communicate
    original = getattr(module, "WSS_URL", None)

    async def _run():
        kwargs = {"proxy": proxy} if proxy else {}
        communicate = edge_tts.Communicate(text, voice, rate=rate, **kwargs)
        chunks = []
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                chunks.append(chunk.get("data") or b"")
        return b"".join(chunks)

    try:
        if url and original is not None:
            module.WSS_URL = url
        return asyncio.run(_run())
    finally:
        if original is not None:
            module.WSS_URL = original


def _edge_synth(voice: str, rate: str, text: str, dest_dir: str):
    """用 edge-tts 合成一段语音，写到 ``dest_dir`` 并返回路径（失败返回 None）。

    失败一律静默（返回 None）：没装 edge-tts、没网、音色名写错、服务端抽风，
    都不该让一局副本进行不下去。但**原因**会记进 :func:`last_synth_error`——
    「静默降级」不等于「说不清为什么」，否则用户只会看到「网络不可达」这种
    既不准、也无从下手的提示。

    端点按 :func:`_endpoint_candidates` 依次尝试（新主机 → 旧主机），代理按
    :func:`resolve_proxy` 显式传入。
    """
    global _last_synth_error, _working_wss_url
    _last_synth_error = ""
    try:
        import edge_tts
    except Exception:
        _last_synth_error = "未安装 edge-tts（pip install edge-tts）"
        return None

    try:
        proxy = resolve_proxy()
    except ValueError as exc:
        _last_synth_error = str(exc)
        return None

    candidates = _endpoint_candidates(edge_tts) or [""]
    data = b""
    last_exc = None
    with _endpoint_lock:
        for url in candidates:
            try:
                data = _stream_once(edge_tts, url, text, voice, rate, proxy)
            except Exception as exc:
                last_exc = exc
                data = b""
            if data:
                _working_wss_url = url
                break
    if not data:
        if last_exc is not None:
            _last_synth_error = _describe_synth_error(last_exc)
        else:
            _last_synth_error = "服务端没有返回音频（音色名可能不对，或服务端异常）"
        return None
    try:
        os.makedirs(dest_dir, exist_ok=True)
        path = os.path.join(dest_dir, f"voice_{abs(zlib.crc32(data))}.mp3")
        with open(path, "wb") as handle:
            handle.write(data)
    except Exception as exc:
        _last_synth_error = f"写入临时音频失败（{type(exc).__name__}）"
        return None
    return path


def synthesize(voice, rate, text, dest_dir=None):
    """合成**一句**语音并返回音频路径（失败返回 None）。

    :class:`SpeechDirector` 之外的单句入口：编辑器预听，以及任何「合成一次就
    发出去」的场合。``dest_dir`` 省略时用系统临时目录（调用方自己清理）。

    与副本朗读共用同一套代理（:func:`resolve_proxy`）与失败原因
    （:func:`last_synth_error`）——预听看到的原因就是副本里不出声的原因。
    """
    if dest_dir is None:
        try:
            dest_dir = tempfile.mkdtemp(prefix="voice_synth_")
        except Exception:
            return None
    try:
        return _edge_synth(voice, normalize_rate(rate), str(text or ""), dest_dir)
    except Exception:
        return None


# ---------------- 播放调度 ----------------

class SpeechDirector:
    """一局副本的对话语音：合成 + 播放，一次只念一句，后一句打断前一句。

    :meth:`speak` 只入队就返回（不占帧循环）；合成在工作线程里做（联网，
    通常 0.5~2s），合成完若已被更新的台词顶掉就直接丢弃。
    """

    #: 工作线程醒来的间隔（秒）：负责回收播完的音轨
    TICK_SECONDS = 0.1

    def __init__(self, caster=None, rate=DEFAULT_RATE,
                 level=DEFAULT_VOICE_LEVEL, logger=None, synth=None,
                 enabled=True):
        self.caster = caster
        self.rate = normalize_rate(rate)
        self.level = buses.clamp_step(level, DEFAULT_VOICE_LEVEL)
        self.enabled = bool(enabled)
        #: 章节级物理效果（两档：她 / 其他人），由窗口层在进章时填
        self.effect = {}
        self._logger = logger
        #: 合成实现 ``(voice, rate, text, dest_dir) -> path|None``；自检注入替身用
        self._synth = synth or _edge_synth
        self._jobs = queue.Queue()
        self._sentinel = object()
        self._thread = None
        self._stopped = False
        self._generation = 0
        self._tmp_dir = None
        # 只由工作线程写的播放状态
        self._track = None
        self._fx = None
        self._cache = {}
        #: 效果渲染产物缓存 ``(原声路径, 效果指纹, 音量档位) -> wav 路径``
        self._render_cache = {}
        #: 最近一次实际使用的音色 / 效果 / 引擎（自检/排障用）
        self.last_voice = ""
        self.last_effect = {}
        self.last_engine = "none"
        self._notes = []
        # 「合成不可用」只提示一次：每一句都记一条会把日志刷满
        self._edge_warned = False
        #: 已经提示过「这一档缺解码器」的预设（同样只提示一次）
        self._fx_warned = set()

    # ---------------- 对外 API（线程安全，立即返回） ----------------
    def configure(self, caster=None, rate=None, level=None, enabled=None,
                  effect=None):
        """会话初始化后填入方案配置（探索模式要等方案选定才有）。"""
        if caster is not None:
            self.caster = caster
        if rate is not None:
            self.rate = normalize_rate(rate)
        if level is not None:
            self.level = buses.clamp_step(level, DEFAULT_VOICE_LEVEL)
        if enabled is not None:
            self.enabled = bool(enabled)
        if effect is not None:
            self.set_effect(effect)

    def set_effect(self, spec=None) -> None:
        """换章时切到这一章的物理效果（``{}`` = 不加效果）。

        归一化在 :mod:`dungeon.audio.voice_fx` 里做：未知预设、配错档、越界参数都按
        「不加效果 / 夹取」处理。播放线程只读 ``self.effect``（赋值是原子的），
        已经在放的那一句不受影响——新台词自然按新效果走。
        """
        self.effect = voice_fx.normalize_voice_fx(spec)

    def speak(self, speaker, text) -> bool:
        """念一句台词；返回是否真的排上了队。"""
        if not self.enabled or self._stopped:
            return False
        body = str(text or "").strip()
        if not body or not _clean_name(speaker):
            return False    # 叙述句/无说话人：不念
        if len(body) > MAX_SPEECH_CHARS:
            self._note(f"[Voice] 台词过长（{len(body)} 字），跳过朗读")
            return False
        self._generation += 1
        generation = self._generation
        self._ensure_thread()
        self._jobs.put(("speak", generation, _clean_name(speaker), body))
        return True

    def stop(self) -> None:
        """立刻静音（丢弃排队中的台词，已在放的也停）。"""
        self._generation += 1
        self._dispatch_now(("stop", 0, "", ""))

    def shutdown(self, timeout: float = 1.5) -> None:
        """会话收尾：停播、收线程、清掉合成的临时音频。"""
        self._stopped = True
        self._generation += 1
        thread = self._thread
        if thread is None:
            self._cleanup()
            return
        self._jobs.put(self._sentinel)
        try:
            thread.join(timeout=timeout)
        except Exception:
            pass
        if thread.is_alive():
            self._note(f"[Voice] 语音线程未在 {timeout}s 内退出，直接关闭音频设备")
        self._thread = None
        self._cleanup()

    def wait_idle(self, timeout: float = 4.0) -> bool:
        """等到此前排队的台词都处理完（自检用，返回是否等到）。"""
        if self._thread is None or self._stopped:
            return True
        marker = threading.Event()
        self._jobs.put(("marker", 0, "", marker))
        return marker.wait(timeout)

    @property
    def worker_alive(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def speaking(self) -> bool:
        return self._track is not None

    @property
    def notes(self) -> list:
        return list(self._notes)

    # ---------------- 内部：调度 ----------------
    def _dispatch_now(self, item):
        thread = self._ensure_thread()
        if thread is not None:
            self._jobs.put(item)

    def _ensure_thread(self):
        thread = self._thread
        if thread is not None and thread.is_alive():
            return thread
        try:
            thread = threading.Thread(target=self._loop, name="dungeon-voice",
                                      daemon=True)
        except Exception:
            return None
        self._thread = thread
        thread.start()
        return thread

    def _loop(self):
        while True:
            try:
                item = self._jobs.get(timeout=self.TICK_SECONDS)
            except queue.Empty:
                self._reap_track()
                continue
            if item is self._sentinel:
                return
            kind = item[0]
            if kind == "marker":
                item[3].set()
                continue
            if kind == "stop":
                self._close_track()
                continue
            _kind, generation, speaker, body = item
            if generation != self._generation or self._stopped:
                continue        # 被后来的台词顶掉：不念了
            try:
                self._speak_job(generation, speaker, body)
            except Exception as exc:
                self._note(f"[Voice] 朗读任务异常: {exc}")

    def _speak_job(self, generation, speaker, body):
        caster = self.caster
        voice = caster.voice_for(speaker) if caster is not None else DEFAULT_VOICE_HER
        # 两档分法：她本人走「她」那一档（她在场景里的物理尺度与其他人不是一回事）；
        # 没有音色映射器时按默认音色（她的）处理，判断与音色选择保持一致。
        is_her = (caster is None
                  or caster.role_of(speaker) == getattr(caster, "ROLE_HER", "her"))
        spec = voice_fx.effect_for(
            self.effect, voice_fx.SLOT_HER if is_her else voice_fx.SLOT_OTHERS)
        path = self._synthesize(voice, body)
        # 合成是联网的慢活：这期间可能已经翻页了，那就别念了
        if generation != self._generation or self._stopped or not path:
            return
        self.last_voice = voice
        self.last_effect = dict(spec)
        self._play(path, spec, generation)

    def _synthesize(self, voice, body):
        key = (voice, self.rate, body)
        cached = self._cache.get(key)
        if cached and os.path.exists(cached):
            return cached
        if self._tmp_dir is None:
            try:
                self._tmp_dir = tempfile.mkdtemp(prefix="dungeon_voice_")
            except Exception:
                return None
        path = None
        try:
            path = self._synth(voice, self.rate, body, self._tmp_dir)
        except Exception as exc:
            self._note(f"[Voice] 语音合成失败: {exc}")
        if not path:
            if not self._edge_warned:
                self._edge_warned = True
                reason = last_synth_error() or "合成未返回音频"
                self._note(f"[Voice] 语音合成不可用：{reason}——本局对话不出声")
            return None
        self._cache[key] = path
        return path

    def _play(self, path, spec=None, generation=0):
        """播放一句（含章节配的物理效果）。

        优先级：**离线渲染**（有解码器 + numpy，采样级精确、跨平台一致）→
        **播放侧近似**（零依赖：延迟分拍轨 + 音量包络）→ **原声**。效果绝不叠加：
        渲染成功就不再开分拍轨。任何一步失败都只是"少点效果"，不拖垮朗读。

        响度：渲染路线的响度**烘进样本**（``voice_fx`` 按预设声明值对齐），所以
        播它时音量恒为 1.0；播放侧近似抬不起响度，只能按
        :func:`voice_fx.playback_trim` 做**衰减**——两条例子的相对摆幅因此一致，
        绝对锚点差一个常数（见 ``docs/Dungeon/script.md``）。
        """
        self._close_track()
        # 音量走总线：内容档位（方案 voice.level）→ 最终线性增益。
        gain = buses.resolve_gain(buses.BUS_VOICE, step=self.level)
        if spec:
            if voice_fx.has_effect(spec):
                rendered = self._render(path, spec)
                if rendered and self._start(rendered, 1.0) is not None:
                    self.last_engine = "render"
                    return
            # 播放侧这条路走衰减版的音量（渲染路线的响度已烘进样本）
            gain *= voice_fx.playback_trim(spec)
            plan = voice_fx.playback_plan(spec)
            if plan is None:
                # 只有渲染能做的档（近距震耳这类纯过载）：退回原声并提示一次
                self._warn_fx_unavailable(spec)
                self.last_engine = "none"
                self._start(path, gain)
                return
            track = self._start(path, gain)
            if track is None:
                return
            self.last_engine = "playback"
            effect = voice_fx.PlaybackFx(plan, path, gain, logger=self._note)
            self._fx = effect
            try:
                effect.run(track, lambda: generation != self._generation
                           or self._stopped)
            except Exception as exc:
                self._note(f"[Voice] 播放效果异常：{exc}")
            self._close_track()
            return
        self.last_engine = "none"
        self._start(path, gain)

    def _start(self, path, volume):
        """开一路音轨并起播，成功返回音轨；失败返回 None（只记一条日志）。"""
        track = open_track(path)
        if track is None:
            self._note("[Voice] 无法播放合成语音（没有可用的音频后端）")
            return None
        if not track.start(loop=False, volume=volume):
            try:
                track.close()
            except Exception:
                pass
            self._note("[Voice] 合成语音未能起播")
            return None
        self._track = track
        return track

    def _render(self, path, spec):
        """按效果渲染一份 WAV（结果进缓存）；失败返回 None（调用方退回近似）。"""
        key = (path, voice_fx.spec_key(spec), self.level)
        cached = self._render_cache.get(key)
        if cached and os.path.exists(cached):
            return cached
        if self._tmp_dir is None:
            try:
                self._tmp_dir = tempfile.mkdtemp(prefix="dungeon_voice_")
            except Exception:
                return None
        rendered = voice_fx.render_wav(path, self._tmp_dir, spec, self.level,
                                       logger=self._note)
        if rendered:
            self._render_cache[key] = rendered
        return rendered

    def _warn_fx_unavailable(self, spec):
        """「这一档只有渲染能做，但本机没条件」只提示一次（每预设一条）。"""
        key = str(spec.get("preset") or "")
        if key in self._fx_warned:
            return
        self._fx_warned.add(key)
        reason = ("未安装音频解码器（miniaudio / soundfile）"
                  if not voice_fx.decoder_name() else "缺少 numpy")
        self._note(f"[VoiceFx] 「{voice_fx.label_of(key)}」需要离线渲染，{reason}，"
                   "这一档退回原声")

    def _reap_track(self):
        """空闲时回收播完的音轨（不回收会一直占着设备句柄）。"""
        track = self._track
        if track is None:
            return
        try:
            if track.finished():
                self._close_track()
        except Exception:
            self._close_track()

    def _close_track(self):
        # 分拍轨与主轨一起收：新台词进来时上一句的回声尾巴必须立刻断掉
        effect = self._fx
        self._fx = None
        if effect is not None:
            try:
                effect.close()
            except Exception:
                pass
        track = self._track
        self._track = None
        if track is None:
            return
        try:
            track.close()
        except Exception:
            pass

    def _cleanup(self):
        self._close_track()
        self._cache.clear()
        self._render_cache.clear()
        tmp_dir = self._tmp_dir
        self._tmp_dir = None
        if tmp_dir:
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass

    def _note(self, message: str):
        self._notes.append(message)
        logger = self._logger
        if logger is None:
            return
        try:
            logger(message)
        except Exception:
            pass
