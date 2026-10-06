# 目录重构计划与交接

> 面向「接着做这件事的人（或下一次会话的自己）」。截至 2026-10-06，阶段 0 / 1 / 2 / 3.1
> 已落地，阶段 3（余项）/ 4 待办。所有判断都基于实测，不是设想。

## 1. 现状：分层结构

依赖方向只能向下（上层可用下层），由 `tests/check_import_graph.py` 强制。

| 层 | 位置 | 内容 | 约束 |
|---|---|---|---|
| `infra` | `paths.py` | 路径解析 | 不依赖任何第一方模块 |
| `core` | `core/` | `models` `address_model` `logic` `behavior_runtime` `ai` | 只能依赖 `infra` |
| `dungeon` | `dungeon/*.py` | 副本领域定义与规则（schema / terms / chapters / rules / validate …） | 依赖 `core` `infra`；`dungeon/window/**` 另算 |
| `persistence` | `persistence/` | 仓库层 | 依赖 `core` `dungeon` `infra` |
| `services` | `services/` | 服务层；**含 `services/exploration/`（探索编排）** | 依赖 `persistence` 及以下 |
| `dungeon_window` | `dungeon/window/**` | 副本会话窗口（Dear PyGui） | 可依赖 `services` 及以下；**不得**碰 `tkinter` / `ui.*` |
| `ui` | `ui/` | 专业界面（CTk）+ 挂件界面（原生 tkinter） | 可依赖 `dungeon_window` 及以下 |
| `app` | `main.py` `app_shell.py` `main_window_manager.py` | 入口与应用壳 | 可依赖一切；不被任何人依赖。**不单独打包某个启动模式**：曾有的 `main_mini.py` 固定入口与构建脚本的 `-Mini`/`--mini` 开关已于 2026-10-06 按设计决策再次移除，将来拓展启动器形式时从 git 历史恢复即可 |

**`orchestration` 层已于阶段 3.1 删除**。它原是 `core/context.py` 的专属层，用来给
`ExplorationContext` 这个 God object 开一条全图唯一的**双向**豁免（被 ui 大量引用，
又反向延迟导入 `ui.exploration.creation_params_dlg`）。归位办法不是"把类拆小"而是
**整层搬走**：`ExplorationContext` 连同拆出的六个职责子系统一起迁进
`services/exploration/`，于是 `ui → services` 与 `services → persistence/core` 两侧
都落回既有合法边，豁免不再需要。**不要再把它搬回 `core/`**——守卫现在会直接判越界。

**根目录已冻结**：只剩上表里的 `paths.py` 与四个 `app` 文件。新增根目录 `.py` 会被守卫
直接判失败（`ROOT_MODULE_LAYERS` 是白名单）。

守卫覆盖范围：`tests/check_import_graph.py`（全局层间方向 + 下层禁 UI 框架）、
`check_dungeon_layering.py`（dungeon 内部两层）、`check_mini_layering.py`（挂件层禁 CTk）。

## 2. 进度

### 阶段 0 — 修断链（已完成）

提交 `a35fc89` 删除 `main_mini.py` 时留下三处失配，会直接炸但都被静默掩盖：

1. `ui/mini/app.py` 的 `from services.archive_export import ...` → 实际模块是
   `services/character_service/archive_export.py`（挂件导出档案必抛 `ModuleNotFoundError`）。
2. `main_mini.py` 被删但两个平台构建脚本仍指它 → `-Mini` 打包必然失败。**当时恢复为 6 行薄壳**（后续按「不单独打包启动模式」的设计决策于 2026-10-06 连同构建脚本的 Mini 开关一起再次移除，见 §1 app 行）
   （`run_app(initial_mode=MODE_MINI)`）。
3. 构建脚本 `--icon assets\icon.ico` → 实际 `assets/icons/icon.ico`。

根因：`.gitignore` 里一条 `build/` 同时吞掉了 PyInstaller 产物目录**和构建脚本目录**，
构建脚本从未入库，所以入口被删也没人发现。已改为 `/build/*` + 显式放行 `build/windows/`、
`build/macos/`。

新增 `tests/check_entrypoints.py`：校验构建脚本里的入口 / 图标 / `--add-data` 源路径真实存在，
并静态扫描全仓悬空 import。

### 阶段 1 — 建守卫（已完成）

新增 `tests/check_import_graph.py`：141 个模块、560 条第一方依赖边压缩成一张层间矩阵。

### 阶段 2 — 收编根目录（已完成）

`git mv` 六个模块进 `core/`（历史可 `--follow` 追踪）：

```
models.py -> core/models.py            behavior_runtime.py -> core/behavior_runtime.py
address_model.py -> core/address_model.py    ai.py -> core/ai.py
logic.py -> core/logic.py              context.py -> core/context.py
```

