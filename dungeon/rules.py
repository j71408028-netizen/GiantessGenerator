import random
from typing import Optional

from .models import DungeonState, DungeonTextType


class EvolutionRules:
    """副本文本选择、属性演化和条件判断规则。"""

    DEFAULT_TRANSITION_MATRIX = {
        "background": {"background": 0.4, "branch": 0.3, "dialog": 0.2, "interaction": 0.1, "action": 0.0},
        "branch": {"background": 0.2, "branch": 0.3, "dialog": 0.3, "interaction": 0.2, "action": 0.0},
        "dialog": {"background": 0.1, "branch": 0.2, "dialog": 0.3, "interaction": 0.3, "action": 0.1},
        "interaction": {"background": 0.1, "branch": 0.1, "dialog": 0.2, "interaction": 0.4, "action": 0.2},
        "action": {"background": 0.2, "branch": 0.1, "dialog": 0.1, "interaction": 0.2, "action": 0.4},
    }

    def __init__(self, transition_matrix: Optional[dict] = None,
                 step_overrides: Optional[dict] = None):
        self.transition_matrix = transition_matrix or {
            key: value.copy() for key, value in self.DEFAULT_TRANSITION_MATRIX.items()
        }
        self.step_overrides = (step_overrides or {}).copy()
        for text_type in DungeonTextType:
            self.step_overrides.setdefault(text_type.value, text_type.step_value)

    def get_next_text_type(self, current_type: Optional[DungeonTextType]) -> DungeonTextType:
        if current_type is None:
            return random.choice(list(DungeonTextType))
        probabilities = self.transition_matrix.get(current_type.value)
        if not probabilities:
            return random.choice(list(DungeonTextType))
        types = [DungeonTextType(value) for value in probabilities]
        return random.choices(types, weights=list(probabilities.values()))[0]

    def evolve_attributes(self, state: DungeonState, text_type: DungeonTextType,
                          direction: int, personality,
                          is_interaction_chosen: bool = False,
                          custom_attrs_def: Optional[list[dict]] = None,
                          custom_directions: Optional[dict[str, int]] = None,
                          sensitivity_mods: Optional[dict[str, float]] = None) -> DungeonState:
        new_state = state.clone()
        step = self.step_overrides.get(text_type.value, text_type.step_value)
        sensitivity_mods = sensitivity_mods or {}

        intrusion_rate = personality.step_intrusion
        destruction_rate = personality.step_destruction
        intrusion_delta = (direction + sensitivity_mods.get("介入度", 0.0)) * intrusion_rate * step
        destruction_delta = (direction + sensitivity_mods.get("破坏性", 0.0)) * destruction_rate * step
        if is_interaction_chosen:
            intrusion_delta += direction * personality.sensitivity
            destruction_delta += direction * personality.sensitivity
        new_state.intrusion = max(0.0, min(5.0, new_state.intrusion + intrusion_delta))
        new_state.destruction = max(0.0, min(5.0, new_state.destruction + destruction_delta))

        for attr_def in custom_attrs_def or []:
            name = attr_def["name"]
            rate = attr_def.get("rate", 1.0)
            offset = attr_def.get("random_offset", 0.0)
            attr_direction = (custom_directions or {}).get(name, direction)
            delta = (attr_direction + sensitivity_mods.get(name, 0.0)) * step * rate
            if is_interaction_chosen:
                delta += direction * personality.sensitivity * random.uniform(1 - offset, 1 + offset)
            new_state.custom_attrs[name] = new_state.custom_attrs.get(name, 0.0) + delta

        new_state.total_steps += 1
        new_state.steps_since_trigger += 1
        return new_state

    @staticmethod
    def evaluate_condition(condition: dict, state: DungeonState) -> bool:
        rules = condition.get("rules", []) if condition else []
        if not rules:
            return True
        fixed_values = {
            "介入度": state.intrusion,
            "破坏性": state.destruction,
            "总伤亡": state.total_casualties,
            "总计数": state.total_steps,
            "间隔计数": state.steps_since_trigger,
        }
        results = []
        for rule in rules:
            key = rule.get("key")
            current = fixed_values.get(key, state.custom_attrs.get(key, 0.0))
            expected = rule.get("value", 0)
            results.append({
                ">=": current >= expected,
                "<=": current <= expected,
                ">": current > expected,
                "<": current < expected,
                "==": current == expected,
                "!=": current != expected,
            }.get(rule.get("comparator", ">="), False))
        if condition.get("operator", "and") == "and":
            return all(results)
        if condition.get("operator") == "or":
            return any(results)
        return False


