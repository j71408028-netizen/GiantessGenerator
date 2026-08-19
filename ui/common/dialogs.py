import ctypes
import os
import sys
import tempfile
import tkinter as tk

import customtkinter as ctk
from PIL import Image, ImageTk

from services.image_service import ImageService
from paths import assets_dir
from ui.common import fonts as ui_fonts
from ui.common.theme import (
    TEXT, DLG_BORDER, DLG_HOVER, DLG_BTN_PRIMARY, DLG_BTN_PRIMARY_HOVER, DLG_FG,
    SOFT, HARD_TITLE, PLACEHOLDER, TEXT_WHITE,
    PNL_BG, BORDER_ALT, HOVER_ALT,
    STATUS_OK, OK_BTN_HOVER, STATUS_ERR,
    PROGRESS_BTN, PROGRESS_BTN_HOVER,
    CANVAS_BG, CANVAS_BORDER, OVERLAY, GOLD_OUTLINE,
)


# ── 常量 ──
_ASSETS_DIR = assets_dir()
_MSG_ICON_IMAGES = {
    "info":     "ok.png",
    "warning":  "warning.png",
    "error":    "error.png",
    "question": "askyesno.png",
}
_BUTTON_OK = "确定"
_BUTTON_YES = "是"
_BUTTON_NO = "否"
_BUTTON_CANCEL = "取消"


def _resolve_parent(parent):
    """将 parent 解析为顶层窗口；未指定时使用默认根窗口。"""
    if parent is not None:
        try:
            top = parent.winfo_toplevel()
            if top.winfo_exists():
                return top
        except (tk.TclError, AttributeError):
            pass
    root = getattr(tk, "_default_root", None)
    if root is not None:
        try:
            if root.winfo_exists():
                return root
        except tk.TclError:
            pass
    return None


