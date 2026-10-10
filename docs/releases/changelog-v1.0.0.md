# v1.0.0 更新说明（相对 v1.0.0-preview）

本文记录正式版 `v1.0.0` 相对预览版 `v1.0.0-preview` 的全部变更，供发布说明与后续版本参考。

| 项目 | 预览版 | 正式版 |
|---|---|---|
| 标签 | `v1.0.0-preview`（轻量标签） | `v1.0.0`（附注标签） |
| 定型提交 | `2404e7a` | `9785356` |
| 日期 | 2026-08-19 | 2026-10-09 |
| 版本号（`paths.APP_VERSION`） | `1.0.0 preview` | `1.0.0` |
| 提交差距 | — | 领先 8 个提交，**无分叉** |

> 预览版是正式版的**直接祖先**：`v1.0.0` = `v1.0.0-preview` + 8 个提交。
> 变更规模：**31 个文件、+2368 / −422 行**（不含新增文件的纯增量另计）。

期间提交（时间顺序）：

| 提交 | 日期 | 说明 |
|---|---|---|
| `365679c` | 08-25 | 分离 API 密钥存储；开发者可用静态资源形式存放行为包 |
| `255642a` | 08-25 | 新增英制单位行为包示例；修复启用世界包时静态资源被禁用的问题 |
| `f521341` | 08-27 | 副本与档案修复 |
| `02731a0` | 08-27 | Revert `255642a`（二次返工，见 `652ddb0`/`6459699`） |
| `652ddb0` | 08-27 | `255642a` 的恢复稿 |
| `6459699` | 08-27 | 恢复稿的修订 |
| `befca6f` | 09-01 | 功能新增：HTML 档案导出、屏蔽词、预设权重与取值范围、角色卡导出修复 |
| `9785356` | 10-09 | v1.0.0 定稿：描述风格 `Events.json` 换新版、版本号定为 1.0.0 |

---

## 一、新功能

### 1. 角色 HTML 档案导出（单文件 MHTML 维基式档案）

新增 `services/archive_export.py`（1344 行），把角色文件夹内容打包成**单个 HTML 文件**
（`export_character_mhtml`）：

- 图片以内嵌 `data URI` 封装，脚本与样式全部写在同一文件内，**可被任意浏览器直接打开**，无需外部资源。
- 亮 / 暗双主题可平滑切换；支持在尺寸 / 报告 / 回放中搜索定位。
- 视觉构成：可自动折叠的加高顶栏（滚动进度条 + 滚动高亮）、磨砂质感背景、低对比卡片、
  按修改时间排序的形象图墙与灯箱、双栏报告阅读器（左侧时间索引、右侧正文）、按类型着色的回放时间轴。
- 分区：信息盒（infobox）、分析区（含按比例着色的条形图）、尺寸区、报告区、回放区、结局区。

对应导出入口（`ui/exploration/giantess_state.py`）由此从"提示未实现"改为真正实现：

- 默认扩展名改为 `.html`，默认文件名为角色名；
- `.html` / `.mhtml` / `.mht` / `.htm` 一律导出为 HTML 档案；
- `.json` / `.chara.json` 仍导出角色卡。

### 2. 屏蔽词

新增可配置的**屏蔽词**机制，作用于地标匹配、描述选取与副本提示词：

- `logic.normalize_blocked_words()`：把输入规范为字符串列表，字符串按逗号 / 顿号 / 分号 / 换行拆分。
- `logic.contains_blocked_word()`：大小写不敏感的子串匹配，并以 `@behavior_hook` 开放覆盖。
- `get_comparisons()` 跳过名称含屏蔽词的地标；`select_quip_with_budget()` 跳过正文含屏蔽词的描述。
- 副本侧（`dungeon/prompts.py`）同样过滤地标对比与描述。
- 设置项 `blocked_words`（默认空列表），设置面板「屏蔽词」输入框，占位文案「用逗号分隔」。

### 3. 身材 / 性格预设的「权重 + 参数取值范围」

预设表新增两列能力，让随机生成更有层次：

- **权重列 `weight`**（同时接受中文列名 `权重`）：随机挑选身材 / 性格时按权重抽取，
  全零或无有效权重时退回等概率（`persistence/static_table.py: weighted_choice`）。
