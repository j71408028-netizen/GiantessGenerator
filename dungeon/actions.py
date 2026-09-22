"""副本触发器动作类型的单一注册表。

注册表只登记**运行时真正支持的动作**——五种新版动作，外加只读兼容的结局：

- ``insert`` / ``option`` / ``effect`` / ``goto`` / ``none``：一次性的单步
  行为、跳转到章节、以及只标记条件成立的空动作。编辑器只提供这五种的
  新建入口。
- ``ending``：旧配置里的结局触发器，运行时仍执行；新方案改由「结束章节」
  承担（章节属性 ``ending=True``），编辑器不再提供新建入口。

已退场的旧版动作（背景切换 / 性格敏感化）**不在本注册表内**：它们的职责
已由章节自身的属性承担（见 ``dungeon.chapters``），运行时落入「未知动作」
的通用跳过路径。存量配置里的残留条目由校验器（``dungeon/validate.py`` 的
``LEGACY_ACTIONS``）在保存与启动前报告为专项警告，编辑器把它们显示为
「旧版·已失效」（无表单，提示删除）——让作者在校验阶段看见并处理，而不是
在运行时静默吞掉。

展示名、动作集合、归一化都从这里取，避免各处各写一份字符串常量。
"""

# 运行时支持的动作：单步行为 + 章节跳转 + 条件标记
NEW_ACTIONS = ("insert", "option", "effect", "goto", "none")

# 「结局」不再是触发器动作：改由「结束章节」承担（章节属性 ``ending=True``）。
# 运行时仍兼容旧配置中的 ``ending`` 触发器与旧回放记录；仅编辑器不再提供
# 新建入口（见 ui/scenario/trigger_dlg.py），运行时遭遇结束章节所在作用域的
# goto/option 触发器时会跳过。
ENDING_ACTION = "ending"

ACTION_LABELS = {
    "insert": "插入文本",
    "option": "弹出选项",
    "effect": "短暂视效",
    "goto": "跳转章节",
    "ending": "结局",
    "none": "条件标记",
}

# 不产生实际动作、仅标记条件已成立的动作
EMPTY_ACTIONS = (None, "", "none")

# 短暂视效可选滤镜：本仓唯一的滤镜清单（键 + 展示名）。
# background.py 在导入时校验其 PIL 映射的键与这里一致（不一致直接报错），
# 校验器（dungeon/validate.py）也按这里的键检查配置。
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
    """归一化动作类型：处理缺失的动作字段。

    早前的配置把动作类型写在 ``action_data["type"]`` 里，动作类型本身为空。
    这里统一回落到那里读取。

    注意：**不做旧动作的别名转换**。``sensitive``/``background`` 这类已退场
    的类型原样返回，运行时跳过，并由校验器报警告提示作者删除。
    """
    if action_type is None or action_type == "":
        return (action_data or {}).get("type") or "none"
    return action_type


def action_label(action_type) -> str:
    """动作类型的中文展示名，未知类型原样返回。"""
    if action_type is None or action_type == "":
        action_type = "none"
    return ACTION_LABELS.get(action_type, action_type)
