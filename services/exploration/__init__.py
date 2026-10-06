"""探索流程的服务层实现（阶段 3.1 从 ``core/context.py`` 按职责抽出）。

``ExplorationContext`` 原本是一个 1100+ 行的 God object，既做目录/配置聚合，又做
报告引擎、地址规划、报告文本渲染、角色装配与导出。而它顶层 import 了
``persistence``（8 个 repo）与 ``services``，本就违反 ``core`` 只依赖 ``infra``
的约束——所以这一轮的结论不是「把类拆小」，而是**把它整体搬到 ``services`` 层**。

模块职责：

===================  ==================================================
``catalog``          目录与配置聚合（``ExplorationCatalog``）
``report``           报告引擎：产出结构化报告数据（``ReportEngine``）
``address_plan``     注册地址系统的规划与筛选（``AddressPlanner``）
``report_text``      报告正文/详情文本拼装（模块级纯函数）
``character``        角色装配、尺寸解锁、头像兜底（``CharacterAssembler``）
``export``           角色卡导出数据（模块级函数）
``context``          薄外观 ``ExplorationContext``：保 API + 编排入口
===================  ==================================================

层约束（``tests/check_import_graph.py``）：本包属 ``services`` 层，**不得** import
``ui.*``，也不得引入 ``tkinter`` / ``customtkinter`` / ``dearpygui`` / ``PIL``。
唯一的例外是 ``services/preview``（预览渲染，已单独登记），本包只按需延迟
调用它，不在模块顶层 import。
"""

from .catalog import ExplorationCatalog
from .character import CharacterAssembler
from .context import ExplorationContext
from .export import build_export_card_data, build_export_card_from_state
from .report_text import build_detail_text, build_report_text

__all__ = [
    "ExplorationCatalog",
    "CharacterAssembler",
    "ExplorationContext",
    "build_export_card_data",
    "build_export_card_from_state",
    "build_report_text",
    "build_detail_text",
]
