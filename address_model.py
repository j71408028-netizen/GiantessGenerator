"""地标风格 / 独特地标 / 描述风格的注册地址系统。

本模块是“注册地址系统”的纯逻辑部分（不依赖 UI / 数据仓库），负责：

- 解析并规范化地址字符串；
- 组合「风格注册地址」与「地标注册地址」成完整地标地址；
- 计算两个地址是否“同一地址 / 互相包含”（距离为 0）；
- 在同一世界观内按层级边长估算地址间距离（米），供可达性判定使用。

地址格式（四段，用“-”连接）：:
    <世界观>-<一级地域>-<二级地域>-<三级地域>

- 世界观：编号或字母组成的记号，世界观不同视为不可互相到达。
- 一级地域 / 二级地域 / 三级地域：十进制数字串。每一段数字的**末位**兼任
  缩放指数 e 与 id 的一部分：该段除去末位后的数字是本级“单元编号”，
  2^e 是该级相对相邻更小一级的放大倍数。
- 三级地域的最后一位还决定基础规模：三级地域基础边长 = 10 × 2^e（米）。

距离规则
--------
- 两个地址相同、或一个完全包含另一个 → 距离为 0。
- 其余情况（同一世界观内）：以“最深共同前缀之后、编号第一次出现不同的那一级”
  的边长作为两地址的距离（同级不同编号按本级边长处理）。
  例如 a-b-c 与 a-d / a-d-e 在世界观 a 之后的一级地域不同（b ≠ d），距离按
  一级地域边长计算。
- 世界观不同 → 距离未知（视为不可达）。

风格可以注册最上面的若干级；其独特地标注册余下的若干级。完整地标地址
= 风格注册地址 + 地标剩余级（由 :func:`resolve_full_address` 组合）。
"""

import re
from typing import List, Optional, Tuple

# 各级名称（世界级 index 0）
LEVEL_NAMES = ("world", "large", "medium", "small")
LEVEL_LABELS = {"world": "世界观", "large": "一级地域", "medium": "二级地域", "small": "三级地域"}
REGION_LABELS = ("一级地域", "二级地域", "三级地域")

# 三级地域基础规模：10 × 2^末位（米）
SMALL_BASE_MULTIPLIER = 10.0

# 世界级最多三级地区，加上世界观共四段
MAX_PARTS = 4

# 兼容世界名：数字或字母（允许下划线），不包含分隔符与空白
_WORLD_RE = re.compile(r"^[A-Za-z0-9_]{1,48}$")
_REGION_RE = re.compile(r"^\d{1,12}$")


def split_address(text: str) -> List[str]:
    """把地址字符串拆成段，剔除空段。"""
    if not text:
        return []
    return [p.strip() for p in str(text).strip().strip("-").split("-") if p.strip()]


def parse_parts(text: str) -> Optional[Tuple[str, str, str, str]]:
    """把地址文本解析为 (world, large, medium, small) 元组。

    不合法返回 None。允许少于四段（越靠后越粗）。
    """
    parts = split_address(text)
    if not parts or len(parts) > MAX_PARTS:
        return None
    world = parts[0]
    if not _WORLD_RE.match(world):
        return None
    result: List[Optional[str]] = [world, None, None, None]
    for i in range(1, len(parts)):
        seg = parts[i]
        if not _REGION_RE.match(seg):
            return None
        result[i] = seg
    return (result[0], result[1], result[2], result[3])


def world_of(text: str) -> str:
    """返回地址文本的世界观段；空 / 非法返回 ''。"""
    parts = split_address(text)
    return parts[0] if parts else ""


def depth_of(text: str) -> int:
    """地址的有效级数（含世界观 1~4）。空返回 0。"""
    return len(split_address(text))


def segment_exponent(seg: Optional[str]) -> Optional[int]:
    """返回一个地区数字段的缩放指数（末位数字）。段为空返回 None。"""
    if not seg:
        return None
    return int(seg[-1])


def segment_index(seg: Optional[str]) -> int:
    """返回地区数字段的“单元编号”（去掉末位指数后的整数值）。"""
    if not seg:
        return 0
    return int(seg[:-1]) if len(seg) > 1 else 0


def is_region_equal(a: Optional[str], b: Optional[str]) -> bool:
    """两个地区段是否完全相同（字符串比较）。"""
    if not a or not b:
        return False
    return a == b


