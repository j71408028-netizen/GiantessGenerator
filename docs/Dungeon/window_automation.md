# 副本窗口调试自动化索引

> **定位**：`scripts/dungeon_autopilot.py` 的**场景、注入点与坑清单索引**。
> 窗口生命周期见 [窗口文档](window.md)，宿主边界见 [宿主边界与可移植性](window_host.md)，
> 全系列入口见 [副本文档索引](README.md)。
> 本文回答一个问题：**调试副本窗口能不能不靠人手点电脑？**

## 目录

| 节 | 内容 |
|---|---|
| §1 | 结论与现状（场景表） |
| §2 | 原先为什么必须手点 |
| §3 | 分层方案与注入点索引 |
| §4 | 命令与判定方式 |
| §5 | 坑清单 |
| §6 | 下一步建议 |
| §7 | 仍然需要人工的部分 |

---

## 1. 结论与现状

**能，而且大部分已经做到。** 已落地 `scripts/dungeon_autopilot.py`（GUI 冒烟：无人手点击、不联网、不碰真实 `data/`）。

基线实测（本机 Windows / imgui-bundle 1.92.900 / Python 3.13，2026-10-17 P7 复跑）：

| 场景 | 覆盖 | 断言 |
|---|---|---|
| `chapter-bgm` | 章节背景音乐接线：起始章节起播 → 跳到无音乐章节停播 → 收尾收工（替身后端，自检不出声；真实后端由 `check_audio_chain.py` 探） | 7 项 |
| `chapter-voice-fx` | 章节对话语音**物理**效果接线（她 / 其他人两档）：她那一档开分拍回声 → 其他人那一档只缓抖 → 叙述句不念也不进合成队列 → 跳到没配效果的章节清空 → 回放记录带 `voice_fx` → 收尾线程与临时音频收干净（假合成器不联网 + 替身后端不出声；假音频必然解不开，顺带验证「解不开也不拖垮朗读」） | 11 项 |
| `dialogue-voice` | Solea/Bulla 对话语音接线：她 / 主角 / 配角分嗓子、叙述句不念、语音总线拧到 0 一声不吭（连合成都省）、收尾收工与临时音频清理（假合成器不联网 + 替身后端不出声） | 9 项 |
| `entry-cancel` | 探索模式入口页 → 中途读一次 `ui_snapshot()` 断言入口页状态 → 点「返回」 | 9 项 |
| `entry-replay` | 入口页「加载回放」→ 取消一次 → 再选文件 → **同一窗口内**切回放（L4 回归；「取消那一刻仍停在入口页」也由快照断定） | 12 项 |
| `entry-start` | 探索模式入口页 → 点「开始副本」→ 步进 → 关闭 | 8 项 |
| `mini-game` | web 后端小游戏触发器：整数返回值落选择记录、关联 goto 同步跳转、中断不清章节（桩宿主立即回传结果） | 2 项 |
| `mini-game-escape` | py 后端 `escape_giantess` 真覆盖层舞台：离屏画布 GL 纹理上传 + 每帧绘制指令 + ESC 中止 | 1 项 |
| `mini-game-py` | py 后端小游戏（覆盖层舞台）：打开 / 结算记返回值 / ESC 中止 | 1 项 |
| `mini-game-repeat` | 可再次触发的小游戏：结算后回副本主页面（不原地重启）、后续步进可再进 | 1 项 |
| `native-close` | 视口通过原生关闭键（X / `WM_CLOSE`）关闭：队列不得残留 `WM_QUIT`、收尾提示与宿主计时器不卡死 | 7 项 |
| `native-input` | 真原生按键（`SendMessage` 直投 `WM_KEYDOWN`）落到窗口**自己的输入接线**上并在帧循环线程执行（原名 `callback-thread`：立即模式没有回调线程，这条不变量换了个守法的对象） | 6 项 |
| `session-close` | 挑战模式直进会话 → 步进 → 中途读一次 `ui_snapshot()` → 关闭（含帧时钟 / 像素工作者收工、结果对象字段） | 19 项 |
| `text-components` | 文本组件家族（`text_card` / `text_nvl`）接管显示的会话中途可见状态 + `text_component` 字段三选一与旧写法提升 + 覆盖层服务面（调出/阅读模态挂起/关闭恢复/兄弟组件只读访问）；`("check", fn)` 脚本动作在帧循环线程上断言 | 9 项 |
| `tk-host` | 宿主换成真 Tk 根窗口（`TkHost` 适配器）：会话期间宿主心跳照跑（L0 回归） | 7 项 |

