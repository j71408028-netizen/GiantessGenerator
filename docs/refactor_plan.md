# 目录分层重构：现状与交接

> **2026-10-06。阶段 0 – 3.2 与 §4.1（拆 `core/logic.py`）已全部落地，守卫的
> 「已登记例外」是一张空表**；剩下的只有 §4.2 – §4.5。本文件原名《目录重构计划》，
> 规划部分已执行完，现转为**持续维护的交接文档**——路径沿用 `docs/refactor_plan.md`
> 不改名，免得 README 与工作笔记里的链接断掉。文中所有数字与结论都是实测的，不是设想。

按需求查：

| 你想… | 去哪 |
|---|---|
| 动手搬代码 / 新增包 / 加删守卫 | §1 现状与约束 → §5 操作手册 |
| 搞清楚某次改动**为什么**这么做 | §3 历程索引 → §8 可复用拆法 |
| 接着往下做 | §4 剩余待办 |
| 提交代码 | §6 提交规范 |

---

## 1. 现状

### 1.1 分层与依赖方向

依赖只能向下（上层可用下层），由 `tests/check_import_graph.py` 强制。

| 层 | 位置 | 内容 | 可以依赖 |
|---|---|---|---|
| `infra` | `paths.py` | 路径解析 | 无 |
| `core` | `core/` | 领域模型：`models` `address_model` `logic` `behavior_runtime` `ai` `appearance` `imaging` | `infra` |
| `dungeon` | `dungeon/*.py` | 副本领域定义与规则（schema / terms / chapters / rules / validate …） | `infra` `core` |
| `persistence` | `persistence/` | 仓库层 | + `dungeon` |
| `services` | `services/` | 服务层；**含 `services/exploration/`（探索编排）** | + `persistence` |
| `dungeon_window` | `dungeon/window/**` | 副本会话窗口（Dear PyGui） | + `services`；**禁** `tkinter` / `ui.*` |
| `ui` | `ui/` | 专业界面（CTk）+ 挂件界面（原生 tkinter） | + `dungeon_window` |
| `app` | `main.py` `app_shell.py` `main_window_manager.py` | 入口与应用壳 | 全部；**不被任何人依赖** |

**`orchestration` 层已删除**（阶段 3.1）。它曾是 `core/context.py` 的专属层，用来给
`ExplorationContext` 这个 God object 开一条全图唯一的**双向**豁免。归位办法不是"把类拆小"
而是**整层搬走**：`ExplorationContext` 连同拆出的六个职责子系统一起迁进
`services/exploration/`，于是 `ui → services` 与 `services → persistence/core` 两侧都落回
既有合法边，豁免不再需要。**不要再把它搬回 `core/`**——守卫会直接判越界。

**根目录已冻结**：只剩上表里的 `paths.py` 与三个 `app` 文件。新增根目录 `.py` 会被守卫直接
判失败（`ROOT_MODULE_LAYERS` 既是层归属表，也是白名单）。

### 1.2 三道守卫的分工

| 脚本 | 管什么 |
|---|---|
| `tests/check_import_graph.py` | **全局层间方向** + 下层禁 UI 框架（本仓最核心的一道） |
| `tests/check_dungeon_layering.py` | dungeon 内部：领域层 / 窗口层 |
| `tests/check_mini_layering.py` | 挂件层禁 CTk、禁 `ui.common.{theme,fonts,widgets,dialogs}` |
| `tests/check_entrypoints.py` | 构建脚本的入口 / 图标 / `--add-data` 源路径存在性 + 全仓悬空 import |

另有 `check_scenario_schema.py` 守住副本方案字段的**端到端接线**（`schema` 声明 →
`normalize` 产出 → 源码级接线字符串），加字段时别漏，见 §5.4。

### 1.3 当前实测数字（2026-10-06）

```
[check_import_graph] 已检查 154 个模块，595 条第一方依赖边
[check_import_graph] PASSED：层间依赖方向与 UI 框架禁令均无未登记越界
KNOWN_EXCEPTIONS = []          # 原 11 条已全部消除
tests/run_checks.py            # 13/13
```

