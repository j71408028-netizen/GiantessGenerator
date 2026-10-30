# -*- coding: utf-8 -*-
"""地址索引：地标 ↔ 完整地址的一次性解析与双向查询。

资源与地址之间要交换的是**解析结果**，不是变更事件。本模块是只读索引（拉模型）：
``rebuild()`` 时把每条地标解析成一个 ``AddressHandle``，之后地址规划器、报告引擎、
选取仲裁器都读同一份 handle，不再各自拼地址、各自拼耐久键。

三个查询：

- 正向 ``handle`` / ``handle_of`` / ``address``：地标 → handle（完整地址文本、
  解析结果、维度、私有名、最小单元边长、错误原因）；
- 反向 ``referencing(prefix)``：地址（或其地域前缀）→ 引用该地址的资源；
  按「维度 + 各级地域」建倒排，不做全表遍历；
- 空间 ``nearby(position, reach_m)``：位置附近、可配对且可达的资源。

身份用 :class:`core.resource_ref.Ref`，不是 ``id(landmark)``：``replace()`` 出来的
副本会换内存地址，但 ``Ref`` 由「风格 + 显示名」这种声明信息构成，副本照样算得出来。
``_objects`` 只是「进程内对象 → 已声明 Ref」的反查指针（``Landmark`` 不可哈希），
副本查不到时按「显示名唯一」回落；名字在多个风格间重名则视为无法归属，交由地标
自身的 ``address`` 字段兜底（导出的副本正是这种情况，它带的是完整地址）。

失效不搞事件订阅，只有两类写路径会改变解析结果：

1. 合并数据重建（``ExplorationCatalog.reload_merged_data``，覆盖风格选择变更、
   世界包激活切换、挑战包导入）；
2. 风格注册地址保存/删除（编辑地址后由界面触发同一处重建）。

地标内容本身变了必然伴随 ``merged_landmarks`` 整体替换，因此不需要 dirty 标记。
"""

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from core.address_model import (
    Addr, canonical_key, cell_width_m, compose_address_ex, covers, parse_full,
    relate, world_of,
)
from core.resource_ref import Ref

#: 地标在资源声明里的类型 id（与 ``core/resource_spec.py`` 一致）
LANDMARK_KIND = "landmarks"

#: ``address_mode`` 的归一结果
MODE_PORTABLE = "portable"
MODE_INHERIT = "inherit"
MODE_EXACT = "exact"


@dataclass(frozen=True)
class AddressHandle:
    """单条地标的地址解析结果（一次算清，多处复用）。

    ``text`` 为空表示该地标不参与地址规则（portable），或地址存在但组合失败
    （此时 ``error`` 非空）。``cell_width_m`` 是地址最小单元的边长，0 表示
    纯维度或无法解析。
    """

    ref: Ref
    name: str
    style: str
    mode: str
    text: str
    addr: Optional[Addr]
    canonical: Optional[tuple]
    world: str
    chunk: str
    cell_width_m: float
    error: str = ""

    @property
    def portable(self) -> bool:
        """不参与地址相配（无地址，或显式 portable）。"""
        return not self.text


def address_mode_of(landmark) -> str:
    """把地标的 ``address_mode`` 归一为 portable / inherit / exact。"""
    addr = (getattr(landmark, "address", "") or "").strip()
    mode = (getattr(landmark, "address_mode", "") or "").strip().lower()
    if mode in (MODE_PORTABLE, MODE_INHERIT, MODE_EXACT, "relative"):
        return MODE_EXACT if mode == "relative" else mode
    if addr:
        return MODE_EXACT
    return MODE_INHERIT if landmark.frequency == "unique" else MODE_PORTABLE


def _resolve_text(landmark, style_reg: str, mode: str) -> Tuple[str, str]:
    """按参与方式组合出完整地址，返回 ``(地址文本, 错误文本)``。"""
    if mode == MODE_PORTABLE:
        return "", ""
    if mode == MODE_INHERIT:
        if not style_reg:
            return "", ""
        resolved, err = compose_address_ex(style_reg, "")
        return ("", f"{landmark.name}: {err}") if err else (resolved, "")
    addr_text = (getattr(landmark, "address", "") or "").strip()
    if not addr_text:
        return "", ""
    resolved, err = compose_address_ex(style_reg, addr_text)
    return ("", f"{landmark.name}: {err}") if err else (resolved, "")


def _build_handle(ref: Ref, landmark, style_reg: str) -> AddressHandle:
    name = getattr(landmark, "name", "") or ""
    mode = address_mode_of(landmark)
    text, error = _resolve_text(landmark, style_reg, mode)
    addr = parse_full(text) if text else None
    chunk = ""
    if addr is not None and addr.has_mark and addr.chunk not in ("", "*"):
        chunk = addr.chunk
    return AddressHandle(
        ref=ref,
        name=name,
        style=ref.scope,
        mode=mode,
        text=text,
        addr=addr,
        canonical=canonical_key(text) if text else None,
        world=world_of(text) if text else "",
        chunk=chunk,
        cell_width_m=cell_width_m(text) if text else 0.0,
        error=error,
    )


def _ids_of(addr: Optional[Addr]) -> List[str]:
    """地址的 id 段（维度 + 各级地域），空段省略。"""
    if addr is None or not addr.world:
        return []
    return [addr.world] + [seg for seg in addr.regions if seg]


