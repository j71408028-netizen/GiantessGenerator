"""副本内地点与参照物自检：章节地址 → 地标候选池。

用法：``python tests/check_dungeon_scene.py``
输出：控制台一行 ASCII 结论 + UTF-8 报告文件路径。

覆盖 P1「按章节地址构建地标候选池」这一段：

1. 旧方案回归（无地址、无清单）与四种组合的池子；
2. 地址过滤的粒度（二级 / 三级 / 兄弟格 / 跨二级 / 跨维度 / 私有名 / portable）；
3. ``landmarks`` 白名单的三种写法（缺省 / 空列表 / 名字子集）；
4. 坏地址只留清单、**不**回落全量，且校验器报 error；
5. 会话访问器与提示词读同一个池子，按章节缓存、切章失效；
6. 父进程把地标解析成完整地址再过载荷，副本内的地址相配才可能成立；
7. 展示：``place`` / 紧凑地址 / 空三种形态，以及提示词的「当前地点」行；
8. 结局传送：写回 ``position``、坏地址不动位置、未建角色时暂存。

坐标带出预算属于后续阶段，不在本文件断言。
"""

import json
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_report_path = os.path.join(tempfile.mkdtemp(prefix="dungeon_scene_"), "report.txt")
_log = open(_report_path, "w", encoding="utf-8")
sys.stdout = _log
sys.stderr = _log

failures = []
total = 0


def check(name, ok, extra=""):
    global total
    total += 1
    print(("  OK   " if ok else "  FAIL ") + name + (f"  <{extra}>" if extra and not ok else ""))
    if not ok:
        failures.append(name)


from core.models import Landmark  # noqa: E402
from dungeon import scene  # noqa: E402
from dungeon.scenario_graph import build_graph  # noqa: E402
from dungeon.validate import format_diagnostics, validate_scenario_config  # noqa: E402

# 地址文本按 core.address_model 的规范格式：<规模>@<维度>-<一级>-<二级>-<三级>
_STYLE_REG = "2e6_3e5_5e4@RW-CN1"
_CITY = "2e6_3e5_5e4@RW-CN1-HD"          # 二级地域
_CELL = "2e6_3e5_5e4@RW-CN1-HD-9"        # 三级地域（_CITY 之内）
_SIBLING = "2e6_3e5_5e4@RW-CN1-HD-8"     # 兄弟格
_OTHER_CITY = "2e6_3e5_5e4@RW-CN1-XY"    # 同维度、不同二级
_OTHER_WORLD = "2e6_3e5_5e4@XW-CN1-HD"   # 不同维度


def _lm(name, size, address="", address_mode="", dimension="vertical"):
    return Landmark(name=name, size=size, dimension=dimension,
                    frequency="unique", address=address,
                    address_mode=address_mode)


def _names(pool):
    return [landmark.name for landmark in pool]


# ---------------- 1. 四种组合 ----------------
_merged = [
    _lm("黑塔", 468, _CELL),
    _lm("断门", 120, _SIBLING),
    _lm("跨城桥", 900, _OTHER_CITY),
    _lm("别界塔", 700, _OTHER_WORLD),
    _lm("无主塔", 300),                       # 没地址：不参与相配
    _lm("便携塔", 50, _CELL, address_mode="portable"),
]
_config = {"scene_landmarks": [
    {"name": "护城河", "size": 40, "dimension": "horizontal",
     "horizontal_type": "length"},
    {"name": "黑塔", "size": 500},           # 与全局池同名：清单优先
]}

check("无地址、无清单：候选池 = merged_landmarks 全量",
      _names(scene.landmarks_for_chapter({}, {}, _merged)) == _names(_merged))
check("无地址、有清单：只有清单",
      _names(scene.landmarks_for_chapter(_config, {}, _merged)) == ["护城河", "黑塔"])
