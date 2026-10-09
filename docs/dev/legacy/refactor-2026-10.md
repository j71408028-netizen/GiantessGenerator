# 重构历程：2026-10 结构与 God object 拆分

> **只读 · 仅供溯源**：本文是 `architecture.md` 抽出的**历史索引**——
> 一次结构性重构（阶段 0–4.3）做了什么、以及拆 God object 的可复用拆法。
> 现状与硬约束一律以 [architecture.md](../architecture.md) 为准；
> 本文的结论（如「按依赖定层」）已在现状文档 §2.4 生效，本文只留过程。

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
| **4.2** 整理 `services/` | `d5e2b9e`…`2d97d01`（7 个提交） | 十个散文件按域归位：`scale_reference` 上浮 `core/`；`chat/` 收编 persona / experience_events；建 `worlds/`、`challenges/` 两域包（`helpers.py` 拆散删除）；`news` 独立附加功能包；`character_service` 改名 `character`（边界收窄）；`services/__init__` 门面清零改层说明。`creation_service` / `state_service` / `ui_mode` 留顶层（行为包 import 契约 / 3.2.2 判例） | 每步 grep 零残留 + 13/13；边 595 → 592（删 4 条门面死边）、模块 154 → 157（新门面 / imports 文件登记）；收口：smoke_mini 全部通过、autopilot --in-process 100/100 |
| **4.3** `app/` 包 + smoke_switch 断言修复 | `8f0229a`…`859e880`（2 个提交） | `app_shell.py` → `app/shell.py`、`main_window_manager.py` → `app/window_manager.py`，`main.py` 留根目录（构建脚本字面量入口）；守卫 ROOT_MODULE_LAYERS 删两项、PACKAGE_LAYERS 加 `"app": "app"`。顺带修 smoke_switch 两条陈旧断言（设置页「启动界面模式」写 `ui_startup` 不碰 `ui_mode`；按钮文案「⇄ ME模式」） | grep 零残留 + 13/13；边 592 不变、模块 157→158；`smoke_switch` **50/50**（47/49 基线回满，断言拆细后总数 +1）、`invalid command name` 0 条 |
| **顺带** | `93de7ae` | `ui/settings/`、`ui/quip/`、`ui/landmark/`、`ui/challenge/`、`services/chat/`、`services/preview/`、`dungeon/audio/` 包内分组；`tests/` 命名统一为 `check_*` / `smoke_*` | — |

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

## 7. 附：拆 God object 的可复用拆法（阶段 3.1 的经验）

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
