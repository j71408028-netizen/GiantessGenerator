"""S2 自检：schema 单一真相源 + 校验器（无 GUI，不碰真实 data/）。

用法：``python scripts/check_scenario_schema.py``
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
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_report_path = os.path.join(tempfile.mkdtemp(prefix="scenario_schema_"), "report.txt")
_log = open(_report_path, "w", encoding="utf-8")
sys.stdout = _log
sys.stderr = _log

failures = []
total = 0


def check(name, ok, extra=""):
    global total
    total += 1
    print(("  OK   " if ok else "  FAIL ") + name + (f"  <{extra}>" if extra and not ok else ""))
    if not ok:
        failures.append(name)


from dungeon import schema  # noqa: E402
from dungeon.chapters import normalize_chapter  # noqa: E402
from dungeon.validate import (  # noqa: E402
    format_diagnostics, has_errors, validate_scenario_config)

# ---------------- 1. 空方案模板 golden 对比 ----------------
GOLDEN_EMPTY = {
    "initial_prompt": "",
    "coupling_level": "velum",
    "protagonist_title": "",
    "entry_action_cost": 0,
    "section_prompts": {"background": "", "branch": "", "dialog": "",
                        "interaction": "", "action": ""},
    "evolution_attrs": [
        {"type": "intrusion", "name": "介入度", "display_state": "collapse"},
        {"type": "destruction", "name": "破坏性", "display_state": "collapse"},
        {"type": "casualty", "name": "总伤亡", "display_state": "collapse"},
    ],
    "chapters": [],
    "triggers": [],
}
check("空方案模板与旧版 _empty_config 一致",
      schema.empty_scenario_config() == GOLDEN_EMPTY)

# ---------------- 2. schema ↔ normalize 一致性 ----------------
normalized = normalize_chapter({"name": "示例"}, 0)
declared = set(schema.field_map(schema.CHAPTER_FIELDS))
check("normalize_chapter 产出的字段都被 schema 声明",
      set(normalized) <= declared, set(normalized) - declared)
check("schema 声明的必填字段存在", schema.field_map(schema.CHAPTER_FIELDS)["name"].required)

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

# ---------------- 6. 演化规则：配置矩阵/步长真的生效 ----------------
# 曾经 transition_matrix 与 section_steps 只写进 config.json、从未进运行时，
# 这里按「配置 → EvolutionRules」这条链路断言，防止再次脱钩。
import ast as _ast  # noqa: E402
from collections import Counter  # noqa: E402

from dungeon.models import DungeonTextType  # noqa: E402
from dungeon.rules import EvolutionRules  # noqa: E402

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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

