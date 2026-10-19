# 副本架构说明（`dungeon/`）

> **定位**：`dungeon/` 的分层、模块地图、一次步进的数据流、收尾与自检脚本的**总索引**。
> 数据是配置结构看 [数据模型](script.md)，窗口细节看 [窗口文档](window.md)，
> 术语看 [术语表](domain_terms.md)。全系列入口见 [副本文档索引](README.md)。

适用范围：当前 `dungeon/` 的实现现状。

## 目录

| 节 | 内容 |
|---|---|
| §1 | 一句话概览与分层 |
| §2 | 术语速查 |
| §3 | 分层与依赖规则 |
| §4 | 领域层模块地图 |
| §5 | 一次步进的数据流 |
| §6 | 生命周期与统一收尾 |
| §7 | 配置治理链路 |
| §8 | 持久化布局与原子写 |
| §9 | 自检与守卫脚本 |
| §10 | 硬性约定 |
| §11 | 显示组件包 |

---

## 1. 一句话概览

副本是一台**生成式步进引擎**：每一步由 LLM 产出一段故事文本并给出方向，引擎按
「段落类型 → 转移概率 → 属性值演化 → 伤亡结算 → 触发器判定」推进，把作者写好的
**方案定义**跑成**一局玩家会话**。

三层边界由脚本强制：

| 层 | 位置 | 职责 | 禁令 |
|---|---|---|---|
| 领域层 | `dungeon/` 包根 | 纯玩法逻辑 | 禁 import `imgui_bundle` / `dearpygui` / `tkinter` / `customtkinter` / `services` / `ui` / `dungeon.window` |
| UI 层 | `dungeon/window/` | 窗口、imgui 绘制、宿主接线 | 禁 import `tkinter` / `customtkinter` / `ui`（宿主细节留在 `ui/hosting/tk_host.py`） |
| 编辑器 / 持久化 | `ui/scenario/`、`persistence/` | 方案编辑与落盘 | — |

纪律：**领域层不认识 GUI、不认识服务层；UI 层负责接线与注入；外部行为一律注入，不反向 import。**

## 2. 术语速查

| 概念 | 含义 | 持久化位置 | 命名 |
|---|---|---|---|
| **副本方案 Scenario** | 作者编写的**定义**：章节、触发器、提示词、演化属性、转移矩阵、显示组件、素材 | `data/packs/scenarios/<方案 id>/config.json`（+ `images/`、`components/`） | `scenario_*` |
| **一局 Run** | 某角色用某方案跑的一次会话：`DungeonState`、回放、结局达成、行动点数消耗 | `data/archives/<角色>/回放\|报告`；挑战模式写 `data/user/replays\|reports` | `dungeon_*` |

编辑器（`ui/scenario/`）编辑的是**方案**，`DungeonSessionWindow` 运行的是**一局**。
完整规则与兼容读法见 [术语表](domain_terms.md)。

## 3. 分层与依赖规则

约束写在 `dungeon/__init__.py`，由 `tests/check_dungeon_layering.py` 用 AST 强制。

### 3.1 领域层（`dungeon/` 包根）

- 禁止 import：`imgui_bundle` / `dearpygui` / `customtkinter` / `tkinter` / `services.*` / `ui.*`
- 禁止反向依赖自己的 UI 子包：`dungeon.window`（含 `from .window import`）
- 禁止逃逸出包的相对导入：`from .. import`
- 需要外部行为时**依赖注入**（唯一现状：`EvolutionRules(step_decay=StateService.decay_step_rates)`，由 `window/base.py::_init_session` 传入）

### 3.2 窗口层（`dungeon/window/`）

- 所有需要 imgui_bundle / 服务层的代码在此；`background` `dispatcher` `launcher` `component_registry` `components` 均已从包根迁回 `window/`
- 宿主能力（尺寸/DPI、显隐、事件泵、收尾弹框、活动窗口登记、字体）一律经 `dungeon/window/host.py::HostPort` 取得，Tk 实现是 `ui/hosting/tk_host.py::TkHost`，构造时注入（`DungeonSessionWindow(..., host=TkHost(self))`）
- 端口方法表见 [窗口文档](window.md) §5-C8

### 3.3 依赖倒置清单

