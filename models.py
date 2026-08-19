from typing import List, Dict, Optional
from dataclasses import dataclass, field, fields
import datetime

@dataclass
class Landmark:
    name: str
    size: float
    dimension: str  # 'vertical' 或 'horizontal'
    frequency: str  # 'unique' 或 'common'
    horizontal_type: Optional[str] = None

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

    @classmethod
    def from_dict(cls, data: dict) -> "Personality":
        """从字典构造，忽略残余的未知字段（如历史遗留的 enabled）。"""
        allowed = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in allowed})


@dataclass
class CharacterSnapshot:
    giantess_id: str
    name: str
    nick: str = ""
    original_height: float = 1.6
    height: float = 1.6
    body_parts: Dict[str, float] = field(default_factory=dict)
    intrusion: float = 0.0
    destruction: float = 0.0
    intrusion_evolution: List[float] = field(default_factory=list)
    destruction_evolution: List[float] = field(default_factory=list)
    action_points: int = 0
    report_generated: bool = False
    negative_triggered: bool = False
    negative_reduction_intrusion: float = 0.0
    negative_reduction_destruction: float = 0.0
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
    total_casualties: float = 0.0
    casualties_evolution: List[float] = field(default_factory=list)
    size_unlocks: Dict[str, str] = field(default_factory=dict)  # 部位 -> 解锁描述；"MEASURED" 表示已测量无描述；"" 表示未解锁
    # 达成的重要结局索引（仅记录配置了图标的结局）：
    # 每条含 dungeon_id、trigger_index（结局触发器在副本 triggers 列表中的下标）、
    # name、icon_path（相对副本目录）、ending_text、replay_path、achieved_at。
    # icon_path 为空表示该结局不重要，不会出现在本列表中。
    achieved_endings: List[Dict] = field(default_factory=list)
    news_checked_at: str = ""          # 上次弹出新闻的时间（每日9点后首载门控）
    last_news: str = ""                # 上一次新闻正文，避免连续两次主干完全一致


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
