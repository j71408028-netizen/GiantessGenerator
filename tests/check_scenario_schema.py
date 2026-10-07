"""S2 自检：schema 单一真相源 + 校验器（无 GUI，不碰真实 data/）。

用法：``python tests/check_scenario_schema.py``
输出：控制台一行 ASCII 结论 + UTF-8 报告文件路径。

覆盖：
1. 空方案模板 golden 对比（与改名前的 ``_empty_config`` 完全一致）；
2. ``normalize_chapter`` 产出的字段都在 schema 声明内（防再次漂移）；
3. 滤镜键单一出处（``actions.VISUAL_FILTERS`` ↔ ``background._PIL_FILTERS``）；
4. 校验器规则用例（合成坏配置的每类诊断 + 干净配置零误报）；
5. ``ScenarioRepo.save_config`` 诊断接入。
6. 演化规则的配置链路：``transition_matrix`` / ``section_steps`` 真的进
   ``EvolutionRules``（含 window/base.py 的接线守卫）。
"""

import json
import os
import struct
import sys
import tempfile
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_report_path = os.path.join(tempfile.mkdtemp(prefix="scenario_schema_"), "report.txt")
_log = open(_report_path, "w", encoding="utf-8")
sys.stdout = _log
sys.stderr = _log

failures = []
total = 0
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check(name, ok, extra=""):
    global total
    total += 1
    print(("  OK   " if ok else "  FAIL ") + name + (f"  <{extra}>" if extra and not ok else ""))
    if not ok:
        failures.append(name)


from dungeon import schema  # noqa: E402
from dungeon.audio.chapters import (DEFAULT_BGM_FADE_SECONDS, DEFAULT_BGM_VOLUME,  # noqa: E402
                              MAX_BGM_VOLUME, normalize_bgm, normalize_chapter)
from dungeon.validate import (  # noqa: E402
    format_diagnostics, has_errors, validate_scenario_config)

# ---------------- 1. 空方案模板 golden 对比 ----------------
GOLDEN_EMPTY = {
    "initial_prompt": "",
    "coupling_level": "velum",
    "protagonist_title": "",
    "text_component": "text",
    "entry_action_cost": 0,
    "ending_policy": "required",
    "section_prompts": {"background": "", "branch": "", "dialog": "",
                        "interaction": "", "action": ""},
    "evolution_attrs": [
        {"type": "intrusion", "name": "介入度", "display_state": "collapse"},
        {"type": "destruction", "name": "破坏性", "display_state": "collapse"},
        {"type": "casualty", "name": "总伤亡", "display_state": "collapse"},
    ],
    "chapters": [],
    "triggers": [],
    # 对话语音：默认开、音色取默认（字面量写死，改动默认值必须让这里先红）
    "voice": {
        "enabled": True,
        "voice_her": "zh-CN-XiaoxiaoNeural",
        "voice_protagonist": "zh-CN-YunxiNeural",
        "voice_other": ("zh-CN-YunjianNeural", "zh-CN-YunyangNeural",
                        "zh-CN-XiaoyiNeural"),
        "rate": "+0%",
        "volume": 90,
    },
}
check("空方案模板与旧版 _empty_config 一致",
      schema.empty_scenario_config() == GOLDEN_EMPTY)

# ---------------- 2. schema ↔ normalize 一致性 ----------------
normalized = normalize_chapter({"name": "示例"}, 0)
declared = set(schema.field_map(schema.CHAPTER_FIELDS))
check("normalize_chapter 产出的字段都被 schema 声明",
      set(normalized) <= declared, set(normalized) - declared)
check("schema 声明的必填字段存在", schema.field_map(schema.CHAPTER_FIELDS)["name"].required)
check("章节背景音乐字段已在 schema 声明", "bgm" in declared
      and "bgm" in normalized, set(normalized) - declared)
check("bgm 字段声明与 normalize 产出的键一致",
      set(normalize_bgm({"path": "audio/a.mp3"}))
      == set(schema.field_map(schema.BGM_FIELDS)))

# ---------------- 2b. 对话语音 schema ↔ normalize ↔ 接线 ----------------
import dungeon.audio.speech as speech_mod  # noqa: E402
from dungeon.audio.speech import MAX_VOICE_VOLUME, normalize_voice  # noqa: E402

