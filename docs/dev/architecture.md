# 架构与分层（architecture）

> 讲清楚这个仓库**现在**长什么样、哪些契约不能碰、怎么安全地动它。
> 文中所有数字均为实测。

按需求查：

| 你想… | 去哪                 |
|---|--------------------|
| 弄清分层 / 守卫 / 目录现状 | §1 现状              |
| 改代码前核对红线 | §2 硬约束             |
| 搞清某个结构**为什么**这样 | [legacy/refactor-2026-10.md](legacy/refactor-2026-10.md) |
| 动手搬代码 / 新增包 / 加删守卫 | §5 操作手册            |
| 提交代码 | §6 提交规范            |

---

## 1. 现状

### 1.1 分层与依赖方向

依赖只能向下（上层可用下层），由 `tests/check_import_graph.py` 强制。

| 层 | 位置 | 内容 | 可以依赖 |
|---|---|---|---|
| `infra` | `paths.py` | 路径解析 | 无 |
| `core` | `core/` | 领域模型与共享基础：`models` `address_model` `logic`（子包） `behavior_runtime` `ai` `appearance` `imaging` `scale_reference` | `infra` |
| `dungeon` | `dungeon/*.py` | 副本引擎核心：方案定义、运行规则与 Run 状态（schema / terms / chapters / rules / validate / models …） | `infra` `core` |
| `persistence` | `persistence/` | 仓库层 | + `dungeon` |
| `services` | `services/` | 服务层；按子域分包（`exploration` 探索编排 / `chat` 聊天 / `worlds` 世界包 / `challenges` 挑战包 / `character` 角色长时行为 / `news` 近况 / `preview` 剪影渲染），顶层只留行为包契约服务 `creation_service` / `state_service` 与 `ui_mode`（§2.5） | + `persistence` |
| `dungeon_window` | `dungeon/window/**` | 副本会话窗口运行时（Session：窗口 / 帧循环 / 宿主端口；imgui-bundle） | + `services`；**禁** `tkinter` / `ui.*` |
| `ui` | `ui/` | 专业界面（CTk）+ 挂件界面（原生 tkinter）；`ui/hosting/` 是两模式共用的**进程与宿主基础设施**（副本子进程父/子两侧、`tk_host` 宿主桥、`x11` 支持、`mini_game_host`），包规约：不顶层 import CTk 组件，弹框一律注入 | + `dungeon_window` |
| `app` | `main.py`（字面量入口，留根目录）+ `app/`（`shell` / `window_manager`） | 入口与应用壳 | 全部；**不被任何人依赖** |

`dungeon` 是仓库内一个**逻辑子项目**的引擎核心，不是普通功能目录；`dungeon_window`
是它的窗口呈现运行时，`persistence.scenario_repo` / `ui/scenario/` / `ui/hosting/` /
`services` 的副本适配是宿主侧集成。三者同仓库、同版本、不单独发版。术语上
`Session` 是一次窗口生命周期，最多承载一局 `Run`；详见
[Dungeon 术语表](../Dungeon/domain_terms.md)。

**根目录已冻结**：只剩 `paths.py` 与字面量入口 `main.py` 两个文件（构建脚本写死了
`main.py`，不能搬走）。新增根目录 `.py` 会被守卫直接判失败（`ROOT_MODULE_LAYERS`
既是层归属表，也是白名单）。

### 1.2 三道守卫的分工

| 脚本 | 管什么 |
|---|---|
| `tests/check_import_graph.py` | **全局层间方向** + 下层禁 UI 框架（本仓最核心的一道） |
| `tests/check_dungeon_layering.py` | dungeon 内部：领域层 / 窗口层 |
| `tests/check_mini_layering.py` | 挂件层禁 CTk、禁 `ui.ctk.{theme,fonts,widgets,dialogs}` |
| `tests/check_entrypoints.py` | 构建脚本的入口 / 图标 / `--add-data` 源路径存在性 + 全仓悬空 import |

另有 `check_scenario_schema.py` 守住副本方案字段的**声明 ↔ 产出 ↔ 校验器**（`schema`
→ `normalize` → `validate`）；音频端到端链（说话人分档 / 三条引擎路径 / 降级 /
播放收尾）在 `check_audio_chain.py`。加字段时别漏，见 §5.4。

### 1.3 当前实测数字（2026-10-27）

