"""固定文本章节的脚本文本：逻辑段落与指令行的解析。

「固定文本章节」不走 AI 生成——章节字段 ``fixed_text.path`` 指向方案目录下的
一个 ``.txt``，整篇现成文本按**逻辑段落**推进（字段索引见
``docs/Dungeon/script.md`` §2.5）：

- **空行分段**：一个逻辑段落 = 一次步进（一次点击推进），与 AI 段落同构，
  所以「节内计数 / 总计数 / 间隔计数」都以它为单位；段内正文再由内置分句器
  （``dungeon.splitter``）切成**显示段落**逐句上屏——与 AI 段落同一套呈现。

- **指令行**：段落首行可以独占一行写一条方括号指令（只解析、不上屏），同时
  声明*段落类型*与*演化方向*::

      [对话 +1]
      她低下头，看着掌心里的你，第一次没有把你当作普通人。

      [互动 破坏性-1]
      她的手掌轻轻落下，地面没有裂开。

  段落类型决定该段按哪种节奏推进（步进值 / 伤亡 / 类型语义），取值是五种
  段落类型的中文短名（环境、分支、对话、互动、行动；英文键也接受）。
  演化方向决定坐标往哪走：

  * ``+1`` / ``-1`` / ``0``：同时作用于介入度与破坏性（与 AI 协议的
    ``direction`` 一致）；
  * ``介入度+1`` / ``破坏性-1``：只改其中一路；
  * ``热度+1``：改 ``evolution_attrs`` 里声明的自定义属性。

  类型与方向可以合并写成一个记号（``[对话+1]``），等价于 ``[对话 +1]``。

**只有类型与方向都写全的段落才参与属性演化**（``Directive.complete``）；缺任
一项的段落只推进步数：不改变坐标、自定义属性与步长，也不产生伤亡——让纯叙述
段落安静地过去，需要她长起来的地方才写指令。

解析是纯函数（无 IO、无 UI、无 AI）；文件读取见 :func:`load_script_file`。
未识别的记号不会让解析失败：它们变成 :class:`ScriptWarning`，由校验器
（``dungeon/validate.py``）报给作者，运行时忽略该项。
"""

import re
from typing import NamedTuple, Optional

from dungeon.models import DungeonTextType

#: 段落类型短名 -> 类型。中文短名与英文键都认（``背景`` / ``环境`` 同义）。
TYPE_ALIASES = {
    "环境": DungeonTextType.BACKGROUND,
    "背景": DungeonTextType.BACKGROUND,
    "分支": DungeonTextType.BRANCH,
    "对话": DungeonTextType.DIALOG,
    "互动": DungeonTextType.INTERACTION,
    "行动": DungeonTextType.ACTION,
}
TYPE_ALIASES.update({member.value: member for member in DungeonTextType})

#: 类型反查（编辑器提示与诊断文案用；``背景`` / ``环境`` 取规范的那个）。
TYPE_SHORT_NAMES = {
    DungeonTextType.BACKGROUND: "环境",
    DungeonTextType.BRANCH: "分支",
    DungeonTextType.DIALOG: "对话",
    DungeonTextType.INTERACTION: "互动",
    DungeonTextType.ACTION: "行动",
}

#: 可以直接写在方向记号里的**内置**坐标名；其余名字一律按自定义属性处理。
BUILTIN_DIRECTION_NAMES = ("介入度", "破坏性")

#: 方向取值域（与 AI 协议的 ``direction`` 一致）
DIRECTION_MIN, DIRECTION_MAX = -1, 1

#: 解析方向时报告越界 / 记号无法识别；解析方负责打印行号。
DIRECTION_HINT = "方向只能是 +1 / -1 / 0，或写成「属性名+1」（如 破坏性-1）"

# 独占一行的指令行： ``[ ... ]``（括号内不含 ] 与换行）
_DIRECTIVE_LINE_RE = re.compile(r"^\s*\[(?P<body>[^\]\n]*)\]\s*$")
# 方向记号：可选属性名前缀 + 带符号整数（``+1`` / ``-1`` / ``0`` / ``破坏性+1``）
_DIRECTION_TOKEN_RE = re.compile(r"^(?P<name>[^\d+\-\s]*)(?P<value>[+\-]?\d+)$")


class Directive(NamedTuple):
    """一条指令行的解析结果（没有指令行时是 :data:`EMPTY_DIRECTIVE`）。"""

    #: 段落类型；``None`` 表示该段没有声明类型
    text_type: Optional[DungeonTextType]
    #: 通用方向：介入度与破坏性同时适用（``attr_directions`` 里指名的那一路优先）
    direction: int
    #: 指名方向：``{属性名: -1/0/1}``，属性名可以是内置坐标或自定义属性
    attr_directions: dict
    #: 是否至少写出了一个方向记号（``+1`` / ``破坏性-1`` 都算）
    has_direction: bool

    @property
    def complete(self) -> bool:
        """类型与方向都写全——只有这种段落参与属性演化。"""
        return self.text_type is not None and self.has_direction

    @property
    def builtin_directions(self) -> dict:
        """指名方向里作用于介入度 / 破坏性的部分。"""
        return {name: value for name, value in self.attr_directions.items()
                if name in BUILTIN_DIRECTION_NAMES}

    @property
    def custom_directions(self) -> dict:
        """指名方向里作用于自定义属性的部分。"""
        return {name: value for name, value in self.attr_directions.items()
                if name not in BUILTIN_DIRECTION_NAMES}


#: 完全没有指令行时的占位（方便运行时少写一堆 None 判断）
EMPTY_DIRECTIVE = Directive(text_type=None, direction=0, attr_directions={},
                            has_direction=False)


