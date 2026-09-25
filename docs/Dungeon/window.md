# 副本窗口（DearPyGui）索引

> **定位**：`dungeon/window/` 会话窗口的**文件地图、生命周期与约束清单**。
> 分层与领域层见 [架构说明](architecture.md)，配置结构见 [数据模型](script.md)，
> 架构改造背景见 [宿主边界与可移植性](window_host.md)，自检见 [调试自动化](window_automation.md)。
> **改动 `dungeon/` 下任何代码前请先扫 §5 约束清单。**

窗口由 Tkinter 主程序宿主、在 Tk 主线程内运行的 DearPyGui（下称 DPG）会话组成。
渲染帧由窗口自己驱动（`run()` 里的手动渲染循环），不用 `dpg.start_dearpygui()` 堵死宿主主循环。
窗口对宿主的全部依赖收敛在宿主端口 `dungeon/window/host.py`，因此 `dungeon/window/` 里没有任何
`tkinter` / `ui.*` import。

## 目录

| 节 | 内容 |
|---|---|
| §1 | 文件地图 |
| §2 | 生命周期（构造 / 运行 / 帧循环 / 阶段 / 退出 / 收尾） |
| §3 | 一步会话数据流 |
| §4 | 线程与帧时钟 |
| §5 | 约束清单（C1–C11） |
| §6 | 自检命令 |

---

## 1. 文件地图

| 文件 | 职责 |
|---|---|
| `dungeon/window/__init__.py` | 用 mixin 组装出 `DungeonSessionWindow` |
| `dungeon/window/base.py` | 构造（`__init__` 只存参数 + 会话初始化）与运行（`run()` 建 UI、帧循环 `_run_frame_loop`、收尾 `_finish_session`）、入口 / 会话阶段切换、关闭路径、视口尺寸（取自宿主端口） |
| `dungeon/window/host.py` | **宿主端口** `HostPort`（含无宿主缺省实现）：尺寸/DPI、宿主显隐、事件泵、收尾弹框、活动窗口登记、缺省字体、回放文件选择 |
| `ui/common/tk_host.py` | 宿主端口的 Tk/CTk 适配器 `TkHost`——**window 层唯一的 Tk 细节所在地** |
| `dungeon/window/ui.py` | DPG 上下文 / 视口 / 主窗口构建、文本显示、布局自适应（`_relayout`）、输入事件、**F12 过程日志开关** 与 **顶部轻通知窗**（`_notify`，窗口级常驻） |
| `dungeon/window/engine.py` | 推进核心：AI 流式生成、JSON 解析、伤亡结算、回放步进、关闭回调 `_on_close` |
| `dungeon/window/triggers.py` | 触发器判定（所在章节 / 前置 / 条件）、章节进入、短暂视效、插入段落 |
| `dungeon/window/options.py` | 选项触发器：后台生成选项文字并弹出选择弹窗 |
| `dungeon/window/ending.py` | 敏感效果结算与结局生成 |
| `dungeon/window/persistence.py` | 统一收尾 `_finalize`、退出处理 `_handle_exit`、回放 / 报告保存（原子写）、结局索引 `data/user/endings.json`；弹框一律经宿主端口 |
| `dungeon/window/components.py` | 显示组件生命周期接入（构建 / 布局 / 刷新 / 销毁） |
| `dungeon/window/launcher.py` | 入口阶段混入 `DungeonLaunchStages`：动态背景轮播、方案选择面板、结局图标轮播、阶段切换 |
| `dungeon/window/background.py` | 背景图加载 / 旋转 / 模糊 / 淡入淡出 / 纹理更新（含单工作者 `PixelWorker`） |
| `dungeon/window/frame.py` | 帧时钟 `FrameScheduler`（窗口实例成员 `self._frame`）：`every` / `after` / `cancel` / `call` / `tick` / `drain` / `stop`——window 层唯一的时间源 |
| `dungeon/window/result.py` | `SessionResult` 与原因常量：`run()` 的返回值，替代「读窗口私有属性」 |
| `dungeon/window/component_registry.py` | 显示组件注册表与官方组件包加载 |
| `dungeon/window/overlay.py` | **覆盖层服务面**（OverlayHandler）：`toggle_overlay / close_overlay / overlay_open / component`——文本主组件经 ctx 调出对话记录等模态浮层；打开期间进入阅读模态（点击只关闭浮层、剧情推进挂起），H 调出 / ESC 关闭 |

