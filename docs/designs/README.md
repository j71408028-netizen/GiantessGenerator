# designs/：玩家与资源开发者

> **这一组回答什么问题**：这套东西是**怎么设计的**——地标之间怎么算距离、角色怎么随
> 故事演化、资源包能覆盖到什么程度、小游戏怎么接入。玩家想理解系统怎么运作、资源
> 开发者想扩展它，读的是**同一份知识**，所以这两类读者共用本目录。

## 成员

| 文档 | 主要读者 | 性质 |
|---|---|---|
| [address_system.md](address_system.md) | 资源开发者（地标 / 描述风格作者） | 现状 |
| [character_evolution.md](character_evolution.md) | 核心开发者 + 行为包作者 | 现状 |
| [world_pack_behaviors.md](world_pack_behaviors.md) | 世界包 / 行为包作者 | 现状 |
| [minigame.md](minigame.md) | 小游戏作者 | 现状 |

## 推荐阅读顺序

1. 想改**地标 / 地址**：读 [address_system.md](address_system.md)。
2. 想改**角色数值演化**：先读 [character_evolution.md](character_evolution.md)（全景），
   再读 [world_pack_behaviors.md](world_pack_behaviors.md)（可覆盖的代码入口）。
3. 想**扩展程序行为**而不改主程序：读 [world_pack_behaviors.md](world_pack_behaviors.md)。
4. 想做**小游戏**：读 [minigame.md](minigame.md)。

## 本组文档的写法：前半全景，后半开发事项

每一篇都分两半，读者按需读到一半即止：

| 文档 | 全景（读到哪够） | 开发事项（从哪继续） | 谁能改 |
|---|---|---|---|
| [address_system.md](address_system.md) | §操作规程 | 附录 A–C：地址格式与距离规则 | 资源开发者 |
| [character_evolution.md](character_evolution.md) | §1–§2：参与的量与分工 | §3–§6：公式、模拟工具 | 核心开发者 + 行为包作者 |
| [world_pack_behaviors.md](world_pack_behaviors.md) | §开发流程 | 附录 A–C：注册 API 与示例 | 资源开发者 |
| [minigame.md](minigame.md) | §1：两种后端 | §2+：包格式与作者 API | 小游戏作者 |

写新篇时照这个体裁：**玩家 / 使用者读到前一半能理解系统怎么运作，资源开发者接着被引到
「你能改哪里、改完怎么验证」。**

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
