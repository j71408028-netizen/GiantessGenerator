# 副本窗口（DearPyGui）调试自动化

> 配套：`docs/Dungeon/dungeon_window.md`（窗口生命周期与开发约束）、
> `docs/Dungeon/dungeon_architecture.md`（分层与自检脚本总览）。
> 本文回答一个问题：**调试副本窗口能不能不靠人手点电脑？** 结论与已落地的东西都在这里。

写于 2026-09-22。

---

## 0. 结论

**能，而且大部分已经能做到。** 已落地第一版：`scripts/dungeon_autopilot.py`
（GUI 冒烟自检：无人手点击、不联网、不碰真实 `data/`）。

实测（本项目环境，Windows / DPG 2.3.1 / Python 3.13）：

| 场景 | 覆盖 | 断言 | 耗时 |
|---|---|---|---|
| `session-close` | 挑战模式直进会话 → 步进 → 点 X 关闭 | 7 项 | ~6s |
| `entry-cancel` | 探索模式入口页 → 点「返回」 | 4 项 | ~5s |
| `entry-start` | 探索模式入口页 → 点「开始副本」→ 步进 → 关闭 | 5 项 | ~9s |

三个场景共 16 项断言全绿，父进程汇总 `PASSED 6/6`。

**仍然需要人工的只剩"看起来对不对"这一类**（背景观感、字体 DPI、留白、真机手感），
以及真实 AI 的输出质量。其余——尤其是 §5.1 那种"关窗后进程死没死"的回归——
都可以脚本化。

---

## 1. 为什么现在必须手点：四个根因

1. **窗口生命周期就是构造函数。** `DungeonSessionWindow.__init__` 同步阻塞到窗口关闭
   （`dungeon_window.md` §2），代码里没有"跑一步"的入口，唯一的推进方式是鼠标/空格，
   脚本无从下手。
2. **崩溃只在真窗口复现。** §5.1 的堆损坏没有 Python traceback，最小 DPG 脚本复现不了
   Tk 宿主 + 完整线程栈的组合行为，于是只能人手点"进入副本 → 返回 → 再进入副本"。
3. **一次会话要真 AI。** 网络 + 计费 + 数十秒，迭代一次的成本太高，且不可复现。
4. **双宿主。** 验证"关闭后进程存活"必须回到 Tk 主循环，脚本里没有宿主可回。

这四条都能绕过：第 1、3、4 条靠"替身 + 注入"（见 §3），第 2 条靠
"一个最小 Tk 宿主 + 子进程退出码"（见 §2 的 L2）。

---

## 2. 分层方案

| 层 | 覆盖什么 | 状态 | 说明 |
|---|---|---|---|
| **L0 纯逻辑** | 收尾路径、原子写、schema、命名、分层 | 已有 | `scripts/check_*.py`，无 GUI，进 CI 门禁 |
| **L1 无宿主自动驾驶** | 构造 → 步进 → 关闭 → 落盘的完整生命周期 | **已落地** | `scripts/dungeon_autopilot.py`，`parent=None` 直起 DPG |
| **L2 Tk 宿主 + 自动驾驶** | §5.1 崩溃回归：关窗后进程是否还活着 | 建议下一步 | 一个几十行的 Tk 根窗宿主 + 子进程退出码，替代人手点两遍 |
| **L3 真实输入事件** | 鼠标 handler、键盘、F11、resize | 可选 | pywinauto / Win32 `SendInput`；多数情况可用 enqueue 同一回调替代 |
| **L4 视觉回归** | 布局/背景/字体 | 可选、优先级低 | DPG 没有 headless 渲染，只能截屏比对或断言几何量 |

**L1 与 L0 的分工**：L0 保证逻辑对，L1 保证"真窗口跑起来也是对的"——
只有真窗口才会走的 `create_context / start_dearpygui / destroy_context`、
`_dispatch` 帧回调链、后台线程与关闭路径的交互。

---

## 3. 已验证的注入点（照抄即可）

| 目的 | 做法 | 依据 |
|---|---|---|
| 不要 Tk 主窗口 | `DungeonSessionWindow(parent=None, ...)` | `base.py` 里 `self.parent` 的用法全部有 None/hasattr 守卫（185 / 197 / 470 行） |
| 假 AI（不联网） | 打桩 `dungeon.window.base.create_client`，实现 `generate_stream` / `generate` | `base.py:329` 用它建 `self.ai_client` |
| 数据隔离 | 把 `paths.data_dir` 重定向到临时目录 | **必须在 import `dungeon.window.*` 之前**——那些模块用 `from paths import data_dir` 在导入时绑定函数对象 |
| 屏蔽对话框 | 打桩 `ui.common.dialogs.showinfo/showerror/askyesno` | 真实对话框是 CTk 模态窗，需要 Tk 根窗口 |
| 主线程操作 | 一律 `_dispatch.enqueue(fn)`，后台线程绝不直接调 DPG | `dungeon_window.md` §3 |
| 关闭窗口 | `_close_loop()`（WM_CLOSE 路径） | §5.1：**不要** `dpg.stop_dearpygui()` |
| 脚本化动作 | `click / enter / cancel / close / sleep`，见 `AutopilotWindow._run_script` | 本文配套脚本 |

