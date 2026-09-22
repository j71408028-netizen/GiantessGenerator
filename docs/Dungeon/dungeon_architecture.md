# 副本架构说明（`dungeon/`）

适用范围：本次分层重构（S1 命名规范 → S2 配置治理 → S3 死代码清理 → S4 分层修正）落地后的现状。
写于 2026-09-22。

配套文档：

- `docs/dungeon_chapters.md`：数据模型细则（章节 / 触发器 / 动作 / 剧情压缩）
- `docs/dungeon_window.md`：会话窗口（DPG）生命周期、线程模型与开发注意事项
- `docs/domain_terms.md`：**方案（Scenario）** 与 **一局（Run）** 的术语与命名约定

---

## 1. 一句话概览

副本是一台**生成式步进引擎**：每一步由 LLM 产出一段故事文本并给出方向，引擎按「段落类型 → 转移概率 → 属性值演化 → 伤亡结算 → 触发器判定」推进，
把作者用编辑器写好的**方案定义**跑成**一局玩家会话**。

整包代码按三层切开，边界由脚本强制：

```
┌──────────────┐  编辑/保存    ┌───────────────────────┐   加载    ┌─────────────────────────┐
│ ui/scenario/ │ ───────────► │ persistence/          │ ───────► │ dungeon/window/         │
│ 方案编辑器    │ ◄─────────── │ ScenarioRepo          │ ◄─────── │ DungeonSessionWindow    │
│              │  诊断(JSON)  │ JsonStore（原子写+bak）│  读配置   │ （DPG，9 个 mixin）      │
└──────────────┘              └───────────────────────┘          └───────────┬─────────────┘
       │                                 │                                    │ 调用 / 注入
       │ 字段声明 ←───────────────────────┤                                    │
       ▼                                 ▼                                    ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ dungeon/ 根（纯领域层：models rules chapters actions coupling prompts response          │
│            splitter summary details schema validate terms）                             │
│ · 禁止 import dearpygui / tkinter / customtkinter / services / ui / dungeon.window      │
│ · 外部行为靠注入，例如 EvolutionRules(step_decay=StateService.decay_step_rates)          │
└────────────────────────────────────────────────────────────────────────────────────────┘
                                              ▲
                                              │ AST 强制：scripts/check_dungeon_layering.py
```

一句话纪律：**领域层不认识 GUI、不认识服务层；UI 层负责接线与注入；外部行为一律注入，不反向 import。**

---

## 2. 术语（先分清，代码里不许混用）

| 概念 | 含义 | 持久化位置 | 命名约定 |
|---|---|---|---|
| **副本方案 Scenario** | 作者编写的**定义**：章节、触发器、提示词、演化属性、转移矩阵、显示组件、素材 | `data/packs/scenarios/<方案 id>/config.json`（+ `images/`、`components/`） | `scenario_*` |
| **一局 Run** | 某角色用某方案跑的一次会话：`DungeonState`、回放、结局达成、行动点数消耗 | `data/archives/<角色>/回放|报告`，挑战模式写 `data/user/replays|reports` | `dungeon_*` |

编辑器（`ui/scenario/`）编辑的是**方案**，`DungeonSessionWindow` 运行的是**一局**。详见 `docs/domain_terms.md`。

---

## 3. 分层与依赖规则

### 3.1 `dungeon/` 根 = 纯领域层

约束写在 `dungeon/__init__.py`，由 `scripts/check_dungeon_layering.py` 用 AST 强制（CI / 手工跑均可）：

- 禁止 import：`dearpygui` / `customtkinter` / `tkinter` / `services.*` / `ui.*`
- 禁止反向依赖自己的 UI 子包：`dungeon.window`（含 `from .window import`）
- 禁止逃逸出包的相对导入：`from .. import`
- 需要外部行为时**依赖注入**（唯一现状：`EvolutionRules(step_decay=StateService.decay_step_rates)`，由 `window/base.py::_init_session` 传入）

### 3.2 `dungeon/window/` = UI 层