十五个场景共 **109 项断言全绿**（2026-10-17 imgui 迁移后实测：7 + 11 + 9 + 9 + 12 + 8 + 2 + 1 + 1 + 1 + 7 + 6 + 19 + 9 + 7），
全部子进程**自行干净退出**（rc=0）。每个场景结束还会断言 **会话内无残留非 daemon 线程**
（`threading.enumerate()` 为空）——这是 L3 帧时钟 + `PixelWorker` 的验收条件。
单场景耗时数秒到二十几秒不等，全套（`--scene all`）几分钟量级。

窗口状态一律经窗口自己的 **`ui.ui_snapshot()` 值快照**读取（P5 交付、P7 收编）：
立即模式没有 retained 控件树，DPG 时代的 `dungeon_autopilot` 内省（`does_item_exist` /
`get_item_configuration`）与 `minimize_viewport` 探针在 P7 一并删除——它们在没有 DPG
上下文的进程里是 access violation，不是异常。

注意：官方组件包是窗口层常驻 Python 包 `dungeon/window/component_pack/`，随 registry
正常导入，与 data_dir 重定向无关——autopilot **不需要**任何组件包注入（历史上曾把注册表
显式指向 `assets/components/`，该目录已随拆包移除）。组件包本身由无 GUI 守卫
`check_component_pack.py` 覆盖；本脚本的 `text-components` 场景只负责真窗口里的接管可见状态。

L2 之后注入点更干净：宿主能力（尺寸/DPI、显隐、事件泵、收尾弹框、回放文件选择）全部经
`dungeon.window.host.HostPort` 注入，**不用再打桩 `ui.ctk.dialogs`**——
`ScriptedHost`（无宿主）与 `RecordingTkHost`（真 Tk 度量 + 记录弹框）就够了。

## 2. 覆盖范围与边界（现状）

| 能自动化的 | 不能 / 不做 |
|---|---|
| 真窗口生命周期：构造 → `run()` → 步进 → 关闭 → 落盘 | **进程退出码**不能当验收条件（退出阶段可能挂死，是环境现象，见 [退出挂死调查](history/exit_hang_investigation.md)） |
| 收尾路径（`_finalize` 两条分支）、结果对象字段 | **视觉观感**（背景、字体 DPI、留白） |
| 宿主心跳（会话期间宿主不被冻结） | **真机手感**（F11、拖 resize、高 DPI 首图时机） |
| 回放切换（取消 / 选中两条分支） | **真实 AI 的输出质量** |
| 会话结束后无残留非 daemon 线程 | 真实鼠标输入与拖 resize（L3 的键盘一侧已由 `native-input` 覆盖）、视觉回归（L4，可选，优先级低） |

改造前为什么必须手点（构造即运行、崩溃只在真窗口复现、一次会话要真 AI、双宿主四条根因）
属于历史，见 [宿主改造档案](history/host_refactor.md)。

## 3. 分层方案与注入点索引

