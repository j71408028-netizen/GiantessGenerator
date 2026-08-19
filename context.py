import copy
import datetime
import os
import random
import re
import tempfile
from dataclasses import asdict
from typing import List, Dict, Any, Optional, Union

from persistence import DungeonRepo
from persistence.name_repo import NameRepo, DEFAULT_NAME_TABLE
from persistence.landmark_repo import LandmarkRepo, DEFAULT_LANDMARK_STYLE
from persistence.quip_repo import QuipRepo, DEFAULT_QUIP_STYLE
from persistence.preset_repo import PresetRepo
from persistence.personality_repo import PersonalityRepo
from persistence.character_repo import CharacterRepo
from persistence.settings_repo import SettingsRepo
from logic import ALL_PART_NAMES, format_size, get_size_category, replace_quip_tags, \
    get_comparisons, apply_size_unlock_updates, compute_casualty, compute_environment_factor, build_size_description, \
    select_quip_with_budget
from models import CharacterSnapshot, ReportData
from services import build_detail_pools
from services.creation_service import CreationService
from services.news_service import DEFAULT_NEWS_TABLE, NewsService
from services.state_service import StateService


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
            dungeon_repo: Optional[DungeonRepo] = None,
            world_state=None
    ):
        self.settings = settings
        self.landmark_repo = landmark_repo
        self.quip_repo = quip_repo
        self.preset_repo = preset_repo
        self.personality_repo = personality_repo
        self.character_repo = character_repo
        self.settings_repo = settings_repo
        self.dungeon_repo = dungeon_repo
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
        self.merged_landmarks = landmark_repo.load_merged(self.selected_styles)
        self.quips = quip_repo.load_merged(self.selected_quip_styles)
        self.detail_pools = build_detail_pools(self.quips)

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

    def reload_merged_data(self):
        self.merged_landmarks = self.landmark_repo.load_merged(self.selected_styles)
        self.quips = self.quip_repo.load_merged(self.selected_quip_styles)
        self.detail_pools = build_detail_pools(self.quips)

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

    # ==================== 报告生成（核心） ====================

    def report_from_core_or_character(self, source: Union[dict, CharacterSnapshot],
                                      selected_styles: List[str],
                                      selected_quip_styles: List[str],
                                      consume_points: bool = False,
                                      state_service=None) -> Optional[ReportData]:
        if isinstance(source, CharacterSnapshot):
            state = source
            personality = state.personality
            if personality is None:
                return None
            svc = state_service or StateService
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
                "greed": state.greed,
                "will": state.will,
                "will_status": state.will_status,
                "selected_tags": state.selected_tags,
                "intro_hidden": state.intro_hidden,
                "intro_visible": state.intro_visible,
                "birthday": state.birthday,
                "uploaded_image": self.character_repo.get_avatar_abspath(state.giantess_id, state.avatar_path) or None,
                "multiplier": state.height / state.original_height if state.original_height > 0 else 1.0,
            }
            if consume_points:
                self.character_repo.save(state)
        else:
            core = source.copy()
            if "base_intrusion" not in core:
                core["base_intrusion"] = core["personality_obj"].init_intrusion
            if "base_destruction" not in core:
                core["base_destruction"] = core["personality_obj"].init_destruction

        report_data = self._generate_report_data(core, selected_quip_styles)
        if report_data is None:
            return None

        if isinstance(source, CharacterSnapshot):
            state = source
            refund = self._apply_size_unlocks_from_report(state, report_data)
            if consume_points and refund > 0:
                state.action_points = min(100, state.action_points + refund)
                print(f"✨ 解锁了{refund // 3}个部位尺寸描述，返还{refund}行动点数。")

        if isinstance(source, CharacterSnapshot) and consume_points:
            state = source
            state.total_casualties += report_data["total_casualties"]
            cumul = state.total_casualties
            state.casualties_evolution.append(cumul)
            if personality.init_intrusion != 0:
                state.intrusion = report_data["curr_intrusion"]
            if personality.init_destruction != 0:
                state.destruction = report_data["curr_destruction"]
            state.intrusion_evolution.append(state.intrusion)
            state.destruction_evolution.append(state.destruction)
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
            report_text=self._build_report_text(report_data),
            detail_text=self._build_detail_text(report_data["body_parts"], report_data["height"]),
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
        )

    def _generate_report_data(self, core_state: dict, selected_quip_styles: List[str]) -> dict:
        curr_intrusion = core_state["base_intrusion"]
        curr_destruction = core_state["base_destruction"]
        name = core_state["name"]
        nick = core_state["nick"]
        height = core_state["height"]
        body_parts = core_state["body_parts"]
        personality_obj = core_state["personality_obj"]
        preset_obj = core_state["preset_obj"]
        selected_tags = core_state["selected_tags"]
        greed = core_state["greed"]
        will = core_state["will"]

        comparison_order = self.settings.get("comparison_order", "match")
        comparison_count = self.settings.get("comparison_count", 5)
        selected_parts = self.settings.get("selected_parts", ALL_PART_NAMES.copy())

        comparisons = get_comparisons(
            self.merged_landmarks,
            body_parts,
            order=comparison_order,
            limit=comparison_count,
            selected_tags=selected_tags,
            skip_base_prob=personality_obj.skip_base_prob,
            selected_parts=selected_parts
        )

        quips_working = copy.deepcopy(self.quips)
        locked_coords = {(4, 4)}
        breakthrough_attempts = 0

        size_cat = get_size_category(height)
        enable_confusion = self.settings.get("enable_confusion", False)
        rate_factor = self.settings.get("quip_rate_factor", 1.0)
        cumulative_actual = 0.0
        cumulative_base = 0.0

        style_meta_cache = {}
        for st in selected_quip_styles:
            style_meta_cache[st] = self.quip_repo.load_meta(st)

        quip_results = []
        total_casualties = 0.0
        has_quips = bool(self.quips)

        for idx, comp in enumerate(comparisons):
            if curr_intrusion == 0:
                curr_intrusion = random.randint(1, 4)
            if curr_destruction == 0:
                curr_destruction = random.randint(1, 4)

            part = comp["part"]
            size_str = format_size(comp['size'], base_size=height)
            ratio = comp["ratio"]
            suffix = "高" if comp["landmark"].dimension == "vertical" else (
                "长" if comp["landmark"].horizontal_type == "length" else "宽")
            if comp["landmark"].frequency == "unique":
                compare_text = f"    └─ 约等于{comp['landmark'].name}{suffix}度的{ratio:.2f}倍"
            else:
                if ratio < 0.5:
                    compare_text = f"    └─ 尚不足{comp['landmark'].name}的{suffix}度"
                elif ratio > 1.5:
                    compare_text = f"    └─ 完全超过{comp['landmark'].name}的{suffix}度"
                else:
                    compare_text = f"    └─ 相当于{comp['landmark'].name}的{suffix}度"

            quip_text = ""
            quip_style = ""
            coord = None
            actual_step = 0

            if has_quips:
                quip_text, quip_style, coord, actual_step, cumulative_actual, cumulative_base = select_quip_with_budget(
                    size_cat, curr_intrusion, curr_destruction,
                    quips_working, locked_coords,
                    cumulative_actual, cumulative_base, rate_factor,
                    step_index=idx,
                    selected_tags=selected_tags,
                    skip_base_prob=personality_obj.skip_base_prob,
                    posture_list=comp["posture"]
                )
                if quip_text is None:
                    quip_text = "尺寸通过远程测量取得，尚未收集到事件记录。"
                    quip_style = ""
                else:
                    quip_text = quip_text.replace('{name}', name).replace('{nick}', nick)
                    allow_confusion_map = {}
                    if quip_style and quip_style in style_meta_cache:
                        meta = style_meta_cache[quip_style]
                        custom_types = meta.get("custom_types", {})
                        for letter in ("c", "d", "e"):
                            if letter in custom_types:
                                allow_confusion_map[letter] = custom_types[letter].get("allow_confusion", False)
                    quip_text = replace_quip_tags(
                        quip_text, quip_style, size_cat,
                        self.detail_pools, quips_working=quips_working,
                        intr=curr_intrusion, dest=curr_destruction,
                        enable_confusion=enable_confusion,
                        allow_confusion_map=allow_confusion_map
                    )
                    quip_text = quip_text.replace('{name}', name).replace('{nick}', nick)
                    quip_text = re.sub(r'\s*\[summary:.*?\]', '', quip_text)

            if quip_text is not None:
                curr_intrusion += personality_obj.step_intrusion * actual_step
                curr_destruction += personality_obj.step_destruction * actual_step
                curr_intrusion = max(0.5, min(4.5, curr_intrusion))
                curr_destruction = max(0.5, min(4.5, curr_destruction))

                if (4, 4) in locked_coords and curr_intrusion >= 4.0 and curr_destruction >= 4.0:
                    if "涩涩" in selected_tags:
                        locked_coords.remove((4, 4))
                    else:
                        prob = min(1.0, (greed / 100.0) * (breakthrough_attempts + 1)) if will else 0.0
                        if random.random() < prob:
                            locked_coords.remove((4, 4))
                        else:
                            breakthrough_attempts += 1

            effective_step = actual_step if actual_step > 0 else 0.05
            env_factor = compute_environment_factor(quip_text) if quip_text else 0.3
            casualty_increase = compute_casualty(
                height, effective_step, curr_destruction, quip_text or "", env_factor)
            total_casualties += casualty_increase

            quip_results.append({
                "part": part,
                "size_str": size_str,
                "compare_text": compare_text,
                "quip_text": quip_text,
                "quip_style": quip_style,
                "intrusion": curr_intrusion,
                "destruction": curr_destruction,
                "coord": coord,
                "environment_factor": env_factor,
                "casualty_increase": casualty_increase
            })

        return {
            **core_state,
            "comparisons": comparisons,
            "quip_results": quip_results,
            "curr_intrusion": curr_intrusion,
            "curr_destruction": curr_destruction,
            "total_casualties": total_casualties,
            "size_cat": size_cat,
            "style_meta_cache": style_meta_cache,
        }

    def _build_report_text(self, data: dict) -> str:
        name = data["name"]
        nick = data["nick"]
        height = data["height"]
        original_height = data["original_height"]
        will_status = data["will_status"]
        intro_visible = data["intro_visible"]
        quip_results = data["quip_results"]
        today_str = datetime.date.today().strftime("%y/%m/%d")

        report = []

        world_setting = self.settings.get("world_setting", "appear")
        if world_setting in ("abs_giant", "rel_giant") and original_height is not None:
            strike_text = format_size(original_height)
            height_line = f"{name}    身高：[STRIKE]{strike_text}[/STRIKE] {format_size(height)}"
        else:
            height_line = f"{name}    身高：{format_size(height)}"
        report.append(height_line)
        report.append(f"{'═' * (16 + len(name))}\n")

        intro_display = intro_visible.strip()
        if intro_display:
            for line in intro_display.splitlines():
                if line.strip():
                    report.append(f"\u200b{line}")

        will_msg = {
            "implemented": f"✨ {name}表示内心渴望得到了回应。",
            "failed": f"💔 {name}似乎觉得还不够...",
            "within": f"✅ 身体规模满足了{name}的预期。"
        }.get(will_status, "")
        if will_msg:
            report.append(f"\n{will_msg}")

        report.append("")

        total_casualties = data.get("total_casualties", 0.0)
        for res in quip_results:
            report.append(f"📏 {res['part']} {res['size_str']}")
            report.append(res['compare_text'])
            quip_line = f"QUIP_LINE:\"{res['quip_text']}\"\n" if res['quip_text'] else "QUIP_LINE:"
            report.append(quip_line)

        report.append(f"    {'─' * 20}")
        report.append(f"    {today_str}")
        total_cas = int(total_casualties)
        if total_cas > 999999999:
            report.append("     本报告总计伤亡：999,999,999+")
        else:
            report.append(f"    本报告总计伤亡：{total_cas:,}")

        return "\n".join(report)

    @staticmethod
    def _build_detail_text(body_parts: dict, height: float) -> str:
        lines = []
        for k, v in body_parts.items():
            lines.append(f"{k:<12} {format_size(v, base_size=height)}")
        return "\n".join(lines)

    # ---------- 创建角色 ----------
    def character_from_core_or_report(self, source: Union[dict, ReportData]) -> CharacterSnapshot:
        if isinstance(source, ReportData):
            core = {
                "name": source.name,
                "nick": source.nick,
                "original_height": source.original_height,
                "height": source.height,
                "body_parts": source.body_parts,
                "personality_obj": source.personality,
                "preset_obj": source.preset,
                "base_intrusion": source.final_intrusion,
                "base_destruction": source.final_destruction,
                "greed": source.greed,
                "will": source.will,
                "will_status": source.will_status,
                "selected_tags": source.selected_tags,
                "intro_hidden": source.intro_hidden,
                "intro_visible": source.intro_visible,
                "birthday": source.birthday,
                "uploaded_image": source.uploaded_image_path,
                "total_casualties": source.total_casualties,
            }
        else:
            core = source

        giantess_id = f"{core['name']}_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
        total_casualties = core.get('total_casualties', 0.0)
        casualties_evolution = []
        if isinstance(source, ReportData):
            cumulative = 0.0
            for qr in source.casualty_breakdown:
                cumulative += qr.get('casualty_increase', 0.0)
                casualties_evolution.append(cumulative)
        from_report = isinstance(source, ReportData)
        state = CharacterSnapshot(
            giantess_id=giantess_id,
            name=core['name'],
            nick=core.get('nick', ''),
            original_height=core.get('original_height', 1.6),
            height=core['height'],
            personality=core['personality_obj'],
            greed=core.get('greed', 0.0),
            will=core.get('will', False),
            will_status=core.get('will_status'),
            selected_tags=core.get('selected_tags', []),
            intro_hidden=core.get('intro_hidden', ''),
            intro_visible=core.get('intro_visible', ''),
            birthday=core.get('birthday', ''),
            body_parts=core['body_parts'],
            intrusion=core['base_intrusion'],
            destruction=core['base_destruction'],
            intrusion_evolution=[],
            destruction_evolution=[],
            action_points=50,
            report_generated=from_report,
            negative_triggered=False,
            negative_reduction_intrusion=0.0,
            negative_reduction_destruction=0.0,
            total_casualties=total_casualties,
            casualties_evolution=casualties_evolution,
        )

        uploaded_image = core.get('uploaded_image')

        if from_report:
            state.size_unlocks = self._init_size_unlocks_from_report(source, state.body_parts)

        if uploaded_image and os.path.exists(uploaded_image):
            state.avatar_path = self.character_repo.save_avatar(
                state.giantess_id, uploaded_image,
                low_resolution=self.settings.get("save_low_resolution_image", False)
            )

        if not state.avatar_path:
            self.ensure_avatar_for_state(state)

        self.character_repo.save(state)
        return state

    def _detail_selected_parts(self) -> set:
        return set(self.settings.get("selected_parts", ALL_PART_NAMES.copy()))

    def _init_size_unlocks_from_report(self, report: ReportData, body_parts: dict) -> Dict[str, str]:
        """从报告创建角色时初始化尺寸解锁信息。"""
        selected = self._detail_selected_parts()
        mentioned = {}
        for qr in (report.quip_results or []):
            part = qr.get("part", "")
            if part and part != "身高":
                mentioned[part] = build_size_description(qr)
        unlocks = {}
        for part in body_parts:
            if part == "身高":
                continue
            if part in mentioned:
                unlocks[part] = mentioned[part]
            elif part in selected:
                unlocks[part] = "MEASURED"
            else:
                unlocks[part] = ""
        return unlocks

    def _apply_size_unlocks_from_report(self, state: CharacterSnapshot, report_data: dict) -> int:
        """根据报告正文/详细信息更新角色的尺寸解锁信息，返回应返还的行动点数。"""
        unlocks = dict(state.size_unlocks or {})
        refund = 0
        selected = self._detail_selected_parts()
        mentioned = {}
        for qr in (report_data.get("quip_results", []) or []):
            part = qr.get("part", "")
            if part and part != "身高":
                mentioned[part] = build_size_description(qr)
        info_update_rate = self.settings.get("info_update_rate", 0.5)
        body_parts = report_data.get("body_parts", {}) or {}
        for part in body_parts:
            if part == "身高":
                continue
            old = unlocks.get(part, "")
            if part in mentioned:
                if old == "":
                    refund += 3
            elif part in selected:
                if old == "":
                    unlocks[part] = "MEASURED"
        updates = {part: desc for part, desc in mentioned.items() if part in body_parts}
        state.size_unlocks = apply_size_unlock_updates(unlocks, updates, info_update_rate)
        return refund

    # ---------- 副本准备 ----------
    def dungeon_data_from_any(self, source: Union[dict, CharacterSnapshot, ReportData]) -> dict:
        if isinstance(source, CharacterSnapshot):
            state = source
            personality = state.personality
            if personality is None:
                return None
            return {
                "name": state.name,
                "nick": state.nick,
                "height": state.height,
                "original_height": state.original_height,
                "personality_obj": personality,
                "preset_obj": None,
                "body_parts": state.body_parts,
                "intro_hidden": state.intro_hidden,
                "intro_visible": state.intro_visible,
                "selected_tags": state.selected_tags,
                "birthday": state.birthday,
                "uploaded_image": self.character_repo.get_avatar_abspath(state.giantess_id, state.avatar_path) or None,
                "greed": state.greed,
                "curr_intrusion": state.intrusion,
                "curr_destruction": state.destruction,
            }
        elif isinstance(source, ReportData):
            return {
                "name": source.name,
                "nick": source.nick,
                "height": source.height,
                "original_height": source.original_height,
                "personality_obj": source.personality,
                "preset_obj": None,
                "body_parts": source.body_parts,
                "intro_hidden": source.intro_hidden,
                "intro_visible": source.intro_visible,
                "selected_tags": source.selected_tags,
                "birthday": source.birthday,
                "uploaded_image": source.uploaded_image_path,
                "greed": source.greed,
                "curr_intrusion": source.final_intrusion,
                "curr_destruction": source.final_destruction,
            }
        else:
            return {
                "name": source["name"],
                "nick": source.get("nick", ""),
                "height": source["height"],
                "original_height": source.get("original_height", 1.6),
                "personality_obj": source["personality_obj"],
                "preset_obj": None,
                "body_parts": source.get("body_parts", {}),
                "intro_hidden": source.get("intro_hidden", ""),
                "intro_visible": source.get("intro_visible", ""),
                "selected_tags": source.get("selected_tags", []),
                "birthday": source.get("birthday", ""),
                "uploaded_image": source.get("uploaded_image"),
                "greed": source.get("greed", 0),
                "curr_intrusion": source.get("base_intrusion", 0.0),
                "curr_destruction": source.get("base_destruction", 0.0),
            }

    # ---------- 头像兜底 ----------
    def ensure_avatar_for_state(self, state: "CharacterSnapshot") -> str:
        """未上传形象且开启“未上传形象时使用身材预览图”时，把身材预览渲染为 PNG 作为头像。

        返回头像相对路径（同 avatar_path）；已有头像、未开启或缺少身材数据时返回 ''。
        成功生成后会写入档案目录并保存角色状态。
        """
        if state.avatar_path:
            abspath = self.character_repo.get_avatar_abspath(state.giantess_id, state.avatar_path)
            if abspath and os.path.exists(abspath):
                return state.avatar_path
        if not self.settings.get("use_preview_image_as_avatar", False):
            return ""
        if not state.body_parts or state.height <= 0:
            return ""
        try:
            from ui.exploration.creation_params_dlg import render_body_preview_to_file
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".png")
            os.close(tmp_fd)
            try:
                if not render_body_preview_to_file(state.body_parts, state.height, tmp_path):
                    return ""
                state.avatar_path = self.character_repo.save_avatar(
                    state.giantess_id, tmp_path, low_resolution=False)
            finally:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            self.character_repo.save(state)
            return state.avatar_path
        except Exception:
            return ""

    def ensure_avatar_for_state_id(self, giantess_id: str) -> str:
        """按角色 id 加载并确保头像（用于角色列表等只持有 id 的展示路径）。"""
        state = self.character_repo.load(giantess_id)
        if state is None:
            return ""
        return self.ensure_avatar_for_state(state)

    # ==================== 状态管理 ====================

    def load_character_state(self, giantess_id: str) -> Optional[CharacterSnapshot]:
        """加载角色状态，更新行动点并应用衰退。返回 None 表示无效。"""
        state = self.character_repo.load(giantess_id)
        if not state:
            return None

        now = datetime.datetime.now()
        updated = datetime.datetime.fromisoformat(state.updated_at)
        delta_minutes = (now - updated).total_seconds() / 60.0
        add_points = int(delta_minutes * 0.5)
        state.action_points = min(100, state.action_points + add_points)

        if state.personality is None:
            return None

        saved_at = state.updated_at  # 本次加载前的保存时间（用于新闻七天判断）
        self.state_service.apply_negative_evolution(state)
        self.state_service.apply_step_decay(state, 0.1)
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

    # ==================== 角色卡导出 ====================

    def build_export_card_data(self, name: str, nick: str, original_height: float,
                                personality_obj, preset_obj,
                                intro_hidden: str, intro_visible: str,
                                selected_tags: list, birthday: str,
                                uploaded_image_path: str) -> dict:
        """构建角色卡导出数据字典"""
        from services.image_service import ImageService
        image_b64 = ImageService.file_to_base64(uploaded_image_path) or None
        return {
            "version": "2.0",
            "name": name,
            "nick": nick,
            "original_height": original_height,
            "personality_data": asdict(personality_obj),
            "preset_data": asdict(preset_obj),
            "intro_hidden": intro_hidden,
            "intro_visible": intro_visible,
            "tags": selected_tags,
            "birthday": birthday,
            "image_b64": image_b64
        }

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
