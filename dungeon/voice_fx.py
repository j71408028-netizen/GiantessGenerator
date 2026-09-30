"""章节对话语音的「巨大娘尺度」物理声学效果（纯领域模块：不碰任何 UI 框架）。

她作为巨大娘说话时，听感上的差异主要不是**语气**，而是**声源尺度与所处空间**
的物理后果：巨大胸腔与肺量带来的低频轰鸣、离得远时空气吸收吃掉的高频、空旷地形上
折回的稀疏回声、庞大身躯搅动空气造成的缓抖……

配置挂在**章节**上（与 ``bgm`` 同级，见 ``dungeon/chapters.normalize_chapter``），
分「她 / 其他人」两档：同一个物理环境里，巨大声源与普通尺度声源该有不同的效果。
**这是环境与尺度的物理量，不是情绪**——所以预设名全是描述物理的（巨躯轰鸣、
旷野回声、高空遥语、震空低鸣、近距震耳），不含语气类预设。

两级引擎（同一套预设参数，按本机能力择优，**互不叠加**）：

- **播放侧（零依赖）**：延迟分拍轨 = 稀疏回声、音量包络 = 缓抖。拿现成的合成
  MP3 直接放，不需要解码器；Windows MCI 实测能同时开多路同一文件、起播延迟在
  毫秒级，稀疏拍点足够像「声音撞上山体折回」。
- **离线渲染（可选解码依赖）**：解码成 PCM（``miniaudio`` 或 ``soundfile`` 任装
  一个）+ ``numpy`` 做空气吸收（低通）、躯体共振（低频增益）、过载（软饱和）、
  次声底噪、预延迟与回声，再写成 16bit WAV。**没装就只是少几种效果**，出声不受
  影响——与 edge-tts / pygame 的降级策略一致。

有解码器时一律走渲染（采样级精确、跨平台一致）；否则退到播放侧近似；预设若没有
播放侧近似（如「近距震耳」纯过载）就退回原声，并在日志里留一条。

音量在渲染路线上**烘进样本**：WAV 走 MCI（waveaudio）时 ``setaudio`` 不被驱动
支持（实测错误 261），播放层根本设不了音量。
"""

import math
import os
import time
import zlib
from dataclasses import dataclass, field

# ---------------- 两档 ----------------

#: 巨大声源那一档（她本人：按 VoiceCaster 认名字/昵称）
SLOT_HER = "her"
#: 普通尺度声源那一档（主角与其余角色：处在同一个物理环境里）
SLOT_OTHERS = "others"
SLOTS = (SLOT_HER, SLOT_OTHERS)
SLOT_LABELS = {SLOT_HER: "她", SLOT_OTHERS: "其他人"}

#: 「不加效果」的预设键（编辑器下拉里的第一项）
PRESET_NONE = "none"
#: 缺省强度 0-100：预设的基准参数按 ``intensity/100`` 缩放
DEFAULT_INTENSITY = 60
MAX_INTENSITY = 100

#: 被解码音频的解码目标采样率（edge-tts 输出就是 24kHz 单声道）
DECODE_RATE = 24000

#: 效果参数：``(下限, 上限)``。归一化夹取与校验提示共用同一份。
#: 0 值表示该环节关闭（归一化后会从参数里消失）。
PARAM_RANGES = {
    "pre_delay": (0.0, 0.40),
    "muffle_hz": (300.0, 8000.0),
    "low_gain_db": (0.0, 18.0),
    "drive": (0.0, 1.0),
    "rumble_hz": (15.0, 80.0),
    "rumble_level": (0.0, 0.6),
    "tremor_hz": (0.5, 12.0),
    "tremor_depth": (0.0, 0.9),
    "echo_delay": (0.02, 3.0),
    "echo_count": (1, 6),
    "echo_decay": (0.0, 0.95),
    "echo_wet": (0.0, 0.9),
}
#: 参数中文名（编辑器/文档/校验共用）
PARAM_LABELS = {
    "pre_delay": "预延迟（秒）",
    "muffle_hz": "空气吸收拐点（Hz）",
    "low_gain_db": "躯体共振增益（dB）",
    "drive": "过载强度",
    "rumble_hz": "隆隆频率（Hz）",
    "rumble_level": "隆隆声量",
    "tremor_hz": "缓抖频率（Hz）",
    "tremor_depth": "缓抖深度",
    "echo_delay": "首拍延迟（秒）",
    "echo_count": "回声拍数",
    "echo_decay": "每拍衰减",
    "echo_wet": "回声湿量",
}
#: 整数型参数
INT_PARAMS = ("echo_count",)
#: 一档配置里认识的键（校验与守卫共用）
SLOT_KEYS = ("preset", "intensity") + tuple(PARAM_RANGES)
#: 播放侧（零依赖）能做出的环节：延迟分拍 = 回声，音量包络 = 缓抖
PLAYBACK_KEYS = ("echo_delay", "echo_count", "echo_decay", "echo_wet",
                 "tremor_hz", "tremor_depth")