| 领域层需要的外部行为 | 注入方式 | 注入方 |
|---|---|---|
| 二阶量（步长）不适应边界衰减 | `EvolutionRules(step_decay=...)` | `window/base.py` 传 `StateService.decay_step_rates` |
| AI 生成 / 提示词取材 | `DungeonPromptBuilder(self)` 读窗口实例公开属性 | `window/base.py` |
| AI 客户端 | `self.ai_client`（`engine` 惰性检查，缺失时 `_show_ai_error`） | `window/base.py::create_client`（按 `core/netproxy` 注入代理） |
| 联网失败说清原因 | `describe_ai_error`（AI）/ `_describe_synth_error`（语音），同口径按 HTTP 状态码分类 | 各暴露点调用（见 §4.5） |
| 剧情压缩用 AI | `StorySummarizer.maybe_compress(ai_client=...)`，失败回退内部算法 | `window/engine.py` |
| 副本独占设置（音量 / 字体 / 记忆窗口 / 自动回放 / 帧率） | `dungeon_settings` 字典 + `dungeon_settings_repo`（读 / 写）；缺省空表退回 `dungeon/settings.py` 常量 | `ChildHost` 子进程按数据目录自建（见 §8）；窗口内改走 `base.set_dungeon_settings` |

## 4. 领域层模块地图

| 模块 | 职责 | 关键出口 |
|---|---|---|
| `models.py` | 运行态数据与枚举 | `DungeonTextType`（5 种段落类型 + 默认步进值）、`DungeonState`（坐标 / 自定义属性 / 计数 / 二阶步长） |
| `rules.py` | 演化与条件求值 | `EvolutionRules`（转移矩阵、分节步长、属性演化）、`TriggerRules.evaluate` |
| `chapters.py` | 章节模型与归一化 | `normalize_chapter(s)`、`normalize_chapters`、`normalize_bgm`、`find_chapter`、`is_terminating_chapter`、`overflow_jump_target`、`chapter_step_override`、`sensitivity_amount`、`matches_scope` |
| `audio/buses.py` | 音频音量**总线单一真相源**（总控 → 音乐 / 语音 / 音效） | `BUSES` / `BUS_LABELS`、`STEPS` / `STEP_LABELS` / `STEP_DB` / `BUS_ANCHOR_DB`（档位与分层的 dB 单一真相源）、`step_options` / `step_choices`（编辑器档位候选）、`clamp_step` / `step_from_level`（0-4 档位与旧 0-100 迁移）、`levels_from_settings` / `set_bus_levels` / `effective_bus_level`（玩家侧实时电平，缺省 `DEFAULT_BUS_LEVELS`）、`bus_default` / `clamp_level` / `resolve_gain`（档位×总线×总控）、`to_ratio` / `to_db` / `db_gain` |
| `audio.py` | 音频播放底座（纯领域，不碰 UI 框架） | `BgmPlayer.play/stop/shutdown/wait_idle`、`open_track`、`backend_name`、`set_backends`（MCI → pygame → 静音降级） |
| `speech.py` | Solea/Bulla 对话的合成语音（纯领域） | `VoiceCaster.voice_for`（她 / 主角 / 配角按名字稳定映射）、`SpeechDirector.speak/stop/set_effect/shutdown`、`synthesize`（合成**一句就返回路径**的入口，供编辑器预听）、`set_proxy` / `resolve_proxy` / `normalize_proxy`（**委托** `core/netproxy`：语音与 AI 共用一份代理）、`_endpoint_candidates` / `synthesis_endpoints`（合成端点：新主机 `api.msedgeservices.com` 优先、旧主机回退，edge-tts PR #491 未并入时本地补齐）、`last_synth_error`（失败原因，日志与预听复用）、`normalize_voice`、`normalize_rate`、`RECOMMENDED_VOICES`、`edge_tts_available` |
| `voice_fx.py` | 章节对话语音的**物理**声学效果（她 / 其他人两档）；响度为**声明式设计量** | `PRESETS`（预设单一真相源，含 `Preset.loudness_db`）、`normalize_voice_fx`、`resolved_params`、`loudness_target` / `loudness_hint` / `playback_trim`（响度目标与其换算）、`LOUDNESS_SPREAD_DB`（摆幅窗口，9 dB＝1.5 档）、`_match_loudness`（渲染末尾按 RMS 对齐，峰值护栏优先）、`playback_plan`（零依赖近似）、`render_wav`（可选解码器 + numpy）、`PlaybackFx`（分拍轨 + 音量包络） |
| `preview.py` | 编辑器里的语音**预听**（纯领域，零 UI 依赖） | `VoicePreview.play(voice, slot, rate, level, effect, text, on_done)`（合成一次、按当前效果放一次；`on_done` 在工作线程回调，Tk 调用方自行 `after` 回主线程）、`stop` / `wait_idle` / `shutdown`（关编辑器时收尾）、`SAMPLE_TEXT` |
| `actions.py` | 动作类型单一注册表 | `NEW_ACTIONS`（insert/option/effect/goto/none）、`ENDING_ACTION`（只读兼容）、`ACTION_LABELS`、`VISUAL_FILTERS` |
| `coupling.py` | 耦合等级与对应提示词 | `coupling_prompts`、`section_instruction`、`normalize_coupling_level` |
| `prompts.py` | 系统与玩家提示词构建 | `DungeonPromptBuilder.build_system_prompt / build_user_prompt` |
| `response.py` | AI 响应解析容错 | `extract_stream_text`、`parse_final_json` |
| `splitter.py` | 内置确定性分句 | `split_stream_units`（流式：首句上屏，余句排队）、`split_full_text`（终局定格） |
| `summary.py` | 剧情压缩与常驻记忆 | `StorySummarizer`（按块 / 按章节压缩 + 最近 N 段原文 + 关键事件 + 事实卡） |
| `details.py` | 细节探究 | `build_detail_query_prompt`、`parse_detail_queries`、`search_replay_details` |
| `schema.py` | 方案配置字段**单一真相源** | `SCENARIO_FIELDS` / `CHAPTER_FIELDS` / `TRIGGER_FIELDS` / `FieldSpec` / `empty_scenario_config()` |
| `validate.py` | 结构化诊断（error / warning / info） | `validate_scenario_config(config, scenario_dir=...)`、`has_errors`、`format_diagnostics` |
| `terms.py` | 术语与持久化契约常量 | `SCENARIO_ID_KEY`、`DEFAULT_SCENARIO_ID`、旧键迁移与兼容读取 |
| `settings.py` | 副本独占设置的键名与缺省（单一真相源，见 §8） | `defaults()`、`LEGACY_KEYS`、`DEFAULT_FPS_MAX`、`DEFAULT_STORY_RECENT_COUNT` |

