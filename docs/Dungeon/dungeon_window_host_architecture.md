# 副本窗口架构优化分析：Tk 宿主 × DearPyGui

> 状态：**只做分析，未改动任何代码**（2026-09-22 实测）。
> 姊妹篇：[窗口逻辑与开发事项](dungeon_window.md)（现状约束）、
> [调试自动化](dungeon_window_automation.md)（自动驾驶脚本）。
> 本文只回答一个问题：DPG 在 Tk 宿主内的架构能不能优化、怎么优化、代价多大。

## 0. 结论

**能，而且最大的杠杆不是"多写几个脚本"，而是换掉 DPG 的驱动方式**：
不再用 `dpg.start_dearpygui()` 把整个 Tk 主循环堵死，改由 Tk 的 `after()` 节拍
调用 `dpg.render_dearpygui_frame()` 手动渲染一帧。

本机实测（DPG 2.3.1 + Python 3.13 + Windows）四项全部通过：

| 验证项 | 结果 |
|---|---|
| Tk 主循环驱动 DPG 渲染 | 45 帧 / 1.47s（≈31fps），期间 Tk 心跳定时器跑了 43 次 → **宿主不冻结** |
| 一个进程内连开两轮窗口 | 两轮 `create_context → 渲染 → destroy_context` 均正常，**退出码 0，全程 3.4s** |
| 关闭路径（最接近 §5.1 崩溃场景） | 两轮各投递一次 WM_CLOSE：窗口关闭可检测、继续渲染不报错、`destroy_context` 干净，**退出码 0，全程 2.9s** |
| 手动渲染下是否还能接收输入 | 合成鼠标点击后 1ms 内触发 DPG 的 mouse click handler → **输入链路完好** |

对照现状：现有架构跑完一次会话后，进程会在退出阶段**挂死**（见
`dungeon_window_automation.md` 的实测记录，~190MB 常驻、无残留非 daemon 线程）。
上面第 2、3 项正是这条挂死路径的形状，换成手动渲染后**不复现**——高度怀疑根因在
`start_dearpygui()` 的内部循环/GLFW 清理，而不是我们的业务代码。

---

## 1. 现状：一次构造 = 一整局

`DungeonSessionWindow.__init__`（`dungeon/window/base.py:172-219`）把四件事串成
一条同步流程：建 UI → `parent.withdraw()` → `dpg.start_dearpygui()`（**阻塞直到
窗口关闭**）→ 收尾/弹 Tk 对话框 → `dpg.destroy_context()`。

这条流程决定了下面所有约束，也决定了所有痛点：

- 调用方在 Tk 回调里 `new` 一个类，`__init__` 不返回控制权，Tk 主循环整体停摆；
- 关闭窗口 = 让那个阻塞的循环结束，于是才有了 Windows 专有的 WM_CLOSE hack；
- 一局的"结果"只能靠调用方读实例私有属性（`_launch_error` / `_launch_choice`）；
- 想再开一局（回放）就在 Tk 侧再 `new` 一个窗口（`ui/exploration/exp_frame.py:510-545`）。

## 2. 结构性问题清单