被本层消费的领域模块：

| 模块 | 用途 |
|---|---|
| `dungeon/prompts.py` | `DungeonPromptBuilder`：系统 / 用户提示词构建 |
| `dungeon/summary.py` | `StorySummarizer`：剧情压缩（概要 + 事实卡 + 关键事件）注入提示词 |
| `dungeon/details.py` | 细节探究提问与回放检索 |
| `dungeon/rules.py` `dungeon/models.py` | 演化规则、触发器条件规则、状态模型 |
| `dungeon/actions.py` `dungeon/chapters.py` | 触发器 / 章节数据模型与动作注册表（见 [数据模型](script.md)） |
| `dungeon/terms.py` | 领域术语与持久化契约常量（见 [术语表](domain_terms.md)） |
| `dungeon/schema.py` `dungeon/validate.py` | 方案字段单一真相源 + 结构化校验器 |
| `dungeon/process_log.py` | 过程日志：副本各处的过程消息（章节 / 触发器 / 预生成 / 收尾落盘……）经 `process_log.log()` 进入线程安全环形缓冲，窗口订阅后经帧时钟投递到「过程日志」面板；**副本层禁止再直接 `print`**（`host.py` 的无宿主弹框桩除外） |
| `persistence/scenario_repo.py` | 方案仓库：`data/packs/scenarios/<id>/` 的读写（读写根分离、旧目录自愈迁移、原子写、保存即校验） |

MRO 顺序：`DungeonWindowBase, DungeonLaunchStages, DungeonWindowUI, DungeonStoryEngine,
TriggerHandler, OptionHandler, EndingHandler, DungeonPersistence, ComponentHandler`。
新增方法时注意同名方法会被排在前面的 mixin 覆盖。

## 2. 生命周期

### 2.1 构造与运行分离

`__init__` 只保存参数并初始化会话内容，不碰 DPG；`run()` 建 UI、驱动帧循环、收尾，并返回 `SessionResult`。
调用方在 Tk 回调里 `result = DungeonSessionWindow(...).run()`，只看返回值就够了
（`result.failed` / `result.cancelled` / `result.succeeded`）；需要细看窗口内部状态时也可以自己留着实例引用
（自动驾驶自检就是这么做的）。

```
__init__
├─ 保存参数（每个构造参数都必须存到 self 上，见 §5-C3）
├─ 回放模式？ → _init_replay()（不建 AI 客户端）
│   无入口阶段？ → _init_session()（立即初始化）
│   有 scenario_ids？ → 延迟到入口选择后再初始化（_session_initialized 标记）

run() → SessionResult
├─ _register_with_parent()
├─ _start_session()
│  ├─ _build_ui()          # create_context → 视口 → 主窗口 → 事件注册 → setup/show
│  ├─ _enter_entry_phase() # 仅探索模式：动态背景 + 方案选择面板
│  │   或 _init_components() + _build_components()  # 挑战/回放：直接进会话
│  ├─ host.hide_window()
│  ├─ _frame.call(_fix_windows_title)   # 首帧：此时原生窗口已建好
│  └─ _run_frame_loop()    # ← 手动渲染
└─ _finish_session()    # _frame.stop() + 背景工作者 shutdown → 恢复主窗口 → join 结局线程 0.5s
   ├─ _closing 且非入口退出 → _handle_exit()
   └─ destroy_context() → _unregister_with_parent()
```

注：入口页「加载回放」不再重开窗口——同一生命周期内由 `_on_entry_replay()` 经端口
`open_replay_file()` 取数据后 `_enter_replay_phase()` 切 `is_replay`。

### 2.2 帧循环

每帧顺序固定：`_pump_host_events()` → `self._frame.tick()` → 判 `dpg.is_dearpygui_running()` → `render_dearpygui_frame()`（+ `time.sleep(FRAME_INTERVAL)`，默认 1/60s）。