示例（省略参数）：

```python
import paths; paths.data_dir = lambda: tmp_data        # 先重定向，再导入窗口模块
import dungeon.window.base as wbase
wbase.create_client = lambda *a, **k: ScriptedAI()      # 脚本化 AI
import ui.common.dialogs as d; d.showinfo = lambda *a, **k: None

class Auto(DungeonSessionWindow):
    def _build_ui(self):
        super()._build_ui()
        threading.Thread(target=self._run_script, daemon=True).start()
```

---

## 4. 实测发现的两个坑（写在这里免得再踩）

### 4.1 跑完会话后，进程会在退出阶段挂死

现象：场景断言全部打印完毕、无残留非 daemon 线程、`sys.exit` 与 `os._exit` 都试过，
进程依然不退出（约 190MB 常驻），疑似 DPG 上下文销毁后 native 收尾阶段卡住。

应对（已写进脚本）：

- 判定成败**看子进程打印的结论行，不看退出码**；
- 每个场景开**子进程**，父进程读到结论行后 `kill()`，超时即按失败计；
- **Python 层的看门狗无效**：DPG 循环期间 `threading.Timer` 可能拿不到 GIL，
  实测 40s 定时器根本没触发。超时只能由外层（父进程）兜底。

是否与 §5.1 同源、真实应用里是否表现为"退出整个程序后进程不退"，**有待单独排查**。

### 4.2 不要 `minimize_viewport()`

最小化后 DPG 可能不再跑帧回调，`_dispatch` 队列里的动作（包括关闭）永远执行不到，
窗口会卡在屏幕上。脚本里该开关默认关闭。

### 4.3 其它

- 需要显示器：DPG 没有 headless 渲染，窗口会真的出现（这也是它属于 GUI 冒烟层、
  不进无 GUI CI 门禁的原因）。
- 时序用**轮询/条件等待**，别用固定 sleep；两次点击间隔 ≥ 0.4s（`_on_next_step` 节流）。
- 窗口存活期间读状态要在 enqueue 的回调里读；构造返回后可以随便读。
- 假 AI 必须遵守 `dungeon/response.py` 的解析契约 `{"text","direction","custom_directions"}`，
  否则只测到"生成异常"那条路径。
- 打桩的 `personality` 要补齐 `sensitivity` / `normalized_strength` / `strategy_value`。
  缺字段时每一步 `_finish_step` 都抛异常，而故事**看上去还在正常出字**——和 §5.7
  一样隐蔽，只能靠断言"回放记录是否增长"发现。

---

## 5. 下一步建议（按性价比排序）

1. **L2：Tk 宿主 + 自动驾驶**（替代 §5.1 手测）。最小 Tk 根窗 → 构造窗口 →
   点「返回」→ 回到 `mainloop` → 断言进程存活/退出码为 0；再做"连续开关 N 次"压测。
   这条把目前最贵的手工回归（人手点两遍 + 看事件查看器）变成一条命令。
2. **场景扩充**：选项弹窗、插入/跳转触发器、结束章节结局、回放模式、
   AI 返回脏数据（截断 JSON、无 JSON、空文本）。
3. **真实响应录制**：把一次真实会话的 AI 响应存成 fixture，回归时回放，
   既真实又不花钱、可复现。
4. **`--repeat N` 压力回归**：连续开关窗口 N 次，抓竞态与关闭路径的偶发问题。
5. CI：GUI 冒烟层单独一个 job（需要显示器），不要并进无 GUI 的门禁。

---

## 6. 仍然需要人工的部分

- **视觉观感**：背景裁切/淡入淡出、字体与 DPI 缩放、文本区留白、Ken Burns 运动幅度。
- **真机交互手感**：F11 全屏、拖 resize 的重排效果、高 DPI 下的首图时机。
- **真实 AI 的输出质量**：自动化只能保证"跑得通、状态推进正确、收尾正确"，
  不能保证"故事好看"。

---

## 附：命令

```bash
python scripts/dungeon_autopilot.py                  # 全部场景（每场景一个子进程）
python scripts/dungeon_autopilot.py --scene entry-cancel
python scripts/dungeon_autopilot.py --repeat 10      # 连续开关窗口 10 次
python scripts/dungeon_autopilot.py --in-process     # 当前进程内依次跑（快，但崩溃带走全部）
```
