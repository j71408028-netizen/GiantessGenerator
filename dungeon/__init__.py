"""副本领域模块：不依赖具体 UI 框架，也不依赖服务层。

这里的 import 约束由 ``scripts/check_dungeon_layering.py`` 强制检查（CI/手工跑都行）：

- ``dungeon/`` **根下的模块**（models / rules / chapters / actions / coupling /
  prompts / response / splitter / summary / details / schema / validate …）是纯领域，
  禁止 import ``dearpygui`` / ``tkinter`` / ``customtkinter`` / ``services`` / ``ui``，
  也禁止反向依赖 ``dungeon.window``；
- 需要外部行为时走**依赖注入**：例如 ``EvolutionRules(step_decay=...)`` 由 UI 层
  （``window/base.py``）显式传入 ``StateService.decay_step_rates``；
- 需要 UI 的代码一律放 ``dungeon/window/``（含 background / dispatcher /
  launcher / components）。
"""

from .models import DungeonState, DungeonTextType
from .prompts import DungeonPromptBuilder
from .rules import EvolutionRules

__all__ = ["DungeonState", "DungeonTextType", "EvolutionRules", "DungeonPromptBuilder"]