---

## 2. 硬约束（违反**不会报错**，只会静默失效）

### 2.1 `@behavior_hook` 的 scope 不是模块路径 ⚠️ 最高优先

`core/logic/`（四子模块合计）里有 12 处、`core/behavior_runtime.py` 有 1 处
`@behavior_hook("logic", "format_size")`。`behavior_hook(scope, name)` 把 key 拼成
`f"{scope}.{name}"`，**scope 是写死的字面量，与文件在哪个模块无关**。

这个 key 是**已部署世界包行为包的公开契约**：

```
data/static/behaviors/imperial_units/imperial_units.py:45
    runtime.override("logic.format_size", format_size)
```

所以搬家、重命名 `core/`、把 `logic.py` 拆成多个文件——**`"logic"` 这个字面量一个字都不能改**。
改成 `"core.logic.format_size"` 会让用户已安装的行为包**静默失效**（`resolve()` 返回 None，
静默回退默认实现，不报错、不告警）。

### 2.2 `data/` 是用户数据区

- **禁止在 `data/` 下用通配符删除**。`data/` 大部分文件不受 git 跟踪，删了无法恢复，
  Git Bash 的 `rm` 也不进回收站。
- 清理测试产物一律「移到系统临时区」：`shutil.move(path, tempfile.mkdtemp())`
  （见 `tests/smoke_mini.py::_discard`）。
- 搬迁代码时 `data/` 里的 `.py`（行为包）**不要改**——那是用户可替换的内容。

### 2.3 下层禁 UI 框架的口径（PIL 只禁两个桥）

`infra` / `core` / `persistence` / `services` 不得引入 `tkinter` / `_tkinter` /
`customtkinter` / `dearpygui`；**`PIL` 不在整包禁列**，只禁 `PIL.ImageTk` / `PIL.ImageGrab`
这两个直通 Tk 的桥。

纯图像处理（裁剪 / 缩放 / 缩略图 / base64）没有窗口依赖——`persistence` 做头像缩略图、
`services.preview` 渲染身材剪影都要用它——整包禁掉只会逼出「下层反向依赖服务层」的假例外。
守卫实现上，`collect_imports` 会额外记录**被导入的符号名**，`_framework_hit()` 靠它识别
`from PIL import ImageTk` 这类「顶层名合法、子模块越界」的写法（只看顶层名会漏）。

### 2.4 按依赖定层，不按名字定层

本仓判定一个模块该住哪层的唯一依据是**它实际依赖了什么**，不是它的名字像什么。
两条已落地的判例：

- `services/image_service.py` 名为 service，实为**工具箱**——里面 9 个纯图像函数 + 4 个 CTk
  函数混在一起。正解是按依赖拆开（纯图像下移 `core/imaging.py`，CTk 包装上移
  `ui/common/ctk_image.py`），而不是把整类搬走。
- `services/preview` 里那个 `tk.Canvas` 子类**全仓无任何实例化点**，只是被同模块的 PIL
  子类当绘制配方用 → 正解是**去 tk 化**（改名 `BodyPreviewPainter`，图元原语由子类实现），
  而不是把没人用的控件「上移进界面层」。

新增越界**不许直接塞进 `KNOWN_EXCEPTIONS` 就走**——先想清楚能不能靠"按依赖定层"解决。

---

## 3. 已完成的重构（历程索引）