```
[check_import_graph] 已检查 198 个模块，798 条第一方依赖边
[check_import_graph] PASSED：层间依赖方向与 UI 框架禁令均无未登记越界
KNOWN_EXCEPTIONS = []          # 原 11 条已全部消除
tests/run_checks.py            # 55/55
```

---

## 2. 硬约束（违反**不会报错**，只会静默失效）

### 2.1 `@behavior_hook` 的 scope 不是模块路径 ⚠️ 最高优先

`core/logic/`（四子模块合计）里有 11 处 `@behavior_hook("logic", ...)`，另有 1 处
`behavior_const("logic", "PREDEFINED_TAGS", ...)`。`behavior_hook(scope, name)` 把 key
拼成 `f"{scope}.{name}"`，**scope 是写死的字面量，与文件在哪个模块无关**。

这个 key 是**已部署世界包行为包的公开契约**：

```
data/static/behaviors/imperial_units/imperial_units.py:45
    runtime.override("logic.format_size", format_size)
```

所以搬家、重命名 `core/`、把 `logic.py` 拆成多个文件——**`"logic"` 这个字面量一个字都不能改**。
改成 `"core.logic.format_size"` 会让用户已安装的行为包**静默失效**（`resolve()` 返回 None，
静默回退默认实现，不报错、不告警）。激活时的启用诊断（`load_pack` 产出
`BehaviorLoadReport`）只能拦住行为包**写错 key** 这一类失效；运行时对未命中 key
的回退语义不变，契约字面量仍然一个字都不能改。

每个 hook 还带 `layer` / `contract` / `since` / `deprecated` 元数据，
`runtime.catalog()` 可列出。`layer` 按语义所有权划分，不按目录：基础设施
算法点 / 模型参数、模块决策策略、内容数据。离线恢复开放为 `offline` 组
（`recover_plan` / `report_text`，执行走 `OfflineService` 直连）；报告选取
仲裁开放为 `report` 组（车道 / 联合分 / 提拔与仲裁 `report.model.*` 常量），
硬闸与物理走查不开放；报告引擎的伤亡 / 耐久参数同为 `report.model.*`
常量（突破缩放、耐久伤害系数、残破阈值、无事件兜底）。世界包在 world.json
用 `behavior_api`（整数，未声明按 1）声明面向的契约版本，与运行时
`BEHAVIOR_API_VERSION` 不符时仅在启用诊断中警告；导航栏悬停世界包信息可
展开完整诊断。

### 2.2 `data/` 是用户数据区

- **禁止在 `data/` 下用通配符删除**。`data/` 大部分文件不受 git 跟踪，删了无法恢复，
  Git Bash 的 `rm` 也不进回收站。
- 清理测试产物一律「移到系统临时区」：`shutil.move(path, tempfile.mkdtemp())`
  （见 `tests/smoke_mini.py::_discard`）。

### 2.3 下层禁 UI 框架的口径（PIL 只禁两个桥）

`infra` / `core` / `persistence` / `services` 不得引入 `tkinter` / `_tkinter` /
`customtkinter` / `imgui_bundle` / `dearpygui`；**`PIL` 不在整包禁列**，只禁 `PIL.ImageTk` / `PIL.ImageGrab`
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
- `ui/common` 起初只放专业模式窗口组件，后来混进了与控件无关的进程/宿主机制。2026-10-11
  按依赖拆出 `ui/hosting/`（`x11` `x11_boot` `tk_host` `dungeon_spawner`
  `dungeon_child_host` `mini_game_host`——全部零顶层 CTk 依赖，被两种模式与 main 共用；
  `splash` 因本身是 CTk 窗口留在 CTk 组件侧），并把 `dungeon_spawner` / `tk_host` 的弹框实现改成
  调用方注入（缺省 CTk 版延迟到首次使用才 import）。挂件白名单由此从「点名豁免三个
  例外模块」收敛为整包放行 `ui.hosting`（`tests/check_mini_layering.py`）。
  同日 `ui/common` 其余部分（全部依赖 CTk 的专业组件）整体更名 `ui/ctk`，
  `ui/common` 名号退役——此后「专业组件住 `ui/ctk`，进程宿主住 `ui/hosting`」。

新增越界**不许直接塞进 `KNOWN_EXCEPTIONS` 就走**——先想清楚能不能靠"按依赖定层"解决。

### 2.5 行为包契约的 import 路径不能改名 ⚠️

