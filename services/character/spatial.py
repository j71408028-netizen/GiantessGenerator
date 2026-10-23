# -*- coding: utf-8 -*-
"""角色空间档案：TAG 先验 + 到访日志学习的区域权重树（P3a）。

三层结构（优先级从高到低，全景设计见 .workbuddy/address-scheduler-panorama.md §2.1）：

1. 手工覆盖层：``CharacterSnapshot.activity_regions`` 原样透传，命中判定用
   ``covers``（与地址规划器现行口径一致）；
2. 学习层：从演化表带 ``position`` 的行学习区域权重树（世界观 → 一级 →
   二级；三级单元过细，作为个体地标对待，不进区域统计）。行权重按时间
   指数衰减（EWMA 半衰期），节点份额做狄利赫里平滑——样本少时向均匀
   分布收缩，长期不到访则整体向 1.0 回落，即"区域偏好随时间衰减"；
3. 先验层：TAG → 空间参数向量（``TAG_SPATIAL_PRIORS`` 数据表）。只有纯
   空间语义的标签入表，姿态/内容类标签留在内容层。

与 ``rhythm.py`` 同构（先验 + 借力收缩），差异在特征空间：作息是 7×12
平面网格、用全局均值收缩；地址是层级树、用父节点 + 均匀先验收缩
（n-gram 回退式）。本模块只做学习与装配，不参与选取决策——消费方是
P4a 的角色调度器。
"""

import datetime
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from core.address_model import covers, parse_full

# EWMA 半衰期（天）：到访权重随时间减半的周期，承担区域偏好的时间衰减
_HALF_LIFE_DAYS = 14.0
# 狄利赫里平滑伪计数：越大越向均匀分布收缩（样本越少收缩越强）
_SMOOTHING_K = 6.0
# 调制因子夹取区间（与 rhythm 同口径：围绕 1.0，不归零也不爆炸）
_MIN_FACTOR = 0.15
_MAX_FACTOR = 2.5


@dataclass
class SpatialPriors:
    """TAG 先验给出的空间参数向量（中性值 = 不影响调度）。"""
    range_factor: float = 1.0     # 活动半径倍率（调度器作用于可达范围）
    stickiness: float = 0.0       # 区域粘性：>0 放大学习层对已到访区域的偏向，<0 抹平
    novelty_bonus: float = 0.0    # 新颖加成：未到访分支的因子乘 (1 + bonus)
    anchored_bias: float = 0.0    # 有地址内容相对 portable 内容的偏好偏移


# TAG → 先验参数的数据表。参数语义对齐 get_comparisons 的现行效果：
# 旅行偏爱独特（锚定）地标、地理测量较弱地偏爱独特地标——这两项空间语义
# P4a 迁入调度器后从此表取参；四处走走/能躺着不站着的姿态过滤留在内容层，
# 这里只登记其活动范围倾向。行为包可扩展映射（键值均按标签名对齐）。
TAG_SPATIAL_PRIORS: Dict[str, SpatialPriors] = {
    "旅行": SpatialPriors(range_factor=1.25, novelty_bonus=0.5, anchored_bias=0.3),
    "地理测量": SpatialPriors(range_factor=1.1, novelty_bonus=0.3, anchored_bias=0.15),
    "四处走走": SpatialPriors(range_factor=1.15, stickiness=-0.3),
    "能躺着不站着": SpatialPriors(range_factor=0.8, stickiness=0.4),
}


def priors_for_tags(tags: Iterable[str]) -> SpatialPriors:
    """合并多个标签的先验：半径倍率相乘、其余相加；未知标签不参与。"""
    range_factor = 1.0
    stickiness = novelty = anchored = 0.0
    for tag in tags or []:
        p = TAG_SPATIAL_PRIORS.get(tag)
        if p is None:
            continue
        range_factor *= p.range_factor
        stickiness += p.stickiness
        novelty += p.novelty_bonus
        anchored += p.anchored_bias
    return SpatialPriors(
        range_factor=max(0.5, min(2.0, range_factor)),
        stickiness=max(-0.8, min(0.8, stickiness)),
        novelty_bonus=max(0.0, novelty),
        anchored_bias=max(-0.5, min(0.5, anchored)),
    )


def _age_weight(changed_at: str, now: datetime.datetime) -> float:
    """行的 EWMA 权重 ``0.5 ** (距 now 天数 / 半衰期)``；无时间戳返回 0。"""
    try:
        ts = datetime.datetime.fromisoformat(str(changed_at))
    except (TypeError, ValueError):
        return 0.0
    age_days = max(0.0, (now - ts).total_seconds() / 86400.0)
    return 0.5 ** (age_days / _HALF_LIFE_DAYS)


