"""P0 自检：人物表（cast）+ 主角设定（无 GUI，不碰真实 data/）。

用法：``python tests/check_cast.py``
输出：控制台一行 ASCII 结论 + UTF-8 报告文件路径。

覆盖：
1. **空表零差异**：旧方案（没有 cast / protagonist_background 键）与空表新方案
   的系统提示词、用户提示词逐字一致——这是兼容性红线的第一条断言；
2. 在场行只认章节 ``cast`` 声明，未声明就不写；
3. 首次出场块只出现一次；第二次改注入压缩简报到场角色；
4. 字段按定位取用：``key`` 渲染 background 与别称 / 称她为、``extra`` 渲染 purpose 与 speech；
5. 三条线索全空的成员交给 AI 定，不回写配置；
6. 归一化（别称串 / id 缺省 / 非法 role / 章节 cast 的 @ 与分隔符）；
7. 校验器：重名撞车、非法代词、章节 cast 指向不存在的成员；
8. 构建 / 提交分离：``build_turn`` 不写会话状态，``commit_turn`` 才生效，
   canonical 历史只留章节与叙事指令。
"""

import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_report_path = os.path.join(tempfile.mkdtemp(prefix="cast_check_"), "report.txt")
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
from dungeon.models import DungeonState, DungeonTextType  # noqa: E402
from dungeon.audio.chapters import normalize_chapter  # noqa: E402
from dungeon.prompts import DungeonPromptBuilder  # noqa: E402
from dungeon.validate import validate_scenario_config  # noqa: E402
from dungeon.window.base import DungeonWindowBase  # noqa: E402

_TEXT_TYPE = list(DungeonTextType)[0]


def make_session(*, cast_members=None, protagonist_background=None,
                 chapters=None, current="城里"):
    """提示词构建用的最小会话。不传 cast / 背景时，等价于「旧方案」。"""
    session = types.SimpleNamespace(
        coupling_level=None, protagonist_title="", name="测试", nick="",
        evolution_attrs=[], personality=types.SimpleNamespace(description=""),
        initial_prompt="", section_prompts={},
        body_parts={}, merged_landmarks=[], _chapter_landmark_cache=None,
        settings={"blocked_words": []}, height=480.0,
        chapters=chapters if chapters is not None else [{"name": "城里"}],
        current_chapter=current,
        prompted_parts=set(), keyword_match_given=set(), last_ai_text="",
        prompted_presence_landmark="", option_choice=None, story_summary=None,
        replay_data=[], _detail_queries=None, pending_insertions=[],
        dungeon_state=DungeonState(intrusion=2.0, destruction=2.0),
    )
    session.chapter_landmarks = types.MethodType(
        DungeonWindowBase.chapter_landmarks, session)
    if cast_members is not None:
        session.cast = cast_members
    if protagonist_background is not None:
        session.protagonist_background = protagonist_background
    return session


def system_prompt(session):
    return DungeonPromptBuilder(session).build_system_prompt()


def user_prompt(session):
    return DungeonPromptBuilder(session).build_user_prompt(_TEXT_TYPE)


# ---------------- 1. 空表零差异 ----------------
_legacy = make_session()
check("旧方案：用户提示词不出现「本段在场」",
      "本段在场" not in user_prompt(_legacy))
check("旧方案：系统提示词不出现人物块",
      "【主角】" not in system_prompt(_legacy)
      and "【人物" not in system_prompt(_legacy))

_empty_new = make_session(cast_members=[], protagonist_background="")
check("空表新方案与旧方案的用户提示词逐字一致",
      user_prompt(_legacy) == user_prompt(_empty_new),
      repr(user_prompt(_empty_new)[-120:]))
check("空表新方案与旧方案的系统提示词逐字一致",
      system_prompt(_legacy) == system_prompt(_empty_new))

# ---------------- 2. 在场行只认章节声明 ----------------
_li = cast.normalize_member({
    "id": "li", "role": "key", "name": "李队长",
    "aliases": ["李队", "队长"], "pronoun": "他",
    "call_her": "姑娘", "background": "本地消防中队长，女儿还埋在废墟下",
    "speech": "短句、命令口吻",
})
_wang = cast.normalize_member({
    "id": "wang", "role": "extra", "name": "王店主", "pronoun": "他",
    "purpose": "表现城区的物资已经见底", "speech": "絮叨，反复讲价钱",
})
_undeclared = make_session(cast_members=[_li, _wang],
                           chapters=[{"name": "城里"}])
check("章节没声明在场：不写在场行、也不注入设定",
      "本段在场" not in user_prompt(_undeclared)
      and "李队长" not in user_prompt(_undeclared))

_declared = make_session(cast_members=[_li, _wang],
                         chapters=[{"name": "城里", "cast": ["李队长"]}])
_first = user_prompt(_declared)
check("在场行按章节声明生成", "【本段在场】李队长" in _first, _first[:400])
check("首次出场块渲染 key 的 background 与结构化线索",
      "【人物（故事开始时的设定）】" in _first
      and "本地消防中队长" in _first
      and "也称「李队」「队长」" in _first
      and "称她为「姑娘」" in _first, _first[:500])
