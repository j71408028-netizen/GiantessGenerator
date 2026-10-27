# 聊天机制：玩家身份、话题类型与三轴坐标

> **这一篇回答什么问题**：角色和「某个人」聊天时，她此刻适合聊什么、为什么同一个人
> 换一个身份她就像不认识你、聊天又凭什么反过来改变世界。
>
> 这是**现状**（2026-10-27 已实现部分）。消息的生命周期、协议解析、投递节奏等实现细节
> 看 [`../dev/chat_delivery.md`](../dev/chat_delivery.md)；本篇只讲机制与调参入口。

一句话：聊天按 **玩家身份** 拆成互不相通的线程；每条线程用 **好感、紧张度、状态**
三轴坐标筛选 **话题类型**；AI 在候选类型里决定聊什么，并在回复后最多执行 **一个世界效果**。

---

## 1. 全景

```text
玩家身份 identity_id
      │  (giantess_id, identity_id) → 一条线程：消息 / 好感 / 记忆 / 话题 / 关系
      ▼
三轴坐标 好感 · 紧张度 · 状态      ← 由领域事实派生，AI 看不到数值
      ▼
话题类型候选（半径过滤 + 重复惩罚，最多 5 个）
      ▼
AI 决策：继续 / 开新话题 / 切换 / 结束 / 已读不回
      ├─ 消息（1~4 条 + 投递节奏）
      ├─ ops（好感、行动点、称呼）
      └─ 至多一个 effect ──→ 改世界事实 ──→ 下一轮坐标变化
```

三轴不是给 AI 优化的数值，而是**服务层决定「此刻提供哪些话题类型」**的输入；AI 只
看到定性描述。这条边界是整套机制的核心，改代码时不要破。

## 2. 玩家身份与线程

玩家可以拥有多个**身份**（马甲）。对角色而言它们是不同的人：她不共享消息、记忆、
话题、好感或关系，也不知道背后是同一个玩家。

作用域划分：

| 数据 | 作用域 |
|---|---|
| 好感 `attitude`、消息、记忆、称呼、未读、最后活跃 | 线程（角色 × 身份） |
| 话题列表 / 活跃话题 / 已消费经历事件 / 关系认知 `Relationship` | 线程 |
| 聊天习惯 `ChatParams`、状态 `size_state` | 角色 |
| 世界态势 `world_stance`、事件压力 | 世界（各线程读同一份） |
| 世界效果 | 角色 / 世界；日志记在线程里 |

- 身份列表存在 `data/user/identities.json`，默认身份恒在且排第一，新建身份自动成为
  当前身份；数量上限 12、名字 24 字。
- 线程存档是 `data/archives/<giantess_id>/chats/<identity_id>.json`；旧 `chat.json`
  算默认身份，读时回退、不迁移不删除。
- 未读按线程统计；角色卡徽标是各线程之和，**只是显示层聚合**，域内隔离不受影响。

实现与存储细节见 [`../dev/chat_delivery.md`](../dev/chat_delivery.md) §3。

## 3. 三轴坐标

坐标由 `services/chat/coordinates.py` 的纯函数派生，话题开始时算一次并存进
`Topic.coord` 作为快照；本轮交流中的 `ops` 与 `effect` 只影响下一轮。

| 轴 | 含义 | 来源 | 存盘 |
|---|---|---|---|
| 好感 `affection` | 亲近程度 | `(attitude + 100) / 200`，只由聊天 `ops.attitude` 改变 | 是（`attitude`） |
| 紧张度 `tension` | 世界如何对待巨大娘 + 她如何看待当前这个身份 | 世界态势 + 关系认知 + 近期事件压力 | 否，派生 |
| 状态 `size_state` | 她此刻对自身巨大化的接纳程度（高 = 接纳） | 演化坐标贴边 + 行动点不足 + 个性强度 | 否，派生 |

**紧张度**（`compute_tension`）：

```text
tension = clamp(0.6 × world_stance + 0.4 × identity_offset + 0.3 × event_pressure)
```

- `world_stance` 读 `settings.json` 的 `world_stance`，语义值 `worship 0.10 / accept 0.25 /
  coexist 0.45 / alert 0.70 / hostile 0.90`，缺省 `coexist`；构造 `ChatService` 时可用
  `world_stance_provider` 覆盖（世界包/测试）。尺度 `world_setting` 不参与映射。
- `identity_offset` 由当前身份的关系认知映射：`lover -0.15 / close -0.10 / friend -0.05 /
  stranger +0.05 / rival +0.15 / enemy +0.25`，未知与默认不额外加压。
- `event_pressure` 扫最近 50 行演化记录与 7 天窗口，按伤亡增量与坐标位移计权，排除纯
  步长衰减（`apply_step_decay`）行；旧聊天来源按 0.25 低权重计入；已达成结局也算事件。
  求和后按 48 小时半衰期衰减。

