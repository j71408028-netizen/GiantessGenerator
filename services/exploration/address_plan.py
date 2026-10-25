# -*- coding: utf-8 -*-
"""注册地址系统：地标地址解析与「无路可走」选项。

由 ``core/context.py`` 的 ``ExplorationContext`` 按职责抽出（阶段 3.1 / S2）。
P4a 之后本类收敛为**地址解析 + stuck_options 工具层**：选点循环（可达
过滤、活动区域偏好、位置演化、all_damaged 扫描）已被选取仲裁器
（``services/exploration/arbiter.py``）吸收，口径逐行移植。

依赖 ``ExplorationCatalog`` 而不是把 ``selected_styles`` / ``merged_landmarks`` /
``_landmark_styles`` 拷进来：那些字段会被 ``update_styles()`` 整体替换，拷贝必然
陈旧（见 ``catalog`` 模块的说明）。
"""

from core.address_model import (
    compose_address_ex, world_of, resolve_full_address,
)


class AddressPlanner:
    """地标地址规划器。

    ``landmark_address`` 供 ``ReportEngine`` 在对比循环里解析地标地址（耐久
    键、叙述定位）。
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
