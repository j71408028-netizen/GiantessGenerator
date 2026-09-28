"""低像素风视觉基元：调色板、点阵字体、像素控件与图像像素化。

挂件版不追求信息密度，而追求「小游戏」的整体观感，因此这里统一了三条规则：

1. **直角**——所有控件无圆角，描边固定 2px（tk 的 ``highlightthickness``），模拟
   点阵描边；
2. **有限色**——只用下面这套 16 色调色板，亮/暗各一份，不再各处临时取色；
3. **点阵字**——优先选用带位图字形的字体（Windows 上用宋体），配合 █░ 这类
   方块字符画进度条，避免平滑的抗锯齿字形破坏像素感。

控件一律是 **原生 tkinter** 控件（不套任何外观库）：原生控件不做字体缩放、不替
控件重绘圆角与渐变，字形边缘更硬、更贴近点阵观感。代价是主题切换不再自动生效——
所有配色都在创建时确定并登记到 ``_bindings``，切主题时由 :func:`refresh_theme`
统一重刷；需要跟随父控件底色的控件用 ``FOLLOW`` 占位，重刷时才读取父控件实际底色。

图片类素材（角色剪影）统一做「缩到小尺寸再用最近邻放大 + 限制调色板」处理，
得到块状像素精灵。
"""

import sys
import tkinter as tk
from tkinter import font as tkfont

from PIL import Image, ImageTk

from ui.common import appearance

# ==================== 调色板 ====================
# 每个色号都是 (亮色, 暗色) 二元组；纯 tkinter 控件只接受具体颜色，
# 因此取值一律经由 pick()（或 color() 别名）按当前模式解析。
#
# 纯度是这套配色的关键：低像素下每个色块只有几个像素，混入灰调就会显得脏。
# 因此各色号都按「同一个色相上取到干净的高饱和点、明度对齐」来定，而不是直接
# 把饱和度拉满——拉满会得到纯红纯绿那种刺眼的廉价感。中性色（ink / line / text
# 一类）也统一偏冷紫，和语义色共处一张画面时不会互相污染。
PALETTE = {
    # 底色与层次
    "ink":        ("#F4EEE2", "#0E0C18"),   # 窗口底
    "ink_alt":    ("#FFFBF2", "#17142A"),   # 面板底
    "ink_soft":   ("#EDE2CC", "#221E3C"),   # 输入框 / 按钮底
    "ink_deep":   ("#E4D8BE", "#07060E"),   # 凹陷 / 阴影
    # 描边
    "line":       ("#B3A57F", "#443A72"),
    "line_hi":    ("#FFFFFF", "#8678C8"),
    # 文字
    "text":       ("#2A2318", "#F2EDE0"),
    "text_dim":   ("#6B5C3E", "#A79ED8"),
    "text_off":   ("#A59573", "#6E6698"),
    # 语义色（同时用作对应按钮的描边）
    "accent":     ("#C17400", "#FFC02E"),   # 标题 / 主操作
    "accent_hi":  ("#E08A00", "#FFDA6B"),
    "ok":         ("#2E8B22", "#5CC94A"),   # 创建
    "report":     ("#D2620A", "#FF8A2B"),   # 报告
    "dungeon":    ("#6A2FD0", "#A65CF0"),   # 副本
    "challenge":  ("#17798F", "#38C6E0"),   # 挑战
    "danger":     ("#C42B1C", "#F04438"),   # 删除
}

#: 表示「跟随父控件底色」的占位符（相当于外观库里的 "transparent"）。
FOLLOW = object()

#: 可以接受调色板色号的 tk 选项；重刷配色时只解析这些键。
_COLOR_OPTIONS = (
    'bg', 'fg', 'activebackground', 'activeforeground', 'disabledforeground',
    'highlightbackground', 'highlightcolor', 'insertbackground',
    'selectbackground', 'selectforeground', 'troughcolor', 'readonlybackground',
)

#: 外观库时代的选项名到 tk 选项名的对应，保留下来以免逐个调用点改名。
_ALIASES = {'text_color': 'fg', 'fg_color': 'bg', 'hover_color': 'activebackground'}


