"""调查：在可用资源里掷出「本局提案」。

调查只决定这一局用什么：一个副本方案、一组取材表、一条世界包线索。

- 地标与描述风格不参与掷选：它们由资源中心多选，报告与副本实时读取；调查
  再改写选择，只会把玩家配好的多选压回单选。
- 世界包只出线索、不直接启用：启用会接管风格、取材表、行为包与话题表，是
  玩家在「世界」屏里显式做的决定。
- 挑战包是独立内容（自带角色与数据），入口在顶栏，不占本局的掷选。

角色有可解析地址时，掷选受地址约束：剧本要过得了入口准入（同一地址维度、
距离在上限内），世界包线索只落在角色所在的地址维度里；没有地址时不设约束。

「副本」在主项目里已改名「副本方案（scenario）」，本模块的字段因此用
``scenario_id``；界面文案仍说「剧本」，那是玩家侧的说法。
"""

import random
from dataclasses import dataclass
from typing import Optional, Tuple

from core.address_model import world_of
from paths import data_dir
from persistence.material_tables import DEFAULT_MATERIAL_TABLE, list_groups

EMPTY_LABEL = "（无）"
NO_WORLD_LABEL = "未启用"


@dataclass
class Investigation:
    """一次调查的结果快照。"""

    scenario_id: str = ""
    material_table: str = ""
    world_hint_id: Optional[str] = None
    world_hint_name: str = ""
    address_note: str = ""

    @property
    def has_scenario(self) -> bool:
        return bool(self.scenario_id)

    @property
    def has_material(self) -> bool:
        return bool(self.material_table)

    @property
    def has_world_hint(self) -> bool:
        return bool(self.world_hint_id)

    # ---------- 展示 ----------
    def scenario_text(self) -> str:
        return self.scenario_id or EMPTY_LABEL

    def material_text(self) -> str:
        return self.material_table or EMPTY_LABEL

    def world_hint_text(self) -> str:
        return self.world_hint_name or EMPTY_LABEL


class Investigator:
    """「调查」按钮背后的掷选与应用逻辑。"""

    def __init__(self, app):
        self.app = app

    # ==================== 对外入口 ====================
    def investigate(self, position: str = "") -> Investigation:
        """掷出一套本局提案；``position`` 是角色当前地址（没有就传空串）。"""
        inv = Investigation()
        inv.scenario_id, inv.address_note = self._roll_scenario(position)
        inv.material_table = self._roll_material()
        inv.world_hint_id, inv.world_hint_name = self._roll_world(position)
        return inv

    # ==================== 掷选 ====================
    def _roll_scenario(self, position: str) -> Tuple[str, str]:
        """掷剧本；有地址时只掷进得去的，全都不进得去才放宽并记一笔。"""
        scenario_ids = list(self.app._scenario_repo.list_all())
        if not scenario_ids:
            return "", ""

        reachable = [sid for sid in scenario_ids if self._reachable(sid, position)]
        if not reachable:
            return random.choice(scenario_ids), "地址已放宽"
        note = "已按地址筛选" if len(reachable) < len(scenario_ids) else ""
        return random.choice(reachable), note

    def _reachable(self, scenario_id: str, position: str) -> bool:
        """方案入口地址是否对当前角色开放（无地址时一律视为可达）。"""
        if not position:
            return True
        config = self.app._scenario_repo.load_config(scenario_id)
        if config is None:
            return False
        # 延迟导入：准入判定属于副本域，挂件日常启动不必加载它。
        from dungeon.admission import check_entry_requirements
        return not check_entry_requirements(config, position=position)

    def _roll_material(self) -> str:
        """掷取材表组；被世界包接管时保持包内那一组，不掷。"""
        app = self.app
        current = app.settings.get("material_table") or DEFAULT_MATERIAL_TABLE
        state = app.world_state
        locked = state.locked_keys() if state is not None else set()
        if "material_table" in locked:
            return current

        groups = list_groups(data_dir(), state)
        if not groups:
            return current
        return random.choice(groups)

    def _roll_world(self, position: str) -> Tuple[Optional[str], str]:
        """掷一条世界包线索：只挑未启用的包，且不越过角色所在的地址维度。"""
        app = self.app
        packs = [p for p in app.world_manager.list_packs() if not p.get("error")]
        state = app.world_state
        active_id = state.world_id if state is not None and state.active else None
        world = world_of(position) if position else ""

        candidates = []
        for pack in packs:
            if pack.get("world_id") == active_id:
                continue
            declared = pack.get("worlds") or []
            if world and declared and world not in declared:
                continue
            candidates.append(pack)
        if not candidates:
            return None, ""

        pack = random.choice(candidates)
        world_id = pack.get("world_id")
        return world_id, pack.get("name") or world_id or ""

    # ==================== 应用 ====================
    def apply_material(self, group: str) -> None:
        """把掷中的取材表组落到上下文（与资源中心的下拉是同一条路径）。"""
        if not group:
            return
        self.app.context.update_material_table(group)
