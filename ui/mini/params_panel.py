"""挂件版创建参数面板（低像素风）。

原版一整页的创建参数在这里压成四行「点一下换一个」的循环选择，外加一个名字
输入框：不给滑杆、不给下拉框、不给自定义弹窗。指针点一下就把当前项换成下一个
候选（到末尾回到开头），配合「调查」构成小游戏的抽选循环。

左侧留了一格形象：默认按当前选中的身材预设画一张剪影，点一下可以挑一张图片
顶替它（创建时直接当头像用），右键则退回剪影。这一格与角色卡的立绘同宽同高，
于是「还没创建」与「已创建」两态看起来是同一个位置上的同一块形像。

被牺牲的呈现：参照身高恒为 1.6 米、巨大化模式恒为随机（按规模档位抽），
性格与身材只能在预置表内选择，不能再自定义参数。
"""

import tkinter as tk
from tkinter import filedialog
from typing import Any, Dict

from services.exploration.context import ExplorationContext
from persistence import PersonalityRepo, PresetRepo
from services.preview import render_preset_preview_image
from services.image_service import ImageService
from ui.mini import pixel as px

# 规模档位 -> 随机巨大化的 10^x 区间；原版的两个滑杆由这一行取代。
SCALE_CHOICES = [
    ("No limits", (1, 5)),
    ("Medium", (2, 3)),
    ("Mega", (3, 4)),
    ("Giga", (4, 5)),
]

# 少女的意愿：-5 表示关闭（不参与意愿判定），其余为贪婪值百分比。
WILL_CHOICES = [("关闭", -5), ("1%", 1), ("50%", 50), ("100%", 100)]

REFERENCE_HEIGHT = "1.6"

#: 形象格尺寸：与角色卡的精灵同尺寸，两态之间视觉不跳。
PREVIEW_SIZE = (63, 112)

