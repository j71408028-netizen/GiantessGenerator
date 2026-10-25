"""离线恢复服务：角色离线期间的行动点/坐标衰减与步进伤亡结算。

- 离线时长换算（分钟级行动点恢复、坐标回落）；
- 最近 72 小时窗口内的 2 小时精度明细（含环境噪声）；
- 更早离线时长的按天汇总段。

离线步进曲线由 :class:`RhythmService` 从演化表学到的“角色作息模式”
（星期×2小时桶的归一化调制因子）驱动，替代旧的固定昼夜 sin 周期；
退出后 2 小时内的残留活动快速衰减（指数）仍保留在步进率中。

结算分两层：``offline.recover_plan`` 只读取状态、返回
:class:`~core.models.OfflineRecoveryPlan`；``OfflineService`` 负责把计划
写回状态并记录演化。行为包改的是计划，不是执行与持久化。展示文案由
``offline.report_text`` 从结算结果生成，``recover_offline`` 返回该文案。
"""

import datetime
import math
import random
from dataclasses import dataclass
from typing import Optional

from core.behavior_runtime import behavior_const, behavior_hook, get_runtime
from core.logic import compute_casualty
from core.models import CharacterSnapshot, OfflineDetail, OfflineRecoveryPlan
from services.character.rhythm import RhythmService
from services.character.spatial import EnvironmentProfile


# 72h 明细窗口与 2h 精度同时是导出图表、演化表和 UI 的跨模块契约，固定不开放。
_OFFLINE_WINDOW_HOURS = 72.0
_OFFLINE_BIN_HOURS = 2.0

# 可被行为包覆盖的离线模型参数。
_OFFLINE_STEP_PER_HOUR = behavior_const(
    "offline", "model.step_per_hour", 0.01,
    layer="infra", contract="data")
_OFFLINE_EXIT_AMP = behavior_const(
    "offline", "model.exit_amp", 0.05,
    layer="infra", contract="data")
_OFFLINE_EXIT_TAU = behavior_const(
    "offline", "model.exit_tau", 0.9,
    layer="infra", contract="data")
_OFFLINE_RECOVERY_POINTS_PER_MINUTE = behavior_const(
    "offline", "model.recovery_points_per_minute", 0.5,
    layer="infra", contract="data")
_OFFLINE_RECOVERY_DECAY_PER_MINUTE = behavior_const(
    "offline", "model.recovery_decay_per_minute", 0.01,
    layer="infra", contract="data")
_OFFLINE_NOISE_SCALE = behavior_const(
    "offline", "model.noise_scale", 0.1,
    layer="infra", contract="data")
_OFFLINE_OLD_STEP_DIVISOR = behavior_const(
    "offline", "model.old_step_divisor", math.pi,
    layer="infra", contract="data")


@dataclass(frozen=True)
class OfflineModel:
    """一次离线结算的模型参数。

    ``window_hours`` / ``bin_hours`` 是固定导出契约；其余字段由
    ``offline.model.*`` 常量钩子解析，实例构造时也可显式覆盖。
    """

    window_hours: float = _OFFLINE_WINDOW_HOURS
    bin_hours: float = _OFFLINE_BIN_HOURS
    step_per_hour: float = _OFFLINE_STEP_PER_HOUR
    exit_amp: float = _OFFLINE_EXIT_AMP
    exit_tau: float = _OFFLINE_EXIT_TAU
    recovery_points_per_minute: float = _OFFLINE_RECOVERY_POINTS_PER_MINUTE
    recovery_decay_per_minute: float = _OFFLINE_RECOVERY_DECAY_PER_MINUTE
    noise_scale: float = _OFFLINE_NOISE_SCALE
    old_step_divisor: float = _OFFLINE_OLD_STEP_DIVISOR


def _model_value(explicit: Optional[float], key: str) -> float:
    """显式参数优先；否则读取常量钩子的有效值。"""
    if explicit is not None:
        return float(explicit)
    value = get_runtime().resolve_value(key)
    return float(value) if value is not None else 0.0


def _activity_baseline(personality) -> float:
    """性格倾向：由个性强度(skip_base_prob)归一化到 0.1~1.0。"""
    prob = personality.skip_base_prob if personality else 3.0
    return min(1.0, max(0.1, (prob - 1.0) / 4.0))


