"""运行期角色账本：把「设定」与「状态」分开存。

设定（**初始字段**）来自方案 ``cast[]``，一局开始后不再改写；状态（**可变字段**）
只属于重要角色（``key``），在本局内随剧情变化。群像（``extra``）只有初始字段：
名字、代词、作用、说话方式、音色，不积累特征，也不参与身份揭示。

账本只在本局有效，不回写方案配置。渲染时把两层合成一个「此刻的人物视图」，
供提示词使用；记录本身不依赖 AI，AI 抽取只是可选的状态来源之一。

字段归属：

- ``key``：初始字段（``cast.ROLE_FIELDS["key"]``）+ 可变字段（``KEY_MUTABLE_FIELDS``）；
- ``extra``：只有初始字段（``cast.ROLE_FIELDS["extra"]``）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import cast as cast_mod
from . import process_log

#: 可变字段：只属于重要角色。群像没有可变字段（见模块说明）。
KEY_MUTABLE_FIELDS = (
    "status", "handle", "true_name", "aka", "traits", "relation",
)

#: 在场观测：与身份可变字段分开存，群像也能有「最近出现过」。
PRESENCE_PRESENT = "present"
PRESENCE_MENTIONED = "mentioned"
PRESENCE_ABSENT = "absent"
_PRESENCE_RANK = {PRESENCE_ABSENT: 0, PRESENCE_MENTIONED: 1, PRESENCE_PRESENT: 2}

#: 待确认的合并（AI 给出真名但正文没有锚点）；不自动生效，等运行视图手动确认。
MAX_PENDING_MERGES = 20

#: 身份状态：未知（还没被观测到）/ 临时称呼 / 真名已揭示
IDENTITY_UNKNOWN = "unknown"
IDENTITY_TEMPORARY = "temporary"
IDENTITY_REVEALED = "revealed"

#: 重要角色上限。群像只有初始字段，随人物表存放，不受这个上限影响。
MAX_CHARACTERS = 24
MAX_TRAITS = 4
TRAIT_CHARS = 40
MAX_AKA = 6
RELATION_CHARS = 60
DIGEST_CHARS = 60
BRIEF_CHARS = 600

_CLAUSE_END = re.compile(r"[。！？；!?;]")


def _role(value) -> str:
    """把成员对象或 role 取值归一成 ``key`` / ``extra``。"""
    raw = value.get("role") if isinstance(value, dict) else value
    role = str(raw or cast_mod.DEFAULT_CAST_ROLE).strip().lower()
    return role if role in cast_mod.CAST_ROLES else cast_mod.DEFAULT_CAST_ROLE


def initial_fields(role="") -> tuple:
    """这一档的初始字段名（作者写、跨局不变）。"""
    normalized = _role(role)
    return ("id", "name") + cast_mod.ROLE_FIELDS[normalized]


def mutable_fields(role="") -> tuple:
    """这一档的可变字段名；群像返回空元组。"""
    return KEY_MUTABLE_FIELDS if _role(role) == "key" else ()


def _clip_text(text, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def _contains(text: str, needle: str) -> bool:
    """正文锚点检查：忽略首尾空白，按原样子串匹配。"""
    needle = str(needle or "").strip()
    return bool(needle) and needle in str(text or "")


def _digest(text, limit: int = DIGEST_CHARS) -> str:
    """取一段描述的第一句作为简报的身份句；没有句号时截断。"""
    text = " ".join(str(text or "").split())
    if not text:
        return ""
    match = _CLAUSE_END.search(text)
    if match and match.start() < limit:
        return text[:match.start() + 1]
    return _clip_text(text, limit)


def _join_bits(bits) -> str:
    """把简报里的几段线索拼成一行；句末句号去掉，避免「。；」。"""
    cleaned = []
    for bit in bits:
        text = str(bit or "").strip().rstrip("。")
        if text:
            cleaned.append(text)
    return "；".join(cleaned)


@dataclass
class RunCharacter:
    """一个角色的运行期记录。

    ``initial`` 是方案配置的只读副本，``mutable`` 只对 ``key`` 非空。
    """

    id: str
    role: str
    source: str
    initial: dict
    mutable: dict = field(default_factory=dict)
    observed: dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        return str(self.initial.get("name") or "")

    @property
    def pronoun(self) -> str:
        return str(self.initial.get("pronoun") or "")

    @property
    def voice(self) -> str:
        return str(self.initial.get("voice") or "")

    @property
    def presence(self) -> str:
        return str(self.observed.get("presence") or PRESENCE_ABSENT)

    @property
    def last_seen_step(self) -> int:
        try:
            return int(self.observed.get("last_seen_step") or 0)
        except (TypeError, ValueError):
            return 0

    @property
    def last_seen_chapter(self) -> str:
        return str(self.observed.get("last_seen_chapter") or "")

    @property
    def where(self) -> str:
        return str(self.observed.get("where") or "")

    @property
    def first_seen_step(self) -> int:
        try:
            return int(self.observed.get("first_seen_step") or 0)
        except (TypeError, ValueError):
            return 0

    @property
    def status(self) -> str:
        if self.role != "key":
            return IDENTITY_REVEALED
        return str(self.mutable.get("status") or IDENTITY_REVEALED)

    @property
    def handle(self) -> str:
        """当前对外的称呼；临时称呼期间与真名分开。"""
        if self.role != "key":
            return self.name
        return str(self.mutable.get("handle") or self.initial.get("name") or "")

    @property
    def display_name(self) -> str:
        return self.handle or self.name

    @property
    def true_name(self) -> str:
        if self.role != "key":
            return self.name
        return str(self.mutable.get("true_name") or "")

    @property
    def aka(self) -> list:
        return list(self.mutable.get("aka") or ())

    @property
    def traits(self) -> list:
        return list(self.mutable.get("traits") or ())

    @property
    def relation(self) -> str:
        return str(self.mutable.get("relation") or "")


class CharacterLedger:
    """本局角色账本：seed 自 ``cast[]``，只读投影给提示词。"""

    def __init__(self, members=(), *, limit: int = MAX_CHARACTERS):
        self._characters: dict[str, RunCharacter] = {}
        self._order: list[str] = []
        self._limit = max(1, int(limit))
        #: AI 声称的合并（正文里找不到锚点）：只登记，不生效。
        self.pending_merges: list[dict] = []
        for member in members or ():
            self.add(member)

    @classmethod
    def from_cast(cls, members, **kwargs) -> "CharacterLedger":
        return cls(members, **kwargs)

    # ---------------- 读取 ----------------
    def __len__(self) -> int:
        return len(self._order)

    def __iter__(self):
        for character_id in self._order:
            yield self._characters[character_id]

    def get(self, character_id):
        return self.resolve(character_id)

    def resolve(self, key):
        """按 id / 称呼 / 别称 / 当前称呼 / 真名查角色；查不到返回 ``None``。"""
        target = cast_mod.clean_name(key)
        if not target:
            return None
        if target in self._characters:
            return self._characters[target]
        for character_id in self._order:
            rc = self._characters[character_id]
            if target == rc.name or target in (rc.initial.get("aliases") or ()):
                return rc
            if rc.role == "key":
                if target == rc.handle or target == rc.true_name:
                    return rc
                if target in rc.aka:
                    return rc
        return None

    def display_name(self, member) -> str:
        """成员 dict 或 id 的当前称呼；不在账本里时回退 ``name``。"""
        if isinstance(member, dict):
            rc = self._characters.get(str(member.get("id") or ""))
            if rc is None:
                rc = self.resolve(member.get("name") or "")
            return rc.display_name if rc is not None else cast_mod.clean_name(
                member.get("name"))
        rc = self.resolve(member)
        return rc.display_name if rc is not None else cast_mod.clean_name(member)

    # ---------------- 写入 ----------------
    def add(self, member):
        """把一个设定成员加入账本；重复 id 返回已有记录。"""
        if not isinstance(member, dict):
            return None
        normalized = cast_mod.normalize_member(member, len(self._order))
        character_id = normalized["id"]
        existing = self._characters.get(character_id)
        if existing is not None:
            return existing
        role = normalized["role"]
        if (role == "key" and self._key_count() >= self._limit
                and not self._evict_one()):
            return None

        mutable = {}
        if role == "key":
            mutable = {
                "status": IDENTITY_REVEALED,
                "handle": normalized["name"],
                "true_name": normalized["name"],
                "aka": [],
                "traits": [],
                "relation": "",
            }
        rc = RunCharacter(id=character_id, role=role, source="authored",
                          initial=normalized, mutable=mutable,
                          observed={})
        self._characters[character_id] = rc
        self._order.append(character_id)
        return rc

    def _evict_one(self) -> bool:
        """重要角色容量满时先淘汰最久未见的 emergent；设定成员不淘汰。"""
        candidates = [rc for rc in self
                      if rc.role == "key" and rc.source == "emergent"
                      and rc.presence != PRESENCE_PRESENT]
        if not candidates:
            return False
        victim = min(candidates, key=lambda rc: (rc.last_seen_step, self._order.index(rc.id)))
        self._order.remove(victim.id)
        del self._characters[victim.id]
        return True

    def add_emergent(self, handle):
        """登记一个 AI 现编的角色（临时称呼），返回记录或 ``None``。"""
        name = cast_mod.clean_name(handle)
        if not name:
            return None
        existing = self.resolve(name)
        if existing is not None:
            return existing
        character_id = self._next_emergent_id()
        if (self._key_count() >= self._limit and not self._evict_one()):
            return None
        normalized = cast_mod.normalize_member({
            "id": character_id, "role": "key", "name": name})
        rc = RunCharacter(
            id=character_id, role="key", source="emergent", initial=normalized,
            mutable={
                "status": IDENTITY_TEMPORARY,
                "handle": name,
                "true_name": "",
                "aka": [],
                "traits": [],
                "relation": "",
            },
            observed={})
        self._characters[character_id] = rc
        self._order.append(character_id)
        return rc

    def _next_emergent_id(self) -> str:
        index = 1
        while f"rc{index}" in self._characters:
            index += 1
        return f"rc{index}"

    def _key_count(self) -> int:
        return sum(1 for rc in self._characters.values() if rc.role == "key")

    # ---------------- 在场观测 ----------------
    def _touch(self, rc, *, step=None, chapter="", presence=PRESENCE_ABSENT,
               force=False) -> None:
        """更新观测状态；迟到的后台结果（step 更小）不回退。"""
        observed = rc.observed
        try:
            current_step = int(step) if step is not None else None
        except (TypeError, ValueError):
            current_step = None
        last = rc.last_seen_step
        if current_step is not None and current_step < last:
            return
        if current_step is not None:
            observed["last_seen_step"] = current_step
            if not observed.get("first_seen_step"):
                observed["first_seen_step"] = current_step
        if chapter:
            observed["last_seen_chapter"] = str(chapter)
        old = rc.presence
        if force or current_step is None or current_step != last:
            observed["presence"] = presence
        elif _PRESENCE_RANK.get(presence, 0) > _PRESENCE_RANK.get(old, 0):
            # 同一段里先被点名、又开口说话：升级为在场，不降级
            observed["presence"] = presence

    def mark_declared(self, character_ids, *, step=None, chapter="") -> int:
        """章节 ``cast`` 声明的在场者：直接标为 present。"""
        changed = 0
        for character_id in character_ids or ():
            rc = self.resolve(character_id)
            if rc is None:
                continue
            self._touch(rc, step=step, chapter=chapter,
                        presence=PRESENCE_PRESENT, force=True)
            changed += 1
        return changed

    def observe_speaker(self, handle, *, step=None, chapter=""):
        """登记 ``@说话人@``：未知称呼建 emergent；连续两段出现升级为在场。"""
        rc = self.resolve(handle)
        if rc is None:
            rc = self.add_emergent(handle)
        if rc is None:
            return None
        last = rc.last_seen_step
        consecutive = bool(last and step is not None and last >= int(step) - 1)
        self._touch(rc, step=step, chapter=chapter,
                    presence=PRESENCE_PRESENT if consecutive
                    else PRESENCE_MENTIONED,
                    force=consecutive)
        return rc

    def note_mentions(self, text, *, step=None, chapter="") -> int:
        """正文里被点名的已知角色记为 mentioned；不新建角色。"""
        body = str(text or "")
        if not body:
            return 0
        changed = 0
        for rc in list(self):
            names = {rc.name, rc.handle, rc.true_name, *rc.aka}
            names.update(rc.initial.get("aliases") or ())
            if any(name and name in body for name in names):
                before = rc.presence
                self._touch(rc, step=step, chapter=chapter,
                            presence=PRESENCE_MENTIONED)
                if rc.presence != before:
                    changed += 1
        return changed

    def decay(self, step) -> int:
        """把两段以内没再出现的人标为 absent（保留 last_seen 供「最近离开」）。"""
        try:
            current = int(step)
        except (TypeError, ValueError):
            return 0
        changed = 0
        for rc in self:
            if rc.last_seen_step and rc.last_seen_step < current - 1 \
                    and rc.presence != PRESENCE_ABSENT:
                rc.observed["presence"] = PRESENCE_ABSENT
                changed += 1
        return changed

    # ---------------- 揭示与合并 ----------------
    def _record_pending(self, rc, true_name, *, step=None, chapter="") -> None:
        item = {
            "source": rc.id,
            "handle": rc.handle,
            "true_name": cast_mod.clean_name(true_name),
            "step": step,
            "chapter": chapter,
        }
        if item in self.pending_merges:
            return
        self.pending_merges.append(item)
        del self.pending_merges[:-MAX_PENDING_MERGES]
        process_log.log(f"[Characters] 合并待确认：{rc.handle} → {item['true_name']}")

    def mark_temporary(self, character_id, handle) -> bool:
        """把重要角色切到临时称呼：渲染用 ``handle``，真名暂不出现。"""
        rc = self.resolve(character_id)
        if rc is None or rc.role != "key":
            return False
        rc.mutable["status"] = IDENTITY_TEMPORARY
        rc.mutable["handle"] = cast_mod.clean_name(handle) or rc.name
        rc.mutable["true_name"] = ""
        return True

    def reveal(self, character_id, true_name) -> bool:
        """揭示真名：旧称呼进 ``aka``，以后统一用真名。"""
        rc = self.resolve(character_id)
        if rc is None or rc.role != "key":
            return False
        name = cast_mod.clean_name(true_name)
        if not name:
            return False
        old_handle = rc.handle
        if old_handle and old_handle != name:
            aliases = rc.mutable.setdefault("aka", [])
            if old_handle not in aliases:
                aliases.append(old_handle)
            del aliases[:-MAX_AKA]
        rc.mutable["true_name"] = name
        rc.mutable["handle"] = name
        rc.mutable["status"] = IDENTITY_REVEALED
        return True

    def merge(self, source, target) -> bool:
        """把源角色并入目标角色；两个设定成员永不自动合并。

        ``source`` / ``target`` 可以是 id、称呼或记录本身。合并只保留目标，
        源的特征、别名与观测状态并入目标；返回是否真的合并。
        """
        src = source if isinstance(source, RunCharacter) else self.resolve(source)
        dst = target if isinstance(target, RunCharacter) else self.resolve(target)
        if src is None or dst is None or src.id == dst.id:
            return False
        if src.role != "key" or dst.role != "key":
            return False
        if src.source == "authored" and dst.source == "authored":
            self._record_pending(src, dst.name)
            return False
        aliases = dst.mutable.setdefault("aka", [])
        for alias in (src.handle, src.name, src.true_name, *src.aka):
            name = cast_mod.clean_name(alias)
            if name and name != dst.name and name not in aliases:
                aliases.append(name)
        del aliases[:-MAX_AKA]
        traits = list(dst.mutable.get("traits") or ())
        for trait in src.traits:
            if trait not in traits:
                traits.append(trait)
        dst.mutable["traits"] = traits[-MAX_TRAITS:]
        if src.last_seen_step > dst.last_seen_step:
            dst.observed = dict(src.observed)
        elif src.last_seen_step == dst.last_seen_step and \
                _PRESENCE_RANK.get(src.presence, 0) > _PRESENCE_RANK.get(dst.presence, 0):
            dst.observed["presence"] = src.presence
        name = dst.true_name or dst.name
        dst.mutable["true_name"] = name
        dst.mutable["handle"] = name
        dst.mutable["status"] = IDENTITY_REVEALED
        self._order.remove(src.id)
        del self._characters[src.id]
        process_log.log(
            f"[Characters] 合并：{src.handle or src.name} → {name}（{src.id} 并入 {dst.id}）")
        return True

    def _reveal_or_merge(self, rc, true_name, *, text="", step=None,
                         chapter="") -> bool:
        """真名揭示：有正文锚点才自动生效；撞上已有角色时走合并。"""
        name = cast_mod.clean_name(true_name)
        if not name:
            return False
        handle = rc.handle or rc.name
        if text and not (_contains(text, handle) and _contains(text, name)):
            self._record_pending(rc, name, step=step, chapter=chapter)
            return False
        target = self.resolve(name)
        if target is not None and target.id != rc.id:
            if self.merge(rc, target):
                return True
            self._record_pending(rc, name, step=step, chapter=chapter)
            return False
        return self.reveal(rc.id, name)

    def apply_updates(self, updates, *, text="", step=None, chapter="") -> int:
        """吸收 AI 抽取的角色更新，返回发生变化的角色数。

        未知称呼首次出现时登记成临时角色；群像只有初始字段，不吸收特征与
        身份状态。真名揭示要求正文里同时出现旧称呼与真名（``text`` 为空时
        视为程序化调用，不做锚点检查）；找不到锚点的只进 ``pending_merges``。
        """
        changed = 0
        for item in updates or ():
            if not isinstance(item, dict):
                continue
            handle = cast_mod.clean_name(item.get("handle") or item.get("name"))
            rc = self.resolve(handle) if handle else None
            if rc is None and handle:
                rc = self.add_emergent(handle)
            if rc is None or rc.role != "key":
                continue
            before = (rc.status, rc.handle, rc.true_name,
                      tuple(rc.traits), rc.relation)

            true_name = cast_mod.clean_name(item.get("true_name"))
            revealed = False
            if true_name and rc.status != IDENTITY_REVEALED:
                revealed = self._reveal_or_merge(rc, true_name, text=text,
                                                 step=step, chapter=chapter)

            traits = item.get("traits")
            if isinstance(traits, (list, tuple)):
                merged = list(rc.traits)
                for value in traits:
                    trait = _clip_text(value, TRAIT_CHARS)
                    if trait and trait not in merged:
                        merged.append(trait)
                rc.mutable["traits"] = merged[-MAX_TRAITS:]

            relation = item.get("relation")
            if relation is not None:
                rc.mutable["relation"] = _clip_text(relation, RELATION_CHARS)

            pronoun = str(item.get("pronoun") or "").strip()
            if pronoun in cast_mod.CAST_PRONOUNS and pronoun \
                    and not rc.initial.get("pronoun"):
                rc.initial["pronoun"] = pronoun

            after = (rc.status, rc.handle, rc.true_name,
                     tuple(rc.traits), rc.relation)
            # 合并成功时源记录已并入目标，前后对比看不出来，单独计数
            if revealed or after != before:
                changed += 1
        return changed

    # ---------------- 渲染 ----------------
    def observed_brief(self, exclude_ids=()) -> str:
        """AI 现编、当前确实在场的角色简报；不进「本段在场」声明行。"""
        excluded = set(exclude_ids or ())
        rows = [rc for rc in self
                if rc.id not in excluded and rc.role == "key"
                and rc.source == "emergent"
                and rc.presence == PRESENCE_PRESENT]
        return self.render_brief(rows)

    def roster_sections(self) -> tuple:
        """运行视图用的两档：``(在场, 最近离开)``，各自按最近出现排序。"""
        present = [rc for rc in self
                   if rc.presence in (PRESENCE_PRESENT, PRESENCE_MENTIONED)]
        left = [rc for rc in self
                if rc.presence == PRESENCE_ABSENT and rc.last_seen_step > 0]
        present.sort(key=lambda rc: (-_PRESENCE_RANK.get(rc.presence, 0),
                                     -rc.last_seen_step))
        left.sort(key=lambda rc: -rc.last_seen_step)
        return present, left

    def render_brief(self, members) -> str:
        """在场角色的当前视图；不在场者不渲染（调用方负责筛选）。

        重要角色渲染身份句 + 可变状态；群像只渲染初始字段里的作用与说话方式。
        超出 ``BRIEF_CHARS`` 时降级为「名字 + 在场」。
        """
        rows, seen = [], set()
        for member in members or ():
            if isinstance(member, RunCharacter):
                rc = member
            elif isinstance(member, dict):
                rc = (self._characters.get(str(member.get("id") or ""))
                      or self.resolve(member.get("id") or member.get("name") or ""))
            else:
                rc = self.resolve(member)
            if rc is None or rc.id in seen:
                continue
            seen.add(rc.id)
            rows.append(rc)
        if not rows:
            return ""

        lines = [self._brief_line(rc) for rc in rows]
        if sum(len(line) + 1 for line in lines) > BRIEF_CHARS:
            lines = [self._brief_line(rc, compact=True) for rc in rows]
        text = "\n".join(lines)
        if len(text) > BRIEF_CHARS:
            text = text[:BRIEF_CHARS].rstrip() + "…"
        return "【人物简报（当前状态）】\n" + text + "\n"

    @staticmethod
    def _brief_line(rc: RunCharacter, compact: bool = False) -> str:
        if rc.role == "key" and rc.status == IDENTITY_TEMPORARY:
            label = "临时称呼"
        elif rc.role == "key":
            label = "重要角色"
        else:
            label = "群像"
        head = f"- {rc.display_name}（{label}，在场）"
        if compact:
            return head

        bits = []
        if rc.role == "key":
            digest = _digest(rc.initial.get("background") or "")
            if digest:
                bits.append(digest)
            call_her = str(rc.initial.get("call_her") or "").strip()
            if call_her:
                bits.append(f"称她「{call_her}」")
            if rc.relation:
                bits.append(f"关系：{rc.relation}")
            if rc.traits:
                bits.append("近况：" + "；".join(rc.traits[-2:]))
            if rc.status == IDENTITY_TEMPORARY:
                bits.append("真名尚未揭示")
        else:
            purpose = _digest(rc.initial.get("purpose") or "")
            if purpose:
                bits.append(purpose)
            speech = str(rc.initial.get("speech") or "").strip()
            if speech:
                bits.append(f"说话：{speech}")
        detail = _join_bits(bits)
        return head + ("：" + detail if detail else "")


__all__ = [
    "KEY_MUTABLE_FIELDS",
    "IDENTITY_UNKNOWN", "IDENTITY_TEMPORARY", "IDENTITY_REVEALED",
    "PRESENCE_PRESENT", "PRESENCE_MENTIONED", "PRESENCE_ABSENT",
    "MAX_CHARACTERS", "MAX_TRAITS", "TRAIT_CHARS", "MAX_AKA",
    "RELATION_CHARS", "DIGEST_CHARS", "BRIEF_CHARS", "MAX_PENDING_MERGES",
    "initial_fields", "mutable_fields",
    "RunCharacter", "CharacterLedger",
]