#: 只有离线渲染能做的环节
RENDER_KEYS = ("pre_delay", "muffle_hz", "low_gain_db", "drive",
               "rumble_hz", "rumble_level")


@dataclass(frozen=True)
class Preset:
    """一个物理声学预设。``params`` 是**未被强度缩放**的基准参数。"""

    key: str
    label: str
    hint: str
    slots: tuple
    params: dict = field(default_factory=dict)

    def supports(self, slot) -> bool:
        return slot in self.slots


#: 预设单一真相源。``none`` 必须在第一位（编辑器下拉与默认值都取它）。
PRESETS = (
    Preset(PRESET_NONE, "（不加效果）", "本档不改变声音", SLOTS),
    # ---- 她：巨大声源 ----
    Preset("giant_boom", "巨躯轰鸣",
           "巨大胸腔与肺量：低频厚重、轻微过载，空气随声缓抖",
           (SLOT_HER,),
           {"low_gain_db": 8.0, "drive": 0.35, "rumble_hz": 34.0,
            "rumble_level": 0.18, "tremor_hz": 4.5, "tremor_depth": 0.20}),
    Preset("open_valley", "旷野回声",
           "声音撞上山体与楼群后折回：稀疏的长回声",
           (SLOT_HER,),
           {"echo_delay": 0.45, "echo_count": 3, "echo_decay": 0.55,
            "echo_wet": 0.50}),
    Preset("far_above", "高空遥语",
           "她在远处或高处：空气吸收吃掉高频、直达成变弱且晚到",
           (SLOT_HER,),
           {"pre_delay": 0.09, "muffle_hz": 2200.0, "low_gain_db": 4.0,
            "echo_delay": 0.22, "echo_count": 1, "echo_decay": 0.5,
            "echo_wet": 0.30}),
    Preset("shaking_air", "震空低鸣",
           "庞大身躯搅动空气：低频隆隆裹着缓慢的抖动",
           (SLOT_HER,),
           {"rumble_hz": 28.0, "rumble_level": 0.30, "low_gain_db": 5.0,
            "tremor_hz": 3.0, "tremor_depth": 0.32}),
    Preset("deafening_close", "近距震耳",
           "她俯身贴近：声音过载、低频迅速膨胀、被压实",
           (SLOT_HER,),
           {"drive": 0.70, "low_gain_db": 10.0, "rumble_hz": 42.0,
            "rumble_level": 0.22}),
    # ---- 其他人：普通尺度声源，但处在她的环境里 ----
    Preset("room_echo", "室内回响",
           "普通嗓门在室内：墙面折回的短回声",
           (SLOT_OTHERS,),
           {"echo_delay": 0.11, "echo_count": 2, "echo_decay": 0.50,
            "echo_wet": 0.33}),
    Preset("valley_echo", "山谷折回",
           "同一个旷野里的折回：稀疏回声比她的更弱更短",
           (SLOT_OTHERS,),
           {"echo_delay": 0.50, "echo_count": 3, "echo_decay": 0.60,
            "echo_wet": 0.42}),
    Preset("muffled_wall", "隔墙闷响",
           "隔着一层建筑结构：高频被吃掉，只剩闷响",
           (SLOT_OTHERS,),
           {"muffle_hz": 1400.0, "echo_delay": 0.06, "echo_count": 1,
            "echo_decay": 0.40, "echo_wet": 0.18}),
    Preset("trembling_ground", "震地余波",
           "她一动，地面与空气还在抖：台词裹在低频抖动里",
           (SLOT_OTHERS,),
           {"tremor_hz": 5.5, "tremor_depth": 0.45, "rumble_hz": 30.0,
            "rumble_level": 0.15}),
)