def _exit_residual_rate(at: datetime.datetime, updated: datetime.datetime,
                        personality, model: OfflineModel) -> float:
    """某时刻退出后残留活动的瞬时步进率（步/小时，指数快衰减）。

    退出后的最初约 2 小时内，角色残留“下线前活动”的惯性；
    之后快速归零。昼夜作息不再在此叠加（见 RhythmService）。
    """
    hours_since_exit = (at - updated).total_seconds() / 3600.0
    tau = max(1e-9, float(model.exit_tau))
    amp = max(0.0, float(model.exit_amp))
    return (_activity_baseline(personality) * amp
            * math.exp(-max(0.0, hours_since_exit) / tau))


def _rhythm_mean(rhythm) -> float:
    """作息调制因子的全局均值：即从演化表学到的平均作息水平。

    用于窗口外（>72h）按天汇总段：无 2h 明细时以平均作息替代
    每日各处调制因子的积分均值（≈1.0）。
    """
    matrix = rhythm._learned_weights()
    vals = [matrix[w][b] for w in range(len(matrix))
            for b in range(len(matrix[w]))]
    return sum(vals) / len(vals) if vals else 1.0


def _detail_bins(begin: datetime.datetime, end: datetime.datetime,
                 bin_hours: float) -> list:
    """把 [begin, end] 切成 ≤2 小时、偶数整点对齐的区间列表 [(s, e)]。"""
    if end <= begin or bin_hours <= 0:
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
            nxt = cursor + datetime.timedelta(hours=bin_hours)
        edge = min(nxt, end)
        if edge > cursor:
            bins.append((cursor, edge))
        cursor = edge
    return bins


def _environment_factor(state, step: float,
                        profile: Optional[EnvironmentProfile] = None
                        ) -> float:
    """本次结算的环境因子（统一口径见 ``EnvironmentProfile``）。

    ``profile`` 由调用方学习一次后传入（离线结算的多个明细桶复用
    同一份样本与距离），缺省时现场学习。
    """
    if profile is None:
        profile = EnvironmentProfile().learn_from_evolution(
            getattr(state, "evolution", None) or [])
    position = (getattr(state, "position", "") or "").strip()
    height = max(1.0, getattr(state, "height", None) or 1.0)
    return profile.env_factor(position, step=step, height=height)


def _offline_noise_std(height: float, noise_scale: float) -> float:
    """环境噪声标准差：与角色身高对数成正比。"""
    return max(0.0, float(noise_scale)) * math.log10(max(1.0, height))


