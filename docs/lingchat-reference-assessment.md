# LingChat 对 `/dungeon` 的参考价值评估

调研对象：<https://github.com/SlimeBoyOwO/LingChat>（main 分支，2026-09-19 最后提交）
评估对象：本仓库 `dungeon/`（22 个 .py，含 `dungeon/window/`）
调研日期：2026-09-21

---

## 一、一句话结论

**有参考价值，但价值在「工程纪律」而非「引擎范式」，且只能借鉴设计、不能复制代码。**

- `/dungeon` 是**生成式步进引擎**（LLM 每步产出文本 + 数值演化驱动），LingChat 是**编排式事件引擎**（作者手写事件序列，AI 只在少数事件里被调用）。两者是相邻但不同的范式，`/dungeon` 的核心能力（转移矩阵、属性演化、伤亡结算、流式分句容错）在 LingChat 里**根本没有对应物**，不存在"抄过来升级"的空间。
- 真正值得抄的是 LingChat 在**配置治理**上的四项工程实践：schema 单一真相源、把静默失败变成可见诊断、原子写 + 备份、错误路径也要收尾。这四项恰好命中 `/dungeon` 现存的技术债。
- **许可证是硬阻断**：LingChat 为 **AGPL-3.0**，本项目为 **MIT**。任何直接复制代码都会污染整个项目，必须重新实现。

---

## 二、LingChat 项目画像

| 项 | 值 |
|---|---|
| 定位 | 沉浸式 AI-Galgame 聊天软件（桌宠 / 日程 / 剧情 / 羁绊冒险） |
| 技术栈 | **Tauri 2 + Vue 3 + TypeScript**（前端） / **Rust**（后端） / sea-orm + SQLite |
| 规模 | 2,220 stars · 129 forks · 约 140 MB · 5,518 commits · 88 open issues |
| 活跃度 | 创建于 2025-04，截至 2026-09-19 仍在高频提交（近两周仍有 CI/剧本相关改动） |
| 平台 | Windows / macOS / Linux / Android / iOS |
| **许可证** | **AGPL-3.0** ⚠️ |
| 工程化 | pnpm workspace、husky + prettier + rustfmt、`docs/` 下 20+ 篇架构文档与 HTML 图示 |

值得注意：LingChat 自身经历过 **Python → Rust 重写**。`script_engine/mod.rs` 注释明确写着 "Replaces Python's `ling_chat/core/ai_service/script_engine/` package"，且 events/mod.rs 记录了旧 Python 版的一个真实 bug（`SetVariableEvent` 重写了 `execute()` 而非 `_execute()` 导致静默失效）。**历史 Python 实现已从 main 分支移除，无法直接取用。**

---

## 三、代码组织方式对比

### LingChat：五层 + 单一入口

```
前端组件层   ScriptEditor.vue + 7 个 script-editor/* 子组件
    │ 读写
前端状态层   Pinia setup store（state / getters / actions）
    │ invoke
前端 API 层  api/services/script-editor.ts（纯封装 + 类型）
    ═══════════ Tauri IPC ═══════════
后端命令层   commands.rs（editor_* 前缀，全部读写唯一入口）
    │ 委托
后端子模块   schema.rs · paths.rs · io.rs · validate.rs
    │
磁盘         data/game_data/scripts/<剧本包>/
```

剧本引擎侧则是四件套，边界写得很清楚：

| 模块 | 职责 |
|---|---|
| `ScriptManager` | 剧本发现、生命周期、章节编排（`while next_chapter != "end"`） |
| `Chapter` | 包裹一个章节 YAML，顺序执行其 events，返回下一章名 |
| `EventsHandler` | 章节内顺序事件处理器 |
| `events/` | 16 种事件：trait + 注册表 + 具体实现 |
| `utils/` | 角色查找、变量运算、素材路径 |
| `responses.rs` | 发给前端的 Tauri 事件 payload 类型 |

### `/dungeon`：领域层清晰，UI 层是 9-mixin 巨型类

