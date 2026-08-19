"""触发器判定、解锁坐标与插入段落。"""

import random

from dungeon.dispatcher import _dispatch
from dungeon.models import DungeonState, DungeonTextType
from dungeon.rules import TriggerRules


class TriggerHandler:
    # ---------- 触发器 ----------
    def _check_unlock_coord(self):
        intrusion = self.dungeon_state.intrusion
        destruction = self.dungeon_state.destruction
        if (4, 4) not in self.locked_coords:
            return
        if intrusion > 3.5 and destruction > 3.5:
            if "涩涩" in self.tags:
                self.locked_coords.remove((4, 4))
            else:
                prob = min(1.0, (self.greed / 100.0) * (self.breakthrough_attempts + 1))
                if random.random() < prob:
                    self.locked_coords.remove((4, 4))
                else:
                    self.breakthrough_attempts += 1

    def evaluate_condition(self, condition: dict, state: DungeonState) -> bool:
        return TriggerRules.evaluate(condition, state, self.trigger_choices)

    def check_triggers(self):
        if not self.triggers:
            return
        for trigger_index, trigger in enumerate(self.triggers):
            if trigger.get("name") in self.triggered_names:
                continue
            pre_names = trigger.get("precondition_names", [])
            if not all(name in self.fired_triggers for name in pre_names):
                continue
            cond = trigger.get("condition", {})
            if not self.evaluate_condition(cond, self.dungeon_state):
                continue
            action_type = trigger.get("action_type")
            action_data = trigger.get("action_data", {})
            # 兼容早期配置中的动作名称。
            if action_type == "sensitive":
                action_type = "sensitivity"
            if action_type is None:
                action_type = action_data.get("type")
                if action_type == "sensitive":
                    action_type = "sensitivity"
            action_accepted = True
            if action_type == "background":
                image_path = action_data.get("image_path")
                if not image_path:
                    print(f"[Trigger] 背景触发器 {trigger['name']} 缺少图片路径，已跳过")
                    action_accepted = False
                else:
                    smooth = action_data.get("smooth_transition", False)
                    filter_effect = action_data.get("filter_effect", None)
                    self.change_background(image_path, smooth, filter_effect)
                    print(f"[Trigger] 已触发: {trigger['name']}")
                    print(action_data)
                    self._record_trigger_action(
                        trigger, action_type, action_data,
                        image_path_resolved=self._background.resolve_path(image_path))
            elif action_type == "insert":
                text = str(action_data.get("text", "")).strip()
                if not text:
                    print(f"[Trigger] 插入触发器 {trigger['name']} 缺少插入文本，已跳过")
                    action_accepted = False
                else:
                    delayed = bool(action_data.get("delayed", False))
                    self.pending_insertions.append({
                        "text": text,
                        "text_type": action_data.get("text_type") or "background",
                        "highlight": bool(action_data.get("highlight", False)),
                        "delayed": delayed,
                    })
                    print(f"[Trigger] 已触发: {trigger['name']}，排队插入段落（延迟={delayed}）")
                    self._record_trigger_action(trigger, action_type, action_data)
            elif action_type == "option":
                options = action_data.get("options") or []
                if not options:
                    print(f"[Trigger] 选项触发器 {trigger['name']} 未配置选项，已跳过")
                    action_accepted = False
                elif self.pending_option is not None:
                    print(f"[Trigger] 选项触发器 {trigger['name']} 触发时已有选项弹窗，已跳过")
                    action_accepted = False
                else:
                    self.pending_option = {
                        "name": trigger["name"],
                        "prompt": action_data.get("prompt", ""),
                        "options": [
                            {"id": o.get("id", i), "prompt": o.get("prompt", ""), "text": o.get("text", "")}
                            for i, o in enumerate(options)
                        ],
                    }
                    print(f"[Trigger] 已触发: {trigger['name']}，弹出选项")
                    self._start_option_generation()
                    self._last_option_record = self._record_trigger_action(trigger, action_type, action_data)
            elif action_type == "sensitivity":
                attr = action_data.get("attr", "")
                try:
                    strength = float(action_data.get("strength", 1.0))
                    objective = float(action_data.get("objective", 0.0))
                    duration = int(action_data.get("duration", 3))
                except (TypeError, ValueError):
                    strength, objective, duration = 1.0, 0.0, 3
                sensitivity = getattr(self.personality, "sensitivity", 0.0) if self.personality else 0.0
                amount = strength * (sensitivity + objective)
                self.sensitivity_effects.append({
                    "attr": attr,
                    "amount": amount,
                    "remaining": max(1, duration),
                })
                print(f"[Trigger] 已触发: {trigger['name']}，敏感效果："
                      f"{attr} 倍率 {amount:+.2f}（强度={strength}，性格敏感值={sensitivity}，"
                      f"客观影响={objective}），持续 {duration} 步")
                self._record_trigger_action(trigger, action_type, action_data)
            elif action_type == "ending":
                name = str(action_data.get("name") or action_data.get("ending_text") or "").strip()
                if not name:
                    print(f"[Trigger] 结局触发器 {trigger['name']} 未填写结局名称，已跳过")
                    action_accepted = False
                elif self.dungeon_ended:
                    print(f"[Trigger] 结局触发器 {trigger['name']} 触发时故事已结束，已跳过")
                    action_accepted = False
                else:
                    self.pending_ending = {
                        "name": name, "action_data": action_data, "trigger_index": trigger_index}
                    print(f"[Trigger] 已触发: {trigger['name']}，结局：{name}")
                    self._start_ending_generation()
                    self._last_ending_record = self._record_trigger_action(trigger, action_type, action_data)
            elif action_type in ("none", None, ""):
                # 空触发器：无动作，仅用于标记条件成立，供其他触发器作为前置条件
                print(f"[Trigger] 空触发器 {trigger['name']} 条件成立（无动作）")
                self._record_trigger_action(trigger, action_type, action_data)
            else:
                print(f"未知的触发器动作类型：{action_type}")
                action_accepted = False

            if not action_accepted:
                continue
            # 可再次触发（默认）：不加入已触发集合，条件满足时可重复触发
            self.fired_triggers.add(trigger["name"])
            if not trigger.get("repeatable", True):
                self.triggered_names.add(trigger["name"])
            self.dungeon_state.steps_since_trigger = 0

    def _record_trigger_action(self, trigger, action_type, action_data, **extra) -> dict:
        """触发时把动作与时机写入回放记录，回放时不再判定条件，直接按记录复现。"""
        record = {
            "kind": "trigger",
            "name": trigger["name"],
            "action_type": action_type,
            "action_data": dict(action_data or {}),
            "step": self.dungeon_state.total_steps,
        }
        record.update(extra)
        self.replay_data.append(record)
        return record

    def _replay_trigger(self, record):
        """回放时复现触发器动作（不判定条件、不弹选项，选择作为一步直接展示）。"""
        action_type = record.get("action_type")
        action_data = record.get("action_data", {}) or {}
        name = record.get("name", "")
        if action_type == "background":
            image_path = record.get("image_path_resolved") or action_data.get("image_path")
            if image_path:
                self.change_background(
                    image_path,
                    bool(action_data.get("smooth_transition", False)),
                    action_data.get("filter_effect"),
                )
                print(f"[Replay] 复现背景触发器: {name}")
        elif action_type == "option":
            choice_index = record.get("choice_index")
            if choice_index is None:
                return  # 未完成选择，跳过
            idx = int(choice_index)
            options = action_data.get("options") or []
            chosen = options[idx] if 0 <= idx < len(options) else {}
            text = (record.get("choice_text")
                    or chosen.get("text") or chosen.get("prompt") or f"选项 {idx + 1}")
            self.trigger_choices.setdefault(name, []).append(idx)
            self.option_choice = {
                "name": name,
                "index": idx,
                "prompt": record.get("option_prompt") or chosen.get("prompt", ""),
                "text": chosen.get("text", ""),
            }
            if self.view_mode == "game":
                self.story_history.clear()
            self.story_history.append({"type_str": "【选择】", "text": text, "highlight": True})
            self._update_text_display()
            print(f"[Replay] 复现选项触发器: {name} → 选择 {idx}")
        elif action_type == "ending":
            ending_text = record.get("ending_text", "")
            # 回放时同样显示结局图标（图标路径来自结局动作配置）
            icon_path = (action_data or {}).get("icon_path", "") or ""
            if icon_path:
                self.ending_icon_path = icon_path
                _dispatch.enqueue(self._update_ending_icon)
            if ending_text:
                self.ending_text = ending_text
                if self.view_mode == "game":
                    self.story_history.clear()
                self.story_history.append({"type_str": "【结局】", "text": ending_text, "highlight": True})
                self._update_text_display()
            self.pending_ending = None
            self.dungeon_ended = True
            print(f"[Replay] 复现结局触发器: {name}")
        else:
            # insert / sensitivity / none：效果已随文本步骤与属性快照复现
            print(f"[Replay] 触发器动作无需额外复现: {name} [{action_type}]")

    # ------------------ 插入段落 ------------------
    def _peek_pending_insertion(self):
        if not self.pending_insertions:
            return None
        return self.pending_insertions[0]

    def _consume_pending_insertion(self):
        item = self.pending_insertions.popleft()
        self._display_inserted_paragraph(item)

    def _display_inserted_paragraph(self, item):
        try:
            text_type = DungeonTextType(item["text_type"])
        except (KeyError, ValueError):
            text_type = DungeonTextType.BACKGROUND
        highlight = bool(item.get("highlight"))

        if self.view_mode == "game":
            self.story_history.clear()
        self.story_history.append({
            "type_str": self._type_prefix(text_type),
            "text": item["text"],
            "highlight": highlight,
        })
        self._update_text_display()

        # 与一般段落一样进入对话历史，后续 AI 生成时可见
        self.messages.append({"role": "assistant", "content": item["text"]})
        if len(self.messages) > 21:
            self.messages = [self.messages[0]] + self.messages[-20:]

        self.dungeon_state.total_steps += 1
        self.dungeon_state.steps_since_trigger += 1
        self.step_num = self.dungeon_state.total_steps

        step_info = {
            "step": self.step_num,
            "type": text_type.value,
            "text": item["text"],
            "highlight": highlight,
            "intrusion_before": self.dungeon_state.intrusion,
            "destruction_before": self.dungeon_state.destruction,
            "custom_before": self.dungeon_state.custom_attrs.copy(),
            "direction": 0,
            "custom_directions": {},
        }

        self.dungeon_state = self.dungeon_logic.evolve_attributes(
            self.dungeon_state, text_type, 0, self.personality,
            is_interaction_chosen=False, custom_attrs_def=self.evolution_attrs,
            custom_directions={},
            sensitivity_mods=self._apply_sensitivity_mods()
        )

        self._finish_step(text_type, item["text"], step_info)