def pick(name: str) -> str:
    """按当前外观模式取单色值。"""
    light, dark = PALETTE[name]
    return dark if appearance.is_dark() else light


#: 历史别名：调用点里的 px.color(...) 语义即「取当前模式的颜色」。
color = pick


def _resolve(options: dict) -> dict:
    """把配置项里的调色板色号换成具体颜色；已经是颜色值的原样通过。"""
    out = {}
    for key, value in options.items():
        if isinstance(value, str) and value in PALETTE and key in _COLOR_OPTIONS:
            value = pick(value)
        out[key] = value
    return out


def _normalize(options: dict) -> dict:
    """统一选项名与色号（供各控件的 configure 覆写调用）。"""
    for alias, real in _ALIASES.items():
        if alias in options:
            options[real] = options.pop(alias)
    return _resolve(options)


def base_color(widget) -> str:
    """取控件的底色，用作「透明」子控件的背景。"""
    try:
        return widget.cget('bg')
    except tk.TclError:
        return pick("ink")


# ==================== 主题重刷登记表 ====================
_bindings = []


def _bind(widget, spec):
    """登记一个控件的配色，供 :func:`refresh_theme` 在模式切换时重刷。

    ``spec`` 既可以是 ``{tk 选项: 调色板色号}`` 的字典（值写 ``FOLLOW`` 表示跟随
    父控件底色），也可以是一个无参回调，用于配色随状态变化的控件。
    """
    if callable(spec):
        apply = spec
    else:
        def apply():
            widget.configure(**_resolve_spec(widget, spec))
    _bindings.append((widget, apply))
    apply()


def _resolve_spec(widget, spec: dict) -> dict:
    options = {}
    for option, tone in spec.items():
        options[option] = base_color(widget.master) if tone is FOLLOW else pick(tone)
    return options


def bind_colors(widget, spec: dict):
    """把「当前模式下的配色」登记到一个外部创建的控件上。

    等价于工厂内部使用的登记动作，供不属于本模块的控件（比如根窗口）使用。
    """
    _bind(widget, spec)


def refresh_theme():
    """按当前模式重刷全部登记过的控件（模式变化时由 appearance 广播调用）。"""
    alive = []
    for widget, apply in _bindings:
        try:
            if not widget.winfo_exists():
                continue
            apply()
        except tk.TclError:
            continue                    # 控件已销毁：丢弃该绑定
        alive.append((widget, apply))
    _bindings[:] = alive


appearance.subscribe(refresh_theme)


# ==================== 点阵字体 ====================
# 每种平台按优先级列出候选。
#
# 中文与西文分两套：中文靠宋体这类带内嵌位图字形的字体（Windows 上用宋体），
# 西文则另找一套真正的点阵字（Windows 上的 Terminal）。宋体的西文是衬线体、
# 笔画细，在「块状」界面里显得格格不入；而 Terminal 是 DOS 时代的位图字，
# 字身方正、行高与宋体一致（10/12 像素），换上去整行都不会跳。
_FONT_CANDIDATES = {
    "win32": ["宋体", "Fixedsys"],
    "darwin": ["Songti SC", "PingFang SC", "Heiti SC", "Arial Unicode MS"],
    "linux": ["Noto Sans CJK SC", "WenQuanYi Micro Hei", "Noto Sans SC",
              "DejaVu Sans"],
}

_LATIN_CANDIDATES = {
    "win32": ["Small Fonts", "Terminal", "Fixedsys", "Lucida Console"],
    "darwin": ["Monaco", "Menlo", "Courier"],
    "linux": ["Fixed", "DejaVu Sans Mono", "Liberation Mono", "Courier"],
}

_family_cache = {"cjk": None, "latin": None}
_font_cache = {}


def platform_key() -> str:
    if sys.platform.startswith("darwin"):
        return "darwin"
    if sys.platform.startswith("win"):
        return "win32"
    return "linux"


def _first_available(candidates) -> str:
    try:
        available = set(tkfont.families())
    except Exception:
        available = set()
    return next((name for name in candidates if name in available), candidates[-1])


