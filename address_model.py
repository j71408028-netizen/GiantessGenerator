"""地标风格 / 独特地标 / 描述风格的注册地址系统。

地址格式（新分离写法）：:

    <绝对规模>@[<私有名或*>!]<世界观>-<一级地域>-<二级地域>-<三级地域>

- 绝对规模：以 ``_`` 分隔的 3 个数字，按 一级/二级/三级 给出边长（米），
  例如 ``1e5_5e3_1e2`` = 一级地域 100 km、二级地域 5 km、三级地域 100 m。
  绝对规模**必须提供**（除非地址仅包含世界观，不包含任何地域段）。
- ``@`` 之后、``!`` 之前是“约束段”（可有可无；空或 ``*`` 表示不指定私有名）：
  - 完全没有 ``@...!`` 段 → 不可与任何带约束的地址匹配（仅用于无约束地址）；
  - ``@!`` / ``@*!`` → 只能与**相同规模组**的地址匹配；
  - ``@abc!`` → 只能与规模组相同、且私有名恰为 ``abc`` 的地址匹配。
  注：``*`` 与空等价（都只要求规模组相同）。
- ``-`` 连接世界观与地域 id。地域 id 为任意字母/数字/下划线，不再兼任缩放指数。
- 世界观为字母/数字/下划线，长度 1~48。

距离规则
--------
- 两地址相同、或一个完全包含另一个 → 距离 0。
- 其余情况（同一世界观）：以"共同前缀之后编号首次不同"的那一级作为分歧级，
  距离 = 双方在该级边长的**平均值**（允许不同分支申领不同规模；同一分支内
  的规模一致性由注册表的地址树保证）。
- 世界观不同 → 距离未知（不可达）。

配对规则
--------
- 地域序列同址 / 互相包含即可配对（不再要求规模三元组相等）；
- 私有名约束仍然有效：一方声明了私有名时，另一方必须声明相同私有名。
- 私有名取"最先声明的值"：地址树中子地址的私有名覆写无效（想在其他私有名
  的语境下扩展，应申领平行地址）；组合时风格（较粗一方）的约束优先。

风格注册地址通常包含绝对规模和前几级地域；地标注册地址仅含剩余级（可省略规模，
继承风格的规模）。组合时规模的优先级为"更细的一方胜出"（地标的规模覆写风格的
规模），约束的优先级为"先声明的一方胜出"。
"""

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

LEVEL_NAMES = ("world", "large", "medium", "small")
LEVEL_LABELS = {"world": "世界观", "large": "一级地域", "medium": "二级地域", "small": "三级地域"}
REGION_LABELS = ("一级地域", "二级地域", "三级地域")

MAX_PARTS = 4

_WORLD_RE = re.compile(r"^[A-Za-z0-9_]{1,48}$")
_REGION_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")          # 地域id，字母数字下划线
_CHUNK_RE = re.compile(r"^[A-Za-z0-9_]{0,64}$")          # 私有名允许空
_SCALE_ITEM_RE = re.compile(r"^\d+(\.\d+)?([eE][+-]?\d+)?$")


@dataclass
class Addr:
    """解析后的完整地址。"""
    world: str
    regions: Tuple[str, str, str]   # (一级, 二级, 三级)，缺省为 ""
    scale: Tuple[float, float, float]   # (一级,二级,三级) 边长（米），必须存在（除非纯世界观）
    scale_raw: str                  # 原始规模字符串，如 "1e5_5e3_1e2"
    chunk: str                      # 约束段私有名，"" 或 "*" 表示不指定；其他为具体名
    has_mark: bool                  # 是否带有 @...! 段（带则要求规模组/私有名匹配）

    @property
    def depth(self) -> int:
        if not self.world:
            return 0
        return 1 + sum(1 for r in self.regions if r)

    @property
    def has_regions(self) -> bool:
        return any(self.regions)

    def ids_text(self) -> str:
        return "-".join(p for p in (self.world,) + self.regions if p)

    def prefix_text(self) -> str:
        """重建地址中的“规模/约束段”前缀。"""
        if not self.has_mark and not self.scale_raw:
            return ""
        return f"{self.scale_raw}@{self.chunk}!"


def _parse_scale(text: str) -> Optional[Tuple[float, float, float]]:
    if not text:
        return None
    items = [t.strip() for t in text.split("_") if t.strip()]
    if len(items) != 3:
        return None
    values = []
    for it in items:
        if not _SCALE_ITEM_RE.match(it):
            return None
        try:
            values.append(float(it))
        except ValueError:
            return None
    return (values[0], values[1], values[2])


