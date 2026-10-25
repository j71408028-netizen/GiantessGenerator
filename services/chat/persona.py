"""角色人设的统一组装入口。

此前性格描述、公开/隐藏介绍、个性标签、体型尺度等"人设素材"散落在
intro 面板、副本提示词、探索上下文等各处自行拼接。本模块把它们统一为
一个纯函数，供聊天协议与未来的其他 AI 功能共用；UI 展示仍由各自面板
自行决定（如 `tag_hints` 提示词只属于 intro 面板的界面文案，不在行为包
覆盖范围内）。
"""

from core.address_model import format_addr_compact
from core.logic import format_size
from core.models import CharacterSnapshot
from core.scale_reference import (destruction_level_text,
                                      intrusion_level_text,
                                      size_definition_text)
from services.character.profile_text import describe_rhythm, describe_spatial
from services.character.spatial import build_spatial_profile


def build_character_persona(state: CharacterSnapshot,
                            include_hidden: bool = False) -> str:
    """把角色快照组装为一份完整人设文本。

    include_hidden 为 True 时注入隐藏介绍（聊天场景按态度门槛调用，
    供角色"关系深化后"透露；报告等其他场景一律 False）。
    """
    personality = state.personality
    lines = []
    nick = state.nick or ""
    lines.append(f"名字：{state.name}" + (f"（昵称：{nick}）" if nick else ""))
    if personality is not None:
        lines.append(f"性格：{personality.description or '她有着独特的性格。'}")
    lines.append(f"公开介绍：{state.intro_visible or '（无）'}")
    if include_hidden and state.intro_hidden:
        lines.append(f"隐藏介绍（仅对亲近的人透露）：{state.intro_hidden}")
    if state.selected_tags:
        lines.append("个性标签：" + " ".join(f"#{t}" for t in state.selected_tags))
    if state.birthday:
        lines.append(f"生日：{state.birthday}")
    if state.will_status:
        lines.append(f"意志状态：{state.will_status}")
    lines.append(f"当前身高：{format_size(state.height)}")
    size_text = size_definition_text(state.height)
    if size_text:
        lines.append(size_text)
    lines.append(f"介入度（{state.intrusion:.1f}/4）：{intrusion_level_text(state.intrusion)}")
    lines.append(f"破坏性（{state.destruction:.1f}/4）：{destruction_level_text(state.destruction)}")
    if state.position:
        lines.append(f"当前位置：{format_addr_compact(state.position)}")
    # 作息/空间偏好是演化表的派生结论，随人设一并注入：聊天系统提示与
    # 首次聊天习惯判定（ensure_chat_params）都以此为准；无样本时为空。
    rhythm_text = describe_rhythm(state.evolution)
    if rhythm_text:
        lines.append("作息（从活动记录学到的习惯）：\n" + rhythm_text)
    spatial_text = describe_spatial(build_spatial_profile(state),
                                    state.position)
    if spatial_text:
        lines.append("活动范围（从到访记录学到的习惯）：\n" + spatial_text)
    measured = [(part, desc) for part, desc in state.size_unlocks.items()
                if desc and desc != "MEASURED"]
    if measured:
        lines.append("她已意识到自己这些部位的具体尺寸："
                     + "；".join(f"{part}（{desc}）" for part, desc in measured))
    return "\n".join(lines)