所有需要 DearPyGui / 服务层的代码在这里：`background` `dispatcher` `launcher` `component_registry` `components` 均已从包根迁回 `window/`，包根现在是字面意义的纯领域。

会话窗口由 9 个 mixin 拼装（`dungeon/window/__init__.py`）：

| Mixin | 文件 | 职责 |
|---|---|---|
| `DungeonWindowBase` | `base.py` | 窗口生命周期、方案配置加载与启动前校验、会话初始化、关闭请求 |
| `DungeonLaunchStages` | `launcher.py` | 入口阶段：动态背景轮播、方案选择、已达成结局图标循环、开始/回放/返回 |
| `DungeonWindowUI` | `ui.py` | UI 构建、流式文本揭示动画、布局自适应、鼠标/键盘/视口回调 |
| `DungeonStoryEngine` | `engine.py` | 步进核心：AI 生成、流式分句、属性演化、伤亡结算、收尾回调 |
| `TriggerHandler` | `triggers.py` | 触发器判定、章节进出、短暂视效、插入段落 |
| `OptionHandler` | `options.py` | 选项触发器：后台生成选项文字 → 弹窗 → 记录选择 |
| `EndingHandler` | `ending.py` | 章节敏感效果汇总、结局生成与结局文本构建 |
| `DungeonPersistence` | `persistence.py` | 结局结算、回放/报告落盘、统一收尾 `_finalize(completed)` |
| `ComponentHandler` | `components.py` | 显示组件实例的构建 / 重排 / 刷新 / 销毁接入 |

组装顺序即上表顺序；mixin 之间只通过 `self` 上的约定属性通信（构造参数必须保存到 `self`，见 `docs/dungeon_window.md` §5.2）。

### 3.3 依赖倒置清单

| 领域层需要的外部行为 | 注入方式 | 注入方 |
|---|---|---|
| 二阶量（步长）不适应边界衰减 | `EvolutionRules(step_decay=...)` | `window/base.py` 传 `StateService.decay_step_rates` |
| AI 生成 / 提示词取材 | `DungeonPromptBuilder(self)` 读窗口实例的公开属性 | `window/base.py` |
| AI 客户端 | `self.ai_client`（`engine` 惰性检查，缺失时 `_show_ai_error`） | `window/base.py::create_client` |
| 剧情压缩用 AI | `StorySummarizer.maybe_compress(ai_client=...)` 可选传入，失败回退内部算法 | `window/engine.py` |

---

## 4. 领域层模块地图

| 模块 | 职责 | 关键出口 |
|---|---|---|
| `models.py` | 运行态数据与枚举 | `DungeonTextType`（5 种段落类型 + 默认步进值）、`DungeonState`（坐标/自定义属性/计数/二阶步长） |
| `rules.py` | 演化与条件求值 | `EvolutionRules`（转移矩阵、分节步长、属性演化）、`TriggerRules.evaluate`（条件规则求值） |
| `chapters.py` | 章节模型与归一化 | `normalize_chapter(s)`、`find_chapter`、`is_terminating_chapter`、`overflow_jump_target`、`chapter_step_override`、`sensitivity_amount`、`matches_scope` |
| `actions.py` | 动作类型单一注册表 | `NEW_ACTIONS`（insert/option/effect/goto/none）、`ENDING_ACTION`（只读兼容）、`ACTION_LABELS`、`VISUAL_FILTERS` |
| `coupling.py` | 耦合等级（velum/solea/bulla）与对应提示词 | `coupling_prompts`、`section_instruction`、`normalize_coupling_level` |
| `prompts.py` | 系统与玩家提示词构建 | `DungeonPromptBuilder.build_system_prompt/build_user_prompt` |
| `response.py` | AI 响应解析容错 | `extract_stream_text`（流式裸文本）、`parse_final_json`（末块 JSON → 文本/direction/自定义方向） |
| `splitter.py` | 内置确定性分句 | `split_stream_units`（流式：首句上屏，余句排队）、`split_full_text`（终局定格） |
| `summary.py` | 剧情压缩与常驻记忆 | `StorySummarizer`（按块/按章节压缩 + 最近 N 段原文 + 关键事件 + 事实卡） |
| `details.py` | 细节探究 | `build_detail_query_prompt`、`parse_detail_queries`、`search_replay_details` |
| `schema.py` | 方案配置字段**单一真相源** | `SCENARIO_FIELDS` / `CHAPTER_FIELDS` / `TRIGGER_FIELDS` / `FieldSpec` / `empty_scenario_config()` |
| `validate.py` | 结构化诊断（error/warning/info） | `validate_scenario_config(config, scenario_dir=...)`、`has_errors`、`format_diagnostics` |
| `terms.py` | 术语与持久化契约常量 | `SCENARIO_*` 键名、旧键迁移、兼容读取 |

