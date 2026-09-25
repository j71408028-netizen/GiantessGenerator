"""副本方案配置校验器：把引擎里的静默失败变成作者能看见的诊断。

对应 LingChat ``validate.rs`` 的定位——引擎遇到坏配置只会静默跳过/按 0 计算/
回退默认值，作者却以为配置生效了。这里在**编辑器保存时**与**副本启动前**各跑
一次 ``validate_scenario_config()``，产出结构化诊断：

- ``error``：方案无法按预期运行（悬空的跳转目标、没有起始章节、空的选项列表…），
  启动前发现会阻止进入；
- ``warning``：大概率是笔误/旧配置残留（未知条件键、废弃动作、失效的前置引用…），
  运行时有明确兜底但不符合作者预期；
- ``info``：值得知道的提示（无结局路径、旧版字段、未知顶层键…）。

校验对象既包括编辑器保存的原始配置，也包括 ``load_config()`` 迁移归一后的配置，
因此规则对缺失/类型不符的字段都要宽容（否则会把"未填"误报为崩溃）。
"""

import os
from dataclasses import dataclass

from dungeon import schema
from dungeon.actions import (ENDING_ACTION, VISUAL_FILTER_KEYS,
                             normalize_action_type)
from dungeon.chapters import CHAPTER_NONE, normalize_chapters
from dungeon.coupling import COUPLING_LEVELS, VELUM

LEVEL_LABELS = {"error": "错误", "warning": "警告", "info": "提示"}
_LEVEL_ORDER = {"error": 0, "warning": 1, "info": 2}

# 已退场的旧版动作：只为了把「运行时静默跳过」变成作者能看见的**专项**提示
# 而保留在这里。它们的职责早已由章节自身的属性承担（见 ``dungeon/chapters``），
# 运行时既不认识也不执行——**不要**把它们加回 ``dungeon/actions.py`` 的
# 运行时注册表。``sensitive`` 是 ``sensitivity`` 的早期写法，别名转换也已取消。
# 值：中文展示名，编辑器列表与诊断文案共用同一份。
LEGACY_ACTIONS = {
    "background": "背景切换",
    "sensitivity": "性格敏感化",
    "sensitive": "性格敏感化",
}


@dataclass(frozen=True)
class Diagnostic:
    """一条诊断：``level`` 三级；``path`` 为机器可读定位（如 ``triggers[2].action_data.chapter``）。"""

    level: str
    path: str
    message: str

    def __str__(self):
        return f"{LEVEL_LABELS.get(self.level, self.level)}·{self.path}：{self.message}"


def has_errors(diagnostics) -> bool:
    return any(d.level == "error" for d in (diagnostics or []))


def format_diagnostics(diagnostics, *, include_info: bool = True) -> str:
    """诊断列表 → 可直接展示的多行文本（error 在前）。"""
    diags = sorted((diagnostics or []),
                   key=lambda d: (_LEVEL_ORDER.get(d.level, 9), d.path))
    if not include_info:
        diags = [d for d in diags if d.level != "info"]
    if not diags:
        return "未发现问题。"
    return "\n".join(f"{index}. {diag}" for index, diag in enumerate(diags, 1))


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# ---------------- 主入口 ----------------

