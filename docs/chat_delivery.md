# 角色聊天：消息生命周期与统一投递（阶段一、二）+ 话题状态机与离线恢复（阶段三、四）实施文档

对应总体方案（话题驱动社交化改造）：第一、二阶段为**消息生命周期与持久化**、
**多条回复与统一投递**（已实施）；第三、四阶段为**话题状态机**、**经历事件
与离线恢复 reconcile**（已实施，方案未明确处取粗糙实现）。第五阶段（行为
参数扩展、调试工具）留作未来扩展，不做实现。

## 1. 背景与目标

改造前的问题：

- AI 结果一旦生成就直接落入正式历史（`_finish_exchange` 立即 append），
  "读延迟 / 正在输入"只是 UI 假动作。延迟期间再发消息、或切换渲染时机，
  都会把尚未"到达"的回复提前画出来。
- 协议只有单个 `reply` 字符串，无法表达"一次回复多条消息"。
- 徽标语义混乱：专业模式累计事件数（重启即丢）、挂件模式混用
  "角色未读玩家消息"和"玩家未读角色回复"两种含义。
- 无角色级并发控制：专业 / 挂件两套界面各自持有 `ChatService` 实例，
  参数预热、发送、补话可能交叉写同一份 `chat.json`。
- 撤回按对象身份匹配消息；界面重载后对象换新，撤回会静默失效。

改造后的核心不变式：

1. **AI 已生成 ≠ 消息已送达 ≠ 玩家已读**。三个时刻分别落盘为
   `created_at / delivered_at / read_at`。
2. 消息只有进入 `delivered` 状态后才允许出现在任何界面上；
   `queued` 消息只存在于存档和"正在输入"状态里。
3. 徽标永远由存档重算（`delivered` 且未 `read` 的角色消息数），
   不再依赖事件累加。
4. 同一角色的所有写操作被进程级角色锁串行化；UI 主线程只做
   非阻塞尝试，绝不因 AI 请求卡住界面。

## 2. 总体结构

```
ChatService（协议、排程、写回、事件）
    │  append(status=queued, available_at=…) + 落盘
    ▼
ChatDeliveryScheduler（后台线程，单例）
    │  到点：queued → delivered，落盘并广播
    ▼
chat_events（message_delivered / chat_updated）
    │
    ├── ChatDeliveryController（两套界面共用的节奏控制器）
    │       到点重载聊天域 → 触发重渲染 → "正在输入"状态 → 标记已读
    └── 宿主界面徽标（由存档重算，不累加）
```

## 3. 消息生命周期（models.ChatMessage）

新增字段（旧字段 `read / silent` 保留，语义不变）：

| 字段 | 含义 |
|---|---|
| `id` | 稳定 UUID，落盘即生成；撤回、已读回执都按 id 匹配 |
| `batch_id` | 同一次 AI 决策产生的消息组 id（= 本次 request_id） |
| `sequence` | 组内顺序，从 0 起 |
| `status` | 角色消息：`queued → delivered → read`；玩家消息恒为空串（用 `read/silent`） |
| `available_at` | 计划展示时间（ISO）。`queued` 消息必填 |
| `delivered_at` | 实际投递时间 |
| `read_at` | 玩家读到的时间（打开聊天渲染后统一标记） |
| `seen_by_char_at` | 角色"看到"玩家消息的时间（含已读不回） |
| `silent_reason_internal` | 已读不回的内部原因，不展示给玩家 |

旧档兼容（`__post_init__` 规范化）：

- 旧消息没有 `id` → 载入时补 UUID；
- 旧角色消息没有 `status` → 视为 `read`（历史上早已完整展示过），
  避免迁移后全体误报未读；
- `ChatRepo.save` 写入 `"schema_version": 2`；`ChatState.from_dict`
  按字段名过滤，多余键静默忽略，向前兼容。

状态迁移只有两条路径，均由服务层执行、AI 无法直接操纵：

```
queued  --到点(scheduler/controller)-->  delivered  --玩家读到-->  read
```

"已读不回"不产生角色消息：玩家消息标记 `read=True, silent=True,
seen_by_char_at=…`，与改造前一致。

## 4. 协议 v2：一次决策多条消息

`_OUTPUT_RULE` 中 `reply` 升级为 `messages` 数组：