段落类型的**默认步进值**由 `models.DungeonTextType.step_value` 给出（background 0.02 → action 0.3）；
方案配置里的 `section_steps` 可逐项覆盖，`transition_matrix` 按下述语义覆盖行：

- 配置里**出现的行整行替换**（行内没写的列按 0），未出现的行**沿用内置默认**；
- 权重统一转 float、负值按 0、未知行列键跳过（脏数据已在校验阶段报 warning）；
- 整行权重全为 0 或解析不出有效权重时回退默认行，写坏了也不会乱跳。

---

## 5. 一次步进的数据流

```
鼠标点击 / 空格
    └─► DungeonStoryEngine._on_next_step()
          ├─ 回放模式 ───────────────► _replay_next_step()（按记录重放，不判定条件）
          ├─ 动画未播完 ────────────► _finish_text_animation()
          ├─ 有待揭示的显示段落 ────► _reveal_pending_unit()
          ├─ 有待消费的插入段落 ────► _consume_pending_insertion()
          └─ 正常推进 ─────────────► _generate_next_text()   [后台线程]

_generate_next_text()
  1. next_type = dungeon_logic.get_next_text_type(current_text_type)
     结束章节内强制 → BACKGROUND
  2. prompt   = prompt_builder.build_user_prompt(next_type)   （含概要/最近段落/细节补充）
  3. AI 流式  → extract_stream_text → split_stream_units（首句流式上屏，余句入 _pending_units）
  4. 末块     → parse_final_json → (正文, direction, custom_directions)
  5. 演化     → dungeon_logic.evolve_attributes(...)          （step_override=0 时整段冻结）
  6. 解锁写入  → _apply_prompted_unlocks(正文) → _finish_step(...)
  7. 后台     → _start_detail_query(正文)（下次生成前用回放缓存解答）

_finish_step()
  a. _apply_visual_effects()        短暂视效倒计时递减
  b. _record_casualties()           按身高/步长/破坏性算伤亡，追加 casualty_evolution
  c. replay_data.append(step_info)  一步一条完整快照（前后坐标、前后步长、自定义属性、方向…）
  d. _check_unlock_coord()          身材解锁
  e. summarizer.record_key_event()  坐标跨整数阈值的里程碑（常驻记忆）
  f. summarizer.record/maybe_compress()  剧情压缩（默认每 20 段 / 离开章节时）
  g. check_triggers()               见下
  h. _check_chapter_overflow()      触发器之后再判超限，避免同一步二次跳转
```

**触发器判定**（`triggers.py::check_triggers`）：

条件 = ①`chapter` 作用域（空=任意 / `__none__`=无章节 / 章节名）②`precondition_names` 已触发 ③`condition` 规则组（`TriggerRules.evaluate`）。
命中后按动作执行：`insert`（插入段落，可延迟到下一段的衔接地）/ `option`（弹选项）/ `effect`（限时滤镜）/ `goto`（进入或离开章节）/ `none`（仅标记条件成立）
外加只读兼容的 `ending`（旧配置；新方案改用**结束章节**）。未知动作走通用跳过路径，运行时不再有旧动作专属分支。