class BaseDialog(ctk.CTkToplevel):
    """对话框基类：
    - 提供可靠的主窗口居中（customtkinter 初始化时会 withdraw 窗口，
      此时 winfo_width/height 返回不可靠的默认值，需基于显式设置的几何尺寸计算）；
    - 深色模式下将 Windows 标题栏设为深色（transient() 会重置 DWM 属性，
      因此需在窗口真正映射后再重新应用）；
    - 统一模态弹出行为（transient/grab/居中/等待关闭）与安全关闭流程；
    - 统一背景色；corner_radius 供子类内部部件（按钮/框架）使用，
      操作系统窗口框架本身无法圆角。
    """

    UI_FONT = ui_fonts.ui_font(11)
    UI_FONT_SMALL = ui_fonts.ui_font(10)
    UI_FONT_LARGE = ui_fonts.ui_font(12)
    UI_FONT_BOLD = ui_fonts.ui_font(11, "bold")
    SECTION_FONT = ui_fonts.ui_font(12, "bold")

    def __init__(self, parent, fg_color=None, corner_radius=8, *args, **kwargs):
        if fg_color is None:
            fg_color = DLG_FG
        super().__init__(parent.winfo_toplevel(), fg_color=fg_color)
        self._dialog_geometry_size = None  # 显式设置的窗口逻辑尺寸 (宽, 高)
        self._center_reference = parent.winfo_toplevel()
        self._titlebar_theme_applied = False
        self.corner_radius = corner_radius
        # 初始化期间保持隐藏：避免在默认位置以浅色标题栏/tkinter 默认图标闪现，
        # 待子类构建完成、居中并应用深色标题栏后再显示（见 _center_dialog）
        self.withdraw()
        self._apply_icon()
        # 窗口真正映射后再应用标题栏主题（transient() 会重置 DWM 属性）
        self.bind("<Map>", self._on_dialog_map)

    def geometry(self, geometry_string=None):
        if geometry_string is not None:
            width, height, _x, _y = self._parse_geometry_string(geometry_string)
            if width is not None and height is not None:
                self._dialog_geometry_size = (width, height)
        return super().geometry(geometry_string)

    def resizable(self, width=None, height=None):
        # 绕过 CTkToplevel.resizable：其会调度 after(10, _windows_set_titlebar_color)，
        # 该调度会先 withdraw() 再于 5ms 后重新显示窗口，导致已显示的对话框闪烁。
        # 深色标题栏由 BaseDialog 在显示前/映射时统一处理，此处仅同步重应用一次，
        # 以覆盖 resizable 改变窗口样式可能带来的 DWM 属性重置。
        result = tk.Toplevel.resizable(self, width, height)
        self._apply_titlebar_theme()
        return result

    def _center_dialog(self, parent=None):
        """以主窗口中心放置对话框，并在此前保持隐藏以避免闪烁。

        应用深色标题栏后再显示窗口；支持窗口仍处于 withdraw 状态。
        """
        ref = parent.winfo_toplevel() if parent is not None else self._center_reference
        self.update_idletasks()
        if self._dialog_geometry_size:
            s = self._get_window_scaling()
            w = round(self._dialog_geometry_size[0] * s)
            h = round(self._dialog_geometry_size[1] * s)
        else:
            w = self.winfo_reqwidth()
            h = self.winfo_reqheight()
        x = ref.winfo_rootx() + (ref.winfo_width() - w) // 2
        y = ref.winfo_rooty() + (ref.winfo_height() - h) // 2
        self.geometry(f"+{x}+{y}")
        # 显示前先应用深色标题栏，再取消隐藏（<Map> 事件会再次兜底）
        self._apply_titlebar_theme()
        self.deiconify()

    def _apply_titlebar_theme(self):
        """按当前外观模式设置 Windows 标题栏颜色。"""
        if not sys.platform.startswith("win"):
            return
        try:
            dark = ctk.get_appearance_mode().lower() == "dark"
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            if not hwnd:
                return
            value = ctypes.c_int(1 if dark else 0)
            # Win10 2004+ 使用 20，更早版本使用 19
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 20, ctypes.byref(value), ctypes.sizeof(value)) != 0:
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 19, ctypes.byref(value), ctypes.sizeof(value))
        except Exception:
            pass

    def _on_dialog_map(self, _event=None):
        if not self._titlebar_theme_applied:
            self._titlebar_theme_applied = True
            self._apply_titlebar_theme()

    def _apply_icon(self):
        """创建时立即应用应用图标（assets/icon.ico），避免闪现 tkinter 默认图标。

        Windows 下无法通过 wm iconbitmap() 读取已有图标，故直接设置应用自带的
        icon.ico；若缺失则回退到 customtkinter 默认图标（与 CTkToplevel 延迟
        设置的效果一致，但不会出现约 200ms 的默认图标闪烁）。
        """
        if not sys.platform.startswith("win"):
            return
        try:
            icon_path = os.path.join(_ASSETS_DIR, "icon.ico")
            if not os.path.exists(icon_path):
                icon_path = os.path.join(
                    os.path.dirname(ctk.__file__),
                    "assets", "icons", "CustomTkinter_icon_Windows.ico")
            if not os.path.exists(icon_path):
                return
            self._dialog_icon_locked = True
            self.wm_iconbitmap(icon_path)
        except (tk.TclError, AttributeError):
            self._dialog_icon_locked = False

    def iconbitmap(self, bitmap=None, default=None):
        # 阻止 CTkToplevel 延迟设置的默认图标覆盖已在 _apply_icon 中应用的图标
        if getattr(self, "_dialog_icon_locked", False):
            return
        super().iconbitmap(bitmap, default)

    def _show_modal(self, focus_widget=None):
        """统一模态弹出：置顶于父窗口、独占输入、居中显示并等待关闭。

        focus_widget 指定后聚焦该部件，否则聚焦窗口本身。
        """
        self.transient(self._center_reference)
        self.grab_set()
        self._center_dialog()
        (focus_widget if focus_widget is not None else self).focus_force()
        self.wait_window()

    def _make_std_button(self, parent, text, primary, command=None):
        """标准按钮：主按钮（填充色）或次按钮（透明描边）。"""
        if primary:
            return ctk.CTkButton(
                parent, text=text, width=92, height=32,
                corner_radius=self.corner_radius,
                fg_color=DLG_BTN_PRIMARY, hover_color=DLG_BTN_PRIMARY_HOVER,
                text_color=TEXT_WHITE, font=ui_fonts.ui_font(13),
                command=command)
        return ctk.CTkButton(
            parent, text=text, width=92, height=32,
            corner_radius=self.corner_radius,
            fg_color="transparent", text_color=TEXT, hover_color=DLG_HOVER,
            border_width=1, border_color=DLG_BORDER, font=ui_fonts.ui_font(13),
            command=command)

    def _close(self):
        """统一关闭流程：释放鼠标抓取并先隐藏窗口再销毁，避免关闭时残留图像闪烁。"""
        if getattr(self, '_closing', False):
            return
        self._closing = True
        try:
            self.grab_release()
        except tk.TclError:
            pass
        try:
            self.withdraw()
            self.update_idletasks()
        except tk.TclError:
            pass
        try:
            self.destroy()
        except tk.TclError:
            pass


