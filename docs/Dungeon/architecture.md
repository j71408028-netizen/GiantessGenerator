# 副本架构说明（`dungeon/`）

> **定位**：`dungeon/` 的分层、模块地图、一次步进的数据流、收尾与自检脚本的**总索引**。
> 数据是配置结构看 [数据模型](script.md)，窗口细节看 [窗口文档](window.md)，
> 术语看 [术语表](domain_terms.md)。全系列入口见 [副本文档索引](README.md)。

适用范围：当前 `dungeon/` 的实现现状。

## 目录

| 节 | 内容 |
|---|---|
| §1 | 一句话概览与分层 |
| §2 | 术语速查 |
| §3 | 分层与依赖规则 |
| §4 | 领域层模块地图 |
| §5 | 一次步进的数据流 |
| §6 | 生命周期与统一收尾 |
| §7 | 配置治理链路 |
| §8 | 持久化布局与原子写 |
| §9 | 自检与守卫脚本 |
| §10 | 硬性约定 |
| §11 | 显示组件包 |

---

## 1. 一句话概览

副本是一台**生成式步进引擎**：每一步由 LLM 产出一段故事文本并给出方向，引擎按
「段落类型 → 转移概率 → 属性值演化 → 伤亡结算 → 触发器判定」推进，把作者写好的
**方案定义**跑成**一局玩家会话**。

三层边界由脚本强制：

| 层 | 位置 | 职责 | 禁令 |
|---|---|---|---|
| 领域层 | `dungeon/` 包根 | 纯玩法逻辑 | 禁 import `dearpygui` / `tkinter` / `customtkinter` / `services` / `ui` / `dungeon.window` |
| UI 层 | `dungeon/window/` | 窗口、DPG、宿主接线 | 禁 import `tkinter` / `customtkinter` / `ui`（宿主细节留在 `ui/common/tk_host.py`） |
| 编辑器 / 持久化 | `ui/scenario/`、`persistence/` | 方案编辑与落盘 | — |

纪律：**领域层不认识 GUI、不认识服务层；UI 层负责接线与注入；外部行为一律注入，不反向 import。**

## 2. 术语速查

| 概念 | 含义 | 持久化位置 | 命名 |
|---|---|---|---|
| **副本方案 Scenario** | 作者编写的**定义**：章节、触发器、提示词、演化属性、转移矩阵、显示组件、素材 | `data/packs/scenarios/<方案 id>/config.json`（+ `images/`、`components/`） | `scenario_*` |
| **一局 Run** | 某角色用某方案跑的一次会话：`DungeonState`、回放、结局达成、行动点数消耗 | `data/archives/<角色>/回放\|报告`；挑战模式写 `data/user/replays\|reports` | `dungeon_*` |

编辑器（`ui/scenario/`）编辑的是**方案**，`DungeonSessionWindow` 运行的是**一局**。
完整规则与兼容读法见 [术语表](domain_terms.md)。

## 3. 分层与依赖规则

约束写在 `dungeon/__init__.py`，由 `scripts/check_dungeon_layering.py` 用 AST 强制。

### 3.1 领域层（`dungeon/` 包根）

- 禁止 import：`dearpygui` / `customtkinter` / `tkinter` / `services.*` / `ui.*`
- 禁止反向依赖自己的 UI 子包：`dungeon.window`（含 `from .window import`）
- 禁止逃逸出包的相对导入：`from .. import`
- 需要外部行为时**依赖注入**（唯一现状：`EvolutionRules(step_decay=StateService.decay_step_rates)`，由 `window/base.py::_init_session` 传入）

### 3.2 窗口层（`dungeon/window/`）

- 所有需要 DPG / 服务层的代码在此；`background` `dispatcher` `launcher` `component_registry` `components` 均已从包根迁回 `window/`
- 宿主能力（尺寸/DPI、显隐、事件泵、收尾弹框、活动窗口登记、字体）一律经 `dungeon/window/host.py::HostPort` 取得，Tk 实现是 `ui/common/tk_host.py::TkHost`，构造时注入（`DungeonSessionWindow(..., host=TkHost(self))`）
- 端口方法表见 [窗口文档](window.md) §5-C8

### 3.3 依赖倒置清单

| 领域层需要的外部行为 | 注入方式 | 注入方 |
|---|---|---|
| 二阶量（步长）不适应边界衰减 | `EvolutionRules(step_decay=...)` | `window/base.py` 传 `StateService.decay_step_rates` |
| AI 生成 / 提示词取材 | `DungeonPromptBuilder(self)` 读窗口实例公开属性 | `window/base.py` |
| AI 客户端 | `self.ai_client`（`engine` 惰性检查，缺失时 `_show_ai_error`） | `window/base.py::create_client` |
| 剧情压缩用 AI | `StorySummarizer.maybe_compress(ai_client=...)`，失败回退内部算法 | `window/engine.py` |

