# Linux 兼容计划

> 依据：`docs/linux_verification_log.md`（Linux 试跑检查记录，随本计划一起入库）。
> 目标：把 Linux（X11 / XWayland）从"能跑"推进为**受支持平台**——修复入 git、遗留问题收口、打包与 CI 补位、限制明确文档化。Wayland 原生会话为已知限制，不在本期范围。

## 0. 现状基线（2026-10-06）

- 试跑环境：Ubuntu 26.04 / Python 3.14.4 / Tk 8.6 / X11（Wayland 会话 + XWayland）。
- 离线守卫 `tests/run_checks.py`：13/13 通过；自动驾驶 30/30；`smoke_mini` / `smoke_switch` 全部通过。
- 已验证的修复共 **9 个代码/配置文件**，目前只存在于桌面回传副本（`GiantessGenerator-LinuxInspection`），**未入 git**。
- 遗留问题 4 项（DPG 段错误、方案校验 warning、依赖缺口、置顶不可用），见 §2。
- 代码层现状盘点：
  - `paths.py` 已按 XDG_DATA_HOME 处理 Linux 打包态数据目录 ✅
  - `ui/common/fonts.py` 已有 linux 字体候选链（Noto Sans CJK / 文泉驿）✅
  - `build/` 只有 `windows/`、`macos/`，**无 Linux 打包脚本** ❌
  - 两个打包脚本均引用 `--collect-all zai`，而项目并无该依赖 ❌

## 1. 阶段一（P0）：回灌已验证修复

### 1.1 回灌方式与注意事项

- **不要整目录拷回**。回传副本与仓库的逐文件对比显示：除下述 9 个文件外，其余全部"差异"（`.gitignore`、`LICENSE`、约 30 个 py/json）仅为回传过程产生的换行符变化（CRLF↔LF），整目录覆盖会制造大量噪音 diff。
- 建议在 Windows 仓库里按改动清单**手工重做每处修改**（每处都是几行的小改动），改完用 `diff -u --strip-trailing-cr` 对副本逐文件核对一次，确认语义一致即可。
- 顺带确认：`tests/check_entrypoints.py` 等扫描器对 CRLF 是否无感（当前无感，保持现状，不引入 .gitattributes 强转，避免全仓重写）。

### 1.2 改动清单（按建议提交分组）

| # | 提交建议 | 文件 | 改动内容 | 对应验证 |
|---|---------|------|---------|---------|
| 1 | `fix(audio): 非 Windows 音频后端元组缺逗号` | `dungeon/audio/__init__.py:382` | `_TRACK_BACKENDS = (MiniaudioTrack)` → `(MiniaudioTrack,)`，修复非 Windows 下 `open_track()` 遍历 `TypeError` | `check_scenario_schema.py` 140/140 |
| 2 | `fix(tests): check_entrypoints 排除本地 .venv` | `tests/check_entrypoints.py` | `EXCLUDED_DIRS` 增加 `.venv`，消除虚拟环境 1410 处误报 | `run_checks.py` 回到 13/13 |
| 3 | `fix(data): 默认方案背景图路径改用正斜杠` | `data/packs/scenarios/_default/config.json` | `images\\screenshot-...jpg` → `images/screenshot-...jpg`（两端通用） | `check_scenarios.py` 该 warning 消失 |
| 4 | `feat(window): Linux/X11 安装宽松 X11 错误处理器` | `dungeon/window/dpg_state.py`（新增 `install_x11_error_guard()` + `park_context()` 挂载点）、`dungeon/window/ui.py`（`show_viewport()` 后挂载）、`ui/common/tk_host.py`（`show_window()` / `pump_events()` 前挂载，且仅 `was_created()` 后启用） | Tk 与 DPG/GLFW 同进程共用 X server 时，Tk 事件泵可能处理到已销毁窗口的旧事件，Xlib 默认处理器直接 `exit(1)`；改为 ctypes 安装宽松处理器并记入 `process_log` | `smoke_switch.py` 由硬崩变为 50/50 |
| 5 | `test: 平台不适用冒烟按 SKIP 处理` | `scripts/dungeon_autopilot.py`（`callback-thread` 场景非 Windows SKIP）、`tests/smoke_mini.py`（Linux 下置顶只断言调用不抛异常） | 平台差异不再记成失败 | 自动驾驶 30/30 |
| 6 | **需决策**：`requirements.txt` Pillow 约束 | 桌面副本把 `Pillow>=10.0,<13.0` 放宽为 `Pillow>=10.0`（日志未提及动机，推测为 Python 3.14 下装包需要）。**建议保留上界**，另按 §2.3 单独处理；若确为 3.14 必需，提交时补说明 | — |