class InputDialog(BaseDialog):
    """基于 BaseDialog 的中文输入对话框，居中于父窗口并自动适配深色标题栏。
    注：CTkInputDialog 本身不会居中，故直接基于 BaseDialog 实现。
    """

    def __init__(self, parent, title="输入", prompt="请输入："):
        super().__init__(parent)
        self.title(title)
        self.result = None
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        ctk.CTkLabel(
            self, text=prompt, wraplength=300, font=self.UI_FONT,
            text_color=TEXT).pack(padx=20, pady=(20, 8))

        self._entry = ctk.CTkEntry(
            self, width=230, height=28, font=self.UI_FONT,
            fg_color=PNL_BG,
            border_color=BORDER_ALT,
            text_color=TEXT)
        self._entry.pack(padx=20, pady=(0, 16))

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=(0, 16))
        self._make_std_button(btn_frame, "确定", True, self._ok).pack(side='left', padx=6)
        self._make_std_button(btn_frame, "取消", False, self._cancel).pack(side='left', padx=6)

        self.bind("<Return>", self._ok)
        self.bind("<Escape>", self._cancel)

        self._show_modal(focus_widget=self._entry)

    def _ok(self, _event=None):
        self.result = self._entry.get()
        self._close()

    def _cancel(self):
        self.result = None
        self._close()

    def get_input(self):
        return self.result


