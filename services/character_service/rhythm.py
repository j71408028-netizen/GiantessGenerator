"""角色作息模式学习与 72 小时外推预测。

从角色的演化表（每次在线操作会追加一行，含 changed_at 时间戳与
step 步进权重）学习“角色作息模式”：按 (星期几, 2小时桶) 聚合出
7×12 的活动强度网格，归一化为围绕 1.0 的调制因子（角色作息，
非“用户操作模式”），并外推出未来 72 小时的 2 小时精度桶列表。

设计取舍（轻量优先）：
- 只用标准库，0 依赖；
- EWMA + 收缩（borrowing strength）：样本少时向全局平均收缩，
  避免 7×12 网格稀疏导致某桶爆炸或归零；
- 桶权重下限 0.15：白天不归零，夜晚保留微弱基线——这是“角色作息”
  而非“用户操作模式”（用户深夜可能完全不动，角色仍有自身节奏）；
- 天到天变化由星期维度承载，未来 72h = 星期循环，天然无外推爆炸。
"""

import datetime
import math
from typing import List, Optional, Tuple

# 2 小时桶数（一天 24h / 2h）
_BUCKETS_PER_DAY = 12
_BIN_HOURS = 2.0
# 一周天数
_DAYS_PER_WEEK = 7
# 桶权重下限：角色作息基线（不归零）
_MIN_WEIGHT = 0.15
# 收缩强度：样本总数低于该值时向全局平均收缩（数据越少收缩越强）
_SHRINK_K = 6.0