| # | 问题 | 位置 | 后果 |
|---|---|---|---|
| A | 构造即运行，控制权不交还 | `base.py:172-219` | 无法单测、无法异步、无法返回退出码 |
| B | Tk 主循环被冻结 | `base.py:192` `start_dearpygui()` | 主窗口只能 withdraw/deiconify（闪烁、任务栏跳变）；`main_window_manager.py:133-139` 关闭应用时要"抢救式" stop |
| C | 关闭路径依赖平台 hack | `base.py:434-448` `_request_close()`：`FindWindowW(标题)` + `PostMessageW(WM_CLOSE)` | 靠窗口标题找句柄（临时/真实两套标题，`base.py:166-167`）；非 Windows 直接 `stop_dearpygui()` → 又回到 §5.1 崩溃路径 |
| D | 退出阶段挂死 | 自动化文档记录 | 只能靠父进程超时 kill；真人使用时表现为"关了副本窗口，进程不退" |
| E | 调度器是模块级单例，看门狗从后台线程调 DPG API | `dispatcher.py:58-74,77` | 违反"只有主线程调 DPG"（文档 §3）；实测渲染期间 `threading.Timer` 拿不到 GIL，看门狗根本不触发，形同虚设 |
| F | 两条帧回调链并存 | `dispatcher.py:48-56`（3 帧一跳）与 `launcher.py:434-460`（Ken Burns，2 帧一跳） | `ui.py:266-275` 已注明"避免与调度器自身的 frame callback 链冲突"，属于已知的互相覆盖面 |
| G | 分层倒置：window 层依赖 Tk | `persistence.py:6` import `ui.common.dialogs`（6 处弹框）；`base.py:470-490` 摸 `parent.winfo_toplevel()` + ctypes 取 DPI；`base.py:512-528` 沿 `master/parent` 链用 `hasattr` 猜宿主 | 自动驾驶必须打桩 dialogs；`check_dungeon_layering.py` 只守 `dungeon/*.py`，`window/` 不受约束 |
| H | 阶段状态六个布尔量 + 结果回传靠私有属性 | `base.py:97-142`、`exp_frame.py:510-545` | 入口→会话切换、"加载回放要重开窗口"这类控制流散在调用方，难测难改 |

另有 8 类后台线程（AI 流 / 细节提问 / 选项文字 / 结局生成 / 背景轮播 / 图标轮播 /
淡入淡出 / 重采样 `threading.Timer` / 仿流式动画），每类都要靠 `_closing` 标志轮询
退出，还要在 `__init__` 里逐个 `join(timeout=0.5)`。

---

## 3. 优化方案（按性价比排序）

### L0 — Tk 节拍驱动 DPG（★ 最大杠杆，已实测可行）

```
# 现在
dpg.setup_dearpygui(); dpg.show_viewport(); dpg.start_dearpygui()   # 阻塞

# 改后
dpg.setup_dearpygui(); dpg.show_viewport()
def _pump():                       # 由 root.after(16, _pump) 驱动
    drain_pending_ui_tasks()       # 后台线程投递的更新，每帧清空
    if not dpg.is_dearpygui_running():  # 用户点了 X
        self._closing = True; self._cleanup(); return
    dpg.render_dearpygui_frame()
    root.after(16, _pump)
```

- **收益**：一次解决 A/B/C/D——Tk 不再冻结（宿主窗口可保留、不闪）、关闭不再需要
  `FindWindowW`/WM_CLOSE（跨平台一致）、渲染节拍自己掌握（可暂停：弹模态框时停泵）、
  两轮开关与 WM_CLOSE 关闭实测退出码 0。
- **代价**：`_on_close`（`set_exit_callback`）语义变化——手动模式下它在
  **`destroy_context()` 内部**才触发（实测），所以清理逻辑必须前移到 `_pump` 的
  "running=False" 分支，不能还指望退出回调。
- **风险/待验证**：帧率由 `after(16)` 决定（实测 ≈31fps，可能是 vsync 或 Tk 定时器
  粒度；若要 60fps 试 `after(8)` 或关 `set_viewport_vsync`）；`split_frame` 等依赖
  主循环的行为需复核；`is_viewport_ok()` 在关闭后仍返回 True，**判定关闭只能用
  `is_dearpygui_running()`**。

### L1 — 拆开"构造"与"运行"

`__init__` 只做参数保存与会话初始化；新增 `run()` 负责建 UI + 装帧节拍 + 收尾。
调用方变成 `win = DungeonSessionWindow(...); result = win.run()`。
收益：可测、可捕获异常、能返回结果对象；与 L0 是同一改动的自然切分。代价极小。

### L2 — 宿主能力抽象成端口

定义 `DungeonHost` 协议，把 window 层对 Tk 的直接依赖全部收进去：