## 4. 领域层模块地图

| 模块 | 职责 | 关键出口 |
|---|---|---|
| `models.py` | 运行态数据与枚举 | `DungeonTextType`（5 种段落类型 + 默认步进值）、`DungeonState`（坐标 / 自定义属性 / 计数 / 二阶步长） |
| `rules.py` | 演化与条件求值 | `EvolutionRules`（转移矩阵、分节步长、属性演化）、`TriggerRules.evaluate` |
| `chapters.py` | 章节模型与归一化 | `normalize_chapter(s)`、`normalize_chapters`、`find_chapter`、`is_terminating_chapter`、`overflow_jump_target`、`chapter_step_override`、`sensitivity_amount`、`matches_scope` |
| `actions.py` | 动作类型单一注册表 | `NEW_ACTIONS`（insert/option/effect/goto/none）、`ENDING_ACTION`（只读兼容）、`ACTION_LABELS`、`VISUAL_FILTERS` |
| `coupling.py` | 耦合等级与对应提示词 | `coupling_prompts`、`section_instruction`、`normalize_coupling_level` |
| `prompts.py` | 系统与玩家提示词构建 | `DungeonPromptBuilder.build_system_prompt / build_user_prompt` |
| `response.py` | AI 响应解析容错 | `extract_stream_text`、`parse_final_json` |
| `splitter.py` | 内置确定性分句 | `split_stream_units`（流式：首句上屏，余句排队）、`split_full_text`（终局定格） |
| `summary.py` | 剧情压缩与常驻记忆 | `StorySummarizer`（按块 / 按章节压缩 + 最近 N 段原文 + 关键事件 + 事实卡） |
| `details.py` | 细节探究 | `build_detail_query_prompt`、`parse_detail_queries`、`search_replay_details` |
| `schema.py` | 方案配置字段**单一真相源** | `SCENARIO_FIELDS` / `CHAPTER_FIELDS` / `TRIGGER_FIELDS` / `FieldSpec` / `empty_scenario_config()` |
| `validate.py` | 结构化诊断（error / warning / info） | `validate_scenario_config(config, scenario_dir=...)`、`has_errors`、`format_diagnostics` |
| `terms.py` | 术语与持久化契约常量 | `SCENARIO_ID_KEY`、`DEFAULT_SCENARIO_ID`、旧键迁移与兼容读取 |

### 4.1 窗口层 mixin 组装顺序

`dungeon/window/__init__.py`：
`DungeonWindowBase, DungeonLaunchStages, DungeonWindowUI, DungeonStoryEngine,
TriggerHandler, OptionHandler, EndingHandler, DungeonPersistence, ComponentHandler`

| Mixin | 文件 | 职责 |
|---|---|---|
| `DungeonWindowBase` | `base.py` | 窗口生命周期、方案配置加载与启动前校验、会话初始化、关闭请求 |
| `DungeonLaunchStages` | `launcher.py` | 入口阶段：动态背景轮播、方案选择、已达成结局图标循环、开始 / 回放 / 返回 |
| `DungeonWindowUI` | `ui.py` | UI 构建、流式文本揭示动画、布局自适应、鼠标 / 键盘 / 视口回调 |
| `DungeonStoryEngine` | `engine.py` | 步进核心：AI 生成、流式分句、属性演化、伤亡结算、收尾回调 |
| `TriggerHandler` | `triggers.py` | 触发器判定、章节进出、短暂视效、插入段落 |
| `OptionHandler` | `options.py` | 选项触发器：后台生成选项文字 → 弹窗 → 记录选择 |
| `EndingHandler` | `ending.py` | 章节敏感效果汇总、结局生成与结局文本构建 |
| `DungeonPersistence` | `persistence.py` | 结局结算、回放 / 报告落盘、统一收尾 `_finalize(completed)` |
| `ComponentHandler` | `components.py` | 显示组件实例的构建 / 重排 / 刷新 / 销毁接入 |

mixin 之间只通过 `self` 上的约定属性通信；新增方法时注意排在前面的 mixin 会覆盖同名方法。

### 4.2 段落步进与转移矩阵

- 默认步进值由 `models.DungeonTextType.step_value` 给出（background 0.02 → action 0.3）
- `section_steps` 可逐项覆盖；`transition_matrix` 按**行覆盖**：

| 规则 | 行为 |
|---|---|
| 配置里出现的行 | **整行替换**（行内没写的列按 0） |
| 未出现的行 | 沿用内置默认 `EvolutionRules.DEFAULT_TRANSITION_MATRIX` |
| 脏权重 | 统一转 float、负值按 0、未知行列键跳过（已在校验阶段报 warning） |
| 整行无效 | 全 0 或解析不出有效权重时回退默认行 |

