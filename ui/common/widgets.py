import tkinter as tk
import customtkinter
import customtkinter as ctk
import sys
import difflib

from ui.common import fonts as ui_fonts
from ui.common.theme import (
    BASE, HOVER, BORDER_ALT,
    PNL_BG, HOVER_ALT, MENU_HOVER, TEXT,
    HARD_TITLE, SOFT, TEXT_MUTED,
    GOLD_BORDER,
    SEG_TRACK_BG, SEG_TRACK_BORDER, SEG_SELECTED_BG,
    SEG_SELECTED_HOVER, SEG_SELECTED_TEXT, SEG_UNSELECTED_TEXT, SEG_HOVER, BORDER,
)


class CTkSegmentedControl(customtkinter.CTkFrame):
    """统一分段切换控件（配色参照左侧导航栏）。

    提供与 CTkSegmentedButton 兼容的基础用法（values / command / set / get），
    内部由独立 CTkButton 组成，选中段可单独指定文字颜色，
    避免 CTkSegmentedButton 选中文字对比度不足的问题。
    """

    def __init__(self, master, values=None, command=None, *,
                 font=None, width=None, height=25, corner_radius=6,
                 fg_color=None, border_color=None, border_width=2,
                 orientation="horizontal", **kwargs):
        super().__init__(
            master,
            fg_color=fg_color or SEG_TRACK_BG,
            border_width=border_width,
            border_color=border_color or SEG_TRACK_BORDER,
            corner_radius=corner_radius,
            **kwargs
        )
        self._command = command
        self._values = list(values or [])
        self._current = self._values[0] if self._values else None
        self._font = font or ui_fonts.ui_font(11)
        self._height = height
        self._corner_radius = max(0, corner_radius - 1)
        self._inner_pad = border_width + 1
        self._buttons = {}

        if width is not None:
            # 固定宽度：禁止内容把轨道撑大（按钮仅作文字宽度，grid 会按列等分）
            self.configure(width=width, height=height + 2 * self._inner_pad)
            self.grid_propagate(False)

        self._build(orientation)

    def _build(self, orientation):
        """用 grid + uniform 保证各段等长，pad 大于边框宽度避免背景压线。"""
        vertical = orientation == "vertical"
        pad = self._inner_pad
        n = len(self._values)
        for i, value in enumerate(self._values):
            active = value == self._current
            btn = customtkinter.CTkButton(
                self,
                text=value,
                width=0,
                height=self._height,
                corner_radius=self._corner_radius,
                font=self._font,
                fg_color=SEG_SELECTED_BG if active else "transparent",
                text_color=SEG_SELECTED_TEXT if active else SEG_UNSELECTED_TEXT,
                hover_color=SEG_SELECTED_HOVER if active else SEG_HOVER,
                command=lambda v=value: self._select(v)
            )
            if vertical:
                top = pad if i == 0 else 1
                bottom = pad if i == n - 1 else 1
                btn.grid(row=i, column=0, sticky='nsew', padx=pad, pady=(top, bottom))
                self.grid_rowconfigure(i, weight=1, uniform='segment')
                self.grid_columnconfigure(0, weight=1)
            else:
                left = pad if i == 0 else 1
                right = pad if i == n - 1 else 1
                btn.grid(row=0, column=i, sticky='nsew', padx=(left, right), pady=pad)
                self.grid_columnconfigure(i, weight=1, uniform='segment')
                self.grid_rowconfigure(0, weight=1)
            self._buttons[value] = btn

    def set(self, value):
        """选中指定分段（不触发 command，与 CTkSegmentedButton 一致）。"""
        if value not in self._values:
            return
        self._current = value
        for val, btn in self._buttons.items():
            active = val == value
            btn.configure(
                fg_color=SEG_SELECTED_BG if active else "transparent",
                text_color=SEG_SELECTED_TEXT if active else SEG_UNSELECTED_TEXT,
                hover_color=SEG_SELECTED_HOVER if active else SEG_HOVER,
            )

    def _select(self, value):
        self.set(value)
        if self._command:
            self._command(value)

    def get(self):
        return self._current