- 先 tick 后判关闭：保证「关闭前最后一次 UI 更新」不丢
- 判关闭放在渲染前：`stop_dearpygui()` 后不再白渲染一帧
- 判定关闭**只能**用 `dpg.is_dearpygui_running()`（`is_viewport_ok()` 关闭后仍返回 True）

### 2.3 入口阶段 → 会话阶段（探索模式）

两者共享同一个 DPG 上下文 / 视口 / 主窗口，切换不重建上下文。

| 动作 | 路径 |
|---|---|
| 进入入口 | `_enter_entry_phase()` 启动三组后台轮播（背景图随机、结局图标、Ken Burns 帧级运动），构建右下角方案面板 + 左下角结局面板 |
| 点「开始副本」 | `_enter_dungeon_phase()` → 冻结背景 → 销毁入口 UI → `_load_session_config()`（加载配置、校验 error 级阻止进入、扣 AP、建 AI 客户端与提示词）→ 构建组件 → 显示文本容器 → `check_triggers()`。任一步失败置 `_launch_error` 并 `_close_loop()`，由调用方在 Tk 侧弹错误框 |
| 点「加载回放」 | 冻结背景后经宿主端口 `open_replay_file()` 取数据；取不到就**留在入口页**，取到就在同一 DPG 生命周期里切 `is_replay` |
| 点「返回」 | `_close_loop()` 关窗，不视为会话结束（见 `_exit_from_entry`） |

阶段标记（都定义在 `__init__`，勿随意改名）：

| 标记 | 含义 |
|---|---|
| `_is_entry_phase` | 入口页可见中；事件回调据此忽略点击 / 按键 |
| `_entry_started` | 已完成一次进入选择，防止二次进入 |
| `_session_initialized` | `_init_session()` 是否已执行 |
| `_exit_from_entry` | 入口阶段退出（返回 / 加载回放 / 启动失败），`_finish_session` 据此跳过 `_handle_exit` |
| `_closing` | 已请求关闭；`_on_close` 与 `_close_loop` 都会置位 |
| `_session_running` | `run()` 重入保护（宿主事件泵会回到调用方回调，避免嵌套建第二个 DPG 上下文） |

### 2.4 退出路径

| 入口 | 路径 | 说明 |
|---|---|---|
| 入口页「返回」 | `_on_entry_cancel` → `_close_loop()` → `_request_close()` → `dpg.stop_dearpygui()` | 帧循环下一轮看到 not running，走 `_on_close` 清理 |
| 会话中点窗口 X | DPG 原生关闭 | 同样由帧循环判定（**不再依赖 exit callback**，见 §5-C6） |
| 回放播完 | `_replay_next_step` → `_request_close()` | 同「返回」 |
| 会话未触发结局就退出 | `_handle_exit()` → `_finalize(completed=False)` | 落盘「未完成」回放与报告；**不**写 `endings.json`、不记挑战达成 |
| 宿主关闭整个应用 | `MainWindowManager.on_closing` → `dpg.stop_dearpygui()` → `os._exit(0)` | 帧循环先退出，`os._exit` 不再回来 |

三条程序化关闭路径都归到 `_request_close()`（见 §5-C1）。

### 2.5 收尾（`_finalize`）

唯一入口 `persistence.py::_finalize(completed, reason)`，`_finalized` 标记保证幂等。

| 路径 | 调用点 | `completed` |
|---|---|---|
| 结局生成结束（章末终止 / 旧 `ending` 触发器） | `EndingHandler._generate_ending` 的 finally | `True` |
| 会话中直接点 X 退出（未触发结局） | `_handle_exit` | `False` |
| 点 X 时结局已触发、文本仍在生成 | `_handle_exit`（join 超时分支） | `True`（结局文本兜底为结局名） |
| 生成异常 `engine._note_session_error` | 不结束会话，异常记入 `_session_errors` 并显示在故事区 | — |

- `completed=True`：结算结局增量 → 写 `data/user/endings.json` 结局索引 → 按设置自动保存回放。
- `completed=False`：**不**结算、**不**写 `endings.json`、**不**记挑战达成；已生成内容落盘为「未完成」回放与报告（文件名带 `_未完成`，报告头部标注 `【未完成】` 与原因，中断退出时弹窗给出保存路径）。会话没有任何内容时只提示、不落盘。
- 落盘位置：有角色（探索模式）写 `data/archives/<角色>/回放|报告`；无角色或挑战模式写 `data/user/replays`、`data/user/reports`。
- 回放文件仍是 `[记录, ...]` 数组、后缀仍为 `.replay.json`，回放加载与档案导出照常识别。

