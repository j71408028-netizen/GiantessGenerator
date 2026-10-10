# 宿主边界与可移植性

> **定位**：`dungeon/window/` 与宿主（当前是 Tk）之间的**边界现状**，以及「换 UI 框架能做到哪一步」。
> 窗口自身的生命周期与约束见 [窗口索引](window.md)，自检见 [调试自动化](window_automation.md)，
> 全系列入口见 [副本文档索引](README.md)。
> **改造过程属于历史**，统一放在 [历史档案](history/README.md)，本文只在末尾给指针。

一句话：window 层不认识任何 UI 框架，宿主能力全部经端口索取；DPG 窗口是独立顶层窗口——
**换宿主可以做，嵌入宿主布局做不到**。

> **2026-10 起的进程形态**：副本会话运行在**独立子进程**里——Tk 主程序经
> `ui/common/dungeon_spawner.py` 拉起子进程（`ui/common/dungeon_child_host.py`
> 是子进程内的宿主端口实现），DPG/GLFW 从此独占子进程，与 Tk 不再共享线程
> 消息队列、GLFW 单例与 X11 连接。下面 §1–§4 描述的边界与能力映射不变，
> 只是「宿主」的两种实现分别住在两个进程里（§7）。

## 目录

| 节 | 内容 |
|---|---|
| §1 | 宿主端口：唯一的接触面 |
| §2 | 驱动方式：手动渲染的帧循环 |
| §3 | 换宿主：能 |
| §4 | 嵌入：不能 |
| §5 | 明确不做的事 |
| §6 | 进程退出挂死（结论） |
| §7 | 进程隔离：会话子进程（现状） |

---

## 1. 宿主端口：唯一的接触面

| 项 | 现状 |
|---|---|
| 端口定义 | `dungeon/window/host.py::HostPort`（含无宿主的缺省实现） |
| Tk 实现 | `ui/common/tk_host.py::TkHost`——window 层之外唯一的 Tk 细节所在地（自检/自动驾驶用；产品路径已改走子进程宿主） |
| 子进程实现 | `ui/common/dungeon_child_host.py::ChildHost`——子进程内的宿主端口，宿主能力走协议请父进程代劳（§7） |
| 端口方法表 | 见 [窗口索引](window.md) §5.2（尺寸/DPI、显隐、事件泵、弹框、回放文件选择、小游戏窗口、活动窗口登记、字体） |
| 注入方式 | 父进程：`launch_dungeon_subprocess(...)`（`ui/common/dungeon_spawner.py`）；子进程内构造等价于 `DungeonSessionWindow(host=ChildHost(...), **deserialize_launch(payload))`；不传 host = `HostPort()`（无宿主，自检走这条路径） |
| 守卫 | `tests/check_dungeon_layering.py`：`dungeon/window/*.py` 里出现 `tkinter` / `customtkinter` / `ui` 即失败 |

换宿主要做的事只剩一件：**写一个新适配器**（尺寸/DPI、显隐、事件泵、弹框、活动窗口登记、字体、回放文件选择）。

> 注意：正因为 window 层不认 tkinter，`open_replay_file()` 里的 `json` 解析与 `filedialog`
> 必须住在适配器侧（`ui/common/tk_host.py`）。

已知未收编项：`dungeon/window/ui.py` 里仍有两处 Windows 平台代码（`_temp_title` 与
`_update_dpi_scale` 的 `FindWindowW` / `GetDpiForWindow`），属于 DPG/Win32 细节而非宿主端口范畴，
是后续可选的清理项。

## 2. 驱动方式：手动渲染的帧循环

窗口**不**用 `dpg.start_dearpygui()`（那会把宿主主循环堵死），而是由自己的帧循环调用
`dpg.render_dearpygui_frame()` 手动渲染。每帧顺序与关闭判定见 [窗口索引](window.md) §2.2。

由此得到的三条性质（都是现状）：

| 性质 | 说明 |
|---|---|
| 宿主不冻结 | 帧循环每帧经端口 `pump_events()` 泵一次宿主事件 |
| 关闭不依赖平台 | 程序化关闭只需 `dpg.stop_dearpygui()`（经 `_request_close()` 置位，帧循环在回调批结束后的帧边界统一落实） |
| 时间源唯一 | `FrameScheduler`（`self._frame`）是 window 层唯一的时间源；计时类逻辑用帧任务，不开线程 |

## 3. 换宿主：能

「换宿主」与「嵌入」的答案完全不同：

| 说法 | 含义 | 结论 |
|---|---|---|
| **换宿主** | 换成 Qt / 其它框架 / 无框架脚本来「驱动」副本窗口：谁给主循环节拍、谁给尺寸与 DPI、谁弹对话框和文件选择框 | **能** |
| **嵌入** | 把 DPG 画面当控件塞进宿主布局（Qt 的 QWidget、Tk 的 Frame） | **不能**（§4） |

