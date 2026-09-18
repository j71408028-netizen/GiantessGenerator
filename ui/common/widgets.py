import tkinter as tk
import customtkinter
import customtkinter as ctk
import sys
import difflib

from ui.common import fonts as ui_fonts
from ui.common.theme import (
    WIDGET_BG, WIDGET_PANEL_BG, WIDGET_BORDER, WIDGET_BORDER_STRONG,
    SEG_HOVER, WIDGET_HOVER, WIDGET_MENU_HOVER, WIDGET_TEXT,
    WIDGET_TEXT_SOFT, WIDGET_TEXT_MUTED, WIDGET_TITLE, WIDGET_CARD_HOVER_BORDER,
    SEG_SELECTED_BG, SEG_SELECTED_HOVER, SEG_SELECTED_TEXT, SEG_TRACK_BG,
    SEG_TRACK_BORDER, SEG_UNSELECTED_TEXT,
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


class CycleOptionButton(customtkinter.CTkFrame):
    """固定档位的点击轮换控件（替代选项数量不会拓展的下拉框）。

    左键切到下一档、右键切回上一档，到头循环；右缘画一枚指向右的单箭头，
    提示点击推进到下一档。文字与箭头都直接绘制在控件自身的圆角画布上，
    与底色浑然一体：不引入子控件，因此悬停换色时同步重绘、无残色，也不
    会盖住边框线。
    对外接口与 CTkOptionMenu 对齐：set() / get() / configure(values=, state=)。
    """

    # 箭头图案的单元盒尺寸（逻辑像素），坐标按此盒内设计
    _MARKER_W = 15
    _MARKER_H = 15
    #: 图案距控件右缘的留白
    _MARKER_MARGIN = 8
    #: 文字区右缘 = 右缘留白 + 图案宽 + 缝隙
    _TEXT_RESERVE = _MARKER_MARGIN + _MARKER_W + 3
    #: 文字区左缘留白
    _TEXT_PAD_LEFT = 8

    def __init__(self, master, values=None, command=None, *, variable=None,
                 width=120, height=27, corner_radius=7, border_width=1,
                 font=None, fg_color=None, border_color=None, hover_color=None,
                 text_color=None, text_color_disabled=None, marker_color=None,
                 **kwargs):
        fg_color = WIDGET_PANEL_BG if fg_color is None else fg_color
        super().__init__(master, width=width, height=height,
                         corner_radius=corner_radius, border_width=border_width,
                         fg_color=fg_color,
                         border_color=WIDGET_BORDER if border_color is None else border_color,
                         **kwargs)
        self._values = list(values or [])
        self._command_cb = command
        self._variable = variable
        self._font = font or ui_fonts.ui_font(13)
        # 注意：不能用 _fg_color/_hover_color——CTkFrame 基类的 configure 会把
        # 同名属性改写成"当前底色"，悬停后常态色就被吞掉了，离开时无法恢复
        self._normal_fg = fg_color
        self._hover_fg = WIDGET_HOVER if hover_color is None else hover_color
        self._text_color = WIDGET_TEXT if text_color is None else text_color
        self._text_color_disabled = (
            WIDGET_TEXT_MUTED if text_color_disabled is None else text_color_disabled)
        self._marker_color = WIDGET_TEXT_SOFT if marker_color is None else marker_color
        self._state = "normal"
        self._current = self._values[0] if self._values else ""
        if self._variable is not None:
            # 变量里已有合法档位时以它为准，否则把档位回写到变量
            current = self._variable.get()
            if current in self._values:
                self._current = current
            elif self._current:
                self._variable.set(self._current)

        self.bind("<Enter>", self._on_enter, add="+")
        self.bind("<Leave>", self._on_leave, add="+")
        self.bind("<Button-1>", lambda e: self._step(1), add="+")
        self.bind("<Button-3>", lambda e: self._step(-1), add="+")

        # 基类首绘发生在子类属性就绪之前，且尺寸未变时不再触发重绘，
        # 必须在此显式补一次，否则文字与箭头初始不可见
        self._draw()

    # ── 对外接口 ──
    def set(self, value):
        """切换显示值（不触发 command，与 CTkOptionMenu 一致）。"""
        if value not in self._values:
            return
        self._current = value
        if self._variable is not None:
            self._variable.set(value)
        self._draw_text()

    def get(self):
        return self._current

    def configure(self, **kwargs):
        if "values" in kwargs:
            self._values = list(kwargs.pop("values") or [])
            if self._current not in self._values:
                self.set(self._values[0] if self._values else "")
        if "state" in kwargs:
            self._state = kwargs.pop("state")
            self._refresh_state()
        if "fg_color" in kwargs:
            self._normal_fg = kwargs["fg_color"]
        if "hover_color" in kwargs:
            self._hover_fg = kwargs["hover_color"]
        if "marker_color" in kwargs:
            self._marker_color = kwargs.pop("marker_color")
            self._draw_marker()
        return super().configure(**kwargs)

    def cget(self, attribute_name):
        if attribute_name == "state":
            return self._state
        if attribute_name == "values":
            return list(self._values)
        return super().cget(attribute_name)

    # ── 内部实现 ──
    def _step(self, delta):
        if self._state != "normal" or not self._values:
            return
        idx = self._values.index(self._current) if self._current in self._values else -1
        self.set(self._values[(idx + delta) % len(self._values)])
        if self._command_cb is not None:
            self._command_cb(self._current)

    def _refresh_state(self):
        if self._state != "normal":
            self._on_leave()
        self._draw_text()
        self._draw_marker()

    def _on_enter(self, event=None):
        if self._state != "normal":
            return
        customtkinter.CTkFrame.configure(self, fg_color=self._hover_fg)

    def _on_leave(self, event=None):
        customtkinter.CTkFrame.configure(self, fg_color=self._normal_fg)

    def _draw(self, no_color_updates=False):
        # 基类每次重绘都会重建圆角底与边框，画布内容必须在其后重画才能保持可见
        super()._draw(no_color_updates)
        self._draw_text()
        self._draw_marker()

    def _draw_text(self):
        """把当前档位文字画在控件画布上（子控件式 label 会残留悬停底色）。"""
        if not hasattr(self, "_text_color"):
            return  # super().__init__ 期间的首绘，属性尚未就绪
        canvas = getattr(self, "_canvas", None)
        if canvas is None or not canvas.winfo_exists():
            return
        disabled = self._state != "normal"
        color = self._apply_appearance_mode(
            self._text_color_disabled if disabled else self._text_color)
        scale = self._apply_widget_scaling
        # 文字居中于左侧文字区（右缘为箭头让位）
        cx = scale((self._TEXT_PAD_LEFT + self._current_width - self._TEXT_RESERVE) / 2)
        canvas.delete("cycle_text")
        if self._current:
            canvas.create_text(cx, scale(self._current_height / 2),
                               text=self._current, font=self._font, fill=color,
                               anchor="center", tags="cycle_text")

    def _draw_marker(self):
        """在控件画布右缘绘制指向右的单箭头，提示点击推进到下一档。"""
        if not hasattr(self, "_marker_color"):
            return  # super().__init__ 期间的首绘，属性尚未就绪
        canvas = getattr(self, "_canvas", None)
        if canvas is None or not canvas.winfo_exists():
            return
        disabled = self._state != "normal"
        color = self._apply_appearance_mode(
            self._text_color_disabled if disabled else self._marker_color)
        canvas.delete("cycle_marker")
        scale = self._apply_widget_scaling
        # 图案盒在控件内右缘留白、垂直居中
        x0 = scale(self._current_width - self._MARKER_MARGIN - self._MARKER_W)
        y0 = scale((self._current_height - self._MARKER_H) / 2)

        def pt(u, v):
            return (x0 + scale(u), y0 + scale(v))

        # 带箭杆的单箭头，盒内垂直居中
        canvas.create_polygon(
            pt(2, 5.9), pt(9.5, 5.9), pt(9.5, 4.4), pt(13.5, 7.5),
            pt(9.5, 10.6), pt(9.5, 9.1), pt(2, 9.1),
            fill=color, outline="", tags="cycle_marker")


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
        self.corner = 6 if frame_corner_radius else 0
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
            fg_color = button_color = WIDGET_PANEL_BG[1]
            hover_color = WIDGET_HOVER[1]
            scrollbar_color = WIDGET_HOVER[1]
            scrollbar_hover_color = WIDGET_MENU_HOVER[1]
            border_color = WIDGET_MENU_HOVER[1]
            text_color = WIDGET_TEXT[1]
        else:
            fg_color = button_color = WIDGET_PANEL_BG[0]
            hover_color = WIDGET_HOVER[0]
            scrollbar_color = WIDGET_HOVER[0]
            scrollbar_hover_color = WIDGET_MENU_HOVER[0]
            border_color = WIDGET_MENU_HOVER[0]
            text_color = WIDGET_TEXT[0]

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


class ScrollableComboBox(customtkinter.CTkComboBox):
    """带统一风格下拉列表的组合框（选项数量会变化的场景替代 OptionMenu）。

    下拉列表交给 CTkScrollableDropdownFrame 渲染；CTkComboBox 自带的原生下拉
    始终保持空列表，避免两套弹层同时弹出。state 传入 "normal" 一律按
    "readonly" 处理，禁止手工输入与下拉框内容不一致的文本。
    """

    def __init__(self, master, values=None, command=None, *, variable=None,
                 width=120, height=27, corner_radius=7, border_width=1, font=None,
                 fg_color=None, border_color=None, button_color=None,
                 button_hover_color=None, text_color=None,
                 dropdown_fg_color=None, dropdown_hover_color=None,
                 dropdown_text_color=None, dropdown_font=None,
                 dropdown_height=160, dropdown_button_height=28, **kwargs):
        font = font or ui_fonts.ui_font(13)
        fg_color = WIDGET_PANEL_BG if fg_color is None else fg_color
        border_color = WIDGET_BORDER if border_color is None else border_color
        dropdown_fg_color = WIDGET_PANEL_BG if dropdown_fg_color is None else dropdown_fg_color
        dropdown_hover_color = WIDGET_HOVER if dropdown_hover_color is None else dropdown_hover_color

        super().__init__(
            master, values=[], width=width, height=height,
            corner_radius=corner_radius, border_width=border_width,
            state="readonly", font=font, justify="left",
            variable=variable, command=command,
            fg_color=fg_color, border_color=border_color,
            # CTkComboBox 右段边框也用 button_color 绘制，默认与边框同色外框才均匀；
            # 悬停取更醒目的边框色，保持"按钮属于边框"的一体观感
            button_color=border_color if button_color is None else button_color,
            button_hover_color=(
                WIDGET_BORDER_STRONG if button_hover_color is None else button_hover_color),
            text_color=WIDGET_TEXT if text_color is None else text_color,
            **kwargs
        )

        self._combo_values = list(values or [])
        self._dropdown = CTkScrollableDropdownFrame(
            attach=self, values=self._combo_values, command=self._on_dropdown_select,
            height=dropdown_height, button_height=dropdown_button_height,
            fg_color=dropdown_fg_color, button_color=dropdown_fg_color,
            hover_color=dropdown_hover_color,
            text_color=WIDGET_TEXT if dropdown_text_color is None else dropdown_text_color,
            scrollbar_button_color=dropdown_hover_color,
            scrollbar_button_hover_color=dropdown_hover_color,
            frame_border_color=border_color, frame_border_width=1,
            justify="left", font=dropdown_font or font,
        )
        # CTkComboBox 只对自身名为 ctkcombobox 的控件绑定箭头区域，子类需补绑
        self._canvas.tag_bind("right_parts", "<Button-1>",
                              lambda e: self._dropdown._iconify())
        self._canvas.tag_bind("dropdown_arrow", "<Button-1>",
                              lambda e: self._dropdown._iconify())

    def _on_dropdown_select(self, value):
        """列表选中后转发给外部 command（文本已由 attach.set() 写入）。"""
        if self._command is not None:
            self._command(value)

    def configure(self, **kwargs):
        if "values" in kwargs:
            self._combo_values = list(kwargs.pop("values") or [])
            self._dropdown.configure(values=self._combo_values)
        if "state" in kwargs:
            state = kwargs.pop("state")
            kwargs["state"] = "readonly" if state == "normal" else state
        return super().configure(**kwargs)

    def cget(self, attribute_name):
        if attribute_name == "values":
            return list(self._combo_values)
        return super().cget(attribute_name)


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
            border_color=WIDGET_BORDER,
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
                text_color=WIDGET_TEXT,
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
                text_color=WIDGET_TEXT,
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
                    text_color=WIDGET_TEXT
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
                    text_color=detail_color or WIDGET_TEXT_SOFT,
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
        self.configure(fg_color=WIDGET_HOVER)
        if self._gold_hover:
            self.configure(border_color=WIDGET_CARD_HOVER_BORDER)
        if self._on_enter_cb:
            self._on_enter_cb()

    def _on_leave(self, event=None):
        self.configure(fg_color="transparent")
        if self._gold_hover:
            self.configure(border_color=WIDGET_BORDER)
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
            text_color=WIDGET_TITLE,
            hover_color=WIDGET_HOVER,
            width=self.width,
            border_width=2,
            border_color=WIDGET_BORDER,
            corner_radius=8,
            font=ui_fonts.ui_font(12, "bold")
        )

        self.body = customtkinter.CTkFrame(
            parent,
            fg_color=WIDGET_BG,
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
                                                  font=ui_fonts.ui_font(12, "bold"),
                                                  text_color=WIDGET_TEXT_SOFT)
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
            exportselection=False, font=ui_fonts.ui_font(13),
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
            fg_color="transparent", text_color=WIDGET_TEXT_MUTED,
            hover_color=WIDGET_HOVER, border_width=1,
            border_color=WIDGET_BORDER_STRONG, corner_radius=8,
            font=ui_fonts.ui_font(11)
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
            bg, fg = WIDGET_PANEL_BG[1], WIDGET_TEXT[1]
            select_bg, select_fg = WIDGET_HOVER[1], WIDGET_TITLE[1]
            border_color = WIDGET_BORDER_STRONG[1]
        else:
            bg, fg = WIDGET_PANEL_BG[0], WIDGET_TEXT[0]
            select_bg, select_fg = WIDGET_HOVER[0], WIDGET_TITLE[0]
            border_color = WIDGET_BORDER_STRONG[0]
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
