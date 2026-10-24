"""离线恢复服务：角色离线期间的行动点/坐标衰减与步进伤亡结算。

- 离线时长换算（分钟级行动点恢复、坐标回落）；
- 最近 72 小时窗口内的 2 小时精度明细（含环境噪声）；
- 更早离线时长的按天汇总段。

离线步进曲线由 :class:`RhythmService` 从演化表学到的“角色作息模式”
（星期×2小时桶的归一化调制因子）驱动，替代旧的固定昼夜 sin 周期；
退出后 2 小时内的残留活动快速衰减（指数）仍保留在步进率中。

解耦动机：离线结算是一整套“时间 + 时钟 + 噪声”模型，独立成类便于
未来挂载配置（随机种子、时区/时钟模拟、窗口参数）而不影响在线指令；
同时把 StateService 收敛为“在线游戏循环”的单一职责。
"""

import datetime
import math
import random
from typing import Optional

from core.models import CharacterSnapshot, OfflineDetail
from core.address_model import cell_width_m, relate
from core.logic import compute_casualty
from services.character.rhythm import RhythmService
from services.character.spatial import HALF_LIFE_DAYS


# 离线恢复参数
_OFFLINE_WINDOW_HOURS = 72.0     # 明细/噪声只覆盖最后 72 小时（导出图表窗口）
_OFFLINE_BIN_HOURS = 2.0         # 2 小时精度，偶数整点对齐
_OFFLINE_STEP_PER_HOUR = 0.01    # 白昼闲逛基础步进率（步/小时）
_OFFLINE_EXIT_AMP = 0.05         # 退出后残留活动的初始幅值（步/小时）
_OFFLINE_EXIT_TAU = 0.9          # 退出后残留活动的快速衰减时间常数（小时）

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