def validate_scenario_config(config, *, scenario_dir: str = None) -> list:
    """校验副本方案配置，返回诊断列表（为空表示配置干净）。

    ``scenario_dir``：方案目录，用于核对背景图/结局图标等资产是否真实存在；
    缺省时跳过资产存在性检查。
    """
    diags = []

    def add(level: str, path: str, message: str):
        diags.append(Diagnostic(level, path, message))

    config = config if isinstance(config, dict) else {}
    if not config:
        add("error", "", "方案配置为空")
        return diags

    _validate_top_level(config, add)
    chapters = normalize_chapters(config.get("chapters"))
    chapter_names = {c["name"] for c in chapters if c.get("name")}
    terminating = {c["name"] for c in chapters if c.get("ending")}
    trigger_names, custom_attrs = _collect_names(
        config.get("triggers"), config.get("evolution_attrs"))

    _validate_chapters(config.get("chapters"), chapters, chapter_names,
                       terminating, scenario_dir, add)
    _validate_triggers(config.get("triggers"), chapter_names, terminating,
                       trigger_names, custom_attrs, scenario_dir, add)

    # 起始章节：进入副本后唯一的自动进入点（window/triggers.py::_enter_start_chapter）
    starts = [c["name"] for c in chapters if c.get("start") and c.get("name")]
    if not starts and chapters:
        add("error", "chapters", "没有标记「起始章节」：进入副本后不会进入任何章节")
    elif len(starts) > 1:
        add("warning", "chapters", f"标记了 {len(starts)} 个起始章节（{', '.join(starts)}），"
                                   f"运行时只会进入第一个")

    # 结局可达性：既没有结束章节、也没有旧版结局触发器 = 永远无法正常收尾
    has_ending_trigger = any(
        normalize_action_type(t.get("action"),
                              t.get("action_data") if isinstance(t, dict) else None)
        == ENDING_ACTION
        for t in (config.get("triggers") or []) if isinstance(t, dict))
    if not terminating and not has_ending_trigger:
        add("info", "", "方案没有任何结局路径（无结束章节、无「结局」触发器）："
                        "只能通过退出结束，不产生结局结算与结局索引")

    return diags


# ---------------- 顶层 ----------------

def _validate_top_level(config: dict, add) -> None:
    known = schema.known_scenario_keys()
    for key in config:
        if key in known:
            continue
        if key in schema.DEPRECATED_SCENARIO_KEYS:
            add("warning", key, "旧版字段，加载时会被移除/迁移，可删除")
        else:
            add("info", key, "未知字段（会原样保留，可能是拼写错误或旧版残留）")

    if not isinstance(config.get("initial_prompt"), str):
        add("warning", "initial_prompt", "故事基调与设定必须是字符串")
    if config.get("coupling_level") not in COUPLING_LEVELS:
        add("warning", "coupling_level",
            f"耦合等级「{config.get('coupling_level')}」无效，运行时回退为默认值")
    title = config.get("protagonist_title", "")
    if not isinstance(title, str):
        add("warning", "protagonist_title", "主角称呼必须是字符串")
    elif title.strip() and config.get("coupling_level") == VELUM:
        add("info", "protagonist_title",
            "耦合等级为 Velum（读者模式）时对话段落不标注说话人，主角称呼不会使用")
    cost = config.get("entry_action_cost", 0)
    if not _is_number(cost) or cost < 0:
        add("warning", "entry_action_cost", "进入所需行动点数必须是非负数字")
    if config.get("text_component", schema.DEFAULT_TEXT_COMPONENT) not in schema.TEXT_COMPONENT_IDS:
        add("warning", "text_component",
            f"文本组件「{config.get('text_component')}」无效"
            f"（允许：{', '.join(schema.TEXT_COMPONENT_IDS)}），运行时回退为默认")
    # 旧写法残留：文本组件曾在 components 列表里声明（text_component 字段接管后忽略）
    stale_text = [c for c in (config.get("components") or [])
                  if isinstance(c, str) and c in schema.TEXT_COMPONENT_IDS]
    if stale_text:
        add("info", "components",
            f"文本组件已改由 text_component 字段三选一配置，"
            f"列表里的 {', '.join(stale_text)} 会被忽略，可删除")
    _validate_section_prompts(config.get("section_prompts"), add)
    _validate_section_steps(config.get("section_steps"), add)
    _validate_transition_matrix(config.get("transition_matrix"), add)
    _validate_evolution_attrs(config.get("evolution_attrs"), add)
    if not isinstance(config.get("chapters"), list):
        add("error", "chapters", "章节列表必须是数组")
    if not isinstance(config.get("triggers"), list):
        add("error", "triggers", "触发器列表必须是数组")


