"""customtkinter 运行时补丁（Frame 底色同步 + 输入框 placeholder）。

本模块集中收容对 customtkinter 的运行时修补，import 时自动生效；
不修改 .venv / site-packages 内任何文件。目前已装两个。

1. 模式切换时同步刷新 CTk 控件 Frame 底色
------------------------------------------
customtkinter 的 CTkBaseClass 只在控件创建时（__init__）按当时的主题模式设置一次
tkinter.Frame 的 bg；其 _draw() 中刷新底色的代码被官方注释掉（标注会引起闪烁），
而 _set_appearance_mode 只重绘内部画布、不会刷新 Frame 底色。

因此主题切换后、重启前，若某控件发生几何重排（面板切换、打包/解包、DPI 缩放取整），
内部画布尚未覆盖到的缝隙或瞬间会露出初始化时模式的旧底色——表现为面板/按钮区
闪现或持续显示"另一色"。

本补丁在每次外观模式切换时，为所有 CTk 控件（含之后新建的）同步刷新 Frame 底色，
使其与当前模式一致。CTkScrollableFrame / CTkTabView 官方实现即采用相同做法，
仅时机放在模式切换时（而非每次重绘），不会引入闪烁。

2. CTkEntry 带 textvariable 时 placeholder 不显示、且会污染变量
---------------------------------------------------------------
上游 ``_activate_placeholder`` 的判空条件写作::

    self._textvariable is None or self._textvariable == ""

本意是"变量为空才显示提示文字"，但 ``tkinter.Variable.__eq__`` 只有在与另一个
``Variable`` 比较时才返回布尔，与字符串比较会返回 ``NotImplemented``，Python 于是
回退到恒为 ``False`` 的默认比较。结果：只要传了 ``textvariable``，该条件恒不成立，
placeholder 永远不显示；而没有 ``textvariable`` 的输入框（如创建参数面板的昵称框）
却能正常显示——这就是"只有一种写法能显示"的原因。

另一个隐患是上游把提示文字直接 ``insert`` 进与变量绑定的原生 Entry，一旦显示成功，
写入回调就会把提示词写进用户变量（设置页的屏蔽词、网络代理都会被写脏）。

本补丁的做法：显示提示前先把 ``textvariable`` 从原生 Entry 上摘下来，提示显示期间
变量保持原值，失焦/输入时再挂回去，因此 placeholder 永远不会污染变量；顺带补上
``configure(textvariable=...)`` 重绑变量时上游没有迁移的写入监听（``ui/ctk/widgets``
的自动补全 dropdown 会在运行时重绑）。变量为空时提示只在失焦状态出现，不抢输入光标。
"""

import tkinter


def _install_appearance_bg():
    try:
        from customtkinter.windows.widgets.appearance_mode.appearance_mode_base_class import (
            CTkAppearanceModeBaseClass,
        )
        from customtkinter.windows.widgets.appearance_mode.appearance_mode_tracker import (
            AppearanceModeTracker,
        )
        from customtkinter.windows.widgets.core_widget_classes.ctk_base_class import CTkBaseClass
    except Exception:
        return

    if getattr(CTkBaseClass, "_gg_patched_appearance_bg", False):
        return

    def _patched(self, mode_string):
        # CTkBaseClass 的原实现会为每一个控件调用 update_idletasks()。
        # 数百个控件依次强制布局是切换加载时间过长的主要来源；布局在
        # AppearanceModeTracker 的整批回调完成后统一处理一次即可。
        CTkAppearanceModeBaseClass._set_appearance_mode(self, mode_string)
        try:
            bg = self._apply_appearance_mode(self._bg_color)
            tkinter.Frame.configure(self, bg=bg)
        except Exception:
            pass
        self._draw()

    def _update_callbacks_batched(cls):
        mode = "Dark" if cls.appearance_mode == 1 else "Light"
        for callback in list(cls.callback_list):
            try:
                callback(mode)
            except Exception:
                continue

        # 统一提交整批颜色重绘，避免每个 CTk 控件各自刷新一次布局。
        for app in cls.app_list:
            try:
                if app.winfo_exists():
                    app.update_idletasks()
                    break
            except Exception:
                continue

    CTkBaseClass._set_appearance_mode = _patched
    AppearanceModeTracker.update_callbacks = classmethod(_update_callbacks_batched)
    CTkBaseClass._gg_patched_appearance_bg = True


# ───────────────── placeholder 修补 ─────────────────
# 以下函数都以控件自身为 host，只用 _entry / _textvariable / _placeholder_text 等
# 公开字段，因此既能挂到 CTkEntry 上，也能在离线自检里用替身对象驱动。


def _placeholder_var_value(host):
    """变量当前字符串值；无变量返回 None（不显示提示）。"""
    var = host._textvariable
    if var is None:
        return None
    try:
        return var.get()
    except Exception:
        return None


