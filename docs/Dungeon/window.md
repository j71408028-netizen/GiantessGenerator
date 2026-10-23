# 副本窗口（imgui-bundle）索引

> **定位**：`dungeon/window/` 会话窗口的**文件地图、生命周期与约束清单**。
> 分层与领域层见 [架构说明](architecture.md)，配置结构见 [数据模型](script.md)，
> 架构改造背景见 [宿主边界与可移植性](window_host.md)，自检见 [调试自动化](window_automation.md)。
> **改动 `dungeon/` 下任何代码前请先扫 §5 约束清单。**

窗口由 Tkinter 主程序拉起的**独立子进程**承载：父进程（Tk 主程序）经
`ui/hosting/dungeon_spawner.py` 序列化构造参数并 spawn 子进程，会话窗口在子进程里
跑 **imgui-bundle**（`hello_imgui` + GLFW/OpenGL，2026-10 前是 Dear PyGui，下称 DPG），
渲染后端独占子进程（见 [宿主边界与可移植性](window_host.md) §7）。
窗口起停收在 `dungeon/window/runner.py::WindowRunner.run()`——一次 `hello_imgui.run()`
内部建 GL 后端 / ImGui 上下文 / 视口与帧循环，返回时一并释放，不堵死宿主主循环。
窗口对宿主的全部依赖收敛在宿主端口 `dungeon/window/host.py`，因此 `dungeon/window/` 里没有任何
`tkinter` / `ui.*` import；§5 里约束进程内共存的那几条现在只约束**子进程内部**
（父进程从不 import 副本窗口栈）。

> **术语边界**：本文的「会话（Session）」= `DungeonSessionWindow` 从构造到 `run()` 返回的
> 一次窗口生命周期；「一局（Run）」= 进入正式运行阶段后的一次游戏。入口页返回 / 启动失败
> 只结束 Session，不产生 Run；回放 Session 只重放已有 Run。命名见[术语表](domain_terms.md)。

## 目录

| 节 | 内容 |
|---|---|
| §1 | 文件地图 |
| §2 | 生命周期（构造 / 运行 / 帧循环 / 阶段 / 退出 / 收尾） |
| §3 | 一步会话数据流 |
| §4 | 线程与帧时钟 |
| §5 | 约束清单（C1–C15） |
| §6 | 自检命令 |

---

## 1. 文件地图

| 文件 | 职责 |
|---|---|
| `dungeon/window/__init__.py` | 用 mixin 组装出 `DungeonSessionWindow` |
| `dungeon/window/base.py` | 构造（`__init__` 只存参数 + 会话初始化）与运行（`run()` 建 UI、`_runner.run()` 托管帧循环、收尾 `_finish_session`）、入口 / 运行阶段切换、关闭路径、视口尺寸（取自宿主端口） |
| `dungeon/window/runner.py` | **宿主运行器** `WindowRunner`：把 `RunnerParams` 的回调挂到窗口原有生命周期语义上（`on_post_init` / `on_load_fonts` / `on_frame_start` / `on_draw` / `on_frame_end` / `on_capture` / `on_before_exit`），`request_exit()` 只置位、帧边界落实 `app_shall_exit`（C1） |
| `dungeon/window/host.py` | **宿主端口** `HostPort`（含无宿主缺省实现）：尺寸/DPI、宿主显隐、事件泵、收尾弹框、活动窗口登记、缺省字体、回放文件选择 |
| `ui/hosting/tk_host.py` | 宿主端口的 Tk/CTk 适配器 `TkHost`——**window 层唯一的 Tk 细节所在地** |
| `dungeon/window/ui.py` | 建窗参数与 `WindowRunner` 组装、**副本字体对接**（经 `dungeon/window/fonts.py` 解析家族名→字体文件，按两档字号建 `dungeon_text_font` 与粗体 `dungeon_bold_font` 供文本组件使用）、每帧自绘（`_draw_frame`：根窗口 → 背景 → 文本 → 工具条 → 帧末顶层浮层）、**上屏游标**（`_reveal_*` 一族，30 字/秒限速真相源，仿流式与真流式共用）、布局自适应（`_relayout`，每帧比对 `io.display_size`）、输入接线（`_handle_input` 每帧读 `imgui.is_key_pressed` / `is_mouse_clicked`）、**F12 过程日志开关**、**A 自动播放 / F2 截图 / O 副本设置**（`toggle_autoplay` / `take_screenshot` / `toggle_settings`，与文本组件工具条按钮共用入口）与 **顶部轻通知窗**（`_notify`，窗口级常驻） |
| `dungeon/window/engine.py` | 推进核心：AI 流式生成、JSON 解析、伤亡结算、回放步进、关闭回调 `_on_close` |
| `dungeon/window/triggers.py` | 触发器判定（所在章节 / 前置 / 条件）、章节进入、章节效果短暂覆盖、插入段落 |
| `dungeon/window/options.py` | 选项触发器：后台生成选项文字并弹出选择弹窗 |
| `dungeon/window/ending.py` | 敏感效果结算与结局生成 |
| `dungeon/window/persistence.py` | 统一收尾 `_finalize`、退出处理 `_handle_exit`、回放 / 报告保存（原子写）、结局索引 `data/user/endings.json`；弹框一律经宿主端口 |
| `dungeon/window/components.py` | 显示组件生命周期接入（构建 / 布局 / 每帧绘制 / 销毁） |
| `dungeon/window/launcher.py` | 入口阶段混入 `DungeonLaunchStages`：动态背景轮播、方案选择面板、结局图标轮播、阶段切换 |
| `dungeon/window/background.py` | 背景图加载 / 旋转 / 模糊 / 淡入淡出 / 纹理更新（含单工作者 `PixelWorker`） |
| `dungeon/window/fx.py` | 窗口视效（闪光 / 振动 / 反色 / 色差分离）：顶层绘制列表 + 视效纹理，帧时钟计时、绝对时间消退，不阻塞其他动作 |
| `dungeon/window/gpu_textures.py` | **GPU 纹理句柄的释放口径**（C15）：`TextureGpu` 是 RAII 句柄，只在 GL 存活时丢；`release()` = 正常路径（`before_exit`），`retire()` = GL 已失效时的兜底仓库 |
| `dungeon/window/frame.py` | 帧时钟 `FrameScheduler`（窗口实例成员 `self._frame`）：`every` / `after` / `cancel` / `call` / `tick` / `drain` / `stop`——window 层唯一的时间源 |
| `dungeon/window/result.py` | `SessionResult` 与原因常量：`run()` 的返回值，替代「读窗口私有属性」 |
| `dungeon/window/fonts.py` | **字体与字号唯一真相源**：两档字号（`TEXT_FONT_SIZE` 24 / `UI_FONT_SIZE` 21 / `BOLD_FONT_SIZE`）、家族名→字体文件映射 `_FAMILY_FONT_FILES` + 平台回退链 + 粗体/衬线候选链 + `resolve_font_files()`；窗口 `ui.py` 与组件包共用同一份 |
| `dungeon/window/component_registry.py` | 显示组件注册表：惰性导入官方常驻组件包、id 解析与回退、参数合并（含 `min/max` 夹取）、按窗口复用实例；`source` 记录实际生效的包来源（外部包经 `ComponentRegistry(pack_dir=...)` 显式覆盖） |
| `dungeon/window/component_pack/` | **官方显示组件包**（窗口层常驻 Python 包）：`__init__.py` 组装 `REGISTRY`，各组件拆在同目录模块（`base.py` 共享常量与文本基类 / `text_gradient.py` / `text_card.py` / `text_nvl.py` / `attr_bar.py` / `proc_log.py`） |
| `dungeon/window/overlay.py` | **覆盖层服务面**（OverlayHandler）：`toggle_overlay / close_overlay / overlay_open / toggle_settings / component`——文本主组件经 ctx 调出对话记录等模态浮层；打开期间进入阅读模态（点击只关闭浮层、剧情推进挂起），H 调出对话记录 / O 调出副本设置 / ESC 关闭。**副本设置面板**：半透明全屏遮罩 + 居中卡片，读写副本独占设置（音量 / 帧率 / 记忆窗口 / 自动回放 / 段落字体 / 组件代管区停靠侧），拖动只改内存、松手 / 关面板时经 `base.set_dungeon_settings` 落盘；遮罩画在根窗口内、卡片在 `end()` 之后作为顶层窗口绘制 |

