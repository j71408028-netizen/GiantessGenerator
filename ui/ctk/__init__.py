# -*- coding: utf-8 -*-
"""专业模式的 CTk 组件库（customtkinter 控件、配色、字体、弹窗）。

本包收容一切依赖 CTk（或依赖依赖 CTk 的模块）的专业界面通用件：

- ``theme`` / ``fonts`` / ``ctk_patch`` / ``ctk_image``：配色、字体、运行时补丁与图像包装；
- ``widgets`` / ``dialogs`` / ``managers``：通用控件、模态弹窗、管理面板基类；
- ``loading`` / ``splash``：启动加载页与启动屏；
- ``avatar`` / ``address_dlg``：头像框、地址级联选择等业务组件；
- ``chat_time``：聊天时间标记纯函数（零依赖，挂件将来接入聊天标记时也可用）。

**准入规则**（``tests/check_mini_layering.py`` 守住）：挂件层（``ui/mini``）禁用
本包——挂件是纯 tkinter，配色走 ``core.appearance`` + ``ui.mini.pixel``，字号用
负数像素值。与进程/宿主基础设施（``ui.hosting``）的分界同样按依赖判：凡是
import CTk 的住这里，零 CTk 依赖、被两种模式共用的住那里。
"""