### 4.1 窗口层 mixin 组装顺序

`dungeon/window/__init__.py`：
`DungeonWindowBase, DungeonLaunchStages, DungeonWindowUI, DungeonStoryEngine,
TriggerHandler, OptionHandler, EndingHandler, DungeonPersistence, ComponentHandler`

| Mixin | 文件 | 职责 |
|---|---|---|
| `DungeonWindowBase` | `base.py` | 窗口生命周期、方案配置加载与启动前校验、会话初始化、关闭请求 |
| `DungeonLaunchStages` | `launcher.py` | 入口阶段：动态背景轮播、方案选择、已达成结局图标循环、开始 / 回放 / 返回 |
| `DungeonWindowUI` | `ui.py` | UI 构建、逐帧自绘、流式文本揭示动画、布局自适应、鼠标 / 键盘输入接线 |
| `DungeonStoryEngine` | `engine.py` | 步进核心：AI 生成、流式分句、属性演化、伤亡结算、收尾回调 |
| `TriggerHandler` | `triggers.py` | 触发器判定、章节进出、章节效果短暂覆盖、窗口视效、插入段落 |
| `OptionHandler` | `options.py` | 选项触发器：后台生成选项文字 → 弹窗 → 记录选择 |
| `EndingHandler` | `ending.py` | 章节敏感效果汇总、结局生成与结局文本构建 |
| `DungeonPersistence` | `persistence.py` | 结局结算、回放 / 报告落盘、统一收尾 `_finalize(completed)` |
| `ComponentHandler` | `components.py` | 显示组件实例的构建 / 布局 / 每帧绘制 / 销毁接入 |

mixin 之间只通过 `self` 上的约定属性通信；新增方法时注意排在前面的 mixin 会覆盖同名方法。

### 4.2 段落步进与转移矩阵

- 默认步进值由 `models.DungeonTextType.step_value` 给出（background 0.02 → action 0.3）
- `section_steps` 可逐项覆盖；`transition_matrix` 按**行覆盖**：

| 规则 | 行为 |
|---|---|
| 配置里出现的行 | **整行替换**（行内没写的列按 0） |
| 未出现的行 | 沿用内置默认 `EvolutionRules.DEFAULT_TRANSITION_MATRIX` |
| 脏权重 | 统一转 float、负值按 0、未知行列键跳过（已在校验阶段报 warning） |
| 整行无效 | 全 0 或解析不出有效权重时回退默认行 |

