# 副本对话语音（方案级 `voice`）

> **这一篇回答什么问题**：方案怎么配朗读、谁念什么句子、用什么嗓子、合成走什么
> 底座，以及章节级物理效果怎么接线。章节级 `voice_fx` 的**字段与预设**在
> [数据模型 §2.4](script.md)；音色**解析顺序**（含配角账本命中）见
> [副本角色系统](characters.md) §6。

这两节的配置在编辑器里位于**「角色」页**（`character_page.py`）：音色按角色
分栏（她的音色 / 主角音色 / 其他角色音色池），总开关与语速、档位、预听在页首的语音条；
改动即自动保存。

Solea / Bulla 耦合等级的对话分支会给每句台词标 `@说话人@`（见 Dungeon 窗口文档「说话人标记」）。**只有带说话人的台词会念出来**，叙述段落保持安静。

## 1. 方案字段（`schema.VOICE_FIELDS`）

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `enabled` | bool | `true` | 本方案是否朗读；此外还要过玩家侧的**语音总线**（§4，拧到 0 即静音） |
| `voice_her` | str | `zh-CN-XiaoxiaoNeural` | 她的音色（按本局角色名 / 昵称认） |
| `voice_protagonist` | str | `zh-CN-YunxiNeural` | 主角音色（按方案 `protagonist_title`，留空按「主角」认） |
| `voice_other` | list | 三把备用嗓 | 其余角色的音色池，**按名字稳定映射**：同一个配角每局都是同一把嗓子 |
| `rate` | str | `+0%` | 语速（`+10%` / `-20%`；非法写法回退常速） |
| `level` | int | `3` | 音量**档位** 0-4（`0` 静音 / `1` 很轻 / `2` 轻 / `3` 中 / `4` 响；越界夹到 `MAX_VOICE_LEVEL`）。旧版 0-100 `volume` 仍能读入，折算后只写 `level`。语音是混音 0 dB 参考，同档位下比音乐高 6 dB（`audio/buses.BUS_ANCHOR_DB`） |

## 2. 合成底座：edge-tts，可选依赖、失败不拖垮一局

合成走 **edge-tts**（微软神经网络语音，**需要联网**，`pip install edge-tts`），
是**可选依赖**：没装或网络失败只在日志里留一条，不出声不该拖垮一局副本——与
背景音乐的降级策略一致。播放复用 `dungeon/audio` 的后端（MCI → pygame →
静音占位）。

> **合成端点会自己找路，失败也要说清原因。** 2026-10 微软把朗读合成从旧主机
> `speech.platform.bing.com` 迁到 `api.msedgeservices.com`，**全球分批铺开**
> （先是欧洲，10-12 起北美也中招），铺到哪旧主机就在哪回 **HTTP 403**——所以这
> 与「是不是国区」无关，也不是代码问题：`edge-tts` 已是最新（7.2.8），时钟与服务端
> 一致，`Sec-MS-GEC-Version` 换 143/146/150/155/160/170 都照样 403。上游修法见
> edge-tts **PR #491**（7.2.8 尚未并入）：**新主机沿用同一个旧令牌，只是改挂
> `Ocp-Apim-Subscription-Key`**。本模块在本地补齐了这一步：
>
> - **端点回退** `speech._endpoint_candidates()`：装的那版若仍指向旧主机，就先生成
>   新主机候选、再挂旧主机作回退（新主机成 403 就自动落回旧主机）；**已装版本哪天
>   修好了（不再指向旧主机）就不插手**，不去覆盖人家的正确值。成功过的端点记在
>   `_working_wss_url`，下次优先复用。改写 `edge_tts` 模块级 `WSS_URL` 是有状态的，
>   所以「换端点 → 合成」整段由 `_endpoint_lock` 串行化。
> - **失败原因**记在 `speech.last_synth_error()`，按类分：403 / 401 / 超时 / 时钟
>   （`SkewAdjustmentError`）/ SOCKS / 代理连不上 / 地址非法。副本的一次性告警与
>   编辑器**预听**都复用它，不再笼统报「网络不可达 / 音色名有误」。
> - **代理**由 `speech.set_proxy()` / `resolve_proxy()` 决定——它其实委托给
>   **`core/netproxy`**（语音合成与 AI 文本生成**共用同一份**，设置页就一个
>   「网络代理」框）。优先级：`settings["net_proxy"]` ＞ 语音专用环境变量
>   `DUNGEON_TTS_PROXY` / `EDGE_TTS_PROXY` ＞ 通用环境变量 `DUNGEON_NET_PROXY` /
>   `HTTPS_PROXY` / `HTTP_PROXY` ＞ 直连。三个容易踩的点：**① 必须显式传给
>   `edge_tts.Communicate(proxy=...)`**——aiohttp 的 `trust_env` 只对 `http(s)://`
>   生效，对 `wss://` **不认环境变量**，光挂环境变量代理，WebSocket 握手仍会走直连；
>   ② 没配代理时不传这个关键字（免得老版本 `TypeError`）；③ 代理地址先过
>   `normalize_proxy()`——缺 scheme 补 `http://`（否则 aiohttp 抛 `InvalidURL`），
>   **SOCKS 直接拒绝**（不装 `aiohttp-socks` 走不了，请填代理软件的 HTTP/mixed 端口）。
>   装配点三处：`app/shell.py`（启动 / 切界面）、`window/base.py::_configure_voice`
>   （**副本跑在独立子进程里，主进程设的代理带不过来，必须在子进程再设一次**）、
>   设置页保存后立即生效。

