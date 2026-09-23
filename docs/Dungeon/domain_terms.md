# 副本领域术语索引（Scenario vs Run）

> **定位**：方案（Scenario）与一局（Run）的**命名规则索引**。命名前必读，改名后必跑守卫脚本。
> 全局架构见 [架构说明](architecture.md)，全系列入口见 [副本文档索引](README.md)。

本仓库有两个共用「副本」一词、但层次完全不同的概念，现已按 §1 拆分。
（拆分前的混用情况与目录/字段迁移过程见 [历史](#历史)。）

## 1. 两个概念

| 概念 | 代码命名 | 持久化位置 | 谁在用 |
|---|---|---|---|
| **副本方案 Scenario** | `scenario_id` / `scenario_config` / `scenario_repo` / `ScenarioRepo`（`persistence/scenario_repo.py`） | `data/packs/scenarios/<方案 id>/config.json`（+ `images/`、`components/`） | 编辑器 `ui/scenario/`（`ScenarioEditor`）、入口方案面板、世界包 / 挑战包导出 |
| **一局 Run** | `DungeonSessionWindow` / `DungeonState` / `dungeon_state` / `dungeon_ended` / `dungeon_logic` / 回放 | `data/archives/<角色>/回放\|报告` | 副本会话窗口（`dungeon/window/`）、回放加载、档案导出 |

一句话：**`scenario_*` = 定义（作者写的），`dungeon_*` = 运行（玩家玩的）**。
中文文案对应：「副本方案」（编辑、新建、重命名、删除的对象）与「副本 / 进入副本 / 开始副本」（一局）。

## 2. 命名规则

1. 新代码里引用方案 id 的变量 / 参数 / 字段一律叫 `scenario_id`；引用方案配置字典叫 `scenario_config`；方案仓库叫 `ScenarioRepo`。
2. 方案 id 的持久化字段（世界包清单、`data/user/endings.json`、角色 `achieved_endings[]`、`.chal` 挑战包 payload）一律写 `scenario_id`；读取时用 `dungeon.terms.scenario_id_of()` / `scenario_config_of()` 兼容改名前的 `dungeon_id` / `dungeon_config`。
3. 目录与资源键：`data/packs/scenarios/`、世界包 `resources["scenarios"]`（旧包内的 `dungeons/` 目录与旧 manifest 键由 `ScenarioRepo` / `WorldPackManifest.from_dict` 自动兼容）。
4. 内置方案 id 用常量 `dungeon.terms.DEFAULT_SCENARIO_ID`（`_default`），不要再写字面量。
5. `dungeon_font`（副本窗口字体）属于一局的界面，保持原名。

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
| `scripts/check_scenario_naming.py` | AST 扫描旧标识符残留（`dungeon_id` / `DungeonRepo` 等）+ 兼容读与迁移行为自检 |
| `scripts/check_scenario_schema.py` | schema 单一真相源 + 校验器规则自检 |
| `scripts/check_dungeon_finalize.py` | 收尾路径回归 |
| `scripts/check_dungeon_layering.py` | 领域层不得依赖 UI / 服务层的分层守卫 |
| `scripts/validate_scenarios.py` | 离线批量校验所有方案 |

命令：见 [副本文档索引](README.md) §4。

---

## 历史

本文件只描述**现状**（怎么命名、怎么兼容）。命名拆分（S1.5）与目录 / 字段迁移的过程见
[副本模型与命名演进](history/model_evolution.md) §3。