def parse_full(text: str) -> Optional[Addr]:
    """解析地址；非法/空返回 None。

    强制要求：
    - 纯世界观（如 "world"）允许没有 @...! 段，且无地域；
    - 包含地域（至少一级）必须提供绝对规模（即必须有 @ 且 scale_raw 非空）。
    """
    s = (text or "").strip()
    if not s:
        return None

    scale_raw = ""
    chunk = ""
    has_mark = False
    at = s.find("@")
    if at < 0:
        # 没有 @，必须是纯世界观（无 '-'）
        if "-" in s:
            return None   # 有地域但缺少规模，非法
        # 纯世界观
        if not _WORLD_RE.match(s):
            return None
        return Addr(world=s, regions=("", "", ""), scale=(0.0, 0.0, 0.0),
                    scale_raw="", chunk="", has_mark=False)
    else:
        # 有 @，解析前缀和剩余
        has_mark = True
        pre = s[:at].strip()
        rest = s[at+1:].strip()
        if not pre:
            return None   # 必须有绝对规模
        scale_raw = pre
        scale = _parse_scale(scale_raw)
        if scale is None:
            return None

        # 分离 ! 后与 ! 前
        bang = rest.find("!")
        if bang < 0:
            chunk = ""
            ids_text = rest
        else:
            chunk = rest[:bang]
            ids_text = rest[bang+1:]

        # 校验私有名
        if chunk not in ("", "*") and not _CHUNK_RE.match(chunk):
            return None

        # 解析地域段
        raw_parts = [p.strip() for p in ids_text.split("-") if p.strip()]
        if not raw_parts or len(raw_parts) > MAX_PARTS:
            return None
        if not _WORLD_RE.match(raw_parts[0]):
            return None
        world = raw_parts[0]
        regions = ["", "", ""]
        for i in range(1, len(raw_parts)):
            seg = raw_parts[i]
            if not _REGION_RE.match(seg):
                return None
            regions[i-1] = seg

        return Addr(world=world,
                    regions=(regions[0], regions[1], regions[2]),
                    scale=scale,
                    scale_raw=scale_raw,
                    chunk=chunk,
                    has_mark=has_mark)


def split_address(text: str) -> List[str]:
    """返回地址的地域 id 段列表（不含前缀）。"""
    addr = parse_full(text)
    if addr is None:
        return []
    return [p for p in (addr.world,) + addr.regions if p]


def parse_parts(text: str) -> Optional[Tuple[str, str, str, str]]:
    """返回 (world, large, medium, small) 元组（仅地域 id）。"""
    addr = parse_full(text)
    if addr is None:
        return None
    return (addr.world, addr.regions[0], addr.regions[1], addr.regions[2])


def world_of(text: str) -> str:
    addr = parse_full(text)
    return addr.world if addr else ""


def depth_of(text: str) -> int:
    addr = parse_full(text)
    return addr.depth if addr else 0


def addr_widths(addr: Addr) -> Tuple[float, float, float]:
    """返回 (一级,二级,三级) 边长（米）。要求地址必须已有 scale。"""
    if addr.scale is None:
        raise ValueError(f"地址 {addr.ids_text()} 缺少绝对规模")
    return addr.scale


def cell_width_m(full_addr_text: str) -> float:
    """返回完整地址所在“最小单元”的边长（米）。"""
    addr = parse_full(full_addr_text)
    if addr is None:
        return 0.0
    if addr.regions[2]:
        return addr_widths(addr)[2]
    if addr.regions[1]:
        return addr_widths(addr)[1]
    if addr.regions[0]:
        return addr_widths(addr)[0]
    return 0.0   # 纯世界观，无单元


def _chunk_requirement_ok(this: Addr, other: Addr) -> bool:
    """检查 this 声明的私有名约束对 other 是否满足。

    规模一致性不再作为配对条件（由注册表地址树保证），私有名仍有效。
    """
    if not this.has_mark:
        return True
    if this.chunk not in ("", "*"):
        return other.chunk == this.chunk
    return True


def chunk_compatible(a_text: str, b_text: str) -> bool:
    """检查双方声明的私有名约束是否互相满足。"""
    a = parse_full(a_text)
    b = parse_full(b_text)
    if a is None or b is None:
        return False
    return _chunk_requirement_ok(a, b) and _chunk_requirement_ok(b, a)


def scale_compatible(a_text: str, b_text: str) -> bool:
    """兼容旧名：现为私有名约束检查（规模一致性由地址树保证）。"""
    return chunk_compatible(a_text, b_text)