PRESETS_BY_KEY = {preset.key: preset for preset in PRESETS}
#: 全部预设键（文档/守卫用）
ALL_PRESET_KEYS = tuple(preset.key for preset in PRESETS)


def presets_for(slot) -> tuple:
    """某一档可选的预设（含「不加效果」）。"""
    return tuple(preset for preset in PRESETS if preset.supports(slot))


def preset_keys_for(slot) -> tuple:
    return tuple(preset.key for preset in presets_for(slot))


def label_of(key) -> str:
    """预设键 → 中文名（未知键回退「不加效果」）。"""
    preset = PRESETS_BY_KEY.get(str(key or ""))
    return preset.label if preset is not None else PRESETS_BY_KEY[PRESET_NONE].label


# ---------------- 归一化 ----------------

def _number(value):
    """数字转换；bool 不算数字（``True`` 当 1 用是笔误的常见来源）。"""
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _intensity(value) -> int:
    raw = _number(value)
    if raw is None:
        raw = DEFAULT_INTENSITY
    return int(max(0, min(MAX_INTENSITY, round(raw))))


def _clamp_param(key, value):
    low, high = PARAM_RANGES[key]
    value = max(low, min(high, value))
    return int(round(value)) if key in INT_PARAMS else float(value)


def _preset_for(value, slot) -> Preset:
    """取该档下合法的预设；未知键或「不属于这一档的预设」都回退 none。

    后者（把她的「巨躯轰鸣」配到其他人档）是作者最可能犯的错，运行时按
    「不加效果」处理，由 ``validate.py`` 给出可读提示。
    """
    preset = PRESETS_BY_KEY.get(str(value or "").strip())
    if preset is None or not preset.supports(slot):
        return PRESETS_BY_KEY[PRESET_NONE]
    return preset


def normalize_slot(raw, slot) -> dict:
    """把一档配置归一为 ``{preset, intensity, ...显式参数}``。

    ``none`` 返回**空字典**——与 ``normalize_bgm`` 同一套语义：没配就不摊一份
    只有默认值的空配置。显式写出的参数会被夹取到合法区间（作者手改 JSON 的兜底）。
    """
    raw = raw if isinstance(raw, dict) else {}
    preset = _preset_for(raw.get("preset"), slot)
    if preset.key == PRESET_NONE:
        return {}
    spec = {"preset": preset.key, "intensity": _intensity(raw.get("intensity"))}
    for key in PARAM_RANGES:
        if key in raw:
            value = _number(raw.get(key))
            if value is not None:
                spec[key] = _clamp_param(key, value)
    return spec


def normalize_voice_fx(raw) -> dict:
    """章节级对话语音效果：``{}`` = 本章节不加效果。

    形状：``{"her": {...}, "others": {...}}``（只保留配了效果的那一档；
    预设为 none / 未知 / 不属于该档的一律按「不加效果」处理）。
    """
    raw = raw if isinstance(raw, dict) else {}
    result = {}
    for slot in SLOTS:
        spec = normalize_slot(raw.get(slot), slot)
        if spec:
            result[slot] = spec
    return result


def effect_for(fx, slot) -> dict:
    """取某一档的效果配置（没有就返回空字典 = 不加效果）。"""
    return dict((fx or {}).get(slot) or {})


def spec_key(spec) -> str:
    """效果配置的稳定指纹（渲染缓存键用；不能用内置 hash，它对字典无序）。"""
    spec = spec if isinstance(spec, dict) else {}
    return "|".join(f"{key}={spec[key]!r}" for key in sorted(spec))


