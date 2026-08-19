"""副本推进核心逻辑：AI 生成、伤亡结算、回放步进与关闭处理。"""

import threading
import time

import dearpygui.dearpygui as dpg

from dungeon.dispatcher import _dispatch
from dungeon.models import DungeonTextType
from dungeon.response import extract_stream_text, parse_final_json
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

        if self.pending_option is not None:
            return  # 选项弹窗进行中，打断正常的生成过程

        if self.dungeon_ended or self.pending_ending is not None:
            return  # 结局已生成或正在生成，故事结束

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
            try:
                if getattr(self, "ai_client", None) is None:
                    _dispatch.enqueue(self._show_ai_error)
                    return
                next_type = self.dungeon_logic.get_next_text_type(self.current_text_type)
                user_prompt = self.prompt_builder.build_user_prompt(next_type)
                self.messages.append({"role": "user", "content": user_prompt})

                prefix = self._type_prefix(next_type)

                if self.view_mode == "game":
                    self.story_history.clear()
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
                        current_item["text"] = partial_text
                    else:
                        cleaned_buf = full_response_buffer.replace("```json", "").replace("```", "").strip()
                        if not cleaned_buf.startswith("{"):
                            current_item["text"] = cleaned_buf

                    self._schedule_text_update()

                if self._closing:
                    return

                ai_text, direction, custom_directions = self._parse_final_json(full_response_buffer)

                current_item["text"] = ai_text
                self._schedule_text_update()

                self.messages.append({"role": "assistant", "content": full_response_buffer})
                if len(self.messages) > 21:
                    self.messages = [self.messages[0]] + self.messages[-20:]

                self.dungeon_state.total_steps += 1
                self.dungeon_state.steps_since_trigger += 1
                self.step_num = self.dungeon_state.total_steps

                step_info = {
                    "step": self.step_num,
                    "type": next_type.value,
                    "text": ai_text,
                    "intrusion_before": self.dungeon_state.intrusion,
                    "destruction_before": self.dungeon_state.destruction,
                    "custom_before": self.dungeon_state.custom_attrs.copy(),
                    "direction": direction,
                    "custom_directions": custom_directions
                }

                self.dungeon_state = self.dungeon_logic.evolve_attributes(
                    self.dungeon_state, next_type, direction, self.personality,
                    is_interaction_chosen=False, custom_attrs_def=self.evolution_attrs,
                    custom_directions=custom_directions,
                    sensitivity_mods=self._apply_sensitivity_mods()
                )

                self._apply_prompted_unlocks(ai_text)
                self._finish_step(next_type, ai_text, step_info, check_unlock=True)

            except Exception as e:
                print(f"流式任务执行异常: {e}")
            finally:
                self._generating = False
                # 延迟插入：AI 段已生成并衔接完毕，插入段晋升为下一段
                if self.pending_insertions and self.pending_insertions[0].get("delayed"):
                    self.pending_insertions[0]["delayed"] = False

        threading.Thread(target=task, daemon=True).start()

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

    def _finish_step(self, text_type, text, step_info, check_unlock: bool = False):
        """步进收尾：还原步数计数、结算敏感衰减与伤亡、写入回放，并检查解锁与触发器。"""
        total_steps = self.dungeon_state.total_steps
        steps_since = self.dungeon_state.steps_since_trigger
        self.dungeon_state.total_steps = total_steps
        self.dungeon_state.steps_since_trigger = steps_since
        self._decay_sensitivity_effects()
        casualty_increase = self._record_casualties(text_type, text)

        step_info["intrusion_after"] = self.dungeon_state.intrusion
        step_info["destruction_after"] = self.dungeon_state.destruction
        step_info["custom_after"] = self.dungeon_state.custom_attrs.copy()
        step_info["casualty_increase"] = casualty_increase
        step_info["total_casualties_after"] = self.dungeon_state.total_casualties
        self.replay_data.append(step_info)

        if check_unlock:
            self._check_unlock_coord()
        self.current_text_type = text_type
        self.last_ai_text = text
        self.check_triggers()

    def _record_casualties(self, text_type: DungeonTextType, text: str) -> float:
        """按探索模式的伤亡定义计算本段伤亡，追加到数组并累加总计。"""
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
            dpg.stop_dearpygui()
            return
        if self.current_replay_index >= len(self.loaded_replay):
            print("回放结束")
            dpg.stop_dearpygui()
            return
        entry = self.loaded_replay[self.current_replay_index]
        if entry.get("kind") == "trigger":
            self._replay_trigger(entry)
            self.current_replay_index += 1
            return
        step = entry
        text_type = DungeonTextType(step["type"])
        text = step["text"]
        self._display_text(text, text_type, highlight=step.get("highlight", False))
        self.dungeon_state.intrusion = step["intrusion_after"]
        self.dungeon_state.destruction = step["destruction_after"]
        if "custom_after" in step:
            self.dungeon_state.custom_attrs = step["custom_after"].copy()
        if "total_casualties_after" in step:
            self.dungeon_state.total_casualties = step["total_casualties_after"]
        self.dungeon_state.casualty_evolution.append(step.get("casualty_increase", 0.0))
        self.current_replay_index += 1

    def _show_ai_error(self):
        """AI 客户端未配置时在故事区提示错误。"""
        if self._closing:
            return
        if self.view_mode == "game":
            self.story_history.clear()
        self.story_history.append({
            "type_str": "【错误】",
            "text": "未配置 AI 客户端，请到“设置 → 副本AI设置”中完成配置后重试。",
        })
        self._update_text_display()

    def _on_close(self):
        self._closing = True
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