def _validate_section_prompts(section_prompts, add) -> None:
    if section_prompts in (None, ""):
        return
    if not isinstance(section_prompts, dict):
        add("warning", "section_prompts", "分节提示词必须是对象")
        return
    allowed = set(schema.TEXT_TYPE_KEYS) | {"global"}
    for key, value in section_prompts.items():
        if key not in allowed:
            add("warning", f"section_prompts.{key}",
                f"未知分节「{key}」（允许：{', '.join(sorted(allowed))}），运行时不会使用")
        elif not isinstance(value, str):
            add("warning", f"section_prompts.{key}", "分节提示词必须是字符串")


def _validate_section_steps(section_steps, add) -> None:
    if section_steps in (None, ""):
        return
    if not isinstance(section_steps, dict):
        add("warning", "section_steps", "分节步长必须是对象")
        return
    for key, value in section_steps.items():
        if key not in schema.TEXT_TYPE_KEYS:
            add("warning", f"section_steps.{key}",
                f"未知分节「{key}」（允许：{', '.join(schema.TEXT_TYPE_KEYS)}）")
        elif not _is_number(value) or value <= 0:
            add("warning", f"section_steps.{key}", "分节步长必须是正数")


def _validate_transition_matrix(matrix, add) -> None:
    if matrix is None or matrix == "":
        return  # 未配置：运行时用内置默认矩阵
    if not isinstance(matrix, dict):
        add("warning", "transition_matrix", "转移矩阵必须是对象（行=当前类型，列=下一类型）")
        return
    types = schema.TEXT_TYPE_KEYS
    for row_key, row in matrix.items():
        if row_key not in types:
            add("warning", f"transition_matrix.{row_key}",
                f"未知段落类型「{row_key}」（允许：{', '.join(types)}）")
            continue
        if row is None:
            add("warning", f"transition_matrix.{row_key}", "空行：该段落类型沿用内置默认行")
            continue
        if not isinstance(row, dict):
            add("warning", f"transition_matrix.{row_key}", "矩阵行必须是对象")
            continue
        total = 0.0
        for col_key, weight in row.items():
            if col_key not in types:
                add("warning", f"transition_matrix.{row_key}.{col_key}",
                    f"未知目标类型「{col_key}」")
            elif not _is_number(weight) or not 0.0 <= float(weight) <= 1.0:
                add("warning", f"transition_matrix.{row_key}.{col_key}",
                    f"权重 {weight!r} 必须在 0~1 之间")
            else:
                total += float(weight)
        if row and abs(total - 1.0) > 0.02:
            add("warning", f"transition_matrix.{row_key}",
                f"行权重之和为 {total:.3f}（应为 1.0 左右）")
    for missing in (t for t in types if t not in matrix):
        add("warning", f"transition_matrix.{missing}",
            "缺少该段落类型的行，运行时沿用内置默认行")


def _validate_evolution_attrs(attrs, add) -> None:
    if attrs in (None, ""):
        add("warning", "evolution_attrs", "缺少演化属性定义（至少要包含介入度/破坏性/总伤亡）")
        return
    if not isinstance(attrs, list):
        add("error", "evolution_attrs", "演化属性必须是数组")
        return
    present_types = set()
    custom_names = set()
    for index, attr in enumerate(attrs):
        path = f"evolution_attrs[{index}]"
        if not isinstance(attr, dict):
            add("error", path, "演化属性条目必须是对象")
            continue
        attr_type = attr.get("type")
        if attr_type not in schema.EVOLUTION_ATTR_TYPES:
            add("warning", path + ".type",
                f"未知属性类型「{attr_type}」（允许：{', '.join(schema.EVOLUTION_ATTR_TYPES)}）")
        present_types.add(attr_type)
        name = str(attr.get("name") or "").strip()
        if attr_type == "custom":
            if not name:
                add("warning", path + ".name", "自定义属性缺少名称，运行时不会演化任何属性")
            elif name in custom_names:
                add("warning", path + ".name", f"自定义属性「{name}」重名")
            else:
                custom_names.add(name)
        for numeric in ("rate", "init_value", "random_offset"):
            if numeric in attr and not _is_number(attr[numeric]):
                add("warning", f"{path}.{numeric}", f"{numeric} 必须是数字")
        if "rate" in attr and _is_number(attr["rate"]) and attr["rate"] < 0:
            add("warning", path + ".rate", "rate 为负数（步长系数）")
        if "display_state" in attr and attr["display_state"] not in schema.DISPLAY_STATES:
            add("warning", path + ".display_state",
                f"display_state「{attr['display_state']}」无效"
                f"（允许：{', '.join(schema.DISPLAY_STATES)}）")
    for missing in (t for t in schema.BUILTIN_EVOLUTION_ATTRS if t not in present_types):
        label = {"intrusion": "介入度", "destruction": "破坏性", "casualty": "总伤亡"}[missing]
        add("warning", "evolution_attrs",
            f"缺少内置演化属性「{label}」（伤亡结算/结局结算的代码假定它存在）")


