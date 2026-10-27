# 聊天话题类型：存档 → 提示词的翻译器

> **这一篇回答什么问题**：为什么"这个话题类型"不再只是一句语气提示，而是
> 决定"她此刻知道什么、注意什么、愿不愿意说"的那一层；以及想改某个类型的
> 信息口味时该动哪里。
>
> 这是**调整后的设计**（2026-10-27 落地）。类型本身的坐标、半径、语气与信息
> 视角定义在 `services/chat/topic_kinds.py` 的 `TopicKind`（**一个话题一条**）；
> 字段目录、预算裁剪与渲染在 `services/chat/topic_lens.py`。话题的候选排序、
> 状态机仍看 [`chat_topics.md`](chat_topics.md)，本篇讲"类型 → 信息"这一层。

一句话：**类型管两件事——这一刻该聊什么（坐标/候选），以及这一刻你被允许
知道什么（视角/字段/预算）**。存档不再是每轮全量塞进系统提示词的背景板。

---

## 1. 调整前的样子

话题类型注册表里每条只有名字、坐标、半径和一段 `prompt`。这段 `prompt`
实际只被用了一次：`topic_kind_hint()` 取它第一句，放进候选列表做"区别说明"。
**它从未完整进入提示词**——也就是说，九种类型在提示词里的差别只有那一句话。

与此同时，角色存档是**无条件全量注入**的：

| 位置 | 内容 | 是否随话题变化 |
|---|---|---|
| `【你的人设】` | 名字/昵称、性格、公开介绍、（好感达标时）隐藏介绍、标签、生日、意志状态、身高、尺寸定义（体型+适合/不适合的互动）、介入度、破坏性、位置、作息结论、活动范围结论、已测部位 | 否 |
| `【实时情境】` | 时段提示、所在区域评价、环境口语化描述 | 否 |
| 离线统一决策 | 未读消息、经历事件（新闻/位置/演化/结局） | 否（每次都塞） |

后果有三个：

1. **类型失去意义**。聊"博弈/施压"和聊"暧昧/亲昵"，她掌握的信息完全一样；
   高紧张度话题本该看世界反应与新闻，实际看到的还是一整份简历。
2. **私密信息没有闸门**。隐藏介绍只有一个 `attitude ≥ 40` 门槛，与她此刻
   在聊什么无关——聊戒备也会顺带拿到"她怕被人看见走路的样子"。
3. **不可审计**。"这个类型到底给了 AI 什么"只能人工读提示词，改文案没有
   回归测试可依。

## 2. 调整后的形状

```text
存档（CharacterSnapshot / ChatState / 世界态势）
   │  逐条登记，带层级与渲染器
   ▼
ArchiveField：public | contextual | secret        ← 谁有资格看到
   │  由当前话题类型申请（opens / denies / 权重）
   ▼
TopicKind：coord · frame（聊什么）· prompt（怎么写）· 预算 · 是否允许透露
   │  按权重贪心装进预算，同组限量
   ▼
【此刻你注意到的事】（类型名）
   注意力框架一句 + 只属于这个类型的几条事实 + 说话的体裁一句
   ＋ LensTrace（取用了哪些、挡掉了哪些、为什么）
```

分工：

- `services/chat/topic_kinds.py` 是**唯一一处类型定义**：坐标、半径、框架、体裁、
  开放的字段与预算都在同一条 `TopicKind` 里，另留了整表替换的入口（§7.1）。
- `services/chat/topic_lens.py` **只管字段**：字段注册表、渲染器、预算裁剪与
  账本。它不认"有哪些话题"，只认"这条类型申请了哪些字段"。
- `services/chat/persona.py` 的 `build_identity_frame()` 只出身份层；
  `build_character_persona()` 仍是**完整人设**，留给报告 / 导出 / 一次性聊天
  参数判定，不进聊天提示词。

补上"没有活跃话题"的情形：这不是第十种类型，而是一个虚拟视角 `NO_TOPIC_LENS`
（`virtual=True`，只看此刻的处境），避免"没话题时反而全量注入"。

### 2.1 三个文案字段：frame > prompt > focus