- **参数取值范围**：单元格可写 `[value, spread]`、`(value, spread)` 或 `value, spread`，
  表示在 `value ± spread` 内均匀取值；无浮动时仍写标量，**向后兼容旧表**。
- 对应模型改动：`BodyPreset` / `Personality` 增加 `weight` 与 `parameter_ranges` 字段，
  以及 `randomized(rng)` 方法（取值后按各自上下界裁剪，`BodyPreset` 比例为 (1e-7, 2)，性格为逐字段物理范围）。
- `CreationService.core_from_params` 改为 `weighted_choice` 抽取 + `randomized` 抖动。
- CSV 读写随之新增 `weight` 列，并以 `format_float_parameter` 序列化范围。

### 4. 静态行为包（行为包机制升级）

行为包从"世界包内的单文件模块"升级为"**以文件夹为单位、开发态可静态存放**"：

- 新增静态目录 `data/static/behaviors/<pack>/`（`paths.behaviors_dir()`）——开发副本，
  配置世界包时可**单选**其中之一；解散世界包时也会把包内行为包转存回该目录。
- 世界包内路径改为 `behaviors/<pack>/`，`resources.behaviors` 现为**单元素列表**（一个世界包最多一个行为包）。
- 行为包目录内**每个 `.py` 都会在激活时加载**（`BehaviorRuntime.load_pack`），逐个调用其 `register(runtime)`。
- `persistence/world_pack.py` 新增 `list_behavior_packs()` / `is_behavior_pack_name()` / `resolve_behavior_source()`；
  后者兼容旧版单文件 `<name>.py`。
- 世界包创建对话框资源页把「行为包」加入单选下拉（原来行为包不在列）。

### 5. API 密钥独立存储

API 密钥从 `settings.json` 中**分离**到独立文件 `data/user/api_keys.json`：

- 新增 `persistence/settings_repo.py: AiKeysRepo`；`SettingsRepo.load()` 合并、`save()` 拆出
  `ai_configs` 写入 `api_keys.json`。
- `ai.py`、`developer_tools/quip_filler.py` 改为从 `api_keys.json` 读 `ai_configs`、从 `settings.json` 读 `ai_provider`。
- `.gitignore` 忽略 `data/user/api_keys.json`，避免密钥入库。

---

## 二、改进

- **角色卡导出重构**：新增 `ExplorationContext.build_export_card_from_state()` 与
  `CreationService.preset_from_body_parts()`（`get_body_parts` 的逆运算），
  可直接从**已加载角色**还原性格 / 身材并导出角色卡，修复"从角色导出角色卡失败"的问题
  （见 §三）。
- **未解锁尺寸的展示逻辑**：`context.py` 的 `_init_size_unlocks_from_report()` /
  `_apply_size_unlocks_from_report()` 现在与设置项 `show_all_details`（"测量所有尺寸"）联动——
  关闭时报告不展示未解锁尺寸，未提及的选中部位保持锁定（`""`），不再一律标记为 `MEASURED`。
- **世界包设置锁定语义修正**（`persistence/world_pack.py`）：
  - 世界设定 / 种子：出现在包 `settings` 中即锁定；
  - 静态表（姓名 / 新闻 / 身材 / 性格）：仅当包**实际拥有**对应资源类型时才锁定（`TABLE_SETTING_KEYS` + `owns()`）；
  - `effective_settings()` 只叠加锁定键；`WorldManager` 保存 / 激活路径同步改用 `locked_keys()`。
  - 从当前世界创建世界包时，静态表设置只在该包拥有对应资源时写入，并校正为实际打包的表名。
- **世界包创建 / 导出界面**：资源页文案与排版微调（身材 / 性格下拉 24px 高、标题行间距、
  AI 配置对话框模板按钮右对齐），单选下拉新增 `"<不配置>"` 占位处理。
- **代码整理**：`CreationService.get_body_parts` 用 `_PART_RATIO_ATTRS` 表驱动，消除重复。

---

## 三、修复

- **副本步数与属性演化顺序**（`dungeon/window/engine.py`、`triggers.py`）：
  先 `evolve_attributes` 再写入 `step_info`，`intrusion_before` / `destruction_before` /
  `custom_before` 改为捕获**演化前**状态；移除 `_finish_step` 里对 `total_steps` /
  `steps_since_trigger` 的保存—还原补丁。修正了步数计数与"演化前快照"错位的问题。
