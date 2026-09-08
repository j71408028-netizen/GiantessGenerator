from dataclasses import dataclass, field
from enum import Enum


class DungeonTextType(Enum):
    """副本文本类型及其默认步进权重。"""

    BACKGROUND = "background"
    BRANCH = "branch"
    DIALOG = "dialog"
    INTERACTION = "interaction"
    ACTION = "action"

    @property
    def step_value(self) -> float:
        return {
            DungeonTextType.BACKGROUND: 0.02,
            DungeonTextType.BRANCH: 0.05,
            DungeonTextType.DIALOG: 0.1,
            DungeonTextType.INTERACTION: 0.2,
            DungeonTextType.ACTION: 0.3,
        }[self]


@dataclass
class DungeonState:
    intrusion: float
    destruction: float
    custom_attrs: dict[str, float] = field(default_factory=dict)
    total_steps: int = 0
    steps_since_trigger: int = 0
    total_casualties: float = 0.0
    casualty_evolution: list[float] = field(default_factory=list)
    # 当前介入度/破坏性步长：随故事步进演化（演算前恢复、演算后扣除），
    # 初始来自性格；None 表示沿用性格初始步长（未演化）。
    step_intrusion: float | None = None
    step_destruction: float | None = None

    @property
    def rate_intrusion(self) -> float:
        """当前介入度步长（None 时解释为 0，调用方负责回退性格值）。"""
        return self.step_intrusion if self.step_intrusion is not None else 0.0

    @property
    def rate_destruction(self) -> float:
        """当前破坏性步长（None 时解释为 0，调用方负责回退性格值）。"""
        return self.step_destruction if self.step_destruction is not None else 0.0

    def clone(self) -> "DungeonState":
        return DungeonState(
            self.intrusion,
            self.destruction,
            self.custom_attrs.copy(),
            self.total_steps,
            self.steps_since_trigger,
            self.total_casualties,
            list(self.casualty_evolution),
            self.step_intrusion,
            self.step_destruction,
        )