## 3. 一步会话数据流

```
点击 / 空格（_on_mouse_click → _on_next_step）
├─ pending_option / pending_ending 存在 → 打断
├─ 仿流式输出进行中 → 立即补完本次动画，不推进内容
├─ 逻辑段落切出的显示段落未揭示完 → 揭示下一句（_reveal_pending_unit）
├─ 有非延迟插入段 → 直接消费显示
└─ _generate_next_text()：后台线程
   ├─ _take_pregen_for_next：有预生成结果（或在途则等待）→ 直接采用，
   │     否则 prompt_builder.build_user_prompt → messages 追加
   ├─ ai_client.generate_stream → 逐块 extract_stream_text → split_stream_units
   │     首句流式上屏、完整即定格；后续完成句排队到 _pending_units
   ├─ 完整响应 parse_final_json → 正文 / 方向 / 自定义方向；split_full_text 定格最终切分
   ├─ dungeon_logic.evolve_attributes（含敏感倍率）→ 伤亡结算
   ├─ replay_data.append(step_info)          # 回放记录
   ├─ _apply_prompted_unlocks（写入角色尺寸解锁）
   ├─ _start_detail_query：立即后台询问 AI「想了解的细节」（不阻塞推进）
   └─ _finish_step → 记录阈值跨越关键事件 → check_triggers：
        章节跳转 / 插入段 / 选项弹窗 / 短暂视效 / 结局（结局另起线程生成 → 保存）
   └─ finally：下一次点击必然生成时 → _maybe_pregen_next 后台预生成下一段
```