check("有地址、无清单：只有地址相配的全局地标",
      _names(scene.landmarks_for_chapter({}, {"address": _CITY}, _merged))
      == ["黑塔", "断门"])
check("有地址、有清单：并集，同名以清单为准",
      _names(scene.landmarks_for_chapter(_config, {"address": _CITY}, _merged))
      == ["护城河", "黑塔", "断门"])
check("清单优先：同名地标的尺寸取清单里的",
      [lm.size for lm in scene.landmarks_for_chapter(
          _config, {"address": _CITY}, _merged) if lm.name == "黑塔"] == [500.0])

# ---------------- 2. 地址过滤的粒度 ----------------
def _pool_at(address):
    return _names(scene.ambient_landmarks(_merged, address))


check("二级地址含其下三级地标", "黑塔" in _pool_at(_CITY))
check("三级地址不含兄弟格", "黑塔" in _pool_at(_CELL) and "断门" not in _pool_at(_CELL))
check("同维度、不同二级地域不进池", "跨城桥" not in _pool_at(_CITY))
check("跨维度不进池", "别界塔" not in _pool_at(_CITY))
check("没地址的地标不参与相配", "无主塔" not in _pool_at(_CITY))
check("portable 地标不参与相配", "便携塔" not in _pool_at(_CELL))
check("维度级地址覆盖整个世界（无私有名时）",
      "跨城桥" in _pool_at(_STYLE_REG))

# 私有名必须互相满足：只写 @*! 不算
_private_any = "1e3_1e2_1e1@*!RW-CN1-HD"
_chunked = "1e3_1e2_1e1@HD!RW-CN1-HD"
check("章节地址声明了私有名时与无私有名的地标不配对（约束是双向的）",
      scene.ambient_landmarks(_merged, _chunked) == [])
_same_chunk_lm = [_lm("私名塔", 400, "1e3_1e2_1e1@HD!RW-CN1-HD")]
check("章节地址只写 @*! 时不能满足地标声明的私有名",
      scene.ambient_landmarks(_same_chunk_lm, _private_any) == [])
check("双方私有名一致时配对",
      _names(scene.ambient_landmarks(_same_chunk_lm, _chunked)) == ["私名塔"])

# ---------------- 3. 白名单 ----------------
check("landmarks 缺省 = 整份清单",
      _names(scene.landmarks_for_chapter(_config, {"address": _CITY}, []))
      == ["护城河", "黑塔"])
check("landmarks = [] = 本章不用清单（有地址时只剩全局池）",
      _names(scene.landmarks_for_chapter(
          _config, {"address": _CITY, "landmarks": []}, _merged))
      == ["黑塔", "断门"])
check("landmarks = [] 且无地址：池子为空，不回落到全量",
      scene.landmarks_for_chapter(_config, {"landmarks": []}, _merged) == [])
check("landmarks 名字子集只取被点名的",
      _names(scene.landmarks_for_chapter(
          _config, {"landmarks": ["护城河"]}, _merged)) == ["护城河"])
check("白名单里的陌生名字被忽略（校验器负责报错）",
      _names(scene.landmarks_for_chapter(
          _config, {"landmarks": ["不存在"]}, [])) == [])

# ---------------- 4. 坏地址 ----------------
_bad_address = {"address": "乱写", "landmarks": None}
check("坏地址：只留清单，不回落全量",
      _names(scene.landmarks_for_chapter(_config, _bad_address, _merged))
      == ["护城河", "黑塔"])
check("坏地址 + 无清单：池子为空（宁可没有参照物也不放大范围）",
      scene.landmarks_for_chapter({}, _bad_address, _merged) == [])

