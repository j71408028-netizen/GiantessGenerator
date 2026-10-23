"""副本方案的进入准入：地址距离与身高区间。

方案可以在 ``config.json`` 里声明三项准入条件：

- ``entry_address``：方案所在地址（完整地址文本，见 ``core.address_model``）；
- ``entry_address_max_distance_m``：角色地址与方案地址的最大距离（米）；
  留空 / ``None`` = 不限，``0`` = 必须同址；
- ``entry_height_min_m`` / ``entry_height_max_m``：角色身高的允许区间（米）。
  留空 / ``None`` = 该侧不限。

判定口径：

- 角色**没有可用地址**（``position`` 为空或无法解析）时，地址条件自动跳过；
- 角色有地址时，两者必须在同一世界观。距离按 ``core.address_model.distance_interval_m``
  计算，并用区间上界保守判定（位置越粗，上界越大），超过上限即拒绝；
- 身高条件只看角色身高，落在区间外即拒绝；
- 方案地址本身写坏时按「未配置地址」处理（校验器会给出诊断），避免一个笔误
  把所有人都拦在门外。

本模块只做纯逻辑，供窗口入口、挂件入口与校验器共用；不依赖 UI / 服务层。
"""

from typing import Optional

from core.address_model import distance_interval_m, format_addr_verbose, parse_full
from core.logic import format_size

#: 方案配置里的准入字段（编辑器、持久化迁移与校验器共用同一份键名）
FIELD_ADDRESS = "entry_address"
FIELD_MAX_DISTANCE = "entry_address_max_distance_m"
FIELD_HEIGHT_MIN = "entry_height_min_m"
FIELD_HEIGHT_MAX = "entry_height_max_m"

ADMISSION_FIELDS = (FIELD_ADDRESS, FIELD_MAX_DISTANCE,
                    FIELD_HEIGHT_MIN, FIELD_HEIGHT_MAX)


def coerce_distance(value) -> Optional[float]:
    """把任意来源的距离值归一化为非负浮点数；留空/非法/负数返回 ``None``。"""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number < 0:  # NaN / 负数
        return None
    return number


def coerce_height(value) -> Optional[float]:
    """身高准入值的归一化（与距离同口径：留空/非法/负数 = 不限）。"""
    return coerce_distance(value)


def normalize_fields(config) -> dict:
    """提取并归一化四项准入字段（缺失时按不限处理）。"""
    config = config if isinstance(config, dict) else {}
    address = config.get(FIELD_ADDRESS)
    return {
        FIELD_ADDRESS: address.strip() if isinstance(address, str) else "",
        FIELD_MAX_DISTANCE: coerce_distance(config.get(FIELD_MAX_DISTANCE)),
        FIELD_HEIGHT_MIN: coerce_height(config.get(FIELD_HEIGHT_MIN)),
        FIELD_HEIGHT_MAX: coerce_height(config.get(FIELD_HEIGHT_MAX)),
    }


def _usable_address(text) -> str:
    """只接受能解析的地址；写坏的地址按未配置处理。"""
    text = (text or "").strip() if isinstance(text, str) else ""
    return text if text and parse_full(text) is not None else ""


def check_entry_requirements(config, *, position: str = "",
                             height=None) -> str:
    """检查角色能否进入方案，返回给玩家看的拒绝原因；可以进入返回空串。

    ``position``：角色当前完整地址（``CharacterSnapshot.position``）；
    ``height``：角色身高（米）。挑战模式没有角色时由调用方传窗口身高。
    """
    rules = normalize_fields(config)

    address = _usable_address(rules[FIELD_ADDRESS])
    max_distance = rules[FIELD_MAX_DISTANCE]
    position = (position or "").strip() if isinstance(position, str) else ""
    if address and max_distance is not None and position:
        if parse_full(position) is not None:
            interval = distance_interval_m(position, address)
            if interval is None:
                return (f"该副本要求角色位于地址「{format_addr_verbose(address)}」附近，"
                        f"但角色当前地址「{format_addr_verbose(position)}」"
                        f"与方案地址不在同一世界观，无法进入。")
            nearest, farthest = interval
            if farthest > max_distance:
                if nearest == farthest:
                    distance_text = f"角色当前距离为 {format_size(nearest)}"
                else:
                    distance_text = (f"角色当前距离在 {format_size(nearest)} 到 "
                                     f"{format_size(farthest)} 之间（位置精度不足）")
                return (f"该副本要求角色与方案地址「{format_addr_verbose(address)}」"
                        f"的距离不超过 {format_size(max_distance)}，"
                        f"{distance_text}，无法进入。")

    if height is not None:
        try:
            height_value = float(height)
        except (TypeError, ValueError):
            height_value = None
        if height_value is not None and height_value > 0:
            minimum = rules[FIELD_HEIGHT_MIN]
            maximum = rules[FIELD_HEIGHT_MAX]
            if minimum is not None and height_value < minimum:
                return (f"该副本要求角色身高不低于 {format_size(minimum)}，"
                        f"角色当前身高为 {format_size(height_value)}，无法进入。")
            if maximum is not None and height_value > maximum:
                return (f"该副本要求角色身高不高于 {format_size(maximum)}，"
                        f"角色当前身高为 {format_size(height_value)}，无法进入。")

    return ""


__all__ = [
    "ADMISSION_FIELDS", "FIELD_ADDRESS", "FIELD_MAX_DISTANCE",
    "FIELD_HEIGHT_MIN", "FIELD_HEIGHT_MAX",
    "coerce_distance", "coerce_height", "normalize_fields",
    "check_entry_requirements",
]
