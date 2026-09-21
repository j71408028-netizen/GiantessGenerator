"""副本领域术语与持久化契约常量（单一真相源）。

本仓库有两个容易混淆的概念，代码里必须分开命名：

- **副本方案（Scenario）**：作者编写的定义——章节、触发器、提示词、演化属性、
  转移矩阵、显示组件与素材。持久化为
  ``data/packs/scenarios/<方案 id>/config.json``（+ ``images/``、``components/``）。
  编辑器 ``ui/scenario/``（``ScenarioEditor``）编辑的就是它。
- **一局（Run）**：某个角色用某个方案跑的一次会话——``DungeonState``、回放、
  结局达成、行动点数消耗。对应 ``DungeonSessionWindow`` 与
  ``data/archives/<角色>/回放|报告``。

因此命名约定：``scenario_*`` 只指「方案（定义）」，``dungeon_*`` 只指「一局（运行）」。
持久化记录里引用方案 id 的字段一律写 ``scenario_id``；读取时用
``scenario_id_of()`` 兼容改名前的 ``dungeon_id``。

（术语拆分的背景见 ``docs/domain_terms.md``；改名前的 ``DungeonRepo`` 实际持久化的
就是方案定义，因而更名为 ``persistence.scenario_repo.ScenarioRepo``。）
"""

DEFAULT_SCENARIO_ID = "_default"
SCENARIO_CONFIG_NAME = "config.json"
SCENARIO_ID_KEY = "scenario_id"
SCENARIO_CONFIG_KEY = "scenario_config"

# 目录名 / 世界包资源类型键
SCENARIO_RESOURCE_KEY = "scenarios"
# 改名前的旧契约名：只用于读取兼容与迁移脚本，新代码不得写入
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


def legacy_resource_key(key) -> str:
    """把改名前的资源类型键（``dungeons``）归一化为 ``scenarios``。"""
    text = str(key or "")
    return SCENARIO_RESOURCE_KEY if text == LEGACY_SCENARIO_RESOURCE_KEY else text
