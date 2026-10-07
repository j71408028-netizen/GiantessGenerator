"""副本窗口字体：字号与字体文件候选的唯一真相源（窗口层内部模块）。

DPG 既没有字重 API、也不认系统字体家族名，只认字体文件路径，所以「设置里的家族名
→ 字体文件」的映射、各平台的候选链，以及两档字号都必须只有一份定义：

- :data:`TEXT_FONT_SIZE` 正文 24 / :data:`UI_FONT_SIZE` 面板与工具条 21 /
  :data:`BOLD_FONT_SIZE` 粗体 24（粗度靠换字体文件，不靠字号）；
- :func:`resolve_font_files`：``ui.py`` 建窗口字体时使用（家族名 → 常规 + 粗体文件）；
- :func:`resolve_font` / :func:`resolve_serif_font`：要 face 序号（PIL 离屏画布）
  或 NVL 衬线字体时使用；
- :data:`BOLD_FONT_PATHS` / :data:`SERIF_FONT_PATHS`：候选链，组件包兜底同一条。

历史上 ``ui.py`` 与组件包各留了一份字号与候选链，靠「两处需同步修改」的注释维持，
实际已经漂移（22 vs 21）——窗口层与组件包一律从本模块取值，不要再各留一份。

Linux 说明（发行版字体路径差异大）：在 Linux 上优先问 fontconfig
（``fc-match``，**只认返回字体确实声明覆盖 ``zh-cn`` 的结果**——fontconfig 对不存在
的家族名也会做替换，不核对的话「微软雅黑」会被换成某个拉丁字体，中文静默变豆腐块），
静态候选链只作为没有 fontconfig 时的兜底。两个已知限制：

- DPG 只能按文件建字体，无法指定集合（``.ttc``）内的 face 序号，.ttc 一律取第 0 个
  face（Noto CJK 的第 0 个 face 是 JP：码位覆盖完整、不会出豆腐块，个别字的写法是
  日文变体）。因此候选链把单面简体字体（思源黑体 SC / Noto Sans CJKsc）排在 .ttc 前。
- fontconfig 返回的 face 序号只有能选 face 的调用方（PIL）用得上，见 :func:`resolve_font`。
"""

import functools
import os
import shutil
import subprocess
import sys

#: 正文（文本组件）字号，px @ dpi=1
TEXT_FONT_SIZE = 24
#: UI 文本（属性条 / 过程日志 / 工具条提示）字号
UI_FONT_SIZE = 21
#: 粗体字号（与正文同号，只是换字体文件）
BOLD_FONT_SIZE = 24

_IS_LINUX = sys.platform.startswith("linux")

#: Linux 中文字体候选。单面 SC 字体排前（DPG 取不到 .ttc 的简体 face，见模块说明），
#: 路径按「Debian/Ubuntu → Arch → Fedora」的常见布局罗列；查不到时由 fontconfig 兜底。
_LINUX_SANS_PATHS = (
    "/usr/share/fonts/opentype/source-han-sans/SourceHanSansSC-Regular.otf",
    "/usr/share/fonts/adobe-source-han-sans/SourceHanSansCN-Regular.otf",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/wenquanyi/wqy-microhei/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/usr/share/fonts/truetype/droid/DroidSansFallback.ttf",
)

#: Linux 粗体候选（`BOLD_FONT_PATHS` 的 Linux 段；文泉驿只有一档字重，同文件即常规）
_LINUX_BOLD_PATHS = (
    "/usr/share/fonts/opentype/source-han-sans/SourceHanSansSC-Bold.otf",
    "/usr/share/fonts/adobe-source-han-sans/SourceHanSansCN-Bold.otf",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Bold.otf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/wenquanyi/wqy-microhei/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
)

#: Linux 衬线候选（NVL 阅读模式用）
_LINUX_SERIF_PATHS = (
    "/usr/share/fonts/opentype/source-han-serif/SourceHanSerifSC-Regular.otf",
    "/usr/share/fonts/adobe-source-han-serif/SourceHanSerifCN-Regular.otf",
    "/usr/share/fonts/opentype/noto/NotoSerifCJKsc-Regular.otf",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/google-noto-cjk/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
)

