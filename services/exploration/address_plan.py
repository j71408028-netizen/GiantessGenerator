# -*- coding: utf-8 -*-
"""注册地址系统：地标地址的规划、可达性筛选与「无路可走」判定。

由 ``core/context.py`` 的 ``ExplorationContext`` 按职责抽出（阶段 3.1 / S2）。

依赖 ``ExplorationCatalog`` 而不是把 ``selected_styles`` / ``merged_landmarks`` /
``_landmark_styles`` 拷进来：那些字段会被 ``update_styles()`` 整体替换，拷贝必然
陈旧（见 ``catalog`` 模块的说明）。
"""

import random

from core.address_model import (
    REL_INSIDE, compose_address_ex, covers, world_of, depth_of, relate,
    cell_width_m, parse_full, jitter_address_cell, resolve_full_address,
)


class AddressPlanner:
    """地标地址规划器。

    ``landmark_address`` / ``quip_allowed_styles`` / ``prune_quips_by_styles`` /
    ``plan_address_comparisons`` 是给 ``report.ReportEngine`` 调用的——报告的每个
    地标对比都要先过一遍地址规则，所以这几步没法完全收在内部。
    """

    def __init__(self, catalog):
        self.catalog = catalog

    def _style_reg(self, landmark, registers=None) -> str:
        style = self.catalog._landmark_styles.get(id(landmark), "")
        if not style:
            return ""
        if registers is None:
            return self.catalog.landmark_repo.load_style_address(style) or ""
        return registers.get(style, "") or ""

    @staticmethod
    def _address_mode(landmark) -> str:
        """把地标的 address_mode 归一为 portable / inherit / exact。"""
        addr = (getattr(landmark, "address", "") or "").strip()
        mode = (getattr(landmark, "address_mode", "") or "").strip().lower()
        if mode in ("portable", "inherit", "exact", "relative"):
            return "exact" if mode == "relative" else mode
        if addr:
            return "exact"
        return "inherit" if landmark.frequency == "unique" else "portable"

    def _landmark_address_ex(self, landmark, registers=None):
        """返回 ``(完整地址, 错误文本)``；错误表示地址存在但不可用。"""
        style_reg = self._style_reg(landmark, registers)
        mode = self._address_mode(landmark)
        if mode == "portable":
            return "", ""
        if mode == "inherit":
            if not style_reg:
                return "", ""
            resolved, err = compose_address_ex(style_reg, "")
            if err:
                return "", f"{landmark.name}: {err}"
            return resolved, ""
        addr_text = (getattr(landmark, "address", "") or "").strip()
        if not addr_text:
            return "", ""
        resolved, err = compose_address_ex(style_reg, addr_text)
        if err:
            return "", f"{landmark.name}: {err}"
        return resolved, ""

    def landmark_address(self, landmark, registers=None) -> str:
        """按 address_mode 解析地标地址；'' 表示不参与地址规则或地址无效。"""
        return self._landmark_address_ex(landmark, registers)[0]

    def landmark_full_address(self, landmark, registers=None) -> str:
        """底层组合（不判定 address_mode），主要留给旧调用/诊断。"""
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
        """某条地标对比可衔接的描述风格：地址为空（未注册）的风格，或覆盖
        该地标地址的风格。

        返回 None 表示不限（地标本身无地址 / 旧内容兼容）。
        """
        if not landmark_addr_text:
            return None
        allowed = []
        for style, reg in quip_registers.items():
            reg = (reg or "").strip()
            if not reg or covers(reg, landmark_addr_text):
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

    def _quip_worlds(self) -> set:
        quip_repo = getattr(self.catalog, "quip_repo", None)
        selected = getattr(self.catalog, "selected_quip_styles", None) or []
        if quip_repo is None or not selected:
            return set()
        try:
            regs = quip_repo.load_style_registers(selected)
        except Exception:
            return set()
        return {world_of(r) for r in regs.values() if world_of(r)}

    def plan_address_comparisons(self, candidates, position, height, skip_base_prob,
                                 durability):
        """地址规则规划：返回 dict(engaged, stuck, position, comparisons, invalid)。

        首地标锚定（无位置时不受距离限制），随后只取与角色位置距离
        < 10×身高 且私有名约束互相满足 的地标地址。全部地标都够不着 /
        范围内独特地标耐久均 <0.5 时给出 stuck 原因，由调用方决定切换
        地址 / 世界观或放弃。
        未注册地址的内容（含旧数据）保持原行为；地址存在但无法组合时不再
        退化为“无地址”，而是排除并记入 ``invalid``。
        """
        landmark_repo = self.catalog.landmark_repo
        settings = self.catalog.settings
        registers = landmark_repo.load_style_registers(self.catalog.selected_styles)

        errors = []
        invalid_ids = set()
        addr_cache = {}

        def cand_addr(cand) -> str:
            lm = cand["landmark"]
            key = id(lm)
            if key in addr_cache:
                return addr_cache[key]
            addr, err = self._landmark_address_ex(lm, registers)
            if err:
                invalid_ids.add(key)
                errors.append({"landmark": lm.name,
                               "address": getattr(lm, "address", "") or "",
                               "error": err})
                addr = ""
            addr_cache[key] = addr
            return addr

        def cand_key(cand) -> str:
            """耐久表键：名称@完整地址；无地址时仅名称（旧存档兼容）。"""
            lm = cand["landmark"]
            if lm.frequency != "unique":
                return lm.name
            addr = cand_addr(cand)
            return f"{lm.name}@{addr}" if addr else lm.name

        # engaged：当前选中风格中确有可锚定（带完整地址）的候选
        addressable = [c for c in candidates if cand_addr(c)]
        engaged = bool(addressable)
        limit = int(settings.get("comparison_count", 5) or 5)
        if not engaged:
            valid = [c for c in candidates if id(c["landmark"]) not in invalid_ids]
            chosen = [c for c in valid
                      if self._durable_ok(c, durability, engaged=False,
                                          key=cand_key(c))][:limit]
            stuck = None
            if not chosen and errors:
                stuck = {"reason": "invalid_address", "position": position or ""}
            return {"engaged": False, "stuck": stuck, "position": position or "",
                    "comparisons": chosen, "invalid": errors}

        pos_text = (position or "").strip()
        if pos_text and parse_full(pos_text) is None:
            stuck = {"reason": "invalid_position", "position": pos_text}
            return {"engaged": True, "stuck": stuck, "position": pos_text,
                    "comparisons": [], "invalid": errors}

        # 世界观集合来自所有携带地址的内容（风格注册、地标、描述风格），
        # 不再只看选中风格的注册表。
        content_worlds = set()
        for reg in registers.values():
            w = world_of(reg)
            if w:
                content_worlds.add(w)
        for w in self._quip_worlds():
            content_worlds.add(w)
        for c in candidates:
            w = world_of(cand_addr(c))
            if w:
                content_worlds.add(w)
        content_worlds = sorted(content_worlds)

        pos_world = world_of(pos_text)
        if pos_text and pos_world and content_worlds and pos_world not in content_worlds:
            stuck = {"reason": "world_mismatch", "current_world": pos_world,
                     "worlds": content_worlds}
            return {"engaged": True, "stuck": stuck, "position": pos_text,
                    "comparisons": [], "invalid": errors}

        # 可用（耐久满足）候选；地址无效的地标不参与，避免静默退化为 portable。
        usable = [c for c in candidates
                  if id(c["landmark"]) not in invalid_ids
                  and self._durable_ok(c, durability, engaged=True, key=cand_key(c))]
        usable_addrs = [c for c in usable if cand_addr(c)]

        reach = 10.0 * height
        scan_radius = 50.0 * max(0.0, skip_base_prob or 0.0) * height

        if pos_text and pos_world and (not content_worlds or pos_world in content_worlds):
            pos_now = pos_text
            pick_order = usable
        else:
            # 新角色尚无位置：首个地标不限地址，取排序中第一个可用“带地址”地标锚定。
            # 优先独特地标，避免用均值地标作为新角色的固定落点。
            if not usable_addrs:
                # 独特地标都未注册或全部耐久不足：退回旧行为
                valid = [c for c in candidates if id(c["landmark"]) not in invalid_ids]
                chosen = [c for c in valid
                          if self._durable_ok(c, durability, engaged=False,
                                              key=cand_key(c))][:limit]
                stuck = None
                if not chosen and errors:
                    stuck = {"reason": "invalid_address", "position": pos_text}
                return {"engaged": False, "stuck": stuck, "position": pos_text,
                        "comparisons": chosen, "invalid": errors}
            anchors = [c for c in usable_addrs
                       if c["landmark"].frequency == "unique"] or usable_addrs
            anchor = anchors[0]
            pos_now = cand_addr(anchor)
            pick_order = [anchor] + [c for c in usable if c is not anchor]

        chosen = []
        for cand in pick_order:
            if len(chosen) >= limit:
                break
            addr = cand_addr(cand)
            rel = relate(pos_now, addr) if addr and pos_now else None
            if rel is not None:
                interval = rel.interval
                # 私有名约束互相满足才能配对（与文档口径一致）
                if not rel.chunk_ok or interval is None or interval[0] >= reach:
                    continue
                # 位置比目标粗时，真实距离可能超出 reach；保留可选性但标记不确定。
                if interval[1] > interval[0] and interval[1] >= reach:
                    cand = dict(cand)
                    cand["address_uncertain"] = True
            chosen.append(cand)
            # 抵达/路过地标后更新角色位置：
            # - 地标地址比当前位置更细或同级 → 立即把位置更新到该地标地址；
            # - 地标地址比当前位置更粗（只注册到上级区域）→ 以“身高10倍/地址规模”
            #   概率把位置末位更新到同一详细程度的其它可用单元（下次选取前生效）。
            if engaged and addr and pos_now:
                if depth_of(addr) >= depth_of(pos_now):
                    pos_now = addr
                elif rel.kind == REL_INSIDE and \
                        random.random() < min(1.0, reach / max(1.0, cell_width_m(pos_now))):
                    pos_now = self._shift_position_cell(pos_now, reach)

        stuck = None
        if not chosen:
            stuck = {"reason": "no_reachable", "position": pos_now,
                     "worlds": content_worlds}
        elif engaged and pos_now and scan_radius > 0:
            near = []
            for cand in candidates:
                lm = cand["landmark"]
                if lm.frequency != "unique":
                    continue
                if id(lm) in invalid_ids:
                    continue
                addr = cand_addr(cand)
                if not addr:
                    continue
                rel = relate(pos_now, addr)
                # 私有名冲突的地标不可配对，不算“附近可用”
                if not rel.chunk_ok or rel.interval is None:
                    continue
                if rel.interval[0] < scan_radius:
                    near.append(cand)
            if near and all(durability.get(cand_key(c), 1.0) < 0.5
                            for c in near):
                stuck = {"reason": "all_damaged", "position": pos_now,
                         "worlds": content_worlds}

        return {"engaged": engaged, "stuck": stuck, "position": pos_now,
                "comparisons": chosen, "invalid": errors}

    def stuck_options(self, state, stuck: dict) -> dict:
        """无路可走时可供角色选择的目标：当前/其它世界观内的地标地址，以及可切换的世界观。

        ``worlds`` 只列出“有可用落脚地址、且不同于当前位置”的世界观，避免 UI
        拿到一个无法落地的世界或原地切换。
        """
        landmark_repo = self.catalog.landmark_repo
        registers = landmark_repo.load_style_registers(self.catalog.selected_styles)
        durability = state.landmark_durability or {}
        seen = {}
        for lm in self.catalog.merged_landmarks:
            addr = self.landmark_address(lm, registers)
            if not addr:
                continue
            if lm.frequency == "unique":
                key = f"{lm.name}@{addr}"
                # 耐久低于 0.5 的独特地标已不适合作落脚点
                if durability.get(key, 1.0) < 0.5:
                    continue
            if addr not in seen:
                seen[addr] = lm.name

        seen_worlds = sorted({world_of(a) for a in seen if world_of(a)})
        candidate_worlds = stuck.get("worlds") or seen_worlds
        worlds = [w for w in candidate_worlds if w in seen_worlds]
        current_world = world_of(getattr(state, "position", "") or "")
        worlds = [w for w in worlds if w != current_world]
        return {
            "stuck": stuck,
            "addresses": [{"address": a, "name": n} for a, n in seen.items()],
            "worlds": worlds,
        }
