"""副本领域术语与持久化契约常量（单一真相源）。

本仓库的四个名字不是同义词，代码里必须分开命名：

- **Dungeon / 副本**：产品与功能名（入口、会话窗口、设置、方案编辑、小游戏）。
  ``dungeon/`` 是当前实现包名，不代表领域层级。
- **副本方案（Scenario）**：作者编写的定义——章节、触发器、提示词、演化属性、
  转移矩阵、显示组件与素材。持久化为
  ``data/packs/scenarios/<方案 id>/config.json``（+ ``images/``、``components/``）。
  编辑器 ``ui/scenario/``（``ScenarioEditor``）编辑的就是它。
- **一局（Run）**：某个角色用某个方案执行的一次游戏——``DungeonState``、回放、
  结局达成、行动点数消耗。``DungeonState`` / ``dungeon_state`` / ``dungeon_ended``
  是 Run 的现有实现名；新代码的 Run 领域命名用 ``run_*`` / ``Run*``。
- **会话（Session）**：``DungeonSessionWindow`` 从构造到 ``run()`` 返回的一次窗口
  生命周期，承载入口页与正式运行。一个 Session 可以包含 0 或 1 个新执行的 Run；
  ``SessionResult`` 是 Session 的结果，不是 Run 的结果。回放 Session 只重放已有
  Run，不新增 Run。

因此命名约定：``scenario_*`` 只指「方案（定义）」；``run_*`` 指「一局（执行）」；
``session_*`` 指窗口呈现/生命周期；``dungeon_*`` 是产品/功能与历史兼容命名，不是
精确的领域后缀。持久化记录里引用方案 id 的字段一律写 ``scenario_id``；读取时用
``scenario_id_of()`` 兼容改名前的 ``dungeon_id``。

（术语拆分的背景见 ``docs/Dungeon/domain_terms.md``；改名前的 ``DungeonRepo`` 实际持久化的
就是方案定义，因而更名为 ``persistence.scenario_repo.ScenarioRepo``。）
"""

DEFAULT_SCENARIO_ID = "_default"
SCENARIO_CONFIG_NAME = "config.json"
SCENARIO_ID_KEY = "scenario_id"
SCENARIO_CONFIG_KEY = "scenario_config"

# 目录名 / 世界包资源类型键
SCENARIO_RESOURCE_KEY = "scenarios"
# 改名前的资源类型键：运行时不再兼容，只剩迁移脚本需要它识别旧包
LEGACY_SCENARIO_RESOURCE_KEY = "dungeons"
LEGACY_SCENARIO_ID_KEY = "dungeon_id"
LEGACY_SCENARIO_CONFIG_KEY = "dungeon_config"


def scenario_id_of(record) -> str:
    """从持久化记录里取方案 id，兼容改名前的 ``dungeon_id`` 字段。"""
    if not isinstance(record, dict):
        return ""
    value = record.get(SCENARIO_ID_KEY)
    if not value:
        value = record.get(LEGACY_SCENARIO_ID_KEY)
    return str(value or "")


def scenario_config_of(record) -> dict:
    """从持久化记录里取方案配置字典，兼容旧的 ``dungeon_config`` 字段。"""
    if not isinstance(record, dict):
        return {}
    value = record.get(SCENARIO_CONFIG_KEY)
    if not isinstance(value, dict):
        value = record.get(LEGACY_SCENARIO_CONFIG_KEY)
    return value if isinstance(value, dict) else {}


def is_default_scenario(scenario_id) -> bool:
    """是否为内置方案（不可删除、不可重命名、不进世界包导出）。"""
    return str(scenario_id or "") == DEFAULT_SCENARIO_ID