def touches(a_text: str, b_text: str) -> bool:
    """两个地址在地域 id 上是否同一或互相包含（不校验约束）。"""
    a = parse_full(a_text)
    b = parse_full(b_text)
    if a is None or b is None:
        return False
    if not a.world or a.world != b.world:
        return False
    for level in range(3):
        sa, sb = a.regions[level], b.regions[level]
        if sa and sb:
            if sa == sb:
                continue
            return False
        if not sa and not sb:
            continue
        return True   # 一方更粗，包含
    return True


def can_pair(a_text: str, b_text: str) -> bool:
    """两个地址能否配对：地域序列同址 / 互相包含，且私有名约束互相满足。"""
    if not touches(a_text, b_text):
        return False
    return chunk_compatible(a_text, b_text)


def distance_m(a_text: str, b_text: str) -> Optional[float]:
    """返回两地址的距离（米）；若不可达返回 None。

    地域序列同址 / 包含 → 0；首个分歧级的距离 = 双方该级边长的平均值
    （允许不同分支申领不同规模）。
    """
    a = parse_full(a_text)
    b = parse_full(b_text)
    if a is None or b is None:
        return None
    if not a.world or a.world != b.world:
        return None

    for level in range(3):
        sa, sb = a.regions[level], b.regions[level]
        if sa and sb:
            if sa == sb:
                continue
            wa = a.scale[level] if a.scale else 0.0
            wb = b.scale[level] if b.scale else 0.0
            return (wa + wb) / 2.0
        if not sa and not sb:
            continue
        return 0.0   # 包含
    return 0.0


def reachable(position_text: str, landmark_full_text: str, height: float) -> bool:
    """地标是否在角色 10 倍身高可达范围内。"""
    if not landmark_full_text:
        return True
    if not position_text:
        return True
    d = distance_m(position_text, landmark_full_text)
    if d is None:
        return False
    return d < 10.0 * height


# ==================== 组合与重建 ====================

def compose(world: str, large: str = "", medium: str = "", small: str = "") -> str:
    """仅拼接地域 id 段（不带前缀）。"""
    if not world:
        return ""
    parts = [world]
    for seg in (large, medium, small):
        seg = (seg or "").strip()
        if seg:
            parts.append(seg)
    return "-".join(parts)


def _join_addr_text(addr: Addr) -> str:
    prefix = addr.prefix_text()
    ids = addr.ids_text()
    return f"{prefix}{ids}" if prefix else ids


def resolve_full_address(style_reg: str, landmark_addr: str) -> str:
    """组合风格注册地址与地标地址为完整地标地址。

    风格必须有世界观；规模取"更细一方胜出"（地标带绝对规模时覆写风格的）；
    约束（私有名）取先声明的一方（风格在前）。
    """
    style_reg = (style_reg or "").strip()
    landmark_addr = (landmark_addr or "").strip()
    if not style_reg and not landmark_addr:
        return ""

    s = parse_full(style_reg)
    l = parse_full(landmark_addr)

    # 风格不存在或风格无世界观：直接返回地标地址（必须有效）
    if s is None or not s.world:
        return _join_addr_text(l) if l is not None else ""

    # 风格有效，地标无效：只返回风格（无地域扩展）
    if l is None or not l.world:
        return _join_addr_text(s)

    # 两者都有世界观：用风格的地域前缀 + 地标多余的地域后缀
    s_parts = [p for p in (s.world,) + s.regions if p]
    l_parts = [p for p in (l.world,) + l.regions if p]

    # 如果地标前缀与风格前缀相同，去重
    common = 0
    if l_parts[0] == s_parts[0]:
        n = min(len(s_parts), len(l_parts))
        while common < n and s_parts[common] == l_parts[common]:
            common += 1
    extra = l_parts[common:]
    combined = s_parts + extra

    # 规模：更细的一方（通常为地标）有绝对规模则覆写风格的
    if l.scale_raw:
        merged_scale, merged_scale_raw = l.scale, l.scale_raw
    else:
        merged_scale, merged_scale_raw = s.scale, s.scale_raw
    # 约束：按读取顺序取第一次声明的值（风格在前，子地址覆写无效）
    if s.has_mark:
        merged_chunk, merged_mark = s.chunk, True
    elif l.has_mark:
        merged_chunk, merged_mark = l.chunk, True
    else:
        merged_chunk, merged_mark = "", False

    merged = Addr(
        world=combined[0],
        regions=(combined[1] if len(combined) > 1 else "",
                 combined[2] if len(combined) > 2 else "",
                 combined[3] if len(combined) > 3 else ""),
        scale=merged_scale,
        scale_raw=merged_scale_raw,
        chunk=merged_chunk,
        has_mark=merged_mark
    )
    return _join_addr_text(merged)


