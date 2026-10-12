"""语音**预听**自检：两条引擎路径 + 降级 + 打断 + 收尾（不出声、不联网）。

用法：``python tests/check_voice_preview.py``
输出：控制台一行 ASCII 结论 + UTF-8 报告文件路径。

覆盖：
1. 模块是**零 UI 依赖**的（不 import tkinter / customtkinter / ui）；
2. 入参校验：没音色 / 这一档没配效果 → 直接判失败，不联网合成；
3. **不带效果**：按总线音量放原声（试听音色 / 语速 / 档位），engine = ``none``；
4. **播放侧近似**：主声音量 = 总线增益 × ``playback_trim``，且开了分拍；
5. **离线渲染**：注入解码器后走渲染，播它时音量恒为 1.0（响度已烘进样本）；
6. **只有渲染能做的预设**（近距震耳）+ 没解码器 → 明确失败并说清原因；
7. 合成失败 → 失败信息可读（不是静默）；
8. **后一次打断前一次**：被顶掉的那一次回调 ``ok=False``；
9. ``shutdown`` 收线程并清掉临时目录；
10. 接线守卫：两个编辑器入口都在，且关对话框会收预听。
"""

import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_report_path = os.path.join(tempfile.mkdtemp(prefix="voice_preview_"), "report.txt")
_log = open(_report_path, "w", encoding="utf-8")
sys.stdout = _log
sys.stderr = _log

failures = []
total = 0
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check(name, ok, extra=""):
    global total
    total += 1
    print(("  OK   " if ok else "  FAIL ") + name
          + (f"  <{extra}>" if extra and not ok else ""))
    if not ok:
        failures.append(name)


from dungeon import audio as audio_mod  # noqa: E402
from dungeon.audio import buses  # noqa: E402
from dungeon.audio import preview as preview_mod  # noqa: E402
from dungeon.audio import voice_fx as vf  # noqa: E402

# ---------------- 1. 零 UI 依赖 ----------------

_preview_src = open(os.path.join(_PROJECT_ROOT, "dungeon", "audio", "preview.py"),
                    encoding="utf-8").read()
check("preview.py 不依赖任何 UI 框架",
      "tkinter" not in _preview_src and "customtkinter" not in _preview_src
      and "from ui" not in _preview_src)
check("样例台词非空且够短（合成快）",
      isinstance(preview_mod.SAMPLE_TEXT, str)
      and 0 < len(preview_mod.SAMPLE_TEXT) <= 30, preview_mod.SAMPLE_TEXT)

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
        self._path = path
        self.closed = False

    def start(self, loop=False, volume=1.0):
        self.events.append(("start", round(float(volume), 4),
                            os.path.basename(self._path)))
        return True

    def set_volume(self, volume):
        self.events.append(("volume", round(float(volume), 4), ""))

    def finished(self):
        return True

    def close(self):
        self.closed = True
        self.events.append(("close", None, ""))


def _make_synth(delay_event=None):
    def _synth(voice, rate, text, dest_dir):
        if delay_event is not None:
            delay_event.wait(5.0)
        path = os.path.join(dest_dir, "preview.mp3")
        with open(path, "wb") as handle:
            handle.write(b"fake")
        return path
    return _synth


_real_backends = audio_mod.set_backends([_FakeTrack])
_orig_decoder_name = vf.decoder_name
_num = None
try:
    import numpy as _np
    _num = _np
except Exception:
    pass