def level_width_m(address_parts: Tuple[str, str, str, str]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """按地址各级末位给出 (一级地域, 二级地域, 三级地域) 边长（米）。

    任一必需指数缺失（如未注册三级地域导致基础规模缺失）时，相应上级
    边长按缺失段的指数为 0 推导（基础规模取 10 米）。返回值与段一一对应，
    若世界观本身不参与缩放。
    """
    world, large, medium, small = address_parts
    e_small = segment_exponent(small)
    e_med = segment_exponent(medium)
    e_large = segment_exponent(large)
    base = SMALL_BASE_MULTIPLIER * (2 ** (e_small if e_small is not None else 0))
    width_small = base
    width_medium = width_small * (2 ** (e_med if e_med is not None else 0))
    width_large = width_medium * (2 ** (e_large if e_large is not None else 0))
    return width_large, width_medium, width_small


def compose(world: str, large: str = "", medium: str = "", small: str = "") -> str:
    """按段拼成地址文本（自动跳过空的地区段，但世界观必填）。"""
    if not world:
        return ""
    parts = [world]
    for seg in (large, medium, small):
        seg = (seg or "").strip()
        if seg:
            parts.append(seg)
    return "-".join(parts)


def validate_segment(seg: str, level: str) -> Optional[str]:
    """校验单个地区段输入。合法返回 None，否则返回中文错误提示。"""
    seg = (seg or "").strip()
    if not seg:
        return None
    if not _REGION_RE.match(seg):
        return f"{LEVEL_LABELS[level]}应为纯数字（末位为 2 的指数，其余为编号）"
    return None


def validate_address_text(text: str) -> Optional[str]:
    """校验完整地址文本（可为风格注册 / 地标注册的段组合）。"""
    parts = split_address(text)
    if not parts:
        return None  # 空地址合法（表示未注册 / 到处可用）
    if len(parts) > MAX_PARTS:
        return "地址最多 4 段（世界观-一级地域-二级地域-三级地域）"
    if not _WORLD_RE.match(parts[0]):
        return "世界观段应为数字或字母"
    for seg in parts[1:]:
        if not _REGION_RE.match(seg):
            return "大/中/三级地域段应为纯数字，末位为缩放指数"
    return None


def resolve_full_address(style_reg: str, landmark_addr: str) -> str:
    """组合风格注册地址与地标地址为完整地标地址。

    - 风格未注册（空）：地标地址必须自带世界观，作为完整地址原样使用。
    - 风格已注册：地标地址只允许补“剩下的级”，不允许重复世界观。
    - 双方都为空：返回 ''（未注册地标，视为到处可用）。
    """
    style_reg = (style_reg or "").strip()
    landmark_addr = (landmark_addr or "").strip()
    if not style_reg and not landmark_addr:
        return ""
    s_parts = split_address(style_reg)
    l_parts = split_address(landmark_addr)
    if not s_parts:
        # 风格未注册世界观：地标地址应自带世界观。
        if not l_parts:
            return ""
        return "-".join(l_parts)
    if not l_parts:
        return "-".join(s_parts)
    # 风格已注册：地标只补“剩下的级”。若地标误存了完整地址（以风格前缀开头），
    # 去掉与风格重复的前缀段，只追加多出来的部分。
    common = 0
    if l_parts[0] == s_parts[0]:
        n = min(len(s_parts), len(l_parts))
        while common < n and s_parts[common] == l_parts[common]:
            common += 1
    l_rest = l_parts[common:]
    if not l_rest:
        return "-".join(s_parts)
    return "-".join(s_parts + l_rest)


def _same_world(a_parts, b_parts) -> bool:
    return bool(a_parts[0]) and a_parts[0] == b_parts[0]


def distance_m(a_text: str, b_text: str) -> Optional[float]:
    """返回两地址的距离（米）。

    同一 / 互相包含 → 0.0；世界观不同 → None（不可达）。
    其余情况：取“共同前缀后编号首次不同”那一级的边长作为距离
    （同级不同编号一律按本级边长处理）。边长基准取 a 自身各级指数
    （一般让更细的一方在前，即角色位置）。
    """
    a_parts = parse_parts(a_text)
    b_parts = parse_parts(b_text)
    if a_parts is None or b_parts is None:
        return None
    if not _same_world(a_parts, b_parts):
        return None

    widths = level_width_m(a_parts)  # (大,中,小) 边长按 a 的各级指数推导

    for level in (1, 2, 3):
        sa, sb = a_parts[level], b_parts[level]
        if sa and sb:
            if sa == sb:
                continue
            return widths[level - 1]  # 同级不同编号 → 本级边长（一级）
        if not sa and not sb:
            continue
        # 一方不再注册 → 粗地址包含细地址 → 距离 0
        return 0.0
    return 0.0


def touches(a_text: str, b_text: str) -> bool:
    """两个完整地址距离是否为 0（同一地址或互相包含）。"""
    a_parts = parse_parts(a_text)
    b_parts = parse_parts(b_text)
    if a_parts is None or b_parts is None:
        return False
    if not _same_world(a_parts, b_parts):
        return False
    # 逐级比较，粗地址覆盖细地址即视为包含
    for level in (1, 2, 3):
        a_seg, b_seg = a_parts[level], b_parts[level]
        if not a_seg and not b_seg:
            continue
        if not a_seg or not b_seg:
            return True  # 一方不再注册 → 另一方被包含
        if a_seg != b_seg:
            return False
    return True


def cell_width_m(full_addr_text: str) -> float:
    """返回完整地址所在“最小单元”的边长（米），供可达概率估算。"""
    parts = parse_parts(full_addr_text)
    if parts is None:
        return SMALL_BASE_MULTIPLIER
    widths = level_width_m(parts)
    # 有清晰注册的三级地域 → 用它；否则用能推导的最细一级
    if parts[3]:
        return widths[2]
    if parts[2]:
        return widths[1]
    return widths[0]


def reachable(position_text: str, landmark_full_text: str, height: float) -> bool:
    """地标是否在角色 10 倍身高可达范围内。地标无地址视为到处可用。"""
    if not landmark_full_text:
        return True
    if not position_text:
        # 无角色位置时不约束（首次由第一个地标锚定）
        return True
    d = distance_m(position_text, landmark_full_text)
    if d is None:
        return False
    return d < 10.0 * height


def format_addr_verbose(text: str) -> str:
    """把地址文本格式化为可读的中文描述（供 UI / 报告展示）。"""
    parts = parse_parts(text)
    if parts is None:
        return (text or "").strip() or "（无地址）"
    segs = []
    for level, label in ((0, "世界观"), (1, "一级地域"), (2, "二级地域"), (3, "三级地域")):
        seg = parts[level]
        if not seg:
            break
        if level == 0:
            segs.append(f"{label} {seg}")
        else:
            segs.append(f"{label} {seg}")
    return " / ".join(segs) if segs else "（无地址）"


def describe_scale(full_addr_text: str) -> str:
    """把完整地址的最小单元边长格式化成可读规模。"""
    w = cell_width_m(full_addr_text)
    if w >= 10000:
        return f"约{w / 1000:.1f}千米"
    return f"约{w:.0f}米"