#: 平台缺省常规字体链（设置的家族名映射不到文件时按序回退）。
#: 末位 DejaVu 没有中文字形，只作为「系统真的没装中文字体」时的拉丁兜底。
FONT_FALLBACK_PATHS = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    *_LINUX_SANS_PATHS,
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)

#: 粗体候选链（等线 Bold 的视觉粗度低于雅黑 Bold，故排前；组件包兜底同一条链）
BOLD_FONT_PATHS = (
    "C:/Windows/Fonts/Dengb.ttf",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    *_LINUX_BOLD_PATHS,
)

#: NVL 衬线候选链（仿宋 / 宋体优先）
SERIF_FONT_PATHS = (
    "C:/Windows/Fonts/simsun.ttc",
    "C:/Windows/Fonts/STZHONGS.TTF",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    *_LINUX_SERIF_PATHS,
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
    # Linux：路径按 Debian/Ubuntu 布局；其余发行版由 fontconfig 解析（见 resolve_font）
    "notosanscjksc": "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "notosanscjk": "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "notoserifcjksc": "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "notoserifcjk": "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "wenquanyimicrohei": "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "文泉驿微米黑": "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "wenquanyizenhei": "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "文泉驿正黑": "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "droidsansfallback": "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
}


def first_existing(paths):
    """返回候选链中第一个存在的文件；都不存在时返回 None。"""
    for path in paths:
        if os.path.exists(path):
            return path
    return None


def _normalize_family(family):
    """家族名归一化：去空白 / 连字符 / 下划线并小写（FAMILY_FONT_FILES 的键）。"""
    return (str(family or "").strip()
            .replace(" ", "").replace("-", "").replace("_", "").lower())


@functools.lru_cache(maxsize=1)
def _fontconfig_binary():
    """``fc-match`` 的绝对路径；只在 Linux 上找，找不到（或非 Linux）返回 None。"""
    if not _IS_LINUX:
        return None
    return shutil.which("fc-match") or None


