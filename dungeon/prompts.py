import math
import random
import re

from logic import ALL_PART_NAMES, format_size, get_comparisons, replace_quip_tags, \
    should_skip_by_part_tags, contains_blocked_word
from .models import DungeonTextType


# 部位名 → 触发正则：正文中出现即视为"注意到该部位"。
# 具体部位（脚踝/小臂/食指等）优先于其上级词（脚/臂/指），减少重叠误判。
_PART_TRIGGER_PATTERNS = {
    "步长": r"步长|步伐|迈步|跨步|一步",
    "腿长": r"长腿|双腿|小腿|腿部|腿",
    "臂长": r"手臂|双臂|胳膊|臂",
    "胸宽": r"胸脯|胸前|胸口|胸部|胸膛|乳房|胸",
    "脚长": r"脚掌|脚底|双脚|脚丫|玉足|赤足|足尖|脚(?!步|踝)",
    "脚踝高度": r"脚踝|脚脖子|踝",
    "膝盖高度": r"膝盖|膝",
    "大腿直径": r"大腿|腿缝|腿间",
    "小臂直径": r"手臂|前臂|小臂|胳膊",
    "手掌长度": r"手掌|掌心|掌(?!握|控|管)",
    "食指长度": r"食指|手指",
    "食指直径": r"食指|手指|指尖",
    "指缝宽度": r"指缝|指间",
    "指纹宽度": r"指纹",
}
_PART_TRIGGER_RES = {part: re.compile(pat) for part, pat in _PART_TRIGGER_PATTERNS.items()}

# 句子中出现即表明身体部位属于"她"的标记
_HER_RE = re.compile(
    r"她(?!们)|自己|巨女|少女|姑娘|女子|女孩|"
    r"巨腿|巨脚|巨手|巨胸|巨掌|巨指|巨臂|巨足|巨人"
)

# 句子中出现即表明身体部位可能属于"其他人"的标记
_OTHER_OWNER_RE = re.compile(
    r"人们|人群|众人|大家|他们|她们|它们|他|路人|士兵|市民|居民|群众|平民|凡人|人类|"
    r"灾民|难民|幸存者|所有人|每个人|众生|这些|那些|这群|那群"
)


