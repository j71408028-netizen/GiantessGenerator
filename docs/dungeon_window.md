# 副本窗口（DearPyGui）逻辑与开发事项

副本模式窗口由 Tkinter 主程序宿主、在 Tk 主线程内运行的 DearPyGui（下称 DPG）
会话组成。本文记录当前实现逻辑、线程模型，以及开发时必须遵守的约束和已踩过
的坑。改动 `dungeon/` 下任何代码前请先读第 5 节。

## 1. 文件地图

| 文件 | 职责 |
|------|------|
| `dungeon/window/__init__.py` | 用 mixin 组装出 `DungeonSessionWindow` |
| `dungeon/window/base.py` | 构造函数与生命周期：会话初始化、入口/会话阶段切换、关闭路径、视口尺寸 |
| `dungeon/window/ui.py` | DPG 上下文/视口/主窗口构建、文本显示、布局自适应（`_relayout`）、输入事件 |
| `dungeon/window/engine.py` | 推进核心：AI 流式生成、JSON 解析、伤亡结算、回放步进、窗口关闭回调 `_on_close` |
| `dungeon/window/triggers.py` | 触发器判定（所在章节/前置/条件）、章节进入、短暂视效、插入段落 |
| `dungeon/window/options.py` | 选项触发器：后台生成选项文字并弹出选择弹窗 |
| `dungeon/window/ending.py` | 敏感效果结算与结局生成 |
| `dungeon/window/persistence.py` | 统一收尾 `_finalize`、退出处理 `_handle_exit`、回放/报告保存（原子写）、结局索引（`data/user/endings.json`） |
| `dungeon/window/components.py` | 显示组件生命周期接入（构建/布局/刷新/销毁） |
| `dungeon/launcher.py` | 入口阶段混入 `DungeonLaunchStages`：动态背景轮播、方案选择面板、结局图标轮播、阶段切换 |
| `dungeon/background.py` | 背景图加载/旋转/模糊/淡入淡出/纹理更新 |
| `dungeon/dispatcher.py` | `_dispatch` 单例：后台线程 → DPG 主线程的更新队列（含看门狗） |
| `dungeon/components.py` | 显示组件注册表与官方组件包加载 |
| `dungeon/prompts.py` | `DungeonPromptBuilder`：系统/用户提示词构建 |
| `dungeon/summary.py` | `StorySummarizer`：剧情压缩（概要 + 最近 N 段）注入提示词 |
| `dungeon/rules.py` `dungeon/models.py` | 演化规则、触发器条件规则、状态模型 |
| `dungeon/actions.py` `dungeon/chapters.py` | 触发器/章节的数据模型与动作类型注册表（见 `docs/dungeon_chapters.md`） |
| `dungeon/terms.py` | 领域术语与持久化契约常量（方案 vs 一局、`DEFAULT_SCENARIO_ID`、兼容读），见 `docs/domain_terms.md` |
| `persistence/scenario_repo.py` | 副本**方案**仓库：`data/packs/scenarios/<id>/` 的读写（读写根分离、旧目录自愈迁移、原子写） |

`DungeonSessionWindow` 的 MRO 顺序为
`DungeonWindowBase, DungeonLaunchStages, DungeonWindowUI, DungeonStoryEngine,
TriggerHandler, OptionHandler, EndingHandler, DungeonPersistence, ComponentHandler`。
新增方法时注意同名方法会被排在前面的 mixin 覆盖。

## 2. 生命周期

整个窗口的生命周期发生在 `DungeonWindowBase.__init__` 内：构造函数**同步阻塞**
直到窗口关闭才返回（调用方在 Tk 回调里同步 new 这个类）。主流程：