| 字段 | 管什么 | 出现在哪 | 不该写什么 |
|---|---|---|---|
| `frame` | 此刻聊什么、注意力落在哪、行为边界 | 视角段标题下的第一行（永远在最前）；**候选行后半**取它的第一小句 | 句长、用词这类写法 |
| `prompt` | **体裁**：句长、节奏、用词层级（口语 / 书面 / 网络用语）、标点、要不要表情符号 | 只在视角段最后一行 `说话的体裁：…` | 话题内容、情绪态度 |
| `focus` | 选它之后会更关注什么（信息预告） | **只在候选列表**，行首（`信息｜姿态`） | 任何会被当指令执行的内容 |

候选行读作"**会关注什么｜聊起来什么姿态**"，两个字段各占一半，`prompt` 不再出现在
候选列表里——它只影响选中之后的写法，帮不上"选哪个"这个决定。

顺序与权重一致：**框架决定聊什么，体裁只决定怎么打字**。所以"挑衅、试探底线、
调情、警告赶人"这类内容全部归 `frame`；`prompt` 里写"语气带压迫感"就是错位——
它会让同一类型的注意力范围与说话腔调互相打架（表现为语气忽高忽低）。
全局规则（`_SYSTEM_TEMPLATE` 第 8 条）也写明了：两者冲突时以框架为准。

## 3. 三条硬规则

### 3.1 分层：没有代码路径可以无条件注入

| 层级 | 含义 | 谁能用 |
|---|---|---|
| `public` | 名字、性格、公开介绍、个性标签 | 身份层恒在；视角不得重复申请 |
| `contextual` | 身高、尺寸能力、已测部位、意志、位置、作息、活动范围、实时情境、新闻、世界态度、结局 | 只有当前话题类型的视角 |
| `secret` | 隐藏介绍、生日 | 视角开放 **且** `disclosure=True` **且** 好感 ≥ 40 |

`identity_frame()` 的注释即契约：它只能出 `public` 字段。自检里直接断言
"隐藏介绍 / 位置 / 已测尺寸 / 新闻 不出现在身份层"，所以"手滑加进去"会红。

### 3.2 预算：类型之间真的在抢位置

每个视角有字符预算（`TopicKind.budget`），只约束字段文本；标题、框架、体裁
行不被裁——"怎么说话"是必须的，"知道多少"才是可裁的。

- 排序分 = 视角给的权重 × 字段自身分量；
- 同组（`self / body / life / world / private / place`）最多 2 条，
  防止世界类字段霸占整个预算；
- 放不下就**整条挡掉**，不把一句话砍成半句（剩余空间 < `MIN_FIELD_CHARS`
  即停止塞）；
- 挡掉必须留原因：`empty / denied / gated / budget / group-cap / unregistered`。

`aloof`（疏离）预算 320 字、只开两条字段，`spar`（博弈）820 字、世界态度与
新闻在前——"疏离时话少信息也少"是可验证的，不是文案效果。

### 3.3 审计：每次渲染都有一本账

`render_lens()` 返回 `(文本, LensTrace)`。Trace 里是取用清单（字段、分数、
字数）、挡掉清单（字段、原因）、字段总量与整段长度，`summary()` 一行读完。

调试入口：

```bash
python developer_tools/lens_preview.py                 # 逐类型打印真实存档的账
python developer_tools/lens_preview.py 角色ID --kind spar --full   # 整份系统提示词
python developer_tools/chat_session_preview.py 角色ID  # 真聊：逐轮打印本轮那本账
python tests/check_chat_topic_lens.py                  # 机制守卫
```

`chat_session_preview.py` 是"真发消息"版的账本：每轮打印 `视角｜…` 一行（预算用的
是本轮实际那份）、话题怎么变、属性怎么动，`/lens` 看原文、`/topic KIND` 把类型锁死
后单测一套视角、`/attitude` 验好感门槛、`/reroll` 回到发送前重掷同一条。会话在
`_out/` 沙盒里跑，不写 `data/`。

## 4. 字段注册表

