"""选项触发器：后台生成选项文字并弹出选择弹窗。"""

import threading

import dearpygui.dearpygui as dpg

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
        """在主线程弹出选项弹窗（modal），点击任意选项后继续。"""
        if self._closing:
            return
        pending = self.pending_option
        if not pending:
            return
        if dpg.does_item_exist("option_dialog"):
            dpg.delete_item("option_dialog")

        labels = list(labels) or [o.get("prompt") or f"选项 {i}" for i, o in enumerate(pending["options"])]
        pending["labels"] = labels
        scale = max(getattr(self, "_dpi_scale", 1.0), 0.5)
        width = round(440 * scale)
        row_h = round(46 * scale)
        height = round(70 * scale) + row_h * len(labels)
        vw, vh = dpg.get_viewport_client_width(), dpg.get_viewport_client_height()
        pos = [round((vw - width) / 2), round((vh - height) / 2)]

        with dpg.window(tag="option_dialog", modal=True, no_title_bar=True,
                        no_move=True, no_resize=True, no_close=True,
                        width=width, height=height, pos=pos):
            dpg.add_text(coupling_prompts(normalize_coupling_level(
                getattr(self, "coupling_level", None)))["option_dialog_title"],
                wrap=width - 60)
            for i, label in enumerate(labels):
                dpg.add_button(label=label, width=width - 80,
                               callback=self._on_option_chosen, user_data=i)

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

        if dpg.does_item_exist("option_dialog"):
            dpg.delete_item("option_dialog")
        self.pending_option = None
        self._option_generating = False

        # 可再次触发的选项触发器在选择后仍可被重新检查触发
        self.check_triggers()
        # 选择后下一次点击必然生成：立即补射预生成（check_triggers 排队的
        # 插入段/结局会使其自动跳过，改由相应路径补射）
        self._maybe_pregen_next()
        process_log.log(f"[Option] {pending['name']} 选择了编号 {idx}，"
              f"选择记录: {self.trigger_choices.get(pending['name'])}")