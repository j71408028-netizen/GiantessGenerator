# Linux 支持说明

> 面向使用者的 Linux（X11 / XWayland）兼容说明。Wayland 原生会话是**已知限制**，
> 不在支持范围内。
>
> §1–§8 是**现状**：支持范围、安装差异、各个兼容处理与已知限制。
> 兼容工作的**过程与实测证据**（兼容计划、试跑检查记录）已移入
> [linux-history.md](linux-history.md)，只用于溯源、不据此改代码。

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

- **必须 `pip install -r requirements.txt`**：`openai`（AI 功能）在其中。Linux 试跑环境曾
  因为只装了部分依赖出现难查的报错，整份装最省事。
- **`numpy` 现在也在 `requirements.txt` 里**（原先是漏声明，靠开发机恰好装过）。
  副本窗口背景合成、小游戏画布、挑战数值计算都直接 import 它。
- **不再需要 `networkx` / `graphviz`，也不需要系统级 `dot`**：编辑器「检查动态」已改为
  自绘画布，不再出 PNG，依赖图那套外部重器连同它们的安装说明一并退场（见
  [副本关系图与图校验](../../Dungeon/scenario_graph.md)）。
- 可选依赖的降级表现与 `requirements.txt` 内注释一致：
  | 可选依赖 | 缺失时的表现 |
  |---|---|
  | `edge-tts` | 对话语音不出声（联网合成），副本照常能玩 |
  | `miniaudio` / `soundfile` | 章节语音的「物理效果」退回零依赖的播放侧近似（回声 / 缓抖），方案校验会给出提示；装上即消除 |
  | `pywebview` | 内置小游戏窗口（`web` 后端的兼容通道）打不开，触发器会明确提示；`py` 后端小游戏不受影响 |
- 中文字体（两条链路，都靠系统装中文字体，建议 `sudo apt install fonts-noto-cjk`）：
  - 主界面（Tk / CustomTkinter）走 `ui/ctk/fonts.py` 的家族候选链
    （Noto Sans CJK SC / 文泉驿微米黑），由 Tk 自己按家族名向 fontconfig 取字体；
  - 副本窗口（imgui-bundle）**只认字体文件**，走 `dungeon/window/fonts.py`：Linux 上先问
    `fc-match`（只接受声明覆盖 `zh-cn` 的结果，避免不存在的家族名被替换成拉丁字体），
    查不到再按发行版常见路径回退（Noto CJK / 思源黑体 / 文泉驿 / Droid Sans Fallback）。
    系统里一个中文字体都没有时才会退到 DejaVu Sans——那没有中文字形，中文会是方框；
    窗口开起来后可在过程日志（F12）里看到实际选中的字体文件。
  - 已知取舍：副本窗口按文件建字体，不做集合字体（`.ttc`）内的 face 序号选择（迁移前的
    Dear PyGui 亦然），Noto CJK 只会取到第 0 个
    face（JP）——码位覆盖完整、不会缺字，个别字的写法是日文变体；小游戏离屏画布用 PIL
    渲染，能按 fontconfig 给的序号取到简体 face（详见 `dungeon/window/fonts.py` 模块说明）。

## 3. XInitThreads：为什么要每个入口调一次

Tk（专业模式 / ME模式）与副本窗口的 GL 栈（GLFW，迁移前是 Dear PyGui）在同一个进程里
**各开一条 X display 连接**（产品路径里两者分别在父子进程，只有自检/自动驾驶会同进程
共存），libX11 默认按「单连接单线程」编译——两套工具包各带线程碰同一个 X
server 是 `BadWindow` 与 `ImGui_ImplGlfw_WindowFocusCallback` 段错误的经典成因。

因此每个会建窗口的入口文件在**最顶部**（任何 `tkinter` / 副本窗口 GL 栈之前）都要：

```python
from ui.hosting.x11_boot import boot_x11

boot_x11()          # 调 XInitThreads()，全进程一次性生效
```

已接入的入口：

