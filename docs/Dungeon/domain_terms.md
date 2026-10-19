# 副本领域术语索引（Scenario / Run / Session）

> **定位**：方案（Scenario）、一局（Run）与会话（Session）的**命名规则索引**。命名前必读，改名后必跑守卫脚本。
> 全局架构见 [架构说明](architecture.md)，全系列入口见 [副本文档索引](README.md)。

本仓库有四个容易混用的名字：产品/功能名 `Dungeon`、方案 `Scenario`、一局 `Run`、
窗口会话 `Session`。它们不是同义词，也不是一条「Dungeon 包含 Scenario / Run」的层级链。
拆分前的混用情况与目录/字段迁移过程见 [历史](#历史)。

## 1. 四个名字

| 名字 | 是什么 | 不是什么 | 代码现状 | 持久化 / 生命周期 |
|---|---|---|---|---|
| **Dungeon / 副本** | 产品与功能名：入口、会话窗口、设置、方案编辑、小游戏等玩家可见的「副本功能」 | 不是领域对象，也不是「一局」的精确名字 | `dungeon/` 包名、`Dungeon*` UI / 窗口类、`dungeon_settings`、`ui/hosting/dungeon_*` | 功能本身不落盘 |
| **副本方案 Scenario** | 作者编写的**定义**：章节、触发器、提示词、演化属性、转移矩阵、显示组件与素材 | 不是一次执行，也不是窗口 | `scenario_id` / `scenario_config` / `scenario_repo` / `ScenarioRepo` | `data/packs/scenarios/<方案 id>/config.json`（+ `images/`、`components/`） |
| **一局 Run** | 某角色用某方案**执行的一次游戏**：`DungeonState`、行动点消耗、结局达成、回放 | 不是窗口，也不是入口页；`Session` 也不是它的同义词 | 现名 `DungeonState` / `dungeon_state` / `dungeon_ended` / `dungeon_logic`；目标领域名为 `Run*` / `run_*` | `data/archives/<角色>/回放\|报告`；挑战/无角色写 `data/user/replays\|reports` |
| **会话 Session** | `DungeonSessionWindow` 从构造到 `run()` 返回的一次**窗口生命周期**：入口页、正式运行、关闭与收尾 | 不是「一局」本身；入口返回/启动失败时没有 Run | `DungeonSessionWindow` / `SessionResult` / `_session_running` / `_session_initialized` / `session_waiting_for_input` | 不持久化；`SessionResult` 只跨进程/调用方返回 |

**关系**：

- `Scenario` 与 `Run` 平级：前者是定义，后者是执行；一个方案可以被执行多次，每次是一局。
- `Session` 是呈现层的一次窗口生命周期，**承载 0 或 1 个新执行的 Run**：
  - 入口页点「返回」、配置校验失败 / 行动点不足 → Session 结束，没有 Run；
  - 加载回放对话框取消 → 仍留在入口页，Session 继续；
  - 正常开始副本 → Session 内进入正式运行阶段，产生一局新 Run；
  - 回放模式 → Session 重放已有 Run，不新增 Run、不做结局结算（窗口内会重建只读状态快照用于绘制）。
- `Dungeon` 是产品/功能名，不是上述概念的父类；`dungeon/` 目录同时放 Scenario 与 Run 的领域模块，是当前包结构，不代表包含关系。

> **为什么暂时不改包名**：`dungeon.window.minigame` 是小游戏作者的公开导入路径
> （`data/packs/minigames/*/game.py`、[小游戏框架](../designs/minigame.md)），且
> `dungeon_*` 已散布在 UI / 设置 / 脚本里。术语澄清不需要包改名；将来若按引擎拆分，
> 必须保留旧路径的兼容 shim。

## 2. 命名规则

1. 新代码里引用方案 id 的变量 / 参数 / 字段一律叫 `scenario_id`；引用方案配置字典叫 `scenario_config`；方案仓库叫 `ScenarioRepo`。
2. 方案 id 的持久化字段（世界包清单、`data/user/endings.json`、角色 `achieved_endings[]`、`.chal` 挑战包 payload）一律写 `scenario_id`；读取时用 `dungeon.terms.scenario_id_of()` / `scenario_config_of()` 兼容改名前的 `dungeon_id` / `dungeon_config`。
3. 目录与资源键：`data/packs/scenarios/`、世界包 `resources["scenarios"]`（旧包内的 `dungeons/` 目录与旧 manifest 键由 `ScenarioRepo` / `WorldPackManifest.from_dict` 自动兼容）。
4. 内置方案 id 用常量 `dungeon.terms.DEFAULT_SCENARIO_ID`（`_default`），不要再写字面量。
5. **Run 领域**：新代码中表示一局状态/生命周期的类型与字段用 `Run*` / `run_*`；现有 `DungeonState` / `dungeon_state` / `dungeon_ended` / `dungeon_logic` 是历史实现名，保留但不要扩散。
6. **Session 领域**：`session_*` 只用于窗口呈现/生命周期（`SessionResult`、`_session_running`、`_session_initialized`、`session_waiting_for_input` 等），不要拿它当 Run 的同义词。`DungeonSessionWindow` 是承载 Run 的窗口类，不是 Run 本身。
7. **`dungeon_*` 不是精确的领域后缀**：它同时覆盖产品/功能（`DungeonSessionWindow`、`DungeonWindowUI`、`dungeon_settings`、`ui/hosting/dungeon_*`）与历史 Run 名（`DungeonState` 等）。看到前缀不要反推「它一定是 Run」，也不要据此认为 Scenario 被 Run 包含。
8. `dungeon_font`（副本窗口字体）与 `dungeon_settings` 属于副本界面/功能设置，保持原名。
9. 用户可见文案继续用「副本 / 副本方案 / 进入副本 / 开始副本」；不要把「Session」当玩家术语展示。
10. 新增/改名后跑 `tests/check_scenario_naming.py`；包路径/导入关系变动还要跑 `tests/check_import_graph.py` 与 `tests/check_dungeon_layering.py`。

## 3. 兼容与迁移

| 场景 | 行为 |
|---|---|
| 方案目录 | `ScenarioRepo.__init__` 把改名前的 `packs/dungeons/` 就地并入 `packs/scenarios/`（幂等；同名时保留 `config.json` 较新的一份，旧的留 `.bak`） |
| 世界包 | `WorldPackManifest.from_dict` 把旧 `resources["dungeons"]` 归一化为 `resources["scenarios"]`；成员核对同时接受包内 `dungeons/<id>/` 与 `scenarios/<id>/` 两种路径 |
| 存档字段 | 读取走 `scenario_id_of()`；`python scripts/migrate_scenario_naming.py [--dry-run]` 可把 `endings.json` / 角色档案 / 世界包里的旧字段一次性改写为 `scenario_id`（幂等、原子写 + `.bak`） |
| `.chal` 挑战包 | 加密格式，不做改写，读取侧兼容 |

## 4. 守卫脚本

| 脚本 | 覆盖 |
|---|---|
| `tests/check_scenario_naming.py` | AST 扫描旧标识符残留（`dungeon_id` / `DungeonRepo` 等）+ 兼容读与迁移行为自检 |
| `tests/check_scenario_schema.py` | schema 单一真相源 + 校验器规则自检 |
| `tests/check_dungeon_finalize.py` | 收尾路径回归 |
| `tests/check_dungeon_layering.py` | 领域层不得依赖 UI / 服务层的分层守卫 |
| `scripts/scenario_validate.py` | 离线批量校验所有方案 |

命令：见 [副本文档索引](README.md) §4。

---

## 历史

本文件只描述**现状**（怎么命名、怎么兼容）。命名拆分（S1.5）与目录 / 字段迁移的过程见
[副本模型与命名演进](history/model_evolution.md) §3。
