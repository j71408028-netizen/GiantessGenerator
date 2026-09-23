# DPG 在 Tk 宿主内的改造档案

> **状态：已归档。** L0–L4 已全部实施，L5 经评估不做。
> **现状见**：[宿主边界与可移植性](../window_host.md)、[窗口索引](../window.md) §4–§5。
> 本文保留改造动因、过程与实测数据，仅供溯源，不作为改代码的依据。

实施时间：L0/L1/L2 于 2026-09-23 上午，L3/L4 同日下午。

---

## 1. 改造前的形状：一次构造 = 一整局

`DungeonSessionWindow.__init__` 曾把四件事串成一条同步流程：建 UI → `parent.withdraw()` →
`dpg.start_dearpygui()`（**阻塞直到窗口关闭**）→ 收尾 / 弹 Tk 对话框 → `dpg.destroy_context()`。

这条流程决定了下面所有约束，也决定了所有痛点：

- 调用方在 Tk 回调里 `new` 一个类，`__init__` 不返回控制权，Tk 主循环整体停摆；
- 关闭窗口 = 让那个阻塞的循环结束，于是才有了 Windows 专有的 WM_CLOSE hack；
- 一局的「结果」只能靠调用方读实例私有属性（`_launch_error` / `_launch_choice`）；
- 想再开一局（回放）就在 Tk 侧再 `new` 一个窗口（`ui/exploration/exp_frame.py:510-545`）。

## 2. 结构性问题清单

| # | 问题 | 位置 | 后果 | 结果 |
|---|---|---|---|---|
| A | 构造即运行，控制权不交还 | `base.py` 旧 172-219 | 无法单测、无法异步、无法返回退出码 | 已修（L1） |
| B | Tk 主循环被冻结 | 旧 `start_dearpygui()` | 主窗口只能 withdraw/deiconify（闪烁、任务栏跳变）；`main_window_manager.py:133-139` 关闭应用时要「抢救式」 stop | 已修（L0） |
| C | 关闭路径依赖平台 hack | 旧 `_request_close()`：`FindWindowW(标题)` + `PostMessageW(WM_CLOSE)` | 靠窗口标题找句柄（临时/真实两套标题）；非 Windows 直接 `stop_dearpygui()` → 又回到崩溃路径 | 已删（L0） |
| D | 退出阶段挂死 | 自动化文档记录 | 只能靠父进程超时 kill；真人使用时表现为「关了副本窗口，进程不退」 | **未修，且已查明与 DPG 无关**（见 [退出挂死调查](exit_hang_investigation.md)） |
| E | 调度器是模块级单例，看门狗从后台线程调 DPG API | `dispatcher.py` | 违反「只有主线程调 DPG」；实测渲染期间 `threading.Timer` 拿不到 GIL，看门狗根本不触发 | 已删（L0 删看门狗与帧回调链；L3 删掉整个模块） |
| F | 两条帧回调链并存 | `dispatcher.py`（3 帧一跳）与 `launcher.py`（Ken Burns，2 帧一跳） | `ui.py` 曾注明「避免与调度器自身的 frame callback 链冲突」 | 已归零（L3） |
| G | 分层倒置：window 层依赖 Tk | `persistence.py` import `ui.common.dialogs`；`base.py` 摸 `parent.winfo_toplevel()` + ctypes 取 DPI；沿 `master/parent` 链用 `hasattr` 猜宿主 | 自动驾驶必须打桩 dialogs；`check_dungeon_layering.py` 只守 `dungeon/*.py`，`window/` 不受约束 | 已修（L2） |
| H | 阶段状态六个布尔量 + 结果回传靠私有属性 | `base.py`、`exp_frame.py:510-545` | 入口→会话切换、「加载回放要重开窗口」这类控制流散在调用方 | 已修（L4） |

L3 之前有 8 类后台线程（AI 流 / 细节提问 / 选项文字 / 结局生成 / 背景轮播 / 图标轮播 /
淡入淡出 / 重采样 `threading.Timer` / 仿流式动画），每类都要靠 `_closing` 标志轮询退出，
还要在 `run()` 收尾时逐个 `join(timeout=0.5)`。L3 之后收敛到 2 类（AI 类 + 像素类），
实测会话结束 `threading.enumerate()` 无非 daemon 残留。