_bad_config = {
    "scene_landmarks": [
        {"name": "黑塔", "size": 0},
        {"name": "黑塔", "size": 10, "dimension": "斜向"},
        {"name": "坏\n名字", "size": 1},
        {"name": "护城河", "size": 40, "dimension": "horizontal",
         "frequency": "rare", "horizontal_type": "深度"},
    ],
    "entry_address": _CITY,
    "chapters": [
        {"name": "一", "start": True, "address": "乱写",
         "landmarks": ["黑塔", "不存在"]},
        {"name": "二", "ending": True, "address": _OTHER_WORLD},
    ],
}
_bad_diags = validate_scenario_config(_bad_config)


def _has(level, path, message_part=""):
    return any(d.level == level and d.path == path
               and message_part in d.message for d in _bad_diags)


check("诊断：尺寸非正 error", _has("error", "scene_landmarks[0].size"),
      format_diagnostics(_bad_diags))
check("诊断：维度非法 error", _has("error", "scene_landmarks[1].dimension"),
      format_diagnostics(_bad_diags))
check("诊断：清单内重名 error", _has("error", "scene_landmarks[1].name", "重名"))
check("诊断：名字含换行 error", _has("error", "scene_landmarks[2].name", "换行"))
check("诊断：频率非法 warning",
      _has("warning", "scene_landmarks[3].frequency"), format_diagnostics(_bad_diags))
check("诊断：横向类型非法 warning",
      _has("warning", "scene_landmarks[3].horizontal_type"))
check("诊断：章节地址无法解析 error", _has("error", "chapters[0].address"))
check("诊断：白名单引用不存在的地标 error", _has("error", "chapters[0].landmarks"))
check("诊断：取景地址与入口不在同一维度 warning",
      _has("warning", "chapters[1].address", "维度"))

_clean_scene_config = {
    "scene_landmarks": [
        {"name": "黑塔", "size": 468},
        {"name": "护城河", "size": 40, "dimension": "horizontal"},
    ],
    "entry_address": _CITY,
    "chapters": [
        {"name": "一", "start": True, "place": "黑塔脚下",
         "address": _CITY, "landmarks": ["黑塔"]},
        {"name": "二", "ending": True, "address": _CELL,
         "exit_address": _CELL},
    ],
}
_clean_diags = validate_scenario_config(_clean_scene_config)
_scene_diags = [
    d for d in _clean_diags
    if d.path == "scene_landmarks" or d.path.startswith("scene_landmarks[")
    or d.path.endswith((".address", ".exit_address", ".landmarks"))
]
check("干净的地址与地标配置不报 error/warning",
      not [d for d in _scene_diags if d.level in ("error", "warning")],
      format_diagnostics(_scene_diags))

# ---------------- 5. 影响不到推进 ----------------
_graph_plain = build_graph({"chapters": _clean_scene_config["chapters"]})
_graph_scene = build_graph(_clean_scene_config)
check("改地址 / 改清单不改变关系图的节点集合",
      set(_graph_plain.nodes) == set(_graph_scene.nodes),
      (set(_graph_plain.nodes) ^ set(_graph_scene.nodes)))
check("改地址 / 改清单不改变关系图的边",
      {(e.src, e.dst, e.kind) for e in _graph_plain.edges}
      == {(e.src, e.dst, e.kind) for e in _graph_scene.edges})

# ---------------- 6. 会话访问器与提示词 ----------------
from dungeon.prompts import DungeonPromptBuilder  # noqa: E402
from dungeon.window.base import DungeonWindowBase  # noqa: E402


def _session(chapters, current):
    session = types.SimpleNamespace(
        chapters=chapters, current_chapter=current,
        scenario_config={}, merged_landmarks=_merged,
        _chapter_landmark_cache=None,
        body_parts={"身高": 480.0}, height=480.0,
        settings={"blocked_words": []},
    )
    session.chapter_landmarks = types.MethodType(
        DungeonWindowBase.chapter_landmarks, session)
    return session


_chapters = [
    {"name": "城里", "address": _CITY},
    {"name": "别处", "address": _OTHER_CITY},
    {"name": "无地址"},
]
_session_mixed = _session(_chapters, "城里")
check("访问器：当前章节有地址时只给相配的地标",
      _names(_session_mixed.chapter_landmarks()) == ["黑塔", "断门"],
      _names(_session_mixed.chapter_landmarks()))