@functools.lru_cache(maxsize=64)
def _fontconfig_match(query, require_lang):
    """fc-match 查询 → ``(文件路径, face 序号)``；不可用 / 查不到 / 语言不符返回 None。"""
    binary = _fontconfig_binary()
    if binary is None:
        return None
    try:
        proc = subprocess.run(
            [binary, "-f", "%{file}\t%{index}\t%{lang}", query],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
        return None
    if proc.returncode != 0:
        return None
    fields = proc.stdout.strip().split("\t")
    path = fields[0].strip() if fields else ""
    if not path or not os.path.exists(path):
        return None
    try:
        index = int(fields[1]) if len(fields) > 1 and fields[1].strip() else 0
    except ValueError:
        index = 0
    if require_lang:
        # fontconfig 的 langset 用 `|` 分隔（family 等字符串列表用 `,`），两种都认
        langs = set()
        if len(fields) > 2:
            langs = {item.strip()
                     for item in fields[2].replace(",", "|").split("|")
                     if item.strip()}
        if require_lang not in langs:
            return None
    return path, index


def fontconfig_font(family="", *, bold=False):
    """问 fontconfig 要家族名对应的 ``(文件路径, face 序号)``；不可用/不覆盖中文返回 None。

    ``family`` 可以是具体家族名，也可以是 fontconfig 的通用别名（``serif`` 等）；
    为空时直接问「系统里覆盖简体中文的字体」。查询模式一律带 ``:lang=zh-cn``，
    返回值再核对一次语言表：fontconfig 对不存在的家族名也会做替换（「微软雅黑」
    在 Linux 上会被换成某个默认字体），不约束语言就会把拉丁字体当成中文字体用。
    """
    if _fontconfig_binary() is None:
        return None
    pattern = str(family or "").strip()
    if pattern:
        # fontconfig pattern 里 `:` / `,` 是分隔符，家族名中出现时需转义
        pattern = pattern.replace("\\", "\\\\").replace(":", "\\:").replace(",", "\\,")
    if bold:
        pattern += ":style=Bold"
    pattern += ":lang=zh-cn"
    return _fontconfig_match(pattern, "zh-cn")


def resolve_font(family="", *, bold=False):
    """家族名 → ``(文件路径, face 序号)``；一个都找不到时返回 ``None``。

    解析顺序：Linux 先问 fontconfig（家族名直查，能拿到正确的 face 序号），
    再查 :data:`FAMILY_FONT_FILES` 显式映射，最后走平台候选链。face 序号只有
    能指定集合内 face 的调用方（PIL）用得上；DPG 一律取第 0 个 face。

    ``bold=True`` 时不再查家族表（粗体是另一条文件链）：fontconfig 找不到真粗体就
    回落到 :data:`BOLD_FONT_PATHS`。
    """
    if _IS_LINUX:
        match = fontconfig_font(family, bold=bold)
        if match:
            return match
    if not bold:
        mapped = FAMILY_FONT_FILES.get(_normalize_family(family))
        if mapped and os.path.exists(mapped):
            return mapped, 0
    path = first_existing(BOLD_FONT_PATHS if bold else FONT_FALLBACK_PATHS)
    return (path, 0) if path else None


def resolve_font_files(family=""):
    """把设置的字体家族名解析为 ``(常规文件|None, 粗体文件|None)``（DPG 只要路径）。

    规则与 :func:`resolve_font` 一致：Linux 先问 fontconfig，再查
    :data:`FAMILY_FONT_FILES` 显式映射，最后走 :data:`FONT_FALLBACK_PATHS` /
    :data:`BOLD_FONT_PATHS`。粗体与常规解析到同一个文件时返回 ``None``——同一个
    文件重复建字体没有意义（文泉驿这类只有一档字重的字体就是这种情况）。
    """
    regular = resolve_font(family)
    bold = resolve_font(family, bold=True)
    regular_path = regular[0] if regular else None
    bold_path = bold[0] if bold else None
    if bold_path and bold_path == regular_path:
        bold_path = None
    return regular_path, bold_path


def resolve_serif_font():
    """NVL / 衬线候选 → ``(文件路径, face 序号)``；找不到返回 ``None``。

    Linux 上问 fontconfig 的通用别名 ``serif``（带上 ``:lang=zh-cn``，即系统
    认为最适合排简体中文的衬线字体）；系统没有中文衬线时 fontconfig 会给它
    认为最接近的字体，与「找不到就回退常规字体」的旧行为等价。其余平台走
    :data:`SERIF_FONT_PATHS` 里存在的第一个文件。
    """
    if _IS_LINUX:
        match = fontconfig_font("serif")
        if match:
            return match
    path = first_existing(SERIF_FONT_PATHS)
    return (path, 0) if path else None


#: 「缺字形」参考码位：Unicode 非字符，任何字体都不会映射它
_MISSING_GLYPH_REF = "\U0010FFFE"


def font_covers_cjk(path, index=0, sample="中"):
    """``path``（集合字体按 ``index``）是否真的有简体中文字形；读不出来返回 None。

    FreeType 对没有映射的码位返回 ``.notdef`` 字形，所以「取样字符的位图」必须和
    「参考码位（U+10FFFE，非字符）的位图」不同，才算有真字形。这是诊断与离线守卫
    用的判据（PIL 是已声明的依赖）：副本窗口中文字体链有没有失效，靠它一眼看出。
    """
    try:
        from PIL import ImageFont
        font = ImageFont.truetype(path, 24, index=index)
        return bytes(font.getmask(sample)) != bytes(font.getmask(_MISSING_GLYPH_REF))
    except Exception:
        return None


__all__ = [
    "TEXT_FONT_SIZE", "UI_FONT_SIZE", "BOLD_FONT_SIZE",
    "FONT_FALLBACK_PATHS", "BOLD_FONT_PATHS", "SERIF_FONT_PATHS",
    "FAMILY_FONT_FILES", "first_existing", "fontconfig_font", "resolve_font",
    "resolve_font_files", "resolve_serif_font", "font_covers_cjk",
]