def jitter_address_cell(full_addr_text: str, reach: float, rng=None) -> str:
    """把地址三级地域编号抖到相邻单元（保留前缀）。"""
    import random as _random
    rng = rng or _random
    addr = parse_full(full_addr_text)
    if addr is None or not addr.regions[2]:
        return full_addr_text
    last = addr.regions[2]
    if not last.isdigit():
        return full_addr_text   # 非数字不能抖动
    exponent = int(last[-1])      # 保留末位作为指数（仅用于编号，无实际缩放意义）
    idx = int(last[:-1]) if len(last) > 1 else 0
    cell = max(1.0, cell_width_m(full_addr_text))
    spread = max(1, int(reach / cell) if reach else 1)
    new_idx = max(0, idx + rng.randint(-spread, spread))
    new_last = (str(new_idx) if new_idx else "") + str(exponent)
    merged = Addr(
        world=addr.world,
        regions=(addr.regions[0], addr.regions[1], new_last),
        scale=addr.scale,
        scale_raw=addr.scale_raw,
        chunk=addr.chunk,
        has_mark=addr.has_mark
    )
    return _join_addr_text(merged)


# ==================== 校验与展示 ====================

def validate_segment(seg: str, level: str) -> Optional[str]:
    seg = (seg or "").strip()
    if not seg:
        return None
    if not _REGION_RE.match(seg):
        return f"{LEVEL_LABELS[level]}段应为字母/数字/下划线"
    return None


def validate_address_text(text: str) -> Optional[str]:
    if not (text or "").strip():
        return None
    if parse_full(text) is not None:
        return None
    return _describe_invalid(text)


def _describe_invalid(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return ""
    if s.count("@") > 1 or s.count("!") > 1:
        return "地址中最多允许一个 @ 与一个 !"
    at = s.find("@")
    if at >= 0:
        pre = s[:at]
        if pre:
            items = [t.strip() for t in pre.split("_") if t.strip()]
            if len(items) != 3:
                return "绝对规模应为 3 个数字（如 1e5_5e3_1e2）"
            for it in items:
                if not _SCALE_ITEM_RE.match(it):
                    return f"规模值 {it} 格式不对"
        else:
            return "绝对规模不能为空"
        rest = s[at+1:]
        bang = rest.find("!")
        chunk = rest[:bang] if bang >= 0 else ""
        if chunk not in ("", "*") and not _CHUNK_RE.match(chunk):
            return "私有名只能为字母/数字/下划线"
    else:
        # 没有 @ 但包含 '-' 是不允许的（缺少规模）
        if "-" in s:
            return "包含地域段时必须提供绝对规模（...@...）"
        if not _WORLD_RE.match(s):
            return "世界观段应为字母/数字/下划线"
    parts = split_address(s)
    if not parts:
        return "缺少世界观"
    if not _WORLD_RE.match(parts[0]):
        return "世界观段无效"
    for seg in parts[1:]:
        if not _REGION_RE.match(seg):
            return f"地域段 '{seg}' 应为字母/数字/下划线"
    return "地址格式不正确"


def _fmt_size_m(v: float) -> str:
    if v >= 1000:
        return f"{v / 1000:.4g} 千米"
    return f"{v:.3g} 米"


def format_addr_verbose(text: str) -> str:
    addr = parse_full(text)
    if addr is None:
        return (text or "").strip() or "（无地址）"
    segs = []
    if addr.scale_raw:
        segs.append(f"（地域规模：{_fmt_size_m(addr.scale[0])} /"
                    f" {_fmt_size_m(addr.scale[1])} / {_fmt_size_m(addr.scale[2])}）")
    if addr.chunk not in ("", "*"):
        segs.append(f"私有名 {addr.chunk}")
    elif addr.chunk == "*":
        segs.append("私有名 *")
    labels = ("世界观", "一级地域", "二级地域", "三级地域")
    for label, seg in zip(labels, (addr.world,) + addr.regions):
        if not seg:
            break
        segs.append(f"{label} {seg}")
    return " / ".join(segs) if segs else "（无地址）"


def describe_scale(full_addr_text: str) -> str:
    w = cell_width_m(full_addr_text)
    return _fmt_size_m(w) if w > 0 else "无单元"