def pixel_family() -> str:
    """中文字体族（点阵字形），结果缓存。"""
    if not _family_cache["cjk"]:
        _family_cache["cjk"] = _first_available(_FONT_CANDIDATES[platform_key()])
    return _family_cache["cjk"]


def latin_family() -> str:
    """西文字体族（点阵字形），结果缓存。"""
    if not _family_cache["latin"]:
        _family_cache["latin"] = _first_available(_LATIN_CANDIDATES[platform_key()])
    return _family_cache["latin"]


def is_latin(text: str) -> bool:
    """整串都是可打印 ASCII 时才算「纯西文」。

    其余字符（中文、方块 █░、箭头 ◀▶、全角标点、emoji）在点阵西文字体里没有
    字形，交给系统逐字回退会得到不可控的字形，因此一律留在中文字体里排版。
    """
    return bool(text) and all(0x20 <= ord(ch) <= 0x7E for ch in text)


def font(size: int = 10, bold: bool = False, text: str = None) -> tuple:
    """点阵字体元组；字号沿用原版的小号区间（9~13）。

    ``text`` 给定时按内容选字体：纯西文用点阵西文字，其余用中文字体。

    字号一律写成负数，即 **像素** 字号：tkinter 把正数当「磅」，会再乘一遍屏幕
    DPI（96 DPI 下 1.33 倍）——字号于是随系统缩放漂移，还会落在非整数像素上，
    字形被拉成模糊的抗锯齿轮廓。负数把字号钉死在整数像素，字才会落在内嵌的
    位图字形上，也就是这套界面要的「块状」观感。
    """
    family = latin_family() if text is not None and is_latin(text) else pixel_family()
    size = -abs(int(size))
    return (family, size, "bold") if bold else (family, size)


def measure(text: str, size: int = 10, bold: bool = False) -> int:
    """按点阵字体量出文本的像素宽。

    tk 的 ``Label`` / ``Button`` 宽度以字符计，像素级的按键宽度只能自己量，否则
    按钮宽窄会随字体度量漂移。
    """
    family = latin_family() if is_latin(text) else pixel_family()
    key = (family, size, bold)
    handle = _font_cache.get(key)
    if handle is None:
        handle = tkfont.Font(family=family, size=-abs(int(size)),
                             weight="bold" if bold else "normal")
        _font_cache[key] = handle
    return handle.measure(text or "")


def split_runs(text: str) -> list:
    """把一行文字拆成 ``[(片段, 是否纯西文), ...]``。

    Text 控件可以给不同片段套不同标签，于是同一行里中文用点阵中文字、数字与
    拉丁字母用点阵西文字——「348.1 米」这类混排才不至于让数字拖着一身衬线。
    """
    runs, buffer, flag = [], "", None
    for char in text or "":
        current = 0x20 <= ord(char) <= 0x7E
        if flag is None or current == flag:
            buffer += char
            flag = current
        else:
            runs.append((buffer, flag))
            buffer, flag = char, current
    if buffer:
        runs.append((buffer, flag))
    return runs


# ==================== 控件工厂 ====================
class Panel(tk.Frame):
    """像素面板：直角 + 2px 硬描边；bevel=True 时顶部再压一道亮线做浮雕。"""

    def __init__(self, parent, *, fill: str = "ink_alt", border: str = "line",
                 bevel: bool = False, **kwargs):
        super().__init__(parent, bd=0, highlightthickness=2,
                         highlightbackground=pick(border), bg=pick(fill), **kwargs)
        _bind(self, {'bg': fill, 'highlightbackground': border})
        if bevel:
            self._bevel = tk.Frame(self, height=2, bd=0, highlightthickness=0,
                                   bg=pick("line_hi"))
            self._bevel.pack(fill='x', side='top')
            _bind(self._bevel, {'bg': "line_hi"})


def panel(parent, **kwargs) -> Panel:
    """平面 / 面板容器的工厂入口。"""
    return Panel(parent, **kwargs)


def transparent(parent, **kwargs) -> tk.Frame:
    """「透明」容器：tk 没有透明通道，底色取父控件、主题切换时再跟随一次。"""
    frame = tk.Frame(parent, bd=0, highlightthickness=0, bg=base_color(parent),
                     **kwargs)
    _bind(frame, {'bg': FOLLOW})
    return frame


