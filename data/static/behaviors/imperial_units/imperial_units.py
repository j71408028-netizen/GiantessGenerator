"""将程序显示的长度单位从公制改为英制的示例行为包。

内部计算仍以米为单位；本模块只覆盖显示层：
``logic.format_size``（报告、对比、角色状态、创建面板范围预览）
与 ``logic.length_unit_label``（输入框旁的单位标签）。

创建世界包时在资源页选择 ``imperial_units``，再激活该世界包即可生效。
"""

M_TO_FT = 3.28084
FT_PER_MILE = 5280.0
MILE_THRESHOLD_M = 5000.0


def format_size(size, base_size=None):
    ref = base_size if base_size is not None else size
    if ref >= MILE_THRESHOLD_M:
        miles_ref = ref * M_TO_FT / FT_PER_MILE
        if miles_ref >= 100:
            decimals = 1
        elif miles_ref >= 10:
            decimals = 2
        else:
            decimals = 3
        display = size * M_TO_FT / FT_PER_MILE
        unit = "英里"
    else:
        feet_ref = ref * M_TO_FT
        if feet_ref >= 1000:
            decimals = 0
        elif feet_ref >= 100:
            decimals = 1
        else:
            decimals = 2
        display = size * M_TO_FT
        unit = "英尺"
    return f"{display:.{decimals}f} {unit}"


def length_unit_label():
    return "英尺"


def register(runtime):
    runtime.override("logic.format_size", format_size)
    runtime.override("logic.length_unit_label", length_unit_label)