| 层 | 覆盖什么 | 状态 | 说明 |
|---|---|---|---|
| **L0 纯逻辑** | 收尾路径、原子写、schema、命名、分层 | 已有 | `tests/check_*.py`，无 GUI，进 CI 门禁 |
| **L1 无宿主自动驾驶** | 构造 → 运行 → 步进 → 关闭 → 落盘的完整生命周期 | **已落地** | `scripts/dungeon_autopilot.py`，`parent=None` 直起运行器（无宿主 `HostPort`） |
| **L2 Tk 宿主 + 自动驾驶** | 宿主在会话期间不被冻结 | **场景已落地**（`tk-host`） | 「进程是否退出」这一项**不能断言**，见 §5 |
| **L3 真实输入事件** | 真实按键（`SendMessage(WM_KEYDOWN)` → GLFW → imgui 输入队列）、F11、resize | **部分落地** | `native-input` 场景已覆盖键盘直投；鼠标/重排仍靠 `--check` 钩子与快照 |
| **L4 视觉回归** | 布局 / 背景 / 字体 | 可选、优先级低 | DPG 没有 headless 渲染，只能截屏比对或断言几何量 |

**L1 与 L0 的分工**：L0 保证逻辑对，L1 保证「真窗口跑起来也是对的」——只有真窗口才会走的
运行器起停（`hello_imgui.run()` 的 GL 上下文/窗口生命周期）、调度队列、后台线程与关闭路径的交互。

### 3.1 注入点清单（照抄即可）

| 目的 | 做法 | 依据 |
|---|---|---|
| 不要 Tk 主窗口 | `DungeonSessionWindow(parent=None, ...)` | `base.py` 里 `self.parent` 的用法全部有 None / hasattr 守卫 |
| 真 Tk 宿主 | `parent=tk.Tk()`（屏幕外 + 全透明，见 `scene_tk_host`） | `_pump_host_events` 只要求宿主有 `update()` |
| 假 AI（不联网） | 打桩 `dungeon.window.base.create_client`，实现 `generate_stream` / `generate` | `base.py::_init_session` 用它建 `self.ai_client` |
| 数据隔离 | 把 `paths.data_dir` 重定向到临时目录 | **必须在 import `dungeon.window.*` 之前**——那些模块用 `from paths import data_dir` 在导入时绑定函数对象 |
| 屏蔽对话框 | **注入宿主端口**：`DungeonSessionWindow(..., host=ScriptedHost())` | window 层只经 `HostPort` 弹框；真模态窗需要 Tk 根窗口，自检里点了会挂住 |
| 主线程操作 | 一律 `_frame.call(fn)`，后台线程绝不直接碰窗口/GL 状态 | 立即模式下窗口状态只在帧循环线程上变，[窗口文档](window.md) §4 |
| 关闭窗口 | `_close_loop()` → `_request_close()` → 帧边界落实（运行器收帧） | 业务代码不要自己停帧循环；`_request_close` 只置位 |
| 脚本化动作 | `click / enter / cancel / replay / check / close / native-close / native-key / sleep`，见 `AutopilotWindow._run_script` | 本文配套脚本 |
| 读窗口状态 | `win.ui_snapshot()`（值快照）或 `("check", fn)` 动作 | 立即模式没有 retained 控件树可内省 |

```python
import paths; paths.data_dir = lambda: tmp_data        # 先重定向，再导入窗口模块
import dungeon.window.base as wbase
wbase.create_client = lambda *a, **k: ScriptedAI()      # 脚本化 AI
from dungeon.window.host import HostPort                # 宿主端口：弹框收进 calls

class ScriptedHost(HostPort):
    def dialog(self, kind, title, message):
        self.calls.append((kind, title, message))

class Auto(DungeonSessionWindow):
    def _build_ui(self):
        super()._build_ui()
        threading.Thread(target=self._run_script, daemon=True).start()

Auto(..., host=ScriptedHost()).run()
```

## 4. 命令与判定方式

```bash
python scripts/dungeon_autopilot.py                              # 全部场景（每场景一个子进程）
python scripts/dungeon_autopilot.py --scene tk-host --isolate    # 单场景，子进程隔离
python scripts/dungeon_autopilot.py --scene session-close --repeat 2 --isolate  # 同进程连开两轮
python scripts/dungeon_autopilot.py --in-process                 # 当前进程内依次跑（快，但崩溃带走全部）
```

判定规则（默认**不要**把退出码当验收条件）：

