# -*- coding: utf-8 -*-
"""进程与宿主基础设施（专业模式与挂件模式共用）。

本包收容「Tk/CTk 主进程 ↔ 子进程/副本会话/平台」的生命周期机制，
与界面控件无关：

- ``x11`` / ``x11_boot``：Linux/X11 进程级引导与 EWMH 置顶（零 GUI 框架依赖）；
- ``tk_host``：dungeon 层宿主端口在 Tk/CTk 主程序侧的适配器；
- ``dungeon_spawner`` / ``dungeon_child_host``：副本会话独立子进程的父/子两侧；
- ``mini_game_host``：内置小游戏的 pywebview 子进程宿主。

准入规则（``tests/check_mini_layering.py`` 会守住）：本包**不得顶层 import**
CTk 界面组件（``ui.common.theme/widgets/dialogs`` 等）——需要弹框能力时一律
由调用方注入（如 ``dialogs=`` 参数），缺省实现延迟到真正使用时才 import。
"""