### 4.3 语音效果：两级引擎与实测（2026-09-29 本机实测）

`voice_fx.py` 的「物理效果」建立在下面这组实测结论上（Windows / Python 3.13.7 /
edge-tts 7.2.8 / winmm MCI，探针脚本在临时目录跑，未入库）：

| 实测项 | 结论 | 对实现的约束 |
|---|---|---|
| 合成服务只给 MP3 | `__stream` 里 `outputFormat` 是硬编码字面量；换成 `raw-24khz-16bit-mono-pcm` / `riff-…` / `ogg-…-opus` 一律 `NoAudioReceived` | **不能**指望服务端给 PCM → 效果必须自己算 |
| `mstts:express-as` 风格 | `whispering` / `terrified` / `angry` / `sad` 全部 `NoAudioReceived` | 语气类预设做不了（也**不该**做，见 §2.4 定位） |
| prosody `rate` / `pitch` / `volume` | 生效（`+40%` 语速 2.95s→2.11s；`pitch=-100Hz` 谱重心 3448→1664 Hz；`volume=-40%` RMS 0.089→0.053） | 语速/音量/变调是**合成侧**免费参数（本轮按用户要求不纳入效果，见 `voice.level` 的音量档位仍走原字段） |
| MP3 解码 | `miniaudio` 1.6 ms、`soundfile`（libsndfile 1.2.2）2.4 ms（2.95s 音频） | 离线渲染的代价可忽略（网络合成 ~1 s 才是瓶颈） |
| numpy 效果链 | 缓抖 1.2 ms、三拍回声 0.7 ms、FFT 混响 47 ms（首帧），三者串联 7.3 ms | 全部在语音工作线程里做，不占帧循环 |
| MCI 同一文件多路 | 同一文件开 8 个 alias 全部 `playing`；命令到 `playing` 0.3~1.0 ms | 播放侧「延迟分拍 = 回声」可行（零依赖） |
| MCI `setaudio volume`（mp3/mpegvideo） | 支持，**播放中**也支持；`left volume` 支持，`bass` 不支持 | 播放侧的「缓抖 = 音量包络」可行 |
| MCI `setaudio`（wav/waveaudio） | **整条命令不被驱动支持**（错误 261） | 渲染路线必须把音量烘进样本；**顺带**：BGM 选 `.wav` 时音量/淡入淡出同样静默失效 |
| MCI `save … type waveaudio` | 错误 295 / 274（`mpegvideo` 设备不支持） | 不能靠 MCI 白嫖 MP3→WAV 解码 |

因此的降级阶梯（`speech.SpeechDirector._play`）：**离线渲染 → 播放侧近似 → 原声**，
三者互不叠加；任何一步失败只影响「听感」，不影响「出声」。

### 4.4 效果响度：声明式设计量（非归一）

上表的物理效果会在**峰值几乎不动（≤ +0.3 dB）**的前提下大幅改变 RMS：实测渲染链
的 RMS 变化可跨 **−1.6…+11.5 dB**——同一个音量档位换个效果，响度差十几个 dB，
作者填的档位管不到这一层。所以响度被提升为**每个预设声明的设计值**，而不是调参的副产物：

| 设计点 | 做法 | 编码处 |
|---|---|---|
| 声明 | 每个预设写 `Preset.loudness_db`（dB，相对干声的 RMS 变化，满强度） | `PRESETS` |
| 对齐 | 渲染链末尾按实测 RMS 把结果拉回声明目标（**不是**归一：各预设目标不同） | `loudness_target` → `_match_loudness` |
| 护栏 | 抬响度前先看峰值余量；不足则停在自然响度（`peak_limited`），绝不削波；`_limit` 与对齐共用 `PEAK_CEILING=0.98` | `_match_loudness` / `_limit` |
| 摆幅 | 全部预设必须落在 `LOUDNESS_SPREAD_DB = 9 dB`（＝1.5 档）窗口内 | 自检 `check_voice_fx_loudness.py` |
| 覆盖 | 单档可写 `loudness_db` 显式覆盖（夹取到 `LOUDNESS_RANGE` ±12 dB） | `loudness_target` |

