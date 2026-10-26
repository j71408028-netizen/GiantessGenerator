from typing import Any, ClassVar, List, Dict, Optional
from dataclasses import dataclass, field, fields, replace
import datetime
import math
import uuid

@dataclass
class Landmark:
    name: str
    size: float
    dimension: str  # 'vertical' 或 'horizontal'
    frequency: str  # 'unique' 或 'common'
    horizontal_type: Optional[str] = None
    # 注册地址：独特地标补在其“风格注册地址”之下的剩余级数（纯文本，
    # 无维度时需自带维度）。空：独特地标继承风格地址，均值地标不参与。
    address: str = ""
    # 地址参与方式："" = 自动（独特地标继承风格、均值地标仅显式地址参与）、
    # "inherit" = 继承风格、 "portable" = 不参与地址规则、
    # "exact"/"relative" = 使用 address 字段。
    address_mode: str = ""

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
    sensitivity: float         # 敏感值 (直接作用于介入度：地标切换、介入度步长演化等)
    gravity: float = 0.0       # 重力 (作用于破坏性：地标切换、破坏性步长演化等)
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
            "gravity": (-5.0, 5.0),
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

    # 策略值参数：行动点数归一基准（自然回复上限）、个性强度归一基准
    STRATEGY_AP_SCALE = 100.0
    STRATEGY_STRENGTH_SCALE = 5.0

    @property
    def normalized_strength(self) -> float:
        """归一个性强度 = skip_base_prob / 5（夹取 0~1）。"""
        return max(0.0, min(1.0, (getattr(self, "skip_base_prob", 3.0) or 0.0)
                            / self.STRATEGY_STRENGTH_SCALE))

    def strategy_value(self, action_points: Optional[float] = None) -> float:
        """策略值 = 敏感值 × (1 - 归一行动点数) + 重力 × 归一个性强度。

        除“直接操作当前介入度”的场景外，其余原先使用敏感值的属性操作
        （贪婪生成时的身高重映射、副本自定义属性等）均改用本值。
        注意：负向演化的介入度步进仍由敏感值直接控制，不走策略值。

        - 归一行动点数 = 行动点数 / 100（夹取 0~1）；行动点数未知时
          (1 - 归一行动点数) 取缺省 0.5；该因子不小于 0（行动点数因
          返还超过 100 时不产生反向贡献）。
        - 归一个性强度 = skip_base_prob / 5（夹取 0~1）。
        """
        if action_points is None:
            ap_factor = 0.5
        else:
            ap_factor = 1.0 - max(0.0, min(1.0,
                                           (action_points or 0.0) / self.STRATEGY_AP_SCALE))
        return (self.sensitivity * ap_factor
                + getattr(self, "gravity", 0.0) * self.normalized_strength)


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
    # 到访地址（完整地址文本）：本行发生时角色所在位置，供空间档案学习区域
    # 权重（services/character/spatial.py）。空 = 该行不作为到访证据（旧档
    # 与非故事性行）；仅报告收尾与副本出口等有明确位置的行会填写。
    position: str = ""
    # 环境沉淀：本行结算实际采用的环境因子（在线报告路径为各段落文本因子
    # 的步进加权均值）。与 position 联合构成“地址环境因子”的到访证据，
    # 供离线结算按地址聚合（services/character/offline.py）；None = 本行
    # 未做文本环境结算（旧档与非故事性行），不作为环境证据。
    env_factor: Optional[float] = None


