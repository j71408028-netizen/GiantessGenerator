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
    # 进入当前章节后的步数；进入/离开章节时清零（条件键“节内计数”）
    chapter_steps: int = 0
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
        """复制状态。

        字段一律按关键字传递：本类是数据类，字段顺序会随功能迭代变化
        （``chapter_steps`` 就是从中间插入的），按位置一一对应会在增删字段
        时静默错位——曾导致伤亡数组被填成步长浮点数，每步结算都抛异常、
        副本无法继续推进。
        """
        return DungeonState(
            intrusion=self.intrusion,
            destruction=self.destruction,
            custom_attrs=self.custom_attrs.copy(),
            total_steps=self.total_steps,
            steps_since_trigger=self.steps_since_trigger,
            chapter_steps=self.chapter_steps,
            total_casualties=self.total_casualties,
            casualty_evolution=list(self.casualty_evolution),
            step_intrusion=self.step_intrusion,
            step_destruction=self.step_destruction,
        )
