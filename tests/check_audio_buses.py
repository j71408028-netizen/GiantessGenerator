"""音频总线自检：音量**档位**单一真相源 + 旧 0-100 数据迁移 + 分层锚点。

用法：``python tests/check_audio_buses.py``
输出：控制台一行 ASCII 结论 + UTF-8 报告文件路径。

覆盖：
1. 档位表自洽（0-4 / 中文名 / dB 表 / 总线锚点齐备，顺序稳定）；
2. ``clamp_step``：夹取、非法值回退、bool 不算数字；
3. 显示文案往返：``step_display`` ↔ ``step_from_display``、``step_choices``；
4. **旧数据迁移**：``step_from_level`` 把 0-100 按响度就近折算成档位；
5. 档位 → 增益：静音档归零、dB 表换算、**音乐锚点比语音低 6 dB**；
6. ``resolve_gain`` 组合：档位 × 总线电平 × 总控，任一层归零即静音；
7. **玩家侧实时电平**：``levels_from_settings`` / ``set_bus_levels`` /
   ``effective_bus_level`` 接管后影响 ``resolve_gain``，``None`` 交还缺省；
8. 电平换算接口（``to_ratio`` / ``to_db`` / ``db_gain`` / ``clamp_level``）；
9. **缺省对齐**：``DEFAULT_BGM_LEVEL`` / ``DEFAULT_VOICE_LEVEL`` 都取总线缺省档；
10. **归一迁移**：``bgm_level`` / ``voice_level`` 优先新键、回退旧键；``normalize_*``
    只产出 ``level``；``overrides.parse_overrides`` 两种键都认；
11. **设置缺省**：``SettingsRepo`` 缺省含四条总线电平（取自总线单一真相源）；
12. 接线守卫：三条播放路径都改走总线，副本窗口把 settings 电平装进混入层，
    设置页读写 ``audio_levels``，旧的 ``volume / 100`` 换算已消失。
"""

import math
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


def near(a, b, tol=1e-9):
    return abs(a - b) <= tol


from dungeon.audio import buses  # noqa: E402
from dungeon.audio import chapters as chapters_mod  # noqa: E402
from dungeon.audio import speech as speech_mod  # noqa: E402
from dungeon import overrides as overrides_mod  # noqa: E402

# ---------------- 1. 档位表自洽 ----------------
check("档位顺序与范围：0-4，缺省「中」（3）",
      buses.STEPS == (0, 1, 2, 3, 4)
      and (buses.STEP_MIN, buses.STEP_MAX, buses.DEFAULT_STEP) == (0, 4, 3),
      (buses.STEPS, buses.STEP_MIN, buses.STEP_MAX, buses.DEFAULT_STEP))
check("每个档位都有中文名",
      set(buses.STEP_LABELS) == set(buses.STEPS),
      buses.STEP_LABELS)
check("dB 表覆盖所有档位，静音档为 None、满档为 0 dB",
      set(buses.STEP_DB) == set(buses.STEPS)
      and buses.STEP_DB[0] is None and buses.STEP_DB[4] == 0.0,
      buses.STEP_DB)
check("每条总线都有锚点 dB",
      all(_b in buses.BUS_ANCHOR_DB for _b in buses.BUSES),
      buses.BUS_ANCHOR_DB)
check("每条总线都有标签与玩家电平缺省",
      all(_b in buses.BUS_LABELS for _b in buses.BUSES)
      and all(_b in buses.DEFAULT_BUS_LEVELS for _b in buses.BUSES))
check("总线电平默认不衰减（玩家滑条落地前恒 100）",
      all(buses.bus_default(_b) == buses.MAX_LEVEL for _b in buses.BUSES),
      buses.DEFAULT_BUS_LEVELS)

# ---------------- 2. clamp_step ----------------
check("clamp_step 夹取到 0-4",
      buses.clamp_step(2) == 2 and buses.clamp_step(9) == 4
      and buses.clamp_step(-3) == 0)
check("clamp_step 非法值回退缺省档（3）",
      buses.clamp_step(None) == 3 and buses.clamp_step("x") == 3,
      (buses.clamp_step(None), buses.clamp_step("x")))
check("clamp_step 把 bool 当非法（True 不当 1）",
      buses.clamp_step(True) == 3 and buses.clamp_step(False) == 3)
check("clamp_step 认数字字符串与四舍五入",
      buses.clamp_step("2") == 2 and buses.clamp_step(2.6) == 3)
check("step_label 取中文名，越界回退缺省名",
      buses.step_label(9) == "响" and buses.step_label(None) == "中",
      (buses.step_label(9), buses.step_label(None)))

