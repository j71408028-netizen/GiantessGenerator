from models import CharacterSnapshot
from behavior_runtime import behavior_hook


class StateService:

    @staticmethod
    @behavior_hook("StateService", "apply_negative_evolution")
    def apply_negative_evolution(state: CharacterSnapshot):
        if state.action_points >= 50:
            return
        personality = state.personality
        if personality is None:
            return
        if not state.negative_triggered:
            state.intrusion -= personality.step_intrusion
            state.destruction -= personality.step_destruction
            state.negative_triggered = True
            state.negative_reduction_intrusion = 1.0
            state.negative_reduction_destruction = 1.0
        else:
            if state.negative_reduction_intrusion < 1.0:
                remaining = 1.0 - state.negative_reduction_intrusion
                reduction = min(personality.sensitivity / personality.step_intrusion, remaining)
                state.intrusion -= reduction * personality.step_intrusion
                state.negative_reduction_intrusion += reduction
            if state.negative_reduction_destruction < 1.0:
                remaining = 1.0 - state.negative_reduction_destruction
                reduction = min(personality.sensitivity / personality.step_destruction, remaining)
                state.destruction -= reduction * personality.step_destruction
                state.negative_reduction_destruction += reduction
        state.intrusion = max(0.5, state.intrusion)
        state.destruction = max(0.5, state.destruction)

    @staticmethod
    @behavior_hook("StateService", "consume_action_points")
    def consume_action_points(state: CharacterSnapshot, cost: int) -> bool:
        if state.action_points < cost:
            return False
        state.action_points -= cost
        return True

    @staticmethod
    @behavior_hook("StateService", "calculate_recovery_increment")
    def calculate_recovery_increment(state: CharacterSnapshot) -> int:
        strength = state.personality.skip_base_prob if state.personality else 3.0
        return 1 + int(0.3 * strength)

    @staticmethod
    @behavior_hook("StateService", "apply_step_decay")
    def apply_step_decay(state: CharacterSnapshot, fraction: float = 0.1):
        personality = state.personality
        if personality is None:
            return
        bound_intrusion = personality.init_intrusion + personality.step_intrusion
        bound_destruction = personality.init_destruction + personality.step_destruction
        if personality.step_intrusion > 0:
            if state.intrusion > bound_intrusion:
                state.intrusion = max(bound_intrusion, state.intrusion - fraction * personality.step_intrusion)
        else:
            if state.intrusion < bound_intrusion:
                state.intrusion = min(bound_intrusion, state.intrusion - fraction * personality.step_intrusion)
        if personality.step_destruction > 0:
            if state.destruction > bound_destruction:
                state.destruction = max(bound_destruction, state.destruction - fraction * personality.step_destruction)
        else:
            if state.destruction < bound_destruction:
                state.destruction = min(bound_destruction, state.destruction - fraction * personality.step_destruction)

    @staticmethod
    @behavior_hook("StateService", "recover_action_points")
    def recover_action_points(state: CharacterSnapshot):
        increment = StateService.calculate_recovery_increment(state)
        state.action_points = min(100, state.action_points + increment)