| id | 层 | 组 | 内容 | 分量 |
|---|---|---|---|---|
| `pub.name` / `pub.personality` / `pub.intro` / `pub.tags` | public | identity | 身份层四件套（由 `identity_frame` 渲染） | — |
| `self.size` | contextual | self | 身高 + 体型类别一句 | 3.0 |
| `self.scale` | contextual | body | 尺寸能做什么 / 不能做什么（互动尺度） | 1.4 |
| `self.edge` | contextual | self | 介入度、破坏性的定性描述 | 1.5 |
| `self.measured` | contextual | body | 已测量、心里有数的部位尺寸 | 2.0 |
| `self.will` | contextual | self | 她对自己这个规模的感觉（`will_status` 译成人话） | 1.0 |
| `self.greed` | contextual | self | "再大一点"的念头强度（`greed` 译成三档定性，0 不渲染） | 1.0 |
| `self.rhythm` | contextual | life | 从活动记录学到的作息 | 1.0 |
| `self.spatial` | contextual | life | 常活动地带与环境偏好 | 1.0 |
| `self.live` | contextual | life | 实时情境（时段 + 所在这一带） | 1.2 |
| `self.position` | contextual | place | 当前地址（紧凑格式） | 1.0 |
| `world.news` | contextual | world | 关于她的最近一条新闻（截 120 字） | 2.0 |
| `world.stance` | contextual | world | 外界对她的定性态度（五档语义） | 1.5 |
| `world.endings` | contextual | world | 已达成的重大事件（最近 3 条） | 1.2 |
| `world.travel` | contextual | world | 旅居过的世界包名（`world_history` 最近 3 条） | 1.0 |
| `private.hidden` | **secret** | private | 隐藏介绍，以"平时不主动说"的口吻给出 | 2.5 |
| `private.birthday` | **secret** | private | 生日 | 0.6 |

新增字段 = 加一条 `ArchiveField` + 在需要的视角 `opens` 里挂上；忘了挂，
它就永远不会出现（这正是设计意图）。

## 5. 视角配方（初始值）

坐标顺序仍为 好感 / 紧张度 / 状态；"是否透露"= `disclosure`，只有它为真时
secret 字段才可能进预算。

