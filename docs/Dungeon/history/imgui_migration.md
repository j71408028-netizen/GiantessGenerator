# imgui 迁移档案（Dear PyGui → imgui-bundle）

> **现状见**：[窗口](../window.md)（生命周期与约束清单）、[宿主边界与可移植性](../window_host.md)、
> [架构说明](../architecture.md)。
> **性质**：只读历史。这里记的是 2026-10 这一次后端整体迁移**怎么发生的**，
> 不构成对现状的约束——约束以窗口文档 §5 为准。

## 0. 一句话

副本会话窗口的渲染后端从 **Dear PyGui（DPG 2.3.1）** 换成 **imgui-bundle（1.92.900，
`hello_imgui` + GLFW/OpenGL）**。迁移按 P0–P9 分阶段进行，全部落在
`refactor/dungeon-imgui` 分支；本文记录 P0–P8 的取舍与验收，P9（打包发布）另见
[发布文档](../../ops/release.md)。

迁移期间的方案 / 评估 / 逐阶段验收与取证脚本保存在本地 `.workbuddy/imgui-migration/`
（该目录不入库），本文只保留结论。

## 1. 为什么要迁

- DPG 2.3.1 的进程单例上下文 + GLFW 生命周期在「Tk 宿主 + 独立窗口」形态下长出一整套
  同进程补救机器（保活视口、`WM_QUIT` 残留清理、视口僵尸窗口销毁、热切换兜底修复），
  维护成本高、崩溃面大（见 [宿主改造档案](host_refactor.md) 与
  [退出挂死调查](exit_hang_investigation.md)）。
- DPG 停止演进、上游问题修复缓慢；imgui-bundle 与 DPG 同为 Dear ImGui 生态，
  立即模式的绘制语义反而更贴近「每帧重画」的本窗口。
- 迁移后上下文 / 视口 / 帧循环全部收进 `hello_imgui.run(RunnerParams)`，返回即释放；
  会话又已搬进独立子进程，父进程彻底不碰渲染栈。

## 2. 阶段与验收

| 阶段 | 内容 | 提交 / 结论 |
|---|---|---|
| **P0 打地基** | 加 `imgui-bundle==1.92.900`（保留 dearpygui 到 P8）；三个 spike | 生命周期 spike：**同进程可用**，3 轮开关窗退出码全 0、宿主 `after` 心跳照常、`run()` 后 Tk 根可安全 `destroy()`、原生关闭键不再残留 `WM_QUIT` → C2/C12/C14 可整体退休；打包 spike：PyInstaller 三变体全过，`--collect-all imgui_bundle` 充分；纹理/DPI spike：`TextureGpu` 不能原地改像素，更新=每帧新建+丢旧句柄，720p 1.97 ms/帧不泄漏，建窗尺寸是 96-PPI 逻辑点 |
| **P1 窗口骨架** | 新增 `runner.py`（`RunnerParams` + 帧回调）；重写 `base` 的起停；`ui` 只保留建窗+背景 | `a421721`：门通过（entry-cancel / session-close / smoke_switch 50/50）。新风险已修：会话初始化早于 GL 上下文，首图同步上传会 0xC0000005 → 改为 `post_init` 后首帧上传（`_pending_bg`） |
| **P2 阅读主循环** | 字体两档、渐变文本区、上屏游标、点击三态机、键位、工具条 | `e911aca`：`check_reveal_cursor` OK、`check_splitter` 15/15。顺带关闭 R11（`FrameScheduler.every()` 改相位累积）与修掉「满屏根窗口盖住浮层」（浮层改用 `_panel_flags` + 根窗口 `end()` 之后绘制） |
| **P3 入口阶段** | 结局图标 `TextureGpu` 预载、方案面板、结局图标轮播、Ken Burns | `c54ac45`：entry-start / entry-cancel / entry-replay 全 2/2 |
| **P4 组件包与服务面** | `component_pack/*` 改立即模式、文本三组件、属性条、过程日志、对话记录遮罩 | `acc6bb3`：`check_component_pack` 93/93、text-components 2/2 |
| **P5 视效与平台能力** | `flash`/`shake`/`invert`/`rgb_split` 重写；`gl_util.py`（`glReadPixels` ctypes，零新增依赖）；`glfw_fullscreen.py`（用 imgui-bundle 自带 glfw3 做真全屏）；修正建窗口径 R7；交付 `ui.ui_snapshot()` | `84df70f`：F2 截图 2240×1260、F11 可进可还原 |
| **P6 小游戏舞台** | `minigame/stage.py` 等价迁移：舞台=帧末顶层窗口、离屏画布=单张 GL 纹理 + 脏矩形 `glTexSubImage2D`（新模块 `gl_texture.py`）、输入改轮询 | `7db17dd`：`mini-game-py`（迁移前 `rc=3221225477`）/ `mini-game-escape` / `mini-game` / `mini-game-repeat` 全 2/2；逐像素取证朝向正确、增量上传落到 GPU、舞台压在界面之上 |
| **P7 自动化闸门改造** | `dungeon_autopilot.py` 去掉 DPG 内省与探针，改读 `ui.ui_snapshot()` 值快照；`callback-thread` 场景改名 `native-input` | `d722f3d`：autopilot 15 场景 / 109 项断言全绿，子进程全部 `rc=0` 自行退出 |
| **P8 清场** | 删 `dungeon/window/dpg_state.py`；重写窗口契约守卫；`requirements.txt` 去掉 `dearpygui`；文档与 DPG 时代注释清理；`scripts/dpg_probe/` 归档保留 | 本文所在阶段；门：`tests/run_checks.py` + `scripts/scenario_validate.py` |