# ---------------- 3. 显示文案往返 ----------------
check("step_display 同时给数字与名字",
      buses.step_display(3) == "3 · 中" and buses.step_display(0) == "0 · 静音",
      (buses.step_display(3), buses.step_display(0)))
check("step_choices 是 5 个（文案, 档位）对",
      len(buses.step_choices()) == 5
      and buses.step_choices()[0] == ("0 · 静音", 0)
      and buses.step_choices()[-1] == ("4 · 响", 4),
      buses.step_choices())
check("step_options 是（档位, 名字）对",
      buses.step_options() == ((0, "静音"), (1, "很轻"), (2, "轻"),
                               (3, "中"), (4, "响")),
      buses.step_options())
check("显示文案可往返",
      all(buses.step_from_display(buses.step_display(_s)) == _s
          for _s in buses.STEPS))
check("step_from_display 也认纯数字 / 中文名 / 脏字符串",
      buses.step_from_display("0") == 0
      and buses.step_from_display("响") == 4
      and buses.step_from_display(2) == 2
      and buses.step_from_display("abc") == 3
      and buses.step_from_display(True) == 3,
      (buses.step_from_display("0"), buses.step_from_display("响"),
       buses.step_from_display(2), buses.step_from_display("abc")))

# ---------------- 4. 旧 0-100 → 档位 ----------------
_legacy = [(0, 0), (1, 1), (24, 1), (25, 2), (49, 2), (50, 3), (79, 3),
           (80, 4), (100, 4)]
check("旧 0-100 按响度就近折算成档位",
      all(buses.step_from_level(_v) == _s for _v, _s in _legacy),
      [(v, buses.step_from_level(v)) for v, _s in _legacy])
check("旧值越界被夹取",
      buses.step_from_level(-5) == 0 and buses.step_from_level(999) == 4,
      (buses.step_from_level(-5), buses.step_from_level(999)))
check("旧值非法回退缺省档 / bool 不算数字",
      buses.step_from_level("x") == 3 and buses.step_from_level(True) == 3)

# ---------------- 5. 档位 → 增益（含总线锚点） ----------------
check("静音档（0）在线性增益上就是 0",
      buses.step_gain(buses.BUS_MUSIC, 0) == 0.0
      and buses.step_gain(buses.BUS_VOICE, 0) == 0.0)
check("step_db：语音满档 0 dB、音乐满档 −6 dB",
      buses.step_db(buses.BUS_VOICE, 4) == 0.0
      and buses.step_db(buses.BUS_MUSIC, 4) == -6.0,
      (buses.step_db(buses.BUS_VOICE, 4), buses.step_db(buses.BUS_MUSIC, 4)))
check("step_db：中档语音 −6 dB、中档音乐 −12 dB",
      buses.step_db(buses.BUS_VOICE, 3) == -6.0
      and buses.step_db(buses.BUS_MUSIC, 3) == -12.0,
      (buses.step_db(buses.BUS_VOICE, 3), buses.step_db(buses.BUS_MUSIC, 3)))
check("step_gain：语音满档 1.0、音乐满档 ≈0.501",
      buses.step_gain(buses.BUS_VOICE, 4) == 1.0
      and near(buses.step_gain(buses.BUS_MUSIC, 4), 10 ** (-6 / 20)),
      (buses.step_gain(buses.BUS_VOICE, 4), buses.step_gain(buses.BUS_MUSIC, 4)))
_music = buses.step_gain(buses.BUS_MUSIC, 3)
_voice = buses.step_gain(buses.BUS_VOICE, 3)
check("同档位下音乐比语音低 ≈6 dB（分层落在锚点，不靠逐值调）",
      near(20 * math.log10(_voice / _music), 6.0, tol=1e-6),
      20 * math.log10(_voice / _music))

# ---------------- 6. resolve_gain 组合 ----------------
check("缺省档（中）下的线性增益：音乐 ≈0.251 / 语音 ≈0.501",
      near(buses.resolve_gain(buses.BUS_MUSIC), 10 ** (-12 / 20))
      and near(buses.resolve_gain(buses.BUS_VOICE), 10 ** (-6 / 20)),
      (buses.resolve_gain(buses.BUS_MUSIC), buses.resolve_gain(buses.BUS_VOICE)))
check("显式满档语音 → 1.0（总线缺省不衰减，没偷加全局 trim）",
      buses.resolve_gain(buses.BUS_VOICE, step=4) == 1.0)
check("档位、总线电平、总控三者相乘",
      near(buses.resolve_gain(buses.BUS_VOICE, step=4, bus_level=50, master=50), 0.25),
      buses.resolve_gain(buses.BUS_VOICE, step=4, bus_level=50, master=50))