class ImageCropDialog(BaseDialog):
    MODE_CHARACTER = "character"
    MODE_BACKGROUND = "background"
    DIR_PORTRAIT = "portrait"
    DIR_LANDSCAPE = "landscape"

    RATIO_MIN = 0.5
    RATIO_MAX = 1.5

    IMG_MAX_W = 450
    IMG_H_TALL = 450   # 纵向很高图片使用的较大高度
    IMG_H_WIDE = 300   # 横图使用的较小高度
    CTRL_W = 300       # 竖长图时侧边控件区宽度
    CTRL_H = 130       # 横图时底部控件区高度

    def __init__(self, parent, image_path: str, mode: str = MODE_CHARACTER):
        super().__init__(parent)
        self.title("裁剪图片")

        self._mode = mode
        self._result_path = None
        self._image_path = image_path
        self._dragging = False
        self._drag_start = 0
        self._drag_start_off = 0.0

        self._load_image()
        self._build_ui()
        self._update_overlay()

        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        self._show_modal()

    def _load_image(self):
        raw = Image.open(self._image_path)
        # 保留 PNG 的透明通道
        self._has_alpha = raw.mode in ('RGBA', 'LA', 'PA') or (
            raw.mode == 'P' and 'transparency' in raw.info)
        self._original = raw.convert('RGBA') if self._has_alpha else raw.convert('RGB')
        ow, oh = self._original.size
        self._orig_ratio = ow / oh

        # 两种预设：宽高比 < 2:3 的竖长图使用较大高度，其余使用较小高度
        if self._orig_ratio < 2.0 / 3.0:
            self._ctrl_bottom = False   # 控件在图片右侧
            target_h = self.IMG_H_TALL
        else:
            self._ctrl_bottom = True    # 控件在图片下方
            target_h = self.IMG_H_WIDE
        target_w = int(round(target_h * self._orig_ratio))
        if target_w > self.IMG_MAX_W:
            target_w = self.IMG_MAX_W
            target_h = int(round(self.IMG_MAX_W / self._orig_ratio))
        target_w = max(1, target_w)
        target_h = max(1, target_h)

        # 按窗口缩放系数放大显示图，使图片渲染尺寸与窗口保持一致
        s = self._get_window_scaling()
        self._window_scaling = s
        target_w = int(round(target_w * s))
        target_h = int(round(target_h * s))

        self._display = self._original.resize((target_w, target_h),
                                              Image.Resampling.LANCZOS)
        self._scale = target_w / ow
        self._display_tk = self._to_photo(self._display)

        if self._mode == self.MODE_BACKGROUND:
            # 固定比例：background 为 16:9（横版背景）
            self._ratio = 16.0 / 9.0
            self._can_restore = False
        else:
            # 原比例落在合法裁剪范围则初始使用原比例，并提供“还原”功能
            self._can_restore = self.RATIO_MIN <= self._orig_ratio <= self.RATIO_MAX
            self._initial_ratio = self._orig_ratio if self._can_restore else 1.0
            self._ratio_var = tk.DoubleVar(value=self._initial_ratio)
            self._ratio_var.trace_add('write', lambda *_: self._update_overlay())

        self._crop_dir = self.DIR_PORTRAIT

    def _to_photo(self, img):
        """有透明通道时先合到画布底色上再显示"""
        if self._has_alpha:
            disp = img.convert('RGBA')
            bg = Image.new('RGBA', disp.size, (45, 45, 45, 255))
            disp = Image.alpha_composite(bg, disp).convert('RGB')
        else:
            disp = img.convert('RGB')
        return ImageTk.PhotoImage(disp)

    def _build_ui(self):
        dw, dh = self._display.size
        canvas_w, canvas_h = dw, dh
        pad = 10
        gap = 14
        s = self._window_scaling

        if self._ctrl_bottom:
            # 横图：图像在上、控件在下，两者横向居中，纵向间隔稍大
            content_w = max(dw, self.CTRL_W * s)
            win_w_phys = content_w + 2 * pad * s
            win_h_phys = dh + gap * s + self.CTRL_H * s + 2 * pad * s + 10
            slider_w = self.CTRL_W - 130
            slider_w = max(170, slider_w)
        else:
            # 竖长图：控件在图片右侧，使用固定控件列宽
            win_w_phys = dw + 8 * s + self.CTRL_W * s + 2 * pad * s
            win_h_phys = dh + 2 * pad * s + 10
            slider_w = self.CTRL_W - 130
            slider_w = max(170, slider_w)

        win_w = max(320, int(win_w_phys / s))
        win_h = max(240, int(win_h_phys / s))
        self.geometry(f"{win_w}x{win_h}")

        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill='both', expand=True, padx=pad, pady=pad)

        cv_frame = ctk.CTkFrame(main, fg_color="transparent")
        ctrl = self._build_controls(main, self._ctrl_bottom, slider_w)

        if self._ctrl_bottom:
            cv_frame.pack(anchor='n')
            ctrl.pack(anchor='n', pady=(gap, 0))
        else:
            cv_frame.pack(side='left', anchor='nw', padx=(0, 8))
            ctrl.pack(side='right', fill='y', padx=(8, 0))

        self._canvas = tk.Canvas(
            cv_frame, width=canvas_w, height=canvas_h,
            highlightthickness=1, highlightbackground=CANVAS_BORDER,
            bg=CANVAS_BG, cursor="hand2"
        )
        self._canvas.pack()
        cx, cy = canvas_w // 2, canvas_h // 2
        self._canvas.create_image(cx, cy, image=self._display_tk, anchor='center', tag='img')
        self._canvas.bind("<ButtonPress-1>", self._on_drag_start)
        self._canvas.bind("<B1-Motion>", self._on_drag_move)
        self._canvas.bind("<ButtonRelease-1>", self._on_drag_end)

    def _build_controls(self, parent, bottom=False, slider_w=250):
        ctrl = ctk.CTkFrame(parent, fg_color="transparent")
        ctrl.configure(width=self.CTRL_W)
        ctrl.pack_propagate(False)

        # ── Row 1: ratio slider ──
        if self._mode == self.MODE_CHARACTER:
            row1 = ctk.CTkFrame(ctrl, fg_color="transparent")
            row1.pack(fill='x', pady=(0, 8))
            ctk.CTkLabel(row1, text="比例", width=56,
                         font=self.UI_FONT,
                         text_color=HARD_TITLE).pack(side='left')

            # 横向范围从 0.5 到 1.5，纵向固定为 1
            self._ratio_slider = ctk.CTkSlider(
                row1, from_=self.RATIO_MIN, to=self.RATIO_MAX, number_of_steps=100,
                variable=self._ratio_var, width=slider_w,
                command=self._on_slider_change,
                button_color=PROGRESS_BTN,
                button_hover_color=PROGRESS_BTN_HOVER,
                progress_color=PROGRESS_BTN,
                fg_color=HOVER_ALT
            )
            self._ratio_slider.pack(side='left', padx=6)

            # 两位小数格式化显示（横向:纵向）
            self._ratio_label = ctk.CTkLabel(
                row1, text="1.00:1", font=self.UI_FONT,
                text_color=SOFT, width=56
            )
            self._ratio_label.pack(side='left')

        # ── Row 2: offset slider ──
        row2 = ctk.CTkFrame(ctrl, fg_color="transparent")
        row2.pack(fill='x')

        self._axis_label_widget = ctk.CTkLabel(
            row2, text="纵向位置",
            font=self.UI_FONT, width=56, anchor="w",
            text_color=HARD_TITLE
        )
        self._axis_label_widget.pack(side='left')

        self._offset_var = tk.DoubleVar(value=0.5)
        self._offset_var.trace_add('write', lambda *_: self._update_overlay())
        self._offset_slider = ctk.CTkSlider(
            row2, from_=0.0, to=1.0, number_of_steps=100,
            variable=self._offset_var, width=slider_w,
            command=self._on_slider_change,
            button_color=PROGRESS_BTN,
            button_hover_color=PROGRESS_BTN_HOVER,
            progress_color=PROGRESS_BTN,
            fg_color=HOVER_ALT
        )
        self._offset_slider.pack(side='left', padx=6)
        self._offset_label = ctk.CTkLabel(
            row2, text="50%", font=self.UI_FONT,
            text_color=SOFT, width=40
        )
        self._offset_label.pack(side='left')

        if self._mode == self.MODE_BACKGROUND:
            ctk.CTkLabel(ctrl, text="裁剪比例固定为 16:9",
                         font=self.UI_FONT_SMALL,
                         text_color=PLACEHOLDER).pack(anchor='w', pady=(6, 0))

        # ── Buttons ──
        btn_frame = ctk.CTkFrame(ctrl, fg_color="transparent")
        btn_frame.pack(fill='x', pady=(14, 0))
        if self._mode == self.MODE_CHARACTER and self._can_restore:
            ctk.CTkButton(
                btn_frame, text="还原", width=80, height=28,
                fg_color="transparent",
                text_color=HARD_TITLE,
                hover_color=DLG_HOVER,
                border_width=1, border_color=DLG_BORDER,
                corner_radius=self.corner_radius, font=self.UI_FONT,
                command=self._restore
            ).pack(side='left', padx=5)
        ctk.CTkButton(
            btn_frame, text="确定", width=80, height=28,
            fg_color=STATUS_OK,
            text_color=TEXT_WHITE,
            hover_color=OK_BTN_HOVER,
            corner_radius=self.corner_radius, font=self.UI_FONT,
            command=self._confirm
        ).pack(side='right', padx=5)
        ctk.CTkButton(
            btn_frame, text="取消", width=80, height=28,
            fg_color="transparent",
            text_color=STATUS_ERR,
            hover_color=DLG_HOVER,
            border_width=1, border_color=DLG_BORDER,
            corner_radius=self.corner_radius, font=self.UI_FONT,
            command=self._cancel
        ).pack(side='right', padx=5)

        return ctrl

    def _calc_crop_rect(self):
        dw, dh = self._display.size
        r = self._ratio if self._mode == self.MODE_BACKGROUND else self._ratio_var.get()
        r = max(0.1, min(3.0, r))

        # r 即为 W/H
        cw = dw
        ch = cw / r
        if ch > dh:
            ch = dh
            cw = ch * r

        max_off_x = dw - cw
        max_off_y = dh - ch

        # 依据余量较大的维度动态调整偏移方向
        if max_off_y > max_off_x:
            self._crop_dir = self.DIR_PORTRAIT
            x = (dw - cw) / 2
            y = max_off_y * self._offset_var.get()
        else:
            self._crop_dir = self.DIR_LANDSCAPE
            x = max_off_x * self._offset_var.get()
            y = (dh - ch) / 2

        return int(x), int(y), int(cw), int(ch)

    def _update_overlay(self):
        self._canvas.delete('overlay')
        dw, dh = self._display.size
        rx, ry, rw, rh = self._calc_crop_rect()

        overlay_color = OVERLAY
        if ry > 0:
            self._canvas.create_rectangle(0, 0, dw, ry, fill=overlay_color,
                                          stipple='gray25', tag='overlay', outline='')
        if ry + rh < dh:
            self._canvas.create_rectangle(0, ry + rh, dw, dh, fill=overlay_color,
                                          stipple='gray25', tag='overlay', outline='')
        if rx > 0:
            self._canvas.create_rectangle(0, ry, rx, ry + rh, fill=overlay_color,
                                          stipple='gray25', tag='overlay', outline='')
        if rx + rw < dw:
            self._canvas.create_rectangle(rx + rw, ry, dw, ry + rh, fill=overlay_color,
                                          stipple='gray25', tag='overlay', outline='')

        self._canvas.create_rectangle(rx, ry, rx + rw, ry + rh,
                                      outline=GOLD_OUTLINE, width=2, dash=(6, 3),
                                      tag='overlay')

        # 更新横向与纵向比例提示标签（横向保留 2 位小数）
        if self._mode == self.MODE_CHARACTER:
            v = self._ratio_var.get()
            self._ratio_label.configure(text=f"{v:.2f}:1")

        # 动态切换二次移动位置的提示词
        if hasattr(self, '_axis_label_widget'):
            lbl = "纵向位置" if self._crop_dir == self.DIR_PORTRAIT else "横向位置"
            self._axis_label_widget.configure(text=lbl)

        self._offset_label.configure(text=f"{int(self._offset_var.get() * 100)}%")

    def _on_slider_change(self, _=None):
        self._update_overlay()

    def _restore(self):
        """还原到初始状态（原图片比例、居中）"""
        self._ratio_var.set(self._initial_ratio)
        self._offset_var.set(0.5)

    def _on_drag_start(self, event):
        rx, ry, rw, rh = self._calc_crop_rect()
        if rx <= event.x <= rx + rw and ry <= event.y <= ry + rh:
            self._dragging = True
            self._drag_start = event.x if self._crop_dir == self.DIR_LANDSCAPE else event.y
            self._drag_start_off = self._offset_var.get()

    def _on_drag_move(self, event):
        if not self._dragging:
            return
        dw, dh = self._display.size
        r = self._ratio if self._mode == self.MODE_BACKGROUND else self._ratio_var.get()

        cw = dw
        ch = cw / r
        if ch > dh:
            ch = dh
            cw = ch * r

        if self._crop_dir == self.DIR_PORTRAIT:
            max_off = dh - ch
            if max_off <= 0:
                return
            dy = event.y - self._drag_start
            new_off = self._drag_start_off + dy / max_off
        else:
            max_off = dw - cw
            if max_off <= 0:
                return
            dx = event.x - self._drag_start
            new_off = self._drag_start_off + dx / max_off

        new_off = max(0.0, min(1.0, new_off))
        self._offset_var.set(new_off)
        self._offset_slider.set(new_off)

    def _on_drag_end(self, _=None):
        self._dragging = False

    def _confirm(self):
        r = self._ratio if self._mode == self.MODE_BACKGROUND else self._ratio_var.get()
        cropped = ImageService.crop_aspect(self._original, r, self._offset_var.get())
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
        cropped.save(tmp, format='PNG')
        tmp.close()
        self._result_path = tmp.name
        self._close()

    def _cancel(self):
        self._result_path = None
        self._close()

    def get_cropped_path(self):
        return self._result_path