class CTkScrollableDropdownFrame(customtkinter.CTkFrame):
    '''
    Advanced Scrollable Dropdown Frame class for customtkinter widgets
    Author: Akash Bora
    Modifications: added disabled_values support, sorted display (enabled first, disabled last)
    '''

    def __init__(self, attach, x=None, y=None, button_color=None, height: int = 200, width: int = None,
                 fg_color=None, button_height: int = 20, justify="center", scrollbar_button_color=None,
                 scrollbar=True, scrollbar_button_hover_color=None, frame_border_width=2, values=[],
                 command=None, image_values=[], double_click=False, frame_corner_radius=True, resize=True,
                 frame_border_color=None, text_color=None, autocomplete=False, disabled_values=None,
                 text_colors=None, hover_color=None, **button_kwargs):

        super().__init__(master=attach.winfo_toplevel(), bg_color=attach.cget("bg_color"))

        self.attach = attach
        self.corner = 11 if frame_corner_radius else 0
        self.padding = 0
        self.disable = True

        self.hide = True   # 初始隐藏
        self.attach.bind('<Configure>', lambda e: self._withdraw() if not self.disable else None, add="+")
        self.attach.winfo_toplevel().bind("<ButtonPress>", lambda e: self._withdraw(e) if not self.disable else None, add="+")
        self.bind("<Escape>", lambda e: self._withdraw() if not self.disable else None, add="+")

        self.disable = False
        self.fg_color = customtkinter.ThemeManager.theme["CTkFrame"]["fg_color"] if fg_color is None else fg_color
        self.scroll_button_color = customtkinter.ThemeManager.theme["CTkScrollbar"]["button_color"] if scrollbar_button_color is None else scrollbar_button_color
        self.scroll_hover_color = customtkinter.ThemeManager.theme["CTkScrollbar"]["button_hover_color"] if scrollbar_button_hover_color is None else scrollbar_button_hover_color
        self.frame_border_color = customtkinter.ThemeManager.theme["CTkFrame"]["border_color"] if frame_border_color is None else frame_border_color
        self.button_color = customtkinter.ThemeManager.theme["CTkFrame"]["top_fg_color"] if button_color is None else button_color
        self.text_color = customtkinter.ThemeManager.theme["CTkLabel"]["text_color"] if text_color is None else text_color

        if scrollbar is False:
            self.scroll_button_color = self.fg_color
            self.scroll_hover_color = self.fg_color

        self.frame = customtkinter.CTkScrollableFrame(self, fg_color=self.fg_color, bg_color=attach.cget("bg_color"),
                                        scrollbar_button_hover_color=self.scroll_hover_color,
                                        corner_radius=self.corner, border_width=frame_border_width,
                                        scrollbar_button_color=self.scroll_button_color,
                                        border_color=self.frame_border_color)
        self.frame._scrollbar.grid_configure(padx=3)
        self.frame.pack(expand=True, fill="both")

        if self.corner==0:
            self.corner = 21

        self.dummy_entry = customtkinter.CTkEntry(self.frame, fg_color="transparent", border_width=0, height=1, width=1)
        self.no_match = customtkinter.CTkLabel(self.frame, text="No Match")
        self.height = height
        self.height_new = height
        self.width = width
        self.command = command
        self.fade = False
        self.resize = resize
        self.autocomplete = autocomplete
        self.var_update = customtkinter.StringVar()
        self.appear = False

        if justify.lower()=="left":
            self.justify = "w"
        elif justify.lower()=="right":
            self.justify = "e"
        else:
            self.justify = "c"

        self.button_height = button_height
        self.values = values
        self.image_values = None if len(image_values)!=len(self.values) else image_values
        self.disabled_values = set(disabled_values or [])
        self.text_colors = text_colors

        self.button_kwargs = dict(button_kwargs)
        if hover_color is not None:
            self.button_kwargs["hover_color"] = hover_color

        self._init_buttons(**self.button_kwargs)

        # Add binding for different ctk widgets
        if double_click or self.attach.winfo_name().startswith("!ctkentry") or self.attach.winfo_name().startswith("!ctkcombobox"):
            self.attach.bind('<Double-Button-1>', lambda e: self._iconify(), add="+")
            self.attach._entry.bind('<FocusOut>', lambda e: self._withdraw() if not self.disable else None, add="+")
        else:
            self.attach.bind('<Button-1>', lambda e: self._iconify(), add="+")

        if self.attach.winfo_name().startswith("!ctkcombobox"):
            self.attach._canvas.tag_bind("right_parts", "<Button-1>", lambda e: self._iconify())
            self.attach._canvas.tag_bind("dropdown_arrow", "<Button-1>", lambda e: self._iconify())

            if self.command is None:
                self.command = self.attach.set

        if self.attach.winfo_name().startswith("!ctkoptionmenu"):
            self.attach._canvas.bind("<Button-1>", lambda e: self._iconify())
            self.attach._text_label.bind("<Button-1>", lambda e: self._iconify())
            if self.command is None:
                self.command = self.attach.set

        self.x = x
        self.y = y

        self.attach.bind("<Destroy>", lambda _: self._destroy(), add="+")

        if self.autocomplete:
            self.bind_autocomplete()

    def _destroy(self):
        self.after(500, self.destroy_popup)

    def _withdraw(self, event=None):
        # 如果是鼠标点击事件，判断坐标，防止内部点击导致组件提前隐藏
        if event and hasattr(event, 'x_root'):
            try:
                # 检查点击坐标是否在下拉框自身区域内
                x1, y1 = self.winfo_rootx(), self.winfo_rooty()
                x2, y2 = x1 + self.winfo_width(), y1 + self.winfo_height()
                if x1 <= event.x_root <= x2 and y1 <= event.y_root <= y2:
                    return  # 在下拉框内，放行点击，不隐藏

                # 检查点击坐标是否在绑定的主组件 (如 ComboBox) 区域内
                ax1, ay1 = self.attach.winfo_rootx(), self.attach.winfo_rooty()
                ax2, ay2 = ax1 + self.attach.winfo_width(), ay1 + self.attach.winfo_height()
                if ax1 <= event.x_root <= ax2 and ay1 <= event.y_root <= ay2:
                    return  # 在触发组件内，交给 _iconify 处理，不强制隐藏
            except Exception:
                pass

        # 修复了原本 if 逻辑异常的问题，只要目前是可见状态就隐藏
        if self.winfo_viewable():
            self.place_forget()

        self.event_generate("<<Closed>>")
        self.hide = True

    def _update(self, a, b, c):
        self.live_update(self.attach._entry.get())

    def bind_autocomplete(self, ):
        def appear(x):
            self.appear = True

        if self.attach.winfo_name().startswith("!ctkcombobox"):
            self.attach._entry.configure(textvariable=self.var_update)
            self.attach.set(self.values[0])
            self.attach._entry.bind("<Key>", appear)
            self.var_update.trace_add('write', self._update)

        if self.attach.winfo_name().startswith("!ctkentry"):
            self.attach.configure(textvariable=self.var_update)
            self.attach.bind("<Key>", appear)
            self.var_update.trace_add('write', self._update)

    def _init_buttons(self, **button_kwargs):
        self.i = 0
        self.widgets = {}
        for idx, row in enumerate(self.values):
            is_disabled = row in self.disabled_values
            if self.text_colors and isinstance(self.text_colors, dict):
                color = self.text_colors.get(row, self.text_color)
            elif self.text_colors and isinstance(self.text_colors, (list, tuple)):
                color = self.text_colors[idx] if idx < len(self.text_colors) else self.text_color
            else:
                color = self.text_color
            btn_color = "gray" if is_disabled else color
            btn = customtkinter.CTkButton(self.frame,
                                          text=row,
                                          height=self.button_height,
                                          fg_color=self.button_color,
                                          text_color=btn_color,
                                          image=self.image_values[self.i] if self.image_values is not None else None,
                                          anchor=self.justify,
                                          command=lambda k=row: self._attach_key_press(k), **button_kwargs)
            btn._disabled = is_disabled
            self.widgets[self.i] = btn
            btn.pack(fill="x", pady=2, padx=(self.padding, 0))
            self.i += 1

        self.button_num = len(self.values)

    def apply_theme(self, mode):
        """更新已创建的自定义下拉框，避免主题切换后保留旧色。"""
        if mode.lower() == "dark":
            fg_color = button_color = PNL_BG[1]
            hover_color = HOVER_ALT[1]
            scrollbar_color = HOVER_ALT[1]
            scrollbar_hover_color = MENU_HOVER[1]
            border_color = MENU_HOVER[1]
            text_color = TEXT[1]
        else:
            fg_color = button_color = PNL_BG[0]
            hover_color = HOVER_ALT[0]
            scrollbar_color = HOVER_ALT[0]
            scrollbar_hover_color = MENU_HOVER[0]
            border_color = MENU_HOVER[0]
            text_color = TEXT[0]

        self.fg_color = fg_color
        self.button_color = button_color
        self.scroll_button_color = scrollbar_color
        self.scroll_hover_color = scrollbar_hover_color
        self.frame_border_color = border_color
        self.text_color = text_color
        self.configure(bg_color=fg_color)
        self.frame.configure(
            fg_color=fg_color,
            bg_color=self.attach.cget("bg_color"),
            border_color=border_color,
            scrollbar_button_color=scrollbar_color,
            scrollbar_button_hover_color=scrollbar_hover_color
        )
        for button in self.widgets.values():
            button.configure(fg_color=button_color,
                             text_color="gray" if getattr(button, "_disabled", False) else text_color,
                             hover_color=hover_color)

    def destroy_popup(self):
        self.destroy()
        self.disable = True

    def place_dropdown(self):
        x_offset = 0
        y_offset = 0
        target = self.attach
        toplevel = self.attach.winfo_toplevel()

        while target and str(target) != str(toplevel):
            x_offset += target.winfo_x()
            y_offset += target.winfo_y()
            parent_name = target.winfo_parent()
            if not parent_name:
                break
            target = target._nametowidget(parent_name)

        try:
            scaling = self._get_widget_scaling()
        except AttributeError:
            scaling = 1.0

        logical_x = x_offset / scaling
        logical_y = y_offset / scaling
        logical_height = self.attach.winfo_height() / scaling
        logical_width = self.attach.winfo_width() / scaling

        self.x_pos = logical_x if self.x is None else self.x
        self.y_pos = logical_y + logical_height + 5 if self.y is None else self.y
        frame_border = self.frame.cget("border_width")
        self.width_new = logical_width - 45 + self.corner - frame_border if self.width is None else self.width

        if self.resize:
            if self.button_num <= 5:
                self.height_new = self.button_height * self.button_num + 55
            else:
                self.height_new = self.button_height * self.button_num + 35
            if self.height_new > self.height:
                self.height_new = self.height

        self.frame.configure(width=self.width_new, height=self.height_new)
        self.frame._scrollbar.configure(height=self.height_new)

        self.place(x=self.x_pos, y=self.y_pos)

        if sys.platform.startswith("darwin"):
            self.dummy_entry.pack()
            self.after(100, self.dummy_entry.pack_forget())

        self.lift()
        self.attach.focus()

    def _iconify(self):
        if self.attach.cget("state")=="disabled": return
        if self.disable: return
        if self.hide:
            self.event_generate("<<Opened>>")
            self.hide = False
            self.place_dropdown()
        else:
            self.place_forget()
            self.hide = True

    def _attach_key_press(self, k):
        self.event_generate("<<Selected>>")
        self.fade = True

        if hasattr(self.attach, "set"):
            self.attach.set(k)

        if self.command:
            self.command(k)

        self.fade = False
        self.place_forget()
        self.hide = True

    def live_update(self, string=None):
        if not self.appear: return
        if self.disable: return
        if self.fade: return

        # 先全部隐藏
        for key in self.widgets.keys():
            self.widgets[key].pack_forget()
        self.no_match.pack_forget()

        # 收集所有按钮的启用/禁用状态
        all_enabled = []
        all_disabled = []
        for key in self.widgets.keys():
            btn = self.widgets[key]
            if getattr(btn, '_disabled', False):
                all_disabled.append(btn)
            else:
                all_enabled.append(btn)

        if not string:
            # 无过滤：全部显示，启用在前，禁用在后
            for btn in all_enabled:
                btn.pack(fill="x", pady=2, padx=(self.padding, 0))
            for btn in all_disabled:
                btn.pack(fill="x", pady=2, padx=(self.padding, 0))
            self.button_num = len(self.widgets)
            self.place_dropdown()
            self.frame._parent_canvas.yview_moveto(0.0)
            self.appear = False
            return

        # 有过滤
        string_lower = string.lower()
        matched_enabled = []
        matched_disabled = []
        for btn in all_enabled + all_disabled:
            text = btn.cget("text").lower()
            if text.startswith(string_lower) or difflib.SequenceMatcher(None, text[0:len(string_lower)], string_lower).ratio() > 0.75:
                if getattr(btn, '_disabled', False):
                    matched_disabled.append(btn)
                else:
                    matched_enabled.append(btn)

        if not matched_enabled and not matched_disabled:
            self.no_match.pack(fill="x", pady=2, padx=(self.padding, 0))
            self.button_num = 1
            self.place_dropdown()
            return

        # 先启用，后禁用
        for btn in matched_enabled:
            btn.pack(fill="x", pady=2, padx=(self.padding, 0))
        for btn in matched_disabled:
            btn.pack(fill="x", pady=2, padx=(self.padding, 0))

        self.button_num = len(matched_enabled) + len(matched_disabled)
        self.place_dropdown()
        self.frame._parent_canvas.yview_moveto(0.0)
        self.appear = False

    def insert(self, value, **kwargs):
        btn_kwargs = dict(self.button_kwargs)
        btn_kwargs.update(kwargs)
        self.widgets[self.i] = customtkinter.CTkButton(self.frame,
                                                       text=value,
                                                       height=self.button_height,
                                                       fg_color=self.button_color,
                                                       text_color=self.text_color,
                                                       anchor=self.justify,
                                                       command=lambda k=value: self._attach_key_press(k), **btn_kwargs)
        self.widgets[self.i].pack(fill="x", pady=2, padx=(self.padding, 0))
        self.i+=1
        self.values.append(value)
        self.button_num = len(self.values)

    def _deiconify(self):
        if len(self.values)>0:
            self.pack_forget()

    def popup(self, x=None, y=None):
        self.x = x
        self.y = y
        self.hide = True
        self._iconify()

    def configure(self, **kwargs):
        if "height" in kwargs:
            self.height = kwargs.pop("height")
            self.height_new = self.height
        if "alpha" in kwargs:
            self.alpha = kwargs.pop("alpha")
        if "width" in kwargs:
            self.width = kwargs.pop("width")
        if "fg_color" in kwargs:
            self.frame.configure(fg_color=kwargs.pop("fg_color"))
        if "values" in kwargs or "disabled_values" in kwargs:
            # 同时更新 values 和 disabled_values 时重建按钮
            if "values" in kwargs:
                self.values = kwargs.pop("values")
            if "disabled_values" in kwargs:
                self.disabled_values = set(kwargs.pop("disabled_values") or [])
            if "text_colors" in kwargs:
                self.text_colors = kwargs.pop("text_colors")
            if "image_values" in kwargs:
                self.image_values = kwargs.pop("image_values")
            # 重置 image_values 防止与新的 values 长度不匹配
            if self.image_values is not None and len(self.image_values) != len(self.values):
                self.image_values = None
            # 清除旧按钮
            for key in list(self.widgets.keys()):
                self.widgets[key].destroy()
            self.widgets = {}
            # 保留初始的按钮级参数（如 hover_color、font 等），并合并本次额外传入的按钮参数
            _reserved = {"text", "height", "fg_color", "text_color", "image", "anchor", "command"}
            for _k, _v in kwargs.items():
                if _k not in _reserved:
                    self.button_kwargs[_k] = _v
            self._init_buttons(**self.button_kwargs)
            # _init_buttons 不会改变 self.hide，保持原有状态（True）
            return
        if "text_colors" in kwargs:
            self.text_colors = kwargs.pop("text_colors")
            for key in self.widgets.keys():
                btn = self.widgets[key]
                text = btn.cget("text")
                if self.text_colors and isinstance(self.text_colors, dict):
                    color = self.text_colors.get(text, self.text_color)
                elif self.text_colors and isinstance(self.text_colors, (list, tuple)):
                    idx = list(self.widgets.keys()).index(key)
                    color = self.text_colors[idx] if idx < len(self.text_colors) else self.text_color
                else:
                    color = self.text_color
                btn.configure(text_color=color)
        if "image_values" in kwargs:
            self.image_values = kwargs.pop("image_values")
            self.image_values = None if len(self.image_values)!=len(self.values) else self.image_values
            if self.image_values is not None:
                i=0
                for key in self.widgets.keys():
                    self.widgets[key].configure(image=self.image_values[i])
                    i+=1
        if "button_color" in kwargs:
            button_color = kwargs.pop("button_color")
            for key in self.widgets.keys():
                self.widgets[key].configure(fg_color=button_color)
        if "font" in kwargs:
            font = kwargs.pop("font")
            for key in self.widgets.keys():
                self.widgets[key].configure(font=font)
        for key in self.widgets.keys():
            self.widgets[key].configure(**kwargs)


