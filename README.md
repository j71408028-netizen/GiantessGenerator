# 巨大娘生成器

一个面向桌面端的「巨大娘」主题互动创作工具。你可以在世界中设定地标与事件，生成一位巨大化的少女角色，将她的身体部位与建筑等地标进行对比，并借助 AI 生成图文并茂的报告和副本；也可以制作角色、副本方案和挑战包，分享你的角色或世界设定。
- 披露：代码和部分素材采用AI辅助生成。

- 纯本地运行：角色、地标、描述等数据全部保存在本机数据目录（源码版为项目内 `data/`，打包版为系统用户数据目录）
- AI 辅助写作：可选接入任意兼容 OpenAI SDK 的 API 生成报告与副本文本
- 浅色 / 深色主题

## 功能一览

| 模块   | 说明                                            |
|------|-----------------------------------------------|
| 探索模式 | 测量巨大娘的身体部位、地标对比，生成探索报告或AI副本；创建角色后可拥有长期档案与聊天系统 |
| 挑战模式 | 将角色、资源打包为加密挑战包（`.chal`），挑战在其中达成副本结局           |
| 文本管理 | 管理地标、描述等文本资源，支持多风格与自定义风格                      |
| 副本编辑 | 编辑剧本方案，设定AI提示词与独特叙事功能                         |
| 设置   | 主题外观、生成参数、AI 服务商配置等                           |

## 环境要求

- Windows、macOS 12 或更新版本；Linux（X11 / XWayland 会话）见下方「Linux（X11 / XWayland）」与 [docs/linux.md](docs/linux.md)
- Python 3.10 及以上（开发环境为 Python 3.13；Linux 上 Python 3.12 / 3.14 均已实测）
- macOS 需要可用的 Tcl/Tk 图形组件。使用 python.org 安装包或 Homebrew Python 均可
- 若使用 AI 生成功能，需自行准备对应服务商的 API Key（可选功能）
- 触发器依赖关系图为可选功能，需要额外安装 Graphviz 的 `dot` 命令

### 支持平台矩阵

| 平台 | 状态 | 说明 |
|---|---|---|
| Windows 10/11 | ✅ 受支持 | 打包发行（`build/windows/build_windows.ps1`），系统自带 WebView2 支撑内置小游戏 |
| macOS 12+ | ✅ 受支持 | 打包发行（`build/macos/build_macos.sh`） |
| Linux · X11 会话 | ✅ 受支持 | 见下方「Linux（X11 / XWayland）」；Linux 打包脚本尚在补位 |
| Linux · Wayland + XWayland | ⚠️ 可用 | 副本窗口经 XWayland 显示；窗口置顶依赖窗口管理器的 EWMH 支持 |
| Linux · 纯 Wayland（无 XWayland） | ❌ 不支持 | 副本窗口（GLFW）无法创建 |

## 安装与运行