class _MsgBox(BaseDialog):
    """
    极简消息框：左侧带透明度的 PNG 图标，右侧文本与中文按钮。
    图标由对话框内部呈现。
    """

    _ICON_SCALE = 116  # 图标显示高度上限

    def __init__(self, parent, title, message, icon, options):
        super().__init__(_resolve_parent(parent))
        self.title(title)
        self.resizable(False, False)
        self.result = None
        self._closed = False

        self.protocol("WM_DELETE_WINDOW", lambda: self._close(None))

        self._build(message, icon, options)
        self._show_modal()

    def _load_icon_image(self, icon):
        """加载 assets 中对应图标类型的 PNG（保留透明通道）并缩放至合适尺寸。"""
        img_path = os.path.join(
            _ASSETS_DIR, _MSG_ICON_IMAGES.get(icon, _MSG_ICON_IMAGES["info"]))
        if not os.path.exists(img_path):
            return None
        try:
            img = Image.open(img_path).convert("RGBA")
            scale = self._ICON_SCALE / max(img.size)
            size = (max(1, int(img.size[0] * scale)),
                    max(1, int(img.size[1] * scale)))
            ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=size)
            self._msgbox_icon_img = ctk_img  # 保持引用，防止被回收
            return ctk_img
        except Exception:
            return None

    def _build(self, message, icon, options):
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(padx=22, pady=(12, 20))

        # ── 左侧：带透明度的 PNG 图标 ──
        icon_img = self._load_icon_image(icon)
        if icon_img is not None:
            left = ctk.CTkFrame(main, fg_color="transparent")
            left.pack(side="left", padx=(0, 16), fill="y")
            ctk.CTkLabel(left, image=icon_img, text="").pack(expand=True)

        # ── 右侧：两行（文本标签在上、按键在下）──
        right = ctk.CTkFrame(main, fg_color="transparent")
        right.pack(side="left", fill="both", expand=True)

        content = ctk.CTkFrame(right, fg_color="transparent")
        content.pack(expand=True)

        ctk.CTkLabel(content, text=message, wraplength=340, justify="left",
                     anchor="w", text_color=TEXT,
                     font=ui_fonts.ui_font(13)).pack(anchor="w", pady=(4, 14))

        btn_frame = ctk.CTkFrame(content, fg_color="transparent")
        btn_frame.pack(anchor="e")
        for i, opt in enumerate(options):
            btn = self._make_std_button(btn_frame, opt, primary=(i == 0),
                                        command=lambda v=opt: self._close(v))
            btn.pack(side="left", padx=6)

        self.bind("<Return>", lambda e: self._close(options[0]))
        self.bind("<Escape>", lambda e: self._close(None))

    def _close(self, value):
        if self._closed:
            return
        self._closed = True
        self.result = value
        super()._close()


def showinfo(title, message, parent=None, **kwargs):
    return _MsgBox(parent, title, message, "info", [_BUTTON_OK]).result


def showwarning(title, message, parent=None, **kwargs):
    return _MsgBox(parent, title, message, "warning", [_BUTTON_OK]).result


def showerror(title, message, parent=None, **kwargs):
    return _MsgBox(parent, title, message, "error", [_BUTTON_OK]).result


def askyesno(title, message, parent=None, **kwargs):
    return _MsgBox(parent, title, message, "question",
                   [_BUTTON_YES, _BUTTON_NO]).result == _BUTTON_YES


def askyesnocancel(title, message, parent=None, **kwargs):
    result = _MsgBox(parent, title, message, "question",
                     [_BUTTON_YES, _BUTTON_NO, _BUTTON_CANCEL]).result
    if result == _BUTTON_YES:
        return True
    if result == _BUTTON_NO:
        return False
    return None