```
__init__
├─ 保存参数（注意：每个构造参数都必须存到 self 上，见第 5.2 节）
├─ 回放模式？ → _init_replay()（不建 AI 客户端）
│   无入口阶段？ → _init_session()（立即初始化）
│   有 scenario_ids？ → 延迟到入口选择后再初始化（_session_initialized 标记）
├─ _build_ui()          # create_context → 视口 → 主窗口 → 事件注册 → setup/show
├─ _enter_entry_phase() # 仅探索模式：动态背景 + 方案选择面板
│   或 _init_components() + _build_components()  # 挑战/回放：直接进会话
├─ parent.withdraw / start_dearpygui()   # ← 阻塞直到循环结束
├─ _dispatch.stop()、恢复主窗口、join 后台线程（各 0.5s 超时）
├─ _closing 且非入口退出 → _handle_exit()  # 未达结局→_finalize(False) 落盘「未完成」回放；已达成→询问保存回放
├─ destroy_context()
└─ 返回调用方；调用方读取 _launch_error / _launch_choice 决定是否提示或转回放
```

### 2.1 入口阶段 → 会话阶段（探索模式）

探索模式下入口页与正式会话共享同一个 DPG 上下文/视口/主窗口，切换不重建上下文：

- **进入入口**：`_enter_entry_phase()` 启动三组后台轮播（背景图随机轮播、结局
  图标轮播、Ken Burns 帧级运动），并构建右下角方案面板 + 左下角结局面板。
- **点“开始副本”**：`_enter_dungeon_phase()` → 冻结背景、销毁入口 UI、
  `_load_session_config()`（加载配置、扣 AP、`_init_session()` 建 AI 客户端与
  提示词）→ 构建组件 → 显示文本容器 → `check_triggers()`。任何一步失败都会
  置 `_launch_error` 并 `_close_loop()` 关窗，由调用方在 Tk 侧弹错误框。
- **点“加载回放”**：同样冻结背景后关窗，`_launch_choice = REPLAY_MARK`，
  由调用方弹文件选择框后以 `is_replay=True` 重新构造窗口。
- **点“返回”**：`_close_loop()` 关窗，不视为会话结束（见 `_exit_from_entry`）。

阶段标记的语义（都定义在 `__init__`，勿随意改名）：

| 标记 | 含义 |
|------|------|
| `_is_entry_phase` | 入口页可见中；事件回调据此忽略点击/按键 |
| `_entry_started` | 已完成一次进入选择，防止二次进入 |
| `_session_initialized` | `_init_session()` 是否已执行 |
| `_exit_from_entry` | 入口阶段退出（返回/加载回放/启动失败），`__init__` 据此跳过 `_handle_exit` |
| `_closing` | 已请求关闭；`_on_close` 与 `_close_loop` 都会置位 |
| `_launch_error` / `_launch_choice` | 关窗后传给调用方的结果 |

### 2.2 退出路径

| 入口 | 路径 | 说明 |
|------|------|------|
| 入口页“返回” | `_on_entry_cancel` → `_close_loop()` → `_request_close()` | 见第 5.1 节，**不可直接调 `dpg.stop_dearpygui()`** |
| 会话中点窗口 X | DPG 原生关闭 → 最后一帧执行 `_on_close`（`ui.py` 注册的 exit callback） | `_on_close` 置 `_closing`、入口阶段则置 `_exit_from_entry`、停 `_dispatch` |
| 回放播完 | `_replay_next_step` → `_request_close()` | 同样走 WM_CLOSE 路径 |
| 会话未触发结局就退出 | `_handle_exit()` → `_finalize(completed=False)` | 落盘「未完成」回放与报告（不再整局丢弃）；**不**写 `endings.json`、不记挑战达成 |

### 2.3 收尾（`_finalize`）

副本收尾只有一个入口：`dungeon/window/persistence.py::_finalize(completed, reason)`，
三条结束路径都走它（`_finalized` 标记保证幂等）。