# ---------------- 章节 / 触发器 / 条件 ----------------

def _collect_names(triggers, evolution_attrs):
    """返回（触发器名集合，自定义属性名集合）——条件键与前置引用的合法域。"""
    trigger_names = set()
    for trigger in (triggers or []):
        if isinstance(trigger, dict):
            name = str(trigger.get("name") or "").strip()
            if name:
                trigger_names.add(name)
    custom_attrs = set()
    for attr in (evolution_attrs or []):
        if isinstance(attr, dict) and attr.get("type") == "custom":
            name = str(attr.get("name") or "").strip()
            if name:
                custom_attrs.add(name)
    return trigger_names, custom_attrs


def _asset_exists(scenario_dir: str, rel_path: str) -> bool:
    if not rel_path:
        return False
    if os.path.isabs(rel_path):
        return os.path.isfile(rel_path)
    return os.path.isfile(os.path.join(scenario_dir, rel_path))


def _validate_chapters(chapters_raw, effective, chapter_names, terminating,
                       scenario_dir, add) -> None:
    """``chapters_raw`` 用于未知键检查；``effective`` 是 normalize 后的运行时视图。"""
    if not isinstance(chapters_raw, list):
        return  # 已在顶层报过
    seen = set()
    for index, chapter in enumerate(effective):
        path = f"chapters[{index}]"
        name = chapter.get("name") or ""
        if not name:
            add("warning", path, "章节缺少名称，加载时会被丢弃")
            continue
        if name in seen:
            add("warning", path + ".name",
                f"章节「{name}」重名，加载时会自动加序号（引用旧名的触发器会失效）")
        seen.add(name)
        target = str(chapter.get("overflow_target") or "").strip()
        if target and target not in chapter_names:
            add("error", f"{path}.overflow_target",
                f"超限跳转目标章节「{target}」不存在（空串=离开章节，是合法值）")
        background = chapter.get("background") or {}
        if isinstance(background, dict):
            image = str(background.get("image_path") or "").strip()
            if image and scenario_dir and not _asset_exists(scenario_dir, image):
                add("warning", f"{path}.background.image_path",
                    f"背景图不存在：{image}（运行时保持当前背景不变）")
            filter_key = background.get("filter_effect")
            if filter_key and filter_key not in VISUAL_FILTER_KEYS:
                add("warning", f"{path}.background.filter_effect",
                    f"未知滤镜「{filter_key}」（允许：{', '.join(VISUAL_FILTER_KEYS)}）")
        icon = str(chapter.get("icon_path") or "").strip()
        if icon and scenario_dir and not _asset_exists(scenario_dir, icon):
            add("warning", f"{path}.icon_path", f"结局图标不存在：{icon}")
        if name in terminating and not icon:
            add("info", f"{path}（{name}）",
                "结束章节未配置结局图标：达成时不算「重要结局」，不写结局索引")
    known = schema.field_map(schema.CHAPTER_FIELDS)
    for index, chapter in enumerate(chapters_raw or []):
        if not isinstance(chapter, dict):
            add("error", f"chapters[{index}]", "章节必须是对象")
            continue
        for key in chapter:
            if key != "name" and key not in known:
                add("info", f"chapters[{index}].{key}",
                    "未知章节字段（normalize 会原样保留，编辑器不识别）")


