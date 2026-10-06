"""探索流程的薄外观（Facade）：编排入口 + 兼容旧 API。

**来历**：本文件原为 ``core/context.py``，里面是一个 1100+ 行的 ``ExplorationContext``
God object。它既做目录聚合，又做报告引擎、地址规划、报告文本、角色装配与导出，
而且顶层 import 了 ``persistence``（8 个 repo）与 ``services``——这在 ``core`` 层
是违规的，守卫为此给它开了一条全图唯一的双向豁免（``orchestration`` 层）。

阶段 3.1 的处理不是"把类拆小"，而是**把它整体搬到 ``services`` 层**：

- 职责已按对象切分到 ``catalog`` / ``report`` / ``address_plan`` / ``report_text``
  / ``character`` / ``export``（见各模块 docstring）；
- 本类只保留 **①编排入口**（``report_from_core_or_character``：负向演化、扣行动点、
  写 ``character_repo``、stuck 循环、归还点数）与 **②状态管理**
  （``load_character_state`` / ``prepare_news_for_character_load``），其余一律转发；
- 属性名与方法名**原样保留**，所以 ``ui`` 侧的 50+ 个调用点一行都不用改
  （``selected_styles`` / ``selected_quip_styles`` 还额外提供了 setter，因为
  ``ui/challenge/__init__.py`` 会直接赋值）。

搬完之后 ``orchestration`` 层从 ``tests/check_import_graph.py`` 的矩阵里整个消失，
两侧重新落回既有合法边：``ui → services``、``services → persistence/core``。

**后续（可选，阶段 3.x）**：让 ui 直接持有子系统（``ctx.catalog.selected_styles``、
``ctx.reports.generate(...)``），把本外观压薄甚至删掉。那一步**单独提交**，不要和
别的迁移混在一起。
"""

from typing import List, Dict, Any, Optional, Union

from persistence import ScenarioRepo
from persistence.landmark_repo import LandmarkRepo
from persistence.quip_repo import QuipRepo
from persistence.preset_repo import PresetRepo
from persistence.personality_repo import PersonalityRepo
from persistence.character_repo import CharacterRepo
from persistence.settings_repo import SettingsRepo
from core.models import CharacterSnapshot, ReportData
from services.exploration.address_plan import AddressPlanner
from services.exploration.catalog import ExplorationCatalog
from services.exploration.character import CharacterAssembler
from services.exploration.report import ReportEngine
from services.exploration.report_text import build_report_text, build_detail_text
from services.exploration.export import (
    build_export_card_data as _build_export_card_data,
    build_export_card_from_state as _build_export_card_from_state,
)


