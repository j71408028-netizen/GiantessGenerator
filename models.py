from typing import List, Dict, Optional
from dataclasses import dataclass, field, fields, replace
import datetime

@dataclass
class Landmark:
    name: str
    size: float
    dimension: str  # 'vertical' 或 'horizontal'
    frequency: str  # 'unique' 或 'common'
    horizontal_type: Optional[str] = None
    # 注册地址：独特地标补在其“风格注册地址”之下的剩余级数（纯文本，
    # 无世界观时需自带世界观）。空表示未注册（到处可用 / 不参与地址规则）。
    address: str = ""

    def __post_init__(self):
        if self.dimension == "horizontal" and self.horizontal_type is None:
            self.horizontal_type = "length"


@dataclass
class BodyPreset:
    name: str
    # 与垂直和水平地标都对比的部位
    height_ratio: float = 1.0  # 身高 (基准)
    leg_ratio: float = 0.5  # 腿长
    foot_length_ratio: float = 0.15  # 脚长
    arm_span_ratio: float = 0.35  # 臂长
    index_finger_ratio: float = 0.05  # 食指长度
    palm_length_ratio: float = 0.1  # 手掌长度
    chest_width_ratio: float = 0.25  # 胸宽
    thigh_diameter_ratio: float = 0.12  # 大腿直径
    forearm_diameter_ratio: float = 0.08  # 小臂直径
    index_finger_diameter_ratio: float = 0.008  # 食指直径比例
    fingerprint_width_ratio: float = 0.0005  # 指纹宽度比例
    finger_gap_ratio: float = 0.002  # 指缝宽度

    # 只与垂直地标对比的部位
    knee_height_ratio: float = 0.3  # 膝盖高度
    ankle_height_ratio: float = 0.08  # 脚踝高度

    # 只与水平地标对比的部位
    stride_ratio: float = 0.8  # 步长
    weight: float = field(default=1.0, compare=False, repr=False)
    parameter_ranges: Dict[str, float] = field(default_factory=dict, compare=False,
                                                repr=False)

    def randomized(self, rng) -> "BodyPreset":
        bounds = {attr: (0.0000001, 1.9999999) for attr in self.parameter_ranges}
        values = {
            attr: max(bounds[attr][0], min(bounds[attr][1], rng.uniform(
                value - self.parameter_ranges[attr], value + self.parameter_ranges[attr])))
            for attr, value in self.__dict__.items()
            if attr in self.parameter_ranges
        }
        return replace(self, **values, parameter_ranges={})


@dataclass
class Personality:
    name: str
    init_intrusion: float      # 初始介入度 (0-4)
    step_intrusion: float      # 介入度步长
    init_destruction: float    # 初始破坏性 (0-4)
    step_destruction: float    # 破坏性步长
    sensitivity: float         # 敏感值 (影响地标切换时的额外变化)
    description: str = ""
    skip_base_prob: float = 3.0  # 个性强度，推荐 2~4
    weight: float = field(default=1.0, compare=False, repr=False)
    parameter_ranges: Dict[str, float] = field(default_factory=dict, compare=False,
                                                repr=False)

    def randomized(self, rng) -> "Personality":
        bounds = {
            "init_intrusion": (0.0, 4.0),
            "step_intrusion": (-5.0, 5.0),
            "init_destruction": (0.0, 4.0),
            "step_destruction": (-5.0, 5.0),
            "sensitivity": (-5.0, 5.0),
            "skip_base_prob": (0.0, 5.0),
        }
        values = {
            attr: max(bounds[attr][0], min(bounds[attr][1], rng.uniform(
                value - self.parameter_ranges[attr], value + self.parameter_ranges[attr])))
            for attr, value in self.__dict__.items()
            if attr in self.parameter_ranges
        }
        return replace(self, **values, parameter_ranges={})

    @classmethod
    def from_dict(cls, data: dict) -> "Personality":
        """从字典构造，忽略残余的未知字段（如历史遗留的 enabled）。"""
        allowed = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in allowed})


@dataclass
class EvolutionRecord:
    """角色快照演化表的一行。

    每次更改追加一行，依次记录：更改时间、本次的故事步进权重（步进）、
    以及更改后的介入度、破坏性与累计伤亡。
    非故事性调整（如加载期衰退、迁移补行）步进记 0.0；
    负向演化记录其步进（-1 - 0.5*性格敏感值，负值）。
    source 为更改标签，标注该条更改来源于哪一方法。
    """
    changed_at: str
    step: float
    intrusion: float
    destruction: float
    casualties: float
    source: str = ""   # 更改标签：产生本行的方法名（如 apply_negative_evolution）


