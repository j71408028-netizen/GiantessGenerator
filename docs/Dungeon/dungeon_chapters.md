# 副本数据模型：触发器与章节

> 总览（分层、模块地图、一步数据流、收尾与自检脚本）见
> [副本架构说明](dungeon_architecture.md)。本文只写配置里的玩法数据结构。

副本配置（`data/packs/scenarios/<id>/config.json`）里的玩法结构由**触发器**与
**章节**两种模型组成。两者的职责边界是本模型的核心约束：

- **章节**只描述「身处其中时的环境」：特定背景、持续敏感效果、编辑器配色。
  章节**没有任何条件字段**。
- **触发器**是唯一的流程驱动者：条件成立后执行一个动作，进入/离开章节也必须
  由触发器的「跳转章节」动作执行。

早期版本把条件与动作塞在同一个无层级的触发器里，动作类型有插入文本、弹出选项、
性格敏感化、结局、背景切换、空触发器（辅助构建）六种。现在背景切换与性格敏感化
由章节属性承担，动作类型拆成「单步行为」与「章节跳转」两类。

## 1. 章节（Chapter）

```jsonc
{
  "name": "序章",
  "color": "#0F766E",          // 编辑器自定义配色（#RRGGBB）
  "start": true,               // 起始章节：开副本时自动进入（全局最多一个）
  "ending": false,             // 结束章节：见 1.2（段落类型固定结局、步进 0、超限终止）
  "max_paragraphs": 99,        // 节内最大段落数（默认 99）
  "overflow_target": "",       // 超限跳转目标章节；空串=离开章节（无章节）
  "background": {              // 特定背景（可缺省=沿用当前背景）
    "image_path": "images/xxx.png",   // 相对副本目录
    "smooth_transition": true,
    "filter_effect": "blur"           // 可选，见 dungeon.actions.VISUAL_FILTERS
  },
  "sensitivity": [             // 身处本章节时持续生效，离开即失效
    { "attr": "介入度", "strength": 1.0, "objective": 0.0 }
  ],
  "note": "开场说明",
  // 以下字段仅在 ending=true 时使用（结束章节结算，结构同旧结局动作的 action_data）
  "intrusion_delta": 0, "destruction_delta": 0, "casualty_step": 0,
  "action_points_refund": 0, "custom_deltas": {}, "icon_path": ""
}
```

- 进入章节会重置 `节内计数`（`DungeonState.chapter_steps`）并清除上一章遗留的短暂视效。
- 离开章节（跳转到空目标）不清零属性、也不切换背景：只是不再有章节效果。
- 起始章节是唯一的自动进入点。它不带条件，也不违反「章节无条件字段」——
  `start` 描述的是初始状态，不是判定。

实现：`dungeon/chapters.py`（模型与配色）、`dungeon/window/triggers.py`
（`_enter_chapter` / `_enter_start_chapter` / `_apply_chapter_background`）。

### 1.1 最大段落数与超限跳转

- `max_paragraphs`（默认 `DEFAULT_MAX_PARAGRAPHS = 99`）限制**节内计数**：进入章节后
  段落数达到该值时，运行时在 `_finish_step` 末尾（触发器判定之后）自动跳转。
- 普通章节跳转到 `overflow_target`；`overflow_target` 为空串表示离开章节（回到无章节）。
- 触发器的 `goto`/`option` 仍可在达到上限**之前**离开章节；上限只是兜底。
- 跳转与结束都发生在同一步的 `_finish_step` 中，因此回放里的顺序是
  「步进记录 → `kind: "chapter"` 记录」，与触发器跳转一致。

### 1.2 结束章节（原「结束触发器」）

`ending: true` 的章节是副本的收尾容器，拥有以下特殊性质（运行时统一在
`dungeon/chapters.is_terminating_chapter` / `overflow_jump_target` 判定）：

1. **不允许通过触发器跳出或弹出选项**：处于结束章节时，`goto` 与 `option`
   触发器在 `check_triggers` 中被直接跳过（打印提示，不记入已触发集合）。