## 3. P8 清掉/保留了什么

**删除**：

- `dungeon/window/dpg_state.py`——DPG 全局上下文的「保活视口 / 上下文状态标志」，
  含 `park_context` / `unpark_context` / `discard_viewport_windows` / `was_created` /
  `mark_created` 等。迁移后上下文随 `hello_imgui.run()` 生灭，保活视口不需要了。
  其中的 `install_x11_error_guard` 与后端无关，搬到 `ui/hosting/x11.py`。
- `requirements.txt` 里的 `dearpygui==2.3.1`。
- 窗口层对 DPG 的全部 import 与调用（`check_dungeon_window_contract.py` 现在把它
  当作**回归禁令**扫：窗口层再出现 `import dearpygui` / `dpg.*` 即失败）。

**保留（档案，不再演进）**：

- `scripts/dpg_probe/`——2026-10-08 归档的 DPG 原生崩溃探针（部分 ctypes/dbghelp
  工具对 imgui 崩溃同样可用）。依赖已从 `requirements.txt` 移除，因此这些脚本
  **当前不可直接运行**；`tests/check_entrypoints.py` 为其在 `UNDECLARED_ALLOWED`
  里开了一处例外（`dearpygui`）。

**新增的小接口**：

- `dungeon/window/runner.py::times_started()`——本进程累计启动副本窗口的次数
  （只增不减），语义等价于迁移前的 `dpg_state.was_created()`，供
  `tests/smoke_switch.py` 断言「父进程始终没在本进程跑过副本窗口」。产品代码不读它。

## 4. 守卫与约束的改写

- `tests/check_dungeon_window_contract.py` 从「DPG 生命周期调用白名单」改为
  「threading 纪律 + imgui 后端调用」：禁 `threading.Timer` / 无超时 `join`；
  `hello_imgui.run` 与 `app_shall_exit` 赋值只允许出现在 `runner.py`；禁
  `sys.exit` / `os._exit`；全面禁 `dearpygui`。
- 下层「禁 UI 框架」的名单把 `imgui_bundle` 与 `dearpygui` 并列：
  `check_import_graph.py` / `check_dungeon_layering.py` / `check_mini_layering.py` /
  `check_minigame.py` / `check_voice_preview.py`。
- 窗口文档 §5 的 C1/C2/C6/C14 改写为 imgui 版（C2 = 上下文生命周期全交给
  `hello_imgui.run`；C14 = 「收尾不许留下本代窗口」的回归断言），C12 仍有效
  （GLFW 原生关窗的 `WM_QUIT` 残留对**同进程自检**场景仍然存在）。

## 5. 已知遗留

- `scripts/dpg_probe/` 的探针不能直接跑（依赖已移除），需要时先临时装 `dearpygui`。
- Linux/X11 上 `text-components` 场景在 CI 仍被跳过（历史已知崩溃，见
  [linux 兼容记录](../../users/platforms/linux-history.md) 附录A §5）；imgui 迁移后
  预期消失，但尚未在 Linux 复测，留到打包/CI 阶段确认。
- `dungeon/window/ui.py::_update_dpi_scale` 仍含 Win32 `FindWindowW` / `GetDpiForWindow`
  （运行期 DPI 变化重算），属可选清理项。
