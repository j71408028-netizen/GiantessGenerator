# 宿主边界与可移植性

> **定位**：`dungeon/window/` 与宿主（当前是 Tk）之间的**边界现状**，以及「换 UI 框架能做到哪一步」。
> 窗口自身的生命周期与约束见 [窗口索引](window.md)，自检见 [调试自动化](window_automation.md)，
> 全系列入口见 [副本文档索引](README.md)。
> **改造过程属于历史**，统一放在 [历史档案](history/README.md)，本文只在末尾给指针。

一句话：window 层不认识任何 UI 框架，宿主能力全部经端口索取；副本窗口是独立顶层窗口——
**换宿主可以做，嵌入宿主布局做不到**。

> **2026-10 起的进程形态**：副本会话运行在**独立子进程**里——Tk 主程序经
> `ui/hosting/dungeon_spawner.py` 拉起子进程（`ui/hosting/dungeon_child_host.py`
> 是子进程内的宿主端口实现），渲染后端（imgui-bundle / GLFW / OpenGL，迁移前是
> Dear PyGui）从此独占子进程，与 Tk 不再共享线程消息队列、GLFW 单例与 X11 连接。
> 下面 §1–§4 描述的边界与能力映射不变，只是「宿主」的两种实现分别住在两个进程里（§7）。

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
| Tk 实现 | `ui/hosting/tk_host.py::TkHost`——window 层之外唯一的 Tk 细节所在地（自检/自动驾驶用；产品路径已改走子进程宿主） |
| 子进程实现 | `ui/hosting/dungeon_child_host.py::ChildHost`——子进程内的宿主端口，宿主能力走协议请父进程代劳（§7） |
| 端口方法表 | 见 [窗口索引](window.md) §5.2（尺寸/DPI、显隐、事件泵、弹框、回放文件选择、小游戏窗口、活动窗口登记、字体） |
| 注入方式 | 父进程：`launch_dungeon_subprocess(...)`（`ui/hosting/dungeon_spawner.py`）；子进程内构造等价于 `DungeonSessionWindow(host=ChildHost(...), **deserialize_launch(payload))`；不传 host = `HostPort()`（无宿主，自检走这条路径） |
| 守卫 | `tests/check_dungeon_layering.py`：`dungeon/window/*.py` 里出现 `tkinter` / `customtkinter` / `ui` 即失败 |

换宿主要做的事只剩一件：**写一个新适配器**（尺寸/DPI、显隐、事件泵、弹框、活动窗口登记、字体、回放文件选择）。

> 注意：正因为 window 层不认 tkinter，`open_replay_file()` 里的 `json` 解析与 `filedialog`
> 必须住在适配器侧（`ui/hosting/tk_host.py`）。

已知未收编项：`dungeon/window/ui.py::_update_dpi_scale` 里有一处 Windows 平台代码
（`FindWindowW` + `GetDpiForWindow`，运行期发现 DPI 变化后重算 `_dpi_scale`），
属于 Win32 细节而非宿主端口范畴，是后续可选的清理项。

## 2. 驱动方式：宿主循环里的同步窗口

副本窗口**不**把宿主主循环交给库（不用「独占事件循环」那类入口），而是由窗口自己的
运行器 `dungeon/window/runner.py::WindowRunner`
调 `hello_imgui.run(RunnerParams)`——它内部建 GL 后端 / ImGui 上下文 / 视口并驱动帧循环，
返回即全部释放。每帧顺序与关闭判定见 [窗口索引](window.md) §2.2。

由此得到的三条性质（都是现状）：

| 性质 | 说明 |
|---|---|
| 宿主不冻结 | 帧回调每帧经端口 `pump_events()` 泵一次宿主事件（`on_frame_start`） |
| 关闭不依赖平台 | 程序化关闭经 `_request_close()` → `runner.request_exit()` 置位，`before_imgui_render` 帧边界统一写 `app_shall_exit`（C1） |
| 时间源唯一 | `FrameScheduler`（`self._frame`）是 window 层唯一的时间源；计时类逻辑用帧任务，不开线程 |

> **产品路径与自检路径的差异**：上表描述的是「同进程宿主」形态（`TkHost` + 无宿主
> `HostPort`，自检 / 自动驾驶走这条）。**产品路径**里会话跑在独立子进程（§7），
> 子进程内没有 Tk，窗口的宿主是 `ChildHost`——本节的性质在子进程内同样成立
>（帧回调仍每帧泵一次「宿主事件」，只是那里是空操作）。