- **纯领域层**（无 UI 依赖）：`models.py` `rules.py` `chapters.py` `actions.py` `coupling.py` `response.py` `splitter.py` `summary.py` `details.py`
- **UI 层**：`window/` 全部 + `launcher.py` + `components.py`
- **胶水**：`window/__init__.py` 用 **9 个 mixin** 拼出 `DungeonSessionWindow`（base / UI / engine / triggers / options / ending / persistence / components / launcher）

**判断**：领域层的设计其实不差——`dungeon/__init__.py` 明确声明"这里不依赖具体 UI 框架"，`chapters.py` 开篇就写明"章节不带任何条件字段"。问题集中在 UI 层和领域→服务的反向依赖（`rules.py` 函数内 `import services.state_service`）。**LingChat 能教 `/dungeon` 的主要是第二、三层（配置治理与编辑器），不是领域层。**

---

## 四、逐项契合度评分

| LingChat 模块 | 对 `/dungeon` 契合度 | 说明 |
|---|---|---|
| `script_engine/` 事件注册表（trait + `register_event` / `create_event`） | ★★★☆☆ | `/dungeon/actions.py` **已有动作注册表**，形态接近。可借鉴的是它与 schema 联动、支持 `condition`/`duration` 公共字段 |
| `schema.rs` 事件 schema 单一真相源 | ★★★★★ | `/dungeon` **完全没有 schema**，只有 `normalize_*` + `_migrate`。缺口最大、收益最高 |
| `validate.rs` 校验器 | ★★★★★ | 直接对症：`/dungeon` 废弃动作（`background`/`sensitivity`）被静默跳过、异常只 `print`、生成失败 UI 无反馈 |
| `io.rs` 原子写 + `.bak` | ★★★★☆ | `/dungeon` 直接写 `config.json`，无备份。作者写坏方案无回退 |
| `on_script_end` 的 `completed` 标志 + 错误路径也 teardown | ★★★★★ | 直接对症：`/dungeon` 未触发结局就退出会整局丢失 |
| `adventures/` 解锁条件系统 | ★★★☆☆ | 与"挑战模式 `.chal`"有呼应，但 LingChat 只有 4 种条件类型，粒度偏粗 |
| `ScriptChannels` 输入/选择 oneshot 通道 | ★★☆☆☆ | `/dungeon` 是同步步进，不需要挂起等待；`DpgDispatcher` 已解决线程问题 |
| `is_preview` 试玩隔离 | ★★★☆☆ | 副本方案编辑器目前缺"试跑"能力，可借鉴其"试玩产出标记 `preview_gen`，前端丢弃迟到回复"的处理 |
| `evaluate_condition` 变量条件求值 | ★☆☆☆☆ | **比 `/dungeon` 弱**：仅支持 `==`/`!=`/真值判断，不支持 `>=` `<` `&&`；文档自承 "`hp >= 5` 会恒为假"。`/dungeon` 的 `TriggerRules` 支持 6 种比较符 + and/or + `count/ratio/trend/last` 四种度量 + `选择:x`/`伤亡数组` 序列。**这一项不要抄，抄了是降级** |
| 16 种事件类型（music / ambient / present_pic / modify_character…） | ★★☆☆☆ | `/dungeon` 已有 `background.py`、视觉特效、插入段；事件语义不同，参考价值有限 |
| Vue 前端组件 | ☆☆☆☆☆ | 技术栈完全不同（Vue vs Tk/CustomTkinter/DPG），零复用 |
| Rust 源码本体 | ☆☆☆☆☆ | 语言不同 + AGPL，零复用 |

---

## 五、值得借鉴的具体做法（建议重新实现，不要复制）

### 1. 事件/动作 schema 单一真相源 ★★★★★

**LingChat 怎么做**：`schema.rs::build_schema()` 是唯一真相源，导出 `ScriptSchema`（16 种事件的 `type_key / label / category / color / fields` + 公共字段 + action 类型 + 条件语法说明），前端只负责按 `FieldKind` 渲染 13 种控件。文档记录了动机："同一份 schema 此前散落三处（Rust 16 个 handler / 前端 `types/script.ts` / 原型编辑器 `constants/events.ts`），三者互不同步，直接导致原型产出的 `set_variable` / `chapter_end` 跑不通。"

