import random
import re

from core.logic import ALL_PART_NAMES, format_size, get_comparisons, \
    should_skip_by_part_tags, contains_blocked_word
from core.scale_reference import (destruction_calibration_line,
                                  destruction_level_title,
                                  intrusion_calibration_line,
                                  intrusion_level_title)
from dungeon.audio.chapters import find_chapter, is_terminating_chapter
from .coupling import (COMMON_OUTPUT_RULE, DEFAULT_COUPLING_LEVEL,
                       HIGHLIGHT_MARKER_RULE, coupling_prompts,
                       normalize_coupling_level, section_instruction)
from .details import search_replay_details
from .models import DungeonTextType
from .scene import place_label


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
    """副本提示词构建器。context 只需提供窗口当前使用的同名数据。

    提示词按副本的耦合等级（Velum / Solea / Bulla）取用 dungeon.coupling 里
    的硬编码文本：AI 的角色定位、收集职责、段落描写指令、插入衔接与选项走向
    都随等级变化，输出结构（text / direction / custom_directions）三档一致。
    """

    def __init__(self, context):
        self.context = context

    def coupling_level(self) -> str:
        """当前会话的耦合等级（缺省回退到 Velum）。"""
        return normalize_coupling_level(
            getattr(self.context, "coupling_level", None), DEFAULT_COUPLING_LEVEL)

    def protagonist_title(self) -> str:
        """主角称呼（方案配置 protagonist_title，留空回退「主角」）。"""
        title = str(getattr(self.context, "protagonist_title", "") or "").strip()
        return title or "主角"

    def build_system_prompt(self) -> str:
        session = self.context
        pack = coupling_prompts(self.coupling_level())
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
            f"{pack['role'].format(name=session.name, nick=session.nick)}\n"
            f"性格描述：{session.personality.description or '她有着独特的性格。'}\n{height_info}"
            f"{pack['brief']}\n"
            f"{COMMON_OUTPUT_RULE}\n"
            "direction 取值范围：-1（消极/挫败），0（中性），1（积极/满足）。\n"
            'direction 表示这一段对她是顺利（+1）还是受挫（-1），'
            "不表示事情更激烈还是更收敛；内容的深浅以“当前坐标”一行为准。\n"
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
        level = self.coupling_level()
        # 段落描写指令按耦合等级取用（见 dungeon.coupling.COUPLING_PROMPTS）
        type_value = text_type.value if hasattr(text_type, "value") else str(text_type)
        instruction = section_instruction(level, type_value, "生成一段合适的叙述。")
        terminating = is_terminating_chapter(find_chapter(
            getattr(session, "chapters", None),
            getattr(session, "current_chapter", None)))
        # 结束章节：所有段落类型都被覆盖为「结局」，提示词也要相应改为终章语气
        if terminating:
            instruction = coupling_prompts(level)["terminating"]
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
        # 独特地标的另一条触发途径：她与被点名的地标"同时在场"时给出对比
        # （判定是内部的，见 _copresent_unique_line；措辞不出现"同时在场"）
        presence = self._copresent_unique_line()
        if presence:
            context_parts.append(presence)
        if getattr(session, "option_choice", None):
            choice = session.option_choice
            choice_prompt = choice.get("prompt", "")
            if choice_prompt:
                context_parts.append(f"她刚才选择了：{choice_prompt}")
        reference = ""
        if context_parts:
            reference = (f"\n{coupling_prompts(level)['reference_intro']}\n"
                         + "\n".join(context_parts))
        # 剧情概要：从开始到此刻的所有压缩段 + 最近 N 段原文（N 可在设置中调整）
        history = ""
        summarizer = getattr(session, "story_summary", None)
        if summarizer is not None:
            block = summarizer.prompt_block()
            if block:
                history = f"{block}\n"
        if not history:
            history = "".join(
                f"[{step['type']}] {step['text'][:100]}...\n"
                for step in session.replay_data[-3:]
                if "type" in step and step.get("text")
            )
            if history:
                history = f"最近的故事片段：\n{history}\n"
        # 当前章节：让 AI 感知自己身处哪一章（备注是设计者的章节意图，一并列出）
        chapter_line = ""
        chapter_name = getattr(session, "current_chapter", None)
        if chapter_name:
            chapter = find_chapter(getattr(session, "chapters", None), chapter_name)
            note = str((chapter or {}).get("note") or "").strip()
            chapter_line = f"当前章节：{chapter_name}" + (f"（{note}）" if note else "") + "\n"
            # 当前地点：写实副本要的是"她在一座真城市里"，模型知道地名才写得相称；
            # 虚构副本不写地址，这一行就不出现，没有可漏的东西
            place = place_label(chapter)
            if place:
                chapter_line += f"当前地点：{place}\n"
        # 细节补充：AI 在上一段生成后提出的疑问，从回放缓存检索原文解答
        detail_block = ""
        pending_queries = getattr(session, "_detail_queries", None)
        if pending_queries:
            hits = search_replay_details(pending_queries,
                                         getattr(session, "replay_data", None))
            if hits:
                detail_block = ("细节补充（AI 此前想了解的细节，检索自过往正文，"
                                "仅供叙事参考）：\n" + "\n".join(f"- {h}" for h in hits) + "\n")
            session._detail_queries = []
        pack = coupling_prompts(level)
        result = (f"{history}{chapter_line}{self._calibration_block()}{detail_block}"
                  f"{pack['narrate'].format(instruction=instruction)}\n"
                  f"请严格按系统提示的 JSON 格式输出，不要添加额外解释。{reference}")
        # Solea/Bulla 的对话分支：要求 AI 用 @说话人@ 行内标记区分说话人与台词，
        # 分句器解析后供 UI 组件渲染名牌（见 dungeon/splitter.py）
        speaker_rule = pack.get("speaker_rule")
        if (speaker_rule and not terminating
                and type_value in ("dialog", "branch")):
            result += "\n\n" + speaker_rule.format(
                name=session.name, nick=session.nick,
                protagonist=self.protagonist_title())
        # 高亮标记（==...==）：分句器把它折算成显示段落的高亮，标记本身不上屏。
        # 结局段落整体就是高亮色，不必再要求模型标注。
        if not terminating:
            result += "\n\n" + HIGHLIGHT_MARKER_RULE

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
                result += "\n\n" + pack["insert_notice"].format(
                    prefix=prefix, text=item.get('text', ''))

        # 选项选择：点击选项后 AI 提示词带上所选选项的提示
        option_choice = getattr(session, "option_choice", None)
        if option_choice:
            choice_prompt = (option_choice.get("prompt") or "").strip() \
                            or (option_choice.get("text") or "").strip()
            if choice_prompt:
                result += "\n\n" + pack["option_notice"].format(
                    index=option_choice.get('index', 0), prompt=choice_prompt)
        return result

    def _calibration_block(self) -> str:
        """本段写作校准块：坐标数值行 + 两级谓词 + 尺度上限，无坐标时为空串。

        进 user prompt 而不是 system：system 只在开局构建一次，坐标会停在
        开局值。内容的深浅只由坐标决定，与 direction 的符号无关。
        尺度行复用本章换算句（场景同源、随体型变化、视角中立），没有换算句
        时只写身高，绝不引入其他场景词。
        """
        state = getattr(self.context, "dungeon_state", None)
        if state is None:
            return ""
        intrusion = float(state.intrusion)
        destruction = float(state.destruction)
        height_match = self._get_height_match()
        scale = (height_match.rstrip("。") if height_match
                 else f"她的身高 {format_size(getattr(self.context, 'height', 0.0))}")
        lines = [
            f"当前坐标：介入度 {intrusion:.1f}"
            f"（{round(intrusion)} 级：{intrusion_level_title(intrusion)}）"
            f"/ 破坏性 {destruction:.1f}"
            f"（{round(destruction)} 级：{destruction_level_title(destruction)}）",
            intrusion_calibration_line(intrusion),
            destruction_calibration_line(destruction),
            f"尺度：{scale}；本段只发生一件与此级别相称的事，而非毁灭或颠覆整个场景。",
        ]
        return "\n".join(lines) + "\n"

    def _landmark_pool(self) -> list:
        """本章的尺寸换算候选池（章节地址过滤后的全局池）。

        会话提供 ``chapter_landmarks()`` 时以它为准——空池也是答案（作者把
        范围限死了，不该回落到全量）；脱离窗口的调用方只有 ``merged_landmarks``，
        按改动前的全量处理。
        """
        getter = getattr(self.context, "chapter_landmarks", None)
        if callable(getter):
            return getter()
        return getattr(self.context, "merged_landmarks", None) or []

    def _copresent_unique_line(self) -> str:
        """她与独特地标"同时在场"时的身高对比行；不满足判据返回空串。

        判据是内部原则，不是提示词措辞：最近一或两个显示段落内，她的名字
        （或"她"）与独特地标的名字同段出现，或分处相邻两段。独特地标只经
        章节 ``landmarks`` 白名单进池（见 ``dungeon.scene``），被点名即视为
        本章可能在场；正文提到才触发对比。比例超出 ``get_comparisons`` 的
        可比范围、或上一轮刚提示过同一地标时不重复。

        身体部位提及（``_detect_prompted_parts``）是独特地标对比的另一条
        触发途径，与此并行。
        """
        session = self.context
        blocked = self._blocked_words()
        uniques = [lm for lm in self._landmark_pool()
                   if str(getattr(lm, "frequency", "") or "") == "unique"
                   and not contains_blocked_word(lm.name, blocked)]
        if not uniques:
            return ""
        paragraphs = [str(item.get("text") or "")
                      for item in (getattr(session, "story_history", None) or [])
                      if str(item.get("text") or "").strip()][-2:]
        if not paragraphs:
            return ""
        names = [n.strip() for n in (getattr(session, "name", "") or "",
                                     getattr(session, "nick", "") or "")
                 if (n or "").strip()]
        her_re = re.compile("|".join([re.escape(n) for n in names] + [r"她(?!们)"]))
        present = None
        for landmark in uniques:
            lm_re = re.compile(re.escape(landmark.name))
            hers = [bool(her_re.search(text)) for text in paragraphs]
            theirs = [bool(lm_re.search(text)) for text in paragraphs]
            same = any(h and t for h, t in zip(hers, theirs))
            adjacent = (len(paragraphs) == 2
                        and ((hers[0] and theirs[1]) or (theirs[0] and hers[1])))
            if same or adjacent:
                present = landmark
                break
        if present is None or present.name == getattr(
                session, "prompted_presence_landmark", ""):
            return ""
        if not session.body_parts or "身高" not in session.body_parts:
            return ""
        comparisons = get_comparisons(
            [present], {"身高": session.body_parts["身高"]},
            order="match", limit=1, selected_tags=[], skip_base_prob=0.0,
            blocked_words=blocked)
        if not comparisons:
            return ""
        comp = comparisons[0]
        landmark = comp["landmark"]
        suffix = ("高" if landmark.dimension == "vertical"
                  else ("长" if landmark.horizontal_type == "length" else "宽"))
        session.prompted_presence_landmark = landmark.name
        return (f"她的身高 {format_size(session.height)}，"
                f"约等于{landmark.name}{suffix}度的{comp['ratio']:.2f}倍。")

    def _get_height_match(self):
        session = self.context
        if not session.body_parts or "身高" not in session.body_parts:
            return ""
        comparisons = get_comparisons(
            self._landmark_pool(), {"身高": session.body_parts["身高"]},
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
            self._landmark_pool(), {part: session.body_parts[part]},
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


AIPromptBuilder = DungeonPromptBuilder