## 3. 改造过程

### L0 — 手动驱动 DPG 渲染（最大杠杆）

```
# 改造前
dpg.setup_dearpygui(); dpg.show_viewport(); dpg.start_dearpygui()   # 阻塞

# 现在（base.DungeonWindowBase._run_frame_loop）
while True:
    self._pump_host_events()            # 宿主 Tk 的 update()：主窗口不冻结
    self._frame.tick()                  # 帧时钟：跑到期的帧任务 + drain 后台更新
    if not dpg.is_dearpygui_running():  # 用户点 X 或程序 stop
        self._on_close(); break
    dpg.render_dearpygui_frame()
    time.sleep(self.FRAME_INTERVAL)     # 默认 1/60s
```

- **收益**：一次解决 A/B/C——Tk 不再冻结、关闭不再需要 `FindWindowW`/WM_CLOSE（跨平台一致）、渲染节拍自己掌握。
- **实测**：真 Tk 宿主下一个完整会话渲染 134 帧、宿主 100ms 心跳跑了 26 次 → 宿主事件循环确实在跑；`--repeat 2` 同进程连开两轮 14/14 通过。
- **代价**：`set_exit_callback` 语义变化——手动模式下它**只在 `destroy_context()` 内部**才触发（实测），清理逻辑前移到帧循环的 not-running 分支，`ui.py` 不再注册它。
- **实现要点**：判定关闭只能用 `dpg.is_dearpygui_running()`（`is_viewport_ok()` 关闭后仍返回 True）；帧回调在手动渲染下确实会照常触发（实测 `frame_count` 随渲染递增），但 L3 之后不再使用；`run()` 用 `_session_running` 挡住重入。

### L1 — 拆开「构造」与「运行」

`__init__` 只做参数保存与会话初始化；`run()` 负责建 UI + 帧循环 + 收尾，并返回 `SessionResult`
（L4 之后不再是 `self`；需要细看窗口内部状态时可以自己留着实例引用，自动驾驶自检就是这么做的）。
调用方从 `DungeonSessionWindow(...)` 改成 `DungeonSessionWindow(...).run()`
（3 处：`exp_frame.py` ×2、`challenge.py` ×1，`dungeon_autopilot.py` 同步）。

### L2 — 宿主能力抽象成端口

| 端口方法 | 原实现 | 现位置 |
|---|---|---|
| `viewport_metrics()` → `(视口宽, 高, scale, 客户区宽, 高)` | `base.py` Tk + ctypes 取 DPI/客户区 | `TkHost.viewport_metrics` |
| `hide_window()` / `show_window()` | `parent.withdraw()` / `deiconify()+lift()` | `TkHost.hide_window/show_window` |
| `pump_events()` | `parent.update()`（L0 引入） | `TkHost.pump_events` |
| `dialog(kind, title, msg)` | `ui.common.dialogs.*`（7 处） | `TkHost.dialog`（`persistence` 里包装成 `_dialog_info/_warning/_ask`） |
| `register_active_window()` / `unregister_active_window()` | 沿 `master/parent` 链 `hasattr` 猜宿主 | `TkHost.register/unregister_active_window` |
| `default_font()` | `ui.common.fonts.dungeon_font_default()` | `TkHost.default_font` |
| （当时未做）`open_replay_file()` | `exp_frame.py` 的 `filedialog` | L4 补进端口 |

工厂方式：`DungeonSessionWindow(..., host=TkHost(self)).run()`；不传时用 `HostPort()`（无宿主的缺省实现）——
自动驾驶自检就是这条路径，于是**不再需要打桩 `ui.common.dialogs`**。

### L3 — 统一帧时钟，砍掉多余的线程与回调链

新增 `dungeon/window/frame.py`，`FrameScheduler` 是**窗口实例成员** `self._frame`，
取代旧的模块级单例 `_dispatch`（**`dungeon/window/dispatcher.py` 整个删除**）：