class ScriptParagraph(NamedTuple):
    """一个逻辑段落：一次步进 + 它的显示正文。"""

    #: 段序号（从 1 开始，供诊断引用）
    index: int
    #: 段落首行（指令行，若有）在文件中的行号，从 1 起
    line: int
    #: 正文（已剥离指令行）
    text: str
    directive: Directive


class ScriptWarning(NamedTuple):
    """一条解析警告；``line`` 为 1 起的行号，0 表示文件级。"""

    line: int
    message: str

    def __str__(self) -> str:
        return f"第 {self.line} 行：{self.message}" if self.line else self.message


class Script(NamedTuple):
    """一整篇固定文本。"""

    paragraphs: tuple       #: tuple[ScriptParagraph, ...]
    warnings: tuple         #: tuple[ScriptWarning, ...]


# ---------------- 指令行 ----------------


def _record_direction(name, value, line, warnings):
    """把 (属性名, 值) 归一进 [DIRECTION_MIN, DIRECTION_MAX]；越界报警告并丢弃。"""
    if not DIRECTION_MIN <= value <= DIRECTION_MAX:
        warnings.append(ScriptWarning(
            line, f"方向「{name}{value:+d}」超出范围（{DIRECTION_HINT}）"))
        return None
    return value


def _parse_directive(body: str, line: int, warnings: list) -> Directive:
    """解析一条指令行；未识别的记号只记警告，不影响其余部分。"""
    text_type = None
    direction = 0
    attr_directions = {}
    has_direction = False
    for token in body.split():
        if token in TYPE_ALIASES:
            text_type = TYPE_ALIASES[token]
            continue
        match = _DIRECTION_TOKEN_RE.match(token)
        if not match:
            warnings.append(ScriptWarning(
                line, f"无法识别的指令记号「{token}」（{DIRECTION_HINT}）"))
            continue
        name = match.group("name")
        value = int(match.group("value"))
        # 紧凑写法 ``[对话+1]``：记号前缀是段落类型时，方向按通用方向处理
        if name in TYPE_ALIASES:
            if text_type is None:
                text_type = TYPE_ALIASES[name]
            name = ""
        value = _record_direction(name, value, line, warnings)
        if value is None:
            continue
        has_direction = True
        if name:
            attr_directions[name] = value
        else:
            direction = value
    return Directive(text_type=text_type, direction=direction,
                     attr_directions=attr_directions, has_direction=has_direction)


def describe_directive(directive: Optional[Directive]) -> str:
    """把指令渲染成一行短描述（编辑器预览与诊断文案共用）。"""
    if directive is None or (directive.text_type is None
                             and not directive.has_direction):
        return "（未声明指令，不演化）"
    parts = [TYPE_SHORT_NAMES.get(directive.text_type, directive.text_type.value)] \
        if directive.text_type is not None else []
    # 通用方向只有「作者真的写了」或「没有任何指名方向」时才展示，
    # 免得 ``[对话 破坏性-1]`` 被补出一个多余的 0
    if directive.direction or (directive.has_direction and not directive.attr_directions):
        parts.append(f"{directive.direction:+d}")
    for name, value in directive.attr_directions.items():
        parts.append(f"{name}{value:+d}")
    text = " ".join(parts) or "（未声明指令，不演化）"
    if not directive.complete:
        type_missing = directive.text_type is None
        direction_missing = not directive.has_direction
        missing = " 与 ".join(
            [label for label, flag in (("段落类型", type_missing),
                                       ("演化方向", direction_missing)) if flag])
        text += f"（缺少{missing}，不演化）"
    return text


# ---------------- 段落切分 ----------------


def _build_paragraph(entries: list, paragraphs: list, warnings: list) -> None:
    """把若干连续非空行合成一个逻辑段落（首行可是指令行）。"""
    first_line_no, first_line = entries[0]
    match = _DIRECTIVE_LINE_RE.match(first_line)
    if match:
        directive = _parse_directive(match.group("body"), first_line_no, warnings)
        body_lines = entries[1:]
    else:
        directive = EMPTY_DIRECTIVE
        body_lines = entries
    text = "\n".join(line for _no, line in body_lines).strip()
    if not text:
        warnings.append(ScriptWarning(first_line_no, "指令行没有正文，该段落被忽略"))
        return
    paragraphs.append(ScriptParagraph(index=len(paragraphs) + 1,
                                     line=first_line_no, text=text,
                                     directive=directive))


def parse_script(text: str) -> Script:
    """把一篇固定文本解析成逻辑段落列表（纯函数，可重复调用）。"""
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = []
    warnings = []
    buffer = []

    def flush():
        if buffer:
            _build_paragraph(buffer, paragraphs, warnings)
            buffer.clear()

    for lineno, raw in enumerate(normalized.split("\n"), 1):
        if raw.strip():
            buffer.append((lineno, raw))
        else:
            flush()
    flush()
    return Script(paragraphs=tuple(paragraphs), warnings=tuple(warnings))


def load_script_file(path: str):
    """读取并解析固定文本文件。

    编码按 ``utf-8 → gbk → utf-8(容错替换)`` 依次尝试：现成的中文文本很可能是
    GBK，读不动就退到容错解码——**少数字符变问号，总比整章读不出来好**。
    返回 ``(Script|None, 错误说明)``；成功时错误说明为空串。
    """
    for encoding in ("utf-8", "gbk"):
        try:
            with open(path, "r", encoding=encoding) as handle:
                return parse_script(handle.read()), ""
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            return None, f"读取失败：{exc}"
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return parse_script(handle.read()), ""
    except OSError as exc:
        return None, f"读取失败：{exc}"
