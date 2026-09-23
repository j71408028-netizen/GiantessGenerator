# 历史档案（只读 · 仅供溯源）

> 本目录放的是**已经发生过的事**：改造过程、实测记录、调查与决策依据。
> **不要用这里的结论改代码**——现状一律以 [上级目录](../README.md) 下的现状文档为准。
> 每篇档案顶部都标注了「现状见哪篇」。

| 档案 | 时间 | 内容 | 现状见 |
|---|---|---|---|
| [host_refactor.md](host_refactor.md) | 2026-09-23 | DPG 在 Tk 宿主内的 L0–L5 改造全过程、改造前形状、验收数据 | [宿主边界与可移植性](../window_host.md)、[窗口](../window.md) |
| [exit_hang_investigation.md](exit_hang_investigation.md) | 2026-09-22 ~ 23 | 进程退出挂死的复现矩阵与修正结论、重启复测；`destroy_context()` 后碰 Tk 的崩溃实测 | [窗口 §5-C2 / §6](../window.md)、[自检判定 §4](../window_automation.md) |
| [dungeon_model_evolution.md](model_evolution.md) | S1 ~ S4 重构 | 章节 / 触发器模型拆分、动作类型退场、scenario / dungeon 命名拆分与目录迁移 | [数据模型](../script.md)、[术语表](../domain_terms.md)、[架构说明](../architecture.md) |
| [lingchat_evaluation.md](lingchat_evaluation.md) | 重构前 | 同人作品 LingChat 的参考价值评估与「不采纳」清单 | [架构说明](../architecture.md) |

## 约定

1. 现状文档变更时**不必**同步本目录；本目录只追加，不随现状改写。
2. 新的历史（一次改造、一次调查）写成新档案，或追加到对应档案末尾并标注日期。
3. 现状文档里出现「历史」小节时，只放**指针**，不展开叙述——展开的内容属于这里。