| 阶段 | 提交 | 做了什么 | 关键验证 |
|---|---|---|---|
| **0** 修断链 | `93de7ae`（含） | 修 `main_mini.py` 被删留下的三处失配（挂件导入路径、构建入口、图标路径）；`.gitignore` 的 `build/` 改 `/build/*` + 显式放行两个脚本目录；新增 `check_entrypoints.py` | 三处断链消除 |
| **1** 建守卫 | 同上 | `check_import_graph.py`：141 个模块 / 560 条边压成一张层间矩阵 | 依赖图变得机器可断言 |
| **2** 收编根目录 | 同上 | `git mv` 六个模块进 `core/`（改写 48 个文件、81 行 import） | 改动前后边数**都是 560**（纯位移不变量） |
| **3.1** 拆 `context.py` | 同上 | `ExplorationContext` + 6 个职责模块整体迁入 `services/exploration/`；`orchestration` 层连同双向豁免一起删除 | 报告 `report_text` / `detail_text` 逐字节一致；autopilot 100/100 |
| **3.2.1** | `6f3d996` | 消除 4 条例外（`core → persistence`、两条 `services → ui`、`services` 引 `tkinter`） | 11 → 7；预览渲染亮/暗 × 4 组身高共 8 组 PNG **逐字节一致** |
| **3.2.2** | `13dc554` | 消除 2 条 `ui → app_shell`；新建 `services/ui_mode.py`；顺手修掉 tests 改名漏掉的断链 | 7 → 5；`check_entrypoints` 加硬「未知顶层名」检查 |
| **3.2.3** | `3c0737f` | **图像层拆分**，消除最后 5 条例外，`KNOWN_EXCEPTIONS` 清空 | 5 → 0；与 HEAD 旧实现 **83 项逐字节一致** |
| **4.1** 拆 `core/logic.py` | `9c50786` | 578 行杂物间按职责拆为 `core/logic/{sizing,quips,simulation,text}`，`__init__.py` 薄壳再导出 19 个公开名，29 个引用文件 import 零改动；`@behavior_hook("logic",…)` scope 一个字未动 | 探针比对 19 个公开名源码 + 11 个 hook key **逐字节一致**；13/13；边 586 → 595、模块 150 → 154（拆分机械上升，无新跨层依赖） |
| **顺带** | `93de7ae` | `ui/settings/`、`ui/quip/`、`ui/landmark/`、`ui/challenge/`、`services/chat/`、`services/preview/`、`dungeon/audio/` 包内分组；`tests/` 命名统一为 `check_*` / `smoke_*` | — |

> ⚠️ **一个可追溯性瑕疵**：阶段 0 / 1 / 2 / 3.1 的成果在本轮之前**从未入库**，
> 2026-10-06 一次性以 `93de7ae`「检查点」提交（127 项）。所以 `git log --follow` 会把
> 这些迁移都算在检查点那次提交上，而不是某个专门的提交。往后按 §6 一次一件事提交。

### 3.1 五条值得记住的判断

1. **按依赖定层**（§2.4）——`image_service` 拆分是标准判例。
2. **拆 God object 要"整层搬走"**，不是给它开例外：`ExplorationContext` 从 `core/` 迁到
   `services/` 后，`orchestration` 层**整个消失**，而不是豁免继续有效。
3. **注入回调 vs 共享词表下移**：两条 `ui → app_shell` 例外性质不同——
   设置页读写的是**设置项**（与同页的主题 / 字体同类），下移共享词表
   `services/ui_mode.py` 即可；挂件标题栏调的是 `switch_to`（**机制**：销毁根窗口 + 登记请求
   + 落盘），必须**注入回调**，且缺省 `None` 时要提示「界面切换未接线」而不是静默失败。
4. **拆模块会推高依赖边数**：纯位移时边数应不变（阶段 2 的 560 → 560）；拆出 N 个新文件会把
   原先记在一个文件下的 import 分散登记，边数自然上升（3.1: 560 → 584；3.2.3: 583 → 586）。
   别把"边数变了"一律当成引入新依赖。
5. **外观（Facade）先保 API，后收紧**：3.1 只做抽取、不改 50+ 处 ui 调用点，`context.py`
   保留原方法名与属性名。⚠️ **两个属性必须有 setter**——`ui/challenge.py` 会直接赋值
   `context.selected_styles` / `selected_quip_styles` 再调 `reload_merged_data()`；
   只写只读 `@property` 会让赋值**悄悄创建实例属性**盖住真值，随后读到旧值，静默失效。

---