check("方案级对话语音字段已在 schema 声明",
      "voice" in schema.known_scenario_keys())
check("voice 字段声明与 normalize 产出的键一致",
      set(normalize_voice(None)) == set(schema.field_map(schema.VOICE_FIELDS)))
check("语速非法值回退常速",
      normalize_voice({"rate": "快一点"})["rate"] == speech_mod.DEFAULT_RATE
      and normalize_voice({"rate": "+15%"})["rate"] == "+15%")
check("音量越界被夹取",
      normalize_voice({"volume": 500})["volume"] == MAX_VOICE_VOLUME
      and normalize_voice({"volume": -3})["volume"] == 0)
check("空音色回退默认、音色池空回退默认池",
      normalize_voice({"voice_her": "  ", "voice_other": []})["voice_her"]
      == speech_mod.DEFAULT_VOICE_HER
      and normalize_voice({"voice_other": []})["voice_other"]
      == speech_mod.DEFAULT_OTHER_VOICES)

# 说话人 → 音色：她 / 主角走专用音色，其余按名字稳定（同名字两次结果相同）
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

# 播放层接线（领域 → 窗口）：坏接线会让功能静默失效，这里盯住两端
import dungeon.audio as audio_mod  # noqa: E402
import dungeon.window.base as base_mod  # noqa: E402
import dungeon.window.ui as ui_mod  # noqa: E402

_base_src = open(os.path.join(_PROJECT_ROOT, "dungeon", "window", "base.py"),
                 encoding="utf-8").read()
_ui_src = open(os.path.join(_PROJECT_ROOT, "dungeon", "window", "ui.py"),
               encoding="utf-8").read()
check("会话持有语音导演并在收尾时收工",
      "SpeechDirector(" in _base_src and "self._speech.shutdown()" in _base_src)
check("语音按方案配置 + 全局开关装配",
      "dungeon_voice_enabled" in _base_src)
check("带说话人的显示单元定稿时触发朗读",
      "def _speak_item" in _ui_src and "director.speak(" in _ui_src)
check("语音播放复用音频后端接缝", "open_track" in speech_mod.__dict__
      or "open_track" in dir(speech_mod))

# 假合成器 + 假音轨：整条链路跑一遍，不出声、不联网
_notes = []
_synth_calls = []


class _FakeTrack:
    name = "fake"
    #: 收到的指令 ``[(动作, 音量), ...]``，效果链据此断言分拍与调制
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


def _fake_synth(voice, rate, text, dest_dir):
    _synth_calls.append((voice, rate, text))
    path = os.path.join(dest_dir, f"{abs(hash(text))}.mp3")
    with open(path, "wb") as handle:
        handle.write(b"fake")
    return path