_session_mixed.current_chapter = "别处"
check("访问器：切章后池子随之更换",
      _names(_session_mixed.chapter_landmarks()) == ["跨城桥"],
      _names(_session_mixed.chapter_landmarks()))
_session_mixed.current_chapter = "无地址"
check("访问器：无地址章节回到全量",
      _names(_session_mixed.chapter_landmarks()) == _names(_merged))
check("访问器：按章节缓存同一份池子",
      _session_mixed.chapter_landmarks()
      is _session_mixed.chapter_landmarks())

session_city = _session(_chapters, "城里")
_text = DungeonPromptBuilder(session_city)._get_height_match()
check("提示词：换算句取自本章的池子", "黑塔" in _text, _text)
check("提示词：池子外的地标不会出现在换算句里",
      "跨城桥" not in _text and "别界塔" not in _text, _text)
_empty_pool_session = _session([{"name": "空", "address": _OTHER_CITY}], "空")
_empty_pool_session.merged_landmarks = [_lm("只在此城", 400, _CELL)]
check("提示词：地址过滤后没有候选就不产生换算句",
      DungeonPromptBuilder(_empty_pool_session)._get_height_match() == "")

# ---------------- 7. 载荷里的完整地址 ----------------
from dungeon.window.launch_payload import (deserialize_launch,  # noqa: E402
                                           serialize_launch)
from services.exploration.address_plan import AddressPlanner  # noqa: E402


class _FakeLandmarkRepo:
    def __init__(self, registers):
        self._registers = registers

    def load_style_registers(self, style_names):
        return {name: self._registers.get(name, "") for name in (style_names or [])}

    def load_style_address(self, style_name):
        return self._registers.get(style_name, "")


class _FakeCatalog:
    def __init__(self, landmarks, styles, registers):
        self.merged_landmarks = landmarks
        self._landmark_styles = {id(lm): style
                                 for lm, style in zip(landmarks, styles)}
        self.selected_styles = list(registers)
        self.landmark_repo = _FakeLandmarkRepo(registers)


_relative = [
    _lm("黑塔", 468, "-HD-9"),                      # 相对后缀，父进程解析
    _lm("便携塔", 50, _CELL, address_mode="portable"),
    _lm("谜之塔", 300, "-ZZ-1"),                    # 风格未注册，解析不出
]
_catalog = _FakeCatalog(_relative, ["有注册", "有注册", "无注册"],
                       {"有注册": _STYLE_REG})
_exported = AddressPlanner(_catalog).landmarks_for_export()
check("导出：相对后缀被组合成完整地址",
      _exported[0].address.startswith("2e6_3e5_5e4@")
      and _exported[0].address_mode == "exact", _exported[0])
check("导出：原始地标不被改写（父进程的池子仍是相对后缀）",
      _relative[0].address == "-HD-9", _relative[0])
check("导出：portable 地标不参与地址相配",
      _exported[1].address == "" and _exported[1].address_mode == "portable")
check("导出：解析失败的地标照样进载荷，只是不参与相配",
      _exported[2].name == "谜之塔" and _exported[2].address == ""
      and _exported[2].size == 300.0)
check("导出后的地标能被章节地址筛出来",
      _names(scene.ambient_landmarks(_exported, _CITY)) == ["黑塔"],
      _names(scene.ambient_landmarks(_exported, _CITY)))

_payload = serialize_launch({"merged_landmarks": _exported})
try:
    json.dumps(_payload, ensure_ascii=False)
    _json_safe = True
except (TypeError, ValueError):
    _json_safe = False