def surface(parent, *, fill: str = "ink", **kwargs) -> tk.Frame:
    """纯底色容器（无描边）：例如主界面底板。同样登记配色，主题切换才跟得上。"""
    frame = tk.Frame(parent, bd=0, highlightthickness=0, bg=pick(fill), **kwargs)
    _bind(frame, {'bg': fill})
    return frame


class PixelLabel(tk.Label):
    """点阵标签：接受调色板色号，并兼容 ``text_color`` 这类旧选项名。

    文字变化时重新按内容选字体：值从中文换成「ChineseMix」这类纯西文时，
    跟着换成点阵西文字。
    """

    def __init__(self, parent, text: str = "", *, tone: str = "text", size: int = 10,
                 bold: bool = False, fill=FOLLOW, anchor: str = 'w', **kwargs):
        self._size, self._bold = size, bold
        fill_bg = base_color(parent) if fill is FOLLOW else pick(fill)
        super().__init__(parent, text=text, anchor=anchor,
                         font=font(size, bold, text), fg=pick(tone), bg=fill_bg,
                         bd=0, highlightthickness=0, **kwargs)
        _bind(self, {'bg': fill, 'fg': tone})

    def configure(self, cnf=None, **kwargs):
        if 'size' in kwargs or 'bold' in kwargs:
            self._size = kwargs.pop('size', self._size)
            self._bold = kwargs.pop('bold', self._bold)
            kwargs['font'] = font(self._size, self._bold, kwargs.get('text'))
        elif 'text' in kwargs:
            kwargs['font'] = font(self._size, self._bold, kwargs['text'])
        return super().configure(cnf, **_normalize(kwargs))

    config = configure


def label(parent, text: str = "", *, tone: str = "text", size: int = 12,
          bold: bool = False, anchor: str = 'w', **kwargs) -> PixelLabel:
    return PixelLabel(parent, text, tone=tone, size=size, bold=bold, anchor=anchor,
                      **kwargs)


