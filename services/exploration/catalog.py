# -*- coding: utf-8 -*-
"""探索目录：仓库聚合、风格选择、合并数据、常用配置与服务句柄。

由 ``core/context.py`` 的 ``ExplorationContext`` 按职责抽出（阶段 3.1）。
它同时是各职责子系统（报告引擎 / 地址规划 / 角色装配）的**共享状态持有者**：

- ``merged_landmarks`` / ``quips`` / ``detail_pools`` / ``selected_styles`` 会被
  ``update_styles()`` / ``reload_merged_data()`` **整体替换**，所以子系统必须持有
  本对象、在调用时读这些属性，而**不能**在构造时把它们拷成自己的字段——拷了就会
  陈旧。
- ``settings`` 是**共享引用**（外部传入的同一个 dict），对它的修改全层可见。

**持有 vs 拥有**：``state_service`` / ``creation_service`` / ``news_service`` /
``name_repo`` 只是被聚在一起供上层取用，它们的生命周期与建法都没变。
"""

from typing import Any, Dict, List, Optional

from core.logic import ALL_PART_NAMES
from persistence import ScenarioRepo
from persistence.name_repo import NameRepo, DEFAULT_NAME_TABLE
from persistence.landmark_repo import LandmarkRepo, DEFAULT_LANDMARK_STYLE
from persistence.quip_repo import QuipRepo, DEFAULT_QUIP_STYLE
from persistence.preset_repo import PresetRepo
from persistence.personality_repo import PersonalityRepo
from persistence.character_repo import CharacterRepo
from persistence.settings_repo import SettingsRepo
from services import build_detail_pools
from services.creation_service import CreationService
from services.character_service.news import DEFAULT_NEWS_TABLE, NewsService
from services.state_service import StateService


class ExplorationCatalog:
    """探索上下文的数据/服务聚合体（无界面、无编排）。"""

    def __init__(
            self,
            settings: Dict[str, Any],
            landmark_repo: LandmarkRepo,
            quip_repo: QuipRepo,
            preset_repo: PresetRepo,
            personality_repo: PersonalityRepo,
            character_repo: CharacterRepo,
            settings_repo: SettingsRepo,
            scenario_repo: Optional[ScenarioRepo] = None,
            world_state=None
    ):
        self.settings = settings
        self.landmark_repo = landmark_repo
        self.quip_repo = quip_repo
        self.preset_repo = preset_repo
        self.personality_repo = personality_repo
        self.character_repo = character_repo
        self.settings_repo = settings_repo
        self.scenario_repo = scenario_repo
        self.world_state = world_state

        # ---------- 1. 风格选择（合并来源） ----------
        self.selected_styles = self._filter_styles(
            settings.get("selected_styles", []),
            landmark_repo.get_styles(),
            DEFAULT_LANDMARK_STYLE
        )
        self.selected_quip_styles = self._filter_styles(
            settings.get("selected_quip_styles", []),
            quip_repo.get_styles(),
            DEFAULT_QUIP_STYLE
        )

        # ---------- 2. 加载合并数据 ----------
        self.merged_landmarks = []
        self._landmark_styles = {}
        self.quips = {}
        self.detail_pools = {}
        self._load_merged_data()

        # ---------- 3. 核心服务 ----------
        self.state_service = StateService()
        self.name_repo = NameRepo(world_state=world_state)
        self.creation_service = CreationService(
            name_repo=self.name_repo,
            name_table=settings.get("name_table", DEFAULT_NAME_TABLE)
        )
        self.news_table = settings.get("news_table", DEFAULT_NEWS_TABLE)
        self.news_service = NewsService(news_table=self.news_table, world_state=world_state)
        self.preset_table = settings.get("preset_table", "default")
        self.personality_table = settings.get("personality_table", "default")
        self.preset_repo.set_table(self.preset_table)
        self.personality_repo.set_table(self.personality_table)

        # ---------- 4. 常用配置（从设置提取，便于快速访问） ----------
        self.comparison_count = settings.get("comparison_count", 5)
        self.comparison_order = settings.get("comparison_order", "match")
        self.selected_parts = settings.get("selected_parts", ALL_PART_NAMES.copy())
        self.world_setting = settings.get("world_setting", "appear")
        self.reverse_details_order = settings.get("reverse_details_order", False)

    @staticmethod
    def _filter_styles(selected: List[str], available: List[str], default: str) -> List[str]:
        filtered = [s for s in selected if s in available]
        return filtered if filtered else [default]

    def _load_merged_data(self):
        """按当前 ``selected_styles`` / ``selected_quip_styles`` 重建合并数据。

        ``__init__`` 与 ``reload_merged_data()`` 共用本方法——原先两处是逐字重复的
        代码，风格改一处忘一处就会让「设置里改风格」与「启动时读风格」产生分歧。
        """
        self.merged_landmarks = []
        self._landmark_styles = {}
        for _style in self.selected_styles:
            for _lm in self.landmark_repo.load(_style):
                self.merged_landmarks.append(_lm)
                self._landmark_styles[id(_lm)] = _style
        self.quips = self.quip_repo.load_merged(self.selected_quip_styles)
        self.detail_pools = build_detail_pools(self.quips)

    def reload_merged_data(self):
        self._load_merged_data()

    def update_world_setting(self, world_setting: str):
        self.world_setting = world_setting
        self.settings["world_setting"] = world_setting

    def update_name_table(self, table_name: str):
        self.settings["name_table"] = table_name
        self.creation_service.set_name_table(table_name)

    def update_news_table(self, table_name: str):
        self.news_table = table_name or DEFAULT_NEWS_TABLE
        self.settings["news_table"] = self.news_table
        self.news_service.set_table(self.news_table)

    def update_preset_table(self, table_name: str):
        self.preset_table = table_name or "default"
        self.settings["preset_table"] = self.preset_table
        self.preset_repo.set_table(self.preset_table)

    def update_personality_table(self, table_name: str):
        self.personality_table = table_name or "default"
        self.settings["personality_table"] = self.personality_table
        self.personality_repo.set_table(self.personality_table)

    # ==================== 统计数据查询 ====================

    def get_landmark_count(self, style: str) -> int:
        return len(self.landmark_repo.load(style))

    def get_quip_counts_by_size(self, style: str) -> List[int]:
        quips = self.quip_repo.load(style)
        size_order = ["small", "medium", "large", "huge", "colossal"]
        counts = []
        for size in size_order:
            matrix = quips.get(size, {})
            total = sum(len(qlist) for qlist in matrix.values())
            counts.append(total)
        return counts

    # ==================== 设置应用 ====================

    def apply_context_settings(self):
        """从 settings 字典刷新上下文配置"""
        self.comparison_count = self.settings.get("comparison_count", 5)
        self.comparison_order = self.settings.get("comparison_order", "match")
        self.reverse_details_order = self.settings.get("reverse_details_order", False)
        self.selected_parts = self.settings.get("selected_parts", ALL_PART_NAMES.copy())
        self.creation_service.set_name_table(self.settings.get("name_table", DEFAULT_NAME_TABLE))
        self.update_news_table(self.settings.get("news_table", DEFAULT_NEWS_TABLE))
        self.update_preset_table(self.settings.get("preset_table", "default"))
        self.update_personality_table(self.settings.get("personality_table", "default"))

    def update_styles(self, selected_styles: list, selected_quip_styles: list):
        """更新风格选择并重新加载合并数据"""
        self.selected_styles = selected_styles
        self.selected_quip_styles = selected_quip_styles
        self.reload_merged_data()