check("重要角色不渲染说话方式（这一栏只属于群像）",
      "短句、命令口吻" not in _first, _first[:500])
check("未声明的成员一个字都不进提示词", "王店主" not in _first)
check("框定语说明这是起点", "起点不是现状" in _first)
check("在场约束只许到场者说话", "只让主角与到场的角色说话和行动" in _first)

# ---------------- 3. 首次全量，之后简报 ----------------
_second = user_prompt(_declared)
check("第二次出场改用人物简报（完整设定块不再注入）",
      "【本段在场】李队长" in _second
      and "【人物简报（当前状态）】" in _second
      and "本地消防中队长" in _second
      and "【人物（故事开始时的设定）】" not in _second, _second[:400])
check("简报不重复完整设定块的结构化线索",
      "也称「李队」「队长」" not in _second, _second[:400])
check("第二次仍然写在场约束", "只让主角与到场的角色说话和行动" in _second)

# ---------------- 4. extra 渲染 purpose，线索全空交给 AI ----------------
_wang_only = make_session(cast_members=[_wang],
                          chapters=[{"name": "城里", "cast": ["王店主"]}])
_extra_prompt = user_prompt(_wang_only)
check("群像渲染作用与说话方式（不渲染 background）",
      "表现城区的物资已经见底" in _extra_prompt
      and "说话：絮叨，反复讲价钱" in _extra_prompt)
_extra_second = user_prompt(_wang_only)
check("群像第二次出场仍带初始线索（群像只有初始字段）",
      "【人物简报（当前状态）】" in _extra_second
      and "表现城区的物资已经见底" in _extra_second
      and "说话：絮叨，反复讲价钱" in _extra_second, _extra_second[:300])

_extra_full = cast.normalize_member({
    "name": "王店主", "role": "extra", "aliases": ["王老板"], "pronoun": "他",
    "call_her": "姑娘", "purpose": "表现城区的物资已经见底",
})
_extra_full_prompt = user_prompt(make_session(
    cast_members=[_extra_full], chapters=[{"name": "城里", "cast": ["王店主"]}]))
check("群像不渲染别称与「称她为」（这两栏只属于重要角色）",
      "王老板" not in _extra_full_prompt
      and "姑娘" not in _extra_full_prompt, _extra_full_prompt[:400])

check("字段归属：重要角色没有说话方式，群像没有别称 / 称她为",
      cast.role_supports("key", "speech") is False
      and cast.role_supports("extra", "speech") is True
      and cast.role_supports("extra", "aliases") is False
      and cast.role_supports("extra", "call_her") is False
      and cast.role_supports("key", "background") is True
      and cast.role_supports("key", "aliases") is True)

_speech_only = cast.normalize_member({
    "name": "赵司机", "role": "key", "speech": "说话很冲"})
_speech_only_prompt = user_prompt(make_session(
    cast_members=[_speech_only], chapters=[{"name": "城里", "cast": ["赵司机"]}]))
check("重要角色只填了说话方式：当作没填，交给 AI 定",
      "【本段首次出场】赵司机" in _speech_only_prompt
      and "说话很冲" not in _speech_only_prompt, _speech_only_prompt[:400])

_blank = cast.normalize_member({"name": "赵司机", "role": "extra"})
_blank_session = make_session(cast_members=[_blank],
                              chapters=[{"name": "城里", "cast": ["赵司机"]}])
_blank_prompt = user_prompt(_blank_session)
check("线索全空：给出「由 AI 定三件事」的指令",
      "【本段首次出场】赵司机" in _blank_prompt
      and "确定三件事" in _blank_prompt, _blank_prompt[:400])

# ---------------- 5. 主角设定进系统提示词 ----------------
_hero = make_session(protagonist_background="外地调来的救援调度员，想把东岸居民撤完")
_hero_system = system_prompt(_hero)
check("主角设定进系统提示词", "【主角】主角" in _hero_system
      and "外地调来的救援调度员" in _hero_system, _hero_system[-400:])
check("主角设定标明是起点", "起点不是现状" in _hero_system[-400:])
check("主角设定不进用户提示词",
      "外地调来的救援调度员" not in user_prompt(_hero))

# ---------------- 6. 归一化 ----------------
_aliases = cast.normalize_member({"name": "李队长", "aliases": "@李队@、队长"})
check("别称串按分隔符切分并去掉 @",
      _aliases["aliases"] == ["李队", "队长"], _aliases["aliases"])
check("缺 id 时用称呼、缺称呼时给占位 id",
      cast.normalize_member({"name": "李队长"})["id"] == "李队长"
      and cast.normalize_member({}, 2)["id"] == "cast3")
check("非法 role 按 key、非法代词按空",
      cast.normalize_member({"name": "x", "role": "boss"})["role"] == "key"
      and cast.normalize_member({"name": "x", "pronoun": "你"})["pronoun"] == "")
check("非列表的 cast 归一化为空表",
      cast.normalize_cast({"name": "x"}) == [] and cast.normalize_cast(None) == [])

