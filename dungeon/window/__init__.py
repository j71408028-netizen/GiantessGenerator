"""副本会话窗口（由职责拆分后的 mixin 组装）。"""

from .base import DungeonWindowBase
from .ending import EndingHandler
from .engine import DungeonStoryEngine
from .options import OptionHandler
from .persistence import DungeonPersistence
from .triggers import TriggerHandler
from .ui import DungeonWindowUI


class DungeonSessionWindow(DungeonWindowBase, DungeonWindowUI, DungeonStoryEngine,
                           TriggerHandler, OptionHandler, EndingHandler, DungeonPersistence):
    """副本会话窗口：生命周期/UI/推进逻辑/触发器/选项/结局/持久化均由 mixin 提供。"""


__all__ = ["DungeonSessionWindow"]