> 注：`context.py` 在阶段 2 只是**路过** `core/`——阶段 3.1 已把它迁到
> `services/exploration/context.py`（见 §8）。上表记录的是历史路径，不是现状。

- **纯位移**：改写 48 个文件、81 行 import；改动前后依赖边总数**都是 560**，证明没引入或消除
  任何依赖。零残留（全仓 grep 无 `from models import` 之类）。
- `core/__init__.py` 写明包约束与下面 §5 的两条红线。
- 验证：全仓 `compileall` 通过；离线自检 **13/13**；挂件全链路冒烟通过。

**本轮刻意没做的事**（留给阶段 3，避免一次动太多）：没有拆 `logic.py`、没有拆 `context.py`、
没有搬 `app_shell.py` / `main_window_manager.py`。

### 阶段 3.1 — 拆 `core/context.py`（已完成）

`ExplorationContext` 及其职责子系统整体迁入 `services/exploration/`，`orchestration`
层连同它的双向豁免一起删除。详案与实测记录见 §8；执行顺序相对 §8.5 有两处调整，
原因写在 §8.8。

## 3. 阶段 3 — 待办（按建议顺序）

每一步单独提交，跑 `python tests/run_checks.py`（`--smoke` 追加 GUI 冒烟）。

1. ~~**拆 `core/context.py`**~~ —— **已完成（阶段 3.1）**。结论与做法见 §8；
   实际落地时 §8.5 的提交顺序被调整过两处，原因也记在 §8.5。做完的结果：
   `orchestration` 层从守卫矩阵里消失，`core/` 不再有任何 `persistence` / `services` /
   `ui` 的真实 import（只剩 `behavior_runtime → persistence.world_pack` 这条已登记例外）。
2. **拆 `core/logic.py`**（578 行杂物间）：常量与尺寸格式化（`ALL_PART_NAMES` /
   `SIZE_CATEGORIES` / `format_size` / `length_unit_label`）、quip 标签与选取
   （`replace_quip_tags` / `select_quip_with_budget`）、模拟计算（`compute_casualty` /
   `compute_environment_factor` / `apply_size_unlock_updates`）。
   **拆完必须同步改 `@behavior_hook("logic", ...)` 的 scope 吗？不要改**，见 §5。
3. **消除 §4 的 11 条例外**。建议顺序：`services → ui` 两条（把 `appearance` 下移到
   `core` 或 `infra`，它本就是纯 Python）→ `ui → app_shell` 两条（改为注入回调）→
   `core → persistence` 一条（世界包解析器改为由调用方注入）→ `persistence → services`
   与 UI 框架那几条（随 `image_service` / `body_preview` 拆分一起做）。
4. ~~**包内分组**~~ —— **已完成（2026-10-06）**：`ui/settings/`（原 settings.py →
   `__init__.py`、settings_dlg.py → `dialogs.py`）、`ui/quip/`（quip_mgr.py →
   `__init__.py`、quip_dlg.py → `dialogs.py`）、`ui/landmark/`、`ui/challenge/`；
   `services/chat/`（chat_service.py → `__init__.py`、chat_delivery.py → `delivery.py`、
   chat_events.py → `events.py`）、`services/preview/`（原 body_preview.py）；
   `dungeon/audio/`（`__init__` = 原 audio.py，加 voice_fx / speech / chapters 三个子模块）。
5. **搬 `app` 层**：`app_shell.py` → `app/shell.py`、`main_window_manager.py` →
   `app/window_manager.py`。注意 `main.py` 是**构建脚本里的字面量入口**，
   改名要同步 `build/`，`check_entrypoints.py` 会兜住。
6. ~~**`tests/` 命名统一**~~ —— **已完成（2026-10-06）**：现分 `check_*`（守卫 / 行为，
   离线）与 `smoke_*`（需显示器）两类，`run_checks.py` 按前缀自动发现；
   `validate_scenarios.py` → `check_scenarios.py`、`smoke_test_mini.py` → `smoke_mini.py`、
   `smoke_test_switch.py` → `smoke_switch.py`、`mini_game_smoke.py` → `smoke_mini_game.py`。

## 4. 已登记的例外（11 → 7，仍在减少中）

全部在 `tests/check_import_graph.py` 的 `KNOWN_EXCEPTIONS` 里，每条带 `why` 与 `plan`。
守卫会在例外指向的文件不存在时判**配置错误**（强制搬迁后清理），并在例外未触发时提示可删。

### 4.1 阶段 3.2.1 已彻底消除（2026-10-06，4 条）

