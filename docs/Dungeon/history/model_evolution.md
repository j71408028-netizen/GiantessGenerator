# 副本模型与命名演进档案

> **状态：已归档。**
> **现状见**：[数据模型](../script.md)（字段与运行时行为）、
> [术语表](../domain_terms.md)（命名规则与兼容读法）、[架构说明](../architecture.md)（分层）。
> 本文记录这些形状是**怎么变成现在这样的**，以及仍在运行的兼容包袱从哪来。

关联重构：S1 命名规范 → S2 配置治理 → S3 死代码清理 → S4 分层修正。

---

## 1. 章节 / 触发器模型

早期版本把条件与动作塞在**同一个无层级的触发器**里，动作类型有六种：插入文本、弹出选项、
性格敏感化、结局、背景切换、空触发器（辅助构建）。

演进后的边界（现状）：

| 模型 | 现在的职责 |
|---|---|
| 章节 | 只描述「身处其中时的环境」——特定背景、持续敏感效果、编辑器配色；**没有任何条件字段** |
| 触发器 | 唯一的流程驱动者；进入 / 离开章节也必须由它的「跳转章节」动作执行 |

被章节属性接管的旧能力：

- **背景切换** → 章节的 `background` 字段
- **性格敏感化**（早期拼写 `sensitive`）→ 章节的 `sensitivity` 字段

动作类型因此拆成「单步行为」与「章节跳转」两类，注册表收敛到 `dungeon/actions.py`。

## 2. 动作类型的退场

| 动作 | 结局 | 现状 |
|---|---|---|
| `background`（背景切换） | 由章节 `background` 承担 | **运行时不认识**：不在注册表里，落入「未知的触发器动作类型」通用路径（不执行、不记入已触发集合、不重置间隔计数，只打印一行提示）；旧回放里的同类记录同样忽略；编辑器显示为「旧版·已失效」；校验器 `LEGACY_ACTIONS` 报专项 warning |
| `sensitivity`（早期拼写 `sensitive`） | 由章节 `sensitivity` 承担 | 同上 |
| `ending`（结局） | 迁移为**结束章节**（`ending: true` 的章节） | **仍然可以运行**（`ENDING_ACTION = "ending"` 保留），编辑器不再提供新建入口，列表里显示为「结局（旧配置）」 |

设计取舍：配置**不被自动改写**——旧版动作留着只是不再执行，需要清理时由作者在编辑器里删除；
校验阶段就报 warning，而不是让运行时静默吞掉。

`action_data["type"]` 与 `action_type` 冗余存在于旧配置中；`normalize_action_type`
只做「动作字段缺失时回退 `action_data["type"]`」，不再做任何旧别名转换。

## 3. 命名拆分（S1.5）：方案 vs 一局

**S1.5 之前两者混用 `dungeon` 一个词根**：

- `DungeonRepo` 实际持久化的是**方案定义**；
- `dungeon_id` 在编辑器里又叫 `scenario_id`——同一值在一次调用链里换名。

拆分后（现状）：`scenario_*` = 作者写的定义，`dungeon_*` = 玩家跑的一局。目录与资源键也一并迁移：

| 旧 | 新 | 兼容方式 |
|---|---|---|
| `data/packs/dungeons/` | `data/packs/scenarios/` | `ScenarioRepo.__init__` 就地并入（幂等；同名保留 `config.json` 较新的一份，旧的留 `.bak`） |
| 世界包 `resources["dungeons"]` | `resources["scenarios"]` | `WorldPackManifest.from_dict` 归一化；成员核对同时接受包内两种路径 |
| 存档字段 `dungeon_id` / `dungeon_config` | `scenario_id` / `scenario_config` | 读取走 `dungeon.terms.scenario_id_of()` / `scenario_config_of()`；`scripts/migrate_scenario_naming.py` 可一次性改写 |

`.chal` 挑战包是加密格式，不做改写，读取侧兼容。

现状的命名规则与守卫脚本见 [术语表](../domain_terms.md)。

## 4. 配置治理（S2）：schema 单一真相源

由来：`transition_matrix` 与 `section_steps` 曾**只在 config.json 里生效、没进运行时**——
作者在编辑器里改了，副本跑起来却用默认值。修法是让 `window/base.py::_init_session` 把它们传给
`EvolutionRules`，并新增 `scripts/check_scenario_schema.py` 守卫这条链路。

同一时期确立的还有：

| 实践 | 落地位置 |
|---|---|
| schema 单一真相源 | `dungeon/schema.py`（`FieldSpec` 声明 → 空模板 / 校验器 / 编辑器表单三处共用） |
| 把引擎里的静默失败变成作者可见的诊断 | `dungeon/validate.py`（error / warning / info 三级） |
| 原子写 + `.bak` | `persistence/json_store.py` |
| 错误路径也收尾且不记为完成 | `DungeonPersistence._finalize(completed)` |
| 「声明 ↔ 产出」字段一致 | `check_scenario_schema.py` 守卫 `normalize_chapter` / `ScenarioRepo._migrate` 的产出 |

## 5. 分层修正（S3 / S4）

- **S3 死代码清理**：清掉旧动作类型的专属运行时分支、旧的调度与看门狗等。
- **S4 分层修正**：`background` `dispatcher` `launcher` `component_registry` `components`
  从 `dungeon/` 包根迁回 `dungeon/window/`，包根成为字面意义的纯领域层；
  随后由 `scripts/check_dungeon_layering.py` 用 AST 把这条线固定下来。

现状的分层规则见 [架构说明](../architecture.md) §3。
