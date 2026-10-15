"""选项触发器：后台生成选项文字并弹出选择弹窗。

选择弹窗的**绘制**已迁到 ``ui._draw_option_dialog``（立即模式，每帧从
``pending_option["labels"]`` 现画）；本模块只负责生成文字并把它们塞进
``pending_option``，以及处理"选了第几个"。
"""

import threading

from dungeon import process_log
from dungeon.coupling import coupling_prompts, normalize_coupling_level


class OptionHandler:
    # ------------------ 选项触发器 ------------------
    def _start_option_generation(self):
        if self._option_generating:
            return
        self._option_generating = True
        threading.Thread(target=self._generate_option_texts, daemon=True).start()

    def _generate_option_texts(self):
        """后台为每个选项生成简短选项文字（失败时回退到配置提示）。"""
        labels = []
        try:
            pending = self.pending_option
            if not pending:
                return
            client = getattr(self, "ai_client", None)
            # 选项文字的生成提示随耦合等级变化：阅读走向 / 行动选项 / 互动选项
            pack = coupling_prompts(normalize_coupling_level(
                getattr(self, "coupling_level", None)))
            for opt in pending["options"]:
                label = (opt.get("text") or "").strip()
                if not label and client is not None:
                    messages = [
                        {"role": "system", "content": pack["option_system"]},
                        {"role": "user", "content": pack["option_user"].format(
                            situation=pending.get('prompt', ''),
                            option_prompt=opt.get('prompt', ''))},
                    ]
                    try:
                        label = client.generate(messages, temperature=0.9).strip()
                    except Exception as e:
                        process_log.log(f"选项文字生成失败: {e}")
                if not label:
                    label = opt.get("prompt") or f"选项 {opt.get('id', 0)}"
                if len(label) > 28:
                    label = label[:28] + "…"
                labels.append(label)
        except Exception as e:
            process_log.log(f"选项生成任务异常: {e}")
        finally:
            self._option_generating = False
            if not self._closing:
                self._frame.call(self._open_option_dialog, labels)

    def _open_option_dialog(self, labels):
        """把生成的选项文字挂到 ``pending_option``，由 ``ui`` 每帧绘制弹窗。

        原实现这里建 DPG 模态窗（``option_dialog``）；imgui 立即模式下弹窗是
        ``ui._draw_option_dialog`` 的绘制分支，因此这里只做状态赋值。
        """
        if self._closing:
            return
        pending = self.pending_option
        if not pending:
            return
        labels = list(labels) or [o.get("prompt") or f"选项 {i}"
                                  for i, o in enumerate(pending["options"])]
        pending["labels"] = labels

    def _on_option_chosen(self, sender, app_data, user_data):
        if self._closing or not self.pending_option:
            return
        idx = int(user_data)
        pending = self.pending_option
        options = pending["options"]
        chosen = options[idx] if 0 <= idx < len(options) else {}

        # 选择编号加入该触发器对应的数组
        self.trigger_choices.setdefault(pending["name"], []).append(idx)
        # 点击选项后，AI 提示词将带上该选项的提示
        self.option_choice = {
            "name": pending["name"],
            "index": idx,
            "prompt": chosen.get("prompt", ""),
            "text": chosen.get("text", ""),
        }
        # 确定性关键事件：选项选择常驻提示词
        summarizer = getattr(self, "story_summary", None)
        if summarizer is not None:
            choice_desc = (chosen.get("prompt") or chosen.get("text") or "").strip()
            if choice_desc:
                summarizer.record_key_event(f"她选择了：{choice_desc}")

        # 把选择写入回放记录：回放时不再弹窗，选择作为一步直接展示
        if self._last_option_record is not None:
            labels = pending.get("labels") or []
            label = (labels[idx] if 0 <= idx < len(labels)
                     else chosen.get("text") or chosen.get("prompt") or f"选项 {idx + 1}")
            self._last_option_record["choice_index"] = idx
            self._last_option_record["choice_text"] = label
            self._last_option_record["option_prompt"] = chosen.get("prompt", "")
            self._last_option_record["step"] = self.dungeon_state.total_steps
            self._last_option_record = None

        self.pending_option = None
        self._option_generating = False

        # 结算后立即重判（同一轮，见 triggers._begin_trigger_round）：同一触发器
        # 受本轮已触发集合保护不会立刻重发，其他触发器（含另一个选项）可接力
        self.check_triggers()
        # 选择后下一次点击必然生成：立即补射预生成（check_triggers 排队的
        # 插入段/结局会使其自动跳过，改由相应路径补射）
        self._maybe_pregen_next()
        process_log.log(f"[Option] {pending['name']} 选择了编号 {idx}，"
              f"选择记录: {self.trigger_choices.get(pending['name'])}")