| 原 # | 原位置 | 原越界 | 消除办法 |
|---|---|---|---|
| 3 | `core/behavior_runtime.py:92` | `core → persistence.world_pack` | `resolve_behavior_source`（11 行纯 `os.path`）**下移**进 `core/behavior_runtime.py`；`persistence/world_pack.py` 不再定义它，`services/world_service.py` 改从 core 引入。运行时那边本来就在拼 `installed/behaviors` 路径，解析器与它同属一件事 |
| 5 | `services/preview/__init__.py:18` | `services → ui` | `ui/common/appearance.py` → **`core/appearance.py`**（零 import，6 处引用改 `from core import appearance`）。放最底层是唯一不需要反向例外的位置 |
| 6 | `services/creation_service.py:256` | `services → ui` | 改调 `services.preview.render_body_preview_to_file`（与界面那份**逐字节一致**，见 4.3） |
| 10 | `services/preview/__init__.py:13` | 引入 `tkinter` | 绘制配方与画布**解耦**：那个 `tk.Canvas` 基类**全仓无任何实例化点**，改名为 `BodyPreviewPainter`（纯对象 + 6 个图元原语由子类实现），模块彻底不 import tkinter |

> **原计划第 10 条的写法要修正**：它写的是「把 `BodyPreviewCanvas` 上移 `ui/mini`」，
> 但实测它只被同模块的 PIL 子类当绘制配方用，**没有任何界面代码引用它**——挂件用的是
> `render_preset_preview_image()`，专业界面那份是 `creation_params_dlg.py` 里**自带
> tk 画布的独立拷贝**。所以正确的减法是去 tk 化，不是把没人用的控件搬进界面层。

### 4.2 剩余 7 条（本轮继续消除，见 3.2.2 / 3.2.3）

| # | 位置 | 越界 | 性质 |
|---|---|---|---|
| 1 | `ui/settings/__init__.py:893,942` | `ui → app_shell` | 设置页「界面模式」直接调外壳切换函数 |
| 2 | `ui/mini/app.py:895` | `ui → app_shell` | 挂件标题栏「⇄」同上 |
| 4 | `persistence/character_repo.py:10` | `persistence → services` | 生成头像缩略图 |
| 7 | `persistence/character_repo.py:61` | 引入 `PIL` | 缩略图解码 |
| 8 | `services/image_service.py:7` | 引入 `customtkinter` | **服务层里混着界面逻辑** |
| 9 | `services/image_service.py:8` | 引入 `PIL` | 图像处理本体 |
| 11 | `services/preview/__init__.py:16` | 引入 `PIL` | 预览剪影绘制 |

第 8 条最值钱：`services/image_service.py` 用 CTk，而 `ui/common/dialogs.py` 又反向依赖它，
构成 `ui → services → ctk` 的绕行链，把既有守卫（只管 dungeon 与 mini）全绕过去了。

`services/preview/__init__.py` 的 docstring 自己写着「服务层不必反向依赖 UI 层」，还刻意把调色板
写成字面量以避免拉进 CTk——作者有分层意识，只是第 5 条漏了。它和
`ui/exploration/creation_params_dlg.py` 里的同款画法是**两份重复实现**，消除第 8/9 条时应顺便合并。

### 4.3 阶段 3.2.1 的验证证据

| 判据 | 结果 |
|---|---|
| `tests/run_checks.py` | 13/13 ✅ |
| `tests/check_import_graph.py` | PASSED；例外 11 → 7，依赖边 583 → 582（唯一少的那条正是被删除的 `core → persistence` 延迟导入）✅ |
| 预览渲染逐字节回归 | 亮/暗 × 4 组身高共 **8 组 PNG 逐字节一致**（`PREVIEW IDENTICAL`）✅ |
| `tests/smoke_mini.py` | 通过 ✅ |

## 5. 两条红线（违反会静默失效，不会报错）

### 5.1 `@behavior_hook` 的 scope 不是模块路径

`core/logic.py` 里有 12 处 `@behavior_hook("logic", "format_size")`，`behavior_runtime.py`
里有 1 处。`behavior_hook(scope, name)` 把 key 拼成 `f"{scope}.{name}"`，**scope 是写死的
字面量，与文件所在模块无关**。

这个 key 是**已部署世界包行为包的公开契约**：

```
data/static/behaviors/imperial_units/imperial_units.py:45
    runtime.override("logic.format_size", format_size)
```

所以：搬家、重命名 `core/`、把 `logic.py` 拆成多个文件——**`"logic"` 这个字面量一个字都
不能改**。改成 `"core.logic.format_size"` 会让用户已安装的行为包**静默失效**（`resolve()`
返回 None，静默回退默认实现，不报错）。

### 5.2 `data/` 是用户数据区

- **禁止在 `data/` 下用通配符删除**。`data/` 大部分文件不受 git 跟踪，删掉无法恢复，
  Git Bash 的 `rm` 也不进回收站。
- 清理测试产物一律「移到系统临时区」（`shutil.move(path, tempfile.mkdtemp())`，见
  `tests/smoke_mini.py` 的 `_discard()`）。