| 端口方法 | 现在的实现 |
|---|---|
| `viewport_metrics()` | `base.py:466-501`（Tk + ctypes 取 DPI/客户区） |
| `suspend()` / `resume()` | `parent.withdraw()` / `deiconify()` |
| `dialog(kind, title, msg)` | `ui.common.dialogs.*`（6 处） |
| `open_replay_file()` | `exp_frame.py:547-563` 的 `filedialog` |
| `on_session_finished(result)` | 调用方读 `_launch_error` / `_launch_choice` |

生产侧给 `TkHost`，测试侧给 `HeadlessHost`。收益：`dungeon/window/*` 不再 import
`ui`/`tkinter`，分层守卫可以把 `window/` 也纳入；自动驾驶脚本免掉打桩
（现在要打桩 dialogs + paths）；L0 之后想换 Qt/纯脚本宿主也不用改业务代码。

### L3 — 统一帧时钟，砍掉多余的线程与回调链

- `_dispatch` 不再需要 `install()` / 看门狗 / `set_frame_callback`：由 `_pump` 每帧
  drain 队列即可（**根除 E + F**）；`_dispatch` 也可顺势变成实例成员，去掉单例。
- 纯计时的后台线程改为帧任务：背景轮播、结局图标轮播、resize `threading.Timer`、
  Ken Burns、仿流式动画——它们只做"每隔 N 秒/帧动一下"，放到帧时钟里就不必再轮询
  `_closing`、也不必 join。
- **保留**真正吃 CPU 的后台线程：AI 流式、细节提问、选项/结局生成、背景淡入淡出的
  像素混合（只把 `set_value` 投递回主线程）。
  收益：线程数从 8 类降到 ~4 类，退出路径只剩 AI 类；GIL 争用减少。

### L4 — 会话结果对象化

`SessionResult(launch_error, launch_choice, replay_path, report_path, ended)` 取代
"new 一个类 + 读私有属性"。回放加载改成在同一窗口内切 `is_replay`，不再让调用方
`new` 第二个窗口（解决 H）。

### L5 —（远期，视 L0 后的崩溃情况再定）进程隔离

把 DPG 渲染放到子进程，主进程只跑领域逻辑 + Tk。收益：c0000005 崩溃不再带走整个
应用、宿主彻底不阻塞。代价：需要跨进程命令/事件协议、资源与组件包都在子进程、背景
纹理数据要共享内存。**只有在 L0 之后仍观察到 DPG 崩溃时才值得做**——L0 已经消除了
"在回调里 stop"这个已知崩溃诱因。

---

## 4. 建议路线

1. **先做 L0 + L1 的最小切片**：只改 `base.py` 的驱动方式与生命周期切分，不改其它
   mixin；用现有 `scripts/dungeon_autopilot.py` 的三个场景回归（入口返回 / 入口进入 /
   会话关闭），看挂死是否消失、断言是否仍全绿。
2. 挂死消失、场景全绿 → 再上 **L3**（帧时钟 + 去单例），此时 autopilot 可以去掉
   看门狗相关打桩。
3. 随后 **L2**（宿主端口）：把 window 层的 Tk 依赖收干净，顺手让
   `check_dungeon_layering.py` 把 `dungeon/window/` 也纳入扫描。
4. 最后 **L4**（结果对象化），顺带把"加载回放要重开窗口"这段调用方控制流收进窗口内。

每一步的验收：`python scripts/dungeon_autopilot.py`（真窗口生命周期）+ 真应用手测
"进入副本 → 返回 → 再进入副本"（§5.1 的崩溃回归）+ 关窗后进程能正常退出。

## 5. 明确不建议做的

- **不要**在同一进程里同时持有两个 DPG context 或并发两个副本窗口（DPG 的上下文与
  视口都是全局单例语义）。
- **不要**在帧回调/`_pump` 里 `join` 线程（会卡帧）。
- **不要**为了"修挂死"去堆看门狗：实测 Python 层定时器在 DPG 渲染期间拿不到 GIL，
  超时兜底只能放父进程。
- **不要**动 `dungeon/` 领域层（分层守卫脚本守着），本次优化只在 `dungeon/window/`
  与调用方之间动刀。