## 3. 换宿主：能

「换宿主」与「嵌入」的答案完全不同：

| 说法 | 含义 | 结论 |
|---|---|---|
| **换宿主** | 换成 Qt / 其它框架 / 无框架脚本来「驱动」副本窗口：谁给主循环节拍、谁给尺寸与 DPI、谁弹对话框和文件选择框 | **能** |
| **嵌入** | 把副本画面当控件塞进宿主布局（Qt 的 QWidget、Tk 的 Frame） | **不能**（§4） |

驱动侧只依赖宿主「能处理一次挂起事件」这一个能力（经端口 `pump_events()`；无宿主就不泵）。
帧节拍由窗口的运行器（`hello_imgui.run`）提供，宿主不必自带定时器——无宿主的 `HostPort()`
（自检那条路径）就是把 `pump_events()` 做成空操作即可跑。因此任何能处理一次挂起事件的
框架都能当宿主：

| 宿主 | 需要实现的端口 |
|---|---|
| Tk（现状） | 宿主窗口显隐（`TkHost._window_widget()`：控件本身若没有窗口级命令，退回 `winfo_toplevel()`——专业模式传进来的 `ExplorationPanel` 是 `CTkFrame`，`withdraw`/`deiconify` 是 `Wm` 的方法，直接拿控件显隐会**静默失效**）+ `winfo_*` + ctypes DPI + `ui.ctk.dialogs` |
| Qt | `QWidget.hide/show` + `devicePixelRatioF()` + `QMessageBox` / `QFileDialog`（帧回调里调 `processEvents()` 泵宿主） |
| 无框架脚本 / CI | 屏幕尺寸 + no-op + print（`HostPort()` 缺省实现即此） |
| Web / 其它进程 | 做不到（渲染后端必须在本进程有 GLFW 窗口） |

> 注意：副本 `run()` 是**同步阻塞**的（§2）。宿主的节拍是在调度回调里被**消费**的
> （每次 `on_frame_start` 里 `pump_events()`）。`QTimer` 这类事件循环回调式节拍没法直接
> 驱动它——要么继续用「循环内轮询」式（`processEvents()`，即表中 Qt 行的主推方案），
> 要么把会话挪到自己的线程 / 子进程里跑（产品路径选的是后者，见 §7）。

因此「迁移到 Qt」的真正工作量从来不在副本窗口（只需换 host 适配器），而在 `ui/` 那一整套面板。

## 4. 嵌入：不能

imgui-bundle（`hello_imgui`）的窗口同样是**独立的 GLFW 顶层窗口**，公开 API 里没有把
画面渲染进宿主某个控件句柄的入口。强行 `SetParent` 会被 GLFW 把父级改回去，最多做到
「位置跟着宿主移动」的伪嵌入，不是控件级嵌入，跨平台更无从谈起。

结论：**换框架时副本窗口仍然是一个浮在旁边的独立窗口**，这一点不会改变。

## 5. 明确不做的事

| 不要 | 原因 |
|---|---|
| 同一进程里并发两个副本窗口 | 副本的 GL 上下文与视口都是进程级单例语义；`DungeonWindowBase._session_running` 是守门人（产品路径里会话本就在独立子进程，父进程不涉及） |
| 在帧循环里 `join` 线程 | 会卡帧 |
| 业务代码直接退出副本窗口 | 一律走 `_request_close()` → `runner.request_exit()`（只置位，帧边界落实，C1） |
| 为「修挂死」堆看门狗 | Python 层定时器在渲染期间拿不到 GIL，超时兜底只能放父进程；真正卡住时 `os._exit` 与 `Stop-Process -Force` 都无效 |
| 指望把副本窗口嵌入宿主布局 | 只能是独立顶层窗口（§4） |
| 动 `dungeon/` 领域层 | 分层守卫守着；本层只在 `dungeon/window/` 与调用方之间动刀 |
| 在父进程（Tk 主程序）里 import 副本窗口栈（`imgui_bundle` / 迁移前的 `dearpygui`） | 父进程从不创建渲染后端上下文，副本窗口栈只该活在子进程里。父进程侧一切副本交互都经 `ui/hosting/dungeon_spawner.py` |

## 6. 进程退出挂死（结论）

跑完会话后进程可能在**退出阶段挂死**。已查明：这是**长时会话累积出的 OS/驱动级环境现象**，
与本项目代码、与渲染后端、与驱动方式都无关（纯 Tk、不 import 副本窗口栈的进程同样复现，重启即消失）。