### 1.3 阶段一验收

- [ ] Windows 仓库上 6 组提交完成，工作区干净；
- [ ] Windows 下 `tests/run_checks.py` 13/13（确认 #1/#4/#5 未破坏 Windows 路径，`install_x11_error_guard()` 在非 Linux 直接返回 False）；
- [ ] `smoke_switch.py` / `smoke_mini.py` 在 Windows 仍全通过；
- [ ] `git diff --stat` 中除上述文件外无其它变更（换行符零噪音）。

## 2. 阶段二（P1/P2）：遗留问题收口

> **状态（2026-10-07）**：§2.2 / §2.3 / §2.4 已全部落地；§2.1 的 XInitThreads、错误处理器
> 收窄与压测姿势已落地，段错误压测数据见 §5；向 Dear PyGui 提 issue 属长期跟进项。逐项执行
> 记录见文末 §6。

### 2.1 DPG/GLFW 低概率原生段错误（日志 §4.1，P1）

现象：反复 `destroy_context → create_context → show_viewport` 后偶发 `SIGSEGV @ ImGui_ImplGlfw_WindowFocusCallback`，Python 层不可捕获。

计划动作：
1. **XInitThreads 实验**（值得优先尝试的根因候选）：Tk 与 GLFW 未声明多线程访问同一 X display，是 BadWindow / 段错误的经典成因。在 Linux 启动最早处（`main.py` / `dpg_state` 首次 import 前）用 ctypes 调 `XInitThreads()`，连续压测 `smoke_switch.py` ×20 对比段错误率。若有效，它可能比宽松错误处理器更对症，两者可共存。
2. **错误处理器收窄**：当前 guard 忽略一切 X 错误。改为在处理器内用 `XGetErrorData`（或至少记录 `error->error_code`）解析错误码，仅对 `BadWindow(3)` / `BadDrawable(9)` 返回 0，其余走默认处理，避免掩盖真实问题。
3. **压测守护**：`--isolate` + 外部 watchdog 作为长压测标准姿势写进 `docs/Dungeon/window_automation.md`；段错误作为第三方库风险（dearpygui 2.3.1 / GLFW）记录在本文 §5。
4. 若上游可复现最小样例，向 Dear PyGui 提 issue（低优先级，附压测方法）。

### 2.2 默认方案校验 warning / info（日志 §4.2，P2）

- `新选项` 触发器前置条件 `新插入` 不存在 → **内容修复**：要么补上该条件定义，要么改触发器前置，跑 `check_scenarios.py` 清零。
- "没有结局路径" info → 内容决策：默认方案是否需要结局路径；不需要则在方案校验器里给"无结局"一个合法豁免标记，而不是让 info 永远挂着。
- "语音效果需要离线解码器" info → 环境问题非代码问题：Linux 文档写明 `pip install miniaudio` 即消除；不为此改代码。

### 2.3 依赖与环境（日志 §4.3，P1）

逐项澄清与处理：
1. **`networkx` / `graphviz` / `openai`**：`requirements.txt` 其实已声明，试跑环境 `.venv` 未装全是环境问题。处理：
   - Linux 文档明确"开发环境必须 `pip install -r requirements.txt`"；
   - 体验加固：`ui/scenario/dependency_dlg.py` 对 `networkx` import 失败改为友好提示（弹框告知功能不可用 + 安装命令），而不是直接崩。