@dataclass
class OfflineDetail:
    """离线恢复的2小时精度步进/伤亡明细（落在偶数时间格点）。

    start_at/end_at 为本地 ISO 时间，作为该明细在时间轴上的绝对区间
    （对齐偶数整点，供导出图表直接读取）。step/casualties 为该区间
    累计的步进与伤亡，env_factor 为该区间实际采用的环境因子（含噪声）。
    明细只保留最近 _OFFLINE_WINDOW_HOURS(72) 小时；更早的离线时长按
    “24小时平滑步进总量×离线天数×地址环境因子”汇总后只进入演化表总和。
    """
    start_at: str = ""         # 区间起始（本地 ISO 时间）
    end_at: str = ""           # 区间结束（本地 ISO 时间）
    step: float = 0.0          # 该区间累计步进
    casualties: float = 0.0    # 该区间累计伤亡
    env_factor: float = 0.0    # 该区间采用的环境因子（含噪声）

    @classmethod
    def from_dict(cls, data: dict) -> "OfflineDetail":
        allowed = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in allowed})


@dataclass
class OfflineRecoveryPlan:
    """一次离线恢复的纯计划。

    由 ``offline.recover_plan`` 产出，执行层负责写回状态与记录演化。
    ``action_point_gain`` 是请求恢复量（执行时仍受自然回复上限约束）；
    ``intrusion`` / ``destruction`` 是结算后的坐标，``None`` 表示不改坐标；
    ``details`` 为 ``None`` 表示保留现有明细；``casualties`` 是本次新增伤亡，
    不是累计值。计划本身不修改角色状态。
    """

    action_point_gain: int = 0
    intrusion: Optional[float] = None
    destruction: Optional[float] = None
    details: Optional[List[OfflineDetail]] = None
    step: float = 0.0
    casualties: float = 0.0


# 环境偏好先验的箱数：与学习层（services/character/spatial.py 的 _ENV_BINS）
# 同步的硬契约，ModelTuning.env_prior_shares 的每条先验都是这个长度。
ENV_PRIOR_BINS = 7


@dataclass
class SpatialPriorsParams:
    """烙印进角色档案的空间先验四标量（中性值 = 不影响调度）。

    字段语义与 ``services/character/spatial.py::SpatialPriors`` 一致；
    创建时按标签合并烙印，此后终身携带，不随世界包启停变化。
    """
    range_factor: float = 1.0     # 活动半径倍率
    stickiness: float = 0.0       # 区域粘性（放大/抹平学习层偏向）
    novelty_bonus: float = 0.0    # 未到访分支的新颖加成
    anchored_bias: float = 0.0    # 有地址内容相对 portable 内容的偏好偏移


