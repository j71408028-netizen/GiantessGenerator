# 副本（Dungeon）文档索引

> `docs/Dungeon/` 的入口：**六篇现状文档 + `history/` 历史档案（只读）**。
> 查「改哪段代码看哪篇」用 §2，查「跑哪个脚本」用 §4，查「为什么是这样」用 §5。

## 1. 文档地图

| 文档                            | 回答什么问题 | 主要读者 |
|-------------------------------|---|---|
| [架构说明](architecture.md)       | `dungeon/` 怎么分层、一次步进怎么流、收尾与自检脚本在哪 | 了解全局 / 动领域层 |
| [数据模型：章节与触发器](script.md)      | 方案配置能写哪些字段、条件键与动作有哪些 | 写方案 / 改编辑器 / 改校验器 |
| [窗口逻辑与开发事项](window.md)        | DPG 会话窗口的生命周期、帧时钟、约束与坑 | 动 `dungeon/window/` |
| [宿主边界与可移植性](window_host.md)   | window 层与宿主（Tk）的边界在哪、换 UI 框架能做到哪一步 | 评估可移植性 / 换宿主 |
| [调试自动化](window_automation.md) | 怎么不手点电脑就回归窗口 | 改窗口后自检 |
| [领域术语表](domain_terms.md)      | 方案（Scenario）与一局（Run）怎么区分、怎么命名 | 所有人（命名前必读） |

## 2. 按任务查

| 我要改… | 先看 | 然后跑 |
|---|---|---|
| 方案配置字段 / 默认值 | `dungeon/schema.py`（单一真相源）+ [数据模型](script.md) §2、§4 | `check_scenario_schema.py` |
| 校验规则（error / warning / info） | [数据模型](script.md) §9 | `check_scenario_schema.py`、`validate_scenarios.py` |
| 章节 / 触发器运行时行为 | [数据模型](script.md) §3、§5–§6 | `dungeon_autopilot.py` |
| 演化、转移矩阵、段落步长 | [架构](architecture.md) §4 | `check_scenario_schema.py`（演化链路守卫） |
| 窗口生命周期 / 关闭 / 收尾 | [窗口](window.md) §2、§3 | `dungeon_autopilot.py`、`check_dungeon_finalize.py` |
| 帧时钟 / 线程 / 跨线程 UI 更新 | [窗口](window.md) §4 | `dungeon_autopilot.py` |
| 宿主（Tk）耦合 / 换 UI 框架 | [宿主边界](window_host.md) §3、§4 | `check_dungeon_layering.py` |
| 分层越界（领域层碰了 GUI） | [架构](architecture.md) §3 | `check_dungeon_layering.py` |
| 新增 / 改名标识符（scenario vs dungeon） | [术语表](domain_terms.md) §2 | `check_scenario_naming.py` |
| 写盘方式（配置 / 回放 / 报告） | [架构](architecture.md) §8、[窗口](window.md) §5-C10 | `check_dungeon_finalize.py` |
| 自动驾驶脚本 / 自检场景 | [调试自动化](window_automation.md) | `dungeon_autopilot.py` |
| 查「为什么是这样」/ 改造经过 / 历史实测 | [历史档案](history/README.md)（只读、仅供溯源） | — |

## 3. 代码目录索引

| 路径 | 层 | 说明 |
|---|---|---|
| `dungeon/*.py`（包根） | 领域层 | 纯逻辑；禁 import GUI / 服务层（AST 强制） |
| `dungeon/window/*.py` | UI 层 | 唯一允许 DPG 的地方；宿主能力一律经 `host.py` |
| `dungeon/window/host.py` | 端口 | `HostPort`：window 层向宿主索取能力的唯一出口 |
| `ui/common/tk_host.py` | 宿主适配 | `TkHost`——window 层之外唯一的 Tk 细节所在地 |
| `persistence/scenario_repo.py` | 持久化 | 方案读写、旧目录迁移、保存即校验 |
| `ui/scenario/*` | 编辑器 | 按 `dungeon/schema.py` 的声明渲染表单 |
| `scripts/*` | 自检 | 见 §4 |