被本层消费的领域模块：

| 模块 | 用途 |
|---|---|
| `dungeon/prompts.py` | `DungeonPromptBuilder`：系统 / 用户提示词构建 |
| `dungeon/summary.py` | `StorySummarizer`：剧情压缩（概要 + 事实卡 + 关键事件）注入提示词 |
| `dungeon/details.py` | 细节探究提问与回放检索 |
| `dungeon/rules.py` `dungeon/models.py` | 演化规则、触发器条件规则、状态模型 |
| `dungeon/actions.py` `dungeon/audio/chapters.py` | 触发器 / 章节数据模型与动作注册表（见 [数据模型](script.md)） |
| `dungeon/terms.py` | 领域术语与持久化契约常量（见 [术语表](domain_terms.md)） |
| `dungeon/schema.py` `dungeon/validate.py` | 方案字段单一真相源 + 结构化校验器 |
| `dungeon/process_log.py` | 过程日志：副本各处的过程消息（章节 / 触发器 / 预生成 / 收尾落盘……）经 `process_log.log()` 进入线程安全环形缓冲，窗口订阅后经帧时钟投递到「过程日志」面板；**副本层禁止再直接 `print`**（`host.py` 的无宿主弹框桩除外） |
| `persistence/scenario_repo.py` | 方案仓库：`data/packs/scenarios/<id>/` 的读写（读写根分离、旧目录自愈迁移、原子写、保存即校验） |

MRO 顺序：`DungeonWindowBase, DungeonLaunchStages, DungeonWindowUI, DungeonStoryEngine,
TriggerHandler, OptionHandler, EndingHandler, DungeonPersistence, ComponentHandler`。
新增方法时注意同名方法会被排在前面的 mixin 覆盖。

## 2. 生命周期

### 2.1 构造与运行分离

`__init__` 只保存参数并初始化会话内容，不碰渲染后端；`run()` 建 UI、起停收在
`hello_imgui.run()`（帧循环由库驱动）、收尾，并返回 `SessionResult`。
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
│  ├─ host.viewport_metrics()  # 宿主仍可见时先取尺寸/DPI（缓存给 _get_initial_viewport_size）
│  ├─ host.hide_window()       # 副本窗口是独立顶层窗口，不藏会两个窗口同时占屏
│  ├─ _build_ui()              # 算建窗/布局尺寸、建 WindowRunner（此时不碰 GL）
│  ├─ _enter_entry_phase() # 仅探索模式：动态背景 + 方案选择面板
│  │   或 _init_components() + _build_components()  # 挑战/回放：直接进会话
│  └─ _runner.run()        # ← 阻塞到关窗（hello_imgui 内部建上下文/视口/帧循环）
└─ _finish_session()    # _frame.stop() → GPU 纹理兜底退役 → 背景工作者 shutdown → 恢复主窗口 → join 结局线程 0.5s
   ├─ _closing 且非入口退出 → _handle_exit()   # 结局已触发时内部还会再 join 最长 15s（persistence._handle_exit）
   └─ _unregister_with_parent()
