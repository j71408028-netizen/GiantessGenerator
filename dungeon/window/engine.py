"""副本推进核心逻辑：AI 生成、伤亡结算、回放步进与关闭处理。"""

import threading
import time

import dearpygui.dearpygui as dpg

from dungeon.chapters import find_chapter, is_terminating_chapter, overflow_jump_target
from dungeon.details import build_detail_query_prompt, parse_detail_queries
from dungeon.window.dispatcher import _dispatch
from dungeon.models import DungeonTextType
from dungeon.response import extract_stream_text, parse_final_json
from dungeon.splitter import split_full_text, split_stream_units
from logic import apply_size_unlock_updates, compute_casualty


class DungeonStoryEngine:
    # ---------- 核心逻辑 ----------
    def _on_next_step(self):
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

        # 仿流式输出进行中：本次点击立即补完，不推进内容
        if getattr(self, "_text_anim_state", None):
            self._finish_text_animation()
            return

        if self.pending_option is not None:
            return  # 选项弹窗进行中，打断正常的生成过程

        if self.dungeon_ended or self.pending_ending is not None:
            return  # 结局已生成或正在生成，故事结束

        # 逻辑段落（一次 AI 输出）切出的显示段落未揭示完：先逐句展示，不触发生成
        if getattr(self, "_pending_units", None):
            self._reveal_pending_unit()
            return

        pending = self._peek_pending_insertion()
        if pending and not pending["delayed"]:
            self._consume_pending_insertion()
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
                    _dispatch.enqueue(self._show_ai_error)
                    return
                next_type = self.dungeon_logic.get_next_text_type(self.current_text_type)
                # 结束章节：所有段落类型被覆盖为「结局」，步进为 0
                if self._in_terminating_chapter():
                    next_type = DungeonTextType.BACKGROUND
                user_prompt = self.prompt_builder.build_user_prompt(next_type)
                # 本段是延迟插入的衔接段：生成成功后插入段晋升为下一段
                delayed_insert_pending = bool(self.pending_insertions
                                              and self.pending_insertions[0].get("delayed"))
                self.messages.append({"role": "user", "content": user_prompt})

                prefix = self._display_type_prefix(next_type)

                self._pending_units = []
                current_item = {"type_str": prefix, "text": ""}
                self.story_history.append(current_item)

                full_response_buffer = ""

                stream_generator = self.ai_client.generate_stream(self.messages, temperature=0.8)

                for chunk in stream_generator:
                    if self._closing:
                        return
                    full_response_buffer += chunk

                    partial_text = extract_stream_text(full_response_buffer)
                    if partial_text is not None:
                        # 内置分句器：首句流式显示，完整即定格；后续完成句排队待点击
                        self._update_stream_units(current_item, partial_text)
                    else:
                        cleaned_buf = full_response_buffer.replace("```json", "").replace("```", "").strip()
                        if not cleaned_buf.startswith("{"):
                            current_item["text"] = cleaned_buf

                    self._schedule_text_update()

                if self._closing:
                    return

                ai_text, direction, custom_directions = self._parse_final_json(full_response_buffer)

                # 最终切分：整段正文按内置分句器定格，首句已显示，其余排队
                final_units = split_full_text(ai_text)
                self._pending_units = final_units[1:]
                current_item["text"] = final_units[0] if final_units else ai_text
                self._schedule_text_update()

                self.messages.append({"role": "assistant", "content": full_response_buffer})
                if len(self.messages) > 21:
                    self.messages = [self.messages[0]] + self.messages[-20:]

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
                    "text": ai_text,
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

                self._apply_prompted_unlocks(ai_text)
                self._finish_step(next_type, ai_text, step_info, check_unlock=True)
                # 细节探究：立即在后台询问 AI 想了解的细节，下次生成前解答
                self._start_detail_query(ai_text)
                success = True

            except Exception as e:
                print(f"流式任务执行异常: {e}")
                # 异常不再静默：写入会话错误清单（收尾时进「未完成」报告）
                # 并在故事区提示一行，玩家可再次点击推进重试
                self._note_session_error(e)
            finally:
                self._generating = False
                # 延迟插入：本段为其衔接段，生成成功后插入段晋升为下一段
                if success and delayed_insert_pending:
                    if self.pending_insertions and self.pending_insertions[0].get("delayed"):
                        self.pending_insertions[0]["delayed"] = False

        threading.Thread(target=task, daemon=True).start()

    # ---------- 细节探究 ----------
    def _start_detail_query(self, paragraph_text):
        """流式输出完成后立即在后台询问 AI 想了解的细节。

        结果存入 ``_detail_queries``，由 ``build_user_prompt`` 在下一次
        生成前检索回放缓存解答并消费。与正常生成互不阻塞。
        """
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
                        print(f"[DetailQuery] 想了解的细节: {queries}")
            except Exception as e:
                print(f"[DetailQuery] 细节提问失败: {e}")
            finally:
                self._detail_querying = False

        threading.Thread(target=run, daemon=True).start()

    def _update_stream_units(self, current_item, partial_text):
        """流式中用内置分句器更新显示：首句（未完句尾）流式展示，完整即定格。

        后续已完成的句不直接上屏，排队到 ``_pending_units``，等用户点击后
        由 ``_reveal_pending_unit`` 逐句揭示。同一文本整体重算，天然幂等。
        """
        units, tail = split_stream_units(partial_text)
        current_item["text"] = units[0] if units else tail
        self._pending_units = list(units[1:])

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
            print(f"[Chapter] 结束章节「{chapter.get('name')}」达到最大段落数（{limit}），终止副本")
            self._terminate_from_ending_chapter(chapter)
        else:
            print(f"[Chapter] 章节「{chapter.get('name')}」达到最大段落数（{limit}），"
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
        """步进收尾：结算短暂视效与伤亡、写入回放，并检查解锁与触发器。"""
        self._apply_visual_effects()
        casualty_increase = self._record_casualties(text_type, text)

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
        self.check_triggers()
        # 触发器判定后再检查章节段落数上限，避免同一步内先跳转又被覆盖
        self._check_chapter_overflow()

    def _record_casualties(self, text_type: DungeonTextType, text: str) -> float:
        """按探索模式的伤亡定义计算本段伤亡，追加到数组并累加总计。

        结束章节内步进为 0，本段不产生伤亡。
        """
        if self._in_terminating_chapter():
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
            print("回放结束")
            self._request_close()
            return
        if self.current_replay_index >= len(self.loaded_replay):
            print("回放结束")
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
        self._display_text(text, text_type, highlight=step.get("highlight", False))
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
        _dispatch.enqueue(lambda: self._show_ai_error(f"生成失败：{message}"))

    def _show_ai_error(self, message: str = ""):
        """在故事区提示 AI 错误（未配置客户端 / 生成失败）。"""
        if self._closing:
            return
        self.story_history.append({
            "type_str": "【错误】",
            "text": message or "未配置 AI 客户端，请到“设置 → 副本AI设置”中完成配置后重试。",
        })
        self._update_text_display()

    def _on_close(self):
        self._closing = True
        # 尚未进入会话阶段（仍在入口选择页）直接关闭窗口时，
        # 不触发会话退出处理（未触发结局的数据丢失警告只对会话阶段有意义）
        if getattr(self, "_is_entry_phase", False):
            self._exit_from_entry = True
        if self._bg_resize_timer is not None:
            self._bg_resize_timer.cancel()
        self._unregister_with_parent()
        _dispatch.stop()
        dpg.stop_dearpygui()

    # ---------- 辅助方法 ----------
    def _parse_final_json(self, response_text: str):
        custom_names = {
            attr["name"] for attr in self.evolution_attrs
            if attr["type"] == "custom"
        }
        return parse_final_json(response_text, custom_names)