@dataclass
class CharacterSnapshot:
    giantess_id: str
    name: str
    nick: str = ""
    original_height: float = 1.6
    height: float = 1.6
    body_parts: Dict[str, float] = field(default_factory=dict)
    # 演化表：每次更改追加一行（见 EvolutionRecord），
    # 当前的介入度/破坏性/累计伤亡一律以最后一行为准（见下方只读属性）。
    evolution: List[EvolutionRecord] = field(default_factory=list)
    action_points: int = 0
    report_generated: bool = False
    created_at: str = field(default_factory=lambda: datetime.datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.datetime.now().isoformat())
    avatar_path: str = ""
    personality: Optional[Personality] = None      # 完整的性格对象
    greed: float = 0.0
    will: bool = False
    will_status: Optional[str] = None
    selected_tags: List[str] = field(default_factory=list)
    intro_hidden: str = ""
    intro_visible: str = ""
    birthday: str = ""
    size_unlocks: Dict[str, str] = field(default_factory=dict)  # 部位 -> 解锁描述；"MEASURED" 表示已测量无描述；"" 表示未解锁
    # 达成的重要结局索引（仅记录配置了图标的结局）：
    # 每条含 dungeon_id、trigger_index（结局触发器在副本 triggers 列表中的下标）、
    # name、icon_path（相对副本目录）、ending_text、replay_path、achieved_at。
    # icon_path 为空表示该结局不重要，不会出现在本列表中。
    achieved_endings: List[Dict] = field(default_factory=list)
    landmark_durability: Dict[str, float] = field(default_factory=dict)  # 独特地标耐久：地标名 -> 耐久值
    news_checked_at: str = ""          # 上次弹出新闻的时间（每日9点后首载门控）
    last_news: str = ""                # 上一次新闻正文，避免连续两次主干完全一致
    # 注册地址系统：角色当前位置（完整地址，含世界观段）。首次生成报告时由
    # 第一个地标锚定并持久化；空表示尚无位置（新角色或当前内容未注册地址）。
    position: str = ""

    def __post_init__(self):
        # 允许以字典列表构造（从 JSON 加载），统一规范化为 EvolutionRecord
        self.evolution = [
            e if isinstance(e, EvolutionRecord) else EvolutionRecord(**e)
            for e in (self.evolution or [])
        ]

    @classmethod
    def from_dict(cls, data: dict) -> "CharacterSnapshot":
        """从字典构造，忽略残余的未知字段（如历史遗留的 negative_reduction_*），
        并把 personality 字典规范化为 Personality。"""
        personality = data.get("personality")
        if isinstance(personality, dict):
            data = {**data, "personality": Personality.from_dict(personality)}
        allowed = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in allowed})

    @property
    def intrusion(self) -> float:
        """当前介入度：演化表最后一行的记录值。"""
        return self.evolution[-1].intrusion if self.evolution else 0.0

    @property
    def destruction(self) -> float:
        """当前破坏性：演化表最后一行的记录值。"""
        return self.evolution[-1].destruction if self.evolution else 0.0

    @property
    def total_casualties(self) -> float:
        """当前累计伤亡：演化表最后一行的记录值。"""
        return self.evolution[-1].casualties if self.evolution else 0.0

    def record_change(self, step: float = 0.0, intrusion: Optional[float] = None,
                      destruction: Optional[float] = None,
                      casualties: Optional[float] = None,
                      source: str = "") -> EvolutionRecord:
        """更改介入度/破坏性/累计伤亡，并向演化表追加一条完整的新行。

        未给出的数值沿用当前值；step 为本次的故事步进权重，默认 0.0；
        source 为更改标签，标注该条更改来源于哪一方法。
        """
        record = EvolutionRecord(
            changed_at=datetime.datetime.now().isoformat(),
            step=float(step or 0.0),
            intrusion=self.intrusion if intrusion is None else float(intrusion),
            destruction=self.destruction if destruction is None else float(destruction),
            casualties=self.total_casualties if casualties is None else float(casualties),
            source=source,
        )
        self.evolution.append(record)
        return record


@dataclass
class ReportData:
    """一次生成报告的完整结果"""
    name: str
    nick: str
    height: float
    original_height: float
    body_parts: Dict[str, float]
    personality: Personality
    preset: BodyPreset
    comparisons: List[dict]          # 每个元素包含 part, landmark, ratio, size_str, posture 等
    quip_results: List[dict]         # 每个元素包含 part, size_str, compare_text, quip_text, quip_style, intrusion, destruction, coord
    final_intrusion: float
    final_destruction: float
    size_category: str
    report_text: str                 # 完整的报告文本（含尺寸表）
    detail_text: str                 # 详细尺寸文本（表格）
    uploaded_image_path: Optional[str] = None
    greed: float = 0.0
    will: bool = False
    will_status: Optional[str] = None
    selected_tags: List[str] = field(default_factory=list)
    intro_hidden: str = ""
    intro_visible: str = ""
    birthday: str = ""
    total_casualties: float = 0.0
    casualty_breakdown: List[dict] = field(default_factory=list)
    # 副本需要额外字段
    curr_intrusion: float = 0.0
    curr_destruction: float = 0.0
    # 本次报告锚定的角色位置（完整地址）；空表示未锚定
    position: str = ""
