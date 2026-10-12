"""语音效果**响度**自检：声明值可达成 + 摆幅受 1.5 档约束 + 两级引擎口径一致。

用法：``python tests/check_voice_fx_loudness.py``
输出：控制台一行 ASCII 结论 + UTF-8 报告文件路径。

覆盖：
1. 预设声明：每个非 none 预设都声明了 ``loudness_db``，落在 ``LOUDNESS_RANGE`` 内；
2. **摆幅约束**：全部声明值落在一个 ``LOUDNESS_SPREAD_DB``（9 dB = 1.5 档）窗口内；
3. ``loudness_target``：none / 强度 0 → 0；随强度线性缩放；显式覆盖被夹取；
4. ``loudness_hint`` 文案；``playback_trim`` 只衰减（≥0 dB 时恒 1.0）；
5. ``resolved_params`` 不含 ``loudness_db``（它不是处理链的环节）；
6. ``loudness_db`` 进了 ``PARAM_RANGES`` / ``SLOT_KEYS``（共享校验与 schema 声明）；
7. **实测落点**（语音谱型代理信号喂进真实渲染链，level=4 使总线增益为 1.0）：
   每个预设的直达段 RMS 变化都落在声明目标 ±``LOUDNESS_TOLERANCE_DB`` 内；
8. **摆幅收窄**：自然响度跨度 > 12 dB，对齐后跨度 ≤ 9 dB；
9. **不削波**：渲染产物峰值 ≤ ``PEAK_CEILING``；
10. 播放侧接线：``speech._play`` 按 ``playback_trim`` 衰减，且实测音量 = 总线增益 × 衰减。
"""

import math
import os
import sys
import tempfile
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_report_path = os.path.join(tempfile.mkdtemp(prefix="voice_fx_loudness_"), "report.txt")
_log = open(_report_path, "w", encoding="utf-8")
sys.stdout = _log
sys.stderr = _log

failures = []
total = 0
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 代理信号的参数（与 .temp/voice_fx_loudness.py 同一口径）
RATE = 24000
DURATION = 2.5


def check(name, ok, extra=""):
    global total
    total += 1
    print(("  OK   " if ok else "  FAIL ") + name
          + (f"  <{extra}>" if extra and not ok else ""))
    if not ok:
        failures.append(name)


from dungeon.audio import buses  # noqa: E402
from dungeon.audio import speech as speech_mod  # noqa: E402
from dungeon.audio import voice_fx as vf  # noqa: E402

# ---------------- 1-2. 预设声明与摆幅 ----------------

_declared = {p.key: p.loudness_db for p in vf.PRESETS if p.key != vf.PRESET_NONE}
check("每个非 none 预设都声明了响度目标（数字，不是 None）",
      _declared and all(isinstance(v, (int, float)) for v in _declared.values()),
      _declared)
check("声明值都落在 LOUDNESS_RANGE 内",
      all(vf.LOUDNESS_RANGE[0] <= v <= vf.LOUDNESS_RANGE[1]
          for v in _declared.values()), _declared)
_spread = max(_declared.values()) - min(_declared.values())
check(f"全部预设的响度目标跨度 = {_spread:.1f} dB ≤ {vf.LOUDNESS_SPREAD_DB}（1.5 档）",
      _spread <= vf.LOUDNESS_SPREAD_DB + 1e-9, _spread)
check("摆幅窗口常量与「1.5 档 × 6 dB/档」一致",
      abs(vf.LOUDNESS_SPREAD_DB - 9.0) < 1e-9, vf.LOUDNESS_SPREAD_DB)
check("响度目标互不相同（摆幅不是靠全等值伪造的）",
      len(set(_declared.values())) >= 5, sorted(set(_declared.values())))

# ---------------- 3. loudness_target ----------------

check("none 预设没有目标", vf.loudness_target({"preset": "none", "intensity": 100}) == 0.0
      and vf.loudness_target({}) == 0.0 and vf.loudness_target(None) == 0.0)
check("强度 0 = 没有目标（效果本身也关了）",
      vf.loudness_target({"preset": "giant_boom", "intensity": 0}) == 0.0
      and not vf.has_effect({"preset": "giant_boom", "intensity": 0}))