```

注：入口页「加载回放」不再重开窗口——同一生命周期内由 `_on_entry_replay()` 经端口
`open_replay_file()` 取数据后 `_enter_replay_phase()` 切 `is_replay`。

### 2.2 帧循环

窗口不再自己写循环：`WindowRunner` 把本窗口的钩子挂到 `hello_imgui` 的帧回调上，
每帧顺序固定：`before_imgui_render`（先落实关闭请求，再 `on_frame_start`：
`_pump_host_events()` → `self._frame.tick()`）→ `show_gui`（`_draw_frame` 重建整个
画面）→ `after_swap`（计帧/限帧、`on_capture` 抓帧）；收尾走 `before_exit`。
帧率上限由 `fps_max` 给库（`fps_idling.enable_idling = False` 保证静默期也出帧，
否则帧时钟停摆）；真实节拍由 vsync 决定，自检限帧钩子挂在 `after_swap`
（`ui._on_frame_end`，`GIANTESS_DUNGEON_TEST_FRAMES`）。

- **先 tick 后判关闭**：保证「关闭前最后一次 UI 更新」不丢
- **关闭请求在帧边界才落实**（C1）：`request_exit()` 只置位，`before_imgui_render`
  里统一写 `app_shall_exit`。绝不在回调执行途中直接退出——`hello_imgui` 的
  `confirm_exit` 路径会在帧中途把上下文拆掉（`runner.py` 模块文档详述）
- **输入接线在帧线程**：立即模式没有回调线程，`ui._handle_input` 每帧读
  `imgui.is_key_pressed` / `is_mouse_clicked`，天然与渲染同线程（§4）

### 2.3 入口阶段 → 运行阶段（Run，探索模式）

两者共享同一次 Session 生命周期（同一个 ImGui 上下文 / 视口 / 根窗口），切换不重建。

| 动作 | 路径 |
|---|---|
| 进入入口 | `_enter_entry_phase()` 启动三组后台轮播（背景图随机、结局图标、Ken Burns 帧级运动），构建右下角方案面板 + 左下角结局面板 |
| 点「开始副本」 | `_enter_dungeon_phase()` → 冻结背景 → 销毁入口 UI → `_load_session_config()`（加载配置、校验 error 级阻止进入、扣 AP、建 AI 客户端与提示词）→ 构建组件 → 显示文本容器 → `check_triggers()`。任一步失败置 `_launch_error` 并 `_close_loop()`，由调用方在 Tk 侧弹错误框 |
| 点「加载回放」 | 冻结背景后经宿主端口 `open_replay_file()` 取数据；取不到就**留在入口页**，取到就在同一窗口生命周期里切 `is_replay` |
| 点「返回」 | `_close_loop()` 关窗，不视为会话结束（见 `_exit_from_entry`） |

阶段标记（都定义在 `__init__`，勿随意改名）：

| 标记 | 含义 |
|---|---|
| `_is_entry_phase` | 入口页可见中；事件回调据此忽略点击 / 按键 |
| `_entry_started` | 已完成一次进入选择，防止二次进入 |
| `_session_initialized` | `_init_session()` 是否已执行 |
| `_exit_from_entry` | 入口阶段退出（返回 / 加载回放 / 启动失败），`_finish_session` 据此跳过 `_handle_exit` |
| `_closing` | 已请求关闭；`_on_close` 与 `_close_loop` 都会置位 |
| `_session_running` | `run()` 重入保护（宿主事件泵会回到调用方回调，避免嵌套建第二个窗口） |

### 2.4 退出路径

| 入口 | 路径 | 说明 |
|---|---|---|
| 入口页「返回」 | `_on_entry_cancel` → `_close_loop()` → `_request_close()` 置位 → `before_imgui_render` 帧边界写 `app_shall_exit` | 帧回调继续到库决定退出，走 `before_exit` → `_on_close` 清理 |
| 会话中点窗口 X | GLFW 原生关闭 | 库关窗后走同一条 `before_exit` → `_on_close` 清理（**不再依赖 exit callback**，见 §5-C6） |
| 回放播完 | `_replay_next_step` → `_request_close()` | 同「返回」 |
| 会话未触发结局就退出 | `_handle_exit()` → `_finalize(completed=False)` | 落盘「未完成」回放与报告；**不**写 `endings.json`、不记挑战达成 |
| 宿主关闭整个应用 | `MainWindowManager.on_closing` → 句柄 `request_close()`（协议请子进程收尾）→ `os._exit(0)`；父进程退出即管道断开，子进程的看护线程立即终结 | 父进程从不加载副本窗口栈（imgui-bundle）；子进程收尾随管道断开结束 |

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

### 2.6 GPU 纹理句柄的收尾口径（C15）

`hello_imgui.TextureGpu` 是 **RAII** 句柄：丢掉最后一个 Python 引用就去释放底层 GL
纹理，所以**只能在 GL 存活时丢**。窗口层有六处句柄容器（背景 / 窗口工具条 / 结局
图标 / 视效覆盖层 / 组件工具条 / 小游戏贴图），一律走 `dungeon/window/gpu_textures.py`
的两个出口：

| 出口 | 时机 | 行为 |
|---|---|---|
| `ui._release_gpu_textures()` → `gpu_textures.release()` | `before_exit`（**GL 后端与 ImGui 上下文仍存活**，库收尾前最后一站） | 一次性收走全部句柄并丢弃引用 ⇒ RAII 当场释放显存 |
| `ui._retire_gpu_textures()` → `gpu_textures.retire()` | `_finish_session`（`hello_imgui.run()` **已返回**） | 兜底：把残余句柄挂进进程级仓库**保住引用不让析构跑**；正常路径走不到（走到会记一条告警日志） |

坑在容器：清空一个 `{key: TextureGpu}` / `[TextureGpu]` 就等于丢引用，而「清空容器」
看着人畜无害，很容易被顺手写进 `_finish_session`——那样每局收尾都会在驱动层段错误
（2026-10 在 Linux 上实测**每局必崩**，与软/硬 GL 无关）。因此
`ui._drain_gpu_textures()` 是**唯一**的句柄清单，`_retire_gpu_textures()` 必须排在
`_destroy_components` / `_destroy_overlays` / `_window_fx.shutdown`（这三步会清空容器）
之前；`tests/check_gpu_texture_lifetime.py` 机械守住清单、两个出口与调用次序。

## 3. 一步会话数据流

```
点击 / 空格（_on_mouse_click → _on_next_step）
├─ pending_option / pending_ending 存在 → 打断
├─ 上屏游标三态判定（_try_advance_paragraph）
│  ├─ 已上屏 < 5 字 → 吞掉本次点击（防误触 / 防抢在游标起步前切段）
│  ├─ 当前段未补满 100% → 补满并停住，不切段（再点一次才切段）
│  └─ 已补满 → 有排队显示段落就揭示下一句（_reveal_pending_unit）
├─ 有非延迟插入段 → 直接消费显示
└─ _generate_next_text()：后台线程
   ├─ _take_pregen_for_next：有预生成结果（或在途则等待）→ 直接采用，
   │     否则 prompt_builder.build_user_prompt → messages 追加
   ├─ ai_client.generate_stream → 逐块 extract_stream_text → split_stream_units
   │     真流式 chunk 只经 _reveal_extend 延长 target（不直写文本）；
   │     游标按 30 字/秒追赶；后续完成句排队到 _pending_units
   ├─ 完整响应 parse_final_json → 正文 / 方向 / 自定义方向；split_full_text 定格最终切分
   ├─ dungeon_logic.evolve_attributes（含敏感倍率）→ 伤亡结算
   ├─ replay_data.append(step_info)          # 回放记录
   ├─ _apply_prompted_unlocks（写入角色尺寸解锁）
   ├─ _start_detail_query：立即后台询问 AI「想了解的细节」（不阻塞推进）
   └─ _finish_step → 记录阈值跨越关键事件 → check_triggers：
        章节跳转 / 插入段 / 选项弹窗 / 章节效果覆盖 / 结局（结局另起线程生成 → 保存）
   └─ finally：下一次点击必然生成时 → _maybe_pregen_next 后台预生成下一段
