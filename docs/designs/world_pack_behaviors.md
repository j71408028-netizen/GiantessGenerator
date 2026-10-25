# 世界包行为包开发指南

> 角色演化模型的完整设计（量、公式、已验证动态特性）见 `docs/designs/character_evolution.md`；
> 覆盖 `StateService` 坐标/步长方法前建议先阅读该文档。

行为包（Behavior Pack）是世界包中的一种资源类型，允许世界包作者在激活该世界包期间
覆盖核心算法的实现，而无需修改主程序代码。可覆盖范围包括：

- `CreationService` / `StateService` 的部分静态方法；
- `core/logic/`（原 `core/logic.py`，已按职责拆为 `sizing` / `quips` / `simulation` / `text`
  四个子模块，`core/logic/__init__.py` 薄壳再导出全部公开名）中除 `get_size_category` 外的
  全部模块函数，以及 `PREDEFINED_TAGS` 常量（注册用的键**仍是 `"logic.xxx"`**——键是稳定
  契约，不含包路径，拆分不改键）；
- `profile`（`services/character/profile_text.py`，档案派生层的文本化出口）：作息与
  空间偏好的可读结论与实时提示（见附录 A 的 `profile` 组）。scope 同样是与文件位置
  无关的字面量契约——以后新域按此模式扩展。
- `offline`（`services/character/offline.py`，离线恢复）：纯计划
  `offline.recover_plan` 与展示文案 `offline.report_text`；计划收到的模型参数
  快照来自角色出身烙印（见下文三层模型）。执行与持久化仍由服务层负责
  （见附录 A 的 `offline` 组）。
- `select`（`services/exploration/arbiter.py`，报告选取仲裁）：车道窗口 /
  联合分 / 近匹配提拔是模块策略，模型参数是 `select.model.*` 常量钩子；
  硬闸、物理走查与 stuck 判定不开放（见附录 A 的 `select` 组）。

离线模型参数与空间 / 环境先验的调制**不再是运行时钩子**：它们经
`CreationService.derive_model_tuning` 在创建角色时烙印（出身层，见下文）。

**核心规则**：

- 行为包以**文件夹**为存储单位，一个世界包最多附带**一个**行为包；
- 行为包**不能单独启用**，只能随世界包激活；停用、解散或删除世界包后覆盖自动
  清空、恢复默认行为，不需要重启程序；
- 目录内每个 `.py` 模块都会在激活时加载，且必须定义 `register(runtime)` 入口；
- **任意代码执行**：行为包本质是 Python 代码，会在激活时于本机执行。只应加载
  来自可信来源的世界包，如同对待一般软件一样。

程序在 `data/static/behaviors/imperial_units/` 提供了一个现成示例：把显示单位从
米 / 千米改为英尺 / 英里。想快速了解行为包长什么样，可直接阅读它（完整代码见附录 B）。

## 开放边界：三层开放模型与角色身份稳定性

可覆盖目标分三层，这条边界与层级 / 契约元数据并行生效：

| 层 | 内容 | 生效方式 |
|---|---|---|
| 世界规则 | `select.*`（含 `select.model.*` 常量）、`StateService` 坐标/行动点、`logic.*`、`offline.recover_plan` / `report_text`、`profile.*` 文本化 | 运行时钩子，随世界包启停 |
| 角色出身 | 离线结算参数与空间/环境先验的调制烙印（`ModelTuning`） | `CreationService.derive_model_tuning` 创建时烙印，终身携带 |
| 角色习性 | rhythm / spatial 演化表学习 | 模型内部，参数封闭（不开放） |

原理一句话：世界包能改「世界如何运转」，不能重写「她是谁」。出身层让
世界包对角色的影响只发生在创建时（出身论）；习性层让她继续演化
（潜移默化）；两者相乘——烙印定**包络**（整体活动量、先验偏向），学习定
**形状**（何时活跃、常去哪里），现有结构（`step_per_hour × rhythm.factor_at`、
`priors × region_factor`）天然支持。存量角色因此结构性免疫：旧档案无
tuning 字段 → 读档回退默认 → 任何世界包启停都不再触及其离线/空间调制。