class TriggerRules:
    """触发器条件的领域判断，不执行具体动作。"""

    @staticmethod
    def evaluate(condition, state, trigger_choices=None):
        trigger_choices = trigger_choices or {}
        rules = condition.get("rules", []) if condition else []
        if not rules:
            return True

        results = []
        for rule in rules:
            key = rule.get("key")
            comparator = rule.get("comparator", ">=")
            expected = rule.get("value", 0)
            if key and key.startswith("选择:"):
                choices = trigger_choices.get(key[3:], [])
                metric = rule.get("metric", "count")
                target = rule.get("target")
                if metric == "count" and target is None and comparator in ("==", "!="):
                    # 兼容旧格式：== / != 判断是否选过某编号
                    try:
                        expected = int(expected)
                    except (TypeError, ValueError):
                        pass
                    results.append(expected in choices if comparator == "==" else expected not in choices)
                    continue
                if metric == "ratio":
                    if target is None or not choices:
                        results.append(False)
                        continue
                    current = choices.count(target) / len(choices)
                elif metric == "trend":
                    try:
                        window = int(rule.get("window", 5) or 5)
                    except (TypeError, ValueError):
                        window = 5
                    window = max(2, window)
                    if target is None or len(choices) < 2 * window:
                        results.append(False)
                        continue
                    recent = choices[-window:]
                    earlier = choices[:len(choices) - window]
                    current = recent.count(target) / window - earlier.count(target) / len(earlier)
                elif metric == "last":
                    if not choices:
                        results.append(False)
                        continue
                    current = choices[-1]
                else:  # count（默认）
                    current = choices.count(target) if target is not None else len(choices)
            elif key == "伤亡数组":
                series = list(getattr(state, "casualty_evolution", None) or [])
                metric = rule.get("metric", "count")
                if metric == "avg":
                    if not series:
                        results.append(False)
                        continue
                    current = sum(series) / len(series)
                elif metric == "trend":
                    try:
                        window = int(rule.get("window", 5) or 5)
                    except (TypeError, ValueError):
                        window = 5
                    window = max(2, window)
                    if len(series) < 2 * window:
                        results.append(False)
                        continue
                    recent = series[-window:]
                    earlier = series[:len(series) - window]
                    current = sum(recent) / window - sum(earlier) / len(earlier)
                elif metric == "last":
                    if not series:
                        results.append(False)
                        continue
                    current = series[-1]
                else:  # count（默认）
                    current = len(series)
            else:
                values = {
                    "介入度": state.intrusion,
                    "破坏性": state.destruction,
                    "总伤亡": state.total_casualties,
                    "总计数": state.total_steps,
                    "间隔计数": state.steps_since_trigger,
                }
                current = values.get(key, state.custom_attrs.get(key, 0.0))
            try:
                if comparator not in ("==", "!="):
                    expected = float(expected)
            except (TypeError, ValueError):
                results.append(False)
                continue
            results.append({
                ">=": current >= expected,
                "<=": current <= expected,
                ">": current > expected,
                "<": current < expected,
                "==": current == expected,
                "!=": current != expected,
            }.get(comparator, False))

        operator = condition.get("operator", "and")
        return all(results) if operator == "and" else any(results) if operator == "or" else False