| 路径 | 调用点 | completed |
|------|--------|-----------|
| 结局生成结束（章末终止 / 旧 `ending` 触发器） | `ending.py::_generate_ending` 的 finally | `True` |
| 会话中直接点 X 退出（未触发结局） | `persistence.py::_handle_exit` | `False` |
| 点 X 时结局已触发、文本仍在生成 | `_handle_exit`（join 超时分支） | `True`（结局文本兜底为结局名） |
| 生成异常（`engine._note_session_error`） | 不结束会话，异常记入 `_session_errors` 并显示在故事区 | — |

- `completed=True`：结算结局增量 → 记录重要结局索引 → 按设置自动保存回放
  （与收尾重构前的 `_generate_ending` 尾部等价）。
- `completed=False`：**不**结算结局增量、**不**写 `data/user/endings.json`、
  **不**记挑战达成——没真正走完的一局不能算达成；但把已生成的内容落盘为
  「未完成」回放与报告（文件名带 `_未完成`，报告头部标注 `【未完成】` 与原因，
  中断退出时弹窗给出保存路径）。会话没有任何内容时只提示、不落盘。
- 落盘位置：有角色（探索模式）写 `data/archives/<角色>/回放|报告`；
  无角色或挑战模式写 `data/user/replays`、`data/user/reports`。
- 回放文件仍是 `[记录, ...]` 数组、后缀仍为 `.replay.json`，回放加载
  （`_init_replay`）与档案导出（`archive_export`）照常识别。

## 3. 线程模型

**只有主线程（Tk 回调线程）允许直接调用 DPG API 做 UI 更新**。后台线程一律通过
`_dispatch.enqueue(fn, *args)` 把更新投递到主线程；`_dispatch` 是模块级单例，
`install()` 后用 `set_frame_callback` 自递归链每 3 帧清空队列，另有 1s 周期的
看门狗在队列超过 1.5s 未处理时重注册帧回调（防卡死）。窗口关闭时 `_on_close`
调用 `_dispatch.stop()` 让看门狗退出。

现存后台线程（全部 daemon，退出依赖 `_closing` / `_is_entry_phase` 标志，**循环
体内 sleep 后必须再查一次标志**）：

| 线程 | 启动处 | 作用 |
|------|--------|------|
| 背景图轮播 | `launcher._start_background_cycle` | 随机间隔换入口背景（enqueue `_switch_background`） |
| 结局图标轮播 | `launcher._start_ending_cycle` | 3.2s 换一张已通关结局图标 |
| 淡入淡出 | `background.change` 内部 | 24 步 × 0.03s 混合帧，enqueue `_set_bg_texture` |
| 背景重采样定时器 | `background.refresh` | `threading.Timer`，窗口 resize 后重裁背景 |
| AI 流式任务 | `engine._generate_next_text` | 流式拉取 AI 文本，每块 enqueue 刷新 |
| 选项文字生成 | `options._start_option_generation` | 后台为选项生成简短文字 |

后台线程里出现的 `join(timeout=...)` 只允许带超时，且不要在渲染回调内 join
（会阻塞帧处理，最长约 1s 的假死）。

## 4. 会话数据流（一步）

```
点击/空格（_on_mouse_click → _on_next_step）
├─ pending_option / pending_ending 存在 → 打断
├─ 仿流式输出进行中 → 立即补完本次动画，不推进内容
├─ 逻辑段落切出的显示段落未揭示完 → 揭示下一句（_reveal_pending_unit，仿流式上屏）
├─ 有非延迟插入段 → 直接消费显示（同样仿流式上屏）
└─ _generate_next_text()：后台线程
   ├─ prompt_builder.build_user_prompt → messages 追加
   ├─ ai_client.generate_stream：逐块 extract_stream_text → 内置分句器
   │  （dungeon/splitter.py）切分：首句流式显示、完整即定格；后续完成句
   │  排队到 _pending_units，点击后逐句揭示
   ├─ 完整响应 parse_final_json → 正文/方向/自定义方向；split_full_text
   │  定格最终切分
   ├─ dungeon_logic.evolve_attributes（含敏感倍率）→ 伤亡结算
   ├─ replay_data.append(step_info)          # 回放记录
   ├─ _apply_prompted_unlocks（写入角色尺寸解锁）
   ├─ _start_detail_query：立即后台询问 AI「想了解的细节」（不阻塞推进）
   └─ _finish_step → 记录阈值跨越关键事件 → check_triggers：
      章节跳转/插入段/选项弹窗/短暂视效/结局触发器（结局另起线程生成 → 保存）
```