2. **`numpy` 未声明但被直接 import**（`dungeon/window/background.py`、`services/challenges/service.py`、`dungeon/audio/voice_fx.py`）→ 加入 `requirements.txt`（非可选：三个模块都是主链路）。
3. **打包脚本 `--collect-all zai`**：项目无此依赖，从 `build/windows/build_windows.ps1` 与 `build/macos/build_macos.sh` 移除（或查明它是残留的历史 SDK 引用）。
4. **可选依赖声明核查**：`edge-tts` / `miniaudio` / `pywebview` 已按可选列出，保持；在 README 的安装段写清可选缺失的降级表现（与 requirements.txt 内注释一致）。

### 2.4 Linux/X11 置顶不可用（日志 §4.4，P2）

现状：Tk `-topmost` 在 GNOME/X11 写不进 `_NET_WM_STATE_ABOVE`，`ui/mini/app.py:106` 的置顶设置在 Linux 实际无效（静默）。

计划动作（按序尝试，取第一个有效的）：
1. **EWMH 直接写状态**：置顶设置处，Linux 下用 ctypes/`python-xlib` 或轻量 `pywinctl` 直接操作 `_NET_WM_STATE_ABOVE`，成功则回读确认；失败静默回退。依赖策略：能用 stdlib ctypes 就不加依赖。
2. 若实现成本高：**UI 降级**——Linux 检测到置顶写不进时，在挂件设置界面把该开关置灰并提示"当前桌面环境不支持置顶"，避免静默失效。
3. README 已知限制表记录：GNOME 下置顶无效属桌面环境行为（部分 WM 如 KWin 支持该属性，需实测补充支持矩阵）。

## 3. 阶段三（P1/P2）：打包、CI 与文档固化

### 3.1 Linux 打包（P1）

- 新增 `build/linux/build_linux.sh`：PyInstaller onedir，对齐 windows/macos 脚本；**不带** `--collect-all zai`；确认 Tk/DPG/GLFW 隐含依赖被收集。
- 验证 `paths.py` 打包态路径：XDG_DATA_HOME 下 `data/` 建目录、读写正常（源码态用仓库 `data/` 的逻辑不变）。
- 产物形态先做 tar.gz 目录包即可；AppImage / deb 不在本期，记入后续可选。

### 3.2 CI（P2）

分两档，避免 GUI 冒烟拖垮 CI：
- **每 PR**：Ubuntu job 跑 `tests/run_checks.py`（纯离线，13 项）+ `check_scenarios.py`；Windows job 跑同套件防回归。
- **每晚**：`xvfb-run` + 软件 GL（llvmpipe）跑 `smoke_mini.py` / `smoke_switch.py` / `dungeon_autopilot.py`。注意 GLFW 需要可用的 GL 上下文，llvmpipe 可行但慢，timeout 放宽；若 flaky 明显，先只进 autopilot 离线场景。

### 3.3 文档（P1）

- README：支持平台矩阵（Windows / macOS / Linux-X11 / Wayland×），安装差异（`pip install -r requirements.txt` 必装、可选依赖表现）。
- 新增 `docs/linux.md`（或并入 README 已知限制节）：X11 guard 机制、置顶限制、`callback-thread` 场景 SKIP、Wayland 走 XWayland 的说明。
- `docs/linux_verification_log.md` 与本计划一并入库，作为试跑证据链。

## 4. 总体验收标准