```

| 概念 | 说明 |
|---|---|
| **逻辑段落 vs 显示段落** | 一次 AI 输出（约 100 字）是一个逻辑段落，属性演化、回放、剧情压缩、对话历史都以它为单位；内置分句器（`dungeon/splitter.py`）把它切成若干显示段落（换行、对话引号闭合、句末标点、分号、破折号为断点），逐句展示只为阅读节奏。队列耗尽后下一次点击才触发新的 AI 调用；插入触发器的段落走同一条仿流式管线 |
| **上屏游标（唯一速度真相源）** | `ui.py` 的 `_reveal_*` 一族：仿流式（预生成 / 固定文本 / 插入段）与真流式共用**同一条限速**，恒定 `_REVEAL_CHARS_PER_SECOND = 30 字/秒`（帧任务 `_REVEAL_TICK_SECONDS = 0.05` 即 20 Hz，每 tick 约 1.5 字；`frac` 小数累加器消除 1/1/2 的抖动）。状态字典 `self._reveal = {item, target, pos, frac}`，`pos` 是唯一进度真相源。**真流式与仿流式的区别只在 target 何时给定**：仿流式一次给全（`_reveal_start`），真流式的 chunk 到达只 `_reveal_extend` 延长 target、**不直写 item 文本**，游标按固定速率追赶——因此 AI 吐字再快，玩家看到的上屏节奏恒定，分句器也始终有一份领先缓冲，不需要为上屏等待。游标走到 target 末尾时**自我注销**（`self._reveal = None`）并调 `_on_reveal_finished` 登记自动播放的到期时刻 |
| **点击三态机** | `_try_advance_paragraph`：① 已上屏不足 `_REVEAL_MIN_CHARS = 5` 字（≈0.17 秒）→ 吞掉点击，游标与队列都不动；② 未补满 → `_reveal_snap` 补满到 100% 并停住，**不切段**；③ 已补满（自然播完或上次点击补满）→ 才揭示队列里的下一显示段落。这个顺序保证「狂点」最多推进一个单元，不会掀翻分句器队列（`check_reveal_cursor.py` 守此契约） |
| **说话人标记** | Solea/Bulla 耦合等级的对话分支（`dialog`/`branch`，方案可配 `protagonist_title` 指定主角称呼）要求 AI 在对话句句首写 `@说话人@`（规则见 `dungeon/coupling.SPEAKER_MARKER_RULE`）；分句器解析进显示单元（`DisplayUnit.speaker`），`story_history` 条目带 `speaker` 键，UI 组件据此渲染名牌。落盘正文（回放/报告/概要）经 `strip_speaker_markers` 剥离标记，回放文件格式不变；Velum 等级正文无标记，行为不变。**带说话人的显示单元定稿时还会交给语音导演朗读**（`ui.py::_speak_item` → `speech.SpeechDirector`），只有对话句出声，叙述句不念；音色与开关见 script.md §10 |
| **文本显示所有权** | 文本主组件由方案配置 `text_component` 字段**三选一**（`dungeon.schema.TEXT_COMPONENT_IDS`）：`text` **底部渐变式**（视口底部向上淡出的深色衬底上显示最近 N 句）、`text_card` **底部卡片式**（居中圆角半透明卡片）、`text_nvl` **全屏 NVL**（半透明覆盖层堆叠全部历史，可选衬线字体、可滚轮回看；`margin_percent` 按视口边长给覆盖层留百分比边距，0 = 全屏）；`components` 列表只放其余组件，主组件先建、z 序在底。行首标签只显示说话人（类型前缀是内部属性不上屏，过程日志面板可见），继续点击用闪烁 ▼ 提示。接管期间（`owns_text_display = True`）`_update_text_display` 跳过内置 `text_container` 管线、转调组件刷新链；`text`/`text_card` 屏幕只呈现当下几句，完整历史由回放/报告承接 |
| **覆盖层服务面** | 窗口向文本主组件提供受控调用：`ctx.toggle_overlay("log")` 调出**对话记录**（全量 `story_history` 快照、可滚动，居中半透明面板）、`ctx.toggle_overlay("settings")` / `ctx.toggle_settings()` 调出**副本设置**（半透明全屏遮罩 + 居中卡片，读写副本独占设置），`ctx.component(cid)` 只读访问兄弟组件实例；写操作与组件生命周期仍由窗口集中管理，主组件只拿到调度权而非所有权。覆盖层打开时为阅读模态：点击只关闭覆盖层（`ui.py::_on_mouse_click`，点在设置卡片内不算外部），空格/回车推进在 `_on_next_step` 入口挂起 |
| **组件服务面（C13）** | 组件访问窗口的唯一入口集中在 `components.py::ComponentHandler`：几何 `component_viewport()`、帧时钟 `schedule / schedule_every / cancel_task`（**不碰 `self._frame`**）、状态查询 `session_waiting_for_input()`（继续指示与自动播放共用同一判定）/ `component_autoplay_on()`、字体 `text_font_tag() / bold_font_tag()`、顶部让位 `component_top_inset()`（聚合各组件的 `top_inset(ctx)` 可选钩子，与构建顺序无关）。契约的可执行版本是 `check_component_pack.py` 的替身 ctx——它**只**实现服务面，组件越界（读私有）在冒烟与 AST 扫描里都会失败 |
| **组件代管区（slot）** | 文本主组件在 `layout` 阶段生成 `dungeon.window.layout.Slot`：几何 + `SlotStyle` + 可接纳的角色 / 形状 / 容量。`ComponentHandler` 的布局分两遍：先排文本主组件并收集代管区，按子组件声明的 `roles` / `shapes` 机械匹配并发布 `component_rect(cid)` / `component_slot(cid)`，再排其余组件。`surface=host` 由文本组件画底纹（卡片延伸）、`bare` 无底纹（渐变区）、`own` 子组件自绘。官方示例：`text_card` 的 `card_footer`、`text` 的 `scrim_band`、`text_nvl` 的 `nvl_header`，`attr_bar` 以 `status` / `band` 优先落入，没有可用代管区时回退左上角独立面板；`text_card` 另提供**上下两区**：`card_side`（上区，`log` / `rail`，高度上限 `360*s`）与 `card_side_low`（下区，任何 `rail` 组件可认领，高度与卡片相同、底端与卡片对齐，与上区同生共死），`proc_log` 展开时落上区、按 `SlotStyle.side` 从所在侧的屏幕边缘平移进入，卡片 + 代管区作为一组整体居中、卡片往另一侧让位，收起即释放，没有代管区时回退右上角面板；`text` 也提供对齐正文列一侧的 `scrim_side`（`log` / `rail`），停在渐变正文上方、不做横向让位，`proc_log` 按 `SlotStyle.enter_from="bottom"` + `enter_fade` 从下方升起淡入；`text_nvl` 提供贴着正文列的 `nvl_side`（`log` / `rail`），占满覆盖层高度，日志 + 正文整体居中，代管区缺省不带底纹 / 边框 / 分隔线，日志区与正文区共用覆盖层那一层背景（日志经自己的 `draw_own_surface` 可改成自绘面板）。三个文本组件的正文列 / 卡片宽度都有可读上限（`base.py::_MAX_TEXT_WIDTH`，1280 逻辑点）并在可用区域内居中，大屏上不会随窗口宽度无限拉长，卡片与代管区也不会被推到窗口两端。停靠哪一侧是**窗口视觉设置** `dock_side`（`dungeon_settings.json`，副本设置面板里由玩家实时改、改完立即重排组件），不是文本组件的方案参数；组件经服务面 `ctx.component_dock_side()` 读取。毛玻璃卡片经 `ctx.component_backdrop_blur()` 拿到背景的 1/8 缩小模糊副本与背景矩形，按卡片圆角 / UV 截取身后那块模糊影像（随换图 / 振动同步），再叠半透明染色与玻璃边缘；没有纹理时只保留半透明染色。未接入代管区的旧组件继续走 `top_inset` 兼容路径 |
| **细节探究** | 流式输出完成后用刚生成的段落组装提问，后台线程询问 AI 还想了解哪些细节（最多 3 条，存 `_detail_queries`）；下次生成时用问题关键词在回放缓存中检索最相关段落作为「细节补充」注入提示词，随后消费掉这批问题 |
| **预演化** | 步进收尾后（`finally`，插入晋升之后）若下一次点击必然触发生成（无选项/插入/结局排队），立即后台预生成下一段，缓存 `(user_prompt, messages 快照, 原始响应, 段落类型)`；玩家点击时 `_take_pregen_for_next` 直接采用（在途则等待收养，不重复发请求），首句仿流式揭示。选项选择后、插入段消费后各补射一次；预生成失败自动回退实时路径并还原 `keyword_match_given`。调用次数不变，只是提前发出 |
| **章节与触发器** | 会话开始先 `_enter_start_chapter()` 进入起始章节再 `check_triggers()`；完整条件是「所在章节 + 前置触发器 + 条件规则」三者同时满足 |
| **结束章节** | 段落类型固定为「结局」（前缀 `【结局】`），步进 0（`evolve_attributes(step_override=0.0)`），触发器不能跳出或弹选项；节内段落数达到 `max_paragraphs` 时终止并结算 |
| **章节上限兜底** | 普通章节节内段落数达到 `max_paragraphs`（默认 99）时，在 `_finish_step` 末尾（触发器判定之后）自动跳到 `overflow_target`（空串 = 离开章节） |
| **剧情压缩** | `StorySummarizer` 按块（默认 20 段）与换章时机压缩；`build_user_prompt` 注入「全部压缩概要 + 关键事实 + 关键事件」。近期原文不再注入——对话历史（窗口大小即 `story_recent_count` 设置，`_message_window_size`）已逐字携带最近剧情，重复注入只会推高 prefill 与首字延迟。AI 额外输出 `facts`（跨块去重成事实卡）；确定性关键事件（章节进出、选项选择、介入度 / 破坏性跨整数阈值）由代码直接登记 |
| **回放模式** | 不建 AI 客户端，`_replay_next_step` 逐条重放 `loaded_replay`（`kind == "trigger"` → `_replay_trigger`，`kind == "chapter"` → `_replay_chapter`） |
| **过程日志（proc_log 组件）** | 右上角日志面板是官方组件包里的 `proc_log` 组件：方案在 `components` 里声明才构建（`_default` 已声明），默认收起，**F12** 切换展开 / 收起。数据源是 `dungeon/process_log.py`：组件 build 时订阅并取走缓冲积压（构造 / 会话初始化阶段的报错也会补显），此后任意线程的 `log()` 经订阅回调 → `ctx._frame.call()` 进主线程追加（上限 300 行，自动滚底）；组件 destroy 时退订。方案未配置该组件时按 F12 弹通知「日志查看已禁用」 |
| **文本组件功能工具条** | 三种文本组件（text / text_card / text_nvl）各自在合适位置挂一条悬浮工具条（基类统一创建，圆角低透明度容器 + 四个图标按钮——图标为程序化生成的小纹理，无底色仅悬停色，常驻不可关）：播放（自动播放，A）/ 相机（截图，F2）/ 列表（过程日志面板，F12）/ 齿轮（副本设置，O），悬停有文字提示。渐变文本栏放渐变区右下角（继续指示 ▼ 左移让位），卡片文本栏骑在卡片右上角，NVL 顶部居中。按钮只转调窗口同名服务，与快捷键等效；自动播放由帧时钟任务采样（`toggle_autoplay` 挂 `every(_AUTOPLAY_INTERVAL = 0.2)`，只是采样频率），真正的推进节奏由**事件驱动**：上屏游标自然走完时 `_on_reveal_finished` 登记 `_advance_due = now + _AUTOPLAY_DWELL(0.5s) + _AUTOPLAY_DWELL_PER_CHAR(0.012) × 句长`，`_autoplay_tick` 三重检查（等待条件同源 → 游标已收尾 → 停留期已过）后才 `_on_next_step`，长句多停一点、不再固定 1.2 秒。等待条件与继续指示同源（选项 / 结局 / 生成 / 上屏未结束期间自动停），自动播放中播放图标转金色。窗口 `_on_mouse_click` 用 `toolbar_hovered()` 区分点按钮与点屏幕推进剧情。截图经 `RunnerParams.callbacks.after_swap` 的 `on_capture` 钩子（`glReadPixels` 抓整帧）交给 `PixelWorker` 存到 `data/user/screenshots/`（通知延后弹出，避免通知条被截进画面）。正文颜色为固定常量（`_DEFAULT_TEXT_COLOR`），可配置的是标签 / 高亮 / 渐变底色（见组件包 `param_specs`） |
| **轻通知窗** | 主窗口顶部居中的通知条（`ui.py::_notify`），窗口级常驻、**不受组件配置影响**；显示约 3 秒自动隐藏（`after` 同 key 任务互斥，连续通知覆盖前一条并重置计时）。副本内需要「提示但不必确认」的场景都可用它 |

## 4. 线程与帧时钟

**只有主线程（帧循环所在线程）允许直接调用 imgui API。** 后台线程一律通过
`self._frame.call(fn, *args)` 投递；队列由帧回调每帧 `before_imgui_render` 里
`tick()` 时 `drain()` 清空。
会话收尾 `_finish_session()` 会 `_frame.stop()`，此后 `call()` 返回 `False` 而不是抛异常，
后台线程不必再轮询 `_closing`。

**输入只在帧循环线程上落地**：立即模式没有"回调线程"——`ui._handle_input` 每帧读
`imgui.is_key_pressed` / `is_mouse_clicked`，鼠标与键盘天然在帧线程执行，上面的
单线程约定不需要任何"收拢"就成立。自动驾驶 `native-input` 场景用
「SendMessage 直投 WM_KEYDOWN 触发真原生按键」锁住这条性质。

> 迁移前（DPG 2.3.1）控件 / 按键回调默认派发到**工作线程**，窗口靠
> `dpg.configure_app(manual_callback_management=True)` + 帧循环
> `run_callbacks(get_callback_queue())` 把它们收拢到帧线程（旧 C6）。那条补救链
> 已随 imgui 迁移（P1）删除，本段落记的是它的来历；DPG 的其余兼容怪癖见 §5.1。

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

规则：窗口关闭的判定只在主线程；后台线程永远不要直接调 imgui（含 `app_shall_exit`），
要关窗就 `_frame.call(self._close_loop)`。`join(timeout=...)` 只允许带超时，且不要在渲染回调内 join
（会阻塞帧处理，最长约 1s 的假死）。

## 5. 约束清单

| 编号 | 约束 | 原因 / 出处 | 验证 |
|---|---|---|---|
| **C1** | 任何「程序主动关闭副本窗口」都必须走 `base._request_close()`，业务代码不直接退（不写 `app_shall_exit`、不调库的退出函数）；`runner.request_exit()` 只置位，由 `before_imgui_render` 帧边界统一落实 `app_shall_exit` | 在回调执行途中直接退会让 `hello_imgui` 的 `confirm_exit` 路径在帧中途拆掉上下文。迁移前 DPG 直接 `stop_dearpygui()` 同样收尾不干净（入口页「返回」后进程延迟崩溃 / 视口僵尸残留，见历史档案），当时的补救是把停止挪回帧边界——本约束即那条纪律的 imgui 版 | `python tests/check_dungeon_window_contract.py`（`hello_imgui.run` / `app_shall_exit` 只许出现在 `runner.py`）；`python scripts/dungeon_autopilot.py --scene session-close --isolate`（含 `--repeat 2`）；真应用手测「进入副本 → 返回 → 再进入副本」 |
| **C2** | 窗口的 GL 后端 / ImGui 上下文 / 视口生命周期**全权交给 `hello_imgui.run()`**：进程内不手动建 / 毁上下文，`run()` 返回即全部释放，不需要保活视口；收尾（恢复主窗口、弹框）在 `_finish_session` 里、`run()` 返回之后做，与子进程的 GL 栈生命周期无关 | 迁移前 DPG 的进程单例上下文 + GLFW 终止会让 Tk 根窗口失去窗口级命令（`destroy()` / `withdraw()` 硬崩 0xC0000005），才需要「保活视口 `park_context` / `unpark_context`」把进程留在健康状态（当年实测矩阵见 `history/`）。imgui 的 runner 把上述全收进库内部（R1 实测 3 轮开关窗零崩溃）、会话又跑在独立子进程里，那条补救链整体退场 | `tests/smoke_switch.py` 第 2/5 轮（真跑两局副本 + 三次切换，断言父进程始终没在本进程启动副本窗口）；`scripts/dungeon_autopilot.py` |
| **C3** | `__init__` 的每个构造参数都必须存到 `self` 上 | `_init_session()` 在入口阶段**迟到执行**（点「开始副本」时才跑），此时局部变量早已不可见；曾因漏存 `merged_quips` 导致「开始副本」必然 AttributeError | 构造后读属性 / `entry-start` 场景 |
| **C4** | 跨线程 UI 更新走 `self._frame.call()`；禁止后台线程直接调 imgui | 帧时钟是窗口实例成员（随会话创建与停止），不是模块级单例；`stop()` 后 `call()` 返回 `False` 不抛异常 | `dungeon_autopilot.py` |
| **C5** | 计时类逻辑不开线程，用 `every` / `after` 帧任务 | 旧实现各自靠 `_closing` 标志轮询退出、收尾时逐个 join；现在退出不需要 join | `python tests/check_dungeon_window_contract.py`（禁 `threading.Timer`）；场景结束时断言 `threading.enumerate()` 无非 daemon 残留 |
| **C6** | 后端迁移的已知差异与已退役的 DPG 怪癖见 §5.1 | 实测结论 | 手测 + 自动驾驶 |
| **C7** | 入口阶段与会话页共用 `bg_texture` / `bg_image_item` | 进入会话时 `_freeze_background` 把冻结帧设为 `_bg_pil_full`，后续 relayout 沿用冻结画面 | 手测入口 → 会话 |
| **C8** | window 层不许 import `tkinter` / `customtkinter` / `ui`；宿主能力一律经 `HostPort`，见 §5.2 端口表 | 换 UI 框架只需换适配器；自检不再需要打桩 `ui.ctk.dialogs` | `tests/check_dungeon_layering.py` |
| **C9** | 构造 / 复制 `DungeonState` 只用关键字参数 | 字段顺序会随迭代变化（`chapter_steps` 就是从中间插入的）；`clone()` 曾按位置传参导致伤亡数组被填成步长浮点数，于是**每一步** `_finish_step` 都抛 `'float' object is not iterable`——故事看着还在出字，但总计数不增长、触发器与结局永不触发 | 断言「回放记录是否增长」 |
| **C10** | 写盘一律用原子写（`persistence/json_store.py`） | 半截文件不可恢复，`.bak` 是唯一回退；回放 / 报告按时间戳命名、本来不会被覆盖，调用时传 `backup=False` | `tests/check_dungeon_finalize.py` |
| **C11** | 术语不混用：`scenario_*` = 方案（定义），`run_*` = 一局（执行），`session_*` = 窗口会话 / 呈现；`dungeon_*` 是产品 / 历史命名，不是精确的 Run 后缀 | 见 [术语表](domain_terms.md) 与 `dungeon/terms.py` | `tests/check_scenario_naming.py` |
| **C12** | 任何 Tk 交互前必须调用 `self._discard_pending_quit()` | 用户通过视口原生关闭键（X）退出时，GLFW 销毁原生窗口会导致 Windows 在本线程队列中留下一条 `WM_QUIT`；它不会被 Tk 消费，却会让 Windows 停止合成 `WM_TIMER`，造成随后的收尾提示模态对话框（`wait_window`）以及主窗口所有 `after` 计时器彻底收不到事件而表现为**弹出提示对话框后主窗口卡死**；收尾时在 `show_window()` / 弹框前由端口抛弃该残留消息 | `python scripts/dungeon_autopilot.py --scene native-close --isolate` |
| **C13** | 组件只经**组件服务面**访问窗口（`ctx.component_viewport / schedule / schedule_every / cancel_task / session_waiting_for_input / component_autoplay_on / text_font_tag / bold_font_tag / component_top_inset / component`），不得读窗口私有属性（`_dpi_scale` / `_layout_w` / `_frame` / `_autoplay`…） | 组件 ctx 就是窗口实例，摸私有零成本，一旦窗口侧改名即静默 AttributeError；契约的可执行版本是守卫里的**替身 ctx**（只实现服务面）与 AST 越界扫描 | `tests/check_component_pack.py` |
| **C14** | 会话收尾不许留下本代窗口：`hello_imgui.run()` 返回后，本进程不应再有标题以 `副本模式` / `回放模式` 开头的顶层原生窗口 | 迁移前 DPG 的 `destroy_context()` **不销毁**视口对应的 GLFW 原生窗口（2026-10-07 最小复现：`IsWindow/IsWindowVisible` 在 destroy 之后仍为真，`park_context()` 也不会带走它）。典型 DPG 程序感知不到（destroy 后进程随即退出，窗口由 OS 回收），但本程序收尾后仍跑 Tk 主循环，那个不再响应的副本窗口就**留在屏幕上**——用户报的「启动界面点返回后卡死」，且每局累加一个；当时必须趁 GLFW 有效时显式销毁（那之后 GLFW 的 WndProc 已释放，`DestroyWindow` 会以 0xC0000005 掀掉进程）。imgui 的 runner 自己销毁窗口，本条改为**回归断言**：无论后端怎么实现，收尾都不许留下这一代窗口 | `python scripts/dungeon_autopilot.py --scene entry-cancel` / `--scene session-close`（断言「返回/关闭后不再新增副本视口原生窗口」） |
| **C15** | `hello_imgui.TextureGpu` 等 GL 资源句柄**只在 GL 存活时释放**：统一走 `dungeon/window/gpu_textures.py` 的 `release()`（`before_exit`，`ui._release_gpu_textures`）；`hello_imgui.run()` 返回后的 `_finish_session` 只许 `retire()` 兜底保住引用。六处句柄容器（背景 / 窗口工具条 / 结局图标 / 视效覆盖层 / 组件工具条 / 小游戏贴图）一律经 `ui._drain_gpu_textures()` 收走，`_retire_gpu_textures()` 必须排在 `_destroy_components` / `_destroy_overlays` / `_window_fx.shutdown` 之前 | `TextureGpu` 是 RAII：丢最后一个引用即释放底层 GL 纹理。`run()` 返回后 GL 后端与 ImGui 上下文已随库释放，此时再丢引用会去解引用失效后端，在驱动层**直接段错误**——2026-10 在 Linux 上实测**每局副本收尾必崩**（软/硬 GL 都复现），成因是组件基类的 `destroy` 在 `_finish_session` 里清了工具条图标纹理。看着人畜无害的「清空容器」正是丢引用 | `python tests/check_gpu_texture_lifetime.py`（六处容器清单 + `release`/`retire` 口径 + `_finish_session` 次序与 `_on_before_exit` 释放的 AST 断言）；`scripts/dungeon_autopilot.py`（真窗口收尾后退役仓库必须为 0） |

### 5.1 C6：后端差异与已退役的 DPG 怪癖

现行后端是 imgui-bundle（`hello_imgui` + GLFW/OpenGL）。立即模式与 DPG 的保留模式
有几处必须记住的差异：

| 项 | 现状（imgui 立即模式） |
|---|---|
| 画面构建 | 没有常驻控件树：`_build_ui` 只备参数与建 `WindowRunner`，`_draw_frame` 每帧重建全部浮层；「改属性找控件」一律变成「改状态、下帧重画」 |
| 浮层顺序 | 根窗口铺满客户区，入口方案面板 / 工具条 / 通知 / 小游戏舞台必须是根窗口 `end()` 之后的**顶层窗口**，且用 `_panel_flags`（不带 `no_bring_to_front_on_focus`），否则被根窗口整个盖住 |
| 字体 | `hello_imgui.load_font` 只能在 `load_additional_fonts` 回调里调（放 `post_init` 会 access violation）；`push_font(font, base)` 传**基础字号**（库按 dpi_factor 放大） |
| 建窗尺寸 | `window_geometry.size` 是 96-PPI 逻辑点 = 宿主客户区物理像素 ÷ `_dpi_scale`（R7 定标，见 `ui._window_create_size`） |
| 背景 / 视效纹理 | `hello_imgui.TextureGpu` 句柄持有即持有 GPU 资源：换图**先建新句柄再丢旧句柄**，避免中间帧采样到已释放纹理 |
| 截图 / 反色 / 色差抓帧 | 走 `RunnerParams.callbacks.after_swap` 的 `on_capture`（`before_imgui_render` 阶段读后备缓冲是全黑，R13） |
| 静默期出帧 | `fps_idling.enable_idling = False`（否则帧时钟停摆）；`fps_max` 只是高刷屏封顶，节拍由 vsync 定 |
| 关窗 | 点 X 与程序侧关窗共用一条路径（库关窗 → `before_exit`）；`confirm_exit` 显式置 None，不弹库自带确认框 |

DPG 时代记进本小节的怪癖（`dpg.add_child_window(no_scrollbar=)` 创建时不生效、
viewport drawlist 不渲染、动态纹理同 tag 重建复用旧 GL 存储导致花屏、
`set_exit_callback` 是「最后一帧」语义、控件回调默认派发到工作线程需要
`manual_callback_management` 收拢、`destroy_context()` 不销毁视口原生窗口、
保活视口、临时标题绕过标题缓存……）**已随迁移整体退场**。它们的完整来历与
当时的应对留在 [imgui 迁移档案](history/imgui_migration.md) 与更早的
[宿主改造档案](history/host_refactor.md)、[退出挂死调查](history/exit_hang_investigation.md)。

与后端无关的一条旧坑仍在：入口背景图收集 `collect_dungeon_images` 按规范化路径
去重（同一图会经副本仓库相对根与自由副本绝对根各采一次）；只有一张图时轮播禁止
`random.randint(1, n-1)`（low>high 会抛异常杀死线程）。

### 5.2 C8：宿主端口方法表

`dungeon/window/host.py::HostPort`（缺省实现 = 无宿主），Tk 实现 `ui/hosting/tk_host.py::TkHost`，
子进程实现 `ui/hosting/dungeon_child_host.py::ChildHost`（弹框/文件框走协议请父进程代答，见
[宿主边界与可移植性](window_host.md) §7）。

| 端口方法 | 用途 | Tk 实现 |
|---|---|---|
| `viewport_metrics()` | `(视口宽, 视口高, dpi_scale, 宿主客户区宽, 高)` | `winfo_toplevel/winfo_id/winfo_fpixels` + `GetAncestor`/`GetDpiForWindow`/`GetClientRect`（子进程：父进程 spawn 前量好随载荷传入） |
| `hide_window()` / `show_window()` | 副本视口显示时藏起宿主、关闭后恢复 | `withdraw` / `deiconify` + `lift` |
| `pump_events()` | 帧循环里处理一次宿主事件（宿主不冻结） | `widget.update()` |
| `discard_pending_quit()` | 丢弃视口原生关闭（X）留下的退出残留消息（见 §5-C12） | Win32 下 `PeekMessageW(..., WM_QUIT, WM_QUIT, PM_REMOVE)` |
| `dialog(kind, title, message)` | 收尾提示 / 询问（`info`/`warning`/`error`/`ask`） | `ui.ctk.dialogs.*` |
| `open_replay_file()` | 入口页加载回放；缺省实现返回 `None`（等价于用户取消） | `filedialog` + JSON 校验（解析在适配器侧） |
| `launch_mini_game()` | 打开 **web 后端**小游戏窗口（`mini_game` 触发器按 manifest 分派；`py` 后端走会话内覆盖层舞台 `dungeon/window/minigame/`，不经端口）；结果经回调回传；缺省实现返回 `False` | 独立**子进程**跑 pywebview/WebView2（`ui/hosting/mini_game_host.py`，pywebview 强制占主线程），结果写 JSON 文件、watch 线程回调 |
| `register_active_window()` / `unregister_active_window()` | 让宿主整体退出时能找到活动副本窗口 | 沿 `master/parent` 链找持有 `_active_dungeon_window` 的主窗口 |
| `default_font()` | 段落字体缺省家族 | `ui.ctk.fonts.dungeon_font_default()` |

用法：

```python
DungeonSessionWindow(..., host=TkHost(self)).run()   # 探索模式 exp_frame / 挑战模式 challenge
DungeonSessionWindow(...).run()                      # 不传 = HostPort()，无宿主（自检）
```

注意：`dungeon/window/ui.py::_update_dpi_scale` 里仍有一处 Windows 平台代码
（`FindWindowW` + `GetDpiForWindow`，运行期发现 DPI 变化后重算 `_dpi_scale`），
属于 Win32 细节而非宿主端口范畴，是后续可选的清理项（迁移前它还与临时标题
`_temp_title` 配套，后者已随 imgui 迁移删除）。

## 6. 自检命令

| 改动范围 | 先跑 |
|---|---|
| 关闭路径、收尾、入口阶段、生命周期、帧时钟、回调线程、章节背景音乐、章节对话语音物理效果、对话语音 | `python scripts/dungeon_autopilot.py`（15 场景 109 项断言，含 `text-components` 组件冒烟）；单场景 `--scene <名字> --isolate`，连开关窗 `--scene session-close --repeat 2 --isolate` |
| 启动载荷序列化 / 结果往返（进程隔离线格式） | `python tests/check_dungeon_launch_payload.py`（无 GUI，73 项断言） |
| 会话子进程端到端（spawn、协议代答、登记/注销、限帧钩子） | `python tests/smoke_mini.py`（副本链路实测一节；`GIANTESS_DUNGEON_TEST_FRAMES` 限帧子进程） |
| `_finalize` / `json_store` / `scenario_repo` | `python tests/check_dungeon_finalize.py`（无 GUI，32 项断言） |
| 显示组件包 / 文本组件 / 组件参数 | `python tests/check_component_pack.py`（无 GUI，93 项断言：加载链、契约与服务面（替身 ctx + AST 越界扫描）、元数据与参数夹取、外部包覆盖、build/layout/refresh/destroy 冒烟与帧任务成对清理） |
| 分句器 / 说话人标记 | `python tests/check_splitter.py` |
| 新增 import / 分层 | `python tests/check_dungeon_layering.py`（窗口层含 `component_pack/` 子包） |
| 新增后端生命周期 / 线程调用（`hello_imgui.run`、`app_shall_exit`、Timer、join、dearpygui 回归） | `python tests/check_dungeon_window_contract.py`（C1 / C4 / C5 的机械可查部分） |
| 收尾释放纹理 / 新增 `TextureGpu` 持有处 | `python tests/check_gpu_texture_lifetime.py`（C15 的机械可查部分，13 项断言）；真窗口收尾看 `scripts/dungeon_autopilot.py` 后退役仓库是否仍为 0 |
| schema / 校验器 | `python tests/check_scenario_schema.py`；批量校验 `python scripts/scenario_validate.py --errors-only` |

无 GUI 守卫（`check_dungeon_layering.py` / `check_dungeon_finalize.py` / `check_splitter.py` 等）+
真窗口冒烟（`dungeon_autopilot.py`）才算绿。判定方式见 [调试自动化](window_automation.md) §4：**按子进程输出判定，不看退出码**
（退出阶段可能挂死，已查明是环境现象而非代码缺陷，调查记录见
[退出挂死调查](history/exit_hang_investigation.md)）。

其它提示：

- 涉及关闭路径的改动**必须**在真实应用中手测（最小 imgui 脚本复现不了 Tk 宿主 + 完整线程栈的组合行为）。
- 回放结束、入口返回、会话 X 关闭是三条独立路径，改 `_request_close` / `_on_close` / `_handle_exit` 时逐一回归。
- 控制台出现 `流式任务执行异常: '...xxx' object has no attribute ...` 通常是 `_init_session` 没跑完（某属性缺失），先看完整 traceback 的第一个异常点。

---

## 历史

本文件只描述**现状**。以下档案记录窗口层是怎么变成现在这样的：

| 档案 | 内容 |
|---|---|
| [imgui 迁移档案](history/imgui_migration.md) | 2026-10：Dear PyGui → imgui-bundle 的整体迁移（P0 可行性 → P8 清场），逐阶段取舍与验收 |
| [宿主改造档案](history/host_refactor.md) | 手动渲染、构造/运行分离、宿主端口、帧时钟、结果对象化的全过程与验收 |
| [退出挂死调查](history/exit_hang_investigation.md) | 进程退出挂死的复现矩阵与修正结论；`destroy_context()` 后碰 Tk 崩溃的实测（对应 C2） |

约束 C1–C11 里若出现「以前…现在…」的表述，只用于说明**为什么**要这样写，不构成历史记录。