| 概念 | 说明 |
|---|---|
| **逻辑段落 vs 显示段落** | 一次 AI 输出（约 100 字）是一个逻辑段落，属性演化、回放、剧情压缩、对话历史都以它为单位；内置分句器（`dungeon/splitter.py`）把它切成若干显示段落（换行、对话引号闭合、句末标点、分号、破折号为断点），逐句展示只为阅读节奏。队列耗尽后下一次点击才触发新的 AI 调用；插入触发器的段落走同一条仿流式管线 |
| **说话人标记** | Solea/Bulla 耦合等级的对话分支（`dialog`/`branch`，方案可配 `protagonist_title` 指定主角称呼）要求 AI 在对话句句首写 `@说话人@`（规则见 `dungeon/coupling.SPEAKER_MARKER_RULE`）；分句器解析进显示单元（`DisplayUnit.speaker`），`story_history` 条目带 `speaker` 键，UI 组件据此渲染名牌。落盘正文（回放/报告/概要）经 `strip_speaker_markers` 剥离标记，回放文件格式不变；Velum 等级正文无标记，行为不变 |
| **文本显示所有权** | 文本主组件由方案配置 `text_component` 字段**三选一**（`dungeon.schema.TEXT_COMPONENT_IDS`）：`text` **底部渐变式**（视口底部向上淡出的深色衬底上显示最近 N 句）、`text_card` **底部卡片式**（居中圆角半透明卡片）、`text_nvl` **全屏 NVL**（半透明覆盖层堆叠全部历史，可选衬线字体、可滚轮回看）；`components` 列表只放其余组件，主组件先建、z 序在底。行首标签优先用说话人、回退类型前缀，继续点击用闪烁 ▼ 提示。接管期间（`owns_text_display = True`）`_update_text_display` 跳过内置 `text_container` 管线、转调组件刷新链；`text`/`text_card` 屏幕只呈现当下几句，完整历史由回放/报告承接 |
| **覆盖层服务面** | 窗口向文本主组件提供受控调用：`ctx.toggle_overlay("log")` 调出**对话记录**（全量 `story_history` 快照、可滚动，居中半透明面板），`ctx.component(cid)` 只读访问兄弟组件实例；写操作与组件生命周期仍由窗口集中管理，主组件只拿到调度权而非所有权。覆盖层打开时为阅读模态：点击只关闭覆盖层（`ui.py::_on_mouse_click`），空格/回车推进在 `_on_next_step` 入口挂起 |
| **细节探究** | 流式输出完成后用刚生成的段落组装提问，后台线程询问 AI 还想了解哪些细节（最多 3 条，存 `_detail_queries`）；下次生成时用问题关键词在回放缓存中检索最相关段落作为「细节补充」注入提示词，随后消费掉这批问题 |
| **预演化** | 步进收尾后（`finally`，插入晋升之后）若下一次点击必然触发生成（无选项/插入/结局排队），立即后台预生成下一段，缓存 `(user_prompt, messages 快照, 原始响应, 段落类型)`；玩家点击时 `_take_pregen_for_next` 直接采用（在途则等待收养，不重复发请求），首句仿流式揭示。选项选择后、插入段消费后各补射一次；预生成失败自动回退实时路径并还原 `keyword_match_given`。调用次数不变，只是提前发出 |
| **章节与触发器** | 会话开始先 `_enter_start_chapter()` 进入起始章节再 `check_triggers()`；完整条件是「所在章节 + 前置触发器 + 条件规则」三者同时满足 |
| **结束章节** | 段落类型固定为「结局」（前缀 `【结局】`），步进 0（`evolve_attributes(step_override=0.0)`），触发器不能跳出或弹选项；节内段落数达到 `max_paragraphs` 时终止并结算 |
| **章节上限兜底** | 普通章节节内段落数达到 `max_paragraphs`（默认 99）时，在 `_finish_step` 末尾（触发器判定之后）自动跳到 `overflow_target`（空串 = 离开章节） |
| **剧情压缩** | `StorySummarizer` 按块（默认 20 段）与换章时机压缩；`build_user_prompt` 注入「全部压缩概要 + 关键事实 + 关键事件」。近期原文不再注入——对话历史（窗口大小即 `story_recent_count` 设置，`_message_window_size`）已逐字携带最近剧情，重复注入只会推高 prefill 与首字延迟。AI 额外输出 `facts`（跨块去重成事实卡）；确定性关键事件（章节进出、选项选择、介入度 / 破坏性跨整数阈值）由代码直接登记 |
| **回放模式** | 不建 AI 客户端，`_replay_next_step` 逐条重放 `loaded_replay`（`kind == "trigger"` → `_replay_trigger`，`kind == "chapter"` → `_replay_chapter`） |
| **过程日志（proc_log 组件）** | 右上角日志面板是官方组件包里的 `proc_log` 组件：方案在 `components` 里声明才构建（`_default` 已声明），默认收起，**F12** 切换展开 / 收起。数据源是 `dungeon/process_log.py`：组件 build 时订阅并取走缓冲积压（构造 / 会话初始化阶段的报错也会补显），此后任意线程的 `log()` 经订阅回调 → `ctx._frame.call()` 进主线程追加（上限 300 行，自动滚底）；组件 destroy 时退订。方案未配置该组件时按 F12 弹通知「日志查看已禁用」 |
| **轻通知窗** | 主窗口顶部居中的通知条（`ui.py::_notify`），窗口级常驻、**不受组件配置影响**；显示约 3 秒自动隐藏（`after` 同 key 任务互斥，连续通知覆盖前一条并重置计时）。副本内需要「提示但不必确认」的场景都可用它 |

## 4. 线程与帧时钟

**只有主线程（帧循环所在线程）允许直接调用 DPG API。** 后台线程一律通过
`self._frame.call(fn, *args)` 投递；队列由帧循环每帧 `tick()` 时 `drain()` 清空。
会话收尾 `_finish_session()` 会 `_frame.stop()`，此后 `call()` 返回 `False` 而不是抛异常，
后台线程不必再轮询 `_closing`。

帧时钟 API（`dungeon/window/frame.py::FrameScheduler`，窗口实例成员 `self._frame`）：

