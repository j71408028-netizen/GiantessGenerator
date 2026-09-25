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

基线实测（本机 Windows / DPG 2.3.1 / Python 3.13）：

| 场景 | 覆盖 | 断言 | 耗时 |
|---|---|---|---|
| `session-close` | 挑战模式直进会话 → 步进 → 关闭（含帧时钟 / 像素工作者收工、结果对象字段） | 16 项 | ~7s |
| `entry-cancel` | 探索模式入口页 → 点「返回」 | 7 项 | ~5s |
| `entry-start` | 探索模式入口页 → 点「开始副本」→ 步进 → 关闭 | 8 项 | ~8s |
| `entry-replay` | 入口页「加载回放」→ 取消一次 → 再选文件 → **同一窗口内**切回放（L4 回归） | 10 项 | ~7s |
| `tk-host` | 宿主换成真 Tk 根窗口（`TkHost` 适配器）：会话期间宿主心跳照跑（L0 回归） | 7 项 | ~6s |
| `native-close` | 视口通过原生关闭键（X / `WM_CLOSE`）关闭：队列不得残留 `WM_QUIT`、收尾提示与宿主计时器不卡死（§5-C12） | 7 项 | ~7s |
| `text-components` | 文本组件家族（`text_card` / `text_nvl`）接管显示的会话中途可见状态 + `text_component` 字段三选一与旧写法提升 + 覆盖层服务面（调出/阅读模态挂起/关闭恢复/兄弟组件只读访问）；`("check", fn)` 脚本动作在 DPG 存活时断言 | 9 项 | ~20s |

七个场景共 **63 项断言全绿**。每个场景结束还会断言 **会话内无残留非 daemon 线程**
（`threading.enumerate()` 为空）——这是 L3 帧时钟 + `PixelWorker` 的验收条件。

注意：组件包定位随 data_dir 重定向会落空，autopilot 在隔离头部把注册表显式指到
仓库内 `assets/components/`（组件包在 assets，本就不随 data_dir 重定向；见脚本开头 `_component_registry` 注入）。

L2 之后注入点更干净：宿主能力（尺寸/DPI、显隐、事件泵、收尾弹框、回放文件选择）全部经
`dungeon.window.host.HostPort` 注入，**不用再打桩 `ui.common.dialogs`**——
`ScriptedHost`（无宿主）与 `RecordingTkHost`（真 Tk 度量 + 记录弹框）就够了。

## 2. 覆盖范围与边界（现状）

| 能自动化的 | 不能 / 不做 |
|---|---|
| 真窗口生命周期：构造 → `run()` → 步进 → 关闭 → 落盘 | **进程退出码**不能当验收条件（退出阶段可能挂死，是环境现象，见 [退出挂死调查](history/exit_hang_investigation.md)） |
| 收尾路径（`_finalize` 两条分支）、结果对象字段 | **视觉观感**（背景、字体 DPI、留白） |
| 宿主心跳（会话期间宿主不被冻结） | **真机手感**（F11、拖 resize、高 DPI 首图时机） |
| 回放切换（取消 / 选中两条分支） | **真实 AI 的输出质量** |
| 会话结束后无残留非 daemon 线程 | 真实输入事件（L3，可选）、视觉回归（L4，可选，优先级低） |

改造前为什么必须手点（构造即运行、崩溃只在真窗口复现、一次会话要真 AI、双宿主四条根因）
属于历史，见 [宿主改造档案](history/host_refactor.md)。

## 3. 分层方案与注入点索引

| 层 | 覆盖什么 | 状态 | 说明 |
|---|---|---|---|
| **L0 纯逻辑** | 收尾路径、原子写、schema、命名、分层 | 已有 | `scripts/check_*.py`，无 GUI，进 CI 门禁 |
| **L1 无宿主自动驾驶** | 构造 → 运行 → 步进 → 关闭 → 落盘的完整生命周期 | **已落地** | `scripts/dungeon_autopilot.py`，`parent=None` 直起 DPG |
| **L2 Tk 宿主 + 自动驾驶** | 宿主在会话期间不被冻结 | **场景已落地**（`tk-host`） | 「进程是否退出」这一项**不能断言**，见 §5 |
| **L3 真实输入事件** | 鼠标 handler、键盘、F11、resize | 可选 | pywinauto / Win32 `SendInput`；多数可用 enqueue 同一回调替代 |
| **L4 视觉回归** | 布局 / 背景 / 字体 | 可选、优先级低 | DPG 没有 headless 渲染，只能截屏比对或断言几何量 |

**L1 与 L0 的分工**：L0 保证逻辑对，L1 保证「真窗口跑起来也是对的」——只有真窗口才会走的
`create_context / 帧循环 / destroy_context`、调度队列、后台线程与关闭路径的交互。

### 3.1 注入点清单（照抄即可）

