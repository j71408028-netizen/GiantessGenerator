"""quip 类型 meta 读取 + 类型替换静态检查 + 疑似语法问题溯源。

用法:
    python developer_tools/quip_report.py 风格名 尺寸档 [--data-dir DIR] [--json] [--all]
                                          [--no-grammar] [--samples N]
    python developer_tools/quip_report.py --selftest [-v]

**一次只检查一个风格的一个尺寸分组**（尺寸档 = small/medium/large/huge/colossal）。
这是硬约束：全量跑一遍结论太多，逐组看才读得动。风格或尺寸档少给一个，脚本会打印
"可选分组清单"（每个档的坐标数/条目数/标记数）后直接退出，不会闷头跑全量；
多给一个风格则报错退出。尺寸档也可以写成 ``--size/-s small``，两种写法等价。

检查内容（除 meta 声明是文件级，其余全部只覆盖所选尺寸档）：

1. meta 配置读取：各风格 ``_meta.custom_types``（c/d/e）的名称、细分数、
   ``allow_confusion``（是否可混淆）；缺省该键时报告不一致
   （repo 运行时按 False，quip 编辑器按 True，见 services/exploration/report.py:199
   与 ui/quip/__init__.py:267）。
2. 类型替换检查（静态、单遍）：按运行时 replace_quip_tags 的规则构建内容池索引，
   校验每个标记的替换候选是否可用；对可混淆类型按"邻近坐标(距离≤2.0)加权改选编号"
   的规则预计算可达编号集，定位混淆实际生效/空转的格子组合。
3. 疑似格式问题检测并溯源：未知类型字母、空内容/非数字编号标记、游离括号、
   坏坐标键（会让 QuipRepo.load 直接抛 ValueError）、条目 style 字段与所属文件不符
   （会污染内容池归属）、未知 {占位符} 等，每条问题给出
   文件/体型/坐标/条目下标/字符偏移/片段。
4. 类型替换的**组合级语法检查**并溯源：标记 `[类型:编号:文本]` 的意义是"这一格由
   同风格同体型下所有同类型同编号的标记文本互换"，所以语法风险只在**接缝**上——
   候选的开头/结尾字形，碰上模板标记前后的字形（或是相邻标记的另一侧池）。检查：
   - 虚词叠置：标记后紧跟「的」而候选以「的」收尾 → 「的的」；连词/体标记同理。
   - 标点叠置、候选自带句末标点把句子截断、候选引号/括号不成对。
   - 人称代词冲突：候选用「他/它」而上下文以「她」指代主角；候选用第二人称。
   - **同槽位形态不一致**：一处标记自称「的」字定语式，同池候选却是名词短语
     （或反之）——替换后结构就换了。例：small 档 d:1 池里混进裸形容词「细腻」，
     large 档 e:1 池里混进谓词小句「沦为了一场效率惊人的速通记录」。
   - 同槽位候选"是否显式带主语「她」"不一致。
   每条结论都双向溯源：槽位（文件/体型/坐标/条目/偏移）+ 候选出处（同上）+ 替换后片段，
   并按"池 + 形态差异"聚合计数，同一根因只出一条结论。

   效率：只在标记边界上比较**特征**，不做 候选×模板 的笛卡尔展开；内容特征按字符串
   全局缓存，同池同形判定天然合并。复杂度 O(槽位 × 该槽位池内候选)。正文会打印
   "槽位 N 处 / 候选比对 M 次 / 缓存 K 条特征"，便于核对没有爆炸。

退出码：发现 error 级问题返回 1，否则 0（便于日后挂进自检门禁）。
第 4 项只产出 warn/info，不改变退出码口径。选了非法/歧义分组返回 2；仅打印清单返回 0。

输出量：清单默认只列 error/warn，info 只在摘要计数（加 --all 才展开）；每条组合结论
默认带 3 条样例（--samples N 调，0 表示只留结论）。--json 同样遵守这两条。

可见范围：只检查所选尺寸档，所以"某细分编号全风格没人用""某体型档结构坏"这类结论
只在该档被检查时才会出现——不要把它当成整个风格的总检。
"""

import argparse
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict

# 内置类型细分（与 ui/quip/dialogs.py _build_type_list 保持一致）
BUILTIN_SUBTYPES = {
    "a": ["裙子", "制服", "夏季", "冬季"],
    "b": ["站立", "坐下", "躺下", "蹲跪"],
}
CUSTOM_LETTERS = ("c", "d", "e")
KNOWN_PLACEHOLDERS = {"{name}", "{nick}"}
SIZE_CATS = ["small", "medium", "large", "huge", "colossal"]

# 运行时替换用的标记正则（core/logic/quips.py:123、detail_pools.py:11）
TAG_RE = re.compile(r"\[([a-e]):(\d+):([^\]]+)\]")
# 任何成对括号组；掩掉它之后残留的 [ ] 就是游离/嵌套括号
BRACKET_GROUP_RE = re.compile(r"\[[^\[\]]*\]")
SUMMARY_RE = re.compile(r"\[summary:([^\]]*)\]")
PLACEHOLDER_RE = re.compile(r"\{[^{}]*\}")

LEVEL_ORDER = {"error": 0, "warn": 1, "info": 2}

# ===== 组合级（接缝）语法检查用的字形分类 =====
# 判定只看标记边界上的字形，不做 候选×模板 的笛卡尔展开。
DE_PARTICLES = "的地得"          # 结构助词/状语标记：叠置即"的的"
SENT_PUNCT = "。！？…"           # 句末标点：候选自带会把句子截断
CLAUSE_PUNCT = "，、；：—"        # 句中标点：与模板标点相邻即叠用
OPEN_QUOTES = "“‘「『"
CLOSE_QUOTES = "”’」』"
CONJ_CHARS = "和与及或"           # 连词
ASPECT_CHARS = "了着过"           # 体标记：叠置即"了了"
BRACKET_OPEN = "（(【["
BRACKET_CLOSE = "）)】]"

# 谓词性开头（前导副词/介词/动词）。词表刻意保守：只收语料里判定明确、且**不会**
# 与名词短语混淆的形式；命中即认为该候选是"谓词性小句"，用于发现同槽位里混进来的
# 「沦为了一场效率惊人的速通记录」这类换了结构就接不通的候选。
# 注意不含「被/让/给」——「被她无意间踏碎的繁华地段」是偏正结构的名词短语。
PREDICATE_HEAD_RE = re.compile(
    r"^(?:在|像|如|经|连同|瞬间|立刻|随即|同时|整体|彻底|完全|永久性?|逐步|持续|正|随|由"
    r"|沦为|化为|化作|变为|变成|成为|降格为|陷入|吞没|掀飞|扭曲|折断|撕裂|粉碎"
    r"|抹除|抹平|蒸发|液化|气化|塌陷|崩落|崩塌|崩断|崩裂|碾碎|挤压|剥离)")
# 动词打头但紧接着名词性后缀的是"动宾式名词"（崩裂声、崩塌感），不是谓词小句
NOUN_SUFFIX = "声感性度权质者们"
# 人称代词：排除「自我」「其他」这类含人称字的普通词
PERSON1_RE = re.compile(r"(?<!自)(?<!忘)我")
PERSON2_RE = re.compile(r"你|您")
PERSON3_OTHER_RE = re.compile(r"(?<!其)他|它")