| 入口 | 位置 |
|---|---|
| `main.py`（专业模式 / ME模式） | 第 5 行，`import tkinter` 之前 |
| `tests/smoke_switch.py` | `ensure_cwd()` 之后（该脚本的 tkinter 都是函数内延迟 import） |
| `tests/smoke_mini.py` | 路径注入之后、`tkinter` 延迟到 `boot_x11()` 之后再 import |
| `scripts/dungeon_autopilot.py` | 路径注入之后 |
| `ui/hosting/tk_host.py` | `TkHost.__init__` 兜底（幂等，覆盖直接 new 宿主的脚本） |

`XInitThreads()` 必须早于**第一条** X 连接；晚调不会报错但没有意义，`boot_x11()` 会在
已经调用过时直接返回上次结果。这一切都有开关：`GIANTESS_X11=0` 可整体关掉 Linux 原生
X11 支持（做有效/无效对比压测用，不是功能开关）。

## 4. 窗口置顶（`_NET_WM_STATE_ABOVE`）

Tk 的 `-topmost` 在 **GNOME Shell / XWayland 实测静默失效**：既不写
`_NET_WM_STATE_ABOVE`，`attributes("-topmost")` 也一直回报 0（历史记录见
附录B §4.4）。

现在的实现（`ui/mini/topmost.py` + `ui/hosting/x11.py`，纯 stdlib ctypes，不引入
`python-xlib` / `pywinctl`）：

1. 先照常调 Tk 的 `-topmost`（对 KWin 这类 WM 有效）；
2. 再向 root 窗口发 EWMH `ClientMessage`（`_NET_WM_STATE` / `_NET_WM_STATE_ABOVE`，
   `source_indication=1`），只对**窗口管理器认的顶层窗口**发——Tk 的 `winfo_id()`
   在 GNOME 下是客户区子窗口，`wm_frame` 是它的父窗口，`ui.hosting.x11.top_level_window()`
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

`ui/hosting/x11.install_x11_error_guard()`（仅 Linux）会安装一个 ctypes 的
Xlib 同步错误处理器，用于对付「Tk 事件泵处理到其它已销毁顶层窗口的旧事件」——
Xlib 默认处理器会直接 `exit(1)`，表现为冒烟里的 `X Error of failed request: BadWindow`。

策略（2026-10-06 收窄）：

- `BadWindow(3)` / `BadDrawable(9)`：预期内的陈旧窗口错误，忽略，并把前 3 次记入
  `dungeon.process_log`（副本窗口的过程日志面板能看到）；
- 其它错误码：**照样不退出进程，但每次都记录**（含错误码与 request code）。这是刻意
  的取舍——要把默认行为换成"让进程崩"，删掉该函数的调用点即可；风险登记见
  附录A §5。

## 6. 自动化与压测

```bash
# 离线守卫（不需要显示器）：30 项
.venv/bin/python tests/run_checks.py
.venv/bin/python scripts/scenario_validate.py    # 方案校验，0 error 才退 0

# GUI 冒烟（需要显示器 / xvfb-run）
.venv/bin/python tests/smoke_mini.py
.venv/bin/python tests/smoke_switch.py
.venv/bin/python scripts/dungeon_autopilot.py --scene all --isolate --timeout 60
```

平台不适用项按设计 **SKIP**，不计失败：`native-input` 场景（靠 Windows
`SendMessage(WM_KEYDOWN)` 投原生按键）在非 Windows 跳过；`native-close` 同理。
`smoke_mini` 在 Linux 只断言「置顶调用不抛异常」，置顶有效性由
`ui/mini/topmost.py` 的 EWMH 回读路径覆盖。

长压测（抓副本窗口 GL 栈——迁移前是 DPG/GLFW——反复拆建上下文的偶发原生崩溃）的标准姿势是 **`--isolate` +
外层 watchdog 逐轮起子进程、按退出码统计**，见
[Dungeon/window_automation.md §4](../../Dungeon/window_automation.md) 与
`developer_tools/_probe_stress.py`：

```bash
python developer_tools/_probe_stress.py smoke_switch 20            # XInitThreads 生效
GIANTESS_X11=0 python developer_tools/_probe_stress.py smoke_switch 20   # 对照组
```

## 7. 打包与发布

```bash
bash build/linux/build_linux.sh                # 只打包
bash build/linux/build_linux.sh --self-check   # 打包 + 打包自检（需要显示器）
PYTHON_BIN=python3.12 bash build/linux/build_linux.sh   # 指定解释器
```

