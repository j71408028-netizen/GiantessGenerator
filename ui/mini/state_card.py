"""挂件版角色卡（低像素风）。

原版状态面板里的尺寸翻页表、新闻流、随机尺寸展示都不再出现：卡片只保留一眼
能读完的档案摘要、一张按像素精灵画出来的身材预览，以及导出 / 删除 / 新建三个
小按钮——「切换角色」交给标题栏的角色档案屏。

排布是左右两栏：立绘在左、自上而下占满整张卡的高度，档案与按钮在右侧依次排开，
形象因此能拿到卡片的全部纵向空间，不会被压成顶部的一张小图。
"""

import tkinter as tk

from core import imaging
from core.logic import SIZE_DISPLAY, format_size, get_size_category
from core.models import CharacterSnapshot
from services.state_service import StateService
from ui.mini import pixel as px

# 精灵目标尺寸（逻辑像素）：缩到这个尺寸再交回去显示，得到块状点阵观感。
# 高度按「档案 + 按钮」那一摞的自然高度取，形象于是正好顶天立地。
SPRITE_SIZE = (56, 100)


class MiniStateCard(px.Panel):
    """当前角色的像素档案卡。"""

    def __init__(self, parent, app):
        super().__init__(parent, fill="ink_alt", border="line")
        self.app = app
        self.state: CharacterSnapshot = None
        self._sprite = None
        self._recovery_job = None

        self.columnconfigure(1, weight=1)
        self._build_ui()

    # ==================== UI ====================
    def _build_ui(self):
        # 精灵槽：固定像素尺寸的外壳 + 铺满其中的图片标签（tk 的 Label 尺寸随
        # 文字/图片变化，尺寸必须由外壳锁死，否则空图时卡片会塌下来）。
        # sticky='ns' 让它跟着卡片长高：左侧这一列从顶到底都是形象。
        self._sprite_slot = tk.Frame(self, width=SPRITE_SIZE[0], height=SPRITE_SIZE[1],
                                     bd=0, highlightthickness=0,
                                     bg=px.base_color(self))
        px.bind_colors(self._sprite_slot, {'bg': px.FOLLOW})
        self._sprite_slot.grid(row=0, column=0, rowspan=5, padx=(6, 6), pady=6,
                               sticky='ns')
        self._sprite_slot.pack_propagate(False)

        self._sprite_label = px.label(self._sprite_slot, "", fill=px.FOLLOW)
        self._sprite_label.pack(fill='both', expand=True)

        self._name_label = px.label(self, "—", tone="text", size=13, bold=True)
        self._name_label.grid(row=0, column=1, sticky='ew', pady=(6, 0))

        self._size_label = px.label(self, "", tone="text_dim")
        self._size_label.grid(row=1, column=1, sticky='ew')

        self._bar_label = px.label(self, "", tone="ok")
        self._bar_label.grid(row=2, column=1, sticky='ew')

        self._stat_label = px.label(self, "", tone="text_off")
        self._stat_label.grid(row=3, column=1, sticky='ew')

        buttons = px.transparent(self)
        buttons.grid(row=4, column=1, sticky='ew', padx=(0, 6), pady=(2, 6))
        px.small_button(buttons, "聊天", self.app.open_chat,
                        tone="report", width=54).pack(side='left', padx=(0, 2))
        px.small_button(buttons, "导出", self.app.export_character,
                        width=54).pack(side='left', padx=2)
        px.small_button(buttons, "删除", self.app.delete_character,
                        tone="danger", width=54).pack(side='left', padx=2)
        px.small_button(buttons, "新建", self.app.unload_character,
                        tone="accent", width=54).pack(side='left', padx=(2, 0))

    # ==================== 状态同步 ====================
    def update_state(self, state: CharacterSnapshot):
        self.state = state
        if state is None:
            self._name_label.configure(text="—")
            self._size_label.configure(text="")
            self._bar_label.configure(text="")
            self._stat_label.configure(text="")
            self._clear_sprite()
            return

        nick = f"（{state.nick}）" if state.nick else ""
        self._name_label.configure(text=f"{state.name}{nick}")

        category = get_size_category(state.height)
        category_text = SIZE_DISPLAY.get(category, "")
        size_text = format_size(state.height)
        if category_text:
            size_text += f"　{category_text}"
        self._size_label.configure(text=size_text)

        points = max(0, int(state.action_points))
        tone = "ok" if points >= 50 else ("accent" if points >= 20 else "danger")
        self._bar_label.configure(
            text=f"行动点 {px.block_bar(points, 100, 10)} {points}",
            text_color=px.color(tone))

        self._stat_label.configure(
            text=f"介入 {state.intrusion:.1f}　破坏 {state.destruction:.1f}")
        self._refresh_sprite(state)

    def _refresh_sprite(self, state: CharacterSnapshot):
        path = self.app.context.character_repo.get_avatar_abspath(
            state.giantess_id, state.avatar_path)
        pil_image = imaging.load_from_path(path)
        if pil_image is None:
            self._clear_sprite()
            return
        self._apply_sprite(pil_image)

    def _apply_sprite(self, pil_image):
        # 合到面板底色上再量化：量化会丢掉 alpha，透明区必须自己填。
        sprite = px.pixelate(pil_image, SPRITE_SIZE, colors=14,
                             background=px.pick("ink_alt"))
        self._sprite = px.to_photo(sprite, SPRITE_SIZE)
        px.set_image(self._sprite_label, self._sprite)

    def refresh_theme(self):
        """主题切换后按新底色重新生成精灵。"""
        if self.state is not None:
            self._refresh_sprite(self.state)

    def _clear_sprite(self):
        """清空精灵图；空槽由外壳的固定尺寸维持，卡片不会因此塌陷。"""
        self._sprite = None
        px.set_image(self._sprite_label, None)

    # ==================== 离线回复 ====================
    def start_auto_recovery(self):
        if self._recovery_job is not None:
            return
        self._schedule_recovery()

    def stop_auto_recovery(self):
        if self._recovery_job is not None:
            self.after_cancel(self._recovery_job)
            self._recovery_job = None

    def _schedule_recovery(self):
        self._recovery_job = self.after(60000, self._do_recovery)

    def _do_recovery(self):
        self._recovery_job = None
        state = self.state
        if state is not None:
            if state.action_points < 100:
                StateService.recover_action_points(state)
            StateService.apply_step_decay(state, 0.1)
            self.app.context.character_repo.save(state)
            self.update_state(state)
        if self.winfo_exists():
            self._schedule_recovery()