FORM_DE = "de"        # 以「的/地/得」收尾 —— 定语式，后面接名词
FORM_PUNCT = "punct"  # 以真标点（。！？…，、；：）收尾
FORM_QUOTE = "quote"  # 以引号/括号收尾 —— 引号会跟着一起搬走
FORM_PRED = "pred"    # 谓词性小句
FORM_NOUN = "noun"    # 名词短语
FORM_LABEL = {FORM_DE: "「的」字定语式", FORM_PUNCT: "带标点收尾",
              FORM_QUOTE: "引号/括号收尾", FORM_PRED: "谓词性小句",
              FORM_NOUN: "名词短语"}

# 每条聚合结论最多列几条样例（CLI --samples 覆盖）
_MAX_SAMPLES = [3]


class ContentFeature:
    """一条候选文本在"接缝"上看得出的全部信息。按字符串全局缓存。"""

    __slots__ = ("text", "head", "tail", "form", "head_class", "tail_class",
                 "pron1", "pron2", "pron_her", "pron_other", "quotes_ok", "length")

    def __init__(self, text, head, tail, form, head_class, tail_class,
                 pron1, pron2, pron_her, pron_other, quotes_ok, length):
        self.text = text
        self.head = head
        self.tail = tail
        self.form = form
        self.head_class = head_class
        self.tail_class = tail_class
        self.pron1 = pron1
        self.pron2 = pron2
        self.pron_her = pron_her
        self.pron_other = pron_other
        self.quotes_ok = quotes_ok
        self.length = length


def head_class(ch):
    """一个字作为"文本开头"时的类别。"""
    if not ch:
        return "none"
    if ch in DE_PARTICLES:
        return "de"
    if ch in OPEN_QUOTES or ch in BRACKET_OPEN:
        return "quote_open"
    if ch in SENT_PUNCT or ch in CLAUSE_PUNCT or ch in CLOSE_QUOTES or ch in BRACKET_CLOSE:
        return "punct"
    if ch in CONJ_CHARS:
        return "conj"
    if ch in ASPECT_CHARS:
        return "aspect"
    return "word"


def tail_class(ch):
    """一个字作为"文本结尾"时的类别。"""
    if not ch:
        return "none"
    if ch in DE_PARTICLES:
        return "de"
    if ch in SENT_PUNCT:
        return "sent_punct"
    if ch in CLAUSE_PUNCT:
        return "clause_punct"
    if ch in CLOSE_QUOTES or ch in BRACKET_CLOSE:
        return "quote_close"
    if ch in OPEN_QUOTES or ch in BRACKET_OPEN:
        return "quote_open"
    if ch in CONJ_CHARS:
        return "conj"
    if ch in ASPECT_CHARS:
        return "aspect"
    return "word"


def _is_predicate(core):
    """候选是不是"谓词性小句"（换成名词槽位就接不通）。"""
    m = PREDICATE_HEAD_RE.match(core)
    if not m:
        return False
    if m.end() < len(core) and core[m.end()] in NOUN_SUFFIX:
        return False  # 崩裂声 / 崩塌感 —— 动词打头的名词
    return True


QUOTE_PAIRS = {"“": "”", "‘": "’", "「": "」", "『": "』",
               "（": "）", "(": ")", "【": "】", "[": "]"}


def _quotes_balanced(core):
    """引号/括号是否成对且类型对得上（ASCII 双引号按出现次数判奇偶）。

    候选自带半个引号时，替换进模板会把模板里原有的引号一起带偏，所以按内容单独查。
    """
    stack = []
    for ch in core:
        if ch in QUOTE_PAIRS:
            stack.append(QUOTE_PAIRS[ch])
        elif ch in ("”", "’", "」", "』", "）", ")", "】", "]"):
            if not stack or stack.pop() != ch:
                return False
    return not stack and core.count('"') % 2 == 0


def build_feature(text):
    core = text.strip()
    if not core:
        return ContentFeature(text, None, None, FORM_PUNCT, "none", "none",
                              False, False, False, False, True, 0)
    hd, tl = core[0], core[-1]
    hc, tc = head_class(hd), tail_class(tl)
    if tc == "de":
        form = FORM_DE
    elif tc == "sent_punct" or tc == "clause_punct":
        form = FORM_PUNCT
    elif tc in ("quote_close", "quote_open"):
        form = FORM_QUOTE
    elif _is_predicate(core):
        form = FORM_PRED
    else:
        form = FORM_NOUN
    return ContentFeature(
        text, hd, tl, form, hc, tc,
        bool(PERSON1_RE.search(core)), bool(PERSON2_RE.search(core)),
        "她" in core, bool(PERSON3_OTHER_RE.search(core)),
        _quotes_balanced(core), len(core))


def content_feature(text, cache):
    """按内容字符串缓存特征——同一段文本在全库只分析一次。"""
    feat = cache.get(text)
    if feat is None:
        feat = build_feature(text)
        cache[text] = feat
    return feat


def _render(occ, cand, pad=12):
    """把候选填进槽位，给出替换后的实际片段（便于人眼直接判断通不通）。"""
    text = occ["text"]
    left = text[max(0, occ["offset"] - pad):occ["offset"]]
    right = text[occ["end"]:occ["end"] + pad]
    return f"{left}【{cand}】{right}"


def _window(occ, pad=16):
    text = occ["text"]
    return text[max(0, occ["offset"] - pad):occ["end"] + pad]



class Finding:
    __slots__ = ("level", "kind", "message", "loc", "extra")

    def __init__(self, level, kind, message, loc, extra=None):
        self.level = level
        self.kind = kind
        self.message = message
        self.loc = loc  # dict 或 None
        self.extra = extra  # 组合级问题的附带信息（组合计数 / 双向溯源样例）

    def as_dict(self):
        out = {"level": self.level, "kind": self.kind,
               "message": self.message, "loc": self.loc}
        if self.extra is not None:
            out["extra"] = self.extra
        return out


def make_loc(file, size_cat=None, coord=None, entry_idx=None, offset=None, snippet=None):
    loc = {"file": file}
    for key, val in (("size_cat", size_cat), ("coord", coord),
                     ("entry_idx", entry_idx), ("offset", offset), ("snippet", snippet)):
        if val is not None:
            loc[key] = val
    return loc


def format_loc(loc):
    """把溯源定位渲染成一行：文件 [体型 @ 坐标] 条目#N 偏移K。"""
    if not loc:
        return "(无定位)"
    out = loc.get("file", "")
    if "size_cat" in loc:
        out += f" [{loc['size_cat']}"
        if "coord" in loc:
            out += f" @ {loc['coord']}"
        out += "]"
    if "entry_idx" in loc:
        out += f" 条目#{loc['entry_idx']}"
    if "offset" in loc:
        out += f" 偏移{loc['offset']}"
    return out


def format_extra(extra):
    """把组合级问题的双向溯源渲染成缩进行。"""
    if not extra:
        return []
    lines = [f"      ↳ 池 {extra.get('pool', '?')}；本类组合命中 {extra.get('count', '?')} 组"
             f"（涉事候选 {extra.get('distinct_candidates', '?')} 条）"]
    if extra.get("her_candidates"):
        lines.append("        自带主语「她」的候选：" + "、".join(
            f"『{c}』" for c in extra["her_candidates"]))
    for s in extra.get("samples", []):
        lines.append(f"        样例·候选『{s['candidate']}』"
                     f"（来自 {format_loc(s.get('origin'))}）")
        lines.append(f"        　　　填入槽位 {format_loc(s.get('slot'))}")
        lines.append(f"        　　　替换后 → 「{s.get('rendered', '')}」")
    return lines