@behavior_hook("offline", "recover_plan", layer="module", contract="strategy")
def build_recover_plan(state: CharacterSnapshot, now: datetime.datetime,
                       model: OfflineModel) -> Optional[OfflineRecoveryPlan]:
    """按加载间隔生成离线恢复计划，不修改 ``state``。

    步进曲线由 :class:`RhythmService` 的作息调制因子（星期×2h桶，归一化
    围绕 1.0）构成；退出后最初约 2 小时叠加快速指数衰减的残留活动。

    明细策略：仅在最近 72 小时窗口内按 2 小时精度记录 step/伤亡；早于
    窗口的部分按“24 小时平滑步进总量×离线天数×作息均值”汇总成一段伤亡。
    环境因子取演化表中的在线沉淀，无沉淀时按位置精确度兜底，并叠加随
    log10(身高) 增大的噪声。

    返回 ``None`` 表示没有可结算的离线时长（时间戳无效或未前进）。
    行为包可整体替换本函数；执行与持久化仍由服务层负责。
    """
    try:
        updated = datetime.datetime.fromisoformat(state.updated_at)
    except (TypeError, ValueError):
        return None
    delta_seconds = (now - updated).total_seconds()
    if delta_seconds <= 0:
        return None
    delta_minutes = delta_seconds / 60.0

    if state.personality is None:
        intrusion = destruction = None
        casualty_destruction = state.destruction
    else:
        from services.state_service import StateService
        intrusion, destruction = StateService.decayed_coordinates(
            state.personality, state.intrusion, state.destruction,
            delta_minutes * max(0.0, float(model.recovery_decay_per_minute)))
        casualty_destruction = destruction

    height = max(1.0, state.height or 1.0)
    window_start = now - datetime.timedelta(hours=model.window_hours)
    detail_begin = max(updated, window_start)
    env_profile = EnvironmentProfile().learn_from_evolution(state.evolution)
    noise_std = _offline_noise_std(height, model.noise_scale)

    # 作息学习：从演化表学到 (星期×2h桶) 的归一化调制因子。
    rhythm = RhythmService().learn_from_evolution(state.evolution)
    step_per_hour = max(0.0, float(model.step_per_hour))

    # ---- 最近 72h 窗口：2h 精度明细（含噪声） ----
    offline_details = []
    detail_step = 0.0
    detail_casualty = 0.0
    for s, e in _detail_bins(detail_begin, now, model.bin_hours):
        mid = s + (e - s) / 2.0
        rate = (_activity_baseline(state.personality)
                * step_per_hour
                * rhythm.factor_at(mid)
                + _exit_residual_rate(mid, updated, state.personality, model))
        step = rate * (e - s).total_seconds() / 3600.0
        env_factor = max(0.1, min(1.5,
                                  _environment_factor(
                                      state, step, env_profile)
                                  + random.gauss(0, noise_std)))
        casualty = compute_casualty(
            height, step, casualty_destruction, "",
            env_factor=env_factor)
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
        rhythm_mean = _rhythm_mean(rhythm)
        divisor = max(1e-9, float(model.old_step_divisor))
        old_step = (_activity_baseline(state.personality)
                    * step_per_hour * 24.0 / divisor
                    * rhythm_mean * old_days)
        old_casualty = compute_casualty(
            height, old_step, casualty_destruction, "",
            env_factor=_environment_factor(
                state, old_step / max(old_days, 1e-9), env_profile))
    else:
        old_step = 0.0
        old_casualty = 0.0

    return OfflineRecoveryPlan(
        action_point_gain=max(
            0, int(delta_minutes
                   * max(0.0, float(model.recovery_points_per_minute)))),
        intrusion=intrusion,
        destruction=destruction,
        details=offline_details,
        step=old_step + detail_step,
        casualties=old_casualty + detail_casualty,
    )


def apply_recover_plan(state: CharacterSnapshot,
                       plan: Optional[OfflineRecoveryPlan]) -> None:
    """把恢复计划写回状态；只由服务层调用，行为包不应直接调用。"""
    if plan is None:
        return

    from services.state_service import StateService
    old_intrusion = state.intrusion
    old_destruction = state.destruction
    old_casualties = state.total_casualties
    old_points = state.action_points

    StateService.receive_action_points(
        state, max(0, int(plan.action_point_gain)))
    if plan.intrusion is None and plan.destruction is None:
        intrusion, destruction = old_intrusion, old_destruction
    else:
        intrusion = (old_intrusion if plan.intrusion is None
                     else plan.intrusion)
        destruction = (old_destruction if plan.destruction is None
                       else plan.destruction)
        intrusion, destruction = StateService.clamp_coordinates(
            intrusion, destruction)
    if plan.details is not None:
        state.offline_details = list(plan.details)

    step = max(0.0, float(plan.step))
    casualty_delta = max(0.0, float(plan.casualties))
    casualties = old_casualties + casualty_delta

    if (intrusion != old_intrusion or destruction != old_destruction
            or casualties != old_casualties
            or state.action_points != old_points):
        state.record_change(step=step, intrusion=intrusion,
                            destruction=destruction, casualties=casualties,
                            source="recover_evolution")


def _elapsed_minutes(state: CharacterSnapshot,
                     now: datetime.datetime) -> float:
    """离线时长（分钟）；时间戳无效时为 0（与空计划一致）。"""
    try:
        updated = datetime.datetime.fromisoformat(state.updated_at)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, (now - updated).total_seconds() / 60.0)