class DungeonPromptBuilder:
    """副本提示词构建器。context 只需提供窗口当前使用的同名数据。"""

    def __init__(self, context):
        self.context = context

    def build_system_prompt(self) -> str:
        session = self.context
        height_match = self._get_height_match()
        height_info = f"身高：{height_match}\n" if height_match else ""
        custom_names = [a["name"] for a in session.evolution_attrs if a["type"] == "custom"]
        if custom_names:
            fields = ", ".join(f'"{name}": -1/0/1' for name in custom_names)
            output = f'"custom_directions": {{{fields}}},  // 自定义属性变化方向，每个属性值为 -1（下降）、0（平稳）、1（上升）\n'
            parse = f"custom_directions 是一个 JSON 对象，键为自定义属性名称（{', '.join(custom_names)}），值为 -1/0/1。"
        else:
            output = '"custom_directions": {},  // 无自定义属性时固定为空对象\n'
            parse = "如果没有自定义属性，custom_directions 必须为空对象 {}。"

        prompt = (
            f"你是一位细腻的叙事作家，正在讲述一个关于巨大化少女（名字{session.name}，昵称{session.nick}）的故事。\n"
            f"性格描述：{session.personality.description or '她有着独特的性格。'}\n{height_info}"
            "请根据当前故事阶段和提供的信息，生成一段简洁的叙述（一句话，50字以内），并同时输出整体故事氛围方向（direction）和各自定义属性的变化方向（custom_directions）。\n"
            "direction 取值范围：-1（消极/挫败），0（中性），1（积极/满足）。\n"
            f"{parse}\n你必须严格按照以下 JSON 格式输出，不要包含任何额外注释或文字：\n"
            "{\n    \"text\": \"叙述内容\",\n    \"direction\": 1,\n"
            f"    {output}}}\n如果某个自定义属性未提及或不适用，其值默认为 0。\n"
        )
        global_prompt = session.initial_prompt
        section_prompt = session.section_prompts.get("global", "")
        if section_prompt:
            global_prompt += "\n" + section_prompt
        if global_prompt:
            prompt += f"\n\n故事基调与设定：\n{global_prompt}"
        return prompt

    def build_user_prompt(self, text_type: DungeonTextType) -> str:
        session = self.context
        session.prompted_parts = set()
        instructions = {
            DungeonTextType.BACKGROUND: "描写环境或背景细节，营造氛围。",
            DungeonTextType.BRANCH: "描述其他角色之间的对话或互动。",
            DungeonTextType.DIALOG: "描写与她有关的对话（她说话或别人对她说话）。",
            DungeonTextType.INTERACTION: "描写她与其他人的身体互动（如触摸、踩踏等）。",
            DungeonTextType.ACTION: "描写她独自做出的行动（如移动、观察、破坏等）。",
        }
        context_parts = []
        if session.last_ai_text:
            selected = self._select_prompted_part(self._detect_prompted_parts(session.last_ai_text))
            if selected:
                part, match = selected
                session.keyword_match_given.add(part)
                session.prompted_parts.add(part)
                intrusion = getattr(getattr(session, "dungeon_state", None), "intrusion", 0.0)
                if intrusion < 3:
                    context_parts.append("人们注意到了她的身体尺寸：" + match)
                else:
                    context_parts.append("她注意到了自己的身体尺寸：" + match)
        quip = self._get_nearest_quip_no_consume()
        if quip:
            context_parts.append(f"她的态度类似：{quip}")
        if getattr(session, "option_choice", None):
            choice = session.option_choice
            choice_prompt = choice.get("prompt", "")
            if choice_prompt:
                context_parts.append(f"她刚才选择了：{choice_prompt}")
        reference = ""
        if context_parts:
            reference = "\n以下信息可作为你叙事的参考，但不代表目前情境：\n" + "\n".join(context_parts)
        history = "".join(
            f"[{step['type']}] {step['text'][:100]}...\n"
            for step in session.replay_data[-3:]
            if "type" in step and step.get("text")
        )
        if history:
            history = f"最近的故事片段：\n{history}\n"
        result = f"{history}继续叙述后续情节或描写{instructions.get(text_type, '生成一段合适的叙述。')}\n请严格按系统提示的 JSON 格式输出，不要添加额外解释。{reference}"

        # 延迟插入：本段之后将固定插入一段内容，生成时需带上前后衔接限制
        pending = getattr(session, "pending_insertions", None)
        if pending:
            item = pending[0]
            if item.get("delayed"):
                prefix = {
                    "background": "【环境】",
                    "branch": "【分支】",
                    "dialog": "【对话】",
                    "interaction": "【互动】",
                    "action": "【行动】",
                }.get(item.get("text_type", ""), "【未知】")
                result += (
                    f"\n\n重要限制：本段生成结束后，下一段将是固定的插入内容："
                    f"{prefix}{item.get('text', '')}\n"
                    f"请让本段与这段固定内容自然衔接（结合前文为其铺垫），"
                    f"但绝不要在本段中提前写出或复述该内容本身。"
                )

        # 选项选择：点击选项后 AI 提示词带上所选选项的提示
        option_choice = getattr(session, "option_choice", None)
        if option_choice:
            choice_prompt = (option_choice.get("prompt") or "").strip() \
                            or (option_choice.get("text") or "").strip()
            if choice_prompt:
                result += (
                    f"\n\n当前故事走向（她在选项 {option_choice.get('index', 0)} 中做出的选择）："
                    f"{choice_prompt}\n请围绕这一走向继续叙述。"
                )
        return result

    def _get_height_match(self):
        session = self.context
        if not session.body_parts or "身高" not in session.body_parts:
            return ""
        comparisons = get_comparisons(
            session.merged_landmarks, {"身高": session.body_parts["身高"]},
            order="match", limit=1, selected_tags=[], skip_base_prob=0.0,
            blocked_words=self._blocked_words())
        if not comparisons:
            return ""
        comp = comparisons[0]
        landmark = comp["landmark"]
        suffix = "高" if landmark.dimension == "vertical" else ("长" if landmark.horizontal_type == "length" else "宽")
        if landmark.frequency == "unique":
            return f"她的身高 {format_size(session.height)}，约等于{landmark.name}{suffix}度的{comp['ratio']:.2f}倍。"
        return f"她的身高 {format_size(session.height)}，相当于{landmark.name}的{suffix}度。"

    def _detect_prompted_parts(self, text: str) -> list:
        """从上一段正文中识别"被注意到"的部位（覆盖 ALL_PART_NAMES）。

        按句子（。！？；换行）判断部位归属：句子含有"她/自己/巨人/名字"等
        标记，或没有任何"其他人"标记时，视为她的部位；否则（如
        "惊恐的人们留在原地，无法迈开双腿"）视为他人部位而忽略。允许少量误判。
        """
        session = self.context
        text = text or ""
        found = set()
        extra_re = None
        names = [n for n in (getattr(session, "name", "") or "",
                             getattr(session, "nick", "") or "") if (n or "").strip()]
        if names:
            extra_re = re.compile("|".join(re.escape(n.strip()) for n in names))
        for sent in re.split(r"[。！？!?；;\n]", text):
            sent = sent.strip()
            if not sent:
                continue
            parts = [p for p, pat in _PART_TRIGGER_RES.items() if pat.search(sent)]
            if not parts:
                continue
            is_her = bool(_HER_RE.search(sent)) or (extra_re is not None and extra_re.search(sent))
            is_other = bool(_OTHER_OWNER_RE.search(sent))
            if is_her or not is_other:
                found.update(parts)
        return [p for p in ALL_PART_NAMES if p in found]

    def _select_prompted_part(self, detected: list):
        """在识别出的多个部位中选取一个用于本次提示，返回 (部位名, 尺寸描述) 或 None。

        先按"摄影师请就位/细节控"标签偏好尝试跳过，再从未跳过中随机选择一个，
        避免多个部位采用同一解锁提示；全部被跳过时回退到全部候选。仅返回能
        生成尺寸描述的候选。
        """
        session = self.context
        body_parts = session.body_parts or {}
        available = [p for p in detected
                     if p not in session.keyword_match_given and p in body_parts]
        if not available:
            return None
        if len(available) > 1:
            personality = getattr(session, "personality", None)
            skip_base_prob = float(getattr(personality, "skip_base_prob", 0.0) or 0.0) \
                if personality else 0.0
            selected_tags = getattr(session, "tags", None) or []
            p = skip_base_prob / (sum(1 for _ in selected_tags) + 3)
            kept = [part for part in available if not should_skip_by_part_tags(part, selected_tags, p)]
            if kept:
                available = kept
        random.shuffle(available)
        for part in available:
            match = self._get_body_part_match(part)
            if match:
                return part, match
        return None

    def _get_body_part_match(self, part):
        session = self.context
        if not part or part not in session.body_parts:
            return ""
        comparisons = get_comparisons(
            session.merged_landmarks, {part: session.body_parts[part]},
            order="match", limit=1, selected_tags=[], skip_base_prob=0.0,
            blocked_words=self._blocked_words())
        if not comparisons:
            return ""
        comp = comparisons[0]
        landmark = comp["landmark"]
        suffix = "高" if landmark.dimension == "vertical" else ("长" if landmark.horizontal_type == "length" else "宽")
        size = format_size(comp["size"], base_size=session.height)
        if landmark.frequency == "unique":
            return f"她的{part} {size}，约等于{landmark.name}{suffix}度的{comp['ratio']:.2f}倍。"
        return f"她的{part} {size}，相当于{landmark.name}的{suffix}度。"

    def _blocked_words(self):
        return (getattr(self.context, "settings", None) or {}).get("blocked_words", [])

    def _get_nearest_quip_no_consume(self):
        session = self.context
        if not session.selected_quip_styles:
            return ""
        matrix = session.quips_working.get(session.size_cat, {})
        blocked_words = self._blocked_words()
        usable = {}
        for (i, d), quips in matrix.items():
            if (i, d) in session.locked_coords:
                continue
            kept = [q for q in quips if not contains_blocked_word(q.get("text", ""), blocked_words)]
            if kept:
                usable[(i, d)] = kept
        if not usable:
            return ""
        intrusion = session.dungeon_state.intrusion
        destruction = session.dungeon_state.destruction
        distance = min(math.hypot(i - intrusion, d - destruction) for i, d in usable)
        nearest = [(i, d) for i, d in usable if abs(math.hypot(i - intrusion, d - destruction) - distance) < 1e-9]
        selected = random.choice(nearest)
        quip_dict = random.choice(usable[selected])
        text = quip_dict["text"].replace("{name}", session.name).replace("{nick}", session.nick)
        text = replace_quip_tags(text, quip_dict["style"], session.size_cat, session.detail_pools)
        return text.replace("{name}", session.name).replace("{nick}", session.nick)


AIPromptBuilder = DungeonPromptBuilder