| 类型 | `focus`（候选列表里给 AI 看） | 开放字段（权重） | 预算 | 透露 |
|---|---|---|---|---|
| `open`（无话题） | 此刻的处境 | live 1.2 · position 1.0 · size 0.8 · news 0.7 · rhythm 0.5 | 520 | — |
| `daily` 初识/日常 | 眼前的小事与对方的近况 | live 1.3 · position 1.0 · size 0.8 · rhythm 0.8 · news 0.4 · birthday 0.3 | 700 | 否 |
| `spar` 博弈/施压 | 外界的反应、规则与底线 | stance 1.7 · news 1.5 · edge 1.5 · size 1.2 · measured 0.8（明确 deny hidden/birthday） | 820 | 否 |
| `flirt` 暧昧/亲昵 | 你自己那点没说出口的事 | hidden 1.9 · will 1.0 · live 1.0 · birthday 0.8 · size 0.6（deny news/stance） | 820 | **是** |
| `reflect` 思辨/抽离 | 你怎么看自己身上发生的变化 | will 1.5 · size 1.3 · edge 1.2 · rhythm 1.0 · endings 0.8（deny news） | 780 | 否 |
| `body` 身体/尺寸 | 你自己的尺寸与身体感受 | measured 2.0 · scale 2.2 · size 1.8 · edge 1.0 · live 0.5（deny news/stance/hidden） | 780 | 否 |
| `memory` 回忆/沉重 | 你们共同经历过的事 | endings 1.8 · hidden 1.2 · will 1.2 · spatial 0.8（deny news/measured） | 820 | **是** |
| `guard` 戒备/恐惧 | 这个人是否危险 | stance 1.8 · news 1.2 · edge 1.3 · size 0.8 · position 0.7（deny hidden/birthday） | 720 | 否 |
| `aloof` 疏离/客套 | 只有最基本的处境 | live 0.6 · size 0.5 | 320 | 否 |
| `showoff` 自得/炫耀 | 你有多大、能做什么 | size 2.0 · measured 1.8 · scale 1.6 · edge 1.6 · stance 0.8（deny hidden/news） | 780 | 否 |
| `sharing` 分享/发梗 | 他会不会也觉得这个好笑 | live 1.2 · position 0.4（deny news/stance/size/measured/hidden/birthday） | 260 | 否 |
| `talk` 闲谈/看法 | 一件跟她无关、但她有看法的事 | news 1.0 · will 0.8 · stance 0.4（deny hidden/birthday/measured） | 460 | 否 |
| `mundane` 学习/工作 | 普通人的一天怎么过 | live 1.4 · rhythm 1.0 · position 0.8 · spatial 0.5（deny news/stance/hidden/size/measured/edge） | 480 | 否 |
| `elders` 客套/应答 | 像被长辈问话一样应付 | live 0.6（deny news/stance/endings/hidden/birthday/size/measured） | 240 | 否 |
| `plan` 打算/规划 | 你接下来打算怎么办 | will 1.6 · spatial 1.2 · position 0.8（deny news/stance/hidden） | 520 | 否 |
| `encounter` 异界见闻 | 别处传来的留言，或你碰见过的东西 | travel 2.2 · news 1.0 · position 0.8（deny hidden/birthday/measured） | 560 | 否 |
| `observation` 个人观察 | 城市与山川是怎么长的，人遇到危险会怎么做 | position 1.6 · spatial 1.4 · edge 1.2 · news 0.8 · size 0.6（deny hidden/birthday/stance） | 720 | 否 |
| `hobby` 个人爱好 | 你自己愿意花时间的事 | spatial 1.4 · rhythm 1.2 · live 1.0 · position 0.6（deny news/stance/endings/hidden/birthday/size/measured） | 540 | 否 |
| `desire` 个人欲望 | 你真正想要、但未必说得出的事 | hidden 1.8 · will 1.6 · greed 1.2（deny news/stance/measured/size） | 700 | **是** |

后九行是 2026-10-28 加入的**试运行批次**（未定版）：`sharing` / `talk` / `mundane` /
`elders` / `plan` / `encounter` / `observation` / `hobby` / `desire` 都只回答"聊什么"，
不带个人状态；`elders` 甚至不给信息（`live` 一条），靠体裁成型。`encounter` 用到了为它
新增的 `world.travel`，`desire` 用到了 `self.greed`；`hobby` 没有专属存档字段，靠
`spatial`（常去哪、环境偏好）与 `rhythm`（作息）侧写，所以 `frame` 明确要求"别新编
一个爱好"。

三类信息的口味差别一眼可见：

- **外部压力型**（`spar` / `guard` / `showoff`）：世界态度 + 新闻 + 影响面；
  私事关死。
- **自我审视型**（`body` / `reflect` / `memory`）：身体、意志、经历；
  新闻关死，`memory` 允许在回忆里透一点秘密。
- **关系型**（`daily` / `flirt` / `aloof`）：从"眼前的小事"到"没说出口的事"
  到"什么都不给"，同一套存档，三种密度。
- **日常话题型**（`sharing` / `talk` / `mundane` / `plan` / `encounter` /
  `observation` / `hobby` / `desire`）：只给生活侧写（位置、常活动范围、作息、
  旅居过的世界、念头），不给外部敌意；身体数据基本关闭，`observation` 只借
  `size` 当参照。`desire` 是这一组里唯一会用 `private.hidden` 的一条。

### 5.1 体裁（`prompt`）初始值

`prompt` 只描述**怎么打字**，不描述态度与话题；它渲染在视角段最后一行。
同一个角色在 `reflect` 下写长句书面语、在 `aloof` 下能一个字回答，都是体裁的
差别，不是人格变了。