- **形态**：PyInstaller **onedir**，产物 `dist/GiantessGenerator/`（可执行文件与目录同名），
  另打 `dist/GiantessGenerator-linux-<arch>.tar.gz` 目录包。AppImage / deb 与桌面集成
  （`.desktop`、PATH 安装）不在本期。
- **依赖收集**：与 Windows / macOS 脚本同一套 `--collect-all`（customtkinter / imgui_bundle /
  PIL / openai / numpy / webview），**不带** `zai`（项目没有该依赖，见
  附录A §2.3）。关系图不再需要 Graphviz 的 `dot` 二进制，打包无外部可选依赖。
- **构建环境**：脚本自建 `.venv-buildlinux` 并 `pip install -r requirements.txt pyinstaller`，
  不污染开发 venv。Python 3.14 下 `pywebview` 会装到 6.2.1（更新的版本声明 `<3.14`），
  web 后端小游戏窗口照常可用。
- **数据目录**：打包版走 XDG —— `$XDG_DATA_HOME/GiantessGenerator/data`
  （默认 `~/.local/share/GiantessGenerator/data`），首启从包内 `data/packs`、`data/static`
  拷贝出可写副本；源码运行仍用仓库内 `data/`（见 `paths.py`）。
- **打包自检**（`--self-check`）：同一套依赖把 `scripts/dungeon_autopilot.py` 也打成一个包，
  然后
  1. 用临时 `XDG_DATA_HOME` 启动发布包（不碰真实用户数据），确认进程存活且
     `data/{packs,static,user,archives}` 引导成功（有 `xwininfo` 时顺带核对主窗口标题）；
  2. 在**打包态**里逐场景真开副本窗口：`entry-start`（探索入口 → 进副本）、
     `session-close`、`text-components`（文本 / 字体组件）、`mini-game`（小游戏触发器
     返回值链）、`mini-game-py`（py 后端小游戏覆盖层舞台）。

  退出码 0 才算通过——这是「打包后仍然能进副本、小游戏触发器还能跑」的可复现证据。

### 7.1 CI（GitHub Actions）

| 工作流 | 触发 | 内容 |
|---|---|---|
| [`checks.yml`](../../../.github/workflows/checks.yml) | 每 PR / 推 `main` | 离线门禁：Ubuntu（Python 3.12 与 3.14 各一轮）+ Windows（3.12）跑 `tests/run_checks.py`（全部离线守卫）与 `scripts/scenario_validate.py`（默认方案 0 error） |
| [`gui-smoke.yml`](../../../.github/workflows/gui-smoke.yml) | 每晚 02:00（CST）+ 手动 | xvfb + 软件 GL（llvmpipe）：`dungeon_autopilot --scene all --isolate`、`smoke_mini`、`smoke_switch`；另一个 job 跑 `build/linux/build_linux.sh --self-check` 并上传目录包 |

GUI 用例对长会话与驱动状态敏感（见 §5 与 附录A §5）：
某条明显 flaky 时先在工作流里注释掉它，保留 autopilot 离线场景组。

## 8. 已知限制

- **纯 Wayland 不支持**：副本窗口（GLFW/X11）创建不出来，请用 X11 会话或 XWayland。
- **窗口置顶依赖桌面环境**：GNOME Shell 走 EWMH 可用；不声明该属性的 WM 下开关会置灰。
- **Dear PyGui 2.3.1 / GLFW 低概率原生段错误**：反复 `destroy_context → create_context
  → show_viewport` 后偶发 `SIGSEGV @ ImGui_ImplGlfw_WindowFocusCallback`，Python 层
  捕不到。属第三方库风险，缓解手段是 XInitThreads + 错误处理器收窄 + `--isolate`
  压测；长期跟随上游（见 附录A §5）。
- **包形态**：目前只发 tar.gz 目录包，没有 AppImage / deb / 桌面项；运行方式为解包后直接
  执行 `GiantessGenerator/GiantessGenerator`。

---

> **历史归档**：本文的兼容计划（目标、逐阶段落地内容、验收标准、风险登记与
> 执行记录）与试跑检查记录（逐项实测证据链）已移入
> [linux-history.md](linux-history.md)，只用于溯源、不据此改代码。