- **世界规则可以换**（运行时层）。坐标演化（`StateService` 坐标 / 步长原语）
  服务于资源匹配，世界包自带与演化动态匹配的资源；选取仲裁（`select.*`）
  配对的是世界内的资源供给；离线结算是把角色活动换算为世界效果的转换层
  （`offline.recover_plan`，收到的 model 即来自角色烙印）。覆盖这些改变
  「世界如何运转」，角色已学到的东西不被重新解释。
- **角色出身只在创建时烙印**。角色的离线结算参数与空间 / 环境先验以
  `ModelTuning` 烙印在档案里（`CreationService.derive_model_tuning` 派生，
  内部 sanitize 夹取归一；`origin_world` 记录出身世界包）：世界包对
  「她是谁」的调制只发生在出身时刻，已有角色的烙印终身携带、不随世界包
  启停变化。
- **角色习性不可重写**。作息学习（`RhythmService`）与空间 / 环境 / 偏好学习
  （`spatial.py` 学习层）的参数——半衰期、平滑伪计数、收缩强度、调制夹取、
  成熟度阻尼等——**不开放**：它们决定「从角色历史里学出什么样的她」，覆盖
  会让已有角色在加载世界包后性情大变。

后续开放的新域（如 `chat.*` 人设相关）同样遵守：世界包可以追加世界语境，
不可改写角色的既有身份事实（名字、性格、学到的习惯、达成的经历）；出身与
履历文本可经 persona 注入，但同样只追加语境。

## 开发流程

### 第 1 步：编写行为模块

在静态目录 `data/static/behaviors/<pack>/` 下创建文件夹（`<pack>` 只能由字母开头，
后跟字母、数字或下划线），在其中编写 `.py` 模块。

每个模块需定义 `register(runtime)` 入口函数；程序激活世界包时会加载全部模块并
把全局行为运行时传入：

```python
# data/static/behaviors/my_pack/height_rules.py
from services.creation_service import CreationService


def my_calculate_height(option, custom_val, min_slide, max_slide,
                        use_will, greed, rng, personality=None):
    # ...自定义身高算法...
    return 3.0, "within"


def register(runtime):
    # target 传字符串或静态方法对象均可
    runtime.override(CreationService.calculate_height, my_calculate_height)
```

覆盖实现必须与被覆盖函数保持**相同的参数顺序、默认值与返回约定**，否则会导致
运行时参数错位或下游解析失败。可覆盖的目标及签名见附录 A。

### 第 2 步：把行为包声明进世界包

创建世界包时在资源页**单选**该行为包，清单会自动写入；也可以直接手写
`world.json`：

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

目录结构：

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

### 第 3 步：激活世界包并验证

激活世界包后，覆盖立即生效——无论调用方是 `CreationService.x(...)` 这样的静态
调用（`ui` 中的直接引用也在内），还是通过 `context.creation_service` 等实例调用，
都会使用覆盖实现。停用或解散世界包后自动恢复默认行为；切换世界包时会先卸载
旧包再加载新包，不会串包。

### 打包与分发

- 使用「设置 → 世界包」的导出功能，或手动把整个 `<world_id>` 目录打包为
  `<world_id>.world.zip`；
- 解散世界包时，行为包会自动转存回 `data/static/behaviors/`（目录名冲突则
  追加世界包名与版本号）。

## 附录 A：注册 API 与可覆盖目标

### 注册 API

| 方法 | 说明 |
|------|------|
| `runtime.override(target, impl)` | 注册对某个可覆盖目标的实现。`target` 可以是 `"logic.compute_casualty"` 这样的字符串，也可以是 `CreationService.calculate_height` / `logic.compute_casualty` 等被包装的函数对象（带 `__hook_key__`）。函数覆盖的 `impl` 必须可调用且接受原函数的全部具名参数（或以 `**kwargs` 兜底）；常量覆盖的 `impl` 须为同类型值或返回该值的可调用对象。不满足时该条注册在激活时被拒绝并记入启用诊断。 |
| `runtime.resolve_value(key)` | 读取常量细度目标的有效值（覆盖实现可调用则调用取值；无覆盖返回默认值）。 |
| `runtime.default(target)` | 返回被覆盖前的默认实现。覆盖函数内部如需复用默认逻辑，可调用 `runtime.default(target)(...)`。 |
| `runtime.reset()` | 清空全部覆盖（一般由程序自动调用，模块内无需使用）。 |
| `runtime.catalog()` | 返回全部可覆盖目标的元数据（见下节），供开发工具与行为包作者查询。 |