class OfflineService:
    """离线结算：把角色从 updated_at 到 now 的离线时长换算为状态恢复。

    实例形态：时钟/噪声参数（随机种子、窗口、步进率等）在实例上可配置，
    便于测试；``recover_evolution`` 的静态入口由 :class:`StateService`
    委托进来，行为包覆盖键保持不变。环境因子取角色演化表中的在线沉淀
    （按地址聚合，见 ``_environment_factor``），无沉淀时按位置精确度兜底。
    """

    def __init__(self, window_hours: float = _OFFLINE_WINDOW_HOURS,
                 bin_hours: float = _OFFLINE_BIN_HOURS,
                 step_per_hour: float = _OFFLINE_STEP_PER_HOUR,
                 exit_amp: float = _OFFLINE_EXIT_AMP,
                 exit_tau: float = _OFFLINE_EXIT_TAU):
        self.window_hours = window_hours
        self.bin_hours = bin_hours
        self.step_per_hour = step_per_hour
        self.exit_amp = exit_amp
        self.exit_tau = exit_tau

    # ==================== 纯计算辅助 ====================

    @staticmethod
    def _activity_baseline(personality) -> float:
        """性格倾向：由个性强度(skip_base_prob)归一化到 0.1~1.0。"""
        prob = personality.skip_base_prob if personality else 3.0
        return min(1.0, max(0.1, (prob - 1.0) / 4.0))

    def _exit_residual_rate(self, at: datetime.datetime,
                            updated: datetime.datetime,
                            personality) -> float:
        """某时刻退出后残留活动的瞬时步进率（步/小时，指数快衰减）。

        退出后的最初约 2 小时内，角色残留“下线前活动”的惯性；
        之后快速归零。昼夜作息不再在此叠加（见 RhythmService）。
        """
        hours_since_exit = (at - updated).total_seconds() / 3600.0
        return (self._activity_baseline(personality)
                * self.exit_amp
                * math.exp(-max(0.0, hours_since_exit) / self.exit_tau))

    @staticmethod
    def _rhythm_mean(rhythm) -> float:
        """作息调制因子的全局均值：即从演化表学到的平均作息水平。

        用于窗口外（>72h）按天汇总段：无 2h 明细时以平均作息替代
        每日各处调制因子的积分均值（≈1.0）。
        """
        matrix = rhythm._learned_weights()
        vals = [matrix[w][b] for w in range(len(matrix))
                for b in range(len(matrix[w]))]
        return sum(vals) / len(vals) if vals else 1.0

    def _detail_bins(self, begin: datetime.datetime,
                     end: datetime.datetime) -> list:
        """把 [begin, end] 切成 ≤2 小时、偶数整点对齐的区间列表 [(s, e)]。"""
        if end <= begin:
            return []
        bins = []
        cursor = begin
        while cursor < end:
            nxt = cursor.replace(minute=0, second=0, microsecond=0)
            if nxt < cursor:
                nxt += datetime.timedelta(hours=1)
            if nxt.hour % 2:
                nxt += datetime.timedelta(hours=1)
            if nxt <= cursor:
                nxt = cursor + datetime.timedelta(hours=self.bin_hours)
            edge = min(nxt, end)
            if edge > cursor:
                bins.append((cursor, edge))
            cursor = edge
        return bins

    def _evidence_samples(self, state) -> list:
        """从演化表收集环境沉淀样本 [(距离下界, env, 时间权重)]。

        带 position 且带 env_factor 的行是到访环境证据（在线报告收尾写入）；
        距离取样本地址对当前位置 ``relate`` 距离区间的下界（最乐观可达），
        世界观不一致 / 无法解析的行跳过。时间权重与空间档案同用 EWMA
        半衰期（HALF_LIFE_DAYS），老到访的证据逐渐淡出。
        """
        position = (getattr(state, "position", "") or "").strip()
        if not position:
            return []
        rows = [ev for ev in (getattr(state, "evolution", None) or [])
                if (getattr(ev, "position", "") or "").strip()
                and getattr(ev, "env_factor", None) is not None]
        now = datetime.datetime.now()
        samples = []
        for ev in rows[-_MAX_EVIDENCE_ROWS:]:
            rel = relate((ev.position or "").strip(), position)
            if rel.interval is None:
                continue
            try:
                ts = datetime.datetime.fromisoformat(str(ev.changed_at))
            except (TypeError, ValueError):
                continue
            env = float(ev.env_factor)
            if not math.isfinite(env):
                continue
            age_days = max(0.0, (now - ts).total_seconds() / 86400.0)
            samples.append((rel.interval[0],
                            max(_MIN_ENV, min(_MAX_ENV, env)),
                            0.5 ** (age_days / HALF_LIFE_DAYS)))
        return samples

    @staticmethod
    def _settled_env(default: float, samples: list, spread_m: float) -> float:
        """沉淀样本的核加权均值，并按样本总量向默认值收缩。

        距离越近权重越高，展开半径外的样本淡出；证据越少（或越旧）越
        贴近精确度默认值（狄利赫里收缩，与空间档案同构）。无样本时即
        默认值。
        """
        if not samples:
            return default
        weight_sum = env_sum = 0.0
        for dist, env, age_w in samples:
            w = age_w * math.exp(-dist / spread_m)
            weight_sum += w
            env_sum += w * env
        if weight_sum <= 0:
            return default
        mean = env_sum / weight_sum
        shrink = weight_sum / (weight_sum + _EVIDENCE_SMOOTHING)
        return shrink * mean + (1.0 - shrink) * default

    def _environment_factor(self, state, step: float,
                            samples: Optional[list] = None) -> float:
        """本次结算的环境因子（统一口径，参数见模块头注释）。

        - 可信地址（trust 高）：沉淀聚合按 ``spread(步进)`` 展开，步进越高
          附近地址的沉淀参与越多；
        - 不可信地址（trust 低）：文本沉淀仍是基础量，但只取角色所在处
          （最小展开）的沉淀；步进越高，按当前位置单元边长折算的扫掠占比
          越大，精确度默认值加权越多。
        无任何沉淀时两条路都只剩精确度默认值。
        """
        position = (getattr(state, "position", "") or "").strip()
        if samples is None:
            samples = self._evidence_samples(state)
        default = _default_env_by_precision(position)
        if not samples:
            return default
        trust = _geo_trust(position)
        height = max(1.0, getattr(state, "height", None) or 1.0)
        # 步进调制 p∈[0,1]：步进越高扫掠范围越大，同时驱动邻域半径展开与
        # 不可信侧默认值权重；半径以当前单元边长为基底（reach 只作上限），
        # 否则大体型角色低步进时也会把邻址全权重卷进来，步进失去分辨力
        p = min(1.0, max(0.0, step) / _STEP_SATURATION)
        cell = max(1.0, cell_width_m(position))
        spread = min(_REACH_CELL_WIDTHS * height,
                     cell * (1.0 + p * (_SPREAD_STEP_MAX - 1.0)))
        trusted = self._settled_env(default, samples, spread)
        env = trusted
        if trust < 1.0:
            # 不可信侧：文本沉淀只取所在处，默认值按扫掠占比加权
            core = self._settled_env(default, samples, cell)
            sweep = _SWEEP_CAP * p
            env = trust * trusted + (1.0 - trust) * ((1.0 - sweep) * core
                                                     + sweep * default)
        return max(_MIN_ENV, min(_MAX_ENV, env))

    def _offline_noise_std(self, height: float) -> float:
        """环境噪声标准差：与角色身高对数成正比。"""
        return 0.1 * math.log10(max(1.0, height))

    # ==================== 离线结算入口 ====================

    def recover_offline(self, state: CharacterSnapshot,
                        now: Optional[datetime.datetime] = None):
        """按加载间隔时长恢复：每分钟恢复行动点、坐标衰减，
        并结算离线期间的步进与伤亡。

        步进曲线：由 :class:`RhythmService` 从演化表学到的作息调制因子
        （星期×2h桶，归一化围绕 1.0）构成，替代旧固定昼夜 sin 周期；
        退出后最初约 2 小时内叠加快速指数衰减的残留活动。精度 2 小时
        （偶数时间格点）。

        明细策略：仅在最近 72 小时窗口内按 2 小时精度记录 step/伤亡并附加
        到角色状态（环境因子=沉淀环境+噪声，噪声随 log10(身高) 增大）；
        早于窗口的部分按“24 小时平滑步进总量×离线天数×地址环境因子”
        汇总成一段伤亡，只计入演化表总和，不进入明细。

        ``now`` 为空时使用系统当前时间；恢复后的状态变化会写入演化表
        （record_change），调用方负责持久化。
        """
        recovery_points_per_minute = 0.5
        recovery_decay_per_minute = 0.01

        now = now or datetime.datetime.now()

        try:
            updated = datetime.datetime.fromisoformat(state.updated_at)
        except (TypeError, ValueError):
            return
        delta_seconds = (now - updated).total_seconds()
        if delta_seconds <= 0:
            return
        delta_minutes = delta_seconds / 60.0

        old_intrusion = state.intrusion
        old_destruction = state.destruction
        old_casualties = state.total_casualties
        old_points = state.action_points

        # 行动点恢复与坐标衰减委托给 StateService 的在线恢复方法，
        # 避免在离线服务内重复实现自然回复/边界回落语义。
        from services.state_service import StateService
        StateService.receive_action_points(
            state, int(delta_minutes * recovery_points_per_minute))
        intrusion, destruction = StateService.decayed_coordinates(
            state.personality, state.intrusion, state.destruction,
            delta_minutes * recovery_decay_per_minute)

        height = max(1.0, state.height or 1.0)
        window_start = now - datetime.timedelta(hours=self.window_hours)
        detail_begin = max(updated, window_start)
        env_samples = self._evidence_samples(state)
        noise_std = self._offline_noise_std(height)

        # 作息学习：从演化表学到 (星期×2h桶) 的归一化调制因子，
        # 替代旧固定昼夜 sin 周期；每个明细桶按其中点时段的作息取值。
        rhythm = RhythmService().learn_from_evolution(state.evolution)

        # ---- 最近 72h 窗口：2h 精度明细（含噪声） ----
        offline_details = []
        detail_step = 0.0
        detail_casualty = 0.0
        for s, e in self._detail_bins(detail_begin, now):
            mid = s + (e - s) / 2.0
            rate = (self._activity_baseline(state.personality)
                    * self.step_per_hour
                    * rhythm.factor_at(mid)
                    + self._exit_residual_rate(mid, updated, state.personality))
            step = rate * (e - s).total_seconds() / 3600.0
            env_factor = max(0.1, min(1.5,
                                      self._environment_factor(
                                          state, step, env_samples)
                                      + random.gauss(0, noise_std)))
            casualty = compute_casualty(
                height, step, destruction, "", env_factor=env_factor)
            offline_details.append(OfflineDetail(
                start_at=s.isoformat(), end_at=e.isoformat(),
                step=step, casualties=casualty, env_factor=env_factor))
            detail_step += step
            detail_casualty += casualty

        # ---- 窗口之前（仅当离线 >72h）：按天汇总 ----
        old_hours = (detail_begin - updated).total_seconds() / 3600.0
        if old_hours > 0:
            old_days = old_hours / 24.0
            # 窗口外无作息明细，按“24h 平滑步进总量×离线天数×作息均值”汇总
            rhythm_mean = self._rhythm_mean(rhythm)
            old_step = (self._activity_baseline(state.personality)
                        * self.step_per_hour * 24.0 / math.pi
                        * rhythm_mean * old_days)
            old_casualty = compute_casualty(
                height, old_step, destruction, "",
                env_factor=self._environment_factor(
                    state, old_step / max(old_days, 1e-9), env_samples))
        else:
            old_step = 0.0
            old_casualty = 0.0

        state.offline_details = offline_details
        total_step = old_step + detail_step
        total_casualty = old_casualty + detail_casualty
        casualties = old_casualties + total_casualty

        if (intrusion != old_intrusion or destruction != old_destruction
                or casualties != old_casualties
                or state.action_points != old_points):
            state.record_change(step=total_step, intrusion=intrusion,
                                destruction=destruction, casualties=casualties,
                                source="recover_evolution")