1. 控制台出现 `[dungeon_autopilot] PASSED n/n` 即通过；脚本自身会用 `report:` 行给出明细报告路径。
2. 判定成败**看子进程打印的结论行，不看退出码**。结论行在子进程里打印后立即 flush（管道 stdout 是
   块缓冲，默认 flush 排在解释器退出收尾的最后——退出挂死时结论行会困在缓冲里，父进程就会把
   「已通过但退出挂死」误判成「超时未出结论」）。
3. 每个场景开**子进程**（单场景用 `--isolate`）。读输出在父进程的独立线程里做，主线程按 deadline
   轮询，所以超时是**硬超时**——子进程停止出字（如渲染占住 GIL）也吊不住父进程。读到结论行
   后等 `EXIT_GRACE`（5s）宽限，仍不退出才 `kill()`（正常子进程在结论行后约 0.1s 就退出，这是加
   宽限的原因）。
4. 断言、退出码、退出方式（自行退出 / 超时未出结论 / 结论已出但退出挂死）**分开汇报**，默认只有
   断言影响成败；`--require-clean-exit` 把「未自行干净退出」也判为失败（退出路径的环境复测用，
   见 §6 与 [宿主边界](window_host.md) §6）。
5. CI：GUI 冒烟层单独一个 job（需要显示器），不要并进无 GUI 的门禁。已落地为
   `.github/workflows/gui-smoke.yml`（每晚 + 手动：`xvfb-run` + 软件 GL 跑
   `dungeon_autopilot --scene all --isolate`、`smoke_mini`、`smoke_switch`，并带一个
   Linux 打包自检 job）。

### 4b. 长压测标准姿势：`--isolate` + 外层 watchdog

反复开关窗口（`hello_imgui.run()` 每次拆建 GLFW 窗口 + GL 上下文）会暴露 imgui/GLFW 的低概率
**原生**段错误（`SIGSEGV @ ImGui_ImplGlfw_WindowFocusCallback`，
Python 层 `try/except` 捕不到）。这类问题只能靠「一轮一个子进程 + 外层统计退出码」
抓：单轮跑在父进程里，一次崩溃就把整轮统计带走，永远得不到"20 轮里崩了几轮"。

标准做法是**逐轮起子进程、按退出码分类**，别把 `--in-process` 用于压测：

```bash
# 现成脚本（developer_tools/，本地探针目录，不入库）：
python developer_tools/_probe_stress.py smoke_switch 20                    # 默认：XInitThreads 生效
GIANTESS_X11=0 python developer_tools/_probe_stress.py smoke_switch 20     # 对照组
python developer_tools/_probe_stress.py autopilot 5 -- --scene all --isolate
```

它每轮打印 `rc` 与耗时，末尾汇总 `PASS / 非零退出 / 原生崩溃`——**负的 rc 就是信号**
（`-11` = SIGSEGV）。等价的手工版本：

```bash
for i in $(seq 1 20); do
  .venv/bin/python tests/smoke_switch.py >/dev/null 2>&1
  echo "round=$i rc=$?"        # 139 = 128+11 段错误
done
```

Linux 侧配合 `GIANTESS_X11=0` 做 XInitThreads 有效/无效对比（见
[docs/users/platforms/linux.md](../users/platforms/linux.md) §3、§6）。判定只看「跑完且没崩」，不看单轮断言——
断言已由单轮标准跑覆盖。

## 5. 坑清单

