"""副本会话窗口（由职责拆分后的 mixin 组装）。

入口阶段与正式运行阶段（Run）共享同一次 Session 生命周期：
dungeon.window.launcher.DungeonLaunchStages 提供入口阶段（动态背景 + 方案选择），
在 base.DungeonWindowBase.__init__ 构建正式运行 UI 后进入入口阶段，
用户选择后由 _enter_dungeon_phase() 切换到运行阶段。
"""

from .base import DungeonWindowBase
from .components import ComponentHandler
from .ending import EndingHandler
from .engine import DungeonStoryEngine
from .minigame.stage import MiniGameStageHandler
from .options import OptionHandler
from .overlay import OverlayHandler
from .persistence import DungeonPersistence
from .triggers import TriggerHandler
from .ui import DungeonWindowUI
from dungeon.window.launcher import DungeonLaunchStages


class DungeonSessionWindow(DungeonWindowBase, DungeonLaunchStages, DungeonWindowUI,
                           DungeonStoryEngine, TriggerHandler, OptionHandler,
                           EndingHandler, DungeonPersistence, ComponentHandler,
                           OverlayHandler, MiniGameStageHandler):
    """副本会话窗口：生命周期/入口阶段/UI/推进逻辑/触发器/选项/结局/持久化/小游戏均由 mixin 提供。"""


__all__ = ["DungeonSessionWindow"]