**`/dungeon` 现状**：章节/触发器/动作的结构散落在 `chapters.py` 的 `normalize_chapter`、编辑器的 `ui/scenario/`、`window/engine.py` 的读取逻辑里，靠 `normalize_*` 与 `_migrate` 兜底，没有可机器校验的定义。

**建议**：在 `dungeon/` 领域层新增 `schema.py`，用一份声明式定义（dataclass 或 JSON Schema）描述章节字段、触发器字段、动作类型及其参数，同时驱动三处：① `normalize_*` 的补全逻辑 ② 编辑器表单 ③ 校验器。这一步做完，下面的 2 才有落点。

### 2. 把静默失败变成可见诊断 ★★★★★

**LingChat 怎么做**：`validate.rs` 用同一份 schema 做必填/未知字段检查，定位是"把引擎里的静默失败变成作者能看见的诊断"。`SetVariableEvent` 遇到不支持的 action 类型会 `tracing::warn!` 明确报出"此事件仅支持 set_var"，而不是静默丢弃——注释甚至点明"作者很容易以为两处通用"。

**`/dungeon` 现状**：
- `rules.py` 里 `is_interaction_chosen` 已废弃但保留在签名里，传入无任何效果；
- `sensitivity` 动作废弃后 `_decay_sensitivity_effects` 已成死代码，但配置里仍可能残留；
- `background` 动作直接跳过；
- 异常基本只 `print`，生成失败 UI 无反馈。

**建议**：加一个 `validate_dungeon_config()`，产出结构化诊断列表（错误/警告/提示三级），至少覆盖：废弃字段、goto 指向不存在的章节、结束章节内放 `option`/`goto`、`VISUAL_FILTERS` 与 `background.apply_filter` 不一致（注释已承认需手工同步）、触发器引用了不存在的自定义属性。在副本启动前与编辑器保存时各跑一次。

### 3. 原子写 + `.bak` ★★★★☆

**LingChat 怎么做**：`io.rs::atomic_write` —— 同目录临时文件 → `fsync` → `rename`；`backup_if_exists` 只保留最近一份 `.bak`。文档给出的理由很实在："原型编辑器是 `open(f,'w')` 直接截断再写，中途崩溃会把章节清零。"

**`/dungeon` 现状**：`persistence/dungeon_repo.py` 直接写 `data/packs/dungeons/<id>/config.json`。

**建议**：给 `dungeon_repo` 的写入路径套一层 `write_json_atomic()`（同目录 `.tmp` → `os.replace`），覆盖前留一份 `.bak`。改动量很小（一个工具函数 + 替换调用点），但对"编辑方案时崩溃/断电"的防护收益明确。

### 4. 错误路径也要收尾，且不能记为已完成 ★★★★★

**LingChat 怎么做**：`run_to_completion` 刻意写成顺序三步而非 `async {}.await`，因为 async block 会 move 走 `&mut ctx` 导致收尾无法执行。失败时先 `emit_error` 再 `on_script_end(ctx, is_running, completed=false)`，并且明确注释：

> "A failed run must not be recorded as completed — that would unlock follow-up adventures the player never actually finished."

**`/dungeon` 现状**：未触发结局就退出 → 整局丢失，只弹一个警告。回放是"录制重播"，不可续跑。

**建议**：把副本的收尾抽成单一函数 `_finalize(completed: bool)`，在异常/用户中断/正常结局三条路径上都调用；`completed=False` 时仍然写回放与报告（保住用户已生成的内容），但不写入 `endings.json`、不记挑战达成。这是本次评估里**性价比最高的一条**——直击现存痛点，且改动集中在 `window/ending.py` / `window/persistence.py`。

### 5. 解锁条件系统（供挑战模式参考）★★★☆☆

**LingChat 怎么做**：`adventures/trigger.rs::check_all_adventures` 遍历所有冒险，条件按 AND 求值，支持 `chat_count` / `time_range`（含跨午夜）/ `adventure_completed`（前置冒险）/ `achievement_unlocked` 四类；无条件则默认解锁。

**建议**：仅作思路参考。若要给挑战包加"解锁前置"，建议直接扩展 `/dungeon` 已有的 `TriggerRules`（它已支持 6 种比较符与 4 种度量，表达力更强），而不是引入 LingChat 那套更弱的条件语法。