## 4. 剩余待办

都不是"必须做"——重构的主干已经完成。按建议顺序：

### 4.2 整理 `services/` 目录（§4.3 的前置，2026-10-06 定版）

**问题**：三种组织范式并存——新范式包（`exploration/`，有门面有文档）、旧包
（`character_service/`，`__init__.py` 是空的；名字的旧语义「任何建立在持久化角色之上
的行为」已失真——聊天支撑件同样建立在持久化角色之上却散在外面）、外加 10 个散文件。

**契约红线**（执行时逐条对照）：

1. 钩子 key（`"CreationService.*"` ×2、`"StateService.*"` ×14）是 `scope.name` 字面量，
   与文件路径无关，搬家安全；scope 一个字不许改。
2. `services/creation_service.py` 与 `services/state_service.py` 的**模块路径本身**是
   行为包契约——`docs/world_pack_behaviors.md` 的示例教行为包作者
   `from services.creation_service import CreationService`，已部署行为包可能照此
   import → **两文件留顶层不动**（定版：不收编、无 shim 税）。
3. `developer_tools/debug_archive_preview.py` 引用
   `services.character_service.archive_export`——改名步骤必须同步它，否则
   `check_entrypoints` 的全仓悬空 import 检查会挂。
4. `data/`、`scripts/`、`build/` 已实测零 services 路径引用。

**目标结构**（§2.4 按实际内容定归属；三条定版决策：`scale_reference` 上浮 core——
将来供 dungeon 与外部工具，core 是能让最多方合法引用的最底层（`appearance` 判例）；
`news` 独立成附加功能包——与角色长时行为正交；creation / state 留原位）：

```
services/
  __init__.py            # 门面清零：只剩 docstring（现 6 个 re-export 仅 3 处在用）
  creation_service.py    # 留顶层 —— 行为包契约 + 跨域共用核心服务
  state_service.py       # 留顶层 —— 同上（dungeon 也直接用）
  ui_mode.py             # 留顶层 —— 3.2.2 判例，理由已写在模块 docstring
  preview/               # 不动
  exploration/
    + detail_pools       # ← helpers.build_detail_pools（唯一消费者是 catalog）
  chat/
    + experience_events  # ← 散文件收编（chat_delivery.md 阶段四的事件流）
    + persona            # ← character_persona.py
  worlds/                # 新包：world_service + address_registry（地址注册表即世界包选址）
  challenges/            # 新包：challenge_service + helpers 的 3 个挑战包函数
  character/             # ← character_service/ 改名，只留角色自身长时行为
                         #   （archive_export / offline / rhythm + 门面 docstring 写明边界）
  news/                  # 新包（news.py → __init__.py，同 preview/ 构型）：
                         #   NewsService + DEFAULT_NEWS_TABLE
core/
  scale_reference.py     # ← 上浮（现内容只依赖 core.logic 的两个名字，零窗口依赖）
```

**执行步骤**（一次一件事，每步一提交）：

| 步 | 提交 | 动作 | 引用点改写 |
|---|---|---|---|
| 1 | `refactor(core)` | `scale_reference.py` 上浮 `core/` | character_persona、chat |
| 2 | `refactor(services)` | `chat/` 收编 experience_events、character_persona | chat_delivery.md 的路径引用 |
| 3 | `refactor(services)` | 建 `challenges/`，helpers 的 3 个挑战包函数并入；`build_detail_pools` → `exploration/`；删 `helpers.py` | ui/landmark、ui/quip 改直连 |
| 4 | `refactor(services)` | 建 `worlds/` 收编 world_service + address_registry | main 三件套 |
| 5 | `refactor(services)` | `news` 独立成 `services/news/` 包 | ui/settings、exploration/catalog |
| 6 | `refactor(services)` | `character_service/` → `character/` + 门面 docstring | state_service 延迟 import、ui/exploration/giantess_state、ui/mini/app、smoke_mini、developer_tools 一处 |
| 7 | `refactor(services)` | `services/__init__.py` 门面清零 | ui/settings 的 `from services import ui_mode` 改直连 |
| 8 | `docs` | README 结构约定、chat_delivery.md、本文件记录完成 | — |