| 类型 | 体裁 |
|---|---|
| `open`（无话题） | 短句、口语，想到什么说什么 |
| `daily` | 短句、口语、句子松散，允许语气词与少量网络用语 |
| `spar` | 短、收得干净，陈述句与命令句，少语气词、不用表情符号 |
| `flirt` | 短句、跳着说，半句与省略号，语气词多 |
| `reflect` | 长句、偏书面语，可自问自答与破折号，不用网络用语 |
| `body` | 短句、分得碎，动词与具体名词多，节奏慢 |
| `memory` | 句子偏长、节奏慢，省略号与停顿，口语但克制 |
| `guard` | 极短，常只有几个字，陈述与命令，不解释 |
| `aloof` | 能一个字就不写一句，书面、客气，无语气词 |
| `showoff` | 短句、节奏快，可夸张反问，口语 |
| `sharing` | 一两句、短跳、没头没尾，网络用语与缩写，标点能省就省 |
| `talk` | 句子完整、节奏平，可比喻反问，不用烂梗也不说教 |
| `mundane` | 平铺直叙，句子完整，具体名词多，少形容词 |
| `elders` | 礼貌短句，万能回答，不反问、不接话头 |
| `plan` | 中等长度、陈述为主，「打算」「准备」「还没想好」 |
| `encounter` | 叙述为主、句子偏长，可插一句判断，不用「系统/设定」 |
| `observation` | 中等长度，以看到的东西为主，具体名词多，判断放在后面 |
| `hobby` | 松、口语，说细节不说意义，可以停顿补一句「就是……」 |
| `desire` | 句子可以断，说到一半停住；少解释、不承诺 |

三类话题的体裁也跟着分成三档：外部压力型偏**短促、克制**，自我审视型偏
**长句、书面**，关系型从**松散口语**到**极简客套**。

## 6. 与坐标、候选的关系

坐标（好感 / 紧张度 / 状态）决定**此刻哪些类型够得着**（半径过滤 + 重复惩罚，
见 `chat_topics.md` §4）；视角决定**够得着的类型各自给什么信息**。两者不交叉：
视角不读坐标来选字段，也不会因为坐标高就多给。

唯一的耦合是 `secret` 的好感门槛（`HIDDEN_INTRO_ATTITUDE = 40`）：坐标不够，
即便类型允许透露也拿不到——"关系到了 + 聊到了"两个条件都要满足。

候选列表每行现在是两段：信息预告（`focus`）+ 姿态（`topic_kind_hint`，取 `frame`
的第一小句）。

```text
  - spar（博弈/施压）：外界的反应、规则与底线｜你在掂量对方：他敢到哪一步、外面的世界又是怎么议论你的
  - flirt（暧昧/亲昵）：你自己那点没说出口的事｜你想让他多知道一点你，但不会一次说完
```

于是 **AI 选类型 = 选一套关注点 + 一副姿态 + 一套写法**（写法由 `prompt` 在选中
之后生效）。这也让"换了话题她却还在念同一份简历"这类穿帮在机制上消失。

## 7. 接线与调参入口

一个话题的全部定义（坐标 + 半径 + 框架 + 体裁 + 视角）都在
`services/chat/topic_kinds.py` 的同一条 `TopicKind` 里，"改类型"和"改视角"
不再是两处。

| 想调什么 | 改哪里 |
|---|---|
| 新增 / 删除一个话题 | `services/chat/topic_kinds.py`：`TOPIC_KINDS` 里加或删一条 `TopicKind` |
| 某个类型给什么信息、多少字 | 同一条条目的 `opens` / `denies` / `budget` / `disclosure` |
| 某个类型聊什么、注意什么、行为边界 | 同一条条目的 `frame`（权重最高，写在最前面） |
| 某个类型的句长 / 用词 / 标点 | 同一条条目的 `prompt`（只管体裁，不改变聊什么） |
| 类型的坐标、半径、优先级 | 同一条条目的 `coord` / `radius` / `priority`（排序见 `chat_topics.md` §4） |
| 无话题时的信息量 | 同文件：`NO_TOPIC_LENS` |
| 新增/改写一个存档字段 | `services/chat/topic_lens.py`：`ARCHIVE_FIELDS` + 渲染器 |
| 隐藏介绍门槛 | 同文件：`HIDDEN_INTRO_ATTITUDE`（`services/chat.REVEAL_ATTITUDE` 为同值别名） |
| 同组限量 / 最小碎片 | 同文件：`GROUP_CAP`、`MIN_FIELD_CHARS` |