@dataclass
class ModelTuning:
    """角色出身调制烙印：创建时经 ``CreationService.derive_model_tuning``
    派生、烙进档案后终身携带（世界包启停不再触及已有角色）。

    - 离线 8 项：离线结算模型参数（步进率、退出残留、行动点恢复、噪声、
      旧段除数、作息放大）；
    - ``spatial_priors`` / ``env_prior_shares``：创建时按标签合并的空间
      先验与环境份额先验（后者 None = 均匀兜底）；
    - ``origin_world``：出身世界包记录 ``{pack_id, name, version}`` 或
      None，由烙印点写入，供导出与排查；
    - ``extra``：行为包自定义键，核心模型忽略，下游钩子经
      ``state.model_tuning.extra`` 可读。

    数值统一经 :meth:`sanitize` 夹取归一（烙印与 ``from_dict`` 两端都走），
    危险值进不来。旧档案无本字段时走全默认，数值与不烙印一致。
    """

    step_per_hour: float = 0.01
    exit_amp: float = 0.05
    exit_tau: float = 0.9
    recovery_points_per_minute: float = 0.5
    recovery_decay_per_minute: float = 0.01
    noise_scale: float = 0.1
    old_step_divisor: float = math.pi
    rhythm_swing: float = 1.0   # 作息调制对离线步进的放大：0=平坦 1=默认，夹取 0~3
    spatial_priors: SpatialPriorsParams = field(default_factory=SpatialPriorsParams)
    env_prior_shares: Optional[List[float]] = None
    origin_world: Optional[Dict[str, str]] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def _num(value, fallback: float, lo: float = None,
             hi: float = None) -> float:
        """数值清洗：不可转 / 非有限回默认，再夹取到给定区间。"""
        try:
            v = float(value)
        except (TypeError, ValueError):
            return fallback
        if not math.isfinite(v):
            return fallback
        if lo is not None:
            v = max(lo, v)
        if hi is not None:
            v = min(hi, v)
        return v

    def _sanitize_shares(self) -> Optional[List[float]]:
        """环境份额：须恰好 ``ENV_PRIOR_BINS`` 个有限数值，负值归零后归一；
        形状不符（含 None）返回 None = 均匀兜底，非 None 的非法值警告一次。"""
        shares = self.env_prior_shares
        if shares is None:
            return None
        try:
            vals = [float(v) for v in shares]
        except (TypeError, ValueError):
            self._warn_bad_shares("含不可解析为数值的条目")
            return None
        if len(vals) != ENV_PRIOR_BINS:
            self._warn_bad_shares(
                f"须为 {ENV_PRIOR_BINS} 元（得到 {len(vals)} 个）")
            return None
        if not all(math.isfinite(v) for v in vals):
            self._warn_bad_shares("含非有限数值")
            return None
        vals = [max(0.0, v) for v in vals]
        total = sum(vals)
        if total <= 0:
            self._warn_bad_shares("总和为零")
            return None
        return [v / total for v in vals]

    # 非法份额的警告去重（每进程每原因一次）；ClassVar 不进字段。
    _SHARES_WARNED: ClassVar[set] = set()

    @classmethod
    def _warn_bad_shares(cls, reason: str) -> None:
        """[BehaviorPack] 通道一次性警告：烙印路径的非法环境份额按兜底处理。"""
        if reason in cls._SHARES_WARNED:
            return
        cls._SHARES_WARNED.add(reason)
        print(f"[BehaviorPack] 警告：model_tuning.env_prior_shares {reason}，"
              f"已回退均匀兜底")

    def sanitize(self) -> "ModelTuning":
        """夹取归一，返回清洗后的新实例。

        烙印（derive 之后、写档案之前）与读档（``from_dict``）两端统一走
        这里：非有限值回默认，各参数夹到与消费端一致的安全区间，空间先验
        按合并口径夹取，环境份额归一。
        """
        priors = (self.spatial_priors
                  if isinstance(self.spatial_priors, SpatialPriorsParams)
                  else SpatialPriorsParams())
        return ModelTuning(
            step_per_hour=self._num(self.step_per_hour, 0.01, lo=0.0),
            exit_amp=self._num(self.exit_amp, 0.05, lo=0.0),
            exit_tau=self._num(self.exit_tau, 0.9, lo=1e-9),
            recovery_points_per_minute=self._num(
                self.recovery_points_per_minute, 0.5, lo=0.0),
            recovery_decay_per_minute=self._num(
                self.recovery_decay_per_minute, 0.01, lo=0.0),
            noise_scale=self._num(self.noise_scale, 0.1, lo=0.0),
            old_step_divisor=self._num(
                self.old_step_divisor, math.pi, lo=1e-9),
            rhythm_swing=self._num(self.rhythm_swing, 1.0, lo=0.0, hi=3.0),
            spatial_priors=SpatialPriorsParams(
                range_factor=self._num(priors.range_factor, 1.0, 0.5, 2.0),
                stickiness=self._num(priors.stickiness, 0.0, -0.8, 0.8),
                novelty_bonus=self._num(priors.novelty_bonus, 0.0, lo=0.0),
                anchored_bias=self._num(priors.anchored_bias, 0.0, -0.5, 0.5),
            ),
            env_prior_shares=self._sanitize_shares(),
            origin_world=(self.origin_world
                          if isinstance(self.origin_world, dict) else None),
            extra=dict(self.extra) if isinstance(self.extra, dict) else {},
        )

    @classmethod
    def from_dict(cls, data: dict) -> "ModelTuning":
        """从字典构造（忽略未知字段），并经 sanitize 夹取归一。"""
        if not isinstance(data, dict):
            return cls()
        allowed = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in allowed}
        priors = kwargs.get("spatial_priors")
        if isinstance(priors, dict):
            priors_allowed = {f.name for f in fields(SpatialPriorsParams)}
            kwargs["spatial_priors"] = SpatialPriorsParams(
                **{k: v for k, v in priors.items() if k in priors_allowed})
        return cls(**kwargs).sanitize()


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
    # 当前介入度/破坏性步长：步长随故事演进而变化（演化坐标前向初始值恢复，
    # 演化坐标后减去 步进×敏感/重力），初始值与 personality 一致，之后独立存储。
    step_intrusion: Optional[float] = field(default=None, compare=False, repr=False)
    step_destruction: Optional[float] = field(default=None, compare=False, repr=False)
    selected_tags: List[str] = field(default_factory=list)
    intro_hidden: str = ""
    intro_visible: str = ""
    birthday: str = ""
    size_unlocks: Dict[str, str] = field(default_factory=dict)  # 部位 -> 解锁描述；"MEASURED" 表示已测量无描述；"" 表示未解锁
    # 达成的重要结局索引（仅记录配置了图标的结局）：
    # 每条含 scenario_id、trigger_index（结局触发器在副本 triggers 列表中的下标）、
    # name、icon_path（相对副本目录）、ending_text、replay_path、achieved_at。
    # icon_path 为空表示该结局不重要，不会出现在本列表中。
    achieved_endings: List[Dict] = field(default_factory=list)
    # 独特地标耐久：地标键 -> 耐久值。地标键为“名称@完整地址”；无地址时仅名称
    # （兼容旧存档），因此同名不同地址的地标各自独立计算耐久。
    landmark_durability: Dict[str, float] = field(default_factory=dict)
    # 独特地标地址：地标键 -> 完整地址文本，与 landmark_durability 同键
    landmark_addresses: Dict[str, str] = field(default_factory=dict)
    news_checked_at: str = ""          # 上次弹出新闻的时间（每日9点后首载门控）
    last_news: str = ""                # 上一次新闻正文，避免连续两次主干完全一致
    # 注册地址系统：角色当前位置（完整地址，含维度段）。首次生成报告时由
    # 第一个地标锚定并持久化；空表示尚无位置（新角色或当前内容未注册地址）。
    position: str = ""
    # 活动区域（完整地址文本列表，软约束）：报告选取时区域内地标优先，让角色
    # 的空间行为有惯性；空 = 无偏好。不做合法性校验，坏文本使用时不命中。
    activity_regions: List[str] = field(default_factory=list)
    # 离线恢复期间的2小时精度步进与伤亡明细，由离线结算
    # （services/character/offline.py::recover_offline）计算填充。
    offline_details: List[OfflineDetail] = field(default_factory=list)
    # 出身调制烙印：创建期由 CreationService.derive_model_tuning 派生并经
    # sanitize 后烙印（见 ModelTuning）。旧档无此字段 → 全默认，任何世界包
    # 启停都不再触及已有角色的离线/空间调制。
    model_tuning: ModelTuning = field(default_factory=ModelTuning)
    # 世界履历：旅居过的世界包记录 [{pack_id, name, version, first_at,
    # last_at, born}]。创建时写 born 条目，报告生成成功与离线结算完成时
    # 更新 last_at / 追加新条目；始终记录（world_history_enabled 开关只
    # 控制展示/导出读取，不影响写入）。与地址 / 学习层正交：不产生地址、
    # 不改写演化表已有行。
    world_history: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self):
        # 允许以字典列表构造（从 JSON 加载），统一规范化为 EvolutionRecord
        self.evolution = [
            e if isinstance(e, EvolutionRecord) else EvolutionRecord(**e)
            for e in (self.evolution or [])
        ]
        self.offline_details = [
            d if isinstance(d, OfflineDetail) else OfflineDetail.from_dict(d)
            for d in (self.offline_details or [])
        ]
        if isinstance(self.model_tuning, dict):
            self.model_tuning = ModelTuning.from_dict(self.model_tuning)
        self.world_history = [e for e in (self.world_history or [])
                              if isinstance(e, dict)]

    @classmethod
    def from_dict(cls, data: dict) -> "CharacterSnapshot":
        """从字典构造，忽略残余的未知字段（如历史遗留的 negative_reduction_*），
        并把 personality / model_tuning 字典规范化为对应对象。"""
        personality = data.get("personality")
        if isinstance(personality, dict):
            data = {**data, "personality": Personality.from_dict(personality)}
        model_tuning = data.get("model_tuning")
        if isinstance(model_tuning, dict):
            data = {**data,
                    "model_tuning": ModelTuning.from_dict(model_tuning)}
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
    def current_step_intrusion(self) -> float:
        """当前介入度步长：存储字段；未初始化（None）时回退到性格初始步长。"""
        if self.step_intrusion is not None:
            return self.step_intrusion
        return self.personality.step_intrusion if self.personality else 0.0

    @property
    def current_step_destruction(self) -> float:
        """当前破坏性步长：存储字段；未初始化（None）时回退到性格初始步长。"""
        if self.step_destruction is not None:
            return self.step_destruction
        return self.personality.step_destruction if self.personality else 0.0

    @property
    def total_casualties(self) -> float:
        """当前累计伤亡：演化表最后一行的记录值。"""
        return self.evolution[-1].casualties if self.evolution else 0.0

    def record_change(self, step: float = 0.0, intrusion: Optional[float] = None,
                      destruction: Optional[float] = None,
                      casualties: Optional[float] = None,
                      source: str = "",
                      position: str = "",
                      env_factor: Optional[float] = None) -> EvolutionRecord:
        """更改介入度/破坏性/累计伤亡，并向演化表追加一条完整的新行。

        未给出的数值沿用当前值；step 为本次的故事步进权重，默认 0.0；
        source 为更改标签，标注该条更改来源于哪一方法；
        position 为本行到访地址（空 = 不作为到访证据）；
        env_factor 为本行环境沉淀（None = 本行未做文本环境结算）。
        """
        record = EvolutionRecord(
            changed_at=datetime.datetime.now().isoformat(),
            step=float(step or 0.0),
            intrusion=self.intrusion if intrusion is None else float(intrusion),
            destruction=self.destruction if destruction is None else float(destruction),
            casualties=self.total_casualties if casualties is None else float(casualties),
            source=source,
            position=position or "",
            env_factor=env_factor,
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
    # 本次报告演化后的当前步长（介入度/破坏性）
    step_intrusion: float = 0.0
    step_destruction: float = 0.0
    # 本次报告锚定的角色位置（完整地址）；空表示未锚定
    position: str = ""


@dataclass
class ChatMessage:
    """聊天记录中的一条消息。

    role 为 "user"（玩家发送）或 "char"（角色回复）；read 标记对方是否
    已读（仅对 user 消息有意义）；silent 为"已读不回"：对方已读但未回复，
    此时本条 user 消息后不会紧跟 char 消息。

    生命周期字段（AI 已生成 ≠ 已送达 ≠ 玩家已读，见 docs/dev/chat_delivery.md）：
    - id / batch_id / sequence：稳定身份与"一次决策多条消息"的分组；
    - status：角色消息 queued → delivered → read；user 消息恒为空串
      （沿用 read/silent 表达"角色是否看到/是否已读不回"）；
    - available_at：计划展示时间（queued 消息必填），delivered_at /
      read_at 为实际到达与被读时间；
    - seen_by_char_at：角色看到本条 user 消息的时间（含已读不回）。
    旧档迁移：缺 id 补 UUID；角色消息缺 status 视为 read（早已展示过）。
    """
    role: str
    text: str
    created_at: str = field(default_factory=lambda: datetime.datetime.now().isoformat())
    read: bool = False
    silent: bool = False
    id: str = ""
    batch_id: str = ""
    sequence: int = 0
    status: str = ""
    available_at: str = ""
    delivered_at: str = ""
    read_at: str = ""
    seen_by_char_at: str = ""
    silent_reason_internal: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex
        # 旧档角色消息没有 status：历史上已完整展示，直接视为已读，
        # 避免迁移后全体误报未读；新消息由服务层显式写入 status。
        if self.role == "char" and not self.status:
            self.status = "read"

    @classmethod
    def from_dict(cls, data: dict) -> "ChatMessage":
        allowed = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in allowed})


