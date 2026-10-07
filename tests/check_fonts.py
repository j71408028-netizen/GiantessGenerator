"""副本窗口中文字体守卫：候选链覆盖 + 解析结果「真的有中文字形」。

背景：Linux 上副本窗口（DPG）的字体链原先只有 ``DejaVuSans.ttf`` 一个 Linux 路径，
它没有中文字形——设置里的「Noto Sans CJK SC」解析成 DejaVu 后中文全是豆腐块／问号。
本脚本把这件事变成可回归的断言：

1. **结构**：常规 / 粗体 / 衬线三条候选链都必须带 Linux 中文字体条目（发行版路径），
   不能只剩 DejaVu 这种纯拉丁兜底；
2. **行为**：如果本机装有任何中文字体（fontconfig 或候选链里能找到），
   那么 ``resolve_font_files()``（含设置里常见的 Windows 家族名）与
   ``resolve_serif_font()`` 解析出来的文件必须真的覆盖中文码位。

「真的有中文字形」的判据见 ``fonts.font_covers_cjk``：FreeType 对未映射码位给的是
``.notdef`` 位图，取样字符的位图与 U+10FFFE 的位图必须不同。

本机一个中文字体都没有时（例如极简 CI 镜像）打印 SKIP 并以 0 退出——这不代表代码
没问题，只代表环境无法验证；装上 ``fonts-noto-cjk`` 即恢复断言。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dungeon.window.fonts import (  # noqa: E402
    BOLD_FONT_PATHS, FONT_FALLBACK_PATHS, SERIF_FONT_PATHS,
    first_existing, font_covers_cjk, fontconfig_font, resolve_font_files,
    resolve_serif_font,
)

#: 判定「这是 Linux 中文字体条目」的路径关键词
_LINUX_CJK_HINTS = ("NotoSansCJK", "NotoSerifCJK", "SourceHan", "wqy", "DroidSansFallback")


def _chain_has_linux_cjk(chain):
    return any(
        path.startswith("/usr/share/fonts") and any(hint in path for hint in _LINUX_CJK_HINTS)
        for path in chain
    )


def check_chains() -> list[str]:
    """三条候选链都必须带 Linux 中文字体条目（旧 bug 的形状守卫）。"""
    problems = []
    for name, chain in (("FONT_FALLBACK_PATHS", FONT_FALLBACK_PATHS),
                        ("BOLD_FONT_PATHS", BOLD_FONT_PATHS),
                        ("SERIF_FONT_PATHS", SERIF_FONT_PATHS)):
        if not _chain_has_linux_cjk(chain):
            problems.append(f"{name} 缺少 Linux 中文字体候选（只剩拉丁兜底会让中文变豆腐块）")
    return problems


def _installed_cjk_available() -> bool:
    """本机是否装有任何中文字体：先问 fontconfig，再看候选链里存在的文件。"""
    match = fontconfig_font("")
    if match and font_covers_cjk(*match) is True:
        return True
    for chain in (FONT_FALLBACK_PATHS, BOLD_FONT_PATHS, SERIF_FONT_PATHS):
        for path in chain:
            if os.path.exists(path) and font_covers_cjk(path) is True:
                return True
    return False


def check_resolution() -> list[str]:
    """解析结果必须真的覆盖中文（本机没有中文字体时跳过）。"""
    if not _installed_cjk_available():
        print("SKIP  本机没有安装任何中文字体（如 Linux 上未装 fonts-noto-cjk），无法验证解析结果")
        return []

    problems = []
    # 设置里可能存着 Windows 家族名（跨平台迁移的旧设置），一律必须回落到中文字体
    for family in ("", "Noto Sans CJK SC", "Microsoft YaHei", "微软雅黑", "SimSun"):
        regular, bold = resolve_font_files(family)
        if not regular:
            problems.append(f"resolve_font_files({family!r}) 常规字体为 None")
            continue
        covered = font_covers_cjk(regular)
        if covered is not True:
            problems.append(
                f"resolve_font_files({family!r}) → {regular} 没有中文字形"
                f"（font_covers_cjk={covered}）")
        if bold:
            covered_bold = font_covers_cjk(bold)
            if covered_bold is not True:
                problems.append(
                    f"resolve_font_files({family!r}) 粗体 → {bold} 没有中文字形"
                    f"（font_covers_cjk={covered_bold}）")

    serif = resolve_serif_font()
    if serif is None:
        problems.append("resolve_serif_font() 为 None（本机有中文字体时不该发生）")
    elif font_covers_cjk(*serif) is not True:
        problems.append(f"resolve_serif_font() → {serif[0]}[{serif[1]}] 没有中文字形")

    # 组件包兜底走的粗体链第一个存在的文件也必须覆盖中文
    bold_fallback = first_existing(BOLD_FONT_PATHS)
    if bold_fallback and font_covers_cjk(bold_fallback) is not True:
        problems.append(
            f"组件包粗体兜底 {bold_fallback} 没有中文字形（BOLD_FONT_PATHS 顺序/内容需调整）")
    return problems


def main() -> int:
    problems = check_chains() + check_resolution()
    if problems:
        print("[check_fonts] FAILED")
        for item in problems:
            print("  - " + item)
        return 1
    print("[check_fonts] PASSED：候选链含 Linux 中文字体，解析结果确实覆盖中文")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