class ClickableCard(ctk.CTkFrame):
    """可点击卡片组件，含悬停变色、标题行、可选详情行及右侧按键"""

    def __init__(self, master, *, title, title_extra=None,
                 detail=None, is_detail_textbox=False, detail_height=55, detail_cb=None,
                 title_font=None, detail_font=None, detail_color=None,
                 info_pad=None,
                 on_click=None, on_enter=None, on_leave=None,
                 buttons=None, gold_hover=True, cursor=None,
                 corner_radius=8, **kwargs):
        super().__init__(
            master,
            fg_color="transparent",
            corner_radius=corner_radius,
            border_width=1,
            border_color=BORDER,
            **kwargs
        )
        self._gold_hover = gold_hover
        self._on_enter_cb = on_enter
        self._on_leave_cb = on_leave

        info_pad = info_pad or (12, 10)
        self._info = ctk.CTkFrame(self, fg_color="transparent")
        self._info.pack(side='left', fill='both', expand=True,
                        padx=info_pad[0], pady=info_pad[1])

        if title_extra:
            tf = ctk.CTkFrame(self._info, fg_color="transparent")
            tf.pack(fill='x')
            ctk.CTkLabel(
                tf, text=title,
                font=title_font or ui_fonts.ui_font(13, "bold"),
                text_color=TEXT,
                anchor='w'
            ).pack(side='left')
            for i, kw in enumerate(title_extra):
                kw = dict(kw)
                anchor = kw.pop("anchor", "w")
                lbl = ctk.CTkLabel(tf, anchor=anchor, **kw)
                if i == len(title_extra) - 1:
                    # 最后一个副文本标签向右填充剩余空间，扩大可点击范围
                    lbl.pack(side='left', padx=(10, 0), fill='x', expand=True)
                else:
                    lbl.pack(side='left', padx=(10, 0))
        else:
            ctk.CTkLabel(
                self._info, text=title,
                font=title_font or ui_fonts.ui_font(14, "bold"),
                text_color=TEXT,
                anchor='w'
            ).pack(fill='x')

        detail_widget = None
        if detail:
            if is_detail_textbox:
                tb = ctk.CTkTextbox(
                    self._info,
                    fg_color="transparent",
                    font=ui_fonts.ui_font(13),
                    wrap="word",
                    border_width=0,
                    height=detail_height,
                    text_color=TEXT
                )
                tb.pack(fill='x', pady=(0, 0))
                tb.insert("0.0", detail)
                if detail_cb:
                    detail_cb(tb)
                tb.configure(state="disabled")
                detail_widget = getattr(tb, "_textbox", tb)
            else:
                detail_widget = ctk.CTkLabel(
                    self._info, text=detail,
                    font=detail_font or ui_fonts.ui_font(12),
                    text_color=detail_color or SOFT,
                    anchor='w'
                )
                detail_widget.pack(fill='x')

        if buttons:
            btn_frame = ctk.CTkFrame(self, fg_color="transparent")
            btn_frame.pack(side='right', padx=5, pady=5)
            for btn_cfg in buttons:
                cfg = btn_cfg.copy()
                btn_text = cfg.pop("text")
                btn_cmd = cfg.pop("command", None)
                pk = cfg.pop("pack_kw", {"side": "left", "padx": 2})
                ctk.CTkButton(btn_frame, text=btn_text, command=btn_cmd, **cfg).pack(**pk)

        def _all_children(w):
            # 只递归 CTkFrame 容器；CTkLabel 等叶子控件不进入其内部
            # （canvas / 内部 tk label），否则它们的 .bind() 会把回调
            # 同时绑到内部部件上，与这里直接绑定造成重复触发。
            kids = []
            for c in w.winfo_children():
                if isinstance(c, ctk.CTkFrame):
                    kids.extend(_all_children(c))
                else:
                    kids.append(c)
            return kids
        targets = [self, self._info] + _all_children(self._info)
        if detail_widget is not None:
            targets = [detail_widget]
        for w in targets:
            if cursor:
                w.configure(cursor=cursor)
            w.bind("<Enter>", self._on_enter, add="+")
            w.bind("<Leave>", self._on_leave, add="+")
            if on_click:
                w.bind("<Button-1>", lambda e: on_click(), add="+")

    def _on_enter(self, event=None):
        self.configure(fg_color=HOVER)
        if self._gold_hover:
            self.configure(border_color=GOLD_BORDER)
        if self._on_enter_cb:
            self._on_enter_cb()

    def _on_leave(self, event=None):
        self.configure(fg_color="transparent")
        if self._gold_hover:
            self.configure(border_color=BORDER)
        if self._on_leave_cb:
            self._on_leave_cb()