改完至少跑：

```bash
python tests/run_checks.py check_chat_topic_lens check_chat_topics \
    check_chat_topic_selection check_chat_effects check_profile_text
```

### 7.1 增删一个话题（含世界包路径）

代码里增删：在 `TOPIC_KINDS` 加/删一条，`topics` 里的字段 id 必须是
`ARCHIVE_FIELDS` 已注册的；漏注册会在 `check_chat_topic_lens` 里报
`unregistered`，不会静默丢弃。

运行时整表替换（世界包将来走这条）：两个装卸函数 + 两个 JSON 互转。

```python
from services.chat.topic_kinds import (
    install_topic_kinds, reset_topic_kinds,
    topic_kind_from_dict, topic_kind_to_dict,
)

install_topic_kinds([...])   # 校验不过抛 ValueError，且不改动现有表
reset_topic_kinds()          # 还原代码里的默认表
```

- 条目可以是 `TopicKind`，也可以是 JSON dict（`topics/*.json` 的形状）；
  `topic_kind_to_dict` 导出的条目能原样导回。
- 表里带 `virtual` 的条目（通常就是无话题视角）会被摘出来当兜底，不参与候选，
  所以"整表导出再整表导入"可以原样往返。
- 校验分两层：`validate_topic_kinds` 管结构（id 重复、缺 frame/focus、
  opens/denies 自相矛盾），`topic_lens.lens_coverage` 管字段引用。
- **别在会话中途换表**：同一轮里两次取表可能拿到不同内容；世界包在加载时换。

## 8. 已知边界

- **切换有一轮延迟**：视角按"当前活跃话题的类型"渲染。AI 在回复里才决定
  `switch`，所以新类型的信息从下一轮起生效；第一轮仍用旧视角（或
  `NO_TOPIC_LENS`）。两段式请求能消掉这个延迟，但要多一次模型调用，暂不做。
- **一次性聊天参数判定仍读完整人设**：`ensure_chat_params` 用
  `build_character_persona(include_hidden=True)`，输出只有三个整数，是
  "读全量、吐标量"，不构成内容泄漏；如果以后改成生成文字，必须改走视角。
- **`memory` 摘要单独注入**：更早聊天的 `ChatState.memory` 仍作为独立系统
  消息跟在系统提示词后（见 `build_chat_messages`），没有做成字段——它是
  "前情"而不是"此刻注意到的事"，重复注入会挤占预算。
- **视角是静态表**：字段权重不随坐标强度连续变化（例如"极高紧张度"不会让
  `spar` 多给新闻）。要做就得引入权重调制函数，先观察手感再说。
- **位置只挂在部分视角**：`self.position` 属"处境"而非"人格"，`body` / `memory` /
  `showoff` 等视角刻意不给（聊身体和回忆时不需要知道邮编）。若实际对话里发现
  她"忘了自己在哪"，把它挂到该视角即可，不是机制缺陷。
- **世界包还没有 `topics` 资源类型**：整表替换与 JSON 解析的入口已就位
  （§7.1），但世界包清单（`persistence/world_pack.py`）还没声明这一类型、
  也没接进加载流程；目前增删话题只能改 `TOPIC_KINDS`。`chat.*` 行为包钩子
  同样还等接口稳定。
- **trace 只进测试与预览工具**：界面里还没有调试面板，排查线上穿帮要手动跑
  `lens_preview.py`（静态看账）或 `chat_session_preview.py`（真聊、逐轮看账）。

## 相关

- 话题类型的坐标、候选、状态机与关系认知：[`chat_topics.md`](chat_topics.md)
- 消息生命周期、协议解析、投递节奏：[`../dev/chat_delivery.md`](../dev/chat_delivery.md)
- 完整人设（报告 / 导出用）：`services/chat/persona.py` 的 `build_character_persona`
- 演化坐标与不适应性：[`character_evolution.md`](character_evolution.md)