钩子 key（§2.1）与模块路径是**两回事**，但后者同样是契约：`docs/designs/behaviors/dev-guide.md`
的示例教行为包作者写 `from services.creation_service import CreationService`，
`core/behavior_runtime.py` 的模块文档也引用了它——已部署行为包（用户数据区，git 不可见）
可能照此 import。因此 **`services/creation_service.py` 与 `services/state_service.py`
两个文件不得改名、不得收编进子包**（除非留 shim，那是一笔永久的税，别轻易决定）。
`services/` 整理（2026-10-06）据此把它们留在顶层，理由记录在 `services/__init__.py`。
出身烙印基线（2026-10）删除 `spatial.TAG_*` 常量钩子后，`spatial.py` 的模块
路径不再承载行为包契约（先验调制改经 `CreationService.derive_model_tuning`），
但其公开名（`build_spatial_profile` 等）仍是 services 内部的直连入口契约。

### 2.6 档案派生层（rhythm / spatial / profile_text）

`services/character/` 下的 `rhythm.py`、`spatial.py`、`profile_text.py` 是一族
**无状态派生视图**，与其他 service（有状态编排、触仓储）不同：全部从演化表
（`EvolutionRecord` 行集）即时推导，不持久化、不设单例、不做 IO，`now` 一律
可注入（测试用固定时钟）。约定：

- 只依赖 core（`models` / `address_model` / `behavior_runtime`），不碰
  persistence、不依赖其他 service 子域；
- 消费方（exploration 的仲裁数值、chat / archive_export 的文本）按 services
  子域直连 import，一律从层内装配入口取数（`build_spatial_profile` /
  `RhythmService().learn_from_evolution`），不各自复刻学习逻辑；
- `profile_text.py` 的四个入口是行为包钩子，scope 为字面量 `"profile"`
  （§2.1 同款契约）；空间 / 环境先验的调制经创建期烙印
  （`CharacterSnapshot.model_tuning`，由 `CreationService.derive_model_tuning`
  派生）进入 `build_spatial_profile`，spatial 模块只保留 TAG 默认表与学习层，
  三层开放模型与「学习层不开放」的边界见
  `docs/designs/behaviors/dev-guide.md`。

### 2.7 资源类型清单只有一份声明

「资源」指成组、可替换、可打包的内容（地标风格、描述风格、副本方案、挑战包、
行为包、小游戏、四张静态表）。**类型清单只写在 `core/resource_spec.py`**：
容器形状、主文件模板、字段清单、id 规则、存储根、设置键、界面显示名与选择粒度。

派生出口是 `persistence/resources/registry.py`，其余模块一律从这里取值：

- 包内目录（`PACK_RESOURCE_PATHS`）、可枚举类型（`LIST_RESOURCE_TYPES`）、
  资源域设置键（`RESOURCE_SETTINGS_KEYS`）、界面显示名与多选/单选分组；
- 路径定位：`free_dir` / `free_path` / `read_dir` / `read_path` / `addr_path`，
  不要自己拼 `os.path.join(data_dir, "packs", ...)`；
- 校验：`validate_resource()` 是唯一判定出口，成员核对是 `missing_members()`。

新增一类资源 = 一份声明 + 一个 repo + 一处界面接入。再写第二份类型清单会让
「登记了但没人读」和「读了但没登记」同时出现，`tests/check_resource_spec.py`
会拦。资源 id 的全局规则是小写字母/数字/`._-`，类型可声明更宽或更严的
`id_pattern`（行为包要能当 Python 标识符）；`WorldPackManifest.validate()`
对**所有**类型都按各自规则校验 id，不留旧名宽松分支。

元数据走兄弟文件 `<id>.meta.json`（`persistence/resources/envelope.py`），
与既有的 `<id>.addr.json` 同构：字段全可选、读取侧补默认值不写回、未知字段保留、
`content_hash` 判定「本地改过没有」。列目录一律走 `registry.is_resource_entry()`
（伴随文件、`.bak`、原子写的 `.tmp` 都由它排除），不要自己写 `endswith(".json")`。

**单文件 JSON 容器的主文件不带扩展名**：`landmarks/chinese_mix`、`quips/events`，
路径本身就是资源地址。理由是三份文件挤在同一层的目录里时，`x.json` +
`x.addr.json` + `x.meta.json` 让「按名浏览」和「按类型浏览」看不出区别；去掉主文件
后缀后，只有内容和它的伴随文件，两类视图才各有意义。需要外部程序认领的容器仍带
后缀：`.csv`（表格软件）、`.chal`（整包）、`.py`（Python 包），目录型容器的主文件
在目录内（`<id>/config.json`、`<id>/manifest.json`），不在此列。

