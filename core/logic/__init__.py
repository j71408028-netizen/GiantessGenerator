"""领域逻辑：原 ``core/logic.py`` 杂物间按职责拆为四个子模块，此处薄壳再导出。

拆分（2026-10-06，交接文档 §4.1）后各子模块::

    sizing.py     尺寸与常量（ALL_PART_NAMES / SIZE_* / format_size / length_unit_label
                  / get_size_category / build_size_description）
    quips.py      quip 标签与选取（PREDEFINED_TAGS / get_predefined_tags
                  / replace_quip_tags / get_comparisons / select_quip_with_budget）
    simulation.py 模拟计算（compute_casualty / compute_environment_factor
                  / apply_size_unlock_updates）
    text.py       文本屏蔽（normalize_blocked_words / contains_blocked_word
                  / should_skip_by_part_tags / comparison_lines）

调用点继续 ``from core.logic import X``，零改动；全部公开名在此再导出。

**行为包 hook key 不是模块路径**：各子模块里 ``@behavior_hook("logic", "format_size")``
拼出的 key 是 ``"logic.format_size"``，这个 key 是**已部署世界包行为包的公开契约**
（示例见 ``data/static/behaviors/``）。scope ``"logic"`` 与文件在哪个模块无关，
重命名或再重排文件时**不得**把它改成 ``"core.logic.format_size"``——那会让用户
已安装的行为包静默失效。
"""

from core.logic.sizing import (
    ALL_PART_NAMES, SIZE_CATEGORIES, SIZE_DISPLAY,
    build_size_description, format_size, get_size_category, length_unit_label,
)
from core.logic.text import (
    comparison_lines, contains_blocked_word, normalize_blocked_words,
    should_skip_by_part_tags,
)
from core.logic.simulation import (
    apply_size_unlock_updates, compute_casualty, compute_environment_factor,
)
from core.logic.quips import (
    PREDEFINED_TAGS, get_comparisons, get_predefined_tags, replace_quip_tags,
    select_quip_with_budget,
)