- 搬迁代码时 `data/` 里的 `.py`（行为包）**不要改**——它们是用户可替换的内容。

## 6. 操作手册

### 搬一个模块/子包

1. `git mv`（保留历史，`git log --follow` 可追）。**
2. 改引用：本仓的绝对导入风格是 `from core.models import X`，不要用相对导入跨包。
   机械改写后**必须 grep 验证零残留**：

   ```bash
   grep -rn --include=*.py -E "^\s*(from|import)\s+<旧名>\b" . | grep -v __pycache__
   ```

   注意 `data/` 与字符串形式的引用（本项目已知一处：`@behavior_hook` 的 scope，见 §5.1）。
3. 同步 `tests/check_import_graph.py` 的 `ROOT_MODULE_LAYERS` / `PACKAGE_LAYERS`
   （以及必要时的 `_layer_from_parts` 特例）与 `KNOWN_EXCEPTIONS` 里的 `src` 路径。
4. 跑 `python tests/run_checks.py`。**看一眼依赖边总数**——纯位移的话它应当不变；
   变了说明你无意中增删了依赖。

### 自检怎么跑

| 命令 | 用途 | 注意 |
|---|---|---|
| `python tests/run_checks.py` | 离线 13 项，提交前必跑 | 纯静态 + 无显示器 |
| `python tests/run_checks.py --smoke` | 追加三个 GUI 冒烟 | **两个 GUI 自检不可并发** |
| `python scripts/dungeon_autopilot.py --in-process` | 11 场景 95 项，最强回归 | stdout 在报告文件里，别只看终端 |

环境注意：

- 带依赖的解释器是 `C:/Users/M/AppData/Local/Programs/Python/Python313/python.exe`
  （managed 的那个不一定装了项目依赖）。
- Bash 工具的 shim 会破坏 `PATH`，脚本里先
  `export PATH="/usr/bin:/bin:/mingw64/bin:$PATH"`。
- **GUI 自检的退出码在沙箱里不可信**：跑过 `tk.Tk()` 的进程 `os._exit` 有时不返回，
  表现为「永远 running」或莫名的 99，但 `Get-Process` 查下来进程其实已退出。
  以日志内容为准。临时探针别用 `os._exit(0)` 收尾，用
  `ctypes.windll.kernel32.TerminateProcess(GetCurrentProcess(), 0)`。

### 界面热切换与副本窗口

改 `app_shell.py` 或副本收尾逻辑前，**先读 `.workbuddy/memory/` 当日的完整记录**。那里有
五个「会让进程直接死」的陷阱（DPG 未建 context 时调 `is_dearpygui_running()` 段错误、
跑完一局副本后 Tk 根不能再 `destroy()`、保活视口 `park_context` / `unpark_context` 的顺序
等）。改完必须跑 `python tests/smoke_switch.py`，日志里 `invalid command name` 条数应为 0。

## 7. 待决问题

1. `developer_tools/escape_giantess_web/` 与 `data/packs/minigames/escape_giantess/game.py`
   是同一款游戏的两份，但前者被 gitignore——**谁是上游源码、谁是产物**？决定要不要把前者
   纳入版本控制。
2. `scripts/dungeon_autopilot.py`（1698 行）是主力回归但需要真实窗口：留在 `scripts/`
   还是移进 `tests/` 由 `run_checks --smoke` 收录？
3. ~~`context.py` 的拆分切法（按调用方 vs 按职责）。~~ **已定**：按职责切分 + 薄外观保
   API + 整体迁到 `services/` 层，见 §8。
4. `data/packs/challenges/` 是个冒烟测试留下的空目录（只移走了 `.chal` 没删目录），无害，
   要不要补清理逻辑。
5. ~~§8.3 的反向边切法选 (A) 还是 (B)~~ **已定：选 (A)**，并且实测通过——两份
   `render_body_preview_image` 在**同一外观模式**下（亮/暗 × 4 组身高）PNG **逐字节一致**。
   注意陷阱：`ui` 那份读 `ctk.get_appearance_mode()`，`services` 那份读
   `ui.common.appearance.is_dark()`，而 `appearance` 的默认值是 `Dark`、ctk 的默认值是
   `Light`——直接对拉会得出"不一致"的假结论，必须先按应用的做法把两个来源同步。
6. 要不要在 §8.6 之外再建一个**报告黄金样本**回归（`tests/test_report_*`）：固定种子 +
   固定角色，把 `report_text` / `detail_text` 存成基线逐字节比对。本次是用临时脚本做的
   （见 §8.8），**还没进仓**——要不要固化成 `tests/` 里的常驻回归。