class AddressIndex:
    """地标地址的只读索引。构造后调 ``rebuild(entries)`` 才有内容。"""

    def __init__(self, landmark_repo=None):
        self._repo = landmark_repo
        self._handles: Dict[Ref, AddressHandle] = {}
        self._objects: Dict[int, Ref] = {}
        self._by_name: Dict[str, List[Ref]] = {}
        self._by_prefix: Dict[Tuple[str, ...], List[Ref]] = {}
        self._revision = 0

    # ==================== 重建 ====================

    def rebuild(self, entries: Iterable[Tuple[Ref, object]]) -> None:
        """按 ``[(Ref, Landmark)]`` 全量重建。

        同一条 ``Ref`` 出现多次（同风格内重名）只保留首条，避免互相覆盖；
        对象指针仍然逐条登记，``handle_of()`` 对两个同名对象都返回同一条 handle。
        """
        pairs = [(ref, lm) for ref, lm in (entries or [])
                 if isinstance(ref, Ref) and lm is not None]
        styles: List[str] = []
        for ref, _lm in pairs:
            if ref.scope and ref.scope not in styles:
                styles.append(ref.scope)
        registers = {}
        if self._repo is not None and styles:
            registers = self._repo.load_style_registers(styles) or {}

        handles: Dict[Ref, AddressHandle] = {}
        objects: Dict[int, Ref] = {}
        by_name: Dict[str, List[Ref]] = {}
        for ref, landmark in pairs:
            objects[id(landmark)] = ref
            name = getattr(landmark, "name", "") or ""
            by_name.setdefault(name, []).append(ref)
            if ref in handles:
                continue
            handles[ref] = _build_handle(
                ref, landmark, (registers.get(ref.scope) or "").strip())

        prefixes: Dict[Tuple[str, ...], List[Ref]] = {}
        for ref, handle in handles.items():
            ids = _ids_of(handle.addr)
            for depth in range(1, len(ids) + 1):
                prefixes.setdefault(tuple(ids[:depth]), []).append(ref)

        self._handles = handles
        self._objects = objects
        self._by_name = by_name
        self._by_prefix = prefixes
        self._revision += 1

    @property
    def revision(self) -> int:
        """重建次数（诊断用，也可作为外部缓存的失效判断）。"""
        return self._revision

    def __len__(self) -> int:
        return len(self._handles)

    # ==================== 正向：资源 → 地址 ====================

    def handle(self, ref) -> Optional[AddressHandle]:
        return self._handles.get(ref) if isinstance(ref, Ref) else None

    def ref_of(self, landmark) -> Optional[Ref]:
        """地标对象 → ``Ref``。

        先查 ``rebuild()`` 登记的进程内指针，再按显示名回落；名字在多个风格间
        重名时不猜，返回 None（调用方按地标自身字段兜底）。
        """
        if landmark is None:
            return None
        ref = self._objects.get(id(landmark))
        if ref is not None:
            return ref
        candidates = self._by_name.get(getattr(landmark, "name", "") or "") or []
        if len(candidates) == 1:
            return candidates[0]
        return None

    def handle_of(self, landmark) -> Optional[AddressHandle]:
        return self.handle(self.ref_of(landmark))

    def style_of(self, landmark) -> str:
        """地标所属风格 id；无法归属时为空串。"""
        ref = self.ref_of(landmark)
        return ref.scope if ref is not None else ""

    def address_ex(self, landmark) -> Tuple[str, str]:
        """``(完整地址, 错误文本)``。未登记的副本按自身字段就地解析。"""
        handle = self.handle_of(landmark)
        if handle is not None:
            return handle.text, handle.error
        return _resolve_text(landmark, "", address_mode_of(landmark))

    def address(self, landmark) -> str:
        return self.address_ex(landmark)[0]

    def durability_key(self, landmark) -> str:
        """耐久表/地址表的键：能归属就用 ``Ref`` 文本，否则退回名称。

        耐久按「声明的地标」记，不按地址记——地址改了不该清零，这也是旧键
        ``名称@完整地址`` 被替换的原因（拼接键在改名/改址时断档）。
        """
        ref = self.ref_of(landmark)
        return ref.text if ref is not None else (getattr(landmark, "name", "") or "")

    # ==================== 反向与空间查询 ====================

    def referencing(self, prefix: str) -> List[Ref]:
        """地址或其地域前缀覆盖到的资源，按 ``Ref`` 排序。

        ``prefix`` 必须是能 ``parse_full`` 的地址：纯维度（``W``）或带规模/约束的
        完整前缀（``1e4_1e3_1e2@W-A``、``1e4_1e3_1e2@A!W-A``）。地域前缀不看
        规模——``1e5_...`` 与 ``1e4_...`` 注册在 ``W-A`` 的地标会一起命中；
        带约束段的前缀还要逐个用 ``covers()`` 精算。
        """
        text = (prefix or "").strip()
        addr = parse_full(text)
        ids = _ids_of(addr)
        if not ids:
            return []
        refs = self._by_prefix.get(tuple(ids)) or []
        if not refs:
            return []
        if not addr.has_mark and not addr.scale_raw:
            return sorted(refs)
        constrained = []
        for ref in refs:
            handle = self._handles.get(ref)
            if handle is not None and covers(text, handle.text):
                constrained.append(ref)
        return sorted(constrained)

    def nearby(self, position: str, reach_m: float) -> List[Ref]:
        """位置附近可配对的资源（私有名约束必须互相满足），按 ``Ref`` 排序。"""
        pos = (position or "").strip()
        if not pos or reach_m <= 0:
            return []
        found = []
        for ref, handle in self._handles.items():
            if not handle.text:
                continue
            rel = relate(pos, handle.text)
            if not rel.chunk_ok or rel.interval is None:
                continue
            if rel.interval[0] < reach_m:
                found.append(ref)
        return sorted(found)