```json
{
    "messages": ["第一条", "可选：紧跟着再发一条"],
    "silent_reason": "已读不回时的真实原因；正常回复时省略",
    "ops": {"attitude": 0, "action_points": 0, "intrusion": 0,
             "destruction": 0, "casualties": 0},
    "nick": "可选：改变对玩家的称呼",
    "memory_note": "可选：值得长期记住的事"
}
```

- 1~4 条（`MAX_BATCH_MESSAGES`），每条截断到 200 字
  （`SINGLE_MESSAGE_CHAR_LIMIT`）；
- `messages` 缺失时回退读 `reply`（兼容弱模型与旧协议）；
- 空数组 / null = 已读不回；
- 非 JSON 回复仍视为 API 拒绝（`refused=True`），保留一次重试，
  与"角色主观沉默"区分。

排程（`schedule_batch_available_at`）：第一条 = 当前时间 + 读延迟 +
其打字延迟；之后每条在前一条的 `available_at` 上加自己的打字延迟。
读 / 打字延迟沿用原函数与在线强度分档倍率，服务层统一计算，
UI 不再自行模拟节奏。

## 5. 投递调度器（services/chat/delivery.py）

`ChatDeliveryScheduler`：模块级单例（`get_scheduler()`），daemon 线程。

- `notify_queued(giantess_id, available_at)`：有消息入列时登记最早到点
  时间并唤醒；
- 启动时 `ChatRepo.ids_with_queued()` 扫描上次会话遗留的 `queued`
  消息并接管——应用关闭期间到期的消息在下次启动时直接提升为
  `delivered`（延迟结算，不做逐分钟后台模拟）；
- 到点调用 `ChatService.deliver_due()`：`queued → delivered`、落盘、
  广播 `message_delivered`；若仍有未到点消息则自动登记下一次唤醒。

`ChatDeliveryController`：两套界面共用的节奏控制器（工具包无关）。
界面提供 `after(delay_ms, fn)` 与两个回调：

- `on_reload(chat_state)`：重载聊天域（含到点提升）后重渲染；
- `on_status(kind)`：`"typing"`（还有 `queued` 消息）/ `""`。

控制器每个 tick：`load_chat(blocking=False)` → 回调渲染 →
`mark_char_messages_read()`（`delivered → read`）→ 若仍有 queued
消息则按最早 `available_at` 安排下一次 after。attach / detach / refresh
用 token 取消过期回调。

## 6. 并发与一致性

- **角色级锁**：`chat_service` 模块级 `_CHAR_LOCKS`（跨实例共享，
  专业 / 挂件 / 调度器三方串行）。发送、补话、主动搭话、投递、标记已读
  全部持锁执行；锁覆盖 AI 调用全程。
- **主线程非阻塞**：UI 侧的 `load_chat / mark_char_messages_read`
  以 `blocking=False` 尝试；锁被占用（AI 进行中）时 `load_chat`
  返回 `None`，控制器保留旧状态 1 秒后重试，界面永不卡顿。
- **按 id 撤回**：`recall_exchange` 改为按 `id` 匹配，界面重载后
  依旧有效；仅允许删除 `queued` 角色消息和未被看到的玩家消息，
  已投递内容拒绝删除。
- **幂等**：投递与标记已读都按 `status` 条件迁移，事件重放、
  调度器与控制器同时触发同一批消息不会产生重复副作用。

## 7. 界面职责（改造后）

两套界面（`ui/exploration/chat_panel.py`、`ui/mini/screens.py`）：

- 渲染时跳过 `queued` / 非 `delivered|read` 的角色消息；
- 不再自行计算读 / 打字延迟，全部交给控制器；
- 发送、补话完成后只调用 `controller.refresh()`；
- 撤回窗口 = AI 应答完成前（角色已"看到"即关闭），比旧版
  （读延迟内）略短，但与数据语义一致。

徽标宿主（`exp_frame.py`、`ui/mini/app.py`）：

- 收到任意聊天事件时按存档重算 `pending_char_messages` 数量；
- 语义统一为"玩家未读的角色消息"；角色未读玩家消息的红点逻辑
  不再混入（离线积压仍由打开聊天时的自动补话处理）。

## 8. 事件

| 事件 | 时机 | 携带 |
|---|---|---|
| `chat_updated` | 消息入列 / 状态变更 / ops 写回 | `giantess_id`、`replied`、`ops_applied` |
| `message_delivered` | queued → delivered | `giantess_id`、`message_ids` |

订阅者依旧负责把回调投递回主线程；发布方不感知界面。

