"""角色账本自检：初始字段 / 可变字段分层与简报渲染（无 GUI，不碰 data/）。

用法：``python tests/check_characters.py``
输出：控制台一行 ASCII 结论 + UTF-8 报告文件路径。

覆盖：
1. 字段分层：``key`` 有初始字段 + 可变字段，``extra`` 只有初始字段；
2. seed：设定成员进账本，重要角色默认已揭示；
3. 解析：id / 称呼 / 别称 / 当前称呼 / 真名；
4. 临时称呼 → 揭示真名：旧称呼进 ``aka``，不再显示「真名尚未揭示」；
5. 可变字段有界：特征裁剪、条数上限、关系裁剪；
6. 群像不吸收任何可变更新；
7. 简报只渲染传入的在场成员，超长时降级为「名字 + 在场」。
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_report_path = os.path.join(tempfile.mkdtemp(prefix="characters_check_"), "report.txt")
_log = open(_report_path, "w", encoding="utf-8")
sys.stdout = _log
sys.stderr = _log

failures = []
total = 0


def check(name, ok, extra=""):
    global total
    total += 1
    print(("  OK   " if ok else "  FAIL ") + name
          + (f"  <{extra}>" if extra and not ok else ""))
    if not ok:
        failures.append(name)


from dungeon import cast  # noqa: E402
from dungeon.characters import (BRIEF_CHARS, KEY_MUTABLE_FIELDS,  # noqa: E402
                                MAX_TRAITS, CharacterLedger,
                                IDENTITY_REVEALED, IDENTITY_TEMPORARY,
                                initial_fields, mutable_fields)


def make_key(**overrides):
    data = {
        "id": "li", "role": "key", "name": "李队长",
        "aliases": ["李队", "队长"], "pronoun": "他",
        "background": "本地消防中队长，女儿还埋在废墟下。",
        "call_her": "姑娘", "voice": "voice-li",
    }
    data.update(overrides)
    return cast.normalize_member(data)


def make_extra(**overrides):
    data = {
        "id": "wang", "role": "extra", "name": "王店主", "pronoun": "他",
        "purpose": "表现城区的物资已经见底。",
        "speech": "絮叨，反复讲价钱", "voice": "voice-wang",
    }
    data.update(overrides)
    return cast.normalize_member(data)


# ---------------- 1. 字段分层 ----------------
_key_fields = initial_fields("key")
_extra_fields = initial_fields("extra")
check("重要角色的初始字段含身份背景 / 别称 / 称她为",
      "background" in _key_fields and "aliases" in _key_fields
      and "call_her" in _key_fields)
check("重要角色的初始字段不含群像专属的作用 / 说话方式",
      "purpose" not in _key_fields and "speech" not in _key_fields)
check("群像的初始字段含作用 / 说话方式",
      "purpose" in _extra_fields and "speech" in _extra_fields)
check("群像的初始字段不含身份背景 / 别称 / 称她为",
      "background" not in _extra_fields and "aliases" not in _extra_fields
      and "call_her" not in _extra_fields)
check("可变字段只属于重要角色",
      set(mutable_fields("key")) == set(KEY_MUTABLE_FIELDS)
      and mutable_fields("extra") == ())

# ---------------- 2. seed ----------------
_ledger = CharacterLedger([make_key(), make_extra()])
_li = _ledger.get("li")
_wang = _ledger.get("wang")
check("重要角色进账本后默认已揭示",
      _li is not None and _li.status == IDENTITY_REVEALED
      and _li.handle == "李队长" and _li.true_name == "李队长")
check("重要角色带全部可变字段",
      set(_li.mutable) == set(KEY_MUTABLE_FIELDS), sorted(_li.mutable))
check("群像只有初始字段，没有任何可变状态",
      _wang is not None and _wang.mutable == {}, _wang.mutable if _wang else None)
check("群像的状态恒为已揭示、称呼恒为名字",
      _wang.status == IDENTITY_REVEALED and _wang.handle == "王店主")

# ---------------- 3. 解析 ----------------
check("按 id / 称呼 / 别称都能解析到同一角色",
      _ledger.resolve("li") is _li
      and _ledger.resolve("李队长") is _li
      and _ledger.resolve("队长") is _li)
check("查无此人返回 None", _ledger.resolve("查无此人") is None)
check("display_name 接受成员 dict 与 id",
      _ledger.display_name(make_key()) == "李队长"
      and _ledger.display_name("li") == "李队长")

# ---------------- 4. 临时称呼 → 揭示 ----------------
check("重要角色可以切到临时称呼",
      _ledger.mark_temporary("li", "柜台后的男人") is True
      and _li.status == IDENTITY_TEMPORARY
      and _li.handle == "柜台后的男人"
      and _li.true_name == "")
_brief = _ledger.render_brief([make_key()])
check("临时称呼的简报标明身份未揭示",
      "临时称呼" in _brief and "真名尚未揭示" in _brief
      and "柜台后的男人" in _brief, _brief)
check("揭示真名后称呼切回真名、旧称呼进 aka",
      _ledger.reveal("li", "李队长") is True
      and _li.status == IDENTITY_REVEALED
      and _li.handle == "李队长"
      and "柜台后的男人" in _li.aka)
check("揭示后旧称呼仍能解析到同一角色",
      _ledger.resolve("柜台后的男人") is _li)
check("揭示后的简报不再标「真名尚未揭示」",
      "真名尚未揭示" not in _ledger.render_brief([make_key()]))
check("群像不能切临时称呼、也不能被揭示",
      _ledger.mark_temporary("wang", "路人") is False
      and _ledger.reveal("wang", "王老板") is False
      and _wang.handle == "王店主")

# ---------------- 5. 可变字段有界 ----------------
_long_trait = "非常" * 30
_ledger.apply_updates([{
    "handle": "李队长",
    "traits": [_long_trait, "与主角争执", "护着女儿", " distrust", "第五", "第六"],
    "relation": "对主角既依赖又防备" * 10,
}])
check("特征单条裁剪到上限", all(len(t) <= 41 for t in _li.traits), _li.traits)
check("特征条数有上限", len(_li.traits) == MAX_TRAITS, _li.traits)
check("关系描述裁剪到上限", len(_li.relation) <= 61, len(_li.relation))
check("重复特征不重复追加",
      _ledger.apply_updates([{"handle": "李队长", "traits": ["第六"]}])
      == 0 and len(_li.traits) == MAX_TRAITS, _li.traits)
check("未知角色与群像的更新被忽略",
      _ledger.apply_updates([{"handle": "查无此人", "traits": ["x"]},
                             {"handle": "王店主", "traits": ["x"]}]) == 0
      and _wang.mutable == {})

# ---------------- 6. 简报只渲染传入的在场成员 ----------------
_brief_key = _ledger.render_brief([make_key()])
check("重要角色简报含身份句与称呼线索",
      "本地消防中队长" in _brief_key and "称她「姑娘」" in _brief_key
      and "。；" not in _brief_key, _brief_key)
check("重要角色简报带可变状态",
      "近况：" in _brief_key and "关系：" in _brief_key, _brief_key)
_brief_extra = _ledger.render_brief([make_extra()])
check("群像简报只由初始字段构成",
      "表现城区的物资已经见底" in _brief_extra
      and "说话：絮叨，反复讲价钱" in _brief_extra, _brief_extra)
check("不在场的成员不进简报",
      "王店主" not in _ledger.render_brief([make_key()])
      and "李队长" not in _ledger.render_brief([make_extra()]))
check("空名单渲染空串", _ledger.render_brief([]) == "")

# ---------------- 7. 超长降级 ----------------
_many = [make_key(id=f"k{i}", name=f"角色{i}", background="很长的身份描述。" * 8)
         for i in range(30)]
_big = CharacterLedger(_many, limit=30)
_big_brief = _big.render_brief(_many)
check("简报超长时降级并截断",
      _big_brief.startswith("【人物简报（当前状态）】")
      and len(_big_brief) <= BRIEF_CHARS + 20
      and "很长的身份描述" not in _big_brief, len(_big_brief))

# ---------------- 结论 ----------------
print()
print(f"共 {total} 项，失败 {len(failures)} 项")
if failures:
    for name in failures:
        print("  FAILED: " + name)
_log.flush()
_log.close()
sys.stdout = sys.__stdout__
print(("ALL PASS" if not failures else f"FAILED {len(failures)}/{total}") + f"  report: {_report_path}")
sys.exit(1 if failures else 0)