2. **所有段落类型一律被覆盖为「结局」，步进为 0**：`_generate_next_text` /
   `_display_inserted_paragraph` 强制以结局语气生成，显示前缀为 `【结局】`；
   `rules.evolve_attributes(step_override=0.0)` 冻结坐标、自定义属性与步长演化，
   只累加计数（`总计数`/`节内计数`），伤亡步进也为 0。
3. **最大段落数后跳转到终止**：`overflow_target` 恒为空串（离开章节），并调用
   `_terminate_from_ending_chapter` 按章节自身的结算字段
   （`intrusion_delta` / `destruction_delta` / `casualty_step` /
   `action_points_refund` / `custom_deltas` / `icon_path`）生成结局并结束副本，
   复用旧的结局生成管线（`EndingHandler._start_ending_generation`）。

旧配置中的 `ending`（结局）触发器**仍然可以运行**：`dungeon/actions.py` 保留
`ENDING_ACTION = "ending"`，运行时照旧执行、回放照旧复现；编辑器不再提供新建入口
（`trigger_dlg` 的动作按钮行已被移除，点击会提示迁移到「结束章节」），既有这类触发器
在列表里显示为「结局（旧配置）」。

## 2. 触发器（Trigger）

```jsonc
{
  "name": "进高潮",
  "chapter": "序章",           // 所在章节（条件的一部分）
  "precondition_names": [],    // 前置触发器
  "condition": {               // 条件规则
    "operator": "and",
    "rules": [ { "key": "节内计数", "comparator": ">=", "value": 3 } ]
  },
  "action_type": "goto",
  "action_data": { "type": "goto", "chapter": "高潮" },
  "repeatable": false
}
```

### 2.1 触发条件 = 三者的与

判定顺序见 `TriggerHandler.check_triggers`，任何一项不满足即跳过：

1. **所在章节**（`chapter` 字段，`dungeon.chapters.matches_scope`）
   - `""`：任意章节都参与判定（不限制章节）
   - `"__none__"`：仅当当前没有章节时判定
   - 章节名：仅在该章节内判定
2. **前置触发器**（`precondition_names`）：列表内每个触发器都至少触发过一次
   （`fired_triggers`）
3. **条件规则**（`condition`，`TriggerRules.evaluate`）：

| 键 | 含义 |
|----|------|
| `介入度` / `破坏性` | 演化坐标 |
| `总伤亡` | 累计伤亡 |
| `总计数` | 副本总步数 |
| `间隔计数` | 距上次任意触发器触发后的步数 |
| `节内计数` | **进入当前章节后的步数**（本次新增） |
| `伤亡数组` | 每步伤亡序列，支持 `count`/`avg`/`trend`/`last` 指标 |
| `选择:<触发器名>` | 该选项触发器的选择记录，支持 `count`/`ratio`/`trend`/`last` |
| 自定义演化量名 | `evolution_attrs` 中 `type == "custom"` 的属性 |

比较符：`>= <= > < == !=`；整体关系：`and` / `or`。

### 2.2 动作类型

动作注册表在 `dungeon/actions.py`，展示名、动作集合、归一化都从那里取，
编辑器、依赖图、回放导出共用，不再各写一份字符串常量。

**运行时动作**（编辑器主按钮行提供前五种，`ending` 只读兼容）：

| 类型 | 展示名 | `action_data` | 说明 |
|------|--------|---------------|------|
| `insert` | 插入文本 | `text, text_type, highlight, delayed` | 插入一段文本，`delayed` 表示等下一段 AI 段落返回后再显示 |
| `option` | 弹出选项 | `prompt, options[{id,prompt,text}]` | 弹出选择弹窗，选择按编号记入 `选择:<名字>` |
| `effect` | 短暂视效 | `filter, duration` | 给当前背景叠加限时滤镜，`duration` 步后恢复章节默认滤镜 |
| `goto` | 跳转章节 | `chapter`（空串=离开章节） | 进入目标章节，应用其背景与持续敏感效果 |
| `ending` | 结局（旧配置） | `name, intrusion_delta, destruction_delta, casualty_step, action_points_refund, custom_deltas, icon_path` | **已迁移为「结束章节」**：编辑器不再提供新建入口，运行时与回放仍兼容旧配置 |
| `none` | 条件标记 | `{}` | 无动作，只标记条件成立，供其他触发器作前置条件 |