## 9. 阶段三：话题状态机（已实施）

### 数据模型（models.Topic / ChatState）

```text
Topic
- id / title（≤30 字）
- status: active | ended
- source: user | character | event
- started_at / last_activity_at / ended_at
- end_reason: natural（AI 收尾）| switched（切新话题）| stale（强制收束）
- turn_count
```

`ChatState` 新增 `topics`（列表，repo 侧保留最近 20 个）、
`active_topic_id`。旧档无这些字段 → 无活跃话题，行为退化为
"每次对话兜底建立隐式话题"，无需迁移脚本。

### 协议扩展（v3）

输出协议增加 `topic` 键（省略等同 `continue`）：

```json
{"action": "continue|start|switch|end", "title": "新话题标题（start/switch 必填）"}
```

系统提示词新增【当前话题】段落：进行中的话题及轮数、最近结束的
话题（避免重复）、以及"话题由你主导"的规则；AI 自主决定延续、
切换或结束。

### 服务层硬约束（粗糙实现，够用为先）

- 单话题 `MAX_TOPIC_TURNS = 20` 轮，达到即强制收束（stale）；
- `expire_stale_topic()`：进行中话题超过 `TOPIC_EXPIRE_HOURS = 6`
  小时未续聊，服务端直接收束（不需 AI 参与），reconcile 开头执行；
- 无活跃话题但 AI 产生了回复时，兜底建立隐式话题（title 取
  AI 给出的 title 或"自由闲聊"），保证对话不游离在状态机之外；
- 非回复型决策（已读不回）不计轮数。

## 10. 阶段四：经历事件与 reconcile（已实施）

### 经历事件（services/chat/experience_events.py）

从角色档案收集四类事件，每条有**确定性 id**（kind + 内容 md5 前缀），
消费记录存 `ChatState.consumed_event_ids`（repo 侧保留最近 200 个），
同一事件不会被反复提起：

| type | 来源 | 事件 id 依据 |
|---|---|---|
| news | `state.last_news` | 新闻正文（内容变化即新事件） |
| position | `state.position` | 位置文本 |
| evolution | 演化表最近 10 条有效记录（步进非 0，或离线恢复/聊天写入） | `changed_at` |
| ending | `achieved_endings` | `achieved_at + name` |

收集是纯函数；消费只在 AI 决策成功后由服务层执行，失败保持未消费、
下次打开继续。注入提示词的最多 5 条，其余静默消费。

### reconcile()：统一社交决策入口

`catchup_reply` / `proactive_message` 已删除，合并为一个入口：

```text
should_reconcile(state, chat_state)？   ← 未读积压 / 未消费事件 / 超过主动搭话门槛
    ↓ 是
expire_stale_topic()                    ← 服务端收束过期话题
    ↓
一次 AI 决策（合并指令）：
    - 有未读 → 回应积压（可合并/逐条/多条/沉默）
    - 有事件 → 决定要不要拿某件事开新话题
    - 都没有 → 纯主动搭话判断
    ↓ 成功
积压计已读 + 事件计已消费 + 话题落地 + 回复批量 queued 入列
（失败则一切保持原状，下次打开继续）
```

两套界面的"自动补话"统一改为：打开聊天 → `should_reconcile()` 成立
则后台调用 `reconcile()`，展示节奏仍由投递控制器接管。沉默也会刷新
`last_active_at`，天然形成主动搭话的冷却间隔。

## 11. 已知取舍与后续阶段（第五阶段留作扩展）

- 应用关闭后不产生"错过感"：离线期间到期的消息启动时一次性
  结算为已投递；reconcile 是打开聊天触发的"延迟结算"，不做
  逐分钟后台模拟，也不在关闭期间生成消息。
- 演化事件摘要是粗糙文本（来源 + 步进值），未做叙事化；
  新闻/位置只有"当前值"，没有历史轨迹。
- 隐式话题（AI 未表态时的兜底）标题固定"自由闲聊"，未来可让
  AI 在 memory_note 或独立字段里给出更好的标题。
- 主动搭话的间隔门槛仍只由 `boundary` 单参数控制；`initiative /
  topic_persistence / burstiness / event_sensitivity` 等行为参数、
  事件敏感度分档、调试日志与回归测试集，留作第五阶段扩展。
- `queued` 消息受 200 条历史上限裁剪保护，极端积压下可能被
  硬裁剪兜底删除（与旧行为一致）。