@dataclass
class ChatParams:
    """角色的纯聊天参数（只存聊天域，不进主档案，不污染既有字段）。

    首次聊天前由 AI 依据人设（公开+隐藏介绍）一次性决定，之后不再更改；
    三项均取 0/1/2 三档（各档含义见 services.chat 的档位表）：
    - online 在线强度：决定回复延迟（读/打字延迟的档位倍率）；
    - warmup 预热速率：决定聊天中态度 ops 的步长倍率；
    - boundary 聊天边界：决定已读不回倾向与主动搭话的间隔门槛。
    介入度/破坏性/行动点数等既有字段由聊天逻辑直接复用（决定谈及人类时
    的态度与此刻的活力），不在此重复存储。
    """
    online: int = 1
    warmup: int = 1
    boundary: int = 1

    @classmethod
    def from_dict(cls, data: dict) -> "ChatParams":
        allowed = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in allowed})


# 玩家身份（聊天线程的所有者）与她对当前身份的认知。
DEFAULT_IDENTITY_ID = "default"
DEFAULT_RELATIONSHIP_KIND = "stranger"
RELATIONSHIP_KINDS = (
    "stranger", "acquaintance", "friend", "close", "lover", "family",
    "worshipper", "subject", "rival", "enemy", "prey",
)