7. **`tests/smoke_switch.py` 有 2 条陈旧断言**（与本次重构无关）：
   - 「设置里选挂件模式会写进设置」仍在调 `_on_ui_mode_changed("挂件模式")` 并断言
     `load_mode() == MODE_MINI`；但设置页早已重做为「启动界面模式」，选项是
     `["专业模式", "ME模式", "退出时模式"]`，写的是 `ui_startup`（`save_startup_mode`），
     **故意不再碰 `ui_mode`**；
   - 「切换按钮文案标明是换界面」断言按钮文案含「切换界面」，而导航栏按钮现在的文案是
     `"  ⇄  ME模式"`。
   所以本次跑 `smoke_switch.py` 是 **47/49**，那 2 项与 `ExplorationContext` 无关。
   要修就得先确认这两处的预期语义（是改测试还是改界面）。

## 8. 阶段 3.1 详案：拆 `core/context.py`（**已落地**）

> 对 §3 待办第 1 条的细化。**结论先说**：目标不是"把一个类拆小"，而是——
> ① 把职责拆成独立对象；② 把 `ExplorationContext` **整体搬出 `core/`** 到 `services/`；
> ③ 切掉全图唯一那条反向边（`context → ui`）。做完这三件，`orchestration` 层就从守卫矩阵
> 里**整个消失**，而不是"豁免继续有效"。
>
> **状态：2026-10-06 已全部落地。** 实测结果与两处顺序调整见 §8.8；
> §8.5 的步骤表保留原样（它是方案），实际执行顺序以 §8.8 为准。

### 8.1 为什么现在的归层是错的

`core/` 在本仓的约束是「只能依赖 `infra(paths)`」。而 `ExplorationContext` 顶层就
`import persistence`（8 个 repo）与 `import services`（`build_detail_pools` /
`CreationService` / `NewsService` / `StateService`），另有 3 处函数内 import：两处是
`services`（可以留在同层），第 3 处 `core/context.py:999` 是
`ui.exploration.creation_params_dlg` ——**这就是那条反向边**。

所以「在矩阵里给 `core/context.py` 开一条 `orchestration` 例外」只是给 God object 找台阶。
正解是**整体下移到 `services` 层**：`services` 本就允许依赖 `persistence` / `core` /
`dungeon`，而 `ui` 本就允许依赖 `services`。归位后两侧都是**既有合法边，零新增豁免**。

### 8.2 归位表（差分的核心，逐成员）

目标包：`services/exploration/`（与既有的 `services/character_service/`、`ui/exploration/` 命名对齐）。

| # | 现有成员（行） | 目标 |
|---|---|---|
| **A** | `__init__` 的 repo/settings 聚合 `46–54`、`_filter_styles` `99`、`selected_styles`/`selected_quip_styles` `57–66`、`merged_landmarks`/`_landmark_styles` `69–74`、`quips`/`detail_pools` `75–76`、`state_service`/`name_repo`/`creation_service`/`news_service`/两张表 `78–90`、`comparison_count`/`comparison_order`/`selected_parts`/`world_setting`/`reverse_details_order` `93–97`、`reload_merged_data` `104`、`update_world_setting` `114`、`update_name/preset/personality/news_table` `118–135`、`get_landmark_count`/`get_quip_counts_by_size` `1106–1117`、`apply_context_settings` `1121`、`update_styles` `1132` | `services/exploration/catalog.py` :: `ExplorationCatalog` |
| **B** | `_generate_report_data` `475–702` | `services/exploration/report.py` :: `ReportEngine` |
| **C** | `_landmark_full_address` `309`、`_durable_ok` `319`、`_quip_allowed_styles` `327`、`_prune_quips_by_styles` `342`、`_shift_position_cell` `357`、`_plan_address_comparisons` `364–473`、`stuck_options` `284` | `services/exploration/address_plan.py` :: `AddressPlanner` |
| **D** | `_build_report_text` `704–765`、`_build_detail_text` `767–772` | `services/exploration/report_text.py`（模块级纯函数） |
| **E** | `character_from_core_or_report` `775–852`、`_detail_selected_parts` `854`、`size_unlocks_from_report` `857`、`_init_size_unlocks_from_report` `866`、`_apply_size_unlocks_from_report` `891`、`dungeon_data_from_any` `923–981`、`ensure_avatar_for_state` `984`、`ensure_avatar_for_state_id` `1017` | `services/exploration/character.py` :: `CharacterAssembler` |
| **F** | `build_export_card_data` `1056`、`build_export_card_from_state` `1078` | `services/exploration/export.py` |
| **G** | `load_character_state` `1026`、`prepare_news_for_character_load` `1043` | **留在外观**（见 §8.4） |
| **H** | `report_from_core_or_character` `139–282` | **留在外观**：它是**编排入口**（负向演化 / 扣行动点 / 写 `character_repo` / stuck 循环 / 归还点数），不是内容生产；它串起 B + D + E |