### Hook 目录与层级

`runtime.catalog()` 返回按 key 排序的元数据列表：

| 字段 | 说明 |
|------|------|
| `key` | 稳定注册键 |
| `scope` | key 的第一段 |
| `kind` | `function` / `const` |
| `layer` | `infra` / `module` / `content` |
| `contract` | `point` / `strategy` / `data` |
| `since` | 进入目录的版本 |
| `deprecated` | 是否计划废弃 |
| `params` | 函数细度的具名参数；常量为空列表 |

层级按语义所有权划分，不按文件目录：

| 层级 / 契约 | 开放方式 | 典型目标 |
|------|------|------|
| `infra/point` | 固定流程中的单个算法点 | `logic.*` 数值核、`StateService` 坐标 / 行动点原语 |
| `infra/data` | 固定流程中的模型参数（数值 / 表） | `select.model.*` |
| `module/strategy` | 模块门面的决策策略，返回决策或计划 | `profile.*`、`offline.recover_plan`、`select.*`、`CreationService.derive_model_tuning`（创建期烙印）；后续 `chat.*` / `news.*` |
| `content/data` | 数据表、文案、阈值 | `logic.PREDEFINED_TAGS` |
| 核心不变式 | 不开放，不进入目录 | 坐标夹取、AP 上限、JSON 协议、消息状态机、持久化、习性学习参数 |

> 全目录 `since` 以 **2.0** 为基线（出身烙印基线在 2.0 alpha 窗口重定了整个
> 开放面）；已删 key 不回潮：`offline.model.*`、`spatial.TAG_*`、
> `StateService.recover_evolution`（离线恢复入口已收敛为 `offline.recover_plan`
> 与 `OfflineService` 直连）。

### `CreationService`

