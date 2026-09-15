"""副本触发器动作类型的单一注册表。

动作分成两组：

- 新版动作（``NEW_ACTIONS``）：一次性的单步行为，或者跳转到章节；
- 旧版动作（``LEGACY_ACTIONS``）：背景切换与性格敏感化。这两项已经是
  章节自身的属性（见 ``dungeon.chapters``），**不再兼容**：读到带这两类
  动作的旧触发器时运行时直接跳过（``is_legacy_action``），编辑器也不再
  提供它们的入口，旧配置里的这些触发器可由用户在编辑器里删除。

展示名、动作集合、别名归一化都从这里取，避免各处各写一份字符串常量。
"""

# 新版动作：单步行为 + 章节跳转
NEW_ACTIONS = ("insert", "option", "effect", "goto", "ending", "none")
# 旧版动作：已由章节属性承担，读到即跳过（仅为识别保留）
LEGACY_ACTIONS = ("background", "sensitivity")
ALL_ACTIONS = NEW_ACTIONS + LEGACY_ACTIONS

ACTION_LABELS = {
    "insert": "插入文本",
    "option": "弹出选项",
    "effect": "短暂视效",
    "goto": "跳转章节",
    "ending": "结局",
    "none": "条件标记",
    "background": "背景切换",
    "sensitivity": "性格敏感化",
}

# 不产生实际动作、仅标记条件已成立的动作
EMPTY_ACTIONS = (None, "", "none")

# 旧配置里的动作别名
_ALIASES = {"sensitive": "sensitivity"}

# 短暂视效可选滤镜：必须与 DungeonBackground.apply_filter 的键一致
# （dungeon/background.py，新增滤镜时两边同步）
VISUAL_FILTERS = (
    ("blur", "模糊"),
    ("smooth", "柔化"),
    ("smooth_more", "强柔化"),
    ("sharpen", "锐化"),
    ("detail", "细节增强"),
    ("edge_enhance", "描边"),
    ("edge_enhance_more", "重描边"),
    ("emboss", "浮雕"),
    ("contour", "轮廓"),
    ("find_edges", "提取边缘"),
)
VISUAL_FILTER_KEYS = tuple(key for key, _label in VISUAL_FILTERS)


def normalize_action_type(action_type, action_data: dict = None) -> str:
    """归一化动作类型：处理旧别名与缺失值。

    早前的配置把动作类型写在 ``action_data["type"]`` 里，动作类型本身为空；
    也出现过 ``sensitive`` 这种早期写法。统一在此转换。
    """
    if action_type in _ALIASES:
        return _ALIASES[action_type]
    if action_type is None or action_type == "":
        fallback = (action_data or {}).get("type")
        if fallback in _ALIASES:
            return _ALIASES[fallback]
        return fallback or "none"
    return action_type


def is_legacy_action(action_type, action_data: dict = None) -> bool:
    """是否为旧版动作（背景切换 / 性格敏感化）。

    这两类动作已由章节属性承担，运行时读到就跳过，不做任何兼容处理。
    """
    return normalize_action_type(action_type, action_data) in LEGACY_ACTIONS


def action_label(action_type) -> str:
    """动作类型的中文展示名，未知类型原样返回。"""
    if action_type is None or action_type == "":
        action_type = "none"
    return ACTION_LABELS.get(action_type, action_type)
