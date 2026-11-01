"""副本内地点与参照物自检：章节独特地标与常见地标私有名过滤。

用法：``python tests/check_dungeon_scene.py``
输出：控制台一行 ASCII 结论 + UTF-8 报告文件路径。

覆盖「按章节构建地标候选池」这一段：

1. 候选池四种组合（独特地标内联 / 私有名过滤常见地标 / 并集 / 空池不回落）；
2. 私有名过滤的判据（chunk 一致 / 无私有名 / 独特地标不经私有名进池 / portable）；
3. 坏条目跳过（校验 error），旧字段退役 warning；
4. 会话访问器与提示词读同一个池子，按章节缓存、切章失效；
5. 父进程把地标解析成完整地址再过载荷（含常见地标按风格注册地址升级，
   私有名随地址进载荷），子进程按私有名过滤才可能成立；
6. 展示：``place`` / 紧凑地址 / 空三种形态，以及提示词的「当前地点」行；
7. 结局传送：写回 ``position``、坏地址不动位置、未建角色时暂存；
8. 独特地标的「同时在场」触发：同段 / 相邻两段 / 缺她 / 隔两段 / 防重复；
9. 文本校准块：坐标数值行、谓词与上界、尺度行复用本章换算句、
   ``direction`` 顺挫澄清句。

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

# 地址文本按 core.address_model 的规范格式：<规模>@<私有名>!<维度>-<一级>-<二级>-<三级>
_CHUNK_HD = "1e3_1e2_1e1@HD!RW-CN1-HD"
_CELL = "1e3_1e2_1e1@RW-CN1-HD-9"         # 无私有名的完整地址
_OTHER_CHUNK = "1e3_1e2_1e1@XY!RW-CN1-HD-3"
_STYLE_REG = "1e3_1e2_1e1@HD!RW-CN1"      # 带私有名的风格注册地址
_CITY = "1e3_1e2_1e1@HD!RW-CN1-HD"


def _lm(name, size, address="", address_mode="", dimension="vertical",
        frequency="unique"):
    return Landmark(name=name, size=size, dimension=dimension,
                    frequency=frequency, address=address,
                    address_mode=address_mode)


def _cm(name, size, address="", address_mode="", dimension="vertical"):
    """常见地标：私有名过滤只收这一类。"""
    return _lm(name, size, address, address_mode, dimension, frequency="common")


def _names(pool):
    return [landmark.name for landmark in pool]


# ---------------- 1. 候选池的四种组合 ----------------
_merged = [
    _cm("高楼", 60, _CHUNK_HD),
    _cm("路灯", 8, _OTHER_CHUNK),
    _cm("裸楼", 20, _CELL),                    # 地址没带私有名（chunk 为空）
    _cm("无主楼", 30),                         # 没地址
    _cm("便携楼", 50, _CHUNK_HD, address_mode="portable"),
    _lm("黑塔", 468, _CHUNK_HD),               # 独特：私有名通道也进不来
]
_chapter_landmarks = [
    {"name": "黑塔", "size": 500},
    {"name": "护城河", "size": 40, "dimension": "horizontal",
     "horizontal_type": "length"},
]

check("空配置：候选池为空，不回落全量",
      scene.landmarks_for_chapter({}, _merged) == [])
check("只配独特地标：只有内联清单",
      _names(scene.landmarks_for_chapter({"scene_landmarks": _chapter_landmarks},
                                         _merged)) == ["黑塔", "护城河"])
check("只填私有名：只有私有名一致的常见地标",
      _names(scene.landmarks_for_chapter({"common_private_name": "HD"}, _merged))
      == ["高楼"])
check("两者都配：并集，同名以独特地标为准",
      _names(scene.landmarks_for_chapter(
          {"scene_landmarks": [{"name": "高楼", "size": 999}],
           "common_private_name": "HD"}, _merged)) == ["高楼"])

# ---------------- 2. 私有名过滤的判据 ----------------
def _pool_at(private_name):
    return _names(scene.ambient_landmarks(_merged, private_name))


check("私有名一致才进池", _pool_at("HD") == ["高楼"])
check("其它私有名对应各自的常见地标", _pool_at("XY") == ["路灯"])
check("没填私有名 = 不使用常见地标", _pool_at("") == [] and _pool_at(None) == [])
check("没有这个私有名就是空池", _pool_at("没有") == [])
check("地址没带私有名的地标不进池", "裸楼" not in _pool_at("HD"))
check("没地址的常见地标不进池", "无主楼" not in _pool_at("HD"))
check("portable 常见地标不进池", "便携楼" not in _pool_at("HD"))
check("独特地标不经私有名通道进池", "黑塔" not in _pool_at("HD"))

# 章节独特地标：坏条目跳过、重名取第一条、横向类型回落默认
_mixed = scene.chapter_scene_landmarks({"scene_landmarks": [
    {"name": "黑塔", "size": 468},
    {"name": "黑塔", "size": 10, "dimension": "斜向"},     # 写坏 + 重名：跳过
    {"name": "坏\n名字", "size": 1},                        # 写坏：跳过
    {"name": "护城河", "size": 40, "dimension": "horizontal",
     "horizontal_type": "深度"},                            # 类型非法：回落 length
    "不是对象",
]})
check("独特地标：重名与非法维度跳过，其余照常（换行名留给校验器拦截）",
      _names(_mixed) == ["黑塔", "坏\n名字", "护城河"], _names(_mixed))
check("独特地标：横向类型非法回落 length",
      _mixed[2].horizontal_type == "length", _mixed[2])

# ---------------- 3. 校验诊断 ----------------
_bad_config = {
    "scene_landmarks": [{"name": "方案级旧清单", "size": 1}],
    "chapters": [
        {"name": "一", "start": True, "address": "乱写",
         "landmarks": ["旧白名单"],
         "scene_landmarks": [
             {"name": "黑塔", "size": 0},
             {"name": "黑塔", "size": 10, "dimension": "斜向"},
             {"name": "坏\n名字", "size": 1},
             {"name": "石塔", "size": 30},
         ],
         "common_private_name": "非法!名"},
        {"name": "二", "ending": True,
         "scene_landmarks": [{"name": f"塔{i}", "size": 10} for i in range(6)]
         + [{"name": "长桥", "size": 50, "dimension": "horizontal",
             "horizontal_type": "深度"}]},
    ],
}
_diags = validate_scenario_config(_bad_config)


def _has(level, path, message_part=""):
    return any(d.level == level and d.path == path and message_part in d.message
               for d in _diags)


check("诊断：尺寸非正 error", _has("error", "chapters[0].scene_landmarks[0].size"),
      format_diagnostics(_diags))
check("诊断：维度非法 error", _has("error", "chapters[0].scene_landmarks[1].dimension"))
check("诊断：本章重名 error",
      _has("error", "chapters[0].scene_landmarks[1].name", "重名"))
check("诊断：名字含换行 error", _has("error", "chapters[0].scene_landmarks[2].name", "换行"))
check("诊断：横向类型非法 warning",
      _has("warning", "chapters[1].scene_landmarks[6].horizontal_type"))
check("诊断：只有一种维度 warning",
      _has("warning", "chapters[0].scene_landmarks", "只有竖向"))
check("诊断：私有名非法 warning",
      _has("warning", "chapters[0].common_private_name", "非法字符"))
check("诊断：独特地标超过 5 条 warning",
      _has("warning", "chapters[1].scene_landmarks", "超过 5 条"))
check("诊断：章节 landmarks 白名单已退役 warning",
      _has("warning", "chapters[0].landmarks", "退役"))
check("诊断：方案级 scene_landmarks 已退役 warning",
      _has("warning", "scene_landmarks", "退役"))
check("诊断：章节地址无法解析 error", _has("error", "chapters[0].address"))

_clean_config = {
    "chapters": [
        {"name": "一", "start": True, "place": "黑塔脚下",
         "scene_landmarks": _chapter_landmarks,
         "common_private_name": "HD"},
        {"name": "二", "ending": True, "address": _CITY,
         "exit_address": _CITY},
    ],
}
_clean_diags = validate_scenario_config(_clean_config)
_scene_diags = [
    d for d in _clean_diags
    if d.path == "scene_landmarks" or d.path.startswith("scene_landmarks[")
    or d.path.endswith((".address", ".exit_address", ".landmarks",
                        ".scene_landmarks", ".common_private_name"))
    or ".scene_landmarks" in d.path
]
check("干净的地标与私有名配置不报 error/warning",
      not [d for d in _scene_diags if d.level in ("error", "warning")],
      format_diagnostics(_scene_diags))

# ---------------- 4. 影响不到推进 ----------------
_graph_plain = build_graph({"chapters": _clean_config["chapters"]})
_graph_scene = build_graph(_clean_config)
check("改地标 / 改私有名不改变关系图的节点集合",
      set(_graph_plain.nodes) == set(_graph_scene.nodes),
      (set(_graph_plain.nodes) ^ set(_graph_scene.nodes)))
check("改地标 / 改私有名不改变关系图的边",
      {(e.src, e.dst, e.kind) for e in _graph_plain.edges}
      == {(e.src, e.dst, e.kind) for e in _graph_scene.edges})

# ---------------- 5. 会话访问器与提示词 ----------------
from dungeon.prompts import DungeonPromptBuilder  # noqa: E402
from dungeon.window.base import DungeonWindowBase  # noqa: E402


def _session(chapters, current):
    session = types.SimpleNamespace(
        chapters=chapters, current_chapter=current,
        merged_landmarks=_merged,
        _chapter_landmark_cache=None,
        body_parts={"身高": 480.0}, height=480.0,
        settings={"blocked_words": []},
    )
    session.chapter_landmarks = types.MethodType(
        DungeonWindowBase.chapter_landmarks, session)
    return session


_chapters = [
    {"name": "城里", "scene_landmarks": _chapter_landmarks},
    {"name": "别处", "common_private_name": "HD"},
    {"name": "空的"},
]
_session_mixed = _session(_chapters, "城里")
check("访问器：只配独特地标的章节拿到内联清单",
      _names(_session_mixed.chapter_landmarks()) == ["黑塔", "护城河"],
      _names(_session_mixed.chapter_landmarks()))
_session_mixed.current_chapter = "别处"
check("访问器：切章后池子随之更换",
      _names(_session_mixed.chapter_landmarks()) == ["高楼"],
      _names(_session_mixed.chapter_landmarks()))
_session_mixed.current_chapter = "空的"
check("访问器：空配置章节池子为空",
      _session_mixed.chapter_landmarks() == [])
check("访问器：按章节缓存同一份池子",
      _session_mixed.chapter_landmarks()
      is _session_mixed.chapter_landmarks())

session_city = _session(_chapters, "城里")
_text = DungeonPromptBuilder(session_city)._get_height_match()
check("提示词：换算句取自本章的池子", "黑塔" in _text, _text)
check("提示词：池子外的地标不会出现在换算句里",
      "高楼" not in _text and "路灯" not in _text, _text)
_empty_pool_session = _session([{"name": "空", "common_private_name": "没有"}], "空")
check("提示词：私有名匹配不到就不产生换算句",
      DungeonPromptBuilder(_empty_pool_session)._get_height_match() == "")

# ---------------- 6. 载荷里的完整地址 ----------------
from dungeon.window.launch_payload import (deserialize_launch,  # noqa: E402
                                           serialize_launch)
from core.resource_ref import Ref  # noqa: E402
from services.exploration.address_index import AddressIndex  # noqa: E402
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
        # 每条地标的风格归属按 styles 顺序给出，与目录里的 merged_refs 同构
        self.merged_refs = [Ref.scoped("landmarks", style, lm.name)
                            for lm, style in zip(landmarks, styles)]
        self.selected_styles = list(registers)
        self.landmark_repo = _FakeLandmarkRepo(registers)
        self.address_index = AddressIndex(self.landmark_repo)
        self.address_index.rebuild(zip(self.merged_refs, self.merged_landmarks))


_relative = [
    _lm("黑塔", 468, "-HD-9"),                      # 独特：相对后缀，父进程解析
    _cm("均值楼", 60),                              # 常见、无显式地址：按风格注册地址参与
    _lm("便携塔", 50, _CELL, address_mode="portable"),
    _lm("谜之塔", 300, "-ZZ-1"),                    # 风格未注册，解析不出
]
_catalog = _FakeCatalog(_relative, ["有注册", "有注册", "有注册", "无注册"],
                        {"有注册": _STYLE_REG})
_exported = AddressPlanner(_catalog).landmarks_for_export()
check("导出：相对后缀被组合成完整地址",
      _exported[0].address.startswith("1e3_1e2_1e1@")
      and _exported[0].address_mode == "exact", _exported[0])
check("导出：原始地标不被改写（父进程的池子仍是相对后缀）",
      _relative[0].address == "-HD-9", _relative[0])
check("导出：缺省 portable 的常见地标按风格注册地址参与（私有名随地址进载荷）",
      _exported[1].address.startswith("1e3_1e2_1e1@HD!")
      and _exported[1].address_mode == "exact", _exported[1])
check("导出：portable 地标不参与私有名过滤",
      _exported[2].address == "" and _exported[2].address_mode == "portable")
check("导出：解析失败的地标照样进载荷，只是不参与过滤",
      _exported[3].name == "谜之塔" and _exported[3].address == ""
      and _exported[3].size == 300.0)
check("导出后的地标能按私有名筛出来",
      _names(scene.ambient_landmarks(_exported, "HD")) == ["均值楼"],
      _names(scene.ambient_landmarks(_exported, "HD")))

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
check("载荷：还原后的地址仍能按私有名过滤",
      _names(scene.ambient_landmarks(_restored["merged_landmarks"], "HD"))
      == ["均值楼"])

# ---------------- 7. 展示：当前地点 ----------------
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
        prompted_presence_landmark="",
        option_choice=None, story_summary=None, replay_data=[], _detail_queries=None,
        pending_insertions=[], selected_quip_styles=[],
        settings={"blocked_words": []}, height=480.0,
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

# ---------------- 7b. 文本校准块 ----------------
from core.scale_reference import (destruction_calibration_line,  # noqa: E402
                                  destruction_level_title,
                                  intrusion_calibration_line,
                                  intrusion_level_title,
                                  intrusion_level_text)

check("校准：谓词行给上下界，4 级没有上界",
      "尚未到" in intrusion_calibration_line(3.2)
      and "尚未到" not in intrusion_calibration_line(4.2)
      and "尚未到" in destruction_calibration_line(1.8)
      and "尚未到" not in destruction_calibration_line(4.0))
check("校准：分级原文一字未改",
      intrusion_level_text(3.0).startswith("主动的城市互动："),
      intrusion_level_text(3.0))
check("校准：短标题取自分级定义的冒号前名称",
      intrusion_level_title(3.2) == "主动的城市互动"
      and destruction_level_title(1.8) == "可能破坏")

_calib_prompt = DungeonPromptBuilder(
    _prompt_session([{"name": "城里"}], "城里")).build_user_prompt(_text_type)
check("校准：用户提示词给出坐标数值行",
      "当前坐标：介入度 2.0（2 级：无意的日常动作）"
      "/ 破坏性 2.0（2 级：可能破坏）" in _calib_prompt,
      _calib_prompt[:800])
check("校准：谓词与上界随坐标出现",
      "介入度 2：" in _calib_prompt and "破坏性 2：" in _calib_prompt
      and "尚未到" in _calib_prompt)
check("校准：尺度行给出密度与后果上限规则",
      "本段只发生一件与此级别相称的事" in _calib_prompt
      and "不超过她脚掌覆盖的范围" in _calib_prompt)
check("校准：无换算句时尺度行只写身高",
      "尺度：她的身高 480.0 米；" in _calib_prompt, _calib_prompt[:800])
_calib_session = _prompt_session(_chapters, "城里")
_calib_session.body_parts = {"身高": 480.0}
_calib_city_prompt = DungeonPromptBuilder(_calib_session).build_user_prompt(_text_type)
check("校准：尺度行复用本章换算句",
      "尺度：她的身高 480.0 米，约等于黑塔" in _calib_city_prompt,
      _calib_city_prompt[:800])
check("校准：坐标行紧跟地点行之后",
      _place_prompt.index("当前坐标") > _place_prompt.index("当前地点"))

_sys_session = types.SimpleNamespace(
    coupling_level=None, protagonist_title="", name="测试", nick="",
    evolution_attrs=[], personality=types.SimpleNamespace(description=""),
    initial_prompt="", section_prompts={},
    body_parts={}, merged_landmarks=[], _chapter_landmark_cache=None,
    settings={"blocked_words": []},
)
_sys_session.chapter_landmarks = types.MethodType(
    DungeonWindowBase.chapter_landmarks, _sys_session)
_system_prompt = DungeonPromptBuilder(_sys_session).build_system_prompt()
check("校准：系统提示词澄清 direction 只表顺挫",
      "顺利（+1）还是受挫（-1）" in _system_prompt
      and "不表示事情更激烈还是更收敛" in _system_prompt
      and "内容的深浅以“当前坐标”一行为准" in _system_prompt,
      _system_prompt[:600])

# ---------------- 8. 结局传送 ----------------
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

# ---------------- 9. 同时在场触发（独特地标对比的第二条途径） ----------------
_TOWER_CHAPTER = {"name": "城里",
                  "scene_landmarks": [{"name": "黑塔", "size": 468}]}


def _presence_session(history, chapters=None, merged=None):
    session = types.SimpleNamespace(
        chapters=chapters if chapters is not None else [_TOWER_CHAPTER],
        current_chapter="城里",
        name="测试", nick="", coupling_level=None, protagonist_title="",
        prompted_parts=set(), keyword_match_given=set(), last_ai_text="",
        prompted_presence_landmark="",
        option_choice=None, story_summary=None, replay_data=[], _detail_queries=None,
        pending_insertions=[], selected_quip_styles=[],
        settings={"blocked_words": []},
        merged_landmarks=merged if merged is not None else [],
        _chapter_landmark_cache=None,
        body_parts={"身高": 480.0}, height=480.0,
        dungeon_state=DungeonState(intrusion=2.0, destruction=2.0),
        story_history=history,
    )
    session.chapter_landmarks = types.MethodType(
        DungeonWindowBase.chapter_landmarks, session)
    return session


def _prompt_of(session):
    return DungeonPromptBuilder(session).build_user_prompt(_text_type)


def _tower_compares(session):
    """提示词里黑塔对比句的出现次数：尺度行恒占 1 次，在场对比再加 1 次。"""
    return _prompt_of(session).count("约等于黑塔")


_same = _presence_session([{"text": "测试抬起头，望见了黑塔的塔尖。"}])
check("在场：同段出现她与独特地标 → 对比进提示词",
      _tower_compares(_same) == 2, _prompt_of(_same)[-400:])
_adjacent = _presence_session([{"text": "黑塔矗立在街区尽头。"},
                               {"text": "测试沿着街道走近，她抬头仰望。"}])
check("在场：分处相邻两段同样触发",
      _tower_compares(_adjacent) == 2, _prompt_of(_adjacent)[-400:])
_only_landmark = _presence_session([{"text": "黑塔矗立在街区尽头。"},
                                    {"text": "黑塔的影子铺满广场。"}])
check("在场：段落里没有她就不触发",
      _tower_compares(_only_landmark) == 1,
      _prompt_of(_only_landmark)[-400:])
_too_far = _presence_session([{"text": "黑塔。"}, {"text": "人群散去。"},
                              {"text": "测试继续往前走。"}])
check("在场：地标只在两段之前出现不触发",
      _tower_compares(_too_far) == 1, _prompt_of(_too_far)[-400:])
_repeat = _presence_session([{"text": "测试望见了黑塔。"}])
check("在场：首次触发给出对比", _tower_compares(_repeat) == 2)
check("在场：上一轮刚提示过同一地标时不重复", _tower_compares(_repeat) == 1)
_common_only = _presence_session(
    [{"text": "测试从高楼旁走过。"}],
    chapters=[{"name": "城里", "common_private_name": "HD"}],
    merged=[_cm("高楼", 60, _CHUNK_HD)])
check("在场：常见地标的名字出现在正文不触发在场对比",
      "约等于高楼" not in _prompt_of(_common_only))
_no_height = _presence_session([{"text": "测试望见了黑塔。"}])
_no_height.body_parts = {}
check("在场：没有身高数据时不产生对比行",
      "约等于黑塔" not in _prompt_of(_no_height))

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