def resolved_params(spec) -> dict:
    """把强度乘进基准参数，返回**只含开启环节**的参数表（空 = 无效果）。

    强度缩放的物理直觉：频率不该跟着强度变（抖得快慢是尺度决定的），
    **量**跟着变——湿量、深度、增益、过载；拍数按强度取整（强度到 0 就没有拍）。
    空气吸收的低通拐点随强度往低走（越强越闷）。
    """
    spec = spec if isinstance(spec, dict) else {}
    # 分档由 ``normalize_*`` 负责；这里只认预设键（空/未知 = 无效果）
    preset = PRESETS_BY_KEY.get(str(spec.get("preset") or "").strip())
    if preset is None or preset.key == PRESET_NONE:
        return {}
    ratio = _intensity(spec.get("intensity")) / 100.0
    if ratio <= 0:
        return {}
    params = dict(preset.params)
    for key in PARAM_RANGES:
        if key in spec:
            params[key] = spec[key]
    out = {}
    for key, value in params.items():
        if key not in PARAM_RANGES:
            continue
        scaled = _scale_param(key, value, ratio)
        if scaled:
            out[key] = scaled
    return out


def _scale_param(key, value, ratio):
    """按强度缩放单个参数；返回 0/0.0 表示该环节关闭。"""
    if key in ("tremor_hz", "rumble_hz", "echo_delay", "pre_delay"):
        # 频率与延迟由尺度/距离决定，强度只决定"有没有"
        return value if ratio > 0 else 0.0
    if key == "echo_decay":
        return min(PARAM_RANGES[key][1], value * (0.2 + 0.8 * ratio))
    if key == "echo_count":
        return int(max(0, round(value * ratio)))
    if key == "echo_wet":
        return value * ratio
    if key == "muffle_hz":
        # 越强越闷：拐点从 8000Hz 往预设值靠
        return max(PARAM_RANGES[key][0], 8000.0 - (8000.0 - value) * ratio)
    return value * ratio


# ---------------- 引擎能力 ----------------

_DECODER_CACHE = None


def decoder_name() -> str:
    """本机可用的音频解码器名（``miniaudio`` / ``soundfile`` / 空串）。

    两个都是**可选依赖**（和 edge-tts / pygame 一个档次）：装了才谈得上离线
    渲染，没装只是效果表变短，不影响出声。结果缓存：这是给"每句台词"用的路径。
    """
    global _DECODER_CACHE
    if _DECODER_CACHE is None:
        _DECODER_CACHE = ""
        for name in ("miniaudio", "soundfile"):
            try:
                __import__(name)
            except Exception:
                continue
            _DECODER_CACHE = name
            break
    return _DECODER_CACHE


def has_effect(spec) -> bool:
    """这一档配了效果（归一后还有开着的环节）。"""
    return bool(resolved_params(spec))


def dsp_available() -> bool:
    """有没有 numpy（DSP 用；项目里 ``dungeon/window/background.py`` 已在用）。"""
    try:
        import numpy  # noqa: F401
    except Exception:
        return False
    return True


def render_available(spec) -> bool:
    """这一档能否走离线渲染（有解码器 + numpy 且确实配了效果）。"""
    if not resolved_params(spec):
        return False
    return bool(decoder_name()) and dsp_available()


def needs_render(spec) -> bool:
    """这一档是否含「只有渲染能做」的环节（空气吸收/躯体共振/过载/隆隆/预延迟）。"""
    return any(key in RENDER_KEYS for key in resolved_params(spec))


def playback_plan(spec):
    """播放侧近似（零依赖）：``{"taps": ((延迟秒, 相对音量), ...), "tremor": (hz, depth)}``。

    没有可做的环节时返回 ``None``（调用方按「不加效果」处理）。
    分拍音量的物理含义：每一拍是折回来的一次反射，次第衰减。
    """
    params = resolved_params(spec)
    if not params:
        return None
    taps = ()
    count = int(params.get("echo_count", 0) or 0)
    wet = float(params.get("echo_wet", 0.0) or 0.0)
    if count > 0 and wet > 0:
        delay = float(params.get("echo_delay", 0.3))
        decay = float(params.get("echo_decay", 0.5))
        taps = tuple((delay * (index + 1), wet * (decay ** index))
                     for index in range(count))
    tremor = None
    depth = float(params.get("tremor_depth", 0.0) or 0.0)
    if depth > 0:
        tremor = (float(params.get("tremor_hz", 4.0)), depth)
    if not taps and tremor is None:
        return None
    return {"taps": taps, "tremor": tremor}


