# -*- coding: utf-8 -*-
"""注册地址系统：地标地址解析与「无路可走」选项。

由 ``core/context.py`` 的 ``ExplorationContext`` 按职责抽出（阶段 3.1 / S2）。
P4a 之后本类收敛为**地址解析 + stuck_options 工具层**：选点循环（可达
过滤、活动区域偏好、位置演化、all_damaged 扫描）已被选取仲裁器
（``services/exploration/arbiter.py``）吸收，口径逐行移植。

S3 之后地址解析本身也搬走了：``AddressIndex`` 一次性把每条地标解析成
``AddressHandle``，本类只是它的门面（外加副本导出与 stuck 选项两件编排）。
因此这里不再有「按 ``id(landmark)`` 查风格归属」和「逐条回落磁盘读
``.addr.json``」——归属来自声明（``Ref``），注册表在重建时一次性读齐。

依赖 ``ExplorationCatalog`` 而不是把 ``merged_landmarks`` / 地址索引拷进来：
那些字段会被 ``reload_merged_data()`` 整体替换，拷贝必然陈旧。
"""

from dataclasses import replace

from core.address_model import world_of
from services.exploration.address_index import AddressIndex


class AddressPlanner:
    """地标地址规划器（地址解析的薄门面）。

    ``landmark_address`` / ``durability_key`` 供 ``ReportEngine`` 与
    ``SelectionArbiter`` 在对比循环里取地址与耐久键。
    """

    def __init__(self, catalog, index=None):
        self.catalog = catalog
        self.index = index if index is not None else self._index_of(catalog)

    @staticmethod
    def _index_of(catalog) -> AddressIndex:
        """取目录自带的索引；没有就按目录现有数据临时建一个（测试/旧构造点）。"""
        index = getattr(catalog, "address_index", None)
        if isinstance(index, AddressIndex):
            return index
        index = AddressIndex(getattr(catalog, "landmark_repo", None))
        index.rebuild(zip(getattr(catalog, "merged_refs", []) or [],
                          getattr(catalog, "merged_landmarks", []) or []))
        catalog.address_index = index
        return index

    # ---------- 地址解析（全部转发给索引，本类不重复判定口径） ----------

    def address_ex(self, landmark):
        """返回 ``(完整地址, 错误文本)``；错误表示地址存在但不可用。"""
        return self.index.address_ex(landmark)

    def landmark_address(self, landmark) -> str:
        """按 address_mode 解析地标地址；'' 表示不参与地址规则或地址无效。"""
        return self.index.address(landmark)

    def durability_key(self, landmark) -> str:
        """耐久表 / 地址表的键（``Ref`` 文本；无法归属时退回名称）。"""
        return self.index.durability_key(landmark)

    def ref(self, landmark):
        """地标的声明身份；无法归属时为 None。"""
        return self.index.ref_of(landmark)

    def handle(self, landmark):
        """地标对应的 ``AddressHandle``；未登记时为 None。"""
        return self.index.handle_of(landmark)

    # ---------- 编排 ----------

    def landmarks_for_export(self) -> list:
        """副本启动载荷用的地标：每条都带解析好的**完整地址**。

        副本会话在子进程里跑，那里没有地标仓库也没有风格注册表，``Landmark.address``
        的相对后缀解析不出来，所以地址必须在父进程组合好再进载荷（见
        ``docs/designs/dungeon_scene.md`` §6）。解析不了的地标照样导出（名字与
        尺寸不变），只是标成 ``portable``——不参与副本内的地址相配。

        导出的副本本身是自足的：``address`` 已是完整地址、``address_mode`` 是
        ``exact``，即使拿它回头再问一次 ``landmark_address()`` 也得到同一结果。
        """
        exported = []
        for landmark in self.catalog.merged_landmarks:
            address = self.landmark_address(landmark)
            if address:
                exported.append(replace(landmark, address=address,
                                        address_mode="exact"))
            else:
                exported.append(replace(landmark, address="",
                                        address_mode="portable"))
        return exported

    def stuck_options(self, state, stuck: dict) -> dict:
        """无路可走时可供角色选择的目标：当前/其它维度内的地标地址，以及可切换的维度。

        ``worlds`` 只列出“有可用落脚地址、且不同于当前位置”的维度，避免 UI
        拿到一个无法落地的世界或原地切换。
        """
        durability = state.landmark_durability or {}
        seen = {}
        for lm in self.catalog.merged_landmarks:
            addr = self.landmark_address(lm)
            if not addr:
                continue
            if lm.frequency == "unique":
                # 耐久低于 0.5 的独特地标已不适合作落脚点
                if durability.get(self.durability_key(lm), 1.0) < 0.5:
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