驱动侧只依赖宿主「能处理一次挂起事件」这一个能力（经端口 `pump_events()`；无宿主就不泵）。
已实测过零框架驱动（纯 `while` + `sleep`），因此任何能提供定时回调的框架都能当宿主：

| 宿主 | 节拍源 | 需要实现的端口 |
|---|---|---|
| Tk（现状） | `while` + `root.update()` + `sleep(1/60)` | 宿主窗口显隐（`TkHost._window_widget()`：控件本身若没有窗口级命令，退回 `winfo_toplevel()`——专业模式传进来的 `ExplorationPanel` 是 `CTkFrame`，`withdraw`/`deiconify` 是 `Wm` 的方法，直接拿控件显隐会**静默失效**）+ `winfo_*` + ctypes DPI + `ui.common.dialogs` |
| Qt | `while` + `QApplication.processEvents()`，或 `QTimer(16)` | `QWidget.hide/show` + `devicePixelRatioF()` + `QMessageBox` / `QFileDialog` |
| 无框架脚本 / CI | `while` + `sleep` | 屏幕尺寸 + no-op + print |
| Web / 其它进程 | 做不到（DPG 必须在本进程有 GLFW 窗口） | — |

> 注意：副本 `run()` 是**同步阻塞**的帧循环（§2），节拍源是在循环内部被**消费**的（每帧
> `pump_events()` + `sleep`）。`QTimer` 这类事件循环回调式节拍没法直接驱动它——要么继续用
> 「循环内轮询」式节拍（`processEvents()`，即表中 Qt 行的主推方案），要么把会话挪到自己的
> 线程里跑、让宿主主循环保持空闲。

因此「迁移到 Qt」的真正工作量从来不在副本窗口（只需换 host 适配器），而在 `ui/` 那一整套面板。

## 4. 嵌入：不能

DPG 2.3.1 的视口是**独立的 GLFW 顶层窗口**，公开 API 里没有任何取窗口句柄的入口
（`get_viewport_*` 只有尺寸 / 位置 / 标题）。强行 `SetParent` 会被 GLFW 把父级改回去，
最多做到「位置跟着宿主移动」的伪嵌入，不是控件级嵌入，跨平台更无从谈起。

结论：**换框架时 DPG 窗口仍然是一个浮在旁边的独立窗口**，这一点不会改变。

## 5. 明确不做的事

| 不要 | 原因 |
|---|---|
| 同一进程里同时持有两个 DPG context 或并发两个副本窗口 | DPG 的上下文与视口都是全局单例语义；`DungeonWindowBase._session_running` 是守门人 |
| 在帧循环里 `join` 线程 | 会卡帧 |
| 把「GLFW 终止**后**才崩的 Tk 交互」排到 `destroy_context()` **之后** | GLFW 终止前 Tk 照常可用（收尾顺序「恢复主窗口 → 弹框 → `destroy_context()`」正是刻意把 Tk 交互都放在销毁之前）；终止后那个 Tk 根窗口的 `destroy()` / `withdraw()` 硬崩（0xC0000005）。可用/崩溃操作矩阵见 [窗口索引](window.md) §5-C2 与 `dungeon/window/dpg_state.py`。**2026-10 起这条只约束子进程内部**——父进程不再跑 DPG，不再有共存问题；子进程收尾的保活视口（`park_context`）在进程随即退出时只是无害的收尾动作 |
| 让会话收尾之后进程里**没有**活的 DPG 上下文/视口 | `destroy_context()` 终止 GLFW，那个 Tk 根窗口随即不能 `destroy()`/`withdraw()`，之后新建的 `CTk` 根也起不来（首次 `deiconify` 硬崩）——宿主的热切换就完了。收尾必须补一个隐藏保活视口（`dpg_state.park_context()`），下一局开头（宿主隐藏之后）再 `unpark_context()` 拆掉。**同上：现在只约束子进程内部**；子进程跑完一局即退出，父进程的 Tk 从此与 GLFW 无关 |
| 在业务代码里直接 `dpg.stop_dearpygui()` | 一律走 `_request_close()` |
| 为「修挂死」堆看门狗 | Python 层定时器在 DPG 渲染期间拿不到 GIL，超时兜底只能放父进程；真正卡住时 `os._exit` 与 `Stop-Process -Force` 都无效 |
| 指望把 DPG 窗口嵌入宿主布局 | 只能是独立顶层窗口（§4） |
| 动 `dungeon/` 领域层 | 分层守卫守着；本层只在 `dungeon/window/` 与调用方之间动刀 |
| 在父进程（Tk 主程序）里 import `dearpygui` | 父进程从不创建 DPG 上下文，对从未建上下文的 DPG 调用函数会直接段错误（`try/except` 挡不住，见 `dungeon/window/dpg_state.py` 模块说明）。父进程侧一切副本交互都经 `ui/common/dungeon_spawner.py` |

