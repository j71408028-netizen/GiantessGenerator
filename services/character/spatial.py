# -*- coding: utf-8 -*-
"""角色空间档案：TAG 先验 + 到访日志学习的区域权重树（P3a）。

三层结构（优先级从高到低，消费方见 docs/designs/report_selection.md §3）：

1. 手工覆盖层：``CharacterSnapshot.activity_regions`` 原样透传，命中判定用
   ``covers``（与地址规划器现行口径一致）；
2. 学习层：从演化表带 ``position`` 的行学习区域权重树（世界观 → 一级 →
   二级；三级单元过细，作为个体地标对待，不进区域统计）。行权重按时间
   指数衰减（EWMA 半衰期），节点份额做狄利赫里平滑——样本少时向均匀
   分布收缩，长期不到访则整体向 1.0 回落，即"区域偏好随时间衰减"；
3. 先验层：TAG → 空间参数向量（``TAG_SPATIAL_PRIORS`` 数据表）。只有纯
   空间语义的标签入表，姿态/内容类标签留在内容层。

模块同时承载环境轴（``EnvironmentProfile``）：在线报告沉淀的环境证据行
按距离核聚合成地址环境估计，是环境因子的唯一事实来源（离线结算消费）。

与 ``rhythm.py`` 同构（先验 + 借力收缩），差异在特征空间：作息是 7×12
平面网格、用全局均值收缩；地址是层级树、用父节点 + 均匀先验收缩
（n-gram 回退式）。本模块只做学习与装配，不参与选取决策——消费方是
P4a 的角色调度器。
"""

import datetime
import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from core.address_model import cell_width_m, covers, parse_full, relate