**逻辑段落与显示段落**：一次 AI 输出（约100字）是一个逻辑段落，属性演化、
回放、剧情压缩、对话历史都以它为单位；内置分句器把它切成若干显示段落
（换行、对话引号闭合、句末标点、分号、破折号均为断点，引号内只认闭合
引号，截断处标点归一化），逐句展示只是为了阅读节奏，首句流式上屏，
其余句每次点击揭示一句（`_animate_reveal` 仿流式逐字上屏，再点一次
立即补完），队列耗尽后下一次点击才触发新的 AI 调用。插入触发器的段落
走同一条仿流式管线。

**细节探究**（`dungeon/details.py`）：流式输出完成后，立即用刚生成的段落
组装提问，后台线程询问 AI 还想了解哪些重要细节（最多 3 条简短问题，存入
`_detail_queries`）；玩家点击触发生成后续段落时，`build_user_prompt` 用
问题关键词在 replay 缓存（全部历史段落原文）中检索最相关段落，把命中
原文作为「细节补充」注入提示词，随后消费掉这批问题（未命中的直接丢弃，
提问调用进行中则跳过新一轮提问）。

会话开始时（`_enter_dungeon_phase`，挑战模式则在构造尾部）先调用
`_enter_start_chapter()` 进入起始章节，再 `check_triggers()`。触发器的完整条件是
「所在章节 + 前置触发器 + 条件规则」三者同时满足（见 `docs/dungeon_chapters.md`）。

- **结束章节**：`ending: true` 的章节内段落类型固定为「结局」（前缀 `【结局】`），
  步进为 0（`evolve_attributes(step_override=0.0)` 冻结属性演化），触发器不能跳出或
  弹出选项；节内段落数达到 `max_paragraphs` 时终止副本并按章节结算生成结局。
- **章节上限兜底**：普通章节节内段落数达到 `max_paragraphs`（默认 99）时，在
  `_finish_step` 末尾（触发器判定之后）自动跳转到 `overflow_target`（空串=离开章节）。
- **剧情压缩**：`StorySummarizer` 按块（默认 20 段）与换章时机压缩剧情，
  `build_user_prompt` 始终注入「全部压缩概要 + 关键事实 + 关键事件 + 最近 N 段
  原文」（N 可在设置调整）。压缩调用要求 AI 额外输出 `facts`（设定级关键事实，
  跨块去重合并成事实卡，只记定性信息）；确定性关键事件（章节进出、选项选择、
  介入度/破坏性跨整数阈值）由代码路径直接登记（`record_key_event`），不依赖 AI
  也不记敏感等纯数值。提示词还会带一行「当前章节：X（备注）」，让 AI 感知
  所处章节。

回放模式不建 AI 客户端，`_replay_next_step` 逐条重放 `loaded_replay`（`kind ==
"trigger"` 走 `_replay_trigger`，`kind == "chapter"` 走 `_replay_chapter` 复现章节进入）。

## 5. 已知开发事项（重要约束与坑）

### 5.1 禁止在 DPG 回调内直接调用 `dpg.stop_dearpygui()`

**症状**：窗口看似正常关闭，但进程回到 Tk 主循环后随即在 `_dearpygui.pyd` 内
access violation（事件日志 c0000005，随后常伴 c000041d 堆损坏）死亡，无任何
Python traceback；表现为整个应用“闪退”或假死。入口页“返回”按钮曾长期存在
此问题（2026-09-13 修复）。