**状态**（`compute_size_state`）：

```text
size_state = clamp(0.5 + 0.4 × resilience − 0.5 × edge − 0.5 × ap_deficit)
```

`resilience` 是归一个性强度（越高越适应，缺性格时 0.6），`edge` 取介入度/破坏性里更
贴边的那个（0.5 软边界），`ap_deficit` 以行动点 50 为线。状态大部分时间偏高，只有贴边
或行动点吃紧时才明显下沉——低状态意味着「不适/危机」，对应的话题类型少而重。

**AI 看到的只有定性描述**：好感四档、紧张度四档、状态四档（见
`describe_affection / describe_tension / describe_size_state`）。坐标原值、类型坐标与半径
都不进提示词，避免 AI 把数值当台词或反向刷分。

## 4. 话题类型

`services/chat/topic_kinds.py` 的注册表是坐标上的「格点」。每个类型有坐标、半径、
增量提示词、标签、优先级与冷却字段；提示词是**叠加在人设之上的片段**，不是完整人设。

初始注册表（坐标顺序：好感 / 紧张度 / 状态）：

| id | 名称 | 坐标 | 语气方向 |
|---|---|---|---|
| `daily` | 初识/日常 | 0.50 / 0.35 / 0.75 | 轻松寒暄、观察、轻量试探；兜底类型 |
| `spar` | 博弈/施压 | 0.25 / 0.75 / 0.80 | 规则、挑衅、用尺寸压人，先停在言语层面 |
| `flirt` | 暧昧/亲昵 | 0.80 / 0.30 / 0.85 | 放松、调情、距离近但不讨好 |
| `reflect` | 思辨/抽离 | 0.70 / 0.30 / 0.30 | 用抽象讨论回避身体的不适 |
| `body` | 身体尺寸 | 0.80 / 0.75 / 0.85 | 尺寸、触及、占有，强调掌控感 |
| `memory` | 回忆/沉重 | 0.75 / 0.75 / 0.30 | 共同经历、创伤，语气沉、不强解 |
| `guard` | 戒备/恐惧 | 0.20 / 0.75 / 0.25 | 警告、驱逐、试探，随时可能结束 |
| `aloof` | 疏离/客套 | 0.20 / 0.25 / 0.30 | 冷淡、公事公办、回避私事 |
| `showoff` | 自得/炫耀 | 0.25 / 0.25 / 0.80 | 享受尺寸、带炫耀、保持距离 |

选择规则（`rank_topic_kinds` / `candidate_topic_kinds`）：

- 只保留**三轴欧氏距离 ≤ 该类型半径**（默认 0.35）的类型；坐标相近或完全重叠时多个
  类型同时入候选，**不强制选最近**。
- 排序看 `距离 + 重复惩罚`，再看优先级。重复惩罚对最近用过的类型按 0.08、逐次半衰叠加。
- 注入提示词的候选最多 5 个，每个只给名称与一句区别（`topic_kind_hint` 取提示词首句）。
- 无候选时回退 `daily`；AI 给出的 `topic.kind` 不在候选内就回退第一个候选。
- 当前话题坐标超出自身类型半径时，提示词会告诉 AI 可以结束或换类型。

## 5. 继续、切换、结束与沉默

协议里 `topic.action` 只有 `continue / start / switch / end`，一切都由 AI 自己判断，
不做关键词匹配：

- `MAX_TOPIC_TURNS = 20`：同一话题聊满 20 轮由服务端强制收束（`stale`）。
- `TOPIC_EXPIRE_HOURS = 6`：进行中话题超过 6 小时没续聊，`reconcile()` 开头直接收束，
  不需要 AI 参与。
- 沉默：`messages: []` + 非空 `silent_reason` 表示已读不回，仍会应用 `ops`、`memory_note`、
  `effect` 与话题状态，只是不排队消息。
- 复用类型不算复活旧话题：同一个 `TopicKind` 再次出现时是新的 `Topic` 实例，重新计时
  计轮数，旧的保持 `ended`。

## 6. 关系认知（身份更新）

每条线程带一份 `Relationship`（`kind / note / since / updated_at`），默认 `stranger`。
它回答「她认为对面是谁」，与好感可以背离：高好感也可能把你当猎物，低好感也可能把你
当同类。

- AI 在协议里给 `identity_update: {kind, note}`，但只在话题 `end` 或 `switch` 时落地；
  继续中、沉默轮、`stale` 强制收束都丢弃。
- `kind` 必须沿 `IDENTITY_TRANSITIONS` 白名单逐级变化（不能一轮从陌生人跳到恋人/敌人），
  非法则连同 note 整条丢弃；note 单独也可更新，截断到 200 字。