## 6. 进程退出挂死（结论）

跑完会话后进程可能在**退出阶段挂死**。已查明：这是**长时会话累积出的 OS/驱动级环境现象**，
与本项目代码、与 DPG、与驱动方式都无关（纯 Tk、不 import dpg 的进程同样复现，重启即消失）。

因此：自检按「读输出判定 + 父进程超时兜底」，默认**不**把退出码当失败信号；真遇到症状**先重启系统**
再复现，不要改代码。复测退出路径时用 `dungeon_autopilot.py --require-clean-exit`（重启后应全绿；
它把「未自行干净退出」也判为失败）。父进程的等待是**硬超时**（读输出在独立线程里做，主线程按
deadline 轮询），子进程的结论行打印后立即 flush——「已通过但退出挂死」与「超时未出结论」因此可以
分开汇报。完整复现矩阵与修正过程见 [退出挂死调查](history/exit_hang_investigation.md)。

---

## 7. 进程隔离：会话子进程（现状）

副本会话（`DungeonSessionWindow.run()` 的整个生命周期）运行在**独立子进程**里。
这是 2026-10 对 [宿主改造档案](history/host_refactor.md) L5「进程隔离」决策的**反转**：
当年否决它的依据是「它唯一能治愈的症状（退出挂死）不存在」，但此后 09-28（C2）与
10-07（C12/C14）发现的同进程共存问题让它的收益面被大幅低估——保活视口、`WM_QUIT`
残留清理、视口僵尸窗口销毁、热切换兜底修复这一整套机器，全部只在描述「Tk 与 DPG
同进程」。拆进程后这些机制要么随子进程消失，要么退化为子进程内部的无害收尾。

| 件 | 位置 | 职责 |
|---|---|---|
| 父进程侧 | `ui/common/dungeon_spawner.py` | 序列化构造参数（`dungeon/window/launch_payload.py`）→ `multiprocessing.Process` 拉起子进程 → 泵循环（`host_window.update()`）→ 代答协议请求 → 还原 `SessionResult`；会话期把句柄登记到 `app._active_dungeon_window`（老代码的「副本进行中」守卫与 `request_close()` 因此零改动） |
| 子进程侧 | `ui/common/dungeon_child_host.py` | `ChildHost(HostPort)`：尺寸/弹框/回放文件框走协议请父进程代劳，小游戏孙进程照旧；管道断开（父进程死亡）即 `os._exit`，孤儿副本不会继续写 `data/` |
| 线格式 | `dungeon/window/launch_payload.py` | 构造参数 dict ↔ 纯数据 dict 的往返（dataclass 落 `asdict`、宿主侧参数剔除）；`SessionResult` 的往返同文件。守卫：`tests/check_dungeon_launch_payload.py` |

协议（`multiprocessing.Pipe` 双向，JSON 安全 dict）：子 → 父发
`show_window` / `dialog` / `open_replay_file`（带请求 id）/ 最终 `result`；
父 → 子发 `{"reply": id, "answer": ...}` 与 `{"req": "close"}`（父进程整体退出前请
子进程走帧边界收尾）。子进程的弹框请求在父进程泵循环里用调用方自己的弹框实现
代答（专业版 `ui.common.dialogs`、挂件版 `MiniDialogs`）——子进程内因此没有任何
Tk 代码，`tests/check_mini_layering.py` 白名单放行 `dungeon_spawner` 的依据正是它
模块级零 CTk 依赖。

父进程侧随之删除/简化的死机器：`app/shell.py` 的根窗口修复三件套
（`_root_needs_repair` / `_revive_dpg_for_teardown` / `_retire_root`）、
`MainWindowManager.on_closing` 里的 `dpg.stop_dearpygui()` 分支（父进程不再有
DPG 上下文，**import dearpygui 本身都会段错误**）。

代价（真实存在，已接受）：构造参数必须跨进程序列化（见线格式）；角色在子进程里
的改动（扣 AP、结局索引）由子进程直接写盘、父进程在会话结束后重载；每次开局有
一次子进程解释器/导入的启动延迟。挂死的 §6 结论不变：环境现象，与进程形态无关。

---

## 历史

本文只描述**现状**。改造过程与实测数据见 [历史档案](history/README.md)：

| 档案                                           | 内容 |
|----------------------------------------------|---|
| [宿主改造档案](history/host_refactor.md)          | 改造前的形状、结构性问题清单（A–H）、L0–L4 全过程与验收记录、L5 为什么不做、零框架驱动与 `SetParent` 实测 |
| [退出挂死调查](history/exit_hang_investigation.md) | §6 结论的由来：09-22 矩阵 → 09-23 修正 → 重启后复测 |
