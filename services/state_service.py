import datetime
from typing import Optional, Tuple

from models import CharacterSnapshot
from behavior_runtime import behavior_hook
from logic import compute_casualty


def _daytime_hours(start: datetime.datetime, end: datetime.datetime) -> float:
    """统计 [start, end) 中落在日间的小时数（按整点分段，不足一小时按比例计）。"""
    day_start_hour = 6  # 日间起点（含）
    night_start_hour = 18  # 夜间起点（含），日间为 [6, 18)
    if end <= start:
        return 0.0
    total = 0.0
    t = start
    while t < end:
        next_t = min(t.replace(minute=0, second=0, microsecond=0)
                     + datetime.timedelta(hours=1), end)
        if day_start_hour <= t.hour < night_start_hour:
            total += (next_t - t).total_seconds() / 3600.0
        t = next_t
    return total


class StateService:
    """角色状态（坐标=介入度/破坏性、行动点数）操作的统一入口。

    坐标方法分两层：纯数值运算（clamp/shift/advance/decayed_coordinates，
    接受任意状态来源的数值，报告生成等字典流程也可直接使用），
    以及快照级操作（传入 CharacterSnapshot，有变化时向演化表追加一行）。
    行动点数的消耗/恢复/返还同样统一在此。
    """

    # ==================== 坐标：纯数值运算 ====================

    @staticmethod
    @behavior_hook("StateService", "clamp_coordinates")
    def clamp_coordinates(intrusion: float, destruction: float) -> Tuple[float, float]:
        """把介入度/破坏性夹取到统一的 0.5~4.5 边界。"""
        return (max(0.5, min(4.5, intrusion)),
                max(0.5, min(4.5, destruction)))

    @staticmethod
    @behavior_hook("StateService", "shift_coordinates")
    def shift_coordinates(intrusion: float, destruction: float,
                          intrusion_delta: float,
                          destruction_delta: float) -> Tuple[float, float]:
        """按原始坐标增量平移坐标（副本结局增量等场景）。"""
        return StateService.clamp_coordinates(
            intrusion + intrusion_delta, destruction + destruction_delta)

    @staticmethod
    @behavior_hook("StateService", "advance_coordinates")
    def advance_coordinates(personality, intrusion: float, destruction: float,
                            step: float) -> Tuple[float, float]:
        """按故事步进推进坐标：坐标 += 步进 × 性格步长。
        步进视场景而定且本身无限制：报告事件取事件的实际步进，
        负向演化取 -1 - 0.5 * 性格敏感值，由调用方传入。
        """
        if personality is None:
            return intrusion, destruction
        return StateService.shift_coordinates(
            intrusion, destruction,
            personality.step_intrusion * step, personality.step_destruction * step)

    @staticmethod
    @behavior_hook("StateService", "apply_landmark_switch")
    def apply_landmark_switch(personality, intrusion: float, destruction: float,
                              to_frequency: str) -> Tuple[float, float]:
        """地标切换（含首次匹配）时的敏感值调整。
        切换到 common：介入度 + 敏感值；切换到 unique：介入度 - 敏感值。
        破坏性达到 3 以上时：切换到 unique 破坏性 + 敏感值，切换到 common 破坏性 - 敏感值。
        """
        if personality is None:
            return intrusion, destruction
        
        destruction_switch_threshold = 3.0  # 破坏性达到该值后，地标切换才影响破坏性
        sensitivity = personality.sensitivity
        to_unique = to_frequency == "unique"
        intrusion += -sensitivity if to_unique else sensitivity
        if destruction >= destruction_switch_threshold:
            destruction += sensitivity if to_unique else -sensitivity

        return StateService.clamp_coordinates(intrusion, destruction)

    @staticmethod
    @behavior_hook("StateService", "decayed_coordinates")
    def decayed_coordinates(personality, intrusion: float, destruction: float,
                            fraction: float) -> Tuple[float, float]:
        """步进衰减的纯计算：仅当坐标越过性格边界（初始值 - 0.5*步长）时向边界回落 fraction 个步长。
        fraction 为本次回落的步进，视场景而定（加载期按离线分钟数、面板逗留每分钟 0.1）；衰减不会越过边界。
        不改状态、不记表。
        """
        if personality is None:
            return intrusion, destruction
        bound_intrusion = personality.init_intrusion - 0.5 * personality.step_intrusion
        bound_destruction = personality.init_destruction - 0.5 * personality.step_destruction
        if personality.step_intrusion > 0:
            if intrusion > bound_intrusion:
                intrusion = max(bound_intrusion,
                                intrusion - fraction * personality.step_intrusion)
        elif personality.step_intrusion < 0:
            if intrusion < bound_intrusion:
                intrusion = min(bound_intrusion,
                                intrusion - fraction * personality.step_intrusion)
        if personality.step_destruction > 0:
            if destruction > bound_destruction:
                destruction = max(bound_destruction,
                                  destruction - fraction * personality.step_destruction)
        elif personality.step_destruction < 0:
            if destruction < bound_destruction:
                destruction = min(bound_destruction,
                                  destruction - fraction * personality.step_destruction)

        return StateService.clamp_coordinates(intrusion, destruction)

    # ==================== 坐标：快照级操作 ====================

    @staticmethod
    @behavior_hook("StateService", "apply_negative_evolution")
    def apply_negative_evolution(state: CharacterSnapshot):
        """行动点数不足（<50）时应用负向演化。
        步进取 -1 - 0.5 * 性格敏感值，无限制地每次应用，仅受 0.5~4.5 边界约束；
        有变化时向演化表追加一行，行的步进取本次负向步进。
        """
        if state.action_points >= 50:
            return
        personality = state.personality
        if personality is None:
            return
        step = -1.0 - 0.5 * personality.sensitivity
        intrusion, destruction = StateService.advance_coordinates(
            personality, state.intrusion, state.destruction, step)
        if intrusion != state.intrusion or destruction != state.destruction:
            state.record_change(step=step, intrusion=intrusion,
                                destruction=destruction,
                                source="apply_negative_evolution")

    @staticmethod
    @behavior_hook("StateService", "apply_step_decay")
    def apply_step_decay(state: CharacterSnapshot, fraction: float = 0.1):
        """步进衰减（角色面板逗留等在线场景）：向性格边界回落 fraction 个步长。
        有变化时向演化表追加一行（步进记 0.0）。
        """
        if state.personality is None:
            return
        intrusion, destruction = StateService.decayed_coordinates(
            state.personality, state.intrusion, state.destruction, fraction)
        if intrusion != state.intrusion or destruction != state.destruction:
            state.record_change(intrusion=intrusion, destruction=destruction,
                                source="apply_step_decay")

    @staticmethod
    @behavior_hook("StateService", "recover_evolution")
    def recover_evolution(state: CharacterSnapshot,
                          now: Optional[datetime.datetime] = None):
        """按加载间隔时长恢复：每分钟恢复少量行动点数并按步进衰减回落坐标。
        衰减比例取离线分钟数 × 每分钟回落比例。
        间隔超出宽限时长（1小时）的部分，按日间每小时 0.01 步进（夜间为 0）
        累积离线步进，并按伤亡公式结算伤亡（伤亡用回落后的破坏性）。
        有任何变化时向演化表追加一行，
        """
        idle_grace_hours = 1  # 不计离线步进的宽限时长（小时）
        idle_step_per_hour = 0.01  # 宽限后日间每小时累积的步进，夜间为 0
        recovery_points_per_minute = 0.5  # 每分钟恢复的行动点数
        recovery_decay_per_minute = 0.01  # 每分钟回落的步进比例（占一个性格步长）

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

        StateService.receive_action_points(
            state, int(delta_minutes * recovery_points_per_minute))
        intrusion, destruction = StateService.decayed_coordinates(
            state.personality, state.intrusion, state.destruction,
            delta_minutes * recovery_decay_per_minute)

        idle_start = updated + datetime.timedelta(hours=idle_grace_hours)
        idle_step = _daytime_hours(idle_start, now) * idle_step_per_hour
        height = max(1.0, state.height or 1.0)
        casualties = old_casualties + compute_casualty(
            height, idle_step, destruction, "", env_factor=0.4)

        if (intrusion != old_intrusion or destruction != old_destruction
                or casualties != old_casualties
                or state.action_points != old_points):
            state.record_change(step=idle_step, intrusion=intrusion,
                                destruction=destruction, casualties=casualties,
                                source="recover_evolution")

    # ==================== 行动点数 ====================

    @staticmethod
    @behavior_hook("StateService", "consume_action_points")
    def consume_action_points(state: CharacterSnapshot, cost: int) -> bool:
        if state.action_points < cost:
            return False
        state.action_points -= cost
        return True

    @staticmethod
    @behavior_hook("StateService", "receive_action_points")
    def receive_action_points(state: CharacterSnapshot, amount: float) -> int:
        """自然回复行动点数（离线恢复、角色面板在线恢复等），夹取到 0~100。

        100 只是自然回复的上限；副本结算等“返还”请用
        :func:`refund_action_points`（无上限）。返回实际变化量。
        """
        old = state.action_points
        state.action_points = max(0, min(100, state.action_points + int(amount)))
        return state.action_points - old

    @staticmethod
    @behavior_hook("StateService", "refund_action_points")
    def refund_action_points(state: CharacterSnapshot, amount: float) -> int:
        """返还行动点数（副本结局返还、报告解锁返还等），不设 100 上限。

        仅保证不低于 0。返回实际变化量。
        """
        old = state.action_points
        state.action_points = max(0, state.action_points + int(amount))
        return state.action_points - old


    @staticmethod
    @behavior_hook("StateService", "recover_action_points")
    def recover_action_points(state: CharacterSnapshot):
        """恢复行动点数（角色面板每分钟在线恢复，自然回复上限 100）。"""
        recovery = int(1 + 0.3 * state.personality.skip_base_prob) if state.personality else 1
        StateService.receive_action_points(state, recovery)