def _validate_triggers(triggers, chapter_names, terminating, trigger_names,
                       custom_attrs, scenario_dir, add) -> None:
    if not isinstance(triggers, list):
        return  # 已在顶层报过
    seen = set()
    for index, trigger in enumerate(triggers):
        path = f"triggers[{index}]"
        if not isinstance(trigger, dict):
            add("error", path, "触发器必须是对象")
            continue
        name = str(trigger.get("name") or "").strip()
        label = f"{path}（{name or '未命名'}）"
        if not name:
            add("warning", path + ".name", "触发器缺少名称，加载时会被重命名")
        elif name in seen:
            add("warning", path + ".name", f"触发器「{name}」重名，加载时会自动加序号")
        else:
            seen.add(name)
        for key in schema.DEPRECATED_TRIGGER_KEYS:
            if key in trigger:
                add("warning", path + "." + key, "旧版字段，加载时会被迁移/移除")
        scope = str(trigger.get("chapter") or "").strip()
        if scope and scope != CHAPTER_NONE and scope not in chapter_names:
            add("warning", path + ".chapter",
                f"所在章节「{scope}」不存在，这个触发器永远不会判定")
        for pre in (trigger.get("precondition_names") or []):
            if pre not in trigger_names:
                add("warning", path + ".precondition_names",
                    f"前置触发器「{pre}」不存在，条件永远无法满足")
        _validate_condition(trigger.get("condition"), path + ".condition",
                            add, custom_attrs, trigger_names)
        raw_data = trigger.get("action_data")
        action_data = raw_data if isinstance(raw_data, dict) else {}
        action_type = normalize_action_type(trigger.get("action"), action_data)
        action_path = path + ".action"
        in_terminating = scope not in ("", CHAPTER_NONE) and scope in terminating
        if action_type in LEGACY_ACTIONS:
            add("warning", action_path,
                f"旧版动作「{LEGACY_ACTIONS[action_type]}」运行时会直接跳过，"
                f"该职责已由章节属性承担，请在编辑器中删除或改写")
        elif action_type == ENDING_ACTION:
            add("warning", action_path,
                "「结局」触发器是旧配置：编辑器不再提供新建入口，运行时仍兼容执行，"
                "新方案请改用「结束章节」")
            if not str(action_data.get("name") or action_data.get("ending_text") or "").strip():
                add("warning", path + ".action_data.name", "结局触发器未填写结局名称，运行时会跳过")
            icon = str(action_data.get("icon_path") or "").strip()
            if icon and scenario_dir and not _asset_exists(scenario_dir, icon):
                add("warning", path + ".action_data.icon_path", f"结局图标不存在：{icon}")
        elif action_type == "goto":
            target = str(action_data.get("chapter") or "").strip()
            if target and target not in chapter_names:
                add("error", path + ".action_data.chapter",
                    f"跳转目标章节「{target}」不存在（空串=离开章节，是合法值）")
            if in_terminating:
                add("warning", label, "触发器位于结束章节作用域，运行时会跳过 goto")
        elif action_type == "option":
            options = action_data.get("options") or []
            if not isinstance(options, list) or not options:
                add("error", path + ".action_data.options",
                    "选项触发器没有配置任何选项，运行时会跳过")
            else:
                for opt_index, option in enumerate(options):
                    if not isinstance(option, dict) or not (
                            str(option.get("text") or option.get("prompt") or "").strip()):
                        add("warning", f"{path}.action_data.options[{opt_index}]",
                            "选项缺少 text/prompt，展示时回退为「选项 N」")
            if in_terminating:
                add("warning", label, "触发器位于结束章节作用域，运行时会跳过 option")
        elif action_type == "insert":
            if not str(action_data.get("text") or "").strip():
                add("error", path + ".action_data.text", "插入触发器缺少插入文本，运行时会跳过")
            text_type = action_data.get("text_type") or "background"
            if text_type not in schema.TEXT_TYPE_KEYS:
                add("warning", path + ".action_data.text_type",
                    f"未知段落类型「{text_type}」（允许：{', '.join(schema.TEXT_TYPE_KEYS)}）")
        elif action_type == "effect":
            filter_key = str(action_data.get("filter") or "").strip()
            if filter_key not in VISUAL_FILTER_KEYS:
                add("warning", path + ".action_data.filter",
                    f"未知滤镜「{filter_key}」（允许：{', '.join(VISUAL_FILTER_KEYS)}），运行时会跳过")
            duration = action_data.get("duration", 1)
            if not _is_number(duration) or duration < 1:
                add("warning", path + ".action_data.duration", "持续步数必须是 ≥1 的数字")
        elif action_type != "none":
            add("warning", action_path, f"未知动作类型「{action_type}」，运行时会跳过")