| 方法 | 用途 |
|---|---|
| `every(interval, fn, key=...)` | 周期性任务；同 key 天然互斥，掉帧不补跑 |
| `after(delay, fn, key=...)` | 一次性延时任务 |
| `cancel(key)` | 按 key 取消（替代 `threading.Timer.cancel`） |
| `call(fn, *args)` | 后台线程投递：下一帧 drain 时在主线程执行 |
| `tick(now=None)` | 帧循环每帧调一次；先跑到期任务，再 drain |
| `stop()` | 会话收尾：清空任务与队列，之后 `call` 一律丢弃 |

任务类型分工：

| 类别 | 成员 | 为什么还留在线程里 |
|---|---|---|
| **帧任务**（不开线程） | 入口背景轮播、结局图标轮播、resize 防抖、Ken Burns、仿流式出字 | 纯计时，退出不需要 join |
| **AI 类**（后台线程） | AI 流式生成、细节提问、选项文字生成、结局生成 | 真正吃 IO/CPU，会阻塞数秒；结果经 `_frame.call()` 回主线程 |
| **像素类**（单工作者） | `PixelWorker`（背景重采样与淡入淡出的像素混合） | `Image.resize` / `Image.blend` 是 CPU 密集；**节拍仍由帧任务决定**，工作者只干脏活 |

规则：窗口关闭的判定只在主线程；后台线程永远不要直接调 DPG（含 `stop_dearpygui`），
要关窗就 `_frame.call(self._close_loop)`。`join(timeout=...)` 只允许带超时，且不要在渲染回调内 join
（会阻塞帧处理，最长约 1s 的假死）。

## 5. 约束清单

| 编号 | 约束 | 原因 / 出处 | 验证 |
|---|---|---|---|
| **C1** | 任何「程序主动关闭副本窗口」都必须走 `base._request_close()`，业务代码不直接 `dpg.stop_dearpygui()` | 手动渲染下一次回调与下一帧之间隔着帧循环，`_request_close()` 直接 `stop_dearpygui()` 即可安全收尾；`FindWindowW` + `WM_CLOSE` 平台 hack 已整体删除 | `python scripts/dungeon_autopilot.py --scene session-close --isolate`（含 `--repeat 2`）；真应用手测「进入副本 → 返回 → 再进入副本」 |
| **C2** | `destroy_context()` **之后**不得调用任何 Tk API（`root.update()` / `destroy()` / `winfo_*`） | 会以 0xC0000005 直接死掉（无 traceback），新旧两种驱动都能复现；`_finish_session()` 的顺序（恢复主窗口 → join → `_handle_exit()` 弹框 → `destroy_context()` → 解除宿主登记）就是为此固定的 | 自检里 `scene_tk_host` 故意不销毁根窗口 |
| **C3** | `__init__` 的每个构造参数都必须存到 `self` 上 | `_init_session()` 在入口阶段**迟到执行**（点「开始副本」时才跑），此时局部变量早已不可见；曾因漏存 `merged_quips` 导致「开始副本」必然 AttributeError | 构造后读属性 / `entry-start` 场景 |
| **C4** | 跨线程 UI 更新走 `self._frame.call()`；禁止后台线程直接调 DPG | 帧时钟是窗口实例成员（随会话创建与停止），不是模块级单例；`stop()` 后 `call()` 返回 `False` 不抛异常 | `dungeon_autopilot.py` |
| **C5** | 计时类逻辑不开线程，用 `every` / `after` 帧任务 | 旧实现各自靠 `_closing` 标志轮询退出、收尾时逐个 join；现在退出不需要 join | 场景结束时断言 `threading.enumerate()` 无非 daemon 残留 |
| **C6** | DPG 2.3.1 兼容性怪癖，见 §5.1 子表 | 实测结论 | 手测 + 自动驾驶 |
| **C7** | 入口阶段与会话页共用 `bg_texture` / `bg_image_item` | 进入会话时 `_freeze_background` 把冻结帧设为 `_bg_pil_full`，后续 relayout 沿用冻结画面 | 手测入口 → 会话 |
| **C8** | window 层不许 import `tkinter` / `customtkinter` / `ui`；宿主能力一律经 `HostPort`，见 §5.2 端口表 | 换 UI 框架只需换适配器；自检不再需要打桩 `ui.common.dialogs` | `scripts/check_dungeon_layering.py` |
| **C9** | 构造 / 复制 `DungeonState` 只用关键字参数 | 字段顺序会随迭代变化（`chapter_steps` 就是从中间插入的）；`clone()` 曾按位置传参导致伤亡数组被填成步长浮点数，于是**每一步** `_finish_step` 都抛 `'float' object is not iterable`——故事看着还在出字，但总计数不增长、触发器与结局永不触发 | 断言「回放记录是否增长」 |
| **C10** | 写盘一律用原子写（`persistence/json_store.py`） | 半截文件不可恢复，`.bak` 是唯一回退；回放 / 报告按时间戳命名、本来不会被覆盖，调用时传 `backup=False` | `scripts/check_dungeon_finalize.py` |
| **C11** | 术语不混用：`scenario_*` = 方案，`dungeon_*` = 一局 | 见 [术语表](domain_terms.md) 与 `dungeon/terms.py` | `scripts/check_scenario_naming.py` |
| **C12** | 任何 Tk 交互前必须调用 `self._discard_pending_quit()` | 用户通过视口原生关闭键（X）退出时，GLFW 销毁原生窗口会导致 Windows 在本线程队列中留下一条 `WM_QUIT`；它不会被 Tk 消费，却会让 Windows 停止合成 `WM_TIMER`，造成随后的收尾提示模态对话框（`wait_window`）以及主窗口所有 `after` 计时器彻底收不到事件而表现为**弹出提示对话框后主窗口卡死**；收尾时在 `show_window()` / 弹框前由端口抛弃该残留消息 | `python scripts/dungeon_autopilot.py --scene native-close --isolate` |