def normalize_identity_id(identity_id) -> str:
    """身份 id 归一：空值/非字符串回退默认身份，其余去首尾空白。"""
    if not isinstance(identity_id, str) or not identity_id.strip():
        return DEFAULT_IDENTITY_ID
    return identity_id.strip()


@dataclass
class PlayerIdentity:
    """玩家可切换的身份；角色视角里每个身份都是不同的人。

    id 是线程隔离的稳定键，默认身份固定为 ``default``；label 仅用于界面
    显示。身份本身不知道、也不记录其他身份的存在。
    """
    id: str = DEFAULT_IDENTITY_ID
    label: str = "默认身份"
    description: str = ""
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        self.id = normalize_identity_id(self.id)
        if not isinstance(self.label, str) or not self.label.strip():
            self.label = "默认身份"
        if not isinstance(self.description, str):
            self.description = ""
        now = datetime.datetime.now().isoformat()
        if not isinstance(self.created_at, str) or not self.created_at.strip():
            self.created_at = now
        if not isinstance(self.updated_at, str) or not self.updated_at.strip():
            self.updated_at = self.created_at

    @classmethod
    def from_dict(cls, data: dict) -> "PlayerIdentity":
        allowed = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (data or {}).items() if k in allowed})


@dataclass
class Relationship:
    """角色对某条聊天线程（某个玩家身份）的关系认知。

    kind 是稳定枚举（见 ``RELATIONSHIP_KINDS``），用于映射紧张度；note 保留
    「她把你当成什么」的细微语义。认知可以与好感背离，二者互不覆盖。
    """
    kind: str = DEFAULT_RELATIONSHIP_KIND
    note: str = ""
    since: str = ""
    updated_at: str = ""

    def __post_init__(self):
        if self.kind not in RELATIONSHIP_KINDS:
            self.kind = DEFAULT_RELATIONSHIP_KIND
        if not isinstance(self.note, str):
            self.note = ""
        if not isinstance(self.since, str):
            self.since = ""
        if not isinstance(self.updated_at, str):
            self.updated_at = ""

    @classmethod
    def from_dict(cls, data: dict) -> "Relationship":
        allowed = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (data or {}).items() if k in allowed})


