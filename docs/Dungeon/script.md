# 副本数据模型索引：章节与触发器

> **定位**：方案配置（`data/packs/scenarios/<id>/config.json`）里玩法结构的**字段索引**。
> 分层与运行时数据流见 [架构说明](architecture.md)，窗口侧见 [窗口文档](window.md)。
> 字段声明的单一真相源是 `dungeon/schema.py`（`CHAPTER_FIELDS` / `TRIGGER_FIELDS` / `SCENARIO_FIELDS`）。

## 目录

| 节 | 内容 |
|---|---|
| §1 | 模型边界（章节 vs 触发器） |
| §2 | 章节字段索引 |
| §3 | 章节运行时规则 |
| §4 | 触发器字段索引 |
| §5 | 条件键索引 |
| §6 | 动作注册表 |
| §7 | 短暂视效 |
| §8 | 编辑器文件索引 |
| §9 | 校验、迁移与持久化 |

---

## 1. 模型边界

| 模型 | 职责 | 有没有条件字段 |
|---|---|---|
| **章节 Chapter** | 只描述「身处其中时的环境」：特定背景、持续敏感效果、编辑器配色 | **没有** |
| **触发器 Trigger** | 唯一的流程驱动者：条件成立后执行一个动作；进入 / 离开章节也必须由它的「跳转章节」动作执行 | 有 |

旧版把条件与动作塞在同一个无层级的触发器里，动作有六种；现在背景切换与性格敏感化由
**章节属性**承担，动作类型拆成「单步行为」与「章节跳转」两类。
（演进过程与旧动作退场清单见 [副本模型与命名演进](history/model_evolution.md)，不据此改代码。）

## 2. 章节字段索引

来源：`schema.CHAPTER_FIELDS`（与 `chapters.normalize_chapter` 的产出一致，由 `check_scenario_schema.py` 守卫）。

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `name` | str | — | 章节名（**必填**） |
| `color` | color | — | 编辑器自定义配色 `#RRGGBB`（浅色模式基准） |
| `start` | bool | `false` | 起始章节：开副本时自动进入，全局最多一个 |
| `ending` | bool | `false` | 结束章节，见 §3.2 |
| `note` | str | `""` | 备注；提示词里会带一行「当前章节：X（备注）」 |
| `max_paragraphs` | int | `99` | 节内最大段落数（`chapters.DEFAULT_MAX_PARAGRAPHS`） |
| `overflow_target` | str | `""` | 超限跳转目标；空串 = 离开章节（无章节） |
| `background` | dict | — | 章节背景，见 §2.1 |
| `sensitivity` | list | — | 身处本章节时持续生效、离开即失效，见 §2.2 |
| `intrusion_delta` | float | `0` | 结束结算：介入度增量（仅 `ending=true`） |
| `destruction_delta` | float | `0` | 结束结算：破坏性增量（仅 `ending=true`） |
| `casualty_step` | float | `0` | 结束结算：伤亡步进（仅 `ending=true`） |
| `action_points_refund` | int | `0` | 结束结算：行动点返还（仅 `ending=true`） |
| `custom_deltas` | dict | — | 结束结算：自定义属性增量（仅 `ending=true`） |
| `icon_path` | str | `""` | 结局图标（相对方案目录，仅 `ending=true`） |

### 2.1 `background`（`schema.BACKGROUND_FIELDS`）

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `image_path` | str | `""` | 背景图，相对副本目录 |
| `smooth_transition` | bool | `true` | 平滑切换 |
| `filter_effect` | enum | `None` | 滤镜键，取值来自 `dungeon/actions.py::VISUAL_FILTERS` |

### 2.2 `sensitivity[]`（`schema.SENSITIVITY_FIELDS`）

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `attr` | str | — | 作用属性 |
| `strength` | float | `1.0` | 强度 |
| `objective` | float | `0.0` | 客观影响 |

敏感倍率公式（触发器与章节共用 `dungeon.chapters.sensitivity_amount`）：

```
倍率 = 强度 ×（性格值 + 客观影响）
# 破坏性 → 性格重力；介入度 → 性格敏感值；其余属性 → 策略值
#   策略值 = 敏感 × (1 - 归一行动点数) + 重力 × 归一个性强度
#   归一行动点数 = 行动点数/100（因子不小于 0，未知时缺省 0.5）
#   归一个性强度 = skip_base_prob / 5
```