**实测（`本机`，语音谱型代理信号，强度 100，`.temp/voice_fx_loudness.py`）**：
9 个预设在渲染链里的**自然**响度跨度 **16.9 dB**，对齐后收窄到 **9.0 dB**（恰好
1.5 档），逐预设落点偏差 **0.00 dB**，全程无削波（峰值 ≤ 0.98）。之所以是「收窄」
而非「拉平」：声明值一律**不高于**该预设的自然响度，于是对齐永远是「降」——降幅
总能做到，而抬响度要看峰值余量。

**两条引擎的相对/绝对口径**：渲染路线把响度烘进样本，播放恒为 1.0；播放侧近似
（零依赖）峰值顶在原声上、**抬不起**响度，只能按 `playback_trim`（≤1）做衰减。
于是两条例子的**相对摆幅一致**（都按同一声明值走），**绝对锚点差一个常数**——
装了 vs 没装解码器的机器听感仍有系统性偏移，这是零依赖近似的固有代价。

### 4.5 联网功能：共用代理与统一的失败口径

两处**需要联网**——语音合成（`audio/speech.py`，edge-tts）与 AI 文本生成
（`core/ai.py`，OpenAI 兼容）——共用同一份设置与同一套「说清原因」的口径。

| 关注点 | 做法 | 编码处 |
|---|---|---|
| 代理单一入口 | 显式覆盖 ＞ 语音专用环境变量（`DUNGEON_TTS_PROXY` / `EDGE_TTS_PROXY`）＞ 通用环境变量（`DUNGEON_NET_PROXY` / `HTTPS_PROXY` / `HTTP_PROXY`）＞ 直连 | `core/netproxy.resolve` / `set_override` |
| 代理整理 | 缺 scheme 补 `http://`；SOCKS / 未知协议**明确拒绝**（aiohttp 与 httpx 都没装 SOCKS 支持，放行只会换来看不懂的连接错误） | `core/netproxy.normalize_proxy` |
| 存储 | 设置页「网络代理」一个框 → `settings["net_proxy"]`（旧键 `voice_proxy` 在 `SettingsRepo.load` 自动迁移） | `persistence/settings_repo.py` |
| 语音侧注入口 | 解析结果**显式**传给 `edge_tts.Communicate(proxy=...)`：aiohttp 的 `trust_env` 只认 `http(s)://`，对 `wss://` 不认环境变量，光挂环境变量握手仍走直连 | `audio/speech.py` |
| AI 侧注入口 | 配了代理时构造 `openai.DefaultHttpxClient(proxy=...)` 注入 `OpenAI(http_client=...)`（用 openai 自己导出的类，免得直接 import httpx 多一条依赖声明）；没配就不传（交 SDK 默认读环境变量） | `core/ai.py::OpenAICompatibleClient.__init__` |
| 失败说清 | 两端同口径，**看 HTTP 状态码**（各 SDK 异常都带 `status_code`）而非死认类型：401 / 403 / 404 / 429 / 400 / 5xx / 超时 / 连接 / 代理各给一句人话 | `speech._describe_synth_error` / `ai.describe_ai_error` |
| 挂到界面 | 副本生成失败（`window/engine.py::_note_session_error`）、聊天失败（`services/chat`）、两处「测试连接」（`ui/settings/dialogs.py` / `ui/mini/screens.py`）都用可读原因；原始异常仍留在 `_session_errors` / 日志里 | 各调用点 |

**为什么单独成模块**：`core` 只依赖 `infra`，`netproxy` 零第三方依赖（只用 `os`），
于是 `dungeon`（语音）与 `core`（AI）都能安全引用。两个已知的坑（环境变量代理对
WebSocket 不生效、VPN「系统代理」WinINET 对 Python 不可见）在两处是一样的，收在一处
免得各错一次。

**装配点**（`net_proxy` 在**创建 AI 客户端之前**生效，代理在客户端构造时定格）：
`app/shell.py`（启动 / 每次切界面）、`dungeon/window/base.py::_init_session`（在建
`ai_client` 前）与 `::_configure_voice`、设置页保存后立即生效。副本跑在**独立子进程**
里，主进程设的代理带不过来，必须在子进程再设一次。

守卫：`tests/check_ai_network.py`（AI 侧）、`tests/check_voice_tts.py`（语音侧）。