IMAGE_FILETYPES = [("图片", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"),
                   ("所有文件", "*.*")]


class MiniParamsPanel(px.Panel):
    """左侧形象 + 四行循环选择 + 名字输入，get_params() 与原版面板的键一致。"""

    def __init__(self, parent, context: ExplorationContext,
                 preset_repo: PresetRepo, personality_repo: PersonalityRepo):
        super().__init__(parent, fill="ink_alt", border="line")
        self.context = context
        self.preset_repo = preset_repo
        self.personality_repo = personality_repo
        self.uploaded_image_path = None      # 用户点选的形象图；None 表示用剪影
        self._preview_photo = None

        self.name_var = tk.StringVar(value="神秘少女")
        self.columnconfigure(1, weight=1)    # 左列是形象格，右列（参数）吃满余宽

        self._build_preview()
        body = px.transparent(self)
        body.grid(row=0, column=1, sticky='ew', padx=(0, 6), pady=6)

        name_row = px.transparent(body)
        name_row.pack(fill='x', pady=(0, 4))
        name_row.columnconfigure(1, weight=1)
        px.label(name_row, "名字", tone="text_dim").grid(
            row=0, column=0, sticky='w', padx=(0, 6))
        px.entry(name_row, textvariable=self.name_var, height=24).grid(
            row=0, column=1, sticky='ew', padx=(0, 4))
        px.PixelButton(name_row, "换", self.refresh_random, tone="text_dim",
                       width=26, height=24).grid(row=0, column=2)

        self.scale_row = px.CycleRow(body, "规模", SCALE_CHOICES,
                                     button_width=96)
        self.scale_row.set_value((2, 3))
        self.scale_row.pack(fill='x', pady=3)

        self.will_row = px.CycleRow(body, "意愿", WILL_CHOICES, tone="accent",
                                    button_width=96)
        self.will_row.pack(fill='x', pady=3)

        self.personality_row = px.CycleRow(
            body, "性格", [("随机", None)], tone="report",
            button_width=96)
        self.personality_row.pack(fill='x', pady=3)

        self.preset_row = px.CycleRow(
            body, "身材", [("随机", None)], tone="dungeon",
            button_width=96,
            on_change=lambda _v: self.refresh_preview())
        self.preset_row.pack(fill='x', pady=3)

        self.refresh_choices()

    def _build_preview(self):
        """左侧形象格：图片在上、提示在下，点一下换图、右键退回剪影。"""
        column = px.transparent(self)
        column.grid(row=0, column=0, sticky='ns', padx=(6, 4), pady=6)

        self._preview_slot = tk.Frame(column, width=PREVIEW_SIZE[0],
                                      height=PREVIEW_SIZE[1], bd=0,
                                      highlightthickness=0,
                                      bg=px.base_color(self))
        px.bind_colors(self._preview_slot, {'bg': px.FOLLOW})
        self._preview_slot.pack()
        self._preview_slot.pack_propagate(False)

        self._preview_label = px.label(self._preview_slot, "", fill=px.FOLLOW)
        self._preview_label.pack(fill='both', expand=True)

        self._preview_hint = px.label(column, "上传形象...", tone="text_off", size=12)
        self._preview_hint.pack(pady=(2, 0))

        for widget in (self._preview_slot, self._preview_label):
            widget.configure(cursor='hand2')
            widget.bind("<Button-1>", self.choose_image)
            widget.bind("<Button-3>", self.clear_image)

    # ==================== 形象 ====================
    def refresh_preview(self):
        """重画左侧形象：优先用已选图片，否则按当前身材预设画剪影。"""
        if self.uploaded_image_path:
            image = ImageService.load_from_path(self.uploaded_image_path)
        else:
            image = render_preset_preview_image(self.current_preset())
        if image is None:
            self._preview_photo = None
            px.set_image(self._preview_label, None)
            return
        # 与角色卡同一套处理：缩到目标尺寸、限量调色板、再放大成块状精灵。
        sprite = px.pixelate(image, PREVIEW_SIZE, colors=14,
                             background=px.pick("ink_alt"))
        self._preview_photo = px.to_photo(sprite, PREVIEW_SIZE)
        px.set_image(self._preview_label, self._preview_photo)

    def current_preset(self):
        """当前身材预设；选了「随机」时用表里的第一项占位，好让形象格有东西可看。"""
        preset = self.preset_row.get()
        if preset is not None:
            return preset
        presets = self.preset_repo.load()
        return presets[0] if presets else None

    def choose_image(self, _event=None):
        """点形象格：挑一张图片顶替剪影（创建时直接当头像）。"""
        path = filedialog.askopenfilename(title="选择形象图片",
                                          filetypes=IMAGE_FILETYPES)
        if not path:
            return
        self.uploaded_image_path = path
        self.refresh_preview()
        self._preview_hint.configure(text="右键还原")

    def clear_image(self, _event=None):
        """右键形象格：退回按身材预设画的剪影。"""
        if not self.uploaded_image_path:
            return
        self.uploaded_image_path = None
        self.refresh_preview()
        self._preview_hint.configure(text="点击换图")

    def refresh_theme(self):
        """主题切换后按新底色重画剪影。"""
        self.refresh_preview()

    # ==================== 对外接口 ====================
    def get_params(self) -> Dict[str, Any]:
        """返回与 CreationService.core_from_params 约定一致的参数字典。"""
        low, high = self.scale_row.get()
        greed = max(0, int(self.will_row.get()))
        return {
            "name": self.name_var.get().strip() or "神秘少女",
            "nick": "",
            "original_height": REFERENCE_HEIGHT,
            "height_option": "random",
            "custom_height": "",
            "min_slider": low,
            "max_slider": high,
            "will": greed > 0,
            "greed": greed,
            "selected_personality_index": -1,
            "current_personality_obj": self.personality_row.get(),
            "current_preset_obj": self.preset_row.get(),
            "intro_hidden": "",
            "intro_visible": "",
            "selected_tags": [],
            "birthday": "",
            "uploaded_image_path": self.uploaded_image_path,
        }

    def refresh_choices(self):
        """重新读取性格 / 身材表（世界包切换或表格变化后调用）。"""
        personalities = [("随机", None)] + [
            (p.name, p) for p in self.personality_repo.load()]
        presets = [("随机", None)] + [
            (p.name, p) for p in self.preset_repo.load()]
        self.personality_row.set_options(personalities)
        self.preset_row.set_options(presets)
        self.refresh_preview()

    def refresh_random(self):
        """重掷名字与参照身高（参照身高只作档案记录，不参与界面选择）。"""
        name, _nick = self.context.creation_service.generate_random_name_nick()
        self.name_var.set(name)

    def reset(self):
        """卸下角色后清空性格 / 身材的选择与已选形象。"""
        self.personality_row.set_value(None)
        self.preset_row.set_value(None)
        self.uploaded_image_path = None
        self._preview_hint.configure(text="点击换图")
        self.refresh_preview()

    def height_range(self):
        """当前随机巨大化的身高区间（下限, 上限），单位米，供调查试算使用。"""
        low, high = self.scale_row.get()
        return 10 ** low, 10 ** high

    def update_theme(self, mode=None):
        """配色由像素调色板统一给出，无需逐个刷新。"""
