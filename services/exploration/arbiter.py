# -*- coding: utf-8 -*-
"""选取仲裁器（P4a）：在地标选取与描述选取之间仲裁。

设计全文：``docs/designs/report_selection.md``。定位是**仲裁者，不是
指挥官**——只有"槽位 × 描述风格"的配对资格（车道窗口）与近匹配提拔是
裁定级输出；联合分、档案权重、路径连贯都是建议，报告引擎的预算走步与
坐标演化可自由偏离。硬闸（可达 / 私有名 / 维度 / 耐久）口径原样继承
自地址规划器；本类吸收其选点循环后，``AddressPlanner`` 收敛为地址解析 +
stuck_options 工具层。

四阶段：A 槽位装配（地址解析 + 硬闸）→ B 车道与充裕度 → C 联合排序
（近匹配提拔 + 档案权重，裁定级）→ D 物理走查（可达演化 + 位置更新，
逐行移植原规划器选点循环，含 pos_now 演化与 all_damaged 扫描）。

行为包开放面（scope ``report``）：车道窗口 / 联合分 / 近匹配提拔是
模块策略，模型参数是 ``report.model.*`` 常量钩子；硬闸、物理走查与
stuck 判定不开放。见 docs/designs/behaviors/hooks-report.md 的 report 组。
"""

import math
import random
from dataclasses import dataclass, field, replace
from typing import List, Optional, Tuple

from core.address_model import (
    REL_CONTAINS, REL_INSIDE, REL_SAME, cell_width_m, depth_of,
    jitter_address_cell, parse_full, relate, world_of,
)
from core.behavior_runtime import behavior_const, behavior_hook, get_runtime
from services.character.spatial import SpatialProfile

# 车道（Q4 分层）：同址 > 区域覆盖 > portable（未注册 / 无地址）
LANE_EXACT = "exact"
LANE_COVER = "cover"
LANE_FREE = "free"

# 可被行为包覆盖的仲裁模型参数（常量钩子的 key 是公开契约；消费一律走
# resolve_value，此处不保留模块级名字）。lane_weights 需要给测试引用，保留赋值。
LANE_WEIGHTS = behavior_const("report", "model.lane_weights", {
    LANE_EXACT: 1.0, LANE_COVER: 0.8, LANE_FREE: 0.5,
}, layer="infra", contract="data")
# 近匹配容差（log 尺度）：提拔只发生在该带内——覆盖只破平局与近局，
# 不推翻尺寸匹配的主导地位（"指令不是绝对的"在选取上的体现）
behavior_const("report", "model.near_match_tau", 0.35,
               layer="infra", contract="data")
# 联合分权重（默认值仅为建议，测试不锁数值）
behavior_const("report", "model.cover_weight", 0.5,
               layer="infra", contract="data")
behavior_const("report", "model.region_weight", 1.5,
               layer="infra", contract="data")
behavior_const("report", "model.path_weight", 0.3,
               layer="infra", contract="data")
# 充裕度：池深饱和折算除数、坐标邻域口径
behavior_const("report", "model.pool_saturation", 2.0,
               layer="infra", contract="data")
behavior_const("report", "model.pool_neighborhood", 2.0,
               layer="infra", contract="data")
# 提拔上限 = 对比槽位数 / 该除数（除数下限 1.0）
behavior_const("report", "model.promotion_cap_divisor", 2.0,
               layer="infra", contract="data")
# 可达范围 = 身高倍数（硬闸语义不开放，仅开放数值）
behavior_const("report", "model.reach_multiplier", 10.0,
               layer="infra", contract="data")


def _model_value(key: str) -> float:
    """report.model.* 常量钩子的有效值（覆盖优先于注册默认值）。"""
    return float(get_runtime().resolve_value(key))


@dataclass
class SlotRuling:
    """单个对比槽位的仲裁结果。

    ``lanes`` 是给 ``select_quip_with_budget`` 的资格窗口；无地址槽位为
    全部风格 FREE（None = 不限的旧语义等价于均匀权重，均匀权重不
    改变同池相对顺序）；无描述内容时为空表。``starved`` 仅在有描述内容
    但充裕度为 0 时置位（纯地址内容不参与仲裁）。
    ``env_estimate`` 是槽位地址的环境估计（档案环境轴，None = 零证据），
    供 report 做文本环境一致性软权重。
    """
    cand: dict
    addr: str
    lanes: List[Tuple[str, float]] = field(default_factory=list)
    affordance: float = 0.0
    starved: bool = False
    promoted: bool = False
    env_estimate: Optional[float] = None