check("任一层归零 → 整条链静音",
      buses.resolve_gain(buses.BUS_VOICE, step=4, bus_level=0) == 0.0
      and buses.resolve_gain(buses.BUS_VOICE, step=4, master=0) == 0.0
      and buses.resolve_gain(buses.BUS_VOICE, step=0) == 0.0)
check("step=None 走缺省档（与显式 3 一致）",
      near(buses.resolve_gain(buses.BUS_MUSIC),
           buses.resolve_gain(buses.BUS_MUSIC, step=3)))

# ---------------- 7. 玩家侧实时电平 ----------------
check("levels_from_settings 缺省回退 100（未调过滑条 / 空 settings）",
      buses.levels_from_settings({}) == {_b: 100 for _b in buses.BUSES}
      and buses.levels_from_settings(None) == buses.DEFAULT_BUS_LEVELS,
      buses.levels_from_settings({}))
check("levels_from_settings 读 settings[audio_levels] 并夹取越界",
      buses.levels_from_settings({"audio_levels": {"music": 40}})["music"] == 40
      and buses.levels_from_settings({"audio_levels": {"voice": 999}})["voice"] == 100
      and buses.levels_from_settings({"audio_levels": {"master": -5}})["master"] == 0,
      buses.levels_from_settings({"audio_levels": {"music": 40, "voice": 999,
                                                   "master": -5}}))
check("levels_from_settings 结构坏掉时整体回退缺省",
      buses.levels_from_settings({"audio_levels": "x"}) == buses.DEFAULT_BUS_LEVELS)

check("未接管时 effective_bus_level 取缺省 100",
      buses.effective_bus_level(buses.BUS_MUSIC) == 100
      and buses.effective_bus_level(buses.BUS_MASTER) == 100)
buses.set_bus_levels({buses.BUS_MUSIC: 30})
check("set_bus_levels 缺项回退缺省、逐条生效",
      buses.effective_bus_level(buses.BUS_MUSIC) == 30
      and buses.effective_bus_level(buses.BUS_VOICE) == 100)
buses.set_bus_levels({buses.BUS_MASTER: 100, buses.BUS_MUSIC: 50,
                      buses.BUS_VOICE: 50, buses.BUS_SFX: 100})
check("实时电平压到 resolve_gain（内容总线）",
      buses.effective_bus_level(buses.BUS_MUSIC) == 50
      and near(buses.resolve_gain(buses.BUS_MUSIC),
               buses.step_gain(buses.BUS_MUSIC, 3) * 0.5),
      buses.resolve_gain(buses.BUS_MUSIC))
check("显式传参优先于实时值",
      near(buses.resolve_gain(buses.BUS_VOICE, step=4, bus_level=100, master=100), 1.0))
buses.set_bus_levels({buses.BUS_MASTER: 50, buses.BUS_MUSIC: 100,
                      buses.BUS_VOICE: 100, buses.BUS_SFX: 100})
check("总控实时电平也压到内容总线",
      near(buses.resolve_gain(buses.BUS_VOICE, step=4), 0.5),
      buses.resolve_gain(buses.BUS_VOICE, step=4))
buses.set_bus_levels(None)
check("set_bus_levels(None) 交还缺省（实时值不再生效）",
      buses.effective_bus_level(buses.BUS_MUSIC) == 100
      and near(buses.resolve_gain(buses.BUS_VOICE, step=4), 1.0))

# ---------------- 8. 电平换算接口 ----------------
check("clamp_level 夹取 / 回退",
      buses.clamp_level(150) == 100 and buses.clamp_level(-5) == 0
      and buses.clamp_level(None) == 100 and buses.clamp_level("x", 70) == 70)
check("to_ratio：50 → 0.5、0 → 0.0",
      buses.to_ratio(50) == 0.5 and buses.to_ratio(0) == 0.0)
check("to_db：100 → 0 dB、0 → 静音下限",
      buses.to_db(100) == 0.0 and buses.to_db(0) == buses.DB_FLOOR,
      (buses.to_db(100), buses.to_db(0)))
check("db_gain：0 dB → 1.0、静音下限 → 0.0",
      buses.db_gain(0.0) == 1.0 and buses.db_gain(buses.DB_FLOOR) == 0.0)
check("to_db 与 db_gain 互逆",
      near(buses.db_gain(buses.to_db(50)), 0.5, tol=1e-9),
      buses.db_gain(buses.to_db(50)))

# ---------------- 9. 模块缺省对齐 ----------------
check("章节 / 语音缺省档都取总线缺省（单一真相源）",
      chapters_mod.DEFAULT_BGM_LEVEL == buses.DEFAULT_STEP
      and speech_mod.DEFAULT_VOICE_LEVEL == buses.DEFAULT_STEP,
      (chapters_mod.DEFAULT_BGM_LEVEL, speech_mod.DEFAULT_VOICE_LEVEL))
