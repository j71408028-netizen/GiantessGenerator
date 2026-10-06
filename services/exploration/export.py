# -*- coding: utf-8 -*-
"""角色卡导出数据（阶段 3.1 / S1 从 ``core/context.py`` 抽出）。

这两个函数原本是 ``ExplorationContext`` 的方法，只用到 ``services.image_service``
与 ``services.creation_service``，与探索上下文的其余状态无关，因此抽成模块级
函数而不是再建一个类。

被搬动的方法名（``build_export_card_data`` / ``build_export_card_from_state``）
在薄外观 ``ExplorationContext`` 上原样保留，ui 调用点不必改。
"""

from dataclasses import asdict

from core.models import CharacterSnapshot


def build_export_card_data(name: str, nick: str, original_height: float,
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


def build_export_card_from_state(state: CharacterSnapshot,
                                 character_repo) -> dict:
    """从已加载角色还原性格/身材并构建角色卡数据。

    ``character_repo`` 用来取头像的绝对路径——这是本函数唯一的外部状态，
    因此作为参数显式注入而不是再造一个类。
    """
    from services.creation_service import CreationService
    if state is None or state.personality is None:
        raise ValueError("角色没有性格数据，无法导出角色卡")
    height = state.height or state.original_height or 0
    preset_obj = CreationService.preset_from_body_parts(
        state.body_parts, height,
        name=f"{state.name}的身材" if state.name else "还原身材")
    if preset_obj is None:
        raise ValueError("角色没有身材数据，无法导出角色卡")
    avatar = character_repo.get_avatar_abspath(
        state.giantess_id, state.avatar_path) or ""
    return build_export_card_data(
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