每步固定动作：`git mv` → grep 零残留 → `run_checks` 13/13 → **核对边数不变**
（全是纯位移、无拆分——与 §4.1 的「机械上升」判据不同）。全部完成后跑
`smoke_mini`（75 项）+ `autopilot --in-process`（100 项）收口。守卫零改动：
`PACKAGE_LAYERS` 按一级包归层，services / core 下的新子包自动归层。

### 4.3 搬 `app` 层进 `app/` 包

`app_shell.py`（399 行）→ `app/shell.py`，`main_window_manager.py`（674 行）→
`app/window_manager.py`；**`main.py` 留在根目录**（它是构建脚本里的字面量入口）。

同步守卫：`ROOT_MODULE_LAYERS` 删这两项、`PACKAGE_LAYERS` 加 `"app": "app"`；
`main.py` 里对 `app_shell` 的引用（含热切换时对 `switch_to` 的接线）一并改。

### 4.4 两个小尾巴

- **`tests/smoke_switch.py` 有 2 条陈旧断言**（与重构无关，见 §7 第 5 条），修完基线可回
  49/49。
- 文档里的数字偶尔会陈旧（如 autopilot 的项数），改的时候顺手对一下。

### 4.5 可选：文档改名

本文件叫 `refactor_plan.md` 已名不副实（规划已执行完）。若改名为 `refactor_handoff.md`，
需同步 README（2 处）与 `.workbuddy/memory/` 里的引用。**不改也完全可行**，别为了改名而断链。

---

## 5. 操作手册

### 5.1 搬一个模块 / 子包

1. `git mv`（保留历史，`git log --follow` 可追）。
2. 改引用：本仓绝对导入风格是 `from core.models import X`，**跨包不要用相对导入**。
   机械改写后必须 grep 验证零残留：

   ```bash
   grep -rn --include=*.py -E "^\s*(from|import)\s+<旧名>\b" . | grep -v __pycache__
   ```

   别忘了 `data/` 与**字符串形式**的引用（已知一处：`@behavior_hook` 的 scope，§2.1）。
3. 同步 `tests/check_import_graph.py` 的 `ROOT_MODULE_LAYERS` / `PACKAGE_LAYERS`
   （必要时还有 `_layer_from_parts` 的特例）与 `KNOWN_EXCEPTIONS` 里的 `src` 路径。
4. **搬完后检查 `tests/` 里自建该控件的 helper**：在 app 层给 ui 注入的回调，测试若自己
   `build_*` 一份实例就会丢掉接线（3.2.2 踩过，表现为挂住而不是报错）。
5. 跑 `python tests/run_checks.py`。**看一眼依赖边总数**——纯位移应当不变；变了先判断
   是"拆模块导致的正常上升"还是"真的增删了依赖"（§3.1 第 4 条）。
6. 同步文档：`README.md` 的「仓库结构约定」、`core/__init__.py` 之类的包文档、本文件。

### 5.2 自检怎么跑

| 命令 | 用途 | 注意 |
|---|---|---|
| `python tests/run_checks.py` | **离线 13 项，提交前必跑** | 纯静态，不需要显示器 |
| `python tests/run_checks.py --smoke` | 追加 GUI 冒烟（3 个） | **两个 GUI 自检不可并发** |
| `python tests/smoke_mini.py` | 挂件全链路（75 项）+ 真实副本窗口 | 需显示器 |
| `python tests/smoke_switch.py` | 界面热切换 5 轮 | 改切换 / 收尾逻辑后必跑；当前 47/49（§7 第 5 条） |
| `python scripts/dungeon_autopilot.py --in-process` | 11 场景 **100 项**，最强回归 | stdout 在报告文件里，别只看终端 |

`run_checks` 按前缀自动发现：`check_*.py` 离线、`smoke_*.py` 需显示器。新增脚本放进对应
类别即可，无需登记清单。