try:
    # ---------------- 2. 入参校验 ----------------
    _p = preview_mod.VoicePreview(synth=_make_synth())
    _calls = []
    check("没音色：直接失败且不合成",
          _p.play("", effect={"preset": "open_valley", "intensity": 80},
                  on_done=lambda ok, m: _calls.append((ok, m))) is False
          and _calls and _calls[-1][0] is False and _p.last_ok is False,
          _calls)
    check("这一档没配效果（none / 强度 0）：直接失败",
          _p.play("zh-CN-XiaoxiaoNeural", effect={"preset": "none", "intensity": 80},
                  on_done=lambda ok, m: _calls.append((ok, m))) is False
          and _p.play("zh-CN-XiaoxiaoNeural",
                      effect={"preset": "open_valley", "intensity": 0},
                      on_done=lambda ok, m: _calls.append((ok, m))) is False,
          _calls)
    _p.shutdown()

    # ---------------- 3. 不带效果（试听音色 / 语速 / 档位） ----------------
    _FakeTrack.events = []
    _p = preview_mod.VoicePreview(synth=_make_synth())
    _p.play("zh-CN-XiaoxiaoNeural", effect=None, level=buses.STEP_MAX)
    _p.wait_idle(6.0)
    _starts = [v for k, v, _f in _FakeTrack.events if k == "start"]
    check("不带效果：放原声、engine = none、音量 = 总线增益",
          _p.last_ok is True and _p.last_engine == "none"
          and _starts == [round(buses.resolve_gain(buses.BUS_VOICE,
                                                   step=buses.STEP_MAX), 4)],
          (_p.last_ok, _p.last_engine, _starts))
    _p.shutdown()

    # ---------------- 4. 播放侧近似 ----------------
    _FakeTrack.events = []
    _p = preview_mod.VoicePreview(synth=_make_synth())
    vf.decoder_name = lambda: ""            # 强制走零依赖那条路
    _p.play("zh-CN-XiaoxiaoNeural", slot=vf.SLOT_HER,
            effect={"preset": "open_valley", "intensity": 80},
            level=buses.STEP_MAX)
    _p.wait_idle(6.0)
    _starts = [v for k, v, _f in _FakeTrack.events if k == "start"]
    _expected = (buses.resolve_gain(buses.BUS_VOICE, step=buses.STEP_MAX)
                 * vf.playback_trim({"preset": "open_valley", "intensity": 80}))
    check("播放侧：主声音量 = 总线增益 × 响亮衰减",
          _starts and abs(_starts[0] - round(_expected, 4)) < 2e-3,
          (_starts, round(_expected, 4)))
    check("播放侧：开了分拍（回声尾巴）且 engine = playback",
          _p.last_engine == "playback" and len(_starts) >= 2
          and _p.last_ok is True,
          (_p.last_engine, _starts))
    check("播放结束不留音轨（playing 为假、全部已关）",
          _p.playing is False
          and sum(1 for k, _v, _f in _FakeTrack.events if k == "close") >= len(_starts))

    # ---------------- 6. 只有渲染能做的预设 + 没解码器 ----------------
    _calls = []
    _ok = _p.play("zh-CN-XiaoxiaoNeural", slot=vf.SLOT_HER,
                  effect={"preset": "deafening_close", "intensity": 100},
                  level=buses.STEP_MAX, on_done=lambda ok, m: _calls.append((ok, m)))
    _p.wait_idle(6.0)
    check("纯渲染预设 + 无解码器：明确失败并说清原因",
          _ok is True and _calls and _calls[-1][0] is False
          and "离线渲染" in _calls[-1][1], _calls[-1:] if _calls else None)
    _p.shutdown()

    # ---------------- 5. 离线渲染 ----------------
    if _num is None:
        check("离线渲染路径需要 numpy（项目硬依赖）", False, "numpy 缺失")
    else:
        vf.decoder_name = lambda: "miniaudio"
        _orig_decode = vf._decode
        _dry = _num.linspace(-0.4, 0.4, RATE, dtype="float32")
        vf._decode = lambda path: (_dry.copy(), RATE)
        try:
            _FakeTrack.events = []
            _p = preview_mod.VoicePreview(synth=_make_synth())
            _p.play("zh-CN-XiaoxiaoNeural", slot=vf.SLOT_HER,
                    effect={"preset": "open_valley", "intensity": 80},
                    level=buses.STEP_MAX)
            _p.wait_idle(10.0)
            _starts = [v for k, v, _f in _FakeTrack.events if k == "start"]
            _files = [f for k, _v, f in _FakeTrack.events if k == "start"]
            check("有解码器：走离线渲染、engine = render、音量恒 1.0",
                  _p.last_ok is True and _p.last_engine == "render"
                  and _starts == [1.0] and _files and _files[0].endswith(".wav"),
                  (_p.last_ok, _p.last_engine, _starts, _files))
            _p.shutdown()
        finally:
            vf._decode = _orig_decode
            vf.decoder_name = _orig_decoder_name

    # ---------------- 7. 合成失败 ----------------
    _calls = []
    _p = preview_mod.VoicePreview(synth=lambda *a: None)
    _p.play("zh-CN-XiaoxiaoNeural", effect={"preset": "open_valley",
                                            "intensity": 80})
    _p.wait_idle(6.0)
    check("合成失败：last_ok 为假、给得出可读原因",
          _p.last_ok is False and bool(_p.last_message)
          and ("合成" in _p.last_message), _p.last_message)
    _p.shutdown()

    # ---------------- 8. 后一次打断前一次 ----------------
    _gate = threading.Event()
    _done = []
    _p = preview_mod.VoicePreview(synth=_make_synth(delay_event=_gate))
    _p.play("zh-CN-XiaoxiaoNeural", effect={"preset": "open_valley", "intensity": 80},
            on_done=lambda ok, m: _done.append(("A", ok, m)))
    # 等第一次真的进了工作线程（否则它在队列里就会被算作“被取代”也行）
    _p.play("zh-CN-XiaoxiaoNeural", effect={"preset": "room_echo", "intensity": 90},
            slot=vf.SLOT_OTHERS,
            on_done=lambda ok, m: _done.append(("B", ok, m)))
    _gate.set()
    _p.wait_idle(8.0)
    _superseded = [d for d in _done if d[0] == "A"]
    _kept = [d for d in _done if d[0] == "B"]
    check("被顶掉的那一次回调 ok=False，并说明「取代」",
          _superseded and _superseded[-1][1] is False
          and "取代" in _superseded[-1][2], _done)
    check("最后一次正常播完（last_ok 为真）", _kept and _kept[-1][1] is True
          and _p.last_ok is True, _done)

    # ---------------- 9. shutdown 收尾 ----------------
    _tmp = _p._tmp_dir
    _p.shutdown()
    check("shutdown：线程收工", _p.worker_alive is False)
    check("shutdown：临时目录被清掉",
          _tmp is None or not os.path.exists(_tmp), _tmp)
