"""跨平台中文字体解析。

Windows / macOS / Linux 的中文字族互不相同：
- Windows：微软雅黑（Microsoft YaHei）
- macOS ：苹方（PingFang SC）/ 黑体-简（Heiti SC）
- Linux ：Noto Sans CJK SC / 文泉驿微米黑

各 UI 模块应尽量通过本模块取得字体，而不直接硬编码家族名，保证在 macOS
上文字不会因家族缺失而退化为不可读的默认字体。
"""

import sys


# 各平台首选家族 + 回退候选列表（按可用性优先排序）。
_PLATFORM_FONTS = {
    "darwin": ("PingFang SC", [
        "PingFang SC",
        "Hiragino Sans GB",
        "STHeiti",
        "Heiti SC",
        "Arial Unicode MS",
        "Microsoft YaHei",
        "Helvetica",
    ]),
    "win32": ("Microsoft YaHei", [
        "Microsoft YaHei",
        "微软雅黑",
        "SimHei",
        "Arial",
    ]),
    "linux": ("Noto Sans CJK SC", [
        "Noto Sans CJK SC",
        "Noto Sans SC",
        "WenQuanYi Micro Hei",
        "WenQuanYi Zen Hei",
        "Droid Sans Fallback",
        "DejaVu Sans",
    ]),
}

# 用户可配置家族内的平台缺省映射（settings 的 report/desc/dungeon 字体缺省）。
_WINDOWS_ONLY_FONTS = {
    "仿宋", "FangSong", "宋体", "SimSun", "微软雅黑", "Microsoft YaHei", "楷体", "KaiTi"
}


def platform_key() -> str:
    if sys.platform.startswith("darwin"):
        return "darwin"
    if sys.platform.startswith("win"):
        return "win32"
    return "linux"


def cjk_family() -> str:
    """返回当前平台的首选中文字体系族。"""
    return _PLATFORM_FONTS[platform_key()][0]


def cjk_family_candidates() -> tuple:
    """返回当前平台可用的中文字体系族候选（首选的排最前）。"""
    return tuple(_PLATFORM_FONTS[platform_key()][1])


def ui_font(size: int, weight: str = "") -> tuple:
    """构造跨平台的 UI 字体元组（家族名，字号[, 字重]）。"""
    if weight:
        return (cjk_family(), size, weight)
    return (cjk_family(), size)


def font_family_for(name: str, default: str) -> str:
    """把常见的 Windows 字体配置映射为当前平台的等价默认字体。"""
    name = (name or "").strip()
    if not name:
        return cjk_family()
    key = platform_key()
    if key != "win32":
        if name in _WINDOWS_ONLY_FONTS:
            return cjk_family()
        if name == "Consolas":
            return "Menlo" if key == "darwin" else "DejaVu Sans Mono"
        if name in cjk_family_candidates():
            return name
    return name or default or cjk_family()


def report_font_default() -> str:
    """报告正文字体缺省（macOS 用苹方替代 Windows 的仿宋）。"""
    return "仿宋" if platform_key() == "win32" else cjk_family()


def desc_font_default() -> str:
    """报告描述（等宽）字体缺省（macOS 用 Menlo 替代 Windows 的 Consolas）。"""
    key = platform_key()
    if key == "darwin":
        return "Menlo"
    return "Consolas" if key == "win32" else "DejaVu Sans Mono"


def dungeon_font_default() -> str:
    """副本段落字体缺省（macOS 用苹方替代 Windows 的微软雅黑）。"""
    return cjk_family()


def graphviz_font() -> str:
    """Graphviz 渲染依赖图所用的字体名（需与当前平台可用字体一致）。"""
    return cjk_family()
