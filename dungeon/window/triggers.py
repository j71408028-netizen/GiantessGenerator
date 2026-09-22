"""触发器判定、章节进入、短暂视效与插入段落。

触发器 = 条件（所在章节 + 前置触发器 + 条件规则） + 一个动作。
章节自身没有条件：进入/离开都由触发器的 ``goto`` 动作执行，章节只描述
身处其中时的背景与敏感效果。
"""

import random

from dungeon.actions import (EMPTY_ACTIONS, VISUAL_FILTER_KEYS,
                             normalize_action_type)
from dungeon.chapters import (find_chapter, is_terminating_chapter,
                              matches_scope, sensitivity_amount)
from dungeon.window.dispatcher import _dispatch
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

    # ------------------ 章节 ------------------
    def _build_chapter_effect(self, effect) -> dict:
        """章节敏感效果：无持续步数，离开章节即失效。"""
        return {
            "attr": effect.get("attr", ""),
            "amount": sensitivity_amount(effect.get("strength", 1.0),
                                         effect.get("objective", 0.0),
                                         effect.get("attr", ""), self.personality,
                                         action_points=self._current_action_points()),
        }

    def _enter_chapter(self, name, record: bool = False, background=None,
                       allow_unknown: bool = False) -> bool:
        """进入章节（``""`` 表示离开章节）。

        ``record`` 为真时写入一条回放记录；``background`` 用于回放时直接
        复现当时的环境，避免依赖当前加载的章节配置。
        """
        name = str(name or "").strip()
        chapter = find_chapter(self.chapters, name) if name else None
        if name and chapter is None and not allow_unknown:
            print(f"[Chapter] 章节不存在：{name}")
            return False

        # 离开当前章节：压缩本章剩余段落（作为提示词概要的一部分）
        summarizer = getattr(self, "story_summary", None)
        if summarizer is not None and self.current_chapter != (name or None):
            summarizer.flush_chapter(self.current_chapter or "",
                                     ai_client=getattr(self, "ai_client", None))
            # 确定性关键事件：章节进出常驻提示词，保证 AI 对章节流转有感知
            if self.current_chapter:
                summarizer.record_key_event(f"离开章节「{self.current_chapter}」")
            if name:
                summarizer.record_key_event(f"进入章节「{name}」")

        self.current_chapter = name or None
        self.dungeon_state.chapter_steps = 0
        self.chapter_sensitivity_effects = [
            self._build_chapter_effect(effect)
            for effect in (chapter or {}).get("sensitivity", [])
        ]
        # 换章清除上一章遗留的短暂视效，并让背景滤镜回到本章节默认值
        self.visual_effects = []
        self._applied_visual_filter = None

        bg = background if background is not None else (chapter or {}).get("background")
        if record:
            self.replay_data.append({
                "kind": "chapter",
                "name": self.current_chapter or "",
                "background": dict(bg or {}),
                "step": self.dungeon_state.total_steps,
            })
        self._apply_chapter_background(bg)
        return True

    def _enter_start_chapter(self):
        """进入标记为起始的章节：章节不带条件，起始章节是唯一的自动进入点。"""
        if self.current_chapter or not self.chapters:
            return
        start = next((c for c in self.chapters if c.get("start") and c.get("name")), None)
        if start is None:
            return
        if self._enter_chapter(start["name"], record=True):
            print(f"[Chapter] 起始章节：{start['name']}")

    def _apply_chapter_background(self, background: dict = None):
        """应用章节的特定背景（未配置背景时保持当前背景不变）。"""
        if background is None:
            chapter = (find_chapter(self.chapters, self.current_chapter)
                       if self.current_chapter else None)
            background = (chapter or {}).get("background") or {}
        image_path = (background or {}).get("image_path")
        if not image_path:
            return
        self._set_background(image_path,
                             bool(background.get("smooth_transition", True)),
                             background.get("filter_effect"))

    def _set_background(self, image_path, smooth: bool = False, filter_effect=None):
        """切换背景并记录当前图路径，供短暂视效重刷滤镜使用。"""
        self.current_background_path = image_path or ""
        self.change_background(image_path, smooth, filter_effect)

    # ------------------ 短暂视效 ------------------
    def _active_visual_filter(self):
        """当前生效的视效滤镜（后触发的优先），无则返回 None。"""
        for effect in reversed(self.visual_effects):
            if effect.get("remaining", 0) > 0 and effect.get("filter"):
                return effect["filter"]
        return None

    def _refresh_visual_effect(self):
        """按当前生效的视效重刷背景滤镜；无视效则恢复章节默认滤镜。"""
        active = self._active_visual_filter()
        if active == self._applied_visual_filter:
            return
        self._applied_visual_filter = active
        if not self.current_background_path:
            if active:
                print(f"[Trigger] 短暂视效 {active} 没有可用的背景，已忽略")
            return
        effect_filter = active
        if effect_filter is None:
            chapter = (find_chapter(self.chapters, self.current_chapter)
                       if self.current_chapter else None)
            effect_filter = ((chapter or {}).get("background") or {}).get("filter_effect")
        self._set_background(self.current_background_path, False, effect_filter)

    def _apply_visual_effects(self):
        """每步收尾：消耗视效持续时间，到期后恢复章节滤镜。"""
        if not self.visual_effects:
            return
        for effect in self.visual_effects:
            effect["remaining"] = effect.get("remaining", 0) - 1
        self.visual_effects = [e for e in self.visual_effects if e["remaining"] > 0]
        self._refresh_visual_effect()

    # ------------------ 触发判定 ------------------
    def check_triggers(self):
        if not self.triggers:
            return
        for trigger_index, trigger in enumerate(self.triggers):
            if trigger.get("name") in self.triggered_names:
                continue
            action_data = trigger.get("action_data", {})
            action_type = normalize_action_type(trigger.get("action_type"), action_data)
            # 所在章节：触发器只在指定章节内参与判定
            if not matches_scope(trigger.get("chapter"), self.current_chapter):
                continue
            pre_names = trigger.get("precondition_names", [])
            if not all(name in self.fired_triggers for name in pre_names):
                continue
            cond = trigger.get("condition", {})
            if not self.evaluate_condition(cond, self.dungeon_state):
                continue
            action_accepted = True
            if action_type == "insert":
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
                # 结束章节：不允许弹出选项
                if is_terminating_chapter(
                        find_chapter(self.chapters, self.current_chapter)):
                    print(f"[Trigger] 选项触发器 {trigger['name']} 位于结束章节内，已跳过")
                    action_accepted = False
                    continue
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
            elif action_type == "effect":
                filter_key = str(action_data.get("filter") or "").strip()
                if filter_key not in VISUAL_FILTER_KEYS:
                    print(f"[Trigger] 视效触发器 {trigger['name']} 的视效无效：{filter_key}，已跳过")
                    action_accepted = False
                else:
                    try:
                        duration = max(1, int(float(action_data.get("duration", 1))))
                    except (TypeError, ValueError):
                        duration = 1
                    self.visual_effects.append({"filter": filter_key, "remaining": duration})
                    self._refresh_visual_effect()
                    print(f"[Trigger] 已触发: {trigger['name']}，短暂视效 {filter_key}（{duration} 步）")
                    self._record_trigger_action(trigger, action_type, action_data)
            elif action_type == "goto":
                # 结束章节：不允许通过触发器跳出
                if is_terminating_chapter(
                        find_chapter(self.chapters, self.current_chapter)):
                    print(f"[Trigger] 跳转触发器 {trigger['name']} 位于结束章节内，已跳过")
                    action_accepted = False
                    continue
                target = str(action_data.get("chapter") or "").strip()
                chapter = find_chapter(self.chapters, target) if target else None
                if target and chapter is None:
                    print(f"[Trigger] 跳转触发器 {trigger['name']} 的目标章节不存在：{target}，已跳过")
                    action_accepted = False
                elif self.current_chapter == (target or None):
                    print(f"[Trigger] 跳转触发器 {trigger['name']} 已处于章节「{target or '无章节'}」，已跳过")
                    action_accepted = False
                else:
                    self._enter_chapter(target)
                    print(f"[Trigger] 已触发: {trigger['name']}，"
                          f"跳转章节：{target or '离开章节'}")
                    self._record_trigger_action(
                        trigger, action_type, action_data,
                        chapter_background=dict((chapter or {}).get("background") or {}))
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
            elif action_type in EMPTY_ACTIONS:
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

    def _replay_chapter(self, record):
        """回放时直接复现章节进入（含当时的背景，不依赖当前章节配置）。"""
        name = record.get("name") or ""
        self._enter_chapter(name, record=False,
                            background=record.get("background"),
                            allow_unknown=True)
        print(f"[Replay] 复现章节进入: {name or '无章节'}")

    def _replay_trigger(self, record):
        """回放时复现触发器动作（不判定条件、不弹选项，选择作为一步直接展示）。"""
        action_type = record.get("action_type")
        action_data = record.get("action_data", {}) or {}
        name = record.get("name", "")
        if action_type == "goto":
            self._enter_chapter(str(action_data.get("chapter") or ""),
                                record=False,
                                background=record.get("chapter_background"),
                                allow_unknown=True)
            print(f"[Replay] 复现章节跳转: {name}")
        elif action_type == "effect":
            filter_key = str(action_data.get("filter") or "").strip()
            if filter_key:
                try:
                    duration = max(1, int(float(action_data.get("duration", 1))))
                except (TypeError, ValueError):
                    duration = 1
                self.visual_effects.append({"filter": filter_key, "remaining": duration})
                self._refresh_visual_effect()
            print(f"[Replay] 复现短暂视效: {name}")
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
        # 结束章节：段落类型被覆盖为「结局」，步进为 0
        in_ending = self._in_terminating_chapter()
        if in_ending:
            text_type = DungeonTextType.BACKGROUND
        highlight = bool(item.get("highlight"))

        # 插入段同样仿流式输出：先空文本上屏，再逐字增长
        inserted_text = item["text"]
        anim_item = {
            "type_str": self._display_type_prefix(text_type),
            "text": "",
            "highlight": highlight,
        }
        self.story_history.append(anim_item)
        self._update_text_display()
        self._animate_reveal(anim_item, inserted_text)

        # 与一般段落一样进入对话历史，后续 AI 生成时可见
        self.messages.append({"role": "assistant", "content": item["text"]})
        if len(self.messages) > 21:
            self.messages = [self.messages[0]] + self.messages[-20:]

        before_state = self.dungeon_state
        self.dungeon_state = self.dungeon_logic.evolve_attributes(
            before_state, text_type, 0, self.personality,
            custom_attrs_def=self.evolution_attrs,
            custom_directions={},
            sensitivity_mods=self._apply_sensitivity_mods(),
            action_points=self._current_action_points(),
            step_override=0.0 if in_ending else None,
        )
        self.step_num = self.dungeon_state.total_steps

        step_info = {
            "step": self.step_num,
            "type": text_type.value,
            "text": item["text"],
            "highlight": highlight,
            "intrusion_before": before_state.intrusion,
            "destruction_before": before_state.destruction,
            "custom_before": before_state.custom_attrs.copy(),
            "direction": 0,
            "custom_directions": {},
        }
        if in_ending:
            step_info["ending_chapter"] = True

        self._finish_step(text_type, item["text"], step_info)