class PixelButton(tk.Frame):
    """直角描边按钮：按下时描边与文字转亮，松手复原，模拟按键的物理反馈。

    没有用 ``tk.Button``：它的宽高以字符计、描边无法做到固定 2px，而这里需要的是
    「底板 + 键面」两层——底板给 2px 硬描边与像素尺寸，键面负责文字。因此是
    Frame（描边）+ Label（文字）的组合，事件两边都绑。
    """

    PAD_X = 8          # 文字左右留白，参与自动宽度计算

    def __init__(self, parent, text: str = "", command=None, *, tone: str = "text",
                 size: int = 13, bold: bool = False, width: int = 0, height: int = 24,
                 filled: bool = False, anchor: str = 'center', **kwargs):
        self._tone = tone
        self._size = size
        self._bold = bold
        self._filled = filled
        self._command = command
        self._width = width
        self._state = 'normal'
        self._custom_fg = None
        self._custom_border = None
        self._pressed = False
        self._hovering = False

        fill = "ink_soft" if filled else "ink_alt"
        super().__init__(parent, bd=0, highlightthickness=2,
                         highlightbackground=pick(tone), bg=pick(fill), height=height,
                         **kwargs)
        self.pack_propagate(False)
        self._label = tk.Label(self, text=text, anchor=anchor,
                               font=font(size, bold, text),
                               fg=pick(tone), bg=pick(fill), bd=0,
                               highlightthickness=0, justify='left')
        self._label.pack(fill='both', expand=True)
        self._sync_width()
        _bind(self, self._repaint)

        for widget in (self, self._label):
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)
            widget.bind("<ButtonPress-1>", self._on_press)
            widget.bind("<ButtonRelease-1>", self._on_release)

    # ---------- 配色 ----------
    def _repaint(self):
        """按当前模式、填充样式与状态重刷底板与键面配色。"""
        fill = pick("ink_soft" if self._filled else "ink_alt")
        self.configure(bg=fill)
        self._label.configure(bg=fill)

        if self._pressed and self._state == 'normal':
            fg = border = pick("line_hi")
        elif self._state == 'disabled':
            fg = self._custom_fg or pick("text_off")
            border = self._custom_border or pick("line")
        else:
            fg = self._custom_fg or pick(self._tone)
            border = self._custom_border or pick(self._tone)
        self._label.configure(fg=fg)
        self.configure(highlightbackground=border)

    def _set_hover(self, hovering: bool):
        if self._state == 'disabled':
            return
        self._hovering = hovering
        if hovering and not self._pressed:
            self._label.configure(fg=pick("line_hi"))
        elif not self._pressed:
            self._label.configure(fg=self._custom_fg or pick(self._tone))

    # ---------- 事件 ----------
    def _on_enter(self, _event=None):
        self._set_hover(True)

    def _on_leave(self, _event=None):
        self._hovering = False
        self._pressed = False
        self._repaint()

    def _on_press(self, _event=None):
        if self._state == 'disabled':
            return
        self._pressed = True
        self._repaint()

    def _on_release(self, _event=None):
        was_pressed = self._pressed
        self._pressed = False
        self._repaint()
        if was_pressed and self._state != 'disabled' and self._command:
            self._command()

    # ---------- 接口 ----------
    def configure(self, cnf=None, **kwargs):
        """支持 ``text`` / ``state`` / ``text_color`` / ``border_color`` 等旧式的
        配置项，其余选项原样交给 tk.Frame。"""
        repaint = False
        if 'text' in kwargs:
            text = kwargs.pop('text')
            self._label.configure(text=text, font=font(self._size, self._bold, text))
            self._sync_width()
        if 'state' in kwargs:
            self._state = kwargs.pop('state')
            self._label.configure(cursor='' if self._state == 'disabled' else 'hand2')
            self.configure(cursor='')
            repaint = True
        if 'text_color' in kwargs:
            self._custom_fg = _resolve({'fg': kwargs.pop('text_color')})['fg']
            repaint = True
        if 'border_color' in kwargs:
            self._custom_border = _resolve(
                {'highlightbackground': kwargs.pop('border_color')})['highlightbackground']
            repaint = True
        if 'command' in kwargs:
            self._command = kwargs.pop('command')
        if kwargs:
            super().configure(cnf, **kwargs)
        if repaint:
            self._repaint()

    config = configure

    def cget(self, key):
        if key == 'text':
            return self._label.cget('text')
        if key == 'state':
            return self._state
        if key == 'text_color':
            return self._label.cget('fg')
        if key == 'border_color':
            return super().cget('highlightbackground')
        return super().cget(key)

    def invoke(self):
        if self._state != 'disabled' and self._command:
            self._command()

    def bind_wheel(self, handler):
        """把滚轮事件也接到键面上（tk 不会把滚轮事件冒泡给父控件）。"""
        for widget in (self, self._label):
            for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                widget.bind(sequence, handler, add="+")

    def _sync_width(self):
        if self._width:
            self.configure(width=self._width)
            return
        text = self._label.cget('text')
        self.configure(width=measure(text, self._size, self._bold) + 2 * self.PAD_X)


def small_button(parent, text: str, command=None, *, tone: str = "text_dim",
                 width: int = 0, height: int = 22) -> PixelButton:
    """角色卡 / 列表行里用的小按钮。"""
    return PixelButton(parent, text, command, tone=tone, size=12,
                       width=width, height=height)


def divider(parent, *, tone: str = "line") -> tk.Frame:
    """2px 水平分割线。"""
    line = tk.Frame(parent, height=2, bd=0, highlightthickness=0, bg=pick(tone))
    _bind(line, {'bg': tone})
    return line


def block_bar(value: float, maximum: float, width: int = 10,
              full: str = "█", empty: str = "░") -> str:
    """方块进度条字符串：一个控件就能画出像素条，比自绘控件省得多。"""
    ratio = 0.0 if not maximum else max(0.0, min(1.0, value / maximum))
    filled = int(round(ratio * width))
    return full * filled + empty * (width - filled)


def _normalize_choices(options) -> list:
    """接受 [值, ...] 或 [(标签, 值), ...] 两种写法。"""
    out = []
    for item in options:
        if isinstance(item, tuple) and len(item) == 2:
            out.append((str(item[0]), item[1]))
        else:
            out.append((str(item), item))
    return out