@behavior_hook("offline", "report_text", layer="module", contract="strategy")
def build_offline_report(state: CharacterSnapshot,
                         plan: Optional[OfflineRecoveryPlan],
                         elapsed_minutes: float,
                         action_point_gain: int) -> str:
    """把一次离线结算转成展示文案；``plan`` 为 ``None`` 时返回空串。

    ``elapsed_minutes`` 是离线时长（分钟），``action_point_gain`` 是执行层
    实际恢复的行动点（已受自然回复上限约束）；步进与伤亡增量取自计划。
    """
    if plan is None:
        return ""
    if elapsed_minutes >= 90.0:
        duration = f"{elapsed_minutes / 60.0:.1f} 小时"
    else:
        duration = f"{int(round(elapsed_minutes))} 分钟"
    parts = [f"离线 {duration}"]
    if action_point_gain > 0:
        parts.append(f"行动点恢复 {action_point_gain}")
    if plan.step > 0:
        parts.append(f"步进 {plan.step:.2f}")
    if plan.casualties > 0:
        parts.append(f"新增伤亡 {plan.casualties:.1f}")
    return "，".join(parts)


class OfflineService:
    """离线结算：把角色从 updated_at 到 now 的离线时长换算为状态恢复。

    实例形态：时钟/噪声参数（随机种子、窗口、步进率等）在实例上可配置，
    便于测试；行为包优先覆盖 ``offline.recover_plan``，旧
    ``StateService.recover_evolution`` 保留为兼容入口。环境因子取角色演化表
    中的在线沉淀（``EnvironmentProfile`` 按地址聚合），无沉淀时按位置精确度兜底。
    """

    def __init__(self, window_hours: Optional[float] = None,
                 bin_hours: Optional[float] = None,
                 step_per_hour: Optional[float] = None,
                 exit_amp: Optional[float] = None,
                 exit_tau: Optional[float] = None,
                 recovery_points_per_minute: Optional[float] = None,
                 recovery_decay_per_minute: Optional[float] = None,
                 noise_scale: Optional[float] = None,
                 old_step_divisor: Optional[float] = None):
        self.window_hours = (_OFFLINE_WINDOW_HOURS if window_hours is None
                             else float(window_hours))
        self.bin_hours = (_OFFLINE_BIN_HOURS if bin_hours is None
                          else float(bin_hours))
        self.step_per_hour = _model_value(
            step_per_hour, "offline.model.step_per_hour")
        self.exit_amp = _model_value(exit_amp, "offline.model.exit_amp")
        self.exit_tau = _model_value(exit_tau, "offline.model.exit_tau")
        self.recovery_points_per_minute = _model_value(
            recovery_points_per_minute,
            "offline.model.recovery_points_per_minute")
        self.recovery_decay_per_minute = _model_value(
            recovery_decay_per_minute,
            "offline.model.recovery_decay_per_minute")
        self.noise_scale = _model_value(
            noise_scale, "offline.model.noise_scale")
        self.old_step_divisor = _model_value(
            old_step_divisor, "offline.model.old_step_divisor")

    # ==================== 兼容查询入口 ====================

    def _model(self) -> OfflineModel:
        return OfflineModel(
            window_hours=self.window_hours,
            bin_hours=self.bin_hours,
            step_per_hour=self.step_per_hour,
            exit_amp=self.exit_amp,
            exit_tau=self.exit_tau,
            recovery_points_per_minute=self.recovery_points_per_minute,
            recovery_decay_per_minute=self.recovery_decay_per_minute,
            noise_scale=self.noise_scale,
            old_step_divisor=self.old_step_divisor,
        )

    def _environment_factor(self, state, step: float,
                            profile: Optional[EnvironmentProfile] = None
                            ) -> float:
        """本次结算的环境因子（统一口径见 ``EnvironmentProfile``）。"""
        return _environment_factor(state, step, profile)

    # ==================== 离线结算入口 ====================

    def recover_offline(self, state: CharacterSnapshot,
                        now: Optional[datetime.datetime] = None) -> str:
        """生成恢复计划并写回：每分钟恢复行动点、坐标衰减，结算步进/伤亡。

        ``now`` 为空时使用系统当前时间；恢复后的状态变化会写入演化表
        （record_change），调用方负责持久化。计划生成与计划执行分离：
        ``offline.recover_plan`` 可被行为包整体替换，写回仍由本方法完成。
        返回 ``offline.report_text`` 生成的展示文案，无离线时长时为空串。
        """
        now = now or datetime.datetime.now()
        plan = build_recover_plan(state, now, self._model())
        elapsed_minutes = _elapsed_minutes(state, now)
        before_points = state.action_points
        apply_recover_plan(state, plan)
        return build_offline_report(
            state, plan, elapsed_minutes,
            state.action_points - before_points)