**旧版动作——已完全退场**：`background`（背景切换）与 `sensitivity`（性格敏感化，
早期拼写 `sensitive`）。这两项早就由章节自身的属性承担（见 §1），因此现在：

- 运行时**不认识**这类动作：它们不在注册表里，也没有专门的跳过分支，直接落入
  「未知的触发器动作类型」通用路径（不执行、不记入已触发集合、不重置间隔计数，
  只打印一行提示）；旧回放里的同类记录同样忽略；
- 触发器列表把它们显示为「旧版·已失效」（没有表单，只能删除）；
- 校验器（`dungeon/validate.py` 的 `LEGACY_ACTIONS`）在**编辑器保存时与副本
  启动前**把它们报为专项 warning——作者在校验阶段就能看见残留并处理，而不是
  被运行时静默吞掉。

`action_data["type"]` 与 `action_type` 冗余存在于旧配置中；`normalize_action_type`
只做「动作字段缺失时回退 `action_data["type"]`」，不再做任何旧别名转换。

敏感倍率公式（触发器与章节共用 `dungeon.chapters.sensitivity_amount`）：

```
倍率 = 强度 ×（性格值 + 客观影响）
# 属性为「破坏性」时用性格重力；「介入度」用性格敏感值；
# 其余属性用策略值 = 敏感×(1-归一行动点数) + 重力×归一个性强度
# （归一行动点数 = 行动点数/100，因子不小于 0，未知时缺省 0.5；
#   归一个性强度 = skip_base_prob/5）
```

### 2.3 短暂视效

视效作用于**当前背景图**，滤镜取自 `DungeonBackground.apply_filter` 的键
（`dungeon/actions.VISUAL_FILTERS`，新增滤镜时两边同步）。

- 触发时入队 `visual_effects`，每步在 `_finish_step` 中消耗 1 步；
- 生效期间取最后入队的滤镜，到期后恢复所在章节的默认滤镜；
- 换章立即清空；
- 当前没有背景图时视效被忽略（会打印提示）。

## 3. 编辑器

| 文件 | 职责 |
|------|------|
| `ui/scenario/chapter_trigger_mgr.py` | 章节与触发器**合并面板**：章节行加粗并按自定义颜色着色，其下缩进行是本章节触发器；「任意章节 / 无章节」的触发器排在最后。底部是两个新建键（新建章节 / 新建触发器）与共用的编辑、删除、上移、下移 |
| `ui/scenario/chapter_dlg.py` | 章节编辑：配色色板 + 手填 hex、起始/结束标记、最大段落数与超限跳转、背景导入、敏感效果增删、结束章节的结局结算与图标 |
| `ui/scenario/trigger_dlg.py` | 触发器编辑：新增「所在章节」下拉、`跳转章节`/`短暂视效` 表单、「节内计数」条件键、旧版动作独立按钮行；`结局` 动作已从按钮行移除（仅兼容旧配置） |
| `ui/scenario/asset_import.py` | 背景/结局图标导入的共用实现（裁剪入库、相对路径） |

章节配色以 `#RRGGBB` 存在配置里（浅色模式基准），深色模式下由
`chapters.shade_for_mode` 提亮 35%，保证 Treeview 行文字可读；触发器行沿用
所属章节的颜色，无章节归属的触发器用弱化色。

合并面板的调序规则：触发器只与同组内相邻的触发器交换，越过章节行即到达
章节收尾（不跨章节移动）；章节与相邻章节交换，其下触发器按章节归组自动跟随。
新建触发器默认落在当前选中的章节里；编辑章节改名时，指向它的触发器作用域与
`跳转章节` 目标一并改名。

删除章节会在确认框里列出将被一并删除的触发器；确认后章节与其下所有触发器
一起删除，仍指向该章节的 `跳转章节` 目标会收到警告（配置不会被自动改写）。

## 4. 剧情压缩（提示词上下文）

`dungeon/summary.py` 的 `StorySummarizer` 负责把冗长的段落历史压缩成可注入
提示词的「剧情概要」，由 `DungeonWindowBase._init_session` / `_init_replay` 创建
（`story_summary`），`engine` 每步（含回放）调用 `_record_story_summary` 登记段落。