class CycleRow(tk.Frame):
    """「标签 + 点一下换一个的值」：用循环选择取代下拉框与选择对话框。

    挂件版刻意不做弹出式选择器——所有候选项都装在这一行的按钮里，点一次换下
    一个（到末尾回到开头），因此设置项与创建参数都只是「一行一个控件」。
    """

    def __init__(self, parent, title: str, options, on_change=None, *,
                 tone: str = "accent", size: int = 12, button_width: int = 92):
        super().__init__(parent, bd=0, highlightthickness=0,
                         bg=base_color(parent))
        _bind(self, {'bg': FOLLOW})
        self._choices = _normalize_choices(options)
        self._index = 0
        self._on_change = on_change
        self.columnconfigure(1, weight=1)

        label(self, title, tone="text_dim", size=size).grid(
            row=0, column=0, sticky='w', padx=(0, 6))
        self._button = PixelButton(self, "", self.cycle, tone=tone, size=size,
                                   width=button_width, height=22)
        self._button.grid(row=0, column=1, sticky='e')
        self._sync()

    # ---------- 状态 ----------
    def get(self):
        return self._choices[self._index][1]

    def text(self) -> str:
        return self._choices[self._index][0]

    def option_count(self) -> int:
        return len(self._choices)

    def set_value(self, value, notify: bool = False):
        index = self._index_of(value)
        if index is not None:
            self._index = index
        self._sync()
        if notify and self._on_change:
            self._on_change(self.get())

    def set_options(self, options, keep: bool = True):
        """替换候选项（性格 / 身材表随世界包变化时用）。"""
        current = self.get() if keep else None
        self._choices = _normalize_choices(options)
        index = self._index_of(current) if current is not None else None
        self._index = index if index is not None else 0
        self._sync()

    def _index_of(self, value):
        """先按同一性找，再退回相等比较（数据类对象可能值相同）。"""
        for index, (_text, candidate) in enumerate(self._choices):
            if candidate is value:
                return index
        for index, (_text, candidate) in enumerate(self._choices):
            try:
                if candidate == value:
                    return index
            except Exception:
                pass
        return None

    def cycle(self):
        if not self._choices:
            return
        self._index = (self._index + 1) % len(self._choices)
        self._sync()
        if self._on_change:
            self._on_change(self.get())

    def _sync(self):
        text = self._choices[self._index][0] if self._choices else "—"
        self._button.configure(text=f"◀ {text} ▶")


# ==================== 输入 / 文本 / 滚动 ====================
class PixelEntry(tk.Frame):
    """2px 描边的单行输入框：外壳固定像素高度，内部是原生 Entry。"""

    def __init__(self, parent, *, textvariable=None, width: int = 0, height: int = 24,
                 size: int = 12, tone: str = "text", fill: str = "ink_soft",
                 border: str = "line", show=None, **kwargs):
        super().__init__(parent, bd=0, highlightthickness=2,
                         highlightbackground=pick(border), bg=pick(fill),
                         height=height, width=width or 1, **kwargs)
        self.pack_propagate(False)
        self.entry = tk.Entry(self, textvariable=textvariable, font=font(size),
                              bg=pick(fill), fg=pick(tone), bd=0, relief='flat',
                              highlightthickness=0, insertbackground=pick(tone),
                              selectbackground=pick("line"), selectforeground=pick("text"),
                              show=show or "")
        self.entry.pack(fill='both', expand=True, padx=5)
        _bind(self, {'bg': fill, 'highlightbackground': border})
        _bind(self.entry, {'bg': fill, 'fg': tone, 'insertbackground': tone,
                           'selectbackground': "line", 'selectforeground': "text"})

    def get(self) -> str:
        return self.entry.get()

    def set(self, value: str):
        self.entry.delete(0, 'end')
        self.entry.insert(0, value or "")

    def focus_set(self):
        self.entry.focus_set()

    def bind(self, sequence=None, func=None, add=None):
        return self.entry.bind(sequence, func, add)


def entry(parent, **kwargs) -> PixelEntry:
    return PixelEntry(parent, **kwargs)


