"""customtkinter 运行时补丁：模式切换时同步刷新 tkinter.Frame 自身底色。

customtkinter 的 CTkBaseClass 只在控件创建时（__init__）按当时的主题模式设置一次
tkinter.Frame 的 bg；其 _draw() 中刷新底色的代码被官方注释掉（标注会引起闪烁），
而 _set_appearance_mode 只重绘内部画布、不会刷新 Frame 底色。

因此主题切换后、重启前，若某控件发生几何重排（面板切换、打包/解包、DPI 缩放取整），
内部画布尚未覆盖到的缝隙或瞬间会露出初始化时模式的旧底色——表现为面板/按钮区
闪现或持续显示"另一色"。

本补丁在每次外观模式切换时，为所有 CTk 控件（含之后新建的）同步刷新 Frame 底色，
使其与当前模式一致；不修改 .venv 内任何文件。CTkScrollableFrame / CTkTabView
官方实现即采用相同做法，仅时机放在模式切换时（而非每次重绘），不会引入闪烁。
"""

import tkinter


def _install():
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


_install()