- **压缩时机**：缓冲达到块大小（`block_size`，默认 20 段）时压缩一个块；
  `_enter_chapter` 离开章节时 `flush_chapter` 强制压缩本章余量（不满一块也压缩）；
  结局生成前再 flush 一次，保证概要完整。
- **压缩方式**：优先调用 AI 客户端生成不超过 100 字的第三人称概要；AI 不可用或
  失败时回退到内部算法（首句 + 高频词 + 末句）。回放模式无 AI，走内部算法。
- **提示词内容**：`build_user_prompt` 注入 `prompt_block()`——**从开始到此刻的全部
  压缩概要** + **最近 N 段原文**；N = `settings["story_recent_count"]`，默认 20，
  可在「设置 → 剧情概要保留段数」调整（1~200）。
- 压缩结果保存在内存（`summaries`），不写入回放文件：回放会按同样的块大小重新
  累计压缩，因此不依赖保存时的概要内容。

## 5. 持久化与兼容

- **schema 单一真相源**：字段/默认值/合法取值定义在 `dungeon/schema.py`；
  `ScenarioRepo.empty_scenario_config()`（空方案模板）、`dungeon/validate.py`
  的校验器、后续编辑器表单都从同一份声明取数。
- **运行时消费方**：`window/base.py::_init_session` 把方案配置的
  `transition_matrix` 与 `section_steps` 传给 `EvolutionRules`（同时注入不适应性
  衰减 `StateService.decay_step_rates`）。矩阵按**行覆盖**：配置里出现的行整行
  替换（行内没写的列按 0 计），没出现的行沿用
  `EvolutionRules.DEFAULT_TRANSITION_MATRIX`；非数字/负数权重在构造时被丢弃。
  这两个字段曾一度只在 config.json 里生效、没进运行时，现由
  `scripts/check_scenario_schema.py` 第 6 节守卫。
- **校验器**：`validate_scenario_config(config, scenario_dir=None)` 返回结构化诊断
  （`Diagnostic(level, path, message)`，error/warning/info 三级），覆盖：悬空
  goto/overflow_target（error）、无/多起始章节（error/warning）、结束章节内的
  option/goto（warning）、废弃动作与旧版字段（warning）、条件键/比较符/度量
  合法性（warning）、转移矩阵行缺失与权重越界（warning）、背景图/结局图标
  资产存在性（warning，需传 `scenario_dir`）。接入点：`ScenarioRepo.save_config`
  保存即校验（诊断记入 `last_diagnostics`，编辑器弹窗汇总 error）、
  `window/base.py::_load_session_config` 启动前校验（error 级阻止进入）、
  `scripts/validate_scenarios.py` 离线批量。
- **滤镜键单一出处**：`dungeon/actions.py::VISUAL_FILTERS` 是唯一清单；
  `background.py` 的 PIL 映射在导入时校验键一致（不一致直接报错）。
- `ScenarioRepo._migrate` 以原配置为底稿补齐字段，未知键（含 `components`）原样保留；
  缺失的 `chapters` 补成 `[]`，每个触发器补 `chapter: ""`；章节由
  `chapters.normalize_chapter` 补齐 `ending` / `max_paragraphs` / `overflow_target`
  与结束章节结算字段（旧章节默认 `ending=false`、上限 99、超限离开章节）。
- **旧配置行为不变**：迁移后所有既有触发器都是「任意章节」，也没有章节被自动创建。
  旧版 `background` / `sensitivity` 动作**不再执行**（运行时跳过，见 2.2），
  配置本身不会被改写，需要清理时在编辑器里删除对应触发器。
  旧的 `ending` 动作**继续执行**（结局触发器仍是合法的旧配置），只是编辑器不再
  提供新建入口；需要新做结局时请改用「结束章节」。
- 回放记录新增 `{"kind": "chapter", ...}`（章节进入），`goto` 触发器记录额外带
  `chapter_background`，回放时无需依赖当前章节配置即可复现。
  结束章节产生的步进记录额外带 `ending_chapter: true`，回放时按「结局」类型展示。
  读取回放的地方都按 `not entry.get("kind")` 识别普通步进：
  `engine._replay_next_step`、`persistence`、`archive_export._render_replay_entries`。
