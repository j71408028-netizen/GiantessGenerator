# 世界包行为包开发文档

行为包（Behavior Pack）是世界包中的一种资源类型，允许世界包作者在激活该世界包期间
覆盖核心算法的实现，而无需修改主程序代码。可覆盖范围包括：

- `CreationService` / `StateService` 的部分静态方法；
- `logic.py` 中除 `get_size_category` 外的全部模块函数，以及 `PREDEFINED_TAGS` 常量。

## 原理概述

程序把需要可定制的方法包装为“行为钩子”。每次调用这些函数时，会先查询进程内唯一的
行为运行时（`behavior_runtime.py`）：

- 若当前激活的世界包声明并成功注册了对应覆盖，则调用覆盖实现；
- 否则回退到主程序的默认实现。

因此，无论调用方是 `CreationService.x(...)` 这样的静态调用，还是通过
`context.creation_service` 等实例调用，覆盖都会生效。停用或解散世界包后，覆盖会被清空，
恢复默认行为。

## 目录与清单声明

行为包以**文件夹**为存储单位。开发时放在静态目录 `data/static/behaviors/<pack>/`，
配置世界包时可单选其中一个；解散世界包时也会把包内行为包转存到该目录。
行为包**不能单独启用**，仍只能随世界包激活。

世界包内部对应路径为 `behaviors/<pack>/`，包名在 `world.json` 的
`resources.behaviors` 中以单元素列表声明：

```
data/static/behaviors/my_pack/     # 静态开发副本（文件夹）
├── height_rules.py
└── state_tuning.py

data/worlds/<world_id>/
├── world.json
└── behaviors/
    └── my_pack/                   # 随世界包启用的副本
        ├── height_rules.py
        └── state_tuning.py
```

`world.json` 示例：

```json
{
  "format_version": 1,
  "world_id": "my_world",
  "name": "我的世界",
  "version": "1.0",
  "resources": {
    "behaviors": ["my_pack"]
  }
}
```

行为包名只能由字母开头，后跟字母、数字或下划线。一个世界包最多附带一个行为包；
目录内每个 `.py` 模块都会在激活时加载。

## 行为模块的写法

每个行为模块需要定义 `register(runtime)` 入口函数。程序激活世界包时，会加载该行为包
目录内的全部模块，并把全局行为运行时作为参数传入：

```python
# behaviors/my_pack/height_rules.py
from services.creation_service import CreationService


def my_calculate_height(option, custom_val, min_slide, max_slide,
                        use_will, greed, rng, personality=None):
    # ...自定义身高算法...
    return 3.0, "within"


def register(runtime):
    # target 传字符串或静态方法对象均可
    runtime.override(CreationService.calculate_height, my_calculate_height)
```

### 注册 API

| 方法 | 说明 |
|------|------|
| `runtime.override(target, impl)` | 注册对某个可覆盖目标的实现。`target` 可以是 `"logic.compute_casualty"` 这样的字符串，也可以是 `CreationService.calculate_height` / `logic.compute_casualty` 等被包装的函数对象（带 `__hook_key__`）。函数覆盖的 `impl` 必须可调用且签名与原函数一致；对常量（如 `logic.PREDEFINED_TAGS`）也可直接传列表值。 |
| `runtime.default(target)` | 返回被覆盖前的默认实现。覆盖函数内部如需复用默认逻辑，可调用 `runtime.default(target)(...)`。 |
| `runtime.reset()` | 清空全部覆盖（一般由程序自动调用，模块内无需使用）。 |

覆盖实现必须与被覆盖函数保持相同的参数顺序、默认值与返回约定，否则会导致运行时参数
错位或下游解析失败。

## 可覆盖的目标

### `CreationService`

| 方法 | 签名 | 说明 |
|------|------|------|
| `generate_random_height` | `() -> float` | 生成一个随机的普通身高（米）。 |
| `calculate_height` | `(option, custom_val, min_slide, max_slide, use_will, greed, rng, personality=None) -> (float, Optional[str])` | 核心身高算法。`option` 为 `"custom"` 或 `"will"`；返回 `(身高, 意愿状态)`，状态取 `"within"` / `"implemented"` / `"failed"` / `None`。 |

> `get_body_parts`、`core_from_params`、`get_deterministic_rng` 不开放覆盖：它们属于
> 内部编排/辅助逻辑，覆盖 `calculate_height` 等即可间接改变其输出。

### `StateService`

