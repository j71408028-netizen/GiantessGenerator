"""副本内的地点与参照物：章节地点声明与候选池组装。

副本里没有移动与寻路，章节发生在哪儿由作者写死，所以地址只承担声明职责：
展示地点、结局传送（写回位置）。尺寸换算的候选池（见
``docs/designs/dungeon_scene.md`` §4）两类地标两条路：

- 独特地标由章节内联配置（``scene_landmarks``，编辑器像敏感效果一样逐行添加）；
  「同时在场」的对比触发见 ``dungeon/prompts.py``；
- 常见地标走私有名过滤：章节 ``common_private_name`` 非空时，全局池里完整地址
  带同一私有名的常见地标进池；留空 = 不使用常见地标。

本模块是纯逻辑，只依赖 ``core`` 与同层 ``dungeon.schema``，不碰 UI / services。
"""

from core.address_model import format_addr_compact, parse_full
from core.models import Landmark

from . import schema

__all__ = [
    "chapter_place", "chapter_address", "chapter_scene_landmarks",
    "common_private_name", "exit_address", "ambient_landmarks",
    "landmarks_for_chapter", "place_label", "landing_position",
]


def _coerce_unique_landmark(item: dict):
    """把一条独特地标归一成 ``Landmark``；写坏（校验器会报 error）时返回 None。"""
    name = item.get("name") or ""
    if not name:
        return None
    size = item.get("size")
    if isinstance(size, bool) or not isinstance(size, (int, float)) or size <= 0:
        return None
    dimension = item.get("dimension") or schema.DEFAULT_SCENE_LANDMARK_DIMENSION
    if dimension not in schema.SCENE_LANDMARK_DIMENSIONS:
        return None
    horizontal_type = None
    if dimension == "horizontal":
        horizontal_type = (item.get("horizontal_type")
                           or schema.DEFAULT_SCENE_LANDMARK_HORIZONTAL_TYPE)
        if horizontal_type not in schema.SCENE_LANDMARK_HORIZONTAL_TYPES:
            horizontal_type = schema.DEFAULT_SCENE_LANDMARK_HORIZONTAL_TYPE
    return Landmark(name=name, size=float(size), dimension=dimension,
                    frequency="unique", horizontal_type=horizontal_type)


def chapter_scene_landmarks(chapter) -> list:
    """章节自带的独特地标（``scene_landmarks``）→ ``Landmark`` 列表（重名取第一条）。"""
    raw = (chapter or {}).get("scene_landmarks")
    landmarks = []
    seen = set()
    for item in schema.normalize_scene_landmarks(raw):
        landmark = _coerce_unique_landmark(item)
        if landmark is None or landmark.name in seen:
            continue
        seen.add(landmark.name)
        landmarks.append(landmark)
    return landmarks


def chapter_place(chapter) -> str:
    """章节的展示名；空串表示不展示。"""
    return str((chapter or {}).get("place") or "").strip()


def chapter_address(chapter) -> str:
    """章节的取景地址（完整地址文本）；空串表示没写。"""
    return str((chapter or {}).get("address") or "").strip()


def common_private_name(chapter) -> str:
    """常见地标的过滤私有名；空串 = 不使用常见地标。"""
    return str((chapter or {}).get("common_private_name") or "").strip()


def exit_address(chapter) -> str:
    """结局的传送目的地（完整地址文本）；空串表示位置不变。"""
    return str((chapter or {}).get("exit_address") or "").strip()


def place_label(chapter) -> str:
    """章节的展示地点：``place`` 优先，其次退回紧凑地址描述；都没有返回空串。

    窗口与提示词读同一个值，所以"作者起的名字"总压过"地址原文"——写了
    ``place`` 的章节不会把带精度提示的地址串漏到界面上。
    """
    place = chapter_place(chapter)
    if place:
        return place
    address = chapter_address(chapter)
    return format_addr_compact(address) if address else ""


def landing_position(text) -> str:
    """结局传送目的地：能解析的完整地址原样返回；空 / 解不出返回空串（位置不变）。"""
    text = str(text or "").strip()
    if not text:
        return ""
    return text if parse_full(text) is not None else ""


def ambient_landmarks(merged, private_name) -> list:
    """私有名与章节声明一致的常见地标。

    数据通道是私有名，不是地址相配：地标完整地址的约束段（``@名字!``）与
    章节声明的 ``common_private_name`` 相同才进池。没写私有名时不返回任何
    候选——空就是"不使用常见地标"。独特地标不参与这一路（它们由章节内联
    配置）；显式 ``portable`` 的地标不参与。
    """
    private_name = (private_name or "").strip()
    if not private_name:
        return []
    matched = []
    for landmark in merged or []:
        if str(getattr(landmark, "frequency", "") or "") != "common":
            continue
        landmark_address = str(getattr(landmark, "address", "") or "").strip()
        if not landmark_address:
            continue
        if str(getattr(landmark, "address_mode", "") or "").strip().lower() == "portable":
            continue
        addr = parse_full(landmark_address)
        if addr is not None and addr.chunk == private_name:
            matched.append(landmark)
    return matched


def landmarks_for_chapter(chapter, merged) -> list:
    """本章的尺寸换算候选池：章节独特地标 ∪ 私有名过滤的常见地标。

    - 章节没配独特地标、也没填私有名 → 空池（不产生换算句，也不回落全量）；
    - 只配独特地标 → 只有内联清单；
    - 只填私有名 → 只有私有名一致的常见地标；
    - 两者都配 → 并集，同名以章节独特地标为准。

    旧版地址相配与方案级清单已退役：章节 ``landmarks`` 白名单与方案级
    ``scene_landmarks`` 被忽略（校验器报 warning）。
    """
    uniques = chapter_scene_landmarks(chapter)
    private_name = common_private_name(chapter)
    if not private_name:
        return uniques
    names = {landmark.name for landmark in uniques}
    return uniques + [landmark for landmark in ambient_landmarks(merged, private_name)
                      if landmark.name not in names]