## 5. 一次步进的数据流

```
点击 / 空格 → DungeonStoryEngine._on_next_step()
   ├─ 回放模式 ─────────────► _replay_next_step()（按记录重放，不判定条件）
   ├─ 动画未播完 ───────────► _finish_text_animation()
   ├─ 有待揭示的显示段落 ────► _reveal_pending_unit()
   ├─ 有待消费的插入段落 ────► _consume_pending_insertion()
   └─ 正常推进 ─────────────► _generate_next_text()   [后台线程]

_generate_next_text()
  1. next_type = dungeon_logic.get_next_text_type(current_text_type)   结束章节内强制 → BACKGROUND
  2. prompt    = prompt_builder.build_user_prompt(next_type)           含概要 / 最近段落 / 细节补充
  3. AI 流式   → extract_stream_text → split_stream_units              首句流式上屏，余句入 _pending_units
  4. 末块      → parse_final_json → (正文, direction, custom_directions)
  5. 演化      → dungeon_logic.evolve_attributes(...)                  step_override=0 时整段冻结
  6. 解锁写入  → _apply_prompted_unlocks(正文) → _finish_step(...)
  7. 后台      → _start_detail_query(正文)                             下次生成前用回放缓存解答

_finish_step()
  a. _apply_visual_effects()        短暂视效倒计时递减
  b. _record_casualties()           按身高 / 步长 / 破坏性算伤亡，追加 casualty_evolution
  c. replay_data.append(step_info)  一步一条完整快照
  d. _check_unlock_coord()          身材解锁
  e. summarizer.record_key_event()  坐标跨整数阈值的里程碑
  f. summarizer.record/maybe_compress()  剧情压缩（默认每 20 段 / 离开章节时）
  g. check_triggers()               见 §5.1
  h. _check_chapter_overflow()      触发器之后再判超限，避免同一步二次跳转
```

### 5.1 触发器判定（`triggers.py::check_triggers`）

条件 = 三者同时成立：

1. `chapter` 作用域（空 = 任意 / `__none__` = 无章节 / 章节名）
2. `precondition_names` 已全部触发过
3. `condition` 规则组（`TriggerRules.evaluate`）

命中后按动作执行：`insert`（插入段落，可延迟到下一段的衔接地）/ `option`（弹选项）/
`effect`（限时滤镜）/ `goto`（进入或离开章节）/ `none`（仅标记条件成立）；
外加只读兼容的 `ending`（旧配置，新方案改用**结束章节**）。
未知动作走通用跳过路径。字段与参数详见 [数据模型](script.md) §4–§6。

### 5.2 结束路径

结束章节（章节属性 `ending=true`）内步进恒为 0——坐标、自定义属性、步长全部冻结，仍累计计数；
达到 `max_paragraphs` 时由 `_terminate_from_ending_chapter()` 用章节自身的结算字段
（增量 / 伤亡步进 / 行动点返还 / 图标）生成结局。

## 6. 生命周期与统一收尾

入口阶段与会话阶段共享同一个 DPG 上下文（`base.py` + `launcher.py`），详见
[窗口文档](window.md) §2。收尾只走一个入口：

```python
DungeonPersistence._finalize(completed: bool, reason: str = "")
```

| 路径 | 触发方 | `completed` | 行为 |
|---|---|---|---|
| 正常结局 | `EndingHandler._generate_ending` | `True` | 结算结局增量 → 落角色 → 写结局索引 → 按设置自动保存回放与报告 |
| 结局已触发但生成线程未收尾 | `_handle_exit`（reason「结局已触发，生成未结束」） | `True` | 同上，避免「生成到一半退出」被误判为未完成 |
| 用户中断、未触发结局 | `_handle_exit`（窗口关闭统一走这里） | `False` | **不**结算、**不**写 `endings.json`、**不**记挑战达成；已生成内容落盘为「未完成」回放 + 报告 |
| 生成异常 | `_note_session_error`（不结束会话，可重试） | 视退出时是否已触发结局而定 | 同上；报告里附会话期间的异常摘要 |

`_finalized` 标志保证幂等（多条路径抢跑时只有第一次生效）。
这是针对「未触发结局就退出导致整局丢失」的修复：**没真正走完的一局不算达成，但内容必须保住。**

## 7. 配置治理链路（schema → validate → 运行时）

```
dungeon/schema.py ──字段声明（FieldSpec）──┬──► ScenarioRepo.empty_scenario_config()  空方案模板
   单一真相源                              ├──► dungeon/validate.py                  结构化诊断
                                           └──► ui/scenario/*                        编辑器表单（按声明渲染）
normalize_chapter / ScenarioRepo._migrate（手写，行为经过实战）
        └── 由 scripts/check_scenario_schema.py 守卫「声明 ↔ 产出」字段一致
```