class PixelText(tk.Text):
    """报告正文用的只读文本区（需要配一个 scrollbar 才能滚动）。"""

    def __init__(self, parent, *, size: int = 11, fill: str = "ink",
                 tone: str = "text", **kwargs):
        super().__init__(parent, wrap='word', font=font(size), bg=pick(fill),
                         fg=pick(tone), bd=0, relief='flat', highlightthickness=0,
                         insertbackground=pick(tone), padx=6, pady=4,
                         state='disabled', **kwargs)
        _bind(self, {'bg': fill, 'fg': tone, 'insertbackground': tone})

    def write(self, text: str, tag=None):
        """在末尾追加一行（内部负责临时解除只读）。"""
        self.configure(state='normal')
        if tag:
            self.insert('end', text, tag)
        else:
            self.insert('end', text)
        self.configure(state='disabled')

    def read(self) -> str:
        return self.get("1.0", "end-1c")

    def rewrite(self, lines, writer=None):
        """清空后按 ``lines`` 重新渲染，``writer`` 负责逐行写标签。"""
        self.configure(state='normal')
        self.delete("1.0", "end")
        for line in lines:
            (writer or self.write)(line)
        self.configure(state='disabled')


class PixelScrollbar(tk.Canvas):
    """自绘像素滚动条：硬边凹槽 + 方块滑块。

    原生 ``tk.Scrollbar`` 在 Windows 上用系统的 3D 灰色主题绘制，``bg`` /
    ``troughcolor`` 只能盖住一部分，整条会变成一道浅灰竖条，和这套调色板对不上。
    这里干脆用 Canvas 自己画——反正只是一个凹槽加一个方块，比让系统主题决定观感
    更符合「不外加素材也要像素感」的取舍。
    """

    WIDTH = 11
    MIN_THUMB = 12

    def __init__(self, parent, *, command=None, trough: str = "ink_deep",
                 thumb: str = "line", edge: str = "line_hi"):
        super().__init__(parent, width=self.WIDTH, bd=0, highlightthickness=0,
                         bg=pick(trough))
        self._command = command
        self._first, self._last = 0.0, 1.0
        self._drag_offset = None
        self._tones = (trough, thumb, edge)
        _bind(self, self._repaint)
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)

    # ---------- tk 的 yscrollcommand 协议 ----------
    def set(self, first, last):
        self._first, self._last = float(first), float(last)
        self._draw()

    # ---------- 绘制 ----------
    def _repaint(self):
        trough, thumb, _edge = self._tones
        self.configure(bg=pick(trough))
        self._draw()

    def _draw(self):
        self.delete('all')
        trough, thumb, edge = self._tones
        height = max(1, self.winfo_height())
        top = int(self._first * height)
        size = max(self.MIN_THUMB, int(round((self._last - self._first) * height)))
        bottom = min(height, top + size)
        self.create_rectangle(0, 0, self.WIDTH, height,
                              fill=pick(trough), outline='')
        if self._last - self._first >= 1.0:
            return
        self.create_rectangle(1, top, self.WIDTH - 1, bottom,
                              fill=pick(thumb), outline='')
        self.create_rectangle(1, top, self.WIDTH - 1, top + 1,
                              fill=pick(edge), outline='')

    # ---------- 交互 ----------
    def _thumb_span(self):
        height = max(1, self.winfo_height())
        top = int(self._first * height)
        size = max(self.MIN_THUMB, int(round((self._last - self._first) * height)))
        return top, min(height, top + size)

    def _on_press(self, event):
        top, bottom = self._thumb_span()
        if top <= event.y <= bottom:
            self._drag_offset = event.y - top     # 抓住滑块拖
            return
        self._drag_offset = (bottom - top) // 2   # 点凹槽：滑块跳到该处
        self._drag_to(event.y)

    def _on_drag(self, event):
        if self._drag_offset is not None:
            self._drag_to(event.y)

    def _on_release(self, _event=None):
        self._drag_offset = None

    def _drag_to(self, y: int):
        """把滑块顶端挪到像素 ``y`` 处（y 是控件内的坐标，不是滚动比例）。"""
        height = max(1, self.winfo_height())
        if self._command is None:
            return
        first = (y - (self._drag_offset or 0)) / height
        self._command('moveto', max(0.0, min(1.0, first)))