check("满强度等于声明值",
      abs(vf.loudness_target({"preset": "giant_boom", "intensity": 100})
          - vf.PRESETS_BY_KEY["giant_boom"].loudness_db) < 1e-9)
check("目标随强度线性缩放",
      abs(vf.loudness_target({"preset": "giant_boom", "intensity": 50}) - 1.5) < 1e-9,
      vf.loudness_target({"preset": "giant_boom", "intensity": 50}))
check("未知预设没有目标", vf.loudness_target({"preset": "bogus", "intensity": 100}) == 0.0)
check("显式 loudness_db 覆盖声明值（并按区间夹取）",
      abs(vf.loudness_target({"preset": "open_valley", "intensity": 100,
                              "loudness_db": 4.0}) - 4.0) < 1e-9
      and vf.loudness_target({"preset": "open_valley", "intensity": 100,
                              "loudness_db": 999}) == vf.LOUDNESS_RANGE[1])

# ---------------- 4. hint / playback_trim ----------------

check("loudness_hint：配了效果给 ±dB 文案，没配给空串",
      vf.loudness_hint({"preset": "giant_boom", "intensity": 100}) == "响度 +3.0 dB"
      and vf.loudness_hint({"preset": "none", "intensity": 100}) == ""
      and vf.loudness_hint({}) == "")
_trims = {p.key: vf.playback_trim({"preset": p.key, "intensity": 100})
          for p in vf.PRESETS if p.key != vf.PRESET_NONE}
check("playback_trim 恒 ≤ 1（播放侧抬不起响度）",
      all(0.0 <= t <= 1.0 for t in _trims.values()), _trims)
check("目标 ≥ 0 的预设不衰减（留在该引擎的自然上限）",
      all(_trims[p.key] == 1.0 for p in vf.PRESETS
          if p.key != vf.PRESET_NONE and p.loudness_db >= 0), _trims)
check("目标 < 0 的预设按 dB 折算衰减",
      abs(_trims["far_above"] - 10.0 ** (-3.0 / 20.0)) < 1e-9, _trims["far_above"])
check("强度缩放下衰减变浅（60% 强度 → 目标 ×0.6）",
      vf.playback_trim({"preset": "far_above", "intensity": 60})
      > vf.playback_trim({"preset": "far_above", "intensity": 100}))

# ---------------- 5-6. 参数归属 ----------------

check("resolved_params 不含 loudness_db（不是处理链环节）",
      "loudness_db" not in vf.resolved_params(
          {"preset": "open_valley", "intensity": 80, "loudness_db": 5.0}))
check("loudness_db 进了 PARAM_RANGES / PARAM_LABELS / SLOT_KEYS（共用校验与 schema）",
      vf.LOUDNESS_KEY in vf.PARAM_RANGES and vf.LOUDNESS_KEY in vf.PARAM_LABELS
      and vf.LOUDNESS_KEY in vf.SLOT_KEYS)
check("归一化会夹取越界的 loudness_db",
      vf.normalize_slot({"preset": "open_valley", "loudness_db": 99},
                        vf.SLOT_HER)["loudness_db"] == vf.LOUDNESS_RANGE[1])

# ---------------- 7-9. 实测落点 ----------------

try:
    import numpy as np
    _HAS_NUMPY = True
except Exception:
    _HAS_NUMPY = False


def _speech_proxy(peak=0.95, seed=7):
    """语音谱型代理信号：1/f 倾斜 + 三个共振峰 + 4Hz 音节包络。"""
    n = int(RATE * DURATION)
    rng = np.random.default_rng(seed)
    white = rng.standard_normal(n)
    freqs = np.fft.rfftfreq(n, 1.0 / RATE)
    tilt = 1.0 / np.sqrt(np.maximum(freqs, 1.0))
    shape = np.where((freqs >= 80) & (freqs <= 5000), tilt, 0.0)
    for f0, bw, amp in ((700.0, 220.0, 1.5), (1800.0, 300.0, 1.0),
                        (2800.0, 350.0, 0.6)):
        shape = shape + amp * tilt * np.exp(-0.5 * ((freqs - f0) / bw) ** 2)
    y = np.fft.irfft(np.fft.rfft(white) * shape, n)
    t = np.arange(n) / RATE
    y = y * (0.55 + 0.45 * np.sin(2 * np.pi * 4.0 * t - np.pi / 2))
    y = y / (float(np.max(np.abs(y))) or 1.0) * peak
    return y.astype(np.float32)


