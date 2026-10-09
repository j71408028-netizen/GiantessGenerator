# dev/：代码开发者

> **这一组回答什么问题**：这个仓库**现在**长什么样、哪些契约不能碰、怎么安全地动它、
> 怎么提交。面向改代码的人，不面向使用者。

## 成员

| 文档 | 主要读者 | 性质 |
|---|---|---|
| [architecture.md](architecture.md) | 所有改代码的人 | 现状 |
| [chat_delivery.md](chat_delivery.md) | 改聊天 / 消息投递系统的人 | 现状 |
| [legacy/refactor-2026-10.md](legacy/refactor-2026-10.md) | 要搞清「某个结构为什么这样」的人 | 历史（只读） |
| [../Dungeon/README.md](../Dungeon/README.md) | 改副本的人 | 现状 + `history/` |

## 推荐阅读顺序

1. [architecture.md](architecture.md) §1 现状 —— 先建立分层与守卫的全局观。
2. [architecture.md](architecture.md) §2 硬约束 —— **改代码前必读**，违反不会报错、只会静默失效。
3. 要动副本章节 / 窗口：转 [Dungeon/README.md](../Dungeon/README.md) 的「按任务查」表。
4. [architecture.md](architecture.md) §5 操作手册 + §6 提交规范 —— 动手前过一遍。

## 相关组

- 要在副本内部工作：见 [`../Dungeon/README.md`](../Dungeon/README.md)。
- 要理解某机制的设计（地址、演化、行为包、小游戏）：见
  [`../designs/README.md`](../designs/README.md)。
- 自检与打包：`tests/run_checks.py`；发版见 [`../ops/release.md`](../ops/release.md)。
- 本机组特有的仓库维护事项（git 故障排查等）不入库，见本机 `.workbuddy/`。
