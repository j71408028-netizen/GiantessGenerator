# Linux 支持说明

> 面向使用者的 Linux（X11 / XWayland）兼容说明。实测证据链见
> [linux_verification_log.md](linux_verification_log.md)，推进计划与验收标准见
> [linux_compat_plan.md](linux_compat_plan.md)。Wayland 原生会话是**已知限制**，不在支持范围内。

## 1. 支持范围

| 会话类型 | 状态 | 说明 |
|---|---|---|
| X11（Xorg / XWayland 下的 `DISPLAY`） | ✅ 受支持 | 全部功能可用；GitHub CI 的 Linux job 也跑在同一形态下 |
| Wayland + XWayland | ✅ 可用 | 图形界面与副本窗口经 XWayland 显示；窗口置顶取决于窗口管理器（GNOME Shell 实测**不可用**，见 §4） |
| 纯 Wayland（无 XWayland） | ❌ 不支持 | 副本窗口基于 GLFW/X11，纯 Wayland 后端创建不出窗口 |

前提：能提供 Tk 图形组件（`python3-tk`）与一个可用的 X display。无显示器环境可用
`xvfb-run` 跑 GUI 冒烟（软件渲染 llvmpipe 可行但慢，见 §6）。

## 2. 安装差异

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt     # 必须整份装，见下
python main.py
```

- **必须 `pip install -r requirements.txt`**：`networkx`（触发器依赖图）、`graphviz`
  （依赖图渲染的 Python 侧）、`openai`（AI 功能）都在其中。Linux 试跑环境曾因为
  只装了部分依赖，出现「点『检查依赖』直接 import 失败」；现在缺依赖会给出明确提示
  （`ui/scenario/dependency_dlg.py`），但功能仍然用不了。
- **`numpy` 现在也在 `requirements.txt` 里**（原先是漏声明，靠开发机恰好装过）。
  副本窗口背景合成、小游戏画布、挑战数值计算都直接 import 它。
- 触发器依赖图还需要系统级 `dot`：`sudo apt install graphviz`（Python 包不自带二进制）。
  程序会依次查 `PATH`、`/opt/homebrew/bin/dot`、`/usr/local/bin/dot`。
- 可选依赖的降级表现与 `requirements.txt` 内注释一致：
  | 可选依赖 | 缺失时的表现 |
  |---|---|
  | `edge-tts` | 对话语音不出声（联网合成），副本照常能玩 |
  | `miniaudio` / `soundfile` | 章节语音的「物理效果」退回零依赖的播放侧近似（回声 / 缓抖），方案校验会给出提示；装上即消除 |
  | `pywebview` | 内置小游戏窗口（`web` 后端的兼容通道）打不开，触发器会明确提示；`py` 后端小游戏不受影响 |
- 中文字体走 `ui/common/fonts.py` 的 Linux 候选链（Noto Sans CJK SC / 文泉驿微米黑），
  建议 `sudo apt install fonts-noto-cjk`。

## 3. XInitThreads：为什么要每个入口调一次

Tk（专业模式 / ME模式）与 Dear PyGui/GLFW（副本窗口）在同一个进程里**各开一条 X
display 连接**，libX11 默认按「单连接单线程」编译——两套工具包各带线程碰同一个 X
server 是 `BadWindow` 与 `ImGui_ImplGlfw_WindowFocusCallback` 段错误的经典成因。

因此每个会建窗口的入口文件在**最顶部**（任何 `tkinter` / `dearpygui` 之前）都要：

```python
from ui.common.x11_boot import boot_x11