def _normalize_topic_coord(values) -> List[float]:
    """话题坐标快照：只接受三元数值序列，零夹到 0~1；旧档/坏数据返回空。"""
    if not isinstance(values, (list, tuple)) or len(values) != 3:
        return []
    try:
        coord = [float(value) for value in values]
    except (TypeError, ValueError):
        return []
    if not all(math.isfinite(value) for value in coord):
        return []
    return [max(0.0, min(1.0, value)) for value in coord]


@dataclass
class Topic:
    """聊天话题（docs/dev/chat_delivery.md 阶段三）。

    角色与玩家之间的一段连续对话主题，由 AI 在协议里通过
    topic.action（continue/start/switch/end）自主开启、切换与结束；
    服务层施加硬约束（最大轮数、超时收束、列表容量）。
    status 为 active（进行中）或 ended；end_reason 记录结束方式：
    natural（AI 主动收尾）/ switched（切换到新话题）/ stale（超时或
    超轮数被服务层强制收束）。

    kind_id 为话题类型（TopicKind）的稳定 id，coord 为开题时的三轴坐标
    快照 ``[affection, tension, size_state]``；旧档没有这两项，保持空值。
    """
    id: str = ""
    title: str = ""
    kind_id: str = ""
    coord: List[float] = field(default_factory=list)
    status: str = "active"
    source: str = ""           # user | character | event
    started_at: str = ""
    last_activity_at: str = ""
    ended_at: str = ""
    end_reason: str = ""
    turn_count: int = 0

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex
        if not isinstance(self.kind_id, str):
            self.kind_id = ""
        self.coord = _normalize_topic_coord(self.coord)

    @classmethod
    def from_dict(cls, data: dict) -> "Topic":
        allowed = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in allowed})