class RhythmService:
    """角色作息模式学习器。

    用法：
        rhythm = RhythmService()
        rhythm.learn_from_evolution(state.evolution)
        forecast = rhythm.forecast(now)   # [(start_at, end_at, factor), ...]
    """

    def __init__(self):
        # 7×12 网格：每个桶累加 (样本数, 步进和)。
        # weekday: 0=周一 ... 6=周日（与 datetime.weekday() 一致）
        self._counts = [[0.0] * _BUCKETS_PER_DAY for _ in range(_DAYS_PER_WEEK)]
        self._sums = [[0.0] * _BUCKETS_PER_DAY for _ in range(_DAYS_PER_WEEK)]
        self._learned = False

    # ==================== 学习 ====================

    def learn_from_evolution(self, evolution: list) -> "RhythmService":
        """从演化表学习作息强度。

        每个在线操作（step>0 且带时间戳的行）作为一次活动样本，
        计入 (weekday, 2h桶) 网格。负数/零步进不参与（非活动）。
        """
        self._counts = [[0.0] * _BUCKETS_PER_DAY for _ in range(_DAYS_PER_WEEK)]
        self._sums = [[0.0] * _BUCKETS_PER_DAY for _ in range(_DAYS_PER_WEEK)]
        for ev in evolution:
            step = getattr(ev, "step", None)
            if step is None or not step > 0:
                continue
            ts = getattr(ev, "changed_at", None)
            if not ts:
                continue
            try:
                dt = datetime.datetime.fromisoformat(str(ts))
            except (TypeError, ValueError):
                continue
            weekday = dt.weekday()
            bucket = self._bucket_of(dt)
            self._counts[weekday][bucket] += 1.0
            self._sums[weekday][bucket] += float(step)
        self._learned = True
        return self

    def _bucket_of(self, dt: datetime.datetime) -> int:
        """2 小时桶下标：桶边界对齐到偶数整点（与离线明细同规则）。"""
        return (dt.hour // 2) % _BUCKETS_PER_DAY

    def _learned_weights(self) -> List[List[float]]:
        """返回 7×12 的已学习权重网格（未学习的桶回退默认昼夜曲线）。

        学习到的”作息偏好“（相对整体活跃度的比值）乘在默认昼夜先验上：
        - 某桶样本越多越信局部均值（真实作息），越少越信全局；
        - 偏好 >1 表示该时段比整体更活跃（如晚上 20-22 点操作多）；
        - 再乘昼夜先验保证白天高夜晚低、且从不归零（角色作息，非用户操作模式）。
        """
        weights = [[_MIN_WEIGHT] * _BUCKETS_PER_DAY for _ in range(_DAYS_PER_WEEK)]

        total_samples = sum(self._counts[w][b]
                            for w in range(_DAYS_PER_WEEK)
                            for b in range(_BUCKETS_PER_DAY))
        if total_samples <= 0:
            # 无历史：退化为纯昼夜曲线（兼容现状）
            for w in range(_DAYS_PER_WEEK):
                for b in range(_BUCKETS_PER_DAY):
                    weights[w][b] = self._default_day_night(b)
            return weights

        # 加权全局期望：按样本分布求平均步进（分母），桶均值与之比较得偏好
        total_sum = sum(self._sums[w][b]
                        for w in range(_DAYS_PER_WEEK)
                        for b in range(_BUCKETS_PER_DAY))
        global_mean = total_sum / total_samples

        for w in range(_DAYS_PER_WEEK):
            for b in range(_BUCKETS_PER_DAY):
                cnt = self._counts[w][b]
                sm = self._sums[w][b]
                local_mean = (sm / cnt) if cnt > 0 else global_mean
                # 收缩：样本越多越信局部，越少越信全局
                shrink = _SHRINK_K / (_SHRINK_K + cnt)
                pref = (1.0 - shrink) * local_mean + shrink * global_mean
                # 归一化：偏好 = 收缩后强度 / 全局均值（>1 活跃 / <1 沉寂）
                norm = pref / global_mean if global_mean > 0 else 1.0
                # 乘以昼夜先验后夹取：白天不归零、上限防止单桶爆炸
                base = self._default_day_night(b)
                weights[w][b] = max(_MIN_WEIGHT, min(2.5, base * norm))
        return weights

    def _default_day_night(self, bucket: int) -> float:
        """默认昼夜曲线（无历史时的回退/先验）：白天高、夜晚低，且不归零。

        与旧 sin 周期同构：6–18 点为 sin 钟形（正午最高）、夜间平滑回落，
        凌晨 2-4 点为最低谷；整体下限 0.6（角色作息永不静默）。
        """
        hour = bucket * 2  # 桶起始小时
        if 6 <= hour < 18:
            # sin 钟形：6→1.0, 12→2.0, 18→1.0
            return 1.0 + math.sin(math.pi * (hour - 6) / 12.0)
        # 夜间：从傍晚 18 点起平滑回落到凌晨 2-4 点谷底（0.6），再回升
        if hour >= 18:
            t = (hour - 18) / 8.0   # 18→0, 26→1
            return 1.0 - 0.4 * t    # 18→1.0, 2点→0.6
        # hour < 6：凌晨 0-6 点，0点→0.7, 2-4点→0.6, 6点→1.0
        t = hour / 6.0
        return 0.6 + 0.4 * t        # 0点→0.6, 6点→1.0

    # ==================== 外推 ====================

    def factor_at(self, dt: datetime.datetime) -> float:
        """查询某时刻的作息调制因子（按 weekday×2h桶 直接查矩阵）。

        离线明细桶逐段调用（无需对齐外推列表边界），精确匹配时段。
        """
        weekday = dt.weekday()
        bucket = self._bucket_of(dt)
        return self._learned_weights()[weekday][bucket]

    def forecast(self, now: Optional[datetime.datetime] = None,
                 horizon_hours: float = 72.0) -> List[Tuple[str, str, float]]:
        """外推 anchor 之后 horizon_hours（默认 72h）的 2 小时作息桶。

        返回 [(start_at, end_at, enhance_factor), ...]：
        - start_at/end_at 为本地 ISO 时间，对齐偶数整点（与离线明细同规则）；
        - enhance_factor 乘到离线基础步进率上（>1 增强 / <1 减弱），
          已包含归一化（围绕 1.0）与星期维度变化。
        """
        if not self._learned:
            self.learn_from_evolution([])  # 触发默认曲线初始化
        now = now or datetime.datetime.now()
        weights = self._learned_weights()

        # 起点对齐到偶数整点（外推从 anchor 起，未来整段对齐）
        start = now.replace(minute=0, second=0, microsecond=0)
        if start < now or start.hour % 2:
            start += datetime.timedelta(hours=1)
        if start.hour % 2:
            start += datetime.timedelta(hours=1)

        out: List[Tuple[str, str, float]] = []
        cursor = start
        end = start + datetime.timedelta(hours=horizon_hours)
        while cursor < end:
            nxt = cursor + datetime.timedelta(hours=_BIN_HOURS)
            if nxt > end:
                break
            weekday = cursor.weekday()
            bucket = self._bucket_of(cursor)
            out.append((cursor.isoformat(), nxt.isoformat(),
                        weights[weekday][bucket]))
            cursor = nxt
        return out


def build_rhythm_forecast(evolution: list,
                          now: Optional[datetime.datetime] = None,
                          horizon_hours: float = 72.0
                          ) -> List[Tuple[str, str, float]]:
    """便捷入口：从演化表一键求出未来 72h 的作息外推。"""
    return (RhythmService()
            .learn_from_evolution(evolution)
            .forecast(now, horizon_hours))