| 方法 | 签名 | 说明 |
|------|------|------|
| `generate_random_height` | `() -> float` | 生成一个随机的普通身高（米）。 |
| `calculate_height` | `(option, custom_val, min_slide, max_slide, use_will, greed, rng, personality=None) -> (float, Optional[str])` | 核心身高算法。`option` 为 `"custom"` 或 `"will"`；返回 `(身高, 意愿状态)`，状态取 `"within"` / `"implemented"` / `"failed"` / `None`。 |
| `derive_model_tuning` | `(personality, selected_tags, height, core=None) -> ModelTuning` | 派生**角色出身调制烙印**（since 2.0，`module/strategy`）。创建角色时调用一次，返回值经内部夹取归一（`ModelTuning.sanitize`）后烙进角色档案、终身携带：覆盖它只调制**之后创建**的角色（离线结算参数包络 + 空间/环境先验），不触及已有角色；`origin_world`（出身世界包记录）由程序按激活世界包写入，不经本钩子。默认实现按离线模型常量与空间 TAG 先验表的**当前有效值**派生，行为包也可以整体替换（含 `extra` 自定义键，核心模型忽略、下游钩子可读）。 |

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
| `advance_coordinates`      | `(personality, intrusion, destruction, step, step_intrusion=None, step_destruction=None) -> (float, float)` | 按故事步进推进坐标（坐标 += 步进 × 步长）；未传显式步长时沿用性格初始步长。报告事件与负向演化共用。                 |
| `evolve_step_rates`        | `(personality, step_intrusion, step_destruction, step, intrusion=None, destruction=None) -> (float, float)` | 坐标演化后的步长演化：先按敏感值/重力扣除，再向初始值恢复；传入坐标时叠加不适应性衰减。 |
| `decay_step_rates`         | `(personality, step_intrusion, step_destruction, step_intr, step_destr, intrusion=None, destruction=None) -> (float, float)` | 不适应性衰减的纯计算：坐标贴到 0.5/4.5 边界后，对应步长按 `0.4 ^ (|步进| × (1 - 归一个性强度))` 收敛；仅衰减，不负责敏感/重力扣除。 |
| `apply_landmark_switch`    | `(personality, intrusion, destruction, to_frequency) -> (float, float)` | 地标切换（含首次匹配）时的性格调整：切换到 common 介入度 + 敏感值，切换到 unique 介入度 - 敏感值；破坏性达到 3 以上时，切换到 unique 破坏性 + 重力，切换到 common 破坏性 - 重力。 |
| `decayed_coordinates`      | `(personality, intrusion, destruction, fraction) -> (float, float)` | 步进衰减的纯计算：仅当坐标越过性格边界（初始值 - 0.5 × 性格步长）时向边界回落 fraction 个步长。 |
| `apply_negative_evolution` | `(state) -> None` | 行动点数不足（<50）时应用负向演化：介入度步进取 -1 - 0.5 × 性格敏感值（由敏感值直接控制），破坏性步进取 -1 - 0.5 × 性格重力；步长演化叠加不适应性衰减；有变化时向演化表追加一行（步进取本次负向步进之和）。 |
| `apply_step_decay`         | `(state, fraction=0.1) -> None` | 步进衰减（角色面板逗留每分钟 fraction=0.1），有变化时向演化表追加一行。              |
| `consume_action_points`    | `(state, cost) -> bool` | 消耗行动点数；点数不足返回 `False`。                                  |
| `receive_action_points`    | `(state, amount) -> int` | 自然回复行动点数（离线恢复、角色面板在线恢复），夹取到 0~100，返回实际变化量。 |
| `refund_action_points`     | `(state, amount) -> int` | 返还行动点数（副本结算、报告解锁返还等），不设 100 上限，返回实际变化量。 |
| `recover_action_points`    | `(state) -> None` | 在角色面板逗留时，根据性格恢复行动点数（内部调用 `receive_action_points`，覆盖前者会自动生效）。 |

### `offline`（离线恢复计划）

离线结算拆成两层：`offline.recover_plan` 只读取状态并返回
`OfflineRecoveryPlan`，不写状态、不记演化；`OfflineService` 把计划写回角色并
追加演化行。展示文案同为模块策略：`recover_offline` 把实际恢复的行动点与
离线时长交给 `offline.report_text` 生成并返回。`OfflineModel` 是计划函数收到
的模型参数快照，**由角色出身烙印构造**（`OfflineModel.from_tuning
(state.model_tuning)`，创建期经 `CreationService.derive_model_tuning` 派生并
已夹取；离线 8 项默认值见 `core/models.py::ModelTuning`）。72 小时明细窗口与
2 小时精度同时是导出图表、演化表和 UI 的跨模块契约，固定不开放。

| 目标 | 类型 | 签名 / 默认 | 说明 |
|------|------|------|------|
| `offline.recover_plan` | 函数 | `(state, now, model) -> Optional[OfflineRecoveryPlan]` | 生成恢复计划。返回 `None` 表示时间戳无效或没有可结算时长。计划字段：`action_point_gain`（请求恢复量）、`intrusion` / `destruction`（结算后坐标，`None` 表示不改）、`details`（明细，`None` 表示保留现有明细）、`step`、`casualties`（本次新增伤亡）。 |
| `offline.report_text` | 函数 | `(state, plan, elapsed_minutes, action_point_gain) -> str` | 把一次离线结算转成展示文案，`plan` 为 `None` 返回空串。`elapsed_minutes` 是离线分钟数，`action_point_gain` 是执行层实际恢复的行动点（已受自然回复上限约束）；步进/伤亡增量取自计划。 |

覆盖 `offline.recover_plan` 可调用 `runtime.default("offline.recover_plan")` 复用
默认计划后再改字段。执行层会夹取坐标、把行动点恢复量交给
`StateService.receive_action_points`（自然上限 100），并按 `recover_evolution`
来源标签记录演化（历史演化行的字符串标签，入口本身已删）；这些执行语义不开放。

### `select`（报告选取仲裁）

