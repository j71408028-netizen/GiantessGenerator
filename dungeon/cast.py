"""副本人物表（cast）：配角设定与首次出场线索。

表里只有配角，**不放她、也不放主角**：

- 她：本局角色档案（跨方案复用）；
- 主角：方案级 ``protagonist_title`` + ``protagonist_background``，唯一且视角固定；
- 配角：本表的 ``key``（完整身份背景）与 ``extra``（作用 / 代词 / 说话方式三条线索）。

两档的字段不同：重要角色写身份背景，群像写作用与说话方式；别称与「称她为」只属于
重要角色。可用字段见 ``ROLE_FIELDS``。

表里所有描述都是**故事开始时的样子**，不是状态：谁死了、谁反目了不改这里，
那是 ``StorySummarizer`` 的 facts。同理，首次出场时由 AI 补的线索也不回写配置。
"""

import re

#: 定位：``key`` = 有完整身份背景的重要角色，``extra`` = 只有三条线索的群像
CAST_ROLES = ("key", "extra")
DEFAULT_CAST_ROLE = "key"

#: 代词只有这两档参与渲染与音色分池；空串 = 不指定（不分性别，不猜）
CAST_PRONOUNS = ("", "他", "她")

#: 各档可用的描述字段（``id`` / ``role`` / ``name`` 两档都有，不列）。
#: 渲染与编辑器共用这张表决定字段的去留。
ROLE_FIELDS = {
    "key": ("intro_as", "aliases", "pronoun", "background", "call_her", "voice"),
    "extra": ("pronoun", "purpose", "speech", "voice"),
}

#: 重要角色的建议上限。超了不报错，只提示——上限的用途是防提示词被挤爆
MAX_KEY_MEMBERS = 6

#: 别称串的切分符（作者手写 JSON 时容易写成逗号串）
_ALIAS_SEPARATORS = re.compile(r"[、,，;；\s]+")


def clean_name(value) -> str:
    """去掉说话人标记的 ``@`` 与首尾空白（章节 cast 里两种写法都认）。"""
    return str(value or "").strip().strip("@＠").strip()


def role_supports(member_or_role, field: str) -> bool:
    """这一档是否使用某个描述字段。

    参数可以是成员对象（按它的 ``role``）或 ``role`` 取值本身；缺省按缺省档。
    """
    raw = member_or_role.get("role") if isinstance(member_or_role, dict) else member_or_role
    role = str(raw or DEFAULT_CAST_ROLE).strip().lower()
    return field in ROLE_FIELDS.get(role, ROLE_FIELDS[DEFAULT_CAST_ROLE])


def _clean_list(raw) -> list:
    if isinstance(raw, str):
        raw = _ALIAS_SEPARATORS.split(raw)
    if not isinstance(raw, list):
        return []
    items = []
    for entry in raw:
        name = clean_name(entry)
        if name and name not in items:
            items.append(name)
    return items


def normalize_member(raw, index: int = 0) -> dict:
    """单个成员归一化：补 id / role / 代词，其余描述字段统一成去空白的字符串。

    未知字段原样保留（读写一轮不丢作者手改的内容）。
    """
    member = dict(raw) if isinstance(raw, dict) else {}
    name = str(member.get("name") or "").strip()
    member["name"] = name
    member["id"] = str(member.get("id") or "").strip() or name or f"cast{index + 1}"
    role = str(member.get("role") or DEFAULT_CAST_ROLE).strip().lower()
    member["role"] = role if role in CAST_ROLES else DEFAULT_CAST_ROLE
    member["aliases"] = _clean_list(member.get("aliases"))
    pronoun = str(member.get("pronoun") or "").strip()
    member["pronoun"] = pronoun if pronoun in CAST_PRONOUNS else ""
    member["intro_as"] = clean_name(member.get("intro_as"))
    for key in ("background", "purpose", "speech", "call_her", "voice"):
        member[key] = str(member.get(key) or "").strip()
    return member


def normalize_cast(raw) -> list:
    """方案级人物表归一化；非列表返回空表（旧方案没有这个字段）。"""
    if not isinstance(raw, list):
        return []
    return [normalize_member(entry, index)
            for index, entry in enumerate(raw) if isinstance(entry, dict)]