def engine_for(spec) -> str:
    """这一档实际会用哪条引擎：``render`` / ``playback`` / ``none``（诊断与自检用）。"""
    if not resolved_params(spec):
        return "none"
    if render_available(spec):
        return "render"
    if playback_plan(spec) is not None:
        return "playback"
    return "none"


# ---------------- 离线渲染（可选解码依赖 + numpy） ----------------

def _log(logger, message):
    if logger is None:
        return
    try:
        logger(message)
    except Exception:
        pass


def _numpy():
    try:
        import numpy as np
    except Exception:
        return None
    return np


def _decode(path):
    """``path`` → ``(float32 单声道样本, 采样率)``；没解码器/解不开返回 None。"""
    np = _numpy()
    if np is None or not decoder_name():
        return None
    try:
        import miniaudio
        decoded = miniaudio.decode_file(
            path, output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=1, sample_rate=DECODE_RATE)
        samples = (np.frombuffer(bytes(decoded.samples), dtype=np.int16)
                   .astype(np.float32) / 32768.0)
        if samples.size:
            return samples, int(decoded.sample_rate or DECODE_RATE)
    except Exception:
        pass
    try:
        import soundfile as sf
        data, rate = sf.read(path, dtype="float32", always_2d=True)
        if data.size:
            return data.mean(axis=1).astype(np.float32), int(rate)
    except Exception:
        pass
    return None


def _spectral(np, samples, rate, curve):
    """零相位频域整形（``curve(freqs) -> 增益``）：不引入相位失真，也不需要 scipy。"""
    spectrum = np.fft.rfft(samples)
    freqs = np.fft.rfftfreq(samples.size, 1.0 / rate)
    return np.fft.irfft(spectrum * curve(freqs), samples.size).astype(np.float32)


def _muffle(np, samples, rate, cutoff):
    """空气吸收：越高越远的高频被吃掉（低通），越强越闷。"""
    return _spectral(np, samples, rate,
                     lambda freqs: 1.0 / np.sqrt(1.0 + (freqs / cutoff) ** 4))


def _low_shelf(np, samples, rate, gain_db, corner=220.0):
    """躯体共振：把低频托起来（巨大胸腔与肺量的量感）。"""
    gain = 10.0 ** (gain_db / 20.0)
    return _spectral(np, samples, rate,
                     lambda freqs: 1.0 + (gain - 1.0) / (1.0 + (freqs / corner) ** 2))


def _rumble(np, size, rate, hz, level, peak):
    """次声/低频隆隆：够大够近时空气本身在响，垫在台词下面。"""
    times = np.arange(size, dtype=np.float32) / rate
    wave = np.sin(2 * np.pi * hz * times)
    wave *= 0.6 + 0.4 * np.sin(2 * np.pi * 0.7 * times)
    return wave * (level * max(peak, 1e-6))


def _drive(np, samples, amount):
    """过载（软饱和）：极近距离下听得见"压住了"的那种糊。"""
    peak = float(np.max(np.abs(samples))) or 1.0
    driven = np.tanh(samples * (1.0 + 4.0 * amount))
    driven_peak = float(np.max(np.abs(driven))) or 1.0
    return (driven * (peak / driven_peak)).astype(np.float32)


def _tremor_envelope(np, size, rate, hz, depth):
    """空气缓抖：音量按正弦起伏（频率由尺度决定，与语气无关）。"""
    times = np.arange(size, dtype=np.float32) / rate
    return (1.0 - depth + depth * (0.5 + 0.5 * np.sin(2 * np.pi * hz * times))
            ).astype(np.float32)