| 时机 | 位置 | 力度 |
|---|---|---|
| 编辑器保存 | `ScenarioRepo.save_config()` | 计算并留存诊断（warning / info 不阻断） |
| 副本启动前 | `window/base.py::_load_session_config()` | **error 阻止进入副本**，warning 打到控制台 |
| 离线批量 / CI | `scripts/validate_scenarios.py` | 有 error 时退出码 1 |

诊断分三级：`error`（悬空跳转目标、无起始章节、空选项列表…）/ `warning`（未知条件键、废弃动作、失效前置引用、资产缺失…）/ `info`（无结局路径、旧版字段、未知顶层键…）。

## 8. 持久化布局与原子写

```
data/
  packs/scenarios/<方案 id>/
      config.json            方案定义（+ .bak）
      images/                背景图 / 结局图标
      components/            显示组件包（官方包随分发提供）
  archives/<角色档案目录>/           目录名 = 角色的 giantess_id
      info.json  avatar/             档案本体与形象
      回放/<名>_回放_<时间戳>[_未完成].replay.json
      报告/<名>_报告_<时间戳>[_未完成].txt
  user/
      endings.json  replays/  reports/  （挑战模式与无角色场景）
      settings.json  api_keys.json  address_registry.json
```

写盘统一走 `persistence/json_store.py`：**同目录临时文件 → fsync → 覆盖前留一份 `.bak` → `os.replace`**；
读取用 `load_json_with_backup`（主文件损坏时自愈回退 `.bak`）。禁止再出现 `open(path, 'w')` 直接截断写。

## 9. 自检与守卫脚本

见 [副本文档索引](README.md) §4（无 GUI 六个 + GUI 冒烟一个）。
改动领域层 import、演化链路、收尾路径或配置结构后跑对应脚本；以上全部通过才算绿。

## 10. 硬性约定

| 约定 | 说明 | 出处 |
|---|---|---|
| 数据类一律按关键字构造 | `DungeonState` 字段曾按位置传参导致伤亡数组被写成浮点数 | `models.py::clone` 注释 |
| 构造参数必须保存到 `self` | mixin 方法可能被其他 mixin 调用，`__init__` 里的局部变量会 AttributeError | [窗口文档](window.md) §5-C3 |
| 关闭一律走 `_request_close()` | 业务代码不直接 `dpg.stop_dearpygui()` | [窗口文档](window.md) §5-C1 |
| 跨线程 UI 更新走 `self._frame.call()` | 帧时钟是**窗口实例成员**；计时类逻辑用 `every/after` 帧任务而不是开线程 | [窗口文档](window.md) §5-C4、`window/frame.py` |
| `destroy_context()` 之后不碰 Tk | 会 0xC0000005；`_finish_session()` 的顺序就是为此固定的 | [窗口文档](window.md) §5-C2 |
| 写盘必走原子写 | 半截文件不可恢复 | 本文 §8 |
| 剧本 / 运营术语不混用 | `scenario_*` = 方案，`dungeon_*` = 一局 | [术语表](domain_terms.md) |
| 组件只读窗口状态 | 组件 ctx 即窗口实例，不反向写状态 | `component_registry.py` / `components.py` |

## 11. 显示组件包

官方组件包位于 `assets/components/`（随应用分发的只读资源，经 `paths.dungeon_components_dir()`
定位）。文本主组件由方案配置的 `text_component` 字段**三选一**声明（text / text_card /
text_nvl），`components` 列表只放其余组件（如属性条、过程日志），参数统一记在
`components_params`。组件类只需实现 `build / layout / refresh / destroy` 四个钩子，
由 `ComponentHandler` 接入窗口的更新链（主组件先建、z 序在底）；
可用性与参数声明来自 `component_registry`，编辑器侧由 `ui/scenario/component_mgr.py`
三选一控件 + 卡片化管理。

---

## 历史

本文件只描述**现状**。以下是相关档案（不据此改代码）：

| 档案 | 内容 |
|---|---|
| [副本模型与命名演进](history/model_evolution.md) | 章节 / 触发器模型拆分、动作类型退场、scenario / dungeon 命名拆分与目录迁移、配置治理由来（S1–S4） |
| [宿主改造档案](history/host_refactor.md) | 窗口层手动渲染、构造/运行分离、宿主端口、帧时钟、结果对象化 |
| [LingChat 参考价值评估](history/lingchat_evaluation.md) | 同人作品借鉴价值评估与「不采纳」清单 |

其中一条属于**仍有效的现状约束**，不是历史：LingChat 为 AGPL-3.0、本项目为 MIT，
复制 / 改写 / 移植其源码会强制本项目转为 AGPL——只读其文档与实现、用 Python 独立重写是唯一可行做法。