| 目的 | 做法 | 依据 |
|---|---|---|
| 不要 Tk 主窗口 | `DungeonSessionWindow(parent=None, ...)` | `base.py` 里 `self.parent` 的用法全部有 None / hasattr 守卫 |
| 真 Tk 宿主 | `parent=tk.Tk()`（屏幕外 + 全透明，见 `scene_tk_host`） | `_pump_host_events` 只要求宿主有 `update()` |
| 假 AI（不联网） | 打桩 `dungeon.window.base.create_client`，实现 `generate_stream` / `generate` | `base.py::_init_session` 用它建 `self.ai_client` |
| 数据隔离 | 把 `paths.data_dir` 重定向到临时目录 | **必须在 import `dungeon.window.*` 之前**——那些模块用 `from paths import data_dir` 在导入时绑定函数对象 |
| 屏蔽对话框 | **注入宿主端口**：`DungeonSessionWindow(..., host=ScriptedHost())` | window 层只经 `HostPort` 弹框；真模态窗需要 Tk 根窗口，自检里点了会挂住 |
| 主线程操作 | 一律 `_frame.call(fn)`，后台线程绝不直接调 DPG | [窗口文档](window.md) §4 |
| 关闭窗口 | `_close_loop()` → `_request_close()` → `dpg.stop_dearpygui()` | 帧循环下一轮检测到 not running 后收尾；业务代码不要自己调 stop |
| 脚本化动作 | `click / enter / cancel / close / sleep`，见 `AutopilotWindow._run_script` | 本文配套脚本 |

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

判定规则（**不要**把退出码当验收条件）：

1. 控制台出现 `[dungeon_autopilot] PASSED n/n` 即通过；脚本自身会用 `report:` 行给出明细报告路径。
2. 判定成败**看子进程打印的结论行，不看退出码**。
3. 每个场景开**子进程**（单场景用 `--isolate`），父进程读到结论行后等 `EXIT_GRACE`（5s）宽限，
   仍不退出才 `kill()` 并按挂死记录（正常子进程在结论行后约 0.1s 就退出，这是加宽限的原因）。
4. CI：GUI 冒烟层单独一个 job（需要显示器），不要并进无 GUI 的门禁。

## 5. 坑清单

| 坑 | 现象 | 应对 |
|---|---|---|
| **退出阶段挂死（与 DPG 无关）** | 断言全部打印完毕、无残留非 daemon 线程，`sys.exit` 与 `os._exit` 都试过仍不退出（约 190MB 常驻）；卡点在 Python 之下，卡住后 `Stop-Process -Force` 也杀不掉 | 按 §4 的输出判定 + 父进程宽限兜底；**不要**看退出码。根因与复测见 [退出挂死调查](history/exit_hang_investigation.md)——长时会话累积的环境现象，不建议为它做进程隔离 |
| **Python 层看门狗无效** | DPG 循环期间 `threading.Timer` 可能拿不到 GIL，实测 40s 定时器根本没触发 | 超时只能由父进程兜底 |
| **会话结束后不要再碰 Tk** | `destroy_context()` 之后再调 Tk API 会 0xC0000005 | `scene_tk_host` 故意**不销毁**它创建的根窗口（留在 `_KEEP_ALIVE_ROOTS` 里） |
| **自检里不要用真模态弹框** | L2 之后 `scene_tk_host` 用真 `TkHost`，收尾提示会弹真 CTk 模态框，没人点就一直等（实测 40s/90s 均超时） | 用 `RecordingTkHost(TkHost)`：尺寸/DPI/显隐/事件泵走真适配器，只把 `dialog()` 换成记录器 |
| **不要 `minimize_viewport()`** | 最小化后不再有新帧到达，帧时钟上排队的任务（含关闭）永远执行不到，窗口卡在屏幕上 | 脚本里该开关默认关闭 |
| **需要显示器** | DPG 没有 headless 渲染，窗口会真的出现 | 这是它属于 GUI 冒烟层、不进无 GUI CI 门禁的原因 |
| **时序** | 固定 sleep 不稳 | 用轮询 / 条件等待；两次点击间隔 ≥ 0.4s（`_on_next_step` 节流）；窗口存活期间读状态要在 enqueue 的回调里读，构造返回后可以随便读 |
| **假 AI 必须遵守解析契约** | 否则只测到「生成异常」那条路径 | 遵守 `dungeon/response.py` 的 `{"text", "direction", "custom_directions"}` |
| **打桩的 `personality` 要补齐字段** | 缺 `sensitivity` / `normalized_strength` / `strategy_value` 时每一步 `_finish_step` 都抛异常，而故事**看上去还在正常出字** | 靠断言「回放记录是否增长」发现 |

## 6. 下一步建议（按性价比排序）

1. **场景扩充**：选项弹窗、插入 / 跳转触发器、结束章节结局、回放模式、AI 返回脏数据（截断 JSON、无 JSON、空文本）。
2. **真实响应录制**：把一次真实会话的 AI 响应存成 fixture，回归时回放——既真实又不花钱、可复现。
3. **压测**：`--scene session-close --repeat N --isolate` 连续开关窗口 N 次，抓竞态与关闭路径的偶发问题（已支持，尚未长期跑）。
4. **退出挂死**：已收敛为「长时会话累积的环境现象」，保留 `EXIT_GRACE` 兜底即可；真遇到先重启系统。
5. **换宿主的验证**：想验证「换框架也能跑」，最省事的是写一个 `HostPort` 子类（如 asyncio / Qt 节拍）驱动窗口，`check_dungeon_layering.py` 会保证 window 层不再偷偷拉回 Tk。
6. **CI**：GUI 冒烟层单独一个 job（需要显示器）。

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