### 6. 试玩隔离（`is_preview`）★★★☆☆

**LingChat 怎么做**：`ScriptContext.is_preview` 标记编辑器试玩，产出的 AI 回复带 `preview_gen` 标记，前端据此丢弃中止后迟到的流式回复。

**建议**：副本方案编辑器（`ui/scenario/`）目前没有试跑能力。可借鉴"预览产出打标记 + 不污染正式存档"这一层隔离语义，避免试玩写入 `data/archives/`。

---

## 六、明确不建议借鉴的部分

1. **不要替换步进引擎为事件队列。** `/dungeon` 的转移矩阵 + 属性演化 + 伤亡结算 + 流式分句是它的核心差异化能力，LingChat 无对应实现，替换 = 自废武功。
2. **不要抄 `evaluate_condition`。** 它比 `/dungeon` 的 `TriggerRules` 弱一个量级（无数值比较、无逻辑组合、无序列度量）。
3. **不要因为"它用 Rust 重写了 Python"就跟进。** LingChat 重写的动因是 Tauri 桌面端打包与跨平台（含 Android/iOS）；`/dungeon` 运行在 Python + Tk/CTk/DPG 栈上，没有这个动因，重写收益为负。
4. **不要复制任何源码。** 见下节。
5. **谨慎看待它的高迭代速度。** 5,518 commits / 88 open issues，架构仍在变动（PR #540 才刚新增整个 `script_editor` 后端模块）。借鉴其**已沉淀成文档**的部分（schema/validate/atomic-io），不要追它正在变动的实现细节。

---

## 七、许可证红线 ⚠️

| | LingChat | 本仓库 |
|---|---|---|
| 许可证 | **AGPL-3.0** | **MIT** |

AGPL-3.0 是强传染性 copyleft，且比 GPL 多一层"网络交互即触发源码披露"。**任何将 LingChat 源码（含改写、翻译、移植）并入本项目的行为，都会要求整个项目转为 AGPL-3.0**，这与现有 MIT 授权不可共存，且会摧毁项目对第三方分发的友好性。

**可行做法**：阅读其实现与文档、提炼设计模式与工程实践、用 Python 独立重新实现。思想与方法不受版权保护，代码受。若未来确需直接复用某段实现，必须先与 LingChat 作者另行取得授权许可。

---

## 八、落地优先级建议

| 优先级 | 动作 | 涉及文件 | 预期收益 |
|---|---|---|---|
| **P0** | 收尾路径统一 + `completed` 标志：异常/中断也落盘回放与报告 | `dungeon/window/ending.py`、`window/persistence.py` | 消除"未达结局就丢整局" |
| **P0** | 配置写入改为原子写 + `.bak` | `persistence/dungeon_repo.py` | 防止写坏方案无回退 |
| **P1** | 新增 `dungeon/schema.py` 作为字段定义单一真相源 | `dungeon/` 新增 | 为校验与编辑器打基础 |
| **P1** | 基于 schema 实现 `validate_dungeon_config()`，编辑器保存 + 副本启动前各跑一次 | `dungeon/` 新增 + `ui/scenario/` | 把静默失败变成可见诊断 |
| **P2** | 清理废弃字段与死代码（`is_interaction_chosen`、`_decay_sensitivity_effects`、`background` 动作） | `dungeon/rules.py`、`window/engine.py` | 减少误导 |
| **P2** | 合并 `EvolutionRules.evaluate_condition` 与 `TriggerRules.evaluate` 两份重复的条件求值 | `dungeon/rules.py` | 消除魔法串双写 |
| **P3** | 编辑器增加"试跑"模式，产出打 `preview` 标记不污染正式存档 | `ui/scenario/` | 借鉴 `is_preview` 隔离语义 |
| — | ~~引入事件队列引擎~~ / ~~跟进 Rust 重写~~ | — | **不建议** |

---

## 九、一句话给决策者

把 LingChat 当作**一份"剧本类应用该怎么治理配置与生命周期"的范例文档**来读（它的 `docs/script-editor/architecture.md` 尤其值得通读），而不是当作一个可以取零件的仓库。`/dungeon` 的引擎内核比它更适合本项目的生成式场景，需要补的是**工程纪律**这一课。
