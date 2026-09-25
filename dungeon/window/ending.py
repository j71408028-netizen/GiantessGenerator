"""敏感效果与结局生成。"""

import threading


class EndingHandler:
    # ------------------ 敏感效果汇总 ------------------
    def _apply_sensitivity_mods(self) -> dict:
        """汇总当前章节的持续敏感效果（属性名 -> 倍率改变量，离开章节即失效）。"""
        mods = {}
        for effect in list(self.chapter_sensitivity_effects):
            mods[effect["attr"]] = mods.get(effect["attr"], 0.0) + effect["amount"]
        return mods

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
            self._frame.call(self._update_ending_icon)
        self._ending_thread = threading.Thread(target=self._generate_ending, daemon=True)
        self._ending_thread.start()

    def _build_story_summary(self, limit=12, text_limit=80):
        """收集当前故事状态与最近的过往片段，供结局生成参考。"""
        parts = [f"介入度 {self.dungeon_state.intrusion:.2f} / 破坏性 {self.dungeon_state.destruction:.2f}"
                 f" / 总伤亡 {int(self.dungeon_state.total_casualties):,}"]
        if self.dungeon_state.custom_attrs:
            custom = "，".join(f"{k} {v:.2f}" for k, v in self.dungeon_state.custom_attrs.items())
            parts.append(f"自定义属性：{custom}")
        # 优先使用剧情压缩器产出的完整概要，缺失时回退到最近片段
        summarizer = getattr(self, "story_summary", None)
        summary = summarizer.summary_only() if summarizer is not None else ""
        if summary:
            parts.append("剧情概要：\n" + summary)
        else:
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

            current_item = {"type_str": "【结局】", "text": "", "highlight": True,
                            "speaker": None}
            self.story_history.append(current_item)

            if client is None:
                current_item["text"] = f"结局：{name}"
                self.ending_text = current_item["text"]
                self._schedule_text_update()
                print("[Ending] AI 客户端不可用，仅显示结局名称")
                return

            personality_desc = getattr(self.personality, "description", "") if self.personality else ""
            # 结局生成前把尚未压缩的剩余段落全部压缩，保证概要完整
            summarizer = getattr(self, "story_summary", None)
            if summarizer is not None:
                summarizer.flush_chapter(getattr(self, "current_chapter", "") or "",
                                         ai_client=client)
            # 结局提示同样按耦合等级取用：旅程收束 / 任务收尾 / 关系结局
            pack = coupling_prompts(normalize_coupling_level(
                getattr(self, "coupling_level", None)))
            system_prompt = pack["ending_system"].format(
                name=self.name, nick=self.nick,
                personality=personality_desc or '她有着独特的性格。')
            user_prompt = pack["ending_user"].format(
                ending_name=name, initial_prompt=self.initial_prompt,
                summary=self._build_story_summary())
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
            # 统一收尾（见 window/persistence.py::_finalize）：
            # 结算结局增量 → 记录重要结局索引 → 按设置自动保存回放
            self._finalize(completed=True, reason="触发结局")