def _add_taps(np, samples, rate, delay, count, decay, wet):
    """空间折回：把整句按延迟复制若干拍、次第衰减（稀疏回声）。"""
    step = max(1, int(round(delay * rate)))
    out = np.zeros(samples.size + step * count, dtype=np.float32)
    out[:samples.size] += samples
    for index in range(count):
        offset = step * (index + 1)
        out[offset:offset + samples.size] += samples * (wet * (decay ** index))
    return out


def _limit(np, samples):
    """只在过峰值时压回来：效果不该顺手把整句推响。"""
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak > 0.98:
        return (samples * (0.98 / peak)).astype(np.float32)
    return samples.astype(np.float32)


def _write_wav(path, samples, rate):
    """写 16bit PCM 单声道 WAV（MCI 的 waveaudio 认这个，零依赖）。"""
    import wave
    np = _numpy()
    data = np.clip(samples, -1.0, 1.0)
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(rate))
        handle.writeframes((data * 32767.0).astype("<i2").tobytes())
    return path


def _gain(volume) -> float:
    """音量（0-100）→ 线性增益；渲染路线必须自己乘（WAV 上 MCI 设不了音量）。"""
    value = _number(volume)
    if value is None:
        value = 100.0
    return max(0.0, min(1.0, value / 100.0))


def render_wav(src, dest_dir, spec, volume=100, decoder=None, logger=None):
    """把 ``src`` 按 ``spec`` 渲染成一份 WAV；失败返回 None（静默降级）。

    处理链顺序即物理顺序：预延迟（距离）→ 空气吸收（低通）→ 躯体共振（低频
    增益）→ 次声隆隆 → 过载（软饱和）→ 空气缓抖（音量包络）→ 空间折回
    （分拍回声）→ 限幅 → 音量。

    ``decoder`` 是注入接缝（``(path) -> (samples, rate) | None``）：自检在没有
    解码器的机器上也能验证「解不开就退回原声」这条降级路径。
    """
    params = resolved_params(spec)
    if not params or not os.path.isfile(src):
        return None
    np = _numpy()
    if np is None:
        return None
    decode = decoder or _decode
    try:
        decoded = decode(src)
    except Exception as exc:
        _log(logger, f"[VoiceFx] 解码失败：{exc}")
        return None
    if not decoded:
        return None
    samples, rate = decoded
    try:
        return _render_chain(np, samples, int(rate), params, spec, src, dest_dir,
                             volume)
    except Exception as exc:
        _log(logger, f"[VoiceFx] 渲染失败：{exc}")
        return None


def _render_chain(np, samples, rate, params, spec, src, dest_dir, volume):
    """按 ``params`` 处理样本并落盘（拆出来只为让 ``render_wav`` 只做取舍）。"""
    y = np.asarray(samples, dtype=np.float32)
    if params.get("pre_delay"):
        y = np.concatenate([np.zeros(int(params["pre_delay"] * rate), np.float32), y])
    if params.get("muffle_hz"):
        y = _muffle(np, y, rate, params["muffle_hz"])
    if params.get("low_gain_db"):
        y = _low_shelf(np, y, rate, params["low_gain_db"])
    if params.get("rumble_level"):
        peak = float(np.max(np.abs(y))) if y.size else 0.0
        y = y + _rumble(np, y.size, rate, params.get("rumble_hz", 30.0),
                        params["rumble_level"], peak)
    if params.get("drive"):
        y = _drive(np, y, params["drive"])
    if params.get("tremor_depth"):
        y = y * _tremor_envelope(np, y.size, rate, params.get("tremor_hz", 4.0),
                                 params["tremor_depth"])
    if params.get("echo_wet") and params.get("echo_count"):
        y = _add_taps(np, y, rate, params.get("echo_delay", 0.3),
                      int(params["echo_count"]), params.get("echo_decay", 0.5),
                      params["echo_wet"])
    y = _limit(np, y) * _gain(volume)
    stamp = f"{os.path.basename(src)}|{spec_key(spec)}|{volume}"
    name = f"fx_{abs(zlib.crc32(stamp.encode('utf-8')))}.wav"
    os.makedirs(dest_dir, exist_ok=True)
    return _write_wav(os.path.join(dest_dir, name), y, rate)


