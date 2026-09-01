import random
from tkinter import filedialog

import ui.common.dialogs

import customtkinter as ctk

from context import ExplorationContext
from models import CharacterSnapshot
from persistence.character_repo import CharacterRepo
from services.state_service import StateService
from logic import format_size
from ui.common.theme import (
    BASE, BORDER, TEXT, SOFT, TEXT_MUTED, TITLE,
    PLACEHOLDER, TEXT_DISABLED,
    BORDER_ALT, HOVER_ALT, MENU_HOVER,
    STATUS_ERR, ERR_STRONG, ERR_HOVER,
    STAT_BLUE, STAT_BLUE_LIGHT, STAT_BLUE_DEEP,
)
from ui.common import fonts as ui_fonts


class GiantessStatePanel(ctk.CTkFrame):
    def __init__(self, parent, character_repo: CharacterRepo, context: ExplorationContext, gui_ref, **kwargs):
        super().__init__(parent, fg_color=BASE,
                         border_width=1, border_color=BORDER,
                         corner_radius=14, **kwargs)
        self.character_repo = character_repo
        self.context = context
        self.gui_ref = gui_ref
        self.state_data = {}
        self._last_state = None
        self._recovery_after_id = None
        self._running = False

        # ---- 顶部操作栏 ----
        top_frame = ctk.CTkFrame(self, fg_color="transparent")
        top_frame.pack(fill='x', padx=(14,16), pady=(12, 4))

        self.back_btn = ctk.CTkButton(
            top_frame, text="←", width=27, height=24,
            command=self._back_to_params,
            fg_color="transparent",
            text_color=TEXT_MUTED,
            hover_color=HOVER_ALT,
            border_width=1, border_color=BORDER_ALT,
            corner_radius=8
        )
        self.back_btn.pack(side='left')

        ctk.CTkLabel(top_frame, text="角色档案", font=ui_fonts.ui_font(13, "bold"),
                     text_color=TITLE).pack(side='left', padx=(5, 0))

        # 标题分割线
        sep = ctk.CTkFrame(
            self,
            height=3,
            corner_radius=0,
            fg_color=MENU_HOVER
        )
        sep.pack(
            padx=(14, 16),
            pady=(2, 4),
            fill='x'
        )

        self.delete_btn = ctk.CTkButton(
            top_frame, text="🗑", width=27, height=24,
            command=self._delete_character,
            fg_color="transparent",
            text_color=ERR_STRONG,
            hover_color=ERR_HOVER,
            border_width=1, border_color=ERR_STRONG,
            corner_radius=8
        )
        self.delete_btn.pack(side='right', padx=(2, 0))

        self.export_chara_btn = ctk.CTkButton(
            top_frame, text="📤", width=27, height=24,
            command=self._export_chara,
            fg_color="transparent",
            text_color=TEXT_MUTED,
            hover_color=HOVER_ALT,
            border_width=1, border_color=BORDER_ALT,
            corner_radius=8
        )
        self.export_chara_btn.pack(side='right', padx=(0, 2))

        # ---- 内容区（档案在左，状态指标在右） ----
        content_frame = ctk.CTkFrame(self, fg_color="transparent")
        content_frame.pack(fill='both', expand=True, padx=(14,16), pady=(8, 12))
        content_frame.grid_columnconfigure(0, weight=1)
        content_frame.grid_columnconfigure(1, weight=0, minsize=320)
        content_frame.grid_rowconfigure(0, weight=1)

        profile_frame = ctk.CTkFrame(content_frame, fg_color="transparent")
        profile_frame.grid(row=0, column=0, sticky='nsew', padx=(0, 8))

        self.name_label = ctk.CTkLabel(
            profile_frame, text="",
            font=ui_fonts.ui_font(18, "bold"),
            text_color=TEXT
        )
        self.name_label.pack(anchor='w', pady=(0, 2))

        self.nick_label = ctk.CTkLabel(
            profile_frame, text="",
            font=ui_fonts.ui_font(12),
            text_color=PLACEHOLDER
        )
        self.nick_label.pack(anchor='w', pady=(0, 2))

        height_frame = ctk.CTkFrame(profile_frame, fg_color="transparent")
        height_frame.pack(anchor='w', pady=(0, 2))
        self.height_label = ctk.CTkLabel(
            height_frame, text="",
            font=("Consolas", 12),
            text_color=TEXT_MUTED
        )
        self.height_label.pack(side='left')
        self.random_size_btn = ctk.CTkButton(
            height_frame, text="...", width=24, height=20,
            font=("Consolas", 11), command=self._show_random_size,
            fg_color="transparent", text_color=SOFT,
            hover_color=HOVER_ALT,
            border_width=1, border_color=BORDER_ALT, corner_radius=5
        )
        self.random_size_btn.pack(side='left', padx=(6, 0))

        self.random_size_label = ctk.CTkLabel(
            profile_frame, text="", font=("Consolas", 11),
            text_color=TITLE, justify='left', wraplength=250
        )
        self.random_size_info_label = ctk.CTkLabel(
            profile_frame, text="", font=ui_fonts.ui_font(10),
            text_color=PLACEHOLDER, justify='left', wraplength=250
        )

        # ---- 右侧状态指标（固定宽度列） ----
        self.progress_frame = ctk.CTkFrame(content_frame, fg_color="transparent", width=320)
        self.progress_frame.grid(row=0, column=1, sticky='new')

        self.intrusion_block, self.intrusion_label, self.intrusion_progress, \
            self.intrusion_question, _ = self._build_stat_block(
            "介入度", STAT_BLUE)
        self.destruction_block, self.destruction_label, self.destruction_progress, \
            self.destruction_question, _ = self._build_stat_block(
            "破坏性", STAT_BLUE)
        self.action_block, self.action_label, self.action_progress, _, \
            self.action_pct_label = self._build_stat_block(
            "行动点数", STAT_BLUE, with_pct=True)

        # 伤亡统计
        self.casualty_frame = ctk.CTkFrame(self.progress_frame, fg_color="transparent")
        self.casualty_frame.pack(fill='x', pady=3)
        self.casualty_label = ctk.CTkLabel(
            self.casualty_frame, text="☠ 伤亡  0",
            font=ui_fonts.ui_font(12),
            text_color=ERR_STRONG
        )
        self.casualty_label.pack(side='left')

        self._size_data = {}
        self._size_unlocks = {}
        self._height = 0.0

    @staticmethod
    def _gradient_color(ratio):
        if ratio <= 0:
            return TEXT_DISABLED
        ratio = min(1.0, ratio)
        if ratio <= 0.5:
            t = ratio / 0.5
            r = 76 + int((255 - 76) * t)
            g = 175 + int((193 - 175) * t)
            b = 80 + int((7 - 80) * t)
        else:
            t = (ratio - 0.5) / 0.5
            r = 255 + int((244 - 255) * t)
            g = 193 + int((67 - 193) * t)
            b = 7 + int((54 - 7) * t)
        light = f"#{r:02x}{g:02x}{b:02x}"
        dr = max(0, r - 30)
        dg = max(0, g - 30)
        db = max(0, b - 30)
        dark = f"#{dr:02x}{dg:02x}{db:02x}"
        return (light, dark)

    @staticmethod
    def _progress_track_color(colors):
        """从填充色生成同色系、低对比度的进度条轨道色。"""
        def blend(color, target, amount):
            rgb = tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))
            return "#" + "".join(
                f"{int(channel + (target - channel) * amount):02x}" for channel in rgb
            )

        return blend(colors[0], 255, 0.72), blend(colors[1], 25, 0.55)

    def _build_stat_block(self, title, colors, with_pct=False):
        """构造右侧指标块：标题在左，进度条填充剩余宽度。"""
        block = ctk.CTkFrame(self.progress_frame, fg_color="transparent")
        block.pack(fill='x', pady=3)
        title_label = ctk.CTkLabel(
            block, text=title, font=ui_fonts.ui_font(11), text_color=colors
        )
        title_label.pack(side='left')
        bar_line = ctk.CTkFrame(block, fg_color="transparent")
        bar_line.pack(side='left', fill='x', expand=True, padx=(10, 0))
        bar = ctk.CTkProgressBar(
            bar_line, width=10, height=6, corner_radius=3, border_width=0,
            progress_color=colors, fg_color=self._progress_track_color(colors)
        )
        bar.pack(side='left', fill='x', expand=True)
        if with_pct:
            pct_label = ctk.CTkLabel(
                bar_line, text="0%", font=ui_fonts.ui_font(10),
                text_color=STAT_BLUE_LIGHT
            )
            pct_label.pack(side='right', padx=(6, 0))
        else:
            pct_label = None
        question = ctk.CTkLabel(
            bar_line, text="?",
            font=ui_fonts.ui_font(14, "bold"),
            text_color=TEXT_DISABLED
        )
        return block, title_label, bar, question, pct_label

    def _show_casualties(self):
        if self.gui_ref and hasattr(self.gui_ref, 'settings'):
            return self.gui_ref.settings.get("show_casualties", True)
        return True

    def update_state(self, state_data):
        if isinstance(state_data, CharacterSnapshot):
            data = {
                "name": state_data.name,
                "nick": state_data.nick,
                "height": state_data.height,
                "intrusion": state_data.intrusion,
                "destruction": state_data.destruction,
                "action_points": state_data.action_points,
                "total_casualties": state_data.total_casualties,
                "body_parts": state_data.body_parts,
                "size_unlocks": state_data.size_unlocks,
            }
        else:
            data = state_data

        name = data.get("name", "")
        nick = data.get("nick", "")
        height = data.get("height", 1.6)
        intrusion = data.get("intrusion") or data.get("curr_intrusion", 0.0)
        destruction = data.get("destruction") or data.get("curr_destruction", 0.0)
        action_points = data.get("action_points", 0)

        # 更新基本信息
        self.name_label.configure(text=name)
        self.nick_label.configure(text=f"({nick})" if nick else "")
        self.height_label.configure(text=f"身高  {format_size(height)}")
        self._height = height
        self._size_data = data.get("body_parts", {}) or {}
        self._size_unlocks = data.get("size_unlocks", {}) or {}
        self.random_size_label.pack_forget()
        self.random_size_info_label.pack_forget()

        # ---- 介入度（上限 4.5） ----
        intrusion_val = min(4.5, max(0, intrusion))
        intrusion_ratio = intrusion_val / 4.5

        if intrusion_val > 0:
            self.intrusion_progress.pack(fill='both', expand=True)
            self.intrusion_question.pack_forget()
            self.intrusion_progress.set(intrusion_ratio)
            c1, c2 = self._gradient_color(intrusion_ratio)
            colors = (c1, c2)
            self.intrusion_progress.configure(
                progress_color=colors, fg_color=self._progress_track_color(colors)
            )
        else:
            self.intrusion_progress.pack_forget()
            self.intrusion_question.pack(fill='both', expand=True)
            colors = self._gradient_color(intrusion_ratio)
            self.intrusion_progress.configure(
                progress_color=colors, fg_color=self._progress_track_color(colors)
            )
        self.intrusion_label.configure(text_color=self._gradient_color(intrusion_ratio))

        # ---- 破坏性（上限 4.5） ----
        destruction_val = min(4.5, max(0, destruction))
        destruction_ratio = destruction_val / 4.5

        if destruction_val > 0:
            self.destruction_progress.pack(fill='both', expand=True)
            self.destruction_question.pack_forget()
            self.destruction_progress.set(destruction_ratio)
            c1, c2 = self._gradient_color(destruction_ratio)
            colors = (c1, c2)
            self.destruction_progress.configure(
                progress_color=colors, fg_color=self._progress_track_color(colors)
            )
        else:
            self.destruction_progress.pack_forget()
            self.destruction_question.pack(fill='both', expand=True)
            colors = self._gradient_color(destruction_ratio)
            self.destruction_progress.configure(
                progress_color=colors, fg_color=self._progress_track_color(colors)
            )
        self.destruction_label.configure(text_color=self._gradient_color(destruction_ratio))

        # ---- 行动点数（0–100%） ----
        action_val = min(100, max(0, action_points))
        self.action_pct_label.configure(text=f"{action_val:.0f}%")
        self.action_progress.set(action_val / 100.0)

        if action_val < 50:
            colors = STAT_BLUE_DEEP
        else:
            colors = STAT_BLUE
        self.action_progress.configure(
            progress_color=colors, fg_color=self._progress_track_color(colors)
        )

        # ---- 伤亡统计 ----
        if self._show_casualties():
            self.casualty_frame.pack(fill='x', pady=3)
            casualties = data.get("total_casualties", 0.0)
            if casualties > 999999999:
                self.casualty_label.configure(text="☠ 伤亡  999,999,999+")
            else:
                self.casualty_label.configure(text=f"☠ 伤亡  {int(casualties):,}")
        else:
            self.casualty_frame.pack_forget()

        self._last_state = data

    def _show_random_size(self):
        available_parts = [
            (part, size) for part, size in self._size_data.items()
            if part != "身高" and self._size_unlocks.get(part, "") != ""
        ]
        if not available_parts:
            self.random_size_label.configure(text="暂无已解锁尺寸")
            self.random_size_label.pack(anchor='w')
            self.random_size_info_label.pack_forget()
            return

        part, size = random.choice(available_parts)
        self.random_size_label.configure(text=f"{part}  {format_size(size, base_size=self._height)}")
        self.random_size_label.pack(anchor='w')

        unlock_info = self._size_unlocks.get(part, "")
        if unlock_info != "MEASURED":
            self.random_size_info_label.configure(text=unlock_info)
            self.random_size_info_label.pack(anchor='w', pady=(1, 0))
        else:
            self.random_size_info_label.pack_forget()

    def update_theme(self, mode=None):
        """状态控件使用 CustomTkinter 双主题颜色，由框架自动更新。"""
        return

    def _back_to_params(self):
        if self.gui_ref and hasattr(self.gui_ref, 'generator_panel'):
            if not ui.common.dialogs.askyesno("确认卸载", "确定要卸载当前角色吗？\n未保存的修改将丢失。"):
                return
            self.stop_auto_recovery()
            self.gui_ref.generator_panel.switch_to_params_panel()

    def _export_chara(self):
        if not self.gui_ref:
            ui.common.dialogs.showerror("错误", "未绑定主界面实例")
            return
        state = self.gui_ref.generator_panel.current_state
        if state is None:
            ui.common.dialogs.showerror("错误", "未绑定角色数据")
            return
        file_path = filedialog.asksaveasfilename(
            title="导出角色卡或档案",
            initialfile=state.name or "角色",
            defaultextension=".html",
            filetypes=[
                ("角色档案", "*.html"),
                ("角色卡", "*.json"),
                ("所有文件", "*.*")
            ]
        )
        if not file_path:
            return
        lower = file_path.lower()
        if lower.endswith((".mhtml", ".mht", ".htm")):
            file_path = file_path.rsplit(".", 1)[0] + ".html"
            lower = file_path.lower()
        if lower.endswith(".html"):
            from services.archive_export import export_character_mhtml
            try:
                export_character_mhtml(state, file_path,
                                       show_casualties=self._show_casualties())
            except Exception as e:
                ui.common.dialogs.showerror("错误", f"导出角色档案失败：{e}")
                return
            ui.common.dialogs.showinfo("成功", f"角色档案已导出到：{file_path}")
            return
        if lower.endswith(".chara.json"):
            card_path = file_path
        elif lower.endswith(".json"):
            card_path = file_path[:-5] + ".chara.json"
        else:
            card_path = file_path + ".chara.json"
        self.gui_ref.export_character_card_from_state(state, card_path)

    def _delete_character(self):
        if not self.gui_ref or not self.gui_ref.generator_panel.current_state:
            ui.common.dialogs.showerror("错误", "未绑定角色数据")
            return
        state = self.gui_ref.generator_panel.current_state
        name = state.name
        if not ui.common.dialogs.askyesno("确认删除", f"确定要永久删除角色「{name}」吗？\n此操作不可恢复。"):
            return
        self.character_repo.delete(state.giantess_id)
        self.stop_auto_recovery()
        self.gui_ref.generator_panel.current_state = None
        self.gui_ref.generator_panel.switch_to_params_panel()
        ui.common.dialogs.showinfo("已删除", f"角色「{name}」已永久删除。")

    def start_auto_recovery(self):
        if self._running:
            return
        self._running = True
        self._schedule_recovery()

    def stop_auto_recovery(self):
        self._running = False
        if self._recovery_after_id:
            self.after_cancel(self._recovery_after_id)
            self._recovery_after_id = None

    def _schedule_recovery(self):
        if not self._running:
            return
        self._recovery_after_id = self.after(60000, self._do_recovery)

    def _do_recovery(self):
        if not self._running:
            return
        if self.gui_ref and self.gui_ref.generator_panel.current_state:
            state = self.gui_ref.generator_panel.current_state
            if state.action_points < 100:
                StateService.recover_action_points(state)
            # 在线时每分钟反向演化0.1步
            StateService.apply_step_decay(state, 0.1)
            self.character_repo.save(state)
            self.update_state(state)
        self._schedule_recovery()