finally:
    vf.decoder_name = _orig_decoder_name
    audio_mod.set_backends(_real_backends)

# ---------------- 10. 接线守卫 ----------------

_dlg_src = open(os.path.join(_PROJECT_ROOT, "ui", "scenario", "chapter_dlg.py"),
                encoding="utf-8").read()
_sc_src = open(os.path.join(_PROJECT_ROOT, "ui", "scenario", "sc_frame.py"),
               encoding="utf-8").read()
check("章节对话框：导入并用了 VoicePreview",
      "from dungeon.audio.preview import VoicePreview" in _dlg_src
      and "VoicePreview(" in _dlg_src)
check("章节对话框：两档各有一个「预听」按钮",
      "_preview_fx" in _dlg_src and "预听" in _dlg_src
      and "self._fx_preview_buttons" in _dlg_src)
check("章节对话框：关窗时收预听",
      "def destroy" in _dlg_src and "preview.shutdown()" in _dlg_src)
check("章节对话框：显示只读的声明响度",
      "loudness_hint" in _dlg_src and "_refresh_fx_loudness" in _dlg_src)
check("方案编辑器：音色行有「预听」",
      "from dungeon.audio.preview import VoicePreview" in _sc_src
      and "_preview_voice" in _sc_src and "预听" in _sc_src)
check("方案编辑器：关窗时收预听",
      "def destroy" in _sc_src and "preview.shutdown()" in _sc_src)
check("预听与副本朗读共用同一套效果与合成（不另起一套）",
      "voice_fx.render_wav" in _preview_src and "voice_fx.playback_plan" in _preview_src
      and "_speech.synthesize" in _preview_src)
check("预听也尊重方案档位（走总线增益）",
      "buses.resolve_gain(buses.BUS_VOICE" in _preview_src)

# ---------------- 结论 ----------------
_log.flush()
sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__
if failures:
    print(f"[check_voice_preview] FAILED {len(failures)}/{total}: {failures}")
else:
    print(f"[check_voice_preview] PASSED {total}/{total}")
print(f"[check_voice_preview] report: {_report_path}")
sys.exit(1 if failures else 0)