## 3. 章节运行时规则

### 3.1 进出章节与超限

| 规则 | 行为 |
|---|---|
| 进入章节 | 重置节内计数（`DungeonState.chapter_steps`），清除上一章遗留的短暂视效 |
| 离开章节（跳到空目标） | 不清零属性、不切换背景，只是不再有章节效果 |
| 起始章节 | 唯一的自动进入点；它描述初始状态，不是判定，不违反「章节无条件字段」 |
| 超限跳转 | 节内计数达到 `max_paragraphs` 时，在 `_finish_step` 末尾（触发器判定**之后**）自动跳到 `overflow_target`；空串 = 离开章节 |
| 上限只是兜底 | 触发器的 `goto` / `option` 仍可在达到上限**之前**离开章节 |
| 回放顺序 | 跳转与结束都发生在同一步的 `_finish_step` 中，回放里是「步进记录 → `kind: "chapter"` 记录」 |

### 3.2 结束章节（`ending: true`，原「结束触发器」）

由 `chapters.is_terminating_chapter` / `overflow_jump_target` 统一判定：

1. **不允许跳出或弹选项**：处于结束章节时 `goto` 与 `option` 在 `check_triggers` 中被直接跳过（打印提示，不记入已触发集合）。
2. **所有段落类型一律被覆盖为「结局」，步进为 0**：`_generate_next_text` / `_display_inserted_paragraph` 强制以结局语气生成，显示前缀 `【结局】`；`rules.evolve_attributes(step_override=0.0)` 冻结坐标、自定义属性与步长演化，只累加计数，伤亡步进也为 0。
3. **达到 `max_paragraphs` 后终止**：`overflow_target` 恒为空串，调用 `_terminate_from_ending_chapter` 按章节自身的结算字段生成结局，复用结局生成管线（`EndingHandler._start_ending_generation`）。

旧配置里的 `ending`（结局）触发器**仍然可以运行**：`actions.ENDING_ACTION = "ending"` 保留，运行时照旧执行、回放照旧复现；
编辑器不再提供新建入口，既有这类触发器在列表里显示为「结局（旧配置）」。

## 4. 触发器字段索引

来源：`schema.TRIGGER_FIELDS`。

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `name` | str | — | 触发器名（**必填**；`选择:<名字>` 条件键用它） |
| `chapter` | str | `""` | 所在章节（条件的一部分）：空 = 任意章节，`__none__` = 无章节，章节名 = 仅在该章节内 |
| `precondition_names` | list | — | 前置触发器；列表内每个都至少触发过一次 |
| `condition` | dict | — | 条件规则组，见 §5 |
| `repeatable` | bool | `true` | 可重复触发 |
| `action` | enum | — | 动作类型，见 §6（旧配置键 `action_type` 兼容） |
| `action_data` | dict | — | 动作参数 |

判定顺序见 `TriggerHandler.check_triggers`：**所在章节 → 前置触发器 → 条件规则**，任何一项不满足即跳过。
`action_data["type"]` 与 `action_type` 冗余存在于旧配置中；`normalize_action_type` 只做
「动作字段缺失时回退 `action_data["type"]`」，不再做任何旧别名转换。

## 5. 条件键索引

来源：`schema.FIXED_CONDITION_KEYS` / `SERIES_CONDITION_KEY` / `CHOICE_CONDITION_PREFIX`，
求值在 `rules.TriggerRules.evaluate`。

| 键 | 含义 | 可用度量 |
|---|---|---|
| `介入度` / `破坏性` | 演化坐标 | — |
| `总伤亡` | 累计伤亡 | — |
| `总计数` | 副本总步数 | — |
| `间隔计数` | 距上次任意触发器触发后的步数 | — |
| `节内计数` | 进入当前章节后的步数 | — |
| `伤亡数组` | 每步伤亡序列 | `count` / `avg` / `trend` / `last` |
| `选择:<触发器名>` | 该选项触发器的选择记录 | `count` / `ratio` / `trend` / `last` |
| 自定义演化量名 | `evolution_attrs` 中 `type == "custom"` 的属性 | — |

- 比较符（`CONDITION_COMPARATORS`）：`>=` `<=` `>` `<` `==` `!=`
- 整体关系（`CONDITION_OPERATORS`）：`and` / `or`