boot_x11()          # 调 XInitThreads()，全进程一次性生效
```

已接入的入口：

| 入口 | 位置 |
|---|---|
| `main.py`（专业模式 / ME模式） | 第 5 行，`import tkinter` 之前 |
| `tests/smoke_switch.py` | `ensure_cwd()` 之后（该脚本的 tkinter 都是函数内延迟 import） |
| `tests/smoke_mini.py` | 路径注入之后、`tkinter` 延迟到 `boot_x11()` 之后再 import |
| `scripts/dungeon_autopilot.py` | 路径注入之后 |
| `ui/common/tk_host.py` | `TkHost.__init__` 兜底（幂等，覆盖直接 new 宿主的脚本） |

`XInitThreads()` 必须早于**第一条** X 连接；晚调不会报错但没有意义，`boot_x11()` 会在
已经调用过时直接返回上次结果。这一切都有开关：`GIANTESS_X11=0` 可整体关掉 Linux 原生
X11 支持（做有效/无效对比压测用，不是功能开关）。

## 4. 窗口置顶（`_NET_WM_STATE_ABOVE`）

Tk 的 `-topmost` 在 **GNOME Shell / XWayland 实测静默失效**：既不写
`_NET_WM_STATE_ABOVE`，`attributes("-topmost")` 也一直回报 0（历史记录见
[linux_verification_log.md §4.4](linux_verification_log.md)）。

现在的实现（`ui/mini/topmost.py` + `ui/common/x11.py`，纯 stdlib ctypes，不引入
`python-xlib` / `pywinctl`）：

1. 先照常调 Tk 的 `-topmost`（对 KWin 这类 WM 有效）；
2. 再向 root 窗口发 EWMH `ClientMessage`（`_NET_WM_STATE` / `_NET_WM_STATE_ABOVE`，
   `source_indication=1`），只对**窗口管理器认的顶层窗口**发——Tk 的 `winfo_id()`
   在 GNOME 下是客户区子窗口，`wm_frame` 是它的父窗口，`ui.common.x11.top_level_window()`
   负责上溯；
3. 回读 `_NET_WM_STATE` 确认（WM 是异步的，最多重试两次）；
4. 窗口尚未映射时无法回读，`<Map>` 事件后会自动再落一次。

**降级**：窗口管理器若没有在 `_NET_SUPPORTED` 里声明 `_NET_WM_STATE_ABOVE`，ME模式
设置页的「窗口置顶」开关会**置灰并标注「本桌面环境不支持」**，而不是留一个点了没反应
的开关；用户在不可用环境里尝试打开时也会收到一次明确提示。

支持矩阵（实测 / 待补）：

| 桌面环境 | X11 置顶 | 备注 |
|---|---|---|
| GNOME Shell（XWayland，mutter） | ✅ 可用 | 2026-10-06 实测：EWMH 写入后 `xprop` 可读到 `_NET_WM_STATE_ABOVE` |
| KWin | 待实测 | `_NET_WM_STATE_ABOVE` 在声明列表里，理论上可用 |
| 其它 WM | 待实测 | 以 `_NET_SUPPORTED` 声明为准，声明即尝试 |

## 5. X11 错误兼容处理器

`dungeon/window/dpg_state.install_x11_error_guard()`（仅 Linux）会安装一个 ctypes 的
Xlib 同步错误处理器，用于对付「Tk 事件泵处理到其它已销毁顶层窗口的旧事件」——
Xlib 默认处理器会直接 `exit(1)`，表现为冒烟里的 `X Error of failed request: BadWindow`。

策略（2026-10-06 收窄）：

- `BadWindow(3)` / `BadDrawable(9)`：预期内的陈旧窗口错误，忽略，并把前 3 次记入
  `dungeon.process_log`（副本窗口的过程日志面板能看到）；
- 其它错误码：**照样不退出进程，但每次都记录**（含错误码与 request code）。这是刻意
  的取舍——要把默认行为换成"让进程崩"，删掉该函数的调用点即可；风险登记见
  [linux_compat_plan.md §5](linux_compat_plan.md)。

## 6. 自动化与压测

```bash
# 离线守卫（不需要显示器）：13 项
.venv/bin/python tests/run_checks.py
.venv/bin/python tests/check_scenarios.py          # 方案校验，0 error 才退 0

# GUI 冒烟（需要显示器 / xvfb-run）
.venv/bin/python tests/smoke_mini.py
.venv/bin/python tests/smoke_switch.py
.venv/bin/python scripts/dungeon_autopilot.py --scene all --isolate --timeout 60
```

平台不适用项按设计 **SKIP**，不计失败：`callback-thread` 场景（靠 Windows
`SendMessage(WM_KEYDOWN)` 投原生按键）在非 Windows 跳过；`native-close` 同理。
`smoke_mini` 在 Linux 只断言「置顶调用不抛异常」，置顶有效性由
`ui/mini/topmost.py` 的 EWMH 回读路径覆盖。

长压测（抓 DPG/GLFW 反复拆建上下文的偶发原生崩溃）的标准姿势是 **`--isolate` +
外层 watchdog 逐轮起子进程、按退出码统计**，见
[Dungeon/window_automation.md §4](Dungeon/window_automation.md) 与
`developer_tools/_probe_stress.py`：

```bash
python developer_tools/_probe_stress.py smoke_switch 20            # XInitThreads 生效
GIANTESS_X11=0 python developer_tools/_probe_stress.py smoke_switch 20   # 对照组
```

## 7. 已知限制

- **纯 Wayland 不支持**：副本窗口（GLFW/X11）创建不出来，请用 X11 会话或 XWayland。
- **窗口置顶依赖桌面环境**：GNOME Shell 走 EWMH 可用；不声明该属性的 WM 下开关会置灰。
- **Dear PyGui 2.3.1 / GLFW 低概率原生段错误**：反复 `destroy_context → create_context
  → show_viewport` 后偶发 `SIGSEGV @ ImGui_ImplGlfw_WindowFocusCallback`，Python 层
  捕不到。属第三方库风险，缓解手段是 XInitThreads + 错误处理器收窄 + `--isolate`
  压测；长期跟随上游（见 [linux_compat_plan.md §5](linux_compat_plan.md)）。
- **Linux 打包脚本**：`build/linux/build_linux.sh` 属阶段三（P1）内容，尚未落地；
  当前 Linux 以源码方式运行。