### 5.1 C6：DPG 2.3.1 兼容性怪癖

| 现象 | 应对 |
|---|---|
| `dpg.add_child_window(..., no_scrollbar=...)` 等参数创建时传入不生效 | 创建后 `dpg.configure_item()` 再设一遍（见 `_build_ui` 对 `main_window`） |
| viewport 级 drawlist 不渲染 `draw_image` | 背景图放在主窗口自己的 drawlist（`bg_drawlist`）；主窗口 `no_background=True` |
| 入口阶段控件在容器块**之后**创建，DPG 推断不出父级 | 显式 `parent="main_window"`（见 `launcher._build_entry_ui`） |
| 动态纹理更新 | 尺寸一致时 `set_value`（接受 numpy float32，~30ms）；尺寸变化才 delete + `add_dynamic_texture`（不接受 numpy，必须传 list，约 0.5s）。`pil_to_dpg` 已用 numpy 整块转换，**不要改回逐像素 Python 推导**；保持 `_bg_revision` 检查丢弃过期帧 |
| 初始 `bg_texture` 尺寸 | 按主窗口**客户区**尺寸创建，`_layout_w/h` 初始也取客户区尺寸；视口外框尺寸只用于创建视口，参与布局会因客户区查询时机不同造成首图被守卫丢弃 |
| 无过渡背景（`smooth_transition=False`，如入口首图 `_prime_background`） | 在 `background.change` 内**同步直接应用**，不走 Timer + 队列，否则常因视口未稳定延迟数秒 |
| 窗口标题 | 创建时用临时标题 `DungeonSession`，首帧后 `_fix_windows_title` 改真实标题（绕过 DPG 标题缓存）；`_temp_title` 只用于这个修正 |
| `set_exit_callback` 语义 | 是「最后一帧运行」而非「点 X 时运行」；手动渲染下它**只在 `destroy_context()` 内部**触发，对清理太晚，故 `ui.py` 不再注册，关闭统一由帧循环判定 |
| 入口背景图收集 | `collect_dungeon_images` 按规范化路径去重（同一图会经副本仓库相对根与自由副本绝对根各采一次）；只有一张图时轮播禁止 `random.randint(1, n-1)`（low>high 会抛异常杀死线程） |

### 5.2 C8：宿主端口方法表

`dungeon/window/host.py::HostPort`（缺省实现 = 无宿主），Tk 实现 `ui/common/tk_host.py::TkHost`。

