# scripts/ — 开发者一次性工具

这个目录放**不进常驻自检**的开发者工具；常驻自检（守卫 / 行为 / GUI 冒烟）在
`tests/`，由 `python tests/run_checks.py` 统一驱动。两者语义见根目录 README
「仓库结构约定」。

| 脚本 | 性质 | 用途 |
|---|---|---|
| `dungeon_autopilot.py` | 真窗口自动驾驶 | 副本窗口全生命周期回归（14 场景，需显示器）：构造 → `run()` → 步进 → 关闭 → 落盘。最强回归，`--in-process` 连跑全部场景，`--scene <名> --isolate` 单跑 |
| `escape_sim.py` | 无头模拟 | escape_giantess 小游戏模拟（无显示器可跑）：地图生成 / 出生点连通性 / bot 实跑结算 |
| `migrate_scenario_naming.py` | 一次性迁移 | 把旧标识符（`dungeon_id` / `DungeonRepo` 等）改写为 `scenario_*`（幂等、原子写 + `.bak`），改名落地后仅存档备查 |
| `migrate_snapshot_evolution.py` | 一次性迁移 | 存档演化字段的历史迁移，同上 |

约定：迁移脚本完成后不删（幂等 + 有 `.bak`，可对旧存档重放），但也不再演进；
需要长期维护的回归请写成 `tests/smoke_*.py` 或 `tests/check_*.py`。
