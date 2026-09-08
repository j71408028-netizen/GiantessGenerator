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

from models import CharacterSnapshot, OfflineDetail
from logic import compute_casualty
from services.character_service.rhythm import RhythmService


# 离线恢复参数
_OFFLINE_WINDOW_HOURS = 72.0     # 明细/噪声只覆盖最后 72 小时（导出图表窗口）
_OFFLINE_BIN_HOURS = 2.0         # 2 小时精度，偶数整点对齐
_OFFLINE_STEP_PER_HOUR = 0.01    # 白昼闲逛基础步进率（步/小时）
_OFFLINE_EXIT_AMP = 0.05         # 退出后残留活动的初始幅值（步/小时）
_OFFLINE_EXIT_TAU = 0.9          # 退出后残留活动的快速衰减时间常数（小时）


class OfflineService:
    """离线结算：把角色从 updated_at 到 now 的离线时长换算为状态恢复。

    实例形态：时钟/噪声参数（随机种子、窗口、步进率等）在实例上可配置，
    便于测试与未来接入真实地址环境数据；``recover_evolution`` 的静态入口
    由 :class:`StateService` 委托进来，行为包覆盖键保持不变。
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

    def _simulate_address_env_factor(self, position: str) -> float:
        """模拟地址环境因子（尚无该字段，用位置哈希模拟）。"""
        if not position:
            return 0.4
        h = abs(hash(position)) % 1000
        return 0.2 + 0.6 * h / 1000.0

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
        到角色状态（环境因子=地址模拟值+噪声，噪声随 log10(身高) 增大）；
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
        base_env = self._simulate_address_env_factor(state.position or "")
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
                                       base_env + random.gauss(0, noise_std)))
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
                height, old_step, destruction, "", env_factor=base_env)
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