**根因**：在控件/帧回调内 stop 会让渲染帧中途停止，`destroy_context()` 因 DPG
2.3.1 内部状态未正常收尾而破坏堆。点窗口 X 按钮不受影响——那走的是 DPG 原生
关闭路径（消息轮询阶段、帧间停止）。

**正确做法**：统一使用 `base._request_close()`——向视口窗口 `PostMessageW`
WM_CLOSE，让 DPG 走与点 X 相同的关闭流程（退出回调 `_on_close` 会在最后一帧
自动完成 `_closing`/`_exit_from_entry` 标记与 `_dispatch.stop()`）；非 Windows
回退为直接 stop。今后任何“程序主动关闭副本窗口”的需求都必须走它。

**验证方法**：真实应用中 点“进入副本”→“返回”→ 再点“进入副本”，确认
第二个窗口能打开且进程存活；也可查看 Windows 事件查看器 Application 日志
Id=1000 是否出现 `_dearpygui.pyd`。

### 5.2 构造参数必须保存到 self

`_init_session()` 在入口阶段是**迟到执行**的（点“开始副本”时才跑），此时
`__init__` 的局部参数早已不可见，只能用 `self.*`。曾因漏存 `merged_quips`
导致“开始副本”必然 `AttributeError`（且连锁导致 `prompt_builder` 缺失）。
给 `__init__` 增参时逐条确认有对应的 `self.xxx = xxx`。

### 5.3 `_dispatch` 是模块级单例

跨窗口实例共享：上一个窗口 `stop()` 后，下一个窗口 `install()` 会重新启用。
看门狗线程会从后台线程调用 `set_frame_callback`，依赖 `_installed` 标志退出——
不要绕过 `_dispatch.stop()` 直接销毁上下文，也不要在窗口存活期间让队列长时
无人处理（会触发看门狗频繁重注册）。

### 5.4 DPG 2.3.1 兼容性怪癖

- `dpg.add_child_window(..., no_scrollbar=...)` 等参数**创建时传入不生效**，
  必须创建后 `dpg.configure_item()` 再设一遍（见 `_build_ui` 对 `main_window`）。
- 本版本的 viewport 级 drawlist 不渲染 `draw_image`，背景图放在主窗口自己的
  drawlist（`bg_drawlist`）里；主窗口 `no_background=True`。
- 入口阶段的控件在 `_build_ui` 的容器块**之后**创建，DPG 推断不出父级，必须
  显式 `parent="main_window"`（见 `launcher._build_entry_ui`）。
- 动态纹理更新的既有模式：**尺寸一致时 `set_value`（接受 numpy float32 数组，
  ~30ms），尺寸变化时才 delete + `add_dynamic_texture`**（后者不接受 numpy，
  必须传 list，约 0.5s）。全屏图约 2.5k×1.6k，`pil_to_dpg` 已用 numpy 整块
  转换——不要改回逐像素 Python 推导（约 1s/次，是历史上入口首图延迟数秒、
  启动副本与触发器背景切换缓慢的根因）。改动背景相关代码时保持 revision
  检查（`_bg_revision`）以丢弃过期帧。
- 初始 `bg_texture` 按主窗口客户区尺寸（`_main_client_w/h`）创建，且
  `_layout_w/h` 初始也取客户区尺寸：保证首图应用与后续 relayout 都命中
  set_value 快速路径。视口外框尺寸（含标题栏/边框）只用于创建视口，参与
  布局会因客户区查询时机不同造成首图被 revision/尺寸守卫丢弃。
- 无过渡背景（`smooth_transition=False`，如入口首图 `_prime_background`）
  在 `background.change` 内**同步直接应用**，不走 Timer+队列——首图若经
  队列，常因视口未稳定、revision 被后续刷新顶掉而延迟数秒才显示。
