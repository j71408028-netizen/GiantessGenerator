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
                 step_overrides: Optional[dict] = None,
                 step_decay: Optional[object] = None):
        """``transition_matrix`` / ``step_overrides`` 直接来自副本方案配置。

        两者都是**部分覆盖**：配置里出现的行整行替换（行内没写的列按 0 处理，
        作者显式改动一行就意味着这一行要按他的写法走），完全没有的行沿用内置
        默认——这样改一行也不会让其余段落退化成均匀随机。

        ``step_decay``：不适应性衰减函数，由调用方注入。签名与
        ``services.state_service.StateService.decay_step_rates`` 一致
        （``(personality, step_intrusion, step_destruction, step_intr, step_destr,
        intrusion=..., destruction=...) -> (si, sd)``）。领域层不依赖 services 层：
        副本窗口（``window/base.py``）显式注入；未注入时不做边界衰减
        （纯数值模拟可忽略，正式会话必须注入）。
        """
        self.transition_matrix = {
            key: value.copy() for key, value in self.DEFAULT_TRANSITION_MATRIX.items()
        }
        self.step_overrides = {}
        if transition_matrix:
            self._merge_transition_matrix(transition_matrix)
        if step_overrides:
            self._merge_step_overrides(step_overrides)
        for text_type in DungeonTextType:
            self.step_overrides.setdefault(text_type.value, text_type.step_value)
        self.step_decay = step_decay

    def _merge_transition_matrix(self, matrix) -> None:
        """用配置矩阵覆盖默认矩阵：出现的行整行替换，没出现的行保持默认。

        权重统一转 float（手工编辑 JSON 时可能写成字符串），负值按 0 处理；
        未知的行列键跳过——它们已经在校验阶段报过 warning，运行时不应崩溃。
        一整行都没解析出有效权重时保留默认行，避免配置写坏就退化成乱跳。
        """
        known = {text_type.value for text_type in DungeonTextType}
        for row_key, row in matrix.items():
            if not isinstance(row, dict):
                continue
            merged = {}
            for col_key, weight in row.items():
                if col_key not in known:
                    continue
                try:
                    value = float(weight)
                except (TypeError, ValueError):
                    continue
                merged[col_key] = max(0.0, value)
            if merged:
                self.transition_matrix[row_key] = merged

    def _merge_step_overrides(self, overrides) -> None:
        """分节步长同样按需覆盖：只接受正数与已知段落类型。"""
        known = {text_type.value for text_type in DungeonTextType}
        for key, value in overrides.items():
            if key not in known:
                continue
            try:
                step = float(value)
            except (TypeError, ValueError):
                continue
            if step > 0:
                self.step_overrides[key] = step

    def get_next_text_type(self, current_type: Optional[DungeonTextType]) -> DungeonTextType:
        if current_type is None:
            return random.choice(list(DungeonTextType))
        row = self.transition_matrix.get(current_type.value) or {}
        types = []
        weights = []
        for member in DungeonTextType:
            try:
                weight = float(row.get(member.value, 0.0))
            except (TypeError, ValueError):
                continue
            if weight > 0:
                types.append(member)
                weights.append(weight)
        # 整行权重都为 0（或该行缺失）时没有可选后继，回退均匀随机
        if not types:
            return random.choice(list(DungeonTextType))
        return random.choices(types, weights=weights)[0]

    def evolve_attributes(self, state: DungeonState, text_type: DungeonTextType,
                          direction: int, personality,
                          custom_attrs_def: Optional[list[dict]] = None,
                          custom_directions: Optional[dict[str, int]] = None,
                          sensitivity_mods: Optional[dict[str, float]] = None,
                          action_points: Optional[float] = None,
                          step_override: Optional[float] = None) -> DungeonState:
        """按段落类型演化副本属性。

        交互对坐标的操作统一由绑定触发器跳转具有敏感效果的章节实现
        （sensitivity_mods / sensitivity_amount）。

        ``step_override``：覆盖本段步进值（结束章节为 0，即不产生坐标、
        自定义属性与步长变化，仅累计计数）。
        """
        new_state = state.clone()
        step = self.step_overrides.get(text_type.value, text_type.step_value)
        if step_override is not None:
            step = float(step_override)
        sensitivity_mods = sensitivity_mods or {}
        frozen = step_override is not None and float(step_override) == 0.0

        # 步长演化（演算坐标前）：步长向初始值恢复 0.2*个性强度 的比例差值；
        # 未初始化的步长（None）沿用性格初始步长，不参与恢复。
        # 步进为 0（结束章节）时整套属性演化冻结：坐标、自定义属性与步长均不变。
        si = state.step_intrusion if state.step_intrusion is not None else personality.step_intrusion
        sd = state.step_destruction if state.step_destruction is not None else personality.step_destruction
        if not frozen:
            strength = getattr(personality, "skip_base_prob", 3.0)
            ratio = 0.2 * strength
            si = si + (personality.step_intrusion - si) * ratio
            sd = sd + (personality.step_destruction - sd) * ratio

        intrusion_rate = si
        destruction_rate = sd
        intrusion_delta = (direction + sensitivity_mods.get("介入度", 0.0)) * intrusion_rate * step
        destruction_delta = (direction + sensitivity_mods.get("破坏性", 0.0)) * destruction_rate * step
        # 坐标统一夹取 0.5~4.5（与角色坐标边界一致，副本写回不再越界）
        new_state.intrusion = max(0.5, min(4.5, new_state.intrusion + intrusion_delta))
        new_state.destruction = max(0.5, min(4.5, new_state.destruction + destruction_delta))

        for attr_def in custom_attrs_def or []:
            name = attr_def["name"]
            rate = attr_def.get("rate", 1.0)
            attr_direction = (custom_directions or {}).get(name, direction)
            delta = (attr_direction + sensitivity_mods.get(name, 0.0)) * step * rate
            new_state.custom_attrs[name] = new_state.custom_attrs.get(name, 0.0) + delta

        if frozen:
            new_state.step_intrusion = state.step_intrusion
            new_state.step_destruction = state.step_destruction
        else:
            # 步长演化（演算坐标后）：步长 -= 步进 × 敏感/重力，
            # 叠加不适应性衰减（坐标达到 0.5/4.5 后步长向 0 收敛）
            new_state.step_intrusion = si - step * personality.sensitivity
            new_state.step_destruction = sd - step * getattr(personality, "gravity", 0.0)
            if self.step_decay is not None:
                new_state.step_intrusion, new_state.step_destruction = \
                    self.step_decay(
                        personality, new_state.step_intrusion, new_state.step_destruction,
                        step, step,
                        intrusion=new_state.intrusion, destruction=new_state.destruction)

        new_state.total_steps += 1
        new_state.steps_since_trigger += 1
        new_state.chapter_steps += 1
        return new_state


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
                    "节内计数": state.chapter_steps,
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