角色状态（坐标 = 介入度/破坏性、行动点数）操作的统一入口。坐标方法分两层：
纯数值运算（接受任意状态来源的数值，报告生成等字典流程可直接使用），
以及快照级操作（传入 `CharacterSnapshot`，有变化时向演化表追加一行）。

| 方法                         | 签名 | 说明                                                      |
|----------------------------|------|---------------------------------------------------------|
| `clamp_coordinates`        | `(intrusion, destruction) -> (float, float)` | 把介入度/破坏性夹取到统一的 0.5~4.5 大边界。以下的坐标操作方法都应用此大边界。            |
| `shift_coordinates`        | `(intrusion, destruction, intrusion_delta, destruction_delta) -> (float, float)` | 按原始坐标增量平移坐标（副本结局增量等场景）。                                 |
| `advance_coordinates`      | `(personality, intrusion, destruction, step) -> (float, float)` | 按故事步进推进坐标（坐标 += 步进 × 性格步长）；报告事件与负向演化共用。                 |
| `apply_landmark_switch`    | `(personality, intrusion, destruction, to_frequency) -> (float, float)` | 地标切换（含首次匹配）时的敏感值调整：切换到 common 介入度 + 敏感值，切换到 unique 介入度 - 敏感值；破坏性达到 3 以上时，切换到 unique 破坏性 + 敏感值，切换到 common 破坏性 - 敏感值。 |
| `decayed_coordinates`      | `(personality, intrusion, destruction, fraction) -> (float, float)` | 步进衰减的纯计算：仅当坐标越过性格边界（初始值 - 0.5 × 性格步长）时向边界回落 fraction 个步长。 |
| `apply_negative_evolution` | `(state) -> None` | 行动点数不足（<50）时应用负向演化：步进取 -1 - 0.5 × 性格敏感值；有变化时向演化表追加一行（步进取本次负向步进）。 |
| `apply_step_decay`         | `(state, fraction=0.1) -> None` | 步进衰减（角色面板逗留每分钟 fraction=0.1），有变化时向演化表追加一行。              |
| `recover_evolution`        | `(state, now=None) -> None` | 离线恢复：行动点按分钟恢复、坐标按分钟衰减，宽限后按日间步进结算伤亡；有变化时追加演化表一行。         |
| `consume_action_points`    | `(state, cost) -> bool` | 消耗行动点数；点数不足返回 `False`。                                  |
| `receive_action_points`    | `(state, amount) -> int` | 自然回复行动点数（离线恢复、角色面板在线恢复），夹取到 0~100，返回实际变化量。 |
| `refund_action_points`     | `(state, amount) -> int` | 返还行动点数（副本结算、报告解锁返还等），不设 100 上限，返回实际变化量。 |
| `recover_action_points`    | `(state) -> None` | 在角色面板逗留时，根据性格恢复行动点数（内部调用 `receive_action_points`，覆盖前者会自动生效）。 |

### `logic`（模块函数与常量）

| 目标 | 签名 / 形态 | 说明                                                                                         |
|------|------|--------------------------------------------------------------------------------------------|
| `logic.PREDEFINED_TAGS` | `List[str]` | 预定义标签常量。覆盖可注册列表值，或返回列表的可调用对象；读取入口为 `logic.get_predefined_tags()`。                          |
| `apply_size_unlock_updates` | `(unlocks, updates, info_update_rate=0.5) -> Dict[str, str]` | 写入部位解锁信息。                                                                                  |
| `compute_environment_factor` | `(text) -> float` | 按文本中的环境关键词给出伤亡环境系数。                                                                        |
| `compute_casualty` | `(height, step, destruction, text, env_factor=None) -> float` | 计算一段文本的伤亡增量。                                                                               |
| `format_size` | `(size, base_size=None) -> str` | 尺寸格式化（米 / 千米，含小数位对齐），全流程共用。                                                                |
| `length_unit_label` | `() -> str` | 当前基础长度单位标签（默认“米”），供界面输入/单位标签显示。覆盖 `format_size` 换单位时，一般也覆盖本函数让输入标签一致。 |
| `replace_quip_tags` | `(quip_text, style, size_cat, detail_pools, quips_working=None, intr=None, dest=None, enable_confusion=False, allow_confusion_map=None) -> str` | 替换描述中的 `[类型:编号:文本]` 标记。                                                                    |
| `should_skip_by_part_tags` | `(part, selected_tags, p) -> bool` | 在构造对比时，按与部位展示有关的个性标签添加偏好。                                                                  |
| `contains_blocked_word` | `(text, blocked_words) -> bool` | 判断文本是否包含任一屏蔽词（大小写不敏感的子串匹配）。覆盖后地标匹配、描述匹配与副本提示词过滤都会生效。 |
| `get_comparisons` | `(landmarks, body_parts, order="match", limit=5, selected_tags=None, skip_base_prob=0.0, selected_parts=None, blocked_words=None) -> List[Dict]` | 寻找最接近的地标对比。名称含屏蔽词的地标会被跳过（内部调用 `contains_blocked_word`）。                                                                                |
| `_build_size_description` | `(quip_result) -> str` | 由报告正文中的一条尺寸对比结果构造解锁描述。                                                                     |
| `select_quip_with_budget` | `(size_cat, intrusion_val, destruction_val, quips_working, locked_coords, cumulative_actual, cumulative_base, rate_factor=1.0, step_index=0, selected_tags=None, skip_base_prob=0.0, posture_list=None, blocked_words=None) -> (text, style, coord, actual_step, new_cumulative_actual, new_cumulative_base)` | 在预算内挑选事件描述 quip。含屏蔽词的描述会被跳过（内部调用 `contains_blocked_word`）。注意它会**就地弹出** `quips_working` 中被选中的 quip，覆盖实现必须保持该行为并返回 6 元组。               |