check("载荷：地标序列化成纯数据（JSON 安全）", _json_safe)
_restored = deserialize_launch(json.loads(json.dumps(_payload, ensure_ascii=False)))
check("载荷：还原成 Landmark 且地址保留",
      all(isinstance(lm, Landmark) for lm in _restored["merged_landmarks"])
      and _restored["merged_landmarks"] == _exported,
      _restored["merged_landmarks"])
check("载荷：还原后的地址仍能配对",
      _names(scene.ambient_landmarks(_restored["merged_landmarks"], _CITY))
      == ["黑塔"])

# ---------------- 8. 展示：当前地点 ----------------
from core.models import CharacterSnapshot  # noqa: E402
from dungeon.models import DungeonState, DungeonTextType  # noqa: E402
from dungeon.window.engine import DungeonStoryEngine  # noqa: E402
from dungeon.window.persistence import DungeonPersistence  # noqa: E402

check("地点：place 优先于地址",
      scene.place_label({"place": "黑塔脚下", "address": _CITY}) == "黑塔脚下")
_compact = scene.place_label({"address": _CITY})
check("地点：没写 place 时退回紧凑地址", "CN1" in _compact and _compact != _CITY,
      _compact)
check("地点：两处都空返回空串", scene.place_label({}) == "")
check("地点：坏地址照原样展示（校验器负责报错，展示不做静默吞掉）",
      scene.place_label({"address": "乱写"}) == "乱写")


def _place_session(chapters, current):
    session = types.SimpleNamespace(chapters=chapters, current_chapter=current)
    session.current_place = types.MethodType(DungeonWindowBase.current_place, session)
    return session


_place_state = _place_session(
    [{"name": "城里", "address": _CITY}, {"name": "空章"}, {"name": "有地名",
                                                         "place": "集市"}], "城里")
check("访问器：current_place 读章节地址",
      _place_state.current_place() == _compact, _place_state.current_place())
_place_state.current_chapter = "有地名"
check("访问器：写了 place 就用 place", _place_state.current_place() == "集市")
_place_state.current_chapter = "空章"
check("访问器：没地点的章节返回空串", _place_state.current_place() == "")


def _prompt_session(chapters, current):
    session = types.SimpleNamespace(
        chapters=chapters, current_chapter=current,
        name="测试", nick="", coupling_level=None, protagonist_title="",
        prompted_parts=set(), keyword_match_given=set(), last_ai_text="",
        option_choice=None, story_summary=None, replay_data=[], _detail_queries=None,
        pending_insertions=[], selected_quip_styles=[],
        settings={"blocked_words": []}, scenario_config={},
        merged_landmarks=[], _chapter_landmark_cache=None,
        body_parts={}, dungeon_state=DungeonState(intrusion=2.0, destruction=2.0),
    )
    session.chapter_landmarks = types.MethodType(
        DungeonWindowBase.chapter_landmarks, session)
    return session


_text_type = list(DungeonTextType)[0]
_place_prompt = DungeonPromptBuilder(
    _prompt_session([{"name": "城里", "address": _CITY, "place": "黑塔脚下"}],
                    "城里")).build_user_prompt(_text_type)
check("提示词：章节行之后给出当前地点",
      "当前章节：城里" in _place_prompt and "当前地点：黑塔脚下" in _place_prompt,
      _place_prompt[-300:])
check("提示词：地点行紧跟章节行",
      _place_prompt.index("当前地点：") > _place_prompt.index("当前章节："))
_no_place_prompt = DungeonPromptBuilder(
    _prompt_session([{"name": "城里", "address": _CITY}], "城里")
).build_user_prompt(_text_type)
check("提示词：只写地址的章节用紧凑地址当地点",
      f"当前地点：{_compact}" in _no_place_prompt, _no_place_prompt[-300:])
_bare_prompt = DungeonPromptBuilder(
    _prompt_session([{"name": "城里"}], "城里")).build_user_prompt(_text_type)
check("提示词：虚构章节不出现地点行", "当前地点" not in _bare_prompt,
      _bare_prompt[-300:])