`SelectionArbiter` 在地标候选与描述风格供给之间做配对仲裁：车道资格窗口与
近匹配提拔是裁定级输出，联合分是建议级（报告引擎的预算走步与坐标演化可
自由偏离）。`ruling` 参数是 :class:`~services.exploration.arbiter.SlotRuling`
（字段 `cand` / `addr` / `lanes` / `affordance` / `starved` / `promoted` /
`env_estimate`）。硬闸（可达 / 私有名 / 维度 / 耐久）、物理走查（位置
演化）与 stuck 判定不开放。`logic.select_quip_with_budget` /
`logic.get_comparisons` 的 key 不变（scope `logic` 是已部署契约）。

| 目标 | 类型 | 签名 / 默认 | 说明 |
|------|------|------|------|
| `select.lanes` | 函数 | `(landmark_addr, quip_registers) -> list` | 槽位 × 风格的车道表 `[(style, weight)]`（裁定级）：无地址地标全部风格 FREE；注册地址同址 EXACT、覆盖 COVER；不覆盖 / 私有名冲突不入窗口。 |
| `select.joint_score` | 函数 | `(ruling, position, reach, profile) -> float` | 建议级联合分：尺寸匹配主导，覆盖收益 / 档案 / 路径只调制。 |
| `select.promotions` | 函数 | `(order, cap) -> (list, list)` | 近匹配提拔（裁定级）：星槽在容差带内与有覆盖替代者交换位置，返回 `(新序, 提拔记录)`。 |
| `select.model.lane_weights` | 常量 dict | `{exact: 1.0, cover: 0.8, free: 0.5}` | 车道权重表（只调制排序，不改变资格判定）。 |
| `select.model.near_match_tau` | 常量 float | `0.35` | 近匹配容差（log 尺度）。 |
| `select.model.cover_weight` | 常量 float | `0.5` | 联合分覆盖收益权重。 |
| `select.model.region_weight` | 常量 float | `1.5` | 联合分活动区域命中增益。 |
| `select.model.path_weight` | 常量 float | `0.3` | 联合分路径连贯惩罚权重。 |
| `select.model.pool_saturation` | 常量 float | `2.0` | 充裕度池深饱和折算除数（`depth/(depth+k)`）。 |
| `select.model.pool_neighborhood` | 常量 float | `2.0` | 充裕度坐标邻域口径（坐标距离上限）。 |
| `select.model.promotion_cap_divisor` | 常量 float | `2.0` | 提拔上限 = 对比槽位数 / 该除数（除数下限 1.0）。 |
| `select.model.reach_multiplier` | 常量 float | `10.0` | 可达范围 = 身高倍数；硬闸语义不开放，仅开放数值。 |
| `select.model.env_consistency` | 常量 float | `0.5` | 描述文本环境一致性加分上限（消费于 `logic.select_quip_with_budget`）。 |
| `select.model.env_dev_range` | 常量 float | `1.4` | 环境偏差归一分母（环境因子值域全宽）。 |

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
| `build_size_description` | `(quip_result) -> str` | 由报告正文中的一条尺寸对比结果构造解锁描述。 |                                                                     |
| `select_quip_with_budget` | `(size_cat, intrusion_val, destruction_val, quips_working, locked_coords, cumulative_actual, cumulative_base, rate_factor=1.0, step_index=0, selected_tags=None, skip_base_prob=0.0, posture_list=None, blocked_words=None, lanes=None, env_estimate=None) -> (text, style, coord, actual_step, new_cumulative_actual, new_cumulative_base)` | 在预算内挑选事件描述 quip。含屏蔽词的描述会被跳过（内部调用 `contains_blocked_word`）。注意它会**就地弹出** `quips_working` 中被选中的 quip，覆盖实现必须保持该行为并返回 6 元组。`lanes` 是仲裁资格窗口（裁定级），`env_estimate` 是槽位环境估计（软权重）；覆盖实现必须接受这两个参数并遵循其语义。 |

### `profile`（档案派生层文本化，模块函数）