```python
frame.every(interval, fn, key=...)   # 周期性任务；同 key 天然互斥，掉帧不补跑
frame.after(delay, fn, key=...)      # 一次性延时任务
frame.cancel(key)                    # 按 key 取消（替代 threading.Timer.cancel）
frame.call(fn, *args)                # 后台线程投递：下一帧 drain 时在主线程执行
frame.tick(now=None)                 # 帧循环每帧调一次；先跑到期任务，再 drain
frame.stop()                         # 会话收尾：清空任务与队列，之后 call 一律丢弃
```

设计要点：

- **同 key 互斥**：`every/after` 传同一个 key 会顶掉上一条，不必再手动保存「上一次的 timer」去 cancel（这正是 `_bg_resize_timer` 那套写法容易漏的地方）。
- **repeating 任务掉帧不补跑**：到期后 `due_at = now + interval`，而不是累加若干次，卡顿后不会出现「突然连翻五张背景」。
- **tick 顺序至关重要**：先跑到期任务、再 `drain()` 队列，最后渲染。任务里 `call()` 的界面更新因此能在**同一帧末尾**上屏，少一帧延迟。
- `stop()` 之后 `call()` 返回 `False` 而不是抛异常——后台线程不必再轮询 `_closing`。

已迁入帧任务的五段逻辑：

| 原实现 | 现在 | 备注 |
|---|---|---|
| 入口背景轮播（`while` + `time.sleep` 线程） | `_start_background_cycle` 自续链 + `_BG_CYCLE_TASK` | 纯帧任务，退出无需 join |
| 结局图标轮播（同上） | `_ENDING_CYCLE_TASK` | |
| 窗口 resize 防抖（`threading.Timer`） | `_BG_REFRESH_TASK` + `after(delay)` | 新来一次 resize 会顶掉上一次（同 key） |
| Ken Burns 平移（`dpg.set_frame_callback`） | `_KB_TASK` + `every(0.0)` | 帧回调这条并行时间线消失 |
| 仿流式出字（后台线程逐字 `sleep`） | `_TEXT_ANIM_TASK` + `every(tick)` 按字符数推进 | |

**重活仍留给后台、但时机归帧时钟**：背景重采样与淡入淡出的像素混合（`Image.resize` / `Image.blend`）
是 CPU 密集操作，交给 `DungeonBackground` 里的单个 `PixelWorker`（单工作者线程 + FIFO 队列 + 毒丸收工）；
**什么时候做、做几帧由帧任务决定**，工作者只负责算。线程数因此不再随 resize 次数增长。

`_finish_session()` 的收尾顺序：`_frame.stop()` → `_background.shutdown()` → 恢复宿主 →
`join(_ending_thread, 0.5)` → 退出处理 → `destroy_context()`。

### L4 — 会话结果对象化

新增 `dungeon/window/result.py`：`SessionResult` + 四个原因常量，`run()` 返回它，
调用方**不再读窗口的私有属性**。

```python
result = DungeonSessionWindow(..., host=TkHost(self)).run()
if result.failed:
    ui.common.dialogs.showerror("错误", result.launch_error)
    return
# 其余情况（入口直接返回 / 正常会话结束 / 回放）窗口内部已处理完
```

回放控制流收回窗口内（同时解决了 H）：

1. 端口补 `open_replay_file()`（`TkHost` 用 `filedialog` + JSON 校验实现；缺省实现返回 `None`，等价于「用户取消」）；
2. 入口页「加载回放」→ `window._on_entry_replay()` 自己向宿主要数据：拿不到就**留在入口页**（不再关窗），拿到了就在**同一个 DPG 生命周期**里走 `_enter_replay_phase()` 切 `is_replay`；
3. `ui/exploration/exp_frame.py` 里那段「再 `new` 一个 `DungeonSessionWindow(is_replay=True)`」的调用方控制流已删除，`_load_replay_file()` 也一并移除。

自检覆盖：新增 `entry-replay` 场景，断言「宿主被问过两次文件（一次取消、一次选中）」、
「取消选择时不关窗/不切回放」、「同一窗口实例切到回放且回放只跑一次生命周期」。

### L5 — 进程隔离（评估后决定不做）

设想：把 DPG 渲染放到子进程，主进程只跑领域逻辑 + Tk。收益是 c0000005 崩溃不再带走整个应用、
宿主彻底不阻塞；代价是跨进程命令/事件协议、资源与组件包都在子进程、背景纹理数据要共享内存。

