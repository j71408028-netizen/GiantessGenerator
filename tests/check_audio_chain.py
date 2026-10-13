"""方案音频链路自检：从配置到播放/渲染，不出声、不联网。

用法：``python tests/check_audio_chain.py``
输出：控制台一行 ASCII 结论 + UTF-8 报告文件路径。

覆盖：
1. 说话人 → 音色：她 / 主角专用，配角按名字稳定；
2. ``SpeechDirector`` 队列：叙述句 / 空句 / 超长句不合成，合成用方案音色语速；
3. 章节效果两档：她那一档分拍回声、其他人那一档缓抖、离开章节清空；
4. 三条引擎路径：``none`` / ``playback`` / ``render``，以及解码失败退回原声；
5. ``BgmPlayer`` 在缺/坏音频文件时不抛异常且能收工。
"""

import os
import struct
import sys
import tempfile
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_report_path = os.path.join(tempfile.mkdtemp(prefix="audio_chain_"), "report.txt")
_log = open(_report_path, "w", encoding="utf-8")
sys.stdout = _log
sys.stderr = _log

failures = []
total = 0


def check(name, ok, extra=""):
    global total
    total += 1
    print(("  OK   " if ok else "  FAIL ") + name
          + (f"  <{extra}>" if extra and not ok else ""))
    if not ok:
        failures.append(name)


from dungeon import audio as audio_mod  # noqa: E402
from dungeon.audio import speech as speech_mod  # noqa: E402
from dungeon.audio import voice_fx as fx_mod  # noqa: E402
from dungeon.audio.voice_fx import (SLOT_HER, SLOT_OTHERS, effect_for,  # noqa: E402
                                    has_effect, normalize_voice_fx,
                                    playback_plan)

# ---------------- 假后端与假合成器 ----------------

RATE = 24000


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
        self.started = None
        self.closed = False
        self._path = path

    def start(self, loop=False, volume=1.0):
        self.started = (loop, volume)
        self.events.append(("start", round(float(volume), 3), self._path))
        return True

    def set_volume(self, volume):
        self.events.append(("volume", round(float(volume), 3), self._path))

    def finished(self):
        return True

    def close(self):
        self.closed = True
        self.events.append(("close", None, self._path))


_synth_calls = []


def _fake_synth(voice, rate, text, dest_dir):
    _synth_calls.append((voice, rate, text))
    path = os.path.join(dest_dir, f"{abs(hash(text))}.mp3")
    with open(path, "wb") as handle:
        handle.write(b"fake")
    return path


_caster = speech_mod.VoiceCaster(her_names=("莉莉", "莉"),
                                 protagonist_names=("主角",),
                                 voice_her="V_HER", voice_protagonist="V_PRO",
                                 other_voices=("A", "B", "C"))
check("她与主角各用自己的音色",
      _caster.voice_for("莉莉") == "V_HER" and _caster.voice_for("主角") == "V_PRO")
check("昵称同样认作她", _caster.voice_for("莉") == "V_HER")
check("同一个配角每次都是同一把嗓子",
      _caster.voice_for("军官") == _caster.voice_for("军官"))
check("不同配角的嗓子分得开",
      len({_caster.voice_for(n) for n in ("军官", "居民", "队长")}) > 1)