def load_style_file(path):
    """读风格 JSON，返回 (data, error)。坏 JSON 时 error 带行号信息。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), None
    except json.JSONDecodeError as e:
        return None, f"JSON 解析失败（行 {e.lineno} 列 {e.colno}）：{e.msg}"
    except OSError as e:
        return None, f"文件读取失败：{e}"


def check_meta(file, meta, findings):
    """读取并校验 _meta.custom_types，返回 {letter: info_dict}。"""
    types_info = {}
    custom_types = {}
    if isinstance(meta, dict):
        custom_types = meta.get("custom_types") or {}
        if not isinstance(custom_types, dict):
            findings.append(Finding(
                "error", "meta", "_meta.custom_types 不是对象，运行时会按无自定义类型处理",
                make_loc(file)))
            custom_types = {}
    else:
        findings.append(Finding(
            "error", "meta", "_meta 不是对象，运行时会按默认 meta 处理", make_loc(file)))

    for letter in CUSTOM_LETTERS:
        info = custom_types.get(letter)
        if info is None:
            types_info[letter] = None
            continue
        if not isinstance(info, dict):
            findings.append(Finding(
                "error", "meta", f"custom_types.{letter} 不是对象", make_loc(file)))
            types_info[letter] = None
            continue
        subtypes = info.get("subtypes")
        if not isinstance(subtypes, list) or not subtypes:
            findings.append(Finding(
                "error", "meta",
                f"custom_types.{letter}({info.get('name', '?')}) 缺少非空 subtypes 列表，"
                f"编辑器与混淆权重都无法按细分工作", make_loc(file)))
            subtypes = []
        entry = {
            "name": info.get("name", ""),
            "subtypes": subtypes,
            "allow_confusion": info.get("allow_confusion"),
        }
        if "allow_confusion" not in info:
            findings.append(Finding(
                "warn", "meta",
                f"custom_types.{letter}({entry['name'] or '?'}) 未声明 allow_confusion："
                f"repo 运行时按 False（永不混淆），quip 编辑器却按 True 显示——建议显式声明",
                make_loc(file)))
        elif not isinstance(info["allow_confusion"], bool):
            findings.append(Finding(
                "error", "meta",
                f"custom_types.{letter} 的 allow_confusion 不是布尔值：{info['allow_confusion']!r}",
                make_loc(file)))
            entry["allow_confusion"] = None
        types_info[letter] = entry
    return types_info


def scan_text(text, file, size_cat, coord, entry_idx, findings):
    """单遍扫描一条 quip 文本，返回合法标记 [(letter, num, span, content, prev, next)]。

    prev/next 是标记左右紧邻的一个字符（无则为 None），供组合级接缝检查使用。

    同时检测：未知字母、空内容、非数字编号、游离/嵌套括号、空 summary、
    未知 {占位符}。全部溯源到字符偏移。
    """
    tags = []
    # 已识别的括号组 span，掩掉后找游离括号
    consumed = []

    def consume(m):
        consumed.append(m.span())
        return "\x00" * (m.end() - m.start())

    for m in BRACKET_GROUP_RE.finditer(text):
        inner = m.group()[1:-1]
        loc = lambda off, frag: make_loc(file, size_cat, coord, entry_idx, off, frag)

        if inner.startswith("summary:"):
            if not inner[len("summary:"):].strip():
                findings.append(Finding(
                    "warn", "summary", "summary 标记内容为空", loc(m.start(), m.group())))
            consumed.append(m.span())
            continue

        tm = re.fullmatch(r"([a-e]):(\d+):([^\]]*)", inner)
        if tm:
            letter, num, content = tm.group(1), tm.group(2), tm.group(3)
            if not content.strip():
                # 运行时正则要求 [^\]]+，空内容不会被替换也不会被剥离，原样漏进报告
                findings.append(Finding(
                    "error", "tag_empty",
                    f"标记 [{inner}] 内容为空：replace_quip_tags 的正则匹配不到它，"
                    f"会原样漏进报告正文", loc(m.start(), m.group())))
            else:
                prev_ch = text[m.start() - 1] if m.start() > 0 else None
                next_ch = text[m.end()] if m.end() < len(text) else None
                tags.append((letter, num, m.span(), content, prev_ch, next_ch))
            consumed.append(m.span())
            continue

        tm_bad = re.fullmatch(r"([a-zA-Z]):(\d*):(.*)", inner)
        if tm_bad:
            letter = tm_bad.group(1)
            if letter not in "abcde":
                findings.append(Finding(
                    "error", "tag_letter",
                    f"未知类型字母 '{letter}'（合法为 a-e）：运行时全链路静默忽略，"
                    f"标记原文会漏进报告", loc(m.start(), m.group())))
            elif not tm_bad.group(2):
                findings.append(Finding(
                    "error", "tag_num", f"标记 [{inner}] 缺少数字编号",
                    loc(m.start(), m.group())))
            else:
                findings.append(Finding(
                    "error", "tag_format",
                    f"标记 [{inner}] 无法被运行时正则解析（含空格/嵌套等）",
                    loc(m.start(), m.group())))
            consumed.append(m.span())
            continue

        # 既非标记也非 summary 的括号组：可能是手写错标记
        if re.fullmatch(r"[a-z]:\d+:[^\]]*", inner) or re.fullmatch(r"[A-Z]:\d+:[^\]]*", inner):
            findings.append(Finding(
                "error", "tag_letter", f"未知类型标记 [{inner}]（类型字母须为 a-e）",
                loc(m.start(), m.group())))
        else:
            findings.append(Finding(
                "warn", "bracket", f"无法识别的括号组 [{inner}]：既不是类型标记也不是 summary",
                loc(m.start(), m.group())))
        consumed.append(m.span())
        continue

    masked = BRACKET_GROUP_RE.sub(consume, text)
    for m in re.finditer(r"[\[\]]", masked):
        findings.append(Finding(
            "error", "bracket_stray",
            f"游离括号 '{m.group()}'（不成对或嵌套，如 [a:1:[x]]）：运行时不会替换其内部内容",
            make_loc(file, size_cat, coord, entry_idx, m.start(),
                     text[max(0, m.start() - 10):m.start() + 10])))

    for m in PLACEHOLDER_RE.finditer(text):
        if m.group() not in KNOWN_PLACEHOLDERS:
            findings.append(Finding(
                "warn", "placeholder",
                f"未知占位符 {m.group()}（运行时只替换 {{name}}/{{nick}}）",
                make_loc(file, size_cat, coord, entry_idx, m.start(), m.group())))
    return tags


def scan_style(style_name, data, findings, size_filter=None):
    """扫描一个风格文件，返回 (pools, numset, used_letters, origins, occurrences)。

    size_filter 给出时只扫该尺寸档，其余档整档略过（池按 (尺寸档, 风格, 字母, 编号)
    分裂，所以"只看一档"语义上是自洽的：该档的池天然不含别档内容）。

    pools: {(size_cat, letter, num): set(content)} —— 与 build_detail_pools 同构
           （池按条目自报的 style 字段归属，与运行时一致）。
    numset: {(size_cat, coord_i, coord_d, letter): set(num)} —— 混淆可达编号预计算用。
    origins: {(size_cat, style, letter, num): {content: loc}} —— 候选出处（溯源用）。
    occurrences: 标记出现处，带池键与左右邻字（组合级接缝检查用）。
    """
    pools = defaultdict(set)
    numset = {}
    used_letters = defaultdict(int)
    origins = {}
    occurrences = []
    pool_style = style_name  # 条目 style 字段缺失时 load() 会按文件名补，等价于此

    if not isinstance(data, dict):
        findings.append(Finding(
            "error", "structure", "风格 JSON 顶层不是对象", make_loc(f"{style_name}.json")))
        return pools, numset, used_letters, origins, occurrences

    for key, matrix in data.items():
        if key == "_meta":
            continue
        if size_filter is not None and key != size_filter:
            continue  # 本次只检查选定尺寸档，别档的池/坐标一律不进索引
        if key not in SIZE_CATS:
            findings.append(Finding(
                "warn", "structure",
                f"未知体型档 '{key}'（合法为 {','.join(SIZE_CATS)}），运行时会整档忽略",
                make_loc(f"{style_name}.json")))
        if not isinstance(matrix, dict):
            findings.append(Finding(
                "error", "structure", f"体型档 '{key}' 不是坐标到条目列表的映射",
                make_loc(f"{style_name}.json", key)))
            continue
        for coord_key, quip_list in matrix.items():
            coord = None
            if not isinstance(coord_key, str) or "_" not in coord_key:
                findings.append(Finding(
                    "error", "coord",
                    f"坐标键 {coord_key!r} 缺少 '_'：QuipRepo.load 会在 split('_') 处"
                    f"直接抛异常，导致整个风格加载失败", make_loc(f"{style_name}.json", key)))
            else:
                try:
                    i, d = (int(p) for p in coord_key.split("_"))
                    if not (1 <= i <= 4 and 1 <= d <= 4):
                        raise ValueError
                    coord = (i, d)
                except ValueError:
                    findings.append(Finding(
                        "error", "coord",
                        f"坐标键 '{coord_key}' 不是 1-4 范围的 整数_整数："
                        f"QuipRepo.load 会在 int() 处抛异常，导致整个风格加载失败",
                        make_loc(f"{style_name}.json", key)))
            if not isinstance(quip_list, list):
                findings.append(Finding(
                    "error", "structure", f"坐标 '{coord_key}' 下的条目不是列表",
                    make_loc(f"{style_name}.json", key, coord_key)))
                continue
            for idx, item in enumerate(quip_list):
                if isinstance(item, str):
                    text, item_style = item, style_name
                elif isinstance(item, dict):
                    text = item.get("text")
                    item_style = item.get("style")
                    if not isinstance(text, str):
                        findings.append(Finding(
                            "error", "structure",
                            f"条目缺少 text 字段或 text 不是字符串",
                            make_loc(f"{style_name}.json", key, coord_key, idx)))
                        continue
                    if item_style != style_name:
                        findings.append(Finding(
                            "warn", "style_field",
                            f"条目 style 字段为 {item_style!r}，与所属文件 '{style_name}' 不符："
                            f"选句后 replace_quip_tags 会按该字段取内容池，会串风格/取空池",
                            make_loc(f"{style_name}.json", key, coord_key, idx)))
                        if not item_style:
                            findings.append(Finding(
                                "error", "style_field",
                                f"条目缺少 style 字段：select_quip_with_budget 返回空风格名，"
                                f"replace_quip_tags 取不到内容池，所有标记原样漏进报告",
                                make_loc(f"{style_name}.json", key, coord_key, idx)))
                else:
                    findings.append(Finding(
                        "warn", "structure",
                        f"条目类型为 {type(item).__name__}，运行时会被静默丢弃",
                        make_loc(f"{style_name}.json", key, coord_key, idx)))
                    continue
                pool_key_style = item_style if item_style else style_name
                tags = scan_text(text, f"{style_name}.json", key, coord_key, idx, findings)
                for letter, num, span, content, prev_ch, next_ch in tags:
                    used_letters[letter] += 1
                    if content.strip().upper() == "MARK":
                        continue  # 与 build_detail_pools 一致：MARK 不入池
                    pk = (key, pool_key_style, letter, num)
                    pools[pk].add(content)
                    origins.setdefault(pk, {}).setdefault(
                        content, make_loc(f"{style_name}.json", key, coord_key, idx,
                                          span[0], content[:24]))
                    occurrences.append({
                        "file": f"{style_name}.json", "style": style_name,
                        "size_cat": key, "coord": coord_key, "entry_idx": idx,
                        "letter": letter, "num": num,
                        "offset": span[0], "end": span[1],
                        "inline": content, "prev": prev_ch, "next": next_ch,
                        "pool_key": pk, "text": text,
                    })
                    if coord is not None:
                        numset.setdefault((key, coord[0], coord[1], letter), set()).add(num)
    return pools, numset, used_letters, origins, occurrences


def check_replacement(style_name, pools, numset, types_info, findings):
    """类型替换静态检查：非混淆候选可用性 + 混淆可达性（组合级）。"""
    # 非混淆：池为空即替换必然回退原文。池由标记自身贡献，正常恒非空；
    # 若为空说明该标记内容是 MARK 且同类型同编号再无其他贡献者 —— 运行时
    # MARK 直接替换为空串，不算问题，故此处只对可疑形态（编号越界）告警。
    declared = {}
    for letter in "abcde":
        if letter in BUILTIN_SUBTYPES:
            declared[letter] = len(BUILTIN_SUBTYPES[letter])
        elif types_info.get(letter):
            declared[letter] = len(types_info[letter]["subtypes"])

    used_nums = defaultdict(set)
    for (_size, _st, letter, num) in pools:
        used_nums[letter].add(num)

    # 混淆可达性：对 allow_confusion 的类型，按运行时规则（同风格、距离≤2.0、
    # exp(-dist) 加权）预计算每个格子的可达编号集，定位"开了混淆却空转"的组合。
    confusing = [lt for lt in CUSTOM_LETTERS
                 if types_info.get(lt) and types_info[lt]["allow_confusion"] is True]
    for (size, i, d, letter), own_nums in sorted(numset.items()):
        if letter not in confusing:
            continue
        reachable = set(own_nums)
        for (s2, i2, d2, lt2), nums2 in numset.items():
            if s2 == size and lt2 == letter and math.hypot(i2 - i, d2 - d) <= 2.0:
                reachable |= nums2
        if len(reachable) <= 1:
            findings.append(Finding(
                "info", "confusion_noop",
                f"类型 {letter} 已允许混淆，但格 ({i},{d}) 周边距离≤2.0 内只有编号 "
                f"{sorted(reachable)}：混淆在该格是空转（改选编号只会命中自己）",
                make_loc(f"{style_name}.json", size, f"{i}_{d}")))
    return confusing, used_nums, declared


def check_usage(style_name, used_letters, used_nums, types_info, declared, findings,
                scope="本风格"):
    """meta 声明与实际用量的交叉检查。

    scope 是本次检查的范围（如"尺寸档 small"），只影响提示措辞——这些结论按选中的
    尺寸档统计，写"本风格"会误导。
    """
    for letter in "abcde":
        if letter in CUSTOM_LETTERS and used_letters[letter] and not types_info.get(letter):
            findings.append(Finding(
                "warn", "meta",
                f"{scope}内文本使用了自定义类型 {letter}（{used_letters[letter]} 处），"
                f"但 _meta.custom_types 未声明：编辑器无名称/细分可显示，"
                f"allow_confusion_map 也不会包含它（永不混淆）",
                make_loc(f"{style_name}.json")))
        src = "内置" if letter in BUILTIN_SUBTYPES else "自定义"
        name = (BUILTIN_SUBTYPES.get(letter) and ",".join(BUILTIN_SUBTYPES[letter])) or (
            types_info.get(letter) and types_info[letter]["name"]) or "?"
        if used_letters[letter] == 0:
            if letter in CUSTOM_LETTERS and types_info.get(letter):
                findings.append(Finding(
                    "info", "usage",
                    f"自定义类型 {letter}({types_info[letter]['name']}) 在{scope}内从未使用",
                    make_loc(f"{style_name}.json")))
            continue
        limit = declared.get(letter)
        if limit:
            for num in sorted(used_nums[letter], key=int):
                if int(num) > limit:
                    findings.append(Finding(
                        "warn", "tag_range",
                        f"{src}类型 {letter}({name}) 出现编号 {num}，超出声明的 {limit} 个细分："
                        f"编辑器无法显示该细分，混淆后编辑器归类为未知",
                        make_loc(f"{style_name}.json")))
        # 声明了细分但整档无人用（对自定义类型才有意义；内置类型仅提示缺口）
        if letter in CUSTOM_LETTERS and types_info.get(letter):
            missing = [str(n + 1) for n in range(limit or 0)
                       if str(n + 1) not in used_nums[letter]]
            if missing and used_nums[letter]:
                findings.append(Finding(
                    "info", "usage",
                    f"类型 {letter}({types_info[letter]['name']}) 的细分 "
                    f"{','.join(missing)} 在{scope}内没有任何标记使用",
                    make_loc(f"{style_name}.json")))


def _classify_combo(occ, feat, req, next_occ, prev_occ, pool_head_classes, pool_tail_classes):
    """给一对「槽位 × 候选」下判定，返回 (kind, level, detail, prefix) 或 None。

    优先级：硬接缝（虚词/标点叠置）> 标点截断 > 引号 > 人称代词 > 槽内形态。
    一对组合只报首要的一类，避免同一对组合在多个规则里重复刷屏；被抑制的判定
    不影响结论口径（它们在别的组合上仍会各自出现）。
    """
    prev_ch, next_ch = occ["prev"], occ["next"]
    prev_cls = tail_class(prev_ch)
    next_cls = head_class(next_ch)

    if next_occ is not None:
        # 相邻的两个标记（如 [c:1:…][e:2:…]）：接缝在"左侧候选的收尾 × 右侧池的开头"上
        nheads = pool_head_classes.get(next_occ["pool_key"], {})
        other = f"{next_occ['letter']}:{next_occ['num']}"
        if feat.tail_class == "de" and nheads.get("de"):
            return ("seam_de", "warn", f"|tag:{other}+de",
                    f"标记右侧紧邻另一个类型标记 [{other}]，其候选池里有以「的」开头的文本，"
                    f"而本候选以「{feat.tail}」收尾：两者同时命中会叠成「…的的…」")
        if feat.tail_class in ("sent_punct", "clause_punct") and (
                nheads.get("punct") or nheads.get("quote_open")):
            return ("seam_punct", "warn", f"|tag:{other}+punct",
                    f"标记右侧紧邻另一个类型标记 [{other}]，两侧候选都可能带标点："
                    f"同时命中会标点叠用")
        next_cls = "none"  # 右边是标记而非正文，正文接缝不再适用
    if prev_occ is not None:
        otails = pool_tail_classes.get(prev_occ["pool_key"], {})
        other = f"{prev_occ['letter']}:{prev_occ['num']}"
        if feat.head_class == "de" and otails.get("de"):
            return ("seam_de", "warn", f"tag:{other}|de+de",
                    f"标记左侧紧邻另一个类型标记 [{other}]，其候选池里有以「的」收尾的文本，"
                    f"而本候选以「的」开头：两者同时命中会叠成「…的的…」")
        if feat.head_class in ("punct", "quote_open") and (
                otails.get("sent_punct") or otails.get("clause_punct")):
            return ("seam_punct", "warn", f"tag:{other}|tail+{feat.head}",
                    f"标记左侧紧邻另一个类型标记 [{other}]，两侧候选都可能带标点："
                    f"同时命中会标点叠用")
        prev_cls = "none"

    if feat.tail_class == "de" and next_cls == "de":
        return ("seam_de", "warn", f"de+{next_ch}",
                f"本标记后面紧跟「{next_ch}」，而本候选以「{feat.tail}」收尾："
                f"替换后叠成「{feat.tail}{next_ch}」")
    if prev_cls == "de" and feat.head_class == "de":
        return ("seam_de", "warn", f"{prev_ch}+de",
                f"本标记前面是「{prev_ch}」，而本候选以「{feat.head}」开头："
                f"替换后叠成「{prev_ch}{feat.head}」")
    if feat.tail_class in ("sent_punct", "clause_punct") and next_cls in ("punct", "quote_close"):
        return ("seam_punct", "warn", f"tail{feat.tail}+{next_ch}",
                f"本候选以标点「{feat.tail}」收尾，标记后又是「{next_ch}」：替换后标点叠用")
    if prev_cls in ("sent_punct", "clause_punct") and feat.head_class in ("punct", "quote_open"):
        return ("seam_punct", "warn", f"{prev_ch}+head{feat.head}",
                f"本标记前面是标点「{prev_ch}」，而本候选以「{feat.head}」开头：替换后标点叠用")
    if feat.tail_class == "sent_punct" and next_cls == "word":
        return ("content_sent_punct", "warn", "sent_punct",
                "本候选自带句末标点，而标记后还有正文：替换后句子会在标记处提前结束")
    if prev_cls == "conj" and feat.head_class == "conj":
        return ("seam_conj", "warn", f"{prev_ch}+conj{feat.head}",
                f"本标记前面是连词「{prev_ch}」，而本候选以「{feat.head}」开头：替换后连词叠用")
    if prev_cls == "aspect" and feat.tail_class == "aspect" and next_cls == "aspect":
        return ("seam_aspect", "warn", f"aspect+{feat.tail}",
                f"标记前是「{prev_ch}」，本候选以体标记「{feat.tail}」收尾，后面又是「{next_ch}」："
                f"替换后叠成「{prev_ch}{feat.tail}{next_ch}」")
    if not feat.quotes_ok:
        return ("quote_unbalanced", "warn", "quotes",
                "本候选的引号/括号不成对：替换后与模板里的引号错配，整句引号归属会乱")
    if feat.pron2:
        return ("pron_person2", "warn", "p2",
                "本候选直接称呼读者（第二人称）：与全文第三人称叙述冲突")
    if feat.pron1:
        return ("pron_person1", "info", "p1",
                "本候选使用第一人称：叙述视角会从第三人称跳到第一人称")
    if feat.pron_other and "她" in _window(occ):
        return ("pron_gender", "warn", "p3o",
                "本候选用「他/它」指代，而本处上下文以「她」指代主角："
                "替换后同句出现两种性别/范畴的指代")
    if feat.form != req.form:
        # 只差"引号收尾 vs 裸名词"属于信息级（引号跟着搬走而已）；
        # 「的」字定语式/名词短语/谓词性小句之间的差别会让句子接不通，算告警。
        level = "info" if FORM_QUOTE in (req.form, feat.form) else "warn"
        return ("slot_form", level, f"{req.form}->{feat.form}",
                f"同一槽位的候选形态不一致：本处标记自称{FORM_LABEL[req.form]}"
                f"（原文『{occ['inline']}』），而池内候选是{FORM_LABEL[feat.form]}")
    return None


def check_grammar(style_name, occurrences, pools, origins, findings):
    """类型替换的组合级语法检查：在每个标记出现处，把它和整池候选在边界上比对。

    只在标记边界上比较特征，不做 候选×模板 的笛卡尔展开；内容特征按字符串全局缓存，
    相同的 (池, 槽位上下文, 候选形态) 判定天然合并。复杂度 O(槽位 × 池内候选)。
    返回统计信息，供报告里体现"实际比对了多少次、缓存省了多少"。
    """
    stats = {"slots": 0, "candidates": 0, "feature_cache": 0, "rules": Counter()}
    if not occurrences:
        return stats

    cache = {}
    pool_forms = {}
    pool_heads = {}
    pool_tails = {}
    for pk, contents in pools.items():
        forms, heads, tails = Counter(), Counter(), Counter()
        for c in contents:
            feat = content_feature(c, cache)
            forms[feat.form] += 1
            heads[feat.head_class] += 1
            tails[feat.tail_class] += 1
        pool_forms[pk], pool_heads[pk], pool_tails[pk] = forms, heads, tails
    stats["feature_cache"] = len(cache)

    # 相邻标记：右侧是标记时，接缝落在右侧池的首字分布上
    by_entry = defaultdict(list)
    for occ in occurrences:
        by_entry[(occ["file"], occ["size_cat"], occ["coord"], occ["entry_idx"])].append(occ)
    for grp in by_entry.values():
        grp.sort(key=lambda o: o["offset"])
        for left, right in zip(grp, grp[1:]):
            if left["end"] == right["offset"]:
                left["next_tag"] = right
                right["prev_tag"] = left

    groups = {}
    max_samples = _MAX_SAMPLES[0]

    def agg(kind, level, pk, detail, prefix, sample):
        key = (kind, pk, detail)
        g = groups.get(key)
        if g is None:
            g = {"kind": kind, "level": level, "pk": pk, "prefix": prefix,
                 "count": 0, "samples": [], "seen": set(), "loc": sample["slot"]}
            groups[key] = g
        g["count"] += 1
        # 同一条候选在多个槽位上犯同样的错只留一条溯源样例，样例按候选去重后限条
        if sample["candidate"] not in g["seen"]:
            g["seen"].add(sample["candidate"])
            if len(g["samples"]) < max_samples:
                g["samples"].append(sample)
        stats["rules"][kind] += 1

    ordered = sorted(occurrences, key=lambda o: (
        o["file"], o["size_cat"], o["coord"], o["entry_idx"], o["offset"]))
    for occ in ordered:
        pk = occ["pool_key"]
        pool = pools.get(pk) or ()
        stats["slots"] += 1
        if len(pool) < 2:
            continue  # 池里只有自己，替换无从发生（运行时只会回退原文）
        req = content_feature(occ["inline"], cache)
        slot_loc = make_loc(occ["file"], occ["size_cat"], occ["coord"], occ["entry_idx"],
                            occ["offset"], f"[{occ['letter']}:{occ['num']}:{occ['inline'][:24]}]")
        for cand in sorted(pool):
            if cand == occ["inline"]:
                continue
            stats["candidates"] += 1
            feat = content_feature(cand, cache)
            verdict = _classify_combo(occ, feat, req, occ.get("next_tag"),
                                      occ.get("prev_tag"), pool_heads, pool_tails)
            if verdict is None:
                continue
            kind, level, detail, prefix = verdict
            agg(kind, level, pk, detail, prefix, {
                "candidate": cand,
                "origin": origins.get(pk, {}).get(cand),
                "slot": slot_loc,
                "rendered": _render(occ, cand),
            })

    for g in groups.values():
        pk = g["pk"]
        pool_name = f"({pk[2]},{pk[3]}) @ {pk[0]}"
        forms = pool_forms.get(pk) or {}
        form_desc = " / ".join(f"{FORM_LABEL[k]}{v}条"
                              for k, v in sorted(forms.items(), key=lambda kv: -kv[1]))
        n_cand = len(g["seen"])
        names = "、".join(f"『{s['candidate']}』" for s in g["samples"])
        # --samples 0 时不列候选名，别留下空括号
        if not names:
            cand_desc = f"涉事候选 {n_cand} 条"
        else:
            more = "" if n_cand <= len(g["samples"]) else f" 等 {n_cand} 条"
            cand_desc = f"涉事候选 {n_cand} 条（{names}{more}）"
        extra = {
            "count": g["count"],
            "distinct_candidates": n_cand,
            "pool": pool_name,
            "pool_size": len(pools.get(pk) or ()),
            "pool_forms": form_desc,
            "candidates": [s["candidate"] for s in g["samples"]],
            "samples": [
                {"candidate": s["candidate"],
                 "origin": s["origin"],
                 "slot": s["slot"],
                 "rendered": s["rendered"]}
                for s in g["samples"]],
        }
        findings.append(Finding(
            g["level"], g["kind"],
            f"{g['prefix']}；池 {pool_name} 共 {extra['pool_size']} 条候选（{form_desc}），"
            f"{cand_desc}，共命中 {g['count']} 组组合",
            g["loc"], extra))

    # 池级：候选"是否显式带主语「她」"不一致 —— 替换后同句主语的显隐会跳
    for pk, contents in sorted(pools.items()):
        if len(contents) < 2:
            continue
        hers = sorted(c for c in contents if content_feature(c, cache).pron_her)
        if not hers or len(hers) == len(contents):
            continue
        sample_loc = (origins.get(pk, {}).get(hers[0]) or
                      make_loc(f"{style_name}.json", pk[0]))
        findings.append(Finding(
            "info", "pool_pronoun",
            f"池 ({pk[2]},{pk[3]}) @ {pk[0]} 的 {len(contents)} 条候选里，"
            f"{len(hers)} 条显式写出主语「她」（{len(contents) - len(hers)} 条不带人称代词）："
            f"替换后同一槽位的主语有时显、有时隐，指代表达不统一",
            sample_loc,
            {"count": len(hers), "distinct_candidates": len(hers),
             "pool": f"({pk[2]},{pk[3]}) @ {pk[0]}",
             "pool_size": len(contents),
             "her_candidates": hers[:6]}))
    return stats


def analyze_style(style_name, data, findings, with_grammar=True, size_filter=None):
    """对一个已解析的风格 JSON 跑全部检查（可限定单个尺寸档）。

    返回 (types_info, used_letters, used_nums, confusing, grammar_stats)。
    main 与 --selftest 共用这一条路径，避免自检与真实运行两套口径。
    """
    meta = data.get("_meta") if isinstance(data, dict) else None
    types_info = check_meta(f"{style_name}.json", meta, findings)
    pools, numset, used_letters, origins, occurrences = scan_style(
        style_name, data, findings, size_filter=size_filter)
    confusing, used_nums, declared = check_replacement(
        style_name, pools, numset, types_info, findings)
    scope = f"尺寸档 {size_filter}" if size_filter else "本风格"
    check_usage(style_name, used_letters, used_nums, types_info, declared, findings,
                scope=scope)
    stats = check_grammar(style_name, occurrences, pools, origins, findings) if with_grammar else {}
    return types_info, used_letters, used_nums, confusing, stats


def _selftest_fixture():
    """内存里构造的黄金样本：每个池只暴露一类问题，另有干净池做反例。

    不落盘、不碰 data/（描述目录是用户数据区，测试产物一律不进）。
    """
    def item(text):
        return {"text": text, "style": "SelfTest", "step": 0.1}

    def clean_item(text):
        return {"text": text, "style": "CleanTest", "step": 0.1}

    meta = {"custom_types": {
        lt: {"name": f"自定义{lt}", "subtypes": ["1", "2", "3", "4"],
             "allow_confusion": False} for lt in CUSTOM_LETTERS}}
    dirty = {
        "small": {
            # a1：标记后紧跟「的」，同池候选自身以「的」收尾 → 的的
            "1_1": [item("她托着[a:1:光洁的]下巴。[summary:s]")],
            "1_2": [item("她托着[a:1:光洁]的面颊。[summary:s]")],
            # a2：候选收尾是逗号，槽位后紧跟句号 → 标点叠用
            "1_3": [item("结构在瞬间[a:2:整体崩断]。[summary:s]")],
            "1_4": [item("结构在瞬间[a:2:整体崩断、]。[summary:s]")],
            # a3：候选自带句末标点，槽位后还有正文 → 句子被截断
            "2_1": [item("她注视着[a:3:那块招牌]久久不语。[summary:s]")],
            "2_2": [item("她注视着[a:3:那块招牌。][summary:s]")],
            # a4：候选引号不成对
            "2_3": [item("她念出[a:4:那句“咒语”]。[summary:s]")],
            "2_4": [item("她念出[a:4:那句“咒语]。[summary:s]")],
            # c1：候选使用第一人称
            "3_1": [item("{name}望着[c:1:远处的塔]。[summary:s]")],
            "3_2": [item("{name}望着[c:1:我熟悉的塔]。[summary:s]")],
            # c2 + e4：相邻两个标记，左侧候选收尾是「的」、右侧候选开头是「的」→ 的的
            "3_3": [item("她将[c:2:整排橱窗][e:4:瞬间化为齑粉]。[summary:s]")],
            "3_4": [item("她将[c:2:成群结队的][e:4:的确如此]。[summary:s]")],
            # e1：候选直接称呼读者
            "4_1": [item("{name}低语着[e:1:某种共鸣]。[summary:s]")],
            "4_2": [item("{name}低语着[e:1:你听不见的共鸣]。[summary:s]")],
            # e2：候选以「他/它」指代，而上下文用「她」
            "4_3": [item("她感到[e:2:一阵眩晕]，随即闭上了眼。[summary:s]")],
            "4_4": [item("她感到[e:2:他们的目光]，随即闭上了眼。[summary:s]")],
        },
        # e3：同池候选"是否显式带主语她"不一致 → 池级 info
        "medium": {"1_1": [item("她看着[e:3:废墟]。[summary:s]"),
                           item("她看着[e:3:她脚下的废墟]。[summary:s]")]},
        "_meta": meta,
    }
    clean = {
        "small": {
            # b1：同一池两条都是名词短语，形态一致
            "1_1": [clean_item("{name}选择了[b:1:站直]。[summary:s]")],
            "1_2": [clean_item("{name}选择了[b:1:侧身]。[summary:s]")],
            # b2：同一池两条都以「的」收尾，接缝也干净
            "1_3": [clean_item("{name}露出[b:2:温和的]表情。[summary:s]")],
            "1_4": [clean_item("{name}露出[b:2:柔和的]表情。[summary:s]")],
        },
        "_meta": {"custom_types": {
            lt: {"name": f"自定义{lt}", "subtypes": ["1", "2"], "allow_confusion": False}
            for lt in CUSTOM_LETTERS}},
    }
    return dirty, clean


def run_selftest(verbose=False):
    """逐条验证组合级语法检查的 9 类规则，并确认干净池一条都不报。"""
    dirty, clean = _selftest_fixture()
    expected = {
        "seam_de": "标记后紧跟「的」与候选自身的「的」叠置（含相邻标记两侧）",
        "seam_punct": "候选收尾的标点与模板标点叠用",
        "content_sent_punct": "候选自带句末标点把句子截断",
        "quote_unbalanced": "候选引号不成对",
        "pron_person1": "候选使用第一人称",
        "pron_person2": "候选直接称呼读者",
        "pron_gender": "候选用「他/它」而上下文用「她」",
        "pool_pronoun": "同池候选是否带主语「她」不一致",
        "slot_form": "同槽位候选形态不一致",
    }
    ok = True

    findings = []
    analyze_style("SelfTest", dirty, findings)
    kinds = {f.kind for f in findings
             if f.kind in expected or f.kind.startswith(("seam_", "pron_"))}
    for kind, desc in sorted(expected.items()):
        hit = kind in kinds
        ok = ok and hit
        flag = "OK  " if hit else "MISS"
        print(f"  [{flag}] {kind:<20} {desc}")
        for f in findings:
            if f.kind == kind and verbose:
                print(f"         {f.level} {f.message}")

    clean_findings = []
    analyze_style("CleanTest", clean, clean_findings)
    grammar_clean = [f for f in clean_findings
                     if f.kind in expected or f.kind.startswith(("seam_", "pron_", "quote_"))]
    if grammar_clean:
        ok = False
        print(f"  [FAIL] 干净池不该报组合级问题，却出现 {len(grammar_clean)} 条："
              + "、".join(sorted({f.kind for f in grammar_clean})))
    else:
        print("  [OK  ] 反例池（形态一致的池 / 干净接缝）零误报")

    # 特征缓存确实生效：同一段文本只分析一次
    cache = {}
    probe = ["温和的", "温和的", "柔和的"]
    for t in probe:
        content_feature(t, cache)
    if len(cache) == 2:
        print("  [OK  ] 内容特征缓存：3 次取特征只落 2 条（同字符串不重复分析）")
    else:
        ok = False
        print(f"  [FAIL] 内容特征缓存异常：期望 2 条，得到 {len(cache)} 条")

    print("[quip-check][selftest] " + ("全部通过" if ok else "存在失败项"))
    return 0 if ok else 1


def print_group_menu(data_dir, styles):
    """打印"可选分组清单"：每个风格下各尺寸档的坐标数/条目数/标记数。

    只为挑一组来跑服务的——不做任何检查，不产生 findings。
    """
    print(f"[quip-check] 描述目录：{data_dir}")
    print("[quip-check] 一次只检查一个风格的一个尺寸分组。可选分组：")
    any_group = False
    for style in styles:
        path = os.path.join(data_dir, f"{style}.json")
        data, err = load_style_file(path)
        if err:
            print(f"  ◆ {style}：读不了（{err}）")
            continue
        if not isinstance(data, dict):
            print(f"  ◆ {style}：顶层不是对象")
            continue
        rows = []
        for size in SIZE_CATS:
            matrix = data.get(size)
            if not isinstance(matrix, dict) or not matrix:
                continue
            entries = tags = 0
            for quip_list in matrix.values():
                if not isinstance(quip_list, list):
                    continue
                entries += len(quip_list)
                for item in quip_list:
                    text = item if isinstance(item, str) else (
                        item.get("text") if isinstance(item, dict) else None)
                    if isinstance(text, str):
                        tags += len(TAG_RE.findall(text))
            rows.append((size, len(matrix), entries, tags))
        if not rows:
            print(f"  ◆ {style}：无已知尺寸档数据")
            continue
        print(f"  ◆ {style}")
        for size, coords, entries, tags in rows:
            print(f"      {size:<9} 坐标 {coords:>3} 个 ｜ 条目 {entries:>4} 条 ｜ 标记 {tags:>4} 处")
        any_group = True
    if any_group:
        first = styles[0] if styles else "风格名"
        print(f"[quip-check] 例：python developer_tools/quip_report.py {first} small")
    return 0


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="quip 类型 meta 读取 + 类型替换静态检查 + 疑似语法问题溯源")
    parser.add_argument("styles", nargs="*",
                        help="要检查的风格名（不带 .json）；尺寸档也可写成第二个位置参数")
    parser.add_argument("-s", "--size", choices=SIZE_CATS, default=None,
                        help=f"只检查该尺寸档：{'/'.join(SIZE_CATS)}")
    parser.add_argument("--data-dir", default=None,
                        help="描述目录（含 *.json），缺省为 <仓库根>/data/packs/quips")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    parser.add_argument("--all", action="store_true",
                        help="展开 info 级问题（缺省清单只列 error/warn，摘要仍报 info 数）")
    parser.add_argument("--no-grammar", action="store_true",
                        help="跳过类型替换的组合级语法检查（只看格式与 meta）")
    parser.add_argument("--samples", type=int, default=3,
                        help="每条聚合结论最多列出的溯源样例数（缺省 3）")
    parser.add_argument("--selftest", action="store_true",
                        help="用内存构造的黄金样本自检 9 类组合级规则（不读 data/）")
    parser.add_argument("-v", "--verbose", action="store_true", help="自检时打印命中详情")
    args = parser.parse_args()
    _MAX_SAMPLES[0] = max(0, args.samples)

    if args.selftest:
        return run_selftest(verbose=args.verbose)

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = args.data_dir or os.path.join(repo_root, "data", "packs", "quips")
    if not os.path.isdir(data_dir):
        print(f"[quip-check] 描述目录不存在：{data_dir}")
        return 2

    all_styles = sorted(f[:-5] for f in os.listdir(data_dir)
                        if f.endswith(".json") and not f.endswith(".addr.json"))
    styles = list(args.styles)
    size = args.size
    # 尺寸档写成第二个位置参数也认：`… PixelGame small` 与 `… PixelGame -s small` 等价
    if size is None and len(styles) >= 2 and styles[-1] in SIZE_CATS:
        size = styles.pop()

    missing = [s for s in styles if not os.path.exists(os.path.join(data_dir, f"{s}.json"))]
    for s in missing:
        print(f"[quip-check] 找不到风格文件：{os.path.join(data_dir, s + '.json')}")
    styles = [s for s in styles if s not in missing]

    if len(styles) > 1:
        print(f"[quip-check] 一次只检查一个风格，收到 {len(styles)} 个：{'、'.join(styles)}")
        print()
        print_group_menu(data_dir, styles)
        return 2
    if not styles or size is None:
        # 没指明"哪个风格的哪一档"：只给清单，不跑全量（全量结论读不动）
        print_group_menu(data_dir, styles or all_styles)
        return 0
    style = styles[0]

    # 全局混淆开关上下文（仅用于解读 meta，不改变静态检查结论）
    settings_path = os.path.join(repo_root, "data", "user", "settings.json")
    enable_confusion = None
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            enable_confusion = json.load(f).get("enable_confusion")
    except (OSError, json.JSONDecodeError):
        pass

    findings = []
    meta_report = {}
    grammar_stats = Counter()
    grammar_skipped = args.no_grammar

    path = os.path.join(data_dir, f"{style}.json")
    data, err = load_style_file(path)
    if err:
        print(f"[quip-check] 风格 {style}：{err}")
        return 2
    types_info, used_letters, used_nums, confusing, st = analyze_style(
        style, data, findings, with_grammar=not grammar_skipped, size_filter=size)
    if st:
        for k, v in st.items():
            if k != "rules":
                grammar_stats[k] += v
        for k, v in st["rules"].items():
            grammar_stats[f"rule:{k}"] += v
    if not isinstance(data, dict):
        return 2
    meta_report[style] = {
        "types": {lt: (dict(types_info[lt], used=used_letters[lt],
                           nums=sorted(used_nums[lt], key=int))
                       if types_info.get(lt)
                       else ({"name": {"a": "衣着", "b": "姿势"}[lt],
                              "subtypes": BUILTIN_SUBTYPES[lt],
                              "allow_confusion": False,
                              "used": used_letters[lt],
                              "nums": sorted(used_nums[lt], key=int)}
                             if lt in BUILTIN_SUBTYPES and used_letters[lt] else None))
                  for lt in "abcde"},
        "confusing_types": confusing,
        "tag_total": sum(used_letters.values()),
    }

    counts = {"error": 0, "warn": 0, "info": 0}
    for f in findings:
        counts[f.level] += 1
    findings.sort(key=lambda f: (LEVEL_ORDER[f.level], f.kind, json.dumps(f.loc, ensure_ascii=False)))
    # info 级缺省不展开（--all 才列）；计数照常统计，避免"悄悄少报"
    shown = findings if args.all else [f for f in findings if f.level != "info"]
    hidden_info = counts["info"] if not args.all else 0

    if args.json:
        print(json.dumps({
            "data_dir": data_dir,
            "style": style,
            "size": size,
            "enable_confusion": enable_confusion,
            "all_levels": args.all,
            "meta": meta_report,
            "grammar_stats": dict(grammar_stats),
            "findings": [f.as_dict() for f in shown],
            "hidden_info": hidden_info,
            "counts": counts,
        }, ensure_ascii=False, indent=2))
    else:
        print(f"[quip-check] 描述目录：{data_dir}")
        print(f"[quip-check] 检查范围：风格 {style} ｜ 尺寸档 {size}（只此一组）")
        if enable_confusion is not None:
            print(f"[quip-check] 全局设置 enable_confusion = {enable_confusion}")
        else:
            print("[quip-check] 未读到 data/user/settings.json 的 enable_confusion（运行时缺省 False）")
        print("[quip-check] ===== 类型 meta 配置 =====")
        for st_name, rep in meta_report.items():
            print(f"  ◆ {st_name}（标记总数 {rep['tag_total']}）")
            for lt in "abcde":
                info = rep["types"][lt]
                if info is None:
                    if lt in CUSTOM_LETTERS:
                        print(f"    {lt}: 未在 _meta 声明")
                    continue
                ac = info["allow_confusion"]
                ac_str = {True: "可混淆", False: "不可混淆", None: "未声明"}[ac]
                nums = ",".join(info["nums"]) or "-"
                print(f"    {lt}: {info['name']} | 细分{len(info['subtypes'])}个 | {ac_str}"
                      f" | 用到编号: {nums}")
            if rep["confusing_types"]:
                print(f"    混淆实际生效的类型（当 enable_confusion 开启）: "
                      f"{','.join(rep['confusing_types'])}")
        if grammar_skipped:
            print("[quip-check] 类型替换·组合级语法检查：已跳过（--no-grammar）")
        else:
            print("[quip-check] ===== 类型替换·组合级语法检查 =====")
            print(f"  槽位 {grammar_stats['slots']} 处；候选比对 {grammar_stats['candidates']} 次"
                  f"（按内容字符串缓存 {grammar_stats['feature_cache']} 条特征，"
                  f"未展开 候选×模板 的笛卡尔积）")
            hit = {k[5:]: v for k, v in grammar_stats.items() if k.startswith("rule:")}
            if hit:
                print("  命中组合数（聚合前）：" + "；".join(
                    f"{k}×{v}" for k, v in sorted(hit.items(), key=lambda kv: -kv[1])))
            else:
                print("  未命中任何组合级问题")
        print(f"[quip-check] ===== 问题清单（风格 {style} / 尺寸档 {size}）=====")
        if not shown:
            print("  未发现问题" if not hidden_info else "  本档无 error/warn")
        for f in shown:
            print(f"  [{f.level.upper()}][{f.kind}] {format_loc(f.loc)}")
            print(f"      {f.message}"
                  + (f" 片段：{f.loc['snippet']}" if f.loc and "snippet" in f.loc else ""))
            for line in format_extra(f.extra):
                print(line)
        tail = ""
        if hidden_info:
            tail = f"（另有 info={hidden_info} 条未展开，加 --all 显示）"
        print(f"[quip-check] 汇总：error={counts['error']} warn={counts['warn']} "
              f"info={counts['info']}{tail}")
        if any(f.extra and f.extra.get("samples") for f in shown):
            print(f"[quip-check] 每条组合结论最多列 {_MAX_SAMPLES[0]} 条样例"
                  f"（--samples N 调整，--samples 0 只要结论）")

    return 1 if counts["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