class CollapsibleBlock:
    """折叠块：标题按钮 + 可折叠内容区，风格与探索模式一致"""
    def __init__(self, parent, title, expanded=True, body_padx=20, body_pady=5, on_toggle=None, width=120,
                 body_after=None, header_parent=None):
        self.parent = parent
        self._expanded = expanded
        self._body_padx = body_padx
        self._body_pady = body_pady
        self._on_toggle_cb = on_toggle
        self._body_after = body_after
        self.width = width

        self.header = customtkinter.CTkButton(
            header_parent if header_parent is not None else parent,
            text=("▼ " if expanded else "▶ ") + title, anchor="w",
            fg_color="transparent",
            text_color=HARD_TITLE,
            hover_color=HOVER_ALT,
            width=self.width,
            border_width=2,
            border_color=BORDER,
            corner_radius=8,
            font=ui_fonts.ui_font(12, "bold")
        )

        self.body = customtkinter.CTkFrame(
            parent,
            fg_color=BASE,
            corner_radius=12
        )
        self.body.visible = True

        self.header.configure(command=self.toggle)

    def toggle(self):
        if self._expanded:
            self.body.pack_forget()
            self.header.configure(text=self.header.cget("text").replace("▼", "▶", 1))
            self._expanded = False
        else:
            after_widget = self._body_after if self._body_after is not None else self.header
            self.body.pack(fill='x', padx=self._body_padx, pady=self._body_pady,
                           after=after_widget)
            self.header.configure(text=self.header.cget("text").replace("▶", "▼", 1))
            self._expanded = True
        if self._on_toggle_cb:
            self._on_toggle_cb()

    def expand(self):
        if not self._expanded:
            self.toggle()

    def collapse(self):
        if self._expanded:
            self.toggle()


