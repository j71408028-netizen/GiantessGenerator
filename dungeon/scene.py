"""副本内的地点与参照物：章节地址的声明与候选池组装。

副本里没有移动与寻路，章节发生在哪儿由作者写死，所以地址只承担声明职责：
展示地点、筛出「这一带」的全局地标做尺寸换算、结局传送（写回位置）。

候选池规则（见 ``docs/designs/dungeon_scene.md`` §4）：

- 章节没写地址、也没取清单 → ``merged_landmarks`` 全量（改动前的行为）；
- 写了地址 → 只保留 ``relate(章节地址, 地标地址).pairable()`` 的全局地标；
- 取了清单 → 清单优先，写了地址时与地址相配的全局地标取并集（同名以清单为准）。

本模块是纯逻辑，只依赖 ``core`` 与同层 ``dungeon.schema``，不碰 UI / services。
"""

from core.address_model import format_addr_compact, parse_full, relate
from core.models import Landmark

from . import schema

__all__ = [
    "build_landmarks", "chapter_place", "chapter_address", "exit_address",
    "ambient_landmarks", "landmarks_for_chapter", "place_label", "landing_position",
]


def _coerce_landmark(item: dict):
    """把清单里的一条归一成 ``Landmark``；写坏（校验器会报 error）时返回 None。"""
    name = item.get("name") or ""
    if not name:
        return None
    size = item.get("size")
    if isinstance(size, bool) or not isinstance(size, (int, float)) or size <= 0:
        return None
    dimension = item.get("dimension") or schema.DEFAULT_SCENE_LANDMARK_DIMENSION
    if dimension not in schema.SCENE_LANDMARK_DIMENSIONS:
        return None
    frequency = item.get("frequency") or schema.DEFAULT_SCENE_LANDMARK_FREQUENCY
    if frequency not in schema.SCENE_LANDMARK_FREQUENCIES:
        frequency = schema.DEFAULT_SCENE_LANDMARK_FREQUENCY
    horizontal_type = None
    if dimension == "horizontal":
        horizontal_type = (item.get("horizontal_type")
                           or schema.DEFAULT_SCENE_LANDMARK_HORIZONTAL_TYPE)
        if horizontal_type not in schema.SCENE_LANDMARK_HORIZONTAL_TYPES:
            horizontal_type = schema.DEFAULT_SCENE_LANDMARK_HORIZONTAL_TYPE
    return Landmark(name=name, size=float(size), dimension=dimension,
                    frequency=frequency, horizontal_type=horizontal_type)


def build_landmarks(config) -> list:
    """方案自带的场景地标清单 → ``Landmark`` 列表（重名取第一条）。"""
    raw = config.get("scene_landmarks") if isinstance(config, dict) else None
    landmarks = []
    seen = set()
    for item in schema.normalize_scene_landmarks(raw):
        landmark = _coerce_landmark(item)
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


def ambient_landmarks(merged, address) -> list:
    """从全局地标里筛出与 ``address`` 相配的那一批。

    判据只有地址自身的空间关系（同址 / 包含 / 被包含，且私有名互相满足），
    不做米数阈值；没写地址或地址解析不了时不返回任何候选。
    """
    address = (address or "").strip()
    if not address:
        return []
    matched = []
    for landmark in merged or []:
        landmark_address = str(getattr(landmark, "address", "") or "").strip()
        if not landmark_address:
            continue
        # portable 明确声明不参与地址规则，写了地址也不例外
        if str(getattr(landmark, "address_mode", "") or "").strip().lower() == "portable":
            continue
        if relate(address, landmark_address).pairable():
            matched.append(landmark)
    return matched


def _chapter_selection(scene: list, chapter) -> list:
    """按章节 ``landmarks`` 白名单取清单条目；``None`` = 整份清单，``[]`` = 不用。"""
    names = (chapter or {}).get("landmarks")
    if names is None:
        return list(scene)
    picked = []
    by_name = {landmark.name: landmark for landmark in scene}
    for name in names or []:
        landmark = by_name.get(str(name).strip())
        if landmark is not None and landmark not in picked:
            picked.append(landmark)
    return picked


def landmarks_for_chapter(config, chapter, merged) -> list:
    """本章的尺寸换算候选池：清单 ∪ 与章节地址相配的全局地标。

    四种组合（见 ``docs/designs/dungeon_scene.md`` §4）：

    - 无地址、无清单 → ``merged`` 全量（改动前的行为）；
    - 无地址、有清单 → 只有清单；
    - 有地址、无清单 → 只有地址相配的全局地标；
    - 有地址、有清单 → 并集，同名以清单为准。

    章节 ``landmarks`` 为 ``None`` 时取整份清单，为 ``[]`` 时本章不用清单——
    后者**不是**"回落到全量"，限定范围才是作者的意图。
    """
    scene = build_landmarks(config)
    address = chapter_address(chapter)
    if not scene and not address:
        return list(merged or [])
    selected = _chapter_selection(scene, chapter) if scene else []
    if not address:
        return selected
    names = {landmark.name for landmark in selected}
    selected.extend(landmark for landmark in ambient_landmarks(merged, address)
                    if landmark.name not in names)
    return selected