_real_backends = audio_mod.set_backends([_FakeTrack])
_director = speech_mod.SpeechDirector(caster=_caster, rate="+10%", volume=80,
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
audio_mod.set_backends(_real_backends)

# ---------------- 3. 滤镜键单一出处 ----------------
import dungeon.actions as actions_mod  # noqa: E402
import dungeon.window.background as background_mod  # noqa: E402

check("滤镜键一致（actions 与 background）",
      set(background_mod._PIL_FILTERS) == set(actions_mod.VISUAL_FILTER_KEYS))

# ---------------- 4. 校验器规则用例 ----------------

def _bad_config():
    return {
        "initial_prompt": 123,
        "coupling_level": "nope",
        "protagonist_title": 123,
        "text_component": "nope",
        "components": ["text"],
        "entry_action_cost": -5,
        "view_mode": "game",
        "typo_key": 1,
        "section_prompts": {"typo": "x"},
        "section_steps": {"dialog": "many"},
        "transition_matrix": {"dialog": {"action": 3}},
        "evolution_attrs": [
            {"type": "intrusion", "name": "介入度", "display_state": "collapse"},
            {"type": "custom", "name": "热度", "rate": -1},
            {"type": "custom", "name": "热度"},
            {"type": "unknown"},
        ],
        "chapters": [
            {"name": "开场", "start": True, "overflow_target": "不存在"},
            {"name": "终幕", "ending": True},
        ],
        "triggers": [
            {"name": "t_goto", "chapter": "开场", "action": "goto",
             "action_data": {"chapter": "不存在"}},
            {"name": "t_opt", "chapter": "终幕", "action": "option",
             "action_data": {"options": []}},
            {"name": "t_opt", "action": "insert", "action_data": {"text": ""}},
            {"name": "", "action": "background"},
            {"name": "t_cond", "action": "none",
             "condition": {"operator": "xor", "rules": [
                 {"key": "幽灵", "comparator": "=>", "value": 1},
                 {"key": "选择:不存在", "metric": "avg", "value": 1},
                 {"key": "伤亡数组", "metric": "sum", "value": 1},
             ]}, "precondition_names": ["不存在"]},
            {"name": "t_old", "action": "ending", "action_data": {}},
        ],
    }


diags = validate_scenario_config(_bad_config())


def _expect(level, path_part, label):
    hits = [d for d in diags if d.level == level and path_part in d.path]
    check(f"诊断：{label}", bool(hits),
          format_diagnostics([d for d in diags if path_part in d.path], include_info=True))


_expect("error", "triggers[0].action_data.chapter", "goto 悬空目标（error）")
_expect("error", "triggers[1].action_data.options", "空选项列表（error）")
_expect("error", "triggers[2].action_data.text", "插入触发器缺文本（error）")
_expect("warning", "triggers[1]", "结束章节内放 option（warning）")
_expect("warning", "triggers[2].name", "触发器重名")
_expect("warning", "triggers[3].name", "触发器缺名")
_expect("warning", "triggers[3].action", "旧版动作 background")
_expect("warning", "triggers[4].condition.operator", "非法逻辑组合符")
_expect("warning", "triggers[4].condition.rules[0].comparator", "非法比较符")
_expect("warning", "triggers[4].condition.rules[0].key", "未知条件键")
_expect("warning", "triggers[4].condition.rules[1].key", "选择: 引用不存在的触发器")
_expect("warning", "triggers[4].condition.rules[1].metric", "选项度量不支持")
_expect("warning", "triggers[4].condition.rules[2].metric", "伤亡度量不支持")
_expect("warning", "triggers[4].precondition_names", "前置引用不存在")
_expect("warning", "triggers[5].action", "旧版结局触发器")
_expect("warning", "triggers[5].action_data.name", "结局未填名称")
_expect("error", "chapters[0].overflow_target", "超限跳转悬空（error）")
_expect("warning", "initial_prompt", "initial_prompt 非字符串")
_expect("warning", "coupling_level", "耦合等级无效")
_expect("warning", "protagonist_title", "主角称呼非字符串")
_expect("warning", "text_component", "文本组件无效")
_expect("info", "components", "旧写法文本组件残留")
_expect("warning", "entry_action_cost", "进入点数非法")
_expect("warning", "view_mode", "废弃顶层字段")
_expect("info", "typo_key", "未知顶层字段")
_expect("warning", "section_prompts.typo", "分节提示词未知键")
_expect("warning", "section_steps.dialog", "分节步长非正数")
_expect("warning", "transition_matrix.dialog.action", "转移矩阵权重越界")
_expect("warning", "transition_matrix.background", "转移矩阵缺行")
_expect("warning", "evolution_attrs[1].rate", "rate 为负数")
_expect("warning", "evolution_attrs[2].name", "自定义属性重名")
check("缺内置演化属性（2 条）",
      sum(1 for d in diags if d.level == "warning" and d.path == "evolution_attrs"
          and "内置演化属性" in d.message) == 2)
check("有 1 个起始章节不报起始诊断",
      not [d for d in diags if "起始章节" in d.message])
check("有结束章节不报「无结局路径」",
      not [d for d in diags if "结局路径" in d.message])

# 干净配置：零误报（1 个起始 + 1 个结束章节；结束章节带图标路径但不提供 scenario_dir）
clean = schema.empty_scenario_config()
clean["chapters"] = [{"name": "开场", "start": True}, {"name": "终幕", "ending": True}]
clean_diags = validate_scenario_config(clean)
check("干净配置无错误/警告", not has_errors(clean_diags)
      and not any(d.level == "warning" for d in clean_diags),
      format_diagnostics(clean_diags))
check("干净配置提示结束章节无图标",
      any(d.level == "info" and "结局图标" in d.message for d in clean_diags))

# 空配置与完全空的方案
check("空 dict 报 error", has_errors(validate_scenario_config({})))
check("无章节无触发器：提示无结局路径",
      any(d.level == "info" and "结局路径" in d.message
          for d in validate_scenario_config(schema.empty_scenario_config())))

# ending_policy：作者显式声明开放式方案后不再提示"无结局路径"
_open_config = schema.empty_scenario_config()
_open_config["ending_policy"] = "open"
_open_diags = validate_scenario_config(_open_config)
check("ending_policy=open：不再提示无结局路径",
      not [d for d in _open_diags if "结局路径" in d.message],
      format_diagnostics(_open_diags))
check("ending_policy 默认 required、归一化未知值",
      schema.normalize_ending_policy(None) == "required"
      and schema.normalize_ending_policy("OPEN") == "open"
      and schema.normalize_ending_policy("随便") == "required")
_bad_policy = schema.empty_scenario_config()
_bad_policy["ending_policy"] = "开放式"
check("ending_policy 非法值报 warning",
      any(d.level == "warning" and d.path == "ending_policy"
          for d in validate_scenario_config(_bad_policy)))
_ending_config = schema.empty_scenario_config()
_ending_config["ending_policy"] = "open"
_ending_config["chapters"] = [{"name": "终幕", "ending": True}]
check("有结束章节时 open 也不报无结局路径",
      not [d for d in validate_scenario_config(_ending_config) if "结局路径" in d.message])

# ---------------- 4b. 章节背景音乐 ----------------
_bgm_tmp = tempfile.mkdtemp(prefix="scenario_bgm_")
check("未配音乐归一为空 dict（运行时按「无音乐」处理）",
      normalize_bgm(None) == {} and normalize_bgm({}) == {}
      and normalize_bgm({"path": "  "}) == {})
check("bgm 默认补全", normalize_bgm({"path": "audio/a.ogg"}) == {
    "path": "audio/a.ogg", "volume": DEFAULT_BGM_VOLUME, "loop": True,
    "fade_seconds": DEFAULT_BGM_FADE_SECONDS}, normalize_bgm({"path": "audio/a.ogg"}))
_clamped = normalize_bgm({"path": "a.mp3", "volume": MAX_BGM_VOLUME + 400,
                          "loop": False, "fade_seconds": -1})
check("bgm 越界值被夹取（音量封顶、负淡入淡出归零）",
      _clamped["volume"] == MAX_BGM_VOLUME and _clamped["loop"] is False
      and _clamped["fade_seconds"] == 0.0, _clamped)

_bgm_config = schema.empty_scenario_config()
_bgm_config["chapters"] = [
    {"name": "开场", "start": True,
     "bgm": {"path": "audio/nope.mp3", "volume": 500, "fade_seconds": "x"}},
    {"name": "终幕", "ending": True, "bgm": {"path": "audio/a.xyz"}},
]
_bgm_diags = validate_scenario_config(_bgm_config, scenario_dir=_bgm_tmp)


def _expect_bgm(level, path_part, label):
    hits = [d for d in _bgm_diags if d.level == level and path_part in d.path]
    check(f"诊断：{label}", bool(hits),
          format_diagnostics([d for d in _bgm_diags if "bgm" in d.path],
                             include_info=True))


_expect_bgm("warning", "chapters[0].bgm.path", "背景音乐文件不存在")
_expect_bgm("warning", "chapters[0].bgm.volume", "bgm 音量越界")
_expect_bgm("warning", "chapters[0].bgm.fade_seconds", "bgm 淡入淡出非数字")
_expect_bgm("warning", "chapters[1].bgm.path", "bgm 后缀不是常见格式")
check("bgm 章节没有 scenarios_dir 时不报错路径存在性",
      not [d for d in validate_scenario_config(_bgm_config)
           if "bgm.path" in d.path and "不存在" in d.message])

# 存一轮再读：迁移不得丢字段，且坏值体现在读到的数据里（已夹取）
from persistence.scenario_repo import ScenarioRepo as _Repo  # noqa: E402
_bgm_repo = _Repo(data_dir=os.path.join(_bgm_tmp, "data"))
_bgm_repo.save_config("_bgm", _bgm_config)
_bgm_loaded = _bgm_repo.load_config("_bgm")
check("存读一轮：章节 bgm 未丢失且坏值已修正",
      _bgm_loaded["chapters"][0]["bgm"]["path"] == "audio/nope.mp3"
      and _bgm_loaded["chapters"][0]["bgm"]["volume"] == MAX_BGM_VOLUME
      and _bgm_loaded["chapters"][1]["bgm"]["loop"] is True,
      _bgm_loaded["chapters"])

# 播放层接线（领域 → 窗口）：接线漏了功能会静默失效，这里盯住两端
_base_src = open(os.path.join(_PROJECT_ROOT, "dungeon", "window", "base.py"),
                 encoding="utf-8").read()
_trg_src = open(os.path.join(_PROJECT_ROOT, "dungeon", "window", "triggers.py"),
                encoding="utf-8").read()
check("window/base.py 创建会话级 BgmPlayer", "BgmPlayer(" in _base_src)
check("window/base.py 会话收尾关闭背景音乐", "_bgm.shutdown()" in _base_src)
check("triggers.py 进入章节时应用 bgm", "_apply_chapter_bgm" in _trg_src)
check("triggers.py 回放带着 bgm 复现", '"bgm"' in _trg_src
      and "record.get(\"bgm\")" in _trg_src)

# ---------------- 4c. 章节对话语音效果（她 / 其他人两档） ----------------
# 物理效果而非语气：接线漏了会静默失效，所以这里既盯 schema ↔ normalize，
# 也盯「谁用哪一档 → 用哪条引擎 → 降级」这条链。
import dungeon.audio.voice_fx as fx_mod  # noqa: E402
from dungeon.audio.voice_fx import (DEFAULT_INTENSITY, MAX_INTENSITY,  # noqa: E402
                              PARAM_RANGES, PRESET_NONE, SLOT_HER,
                              SLOT_OTHERS, effect_for, has_effect,
                              normalize_voice_fx, playback_plan,
                              preset_keys_for)

check("voice_fx 字段已在章节 schema 声明", "voice_fx" in declared
      and "voice_fx" in normalized, set(normalized) - declared)
check("两档的键与 schema 声明一致",
      set(normalize_voice_fx({"her": {"preset": "open_valley"},
                               "others": {"preset": "room_echo"}}))
      == set(schema.field_map(schema.VOICE_FX_FIELDS)))
_full_spec = {"preset": "far_above", "intensity": 50}
for _key in PARAM_RANGES:                       # 高级参数也得被 schema 认识
    _full_spec[_key] = PARAM_RANGES[_key][0]
check("单档产出的键 == schema 声明的键（含高级参数）",
      set(normalize_voice_fx({"her": _full_spec})["her"])
      == set(schema.field_map(schema.VOICE_FX_SLOT_FIELDS)),
      set(normalize_voice_fx({"her": _full_spec})["her"])
      ^ set(schema.field_map(schema.VOICE_FX_SLOT_FIELDS)))
check("没配 / 全是 none / 不是 dict 都归一为空（＝不加效果）",
      normalize_voice_fx(None) == {} and normalize_voice_fx({}) == {}
      and normalize_voice_fx({"her": {"preset": PRESET_NONE},
                              "others": None}) == {}
      and normalize_voice_fx({"her": "bad"}) == {})
check("配错档按「不加效果」处理（巨躯轰鸣不属于其他人那一档）",
      normalize_voice_fx({"others": {"preset": "giant_boom"}}) == {})
check("强度 0 = 效果关闭", not has_effect({"preset": "open_valley", "intensity": 0})
      and playback_plan({"preset": "open_valley", "intensity": 0}) is None)
_clamped_fx = normalize_voice_fx({"her": {"preset": "open_valley", "intensity": 999,
                                          "echo_wet": 5, "echo_count": 99}})
check("强度越界被夹取", _clamped_fx["her"]["intensity"] == MAX_INTENSITY, _clamped_fx)
check("高级参数越界被夹取（湿量封顶、拍数封顶）",
      _clamped_fx["her"]["echo_wet"] == PARAM_RANGES["echo_wet"][1]
      and _clamped_fx["her"]["echo_count"] == PARAM_RANGES["echo_count"][1],
      _clamped_fx)
check("缺省强度是 DEFAULT_INTENSITY",
      normalize_voice_fx({"her": {"preset": "giant_boom"}})["her"]["intensity"]
      == DEFAULT_INTENSITY)

# 预设表：唯一性、按档分组、参数键都在 PARAM_RANGES 里
check("预设键与显示名各自唯一",
      len(fx_mod.ALL_PRESET_KEYS) == len(set(fx_mod.ALL_PRESET_KEYS))
      and len({p.label for p in fx_mod.PRESETS}) == len(fx_mod.PRESETS))
check("两档都有「不加效果」且各自非空",
      PRESET_NONE in preset_keys_for(SLOT_HER)
      and PRESET_NONE in preset_keys_for(SLOT_OTHERS)
      and len(preset_keys_for(SLOT_HER)) > 1 and len(preset_keys_for(SLOT_OTHERS)) > 1)
check("预设参数键都在 PARAM_RANGES 里",
      all(key in PARAM_RANGES for preset in fx_mod.PRESETS
          for key in preset.params))
check("预设标注了它属于哪一档", all(preset.slots for preset in fx_mod.PRESETS))
check("两档的预设分得开（她那组不混进其他人那组）",
      not (set(preset_keys_for(SLOT_HER)) - {PRESET_NONE})
      & (set(preset_keys_for(SLOT_OTHERS)) - {PRESET_NONE}))

# 播放侧计划（零依赖那条路能做出什么）
_plan_valley = playback_plan({"preset": "open_valley", "intensity": 100})
check("旷野回声 → 三拍分拍回声",
      _plan_valley is not None and len(_plan_valley["taps"]) == 3, _plan_valley)
check("拍点次第衰减", _plan_valley and all(
    _plan_valley["taps"][i][1] > _plan_valley["taps"][i + 1][1]
    for i in range(len(_plan_valley["taps"]) - 1)), _plan_valley)
_plan_air = playback_plan({"preset": "shaking_air", "intensity": 100})
check("震空低鸣 → 缓抖（无回声拍）",
      _plan_air is not None and _plan_air["taps"] == () and _plan_air["tremor"],
      _plan_air)
check("近距震耳没有播放侧近似（纯渲染预设，缺解码器就退回原声）",
      playback_plan({"preset": "deafening_close", "intensity": 100}) is None)
check("没有效果就没有播放计划",
      playback_plan({}) is None and playback_plan(None) is None)

# ---- 校验器：写错的预设要能被作者看见（看原文，不看 normalize 后的样子） ----
_fx_config = schema.empty_scenario_config()
_fx_config["coupling_level"] = "solea"
_fx_config["chapters"] = [
    {"name": "开场", "start": True,
     "voice_fx": {"her": {"preset": "不存在的预设", "intensity": 500},
                  "others": {"preset": "giant_boom"}}},
    {"name": "终幕", "ending": True,
     "voice_fx": {"her": {"preset": "open_valley", "echo_wet": 9}}},
]
_fx_diags = validate_scenario_config(_fx_config)


def _expect_fx(level, path_part, label):
    hits = [d for d in _fx_diags if d.level == level and path_part in d.path]
    check(f"诊断：{label}", bool(hits),
          format_diagnostics([d for d in _fx_diags if "voice_fx" in d.path]))


_expect_fx("warning", "chapters[0].voice_fx.her.preset", "未知预设")
_expect_fx("warning", "chapters[0].voice_fx.others.preset", "预设配错档")
_expect_fx("warning", "chapters[0].voice_fx.her.intensity", "效果强度越界")
_expect_fx("warning", "chapters[1].voice_fx.her.echo_wet", "效果参数越界")
check("干净配置带效果：零 warning",
      not [d for d in validate_scenario_config({
          **schema.empty_scenario_config(),
          "chapters": [{"name": "开场", "start": True,
                        "voice_fx": {"her": {"preset": "open_valley",
                                             "intensity": 70}}}],
      }) if d.level == "warning"],
      format_diagnostics([d for d in validate_scenario_config({
          **schema.empty_scenario_config(),
          "chapters": [{"name": "开场", "start": True,
                        "voice_fx": {"her": {"preset": "open_valley"}}}],
      }) if d.level == "warning"]))

# ---- 接线字符串：坏了会静默失效，所以两端都要看 ----
_speech_src = open(os.path.join(_PROJECT_ROOT, "dungeon", "audio", "speech.py"),
                   encoding="utf-8").read()
_dlg_src = open(os.path.join(_PROJECT_ROOT, "ui", "scenario", "chapter_dlg.py"),
                encoding="utf-8").read()
_mgr_src = open(os.path.join(_PROJECT_ROOT, "ui", "scenario",
                             "chapter_trigger_mgr.py"), encoding="utf-8").read()
check("triggers.py 进章时应用语音效果", "_apply_chapter_voice_fx" in _trg_src)
check("triggers.py 进章/回放/跳转三处都带 voice_fx",
      '"voice_fx"' in _trg_src and "record.get(\"voice_fx\")" in _trg_src
      and "chapter_voice_fx" in _trg_src)
check("window/base.py 装配章节效果", "current_voice_fx" in _base_src
      and "effect=" in _base_src)
check("speech.py 按说话人分档（她那一档只给她）",
      "SLOT_HER" in _speech_src and "role_of" in _speech_src)
check("speech.py 三条引擎路径都在", "render" in _speech_src
      and "playback" in _speech_src and "_warn_fx_unavailable" in _speech_src)
check("编辑器暴露两档效果", "_voice_fx_from_ui" in _dlg_src
      and "语音效果" in _dlg_src)
check("章节列表显示语音效果列", "_format_voice_fx" in _mgr_src
      and "语音效果" in _mgr_src)

# ---- 整条链：假合成器 + 假音轨，从效果配置到分拍/调制/降级（不出声、不联网） ----
_FakeTrack.events = []
audio_mod.set_backends([_FakeTrack])
_fx_director = speech_mod.SpeechDirector(caster=_caster, volume=80,
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
    check("分拍音量按次第衰减（0.8 → 0.32 → 0.148）",
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
    audio_mod.set_backends(_real_backends)
    check("效果链收尾：语音线程已收工", _fx_director.worker_alive is False)

# 播放层本身：没有音频后端也必须能走完播放/停止/收尾（不能拖垮一局副本）
from dungeon import audio as audio_mod  # noqa: E402

check("当前环境有明确的音频后端结论", bool(audio_mod.backend_name()))
_probe_notes = []
_probe_resolver = {"tone.wav": os.path.join(_bgm_tmp, "tone.wav")}
open(_probe_resolver["tone.wav"], "wb").write(b"RIFF....WAVEfmt ")
_probe = audio_mod.BgmPlayer(resolver=_probe_resolver.get, logger=_probe_notes.append)
_probe.play({"path": "tone.wav", "volume": 40, "loop": True, "fade_seconds": 0})
_probe.play({"path": "不在的曲子.mp3"})
_probe.wait_idle()
_probe.stop()
_probe.wait_idle()
_probe.shutdown()
check("播放器在缺/坏音频文件时不抛异常且能收工",
      not _probe.playing and not _probe.worker_alive
      and any("音频文件不存在" in line for line in _probe_notes),
      _probe_notes)

# ---------------- 5. ScenarioRepo.save_config 诊断接入 ----------------
from persistence.scenario_repo import ScenarioRepo  # noqa: E402

tmp = tempfile.mkdtemp(prefix="scenario_schema_")
repo = ScenarioRepo(data_dir=tmp)
repo.save_config("_default", _bad_config())
check("save_config 记录诊断", has_errors(repo.last_diagnostics))
repo.save_config("_default", clean)
check("保存干净配置后诊断清空", not has_errors(repo.last_diagnostics))
check("加载后再校验（迁移后配置）",
      has_errors(validate_scenario_config(repo.load_config("_default")) or [])
      is False, "迁移后的干净配置仍有 error")

# 旧写法迁移：components 列表里的文本家族成员提升到 text_component
_old_style = dict(clean)
_old_style.pop("text_component", None)
_old_style["components"] = ["text", "attr_bar"]
repo.save_config("_default", _old_style)
_loaded_old = repo.load_config("_default")
check("迁移：旧写法文本组件提升到 text_component",
      _loaded_old.get("text_component") == "text"
      and "text" not in (_loaded_old.get("components") or []),
      _loaded_old)
_old_style["components"] = ["attr_bar", "text_nvl"]
repo.save_config("_default", _old_style)
_loaded_old = repo.load_config("_default")
check("迁移：列表里的 text_nvl 同样提升",
      _loaded_old.get("text_component") == "text_nvl"
      and _loaded_old.get("components") == ["attr_bar"],
      _loaded_old)

# ---------------- 6. 演化规则：配置矩阵/步长真的生效 ----------------
# 曾经 transition_matrix 与 section_steps 只写进 config.json、从未进运行时，
# 这里按「配置 → EvolutionRules」这条链路断言，防止再次脱钩。
import ast as _ast  # noqa: E402
from collections import Counter  # noqa: E402

from dungeon.models import DungeonTextType  # noqa: E402
from dungeon.rules import EvolutionRules  # noqa: E402

_real_config = json.load(  # 真实方案：作者能改到的就是这两个顶层字段
    open(os.path.join(_PROJECT_ROOT, "data", "packs", "scenarios", "_default",
                      "config.json"), encoding="utf-8"))
_real_rules = EvolutionRules(
    transition_matrix=_real_config.get("transition_matrix"),
    step_overrides=_real_config.get("section_steps"))
check("真实方案的转移矩阵进运行时",
      _real_rules.transition_matrix["background"]
      == _real_config["transition_matrix"]["background"])
check("真实方案的分节步长进运行时",
      _real_rules.step_overrides["action"] == _real_config["section_steps"]["action"])

_partial = EvolutionRules(transition_matrix={"background": {"action": 1.0}})
check("配置行整行替换：未写的列按 0",
      set(Counter(_partial.get_next_text_type(DungeonTextType.BACKGROUND).value
                  for _ in range(200))) == {"action"})
check("未配置的行沿用内置默认",
      _partial.transition_matrix["dialog"]
      == EvolutionRules.DEFAULT_TRANSITION_MATRIX["dialog"])

_dirty = EvolutionRules(
    transition_matrix={"background": {"action": "1.0", "typo": 5}, "dialog": None},
    step_overrides={"dialog": "0.25", "typo": 9, "background": -1})
check("字符串权重与步长被转成数字",
      _dirty.transition_matrix["background"]["action"] == 1.0
      and _dirty.step_overrides["dialog"] == 0.25)
check("未知键不进运行时（且 dialog 行保留默认）",
      "typo" not in _dirty.transition_matrix["background"]
      and _dirty.transition_matrix["dialog"]
      == EvolutionRules.DEFAULT_TRANSITION_MATRIX["dialog"])
check("非正步长被丢弃，回退默认步长",
      _dirty.step_overrides["background"] == DungeonTextType.BACKGROUND.step_value)

_zero = EvolutionRules(transition_matrix={"background": {"action": 0.0}})
check("整行权重为 0 时不崩、回退均匀随机",
      len(set(_zero.get_next_text_type(DungeonTextType.BACKGROUND)
              for _ in range(300))) == 5)

# 接线守卫：会话构造 EvolutionRules 时必须带上配置与衰减注入
_window_source = open(os.path.join(_PROJECT_ROOT, "dungeon", "window", "base.py"),
                      encoding="utf-8").read()
_wired = [kw.arg for node in _ast.walk(_ast.parse(_window_source))
          if isinstance(node, _ast.Call)
          and getattr(node.func, "id", "") == "EvolutionRules"
          for kw in node.keywords]
for _kw in ("transition_matrix", "step_overrides", "step_decay"):
    check(f"window/base.py 构造 EvolutionRules 时注入 {_kw}", _kw in _wired, _wired)

# ---------------- 结论 ----------------
_log.flush()
sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__
if failures:
    print(f"[check_scenario_schema] FAILED {len(failures)}/{total}: {failures}")
else:
    print(f"[check_scenario_schema] PASSED {total}/{total}")
print(f"[check_scenario_schema] report: {_report_path}")
sys.exit(1 if failures else 0)

