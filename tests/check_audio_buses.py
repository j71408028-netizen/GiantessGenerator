"""音频总线自检：电平换算单一真相源 + 接入后行为不变。

用法：``python tests/check_audio_buses.py``
输出：控制台一行 ASCII 结论 + UTF-8 报告文件路径。

覆盖：
1. 总线表自洽（标识 / 标签 / 默认值齐备，顺序稳定）；
2. 回填兼容：``DEFAULT_BGM_VOLUME`` / ``DEFAULT_VOICE_VOLUME`` / 上限与旧值一致；
3. **行为不变**：``resolve_gain(bus, item=x)`` 与旧的 ``x/100`` 逐值相等
   （含越界 / 非法值 / 缺省）；
4. 总线电平与总控相乘；
5. dB 曲线接口（``to_db`` / ``db_gain``）正确且互逆；
6. 接线守卫：三条播放路径都改走总线，旧的 ``volume / 100`` 换算已消失。
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_report_path = os.path.join(tempfile.mkdtemp(prefix="audio_buses_"), "report.txt")
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


from dungeon.audio import buses  # noqa: E402
from dungeon.audio import chapters as chapters_mod  # noqa: E402
from dungeon.audio import speech as speech_mod  # noqa: E402

# ---------------- 1. 总线表自洽 ----------------
check("总线顺序固定（总控在前）",
      buses.BUSES == (buses.BUS_MASTER, buses.BUS_MUSIC,
                      buses.BUS_VOICE, buses.BUS_SFX), buses.BUSES)
check("每条总线都有标签与默认值",
      all(_b in buses.BUS_LABELS for _b in buses.BUSES)
      and all(_b in buses.DEFAULT_ITEM_LEVELS for _b in buses.BUSES)
      and all(_b in buses.DEFAULT_BUS_LEVELS for _b in buses.BUSES))
check("总线电平默认不衰减（本步恒 100）",
      all(buses.bus_default(_b) == buses.MAX_LEVEL for _b in buses.BUSES),
      buses.DEFAULT_BUS_LEVELS)

# ---------------- 2. 回填兼容：旧常量值不变 ----------------
check("音乐默认 70 / 语音默认 90（与旧常量同值）",
      chapters_mod.DEFAULT_BGM_VOLUME == 70
      and speech_mod.DEFAULT_VOICE_VOLUME == 90,
      (chapters_mod.DEFAULT_BGM_VOLUME, speech_mod.DEFAULT_VOICE_VOLUME))
check("两种上限都还是 100",
      chapters_mod.MAX_BGM_VOLUME == 100
      and speech_mod.MAX_VOICE_VOLUME == 100)
check("旧常量现在取自总线表（单一真相源）",
      chapters_mod.DEFAULT_BGM_VOLUME == buses.item_default(buses.BUS_MUSIC)
      and speech_mod.DEFAULT_VOICE_VOLUME == buses.item_default(buses.BUS_VOICE))


# ---------------- 3. 行为不变：与旧 volume/100 逐值一致 ----------------
def _old_ratio(value, default):
    """旧实现的换算（``int(float(v))`` 兜底后把比值夹到 0-1）。"""
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number / 100.0))


_samples = [0, 1, 30, 70, 90, 100, 150, -20, 70.9, None, "x", ""]

_bgm_mismatch = [(v, buses.resolve_gain(buses.BUS_MUSIC, item=v), _old_ratio(v, 70))
                 for v in _samples
                 if buses.resolve_gain(buses.BUS_MUSIC, item=v) != _old_ratio(v, 70)]
check("总线接入后 BGM 增益与旧算法逐位一致", not _bgm_mismatch, _bgm_mismatch)

_voice_mismatch = [(v, buses.resolve_gain(buses.BUS_VOICE, item=v), _old_ratio(v, 90))
                   for v in _samples
                   if buses.resolve_gain(buses.BUS_VOICE, item=v) != _old_ratio(v, 90)]
check("总线接入后朗读增益与旧算法逐位一致", not _voice_mismatch, _voice_mismatch)

check("缺省内容电平：音乐 70 / 语音 90",
      buses.resolve_gain(buses.BUS_MUSIC) == 0.7
      and buses.resolve_gain(buses.BUS_VOICE) == 0.9)


# ---------------- 4. 总线电平与总控相乘 ----------------
check("总线电平与总控相乘",
      buses.resolve_gain(buses.BUS_MUSIC, item=100, bus_level=50, master=50) == 0.25,
      buses.resolve_gain(buses.BUS_MUSIC, item=100, bus_level=50, master=50))
check("任一层归零 → 整条链静音",
      buses.resolve_gain(buses.BUS_VOICE, item=90, bus_level=0) == 0.0
      and buses.resolve_gain(buses.BUS_VOICE, item=90, master=0) == 0.0)
check("电平越界 / 非法值被夹取或回退",
      buses.clamp_level(150) == 100 and buses.clamp_level(-5) == 0
      and buses.clamp_level(None) == 100 and buses.clamp_level("x", 70) == 70)


# ---------------- 5. dB 曲线接口（本步未接线） ----------------
check("to_db：100 → 0 dB、0 → 静音下限",
      buses.to_db(100) == 0.0 and buses.to_db(0) == buses.DB_FLOOR,
      (buses.to_db(100), buses.to_db(0)))
check("db_gain：0 dB → 1.0、静音下限 → 0.0",
      buses.db_gain(0.0) == 1.0 and buses.db_gain(buses.DB_FLOOR) == 0.0)
check("to_db 与 db_gain 互逆",
      abs(buses.db_gain(buses.to_db(50)) - 0.5) < 1e-9,
      buses.db_gain(buses.to_db(50)))


# ---------------- 6. 接线守卫 ----------------
def _src(*parts):
    return open(os.path.join(_PROJECT_ROOT, *parts), encoding="utf-8").read()


_init_src = _src("dungeon", "audio", "__init__.py")
_speech_src = _src("dungeon", "audio", "speech.py")
_fx_src = _src("dungeon", "audio", "voice_fx.py")

check("BgmPlayer 走 BUS_MUSIC 总线",
      "buses.resolve_gain(buses.BUS_MUSIC" in _init_src)
check("SpeechDirector 走 BUS_VOICE 总线",
      "buses.resolve_gain(buses.BUS_VOICE" in _speech_src)
check("渲染音量走 BUS_VOICE 总线",
      "buses.resolve_gain(buses.BUS_VOICE" in _fx_src)
check("旧的 volume/100 换算已从播放路径消失",
      "volume / 100.0" not in _init_src and "self.volume / 100" not in _speech_src)

# ---------------- 结论 ----------------
_log.flush()
sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__
if failures:
    print(f"[check_audio_buses] FAILED {len(failures)}/{total}: {failures}")
else:
    print(f"[check_audio_buses] PASSED {total}/{total}")
print(f"[check_audio_buses] report: {_report_path}")
sys.exit(1 if failures else 0)