def member_names(member) -> tuple:
    """成员的可用称呼：本名 + 别称（用于说话人解析与音色匹配）。"""
    names = [member.get("name") or ""]
    names.extend(member.get("aliases") or ())
    return tuple(n for n in (clean_name(x) for x in names) if n)


def find_member(members, key):
    """按 id / 称呼 / 别称 / 临时称呼查成员；查不到返回 ``None``。"""
    target = clean_name(key)
    if not target:
        return None
    for member in members or ():
        if target in (member.get("id"), member.get("name")):
            return member
        if target in (member.get("aliases") or ()):
            return member
        if (role_supports(member, "intro_as")
                and target == clean_name(member.get("intro_as"))):
            return member
    return None


def display_name(member) -> str:
    """成员的对外显示称呼：写了临时称呼的重要角色在揭示前用它，真名先不出现。"""
    if role_supports(member, "intro_as") and member.get("intro_as"):
        return member["intro_as"]
    return member.get("name", "")


def _meta_text(member) -> str:
    """括号里的结构化线索：代词；重要角色另有别称与「称她为」。

    带临时称呼的角色先用 ``intro_as`` 对外，别称往往暗示真名，先不列出。
    """
    bits = []
    if member.get("pronoun"):
        bits.append(member["pronoun"])
    if role_supports(member, "aliases"):
        temporary = role_supports(member, "intro_as") and member.get("intro_as")
        if not temporary:
            aliases = [a for a in (member.get("aliases") or ())
                       if a != member.get("name")]
            if aliases:
                bits.append("也称「" + "」「".join(aliases) + "」")
        if member.get("call_her"):
            bits.append(f"称她为「{member['call_her']}」")
    return "（" + "；".join(bits) + "）" if bits else ""


def intro_line(member) -> str:
    """一个成员的首次出场设定行；没有可渲染内容时返回空串（交给 AI 定）。

    重要角色渲染 ``background``，群像渲染 ``purpose`` 与 ``speech``；
    写了临时称呼的用临时称呼出场，真名等剧情揭示后再出现。
    """
    body_field = "background" if role_supports(member, "background") else "purpose"
    body = str(member.get(body_field) or "").strip()
    speech = (str(member.get("speech") or "").strip()
              if role_supports(member, "speech") else "")
    if not body and not speech:
        return ""
    line = f"{display_name(member)}{_meta_text(member)}"
    if body:
        line += f"：{body}"
    if speech:
        line += f"｜说话：{speech}"
    if role_supports(member, "intro_as") and member.get("intro_as"):
        line += "｜以上是临时对外称呼，真名等剧情揭示后再出现"
    return line


def render_first_seen(members) -> str:
    """首次出场块：已填线索的渲染成设定行，没填的给出一条「由 AI 定」的指令。

    调用方负责保证 ``members`` 都是本段第一次出场的成员——这里不判重。
    """
    members = [m for m in (members or ()) if m.get("name")]
    if not members:
        return ""
    lines, blanks = [], []
    for member in members:
        line = intro_line(member)
        if line:
            lines.append(line)
        else:
            blanks.append(display_name(member))

    out = []
    if lines:
        out.append("【人物（故事开始时的设定）】")
        out.extend(lines)
    if blanks:
        out.append("【本段首次出场】" + "、".join(blanks))
    if not lines and not blanks:
        return ""
    if blanks:
        out.append("你需要在这一段让上述角色自然出场，并确定三件事："
                   "他们为什么在这个故事里、各自的代词、各自的说话方式。"
                   "确定之后保持前后一致。")
    out.append("（以上是起点不是现状，不必每段重述。）")
    return "\n".join(out) + "\n"


__all__ = [
    "CAST_ROLES", "DEFAULT_CAST_ROLE", "CAST_PRONOUNS", "ROLE_FIELDS",
    "MAX_KEY_MEMBERS", "clean_name", "role_supports", "normalize_member",
    "normalize_cast", "member_names", "find_member", "display_name",
    "intro_line", "render_first_seen",
]