> **行为变化（2026-10）**：对齐前 AI 文本生成**不读**这个代理设置，只走 SDK 默认
> （环境变量）。对齐后它也读——好处是「一个代理管所有联网功能」，代价是**一个已经
> 关掉的代理从此也会让 AI 连不上**（此前只是语音受影响）。所以连接类失败会**点名
> 当前代理**并给出「清空或改用直连」的出路，而不是笼统说「连不上服务器」。

## 5. 一次步进的数据流

```
点击 / 空格 → DungeonStoryEngine._on_next_step()
   ├─ 回放模式 ─────────────► _replay_next_step()（按记录重放，不判定条件）
   ├─ 动画未播完 ───────────► _finish_text_animation()
   ├─ 有待揭示的显示段落 ────► _reveal_pending_unit()
   ├─ 有待消费的插入段落 ────► _consume_pending_insertion()
   └─ 正常推进 ─────────────► _generate_next_text()   [后台线程]

_generate_next_text()
  1. next_type = dungeon_logic.get_next_text_type(current_text_type)   结束章节内强制 → BACKGROUND
  2. prompt    = prompt_builder.build_user_prompt(next_type)           含概要 / 最近段落 / 细节补充
  3. AI 流式   → extract_stream_text → split_stream_units              首句流式上屏，余句入 _pending_units
  4. 末块      → parse_final_json → (正文, direction, custom_directions)
  5. 演化      → dungeon_logic.evolve_attributes(...)                  step_override=0 时整段冻结
  6. 解锁写入  → _apply_prompted_unlocks(正文) → _finish_step(...)
  7. 后台      → _start_detail_query(正文)                             下次生成前用回放缓存解答

_finish_step()
  a. _apply_effect_overrides()      章节效果覆盖倒计时递减，到期交还章节默认
  b. _record_casualties()           按身高 / 步长 / 破坏性算伤亡，追加 casualty_evolution
  c. replay_data.append(step_info)  一步一条完整快照
  d. _check_unlock_coord()          身材解锁
  e. summarizer.record_key_event()  坐标跨整数阈值的里程碑
  f. summarizer.record/maybe_compress()  剧情压缩（默认每 20 段 / 离开章节时）
  g. check_triggers()               见 §5.1
  h. _check_chapter_overflow()      触发器之后再判超限，避免同一步二次跳转
```

### 5.1 触发器判定（`triggers.py::check_triggers`）

条件 = 三者同时成立：

1. `chapter` 作用域（空 = 任意 / `__none__` = 无章节 / 章节名）
2. `precondition_names` 已全部触发过
3. `condition` 规则组（`TriggerRules.evaluate`）

命中后按 `actions.ACTION_POSITIONS` 的**固定顺序位置**逐位派发，同位置至多生效一个
（同位置内按配置顺序取第一个满足条件者）：

`fx` → `option` / `mini_game` → `goto` → `effect` → `insert` → `none`

**一轮 = 一次步进收尾（或会话启动）+ 它派生的全部结算重判**（选项选择、小游戏结算，
见 `triggers._begin_trigger_round`）：重判共享「本轮已触发」集合，同一触发器一轮内
至多生效一次；被接纳即清空 `间隔计数`。`effect`（章节效果短暂覆盖）排在 goto 之后，
覆盖才落在新章节里（先于 goto 会被换章清除）。未知动作与旧版动作（`background` /
`sensitivity` / `ending`）走通用跳过路径，`ending` 仅回放兼容。字段与参数详见
[数据模型](script.md) §4–§6.1。

### 5.2 结束路径

结束章节（章节属性 `ending=true`）内步进恒为 0——坐标、自定义属性、步长全部冻结，仍累计计数；
达到 `max_paragraphs` 时由 `_terminate_from_ending_chapter()` 用章节自身的结算字段
（增量 / 伤亡步进 / 行动点返还 / 图标）生成结局。

## 6. 生命周期与统一收尾

入口阶段与会话阶段共享同一次窗口生命周期（同一个 ImGui 上下文 / 视口 / 根窗口，`base.py` + `launcher.py`），详见
[窗口文档](window.md) §2。收尾只走一个入口：

```python
DungeonPersistence._finalize(completed: bool, reason: str = "")
```