class StyleListBox(customtkinter.CTkFrame):
    """可复用的多选列表框组件（用于地标/描述风格组选择）"""

    def __init__(self, parent, title, height=2, on_change=None, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)

        self._on_change_cb = on_change
        self._updating = False

        self.header = customtkinter.CTkFrame(self, fg_color="transparent")
        self.header.pack(fill='x', pady=(0, 3))
        self.title_label = customtkinter.CTkLabel(self.header, text=title,
                                                  font=ui_fonts.ui_font(11, "bold"),
                                                  text_color=SOFT)
        self.title_label.pack(side='left')

        self.border = customtkinter.CTkFrame(
            self,
            border_width=1,
            corner_radius=4,
            height=120
        )
        self.border.pack(fill='x', expand=False)
        self.border.pack_propagate(False)

        self.listbox = tk.Listbox(
            self.border, selectmode=tk.MULTIPLE, height=height,
            exportselection=False, font=ui_fonts.ui_font(16),
            relief='flat', highlightthickness=0, borderwidth=0, justify='center'
        )
        self.listbox.pack(side='left', fill='both', expand=True, padx=(5, 0), pady=3)
        self.scrollbar = customtkinter.CTkScrollbar(
            self.border, orientation="vertical", command=self.listbox.yview, width=12
        )
        self.scrollbar.pack(side='right', fill='y', padx=(0, 2), pady=3)
        self.listbox.configure(yscrollcommand=self.scrollbar.set)

        self.apply_theme(customtkinter.get_appearance_mode())

        if on_change:
            self.listbox.bind('<<ListboxSelect>>', self._notify_change)

    def set_title(self, title):
        """动态更新标题文本（用于实时显示选中数量等）。"""
        self.title_label.configure(text=title)

    def add_button(self, text, command=None, side='right', padx=10):
        customtkinter.CTkButton(
            self.header, text=text, width=50, height=20, command=command,
            fg_color="transparent", text_color=TEXT_MUTED,
            hover_color=HOVER_ALT, border_width=1,
            border_color=BORDER_ALT, corner_radius=8,
            font=ui_fonts.ui_font(10)
        ).pack(side=side, padx=padx)

    def sync_items(self, items, selected_indices=None):
        self._updating = True
        self.listbox.delete(0, tk.END)
        for item in items:
            self.listbox.insert(tk.END, item)
        if selected_indices:
            for i in selected_indices:
                self.listbox.selection_set(i)
        self._updating = False

    def get_selected_raw_names(self):
        indices = self.listbox.curselection()
        return [self.extract_raw_name(self.listbox.get(i)) for i in indices]

    def select_all(self):
        self.listbox.selection_set(0, tk.END)
        self._notify_change()

    def clear_selection(self):
        self.listbox.selection_clear(0, tk.END)
        self._notify_change()

    def set_default(self, default_name="ChineseMix"):
        self.listbox.selection_clear(0, tk.END)
        for i in range(self.listbox.size()):
            if self.extract_raw_name(self.listbox.get(i)) == default_name:
                self.listbox.selection_set(i)
                break
        self._notify_change()

    def apply_theme(self, mode):
        if mode.lower() == "dark":
            bg, fg = PNL_BG[1], TEXT[1]
            select_bg, select_fg = HOVER_ALT[1], HARD_TITLE[1]
            border_color = BORDER_ALT[1]
        else:
            bg, fg = PNL_BG[0], TEXT[0]
            select_bg, select_fg = HOVER_ALT[0], HARD_TITLE[0]
            border_color = BORDER_ALT[0]
        self.listbox.configure(bg=bg, fg=fg, selectbackground=select_bg,
                               selectforeground=select_fg)
        self.border.configure(border_color=border_color, fg_color=bg)

    def set_on_change(self, callback):
        self._on_change_cb = callback
        self.listbox.bind('<<ListboxSelect>>', self._notify_change)

    def _notify_change(self, event=None):
        if self._updating or not self._on_change_cb:
            return
        self._on_change_cb()

    @staticmethod
    def extract_raw_name(display_text: str) -> str:
        return display_text.split('(')[0].strip()