## 4. 自检脚本索引

无 GUI（可进 CI 门禁）：

| 脚本 | 覆盖 | 命令 |
|---|---|---|
| `check_dungeon_layering.py` | 分层守卫：领域层禁 import GUI/服务层/反向依赖 window；`window/` 禁 import `tkinter`/`customtkinter`/`ui` | `python scripts/check_dungeon_layering.py` |
| `check_scenario_schema.py` | schema 单一真相源（空模板 golden、字段漂移）+ 校验器规则 + 演化配置链路 | `python scripts/check_scenario_schema.py` |
| `check_dungeon_finalize.py` | 原子写 / `.bak` / 损坏回退 + `_finalize` 完成与未完成两条路径 | `python scripts/check_dungeon_finalize.py` |
| `check_scenario_naming.py` | 命名守卫（旧标识符残留）+ 兼容读与迁移行为 | `python scripts/check_scenario_naming.py` |
| `validate_scenarios.py` | 离线批量校验所有 / 单个方案，有 error 时退出码 1 | `python scripts/validate_scenarios.py --errors-only` |
| `check_platform_compat.py` | Windows 专有 API 使用点清单（发布 macOS 前必读） | `python scripts/check_platform_compat.py` |

需显示器（GUI 冒烟层，不进无 GUI 门禁）：

| 脚本 | 覆盖 | 命令 |
|---|---|---|
| `dungeon_autopilot.py` | 真窗口生命周期：构造 → `run()` → 步进 → 关闭 → 落盘（5 场景 48 项断言） | `python scripts/dungeon_autopilot.py` |

判定方式详见 [调试自动化](window_automation.md) §4：**看结论行，不看退出码**。

## 5. 历史档案（与现状分离）

改造过程、实测记录、调查与决策依据一律放在 `history/`，**只用于溯源，不据此改代码**：

| 档案 | 内容 | 对应现状文档 |
|---|---|---|
| [host_refactor.md](history/host_refactor.md) | L0–L5 宿主改造全过程、改造前形状、验收记录 | [宿主边界](window_host.md)、[窗口](window.md) |
| [exit_hang_investigation.md](history/exit_hang_investigation.md) | 进程退出挂死的复现矩阵、修正结论、重启复测 | [窗口 §5-C2 / §6](window.md)、[自检 §4](window_automation.md) |
| [model_evolution.md](history/model_evolution.md) | 章节/触发器模型拆分、动作退场、scenario/dungeon 命名与目录迁移 | [数据模型](script.md)、[术语表](domain_terms.md) |
| [lingchat_evaluation.md](history/lingchat_evaluation.md) | 同人作品参考价值评估与「不采纳」清单 | [架构说明](architecture.md) |

完整索引与约定见 [history/README.md](history/README.md)。

**写法约定**

1. 现状文档（§1 六篇）只写「现在是什么」，末尾的「历史」小节只放**指针**，不展开叙述。
2. `history/` 只追加、不随现状改写；新的一次改造或调查写成新档案，或追加到对应档案末尾并标注日期。
3. 只有仍生效的结论（例如许可证红线、退出挂死的应对方式）才允许留在现状文档里。

## 6. 新人阅读顺序

1. [术语表](domain_terms.md) —— 先分清方案与一局，否则读代码会混。
2. [架构说明](architecture.md) §1–§4 —— 分层、模块地图、一次步进。
3. [数据模型](script.md) —— 要写方案或改编辑器时看。
4. [窗口](window.md) —— 只有动 `dungeon/window/` 时才需要，但 §5 约束清单建议先扫一遍。

`history/` 不需要读；只在要追究「为什么是这样」时按 §5 的对照表去查对应档案。