## 6. 动作注册表

单一出处：`dungeon/actions.py`（`NEW_ACTIONS` / `ENDING_ACTION` / `ACTION_LABELS` / `VISUAL_FILTERS`）。
编辑器、依赖图、回放导出共用，不再各写一份字符串常量。

| 类型 | 展示名 | `action_data` | 状态 |
|---|---|---|---|
| `insert` | 插入文本 | `text, text_type, highlight, delayed` | 运行时；`delayed` 表示等下一段 AI 段落返回后再显示 |
| `option` | 弹出选项 | `prompt, options[{id, prompt, text}]` | 运行时；选择按编号记入 `选择:<名字>` |
| `effect` | 短暂视效 | `filter, duration` | 运行时；给当前背景叠加限时滤镜，`duration` 步后恢复章节默认滤镜 |
| `goto` | 跳转章节 | `chapter`（空串 = 离开章节） | 运行时；进入目标章节，应用其背景与持续敏感效果 |
| `none` | 条件标记 | `{}` | 运行时；无动作，只标记条件成立，供其他触发器作前置条件 |
| `ending` | 结局（旧配置） | `name, intrusion_delta, destruction_delta, casualty_step, action_points_refund, custom_deltas, icon_path` | **只读兼容**：已迁移为「结束章节」，编辑器不再提供新建入口 |
| `background` / `sensitivity`（早期拼写 `sensitive`） | 旧版·已失效 | — | **运行时不认识**：不在注册表里，落入「未知的触发器动作类型」通用路径（不执行、不记入已触发集合、不重置间隔计数，只打印一行提示） |

旧版动作的三处处理：运行时跳过、触发器列表显示为「旧版·已失效」（无表单，只能删除）、
校验器 `validate.LEGACY_ACTIONS` 在**编辑器保存时与副本启动前**报专项 warning。

## 7. 短暂视效

滤镜键的单一出处是 `actions.VISUAL_FILTERS`（与 `DungeonBackground.apply_filter` 的键同步，
`background.py` 的 PIL 映射在导入时校验一致性）。

| 规则 | 行为 |
|---|---|
| 触发 | 入队 `visual_effects`，每步在 `_finish_step` 中消耗 1 步 |
| 生效 | 期间取最后入队的滤镜，到期后恢复所在章节的默认滤镜 |
| 换章 | 立即清空 |
| 无背景图 | 视效被忽略（打印提示） |

## 8. 编辑器文件索引

| 文件 | 职责 |
|---|---|
| `ui/scenario/chapter_trigger_mgr.py` | 章节与触发器**合并面板**：章节行加粗并按自定义颜色着色，其下缩进行是本章节触发器；「任意章节 / 无章节」的触发器排在最后。底部是新建章节 / 新建触发器与共用的编辑、删除、上移、下移 |
| `ui/scenario/chapter_dlg.py` | 章节编辑：配色色板 + 手填 hex、起始 / 结束标记、最大段落数与超限跳转、背景导入、敏感效果增删、结束章节的结局结算与图标 |
| `ui/scenario/trigger_dlg.py` | 触发器编辑：所在章节下拉、`跳转章节` / `短暂视效` 表单、`节内计数` 条件键、旧版动作独立按钮行；`结局` 动作已从按钮行移除 |
| `ui/scenario/asset_import.py` | 背景 / 结局图标导入的共用实现（裁剪入库、相对路径） |
| `ui/scenario/component_mgr.py` | 显示组件卡片化管理（开关与参数改动自动保存） |

配色与调序：

- 章节配色以 `#RRGGBB` 存配置（浅色模式基准），深色模式下由 `chapters.shade_for_mode` 提亮 35%；触发器行沿用所属章节的颜色，无归属的用弱化色。
- 触发器只与同组内相邻的触发器交换，越过章节行即到达章节收尾（不跨章节移动）；章节与相邻章节交换，其下触发器按章节归组自动跟随。
- 新建触发器默认落在当前选中的章节里；编辑章节改名时，指向它的触发器作用域与 `跳转章节` 目标一并改名。
- 删除章节会在确认框里列出将被一并删除的触发器；确认后一起删除，仍指向该章节的 `跳转章节` 目标会收到警告（配置不会被自动改写）。

## 9. 校验、迁移与持久化

### 9.1 校验器