class SpatialProfileService:
    """区域权重树学习器（用法与 ``RhythmService`` 同型）。

        spatial = SpatialProfileService().learn_from_evolution(state.evolution)
        factor = spatial.region_factor("1e5_5e3_1e2@*!TH-GSK1-East")

    节点键为地域元组：世界观 ``(w,)``、一级 ``(w, r0)``、二级 ``(w, r0, r1)``。
    """

    def __init__(self):
        self._weights: Dict[tuple, float] = {}
        self._total = 0.0

    def learn_from_evolution(self, evolution: list,
                             now: Optional[datetime.datetime] = None
                             ) -> "SpatialProfileService":
        """从演化表学习。带 ``position`` 的行即到访证据（不限步进正负——
        非故事行不写 position，写了就代表角色确实在那里）。"""
        now = now or datetime.datetime.now()
        self._weights = {}
        self._total = 0.0
        for ev in evolution or []:
            pos = (getattr(ev, "position", "") or "").strip()
            if not pos:
                continue
            addr = parse_full(pos)
            if addr is None or not addr.world:
                continue
            weight = _age_weight(getattr(ev, "changed_at", ""), now)
            if weight <= 0.0:
                continue
            key: tuple = ()
            for level in range(3):
                region = addr.world if level == 0 else addr.regions[level - 1]
                if not region:
                    break
                key = key + (region,)
                self._weights[key] = self._weights.get(key, 0.0) + weight
            self._total += weight
        return self

    def has_data(self) -> bool:
        return self._total > 0.0

    def visited_worlds(self) -> set:
        return {k[0] for k in self._weights if len(k) == 1}

    def visit_weight(self, address_text: str) -> float:
        """地址最深可学习节点（世界观/一级/二级）上的 EWMA 加权到访量。

        更细的候选（三级单元）落在已到访分支上即视为已知区域，取其二级
        节点权重；返回 0 表示该分支从未到访（新颖加成的判定依据）。
        """
        addr = parse_full(address_text)
        if addr is None or not addr.world:
            return 0.0
        key: tuple = ()
        weight = 0.0
        for level in range(3):
            region = addr.world if level == 0 else addr.regions[level - 1]
            if not region:
                break
            key = key + (region,)
            weight = self._weights.get(key, 0.0)
        return weight

    def _sibling_count(self, parent_key: tuple, query_key: tuple) -> int:
        """同父兄弟数：已到访子节点数，查询节点未到访时再 +1（均匀先验
        也要给未知分支留一份质量）。"""
        depth = len(query_key)
        n = sum(1 for key in self._weights
                if len(key) == depth and key[:-1] == parent_key)
        if query_key not in self._weights:
            n += 1
        return max(1, n)

    def _level_factor(self, key: tuple, parent_weight: float, siblings: int) -> float:
        """单层调制 = 平滑份额 × 兄弟数；均匀分布 → 恰好 1.0。"""
        if siblings <= 1 or parent_weight + _SMOOTHING_K <= 0:
            return 1.0
        own = self._weights.get(key, 0.0)
        share = (own + _SMOOTHING_K / siblings) / (parent_weight + _SMOOTHING_K)
        return share * siblings

    def region_factor(self, address_text: str) -> float:
        """候选地址的重访调制因子（围绕 1.0；无数据/无法解析返回 1.0）。

        逐层取调制的连乘：常去分支 > 均匀 > 长期不去的分支；夹取防止单点
        爆炸或归零。
        """
        if not self.has_data():
            return 1.0
        addr = parse_full(address_text)
        if addr is None or not addr.world:
            return 1.0
        factor = 1.0
        parent_key: tuple = ()
        parent_weight = self._total
        for level in range(3):
            region = addr.world if level == 0 else addr.regions[level - 1]
            if not region:
                break
            key = parent_key + (region,)
            siblings = self._sibling_count(parent_key, key)
            factor *= self._level_factor(key, parent_weight, siblings)
            parent_key = key
            parent_weight = self._weights.get(key, 0.0)
        return max(_MIN_FACTOR, min(_MAX_FACTOR, factor))


@dataclass
class SpatialProfile:
    """角色空间档案的装配结果：先验参数 + 学习层 + 手工覆盖层。

    ``region_factor`` 只合并学习层与先验层（粘性放大偏离、新颖加成未到访
    分支）；手工覆盖层不并入数值——命中与否（``in_activity_region``）的
    权重语义留给消费方（P4a 调度器），与现行规划器"只调顺序不改可选集"
    的软偏好兼容。
    """
    priors: SpatialPriors = field(default_factory=SpatialPriors)
    activity_regions: List[str] = field(default_factory=list)
    learned: Optional[SpatialProfileService] = None

    def region_factor(self, address_text: str) -> float:
        svc = self.learned
        if svc is None or not svc.has_data():
            return 1.0
        base = svc.region_factor(address_text)
        if self.priors.stickiness:
            base = 1.0 + (base - 1.0) * (1.0 + self.priors.stickiness)
        if self.priors.novelty_bonus > 0 and svc.visit_weight(address_text) <= 0.0:
            base *= 1.0 + self.priors.novelty_bonus
        return max(_MIN_FACTOR, min(_MAX_FACTOR, base))

    def in_activity_region(self, address_text: str) -> bool:
        """手工活动区域是否覆盖候选地址（软偏好最高层，坏文本不命中）。"""
        if not address_text:
            return False
        return any(r.strip() and covers(r, address_text)
                   for r in (self.activity_regions or []))


def build_spatial_profile(state) -> SpatialProfile:
    """从角色快照装配空间档案：演化表学习 + TAG 先验 + 活动区域透传。

    ``state`` 只需具备 evolution / selected_tags / activity_regions 三个
    属性（``CharacterSnapshot`` 或等价鸭子类型均可）。
    """
    learned = SpatialProfileService().learn_from_evolution(
        getattr(state, "evolution", None) or [])
    return SpatialProfile(
        priors=priors_for_tags(getattr(state, "selected_tags", None) or []),
        activity_regions=[r for r in (getattr(state, "activity_regions", None) or [])
                          if isinstance(r, str) and r.strip()],
        learned=learned,
    )