- 每个身份有 72 小时冷却，冷却期内再次提交被忽略。
- 它**只影响下一次话题选择时的紧张度**，不改变当前话题——这是「AI 不能直接写紧张度」
  的配套约束：只能改认知，紧张度仍由公式算。

## 7. 世界效果

`services/chat/effects.py`：AI 只输出 `effect: {tool, args}`，服务层按注册表校验、夹取、
执行并留审计日志。

| tool | args | 行为 |
|---|---|---|
| `self_view` | `{"text": "..."}` | 追加到 `intro_visible`，不改写旧介绍与 `intro_hidden`，单条 ≤ 500 字，重复内容不重复追加 |
| `step_nudge` | `{"axis": "intrusion\|destruction", "direction": -1\|0\|1}` | 经 `StateService.nudge_step_rates` 微调步长，单次约 ±0.02，接近步长上限时衰减 |

- 每轮最多一个；给成数组视为多个，整条丢弃。未知工具或非法参数只记失败日志，不重试、
  不回滚已经生成的回复。
- 每次执行（含失败）追加一条 `{id, tool, args, applied_at, result}` 到
  `ChatState.applied_effects`，保留最近 50 条随线程落盘。
- **没有**任何直接改 `tension` / `size_state` 的工具，它们只能由世界事实派生。
- `move` / `casualty` 后置，等机制稳定再开。

反馈回路靠四条压住：每轮一个、单次幅度极小、事件压力按时间衰减、聊天来源的事件在
紧张度里低权重。

一次交流的落地顺序固定为：`ops → memory_note → effect → 话题状态机 → identity_update →
保存 chat（线程）与 character（有改动时）`。坐标快照取在 `ops`/`effect` 之前，所以效果
从下一轮才影响话题选择。

## 8. 调参入口

| 想调什么 | 改哪里 |
|---|---|
| 世界整体对巨大娘的敌意 | `data/settings.json` 的 `world_stance`（或世界包注入 `world_stance_provider`） |
| 紧张度权重与衰减 | `services/chat/coordinates.py`：`TENSION_W_*`、`TENSION_TAU_HOURS`、`EVENT_SCAN_LIMIT`、`EVENT_SOURCE_WEIGHTS` |
| 状态权重与阈值 | 同文件：`AP_LOW`、`EDGE_MARGIN`、`W_R / W_E / W_A` |
| 关系认知的紧急度映射 | 同文件：`RELATIONSHIP_TENSION_OFFSETS` |
| 话题类型集 / 坐标 / 半径 / 提示词 | `services/chat/topic_kinds.py`：`TOPIC_KINDS`、`TOPIC_KIND_RADIUS`、`TOPIC_REPEAT_PENALTY`、`TOPIC_CANDIDATE_LIMIT` |
| 关系转移图与冷却 | `services/chat/__init__.py`：`IDENTITY_TRANSITIONS`、`IDENTITY_UPDATE_COOLDOWN_HOURS` |
| 话题轮数上限 / 过期 | 同文件：`MAX_TOPIC_TURNS`、`TOPIC_EXPIRE_HOURS` |
| 效果工具与幅度 | `services/chat/effects.py`：`EFFECT_HANDLERS`、`SELF_VIEW_CHAR_LIMIT`、`STEP_NUDGE_AMOUNT` |

改完至少跑这些守卫（离线）：

```bash
python tests/run_checks.py check_chat_coordinates check_chat_topics \
    check_chat_topic_selection check_chat_effects check_chat_identity_tension \
    check_chat_identities check_chat_threads
```

## 9. 已知边界

- **行为包覆盖未做**：`chat.*` 钩子等类型注册表与 effect handler 接口稳定后再加，现在
  不 bump `BEHAVIOR_API_VERSION`。
- `move` / `casualty` 两个效果后置；`cooldown` 字段在注册表里已预留但暂未生效。
- `ChatParams` 仍在每条线程的 `ChatState` 里，尚未抽成角色级 `chat_profile.json`（多身份
  下每个身份可能各决定一次聊天习惯）。
- 状态长期偏高是待观察的调参问题：若实测贴住 1，把 `W_E / W_A` 调大或把状态中心从 0.5
  下调。
- 跨身份认知（「她知道 A 和 B 是同一个人」）明确不做，属于未来功能。

## 相关

- 消息生命周期、协议解析、投递与界面职责：[`../dev/chat_delivery.md`](../dev/chat_delivery.md)
- 演化坐标、步长与不适应性衰减：[`character_evolution.md`](character_evolution.md)
- 行为包能覆盖什么：[`behaviors/dev-guide.md`](behaviors/dev-guide.md)