因此：自检按「读输出判定 + 父进程超时兜底」，默认**不**把退出码当失败信号；真遇到症状**先重启系统**
再复现，不要改代码。复测退出路径时用 `dungeon_autopilot.py --require-clean-exit`（重启后应全绿；
它把「未自行干净退出」也判为失败）。父进程的等待是**硬超时**（读输出在独立线程里做，主线程按
deadline 轮询），子进程的结论行打印后立即 flush——「已通过但退出挂死」与「超时未出结论」因此可以
分开汇报。完整复现矩阵与修正过程见 [退出挂死调查](history/exit_hang_investigation.md)。

---

## 7. 进程隔离：会话子进程（现状）

副本会话（`DungeonSessionWindow.run()` 的整个生命周期，即 Session）运行在**独立子进程**里。
这是 2026-10 对 [宿主改造档案](history/host_refactor.md) L5「进程隔离」决策的**反转**：
当年否决它的依据是「它唯一能治愈的症状（退出挂死）不存在」，但此后 09-28（C2）与
10-07（C12/C14）发现的同进程共存问题让它的收益面被大幅低估——保活视口、`WM_QUIT`
残留清理、视口僵尸窗口销毁、热切换兜底修复这一整套机器，全部只在描述「Tk 与
副本渲染栈同进程」。拆进程后这些机制要么随子进程消失，要么退化为子进程内部的无害收尾。

| 件 | 位置 | 职责 |
|---|---|---|
| 父进程侧 | `ui/hosting/dungeon_spawner.py` | 序列化构造参数（`dungeon/window/launch_payload.py`）→ `multiprocessing.Process` 拉起子进程 → 泵循环（`host_window.update()`）→ 代答协议请求 → 还原 `SessionResult`；会话期把句柄登记到 `app._active_dungeon_window`（老代码的「副本进行中」守卫与 `request_close()` 因此零改动） |
| 子进程侧 | `ui/hosting/dungeon_child_host.py` | `ChildHost(HostPort)`：尺寸/弹框/回放文件框走协议请父进程代劳，小游戏孙进程照旧；管道断开（父进程死亡）即 `os._exit`，孤儿副本不会继续写 `data/` |
| 线格式 | `dungeon/window/launch_payload.py` | 构造参数 dict ↔ 纯数据 dict 的往返（dataclass 落 `asdict`、宿主侧参数剔除）；`SessionResult` 的往返同文件。守卫：`tests/check_dungeon_launch_payload.py` |

协议（`multiprocessing.Pipe` 双向，JSON 安全 dict）：子 → 父发
`show_window` / `dialog` / `open_replay_file`（带请求 id）/ 最终 `result`；
父 → 子发 `{"reply": id, "answer": ...}` 与 `{"req": "close"}`（父进程整体退出前请
子进程走帧边界收尾）。子进程的弹框请求在父进程泵循环里用调用方自己的弹框实现
代答（专业版 `ui.ctk.dialogs`、挂件版 `MiniDialogs`）——子进程内因此没有任何
Tk 代码，`tests/check_mini_layering.py` 白名单放行 `dungeon_spawner` 的依据正是它
模块级零 CTk 依赖。

**DPI 感知**是进程级的、不随 multiprocessing 继承：子进程的感知档位镜像父进程
（载荷 `dpi_aware` → 子进程 `_ensure_dpi_awareness`，档位与 CTk 相同的
`shcore.SetProcessDpiAwareness(2)`）。父感知而子不感知，Windows 会把视口的
96-DPI 逻辑坐标按系统缩放再放大一次（窗口与字体巨大）；父（挂件版）刻意无感知
而子感知，视口会比主窗口小一圈。诊断：`scripts/child_dpi_probe.py`（拉两个子进程
打印各自的 `GetDpiForSystem()` 与注册表 `AppliedDPI`，人工对照）。原先它是
`tests/check_dungeon_child_dpi.py` 的判红守卫，2026-10-13 移出 `tests/`——"感知
探针应等于 `AppliedDPI`"这个前提在真机上不成立（本机 168 vs 192，见该脚本文末）。

父进程侧随之删除/简化的死机器：`app/shell.py` 的根窗口修复三件套
（`_root_needs_repair` / `_revive_dpg_for_teardown` / `_retire_root`）、
`MainWindowManager.on_closing` 里停止副本上下文的分支（父进程从不创建渲染后端
上下文，**import 副本窗口栈本身都不该发生**）。

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