# ---------------- 9. 结局传送 ----------------
check("传送：合法地址原样返回", scene.landing_position(_CELL) == _CELL)
check("传送：空 / 坏地址一律不动位置",
      scene.landing_position("") == "" and scene.landing_position("乱写") == ""
      and scene.landing_position(None) == "")


def _ending_session(exit_text="", char=None):
    session = types.SimpleNamespace(
        character=char,
        dungeon_state=DungeonState(intrusion=2.0, destruction=2.0),
        height=100.0, ending_text="", name="测试", nick="",
        body_parts={},
        original_height=1.6, personality=None, greed=0.0, tags=[],
        intro_hidden="", intro_visible="", character_repo=None,
        _pending_exit_position="",
        ending_effects={"exit_address": exit_text},
    )
    session._apply_ending_effects = types.MethodType(
        DungeonPersistence._apply_ending_effects, session)
    session._create_character_from_session = types.MethodType(
        DungeonPersistence._create_character_from_session, session)
    return session


def _char(position=""):
    return CharacterSnapshot(giantess_id="g1", name="测试", position=position)


_moved = _ending_session(_CELL, _char(_CITY))
_moved._apply_ending_effects()
check("传送：结算把位置写回 exit_address", _moved.character.position == _CELL,
      _moved.character.position)
check("传送：追加的演化行带上到访地址",
      _moved.character.evolution[-1].position == _CELL,
      _moved.character.evolution[-1].position)

_kept = _ending_session("", _char(_CITY))
_kept._apply_ending_effects()
check("传送：没写 exit_address 时位置不变",
      _kept.character.position == _CITY and _kept.character.evolution[-1].position == "")

_bad_exit = _ending_session("乱写", _char(_CITY))
_bad_exit._apply_ending_effects()
check("传送：坏地址不写位置（校验器报 error，运行期保持原位）",
      _bad_exit.character.position == _CITY)

_pending = _ending_session(_CELL, None)
_pending._apply_ending_effects()
check("传送：尚未建角色时先暂存", _pending._pending_exit_position == _CELL)
_new_char = _pending._create_character_from_session()
check("传送：暂存值成为新角色的初始位置", _new_char.position == _CELL,
      _new_char.position)
check("传送：新角色的初始演化行同样带位置",
      _new_char.evolution[-1].position == _CELL)
check("传送：暂存只消费一次", _pending._pending_exit_position == "")

_new_plain = _ending_session("", None)
_new_plain._apply_ending_effects()
check("传送：没有传送目的地时新角色位置为空",
      _new_plain._create_character_from_session().position == "")

# 传送的唯一入口是结束章节（`ending` 动作已删除，触发器路径不再读 exit_address）
def _ending_chapter_session():
    session = types.SimpleNamespace()
    session._start_ending_generation = lambda: None   # 只验结算字段，不生成结局文本
    return session


_from_chapter = _ending_chapter_session()
DungeonStoryEngine._terminate_from_ending_chapter(
    _from_chapter, {"name": "终章", "ending": True, "exit_address": _CELL})
check("传送：结束章节把 exit_address 带进结算字段",
      _from_chapter.pending_ending["action_data"]["exit_address"] == _CELL,
      _from_chapter.pending_ending)
_no_exit = _ending_chapter_session()
DungeonStoryEngine._terminate_from_ending_chapter(
    _no_exit, {"name": "终章", "ending": True})
check("传送：没写 exit_address 的结束章节结算字段为空",
      _no_exit.pending_ending["action_data"]["exit_address"] == "")

# ---------------- 结论 ----------------
_log.flush()
sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__
if failures:
    print(f"[check_dungeon_scene] FAILED {len(failures)}/{total}: {failures}")
else:
    print(f"[check_dungeon_scene] PASSED {total}/{total}")
print(f"[check_dungeon_scene] report: {_report_path}")
sys.exit(1 if failures else 0)
