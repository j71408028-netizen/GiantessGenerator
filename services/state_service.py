import datetime
from typing import Optional, Tuple

from models import CharacterSnapshot
from behavior_runtime import behavior_hook


class StateService:
    """角色状态（坐标=介入度/破坏性、行动点数）操作的统一入口。

    坐标方法分两层：纯数值运算（clamp/shift/advance/decayed_coordinates，
    接受任意状态来源的数值，报告生成等字典流程也可直接使用），
    以及快照级操作（传入 CharacterSnapshot，有变化时向演化表追加一行）。
    行动点数的消耗/恢复/返还同样统一在此。

    离线结算（角色下线期间的步进/伤亡/明细）已拆分到
    :class:`OfflineService`，本类的 :meth:`recover_evolution` 保留
    静态钩子入口并委托给它的实例方法。
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
                            step: float,
                            step_intrusion: Optional[float] = None,
                            step_destruction: Optional[float] = None,
                            ) -> Tuple[float, float]:
        """按故事步进推进坐标：坐标 += 步进 × 步长。

        未传入显式步长时沿用性格步长（旧行为，不演化步长）；
        传入显式步长（角色已演化的当前步长）时按该步长推进，
        供报告/副本等“步长随故事演化”的流程使用。调用方需自行
        在演算前后调用 evolve_step_rates / evolve_step_rates_after。
        """
        if personality is None:
            return intrusion, destruction
        si = personality.step_intrusion if step_intrusion is None else step_intrusion
        sd = personality.step_destruction if step_destruction is None else step_destruction
        return StateService.shift_coordinates(
            intrusion, destruction, si * step, sd * step)

    @staticmethod
    @behavior_hook("StateService", "evolve_step_rates")
    def evolve_step_rates(personality, step_intrusion: float, step_destruction: float,
                          step: float) -> Tuple[float, float]:
        """按故事步进演化介入度/破坏性步长。

        规则分两步（在演化坐标前后分别调用）：
        - 演算坐标前：步长向初始值恢复 0.2 * 个性强度 的比例差值：
            current += (init - current) * 0.2 * skip_base_prob
        - 演算坐标后：步长减去 步进 × 性格值：
            step_intrusion -= step * sensitivity
            step_destruction -= step * gravity
        调用方在演化坐标前调用本方法得到恢复后的步长，演算坐标后
        再以同样的步进取扣除（见 evolve_step_rates_after）。
        """
        if personality is None:
            return step_intrusion, step_destruction
        strength = getattr(personality, "skip_base_prob", 3.0)
        ratio = 0.2 * strength
        step_intrusion = step_intrusion + (
            personality.step_intrusion - step_intrusion) * ratio
        step_destruction = step_destruction + (
            personality.step_destruction - step_destruction) * ratio
        return step_intrusion, step_destruction

    @staticmethod
    @behavior_hook("StateService", "evolve_step_rates_after")
    def evolve_step_rates_after(personality, step_intrusion: float,
                                step_destruction: float,
                                step: float) -> Tuple[float, float]:
        """演算坐标后按步进扣除步长：步长 -= 步进 × 性格值。"""
        if personality is None:
            return step_intrusion, step_destruction
        return (step_intrusion - step * personality.sensitivity,
                step_destruction - step * getattr(personality, "gravity", 0.0))

    @staticmethod
    @behavior_hook("StateService", "apply_landmark_switch")
    def apply_landmark_switch(personality, intrusion: float, destruction: float,
                              to_frequency: str) -> Tuple[float, float]:
        """地标切换（含首次匹配）时的性格调整。
        切换到 common：介入度 + 敏感值；切换到 unique：介入度 - 敏感值。
        破坏性达到 3 以上时：切换到 unique 破坏性 + 重力，切换到 common 破坏性 - 重力。
        """
        if personality is None:
            return intrusion, destruction

        destruction_switch_threshold = 3.0  # 破坏性达到该值后，地标切换才影响破坏性
        sensitivity = personality.sensitivity
        gravity = getattr(personality, "gravity", 0.0)
        to_unique = to_frequency == "unique"
        intrusion += -sensitivity if to_unique else sensitivity
        if destruction >= destruction_switch_threshold:
            destruction += gravity if to_unique else -gravity

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
        介入度步进取 -1 - 0.5 * 性格敏感值，破坏性步进取 -1 - 0.5 * 性格重力，
        无限制地每次应用，仅受 0.5~4.5 边界约束；
        同时按步长演化规则恢复/扣除介入度与破坏性步长；
        有变化时向演化表追加一行，行的步进取两种步进之和。
        """
        if state.action_points >= 50:
            return
        personality = state.personality
        if personality is None:
            return
        intrusion_step = -1.0 - 0.5 * personality.sensitivity
        destruction_step = -1.0 - 0.5 * getattr(personality, "gravity", 0.0)
        # 演算坐标前：步长向初始值恢复
        si = state.current_step_intrusion
        sd = state.current_step_destruction
        si, sd = StateService.evolve_step_rates(personality, si, sd, 0.0)
        intrusion, destruction = StateService.shift_coordinates(
            state.intrusion, state.destruction,
            si * intrusion_step, sd * destruction_step)
        # 演算坐标后：各分量按自身步进扣除 步进×性格值
        si = si - intrusion_step * personality.sensitivity
        sd = sd - destruction_step * getattr(personality, "gravity", 0.0)
        state.step_intrusion = si
        state.step_destruction = sd
        if (intrusion != state.intrusion or destruction != state.destruction
                or si != state.current_step_intrusion
                or sd != state.current_step_destruction):
            state.record_change(step=intrusion_step + destruction_step,
                                intrusion=intrusion,
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
        """按加载间隔时长恢复：每分钟恢复行动点、坐标衰减，
        并结算离线期间的步进与伤亡。

        离线结算逻辑与参数已拆分到 :class:`OfflineService`（实例方法
        :meth:`OfflineService.recover_offline`），此处仅保留行为钩子
        兼容入口并委托；行为包仍可按 ``"StateService.recover_evolution"``
        覆盖整套恢复流程。
        """
        from services.character_service.offline import OfflineService
        OfflineService().recover_offline(state, now)

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
