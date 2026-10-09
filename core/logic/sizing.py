"""尺寸与常量：部位清单、大小类别、尺寸格式化与单位标签。

自 ``core/logic.py`` 按职责拆出（原文件现为薄壳 ``core/logic/__init__.py``）。
``@behavior_hook`` 的 scope ``"logic"`` 是已部署行为包的公开契约，与文件位置无关，
不得改动（见 ``docs/designs/world_pack_behaviors.md``）。
"""

from core.behavior_runtime import behavior_hook

ALL_PART_NAMES = [
    "身高", "步长", "腿长", "臂长", "胸宽", "脚长",
    "脚踝高度", "膝盖高度", "大腿直径", "小臂直径",
    "手掌长度", "食指长度", "食指直径", "指缝宽度", "指纹宽度"
]

SIZE_CATEGORIES = ["small", "medium", "large", "huge", "colossal"]
SIZE_DISPLAY = {
    "small": "7.5~50m", "medium": "50~300m",
    "large": "300~1800m", "huge": "1800~10000m",
    "colossal": "10~150km",
}


@behavior_hook("logic", "format_size")
def format_size(size: float, base_size: float = None) -> str:
    """根据基准身高(base_size)对齐小数位数的逻辑"""
    ref_val = base_size if base_size is not None else size
    if ref_val >= 10000:
        km_ref = ref_val / 1000
        if km_ref >= 100:
            decimals = 1
        elif km_ref >= 10:
            decimals = 2
        else:
            decimals = 3
        display_val = size / 1000
        unit = "千米"
    else:
        if ref_val >= 1000:
            decimals = 0
        elif ref_val >= 100:
            decimals = 1
        else:
            decimals = 2
        display_val = size
        unit = "米"
    return f"{display_val:.{decimals}f} {unit}"


@behavior_hook("logic", "length_unit_label")
def length_unit_label() -> str:
    """返回当前基础长度单位标签（默认“米”）。

    行为包可覆盖此函数以全流程更换长度单位的显示名称，
    例如返回“英尺”或幻想世界中的“里”等。界面输入/标签
    （如创建参数面板的身高单位标签）会调用它来显示单位。
    """
    return "米"


def get_size_category(height: float) -> str:
    """根据身高返回大小类别"""
    if height < 7.5 or height > 150000:
        return ""
    if height >= 10000:
        return "colossal"
    elif height >= 1800:
        return "huge"
    elif height >= 300:
        return "large"
    elif height >= 50:
        return "medium"
    else:
        return "small"


@behavior_hook("logic", "_build_size_description")
def build_size_description(quip_result: dict) -> str:
    """由报告正文中的一条尺寸对比结果构造解锁描述（使用对应的事件描述 quip）。"""
    quip_text = (quip_result.get("quip_text") or "").strip()
    if quip_text:
        return quip_text
    size_str = quip_result.get("size_str", "")
    compare_text = quip_result.get("compare_text", "")
    compare_text = compare_text.strip().lstrip("└─ ").strip()
    if compare_text:
        return f"{size_str}，{compare_text}"
    return size_str
