"""副本方案（Scenario）配置的字段定义 —— 单一真相源。

S1.5 之前，方案结构散落在三处：``scenario_repo._migrate`` / ``chapters.normalize_*``
的补全逻辑、编辑器 ``ui/scenario/`` 的表单、``window/engine.py`` 的读取——互不同步，
只能靠静默兜底。本模块把字段、类型、默认值、合法取值集中声明，供：

1. ``dungeon/validate.py``：结构化校验（编辑器保存时与副本启动前各跑一次）；
2. ``ScenarioRepo.empty_scenario_config()``：空方案模板（替代仓库内手写字典）；
3. 后续编辑器表单按字段声明渲染。

``chapters.normalize_*`` / ``_migrate`` 目前保持手写（行为经过实战），由
``scripts/check_scenario_schema.py`` 守卫「schema 声明 ↔ normalize 产出」的字段
一致性，防止再次漂移。
"""

from dataclasses import dataclass
from typing import Any

from dungeon.actions import VISUAL_FILTER_KEYS
from dungeon.coupling import COUPLING_LEVELS, DEFAULT_COUPLING_LEVEL
from dungeon.models import DungeonTextType

TEXT_TYPE_KEYS = tuple(t.value for t in DungeonTextType)


@dataclass(frozen=True)
class FieldSpec:
    """单个配置字段的声明。``kind`` 取值：str/int/float/bool/dict/list/enum/color/text。"""

    key: str
    kind: str
    label: str = ""
    default: Any = None
    required: bool = False
    choices: tuple = ()
    note: str = ""


# ---------------- 顶层字段 ----------------

SCENARIO_FIELDS = (
    FieldSpec("initial_prompt", "text", "故事基调与设定", "", note="留空表示无开局设定"),
    FieldSpec("coupling_level", "enum", "耦合等级", DEFAULT_COUPLING_LEVEL, choices=COUPLING_LEVELS),
    FieldSpec("entry_action_cost", "int", "进入所需行动点数", 0, note="0 表示免费"),
    FieldSpec("section_prompts", "dict", "分节提示词"),
    FieldSpec("section_steps", "dict", "分节步长", None,
              note="缺项沿用该段落类型的默认步进值"),
    FieldSpec("transition_matrix", "dict", "段落类型转移矩阵", None,
              note="缺整行时沿用内置默认行；出现的行按配置整行替换"),
    FieldSpec("evolution_attrs", "list", "演化属性"),
    FieldSpec("chapters", "list", "章节"),
    FieldSpec("triggers", "list", "触发器"),
    FieldSpec("components", "list", "启用的显示组件"),
    FieldSpec("components_params", "dict", "显示组件参数"),
)

# 迁移前遗留、``_migrate`` 会移除的顶层键（读到即提示作者清理）
DEPRECATED_SCENARIO_KEYS = ("custom_attrs", "custom_attrs_def", "view_mode")

# ---------------- 章节字段 ----------------
# 与 chapters.normalize_chapter 的产出保持一致（守卫脚本校验）

CHAPTER_FIELDS = (
    FieldSpec("name", "str", "章节名", "", required=True),
    FieldSpec("color", "color", "编辑器配色"),
    FieldSpec("start", "bool", "起始章节", False),
    FieldSpec("ending", "bool", "结束章节", False),
    FieldSpec("note", "str", "备注", ""),
    FieldSpec("max_paragraphs", "int", "最大段落数", 99),
    FieldSpec("overflow_target", "str", "超限跳转目标", ""),
    FieldSpec("intrusion_delta", "float", "结束结算：介入度增量", 0),
    FieldSpec("destruction_delta", "float", "结束结算：破坏性增量", 0),
    FieldSpec("casualty_step", "float", "结束结算：伤亡步进", 0),
    FieldSpec("action_points_refund", "int", "结束结算：行动点返还", 0),
    FieldSpec("custom_deltas", "dict", "结束结算：自定义属性增量", None),
    FieldSpec("icon_path", "str", "结局图标（相对方案目录）", ""),
    FieldSpec("background", "dict", "章节背景"),
    FieldSpec("sensitivity", "list", "章节持续敏感效果"),
)

BACKGROUND_FIELDS = (
    FieldSpec("image_path", "str", "背景图（相对方案目录）", ""),
    FieldSpec("smooth_transition", "bool", "平滑切换", True),
    FieldSpec("filter_effect", "enum", "滤镜", None, choices=("",) + VISUAL_FILTER_KEYS),
)

SENSITIVITY_FIELDS = (
    FieldSpec("attr", "str", "作用属性"),
    FieldSpec("strength", "float", "强度", 1.0),
    FieldSpec("objective", "float", "客观影响", 0.0),
)

# ---------------- 触发器字段 ----------------

TRIGGER_FIELDS = (
    FieldSpec("name", "str", "触发器名", "", required=True),
    FieldSpec("chapter", "str", "所在章节", "", note="空=任意章节，__none__=无章节"),
    FieldSpec("precondition_names", "list", "前置触发器"),
    FieldSpec("condition", "dict", "触发条件"),
    FieldSpec("repeatable", "bool", "可重复触发", True),
    FieldSpec("action", "enum", "动作"),
    FieldSpec("action_data", "dict", "动作参数"),
)

# 迁移前遗留的触发器键（``_migrate`` 会转换/移除）
DEPRECATED_TRIGGER_KEYS = ("id", "precondition_ids")

# ---------------- 条件规则 ----------------

CONDITION_OPERATORS = ("and", "or")
CONDITION_COMPARATORS = (">=", "<=", ">", "<", "==", "!=")
# 数值直读键（与 rules.py TriggerRules.evaluate 的取值一致）
FIXED_CONDITION_KEYS = ("介入度", "破坏性", "总伤亡", "总计数", "间隔计数", "节内计数")
SERIES_CONDITION_KEY = "伤亡数组"
CHOICE_CONDITION_PREFIX = "选择:"
CHOICE_METRICS = ("count", "ratio", "trend", "last")
SERIES_METRICS = ("avg", "trend", "last", "count")

# ---------------- 演化属性 ----------------

EVOLUTION_ATTR_TYPES = ("intrusion", "destruction", "casualty", "custom")
DISPLAY_STATES = ("show", "collapse")
BUILTIN_EVOLUTION_ATTRS = ("intrusion", "destruction", "casualty")


# ---------------- 访问器与模板 ----------------

def field_map(fields) -> dict:
    """字段声明 → {key: FieldSpec}。"""
    return {spec.key: spec for spec in fields}


def known_scenario_keys() -> frozenset:
    """合法顶层键（声明字段 + 触发器/章节容器内已知的衍生键）。"""
    return frozenset(spec.key for spec in SCENARIO_FIELDS)


def empty_scenario_config() -> dict:
    """空方案模板。字段与取值以本模块声明为准（单一真相源）。"""
    return {
        "initial_prompt": "",
        "coupling_level": DEFAULT_COUPLING_LEVEL,
        "entry_action_cost": 0,
        "section_prompts": {key: "" for key in TEXT_TYPE_KEYS},
        "evolution_attrs": [
            {"type": "intrusion", "name": "介入度", "display_state": "collapse"},
            {"type": "destruction", "name": "破坏性", "display_state": "collapse"},
            {"type": "casualty", "name": "总伤亡", "display_state": "collapse"},
        ],
        "chapters": [],
        "triggers": [],
    }