**注意 D 与 B 的分界**——这是本方案里唯一容易搞错的地方：
`_generate_report_data`（B）**是**报告引擎，产出的是结构化数据（quip 结果、伤亡、坐标、
地址规划），**不是**渲染；`_build_report_text` / `_build_detail_text`（D）**才是**把数据
拼成给人看的文本（`[STRIKE]` 标记、`QUIP_LINE:` 前缀、emoji、`═` 分隔线）——D 是**呈现**。
B/D 都"严格上不能算 `core` 内容"：B 依赖 `settings` + 从 `quip_repo` 读的 `load_meta` /
`load_style_registers`，D 虽然只依赖 `settings` + `format_size`（**看起来最像 `core`，
这也是它最容易被误放进 `core` 的原因**），但它产出的是报告工件而非领域模型。

依赖方向核对（全部落在既有允许集内，实测无新越界）：

- `catalog.py` ← `persistence`（8 个 repo）+ `core.logic` 常量 ✅
- `report.py` ← `catalog` + `address_plan` + `services.state_service` + `core.logic`
  （`get_comparisons` / `select_quip_with_budget` / `replace_quip_tags` / `compute_*` 等 8 个函数）✅
- `address_plan.py` ← `landmark_repo` + `core.address_model`（`resolve_full_address` /
  `world_of` / `depth_of` / `distance_m` / `touches` / `cell_width_m` / `can_pair` /
  `jitter_address_cell`）✅
- `character.py` ← `character_repo` + `core.logic`（`build_size_description` /
  `apply_size_unlock_updates`）+ `core.models` ✅
- `export.py` ← `services.creation_service` + `services.image_service` + `core.models` ✅

### 8.3 唯一的反向边：`ensure_avatar_for_state` → ui（**已按方案 (A) 切掉**）

`core/context.py:999` 延迟 import `ui.exploration.creation_params_dlg.render_body_preview_to_file`，
用来把身材预览渲染成 PNG 当头像。**整个仓库只有这一处 `orchestration → ui`。**
现在它改为 `services.body_preview.render_body_preview_to_file`（搬家后落在
`services/exploration/character.py` 里，仍是函数内延迟 import，避免把 tkinter/PIL
拉进模块顶层）。

`services/body_preview.py:959` 有**同名、同签名、同语义**的实现
（`render_body_preview_to_file(body_parts, height, out_path) -> str`），两者是**各自独立的
绘图拷贝**（ui 侧 1523 行 / services 侧 968 行，§4 已记）。当时给的两条切法：

- **(A) 换调用** —— **采这条，已实测通过**：同一外观模式下亮/暗各 4 组身高，
  两侧输出 **PNG 逐字节一致**（不是"看着差不多"，是 sha 相同）。**必须先同步外观模式**：
  ui 那份读 `ctk.get_appearance_mode()`，services 那份读 `ui.common.appearance.is_dark()`，
  而 `appearance` 默认 `Dark`、ctk 默认 `Light`；不对齐就会得到"不一致"的假结论。
  应用里两者由同一 `theme_mode` 驱动（`main.py:202` / `main.py:259`），所以生产环境是同步的。
- **(B) 注入端口** —— 未采用。

**顺带修掉的一个隐性 bug**：挂件模式**从不**调用 `ctk.set_appearance_mode`，所以 ctk 一直
停在默认 `Light`。原实现读 ctk → 挂件下**深色主题也渲染浅色剪影**。改用 `appearance`
（其 docstring 自称"外观模式的唯一来源"）之后，挂件头像终于跟着主题走。
`ui/exploration/creation_params_dlg.py` 里那份 UI 副本保持不动（合并两份实现是独立一步，§8.7）。

### 8.4 外观（Facade）策略：先保 API，后收紧（**已按此执行**）

阶段 3.1 **只做抽取，不改 ui 调用点**：`ExplorationContext` 保留现有的方法名与属性名
（`@property` / 转发方法），内部持有 `catalog` / `_addresses` / `_reports` / `_character`。
三个理由：

1. ui 侧调用点 50+ 处先不动，改动面收敛在 `context.py` + 新模块，review 成本可控；
2. 8.5 的表可以**逐个提交**，每步都能单独跑 `run_checks` 回归；
3. 专业模式与挂件两条 UI 都不必同时改（`context.selected_styles` 这类属性访问量最大）。

⚠️ **两个属性的 setter 不能省**：`ui/challenge.py:570,571,596,597` 会**直接赋值**
`context.selected_styles` / `context.selected_quip_styles` 再调 `reload_merged_data()`。
若只写成只读 `@property`（或改用 `__getattr__` 透传），赋值会**悄悄创建实例属性**盖住
目录里的真值，随后 `reload_merged_data()` 读到旧值——静默失效，不报错。