| 路径 | 触发方 | `completed` | 行为 |
|---|---|---|---|
| 正常结局 | `EndingHandler._generate_ending` | `True` | 结算结局增量 → 落角色 → 写结局索引 → 按设置自动保存回放与报告 |
| 结局已触发但生成线程未收尾 | `_handle_exit`（reason「结局已触发，生成未结束」） | `True` | 同上，避免「生成到一半退出」被误判为未完成 |
| 用户中断、未触发结局 | `_handle_exit`（窗口关闭统一走这里） | `False` | **不**结算、**不**写 `endings.json`、**不**记挑战达成；已生成内容落盘为「未完成」回放 + 报告 |
| 生成异常 | `_note_session_error`（不结束会话，可重试） | 视退出时是否已触发结局而定 | 同上；报告里附会话期间的异常摘要 |

`_finalized` 标志保证幂等（多条路径抢跑时只有第一次生效）。
这是针对「未触发结局就退出导致整局丢失」的修复：**没真正走完的一局不算达成，但内容必须保住。**

## 7. 配置治理链路（schema → validate → 运行时）

```
dungeon/schema.py ──字段声明（FieldSpec）──┬──► ScenarioRepo.empty_scenario_config()  空方案模板
   单一真相源                              ├──► dungeon/validate.py                  结构化诊断
                                           └──► ui/scenario/*                        编辑器表单（按声明渲染）
normalize_chapter / ScenarioRepo._migrate（手写，行为经过实战）
        └── 由 tests/check_scenario_schema.py 守卫「声明 ↔ 产出」字段一致
```

| 时机 | 位置 | 力度 |
|---|---|---|
| 编辑器保存 | `ScenarioRepo.save_config()` | 计算并留存诊断（warning / info 不阻断） |
| 副本启动前 | `window/base.py::_load_session_config()` | **error 阻止进入副本**，warning 打到控制台 |
| 离线批量 / CI | `scripts/scenario_validate.py` | 有 error 时退出码 1 |

诊断分三级：`error`（悬空跳转目标、无起始章节、空选项列表…）/ `warning`（未知条件键、废弃动作、失效前置引用、资产缺失…）/ `info`（无结局路径、旧版字段、未知顶层键…）。

## 8. 持久化布局与原子写

```
data/
  packs/scenarios/<方案 id>/
      config.json            方案定义（+ .bak）
      images/                背景图 / 结局图标
      components/            显示组件包（官方包随分发提供）
  archives/<角色档案目录>/           目录名 = 角色的 giantess_id
      info.json  avatar/             档案本体与形象
      回放/<名>_回放_<时间戳>[_未完成].replay.json
      报告/<名>_报告_<时间戳>[_未完成].txt
  user/
      endings.json  replays/  reports/  （挑战模式与无角色场景）
      settings.json  dungeon_settings.json  api_keys.json  address_registry.json
```

`settings.json` 只存与主程序**共用**的偏好；副本**独占**的运行时设置（音频总线电平、
段落字体、记忆窗口大小、自动保存回放、帧率上限）住在 `user/dungeon_settings.json`
（`persistence/dungeon_settings_repo.py`，键名与缺省见 `dungeon/settings.py`）。
独立成文件是因为副本会话跑在子进程、又要在窗口内直接改并落盘：独占文件同一时刻只有
一个写者（会话期主窗口隐藏），子进程可直读写；共享的 `settings.json` 有「父进程持有
整份 dict + 世界包锁键」的语义，子进程直写会竞态。旧键由 `SettingsRepo.load` 一次性
搬进新文件，旧用户无感。

写盘统一走 `persistence/json_store.py`：**同目录临时文件 → fsync → 覆盖前留一份 `.bak` → `os.replace`**；
读取用 `load_json_with_backup`（主文件损坏时自愈回退 `.bak`）。禁止再出现 `open(path, 'w')` 直接截断写。

## 9. 自检与守卫脚本

见 [副本文档索引](README.md) §4（无 GUI 九个 + GUI 冒烟一个）。
改动领域层 import、演化链路、收尾路径或配置结构后跑对应脚本；以上全部通过才算绿。

## 10. 硬性约定