check("章节 / 语音档位上限都取总线上限",
      chapters_mod.MAX_BGM_LEVEL == buses.STEP_MAX
      and speech_mod.MAX_VOICE_LEVEL == buses.STEP_MAX)

# ---------------- 10. 归一迁移 ----------------
check("bgm_level 优先新键 level",
      chapters_mod.bgm_level({"level": 2}) == 2
      and chapters_mod.bgm_level({"level": 2, "volume": 70}) == 2)
check("bgm_level 回退旧键 volume 并按响度折算",
      chapters_mod.bgm_level({"volume": 70}) == 3
      and chapters_mod.bgm_level({}) == buses.DEFAULT_STEP)
check("voice_level 同样优先新键、回退旧键",
      speech_mod.voice_level({"level": 1}) == 1
      and speech_mod.voice_level({"volume": 90}) == 4
      and speech_mod.voice_level({}) == buses.DEFAULT_STEP)
_bgm = chapters_mod.normalize_bgm({"path": "a.mp3", "volume": 70})
check("normalize_bgm 只写 level（存盘即完成迁移）",
      _bgm.get("level") == 3 and "volume" not in _bgm,
      _bgm)
check("normalize_bgm 缺曲目返回空（本章节不放音乐）",
      chapters_mod.normalize_bgm({"volume": 70}) == {})
_voice = speech_mod.normalize_voice({"volume": 90})
check("normalize_voice 只写 level",
      _voice.get("level") == 4 and "volume" not in _voice,
      _voice)
check("parse_overrides 认新键 bgm_level",
      overrides_mod.parse_overrides({"bgm_level": 2}) == {"bgm_level": 2})
check("parse_overrides 认旧键 bgm_volume（折算成档位）",
      overrides_mod.parse_overrides({"bgm_volume": 70}) == {"bgm_level": 3},
      overrides_mod.parse_overrides({"bgm_volume": 70}))
check("parse_overrides 夹取越界档位、忽略非法值",
      overrides_mod.parse_overrides({"bgm_level": 9}) == {"bgm_level": 4}
      and overrides_mod.parse_overrides({"bgm_level": "x"}) == {})
check("describe 用档位中文名",
      overrides_mod.describe({"bgm_level": 2}) == "音乐档位=轻",
      overrides_mod.describe({"bgm_level": 2}))

# ---------------- 11. 设置缺省 ----------------
from persistence.settings_repo import SettingsRepo  # noqa: E402
check("设置缺省含四条总线电平（取自总线单一真相源）",
      SettingsRepo()._defaults.get("audio_levels") == buses.DEFAULT_BUS_LEVELS,
      SettingsRepo()._defaults.get("audio_levels"))

# ---------------- 12. 接线守卫 ----------------
def _src(*parts):
    return open(os.path.join(_PROJECT_ROOT, *parts), encoding="utf-8").read()


_init_src = _src("dungeon", "audio", "__init__.py")
_speech_src = _src("dungeon", "audio", "speech.py")
_fx_src = _src("dungeon", "audio", "voice_fx.py")
_chapters_src = _src("dungeon", "audio", "chapters.py")
_base_src = _src("dungeon", "window", "base.py")
_settings_src = _src("ui", "settings", "__init__.py")

check("BgmPlayer 走 BUS_MUSIC 总线",
      "buses.resolve_gain(buses.BUS_MUSIC" in _init_src)
check("SpeechDirector 走 BUS_VOICE 总线",
      "buses.resolve_gain(buses.BUS_VOICE" in _speech_src)
check("渲染音量走 BUS_VOICE 总线",
      "buses.resolve_gain(buses.BUS_VOICE" in _fx_src)
check("档位经 buses.clamp_step 归一（播放侧）",
      "buses.clamp_step(" in _speech_src and "buses.clamp_step(" in _init_src)
check("副本窗口把 settings 电平装进总线",
      "buses.set_bus_levels(" in _base_src
      and "buses.levels_from_settings(" in _base_src)
check("设置页读写 audio_levels 并遍历总线表",
      "audio_levels" in _settings_src and "buses.BUSES" in _settings_src)
check("旧的 volume / 100 换算已从播放路径消失",
      "volume / 100.0" not in _init_src and "self.volume / 100" not in _speech_src)
check("旧的 0-100 常量已从音源模块消失",
      "DEFAULT_BGM_VOLUME" not in _chapters_src
      and "DEFAULT_VOICE_VOLUME" not in _speech_src)

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
