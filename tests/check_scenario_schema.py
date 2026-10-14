"""S2 自检：schema 单一真相源 + 校验器（无 GUI，不碰真实 data/）。

用法：``python tests/check_scenario_schema.py``
输出：控制台一行 ASCII 结论 + UTF-8 报告文件路径。

覆盖：
1. 空方案模板的**键集合**稳定（不再钉默认值，默认值由 normalize/校验器覆盖）；
2. ``schema`` ↔ ``normalize`` 一致性：章节 / 对话语音 / 背景音乐 / 语音效果；
3. 滤镜键单一出处（``actions.VISUAL_FILTERS`` ↔ ``background._PIL_FILTERS``）；
4. 校验器规则用例（合成坏配置的每类诊断 + 干净配置零误报）；
5. ``ScenarioRepo.save_config`` 诊断接入与旧写法迁移；
6. 演化规则的配置链路：``transition_matrix`` / ``section_steps`` 真的进
   ``EvolutionRules``（AST 认注入，不再逐字比对源码）。

音频端到端链（说话人分档 / 队列 / 三条引擎路径 / 降级 / BGM 播放收尾）已拆到
``tests/check_audio_chain.py``，本文件只留 schema 与校验器。
"""

import json
import os
import sys
import tempfile

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
from dungeon.audio.chapters import (DEFAULT_BGM_FADE_SECONDS, DEFAULT_BGM_LEVEL,  # noqa: E402
                              MAX_BGM_LEVEL, normalize_bgm, normalize_chapter)
from dungeon.validate import (  # noqa: E402
    format_diagnostics, has_errors, validate_scenario_config)

# ---------------- 1. 空方案模板键集合 ----------------
# 只钉「有哪些键」，不钉默认值：默认音色 / 默认档位改了不该让门禁红，字段少了或
# 多了才该红。默认值的行为由下面的 normalize / 校验器覆盖。
_GOLDEN_EMPTY_KEYS = {
    "initial_prompt", "coupling_level", "protagonist_title", "text_component",
    "entry_action_cost", "ending_policy", "section_prompts", "evolution_attrs",
    "chapters", "triggers", "voice",
}
_empty = schema.empty_scenario_config()
check("空方案模板的顶层键集合稳定",
      set(_empty) == _GOLDEN_EMPTY_KEYS,
      set(_empty) ^ _GOLDEN_EMPTY_KEYS)
check("空方案模板的顶层键都在 schema 声明内",
      set(_empty) <= set(schema.known_scenario_keys()),
      set(_empty) - set(schema.known_scenario_keys()))
check("section_prompts 的键与段落类型一致",
      set(_empty["section_prompts"]) == set(schema.TEXT_TYPE_KEYS),
      set(_empty["section_prompts"]) ^ set(schema.TEXT_TYPE_KEYS))
check("voice 的键与 schema 声明一致",
      set(_empty["voice"]) == set(schema.field_map(schema.VOICE_FIELDS)),
      set(_empty["voice"]) ^ set(schema.field_map(schema.VOICE_FIELDS)))
check("内置演化属性三项齐（type 有序）",
      [attr["type"] for attr in _empty["evolution_attrs"]]
      == list(schema.BUILTIN_EVOLUTION_ATTRS),
      [attr["type"] for attr in _empty["evolution_attrs"]])

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
from dungeon.audio.speech import MAX_VOICE_LEVEL, normalize_voice  # noqa: E402

check("方案级对话语音字段已在 schema 声明",
      "voice" in schema.known_scenario_keys())
check("voice 字段声明与 normalize 产出的键一致",
      set(normalize_voice(None)) == set(schema.field_map(schema.VOICE_FIELDS)))
check("语速非法值回退常速",
      normalize_voice({"rate": "快一点"})["rate"] == speech_mod.DEFAULT_RATE
      and normalize_voice({"rate": "+15%"})["rate"] == "+15%")
check("音量档位越界被夹取",
      normalize_voice({"level": 9})["level"] == MAX_VOICE_LEVEL
      and normalize_voice({"level": -3})["level"] == 0)
check("旧 0-100 volume 迁移成档位（存盘只留 level）",
      normalize_voice({"volume": 90})["level"] == 4
      and "volume" not in normalize_voice({"volume": 90}))
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
    "path": "audio/a.ogg", "level": DEFAULT_BGM_LEVEL, "loop": True,
    "fade_seconds": DEFAULT_BGM_FADE_SECONDS}, normalize_bgm({"path": "audio/a.ogg"}))
_clamped = normalize_bgm({"path": "a.mp3", "level": MAX_BGM_LEVEL + 400,
                          "loop": False, "fade_seconds": -1})