# ---------------- 播放侧效果（零依赖） ----------------

class PlaybackFx:
    """播放侧近似：延迟分拍轨 = 回声，音量包络 = 缓抖。

    分拍就是「同一份合成音频再开一路、晚一点起播、音量更低」——Windows MCI
    实测能同时开 8 路同一文件、起播延迟 0.3~1.0 ms，稀疏拍点足够像空间折回；
    音量调制同样走 MCI 的 ``setaudio``（mp3 设备支持，实测播放中可用）。

    运行期间由它自己按 :attr:`STEP_SECONDS` 推进（语音工作线程专职干这件事），
    每一步都问一次 ``is_stale()``：新台词一到立刻退场，把设备让给下一句。
    """

    #: 调制与分拍的节拍（秒）：4~6Hz 的缓抖需要比语音层 0.1s 更细的步长
    STEP_SECONDS = 0.02

    def __init__(self, plan, path, volume, logger=None):
        self._plan = plan or {}
        self._path = path
        self._volume = max(0.0, min(1.0, float(volume)))
        self._logger = logger
        #: [(音轨, 该拍相对音量), ...]
        self._taps = []

    @property
    def tap_count(self) -> int:
        return len(self._taps)

    def run(self, main_track, is_stale) -> str:
        """推进到本句（含回声尾巴）放完；返回 ``done`` / ``stale`` / ``timeout``。"""
        pending = list(self._plan.get("taps") or ())
        tremor = self._plan.get("tremor")
        started = time.monotonic()
        deadline = started + self._tail_seconds(pending)
        while True:
            if is_stale():
                self.close()
                return "stale"
            now = time.monotonic() - started
            while pending and pending[0][0] <= now:
                self._start_tap(pending.pop(0)[1])
            self._modulate(main_track, tremor, now)
            if not pending and self._main_finished(main_track):
                if self._taps_finished():
                    return "done"
                if time.monotonic() > deadline:
                    _log(self._logger, "[VoiceFx] 回声尾巴没放完就提前收尾")
                    self.close()
                    return "timeout"
            time.sleep(self.STEP_SECONDS)

    def close(self):
        """关掉全部分拍音轨（主轨由语音层关，不进这里）。"""
        taps, self._taps = self._taps, []
        for track, _gain in taps:
            try:
                track.stop()
            except Exception:
                pass
            try:
                track.close()
            except Exception:
                pass

    # ---------- 内部 ----------
    def _tail_seconds(self, pending) -> float:
        """上限 = 最后一拍起播后的一段余量（长台词由主轨自己顶着，不会被切）。"""
        last = max([delay for delay, _gain in pending] or [0.0])
        return last + 12.0

    def _start_tap(self, gain):
        track = None
        try:
            track = _open_track(self._path)
            if track is not None and track.start(loop=False,
                                                 volume=self._volume * gain):
                self._taps.append((track, gain))
                return
        except Exception:
            pass
        if track is not None:
            try:
                track.close()
            except Exception:
                pass
        _log(self._logger, "[VoiceFx] 回声分拍未能起播，跳过这一拍")

    def _modulate(self, main_track, tremor, now):
        if not tremor:
            return
        hz, depth = tremor
        envelope = 1.0 - depth + depth * (0.5 + 0.5 * math.sin(2 * math.pi * hz * now))
        for track, gain in [(main_track, 1.0)] + list(self._taps):
            try:
                track.set_volume(self._volume * gain * envelope)
            except Exception:
                pass

    def _main_finished(self, main_track) -> bool:
        try:
            return bool(main_track.finished())
        except Exception:
            return True

    def _taps_finished(self) -> bool:
        for track, _gain in self._taps:
            try:
                if not track.finished():
                    return False
            except Exception:
                continue
        return True


def _open_track(path):
    """延迟导入 ``dungeon.audio``：``chapters`` 会 import 本模块，而 ``audio``
    会 import ``chapters``——模块级导入会绕成环。"""
    from dungeon.audio import open_track
    return open_track(path)