def _rms(x):
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))) if x.size else 0.0


def _read_wav(path):
    with wave.open(path, "rb") as handle:
        raw = handle.readframes(handle.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


if not _HAS_NUMPY:
    check("实测落点需要 numpy（项目硬依赖）", False, "numpy 缺失")
else:
    check("level=STEP_MAX 时总线增益恰为 1.0（实测口径成立）",
          abs(buses.resolve_gain(buses.BUS_VOICE, step=buses.STEP_MAX) - 1.0) < 1e-9)

    _tmp = tempfile.mkdtemp(prefix="voice_fx_landing_")
    _src = os.path.join(_tmp, "src.mp3")
    with open(_src, "wb") as _handle:
        _handle.write(b"fake-audio")
    _dry = _speech_proxy()
    _dry_rms = _rms(_dry)
    _dry_peak = float(np.max(np.abs(_dry)))

    _naturals = []
    _achieved = []
    _targets = []
    _peaks = []
    _orig_match = vf._match_loudness
    _seen = {}

    def _spy_match(np_mod, wet, dry, target_db, offset=0):
        out, natural, limited = _orig_match(np_mod, wet, dry, target_db, offset)
        _seen["natural"] = natural
        return out, natural, limited

    vf._match_loudness = _spy_match
    try:
        for _preset in vf.PRESETS:
            if _preset.key == vf.PRESET_NONE:
                continue
            for _intensity in (100, 60):
                _spec = {"preset": _preset.key, "intensity": _intensity}
                _params = vf.resolved_params(_spec)
                if not _params:
                    continue
                _seen.clear()
                _out = vf.render_wav(_src, _tmp, _spec, level=buses.STEP_MAX,
                                     decoder=lambda _p, y=_dry: (y.copy(), RATE))
                check(f"渲染产出（{_preset.key}@{_intensity}）", bool(_out), _out)
                if not _out:
                    continue
                _wav = _read_wav(_out)
                _offset = (int(_params["pre_delay"] * RATE)
                           if _params.get("pre_delay") else 0)
                _win = _wav[_offset:_offset + _dry.size]
                _achieved_db = (20.0 * math.log10(_rms(_win) / _dry_rms)
                                if _rms(_win) > 1e-9 else float("-inf"))
                _target = vf.loudness_target(_spec)
                _dev = abs(_achieved_db - _target)
                check(f"落点 {_preset.label}@{_intensity}："
                      f"{_achieved_db:+.2f} dB ≈ 目标 {_target:+.2f} dB",
                      _dev <= vf.LOUDNESS_TOLERANCE_DB,
                      f"偏差 {_dev:.2f} dB")
                if _seen.get("natural") is not None:
                    _naturals.append(_seen["natural"])
                _achieved.append(_achieved_db)
                _targets.append(_target)
                _peaks.append(float(np.max(np.abs(_wav))))
    finally:
        vf._match_loudness = _orig_match

    _runaway = (max(_naturals) - min(_naturals)) if _naturals else 0.0
    _final_span = (max(_achieved) - min(_achieved)) if _achieved else 0.0
    # 16bit 量化会给落点带来 ~0.001 dB 的抖动，摆幅比较留 0.1 dB 余量
    check(f"自然响度跨度 {_runaway:.1f} dB 被收窄到 {_final_span:.1f} dB（≤ 1.5 档）",
          _runaway > 12.0 and _final_span <= vf.LOUDNESS_SPREAD_DB + 0.1,
          (_runaway, _final_span))
    check("对齐后与声明值的最大偏差在容差内",
          _achieved and max(abs(a - t) for a, t in zip(_achieved, _targets))
          <= vf.LOUDNESS_TOLERANCE_DB,
          max(abs(a - t) for a, t in zip(_achieved, _targets)) if _achieved else None)
    check("渲染产物不削波（峰值 ≤ PEAK_CEILING）",
          _peaks and max(_peaks) <= vf.PEAK_CEILING + 1e-3, max(_peaks) if _peaks else None)
    check("既有静音档也不炸（空/无声输入不产出渲染）",
          vf.render_wav(_src, _tmp, {"preset": "giant_boom", "intensity": 0}) is None)

# ---------------- 10. 播放侧接线 ----------------

_speech_src = open(os.path.join(_PROJECT_ROOT, "dungeon", "audio", "speech.py"),
                   encoding="utf-8").read()
_fx_src = open(os.path.join(_PROJECT_ROOT, "dungeon", "audio", "voice_fx.py"),
               encoding="utf-8").read()
check("speech.py 的播放侧乘了 playback_trim",
      "voice_fx.playback_trim(spec)" in _speech_src)
check("渲染链先对齐响度、再限幅（顺序不能反）",
      _fx_src.index("_match_loudness(np, y, dry, target, offset)")
      < _fx_src.index("_limit(np, y) * _gain(level)"))
check("限幅与响度匹配共用同一个峰值护栏常量",
      "PEAK_CEILING" in _fx_src and _fx_src.count("PEAK_CEILING") >= 3)
check("抬响度前会看峰值余量（不是先抬再被压回来）",
      "headroom" in _fx_src and "peak_limited" in _fx_src)
check("链尾是「响度对齐 → 限幅 × 总线增益」一条语句",
      "y = _limit(np, y) * _gain(level)" in _fx_src)


# 端到端：假后端 + 假解码缺失，看播放侧实际拿到的音量
class _FakeTrack:
    name = "fake"
    events = []

    @classmethod
    def supported(cls):
        return True

    @classmethod
    def open(cls, path):
        return cls(path)

    def __init__(self, path=""):
        self._path = path

    def start(self, loop=False, volume=1.0):
        self.events.append(("start", round(float(volume), 4), self._path))
        return True

    def set_volume(self, volume):
        self.events.append(("volume", round(float(volume), 4), self._path))

    def finished(self):
        return True

    def close(self):
        pass


from dungeon import audio as audio_mod  # noqa: E402

_real_backends = audio_mod.set_backends([_FakeTrack])
_synth_calls = []


def _fake_synth(voice, rate, text, dest_dir):
    _synth_calls.append((voice, rate, text))
    path = os.path.join(dest_dir, "preview.mp3")
    with open(path, "wb") as handle:
        handle.write(b"fake")
    return path


# 强制走播放侧近似：把解码器名钉成空串（有 miniaudio/soundfile 的机器也一样）
_orig_decoder_name = vf.decoder_name
vf.decoder_name = lambda: ""
try:
    _notes = []
    _director = speech_mod.SpeechDirector(
        caster=None, level=buses.STEP_MAX, logger=_notes.append,
        synth=_fake_synth, enabled=True)
    try:
        _director.set_effect({"her": {"preset": "far_above", "intensity": 100}})
        _FakeTrack.events = []
        _director.speak("莉莉", "测试音量")
        _director.wait_idle(6.0)
        _starts = [v for kind, v, _p in _FakeTrack.events if kind == "start"]
        _expected = (buses.resolve_gain(buses.BUS_VOICE, step=buses.STEP_MAX)
                     * vf.playback_trim({"preset": "far_above", "intensity": 100}))
        check("播放侧主声实到音量 = 总线增益 × 响亮衰减",
              _starts and abs(_starts[0] - round(_expected, 4)) < 2e-3,
              (_starts, round(_expected, 4)))
        check("播放侧确实走了近似（有起播 + 分拍）",
              _director.last_engine == "playback" and len(_starts) >= 2,
              (_director.last_engine, _starts))
    finally:
        _director.shutdown()
finally:
    vf.decoder_name = _orig_decoder_name
    audio_mod.set_backends(_real_backends)

# ---------------- 结论 ----------------
_log.flush()
sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__
if failures:
    print(f"[check_voice_fx_loudness] FAILED {len(failures)}/{total}: {failures}")
else:
    print(f"[check_voice_fx_loudness] PASSED {total}/{total}")
print(f"[check_voice_fx_loudness] report: {_report_path}")
sys.exit(1 if failures else 0)
