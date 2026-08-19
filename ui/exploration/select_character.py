import datetime
import json
import os

import customtkinter as ctk
from PIL import Image

from ui.common.widgets import ClickableCard
from persistence.character_repo import CharacterRepo
from logic import format_size
from services.image_service import ImageService
from ui.common.theme import (
    TEXT, HARD_TITLE, SOFT, TEXT_MUTED, PLACEHOLDER,
    PNL_BG, BORDER, BORDER_ALT, HOVER_ALT,
)
from ui.common import fonts as ui_fonts

_MAX_SEARCH_LEN = 20
_IMG_HEIGHT = 120


class SelectCharacterPanel(ctk.CTkFrame):
    def __init__(self, parent, character_repo: CharacterRepo, on_selected=None, on_back=None, context=None, **kwargs):
        kwargs.setdefault("height", 310)
        super().__init__(parent, fg_color=PNL_BG,
                         border_width=1, border_color=BORDER,
                         corner_radius=14, **kwargs)
        
        # 阻止子控件撑开/压缩 Frame 尺寸，确保 height 设置生效
        self.grid_propagate(False)

        self.character_repo = character_repo
        self.on_selected = on_selected
        self.on_back = on_back
        self.context = context
        self._characters = []
        self._characters_signature = None
        self._card_widgets = []
        self._img_cache = {}
        self._current_id = None

        self.grid_columnconfigure(0, weight=0, minsize=260)
        self.grid_columnconfigure(1, weight=1, minsize=230)
        self.grid_rowconfigure(0, weight=1)

        self._build_left_panel()
        self._build_right_panel()

        self._load_characters()

    # ── left: search bar + card list ─────────────────────────
    def _build_left_panel(self):
        left = ctk.CTkFrame(self, fg_color="transparent")
        left.grid(row=0, column=0, sticky='nsew', padx=3, pady=12)
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)

        # 顶部标题栏改用 pack 布局
        top = ctk.CTkFrame(left, fg_color="transparent")
        top.grid(row=0, column=0, sticky='ew', pady=(0, 5))

        # 返回按钮（靠左）
        self.back_btn = ctk.CTkButton(
            top, text="←", width=30, height=28,
            command=self._back,
            fg_color="transparent",
text_color=TEXT_MUTED,
                hover_color=HOVER_ALT,
                border_width=1, border_color=BORDER_ALT,
            corner_radius=8
        )
        self.back_btn.pack(side='left', padx=2)

        # 标题文本（靠左）
        title_lbl = ctk.CTkLabel(
            top, text="选择角色", font=ui_fonts.ui_font(12, "bold"),
text_color=SOFT
            )
        title_lbl.pack(side='left', padx=8)

        # 搜索框（填充剩余空间）
        self._search_entry = ctk.CTkEntry(
            top,
            placeholder_text="搜索名称或昵称",
            placeholder_text_color=PLACEHOLDER,
            border_width=1,
            border_color=BORDER_ALT,
            fg_color=PNL_BG
        )
        self._search_entry.pack(side='left', fill='x', expand=True, padx=22)
        
        # 确保初始化时正确绘制并显示 placeholder
        if hasattr(self._search_entry, "_activate_placeholder"):
            self._search_entry._activate_placeholder()

        # 绑定按键释放事件来触发搜索与字数截断
        self._search_entry.bind("<KeyRelease>", self._on_search_change)

        self._scroll = ctk.CTkScrollableFrame(left, fg_color="transparent")
        self._scroll.grid(row=1, column=0, sticky='nsew')

    # ── right: image + info detail panel ─────────────────────
    def _build_right_panel(self):
        self._detail = ctk.CTkFrame(self, fg_color="transparent")
        self._detail.grid(row=0, column=1, sticky='nsew', padx=3, pady=12)
        self._detail.grid_columnconfigure(0, weight=1)
        self._detail.grid_rowconfigure(0, weight=1)

        self._placeholder = ctk.CTkLabel(
            self._detail, text="悬停角色卡片\n查看详情",
            font=ui_fonts.ui_font(11),
            text_color=PLACEHOLDER,
            anchor='center',
            justify='center'
        )
        self._placeholder.pack(expand=True, fill='both')

        self._detail_content = ctk.CTkFrame(self._detail, fg_color="transparent")
        self._detail_content.grid_columnconfigure(1, weight=1)

    # ── search ───────────────────────────────────────────────
    def _on_search_change(self, event=None):
        text = self._search_entry.get()
        if len(text) > _MAX_SEARCH_LEN:
            self._search_entry.delete(_MAX_SEARCH_LEN, "end")
            text = text[:_MAX_SEARCH_LEN]
        self._filter_cards()

    def _filter_cards(self):
        query = self._search_entry.get().strip().lower()
        for cd in self._card_widgets:
            item = cd["item"]
            name = item.get("name", "").lower()
            nick = item.get("nick", "").lower()
            if not query or query in name or query in nick:
                cd["frame"].pack(fill='x')
            else:
                cd["frame"].pack_forget()

    # ── card hover ───────────────────────────────────────────
    def _on_card_enter(self, item):
        self._show_detail(item)

    # ── detail ───────────────────────────────────────────────
    def _show_detail(self, item):
        if self._current_id == item["id"]:
            return
        self._current_id = item["id"]

        self._placeholder.pack_forget()
        for w in self._detail_content.winfo_children():
            w.destroy()

        row = 0

        max_width = max(0, self._detail_content.winfo_width() - 4)
        img = self._load_portrait(item, max_width)
        if img:
            img_lbl = ctk.CTkLabel(self._detail_content, image=img, text="")
            img_lbl.grid(row=row, column=0, columnspan=2, sticky='ew', pady=10)
            row += 1

        name_text = item.get("name", "")
        if item.get("nick"):
            name_text += f" ({item['nick']})"
        ctk.CTkLabel(
            self._detail_content, text=name_text,
            font=ui_fonts.ui_font(12, "bold"),
            text_color=TEXT,
            anchor='center'
        ).grid(row=row, column=0, columnspan=2, sticky='ew', pady=(0, 2))
        row += 1

        fields = [
            ("身高", format_size(item.get('height', 0)) if item.get('height', 0) else "—"),
            ("创建日期", item.get("created_at", "") or "—"),
            ("最后修改", item.get("updated_at", "") or "—"),
        ]
        for label, value in fields:
            ctk.CTkLabel(
                self._detail_content, text=label,
                font=ui_fonts.ui_font(11),
                text_color=SOFT,
                anchor='w'
            ).grid(row=row, column=0, sticky='w', pady=0, padx=(25, 5))
            ctk.CTkLabel(
                self._detail_content, text=value,
                font=ui_fonts.ui_font(11),
                text_color=HARD_TITLE,
                anchor='w'
            ).grid(row=row, column=1, sticky='w', pady=0, padx=(5, 25))
            row += 1

        self._detail_content.pack(fill='both', expand=True)

    def _load_portrait(self, item, max_width):
        avatar_path = item.get("avatar_path", "")
        if not avatar_path and self.context is not None:
            avatar_path = self.context.ensure_avatar_for_state_id(item["id"])
            if avatar_path:
                item["avatar_path"] = avatar_path
        if not avatar_path:
            return None
        abspath = self.character_repo.get_avatar_abspath(item["id"], avatar_path)
        if abspath in self._img_cache:
            return self._img_cache[abspath]
        try:
            pil_img = ImageService.load_from_path(abspath)
            if pil_img is None:
                return None
            w, h = pil_img.size
            if h <= 0:
                return None
            target_w = min(int(w * _IMG_HEIGHT / h), max_width) if max_width > 0 else int(w * _IMG_HEIGHT / h)
            if target_w <= 0:
                return None
            resized = pil_img.resize((target_w, _IMG_HEIGHT), Image.Resampling.LANCZOS)
            ctk_img = ctk.CTkImage(light_image=resized, dark_image=resized,
                                   size=(target_w, _IMG_HEIGHT))
            self._img_cache[abspath] = ctk_img
            return ctk_img
        except Exception:
            return None

    # ── cards ────────────────────────────────────────────────
    def _build_cards(self):
        for cd in self._card_widgets:
            cd["frame"].destroy()
        self._card_widgets.clear()
        self._current_id = None
        self._hide_detail()

        if not self._characters:
            ctk.CTkLabel(self._scroll, text="没有已保存的角色",
text_color=PLACEHOLDER,
                         font=ui_fonts.ui_font(13)).pack(pady=30)
            return

        for item in self._characters:
            title = item.get("name", "")
            title_extra = None
            if item.get("nick"):
                title_extra = [
                    {"text": f"({item['nick']})", "font": ui_fonts.ui_font(10),
                     "text_color": SOFT}
                ]

            card = ClickableCard(
                self._scroll,
                title=title,
                title_extra=title_extra,
                title_font=ui_fonts.ui_font(12, "bold"),
                info_pad=(10, 4),
                on_click=lambda cid=item["id"]: self._on_card_selected(cid),
                on_enter=lambda i=item: self._on_card_enter(i),
                corner_radius=8,
            )
            card.pack(fill='x', padx=5, pady=2)
            self._card_widgets.append({"frame": card, "item": item})

    # ── data ─────────────────────────────────────────────────
    def _load_characters(self) -> bool:
        """从磁盘加载角色列表；若数据未变化则跳过重建卡片，返回是否重建过。

        返回 True 表示卡片已重建，False 表示直接复用了上次的卡片。
        """
        chars = []
        states_dir = self.character_repo.states_dir
        if os.path.exists(states_dir):
            for folder in os.listdir(states_dir):
                data_file = os.path.join(states_dir, folder, "info.json")
                if not os.path.isfile(data_file):
                    continue
                try:
                    with open(data_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                except Exception:
                    continue

                giantess_id = data.get('giantess_id', folder)

                def _fmt(ts):
                    if not ts:
                        return ""
                    try:
                        return datetime.datetime.fromisoformat(ts).strftime("%Y-%m-%d %H:%M")
                    except (ValueError, TypeError):
                        return str(ts)[:16]

                chars.append({
                    "id": giantess_id,
                    "name": data.get('name', '未知'),
                    "nick": data.get('nick', ''),
                    "height": data.get('height', 0),
                    "avatar_path": data.get('avatar_path', ''),
                    "created_at": _fmt(data.get('created_at', '')),
                    "updated_at": _fmt(data.get('updated_at', '')),
                })

        chars.sort(key=lambda x: (not x["updated_at"], x["updated_at"]), reverse=True)

        signature = tuple(
            (c["id"], c["name"], c["nick"], c["height"], c["avatar_path"], c["created_at"], c["updated_at"])
            for c in chars
        )
        if signature == self._characters_signature:
            return False

        self._characters = chars
        self._characters_signature = signature
        self._build_cards()
        return True

    def _hide_detail(self):
        self._placeholder.pack_forget()
        for w in self._detail_content.winfo_children():
            w.destroy()
        self._detail_content.pack_forget()
        self._placeholder.pack(expand=True, fill='both')

    def reset(self):
        """重置搜索并刷新角色列表；数据未变化时复用已有卡片，避免每次打开都重建导致闪烁。"""
        self._search_entry.delete(0, 'end')
        if hasattr(self._search_entry, "_activate_placeholder"):
            self._search_entry._activate_placeholder()
        rebuilt = self._load_characters()
        if not rebuilt:
            # 数据未变：恢复全部卡片显示（上次可能被搜索过滤隐藏）
            for cd in self._card_widgets:
                cd["frame"].pack(fill='x')

    def update_theme(self, mode=None):
        """角色卡片使用 CustomTkinter 双主题颜色，由框架自动更新。"""
        return

    def _on_card_selected(self, giantess_id):
        if self.on_selected:
            self.on_selected(giantess_id)

    def _back(self):
        if self.on_back:
            self.on_back()
