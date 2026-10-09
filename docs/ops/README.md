# ops/：维护者与发版

> **这一组回答什么问题**：怎么发一个版本——发版策略、版本切换点、变更记录。

## 成员

| 文档 | 主要读者 | 性质 |
|---|---|---|
| [release.md](release.md) | 发版 / 规划里程碑的人 | 现状 |
| [../releases/changelog-v1.0.0.md](../releases/changelog-v1.0.0.md) | 想知道某版本改了什么的人 | 快照（append-only） |

## 推荐阅读顺序

1. 首次发版：[release.md](release.md) §1–§7。
2. 规划下一个里程碑：[release.md](release.md) §9（v1.0.0 之后的过渡点建议）。
3. 查历史版本变更：[../releases/](../releases/) 目录。

## 约定

- `releases/` 下的变更记录是**版本快照**，只增不改。
- 发版相关的**执行记录**（某次打包踩了什么坑）属历史，写进对应文档的「历史」小节
  或 `.workbuddy/`，不进 `releases/`。

## 相关组

- 打包脚本与构建：见 [`../dev/README.md`](../dev/README.md) 与 `build/`。
- 平台差异（Linux 打包）：见 [`../users/platforms/linux.md`](../users/platforms/linux.md) §7。