def _placeholder_activate(host):
    """显示提示文字：先把 textvariable 摘掉，避免提示词被写进用户变量。"""
    if host._placeholder_text is None:
        return

    var = host._textvariable
    if var is not None:
        value = _placeholder_var_value(host)
        if value is None or value != "":
            return
        # 已为该变量显示着提示，无需重来（configure 重绑路径会走到这里）。
        if (host._placeholder_text_active
                and getattr(host, "_gg_placeholder_var", None) is var):
            return
    elif host._entry.get() != "":
        return

    host._placeholder_text_active = True
    host._pre_placeholder_arguments = {"show": host._entry.cget("show")}
    host._entry.config(fg=host._apply_appearance_mode(host._placeholder_text_color),
                       disabledforeground=host._apply_appearance_mode(host._placeholder_text_color),
                       show="")
    if var is not None:
        host._gg_placeholder_var = var
        host._entry.configure(textvariable="")
    host._entry.delete(0, tkinter.END)
    host._entry.insert(0, host._placeholder_text)


def _placeholder_deactivate(host):
    """收起提示文字，并把 textvariable 挂回原生 Entry。"""
    if not host._placeholder_text_active:
        return
    if host._entry.cget("state") == "readonly":
        return

    host._placeholder_text_active = False
    host._entry.config(fg=host._apply_appearance_mode(host._text_color),
                       disabledforeground=host._apply_appearance_mode(host._text_color))
    host._entry.delete(0, tkinter.END)
    for argument, value in host._pre_placeholder_arguments.items():
        host._entry[argument] = value

    var = getattr(host, "_gg_placeholder_var", None)
    if var is not None:
        host._gg_placeholder_var = None
        host._entry.configure(textvariable=var)


def _placeholder_textvariable_callback(host, var_name, index, mode):
    """变量被外部改写：空则在失焦时补提示，有值则收起提示。

    判定用上游的 ``_is_focused`` 而非 ``focus_get()``：焦点真伪在这里不重要，要的是
    "控件是否还处于用户正在编辑的状态"——``focus_get()`` 在窗口尚未映射（启动阶段、
    隐藏的父窗口）时会返回 None，会把"正在聚焦中删除文字"误判成失焦，于是在删除
    过程中插入提示词，反被随后的删除清掉。
    """
    if host._textvariable is None:
        return
    if host._textvariable.get() == "":
        if not host._is_focused:
            _placeholder_activate(host)
    elif host._placeholder_text_active:
        _placeholder_deactivate(host)


def _placeholder_sync(host):
    """按当前变量值让提示显隐与之一致（重绑 textvariable 后调用）。"""
    if host._placeholder_text is None:
        return

    var = host._textvariable
    if var is None:
        if host._placeholder_text_active:
            _placeholder_deactivate(host)
        return

    if _placeholder_var_value(host) != "":
        if host._placeholder_text_active:
            _placeholder_deactivate(host)
        return

    if (host._placeholder_text_active
            and getattr(host, "_gg_placeholder_var", None) is var):
        return

    if host._placeholder_text_active:
        _placeholder_deactivate(host)
    _placeholder_activate(host)


def _placeholder_trace_names(var):
    """变量上由本补丁注册的写入监听名（Tk 生成 ``<id><函数名>``）。"""
    try:
        return [name for _, name in var.trace_info()
                if name.endswith("_placeholder_textvariable_callback")]
    except Exception:
        return []


def _placeholder_retrace(host, old_var, new_var):
    """重绑 textvariable 后把写入监听迁到新变量。

    上游各版本行为不一：6.0.0 的 ``configure`` 会自己移除旧监听、在新变量上重挂，
    5.2.2 只把新变量塞给原生 Entry、监听仍留在旧变量上。这里直接看两个变量上实际
    挂着什么，只补缺失的那一半——既不会漏挂，也不会重复挂载后 Tcl 命令被删两次。
    """
    if old_var is not None and old_var is not new_var:
        for name in _placeholder_trace_names(old_var):
            try:
                old_var.trace_remove("write", name)
            except Exception:
                pass

    host._textvariable_callback_name = ""
    if new_var is not None:
        names = _placeholder_trace_names(new_var)
        if names:
            host._textvariable_callback_name = names[0]  # 上游已挂
        else:
            try:
                host._textvariable_callback_name = new_var.trace_add(
                    "write", host._textvariable_callback)
            except Exception:
                pass


def _wrap_configure(orig_configure):
    """包装 CTkEntry.configure：重绑 textvariable 时收尾 placeholder 状态。

    上游 configure 只把新变量塞给原生 Entry，既不迁移写入监听，也会让仍显示着
    提示的控件把旧变量名写回去，因此重绑前先按旧变量收起提示。
    """

    def _configure(self, *args, **kwargs):
        rebind = "textvariable" in kwargs
        if rebind and self._placeholder_text_active:
            _placeholder_deactivate(self)

        old_var = self._textvariable if rebind else None
        orig_configure(self, *args, **kwargs)

        if rebind:
            new_var = self._textvariable
            if new_var is not old_var:
                _placeholder_retrace(self, old_var, new_var)
            _placeholder_sync(self)

    return _configure


def _install_placeholder():
    try:
        from customtkinter.windows.widgets.ctk_entry import CTkEntry
    except Exception:
        return

    if getattr(CTkEntry, "_gg_patched_placeholder", False):
        return

    CTkEntry._activate_placeholder = _placeholder_activate
    CTkEntry._deactivate_placeholder = _placeholder_deactivate
    CTkEntry._textvariable_callback = _placeholder_textvariable_callback
    CTkEntry.configure = _wrap_configure(CTkEntry.configure)
    CTkEntry._gg_patched_placeholder = True


_install_appearance_bg()
_install_placeholder()
