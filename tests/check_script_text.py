"""S4 自检：固定文本的段落切分与指令行解析（无 GUI，可进 CI 门禁）。

用法：``python tests/check_script_text.py``
输出：控制台一行 ASCII 结论。

覆盖：
1. 空行分段：逻辑段落 = 一次步进，段内换行合成同一段正文；
2. 指令行（段落首行独占一行）：类型 + 通用的方向 / 指名方向 / 紧凑写法；
3. ``complete``：类型与方向都写全才演化，缺任一项只推进步数；
4. 解析容错：未识别记号 / 越界方向 / 只有指令没正文——警告而不中断，
   指令行只能独占一行（写在正文里当普通文字）；
5. ``describe_directive`` 渲染（编辑器预览与诊断文案共用）；
6. ``load_script_file`` 编码容错（utf-8 / gbk）；
7. 接线：``EvolutionRules.evolve_attributes`` 的 ``builtin_directions``
   真的把介入度 / 破坏性两路拆开，且缺的一路回退到通用 ``direction``。
"""

import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dungeon import script_text  # noqa: E402
from dungeon.models import DungeonState, DungeonTextType  # noqa: E402
from dungeon.rules import EvolutionRules  # noqa: E402

failures = []
total = 0


def check(name, ok, extra=""):
    global total
    total += 1
    print(("  OK   " if ok else "  FAIL ") + name + (f"  <{extra}>" if extra and not ok else ""))
    if not ok:
        failures.append(name)


def _paragraph(text):
    return script_text.parse_script(text).paragraphs


# ---------------- 1. 空行分段 ----------------
check("空文本没有段落", _paragraph("") == () and _paragraph("   \n\n  ") == ())
check("空行分段成两个逻辑段落",
      len(_paragraph("第一段。\n\n第二段。")) == 2)
check("段内换行不拆段（合成为同一段正文）",
      _paragraph("上句。\n下句。")[0].text == "上句。\n下句。")
check("段序号从 1 开始且连续",
      [p.index for p in _paragraph("A\n\nB\n\nC")] == [1, 2, 3])
check("段落行号指向段落首行",
      [p.line for p in _paragraph("A\n\nB\n\nC")] == [1, 3, 5])
check("CRLF 归一后同样分段",
      len(_paragraph("A\r\n\r\nB")) == 2 and _paragraph("A\r\n\r\nB")[0].text == "A")
check("无指令行时是 EMPTY_DIRECTIVE（不演化）",
      _paragraph("普通叙述。")[0].directive is script_text.EMPTY_DIRECTIVE)

# ---------------- 2. 指令行 ----------------
_script = script_text.parse_script(
    "[对话 +1]\n她低下头。\n\n"
    "[互动 破坏性-1]\n她的手掌轻轻落下。\n\n"
    "[环境]\n远处有雾。\n\n"
    "[+1]\n什么都没写。\n\n"
    "[对话+1]\n紧凑写法。\n\n"
    "[行动 热度+1 破坏性-1]\n她抬手又收回。"
)
check("指令行被剥离、正文只剩脚本", not _script.warnings
      and all(not p.text.startswith("[") for p in _script.paragraphs), _script.warnings)

_d0 = _script.paragraphs[0].directive
check("类型 + 通用方向",
      _d0.text_type is DungeonTextType.DIALOG and _d0.direction == 1
      and _d0.attr_directions == {} and _d0.has_direction and _d0.complete, _d0)

_d1 = _script.paragraphs[1].directive
check("类型 + 指名方向（破坏性）",
      _d1.text_type is DungeonTextType.INTERACTION
      and _d1.attr_directions == {"破坏性": -1} and _d1.direction == 0
      and _d1.builtin_directions == {"破坏性": -1} and _d1.custom_directions == {}
      and _d1.complete, _d1)

_d2 = _script.paragraphs[2].directive
check("只有类型：不演化",
      _d2.text_type is DungeonTextType.BACKGROUND and not _d2.has_direction
      and not _d2.complete, _d2)