- 窗口标题：创建时用临时标题 `DungeonSession`，首帧后 `_fix_windows_title`
  改为真实标题（绕过 DPG 标题缓存）。`_request_close` 找 hwnd 时两个标题都
  会尝试。
- `set_exit_callback` 的语义是“最后一帧运行”，而不是“点 X 时运行”；点 X
  由 DPG 原生停止循环，最后一帧才轮到我们的 `_on_close`。

### 5.5 入口阶段资源共享

入口页与会话页共用 `bg_texture`/`bg_image_item`：进入会话时 `_freeze_background`
把冻结帧设为 `_bg_pil_full`，后续 relayout 沿用冻结画面而非入口轮播原图。改
背景链路时注意 `smooth_transition` 与 revision 的交互（`background.change` 的
两条路径行为不同）。

入口背景图收集（`collect_dungeon_images`）按规范化路径去重——同一图片会经
副本仓库相对根与自由副本绝对根各采一次，不去重会导致轮播在同图的两个路径间
反复重载。只有一张图时轮播线程禁止 `random.randint(1, n-1)`（low>high 会抛
异常杀死线程）。

### 5.7 数据类字段一律按关键字构造

`DungeonState` 是数据类，字段顺序会随功能迭代变化（`chapter_steps` 就是从中间
插入的）。`clone()` 曾按位置一一传递字段，新增字段后参数整体错位——伤亡数组被
填成步长浮点数、总伤亡变成列表，于是**每一步** `_finish_step` 都在伤亡结算处抛
`'float' object is not iterable`：故事看起来还能生成文字，但总计数不再增长、
触发器与结局永不触发，副本实际卡在开局（日志里只有一行
`流式任务执行异常: ...`）。构造/复制 `DungeonState` 请只用关键字参数。

### 5.8 测试提示

- 涉及关闭路径的改动**必须**在真实应用中手测（最小 DPG 脚本复现不了 Tk 宿主
  + 完整线程栈的组合行为）。
- `python scripts/check_dungeon_finalize.py` 覆盖收尾路径与原子写
  （无 GUI、不碰真实 `data/`，32 项断言），改 `_finalize` / `json_store` /
  `scenario_repo` 后先跑它。
- 回放结束、入口返回、会话 X 关闭是三条独立路径，改 `_request_close` /
  `_on_close` / `_handle_exit` 时逐一回归。
- 控制台出现 `流式任务执行异常: '...xxx' object has no attribute ...` 通常是
  `_init_session` 没跑完（某属性缺失），先看完整 traceback 的第一个异常点。

### 5.9 写盘一律用原子写

副本配置、回放、报告与 `data/user/endings.json` 的写入统一走
`persistence/json_store.py`（同目录 `.tmp` → `fsync` → 覆盖前留一份 `.bak` →
`os.replace`），不要新写 `open(path, 'w')` + `json.dump`：编辑器保存方案时崩溃
或断电会把 `config.json` 截断清零，`.bak` 是唯一的回退（`scenario_repo.load_config`
在读取失败时会自动回退到它）。回放/报告按时间戳命名、本来就不会被覆盖，调用时
传 `backup=False`，避免产生无用 `.bak`。

### 5.10 术语：scenario 是方案，dungeon 是一局

`scenario_*`（`ScenarioRepo` / `scenario_id` / `scenario_config` /
`persistence/scenario_repo.py`）只指**副本方案**——作者编写的定义，存于
`data/packs/scenarios/<id>/`；`dungeon_*`（`DungeonSessionWindow` / `DungeonState`
/ `dungeon_state` / `dungeon_ended`）只指**一局**——运行时的会话、回放与结局
达成。详见 `docs/domain_terms.md` 与 `dungeon/terms.py`。新增代码请遵守这套
命名；持久化记录里引用方案 id 的字段一律写 `scenario_id`，读取时用
`dungeon.terms.scenario_id_of()` 兼容改名前的 `dungeon_id`。