@behavior_hook("report", "lanes", layer="module", contract="strategy")
def build_quip_lanes(landmark_addr: str, quip_registers: dict) -> list:
    """槽位 × 风格的车道表 ``[(style, weight)]``（任意序）。

    - 地标无地址（portable / 未注册）：全部风格 FREE；
    - 风格未注册：FREE；
    - 注册地址与地标地址同址（SAME + chunk_ok）：EXACT；
      覆盖（CONTAINS + chunk_ok）：COVER；
    - 其余（不覆盖 / 私有名冲突）：不入窗口。
    """
    weights = dict(get_runtime().resolve_value("report.model.lane_weights"))
    if not landmark_addr:
        return [(s, weights[LANE_FREE]) for s in (quip_registers or {})]
    lanes = []
    for style, reg in (quip_registers or {}).items():
        reg = (reg or "").strip()
        if not reg:
            lanes.append((style, weights[LANE_FREE]))
            continue
        rel = relate(reg, landmark_addr)
        if not rel.chunk_ok:
            continue
        if rel.kind == REL_SAME:
            lanes.append((style, weights[LANE_EXACT]))
        elif rel.kind == REL_CONTAINS:
            lanes.append((style, weights[LANE_COVER]))
    return lanes


@behavior_hook("report", "joint_score", layer="module", contract="strategy")
def joint_score(ruling: SlotRuling, position: str, reach: float,
                profile: SpatialProfile) -> float:
    """建议级联合分：match 主导，覆盖 / 档案 / 路径只调制。"""
    cand = ruling.cand
    match_q = 1.0 / (1.0 + abs(1.0 - float(cand.get("ratio") or 1.0)))
    score = match_q * (1.0 + _model_value("report.model.cover_weight")
                       * ruling.affordance)
    if ruling.addr:
        score *= 1.0 + profile.priors.anchored_bias      # 有地址内容
        score *= profile.region_factor(ruling.addr)
        score *= profile.env_preference_factor(ruling.addr)   # 环境偏好交点
        if profile.in_activity_region(ruling.addr):
            score *= _model_value("report.model.region_weight")
        if position:
            interval = relate(position, ruling.addr).interval
            if interval is not None and reach > 0:
                score /= 1.0 + (_model_value("report.model.path_weight")
                                * (interval[1] / reach))
    else:
        score *= 1.0 - profile.priors.anchored_bias       # portable 内容
    return score


@behavior_hook("report", "promotions", layer="module", contract="strategy")
def apply_near_match_promotions(order: List[SlotRuling],
                                cap: int) -> Tuple[list, list]:
    """近匹配提拔（裁定级）：星槽在容差带内与有覆盖的替代者交换位置。

    交换保持序列长度与其余相对顺序；星槽只是后移（仍可能在余量中被
    选中，届时按现状降级为纯地标对比）。每星槽至多提拔一次，总数
    受 cap 限制。
    """
    tau = _model_value("report.model.near_match_tau")
    order = list(order)
    promotions = []
    if cap <= 0:
        return order, promotions
    used = 0
    for i, ruling in enumerate(order):
        if used >= cap:
            break
        if not ruling.starved:
            continue
        for j in range(i + 1, len(order)):
            alt = order[j]
            if alt.starved or alt.affordance <= 0 or alt.promoted:
                continue
            ri = float(ruling.cand.get("ratio") or 0.0)
            rj = float(alt.cand.get("ratio") or 0.0)
            if ri <= 0 or rj <= 0:
                continue
            if abs(math.log(rj) - math.log(ri)) > tau:
                continue
            order[i], order[j] = alt, ruling
            alt.promoted = True
            promotions.append({
                "starved": getattr(ruling.cand.get("landmark"), "name", "?"),
                "promoted": getattr(alt.cand.get("landmark"), "name", "?"),
            })
            used += 1
            break
    return order, promotions


