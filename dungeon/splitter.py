"""内置分句器：把一次 AI 输出切成多个「显示段落」。

AI 自身的换行与句读不稳定，分句不依赖其分段习惯，而是按确定性规则切分：
换行、对话引号闭合、句末标点、分号、破折号都视为断点，并对截断处标点做
归一化（分号归一为句号、破折号归一为省略号、悬空顿逗清除）。

引号内只认「闭合引号」这一个断点：一段对话保持为单个显示段落，避免把
未闭合的对话拆成两截。

逻辑段落仍是一次 AI 输出的整体（属性演化、回放、压缩都以它为单位），
显示段落只是它的 UI 呈现单位。
"""

# 标点断点的最小单元长度：太短的句读不切分，避免产生碎片段落（换行不受限）
_MIN_UNIT_CHARS = 6

_OPEN_QUOTES = "“『「《〈‘"
_CLOSE_QUOTES = "”』」》〉’"
_END_PUNCT = "。！？!?…"
# 截断处不允许出现的悬空标点，归一化时清除
_TRAILING_NOISE = "，、：:，"


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


def split_stream_units(text: str):
    """把（部分）正文切成 ``(完整单元列表, 未完结尾部)``。

    纯函数：对同一文本重复调用结果一致，流式过程中每收到新数据整体重算。
    """
    units = []
    seg_start = 0
    depth = 0
    i = 0
    n = len(text)

    def try_flush(end: int, min_chars: int) -> bool:
        nonlocal seg_start
        if len(text[seg_start:end].strip()) < min_chars:
            return False
        unit = _normalize_unit_end(text[seg_start:end])
        if unit:
            units.append(unit)
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

    return units, text[seg_start:].strip()


def split_full_text(text: str) -> list:
    """完整正文的最终切分：无结尾断点的尾部作为最后一个显示段落。"""
    if not text:
        return []
    units, tail = split_stream_units(text)
    if tail:
        units.append(_normalize_unit_end(tail) or tail)
    return [u for u in units if u]