## 3. 线程模型与接线

线程模型沿用 `audio.py`：唯一一条后台线程串行处理「合成 → 播放」，
`speak()` 只入队就返回，**新台词打断旧台词**（翻页时不该念上一页的），
超长台词（`MAX_SPEECH_CHARS`，300 字）跳过，会话收尾收工并清掉临时音频。

| 接线 | 位置 |
|---|---|
| 装配音色（按方案配置） | `window/base.py::_configure_voice`（`_init_session` / `_init_replay` 各调一次） |
| 合成端点 | `speech._endpoint_candidates()`：新主机 `api.msedgeservices.com` 优先、旧主机回退（edge-tts PR #491 未并入时本地补齐） |
| 合成代理 | `settings["net_proxy"]` → `core/netproxy`（**语音与 AI 共用**）→ `app/shell.py` 与 `window/base.py` 各设一次（子进程独立）；失败原因见 `speech.last_synth_error()` |
| 触发朗读 | `window/ui.py::_speak_item`（显示单元定稿时，叙述句无 `speaker` 直接返回） |

## 4. 玩家侧音量总线

> 设置页的四条总线滑条（副本独占设置 `audio_levels`，存
> `data/user/dungeon_settings.json`；总音量 / 音乐 / 语音 / 音效）在方案档位之外
> 再乘一次，装进混音层见 `dungeon/audio/buses.py::set_bus_levels`（副本窗口开局时
> 按副本设置装配）。于是「她说什么」的响度 = **方案档位 × 总线电平 × 总控电平**。
>
> **「不想听语音」就是语音电平拧到 0**——原先独立总开关
> `settings["dungeon_voice_enabled"]` 已撤掉。这一档是**真静音**：
> `speech.SpeechDirector._silent()` 在档位 × 语音电平 × 总控归零时直接返回，
> **连合成都不做**（不联网、不占工作线程、离线也不会刷告警），所以「调到 0」等价于
> 旧的「关掉开关」。旧设置文件里残留的键在 `persistence/settings_repo.py::load`
> 迁移：关过的折算成语音电平 0，然后删键。

## 5. 章节级物理效果（`voice_fx`）的接线

音色是**谁在说话**（方案级 `voice`）；效果是**她在这个空间里说话会是什么声音**
（章节级 `voice_fx`，她 / 其他人两档）——字段、预设与两级引擎见
[数据模型 §2.4](script.md)。接线：

| 环节 | 位置 |
|---|---|
| 换章时切效果（没配 = 清空） | `window/triggers.py::_apply_chapter_voice_fx`；配置挂在 `window/base.py::current_voice_fx` |
| 回放复现 | `kind: "chapter"` 记录带 `voice_fx`，`goto` 触发器记录带 `chapter_voice_fx` |
| 按说话人分档 | `speech.py::_speak_job`（`VoiceCaster.role_of` 是她 → `her` 档，否则 `others`） |
| 施加效果 | `speech.py::_play` → `voice_fx.render_wav`（离线渲染）/ `voice_fx.PlaybackFx`（分拍 + 音量包络）/ 原声 |
| 响度对齐 | `voice_fx.loudness_target`（预设声明的 dB）× `_match_loudness`（渲染链末尾按实测 RMS 对齐，再让 `_limit` 守峰值）；播放侧按 `playback_trim` 衰减 |
| 能力与降级 | `voice_fx.has_effect` / `decoder_name` / `playback_plan`；不可用时 `SpeechDirector._warn_fx_unavailable` 每个预设只提示一次 |
| 编辑器**预听** | `dungeon/audio/preview.py::VoicePreview`（零 UI 依赖，与副本同一套合成 + 效果）；章节对话框两档各一个「预听」按钮 + 只读「响度 ±dB」标签；方案编辑器的音色行另有「预听 / 其它音色」试听音色与档位 |

> 效果从属于朗读：方案关掉时 `enabled=False`、或玩家把语音电平拧到 0（§4），
> 都是一句都不念，效果自然也不生效。

## 6. 自检

`python tests/check_audio_chain.py`：说话人分音色 / 队列 / 两档效果 / 三条引擎路径 /
降级 / BGM 缺坏文件收尾（假合成器 + 替身后端，不出声不联网）。