_d3 = _script.paragraphs[3].directive
check("只有方向：不演化",
      _d3.text_type is None and _d3.direction == 1 and not _d3.complete, _d3)

_d4 = _script.paragraphs[4].directive
check("紧凑写法 [对话+1] 等价 [对话 +1]",
      _d4.text_type is DungeonTextType.DIALOG and _d4.direction == 1
      and _d4.complete, _d4)

_d5 = _script.paragraphs[5].directive
check("类型 + 自定义 + 指名内置（两路拆开）",
      _d5.text_type is DungeonTextType.ACTION
      and _d5.builtin_directions == {"破坏性": -1}
      and _d5.custom_directions == {"热度": 1} and _d5.complete, _d5)

# 类型别名：中文短名与英文键
check("别名：背景 == 环境",
      _paragraph("[背景]\nx")[0].directive.text_type is DungeonTextType.BACKGROUND)
check("别名：英文键同样认",
      _paragraph("[dialog +1]\nx")[0].directive.text_type is DungeonTextType.DIALOG)
check("别名：五类短名齐全",
      all(_paragraph(f"[{short}]\nx")[0].directive.text_type is member
          for member, short in script_text.TYPE_SHORT_NAMES.items()))
check("缩进的指令行也认（行首空白不算正文）",
      _paragraph("   [对话 +1]   \n她开口。")[0].directive.text_type
      is DungeonTextType.DIALOG)
check("写的不是独占一行的方括号 → 当普通正文",
      _paragraph("他说[对话 +1]之后在想。")[0].directive
      is script_text.EMPTY_DIRECTIVE)

# ---------------- 3. 容错 ----------------
_bad = script_text.parse_script("[对话 乱写]\n内容。")
_w = _bad.warnings[0] if _bad.warnings else None
check("未识别记号：报警告且不影响已识别的类型",
      _w is not None and _w.line == 1
      and _bad.paragraphs[0].directive.text_type is DungeonTextType.DIALOG
      and not _bad.paragraphs[0].directive.complete, _bad.warnings)

_oor = script_text.parse_script("[对话 破坏性+5]\n内容。")
check("方向越界：报警告并丢弃该方向（不演化、不保留脏值）",
      _oor.paragraphs[0].directive.attr_directions == {}
      and not _oor.paragraphs[0].directive.has_direction
      and any("超出范围" in w.message for w in _oor.warnings), _oor.warnings)

_nobody = script_text.parse_script("[对话 +1]\n\n正文。")
check("指令行没有正文：该段被忽略并报警告",
      any("没有正文" in w.message for w in _nobody.warnings)
      and [p.text for p in _nobody.paragraphs] == ["正文。"], _nobody)

check("ScriptWarning.__str__ 带行号",
      str(script_text.ScriptWarning(7, "x")) == "第 7 行：x"
      and str(script_text.ScriptWarning(0, "x")) == "x")

# ---------------- 4. describe_directive ----------------
check("渲染：类型 + 通用方向", script_text.describe_directive(_d0) == "对话 +1",
      script_text.describe_directive(_d0))
check("渲染：指名方向不补多余的 0",
      script_text.describe_directive(_d1) == "互动 破坏性-1",
      script_text.describe_directive(_d1))
check("渲染：只有类型 → 提示缺方向",
      script_text.describe_directive(_d2) == "环境（缺少演化方向，不演化）",
      script_text.describe_directive(_d2))
check("渲染：只有方向 → 提示缺类型",
      script_text.describe_directive(_d3) == "+1（缺少段落类型，不演化）",
      script_text.describe_directive(_d3))
check("渲染：自定义 + 内置按声明顺序",
      script_text.describe_directive(_d5) == "行动 热度+1 破坏性-1",
      script_text.describe_directive(_d5))
check("渲染：无指令",
      script_text.describe_directive(None) == "（未声明指令，不演化）"
      and script_text.describe_directive(script_text.EMPTY_DIRECTIVE)
      == "（未声明指令，不演化）")