| 约定 | 说明 | 出处 |
|---|---|---|
| 数据类一律按关键字构造 | `DungeonState` 字段曾按位置传参导致伤亡数组被写成浮点数 | `models.py::clone` 注释 |
| 构造参数必须保存到 `self` | mixin 方法可能被其他 mixin 调用，`__init__` 里的局部变量会 AttributeError | [窗口文档](window.md) §5-C3 |
| 关闭一律走 `_request_close()` | 业务代码不直接退（不写 `app_shall_exit`、不调库的退出函数）；由 `before_imgui_render` 帧边界落实 | [窗口文档](window.md) §5-C1 |
| 跨线程 UI 更新走 `self._frame.call()` | 帧时钟是**窗口实例成员**；计时类逻辑用 `every/after` 帧任务而不是开线程 | [窗口文档](window.md) §5-C4、`window/frame.py` |
| 窗口的 GL / 上下文 / 视口生命周期全交给 `hello_imgui.run()` | 进程内不手动建/毁上下文、不需要保活视口；收尾在 `run()` 返回之后做。**2026-10 起会话在独立子进程里跑**（[宿主边界](window_host.md) §7）：父进程从不加载副本窗口栈，热切换与 GL 生命周期不再相关 | [窗口文档](window.md) §5-C2 |
| GL 资源句柄只在 GL 存活时丢 | `hello_imgui.TextureGpu` 是 RAII，清空容器 = 丢引用；释放点在 `before_exit`（`ui._release_gpu_textures`），`run()` 返回后的 `_finish_session` 只许 `retire()` 兜底保住引用——否则每局收尾在驱动层段错误（2026-10 Linux 实测） | [窗口文档](window.md) §5-C15、`window/gpu_textures.py`、`tests/check_gpu_texture_lifetime.py` |
| 写盘必走原子写 | 半截文件不可恢复 | 本文 §8 |
| 剧本 / 运营术语不混用 | `scenario_*` = 方案，`dungeon_*` = 一局 | [术语表](domain_terms.md) |
| 组件只读窗口状态 | 组件 ctx 即窗口实例，不反向写状态；**访问只经组件服务面**（`component_viewport` / `schedule*` / `session_waiting_for_input` / `component_top_inset` 等，C13），不读窗口私有属性 | `component_registry.py::DungeonComponent` 契约文档、`components.py::ComponentHandler`、`tests/check_component_pack.py` |

## 11. 显示组件包

官方组件包是窗口层常驻 Python 包 `dungeon/window/component_pack/`（`__init__.py`
组装 `REGISTRY`，各组件拆在同目录模块——`base.py` 共享常量与文本组件公共基类、
`text_gradient.py` / `text_card.py` / `text_nvl.py` / `attr_bar.py` / `proc_log.py`），
由 `component_registry` 惰性导入。文本主组件由方案配置的 `text_component` 字段**三选一**声明（text / text_card /
text_nvl），`components` 列表只放其余组件（如属性条、过程日志），参数统一记在
`components_params`。组件类需实现 `build / layout / draw / destroy` 四个钩子（`refresh` 保留为 `layout` 的兼容别名），
由 `ComponentHandler` 接入窗口的更新链（主组件先建、z 序在底）；
可用性与参数声明来自 `component_registry`，编辑器侧由 `ui/scenario/component_mgr.py`
三选一控件 + 卡片化管理。
`ComponentRegistry(pack_dir=...)` 可显式指定**外部组件包目录**（入口文件名 `components.py`）
整体替换常驻包，`registry.source` 记录实际生效来源；加载链与组件契约由无 GUI 守卫
`tests/check_component_pack.py` 断言（无 GUI 替身 ctx 的 build/layout/refresh/destroy 冒烟）。
配套约定：组件的展示名 / 说明写在**组件类**的 `label` / `description` 上（编辑器经
`available_component_descriptions()` 取，`_COMPONENT_META` 只是兜底）；参数用
`param_specs` 声明，`min` / `max` 在 `merge_params` 统一夹取；字号与字体候选链只在
`dungeon/window/fonts.py`；组件的 ImGui 窗口/状态 ID 一律以各自的 `TAG_PREFIX` 开头（守卫查冲突）。

---

## 历史

本文件只描述**现状**。以下是相关档案（不据此改代码）：

| 档案 | 内容 |
|---|---|
| [副本模型与命名演进](history/model_evolution.md) | 章节 / 触发器模型拆分、动作类型退场、scenario / dungeon 命名拆分与目录迁移、配置治理由来（S1–S4） |
| [宿主改造档案](history/host_refactor.md) | 窗口层手动渲染、构造/运行分离、宿主端口、帧时钟、结果对象化 |
| [LingChat 参考价值评估](history/lingchat_evaluation.md) | 同人作品借鉴价值评估与「不采纳」清单 |

其中一条属于**仍有效的现状约束**，不是历史：LingChat 为 AGPL-3.0、本项目为 MIT，
复制 / 改写 / 移植其源码会强制本项目转为 AGPL——只读其文档与实现、用 Python 独立重写是唯一可行做法。