_real_backends = audio_mod.set_backends([_FakeTrack])
_notes = []
try:
    # ---------------- SpeechDirector 队列 ----------------
    _director = speech_mod.SpeechDirector(caster=_caster, rate="+10%", level=3,
                                          logger=_notes.append, synth=_fake_synth,
                                          enabled=True)
    check("叙述句（无说话人）不朗读", _director.speak("", "一段描写") is False)
    check("空台词不朗读", _director.speak("莉莉", "   ") is False)
    check("超长台词不朗读",
          _director.speak("莉莉", "啊" * (speech_mod.MAX_SPEECH_CHARS + 1)) is False)
    check("带说话人的台词排上队", _director.speak("莉莉", "你跑不掉的") is True)
    check("等到台词处理完", _director.wait_idle(4.0))
    check("合成用的是她的音色与方案语速",
          _synth_calls and _synth_calls[-1][0] == "V_HER"
          and _synth_calls[-1][1] == "+10%", _synth_calls)
    check("朗读过的音色有留痕", _director.last_voice == "V_HER")
    check("关掉就不出声",
          (_director.configure(enabled=False) or _director.speak("莉莉", "再来一句"))
          is False)
    _director.shutdown()
    check("收尾后语音线程已收工", _director.worker_alive is False
          and _director.speaking is False)

    # ---------------- 章节效果两档 + 引擎路径 ----------------
    _FakeTrack.events = []
    _fx_director = speech_mod.SpeechDirector(caster=_caster, level=3,
                                             logger=_notes.append, synth=_fake_synth,
                                             enabled=True)
    try:
        _fx_director.set_effect({"her": {"preset": "open_valley", "intensity": 80},
                                 "others": {"preset": "trembling_ground",
                                            "intensity": 70}})
        check("效果已按两档归一",
              _fx_director.effect == normalize_voice_fx(
                  {"her": {"preset": "open_valley", "intensity": 80},
                   "others": {"preset": "trembling_ground", "intensity": 70}}),
              _fx_director.effect)
        check("分档取配置：她拿到她的预设",
              effect_for(_fx_director.effect, SLOT_HER)["preset"] == "open_valley"
              and effect_for(_fx_director.effect, SLOT_OTHERS)["preset"]
              == "trembling_ground")

        _FakeTrack.events = []
        _fx_director.speak("莉莉", "你跑不掉的")
        _fx_director.wait_idle(6.0)
        _starts = [v for kind, v, _p in _FakeTrack.events if kind == "start"]
        check("她那一档走播放侧并开了分拍（主声 + 2 拍）",
              _fx_director.last_engine == "playback" and len(_starts) == 3,
              (_fx_director.last_engine, _FakeTrack.events))
        check("分拍音量按次第衰减（主声 > 拍1 > 拍2 > 0）",
              len(_starts) == 3 and _starts[0] > _starts[1] > _starts[2] > 0, _starts)

        _FakeTrack.events = []
        _fx_director.speak("军官", "全体撤退")     # 配角：走「其他人」那一档
        _fx_director.wait_idle(4.0)
        _fx_director.speak("主角", "等等！")       # 主角同样走「其他人」那一档
        _fx_director.wait_idle(4.0)
        _kinds = [kind for kind, _v, _p in _FakeTrack.events]
        check("其他人那一档只有缓抖：每句 1 起播 + 音量调制",
              _kinds.count("start") == 2 and _kinds.count("volume") >= 2, _FakeTrack.events)

        _FakeTrack.events = []
        _fx_director.set_effect({})                # 走到没配效果的章节
        _fx_director.speak("莉莉", "我回来了")
        _fx_director.wait_idle(4.0)
        _kinds = [kind for kind, _v, _p in _FakeTrack.events]
        check("离开章节后效果清空：只起播一次、不动音量",
              _fx_director.last_engine == "none" and _kinds.count("start") == 1
              and _kinds.count("volume") == 0, (_fx_director.last_engine, _kinds))

        # 渲染路线：注入解码器（本机可能没装 miniaudio/soundfile，缺口就在 _decode）
        _fx_tmp = tempfile.mkdtemp(prefix="scenario_fx_")
        _fx_src = os.path.join(_fx_tmp, "src.wav")
        with wave.open(_fx_src, "wb") as _handle:
            _handle.setnchannels(1)
            _handle.setsampwidth(2)
            _handle.setframerate(24000)
            _handle.writeframes(struct.pack("<h", 6000) * 6000)     # 0.25s 方波
        _original_decode = fx_mod._decode

        def _fx_decode(_path):
            import numpy as np
            with wave.open(_fx_src, "rb") as _handle:
                _raw = _handle.readframes(_handle.getnframes())
            return np.frombuffer(_raw, dtype="<i2").astype("float32") / 32768.0, 24000

        fx_mod._decode = _fx_decode
        try:
            _FakeTrack.events = []
            _fx_director.set_effect({"her": {"preset": "giant_boom", "intensity": 80}})
            _fx_director.speak("莉莉", "轰鸣着靠近")
            _fx_director.wait_idle(4.0)
            _started_paths = [_p for _k, _v, _p in _FakeTrack.events if _k == "start"]
            check("有解码器时走离线渲染（engine=render、只起播一次）",
                  _fx_director.last_engine == "render" and len(_started_paths) == 1,
                  (_fx_director.last_engine, _FakeTrack.events))
            check("渲染产物留在临时目录（音量烘进样本，走 waveaudio）",
                  any(str(_p).endswith(".wav") for _p in _started_paths),
                  _started_paths)
        finally:
            fx_mod._decode = _original_decode

        # 解不开 → 静默降级（引擎不该炸，也不该不出声）
        _rendered = fx_mod.render_wav(_fx_src, _fx_tmp,
                                      {"preset": "open_valley", "intensity": 80},
                                      decoder=lambda _path: None)
        check("解码失败 → 不产出渲染文件（调用方退回原声）", _rendered is None,
              _rendered)
        check("解码器可用性可查询（编辑器提示与校验用）",
              isinstance(fx_mod.decoder_name(), str))
        check("没配效果不走渲染",
              fx_mod.render_wav(_fx_src, _fx_tmp, {}) is None)
    finally:
        _fx_director.shutdown()
        check("效果链收尾：语音线程已收工", _fx_director.worker_alive is False)

    # ---------------- BgmPlayer：缺/坏文件也不能拖垮会话 ----------------
    check("当前环境有明确的音频后端结论", bool(audio_mod.backend_name()))
    _bgm_tmp = tempfile.mkdtemp(prefix="audio_chain_bgm_")
    _probe_notes = []
    _probe_resolver = {"tone.wav": os.path.join(_bgm_tmp, "tone.wav")}
    open(_probe_resolver["tone.wav"], "wb").write(b"RIFF....WAVEfmt ")
    _probe = audio_mod.BgmPlayer(resolver=_probe_resolver.get, logger=_probe_notes.append)
    _probe.play({"path": "tone.wav", "level": 3, "loop": True, "fade_seconds": 0})
    _probe.play({"path": "不在的曲子.mp3"})
    _probe.wait_idle()
    _probe.stop()
    _probe.wait_idle()
    _probe.shutdown()
    check("播放器在缺/坏音频文件时不抛异常且能收工",
          not _probe.playing and not _probe.worker_alive
          and any("音频文件不存在" in line for line in _probe_notes),
          _probe_notes)
finally:
    audio_mod.set_backends(_real_backends)

# ---------------- 结论 ----------------
_log.flush()
sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__
if failures:
    print(f"[check_audio_chain] FAILED {len(failures)}/{total}: {failures}")
else:
    print(f"[check_audio_chain] PASSED {total}/{total}")
print(f"[check_audio_chain] report: {_report_path}")
sys.exit(1 if failures else 0)