check("bgm 越界值被夹取（档位封顶、负淡入淡出归零）",
      _clamped["level"] == MAX_BGM_LEVEL and _clamped["loop"] is False
      and _clamped["fade_seconds"] == 0.0, _clamped)
check("bgm 旧 volume 迁移成档位（存盘只留 level）",
      normalize_bgm({"path": "a.mp3", "volume": 70})["level"] == 3
      and "volume" not in normalize_bgm({"path": "a.mp3", "volume": 70}))

_bgm_config = schema.empty_scenario_config()
_bgm_config["chapters"] = [
    {"name": "开场", "start": True,
     "bgm": {"path": "audio/nope.mp3", "level": 9, "fade_seconds": "x"}},
    {"name": "终幕", "ending": True, "bgm": {"path": "audio/a.xyz"}},
]
_bgm_diags = validate_scenario_config(_bgm_config, scenario_dir=_bgm_tmp)


def _expect_bgm(level, path_part, label):
    hits = [d for d in _bgm_diags if d.level == level and path_part in d.path]
    check(f"诊断：{label}", bool(hits),
          format_diagnostics([d for d in _bgm_diags if "bgm" in d.path],
                             include_info=True))


_expect_bgm("warning", "chapters[0].bgm.path", "背景音乐文件不存在")
_expect_bgm("warning", "chapters[0].bgm.level", "bgm 音量档位越界")
_expect_bgm("warning", "chapters[0].bgm.fade_seconds", "bgm 淡入淡出非数字")
_expect_bgm("warning", "chapters[1].bgm.path", "bgm 后缀不是常见格式")
check("bgm 章节没有 scenarios_dir 时不报错路径存在性",
      not [d for d in validate_scenario_config(_bgm_config)
           if "bgm.path" in d.path and "不存在" in d.message])

# 旧键 volume：报 info（可迁移的历史写法，不是笔误）
_legacy_bgm_config = schema.empty_scenario_config()
_legacy_bgm_config["chapters"] = [
    {"name": "旧章", "start": True, "bgm": {"path": "a.mp3", "volume": 70}},
    {"name": "旧终", "ending": True},
]
_legacy_diags = validate_scenario_config(_legacy_bgm_config)
check("旧键 volume 报 info 而非错误（提示已改为档位）",
      any(d.level == "info" and d.path.endswith(".bgm.volume")
          for d in _legacy_diags)
      and not [d for d in _legacy_diags if d.level == "error"],
      format_diagnostics(_legacy_diags, include_info=True))

# 存一轮再读：迁移不得丢字段，且坏值体现在读到的数据里（已夹取）
from persistence.scenario_repo import ScenarioRepo as _Repo  # noqa: E402
_bgm_repo = _Repo(data_dir=os.path.join(_bgm_tmp, "data"))
_bgm_repo.save_config("_bgm", _bgm_config)
_bgm_loaded = _bgm_repo.load_config("_bgm")
check("存读一轮：章节 bgm 未丢失且坏值已修正",
      _bgm_loaded["chapters"][0]["bgm"]["path"] == "audio/nope.mp3"
      and _bgm_loaded["chapters"][0]["bgm"]["level"] == MAX_BGM_LEVEL
      and _bgm_loaded["chapters"][1]["bgm"]["loop"] is True,
      _bgm_loaded["chapters"])

# ---------------- 4c. 章节对话语音效果（她 / 其他人两档） ----------------
# 这里只盯 schema ↔ normalize ↔ 校验器；「谁用哪一档 → 哪条引擎 → 降级」的端到端
# 链在 tests/check_audio_chain.py。
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

# ---- 端到端音频链已拆到 tests/check_audio_chain.py ----
#
# 原先这里还有一串「两端都要看」的源码串断言（base.py / triggers.py / speech.py /
# 编辑器 UI 里出现过某个方法名或字符串），外加一整套假合成器 + 替身后端链。前者守
# 的是当时的写法，后者属于音频行为而非 schema——所以接线断言删掉，行为链搬走。

# ---------------- 4d. 固定文本章节（不走 AI 生成） ----------------
# 「怎么解析」归 tests/check_script_text.py；这里只盯 schema ↔ normalize ↔ 校验器。
from dungeon.audio.chapters import (chapter_fixed_text,  # noqa: E402
                                    normalize_fixed_text)

check("fixed_text 字段已在章节 schema 声明", "fixed_text" in declared
      and "fixed_text" in normalized, set(normalized) - declared)
check("未配固定文本归一为空 dict（运行时按 AI 生成处理）",
      normalize_fixed_text(None) == {} and normalize_fixed_text({}) == {}
      and normalize_fixed_text({"path": "  "}) == {})