**环境注意**：

- 带依赖的解释器是 `C:/Users/M/AppData/Local/Programs/Python/Python313/python.exe`
  （managed 的那个不一定装了项目依赖）。
- Bash 工具的 shim 会破坏 `PATH`，命令前先 `export PATH="/usr/bin:/bin:/mingw64/bin:$PATH"`。
- **GUI 自检的退出码在沙箱里不可信**：跑过 `tk.Tk()` 的进程 `os._exit` 有时不返回，表现为
  「永远 running」或莫名的 99，查下来进程其实已退。**以日志内容为准**。
- 临时探针别用 `os._exit(0)` 收尾，用
  `ctypes.windll.kernel32.TerminateProcess(GetCurrentProcess(), 0)`；也别把多轮探针串成一条
  `for` 命令（第一轮卡住后面全跑不到），每轮单独一个后台任务并各自重定向日志。

### 5.3 界面热切换与副本窗口

改 `app_shell.py` 或副本收尾逻辑前，**先读 `.workbuddy/memory/` 当日的完整记录**
（本地笔记，不在版本库里）与 `docs/Dungeon/window.md` §5-C2。那里有五个「会让进程直接死」
的陷阱：DPG 未建 context 时调 `is_dearpygui_running()` 段错误、跑完一局副本后 Tk 根不能再
`destroy()`、保活视口 `park_context` / `unpark_context` 的顺序等等。

改完必须跑 `python tests/smoke_switch.py`，日志里 `invalid command name` 条数应为 0。

### 5.4 给副本方案加字段

`schema.py` 声明 → `chapters.py` 的 `normalize_*` 接入 `normalize_chapter` → 领域能力模块
（如 `dungeon/audio.py`）→ `window/` 接线（生命周期 + 进入章节时应用）→ `validate.py` 诊断 →
编辑器 UI（导入 + 编辑区 + 列表列）→ **守卫断言**（`check_scenario_schema.py`）。
漏掉最后一步会静默失效。详见 `docs/Dungeon/script.md`。

---

## 6. 提交规范：约定式提交