### Windows

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python main.py
```


### macOS

```bash
xcode-select --install              # 首次配置 macOS 开发工具时需要
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
brew install graphviz               # 可选：仅用于触发器依赖图
python main.py
```

默认从项目根目录启动即可。程序也会在启动时修正工作目录，因此从 Finder、快捷方式或其他目录启动源码版时，`data/` 和 `assets/` 仍能被定位。

### Linux（X11 / XWayland）

```bash
sudo apt install python3-tk fontconfig        # Tk 图形组件与字体；发行版名不同时按包管理器替换
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt     # 必装：networkx / graphviz / openai / numpy 都在这里
sudo apt install graphviz                     # 可选：仅用于触发器依赖图（Python 包不含 dot）
python main.py
```

```bash
# 离线自检（不需要显示器，13 项）
.venv/bin/python tests/run_checks.py
# GUI 冒烟（需要显示器或 xvfb）
.venv/bin/python tests/smoke_switch.py
```

Wayland 原生会话不在支持范围内：请在 **XWayland**（`XDG_SESSION_TYPE=wayland` 时的默认行为）下运行；
纯 Wayland 合成器（如 sway 的纯 Wayland 后端）下副本窗口无法创建。X11 会话可直接使用。
Linux 侧的兼容细节、限制与实测记录见 [docs/linux.md](docs/linux.md) 与
[docs/linux_verification_log.md](docs/linux_verification_log.md)。

### 两套界面：专业模式与ME模式

同一个仓库里有两套界面，共用同一份 `data/`、同一套服务与持久化层，只有入口和界面层不同：

| | 专业模式 | ME模式（挂件模式）                             |
|---|---|----------------------------------------|
| 界面层 | `ui/*`，CustomTkinter，1280×720 多页面 | `ui/mini/*`，原生 tkinter，约 360×620 单窗口挂件 |
| 交互 | 导航栏 + 弹窗 + 滑杆 | 零弹窗，全部整窗换屏，选择项「点一下换下一个」                |
| 观感 | 圆角卡片 + 主题色 | 低像素风：直角硬描边、点阵字、方块进度条                   |


- 入口均为 `python main.py`（`app/shell.py` 的 `run_app` 薄壳）：按设置里的「启动界面模式」启动，之后仍可在两套界面间热切换。两套界面共用同一份数据与进程模型。
- 热切换方式：专业模式点导航栏底部的「⇄ ME模式」，ME模式点标题栏的「⇄」。副本进行中不允许切换。


### macOS 已知事项

- 主界面采用 CustomTkinter，副本窗口采用 Dear PyGui；两者均提供 macOS 轮子。副本窗口必须在主线程启动，当前实现满足该限制。
- Windows DWM 标题栏、`.ico` 图标及 DPI API 已由 `sys.platform` / `platform.system()` 条件保护；macOS 将使用系统标题栏。当前仓库未提供 `.icns`，打包包体会使用 PyInstaller 默认图标；发版前可补充 `assets/icon.icns` 并在 macOS 构建命令中加入 `--icon`。
- macOS 默认中文字体为 `PingFang SC`。旧设置中保存的微软雅黑、仿宋、宋体和 Consolas 会在设置保存时回退到平台默认字体或 `Menlo`。
- Graphviz Python 包不包含 `dot` 二进制文件。若要使用依赖图，请通过 `brew install graphviz` 安装；程序会依次检查 `PATH`、`/opt/homebrew/bin/dot` 与 `/usr/local/bin/dot`。


## 玩家指南

### 快速上手

1. **（可选）进入「设置」页**，按需配置 AI 模型。程序使用 OpenAI SDK 的兼容接口格式，支持配置任意数量的模型并为每个模型命名；设置页内置智谱 AI、DeepSeek、ChatGPT 三个模板，也可以点击「新建」添加其他中转站或本地服务。填写接口 URL、模型名和 API Key 后，可点击「测试连接」验证。
2. **进入「探索模式」**：
   - 在创建参数面板中设定角色姓名、身高、体型预设、性格与个性标签，可上传参考立绘；
   - 也可点击「选择角色」从已有档案中继续。
3. **生成报告**：系统会抽取地标，将角色的身高、步长、腿长等部位与地标逐一对比，生成尺寸表与描述文本。
4. **进入副本**：系统将遵循副本方案，利用AI辅助生成可互动的文字剧情。
5. **创建角色**：创建后方可保存探索报告 / 副本replay，并长期以该角色进行探索。角色也可以从临时报告 / 副本创建。
6. **长期探索**：报告与副本会消耗行动点数并推动角色的属性变化。角色将拥有身为巨大娘的详细档案。

### 核心概念

- **地标（Landmark）**：世界中可供对比的物体，如大楼、桥梁、山脉。每个地标有尺寸、方向（纵向 / 横向）与类型（精确 / 均值），按风格分组管理。
- **描述（Quip）**：按体型、介入度、破坏性匹配的描述文本片段，是报告正文的素材来源。支持多风格与自定义类型。
- **身材（Preset）**：身高、腿长、臂长、胸宽等身体部位的比例集合，决定角色身材。
- **性格（Personality）**：决定介入度、破坏性的初始值、步长，以及敏感值（介入度相关）、重力（破坏性相关）、个性强度，影响角色的演化方向。
- **介入度 / 破坏性**：角色的演化属性，同时为描述条目的静态属性，分别反映角色"进入世界"的程度与对世界的冲击程度。报告中将根据角色的介入度 / 破坏性匹配合适的描述。
- **行动点数**：生成报告需消耗行动点数；行动点数不足时角色的介入度 / 破坏性会逐渐回落。
- **尺寸分类**：报告会按最终身高归入不同体型档位（如数十米、数百米、千米级），决定可用描述与展示格式。

### 副本模块

副本（Dungeon）是可互动的文字剧情模式：AI 按剧本推动剧情，你的选择影响角色状态与结局。两条进入路径共用同一个引擎（专业模式在副本窗口的入口页选方案，ME模式由「调查」直接进）。

- **副本方案（Scenario）**：一份可分享的剧本配置——章节、触发器、结局与演化规则的集合，随资源包 / 世界包分发，也可在程序内的副本编辑器里编写。
- **章节（Chapter）**：剧情按章节推进；进入章节时应用该章的配置（开场文本、背景音乐、语音等）。
- **触发器（Trigger）**：驱动剧情的规则——条件满足时执行动作（跳转章节、播放素材、小游戏等），支持前置条件链。
- **结局（Ending）**：由结局触发器或结束章节收束；达成的结局会写入角色档案。
- **回放（Replay）**：副本过程可存为回放（未触达结局时也会存「未完成」回放），可从回放创建角色。
- **运行时**：副本在独立的 Dear PyGui 窗口中运行，与主界面同进程共存；副本进行中不允许界面热切换。

### 拓展概念：角色 / 聊天 / 世界包

在「探索一局」之上，程序还有三个长期系统：

- **角色（character）**：创建后的角色拥有长期档案—— HTML 历史档案、离线恢复（程序没开的时间里角色仍在按性格与作息曲线演化）、今日新闻。演化模型的完整设计见 [docs/character_evolution.md](docs/character_evolution.md)。
- **聊天（chat）**：与角色持续对话。AI 按 JSON 协议回复（可一次多条消息、可沉默）；AI 可自主开启 / 切换话题，并把少量白名单属性写回角色档案。协议细节见 [docs/chat_delivery.md](docs/chat_delivery.md)。
- **世界包（worlds）**：一键激活后，包内资源按类型**接管**各数据源（地标、描述、副本方案、静态表……），你的自由数据不受影响；停用即恢复。世界包还可携带**行为包**在激活期间改变核心算法（详见「包开发」）。

### 数据与存档

```
data/
├── archives/       # 角色档案与历史报告（txt 格式），按角色名归档
├── packs/          # 资源包（地标、描述、副本、挑战包）；随程序发布，也可自行创建
├── static/         # 静态表（姓名、新闻、性格预设、身材预设、临时存储的行为包）；提供辅助或装饰作用，无程序内编辑器
├── user/           # 用户数据（设置、API秘钥、挑战秘钥、报告与回放、截图、地址注册表）
└── worlds/         # 世界包（<world_id>/）；激活世界包时创建，可携带若干资源包和静态表并一键激活
```

全部数据保存在本机；设置有备份 / 还原能力，程序自检也会先备份再还原。打包版的数据目录**不在安装位置**：Windows 为 `%LOCALAPPDATA%\GiantessGenerator\data`，macOS 为 `~/Library/Application Support/GiantessGenerator/data`。

---

## 包开发

| 你想制作          | 说明                                                     | 开发指引                                       |
|---------------|--------------------------------------------------------|--------------------------------------------|
| 地标、描述、副本方案    | 可供探索的资源                                                | 程序内编辑器（文本管理 / 副本编辑）即可                      |
| 姓名、新闻、性格或身材预设 | 一次性抽取单条的资源，.csv格式                                      | 无程序内编辑器；推荐使用Excel                          |
| 世界包           | 把资源包 + 静态表打成一个可一键激活的 `<world_id>.world.zip`，激活期间接管各数据源 | [世界包行为包开发指南](docs/world_pack_behaviors.md) |
| 行为包           | 世界包可选携带的 Python 代码包，激活期间覆盖角色创建                         | [世界包行为包开发指南](docs/world_pack_behaviors.md) |
| 副本小游戏         | `mini_game` 触发器的运行时与作者 API                             | [小游戏框架](docs/Dungeon/minigame.md)          |
| 挑战包           | 把角色与资源打包成秘钥保护的加密 `.chal`，玩家在挑战模式中达成副本结局                | 程序内「挑战模式」打包                                |
| 世界地址          | 为你的世界申领注册表地址，供地标 / 描述风格跨世界引用                           | [地址系统操作说明](docs/address_system.md)         |

---

## 开发与架构（速查）

分层、守卫与目录组织的**完整口径**（含「为什么」）统一见
[docs/architecture.md](docs/architecture.md)，这里只留速查：

- **分层方向由自检强制**：`infra` → `core` → `dungeon` → `persistence` → `services`
  → `ui` → `app`（另有 `dungeon/window/**` 特例），`python tests/check_import_graph.py`
  越界即失败；「已登记例外」当前为空表。
- **根目录已冻结**：只剩 `paths.py` 与字面量入口 `main.py`，不再新增根目录
  Python 模块；应用壳本体在 `app/` 包。
- **两条对外契约不能碰**：行为包钩子 key 是 `scope.name` 字面量（`"logic.format_size"`
  与文件路径无关）；`services/creation_service.py` / `services/state_service.py`
  的模块路径被行为包文档引用，不得改名。
- **`tests/` 是常驻自检**，命名即分组，`python tests/run_checks.py` 按类别自动发现
  （新增脚本放进对应类别即可，无需登记）：`check_*.py` 为守卫 / 行为自检（离线、
  可进 CI 门禁）；`smoke_*.py` 为需要真实显示器的 GUI 冒烟（`--smoke` 追加）。
- **`scripts/` 是开发者一次性工具区**（不入自检门禁）：真窗口自动驾驶
  （`dungeon_autopilot.py`）、无头模拟（`escape_sim.py`）与数据迁移脚本。要长期
  维护的回归请写成 `tests/` 脚本。
- **`build/` 里只有 `windows/` 与 `macos/` 入库**：暂不支持单独打包某个启动模式。
- **提交信息用约定式提交**（2026-10-06 起）：`<type>(<scope>): <subject>`，`type`
  用英文小写（`feat` / `fix` / `refactor` / `docs` / `test` / `chore` / `build` …），
  `scope` 写子系统，正文用中文；类型表与示例见
  [docs/architecture.md](docs/architecture.md) §6。

## 相关文档

**玩家 / 玩法**

- [演化模型](docs/character_evolution.md)：角色行动点数与演化的量、公式与已验证动态特性
- [聊天协议](docs/chat_delivery.md)：消息生命周期、话题状态机与属性写回白名单

**包开发**

- [世界包行为包开发指南](docs/world_pack_behaviors.md)：行为包的开发流程、注册 API 与可覆盖目标
- [地址系统操作说明](docs/address_system.md)：地标 / 描述风格的地址申领、注册与匹配规则
- [副本文档索引](docs/Dungeon/README.md)：`docs/Dungeon/` 六篇副本开发文档的入口与「按任务查」表（写方案从[数据模型](docs/Dungeon/script.md)进）

**开发与架构**

- [架构与分层](docs/architecture.md)：分层现状与硬约束、结构决策档案（历程索引）、操作手册与提交规范

**平台支持**

- [Linux 支持说明](docs/linux.md)：X11/XWayland 支持范围、XInitThreads 引导、窗口置顶（EWMH）与降级、缺依赖表现、压测与已知限制
- [Linux 试跑检查记录](docs/linux_verification_log.md)：逐项实测与修复证据链
- [Linux 兼容计划](docs/linux_compat_plan.md)：三阶段推进计划、验收标准与风险登记

## 免责声明

本工具为创作辅助软件，所有生成内容均由用户自行负责。请勿将生成内容用于商业用途或违反相关平台规定的场合。
