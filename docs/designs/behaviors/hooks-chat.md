# 行为包钩子：聊天（`chat`）

> 所属机制：[行为包](README.md)（玩家向）· [开发指南](dev-guide.md)。注册方式、
> 签名一致性与层级约定见开发指南的「注册 API」「Hook 目录与层级」。

聊天域的开放原则与世界包一致：**可以追加世界语境、改措辞与手感，不可重写
角色是谁**。世界包能改通讯媒介、世界背景、话题候选与回复节奏；不能改角色
姓名、性格、公开 / 隐藏介绍、履历，也不能绕过 JSON 输出协议与消息状态机。

## 提示词内容（第一批）

| 目标 | 类型 | 签名 / 默认 | 说明 |
|------|------|------|------|
| `chat.framing` | 函数 | `(state, chat_state) -> str`；默认「你正在手机聊天软件里与「{nick}」私聊……」 | 通讯媒介与开场设定。世界包可改成传讯术、终端、信件等；只改媒介，不改扮演对象。返回非字符串或空串时回退默认开场。 |
| `chat.world_context` | 函数 | `(state, chat_state, world_stance=DEFAULT_WORLD_STANCE) -> str`；默认 `""` | 追加世界背景段落。非空时渲染为「【世界背景】」块，放在身份层之后、视角层之前，超过 `WORLD_CONTEXT_LIMIT`（1200 字）截断。只追加，不替换【你是谁】。 |
| `chat.topic_lens` | 函数 | `(ctx: LensContext) -> (str, LensTrace)`；默认 `render_topic_lens` | 话题视角渲染。需要保留默认视角时先调用 `runtime.default("chat.topic_lens")(ctx)` 再追加世界专属观察；不要绕过 `LensContext` 直接读存档。返回类型不符时提示词组装回退默认实现。 |
| `chat.topic_candidates` | 函数 | `(chat_state, coord, limit=TOPIC_CANDIDATE_LIMIT) -> list[TopicKind]`；默认半径过滤 + 重复惩罚 + 兜底 | 只过滤 / 重排已注册类型；新增话题类型不在本批开放。服务层会过滤非 `TopicKind` 或无 id 的元素。 |
| `chat.topic_context` | 函数 | `(chat_state, coord=None, candidates=None) -> str`；默认当前话题 + 候选列表 | 当前话题与候选的措辞。`topic.kind` 仍只能填候选里的 id。 |
| `chat.relationship_text` | 函数 | `(chat_state) -> str`；默认关系档位文案 | 只改措辞，不改 `Relationship.kind` 语义与转移图。 |
| `chat.habits_text` | 函数 | `(chat_params=None) -> str`；默认三档聊天习惯 | 只改措辞，档位仍是 0 / 1 / 2。 |

## 节奏与坐标（第二批）

| 目标 | 类型 | 签名 / 默认 | 说明 |
|------|------|------|------|
| `chat.coord` | 函数 | `(state, chat_state, now=None, world_stance=DEFAULT_WORLD_STANCE) -> ChatCoord` | 三轴坐标（好感 / 紧张度 / 状态）计算。服务层经 `ChatCoord.from_values` 接收；返回非 `ChatCoord` 时回退坐标默认值，不会崩溃。 |
| `chat.should_proactive` | 函数 | `(chat_state) -> bool` | 是否满足主动搭话条件。只改门槛 / 条件，不改 reconcile 与投递流程。 |
| `chat.schedule_batch` | 函数 | `(texts, online=1) -> list[str]` | 为一批回复排定 `available_at` ISO 时间。必须与 `texts` 等长且时间递增；长度、类型或顺序不符时服务层回退默认实现。 |
| `chat.model.read_delay_range` | 常量 tuple | `(1.0, 2.5)` | 拿起手机到看到消息的读延迟区间（秒）。 |
| `chat.model.typing_delay_per_char` | 常量 float | `0.05` | 每字打字延迟（秒）。 |
| `chat.model.typing_delay_range` | 常量 tuple | `(0.5, 8.0)` | 打字延迟上下限（秒）。 |
| `chat.model.online_read_scale` | 常量 dict | `{0: 3.0, 1: 1.0, 2: 0.4}` | 在线强度对读延迟的倍率。 |
| `chat.model.online_typing_scale` | 常量 dict | `{0: 1.8, 1: 1.0, 2: 0.6}` | 在线强度对打字延迟的倍率。 |
| `chat.model.boundary_proactive_gap` | 常量 dict | `{0: 240, 1: 30, 2: 10}` | 聊天边界分档的主动搭话间隔（分钟）。 |

常量用 `runtime.override("chat.model.xxx", 值或返回值的可调用对象)` 注册，
读取侧统一走 `runtime.resolve_value("chat.model.xxx")`。数值需为有限的非负值；
坏值（含负数、NaN、类型不符）回退注册默认值，避免聊天发不出消息。

## 不开放

以下属于核心不变式，不进入钩子目录：

- `parse_chat_response` / `parse_chat_params`：JSON 协议与旧协议回退；
- `apply_chat_ops` / `CHAT_OPS_WHITELIST`：属性写回白名单；
- `apply_topic_decision` / `expire_stale_topic`：话题状态机与轮数 / 超时；
- `apply_identity_update` / `IDENTITY_TRANSITIONS` / 冷却：关系认知转移图；
- `ChatService` 的收发、撤回、reconcile、投递调度与 `ChatRepo` schema；
- `MAX_BATCH_MESSAGES` / `SINGLE_MESSAGE_CHAR_LIMIT`：协议容量；
- `build_identity_frame` / `build_character_persona`：角色身份事实；
- 一次性聊天参数判定（`ensure_chat_params`）：属于角色习性。

话题类型整表替换与聊天 effect handler 扩展也暂缓：它们需要行为包停用后的
全局注册回滚机制，或等世界包 `topics` 资源类型。

## 示例

```python
# data/static/behaviors/my_pack/chat_rules.py
from services.chat import chat_world_context


def world_context(state, chat_state, world_stance="coexist"):
    return ("浮空城悬在云海之上，城里的传讯术靠风铃传递。"
            "这里的人把巨大的存在称作「云柱」。")


def register(runtime):
    runtime.override("chat.world_context", world_context)
    runtime.override("chat.framing",
                     lambda state, chat_state: "你们正通过风铃传讯。")
```

需要保留默认视角时，在覆盖函数里委托：

```python
from core.behavior_runtime import get_runtime


def lens(ctx):
    text, trace = get_runtime().default("chat.topic_lens")(ctx)
    return text + "\n- 今天的风向：北风，云层很低。", trace
```

第一批与第二批的 key 均为 `since="2.0"`、`behavior_api` 仍为 `2`；旧程序
遇到未知的 `chat.*` key 只会拒绝该条注册并记入启用诊断，不会整包失效。