def scrollbar(parent, *, command=None, **kwargs) -> PixelScrollbar:
    """细像素滚动条（凹槽 + 方块滑块）。"""
    return PixelScrollbar(parent, command=command, **kwargs)


class ScrollFrame(Panel):
    """可滚动面板：Canvas + 内容 Frame + 细滚动条。

    内容放进 ``body``；由于 tk 不会把滚轮事件冒泡给父控件，内容里的每个可点控件
    都要额外用 :meth:`bind_wheel` 接一次滚轮。
    """

    def __init__(self, parent, *, fill: str = "ink_alt", border: str = "line",
                 **kwargs):
        super().__init__(parent, fill=fill, border=border, **kwargs)
        self._fill = fill
        self.canvas = tk.Canvas(self, bd=0, highlightthickness=0, bg=pick(fill))
        self.bar = scrollbar(self, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.bar.set)
        # 先摆定宽的滚动条、再摆会撑满的文本区：pack 的 expand 会吃掉全部剩余空间，
        # 顺序反了滚动条只会剩 1px 宽。
        self.bar.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        _bind(self.canvas, {'bg': fill})

        self.body = tk.Frame(self.canvas, bd=0, highlightthickness=0, bg=pick(fill))
        _bind(self.body, {'bg': fill})
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor='nw')
        self.body.bind("<Configure>", self._on_body_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.bind_wheel(self.body)
        self.bind_wheel(self.canvas)

    def _on_body_configure(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        # 内容宽度跟随面板宽度，避免横向留白。
        self.canvas.itemconfigure(self._window, width=event.width)

    def _on_wheel(self, event):
        num = getattr(event, 'num', None)
        if num == 4:
            step = -1
        elif num == 5:
            step = 1
        else:
            step = -1 if getattr(event, 'delta', 0) > 0 else 1
        self.canvas.yview_scroll(step, 'units')

    def bind_wheel(self, widget):
        if hasattr(widget, 'bind_wheel'):
            widget.bind_wheel(self._on_wheel)
            return
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            widget.bind(sequence, self._on_wheel, add="+")


# ==================== 图像像素化 ====================
def pixelate(image: Image.Image, size: tuple, colors: int = 0,
             scale: int = 1, background: str = None) -> Image.Image:
    """把图片压成块状像素精灵：先重采样到 ``size``，再按整数倍 NEAREST 放大。

    ``colors`` 大于 0 时先把颜色量化到该数量，模拟旧主机的有限调色板。
    ``scale`` 控制放大倍数（1 表示按 ``size`` 原样显示，2 表示每个像素占 2×2）。
    ``background`` 指定透明区域的填充色——量化会丢掉 alpha，必须先合到面板
    底色上，否则透明像素会被填成黑色。
    """
    if image is None:
        return None
    small = image.convert("RGBA").resize(size, Image.Resampling.LANCZOS)
    if colors:
        flat = Image.new("RGBA", small.size, background or pick("ink_alt"))
        small = Image.alpha_composite(flat, small)
        small = small.convert("RGB").quantize(
            colors=colors, method=Image.Quantize.MEDIANCUT).convert("RGBA")
    if scale <= 1:
        return small
    return small.resize((size[0] * scale, size[1] * scale),
                        Image.Resampling.NEAREST)


def to_photo(image: Image.Image, size: tuple = None) -> ImageTk.PhotoImage:
    """像素图转 tkinter 可显示的图片对象（亮/暗共用同一张，像素风不做主题滤镜）。

    返回值必须由调用方持有引用（例如挂到控件属性上），否则会被垃圾回收、
    控件只剩一块空白。
    """
    if image is None:
        return None
    if size and image.size != tuple(size):
        image = image.resize(size, Image.Resampling.NEAREST)
    return ImageTk.PhotoImage(image)


def set_image(widget, photo):
    """给 Label 换图并保留引用（tk 不会替调用方记住图片对象）。"""
    widget.configure(image=photo if photo is not None else "")
    widget.image = photo