| 坑 | 现象 | 应对 |
|---|---|---|
| **退出阶段挂死（与 DPG 无关）** | 断言全部打印完毕、无残留非 daemon 线程，`sys.exit` 与 `os._exit` 都试过仍不退出（约 190MB 常驻）；卡点在 Python 之下，卡住后 `Stop-Process -Force` 也杀不掉 | 按 §4 的输出判定 + 父进程硬超时兜底；**不要**看退出码（复测退出路径用 `--require-clean-exit`）。根因与复测见 [退出挂死调查](history/exit_hang_investigation.md)——长时会话累积的环境现象，不建议为它做进程隔离 |
| **Python 层看门狗无效** | 渲染循环期间 `threading.Timer` 可能拿不到 GIL，实测 40s 定时器根本没触发 | 超时只能由父进程兜底 |
| **会话收尾别对旧宿主窗口动手** | DPG 时代 `destroy_context()` 之后对现场 Tk 根做窗口级命令（`destroy()` / `withdraw()`）会硬崩；imgui 侧 P0 已证 `run()` 之后销毁 Tk 根安全（见 `.workbuddy/imgui-migration/30-spike-lifecycle.md`） | 自检里 `scene_tk_host` 仍**不销毁**它创建的根窗口（留在 `_KEEP_ALIVE_ROOTS` 里）——不影响覆盖面，销毁时机等 P8 统一 |
| **自检里不要用真模态弹框** | L2 之后 `scene_tk_host` 用真 `TkHost`，收尾提示会弹真 CTk 模态框，没人点就一直等（实测 40s/90s 均超时） | 用 `RecordingTkHost(TkHost)`：尺寸/DPI/显隐/事件泵走真适配器，只把 `dialog()` 换成记录器 |
| **需要显示器** | 没有 headless 渲染，窗口会真的出现 | 这是它属于 GUI 冒烟层、不进无 GUI CI 门禁的原因 |
| **时序** | 固定 sleep 不稳 | 用轮询 / 条件等待；两次点击间隔 ≥ 0.4s（`_on_next_step` 节流）；窗口存活期间读状态要在 enqueue 的回调里读，构造返回后可以随便读 |
| **假 AI 必须遵守解析契约** | 否则只测到「生成异常」那条路径 | 遵守 `dungeon/response.py` 的 `{"text", "direction", "custom_directions"}` |
| **打桩的 `personality` 要补齐字段** | 缺 `sensitivity` / `normalized_strength` / `strategy_value` 时每一步 `_finish_step` 都抛异常，而故事**看上去还在正常出字** | 靠断言「回放记录是否增长」发现 |

## 6. 下一步建议（按性价比排序）

1. **场景扩充**：选项弹窗、插入 / 跳转触发器、结束章节结局、回放模式、AI 返回脏数据（截断 JSON、无 JSON、空文本）。
2. **真实响应录制**：把一次真实会话的 AI 响应存成 fixture，回归时回放——既真实又不花钱、可复现。
3. **压测**：`--scene session-close --repeat N --isolate` 连续开关窗口 N 次，抓竞态与关闭路径的偶发问题——**已落地标准姿势**（§4b，含逐轮退出码统计与 XInitThreads 对照组）。
4. **退出挂死**：已收敛为「长时会话累积的环境现象」，保留 `EXIT_GRACE` 兜底即可；真遇到先重启系统。
5. **换宿主的验证**：想验证「换框架也能跑」，最省事的是写一个 `HostPort` 子类（如 asyncio / Qt 节拍）驱动窗口，`check_dungeon_layering.py` 会保证 window 层不再偷偷拉回 Tk。
6. **CI**：GUI 冒烟层单独一个 job（需要显示器）——见 `.github/workflows/gui-smoke.yml`（每晚 xvfb + 软件 GL）。

## 7. 仍然需要人工的部分

- **视觉观感**：背景裁切 / 淡入淡出、字体与 DPI 缩放、文本区留白、Ken Burns 运动幅度。
- **真机交互手感**：F11 全屏、拖 resize 的重排效果、高 DPI 下的首图时机。
- **真实 AI 的输出质量**：自动化只能保证「跑得通、状态推进正确、收尾正确」，不能保证「故事好看」。

---

## 历史

本文件只描述**现状**（脚本怎么跑、坑怎么绕）。以下档案记录它是怎么来的：

| 档案                                           | 内容 |
|----------------------------------------------|---|
| [宿主改造档案](history/host_refactor.md)          | 改造前必须手点的四条根因、L1/L2 落地时间、自检场景的扩充过程 |
| [退出挂死调查](history/exit_hang_investigation.md) | §5 挂死坑的复现矩阵、修正结论与 `EXIT_GRACE` 宽限的由来 |
