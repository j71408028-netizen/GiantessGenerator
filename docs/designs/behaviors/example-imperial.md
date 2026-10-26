# 示例行为包：imperial_units

> 所属机制：[行为包](README.md)（玩家向）· [开发指南](dev-guide.md)。开发流程与
> 注册方式见开发指南。

程序自带的示例行为包，把显示单位从米 / 千米改为英尺 / 英里：

```python
# data/static/behaviors/imperial_units/imperial_units.py
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
```