后续（阶段 3.x，可选）再让 ui 直接持有子系统（`ctx.catalog.selected_styles`、
`ctx.reports.generate(...)`），把外观压薄甚至删掉。**这一步不要和后述迁移混在一个提交里。**

### 8.5 提交切分（方案原文；**实际执行顺序见 §8.8**）

| 步 | 内容 | 为什么排这个位置 |
|---|---|---|
| **S1** | 建 `services/exploration/`，先搬 **E 角色装配** + **F 导出** | 这两块本来就在 `services` 允许的依赖范围内，搬完**层归属不变**（外观仍在 `orchestration`），是最小风险的"试水" |
| **S2** | 搬 **B 报告引擎** + **C 地址规划** + **D 报告文本** | **本次的核心动作**。⚠️ 红线复核：B 只 **import** `core.logic` 的 8 个函数，**`@behavior_hook` 的 scope 一个字都不改**（§5.1） |
| **S3** | 搬 **A 目录/配置** | 最后搬它：调用点最密（`selected_styles` 等），但换了之后外观的属性转发一并落地 |
| **S4** | 切反向边（§8.3 的 (A) 或 (B)） | 必须在 S5 之前：`services` 层不许碰 `ui` |
| **S5** | `core/context.py` → `services/exploration/context.py`；删 `orchestration` 层 | 收口。改 8 处 `from core.context import ExplorationContext`（`main.py` / `main_window_manager.py` / `ui/exploration/{exp_frame,creation_params,giantess_state}.py` / `ui/mini/params_panel.py` / `tests/{smoke_test_mini,smoke_test_switch}.py`） |

**S5 的守卫同步清单**（漏一个就会 FAIL 或静默失效）：

- `tests/check_import_graph.py`：
  - 删 `_layer_from_parts` 里 `core/context.py → orchestration` 的特例；
  - `ALL_LAYERS` 与 `BASE_ALLOWED` 同时删 `orchestration`（`check_config()` 断言
    `ALLOWED` 键集 == `ALL_LAYERS`，**必须同改**，否则守卫自己判配置错误）；
  - `ui` 的允许集里删 `orchestration`；
  - `orchestration` 的允许集整条删除；
  - `PACKAGE_LAYERS` 不用动（`core` 仍在，只是不含 context 了）。
- `core/__init__.py` 的包文档（现写着「`core/context.py` 语义上属于 orchestration」）；
- `README.md`（「仓库结构约定」里那句 `core/context.py` 特例）；
- `docs/refactor_plan.md` §1 分层表与 §8 自身（搬完把本节标记为"已落地"）。

### 8.6 验证不变量

**本次不是纯位移**，所以 §6「依赖边总数不变」这条不适用。替换成三条判据：

1. `tests/run_checks.py` 的**越界数恒为 0**（S5 后再无 `orchestration` 层打印出来）；
2. `core/` 目录里**不再出现** `persistence` / `services` / `ui` 的 import
   （一条 `grep` 即可断言：`grep -rn "from \(persistence\|services\|ui\)" core/`）；
3. **报告输出逐字节不变**：同一种子 + 同一角色/设置，比对拆分前后的 `report_text` 与
   `detail_text`。做法（拆分**前**先做一次，把基线存下来）：

   ```python
   import random, json
   random.seed(20261006)            # _generate_report_data 里用到 random.random / randint
   report = ctx.report_from_core_or_character(core, styles, quip_styles)
   baseline = {"report_text": report.report_text, "detail_text": report.detail_text}
   ```

   D（文本拼装）是纯函数，B 在固定种子下确定 → 可以逐字节比对。**每步 S1–S4 都跑一次**。
   注意 `quip_results` 里有 `random` 影响的坐标推进，所以**必须先 `random.seed`**，
   且比对时保持 `data/` 里的风格/描述内容不变。

GUI / 集成回归（`--smoke`，两个 GUI 自检**不可并发**）：

- `tests/smoke_mini.py`（挂件全链路 + 真实副本窗口）：它是唯一会走
  `context.update_styles` / `get_comparisons` / `character_repo` 的离线-在线混合自检；
- `tests/smoke_switch.py`（热切换 5 轮 38 项）：`ExplorationContext` 的构造点在
  `tests/smoke_switch.py:104`，改签名（§8.3(B)）会打到它；
- `scripts/dungeon_autopilot.py --in-process`（连跑 8 局）：**迁层不影响它**，但 S2 动了
  报告引擎，建议至少跑一轮。

### 8.7 不在本步范围内的相关项（避免一次动太多）

- 不拆 `core/logic.py`（§3 第 2 条；它自己的 `@behavior_hook` 红线更硬）；
- 不合并 `services/preview`（原 body_preview）与 `ui/exploration/creation_params_dlg.py` 的**两份预览
  实现**（§4 已记）——S4 若走 (A) 会顺带换掉其中一处调用，但**合并实现**是独立一步；
