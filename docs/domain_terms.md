# 副本领域术语表（Scenario vs Run）

本仓库有两个共用"副本"这个词、但层次完全不同的概念。**S1.5 之前两者混用
`dungeon` 一个词根**——`DungeonRepo` 实际持久化的是方案定义，`dungeon_id`
在编辑器里又叫 `scenario_id`，同一值在一次调用链里换名。现已拆分如下。

## 1. 两个概念

| 概念 | 代码命名 | 持久化位置 | 谁在用 |
|------|----------|------------|--------|
| **副本方案（Scenario）** | `scenario_id` / `scenario_config` / `scenario_repo` / `ScenarioRepo`（`persistence/scenario_repo.py`） | `data/packs/scenarios/<方案 id>/config.json`（+ `images/`、`components/`） | 编辑器 `ui/scenario/`（`ScenarioEditor`）、入口方案面板、世界包/挑战包导出 |
| **一局（Run）** | `DungeonSessionWindow` / `DungeonState` / `dungeon_state` / `dungeon_ended` / `dungeon_logic` / 回放 | `data/archives/<角色>/回放\|报告` | 副本会话窗口（`dungeon/window/`）、回放加载、档案导出 |

一句话：**`scenario_*` = 定义（作者写的），`dungeon_*` = 运行（玩家玩的）**。
中文文案对应：「副本方案」（编辑、新建、重命名、删除的对象）与「副本 /
进入副本 / 开始副本」（一局）。

## 2. 命名规则

1. 新代码里引用方案 id 的变量/参数/字段一律叫 `scenario_id`；
   引用方案配置字典叫 `scenario_config`；方案仓库叫 `ScenarioRepo`。
2. 方案 id 的持久化字段（世界包清单、`data/user/endings.json`、角色
   `achieved_endings[]`、`.chal` 挑战包 payload）一律写 `scenario_id`；
   读取时用 `dungeon.terms.scenario_id_of()` / `scenario_config_of()`
   兼容改名前的 `dungeon_id` / `dungeon_config`。
3. 目录与资源键：`data/packs/scenarios/`、世界包 `resources["scenarios"]`
   （旧包内的 `dungeons/` 目录与旧 manifest 键由 `ScenarioRepo` /
   `WorldPackManifest.from_dict` 自动兼容）。
4. 内置方案 id 用常量 `dungeon.terms.DEFAULT_SCENARIO_ID`，不要再写 `_default`。
5. `dungeon_font`（副本窗口字体）属于一局的界面，保持原名。

## 3. 兼容与迁移

- **方案目录**：`ScenarioRepo.__init__` 会把改名前的 `packs/dungeons/`
  就地并入 `packs/scenarios/`（幂等，同名时保留 `config.json` 较新的一份，
  旧的留 `.bak`）。
- **世界包**：`WorldPackManifest.from_dict` 把旧 `resources["dungeons"]`
  归一化为 `resources["scenarios"]`；成员核对同时接受包内 `dungeons/<id>/`
  与 `scenarios/<id>/` 两种路径。
- **存档字段**：读取走 `scenario_id_of()`；可用
  `python scripts/migrate_scenario_naming.py [--dry-run]` 把
  `endings.json` / 角色档案 / 世界包里的旧字段一次性改写为 `scenario_id`
  （幂等、原子写 + `.bak`）。`.chal` 挑战包是加密格式，不做改写，读取侧兼容。
- **守卫**：`python scripts/check_scenario_naming.py`（AST 扫描旧标识符残留
  + 兼容行为自检）；`python scripts/check_dungeon_finalize.py`（收尾路径回归）。