消费方：角色档案导出的「作息与活动范围观察」块、聊天人设与系统提示词。
返回约定：**纯文本多行（不含 HTML）**；实时提示在样本不足 / 零证据时返回**空串**
（消费方据此整行省略）。默认实现会按样本成熟度自动降级措辞，覆盖实现建议保留该约定。

| 目标 | 签名 | 说明 |
|------|------|------|
| `profile.describe_rhythm` | `(evolution, now=None) -> str` | 作息历史结论（活跃/安静时段、周末与工作日差异、成熟度措辞）；无样本返回空串。 |
| `profile.rhythm_now_hint` | `(evolution, now=None) -> str` | 作息实时提示：当前时刻活跃期 / 休整期各一行措辞，平常时段与样本不足时返回空串。 |
| `profile.describe_spatial` | `(profile, position="") -> str` | 空间偏好历史结论：常活动地带、环境偏好、手工活动区域。`profile` 是 `services.character.spatial.build_spatial_profile` 的装配结果。 |
| `profile.spatial_now_hint` | `(profile, position="") -> str` | 空间实时提示：当前位置相对偏好的评价 + 环境口语化描述；零证据返回空串。 |

### `spatial`（空间档案学习层）

空间 / 环境先验的调制入口是**创建期烙印**（覆盖
`CreationService.derive_model_tuning`，返回值里的 `spatial_priors` 四标量与
`env_prior_shares` 7 元份额），不再提供运行时常量钩子；份额形状在烙印端校验
（非法值回退均匀兜底并经 `[BehaviorPack]` 通道警告一次）。TAG 默认表
（`services/character/spatial.py` 的 `TAG_SPATIAL_PRIORS` / `TAG_ENV_PRIORS`）
只是默认派生的数据来源，直接 import 只能拿到默认值。

学习层参数（EWMA 半衰期、平滑伪计数、调制夹取、成熟度阻尼等）不开放——
它们重新解释角色的到访与沉淀历史，见「三层开放模型与角色身份稳定性」。

## 附录 B：完整示例（imperial_units）

程序自带的示例行为包，把显示单位从米 / 千米改为英尺 / 英里：

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

## 附录 C：注意事项

- **任意代码执行**：行为包本质是 Python 代码，会在激活时于本机执行。只应加载来自可信
  来源的世界包，如同对待一般软件一样。
- **签名一致性**：覆盖实现的参数与返回值约定必须与原静态方法一致。激活时运行时
  会校验：签名缺少默认实现要求的具名参数、或多出默认实现不提供的必填参数，都会被
  拒绝并记入启用诊断，避免参数错位到运行中才暴露。
- **错误隔离**：某个行为模块加载或注册失败时，异常只影响该模块（其余行为模块与
  默认行为不受影响），原因（含异常类型与文本）记入启用诊断。
- **常量覆盖**：`logic.PREDEFINED_TAGS` 是常量细度钩子（`behavior_const` 注册），
  必须用字符串键 `"logic.PREDEFINED_TAGS"` 注册，`impl` 可为列表值或返回列表的
  可调用对象（其他类型被拒绝）。数值常量允许 int / float 互通；可调用对象在每次
  读取时重新求值，返回值类型与默认值不符会回退默认值并记入诊断。程序统一通过
  `logic.get_predefined_tags()` 读取。
- **重复注册**：同一 key 被多个模块或同一模块多次注册时，最后一条生效，启用诊断
  给出警告。
- **启用诊断**：激活行为包时，`load_pack` 逐条校验注册并产出诊断报告
  （`BehaviorLoadReport`）：接管了哪些 key、未知 key / 签名或类型不符的拒绝原因、
  模块加载失败等，经 `[BehaviorPack]` 通道打印；导航栏会显示接管项数或诊断异常
  计数。坏注册只拒绝该条，不影响包内其余注册。
- **能力边界（副本）**：副本演化主体公式 `dungeon/rules.py::evolve_attributes`
  没有行为钩子——副本路径只通过注入的 `StateService.decay_step_rates` 受行为包
  影响；段步进、转移矩阵与章节敏感效果属于方案配置，不应由行为包重复表达。
  副本子进程会随激活世界包加载同一个行为包，父进程已有的钩子在副本内同样生效。
