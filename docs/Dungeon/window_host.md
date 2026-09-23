# 宿主边界与可移植性

> **定位**：`dungeon/window/` 与宿主（当前是 Tk）之间的**边界现状**，以及「换 UI 框架能做到哪一步」。
> 窗口自身的生命周期与约束见 [窗口索引](window.md)，自检见 [调试自动化](window_automation.md)，
> 全系列入口见 [副本文档索引](README.md)。
> **改造过程属于历史**，统一放在 [历史档案](history/README.md)，本文只在末尾给指针。

一句话：window 层不认识任何 UI 框架，宿主能力全部经端口索取；DPG 窗口是独立顶层窗口——
**换宿主可以做，嵌入宿主布局做不到**。

## 目录

| 节 | 内容 |
|---|---|
| §1 | 宿主端口：唯一的接触面 |
| §2 | 驱动方式：手动渲染的帧循环 |
| §3 | 换宿主：能 |
| §4 | 嵌入：不能 |
| §5 | 明确不做的事 |
| §6 | 进程退出挂死（结论） |

---

## 1. 宿主端口：唯一的接触面

| 项 | 现状 |
|---|---|
| 端口定义 | `dungeon/window/host.py::HostPort`（含无宿主的缺省实现） |
| Tk 实现 | `ui/common/tk_host.py::TkHost`——window 层之外唯一的 Tk 细节所在地 |
| 端口方法表 | 见 [窗口索引](window.md) §5.2（尺寸/DPI、显隐、事件泵、弹框、回放文件选择、活动窗口登记、字体） |
| 注入方式 | `DungeonSessionWindow(..., host=TkHost(self)).run()`；不传 = `HostPort()`（无宿主，自检走这条路径） |
| 守卫 | `scripts/check_dungeon_layering.py`：`dungeon/window/*.py` 里出现 `tkinter` / `customtkinter` / `ui` 即失败 |

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
| 关闭不依赖平台 | 程序化关闭只需 `dpg.stop_dearpygui()`（经 `_request_close()`），没有 WM_CLOSE 之类的平台 hack |
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
| Tk（现状） | `while` + `root.update()` + `sleep(1/60)` | `parent.withdraw/deiconify` + `winfo_*` + ctypes DPI + `ui.common.dialogs` |
| Qt | `while` + `QApplication.processEvents()`，或 `QTimer(16)` | `QWidget.hide/show` + `devicePixelRatioF()` + `QMessageBox` / `QFileDialog` |
| 无框架脚本 / CI | `while` + `sleep` | 屏幕尺寸 + no-op + print |
| Web / 其它进程 | 做不到（DPG 必须在本进程有 GLFW 窗口） | — |

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
| 在 `destroy_context()` 之后再碰 Tk | 会 0xC0000005（见 [窗口索引](window.md) §5-C2） |
| 在业务代码里直接 `dpg.stop_dearpygui()` | 一律走 `_request_close()` |
| 为「修挂死」堆看门狗 | Python 层定时器在 DPG 渲染期间拿不到 GIL，超时兜底只能放父进程；真正卡住时 `os._exit` 与 `Stop-Process -Force` 都无效 |
| 指望把 DPG 窗口嵌入宿主布局 | 只能是独立顶层窗口（§4） |
| 动 `dungeon/` 领域层 | 分层守卫守着；本层只在 `dungeon/window/` 与调用方之间动刀 |
| 做「进程隔离」（DPG 渲染放子进程） | 它唯一能治愈的症状（退出挂死）经复测**并不存在**（§6），而代价（跨进程命令/事件协议、资源与组件包搬到子进程、背景纹理共享内存）真实存在 |

## 6. 进程退出挂死（结论）

跑完会话后进程可能在**退出阶段挂死**。已查明：这是**长时会话累积出的 OS/驱动级环境现象**，
与本项目代码、与 DPG、与驱动方式都无关（纯 Tk、不 import dpg 的进程同样复现，重启即消失）。

因此：自检按「读输出判定 + 父进程超时兜底」，**不要**把退出码当失败信号；真遇到症状**先重启系统**
再复现，不要改代码。完整复现矩阵与修正过程见 [退出挂死调查](history/exit_hang_investigation.md)。

---

## 历史

本文只描述**现状**。改造过程与实测数据见 [历史档案](history/README.md)：

| 档案                                           | 内容 |
|----------------------------------------------|---|
| [宿主改造档案](history/host_refactor.md)          | 改造前的形状、结构性问题清单（A–H）、L0–L4 全过程与验收记录、L5 为什么不做、零框架驱动与 `SetParent` 实测 |
| [退出挂死调查](history/exit_hang_investigation.md) | §6 结论的由来：09-22 矩阵 → 09-23 修正 → 重启后复测 |
