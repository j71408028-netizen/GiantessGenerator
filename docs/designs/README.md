# designs/：玩家与资源开发者

> **这一组回答什么问题**：这套东西是**怎么设计的**——地标之间怎么算距离、角色怎么随
> 故事演化、资源包能覆盖到什么程度、小游戏怎么接入。玩家想理解系统怎么运作、资源
> 开发者想扩展它，读的是**同一份知识**，所以这两类读者共用本目录。

## 成员

机制 = 目录：目录的 `README.md` 是全景页，细节页从那里进入；未拆分的机制仍是单篇。

| 机制 | 入口 | 主要读者 |
|---|---|---|
| 地址系统 | [address-system/](address-system/README.md) | 玩家 + 资源开发者（地标 / 描述风格作者） |
| 报告生成 | [report_selection.md](report_selection.md) | 玩家 + 资源开发者（地标 / 描述风格作者） |
| 角色演化 | [character_evolution.md](character_evolution.md) | 核心开发者 + 行为包作者 |
| 角色聊天 | [chat_topics.md](chat_topics.md) | 玩家 + 资源开发者（话题类型 / 世界态势作者） |
| 聊天话题的信息视角 | [chat_topic_lens.md](chat_topic_lens.md) | 核心开发者 + 话题类型作者（存档字段 / 预算） |
| 行为包 | [behaviors/](behaviors/README.md) | 玩家 + 世界包 / 行为包作者 |
| 小游戏 | [minigame.md](minigame.md) | 小游戏作者 |

## 按任务找入口

| 想做什么 | 从哪读 |
|---|---|
| 理解或调整**报告里地标与描述的选取 / 配对** | [report_selection.md](report_selection.md) |
| 给**地标 / 描述风格**申领、注册地址 | [authoring.md](address-system/authoring.md)（地址格式与距离规则见 [matching.md](address-system/matching.md)） |
| 改**角色数值演化** | 先读 [character_evolution.md](character_evolution.md)（全景），再看可覆盖的代码入口 [behaviors/dev-guide.md](behaviors/dev-guide.md) |
| 调整**角色聊天**的话题类型、好感/紧张度/状态的手感 | [chat_topics.md](chat_topics.md)（实现细节转到 [dev/chat_delivery.md](../dev/chat_delivery.md)） |
| 调整**某类话题下 AI 知道什么 / 不知道什么** | [chat_topic_lens.md](chat_topic_lens.md)（改完用 `python developer_tools/lens_preview.py` 静态看账，或 `chat_session_preview.py` 边聊边看） |
| **扩展程序行为**而不改主程序 | [behaviors/README.md](behaviors/README.md)（玩家向）→ [behaviors/dev-guide.md](behaviors/dev-guide.md) |
| 做**小游戏** | [minigame.md](minigame.md) |

## 本组文档的写法：机制 = 目录

- 单篇超过约 300 行，或一篇内出现 3 种以上不同查表任务（找流程 / 查签名 / 查格式规范）
  → 拆为目录；
- 目录的 `README.md` 独立成页，保持线性叙事（玩家 / 使用者读到这页为止）；细节页
  才允许查表型，从全景页链接进入；
- 未拆分的机制仍是单篇：前半写全景、后半写开发事项（见 [report_selection.md](report_selection.md)、
  [character_evolution.md](character_evolution.md)、[minigame.md](minigame.md)）；
- 拆分提交为纯移动（利于 git rename 跟踪），措辞修订另起提交；
- 链接指文件不指标题（锚点跨生成器不稳定），跨组引用用相对链接，别复制正文。

### 改完怎么立刻看效果

- 行为包 / 演化公式：`python developer_tools/evolution_dump.py`（见
  [character_evolution.md](character_evolution.md) §6）。
- 小游戏：`python tests/check_minigame.py`，真渲染见
  `python scripts/dungeon_autopilot.py --scene mini-game-py`。
- 各类开发者工具的入口见 [`../developer_tools/README.md`](../../developer_tools/README.md)。

## 相关组

- 要在**副本里**用这些机制（章节 / 触发器 / 小游戏触发器）：见
  [`../Dungeon/README.md`](../Dungeon/README.md)。
- 要改**程序本身**（分层、契约、提交规范）：见 [`../dev/README.md`](../dev/README.md)。
- 某个**特定风格**的创作工作流**不在本目录**（判据 4）——风格所需的补充步骤随风格
  自身的特质不同，写下来只对一种风格成立，因此该由风格自身的维护约定承担。