**2026-10-06 起，本仓改用 [Conventional Commits](https://www.conventionalcommits.org/)。**
大型结构迭代已结束，不再需要 `20261006阶段3.2.3：…` 这类阶段前缀——阶段信息写进正文，
由本文件与 git 历史承担。

格式：

```
<type>(<scope>): <subject>

<正文：为什么这么改、怎么验证的>
```

- `type` 用英文小写：`feat` 新功能 / `fix` 修缺陷 / `refactor` 重构（**行为不变**的代码结构调整）/
  `docs` 文档 / `test` 测试 / `chore` 杂务 / `build` 打包 / `perf` / `style` / `ci` / `revert`。
- `scope` 用受影响的子系统：`core` `dungeon` `persistence` `services` `ui` `mini` `window`
  `tests` `docs` `build`。跨层或用总括作用域时留空。
- `subject` 与正文用**中文**（本仓既有习惯），一句话说清做了什么，**结尾不加句号**。
- 一次提交只做一件事；纯位移搬迁与逻辑修改不要混在一起（否则 `git log --follow` 与
  "边数不变"这两条判据都失效）。

示例：

```
refactor(core): 拆分图像处理，纯图像下移 core/imaging.py

services/image_service.py 名为 service 实为工具箱：9 个纯图像函数 + 4 个 CTk 包装
混在一起。按依赖一拆为二，CTk 部分上移 ui/common/ctk_image.py。
守卫例外 5 → 0；与 HEAD 旧实现 83 项逐字节一致。

fix(mini): 修复挂件下深色主题仍渲染浅色剪影

挂件模式从不调用 ctk.set_appearance_mode，ctk 一直停在默认 Light。改读
core.appearance（外观模式的唯一来源）后跟随主题。

docs: 重写目录分层交接文档
```

---

## 7. 待决问题

1. `developer_tools/escape_giantess_web/` 与 `data/packs/minigames/escape_giantess/game.py`
   是同一款游戏的两份，但前者被 gitignore——**谁是上游源码、谁是产物**？决定要不要把前者
   纳入版本控制。
2. `scripts/dungeon_autopilot.py`（1698 行，100 项）是主力回归但需要真实窗口：留在
   `scripts/` 还是移进 `tests/` 由 `run_checks --smoke` 收录？
3. `data/packs/challenges/` 是个冒烟测试留下的空目录（只移走了 `.chal` 没删目录），无害，
   要不要补清理逻辑。
4. 要不要把**报告黄金样本**回归固化进 `tests/`（固定种子 + 固定角色 → 存 `report_text` /
   `detail_text` 基线逐字节比对）。3.1 与 3.2 都用临时脚本做过，**还没进仓**。
5. **`tests/smoke_switch.py` 有 2 条陈旧断言**（与重构无关）：
   - 「设置里选挂件模式会写进设置」仍在调 `_on_ui_mode_changed("挂件模式")` 并断言
     `load_mode() == MODE_MINI`；但设置页早已重做为「启动界面模式」，选项是
     `["专业模式", "ME模式", "退出时模式"]`，写的是 `ui_startup`（`save_startup_mode`），
     **故意不再碰 `ui_mode`**；
   - 「切换按钮文案标明是换界面」断言按钮文案含「切换界面」，而导航栏按钮现在的文案是
     `"  ⇄  ME模式"`。

   所以跑 `smoke_switch.py` 是 **47/49**。要修就得先确认这两处的预期语义（改测试还是改界面）。
6. 本文件是否改名为 `refactor_handoff.md`（§4.5）。

---

## 8. 附：拆 God object 的可复用拆法（阶段 3.1 的经验）

将来再遇到"一个大类什么都管、被上下层同时引用"时，这套流程可以直接复用：

1. **先分类成员，按职责切成独立对象**（不要求立刻缩类体量，先把归属画清楚）。
   3.1 把它切成：目录/配置、报告引擎、地址规划、报告文本、角色装配、导出，加一层薄外观。
2. **划清"数据生产"与"呈现"的分界**——3.1 里最易错的一处：`_generate_report_data` 是**引擎**
   （产出结构化数据），`_build_report_text` 才是**呈现**（拼给人看的文本）。
   后者只依赖 `settings` + `format_size`，**看起来最像 `core`，其实产出的是报告工件**——
   别因为它"看起来底层"就放进 `core`。
3. **整体搬到合适的层**，而不是开例外。目标层选在"它依赖的东西都在下面、依赖它的东西都在
   上面"的那一层。
4. **切掉反向边**（3.1 里唯一那条 `context → ui`）：换调用为目标层内的等价实现。对外观
   一致性要**逐字节**验证，且**先同步输入**——两边读的外观模式来源不同（`ctk.get_appearance_mode()`
   vs `appearance.is_dark()`，默认值一个 `Light` 一个 `Dark`），不对齐会得到"不一致"的假结论。
5. **外观先保 API**（§3.1 第 5 条），调用点分批改。
6. **每步跑回归**：`run_checks` + 逐字节基线 + GUI 冒烟 + autopilot。3.1 全程 13/13、
   `REPORT IDENTICAL`。

**两处顺序教训**（方案里排错、实测时改掉的）：

- **切反向边要先于搬依赖它的模块**。3.1 方案把切边排在"搬第一组"之后，但第一组里就含那个
  做延迟 import `ui.*` 的函数——搬进 `services` 的瞬间守卫就会 FAIL。**先切边，再搬。**
- **共享状态要先于消费者搬**。报告 / 地址规划要**实时**读 `selected_styles` /
  `merged_landmarks` / `quips`（它们会被 `update_styles()` 整体替换），不能拷成自己的字段，
  只能持有共享的 `ExplorationCatalog`——所以**目录得先存在**。方案出于"调用点最密、
  review 成本高"把目录排最后，但那笔成本是**属性转发**的，与目录何时搬家无关。