## 完整示例

程序在 `data/static/behaviors/imperial_units/` 提供了一个现成示例，把显示单位从
米 / 千米改为英尺 / 英里。
创建世界包时在资源页选择 `imperial_units` 即可附带。

```python
# data/static/behaviors/imperial_units/imperial_units.py
M_TO_FT = 3.28084
FT_PER_MILE = 5280.0
MILE_THRESHOLD_M = 5000.0


def format_size(size, base_size=None):
    ref = base_size if base_size is not None else size
    if ref >= MILE_THRESHOLD_M:
        miles_ref = ref * M_TO_FT / FT_PER_MILE
        if miles_ref >= 100:
            decimals = 1
        elif miles_ref >= 10:
            decimals = 2
        else:
            decimals = 3
        display = size * M_TO_FT / FT_PER_MILE
        unit = "英里"
    else:
        feet_ref = ref * M_TO_FT
        if feet_ref >= 1000:
            decimals = 0
        elif feet_ref >= 100:
            decimals = 1
        else:
            decimals = 2
        display = size * M_TO_FT
        unit = "英尺"
    return f"{display:.{decimals}f} {unit}"


def length_unit_label():
    return "英尺"


def register(runtime):
    runtime.override("logic.format_size", format_size)
    runtime.override("logic.length_unit_label", length_unit_label)
```

## 打包与分发

1. 在 `data/static/behaviors/<pack>/` 下编写行为模块（每个文件需有 `register`）；
2. 创建世界包时在资源页单选该行为包，清单会写入 `resources.behaviors: ["<pack>"]`；
3. 也可直接使用「设置 → 世界包」的导出功能，或手动把整个 `<world_id>` 目录打包为
   `<world_id>.world.zip`。

解散世界包时，行为包会转存到 `data/static/behaviors/`（目录名冲突则追加世界包名与版本号）。

## 注意事项

- **任意代码执行**：行为包本质是 Python 代码，会在激活时于本机执行。只应加载来自可信
  来源的世界包，如同对待一般软件一样。
- **签名一致性**：覆盖实现的参数与返回值约定必须与原静态方法一致，否则可能引发参数
  错位、异常或生成逻辑出错。
- **错误隔离**：某个行为模块加载或注册失败时，程序会打印
  `[BehaviorPack] ...` 警告并跳过该模块，其余行为模块与默认行为不受影响。
- **激活/停用生效时机**：行为覆盖只在世界包激活期间生效；停用、解散或删除世界包后自动
  清空，不需要重启程序。切换世界包时会先卸载旧包再加载新包，保证不会串包。
- **静态调用同样生效**：由于所有调用都经过行为钩子，`ui` 中直接引用
  `CreationService.calculate_height(...)`、`StateService.recover_action_points(...)`、
  `logic.compute_casualty(...)` 的位置也会一并使用覆盖实现。
- **常量覆盖**：`logic.PREDEFINED_TAGS` 不是函数，必须用字符串键
  `"logic.PREDEFINED_TAGS"` 注册，且 `impl` 可为列表值或返回列表的可调用对象。
  程序统一通过 `logic.get_predefined_tags()` 读取该常量。