def _validate_condition(condition, path, add, custom_attrs, trigger_names) -> None:
    if condition in (None, ""):
        return
    if not isinstance(condition, dict):
        add("warning", path, "条件必须是对象（operator + rules）")
        return
    operator = condition.get("operator", "and")
    if operator not in schema.CONDITION_OPERATORS:
        add("warning", path + ".operator",
            f"逻辑组合符「{operator}」无效（允许：{', '.join(schema.CONDITION_OPERATORS)}），"
            f"运行时按 False 处理")
    rules = condition.get("rules")
    if rules in (None, []):
        return  # 空条件 = 恒真（无条件触发器，合法）
    if not isinstance(rules, list):
        add("error", path + ".rules", "条件规则必须是数组")
        return
    for index, rule in enumerate(rules):
        rule_path = f"{path}.rules[{index}]"
        if not isinstance(rule, dict):
            add("error", rule_path, "条件规则必须是对象")
            continue
        key = rule.get("key")
        comparator = rule.get("comparator", ">=")
        metric = rule.get("metric", "count")
        if comparator not in schema.CONDITION_COMPARATORS:
            add("warning", rule_path + ".comparator",
                f"未知比较符「{comparator}」（允许：{', '.join(schema.CONDITION_COMPARATORS)}），"
                f"运行时按 False 处理")
        key_kind = _condition_key_kind(key, custom_attrs)
        if key_kind is None:
            add("warning", rule_path + ".key",
                f"未知的条件键「{key}」：运行时永远按 0 计算"
                f"（内置键：{', '.join(schema.FIXED_CONDITION_KEYS)}；"
                f"自定义属性需先在演化属性里定义）")
        elif key_kind == "choice":
            target_trigger = str(key)[len(schema.CHOICE_CONDITION_PREFIX):]
            if target_trigger not in trigger_names:
                add("warning", rule_path + ".key",
                    f"「{key}」引用的触发器不存在（选项计数永远为空）")
            if metric not in schema.CHOICE_METRICS:
                add("warning", rule_path + ".metric",
                    f"选项计数不支持度量「{metric}」（允许：{', '.join(schema.CHOICE_METRICS)}）")
        elif key_kind == "series":
            if metric not in schema.SERIES_METRICS:
                add("warning", rule_path + ".metric",
                    f"伤亡序列不支持度量「{metric}」（允许：{', '.join(schema.SERIES_METRICS)}）")
        elif metric not in ("count", None):
            add("info", rule_path + ".metric",
                f"数值键「{key}」不支持度量（metric 会被忽略，直接比较属性值）")
        if key_kind in ("choice", "series") and rule.get("metric") == "trend":
            window = rule.get("window", 5)
            if not _is_number(window) or window < 2:
                add("warning", rule_path + ".window", "trend 度量的窗口必须是 ≥2 的数字")
        if not _is_number(rule.get("value", 0)) and key_kind != "choice":
            add("warning", rule_path + ".value",
                f"条件值 {rule.get('value')!r} 不是数字，数值比较恒为 False")


def _condition_key_kind(key, custom_attrs):
    """条件键分类：fixed / series / choice / custom / None（未知）。"""
    if not isinstance(key, str) or not key:
        return None
    if key in schema.FIXED_CONDITION_KEYS:
        return "fixed"
    if key == schema.SERIES_CONDITION_KEY:
        return "series"
    if key.startswith(schema.CHOICE_CONDITION_PREFIX):
        return "choice"
    if key in custom_attrs:
        return "custom"
    return None

