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
from address_model import (
    resolve_full_address, world_of, depth_of, distance_m, touches,
    cell_width_m, can_pair, jitter_address_cell,
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
        self.merged_landmarks = []
        self._landmark_styles = {}
        for _style in self.selected_styles:
            for _lm in landmark_repo.load(_style):
                self.merged_landmarks.append(_lm)
                self._landmark_styles[id(_lm)] = _style
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
        self.merged_landmarks = []
        self._landmark_styles = {}
        for _style in self.selected_styles:
            for _lm in self.landmark_repo.load(_style):
                self.merged_landmarks.append(_lm)
                self._landmark_styles[id(_lm)] = _style
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
                "landmark_durability": state.landmark_durability.copy(),
                "position": state.position or "",
            }
            if consume_points:
                self.character_repo.save(state)

            report_data = None
            for _attempt in range(5):
                report_data = self._generate_report_data(core, selected_quip_styles)
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
            report_data = self._generate_report_data(core, selected_quip_styles)
            if report_data is None or report_data.get("stuck"):
                return None

        if isinstance(source, CharacterSnapshot):
            state = source
            refund = self._apply_size_unlocks_from_report(state, report_data)
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
            state.landmark_durability = report_data.get("landmark_durability", {})
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
            position=report_data.get("position", ""),
        )

    def stuck_options(self, state: CharacterSnapshot, stuck: dict) -> dict:
        """无路可走时可供角色选择的目标：同一世界观内的地标地址，以及可选世界观。"""
        registers = self.landmark_repo.load_style_registers(self.selected_styles)
        durability = state.landmark_durability or {}
        seen = {}
        for lm in self.merged_landmarks:
            if lm.frequency != "unique":
                continue
            # 耐久低于 0.5 的独特地标已不适合作落脚点
            if durability.get(lm.name, 1.0) < 0.5:
                continue
            addr = self._landmark_full_address(lm, registers)
            if addr and addr not in seen:
                seen[addr] = lm.name
        reg_worlds = sorted({world_of(r) for r in registers.values() if world_of(r)})
        worlds = stuck.get("worlds") or reg_worlds
        return {
            "stuck": stuck,
            "addresses": [{"address": a, "name": n} for a, n in seen.items()],
            "worlds": worlds,
        }

    # ==================== 注册地址系统：规划与筛选 ====================

    def _landmark_full_address(self, landmark, registers=None) -> str:
        """组合风格注册地址与地标地址为完整地址。无注册返回 ''。"""
        style = self._landmark_styles.get(id(landmark), "")
        reg = ""
        if style:
            reg = (registers or {}).get(style, "")
            if reg is None:
                reg = self.landmark_repo.load_style_address(style)
        return resolve_full_address(reg, getattr(landmark, "address", "") or "")

    @staticmethod
    def _durable_ok(cand, durability, engaged: bool) -> bool:
        lm = cand["landmark"]
        if lm.frequency != "unique":
            return True
        value = durability.get(lm.name, 1.0)
        return value >= (0.5 if engaged else 0.0)

    def _quip_allowed_styles(self, landmark_addr_text: str, quip_registers: dict):
        """某条地标对比可衔接的描述风格：地址为空（未注册）的风格，或与其
        地域同址/包含且规模组/私有名约束互相满足的风格。

        返回 None 表示不限（地标本身无地址 / 旧内容兼容）。
        """
        if not landmark_addr_text:
            return None
        allowed = []
        for style, reg in quip_registers.items():
            reg = (reg or "").strip()
            if not reg or can_pair(reg, landmark_addr_text):
                allowed.append(style)
        return allowed

    @staticmethod
    def _prune_quips_by_styles(quips_working: dict, allowed) -> None:
        """把描述池中不属于 allowed 风格的事件就地移除（allowed=None 不限制）。"""
        if allowed is None:
            return
        allowed_set = set(allowed)
        for size_cat, matrix in quips_working.items():
            for coord, quip_list in list(matrix.items()):
                kept = [q for q in quip_list
                        if isinstance(q, dict) and q.get("style", "") in allowed_set]
                if kept:
                    matrix[coord] = kept
                else:
                    matrix.pop(coord, None)

    def _shift_position_cell(self, position_text: str, reach: float) -> str:
        """按“身高10倍/地址规模”概率被触发后：把位置末位更新到同详细程度的其它单元。

        保留地址上的绝对规模/约束段前缀。
        """
        return jitter_address_cell(position_text, reach)

    def _plan_address_comparisons(self, candidates, position, height, skip_base_prob,
                                  durability):
        """地址规则规划：返回 dict(engaged, stuck, position, comparisons)。

        首地标锚定（无位置时不受距离限制），随后只取与角色位置距离
        < 10×身高 的地标地址。全部地标都够不着 / 范围内独特地标耐久
        均 <0.5 时给出 stuck 原因，由调用方决定切换地址 / 世界观或放弃。
        未注册地址的内容（含旧数据）保持原行为。
        """
        registers = self.landmark_repo.load_style_registers(self.selected_styles)

        def cand_addr(cand) -> str:
            lm = cand["landmark"]
            if lm.frequency != "unique":
                return ""
            return self._landmark_full_address(lm, registers)

        # engaged：当前选中风格中确有可锚定（带完整地址）的独特地标候选
        addressable = [c for c in candidates if cand_addr(c)]
        engaged = bool(addressable)
        limit = int(self.settings.get("comparison_count", 5) or 5)
        if not engaged:
            chosen = [c for c in candidates
                      if self._durable_ok(c, durability, engaged=False)][:limit]
            return {"engaged": False, "stuck": None, "position": position or "",
                    "comparisons": chosen}

        reg_worlds = sorted({world_of(r) for r in registers.values() if world_of(r)})
        pos_world = world_of(position)
        if position and pos_world and reg_worlds and pos_world not in reg_worlds:
            stuck = {"reason": "world_mismatch", "current_world": pos_world,
                     "worlds": reg_worlds}
            return {"engaged": True, "stuck": stuck, "position": position,
                    "comparisons": []}

        # 可用（耐久满足）候选
        usable = [c for c in candidates if self._durable_ok(c, durability, engaged=True)]
        usable_addrs = [c for c in usable if cand_addr(c)]

        reach = 10.0 * height
        scan_radius = 50.0 * max(0.0, skip_base_prob or 0.0) * height

        if position and pos_world and (not reg_worlds or pos_world in reg_worlds):
            pos_now = position
            pick_order = usable
        else:
            # 新角色尚无位置：首个地标不限地址，取排序中第一个可用“带地址”地标锚定
            if not usable_addrs:
                # 独特地标都未注册或全部耐久不足：退回旧行为
                chosen = [c for c in candidates
                          if self._durable_ok(c, durability, engaged=False)][:limit]
                return {"engaged": False, "stuck": None, "position": position or "",
                        "comparisons": chosen}
            anchor = usable_addrs[0]
            pos_now = cand_addr(anchor)
            pick_order = [anchor] + [c for c in usable if c is not anchor]

        chosen = []
        for cand in pick_order:
            if len(chosen) >= limit:
                break
            addr = cand_addr(cand)
            if addr and pos_now:
                d = distance_m(pos_now, addr)
                if d is None or d >= reach:
                    continue
            chosen.append(cand)
            # 抵达/路过地标后更新角色位置：
            # - 地标地址比当前位置更细或同级 → 立即把位置更新到该地标地址；
            # - 地标地址比当前位置更粗（只注册到上级区域）→ 以“身高10倍/地址规模”
            #   概率把位置末位更新到同一详细程度的其它可用单元（下次选取前生效）。
            if engaged and addr and pos_now:
                if depth_of(addr) >= depth_of(pos_now):
                    pos_now = addr
                elif touches(pos_now, addr) and \
                        random.random() < min(1.0, reach / max(1.0, cell_width_m(pos_now))):
                    pos_now = self._shift_position_cell(pos_now, reach)

        stuck = None
        if not chosen:
            stuck = {"reason": "no_reachable", "position": pos_now}
        elif engaged and pos_now and scan_radius > 0:
            near = []
            for cand in candidates:
                lm = cand["landmark"]
                if lm.frequency != "unique":
                    continue
                addr = cand_addr(cand)
                if not addr:
                    continue
                d = distance_m(pos_now, addr)
                if d is not None and d < scan_radius:
                    near.append(cand)
            if near and all(durability.get(c["landmark"].name, 1.0) < 0.5
                            for c in near):
                stuck = {"reason": "all_damaged", "position": pos_now}

        return {"engaged": engaged, "stuck": stuck, "position": pos_now,
                "comparisons": chosen}

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

        blocked_words = self.settings.get("blocked_words", [])
        candidates = get_comparisons(
            self.merged_landmarks,
            body_parts,
            order=comparison_order,
            limit=max(comparison_count * 8, 100),
            selected_tags=selected_tags,
            skip_base_prob=personality_obj.skip_base_prob,
            selected_parts=selected_parts,
            blocked_words=blocked_words,
        )

        landmark_durability = core_state.get("landmark_durability", {})
        # 地址规划：锚定、10 倍身高可达筛选、无路可走判定
        plan = self._plan_address_comparisons(
            candidates,
            core_state.get("position") or "",
            height,
            personality_obj.skip_base_prob,
            landmark_durability,
        )
        position_now = plan["position"] or core_state.get("position") or ""
        stuck = plan["stuck"]
        if stuck is not None:
            return {
                **core_state,
                "comparisons": [],
                "quip_results": [],
                "total_casualties": 0.0,
                "curr_intrusion": curr_intrusion,
                "curr_destruction": curr_destruction,
                "size_cat": get_size_category(height),
                "position": position_now,
                "engaged": plan["engaged"],
                "stuck": stuck,
            }
        comparisons = plan["comparisons"]

        quips_working = copy.deepcopy(self.quips)
        locked_coords = {(4, 4)}
        breakthrough_attempts = 0
        previous_frequency = None   # 上一次匹配地标的风貌（unique/common）；首次匹配视为切换

        size_cat = get_size_category(height)
        enable_confusion = self.settings.get("enable_confusion", False)
        rate_factor = self.settings.get("quip_rate_factor", 1.0)
        cumulative_actual = 0.0
        cumulative_base = 0.0

        style_meta_cache = {}
        for st in selected_quip_styles:
            style_meta_cache[st] = self.quip_repo.load_meta(st)
        quip_registers = self.quip_repo.load_style_registers(selected_quip_styles)
        _quip_pruned_styles = None  # 记录上次剪枝的允许集，避免重复无谓剪枝

        quip_results = []
        total_casualties = 0.0
        has_quips = bool(self.quips)
        landmark_registers = self.landmark_repo.load_style_registers(self.selected_styles)

        for idx, comp in enumerate(comparisons):
            if curr_intrusion == 0:
                curr_intrusion = random.randint(1, 4)
            if curr_destruction == 0:
                curr_destruction = random.randint(1, 4)

            # 地标风貌切换（首次匹配视为切换到该风貌）时按性格敏感值调整坐标
            frequency = comp["landmark"].frequency
            if frequency != previous_frequency:
                curr_intrusion, curr_destruction = self.state_service.apply_landmark_switch(
                    personality_obj, curr_intrusion, curr_destruction, frequency)
                previous_frequency = frequency

            part = comp["part"]
            size_str = format_size(comp['size'], base_size=height)
            ratio = comp["ratio"]
            suffix = "高" if comp["landmark"].dimension == "vertical" else (
                "长" if comp["landmark"].horizontal_type == "length" else "宽")
            if comp["landmark"].frequency == "unique":
                lm_name = comp["landmark"].name
                durability = landmark_durability.get(lm_name, 1.0)
                height_ratio = height / comp["landmark"].size
                damage = ratio * (height_ratio ** 2) * curr_destruction * 0.1
                durability -= damage
                landmark_durability[lm_name] = durability
                durability_suffix = "（残破的）" if durability < 0.5 else ""
                compare_text = f"    └─ 约等于{comp['landmark'].name}{durability_suffix}{suffix}度的{ratio:.2f}倍"
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
                # 地址规则：地标对比后只接“地址为空或距离为0”的描述风格
                lm_addr = ""
                if comp["landmark"].frequency == "unique":
                    lm_addr = self._landmark_full_address(comp["landmark"], landmark_registers)
                allowed_styles = self._quip_allowed_styles(lm_addr, quip_registers)
                if allowed_styles != _quip_pruned_styles:
                    self._prune_quips_by_styles(quips_working, allowed_styles)
                    _quip_pruned_styles = allowed_styles

                quip_text, quip_style, coord, actual_step, cumulative_actual, cumulative_base = select_quip_with_budget(
                    size_cat, curr_intrusion, curr_destruction,
                    quips_working, locked_coords,
                    cumulative_actual, cumulative_base, rate_factor,
                    step_index=idx,
                    selected_tags=selected_tags,
                    skip_base_prob=personality_obj.skip_base_prob,
                    posture_list=comp["posture"],
                    blocked_words=blocked_words,
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
                curr_intrusion, curr_destruction = self.state_service.advance_coordinates(
                    personality_obj, curr_intrusion, curr_destruction, actual_step)

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
                "step": effective_step,
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
            "landmark_durability": landmark_durability,
            "position": position_now,
            "engaged": plan["engaged"],
            "stuck": None,
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
                "position": source.position or "",
            }
        else:
            core = source

        giantess_id = f"{core['name']}_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
        total_casualties = core.get('total_casualties', 0.0)
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
            action_points=50,
            report_generated=from_report,
            position=core.get('position', '') or '',
        )
        # 初始演化行：记录创建时的介入度/破坏性/累计伤亡；
        # 来自报告时，步进取报告内各事件步进之和
        step = 0.0
        if from_report:
            step = sum(qr.get("step", 0.0) for qr in (source.casualty_breakdown or []))
        state.record_change(step=step, intrusion=core['base_intrusion'],
                            destruction=core['base_destruction'],
                            casualties=total_casualties,
                            source="character_from_core_or_report")

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
        """从报告创建角色时初始化尺寸解锁信息。

        未提及的选中部位仅在"测量所有尺寸"开启时标记为 MEASURED；
        关闭时报告不展示未解锁的尺寸，保持锁定（""）。
        """
        selected = self._detail_selected_parts()
        measure_all = self.settings.get("show_all_details", False)
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
            elif part in selected and measure_all:
                unlocks[part] = "MEASURED"
            else:
                unlocks[part] = ""
        return unlocks

    def _apply_size_unlocks_from_report(self, state: CharacterSnapshot, report_data: dict) -> int:
        """根据报告正文/详细信息更新角色的尺寸解锁信息，返回应返还的行动点数。

        未提及的选中部位仅在"测量所有尺寸"开启时标记为 MEASURED；
        关闭时报告不展示未解锁的尺寸，保持锁定（""）。
        """
        unlocks = dict(state.size_unlocks or {})
        refund = 0
        selected = self._detail_selected_parts()
        measure_all = self.settings.get("show_all_details", False)
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
            elif part in selected and measure_all:
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

    def build_export_card_from_state(self, state: CharacterSnapshot) -> dict:
        """从已加载角色还原性格/身材并构建角色卡数据。"""
        from services.creation_service import CreationService
        if state is None or state.personality is None:
            raise ValueError("角色没有性格数据，无法导出角色卡")
        height = state.height or state.original_height or 0
        preset_obj = CreationService.preset_from_body_parts(
            state.body_parts, height,
            name=f"{state.name}的身材" if state.name else "还原身材")
        if preset_obj is None:
            raise ValueError("角色没有身材数据，无法导出角色卡")
        avatar = self.character_repo.get_avatar_abspath(
            state.giantess_id, state.avatar_path) or ""
        return self.build_export_card_data(
            name=state.name,
            nick=state.nick,
            original_height=state.original_height,
            personality_obj=state.personality,
            preset_obj=preset_obj,
            intro_hidden=state.intro_hidden or "",
            intro_visible=state.intro_visible or "",
            selected_tags=state.selected_tags or [],
            birthday=state.birthday or "",
            uploaded_image_path=avatar,
        )

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