- **延迟插入晋升**：只有本段**成功生成**后才把延迟插入段晋升为下一段（原来无条件晋升）。
- **副本提示词历史健壮性**：构造最近故事片段时跳过缺 `type` 或空 `text` 的步骤，避免 `KeyError`。
- **副本状态复位**：新增 `keyword_match_given = set()` 复位。
- **角色选择面板空列表**：空状态提示 Label 改为可复用 / 销毁，修复重复加载时的标签堆积。
- **`context.py` 伤亡累计**：删去误缩进的分支，`total_casualties` 与 `casualties_evolution`
  的累加在所有路径下都执行（此前仅在 `consume_points` 分支内执行，导致某些路径漏记）。
- **静态资源被禁用**：修复启用世界包时静态资源被一并禁用的问题（`255642a`）。

---

## 四、数据与内容

- **`data/packs/quips/Events.json` 换新版**：描述风格整体替换（结构不变，仍为
  `small / medium / large / huge / colossal` 五档 + `_meta.custom_types`），
  描述条目由 **123 增至 140**（与 `9785356` 注记"140 条"一致）。
- **新增示例行为包 `data/static/behaviors/imperial_units/`**：把显示长度单位由
  米 / 千米改为**英尺 / 英里**（`format_size` + `length_unit_label` 覆盖，≥5000 m 切英里），
  内部计算仍以米为单位。
- **`data/user/settings.json`**（作者本机数据）：`ai_configs` 迁出至 `api_keys.json`；
  新增 `blocked_words`；其余为个人偏好刷新（如对比数 4→5 等）。

---

## 五、文档

- **`README.md`**：`data/` 目录结构说明重写（`archives / packs / static / user / worlds` 新语义，
  `static/` 单列"静态表"）；模块表措辞更新；"语录"统一为"描述"。
- **`world_pack_behaviors.md`**：按"目录化 + 单选 + 静态开发副本"重写，新增
  `contains_blocked_word` 覆盖目标，更新 `get_comparisons` / `select_quip_with_budget`
  签名（新增 `blocked_words` 形参），内联 `imperial_units` 示例，精简旧示例。

---

## 六、兼容性与升级注意

- **行为包**：旧版世界包中 `behaviors/<name>.py` 单文件形式仍可被识别
  （`resolve_behavior_source` 兼容），但新格式应为 `behaviors/<pack>/*.py`，且**每个世界包只允许一个行为包**。
- **预设表**：新增 `weight` 列与 `[value, spread]` 范围写法；旧 CSV（无 `weight`、纯标量）
  可正常读取，`weight` 缺省为 `1.0`、范围缺省为 `0`。
- **API 密钥**：升级后密钥移入 `data/user/api_keys.json`；首次保存设置时会自动拆分，
  旧 `settings.json` 中的 `ai_configs` 将被迁移。
- **数据目录布局**：`data/` 下的 `packs` / `static` / `worlds` / `user` / `archives`
  语义按 README 新版说明理解；`static/behaviors/` 为新增目录。

---

## 附：变更文件清单（相对 `v1.0.0-preview`）

**新增（3）**

- `services/archive_export.py`、`persistence/static_table.py`、
  `data/static/behaviors/imperial_units/imperial_units.py`

**修改（28）**

- 数据 / 文档：`.gitignore`、`README.md`、`world_pack_behaviors.md`、
  `data/packs/quips/Events.json`、`data/user/settings.json`
- 逻辑核心：`logic.py`、`models.py`、`context.py`、`ai.py`、`behavior_runtime.py`、`main_window_manager.py`
- 持久层：`persistence/__init__.py`、`persistence/world_pack.py`、`persistence/preset_repo.py`、
  `persistence/personality_repo.py`、`persistence/settings_repo.py`
- 服务层：`services/creation_service.py`、`services/world_service.py`
- 界面：`ui/settings.py`、`ui/settings_dlg.py`、`ui/exploration/giantess_state.py`、
  `ui/exploration/select_character.py`
- 副本：`dungeon/prompts.py`、`dungeon/window/base.py`、`dungeon/window/engine.py`、`dungeon/window/triggers.py`
- 开发者工具：`developer_tools/quip_filler.py`
- 路径：`paths.py`