**决定依据**（2026-09-23 01:10 后）：挂死的根因是**环境**而不是代码——任何建过 GUI 窗口的 Python
进程都可能退不出去（纯 Tk、不 import DPG 同样复现），且机器重启后 18 次 GUI 生命周期全部干净退出，
包括同一进程内连做 10 轮 create/destroy。详见 [退出挂死调查](exit_hang_investigation.md)。

因此 L5 唯一能治愈的那个症状**并不存在**，而它的代价真实存在。结论：**不做**。

## 4. 验收记录

（本机，DPG 2.3.1 + Python 3.13 + Windows，2026-09-23 下午，L3 + L4 之后）

| 验证项 | 结果 |
|---|---|
| 五个自检场景（会话关闭 / 入口返回 / 入口进入 / **入口回放** / Tk 宿主） | **48 项断言全绿**（`entry-replay` 10 项、`session-close` 16 项、`entry-start` 8 项、`entry-cancel` 7 项、`tk-host` 7 项） |
| 会话关闭路径连开两轮（`--repeat 2 --isolate`） | **2/2 通过** |
| 会话结束后是否留下自己的线程 | **`threading.enumerate()` 无非 daemon 残留** |
| 回放是否复用同一个窗口生命周期 | **是** |
| 无 GUI 自检是否有回归 | `check_dungeon_finalize.py` 32/32、`check_dungeon_layering.py` PASSED（15 个窗口模块） |

路线验收（每一步都跑过）：`python scripts/dungeon_autopilot.py` +
`--scene session-close --repeat 2 --isolate` + `check_dungeon_layering.py` / `check_dungeon_finalize.py` +
真应用手测「进入副本 → 返回 → 再进入副本 → 加载回放」。

> **2026-09-23 修正**：早先认为「L0 顺带能修好退出挂死」是错的。当晚复测发现退出挂死与 DPG 无关
> （纯 Tk、不 import dpg 的进程同样挂死）。手动渲染依然值得做（它解决宿主冻结、平台 hack、可测性），
> 但不能指望它修挂死。

## 5. 嵌入式挂载的实测（结论：做不到）

DPG 2.3.1 的视口是**独立的 GLFW 顶层窗口**，公开的 API 里没有任何取窗口句柄的入口
（`get_viewport_*` 只有尺寸/位置/标题），想拿到 HWND 只能 `FindWindowW(按标题)`
（这正是 L0 之前 `_request_close()` 用的土办法，现已删除；现在唯一还在用 `FindWindowW` 的地方是
首帧的中文标题修正 `_fix_windows_title`）。

实测 `SetParent(hwnd_dpg, hwnd_host)` 强行改成子窗口：`SetParent` 返回成功，但紧接着
`GetParent(hwnd_dpg)` 又变回 0（GLFW 自己把父级改回去了）；渲染本身没崩，所以最多算
「位置跟着宿主移动」的伪嵌入，不是真正的控件级嵌入，跨平台更是无从谈起。

## 6. 零框架驱动的实测

驱动侧 L0 之后只依赖宿主「能处理一次挂起事件」这一个能力。本机实测：**不用 Tk、不用任何 UI 框架**，
纯 `while` 循环 + `sleep` 就能把会话跑起来——两种节拍源（裸循环 / 固定帧预算）各跑一轮，
渲染、关闭、`destroy_context` 全部正常，1.84s 跑完。

既然连「没有框架」都能驱动，那么任何能提供定时回调的框架都能当宿主：

| 宿主 | 节拍源 | 需要实现的端口 |
|------|--------|----------------|
| Tk（当时现状） | `while` + `root.update()` + `sleep(1/60)` | `parent.withdraw/deiconify` + `winfo_*` + ctypes DPI + `ui.common.dialogs` |
| Qt | `while` + `QApplication.processEvents()`，或 `QTimer(16)` | `QWidget.hide/show` + `devicePixelRatioF()` + `QMessageBox` / `QFileDialog` |
| 无框架脚本 / CI | `while` + `sleep` | 屏幕尺寸 + no-op + print |
| Web / 其它进程 | 做不到（DPG 必须在本进程有 GLFW 窗口） | — |
