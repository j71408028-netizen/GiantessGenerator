"""角色账本自检：初始字段 / 可变字段分层与简报渲染（无 GUI，不碰 data/）。

用法：``python tests/check_characters.py``
输出：控制台一行 ASCII 结论 + UTF-8 报告文件路径。

覆盖：
1. 字段分层：``key`` 有初始字段 + 可变字段，``extra`` 只有初始字段；
2. seed：设定成员进账本，重要角色默认已揭示；
3. 解析：id / 称呼 / 别称 / 当前称呼 / 真名；
4. 临时称呼 → 揭示真名：旧称呼进 ``aka``，不再显示「真名尚未揭示」；
5. 可变字段有界：特征裁剪、条数上限、关系裁剪；
6. 更新吸收：未知称呼登记成临时角色，群像不吸收任何可变更新；
7. 简报只渲染传入的在场成员，超长时降级为「名字 + 在场」；
8. 在场观测：``@说话人@`` 登记、连续两段升级在场、点名、章节声明、衰减；
9. 揭示与合并：正文锚点才自动揭示，无锚点进待确认；设定成员永不自动合并；
10. 容量淘汰：优先淘汰最久未见的非在场 emergent；
11. 细节搭车解析：``parse_character_updates`` 新格式 / 旧格式 / 坏 JSON；
12. ``intro_as``：seed 即临时称呼、显示名与解析、语音稳定键；
13. 摘要搭车：``StorySummarizer`` 抽出角色更新、旧响应兼容；
14. 合并审计（origin 手动/自动）、账本快照还原、结局回顾快照。
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
                                PRESENCE_ABSENT, PRESENCE_MENTIONED,
                                PRESENCE_PRESENT, initial_fields,
                                mutable_fields)
from dungeon.details import parse_character_updates, parse_detail_queries  # noqa: E402
from dungeon.summary import StorySummarizer  # noqa: E402


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
check("未知称呼登记成临时角色、群像更新仍被忽略",
      _ledger.apply_updates([{"handle": "查无此人", "traits": ["x"]}]) == 1
      and _ledger.resolve("查无此人").status == IDENTITY_TEMPORARY
      and _ledger.apply_updates([{"handle": "王店主", "traits": ["x"]}]) == 0
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

# ---------------- 8. 在场观测（确定性通道） ----------------
_obs = CharacterLedger([make_key()])
_timid = _obs.observe_speaker("胆怯的声音", step=5, chapter="第二幕")
check("未知说话人登记成临时角色、记为被提起",
      _timid is not None and _timid.status == IDENTITY_TEMPORARY
      and _timid.presence == PRESENCE_MENTIONED
      and _timid.first_seen_step == 5, _timid.presence if _timid else None)
check("连续两段出现升级为在场",
      _obs.observe_speaker("胆怯的声音", step=6, chapter="第二幕").presence
      == PRESENCE_PRESENT)
check("再次登记同一个称呼不新建角色",
      _obs.observe_speaker("胆怯的声音", step=7, chapter="第二幕") is _timid
      and len(list(_obs)) == 2)
check("正文点名的已知角色记为被提起",
      _obs.note_mentions("李队长望着废墟出神", step=7, chapter="第二幕") == 1
      and _obs.resolve("李队长").presence == PRESENCE_MENTIONED)
check("点名不认识的称呼不新建角色",
      _obs.note_mentions("路人甲匆匆跑过", step=7, chapter="第二幕") == 0
      and _obs.resolve("路人甲") is None
      and len(list(_obs)) == 2)
check("章节声明直接标为在场",
      _obs.mark_declared(["李队长"], step=8, chapter="第二幕") == 1
      and _obs.resolve("李队长").presence == PRESENCE_PRESENT)
check("两段没出现转「最近离开」",
      _obs.decay(10) >= 1
      and _obs.resolve("李队长").presence == PRESENCE_ABSENT
      and _obs.resolve("李队长").last_seen_step == 8)
check("迟到的观测不回退状态",
      _obs.observe_speaker("胆怯的声音", step=3, chapter="第二幕") is _timid
      and _timid.last_seen_step == 7)

# ---------------- 9. 揭示与合并 ----------------
_anchor = CharacterLedger([make_key()])
check("有正文锚点：临时称呼揭示为真名、旧称呼进 aka",
      _anchor.apply_updates(
          [{"handle": "胆怯的声音", "true_name": "王店主"}],
          text="胆怯的声音抬起头：「其实我就是王店主。」") == 1
      and _anchor.resolve("王店主").status == IDENTITY_REVEALED
      and _anchor.resolve("胆怯的声音") is _anchor.resolve("王店主"))
_pending = CharacterLedger([make_key()])
_p = _pending.add_emergent("胆怯的声音")
_pending.apply_updates([{"handle": "胆怯的声音", "true_name": "王店主"}],
                       text="她听见柜台后传来响动。")
check("无正文锚点：只登记待确认，不自动揭示",
      _p.status == IDENTITY_TEMPORARY and len(_pending.pending_merges) == 1
      and _pending.pending_merges[0]["true_name"] == "王店主")
_two = CharacterLedger([make_key(), make_key(id="zhao", name="赵司机")])
_two.mark_temporary("zhao", "面罩男")
_two.apply_updates([{"handle": "面罩男", "true_name": "李队长"}],
                   text="面罩男摘下面具，他就是李队长。")
check("两个设定成员永不自动合并，只记待确认",
      _two.resolve("zhao") is not None
      and len(_two.pending_merges) == 1
      and _two.pending_merges[0]["source"] == "zhao")
_merge = CharacterLedger([make_key()])
_merge.add_emergent("胆怯的声音")
_merge.observe_speaker("柜台后的男人", step=4, chapter="第二幕")
check("合并：源并入目标，别名并集、状态升已揭示",
      _merge.merge("柜台后的男人", "胆怯的声音") is True
      and _merge.resolve("柜台后的男人") is _merge.resolve("胆怯的声音")
      and _merge.resolve("胆怯的声音").status == IDENTITY_REVEALED
      and "柜台后的男人" in _merge.resolve("胆怯的声音").aka)
_reveal_merge = CharacterLedger([make_key()])
_reveal_merge.add_emergent("胆怯的声音")
check("揭示到已有角色的称呼时走合并",
      _reveal_merge.apply_updates(
          [{"handle": "胆怯的声音", "true_name": "李队长"}],
          text="胆怯的声音报上身份：李队长。") == 1
      and _reveal_merge.resolve("胆怯的声音") is _reveal_merge.resolve("李队长"))

# ---------------- 10. 容量淘汰 ----------------
_cap = CharacterLedger([], limit=3)
for i in range(3):
    _cap.add_emergent(f"路人{i}")
_cap.observe_speaker("路人0", step=1, chapter="第一幕")
_cap.observe_speaker("路人1", step=2, chapter="第一幕")
_cap.observe_speaker("路人1", step=3, chapter="第一幕")
_cap.observe_speaker("路人2", step=1, chapter="第一幕")
_cap.add_emergent("路人3")
check("容量满时淘汰最久未见的非在场 emergent",
      _cap.resolve("路人0") is None and _cap.resolve("路人3") is not None)
check("在场的 emergent 不被淘汰",
      _cap.resolve("路人1") is not None
      and _cap.resolve("路人1").presence == PRESENCE_PRESENT)

# ---------------- 11. 运行视图两档 ----------------
_roster = CharacterLedger([make_key(), make_extra()])
_roster.mark_declared(["李队长"], step=11, chapter="第三幕")
_roster.note_mentions("王店主清点着货架", step=9, chapter="第三幕")
_roster.decay(12)
_present, _left = _roster.roster_sections()
check("运行视图分「在场 / 最近离开」两档并按最近出现排序",
      [rc.id for rc in _present] == ["li"]
      and [rc.id for rc in _left] == ["wang"])

# ---------------- 12. 细节搭车解析 ----------------
_raw = ('{"queries": ["她有多大"], "characters": ['
        '{"handle": "胆怯的声音", "true_name": "", "pronoun": "她", '
        '"traits": ["怕她", "躲在柜台后", "怕她", "' + "很" * 50 + '"], '
        '"relation": "被救者"}, {"handle": "", "true_name": ""}]}')
_updates = parse_character_updates(_raw)
check("搭车解析：新格式抽出角色更新",
      len(_updates) == 1 and _updates[0]["handle"] == "胆怯的声音"
      and _updates[0]["pronoun"] == "她"
      and _updates[0]["status"] == "temporary", _updates)
check("搭车解析：特征去重并裁剪",
      len(_updates[0]["traits"]) == 3
      and len(set(_updates[0]["traits"])) == 3
      and all(len(t) <= 41 for t in _updates[0]["traits"]),
      _updates[0]["traits"])
check("搭车解析：称呼全空的条目跳过",
      all(u["handle"] or u["true_name"] for u in _updates))
check("旧格式（只有 queries）解析为空列表",
      parse_character_updates('{"queries": ["她有多大"]}') == [])
check("坏 JSON 解析为空列表", parse_character_updates("不是JSON") == [])
check("提问解析兼容新格式", parse_detail_queries(_raw) == ["她有多大"])
check("提问解析保留非 JSON 行兜底",
      parse_detail_queries("想问她多大\n想问外面情况") == ["想问她多大", "想问外面情况"])

# ---------------- 13. intro_as 与语音解析 ----------------
_mystery = cast.normalize_member({
    "id": "grocer", "role": "key", "name": "王店主", "intro_as": "柜台后的男人",
    "pronoun": "他", "background": "南街杂货店的店主。"})
_tmp = CharacterLedger([make_key(), _mystery])
_grocer = _tmp.get("grocer")
check("写 intro_as 的成员开局是临时称呼",
      _grocer.status == IDENTITY_TEMPORARY
      and _grocer.handle == "柜台后的男人"
      and _grocer.true_name == "" and _grocer.name == "王店主")
check("临时称呼与真名都解析到同一角色",
      _tmp.resolve("柜台后的男人") is _grocer
      and _tmp.resolve("王店主") is _grocer)
check("临时称呼的显示名是 intro_as",
      _tmp.display_name("grocer") == "柜台后的男人")
check("语音稳定键在揭示前就是真名（临时称呼不换嗓子）",
      _tmp.voice_info("柜台后的男人") == ("", "他", "王店主"))
check("揭示后显示名切回真名、旧称呼进 aka",
      _tmp.reveal("grocer", "王店主") is True
      and _grocer.handle == "王店主"
      and "柜台后的男人" in _grocer.aka)
check("揭示后语音稳定键不变",
      _tmp.voice_info("王店主") == ("", "他", "王店主"))
check("显式音色原样带出",
      _tmp.voice_info("李队长") == ("voice-li", "他", "李队长"))
check("查无此人返回 None", _tmp.voice_info("查无此人") is None)
_shy = CharacterLedger([]).add_emergent("胆怯的声音")
check("emergent 的语音稳定键是第一个称呼",
      _shy is not None and _shy.observed.get("voice_key") == "胆怯的声音")

# ---------------- 14. 摘要搭车 ----------------
class _SummaryClient:
    def generate(self, messages, temperature=0.3):
        return ('{"summary": "概括", "facts": ["一条事实"], "character_updates": '
                '[{"handle": "胆怯的声音", "true_name": "王店主", '
                '"traits": ["怕她"]}]}')

_summ = StorySummarizer(block_size=2)
_summ.record("dialog", "胆怯的声音说：我就是王店主。", "第二幕")
_summ.record("dialog", "她点了点头。", "第二幕")
_summ.maybe_compress(2, ai_client=_SummaryClient())
_batch = _summ.drain_character_updates()
check("摘要搭车抽出角色更新并带正文锚点",
      len(_batch) == 1 and "王店主" in _batch[0][0]
      and _batch[0][1][0]["true_name"] == "王店主", _batch)
check("取走后清空", _summ.drain_character_updates() == [])
_summ_ledger = CharacterLedger([make_key()])
_changed = sum(_summ_ledger.apply_updates(u, text=a) for a, u in _batch)
check("摘要搭车的更新走锚点揭示",
      _changed == 1
      and _summ_ledger.resolve("胆怯的声音") is _summ_ledger.resolve("王店主"))
check("旧摘要响应没有 character_updates 时行为不变",
      StorySummarizer._parse_summary_json('{"summary": "s", "facts": ["f"]}')
      == ("s", ["f"], []))

# ---------------- 15. 合并审计与快照还原（P3） ----------------
_audit = CharacterLedger([make_key()])
_audit.add_emergent("胆怯的声音")
check("手动合并记进审计日志",
      _audit.merge("胆怯的声音", "李队长", origin="manual", step=6,
                   chapter="第三幕") is True
      and len(_audit.merge_log) == 1
      and _audit.merge_log[0]["origin"] == "manual"
      and _audit.merge_log[0]["target_name"] == "李队长"
      and _audit.merge_log[0]["step"] == 6)
_auto = CharacterLedger([make_key()])
_auto.add_emergent("胆怯的声音")
_auto.apply_updates([{"handle": "胆怯的声音", "true_name": "李队长"}],
                    text="胆怯的声音就是李队长。")
check("自动合并记 origin=auto",
      len(_auto.merge_log) == 1 and _auto.merge_log[0]["origin"] == "auto")

_src = CharacterLedger([make_key(), make_extra()])
_src.add_emergent("胆怯的声音")
_src.mark_declared(["李队长"], step=5, chapter="第二幕")
_data = _src.snapshot()
_dst = CharacterLedger([])
check("快照还原角色数一致", _dst.restore(_data) == len(list(_src)))
check("快照还原观测与身份状态一致",
      _dst.resolve("李队长").presence == PRESENCE_PRESENT
      and _dst.resolve("李队长").last_seen_step == 5
      and _dst.resolve("胆怯的声音").status == IDENTITY_TEMPORARY)
check("还原后语音稳定键与群像记录保留",
      _dst.voice_info("李队长")[2] == "李队长"
      and _dst.resolve("王店主").role == "extra")
check("空快照不动现有账本",
      _dst.restore({}) == 0 and _dst.resolve("李队长") is not None)
check("旧回放没有快照时账本保持 seed",
      CharacterLedger([make_key()]).restore(None) == 0)

_snap = CharacterLedger([make_key(), make_extra()])
_snap.add_emergent("胆怯的声音")
check("结局快照只含已揭示的重要角色",
      [r["name"] for r in _snap.revealed_snapshot()] == ["李队长"]
      and _snap.revealed_snapshot()[0]["aliases"] == ["李队", "队长"])
_snap.reveal("胆怯的声音", "王店主")
check("揭示后的 emergent 进结局快照",
      [r["name"] for r in _snap.revealed_snapshot()] == ["李队长", "王店主"])

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