| 端口方法 | 用途 | Tk 实现 |
|---|---|---|
| `viewport_metrics()` | `(视口宽, 视口高, dpi_scale, 宿主客户区宽, 高)` | `winfo_toplevel/winfo_id/winfo_fpixels` + `GetAncestor`/`GetDpiForWindow`/`GetClientRect` |
| `hide_window()` / `show_window()` | 副本视口显示时藏起宿主、关闭后恢复 | `withdraw` / `deiconify` + `lift` |
| `pump_events()` | 帧循环里处理一次宿主事件（宿主不冻结） | `widget.update()` |
| `discard_pending_quit()` | 丢弃视口原生关闭（X）留下的退出残留消息（见 §5-C12） | Win32 下 `PeekMessageW(..., WM_QUIT, WM_QUIT, PM_REMOVE)` |
| `dialog(kind, title, message)` | 收尾提示 / 询问（`info`/`warning`/`error`/`ask`） | `ui.common.dialogs.*` |
| `open_replay_file()` | 入口页加载回放；缺省实现返回 `None`（等价于用户取消） | `filedialog` + JSON 校验（解析在适配器侧） |
| `register_active_window()` / `unregister_active_window()` | 让宿主整体退出时能找到活动副本窗口 | 沿 `master/parent` 链找持有 `_active_dungeon_window` 的主窗口 |
| `default_font()` | 段落字体缺省家族 | `ui.common.fonts.dungeon_font_default()` |

用法：

```python
DungeonSessionWindow(..., host=TkHost(self)).run()   # 探索模式 exp_frame / 挑战模式 challenge
DungeonSessionWindow(...).run()                      # 不传 = HostPort()，无宿主（自检）
```

注意：`dungeon/window/ui.py` 里仍有两处 Windows 平台代码（`_temp_title` 与 `_update_dpi_scale` 里的
`FindWindowW` / `GetDpiForWindow`），属于 DPG/Win32 细节而非宿主端口范畴，是后续可选的清理项。

## 6. 自检命令

| 改动范围 | 先跑 |
|---|---|
| 关闭路径、收尾、入口阶段、生命周期、帧时钟 | `python scripts/dungeon_autopilot.py`（5 场景 48 项断言）；单场景 `--scene <名字> --isolate`，连开关窗 `--scene session-close --repeat 2 --isolate` |
| `_finalize` / `json_store` / `scenario_repo` | `python scripts/check_dungeon_finalize.py`（无 GUI，32 项断言） |
| 分句器 / 说话人标记 | `python scripts/check_splitter.py` |
| 新增 import / 分层 | `python scripts/check_dungeon_layering.py` |
| schema / 校验器 | `python scripts/check_scenario_schema.py`；批量校验 `python scripts/validate_scenarios.py --errors-only` |

无 GUI 守卫（`check_dungeon_layering.py` / `check_dungeon_finalize.py` / `check_splitter.py` 等）+
真窗口冒烟（`dungeon_autopilot.py`）才算绿。判定方式见 [调试自动化](window_automation.md) §4：**按子进程输出判定，不看退出码**
（退出阶段可能挂死，已查明是环境现象而非代码缺陷，调查记录见
[退出挂死调查](history/exit_hang_investigation.md)）。

其它提示：

- 涉及关闭路径的改动**必须**在真实应用中手测（最小 DPG 脚本复现不了 Tk 宿主 + 完整线程栈的组合行为）。
- 回放结束、入口返回、会话 X 关闭是三条独立路径，改 `_request_close` / `_on_close` / `_handle_exit` 时逐一回归。
- 控制台出现 `流式任务执行异常: '...xxx' object has no attribute ...` 通常是 `_init_session` 没跑完（某属性缺失），先看完整 traceback 的第一个异常点。

---

## 历史

本文件只描述**现状**。以下档案记录窗口层是怎么变成现在这样的：

| 档案 | 内容 |
|---|---|
| [宿主改造档案](history/host_refactor.md) | 手动渲染、构造/运行分离、宿主端口、帧时钟、结果对象化的全过程与验收 |
| [退出挂死调查](history/exit_hang_investigation.md) | 进程退出挂死的复现矩阵与修正结论；`destroy_context()` 后碰 Tk 崩溃的实测（对应 C2） |

约束 C1–C11 里若出现「以前…现在…」的表述，只用于说明**为什么**要这样写，不构成历史记录。
