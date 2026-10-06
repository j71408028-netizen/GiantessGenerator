# -*- coding: utf-8 -*-
"""角色装配：报告/核心字典 → ``CharacterSnapshot``，以及尺寸解锁与头像兜底。

由 ``core/context.py`` 的 ``ExplorationContext`` 按职责抽出（阶段 3.1 / S1）。
这里的方法原本散在 God object 里，靠 ``self.character_repo`` / ``self.settings``
取协作者；抽出后改为在构造时注入同样的两个对象，行为不变。

**归属说明**：这些方法"严格上不算 core 内容"——它们要写 ``character_repo``
（persistence）、要读设置、要用头像渲染（services），是**服务层**的活。
"""

import datetime
import os
import tempfile
from typing import Any, Dict, Optional, Union

from core.logic import ALL_PART_NAMES, apply_size_unlock_updates, build_size_description
from core.models import CharacterSnapshot, ReportData
from persistence.character_repo import CharacterRepo


class CharacterAssembler:
    """把报告 / 核心字典装配成角色档案，并负责尺寸解锁与头像兜底。

    协作者：

    - ``character_repo``：角色档案的读写（含头像落盘 / 取绝对路径）；
    - ``settings``：与 ``ExplorationContext`` **共用同一个 dict 对象**（不复制），
      因此上层对设置的修改在这里立即可见。

    注意：``settings`` 是共享引用而非快照，切勿在本类里对它做整体替换。
    """

    def __init__(self, character_repo: CharacterRepo, settings: Dict[str, Any]):
        self.character_repo = character_repo
        self.settings = settings

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
        # 步长随本次报告演化后的当前值写入角色存储（新角色步长起点）
        if from_report:
            state.step_intrusion = source.step_intrusion or 0.0
            state.step_destruction = source.step_destruction or 0.0

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

    def size_unlocks_from_report(self, report: ReportData) -> Dict[str, str]:
        """从一份报告现推部位解锁表（未持角色档案时看尺寸一览用）。

        规则与建号时初始化 ``CharacterSnapshot.size_unlocks`` 完全一致：报告里提及
        过的部位才算「已测量」，其余留空——于是「只看报告」与「有角色」在尺寸一览
        里露出的部位一样多，不会一次抖出全部尺寸。
        """
        return self._init_size_unlocks_from_report(report, report.body_parts)

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

    def apply_size_unlocks_from_report(self, state: CharacterSnapshot, report_data: dict) -> int:
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
            # 延迟 import：services.preview 顶层会拉进 PIL，本模块的其它方法
            # （尺寸解锁、副本数据）并不需要它，放在顶层会扩大导入足迹。
            from services.preview import render_body_preview_to_file
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
