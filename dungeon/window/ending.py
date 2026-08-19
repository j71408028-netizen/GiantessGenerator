"""敏感效果与结局生成。"""

import threading

from dungeon.dispatcher import _dispatch


class EndingHandler:
    # ------------------ 敏感触发器 ------------------
    def _apply_sensitivity_mods(self) -> dict:
        """汇总当前生效的敏感倍率修改（属性名 -> 倍率改变量）。"""
        mods = {}
        for effect in self.sensitivity_effects:
            mods[effect["attr"]] = mods.get(effect["attr"], 0.0) + effect["amount"]
        return mods

    def _decay_sensitivity_effects(self):
        """每步结束后消耗 1 步持续时间，到期效果移除。"""
        for effect in self.sensitivity_effects:
            effect["remaining"] -= 1
        self.sensitivity_effects = [e for e in self.sensitivity_effects if e["remaining"] > 0]

    # ------------------ 结局触发器 ------------------
    def _start_ending_generation(self):
        if self._ending_generating:
            return
        self._ending_generating = True
        pending = self.pending_ending or {}
        self._ending_trigger_index = pending.get("trigger_index", -1)
        self._ending_name = pending.get("name", "")
        # 结局图标：配置了图标才算重要结局，结局生成时一起显示
        self.ending_icon_path = (pending.get("action_data") or {}).get("icon_path", "") or ""
        if self.ending_icon_path:
            _dispatch.enqueue(self._update_ending_icon)
        self._ending_thread = threading.Thread(target=self._generate_ending, daemon=True)
        self._ending_thread.start()

    def _build_story_summary(self, limit=12, text_limit=80):
        """收集当前故事状态与最近的过往片段，供结局生成参考。"""
        parts = [f"介入度 {self.dungeon_state.intrusion:.2f} / 破坏性 {self.dungeon_state.destruction:.2f}"
                 f" / 总伤亡 {int(self.dungeon_state.total_casualties):,}"]
        if self.dungeon_state.custom_attrs:
            custom = "，".join(f"{k} {v:.2f}" for k, v in self.dungeon_state.custom_attrs.items())
            parts.append(f"自定义属性：{custom}")
        snippets = []
        for info in self.replay_data[-limit:]:
            if not info.get("text"):
                continue
            t = str(info["text"])
            if len(t) > text_limit:
                t = t[:text_limit] + "..."
            snippets.append(f"[{info.get('type', '?')}] {t}")
        if snippets:
            parts.append("过往故事片段：\n" + "\n".join(snippets))
        return "\n".join(parts)

    def _generate_ending(self):
        try:
            pending = self.pending_ending
            if not pending:
                return
            name = pending["name"]
            client = getattr(self, "ai_client", None)
            self.ending_effects = pending.get("action_data", {}) or {}

            if self.view_mode == "game":
                self.story_history.clear()
            current_item = {"type_str": "【结局】", "text": "", "highlight": True}
            self.story_history.append(current_item)

            if client is None:
                current_item["text"] = f"结局：{name}"
                self.ending_text = current_item["text"]
                self._schedule_text_update()
                print("[Ending] AI 客户端不可用，仅显示结局名称")
                return

            personality_desc = getattr(self.personality, "description", "") if self.personality else ""
            system_prompt = (
                f"你是一位细腻的叙事作家，正在为一段关于巨大化少女（名字{self.name}"
                f"、昵称{self.nick}）的故事写出结局。\n"
                f"性格描述：{personality_desc or '她有着独特的性格。'}\n"
                "请根据给定的结局名称与过往故事，总结这段旅程并写出完整、自然的结局详情：\n"
                "- 自然衔接前文，交代故事的最终走向与局面\n"
                "- 以叙述文书写，可分多段，篇幅 100~300 字\n"
                "- 直接输出结局文字本身，不要输出 JSON、不要添加任何解释"
            )
            user_prompt = (
                f"结局名称：{name}\n"
                f"故事初始基调与设定：{self.initial_prompt}\n"
                "故事当前状态：\n"
                f"{self._build_story_summary()}\n\n"
                "请写出结局。"
            )
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            buffer = ""
            for chunk in client.generate_stream(messages, temperature=0.9):
                if self._closing:
                    return
                buffer += chunk
                current_item["text"] = buffer.strip()
                self._schedule_text_update()
            current_item["text"] = buffer.strip()
            self.ending_text = current_item["text"]
            self._schedule_text_update()
            print(f"[Ending] 结局已生成：{name}")
        except Exception as e:
            print(f"结局生成异常: {e}")
        finally:
            self.pending_ending = None
            self.dungeon_ended = True
            self._ending_generating = False
            if 'name' in locals() and not self.ending_text:
                self.ending_text = f"结局：{name}"
            # 结局文本写回回放记录，供回放时复现结局
            if self._last_ending_record is not None:
                self._last_ending_record["ending_text"] = self.ending_text
                self._last_ending_record = None
            # 最后结算一次增量并写入角色
            self._apply_ending_effects()
            # 记录本次达成的重要结局索引（探索模式写角色档案，挑战模式写 data/user）
            self._record_ending_achievement()
            # 自动保存回放开关
            if self._auto_replay_enabled():
                self._save_replay_record(auto=True)