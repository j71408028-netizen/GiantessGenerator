# docs/ 入口

> 这个目录放的是**面向非特定读者**的说明文档。
> 找东西先看下面两节：先按「我是什么身份」找去向，再看四条准入判据决定「新文档该不该放这」。

## 什么能进这里

只放面向**非特定**玩家 / 资源开发者 / 代码开发者 / 维护者的通用说明。四条判据：

| # | 情况 | 去哪 |
|---|---|---|
| 1 | 面向非特定的某类读者、内容通用 | ✅ 本目录，按读者分组 |
| 2 | 某个部件自己的贡献指南 | ✅ 留在那个部件（`scripts/`、`developer_tools/`） |
| 3 | 为**某一次**改动写的交接稿 / 给 Agent 的说明 | ❌ 不入库，放 `.workbuddy/` |
| 4 | **某个特定风格**的创作工作流 | ❌ 不入库（步骤随风格而异，写下来只对一种风格成立） |

判据 3 的边界：描述**一次特定改动**（而不是一类读者的通用知识）的文档，正文留在
`.workbuddy/`，其**结论**若要长期成立，应写进对应的现状文档。

## 我是谁 → 从哪开始

| 你是 | 先读 | 再读 |
|---|---|---|
| 玩家 | [`../README.md`](../README.md)「玩家指南」 | [`designs/README.md`](designs/README.md) |
| 在 Linux 上跑 | [`users/platforms/linux.md`](users/platforms/linux.md) | [`users/platforms/linux-history.md`](users/platforms/linux-history.md) |
| 做资源包 / 世界包 / 小游戏 | [`designs/README.md`](designs/README.md) | [`designs/world_pack_behaviors.md`](designs/world_pack_behaviors.md) |
| 写副本方案 | [`Dungeon/README.md`](Dungeon/README.md)（按任务查表） | [`Dungeon/script.md`](Dungeon/script.md) |
| 改代码 | [`dev/README.md`](dev/README.md) | [`dev/architecture.md`](dev/architecture.md) |
| 发版 | [`ops/release.md`](ops/release.md) | [`ops/release.md`](ops/release.md) §9（v1.0.0 后的过渡点） |

## 约定

1. 每个子目录都有自己的 README，进入后先看它。
2. 现状文档只写「现在是什么」；历史放 `history/` 或 `legacy/`，只放**指针**，不展开叙述。
3. `designs/` 每篇是**两段式**：前半简介 / 全景，后半开发事项（详见该组 README）。
4. 新增文档前先过上面四条判据；跨组引用用相对链接，别复制正文。