代价是编辑器不再按后缀识别语言：.vscode 未提交关联配置，用 VS Code 直接看这些
文件时需手动 `Change Language Mode → JSON`（或用 `files.associations` 关联
`data/packs/landmarks/*`）。跨过这一层，`<id>` 与 `<id>.addr.json` 谁是需要编辑的
内容一眼可辨。

两个曾经破例的数据形状已在一次性迁移（`scripts/migrate_resource_ids.py`）里改掉：

- `news` 的表头用字段键（`size_category` / `memorial` / `intrusion` /
  `destruction` / `text` / `substitute_1` / `substitute_2`），不再是中文列名，
  列清单与顺序都取自声明；
- `quips` 的坐标写成行数组 `{"intrusion": 1, "destruction": 1, "items": [...]}`，
  正文里的 `[summary:…]` 标记改为条目自带的 `summary` 字段。`QuipRepo.load()`
  对外仍返回 `{(介入度, 破坏性): [条目]}`，格式变化不溢出到调用方。

---

## 3. 历程索引（已移至 legacy/）

本仓 2026-10 的结构性重构（阶段 0–4.3）做过什么、关键验证是什么，是**历史**，
已移入 [legacy/refactor-2026-10.md](legacy/refactor-2026-10.md)。
要搞清「某个结构**为什么**这样」时去那里查；结论若仍生效，已并入本文件 §1–§2。

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
6. 同步文档：`README.md` 的「开发与架构（速查）」、`core/__init__.py` 之类的包文档、本文件。

### 5.2 自检怎么跑

| 命令 | 用途 | 注意 |
|---|---|---|
| `python tests/run_checks.py` | **离线 44 项，提交前必跑** | 纯静态，不需要显示器 |
| `python tests/run_checks.py --smoke` | 追加 GUI 冒烟（3 个） | **两个 GUI 自检不可并发** |
| `python tests/smoke_mini.py` | 挂件全链路（75 项）+ 真实副本窗口 | 需显示器 |
| `python tests/smoke_switch.py` | 界面热切换 5 轮 | 改切换 / 收尾逻辑后必跑；当前 50/50 |
| `python scripts/dungeon_autopilot.py --in-process` | 15 场景 **109 项**，最强回归 | stdout 在报告文件里，别只看终端 |

`run_checks` 按前缀自动发现：`check_*.py` 离线、`smoke_*.py` 需显示器。新增脚本放进对应
类别即可，无需登记清单。

### 5.3 界面热切换与副本窗口

改 `app/shell.py` 或副本收尾逻辑前，**先读 `docs/Dungeon/window.md` §5-C1 / §5-C2**
（两节都已改写成 imgui 版）：进程内主动关窗一律走 `_request_close()` → 帧边界落实
（C1）；窗口的 GL / 上下文 / 视口生命周期全交给 `hello_imgui.run()`，进程内不手动
建 / 毁上下文、不需要保活视口（C2）。子进程形态下的隔离面由 `tests/run_checks.py`
里的 `check_dungeon_window_contract.py` 机械守住。

改完必须跑 `python tests/smoke_switch.py`，日志里 `invalid command name` 条数应为 0。

### 5.4 给副本方案加字段

`schema.py` 声明 → `chapters.py` 的 `normalize_*` 接入 `normalize_chapter` → 领域能力模块
（如 `dungeon/audio.py`）→ `window/` 接线（生命周期 + 进入章节时应用）→ `validate.py` 诊断 →
编辑器 UI（导入 + 编辑区 + 列表列）→ **GUI 冒烟 / 人工核对**。离线守卫只到
`validate` 与音频链行为（`check_scenario_schema.py` / `check_audio_chain.py`）；
编辑器接线漏了不会离线报错，但会静默失效。详见 `docs/Dungeon/script.md`。

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

## 7. 拆 God object 的可复用拆法

阶段 3.1 拆 `ExplorationContext` 时总结的拆法（分类成员 → 划清数据/呈现 →
整层搬走 → 切反向边 → 外观先保 API → 每步跑回归）已移入
[legacy/refactor-2026-10.md](legacy/refactor-2026-10.md)。