class ExplorationContext:

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
        # 阶段 3.1：数据与服务的聚合体（原先是本类自己的一堆字段）。本类的其余
        # 职责（报告引擎 / 地址规划 / 角色装配）都从这里读共享状态。
        self.catalog = ExplorationCatalog(
            settings=settings,
            landmark_repo=landmark_repo,
            quip_repo=quip_repo,
            preset_repo=preset_repo,
            personality_repo=personality_repo,
            character_repo=character_repo,
            settings_repo=settings_repo,
            scenario_repo=scenario_repo,
            world_state=world_state,
        )
        self._addresses = AddressPlanner(self.catalog)
        self._reports = ReportEngine(self.catalog, self._addresses)
        self._character = CharacterAssembler(character_repo, settings)

    # ==================== 目录/配置属性透传 ====================
    # 阶段 3.1 **只做抽取、不动 ui 调用点**，所以下面这些名字必须原样保留。
    # 一律**实时转发**给 ExplorationCatalog，不在本类里缓存副本——`merged_landmarks`
    # / `quips` / `detail_pools` 会被 reload_merged_data() 整体替换，缓存必然陈旧。
    #
    # ⚠️ 不要在别处给这些名字重新赋值：那会创建实例属性、悄悄盖住目录里的真值。

    @property
    def settings(self) -> Dict[str, Any]:
        return self.catalog.settings

    @property
    def landmark_repo(self) -> LandmarkRepo:
        return self.catalog.landmark_repo

    @property
    def quip_repo(self) -> QuipRepo:
        return self.catalog.quip_repo

    @property
    def preset_repo(self) -> PresetRepo:
        return self.catalog.preset_repo

    @property
    def personality_repo(self) -> PersonalityRepo:
        return self.catalog.personality_repo

    @property
    def character_repo(self) -> CharacterRepo:
        return self.catalog.character_repo

    @property
    def settings_repo(self) -> SettingsRepo:
        return self.catalog.settings_repo

    @property
    def scenario_repo(self) -> Optional[ScenarioRepo]:
        return self.catalog.scenario_repo

    @property
    def world_state(self):
        return self.catalog.world_state

    # ---------- 风格与合并数据（前两个可写：ui/challenge 会赋值） ----------

    @property
    def selected_styles(self) -> List[str]:
        return self.catalog.selected_styles

    @selected_styles.setter
    def selected_styles(self, value: List[str]):
        self.catalog.selected_styles = value

    @property
    def selected_quip_styles(self) -> List[str]:
        return self.catalog.selected_quip_styles

    @selected_quip_styles.setter
    def selected_quip_styles(self, value: List[str]):
        self.catalog.selected_quip_styles = value

    @property
    def merged_landmarks(self) -> list:
        return self.catalog.merged_landmarks

    @property
    def _landmark_styles(self) -> dict:
        return self.catalog._landmark_styles

    @property
    def quips(self) -> dict:
        return self.catalog.quips

    @property
    def detail_pools(self) -> dict:
        return self.catalog.detail_pools

    # ---------- 核心服务 ----------

    @property
    def state_service(self):
        return self.catalog.state_service

    @property
    def name_repo(self):
        return self.catalog.name_repo

    @property
    def creation_service(self):
        return self.catalog.creation_service

    @property
    def news_service(self):
        return self.catalog.news_service

    @property
    def news_table(self) -> str:
        return self.catalog.news_table

    @property
    def preset_table(self) -> str:
        return self.catalog.preset_table

    @property
    def personality_table(self) -> str:
        return self.catalog.personality_table

    # ---------- 常用配置 ----------

    @property
    def comparison_count(self) -> int:
        return self.catalog.comparison_count

    @property
    def comparison_order(self) -> str:
        return self.catalog.comparison_order

    @property
    def selected_parts(self) -> list:
        return self.catalog.selected_parts

    @property
    def world_setting(self) -> str:
        return self.catalog.world_setting

    @property
    def reverse_details_order(self) -> bool:
        return self.catalog.reverse_details_order

    # ---------- 目录方法转发 ----------

    def reload_merged_data(self):
        self.catalog.reload_merged_data()

    def update_world_setting(self, world_setting: str):
        self.catalog.update_world_setting(world_setting)

    def update_name_table(self, table_name: str):
        self.catalog.update_name_table(table_name)

    def update_news_table(self, table_name: str):
        self.catalog.update_news_table(table_name)

    def update_preset_table(self, table_name: str):
        self.catalog.update_preset_table(table_name)

    def update_personality_table(self, table_name: str):
        self.catalog.update_personality_table(table_name)

    def get_landmark_count(self, style: str) -> int:
        return self.catalog.get_landmark_count(style)

    def get_quip_counts_by_size(self, style: str) -> List[int]:
        return self.catalog.get_quip_counts_by_size(style)

    def apply_context_settings(self):
        """从 settings 字典刷新上下文配置"""
        self.catalog.apply_context_settings()

    def update_styles(self, selected_styles: list, selected_quip_styles: list):
        """更新风格选择并重新加载合并数据"""
        self.catalog.update_styles(selected_styles, selected_quip_styles)

    # ==================== 报告生成（核心） ====================

    def report_from_core_or_character(self, source: Union[dict, CharacterSnapshot],
                                      selected_styles: List[str],
                                      selected_quip_styles: List[str],
                                      consume_points: bool = False,
                                      state_service=None,
                                      resolve_stuck=None) -> Optional[ReportData]:
        """生成报告。

        resolve_stuck：地址系统“无路可走”（no_reachable / all_damaged /
        world_mismatch）时的决策回调。回调签名 (state, stuck, ctx) -> dict 或 None：
          - 返回 {"kind": "address", "address": 完整地址}：角色移动到该地址；
          - 返回 {"kind": "world", "address": 完整地址}：角色切换到该世界观地址；
          - 返回 None：角色拒绝切换 → 进入负向演化并取消本次报告。
        """
        if isinstance(source, CharacterSnapshot):
            state = source
            personality = state.personality
            if personality is None:
                return None
            # 原先这里回退到 StateService **类**（它的方法全是 staticmethod，故也能跑）。
            # 改为回退到目录持有的实例，语义相同，且不必再 import 那个类。
            svc = state_service or self.state_service
            svc.apply_negative_evolution(state)
            cost = 5 * self.settings.get("comparison_count", 5)
            if consume_points:
                if not svc.consume_action_points(state, cost):
                    return None
            core = {
                "name": state.name,
                "nick": state.nick,
                "original_height": state.original_height,
                "height": state.height,
                "body_parts": state.body_parts,
                "personality_obj": personality,
                "preset_obj": None,
                "base_intrusion": state.intrusion,
                "base_destruction": state.destruction,
                "step_intrusion": state.current_step_intrusion,
                "step_destruction": state.current_step_destruction,
                "greed": state.greed,
                "will": state.will,
                "will_status": state.will_status,
                "selected_tags": state.selected_tags,
                "intro_hidden": state.intro_hidden,
                "intro_visible": state.intro_visible,
                "birthday": state.birthday,
                "uploaded_image": self.character_repo.get_avatar_abspath(state.giantess_id, state.avatar_path) or None,
                "multiplier": state.height / state.original_height if state.original_height > 0 else 1.0,
                "landmark_durability": state.landmark_durability.copy(),
                "landmark_addresses": dict(state.landmark_addresses or {}),
                "position": state.position or "",
            }
            if consume_points:
                self.character_repo.save(state)

            report_data = None
            for _attempt in range(5):
                report_data = self._reports.generate(core, selected_quip_styles)
                if report_data is None or not report_data.get("stuck"):
                    break
                stuck = report_data.get("stuck")
                decision = resolve_stuck(state, stuck, self) if resolve_stuck else None
                if decision is None:
                    # 拒绝切换 → 负向演化并取消报告
                    svc.apply_negative_evolution(state)
                    self.character_repo.save(state)
                    return None
                core["position"] = decision.get("address") or core.get("position", "")
            if report_data is None or report_data.get("stuck"):
                return None
        else:
            core = source.copy()
            if "base_intrusion" not in core:
                core["base_intrusion"] = core["personality_obj"].init_intrusion
            if "base_destruction" not in core:
                core["base_destruction"] = core["personality_obj"].init_destruction
            if "position" not in core:
                core["position"] = ""
            report_data = self._reports.generate(core, selected_quip_styles)
            if report_data is None or report_data.get("stuck"):
                return None

        if isinstance(source, CharacterSnapshot):
            state = source
            refund = self._character.apply_size_unlocks_from_report(state, report_data)
            if consume_points and refund > 0:
                self.state_service.refund_action_points(state, refund)
                print(f"✨ 解锁了{refund // 3}个部位尺寸描述，返还{refund}行动点数。")

            intrusion_after = state.intrusion
            if personality.init_intrusion != 0:
                intrusion_after = report_data["curr_intrusion"]
            destruction_after = state.destruction
            if personality.init_destruction != 0:
                destruction_after = report_data["curr_destruction"]
            # 本次报告记为一行完整演化：步进取报告内各事件步进之和
            step = sum(qr.get("step", 0.0) for qr in report_data.get("quip_results", []) or [])
            state.record_change(step=step, intrusion=intrusion_after,
                                destruction=destruction_after,
                                casualties=state.total_casualties + report_data["total_casualties"],
                                source="report_from_core_or_character")
            # 步长随本次报告演化后的当前值写回角色存储
            state.step_intrusion = report_data.get("step_intrusion",
                                                   state.current_step_intrusion)
            state.step_destruction = report_data.get("step_destruction",
                                                     state.current_step_destruction)
            state.landmark_durability = report_data.get("landmark_durability", {})
            state.landmark_addresses = report_data.get("landmark_addresses",
                                                       dict(state.landmark_addresses or {}))
            new_position = report_data.get("position") or ""
            if new_position and new_position != (state.position or ""):
                state.position = new_position
            self.character_repo.save(state)

        return ReportData(
            name=report_data["name"],
            nick=report_data["nick"],
            height=report_data["height"],
            original_height=report_data["original_height"],
            body_parts=report_data["body_parts"],
            personality=report_data["personality_obj"],
            preset=report_data["preset_obj"],
            comparisons=report_data["comparisons"],
            quip_results=report_data["quip_results"],
            final_intrusion=report_data["curr_intrusion"],
            final_destruction=report_data["curr_destruction"],
            size_category=report_data["size_cat"],
            report_text=build_report_text(report_data, self.settings,
                                          show_will=not consume_points),
            detail_text=build_detail_text(report_data["body_parts"], report_data["height"]),
            uploaded_image_path=report_data.get("uploaded_image"),
            greed=report_data.get("greed", 0),
            will=report_data.get("will", False),
            will_status=report_data.get("will_status"),
            selected_tags=report_data.get("selected_tags", []),
            intro_hidden=report_data.get("intro_hidden", ""),
            intro_visible=report_data.get("intro_visible", ""),
            birthday=report_data.get("birthday", ""),
            total_casualties=report_data["total_casualties"],
            casualty_breakdown=report_data["quip_results"],
            curr_intrusion=report_data["curr_intrusion"],
            curr_destruction=report_data["curr_destruction"],
            step_intrusion=report_data.get("step_intrusion", 0.0),
            step_destruction=report_data.get("step_destruction", 0.0),
            position=report_data.get("position", ""),
        )

    def stuck_options(self, state: CharacterSnapshot, stuck: dict) -> dict:
        """无路可走时可供角色选择的目标（实现见 services.exploration.address_plan）。"""
        return self._addresses.stuck_options(state, stuck)

    # ---------- 创建角色（实现已迁至 services.exploration.character） ----------

    def character_from_core_or_report(self, source: Union[dict, ReportData]) -> CharacterSnapshot:
        """报告 / 核心字典 → 角色档案。"""
        return self._character.character_from_core_or_report(source)

    def size_unlocks_from_report(self, report: ReportData) -> Dict[str, str]:
        """从一份报告现推部位解锁表（未持角色档案时看尺寸一览用）。"""
        return self._character.size_unlocks_from_report(report)

    # ---------- 副本准备 ----------

    def dungeon_data_from_any(self, source: Union[dict, CharacterSnapshot, ReportData]) -> dict:
        """规范化出副本所需的角色数据。"""
        return self._character.dungeon_data_from_any(source)

    # ---------- 头像兜底 ----------

    def ensure_avatar_for_state(self, state: "CharacterSnapshot") -> str:
        """未上传形象时把身材预览渲染为 PNG 当头像。"""
        return self._character.ensure_avatar_for_state(state)

    def ensure_avatar_for_state_id(self, giantess_id: str) -> str:
        """按角色 id 加载并确保头像（角色列表等只持有 id 的展示路径）。"""
        return self._character.ensure_avatar_for_state_id(giantess_id)

    # ==================== 状态管理 ====================

    def load_character_state(self, giantess_id: str) -> Optional[CharacterSnapshot]:
        """加载角色状态，按离线时长恢复行动点/属性并应用负面演化。返回 None 表示无效。"""
        state = self.character_repo.load(giantess_id)
        if not state:
            return None
        if state.personality is None:
            return None

        saved_at = state.updated_at  # 本次加载前的保存时间（用于新闻七天判断）
        # 时间恢复：每分钟恢复行动点并回落属性，超1小时按日间步进结算离线伤亡
        self.state_service.recover_evolution(state)
        self.state_service.apply_negative_evolution(state)
        self.ensure_avatar_for_state(state)
        self.character_repo.save(state)
        state._news_saved_at = saved_at
        return state

    def prepare_news_for_character_load(self, state: CharacterSnapshot):
        saved_at = getattr(state, "_news_saved_at", None) or state.updated_at
        article = self.news_service.choose_for_load(
            state,
            self.settings.get("info_update_rate", 0.5),
            saved_at=saved_at,
        )
        if article is not None:
            self.character_repo.save(state)
        return article

    # ==================== 角色卡导出（实现已迁至 services.exploration.export） ====================

    def build_export_card_data(self, name: str, nick: str, original_height: float,
                                personality_obj, preset_obj,
                                intro_hidden: str, intro_visible: str,
                                selected_tags: list, birthday: str,
                                uploaded_image_path: str) -> dict:
        """构建角色卡导出数据字典"""
        return _build_export_card_data(
            name, nick, original_height, personality_obj, preset_obj,
            intro_hidden, intro_visible, selected_tags, birthday,
            uploaded_image_path)

    def build_export_card_from_state(self, state: CharacterSnapshot) -> dict:
        """从已加载角色还原性格/身材并构建角色卡数据。"""
        return _build_export_card_from_state(state, self.character_repo)
