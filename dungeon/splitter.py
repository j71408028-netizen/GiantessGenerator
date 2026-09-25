"""内置分句器：把一次 AI 输出切成多个「显示段落」，并解析说话人标记。

AI 自身的换行与句读不稳定，分句不依赖其分段习惯，而是按确定性规则切分：
换行、对话引号闭合、句末标点、分号、破折号都视为断点，并对截断处标点做
归一化（分号归一为句号、破折号归一为省略号、悬空顿逗清除）。

引号内只认「闭合引号」这一个断点：一段对话保持为单个显示段落，避免把
未闭合的对话拆成两截。

说话人标记：Solea/Bulla 耦合等级的对话分支里，AI 会在对话句句首写
「@说话人@」（见 dungeon/coupling.SPEAKER_MARKER_RULE）。本模块在显示段落
**起点**识别完整标记，解析出说话人并从显示文本中剥离；标记出现在段落中间
时不处理、原样保留。流式过程中未闭合的半截标记按提示抑制显示（整体重算
幂等，收全后自然解析）。Velum 等级正文无标记，行为与过去完全一致。

逻辑段落仍是一次 AI 输出的整体（属性演化、回放、压缩都以它为单位），
显示段落只是它的 UI 呈现单位；说话人信息只活在显示层，不进持久化——
落盘正文用 :func:`strip_speaker_markers` 剥离标记。
"""

import re
from typing import NamedTuple, Optional

# 标点断点的最小单元长度：太短的句读不切分，避免产生碎片段落（换行不受限）
_MIN_UNIT_CHARS = 6

# 说话人标记：单元起点的 @说话人@（1~12 个非 @ 非 \n 字符）
_SPEAKER_MARKER_RE = re.compile(r"@([^@\n]{1,12})@")
# 正文内任意位置的标记（净化用，比显示层解析宽松）
_SPEAKER_MARKER_ANY_RE = re.compile(r"@[^@\n]{1,12}@")

_OPEN_QUOTES = "“『「《〈‘"
_CLOSE_QUOTES = "”』」》〉’"
_END_PUNCT = "。！？!?…"
# 截断处不允许出现的悬空标点，归一化时清除
_TRAILING_NOISE = "，、：:，"


class DisplayUnit(NamedTuple):
    """一个显示段落：speaker 为 None 表示叙述句。"""

    speaker: Optional[str]
    text: str


def _normalize_unit_end(unit: str) -> str:
    """归一化截断处标点，避免显示段落以半截标点收尾。"""
    unit = unit.strip()
    if not unit:
        return unit
    if unit[-1] == "—":
        return unit.rstrip("—") + "……"
    while unit and unit[-1] in _TRAILING_NOISE:
        unit = unit[:-1]
    if unit and unit[-1] in "；;":
        unit = unit[:-1] + "。"
    return unit


def _split_leading_marker(text: str) -> tuple:
    """识别文本起点的完整说话人标记，返回 (speaker|None, 剥离后的正文)。"""
    if not text.startswith("@"):
        return None, text
    match = _SPEAKER_MARKER_RE.match(text)
    if match:
        return match.group(1), text[match.end():]
    return None, text


def _suppress_incomplete_marker(text: str) -> str:
    """流式尾部的显示净化：起点是未闭合的半截标记时暂不显示，避免闪现。

    整体重算幂等：标记一旦闭合（仍在长度上限内）就会被解析并剥离。
    """
    if not text.startswith("@"):
        return text
    match = _SPEAKER_MARKER_RE.match(text)
    if match:
        return text[match.end():]
    # 长度上限 12 + 前后两个 @：超过仍无闭合即视为普通正文
    if len(text) <= 13:
        return ""
    return text


def strip_speaker_markers(text: str) -> str:
    """剥离正文中全部说话人标记，返回纯正文（回放/概要/报告等落盘文本用）。"""
    if not text:
        return text
    return _SPEAKER_MARKER_ANY_RE.sub("", text)


def split_stream_units(text: str):
    """把（部分）正文切成 ``(完整单元列表, 未完结尾部)``。

    单元是 :class:`DisplayUnit`（含说话人）；尾部是供显示的文本——起点若为
    半截标记会被抑制。纯函数：对同一文本重复调用结果一致，流式过程中每收到
    新数据整体重算。
    """
    units = []
    seg_start = 0
    depth = 0
    i = 0
    n = len(text)

    def try_flush(end: int, min_chars: int) -> bool:
        nonlocal seg_start
        raw = text[seg_start:end].strip()
        if not raw:
            return False
        speaker, display = _split_leading_marker(raw)
        # 最小长度按剥离标记后的正文判断，避免标记本身凑够字数
        if len(display) < min_chars:
            return False
        unit = _normalize_unit_end(display)
        if unit:
            units.append(DisplayUnit(speaker=speaker, text=unit))
        seg_start = end
        return True

    while i < n:
        ch = text[i]
        if ch == "\n":
            # 换行是 AI 显式分段，不受最小长度限制
            try_flush(i, 1)
            i += 1
            continue
        if ch in _OPEN_QUOTES:
            depth += 1
            i += 1
            continue
        if ch in _CLOSE_QUOTES:
            if depth > 0:
                depth -= 1
            # 对话闭合即断点；吞掉紧随的句末标点与多余闭合引号
            j = i + 1
            while j < n and (text[j] in _END_PUNCT or text[j] in _CLOSE_QUOTES):
                j += 1
            if try_flush(j, _MIN_UNIT_CHARS):
                depth = 0
                i = j
                continue
            i += 1
            continue
        if depth == 0:
            if ch in _END_PUNCT:
                j = i + 1
                while j < n and text[j] in _END_PUNCT:
                    j += 1
                if try_flush(j, _MIN_UNIT_CHARS):
                    i = j
                    continue
            elif ch in "；;":
                if try_flush(i + 1, _MIN_UNIT_CHARS):
                    i += 1
                    continue
            elif ch == "—":
                j = i
                while j < n and text[j] == "—":
                    j += 1
                try_flush(j, _MIN_UNIT_CHARS)
                i = j
                continue
        i += 1

    return units, _suppress_incomplete_marker(text[seg_start:].strip())


def split_full_text(text: str) -> list:
    """完整正文的最终切分：无结尾断点的尾部作为最后一个显示段落。

    返回 :class:`DisplayUnit` 列表；若尾部起点是未闭合标记则原样保留
    （落盘前的净化见 :func:`strip_speaker_markers`）。
    """
    if not text:
        return []
    units, tail = split_stream_units(text)
    if tail:
        speaker, display = _split_leading_marker(tail)
        units.append(DisplayUnit(speaker=speaker,
                                 text=_normalize_unit_end(display) or display))
    return [u for u in units if u.text]