`validate.validate_scenario_config(config, scenario_dir=None)` 返回结构化诊断
（`Diagnostic(level, path, message)`，error / warning / info 三级）。

| 诊断级别 | 覆盖示例 |
|---|---|
| `error` | 悬空 goto / overflow_target、无起始章节、空选项列表 |
| `warning` | 多起始章节、结束章节内的 option / goto、废弃动作与旧版字段、条件键 / 比较符 / 度量非法、转移矩阵行缺失与权重越界、背景图 / 结局图标缺失（需传 `scenario_dir`） |
| `info` | 无结局路径、旧版顶层字段、未知顶层键 |

接入点：

| 时机 | 位置 | 力度 |
|---|---|---|
| 编辑器保存 | `ScenarioRepo.save_config()` | 诊断记入 `last_diagnostics`，编辑器弹窗汇总 error |
| 副本启动前 | `window/base.py::_load_session_config()` | error 级阻止进入 |
| 离线批量 / CI | `scripts/validate_scenarios.py` | error 退出码 1 |

### 9.2 迁移与兼容

| 项 | 行为 |
|---|---|
| `ScenarioRepo._migrate` | 以原配置为底稿补齐字段，未知键（含 `components`）原样保留；缺失的 `chapters` 补成 `[]`，每个触发器补 `chapter: ""`；旧写法 `components` 列表里的文本家族成员提升到 `text_component` 字段并从列表移除 |
| `chapters.normalize_chapter` | 补齐 `ending` / `max_paragraphs` / `overflow_target` 与结束章节结算字段（旧章节默认 `ending=false`、上限 99、超限离开章节） |
| 旧配置行为 | 迁移后所有既有触发器都是「任意章节」，也不自动创建章节 |
| 旧版 `background` / `sensitivity` 动作 | **不再执行**，配置本身不被改写，需要清理时在编辑器里删除 |
| 旧 `ending` 动作 | **继续执行**；新做结局请改用「结束章节」 |
| 演化配置链路 | `window/base.py::_init_session` 把 `transition_matrix` 与 `section_steps` 传给 `EvolutionRules`（同时注入 `StateService.decay_step_rates`）；这条链路由 `check_scenario_schema.py` 守卫 |

### 9.3 回放记录

| 记录 | 说明 |
|---|---|
| 普通步进 | 读取处按 `not entry.get("kind")` 识别：`engine._replay_next_step`、`persistence`、`archive_export._render_replay_entries` |
| `kind: "chapter"` | 章节进入；`goto` 触发器记录额外带 `chapter_background`，回放时不依赖当前章节配置即可复现 |
| `kind: "trigger"` | 触发器触发，回放时走 `_replay_trigger` |
| `ending_chapter: true` | 结束章节产生的步进记录，回放时按「结局」类型展示 |

### 9.4 剧情压缩（提示词上下文）

`summary.StorySummarizer` 把冗长段落历史压成可注入提示词的「剧情概要」，由
`DungeonWindowBase._init_session` / `_init_replay` 创建（`story_summary`），`engine` 每步（含回放）调用
`_record_story_summary` 登记段落。

| 项 | 行为 |
|---|---|
| 压缩时机 | 缓冲达到 `block_size`（默认 20 段）压一个块；`_enter_chapter` 离开章节时 `flush_chapter` 强制压本章余量；结局生成前再 flush 一次 |
| 压缩方式 | 优先用 AI 生成不超过 100 字的第三人称概要；AI 不可用 / 失败时回退内部算法（首句 + 高频词 + 末句）；回放模式无 AI，走内部算法 |
| 提示词内容 | `build_user_prompt` 注入 `prompt_block()`：**截至此刻的全部压缩概要** + **最近 N 段原文**；N = `settings["story_recent_count"]`，默认 20，设置项「剧情概要保留段数」（1~200） |
| 存放 | 只存内存（`summaries`），不写入回放文件；回放按同样的块大小重新累计，不依赖保存时的概要内容 |

---

## 历史

本文件只描述**现状**（字段以 `dungeon/schema.py` 为准）。以下档案记录这些形状是怎么来的：

| 档案 | 内容 |
|---|---|
| [副本模型与命名演进](history/model_evolution.md) | 章节 / 触发器模型拆分、动作类型退场（`background` / `sensitivity` / `ending`）、命名与目录迁移 |
