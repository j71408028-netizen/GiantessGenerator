"""副本触发器动作类型的单一注册表。

注册表只登记**运行时真正支持的动作**——七种新版动作，外加只读兼容的结局：

- ``insert`` / ``option`` / ``effect`` / ``fx`` / ``goto`` / ``mini_game`` /
  ``none``：一次性的单步行为、章节效果短暂覆盖、窗口视效、跳转到章节、
  小游戏挑战，以及只标记条件成立的空动作。编辑器为这七种提供新建入口。
- ``ending``：旧配置里的结局触发器，**运行时已不再执行**（仅回放兼容）；
  新方案改由「结束章节」承担（章节属性 ``ending=True``），编辑器不再提供
  新建入口，并把存量条目显示为「旧版·已失效」。

已退场的旧版动作（背景切换 / 性格敏感化）**不在本注册表内**：它们的职责
已由章节自身的属性承担（见 ``dungeon.audio.chapters``），运行时落入「未知动作」
的通用跳过路径。存量配置里的残留条目由校验器（``dungeon/validate.py`` 的
``LEGACY_ACTIONS``）在保存与启动前报告为专项警告，编辑器把它们显示为
「旧版·已失效」（无表单，提示删除）——让作者在校验阶段看见并处理，而不是
在运行时静默吞掉。

展示名、动作集合、归一化都从这里取，避免各处各写一份字符串常量。
"""

# 运行时支持的动作：单步行为 + 章节跳转 + 小游戏挑战 + 条件标记
NEW_ACTIONS = ("insert", "option", "effect", "fx", "goto", "mini_game", "none")

# 「结局」触发器动作已删除：新方案由「结束章节」承担（章节属性 ``ending=True``），
# 结局结算管线（``pending_ending`` + EndingHandler）本身保留。运行时不再执行
# ending 触发器（与 background/sensitivity 同走「未知动作」跳过路径），仅回放
# 兼容——旧存档里的结局触发记录仍按记录复现；编辑器把它显示为「旧版·已失效」，
# 校验器（dungeon/validate.py 的 LEGACY_ACTIONS）在保存与启动前报告专项警告。
ENDING_ACTION = "ending"

ACTION_LABELS = {
    "insert": "插入文本",
    "option": "弹出选项",
    "effect": "效果覆盖",
    "fx": "窗口视效",
    "goto": "跳转章节",
    "mini_game": "小游戏挑战",
    "ending": "结局",
    "none": "条件标记",
}

# 不产生实际动作、仅标记条件已成立的动作
EMPTY_ACTIONS = (None, "", "none")

# 内置小游戏包的缺省 id（data/packs/minigames/ 下的目录名）
MINI_GAME_DEFAULT_ID = "escape_giantess"

# 章节效果覆盖可选滤镜：本仓唯一的滤镜清单（键 + 展示名）。
# background.py 在导入时校验其 PIL 映射的键与这里一致（不一致直接报错），
# 校验器（dungeon/validate.py）也按这里的键检查配置。空串是「显式清除滤镜」，
# 与「不覆盖（沿用章节默认）」区分开，见 dungeon/overrides.py。
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

# 窗口视效清单（键 + 展示名）：与「章节效果覆盖」的重采样背景滤镜相对——
# 这些视效操作整个会话窗口（覆盖层 / 内容位移），以绝对时间（秒）消退，
# 期间其他动作照常。运行时实现见 dungeon/window/fx.py（fx.py 导入时校验
# 键一致），校验器（dungeon/validate.py）也按这里的键检查配置。
WINDOW_FX = (
    ("flash", "闪光"),
    ("shake", "振动"),
    ("invert", "反色"),
    ("rgb_split", "色差分离"),
)
WINDOW_FX_KEYS = tuple(key for key, _label in WINDOW_FX)

# 触发判定的固定顺序位置：一轮判定从先到后逐位派发，**同位置至多生效一个**
# （同位置内按配置顺序取第一个满足条件且配置有效的触发器）：
#
# 1. ``fx``            窗口视效：纯视觉、不等结果，画面反应永远最先；
# 2. ``option`` / ``mini_game``  模态动作：占用步进，先于流程类占位；
# 3. ``goto``          章节跳转：之后生效的 effect / insert 落在新章节里
#                      （effect 若先于 goto 生效会被换章清除，等于白放）；
# 4. ``effect``        章节效果短暂覆盖（背景滤镜 / 音乐音量 / 语音效果 / 敏感效果）；
# 5. ``insert``        插入段落；
# 6. ``none``          条件标记：最后才立，依赖它的触发器最早下一轮可见。
ACTION_POSITIONS = (
    ("fx",),
    ("option", "mini_game"),
    ("goto",),
    ("effect",),
    ("insert",),
    ("none",),
)


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