# EWMA 半衰期（天）：到访/环境证据权重随时间减半的周期，承担区域偏好与
# 地址环境沉淀的时间衰减（离线结算同用此口径）
HALF_LIFE_DAYS = 14.0
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
    return 0.5 ** (age_days / HALF_LIFE_DAYS)


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
    的软偏好兼容。``env`` 是环境轴（沉淀聚合，见 ``EnvironmentProfile``）。
    """
    priors: SpatialPriors = field(default_factory=SpatialPriors)
    activity_regions: List[str] = field(default_factory=list)
    learned: Optional[SpatialProfileService] = None
    env: Optional["EnvironmentProfile"] = None

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

    def env_estimate(self, address_text: str) -> Optional[float]:
        """候选地址的环境估计（最小展开，无步进/体型调制）。

        返回 ``None`` 表示零证据——选取侧据此零权重（环境软权重中性
        缺席），而不是拿精确度默认值制造伪偏差。
        """
        if not address_text or self.env is None or not self.env.has_data():
            return None
        return self.env.env_factor(address_text)

    def in_activity_region(self, address_text: str) -> bool:
        """手工活动区域是否覆盖候选地址（软偏好最高层，坏文本不命中）。"""
        if not address_text:
            return False
        return any(r.strip() and covers(r, address_text)
                   for r in (self.activity_regions or []))


def build_spatial_profile(state, now: Optional[datetime.datetime] = None
                          ) -> SpatialProfile:
    """从角色快照装配空间档案：演化表学习 + TAG 先验 + 活动区域透传。

    ``state`` 只需具备 evolution / selected_tags / activity_regions 三个
    属性（``CharacterSnapshot`` 或等价鸭子类型均可）；``now`` 透传给
    学习层（缺省用系统当前时间，测试可注入固定时钟）。
    """
    evolution = getattr(state, "evolution", None) or []
    learned = SpatialProfileService().learn_from_evolution(evolution, now=now)
    env = EnvironmentProfile().learn_from_evolution(evolution, now=now)
    return SpatialProfile(
        priors=priors_for_tags(getattr(state, "selected_tags", None) or []),
        activity_regions=[r for r in (getattr(state, "activity_regions", None) or [])
                          if isinstance(r, str) and r.strip()],
        learned=learned,
        env=env,
    )


# ==================== 环境轴：沉淀聚合（EnvironmentProfile） ====================

# 环境因子统一口径：两信源按可信度分治
#   env = trust × 沉淀聚合(展开半径) + (1 - trust) × [沉淀聚合(所在处) ↔ 默认值]
#   trust = 几何可信度（位置最小单元边长）；聚合内部按样本量向默认值收缩
#   （证据越少/越旧越贴近默认值），邻域参与度随步进增长
_MIN_ENV, _MAX_ENV = 0.1, 1.5                    # 统一值域（与明细夹取一致）
_EVIDENCE_SMOOTHING = 1.0                        # 证据收缩伪计数：≈1 行近期证据达半信
_MAX_EVIDENCE_ROWS = 500                         # 参与聚合的最近沉淀行数上限
_REACH_CELL_WIDTHS = 10.0                        # 邻域半径上限 = 10×身高（reach 口径）
_STEP_SATURATION = 1.5                           # 步进达到此值时步进调制饱和（p=1）
_SPREAD_STEP_MAX = 3.0                           # 饱和时邻域半径 = 当前单元边长×此值
_SWEEP_CAP = 0.5                                 # 饱和时不可信侧默认值的最大权重


def _default_env_by_precision(position: str) -> float:
    """按位置精确度给环境因子默认值：越细 → 越可能有人烟，默认值越高。

    有细分申领 ≈ 有人烟，但三级也可以是无人区，故先验差值压窄
    （0.32~0.60），把分辨力留给文本环境沉淀。无法解析/纯世界观取下限。
    """
    width = cell_width_m(position)
    if width <= 0:
        return 0.32
    unit = max(0.0, min(1.0, (4.7 - math.log10(width)) / 3.0))
    return 0.32 + 0.28 * unit


def _geo_trust(position: str) -> float:
    """几何可信度：位置最小单元边长越细，地址值越可信（0.3~1.0）。"""
    width = cell_width_m(position)
    if width <= 0:
        return 0.3
    return max(0.3, min(1.0, 1.2 - 0.18 * math.log10(width)))


class EnvironmentProfile:
    """环境轴学习器：在线沉淀行的距离核聚合（环境因子的唯一事实来源）。

        env = EnvironmentProfile().learn_from_evolution(state.evolution)
        factor = env.env_factor("1e5_5e3_1e2@*!TH-GSK1-East", 0.05, 100.0)

    数据源是演化表中带 ``position`` 且带 ``env_factor`` 的行（在线报告
    收尾写入的到访环境证据）；查询按样本地址对目标地址 relate 距离下界
    做核加权，按证据总量向精确度默认值收缩（狄利赫里，与区域权重树
    同构）。时间权重与区域权重树同用 EWMA 半衰期（``HALF_LIFE_DAYS``），
    老到访的证据逐渐淡出。``learn_from_evolution`` 一次、``env_factor``
    多次：距离在单次查询内只算一遍。
    """

    def __init__(self):
        self._samples: List[tuple] = []   # [(样本地址文本, env, 时间权重)]

    def learn_from_evolution(self, evolution: list,
                             now: Optional[datetime.datetime] = None
                             ) -> "EnvironmentProfile":
        """收集环境沉淀样本。超值域/非有限的沉淀行按统一值域夹取，
        坏时间戳的行整行跳过；只取最近 ``_MAX_EVIDENCE_ROWS`` 行。"""
        now = now or datetime.datetime.now()
        self._samples = []
        rows = [ev for ev in evolution or []
                if (getattr(ev, "position", "") or "").strip()
                and getattr(ev, "env_factor", None) is not None]
        for ev in rows[-_MAX_EVIDENCE_ROWS:]:
            try:
                env = float(ev.env_factor)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(env):
                continue
            try:
                ts = datetime.datetime.fromisoformat(str(ev.changed_at))
            except (TypeError, ValueError):
                continue
            age_days = max(0.0, (now - ts).total_seconds() / 86400.0)
            self._samples.append((
                (getattr(ev, "position", "") or "").strip(),
                max(_MIN_ENV, min(_MAX_ENV, env)),
                0.5 ** (age_days / HALF_LIFE_DAYS)))
        return self

    def has_data(self) -> bool:
        return bool(self._samples)

    def _distances(self, query: str) -> List[tuple]:
        """样本到查询地址的距离下界列表 [(距离, env, 时间权重)]，
        世界观不一致 / 无法解析的样本跳过。"""
        out = []
        for addr, env, age_w in self._samples:
            rel = relate(addr, query)
            if rel.interval is None:
                continue
            out.append((rel.interval[0], env, age_w))
        return out

    @staticmethod
    def _settled(default: float, distances: List[tuple],
                 spread_m: float) -> float:
        """沉淀样本的核加权均值，并按样本总量向默认值收缩。

        距离越近权重越高，展开半径外的样本淡出；证据越少（或越旧）越
        贴近精确度默认值。无有效样本时即默认值。
        """
        if not distances:
            return default
        weight_sum = env_sum = 0.0
        for dist, env, age_w in distances:
            w = age_w * math.exp(-dist / spread_m)
            weight_sum += w
            env_sum += w * env
        if weight_sum <= 0:
            return default
        mean = env_sum / weight_sum
        shrink = weight_sum / (weight_sum + _EVIDENCE_SMOOTHING)
        return shrink * mean + (1.0 - shrink) * default

    def env_factor(self, address_text: str, step: float = 0.0,
                   height: float = 1.0) -> float:
        """目标地址的环境因子（统一口径，参数见模块内常量注释）。

        - 可信地址（trust 高）：沉淀聚合按 ``spread(步进)`` 展开，步进越高
          附近地址的沉淀参与越多；
        - 不可信地址（trust 低）：文本沉淀仍是基础量，但只取目标处
          （最小展开）的沉淀；步进越高，按目标单元边长折算的扫掠占比
          越大，精确度默认值加权越多。
        无任何沉淀时两条路都只剩精确度默认值。
        """
        address_text = (address_text or "").strip()
        default = _default_env_by_precision(address_text)
        if not self._samples:
            return default
        distances = self._distances(address_text)
        if not distances:
            return default
        trust = _geo_trust(address_text)
        height = max(1.0, height or 1.0)
        # 步进调制 p∈[0,1]：步进越高扫掠范围越大，同时驱动邻域半径展开与
        # 不可信侧默认值权重；半径以当前单元边长为基底（reach 只作上限），
        # 否则大体型角色低步进时也会把邻址全权重卷进来，步进失去分辨力
        p = min(1.0, max(0.0, step) / _STEP_SATURATION)
        cell = max(1.0, cell_width_m(address_text))
        spread = min(_REACH_CELL_WIDTHS * height,
                     cell * (1.0 + p * (_SPREAD_STEP_MAX - 1.0)))
        trusted = self._settled(default, distances, spread)
        env = trusted
        if trust < 1.0:
            # 不可信侧：文本沉淀只取所在处，默认值按扫掠占比加权
            core = self._settled(default, distances, cell)
            sweep = _SWEEP_CAP * p
            env = trust * trusted + (1.0 - trust) * ((1.0 - sweep) * core
                                                     + sweep * default)
        return max(_MIN_ENV, min(_MAX_ENV, env))