@dataclass
class ChatState:
    """角色聊天域中的一条线程（角色 × 玩家身份）的数据。

    线程独立存档于 ``data/archives/<giantess_id>/chats/<identity_id>.json``；
    默认身份在首次保存时写 ``chats/default.json``，旧 ``chat.json`` 只在
    读取默认身份时作为回退，不删除。identity_id 是线程键，消息、态度、
    记忆、话题与已消费事件都只属于这条线程。

    attitude 为聊天态度值（-100~100）：驱动角色的回复意愿与语气，
    低于 REVEAL_ATTITUDE（见 services.chat）时不注入隐藏介绍；
    memory 为早期对话的压缩摘要与 AI 留下的 memory_note 累积；
    user_nick 为角色对玩家的称呼（聊天域内独立，不改主档案 nick）；
    chat_params 为纯聊天参数（首次聊天前由 AI 一次性决定，见 ChatParams）；
    topics / active_topic_id 为话题状态机（见 Topic）；
    consumed_event_ids 为已消费经历事件的确定性 id（见
    services.chat.experience_events），防止同一新闻/经历被反复提起。
    """
    giantess_id: str
    identity_id: str = DEFAULT_IDENTITY_ID
    messages: List[ChatMessage] = field(default_factory=list)
    memory: str = ""
    attitude: int = 0
    user_nick: str = ""
    last_active_at: str = ""
    chat_params: Optional[ChatParams] = None
    topics: List[Topic] = field(default_factory=list)
    active_topic_id: str = ""
    consumed_event_ids: List[str] = field(default_factory=list)

    def __post_init__(self):
        self.identity_id = normalize_identity_id(self.identity_id)
        self.messages = [
            m if isinstance(m, ChatMessage) else ChatMessage.from_dict(m)
            for m in (self.messages or [])
        ]
        self.topics = [
            t if isinstance(t, Topic) else Topic.from_dict(t)
            for t in (self.topics or [])
        ]
        if isinstance(self.chat_params, dict):
            self.chat_params = ChatParams.from_dict(self.chat_params)

    @classmethod
    def from_dict(cls, data: dict) -> "ChatState":
        allowed = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in allowed})