- [x] §1.3 阶段一验收全过（6 组提交已入库，工作区干净；Windows 路径未破坏——`install_x11_error_guard()` 在非 Linux 直接返回 False）；
- [ ] Linux 实机：`run_checks.py` 13/13、autopilot 30/30、`smoke_mini` / `smoke_switch` 全通过（Python 3.12 / 3.14 各跑一轮，确认版本宽容度）——**3.14 已过（13/13、smoke_mini 全通过；autopilot 29/30，1 项为环境劣化导致的退出挂死；`smoke_switch` 会话初期 50/50，长压测后挂死，见 §5.1）**；3.12 那轮待补；
- [x] 默认方案 `check_scenarios.py` 0 error 0 warning（info 只剩可选依赖类）；
- [ ] Linux PyInstaller 包能启动、创建/进入副本、跑通一个小游戏触发器（阶段三）；
- [x] 段错误压测：§5.1 已跑「XInitThreads 生效 / 关闭」各 6 轮并记录（两组均 0 段错误，结论与局限已写明）。

## 5. 风险登记

| 风险 | 等级 | 状态 |
|------|------|------|
| Dear PyGui 2.3.1 / GLFW 反复拆建上下文的回调状态竞争（SIGSEGV） | 高 | 第三方库风险，靠 §2.1 缓解；长期跟随上游。收窄后的错误处理器已能把它与 X 错误区分开（未预期错误码会大声记录） |
| 宽松 X11 错误处理器可能掩盖真实 X 错误 | 中 | ✅ 已关闭：只对 `BadWindow(3)` / `BadDrawable(9)` 记日志返回，其余错误码每次大声记录（`dpg_state._EXPECTED_X11_ERRORS`） |
| GNOME 置顶等桌面环境差异无法穷举（KDE/Wayland 各发行版） | 中 | ✅ 已收口：EWMH 直接写 + 回读确认，不支持时设置页置灰。支持矩阵只承诺 X11+GNOME 实测项，其余按「尽力而为」标注（docs/linux.md §4） |
| Python 3.14 较新，部分依赖（Pillow 上界、PyInstaller 钩子）兼容面窄 | 中 | §1.2-#6 决策 + 打包验证时确认（阶段三） |
| **长时间连续 GUI 压测后环境劣化**：单轮耗时由 ~45s 涨到 120–170s，收尾路径挂死概率上升（`smoke_switch` 已过第 1 轮、卡在第 2 轮「跑一局副本」；`autopilot` 偶发单场景退出挂死）。重启会话/长时间静置后恢复 | 中 | 与 §5 的「退出挂死」同源，属长时会话累积的环境现象**而非本轮改动**（`GIANTESS_X11=0` 对照同样挂死；单跑 `--scene session-close`、`--repeat 3`、`smoke_mini` 均通过）。压测一律逐轮子进程 + 硬超时，结论按"有没有段错误"读，不把挂死记到功能头上 |

### 5.1 段错误压测数据（2026-10-07）

方法：`developer_tools/_probe_stress_matrix.sh`（逐轮子进程 + 单轮超时 + 逐轮还原
`data/user/settings.json`），两组顺序跑，每组 6 轮 `smoke_switch.py`：

| 组 | 配置 | PASS | 断言失败 | 原生崩溃（段错误） | 挂死 |
|---|---|---|---|---|---|
| A | XInitThreads 生效（`main.py` 引导，默认） | 3/6 | 0 | **0** | 3 |
| B | `GIANTESS_X11=0`（不调 XInitThreads、不写 EWMH） | 0/6 | 0 | **0** | 6 |

读法（必须说清楚的局限）：

- **两组都没有段错误**——§4 日志里那个偶发 SIGSEGV 本轮没有复现，因此「XInitThreads
  是否消除段错误」这轮**得不出结论**；上表的差异只在挂死率（B 组更差），样本太小、
  且 B 组跑在后面（环境已经更劣化），不能当成因果。
- 能确认的是：XInitThreads 真的被调到了（`boot_x11()` 返回 True、`probe_static`
  验证），EWMH 置顶路径在 GNOME 实测可用，收窄后的错误处理器在真实 BadWindow 下
  既不退出进程、也留下了过程日志。
- 挂死点固定在第 2 轮「切换到挂件模式后真实跑一局副本」的挂件构建段；它同时出现在
  A 组、B 组与 `GIANTESS_X11=0` 的单跑复现里，与 X11 引导开关无关。