**结束路径**：结束章节（章节属性 `ending=true`）内步进恒为 0——坐标、自定义属性、步长全部冻结，仍累计计数；
达到 `max_paragraphs` 时由 `_terminate_from_ending_chapter()` 用章节自身的结算字段（增量 / 伤亡步进 / 行动点返还 / 图标）生成结局。

---

## 6. 生命周期与统一收尾

入口阶段与会话阶段共享同一个 DPG 上下文（`base.py` + `launcher.py`），详见 `docs/dungeon_window.md` §2。收尾只走一个入口：

```python
DungeonPersistence._finalize(completed: bool, reason: str = "")
```

| 路径 | 触发方 | `completed` | 行为 |
|---|---|---|---|
| 正常结局 | `EndingHandler._generate_ending`（`ending.py:120`） | `True` | 结算结局增量 → 落角色 → 写结局索引 → 按设置自动保存回放与报告 |
| 结局已触发但生成线程未收尾 | `_handle_exit`（`persistence.py:399`） | `True`（reason「结局已触发，生成未结束」） | 同上，避免"生成到一半退出"被误判为未完成 |
| 用户中断、未触发结局 | `_handle_exit`（`persistence.py:401`，窗口关闭统一走这里） | `False` | **不**结算、**不**写 `endings.json`、**不**记挑战达成；已生成内容落盘为「未完成」回放 + 报告 |
| 生成异常 | `_note_session_error`（不结束会话，可重试） | 视退出时是否已触发结局而定 | 同上；报告里附会话期间的异常摘要 |

`_finalized` 标志保证幂等（多条路径抢跑里只有第一次生效）。

`_finalized` 标志保证幂等。这是针对「未触发结局就退出导致整局丢失」的修复：**没真正走完的一局不算达成，但内容必须保住。**

---

## 7. 配置治理链路（schema → validate → 运行时）

```
dungeon/schema.py  ──字段声明（FieldSpec）──┬──►  ScenarioRepo.empty_scenario_config()  空方案模板
  单一真相源                                ├──►  dungeon/validate.py                  结构化诊断
                                            └──►  ui/scenario/*                        编辑器表单（按声明渲染）
normalize_chapter / _migrate（手写，行为经过实战）
        └── 由 scripts/check_scenario_schema.py 守卫「声明 ↔ 产出」字段一致，防止再次漂移
```

三个接入点共用同一份规则：

| 时机 | 位置 | 力度 |
|---|---|---|
| 编辑器保存 | `ScenarioRepo.save_config()` | 计算并留存诊断（warning/info 不阻断） |
| 副本启动前 | `window/base.py::_load_session_config()` | **error 阻止进入副本**，warning 打到控制台 |
| 离线批量 / CI | `scripts/validate_scenarios.py` | 有 error 时退出码 1 |

诊断分三级：`error`（悬空跳转目标、无起始章节、空选项列表…）/ `warning`（未知条件键、废弃动作、失效前置引用、资产缺失…）/ `info`（无结局路径、旧版字段、未知顶层键…）。

---

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

写盘统一走 `persistence/json_store.py`：**同目录临时文件 → fsync → 覆盖前留一份 `.bak` → `os.replace`**，
读取用 `load_json_with_backup`（主文件损坏时自愈回退 `.bak`）。禁止再出现 `open(path, 'w')` 直接截断写。

---

## 9. 自检与守卫脚本（无 GUI，不碰真实 `data/`）

