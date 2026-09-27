"""副本窗口字体：字号与字体文件候选的唯一真相源（窗口层内部模块）。

DPG 既没有字重 API、也不认系统字体家族名，只认字体文件路径，所以「设置里的家族名
→ 字体文件」的映射、各平台的候选链，以及两档字号都必须只有一份定义：

- :data:`TEXT_FONT_SIZE` 正文 24 / :data:`UI_FONT_SIZE` 面板与工具条 21 /
  :data:`BOLD_FONT_SIZE` 粗体 24（粗度靠换字体文件，不靠字号）；
- :func:`resolve_font_files`：``ui.py`` 建窗口字体时使用（家族名 → 常规 + 粗体文件）；
- :data:`BOLD_FONT_PATHS` / :data:`SERIF_FONT_PATHS`：组件包加载字体时的候选链。

历史上 ``ui.py`` 与组件包各留了一份字号与候选链，靠「两处需同步修改」的注释维持，
实际已经漂移（22 vs 21）——窗口层与组件包一律从本模块取值，不要再各留一份。
"""

import os

#: 正文（文本组件）字号，px @ dpi=1
TEXT_FONT_SIZE = 24
#: UI 文本（属性条 / 过程日志 / 工具条提示）字号
UI_FONT_SIZE = 21
#: 粗体字号（与正文同号，只是换字体文件）
BOLD_FONT_SIZE = 24

#: 平台缺省常规字体链（设置的家族名映射不到文件时按序回退）
FONT_FALLBACK_PATHS = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)

#: 粗体候选链（等线 Bold 的视觉粗度低于雅黑 Bold，故排前；组件包兜底同一条链）
BOLD_FONT_PATHS = (
    "C:/Windows/Fonts/Dengb.ttf",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
)

#: NVL 衬线候选链（仿宋 / 宋体优先）
SERIF_FONT_PATHS = (
    "C:/Windows/Fonts/simsun.ttc",
    "C:/Windows/Fonts/STZHONGS.TTF",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
)

#: 设置里的家族名（去空白/连字符、小写）→ 常规字体文件；未映射的家族走 FONT_FALLBACK_PATHS
FAMILY_FONT_FILES = {
    "microsoftyahei": "C:/Windows/Fonts/msyh.ttc",
    "microsoftyaheiui": "C:/Windows/Fonts/msyh.ttc",
    "微软雅黑": "C:/Windows/Fonts/msyh.ttc",
    "simhei": "C:/Windows/Fonts/simhei.ttf",
    "黑体": "C:/Windows/Fonts/simhei.ttf",
    "simsun": "C:/Windows/Fonts/simsun.ttc",
    "nsimsun": "C:/Windows/Fonts/simsun.ttc",
    "宋体": "C:/Windows/Fonts/simsun.ttc",
    "新宋体": "C:/Windows/Fonts/simsun.ttc",
    "fangsong": "C:/Windows/Fonts/simfang.ttf",
    "仿宋": "C:/Windows/Fonts/simfang.ttf",
    "kaiti": "C:/Windows/Fonts/simkai.ttf",
    "楷体": "C:/Windows/Fonts/simkai.ttf",
    "dengxian": "C:/Windows/Fonts/Deng.ttf",
    "等线": "C:/Windows/Fonts/Deng.ttf",
    "pingfangsc": "/System/Library/Fonts/PingFang.ttc",
    "heitisc": "/System/Library/Fonts/STHeiti Medium.ttc",
}


def first_existing(paths):
    """返回候选链中第一个存在的文件；都不存在时返回 None。"""
    for path in paths:
        if os.path.exists(path):
            return path
    return None


def resolve_font_files(family):
    """把设置的字体家族名解析为 ``(常规文件|None, 粗体文件|None)``。

    先按 :data:`FAMILY_FONT_FILES` 映射家族名，映射不到再走
    :data:`FONT_FALLBACK_PATHS`；粗体一律走 :data:`BOLD_FONT_PATHS`（不随家族变）。
    粗体与常规解析到同一个文件时返回 ``None``——同一个文件重复建字体没有意义。
    """
    key = (str(family or "").strip()
           .replace(" ", "").replace("-", "").replace("_", "").lower())
    regular = FAMILY_FONT_FILES.get(key)
    if not regular or not os.path.exists(regular):
        regular = first_existing(FONT_FALLBACK_PATHS)
    bold = first_existing(BOLD_FONT_PATHS)
    if bold and bold == regular:
        bold = None
    return regular, bold


__all__ = [
    "TEXT_FONT_SIZE", "UI_FONT_SIZE", "BOLD_FONT_SIZE",
    "FONT_FALLBACK_PATHS", "BOLD_FONT_PATHS", "SERIF_FONT_PATHS",
    "FAMILY_FONT_FILES", "first_existing", "resolve_font_files",
]
