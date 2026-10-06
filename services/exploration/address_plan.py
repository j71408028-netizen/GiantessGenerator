# -*- coding: utf-8 -*-
"""注册地址系统：地标地址的规划、可达性筛选与「无路可走」判定。

由 ``core/context.py`` 的 ``ExplorationContext`` 按职责抽出（阶段 3.1 / S2）。

依赖 ``ExplorationCatalog`` 而不是把 ``selected_styles`` / ``merged_landmarks`` /
``_landmark_styles`` 拷进来：那些字段会被 ``update_styles()`` 整体替换，拷贝必然
陈旧（见 ``catalog`` 模块的说明）。
"""

import random

from core.address_model import (
    resolve_full_address, world_of, depth_of, distance_m, touches,
    cell_width_m, can_pair, jitter_address_cell,
)


class AddressPlanner:
    """地标地址规划器。

    ``landmark_full_address`` / ``quip_allowed_styles`` / ``prune_quips_by_styles`` /
    ``plan_address_comparisons`` 是给 ``report.ReportEngine`` 调用的——报告的每个
    地标对比都要先过一遍地址规则，所以这几步没法完全收在内部。
    """

    def __init__(self, catalog):
        self.catalog = catalog

    def landmark_full_address(self, landmark, registers=None) -> str:
        """组合风格注册地址与地标地址为完整地址。无注册返回 ''。"""
        style = self.catalog._landmark_styles.get(id(landmark), "")
        reg = ""
        if style:
            reg = (registers or {}).get(style, "")
            if reg is None:
                reg = self.catalog.landmark_repo.load_style_address(style)
        return resolve_full_address(reg, getattr(landmark, "address", "") or "")

    @staticmethod
    def _durable_ok(cand, durability, engaged: bool, key: str = "") -> bool:
        lm = cand["landmark"]
        if lm.frequency != "unique":
            return True
        value = durability.get(key or lm.name, 1.0)
        return value >= (0.5 if engaged else 0.0)

    def quip_allowed_styles(self, landmark_addr_text: str, quip_registers: dict):
        """某条地标对比可衔接的描述风格：地址为空（未注册）的风格，或与其
        地域同址/包含且规模组/私有名约束互相满足的风格。

        返回 None 表示不限（地标本身无地址 / 旧内容兼容）。
        """
        if not landmark_addr_text:
            return None
        allowed = []
        for style, reg in quip_registers.items():
            reg = (reg or "").strip()
            if not reg or can_pair(reg, landmark_addr_text):
                allowed.append(style)
        return allowed

    @staticmethod
    def prune_quips_by_styles(quips_working: dict, allowed) -> None:
        """把描述池中不属于 allowed 风格的事件就地移除（allowed=None 不限制）。"""
        if allowed is None:
            return
        allowed_set = set(allowed)
        for size_cat, matrix in quips_working.items():
            for coord, quip_list in list(matrix.items()):
                kept = [q for q in quip_list
                        if isinstance(q, dict) and q.get("style", "") in allowed_set]
                if kept:
                    matrix[coord] = kept
                else:
                    matrix.pop(coord, None)

    def _shift_position_cell(self, position_text: str, reach: float) -> str:
        """按“身高10倍/地址规模”概率被触发后：把位置末位更新到同详细程度的其它单元。

        保留地址上的绝对规模/约束段前缀。
        """
        return jitter_address_cell(position_text, reach)

    def plan_address_comparisons(self, candidates, position, height, skip_base_prob,
                                 durability):
        """地址规则规划：返回 dict(engaged, stuck, position, comparisons)。

        首地标锚定（无位置时不受距离限制），随后只取与角色位置距离
        < 10×身高 的地标地址。全部地标都够不着 / 范围内独特地标耐久
        均 <0.5 时给出 stuck 原因，由调用方决定切换地址 / 世界观或放弃。
        未注册地址的内容（含旧数据）保持原行为。
        """
        landmark_repo = self.catalog.landmark_repo
        settings = self.catalog.settings
        registers = landmark_repo.load_style_registers(self.catalog.selected_styles)

        def cand_addr(cand) -> str:
            lm = cand["landmark"]
            if lm.frequency != "unique":
                return ""
            return self.landmark_full_address(lm, registers)

        def cand_key(cand) -> str:
            """耐久表键：名称@完整地址；无地址时仅名称（旧存档兼容）。"""
            lm = cand["landmark"]
            if lm.frequency != "unique":
                return lm.name
            addr = cand_addr(cand)
            return f"{lm.name}@{addr}" if addr else lm.name

        # engaged：当前选中风格中确有可锚定（带完整地址）的独特地标候选
        addressable = [c for c in candidates if cand_addr(c)]
        engaged = bool(addressable)
        limit = int(settings.get("comparison_count", 5) or 5)
        if not engaged:
            chosen = [c for c in candidates
                      if self._durable_ok(c, durability, engaged=False,
                                          key=cand_key(c))][:limit]
            return {"engaged": False, "stuck": None, "position": position or "",
                    "comparisons": chosen}

        reg_worlds = sorted({world_of(r) for r in registers.values() if world_of(r)})
        pos_world = world_of(position)
        if position and pos_world and reg_worlds and pos_world not in reg_worlds:
            stuck = {"reason": "world_mismatch", "current_world": pos_world,
                     "worlds": reg_worlds}
            return {"engaged": True, "stuck": stuck, "position": position,
                    "comparisons": []}

        # 可用（耐久满足）候选
        usable = [c for c in candidates
                  if self._durable_ok(c, durability, engaged=True, key=cand_key(c))]
        usable_addrs = [c for c in usable if cand_addr(c)]

        reach = 10.0 * height
        scan_radius = 50.0 * max(0.0, skip_base_prob or 0.0) * height

        if position and pos_world and (not reg_worlds or pos_world in reg_worlds):
            pos_now = position
            pick_order = usable
        else:
            # 新角色尚无位置：首个地标不限地址，取排序中第一个可用“带地址”地标锚定
            if not usable_addrs:
                # 独特地标都未注册或全部耐久不足：退回旧行为
                chosen = [c for c in candidates
                          if self._durable_ok(c, durability, engaged=False,
                                              key=cand_key(c))][:limit]
                return {"engaged": False, "stuck": None, "position": position or "",
                        "comparisons": chosen}
            anchor = usable_addrs[0]
            pos_now = cand_addr(anchor)
            pick_order = [anchor] + [c for c in usable if c is not anchor]

        chosen = []
        for cand in pick_order:
            if len(chosen) >= limit:
                break
            addr = cand_addr(cand)
            if addr and pos_now:
                d = distance_m(pos_now, addr)
                if d is None or d >= reach:
                    continue
            chosen.append(cand)
            # 抵达/路过地标后更新角色位置：
            # - 地标地址比当前位置更细或同级 → 立即把位置更新到该地标地址；
            # - 地标地址比当前位置更粗（只注册到上级区域）→ 以“身高10倍/地址规模”
            #   概率把位置末位更新到同一详细程度的其它可用单元（下次选取前生效）。
            if engaged and addr and pos_now:
                if depth_of(addr) >= depth_of(pos_now):
                    pos_now = addr
                elif touches(pos_now, addr) and \
                        random.random() < min(1.0, reach / max(1.0, cell_width_m(pos_now))):
                    pos_now = self._shift_position_cell(pos_now, reach)

        stuck = None
        if not chosen:
            stuck = {"reason": "no_reachable", "position": pos_now}
        elif engaged and pos_now and scan_radius > 0:
            near = []
            for cand in candidates:
                lm = cand["landmark"]
                if lm.frequency != "unique":
                    continue
                addr = cand_addr(cand)
                if not addr:
                    continue
                d = distance_m(pos_now, addr)
                if d is not None and d < scan_radius:
                    near.append(cand)
            if near and all(durability.get(cand_key(c), 1.0) < 0.5
                            for c in near):
                stuck = {"reason": "all_damaged", "position": pos_now}

        return {"engaged": engaged, "stuck": stuck, "position": pos_now,
                "comparisons": chosen}

    def stuck_options(self, state, stuck: dict) -> dict:
        """无路可走时可供角色选择的目标：同一世界观内的地标地址，以及可选世界观。"""
        landmark_repo = self.catalog.landmark_repo
        registers = landmark_repo.load_style_registers(self.catalog.selected_styles)
        durability = state.landmark_durability or {}
        seen = {}
        for lm in self.catalog.merged_landmarks:
            if lm.frequency != "unique":
                continue
            addr = self.landmark_full_address(lm, registers)
            key = f"{lm.name}@{addr}" if addr else lm.name
            # 耐久低于 0.5 的独特地标已不适合作落脚点
            if durability.get(key, 1.0) < 0.5:
                continue
            if addr and addr not in seen:
                seen[addr] = lm.name
        reg_worlds = sorted({world_of(r) for r in registers.values() if world_of(r)})
        worlds = stuck.get("worlds") or reg_worlds
        return {
            "stuck": stuck,
            "addresses": [{"address": a, "name": n} for a, n in seen.items()],
            "worlds": worlds,
        }
