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

    def clone(self) -> "DungeonState":
        return DungeonState(
            self.intrusion,
            self.destruction,
            self.custom_attrs.copy(),
            self.total_steps,
            self.steps_since_trigger,
            self.total_casualties,
            list(self.casualty_evolution),
        )