_chapter = normalize_chapter({"name": "城里", "cast": "@李队长@、李队"})
check("章节 cast 归一化：去 @、去重、保持顺序",
      _chapter["cast"] == ["李队长", "李队"], _chapter["cast"])
check("章节没写 cast 时不补键（旧磁盘内容不变）",
      "cast" not in normalize_chapter({"name": "城里"}))
check("按别称能找到成员",
      cast.find_member([_li], "队长") is _li
      and cast.find_member([_li], "李队") is _li
      and cast.find_member([_li], "查无此人") is None)

# ---------------- 7. 校验器 ----------------
_diag = validate_scenario_config({
    "chapters": [{"name": "城里", "start": True}],
    "cast": [
        {"name": "李队长", "aliases": ["队长"], "pronoun": "他"},
        {"name": "队长", "pronoun": "她"},
        {"name": "", "role": "key"},
        {"name": "赵司机", "role": "boss", "pronoun": "你"},
    ],
})
_messages = " | ".join(d.message for d in _diag)
check("校验：别称与另一个成员的称呼撞车 → error",
      any(d.level == "error" and "队长" in d.message for d in _diag), _messages)
check("校验：缺称呼 → error",
      any(d.level == "error" and "缺少称呼" in d.message for d in _diag), _messages)
check("校验：非法 role / 代词 → warning",
      any(d.level == "warning" and "定位" in d.message for d in _diag)
      and any(d.level == "warning" and "代词" in d.message for d in _diag), _messages)

_diag_unknown = validate_scenario_config({
    "chapters": [{"name": "城里", "start": True, "cast": ["查无此人"]}],
    "cast": [{"name": "李队长"}],
})
check("校验：章节 cast 指向表里不存在的成员 → warning",
      any(d.level == "warning" and "查无此人" in d.message for d in _diag_unknown),
      " | ".join(d.message for d in _diag_unknown))

# ---------------- 8. 构建 / 提交分离 ----------------
_pure = make_session(cast_members=[_li, _wang],
                     chapters=[{"name": "城里", "cast": ["李队长"]}])
_builder = DungeonPromptBuilder(_pure)
_turn_a = _builder.build_turn(_TEXT_TYPE)
_turn_b = _builder.build_turn(_TEXT_TYPE)
check("build_turn 不登记首次出场：连续构建两次都是完整设定",
      "【人物（故事开始时的设定）】" in _turn_a.text
      and "【人物（故事开始时的设定）】" in _turn_b.text
      and not getattr(_pure, "introduced_cast", None))
check("build_turn 不改会话的已提示部位 / 关键词",
      _pure.prompted_parts == set() and _pure.keyword_match_given == set())
check("canonical 只留章节与叙事指令，不含现场快照",
      "当前章节：城里" in _turn_a.canonical
      and "【本段在场】" not in _turn_a.canonical
      and "本段只让主角与到场的角色说话和行动" not in _turn_a.canonical)

_builder.commit_turn(_turn_a.effects)
check("commit_turn 登记首次出场", "li" in _pure.introduced_cast)
_turn_c = _builder.build_turn(_TEXT_TYPE)
check("提交后下一段改用简报",
      "【人物简报（当前状态）】" in _turn_c.text
      and "【人物（故事开始时的设定）】" not in _turn_c.text, _turn_c.text[:400])

_queries = make_session(cast_members=[_li], chapters=[{"name": "城里"}])
_queries._detail_queries = ["她有多大"]
_qbuilder = DungeonPromptBuilder(_queries)
_qturn = _qbuilder.build_turn(_TEXT_TYPE)
check("build_turn 不消费细节疑问",
      _queries._detail_queries == ["她有多大"]
      and _qturn.effects.consume_detail_queries)
_qbuilder.commit_turn(_qturn.effects)
check("commit_turn 才清空细节疑问", _queries._detail_queries == [])

_wrapped = make_session(cast_members=[_li],
                        chapters=[{"name": "城里", "cast": ["李队长"]}])
DungeonPromptBuilder(_wrapped).build_user_prompt(_TEXT_TYPE)
check("build_user_prompt 兼容旧调用方：构建即提交",
      "li" in (_wrapped.introduced_cast or set()))

# ---------------- 9. 称呼稳定性规则 ----------------
# 规则只挂在对话分支的说话人规则之后（solea / bulla 档才有 speaker_rule）
_stab = make_session(cast_members=[_li],
                     chapters=[{"name": "城里", "cast": ["李队长"]}])
_stab.coupling_level = "solea"
check("有角色时追加称呼稳定性规则",
      "同一角色在不同段落使用同一个称呼标记"
      in DungeonPromptBuilder(_stab).build_turn(DungeonTextType.DIALOG).text)
_stab_empty = make_session()
_stab_empty.coupling_level = "solea"
check("空配置不注入称呼稳定性规则",
      "同一角色在不同段落使用同一个称呼标记"
      not in DungeonPromptBuilder(_stab_empty).build_turn(DungeonTextType.DIALOG).text)
check("空配置不注入称呼稳定性规则",
      "同一角色在不同段落使用同一个称呼标记" not in user_prompt(_legacy))

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
