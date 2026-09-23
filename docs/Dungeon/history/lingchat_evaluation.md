# LingChat 参考价值评估

> **状态：已归档。** 旧文评估的是同人作品 LingChat（Tauri 2 + Vue 3 + Rust，AGPL-3.0）对本仓库的借鉴价值，
> 其中大部分内容已随分层重构过期。
> **现状见**：[架构说明](../architecture.md)（分层、模块地图、自检脚本）。
> 下文中**只有第 1 条仍然有效**，且它是一条必须长期遵守的许可证约束。

---

## 1. 许可证红线（仍然有效）

LingChat 为 AGPL-3.0，本项目为 MIT。任何复制、改写、移植其源码的行为都会强制本项目转为 AGPL。
**只读它的文档与实现、用 Python 独立重写**，是唯一可行做法。

## 2. 已吸收的四项工程实践（均已在本仓库重新实现，非复制）

| 实践 | 落地位置 |
|---|---|
| schema 单一真相源 | `dungeon/schema.py` |
| 把引擎里的静默失败变成作者可见的诊断 | `dungeon/validate.py` |
| 原子写 + `.bak` | `persistence/json_store.py` |
| 错误路径也收尾且不记为完成 | `DungeonPersistence._finalize(completed)` |

## 3. 明确不采纳

- **不**替换为事件队列引擎：生成式步进是本项目核心差异。
- **不**抄其 `evaluate_condition`：表达力弱于本项目的 `TriggerRules`。
- **不**跟进 Rust 重写：没有桌面端打包动因。
