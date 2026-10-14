"""副本推进核心逻辑：AI 生成、固定文本推进、伤亡结算、回放步进与关闭处理。"""

import os
import threading
import time

from dungeon import process_log
from dungeon import script_text
from dungeon.audio.chapters import find_chapter, is_terminating_chapter, overflow_jump_target
from dungeon.details import build_detail_query_prompt, parse_detail_queries
from dungeon.models import DungeonTextType
from dungeon.response import canonical_history_entry, extract_stream_text, parse_final_json
from dungeon.splitter import split_full_text, split_stream_units, strip_speaker_markers
from core.ai import describe_ai_error
from core.logic import apply_size_unlock_updates, compute_casualty


class DungeonStoryEngine:
    # ---------- 核心逻辑 ----------
    def _on_next_step(self):
        # 覆盖层（对话记录等）打开时进入阅读模态：键盘推进也挂起
        # （鼠标点击由 _on_mouse_click 处理为「关闭覆盖层」）
        if self.overlay_open():
            return
        if self.is_replay:
            self._replay_next_step()
            return

        import time
        current_time = time.time()
        if getattr(self, "_last_click_time", None) is not None and (current_time - self._last_click_time) < 0.4:
            return
        if self._generating:
            return
        self._last_click_time = current_time

        if self.pending_option is not None:
            return  # 选项弹窗进行中，打断正常的生成过程

        if getattr(self, "pending_mini_game", None) is not None:
            return  # 小游戏窗口进行中，等结果回传后再继续

        if self.dungeon_ended or self.pending_ending is not None:
            return  # 结局已生成或正在生成，故事结束

        # 显示段落推进：上屏不足最小字数则吞掉、未补满则补满、已补满才切段。
        # 三种情况都由 _try_advance_paragraph 消化；本逻辑段落没有待揭示的
        # 显示段落（队列空且无游标）时它返回 False，落到下面的生成链——
        # 本次点击转为一个新的逻辑段落。
        if self._try_advance_paragraph():
            return

        pending = self._peek_pending_insertion()
        if pending and not pending["delayed"]:
            self._consume_pending_insertion()
        elif self._in_fixed_text_chapter():
            # 固定文本章节：一次点击推进一个逻辑段落，全程不碰 AI
            self._advance_script()
        else:
            self._generate_next_text()

    def _generate_next_text(self):
        if self._generating:
            return

        self._generating = True

        def task():
            delayed_insert_pending = False
            success = False
            try:
                if getattr(self, "ai_client", None) is None:
                    self._frame.call(self._show_ai_error)
                    return
                # 预演化收养：优先使用提前生成好的下一段（在途则等待完成）
                pregen = self._take_pregen_for_next()
                if pregen is not None:
                    next_type = pregen["text_type"]
                    user_prompt = pregen["user_prompt"]
                    self.messages = list(pregen["messages"])   # 已含本次 user 消息
                    full_response_buffer = pregen["response"]
                else:
                    next_type = self.dungeon_logic.get_next_text_type(self.current_text_type)
                    # 结束章节：所有段落类型被覆盖为「结局」，步进为 0
                    if self._in_terminating_chapter():
                        next_type = DungeonTextType.BACKGROUND
                    user_prompt = self.prompt_builder.build_user_prompt(next_type)
                    self.messages.append({"role": "user", "content": user_prompt})
                    full_response_buffer = ""
                # 本段是延迟插入的衔接段：生成成功后插入段晋升为下一段
                delayed_insert_pending = bool(self.pending_insertions
                                              and self.pending_insertions[0].get("delayed"))

                prefix = self._display_type_prefix(next_type)

                self._pending_units = []
                current_item = {"type_str": prefix, "text": "", "speaker": None}
                self.story_history.append(current_item)

                if pregen is None:
                    # 空响应重试一次：流式路径对空串无处兜底，且空响应
                    # 偶发（实测约 1/20），重试对用户不可见（首趟没上屏）
                    for _attempt in range(2):
                        stream_generator = self.ai_client.generate_stream(self.messages, temperature=0.8)

                        for chunk in stream_generator:
                            if self._closing:
                                return
                            full_response_buffer += chunk

                            partial_text = extract_stream_text(full_response_buffer)
                            if partial_text is not None:
                                # 内置分句器：首句进游标限速上屏，完整即定格；
                                # 后续完成句排队待点击
                                self._update_stream_units(current_item, partial_text)
                            else:
                                # 正文还没从响应里抽出（JSON 前导等）：整块当作
                                # 目标文本送进游标，不走直写（否则会绕过限速）
                                cleaned_buf = full_response_buffer.replace("```json", "").replace("```", "").strip()
                                if not cleaned_buf.startswith("{"):
                                    if self._reveal_active():
                                        self._reveal_extend(current_item, cleaned_buf)
                                    else:
                                        self._reveal_start(current_item, cleaned_buf)

                            self._schedule_text_update()
                        if full_response_buffer.strip():
                            break

                    if self._closing:
                        return

                ai_text, direction, custom_directions = self._parse_final_json(full_response_buffer)

                # 最终切分：整段正文按内置分句器定格，首句已由游标展示（或由此起
                # 始），其余排队。说话人标记（@X@）在切分时解析进显示单元，
                # 落盘与下游一律用净文本
                final_units = split_full_text(ai_text)
                self._pending_units = final_units[1:]
                first_text = strip_speaker_markers(ai_text)
                if final_units:
                    current_item["speaker"] = final_units[0].speaker
                    current_item["text"] = final_units[0].text
                    first_text = final_units[0].text
                else:
                    current_item["text"] = first_text
                self._schedule_text_update()
                # 首句进游标：预生成整段就绪 / 实时流式都走同一条限速上屏。
                # 真流式下游标通常已在跑（_update_stream_units 已 start），
                # 此处只做目标对齐（把游标目标钉到最终定稿的首句）。
                if first_text:
                    if self._reveal_active():
                        self._reveal_extend(current_item, first_text)
                    else:
                        self._reveal_start(current_item, first_text)
                # 首句定稿：带说话人（Solea/Bulla 对话分支）就该念出来
                self._speak_item(current_item)

                # 对话历史中的 assistant 回合一律存规范 JSON 形状（见
                # canonical_history_entry）：原始响应跑格式时若原样入史，
                # 模型会模仿纯文本导致格式漂移自我强化
                self.messages.append({"role": "assistant", "content": canonical_history_entry(
                    ai_text, direction, custom_directions)})
                window = self._message_window_size()
                if len(self.messages) > window + 1:
                    self.messages = [self.messages[0]] + self.messages[-window:]

                # 落盘与结算链统一用剥离说话人标记后的净文本
                clean_text = strip_speaker_markers(ai_text)
                before_state = self.dungeon_state
                step_override = 0.0 if self._in_terminating_chapter() else None
                self.dungeon_state = self.dungeon_logic.evolve_attributes(
                    before_state, next_type, direction, self.personality,
                    custom_attrs_def=self.evolution_attrs,
                    custom_directions=custom_directions,
                    sensitivity_mods=self._apply_sensitivity_mods(),
                    action_points=self._current_action_points(),
                    step_override=step_override,
                )
                self.step_num = self.dungeon_state.total_steps

                step_info = {
                    "step": self.step_num,
                    "type": next_type.value,
                    "text": clean_text,
                    "intrusion_before": before_state.intrusion,
                    "destruction_before": before_state.destruction,
                    "step_intrusion_before": before_state.step_intrusion,
                    "step_destruction_before": before_state.step_destruction,
                    "custom_before": before_state.custom_attrs.copy(),
                    "direction": direction,
                    "custom_directions": custom_directions
                }
                if step_override is not None:
                    step_info["ending_chapter"] = True

                self._apply_prompted_unlocks(clean_text)
                self._finish_step(next_type, clean_text, step_info, check_unlock=True)
                # 本逻辑段落定稿：游标目标已不再增长，直接把首句补满。
                # 否则真流式下若首句比队列长，游标会继续按 30 字/秒慢慢爬，
                # 玩家点「生成下一段」时被 _try_advance_paragraph 拦在 ②。
                self._reveal_snap(current_item)
                # 细节探究：立即在后台询问 AI 想了解的细节，下次生成前解答
                self._start_detail_query(clean_text)
                success = True

            except Exception as e:
                process_log.log(f"流式任务执行异常: {e}")
                # 异常不再静默：写入会话错误清单（收尾时进「未完成」报告）
                # 并在故事区提示一行，玩家可再次点击推进重试
                self._note_session_error(e)
            finally:
                self._generating = False
                # 延迟插入：本段为其衔接段，生成成功后插入段晋升为下一段
                if success and delayed_insert_pending:
                    if self.pending_insertions and self.pending_insertions[0].get("delayed"):
                        self.pending_insertions[0]["delayed"] = False
                # 预演化：下一次点击必然触发生成时，立即后台预生成下一段
                if success:
                    self._maybe_pregen_next()

        threading.Thread(target=task, daemon=True).start()

    # ---------- 固定文本章节（章节字段 fixed_text） ----------
    def _advance_script(self):
        """推进一个逻辑段落；文本已播完则按章节收口（超限跳转 / 终止）。"""
        script = self._ensure_script()
        if script is None or self._script_index >= len(script.paragraphs):
            self._finish_script_chapter()
            return
        paragraph = script.paragraphs[self._script_index]
        self._script_index += 1
        self._present_script_paragraph(paragraph)

    def _ensure_script(self):
        """按当前章节的 fixed_text 取脚本（按「章节 + 路径」缓存，换章即失效）。

        文件缺失 / 读不动时降级为空脚本（没有任何段落）——下一次点击直接走
        :meth:`_finish_script_chapter` 收口，不会把会话卡死；作者那边由校验器
        （``validate._validate_fixed_text``）在保存与启动前报 error 拦住。
        """
        config = self._current_fixed_text()
        rel = str(config.get("path") or "")
        if not rel:
            self._script = None
            self._script_key = None
            return None
        key = (self.current_chapter or "", rel)
        if self._script is not None and self._script_key == key:
            return self._script
        absolute = self._resolve_scenario_file(rel)
        if absolute is None:
            process_log.log(f"[Script] 固定文本文件不存在：{rel}")
            script = script_text.parse_script("")
        else:
            loaded, error = script_text.load_script_file(absolute)
            if loaded is None:
                process_log.log(f"[Script] 固定文本读取失败（{rel}）：{error}")
                script = script_text.parse_script("")
            else:
                script = loaded
        for warning in script.warnings:
            process_log.log(f"[Script] {rel} {warning}")
        self._script = script
        self._script_key = key
        self._script_index = 0
        self._script_exhausted = False
        process_log.log(f"[Script] 已载入固定文本「{rel}」："
                        f"{len(script.paragraphs)} 个逻辑段落")
        return script

    def _resolve_scenario_file(self, rel_path):
        """把方案内的相对路径解析成绝对路径；找不到返回 None。"""
        rel = str(rel_path or "").strip()
        if not rel:
            return None
        if os.path.isabs(rel):
            return rel if os.path.isfile(rel) else None
        candidates = []
        repo = getattr(self, "scenario_repo", None)
        scenario_id = getattr(self, "scenario_id", None)
        if repo is not None and scenario_id:
            try:
                # scenario_dir 同时看世界包根与自由根，比只拼 root 更全
                candidates.append(os.path.join(repo.scenario_dir(scenario_id), rel))
            except Exception:
                pass
        resolved = self._background.resolve_path(rel)
        if resolved:
            candidates.append(resolved)
        for path in candidates:
            if path and os.path.isfile(path):
                return path
        return None

    def _present_script_paragraph(self, paragraph):
        """上屏一个逻辑段落，并按它的指令演化属性。

        与 AI 段落同构：首句立即上屏、其余排队等点击揭示；段落本身（净文本）
        进对话历史、进剧情压缩、进回放，结算链走同一个 :meth:`_finish_step`。
        """
        directive = paragraph.directive
        in_ending = self._in_terminating_chapter()
        # 没有声明类型的段落按「环境」推进（步进值最小）；结束章节一律覆盖为结局
        text_type = directive.text_type or DungeonTextType.BACKGROUND
        display_type = DungeonTextType.BACKGROUND if in_ending else text_type

        # 没写段落类型的段落不显示类型前缀（纯叙述），有类型的照常显示
        prefix = (self._display_type_prefix(display_type)
                  if directive.text_type is not None else "")
        units = split_full_text(paragraph.text)
        self._pending_units = list(units[1:])
        first = units[0] if units else None
        item = {
            "type_str": prefix,
            "text": first.text if first is not None else paragraph.text,
            "speaker": first.speaker if first is not None else None,
        }
        self.story_history.append(item)
        self._update_text_display()
        if item["text"]:
            self._reveal_start(item, item["text"])
        self._speak_item(item)

        custom_directions = directive.custom_directions
        # 固定文本段与 AI 段落一样进对话历史，后续 AI 章节才接得上上下文；
        # 一律用规范 JSON 形状（见 response.canonical_history_entry）
        self.messages.append({"role": "assistant", "content": canonical_history_entry(
            paragraph.text, directive.direction,
            {**directive.builtin_directions, **custom_directions})})
        window = self._message_window_size()
        if len(self.messages) > window + 1:
            self.messages = [self.messages[0]] + self.messages[-window:]

        # 只有「类型 + 方向」都写全的段落参与演化；否则冻结（仅累计计数）。
        # 结束章节的段落一律冻结（步进为 0，与 AI 段落同一条规则）。
        step_override = 0.0 if (in_ending or not directive.complete) else None

        before_state = self.dungeon_state
        self.dungeon_state = self.dungeon_logic.evolve_attributes(
            before_state, text_type, directive.direction, self.personality,
            custom_attrs_def=self.evolution_attrs,
            custom_directions=custom_directions,
            builtin_directions=directive.builtin_directions,
            sensitivity_mods=self._apply_sensitivity_mods(),
            action_points=self._current_action_points(),
            step_override=step_override,
        )
        self.step_num = self.dungeon_state.total_steps

        step_info = {
            "step": self.step_num,
            "type": display_type.value,
            "text": paragraph.text,
            "intrusion_before": before_state.intrusion,
            "destruction_before": before_state.destruction,
            "step_intrusion_before": before_state.step_intrusion,
            "step_destruction_before": before_state.step_destruction,
            "custom_before": before_state.custom_attrs.copy(),
            "direction": directive.direction,
            "custom_directions": custom_directions,
            "step_override": step_override,
            "type_prefix": prefix,
        }
        if in_ending:
            step_info["ending_chapter"] = True
        self._finish_step(display_type, paragraph.text, step_info)

    def _finish_script_chapter(self):
        """固定文本播完：与「段落数超限」走同一个收口。

        编辑器中作者配 ``overflow_target`` 决定去向（空串 = 离开章节）；结束章节
        按章节结算字段生成结局并终止。只收口一次——重复点击不会再触发跳转。
        """
        if self._script_exhausted:
            return
        if self.dungeon_ended or self.pending_ending is not None:
            return
        self._script_exhausted = True
        chapter = find_chapter(getattr(self, "chapters", None),
                               getattr(self, "current_chapter", None))
        if chapter is None:
            return
        target = overflow_jump_target(chapter, self.chapters)
        if is_terminating_chapter(chapter):
            process_log.log(f"[Script] 结束章节「{chapter.get('name')}」文本播完，终止副本")
            self._terminate_from_ending_chapter(chapter)
        else:
            process_log.log(f"[Script] 章节「{chapter.get('name')}」文本播完，"
                            f"跳转到「{target or '离开章节'}」")
            self._enter_chapter(target, record=True)

    # ---------- 细节探究 ----------
    def _start_detail_query(self, paragraph_text):
        """流式输出完成后立即在后台询问 AI 想了解的细节。

        结果存入 ``_detail_queries``，由 ``build_user_prompt`` 在下一次
        生成前检索回放缓存解答并消费。与正常生成互不阻塞。
        """
        if self._in_fixed_text_chapter():
            return  # 固定文本章节没有 AI 生成，细节探究无处消费
        if getattr(self, "_detail_querying", False):
            return
        client = getattr(self, "ai_client", None)
        if client is None or self._closing:
            return
        self._detail_querying = True

        def run():
            try:
                replay = getattr(self, "replay_data", [])
                recent = [e.get("text") for e in replay[:-1]
                          if isinstance(e, dict) and e.get("text")]
                messages = build_detail_query_prompt(
                    paragraph_text, recent_texts=recent,
                    chapter_name=getattr(self, "current_chapter", "") or "",
                    coupling_level=getattr(self, "coupling_level", None))
                raw = client.generate(messages, temperature=0.5)
                queries = parse_detail_queries(raw)
                if not self._closing:
                    self._detail_queries = queries
                    if queries:
                        process_log.log(f"[DetailQuery] 想了解的细节: {queries}")
            except Exception as e:
                process_log.log(f"[DetailQuery] 细节提问失败: {e}")
            finally:
                self._detail_querying = False

        threading.Thread(target=run, daemon=True).start()

    # ---------- 预演化 ----------
    def _maybe_pregen_next(self):
        """下一次点击必然触发生成时，立即在后台预生成下一段（消除首字延迟）。

        预生成只在「世界状态已定、点击不可能转向」的时机发起：选项弹窗、
        插入段排队、结局待生成期间一律跳过（这些路径在各自收尾处补射）。
        生成的调用次数不变——只是把下一次的请求提前发出。
        """
        if getattr(self, "is_replay", False) or self._closing:
            return
        if self._in_fixed_text_chapter():
            return  # 固定文本章节的下一次点击只推进脚本，不必预生成
        if self.dungeon_ended or self.pending_ending is not None:
            return
        if self.pending_option is not None or self.pending_insertions:
            return
        if getattr(self, "pending_mini_game", None) is not None:
            return
        if getattr(self, "_pregen", None) is not None or getattr(self, "_pregen_inflight", False):
            return
        if getattr(self, "ai_client", None) is None:
            return
        self._start_pregen()

    def _start_pregen(self):
        """后台预生成下一段：缓存 (user_prompt, messages快照, 原始响应, 段落类型)。"""
        try:
            next_type = self.dungeon_logic.get_next_text_type(self.current_text_type)
            # 结束章节：所有段落类型被覆盖为「结局」，步进为 0
            if self._in_terminating_chapter():
                next_type = DungeonTextType.BACKGROUND
            # build_user_prompt 会把「被注意到的部位」标记为已提示；快照，
            # 预生成失败（走实时路径重建提示词）时还原，避免误标
            snapshot_given = set(self.keyword_match_given)
            user_prompt = self.prompt_builder.build_user_prompt(next_type)
        except Exception as e:
            process_log.log(f"[Pregen] 组装预生成提示词失败: {e}")
            return
        pre_messages = self.messages + [{"role": "user", "content": user_prompt}]
        self._pregen_inflight = True

        def run():
            response = ""
            try:
                for chunk in self.ai_client.generate_stream(pre_messages, temperature=0.8):
                    if self._closing:
                        return
                    response += chunk
            except Exception as e:
                process_log.log(f"[Pregen] 预生成失败，改回实时路径: {e}")
                self.keyword_match_given = snapshot_given
                return
            finally:
                self._pregen_inflight = False
            if not self._closing and response.strip():
                self._pregen = {
                    "user_prompt": user_prompt,
                    "messages": pre_messages,
                    "response": response,
                    "text_type": next_type,
                }
                process_log.log("[Pregen] 下一段已预生成")

        threading.Thread(target=run, daemon=True).start()

    def _take_pregen_for_next(self):
        """取用预生成结果；仍在途则等待完成（收养，不重复发请求）。

        预生成后、点击前世界状态不会变化（选项/插入/结局存在时不会预生成），
        因此无需校验有效性。失败或无预生成返回 None，走实时请求路径。
        """
        pregen = getattr(self, "_pregen", None)
        if pregen is None and getattr(self, "_pregen_inflight", False):
            while self._pregen_inflight and not self._closing:
                time.sleep(0.05)
            pregen = getattr(self, "_pregen", None)
        self._pregen = None
        return pregen

    def _update_stream_units(self, current_item, partial_text):
        """流式中用内置分句器更新显示：首句（未完句尾）流式展示，完整即定格。

        后续已完成的句不直接上屏，排队到 ``_pending_units``，等用户点击后
        由 ``_try_advance_paragraph`` 逐句揭示。同一文本整体重算，天然幂等。
        显示单元携带说话人（``@说话人@`` 标记解析，见 dungeon/splitter.py）。

        **上屏速度与 chunk 到达解耦**：这里只更新游标目标（``_reveal_extend``），
        实际出字由帧时钟按 30 字/秒推进，因此 AI 吐字再快也不会加速上屏。
        分句器读的是 ``partial_text``（已收全文），不受限速影响、始终领先。
        """
        units, tail = split_stream_units(partial_text)
        if units:
            current_item["speaker"] = units[0].speaker
            head = units[0].text
        else:
            current_item["speaker"] = None
            head = tail
        self._pending_units = list(units[1:])
        # 只延长目标，不直写 current_item["text"]——游标位置由限速循环掌握。
        # current_item["text"] 仍是落盘/回放用的净文本（见 _finish_step），
        # 游标收尾后会由 _reveal_snap 对齐到全文。
        if head:
            self._reveal_extend(current_item, head)

    def _apply_prompted_unlocks(self, text: str):
        """把 AI 针对被注意到部位返回的段落写入该部位解锁信息（写入规则与报告正文一致）。

        在 AI 段落返回后、触发器判定前调用，避免触发器（选项/插入/结局）打断正常流程时漏写。
        """
        parts = getattr(self, "prompted_parts", None) or set()
        if not parts:
            return
        char = self.character
        if char is None:
            return
        text = (text or "").strip()
        if not text:
            return
        try:
            info_update_rate = float((self.settings or {}).get("info_update_rate", 0.5))
        except (TypeError, ValueError):
            info_update_rate = 0.5
        char.size_unlocks = apply_size_unlock_updates(
            char.size_unlocks, {part: text for part in parts}, info_update_rate)

    # ---------- 结束章节 / 章节段落上限 ----------
    def _in_terminating_chapter(self) -> bool:
        """当前是否处于结束章节（段落类型被覆盖为结局、步进为 0）。"""
        chapter = find_chapter(getattr(self, "chapters", None),
                               getattr(self, "current_chapter", None))
        return is_terminating_chapter(chapter)

    def _record_story_summary(self, text_type, text):
        """把本段写入剧情压缩器，并按块触发压缩。"""
        summarizer = getattr(self, "story_summary", None)
        if summarizer is None:
            return
        try:
            type_value = text_type.value if hasattr(text_type, "value") else str(text_type)
        except Exception:
            type_value = str(text_type)
        summarizer.record(type_value, text,
                          chapter_name=getattr(self, "current_chapter", "") or "")
        summarizer.maybe_compress(self.dungeon_state.total_steps,
                                  chapter_name=getattr(self, "current_chapter", "") or "",
                                  ai_client=getattr(self, "ai_client", None))

    def _check_chapter_overflow(self):
        """章节段落数超限：普通章节跳转到 overflow_target，结束章节终止副本。"""
        if self.is_replay or self.dungeon_ended or self.pending_ending is not None:
            return
        chapter = find_chapter(getattr(self, "chapters", None),
                               getattr(self, "current_chapter", None))
        if not chapter:
            return
        try:
            limit = int(chapter.get("max_paragraphs", 99) or 99)
        except (TypeError, ValueError):
            limit = 99
        if self.dungeon_state.chapter_steps < max(1, limit):
            return
        target = overflow_jump_target(chapter, self.chapters)
        if is_terminating_chapter(chapter):
            process_log.log(f"[Chapter] 结束章节「{chapter.get('name')}」达到最大段落数（{limit}），终止副本")
            self._terminate_from_ending_chapter(chapter)
        else:
            process_log.log(f"[Chapter] 章节「{chapter.get('name')}」达到最大段落数（{limit}），"
                  f"跳转到「{target or '离开章节'}」")
            self._enter_chapter(target, record=True)

    def _terminate_from_ending_chapter(self, chapter: dict):
        """结束章节段落数耗尽：按章节结算配置生成结局并终止副本。

        结算字段（icon_path / 各增量 / 行动点返还）直接取章节自身配置，
        与旧「结局触发器」的 action_data 结构一致，复用结局生成管线。
        """
        name = str(chapter.get("name") or "结局").strip()
        action_data = {
            "name": name,
            "intrusion_delta": chapter.get("intrusion_delta", 0),
            "destruction_delta": chapter.get("destruction_delta", 0),
            "casualty_step": chapter.get("casualty_step", 0),
            "action_points_refund": chapter.get("action_points_refund", 0),
            "custom_deltas": dict(chapter.get("custom_deltas") or {}),
            "icon_path": chapter.get("icon_path", ""),
        }
        self.pending_ending = {"name": name, "action_data": action_data,
                               "trigger_index": -1, "chapter": chapter.get("name")}
        self._start_ending_generation()

    def _finish_step(self, text_type, text, step_info, check_unlock: bool = False):
        """步进收尾：结算章节效果覆盖与伤亡、写入回放，并检查解锁与触发器。"""
        self._apply_effect_overrides()
        casualty_increase = self._record_casualties(
            text_type, text, step_override=step_info.get("step_override"))

        step_info["intrusion_after"] = self.dungeon_state.intrusion
        step_info["destruction_after"] = self.dungeon_state.destruction
        step_info["step_intrusion_after"] = self.dungeon_state.step_intrusion
        step_info["step_destruction_after"] = self.dungeon_state.step_destruction
        step_info["custom_after"] = self.dungeon_state.custom_attrs.copy()
        step_info["casualty_increase"] = casualty_increase
        step_info["total_casualties_after"] = self.dungeon_state.total_casualties
        self.replay_data.append(step_info)

        if check_unlock:
            self._check_unlock_coord()
        self.current_text_type = text_type
        self.last_ai_text = text
        # 确定性关键事件：介入度/破坏性跨过整数阈值时登记里程碑
        summarizer = getattr(self, "story_summary", None)
        if summarizer is not None:
            before_i, after_i = step_info["intrusion_before"], step_info["intrusion_after"]
            before_d, after_d = step_info["destruction_before"], step_info["destruction_after"]
            if int(before_i) != int(after_i):
                if after_i > before_i:
                    summarizer.record_key_event(f"介入度突破{int(after_i)}，人们对她的态度升级")
                else:
                    summarizer.record_key_event(f"介入度回落到{int(after_i)}以下")
            if int(before_d) != int(after_d):
                if after_d > before_d:
                    summarizer.record_key_event(f"破坏性突破{int(after_d)}，周围环境遭到更大改变")
                else:
                    summarizer.record_key_event(f"破坏性回落到{int(after_d)}以下")
        # 剧情压缩：记录本段并按块（默认 20 段）压缩
        self._record_story_summary(text_type, text)
        # 步进收尾开启新一轮判定：本轮含它派生的结算重判（见 _begin_trigger_round）
        self._begin_trigger_round()
        self.check_triggers()
        # 触发器判定后再检查章节段落数上限，避免同一步内先跳转又被覆盖
        self._check_chapter_overflow()

    def _record_casualties(self, text_type: DungeonTextType, text: str,
                           step_override: float = None) -> float:
        """按探索模式的伤亡定义计算本段伤亡，追加到数组并累加总计。

        结束章节内步进为 0，本段不产生伤亡；固定文本里没写全指令的段落同样
        冻结（``step_override == 0``），只推进计数、不产生伤亡。
        """
        frozen = step_override is not None and float(step_override) == 0.0
        if self._in_terminating_chapter() or frozen:
            self.dungeon_state.casualty_evolution.append(0.0)
            return 0.0
        step = text_type.step_value
        if self.dungeon_logic is not None:
            step = self.dungeon_logic.step_overrides.get(text_type.value, step)
        height = max(1.0, self.height or 1.0)
        increase = compute_casualty(height, step, self.dungeon_state.destruction, text)
        self.dungeon_state.total_casualties += increase
        self.dungeon_state.casualty_evolution.append(increase)
        return increase

    def _replay_next_step(self):
        if self.dungeon_ended:
            process_log.log("回放结束")
            self._request_close()
            return
        if self.current_replay_index >= len(self.loaded_replay):
            process_log.log("回放结束")
            self._request_close()
            return
        entry = self.loaded_replay[self.current_replay_index]
        if entry.get("kind") == "trigger":
            self._replay_trigger(entry)
            self.current_replay_index += 1
            return
        if entry.get("kind") == "chapter":
            self._replay_chapter(entry)
            self.current_replay_index += 1
            return
        step = entry
        text_type = DungeonTextType(step["type"])
        text = step["text"]
        # 结束章节产生的段落在回放中同样以「结局」类型展示
        if step.get("ending_chapter"):
            text_type = DungeonTextType.BACKGROUND
        self._display_text(text, text_type, highlight=step.get("highlight", False),
                           prefix=step.get("type_prefix"))
        self.dungeon_state.intrusion = step["intrusion_after"]
        self.dungeon_state.destruction = step["destruction_after"]
        if "step_intrusion_after" in step:
            self.dungeon_state.step_intrusion = step["step_intrusion_after"]
        if "step_destruction_after" in step:
            self.dungeon_state.step_destruction = step["step_destruction_after"]
        if "custom_after" in step:
            self.dungeon_state.custom_attrs = step["custom_after"].copy()
        if "total_casualties_after" in step:
            self.dungeon_state.total_casualties = step["total_casualties_after"]
        self.dungeon_state.casualty_evolution.append(step.get("casualty_increase", 0.0))
        # 回放同样喂给剧情压缩器，保证概要/最近段落与实际推进一致
        self._record_story_summary(text_type, text)
        self.current_replay_index += 1

    # ---------- 生成错误 ----------
    def _note_session_error(self, error) -> None:
        """记录一次生成异常：会话仍可重试，但异常必须可见。

        此前这里只有一行 ``print``，玩家看到的是"点了没反应"。现在异常写入
        ``_session_errors``（副本收尾时汇总进「未完成」回放与报告），并在故事区
        插一行提示；副本不因此自动结束，玩家可再次点击推进重试。
        """
        message = f"{type(error).__name__}: {error}"
        errors = getattr(self, "_session_errors", None)
        if errors is None:
            self._session_errors = errors = []
        errors.append(message)
        # 玩家看的是可读原因（「连不上服务器」「API Key 无效」…），不是 SDK 原文；
        # 原文仍留在 ``_session_errors`` 里供收尾报告与日志排查。
        self._frame.call(lambda: self._show_ai_error(
            f"生成失败：{describe_ai_error(error)}"))

    def _show_ai_error(self, message: str = ""):
        """在故事区提示 AI 错误（未配置客户端 / 生成失败）。"""
        if self._closing:
            return
        self.story_history.append({
            "type_str": "【错误】",
            "text": message or "未配置 AI 客户端，请到“设置 → 副本AI设置”中完成配置后重试。",
            "speaker": None,
        })
        self._update_text_display()

    def _on_close(self):
        """收尾钩子：由帧循环在 DPG **已停止**后调用（base._run_frame_loop 的
        退出分支），这里只做清理标记。不再调用 ``stop_dearpygui()``——那时
        DPG 已经停了；业务侧主动关窗一律走 ``base._request_close()``（§5-C1），
        契约由 ``check_dungeon_window_contract.py`` 守住。"""
        self._closing = True
        # 尚未进入会话阶段（仍在入口选择页）直接关闭窗口时，
        # 不触发会话退出处理（未触发结局的数据丢失警告只对会话阶段有意义）
        if getattr(self, "_is_entry_phase", False):
            self._exit_from_entry = True
        # 挂起的重采样任务不必再跑：帧时钟会在 _finish_session 里连同其余任务一起丢掉
        self._background.cancel_pending_refresh()
        self._unregister_with_parent()

    # ---------- 辅助方法 ----------
    def _parse_final_json(self, response_text: str):
        custom_names = {
            attr["name"] for attr in self.evolution_attrs
            if attr["type"] == "custom"
        }
        return parse_final_json(response_text, custom_names)