- 想拿到更有意义的对比，需要在**重新启动的干净会话**里跑，且两组交替进行（否则
  第二组必然跑在更劣化的环境里）。这条留给阶段三 CI（xvfb + llvmpipe）来做。

## 6. 阶段二执行记录（2026-10-07）

| 计划项 | 落地内容 | 验证 |
|---|---|---|
| §2.1-1 XInitThreads 实验 | 新增 `ui/common/x11.py`（`init_x_threads`）+ `ui/common/x11_boot.py`；`main.py` / `smoke_switch` / `smoke_mini` / `dungeon_autopilot` / `TkHost.__init__` 五处接入；`GIANTESS_X11=0` 作对比开关 | 压测见 §5.1；`developer_tools/_probe_xinit.py` 确认 `XInitThreads()` 真实调用成功 |
| §2.1-2 错误处理器收窄 | `dpg_state.install_x11_error_guard()`：ctypes `XErrorEvent` 解析 `error_code`，只对 `BadWindow(3)`/`BadDrawable(9)` 静默记日志，其余错误码每次大声记录 | 注入真实 BadWindow 与伪造 `BadMatch(8)` 各验一次，进程存活且日志正确 |
| §2.1-3 压测守护 | `docs/Dungeon/window_automation.md` 新增 §4b（逐轮子进程 + 硬超时 + 退出码分类 + `GIANTESS_X11` 对照组）；`developer_tools/_probe_stress.py` / `_probe_stress_matrix.sh` | 本文件 §5.1 |
| §2.1-4 上游 issue | 未做（低优先级，需要最小复现样例；压测方法已写进 §4b，随时可提） | — |
| §2.2-1 悬空前置 | `_default/config.json`：`新选项` 触发器移除孤立的 `precondition_names: ["新插入"]` | `check_scenarios.py` 警告清零 |
| §2.2-2 无结局 info | 新增顶层字段 `ending_policy`（`required` 默认 / `open`）+ 归一化 + 非法值 warning；默认方案声明 `open`；校验器 `open` 时不提示 | `check_scenario_schema.py` 144/144（新增 5 项断言）；`check_scenarios.py` info 只剩可选依赖类 |
| §2.2-3 解码器 info | 不改代码；`docs/linux.md` §2 写明 `pip install miniaudio` 即消除 | 文档 |
| §2.3-1 缺依赖体验 | `dependency_dlg.py` 可选 import + `missing_dependencies()` + 对话框内安装提示；`chapter_trigger_mgr.check_dependencies` 先提示再返回（不再 import 崩） | 本机（无 networkx/graphviz）实测提示文案正确 |
| §2.3-2 numpy | `requirements.txt` 增加 `numpy>=1.26`；`check_entrypoints.UNDECLARED_ALLOWED` 移除旧豁免；打包脚本补 `--collect-all numpy` | `check_entrypoints.py` 通过 |
| §2.3-3 zai | 从 Windows/macOS 打包脚本移除 `--collect-all zai` | `check_entrypoints.py`（构建脚本解析）通过 |
| §2.3-4 可选依赖 | README 安装段 + `docs/linux.md` §2 写明缺失降级表现（与 requirements.txt 注释一致） | 文档 |
| §2.4-1 EWMH 置顶 | `ui/common/x11.py`（`top_level_window` / `state_above` / `is_above` / `supports_above`，纯 ctypes）+ `ui/mini/topmost.py`；`MiniApp` 在 `<Map>` 后落地并回读 | GNOME/XWayland 实测：`_NET_WM_STATE_ABOVE` 写入/移除成功，xprop 可读 |
| §2.4-2 UI 降级 | `ui/mini/pixel.CycleRow.set_disabled()`；设置页在 `_NET_SUPPORTED` 无该属性时置灰并标注「本桌面环境不支持」，用户强行开启时弹一次说明 | 组件级实测（置灰后不可点击、文案正确） |
| §2.4-3 支持矩阵 | `docs/linux.md` §4 支持矩阵 + README「支持平台矩阵」 | 文档 |