# ---------------- 5. load_script_file ----------------
_tmp = tempfile.mkdtemp(prefix="script_text_")
_utf8 = os.path.join(_tmp, "u.txt")
with open(_utf8, "w", encoding="utf-8") as fh:
    fh.write("[对话 +1]\n她开口。")
_loaded, _err = script_text.load_script_file(_utf8)
check("读取 utf-8 文件",
      _err == "" and _loaded is not None and len(_loaded.paragraphs) == 1
      and _loaded.paragraphs[0].directive.text_type is DungeonTextType.DIALOG,
      _err)

_gbk = os.path.join(_tmp, "g.txt")
with open(_gbk, "wb") as fh:
    fh.write("[环境]\n远处的雾很浓。".encode("gbk"))
_loaded_g, _err_g = script_text.load_script_file(_gbk)
check("读取 gbk 文件（中文不乱码）",
      _err_g == "" and _loaded_g is not None
      and _loaded_g.paragraphs[0].text == "远处的雾很浓。", _err_g or "decode")

_missing, _err_m = script_text.load_script_file(os.path.join(_tmp, "nope.txt"))
check("文件不存在：返回 None + 错误说明",
      _missing is None and _err_m != "", _err_m)

# ---------------- 6. 接线：两路方向拆开 ----------------
_persona = SimpleNamespace(step_intrusion=1.0, step_destruction=1.0,
                           sensitivity=0.0, gravity=0.0, skip_base_prob=0.0)
_rules = EvolutionRules()
_START = DungeonState(intrusion=2.0, destruction=2.0)


def _evolve(builtin_directions=None, direction=0, step_override=None):
    return _rules.evolve_attributes(
        _START, DungeonTextType.DIALOG, direction, _persona,
        builtin_directions=builtin_directions, step_override=step_override)


_after = _evolve(direction=1)
check("默认（无指名方向）：两路同向 +0.1",
      abs(_after.intrusion - 2.1) < 1e-9 and abs(_after.destruction - 2.1) < 1e-9,
      (_after.intrusion, _after.destruction))

_after = _evolve(builtin_directions={"介入度": 1, "破坏性": -1})
check("两路指名：一升一降",
      abs(_after.intrusion - 2.1) < 1e-9 and abs(_after.destruction - 1.9) < 1e-9,
      (_after.intrusion, _after.destruction))

_after = _evolve(direction=1, builtin_directions={"破坏性": -1})
check("只指名一路：另一路回退通用方向",
      abs(_after.intrusion - 2.1) < 1e-9 and abs(_after.destruction - 1.9) < 1e-9,
      (_after.intrusion, _after.destruction))

_after = _evolve(direction=1, builtin_directions={"介入度": 0, "破坏性": 0})
check("指名 0：该路不演化（不是回退通用方向）",
      abs(_after.intrusion - 2.0) < 1e-9 and abs(_after.destruction - 2.0) < 1e-9,
      (_after.intrusion, _after.destruction))

_after = _evolve(direction=1, builtin_directions={"介入度": 1, "破坏性": 1},
                 step_override=0.0)
check("步进为 0（冻结）：两路都不动、步数仍累计",
      abs(_after.intrusion - 2.0) < 1e-9 and abs(_after.destruction - 2.0) < 1e-9
      and _after.total_steps == _START.total_steps + 1,
      (_after.intrusion, _after.destruction, _after.total_steps))

# 自定义属性同样受指名方向影响（run 时 runtime 传 custom_directions）
_after = _rules.evolve_attributes(
    _START, DungeonTextType.ACTION, 1, _persona,
    custom_attrs_def=[{"name": "热度", "rate": 1.0}],
    custom_directions={"热度": -1}, step_override=0.1)
check("自定义属性按指名方向演化",
      abs(_after.custom_attrs["热度"] + 0.1) < 1e-9, _after.custom_attrs)

# ---------------- 结论 ----------------
if failures:
    print(f"[check_script_text] FAILED {len(failures)}/{total}: {failures}")
    sys.exit(1)
print(f"[check_script_text] PASSED {total}/{total}")