- 不动 `services/image_service.py`（例外 #8，`ui → services → ctk` 绕行链）——它属于 §3 第 3 条；
- `A` 里的 `state_service` / `creation_service` / `name_repo` / `news_service` 是**持有**
  而非**拥有**：`ExplorationCatalog` 只把它们聚在一起供上层取用，生命周期不变。

### 8.8 实际执行记录（2026-10-06）

**落地的模块**（`services/exploration/`，全部新建；`context.py` 由 `core/context.py`
`git mv` 而来，历史可 `--follow`）：

| 文件 | 内容 | 对应方案 |
|---|---|---|
| `catalog.py` | `ExplorationCatalog`：8 个 repo + settings 聚合、风格选择、合并数据、核心服务句柄、常用配置 | A |
| `report.py` | `ReportEngine.generate()` | B |
| `address_plan.py` | `AddressPlanner`（`landmark_full_address` / `plan_address_comparisons` / `stuck_options` / …） | C |
| `report_text.py` | `build_report_text` / `build_detail_text`（模块级纯函数） | D |
| `character.py` | `CharacterAssembler` | E |
| `export.py` | `build_export_card_data` / `build_export_card_from_state` | F |
| `context.py` | 薄外观：编排入口 + 状态管理 + 全部转发 | G/H |

**两处顺序调整**（方案 §8.5 的排序有两处内在矛盾，实际执行时改掉了）：

1. **S4 必须提到 S1 之前。** 方案把切反向边排在 S1 之后，但 **S1 要搬的 E 组里就含
   `ensure_avatar_for_state`**——它那时还在延迟 import `ui.*`。搬进 `services` 的瞬间，
   守卫会看到一条 `services/exploration/character.py -> ui.exploration.creation_params_dlg`
   的未登记越界并 FAIL（`services` 的允许集不含 `ui`）。所以先切边、再搬 E。
2. **S3（catalog）必须提到 S2（报告引擎）之前。** `report` / `address_plan` 要**实时**读
   `selected_styles` / `merged_landmarks` / `quips` / `detail_pools`，而这些东西会被
   `update_styles()` / `reload_merged_data()` **整体替换**。它们不能拷成自己的字段（必然陈旧），
   只能持有那个共享的 `ExplorationCatalog`——所以目录得先存在。方案把 A 排最后是出于
   "调用点最密、review 成本高" 的考虑，但那是**属性转发**的成本，与 A 什么时候搬家无关。

实际执行顺序：**S4 → S1 → S3(catalog) → S2(report/address/text) → S5**。
每步都跑了 `tests/run_checks.py`（全程 13/13）与报告逐字节基线（全程 `REPORT IDENTICAL`）。

**实测结果**

| 判据 | 结果 |
|---|---|
| `tests/run_checks.py` | 13/13 ✅ |
| `tests/check_import_graph.py` | PASSED，矩阵里已无 `orchestration` ✅ |
| `core/` 的越界 import | 只剩 1 条已登记例外（`behavior_runtime → persistence.world_pack`）✅ |
| 报告 `report_text` / `detail_text` | 与拆分前**逐字节一致** ✅ |
| `tests/smoke_mini.py` | 全部通过 ✅ |
| `tests/smoke_switch.py` | 47/49（2 项为陈旧断言，见 §7 第 7 条，与本重构无关）⚠️ |
| `scripts/dungeon_autopilot.py --in-process` | PASSED 100/100 ✅ |
| 全仓 `compileall` | 通过 ✅ |

依赖边总数：560 → 584。**这不是"无意中增删了依赖"**——拆出 7 个新文件，`context.py`
原先写在文件内的那些 import 现在分别记在新模块名下，边数自然上升（§6 的"边数不变"
只适用于纯位移）。

代码量：`core/context.py` 1136 行 → `services/exploration/context.py` 447 行（薄外观），
其余按职责落到 6 个模块。

**顺手做的三件小事**（都在本步范围内，已说明理由）：

1. 顺手修掉 `svc = state_service or StateService` 这个"回退到类"的写法 → 改为回退到
   `self.state_service` 实例。原写法能跑只是因为 `StateService` 的方法全是 `@staticmethod`，
   语义相同但难以理解；改后也不必再 import 那个类。
2. `ExplorationCatalog` 里把 `__init__` 与 `reload_merged_data()` 逐字重复的那段合并成
   `_load_merged_data()`——原先改一处忘一处就会让"启动时读风格"与"设置里改风格"产生分歧。
3. 上述"切反向边"顺带修掉了挂件模式下的明暗剪影 bug（见 §8.3）。

**没有做**：§8.7 列的四项（拆 `logic.py`、合并两份预览实现、动 `image_service.py`、
收紧外观 API）都原样待办。