| 脚本 | 覆盖 | 命令 |
|---|---|---|
| `scripts/check_dungeon_layering.py` | **分层守卫**：领域层不得 import UI/服务层、不得反向依赖 window、不得越包相对导入 | `python scripts/check_dungeon_layering.py` |
| `scripts/check_scenario_schema.py` | schema 单一真相源（空模板 golden、字段漂移）+ 校验器规则 + 演化配置链路（`transition_matrix`/`section_steps` 真进运行时，含 `base.py` 接线 AST 守卫） | 同上 |
| `scripts/check_dungeon_finalize.py` | 原子写/`.bak`/损坏回退 + `_finalize` 完成与未完成两条路径 | 同上 |
| `scripts/check_scenario_naming.py` | 命名守卫（仓库不得再出现 `dungeon_id` / `DungeonRepo` 等旧标识符）+ 兼容读与迁移行为 | 同上 |
| `scripts/validate_scenarios.py` | 离线批量校验所有/单个方案，error 退出码 1 | `python scripts/validate_scenarios.py --errors-only` |
| `scripts/check_platform_compat.py` | Windows 专有 API 使用点清单（发布 macOS 前必读） | `python scripts/check_platform_compat.py` |
| `scripts/dungeon_autopilot.py` | **GUI 冒烟**（需显示器，不进无 GUI 门禁）：真窗口生命周期——构造 → 步进 → 关闭 → 落盘，脚本代替人手点击，不联网、不碰真实 `data/` | `python scripts/dungeon_autopilot.py` |

改动领域层 import、演化链路、收尾路径或配置结构后，跑对应脚本；以上全部通过才算绿。
最后一行是 GUI 冒烟层：它需要显示器与真实窗口，单独跑，细节见
`docs/Dungeon/dungeon_window_automation.md`。

---

## 10. 硬性约定（踩过坑的那几条）

| 约定 | 说明 | 出处 |
|---|---|---|
| 数据类一律按关键字构造 | `DungeonState` 字段曾按位置传参导致伤亡数组被写成浮点数 | `models.py::clone` 注释 |
| 构造参数必须保存到 `self` | mixin 方法可能被其他 mixin 调用，`__init__` 里的局部变量会 AttributeError | `dungeon_window.md` §5.2 |
| 不在 DPG 回调内 `stop_dearpygui()` | 帧中途停止会破坏堆，统一走 `_request_close` + `_dispatch` | `dungeon_window.md` §5.1 / `dispatcher.py` |
| `_dispatch` 是模块级单例 | 所有跨线程 UI 更新都经它排队到下一帧 | `dungeon_window.md` §5.3 |
| 写盘必走原子写 | 半截文件不可恢复 | 本文 §8 |
| 剧本/运营术语不混用 | `scenario_*` = 方案，`dungeon_*` = 一局 | `domain_terms.md` |
| 组件只读窗口状态 | 组件 ctx 即窗口实例，不反向写状态 | `component_registry.py` / `components.py` |

---

## 11. 显示组件包

官方组件包位于 `data/packs/scenarios/_default/components/`，方案在 `config.json` 的 `components` 字段按 id 声明启用项（未声明回退 `["text"]`），参数记在 `components_params`。
组件类只需实现 `build / layout / refresh / destroy` 四个钩子，由 `ComponentHandler` 接入窗口的更新链；可用性与参数声明来自 `component_registry`，编辑器侧由 `ui/scenario/component_mgr.py` 卡片化管理（开关与参数改动自动保存）。

---

## 附：原《LingChat 参考价值评估》结论摘要

旧文评估的是同人作品 LingChat（Tauri 2 + Vue 3 + Rust，AGPL-3.0）对本仓库的借鉴价值，以下三点是仍然有效的结论，其余内容已随本次重构过期：

1. **许可证红线**：LingChat 为 AGPL-3.0，本项目为 MIT。任何复制、改写、移植其源码的行为都会强制本项目转为 AGPL。**只读它的文档与实现，用 Python 独立重写**，这是唯一可行做法。
2. **已吸收的四项工程实践**（均已在本仓库重新实现，非复制）：
   - schema 单一真相源 → `dungeon/schema.py`
   - 把引擎里的静默失败变成作者可见的诊断 → `dungeon/validate.py`
   - 原子写 + `.bak` → `persistence/json_store.py`
   - 错误路径也收尾且不记为完成 → `DungeonPersistence._finalize(completed)`
3. **明确不采纳**：不替换为事件队列引擎（生成式步进是本项目核心差异）；不抄其 `evaluate_condition`（表达力弱于本项目 `TriggerRules`）；不跟进 Rust 重写（没有桌面端打包动因）。
