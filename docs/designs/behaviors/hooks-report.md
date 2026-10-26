# 行为包钩子：report（报告选取与引擎参数）

> 所属机制：[行为包](README.md)（玩家向）· [开发指南](dev-guide.md)。注册方式、
> 签名一致性与层级约定见开发指南的「注册 API」「Hook 目录与层级」。

`SelectionArbiter` 在地标候选与描述风格供给之间做配对仲裁：车道资格窗口与
近匹配提拔是裁定级输出，联合分是建议级（报告引擎的预算走步与坐标演化可
自由偏离）。`ruling` 参数是 :class:`~services.exploration.arbiter.SlotRuling`
（字段 `cand` / `addr` / `lanes` / `affordance` / `starved` / `promoted` /
`env_estimate`）。硬闸（可达 / 私有名 / 维度 / 耐久）、物理走查（位置
演化）与 stuck 判定不开放。`logic.select_quip_with_budget` /
`logic.get_comparisons` 的 key 不变（scope `logic` 是已部署契约）。

报告引擎（`services/exploration/report.py`）的伤亡 / 耐久数值也在此组：
突破概率缩放、耐久伤害系数、残破阈值与无事件兜底（见下表常量行）。
终局坐标锁 `(4, 4)` 与突破的 `will` 门控、「涩涩」直通语义不开放。

| 目标 | 类型 | 签名 / 默认 | 说明 |
|------|------|------|------|
| `report.lanes` | 函数 | `(landmark_addr, quip_registers) -> list` | 槽位 × 风格的车道表 `[(style, weight)]`（裁定级）：无地址地标全部风格 FREE；注册地址同址 EXACT、覆盖 COVER；不覆盖 / 私有名冲突不入窗口。 |
| `report.joint_score` | 函数 | `(ruling, position, reach, profile) -> float` | 建议级联合分：尺寸匹配主导，覆盖收益 / 档案 / 路径只调制。 |
| `report.promotions` | 函数 | `(order, cap) -> (list, list)` | 近匹配提拔（裁定级）：星槽在容差带内与有覆盖替代者交换位置，返回 `(新序, 提拔记录)`。 |
| `report.model.lane_weights` | 常量 dict | `{exact: 1.0, cover: 0.8, free: 0.5}` | 车道权重表（只调制排序，不改变资格判定）。 |
| `report.model.near_match_tau` | 常量 float | `0.35` | 近匹配容差（log 尺度）。 |
| `report.model.cover_weight` | 常量 float | `0.5` | 联合分覆盖收益权重。 |
| `report.model.region_weight` | 常量 float | `1.5` | 联合分活动区域命中增益。 |
| `report.model.path_weight` | 常量 float | `0.3` | 联合分路径连贯惩罚权重。 |
| `report.model.pool_saturation` | 常量 float | `2.0` | 充裕度池深饱和折算除数（`depth/(depth+k)`）。 |
| `report.model.pool_neighborhood` | 常量 float | `2.0` | 充裕度坐标邻域口径（坐标距离上限）。 |
| `report.model.promotion_cap_divisor` | 常量 float | `2.0` | 提拔上限 = 对比槽位数 / 该除数（除数下限 1.0）。 |
| `report.model.reach_multiplier` | 常量 float | `10.0` | 可达范围 = 身高倍数；硬闸语义不开放，仅开放数值。 |
| `report.model.env_consistency` | 常量 float | `0.5` | 描述文本环境一致性加分上限（消费于 `logic.select_quip_with_budget`）。 |
| `report.model.env_dev_range` | 常量 float | `1.4` | 环境偏差归一分母（环境因子值域全宽）。 |
| `report.model.breakthrough_scale` | 常量 float | `1.0` | 终局坐标突破概率整体缩放（`0` = 永不突破）；`will` 门控与「涩涩」直通语义不开放。 |
| `report.model.durability_damage_scale` | 常量 float | `0.1` | 独特地标单次耐久伤害系数（伤害 = 对比倍率 × 身高比² × 破坏性 × 本系数）。 |
| `report.model.worn_threshold` | 常量 float | `0.5` | 耐久残破标注阈值（低于即标注「（残破的）」）。 |
| `report.model.step_fallback` | 常量 float | `0.05` | 无有效步进事件时的伤亡估算兜底步长。 |
| `report.model.env_factor_fallback` | 常量 float | `0.3` | 无事件文本时的伤亡环境系数兜底。 |