class SelectionArbiter:
    """在地标候选与描述风格供给之间做配对仲裁。

    用法（ReportEngine.generate）::

        arbitration = arbiter.arbitrate(candidates, position, height,
                                        durability, profile=profile,
                                        skip_base_prob=…, coords=(intr, dest),
                                        size_cat=size_cat)
        # stuck 早退口径与原 plan_address_comparisons 一致；
        # 循环遍历 arbitration["chosen"]，逐槽把 ruling.lanes 传给 select_quip_with_budget。
    """

    def __init__(self, catalog, addresses):
        self.catalog = catalog
        self.addresses = addresses

    # ==================== 车道：描述配对分层的唯一口径 ====================

    def quip_lanes(self, landmark_addr: str, quip_registers: dict) -> list:
        """车道表（``report.lanes`` 钩子的委托入口）。"""
        return build_quip_lanes(landmark_addr, quip_registers)

    # ==================== 充裕度：描述供给的池深估计 ====================

    def _pool_depth(self, style: str, size_cat: Optional[str],
                    coords: Optional[Tuple[float, float]]) -> int:
        """风格在 size_cat、坐标邻域（与 report 同口径）的可用条数。

        plan 期的估计（起始坐标）；报告内坐标漂移由消费时的窗口复核
        （report 的池空回退）兜住。
        """
        neighborhood = _model_value("report.model.pool_neighborhood")
        matrix = (getattr(self.catalog, "quips", {}) or {}).get(size_cat or "", {})
        if not matrix or coords is None:
            return 0
        intr, dest = coords
        count = 0
        for (i, d), quip_list in matrix.items():
            if math.hypot(i - intr, d - dest) > neighborhood:
                continue
            for q in quip_list:
                if isinstance(q, dict) and q.get("style") == style:
                    count += 1
        return count

    def _affordance(self, lanes: list, size_cat, coords) -> float:
        """槽位充裕度 = 允许车道内 max(车道权重 × 池深饱和折算)。"""
        saturation = _model_value("report.model.pool_saturation")
        best = 0.0
        for style, weight in lanes:
            depth = self._pool_depth(style, size_cat, coords)
            if depth <= 0:
                continue
            best = max(best, weight * (depth / (depth + saturation)))
        return best

    # ==================== 联合分与近匹配提拔 ====================

    def _joint_score(self, ruling: SlotRuling, position: str, reach: float,
                     profile: SpatialProfile) -> float:
        """建议级联合分（``report.joint_score`` 钩子的委托入口）。"""
        return joint_score(ruling, position, reach, profile)

    @staticmethod
    def _apply_promotions(order: List[SlotRuling],
                          cap: int) -> Tuple[list, list]:
        """近匹配提拔（``report.promotions`` 钩子的委托入口）。"""
        return apply_near_match_promotions(order, cap)

    # ==================== 主入口 ====================

    def arbitrate(self, candidates: list, position: str, height: float,
                  durability: dict, profile: Optional[SpatialProfile] = None,
                  skip_base_prob: float = 0.0,
                  coords: Optional[Tuple[float, float]] = None,
                  size_cat: Optional[str] = None, limit: Optional[int] = None
                  ) -> dict:
        """选取仲裁主入口。返回 dict（键与原规划器兼容 + 仲裁扩展）：

        ``engaged / stuck / position / invalid`` 同原 ``plan_address_comparisons``
        口径；``comparisons`` 为旧消费方保留（``[r.cand for r in chosen]``）；
        ``chosen`` 为 SlotRuling 列表；``order`` 为建议全序；``promotions`` /
        ``coverage`` 为可解释性数据（P4a 只落数据不落 UI）。
        """
        catalog = self.catalog
        settings = catalog.settings
        registers = catalog.landmark_repo.load_style_registers(catalog.selected_styles)
        quip_registers = catalog.quip_repo.load_style_registers(
            catalog.selected_quip_styles)
        has_quips = bool(getattr(catalog, "quips", {}))
        limit = int(limit or settings.get("comparison_count", 5) or 5)
        profile = profile or SpatialProfile()

        # ---------- 阶段 A：槽位装配（地址解析 + invalid 收集） ----------
        errors = []
        invalid_ids = set()
        addr_cache = {}

        def cand_addr(cand) -> str:
            lm = cand["landmark"]
            key = id(lm)
            if key in addr_cache:
                return addr_cache[key]
            addr, err = self.addresses.address_ex(lm)
            if err:
                invalid_ids.add(key)
                errors.append({"landmark": lm.name,
                               "address": getattr(lm, "address", "") or "",
                               "error": err})
                addr = ""
            addr_cache[key] = addr
            return addr

        def cand_key(cand) -> str:
            """耐久表键：地标的声明身份（``Ref`` 文本）；无法归属时退回名称。"""
            return self.addresses.durability_key(cand["landmark"])

        def durable_ok(cand, is_engaged: bool) -> bool:
            lm = cand["landmark"]
            if lm.frequency != "unique":
                return True
            return durability.get(cand_key(cand), 1.0) >= (0.5 if is_engaged else 0.0)

        def make_ruling(cand) -> SlotRuling:
            addr = cand_addr(cand)
            lanes = self.quip_lanes(addr, quip_registers) if has_quips else []
            affordance = self._affordance(lanes, size_cat, coords) if lanes else 0.0
            return SlotRuling(cand=cand, addr=addr, lanes=lanes,
                              affordance=affordance,
                              starved=has_quips and affordance <= 0.0,
                              env_estimate=profile.env_estimate(addr))

        # ---------- 阶段 B：车道与充裕度（建槽时一并完成） ----------
        slots = [make_ruling(c) for c in candidates]
        engaged = bool([r for r in slots if r.addr])

        if not engaged:
            valid = [r for r in slots if id(r.cand["landmark"]) not in invalid_ids]
            chosen = [r for r in valid if durable_ok(r.cand, False)][:limit]
            stuck = None
            if not chosen and errors:
                stuck = {"reason": "invalid_address", "position": position or ""}
            return self._result(False, stuck, position or "", chosen, slots, [],
                                errors=errors)

        pos_text = (position or "").strip()
        if pos_text and parse_full(pos_text) is None:
            stuck = {"reason": "invalid_position", "position": pos_text}
            return self._result(True, stuck, pos_text, [], slots, [], errors=errors)

        # 维度集合来自所有携带地址的内容（风格注册、地标、描述风格）
        content_worlds = set()
        for reg in registers.values():
            if world_of(reg):
                content_worlds.add(world_of(reg))
        for reg in quip_registers.values():
            if world_of(reg or ""):
                content_worlds.add(world_of(reg))
        for r in slots:
            if world_of(r.addr):
                content_worlds.add(world_of(r.addr))
        content_worlds = sorted(content_worlds)

        pos_world = world_of(pos_text)
        if pos_text and pos_world and content_worlds and pos_world not in content_worlds:
            stuck = {"reason": "world_mismatch", "current_world": pos_world,
                     "worlds": content_worlds}
            return self._result(True, stuck, pos_text, [], slots, [], errors=errors)

        # ---------- 阶段 C：联合排序 + 近匹配提拔（裁定级） ----------
        usable = [r for r in slots
                  if id(r.cand["landmark"]) not in invalid_ids
                  and durable_ok(r.cand, True)]
        reach = _model_value("report.model.reach_multiplier") * height
        order = self._order(usable, pos_text, reach, profile)
        promotions = []
        if has_quips:
            cap = max(0, int(limit / max(
                1.0, _model_value("report.model.promotion_cap_divisor"))))
            order, promotions = self._apply_promotions(order, cap)

        if pos_text and pos_world and (not content_worlds or pos_world in content_worlds):
            pos_now = pos_text
            walk_order = order
        else:
            # 新角色尚无位置：首个带地址槽位锚定（优先独特地标，避免均值
            # 地标作为固定落点）；锚定序沿用联合序（档案偏好已参与）。
            usable_addrs = [r for r in order if r.addr]
            if not usable_addrs:
                valid = [r for r in slots
                         if id(r.cand["landmark"]) not in invalid_ids]
                chosen = [r for r in valid if durable_ok(r.cand, False)][:limit]
                stuck = None
                if not chosen and errors:
                    stuck = {"reason": "invalid_address", "position": pos_text}
                return self._result(False, stuck, pos_text, chosen, slots, [],
                                    errors=errors)
            anchors = [r for r in usable_addrs
                       if r.cand["landmark"].frequency == "unique"] or usable_addrs
            anchor = anchors[0]
            pos_now = anchor.addr
            walk_order = [anchor] + [r for r in order if r is not anchor]

        # ---------- 阶段 D：物理走查（原规划器选点循环逐行移植） ----------
        chosen = []
        for ruling in walk_order:
            if len(chosen) >= limit:
                break
            addr = ruling.addr
            rel = relate(pos_now, addr) if addr and pos_now else None
            if rel is not None:
                interval = rel.interval
                # 私有名约束互相满足才能配对（与文档口径一致）
                if not rel.chunk_ok or interval is None or interval[0] >= reach:
                    continue
                # 位置比目标粗时真实距离可能超 reach：保留可选性但标记不确定
                if interval[1] > interval[0] and interval[1] >= reach:
                    ruling = replace(ruling, cand={**ruling.cand,
                                                   "address_uncertain": True})
            chosen.append(ruling)
            # 抵达/路过地标后更新角色位置（同原口径）：更细或同级 → 立即
            # 落位；更粗 → 按"身高10倍/地址规模"概率抖到同级其它单元。
            if engaged and addr and pos_now:
                if depth_of(addr) >= depth_of(pos_now):
                    pos_now = addr
                elif rel.kind == REL_INSIDE and \
                        random.random() < min(1.0, reach / max(1.0, cell_width_m(pos_now))):
                    pos_now = jitter_address_cell(pos_now, reach)

        stuck = None
        if not chosen:
            stuck = {"reason": "no_reachable", "position": pos_now,
                     "worlds": content_worlds}
        else:
            scan_radius = 50.0 * max(0.0, skip_base_prob or 0.0) * height
            if engaged and pos_now and scan_radius > 0:
                # 空间候选走地址索引（私有名冲突、不可达都在索引里判过），
                # 只保留本轮的候选槽位，口径与原逐条 relate 一致。
                near_refs = set(self.addresses.index.nearby(pos_now, scan_radius))
                near = [r for r in slots
                        if near_refs
                        and r.cand["landmark"].frequency == "unique"
                        and self.addresses.ref(r.cand["landmark"]) in near_refs]
                if near and all(durability.get(
                        self.addresses.durability_key(r.cand["landmark"]), 1.0) < 0.5
                        for r in near):
                    stuck = {"reason": "all_damaged", "position": pos_now,
                             "worlds": content_worlds}

        return self._result(engaged, stuck, pos_now, chosen, order, promotions,
                            errors=errors,
                            coverage=self._coverage(chosen, quip_registers,
                                                    size_cat, coords))

    # ==================== 排序与装配辅助 ====================

    def _order(self, usable: List[SlotRuling], position: str, reach: float,
               profile: SpatialProfile) -> List[SlotRuling]:
        """建议序。match 模式：联合分稳定排序（仲裁主场）；显式尺寸序
        （size_asc/desc）是玩家可感知的呈现选择，保持原序，仅保留活动
        区域稳定前移（原 _prefer_activity_regions 语义，经档案判定）。
        """
        if self.catalog.settings.get("comparison_order", "match") == "match":
            return sorted(usable, key=lambda r: -self._joint_score(
                r, position, reach, profile))

        def rank(r: SlotRuling) -> int:
            return 0 if (r.addr and profile.in_activity_region(r.addr)) else 1

        return sorted(usable, key=rank)

    def _coverage(self, chosen: List[SlotRuling], quip_registers: dict,
                  size_cat, coords) -> dict:
        """覆盖报告（可解释性数据；fired_styles 由引擎在实际选中后回填）。"""
        chosen_styles = set()
        for r in chosen:
            chosen_styles.update(s for s, _w in r.lanes)
        wasted = [style for style in quip_registers
                  if style not in chosen_styles
                  and self._pool_depth(style, size_cat, coords) > 0]
        return {
            "starved_slots": sum(1 for r in chosen if r.starved),
            "wasted_styles": wasted,
            "fired_styles": [],
        }

    def _result(self, engaged, stuck, position, chosen, order, promotions,
                errors=None, coverage=None):
        return {
            "engaged": engaged,
            "stuck": stuck,
            "position": position or "",
            "comparisons": [r.cand for r in chosen],   # 兼容旧消费方
            "chosen": chosen,
            "order": order,
            "promotions": promotions,
            "coverage": coverage or {"starved_slots": 0, "wasted_styles": [],
                                     "fired_styles": []},
            "invalid": errors or [],
        }