check("fixed_text 路径归一（去空白、反斜杠转正斜杠）",
      normalize_fixed_text({"path": " texts\\a.txt "}) == {"path": "texts/a.txt"})
check("fixed_text 键与 schema 声明一致",
      set(normalize_fixed_text({"path": "x.txt"}))
      == set(schema.field_map(schema.FIXED_TEXT_FIELDS)),
      set(normalize_fixed_text({"path": "x.txt"})))
check("chapter_fixed_text 读章节的固定文本",
      chapter_fixed_text({"fixed_text": {"path": "p.txt"}}) == {"path": "p.txt"}
      and chapter_fixed_text({}) == {} and chapter_fixed_text(None) == {})

_ft_tmp = tempfile.mkdtemp(prefix="scenario_fixed_")
os.makedirs(os.path.join(_ft_tmp, "texts"), exist_ok=True)
with open(os.path.join(_ft_tmp, "texts", "good.txt"), "w", encoding="utf-8") as _fh:
    _fh.write("\n".join([
        "[对话 +1]", "她低下头。",
        "", "[互动 破坏性-1]", "她的手掌轻轻落下。",
        "", "[环境]", "远处有雾。",              # 只有类型：不演化
        "", "[+1]", "什么都没写。",               # 只有方向：不演化
        "", "[对话 幽灵+1]", "引用了未定义属性。",  # 未知属性
        "", "[对话 乱写]", "无法识别的记号。",      # 无法识别
    ]))
with open(os.path.join(_ft_tmp, "texts", "solo.txt"), "w", encoding="utf-8") as _fh:
    _fh.write("[对话 +1]\n她开口了。")

_ft_config = schema.empty_scenario_config()
_ft_config["chapters"] = [
    {"name": "开场", "start": True, "overflow_target": "终幕",
     "fixed_text": {"path": "texts/good.txt"}, "max_paragraphs": 3},
    {"name": "终幕", "ending": True,
     "fixed_text": {"path": "texts/missing.txt"}},
]
_ft_config["triggers"] = [
    {"name": "t_insert", "chapter": "开场", "action": "insert",
     "action_data": {"text": "插入一段"}},
]
_ft_diags = validate_scenario_config(_ft_config, scenario_dir=_ft_tmp)


def _expect_ft(level, path_part, label):
    hits = [d for d in _ft_diags if d.level == level and path_part in d.path]
    check(f"诊断：{label}", bool(hits),
          format_diagnostics([d for d in _ft_diags if "fixed_text" in d.path
                              or "max_paragraphs" in d.path], include_info=True))


_expect_ft("error", "chapters[1].fixed_text.path", "固定文本文件不存在")
_expect_ft("warning", "chapters[0].fixed_text.path", "指令行有无法识别的记号")
_expect_ft("warning", "chapters[0].max_paragraphs", "最大段落数小于逻辑段落数")
check("诊断：指令行引用未定义属性",
      any(d.level == "warning" and "未定义的演化属性" in d.message
          and "幽灵" in d.message for d in _ft_diags),
      format_diagnostics([d for d in _ft_diags if "fixed_text" in d.path],
                         include_info=True))
check("诊断：半写指令（缺类型 / 缺方向）",
      any(d.level == "warning" and "缺少段落类型或演化方向" in d.message
          for d in _ft_diags),
      format_diagnostics([d for d in _ft_diags if "fixed_text" in d.path],
                         include_info=True))
check("诊断：固定文本章节内 insert 被提示会跳过",
      any(d.level == "warning" and d.path == "triggers[0].action"
          and "固定文本章节" in d.message for d in _ft_diags),
      format_diagnostics([d for d in _ft_diags if "triggers" in d.path]))

# 播完没有去向（非结束章节、也没配超限跳转）→ info 提示「播完即离开章节」
_ft_info = schema.empty_scenario_config()
_ft_info["chapters"] = [{"name": "独幕", "start": True,
                         "fixed_text": {"path": "texts/solo.txt"}}]
_ft_info_diags = validate_scenario_config(_ft_info, scenario_dir=_ft_tmp)
check("诊断：固定文本播完无去向 → info",
      any(d.level == "info" and "播完即离开章节" in d.message
          for d in _ft_info_diags),
      format_diagnostics(_ft_info_diags, include_info=True))
check("干净的固定文本章节没有 error/warning",
      not has_errors(_ft_info_diags)
      and not any(d.level == "warning" for d in _ft_info_diags),
      format_diagnostics(_ft_info_diags, include_info=True))
check("无 scenario_dir 时不核对文件存在性（未落盘的方案）",
      not [d for d in validate_scenario_config(_ft_config)
           if "fixed_text.path" in d.path and "不存在" in d.message])

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

