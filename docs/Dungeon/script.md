# 副本数据模型索引：章节与触发器

> **定位**：方案配置（`data/packs/scenarios/<id>/config.json`）里玩法结构的**字段索引**。
> 分层与运行时数据流见 [架构说明](architecture.md)，窗口侧见 [窗口文档](window.md)。
> 字段声明的单一真相源是 `dungeon/schema.py`（`CHAPTER_FIELDS` / `TRIGGER_FIELDS` / `SCENARIO_FIELDS`）。

## 目录

| 节 | 内容 |
|---|---|
| §1 | 模型边界（章节 vs 触发器） |
| §2 | 章节字段索引 |
| §3 | 章节运行时规则 |
| §4 | 触发器字段索引 |
| §5 | 条件键索引 |
| §6 | 动作注册表 |
| §7 | 章节效果短暂覆盖 |
| §8 | 编辑器文件索引 |
| §9 | 校验、迁移与持久化 |

---

## 1. 模型边界

| 模型 | 职责 | 有没有条件字段 |
|---|---|---|
| **章节 Chapter** | 只描述「身处其中时的环境」：特定背景、持续敏感效果、编辑器配色 | **没有** |
| **触发器 Trigger** | 唯一的流程驱动者：条件成立后执行一个动作；进入 / 离开章节也必须由它的「跳转章节」动作执行 | 有 |

旧版把条件与动作塞在同一个无层级的触发器里，动作有六种；现在背景切换与性格敏感化由
**章节属性**承担，动作类型拆成「单步行为」与「章节跳转」两类。
（演进过程与旧动作退场清单见 [副本模型与命名演进](history/model_evolution.md)，不据此改代码。）

## 2. 章节字段索引

来源：`schema.CHAPTER_FIELDS`（与 `chapters.normalize_chapter` 的产出一致，由 `check_scenario_schema.py` 守卫）。

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `name` | str | — | 章节名（**必填**） |
| `color` | color | — | 编辑器自定义配色 `#RRGGBB`（浅色模式基准） |
| `start` | bool | `false` | 起始章节：开副本时自动进入，全局最多一个 |
| `ending` | bool | `false` | 结束章节，见 §3.2 |
| `note` | str | `""` | 备注；提示词里会带一行「当前章节：X（备注）」 |
| `max_paragraphs` | int | `99` | 节内最大段落数（`chapters.DEFAULT_MAX_PARAGRAPHS`） |
| `overflow_target` | str | `""` | 超限跳转目标；空串 = 离开章节（无章节） |
| `background` | dict | — | 章节背景，见 §2.1 |
| `bgm` | dict | — | 章节背景音乐，见 §2.2 |
| `voice_fx` | dict | — | 章节对话语音的**物理**效果（她 / 其他人两档），见 §2.4 |
| `sensitivity` | list | — | 身处本章节时持续生效、离开即失效，见 §2.3 |
| `intrusion_delta` | float | `0` | 结束结算：介入度增量（仅 `ending=true`） |
| `destruction_delta` | float | `0` | 结束结算：破坏性增量（仅 `ending=true`） |
| `casualty_step` | float | `0` | 结束结算：伤亡步进（仅 `ending=true`） |
| `action_points_refund` | int | `0` | 结束结算：行动点返还（仅 `ending=true`） |
| `custom_deltas` | dict | — | 结束结算：自定义属性增量（仅 `ending=true`） |
| `icon_path` | str | `""` | 结局图标（相对方案目录，仅 `ending=true`） |

### 2.1 `background`（`schema.BACKGROUND_FIELDS`）

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `image_path` | str | `""` | 背景图，相对副本目录 |
| `smooth_transition` | bool | `true` | 平滑切换 |
| `filter_effect` | enum | `None` | 滤镜键，取值来自 `dungeon/actions.py::VISUAL_FILTERS` |

### 2.2 `bgm`（`schema.BGM_FIELDS`）

进入章节时播放、离开即停的背景音乐；**没配就是不放**（`normalize_bgm` 把缺曲目的
配置归一成空 dict，也不会往每个章节摊一份只有默认值的空配置）。

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `path` | str | `""` | 音频文件，相对方案目录（编辑器导入的落在 `<方案>/audio/`）；空串 = 本章节不放音乐 |
| `level` | int | `3` | 音量**档位** 0-4：`0` 静音 / `1` 很轻 / `2` 轻 / `3` 中 / `4` 响（越界夹到 `MAX_BGM_LEVEL`）。旧版 0-100 `volume` 仍能读入，按响度就近折算后**只写 `level`** |
| `loop` | bool | `true` | 循环播放 |
| `fade_seconds` | float | `1.5` | 切换时的淡入淡出秒数（`0` = 硬切） |

> **为什么用档位而不是 0-100**：0-100 看着精细，其实是**假精度**——线性刻度下
> `70` 与 `90` 只差 ≈2.2 dB，人耳几乎听不出，作者却要为此做无意义的微调。5 档
> 每档约 6 dB（`audio/buses.STEP_DB`），恰好是人耳能明确分辨的步长。
>
> **分层落在总线锚点上**：同一档位下音乐比语音低 6 dB（`audio/buses.BUS_ANCHOR_DB`
> ——音乐锚点 −6 dB、语音 0 dB），与业界（音乐 −6…−12 dB）一致，不必每章手填。
> 缺省档是「中」（`3`）。**显式填的档位永远优先**，缺省只决定「没填时听到什么」。
> 档位 → 线性增益的唯一入口是 `buses.step_gain` / `resolve_gain`（`step_db` 是
> 这套换算的口径来源）。

播放由领域模块 `dungeon/audio/`（包，`__init__` 即原 audio 模块）承担（**不依赖任何 UI 框架**）：Windows 走
`winmm` 的 MCI（零依赖，mp3/wav/wma 同一套），非 Windows 退到 pygame（装了才用），
两者都不可用时静音降级——**没有声音不该拖垮一局副本**。换曲走「旧曲淡出 → 关 →
新曲从 0 淡入」，同一首曲子重复进入只挪音量、不从头重放；所有设备操作都在一条
后台线程上串行执行，`play()` 只入队就返回，不占帧循环。

MCI 的坑：`play <alias> repeat` 只有 mpegvideo（mp3 等）认，waveaudio（wav）会把整条
命令判成非法——先试 `repeat`，失败就退回普通 play 并把该设备标为「手动续播」，
由工作线程轮询 `status mode` 在曲末从头再放（否则作者写的 `loop: true` 会在第一遍
播完后无声无息地静下去）。

### 2.3 `sensitivity[]`（`schema.SENSITIVITY_FIELDS`）

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `attr` | str | — | 作用属性 |
| `strength` | float | `1.0` | 强度 |
| `objective` | float | `0.0` | 客观影响 |

敏感倍率公式（触发器与章节共用 `dungeon.chapters.sensitivity_amount`）：

```
倍率 = 强度 ×（性格值 + 客观影响）
# 破坏性 → 性格重力；介入度 → 性格敏感值；其余属性 → 策略值
#   策略值 = 敏感 × (1 - 归一行动点数) + 重力 × 归一个性强度
#   归一行动点数 = 行动点数/100（因子不小于 0，未知时缺省 0.5）
#   归一个性强度 = skip_base_prob / 5
```

### 2.4 `voice_fx`（`schema.VOICE_FX_FIELDS` / `VOICE_FX_SLOT_FIELDS`）

章节对话语音的**物理**效果，两档分开配：**她（`her`）是巨大声源的物理后果，其他人
（`others`）是普通尺度声源在同一个环境里的后果**。设计定位是尺度 / 距离 / 空间，
**不是语气**——所以预设名全是物理描述（巨躯轰鸣、旷野回声、高空遥语），没有「愤怒 /
耳语」这类情绪预设。分档由 `speech.SpeechDirector._speak_job` 按说话人认：她本人走
`her` 档，主角与其余角色走 `others` 档。

没配 = 本章节不加效果（`normalize_voice_fx` 返回空 dict，不摊默认配置）；
`normalize_chapter` 产出的 `voice_fx` 与 `CHAPTER_FIELDS` 的一致性由
`check_scenario_schema.py` 守卫。

```jsonc
"voice_fx": {
  "her":    {"preset": "open_valley",  "intensity": 80},
  "others": {"preset": "trembling_ground", "intensity": 70}
}
```

| 档 | 预设 | 物理含义 |
|---|---|---|
| 她 | `giant_boom` 巨躯轰鸣 | 巨大胸腔与肺量：低频厚重、轻微过载、空气随声缓抖 |
| 她 | `open_valley` 旷野回声 | 声音撞上山体与楼群折回：稀疏的长回声 |
| 她 | `far_above` 高空遥语 | 她在远处/高处：空气吸收吃掉高频、直达成变弱且晚到 |
| 她 | `shaking_air` 震空低鸣 | 庞大身躯搅动空气：低频隆隆裹着缓慢的抖动 |
| 她 | `deafening_close` 近距震耳 | 俯身贴近：过载、低频迅速膨胀、被压实 |
| 其他人 | `room_echo` 室内回响 | 普通嗓门在室内：墙面折回的短回声 |
| 其他人 | `valley_echo` 山谷折回 | 同一个旷野里的折回，比她的更弱更短 |
| 其他人 | `muffled_wall` 隔墙闷响 | 隔着一层建筑结构：高频被吃掉，只剩闷响 |
| 其他人 | `trembling_ground` 震地余波 | 她一动，地面与空气还在抖：台词裹在低频抖动里 |

字段（每档）：`preset`（预设键，非法或**不属于该档**都按「不加效果」处理并给诊断）、
`intensity`（0-100，缺省 60：把预设的基准参数按 `intensity/100` 缩放——**量**跟着变
（湿量、深度、增益、过载、拍数），**频率不跟着变**（抖得快慢由尺度决定））。
其余键是**手改 JSON 才用得上的高级参数**（编辑器只暴露预设 + 强度），取值范围单一
真相源在 `voice_fx.PARAM_RANGES`：`pre_delay` / `muffle_hz` / `low_gain_db` / `drive` /
`rumble_hz` / `rumble_level` / `tremor_hz` / `tremor_depth` / `echo_delay` / `echo_count` /
`echo_decay` / `echo_wet` / `loudness_db`，越界运行时夹取、校验给 warning。

**响度是声明量，不是副产物。** 同一句台词配上不同效果，响度天然会飘——实测
（`.temp/voice_fx_loudness.py`，语音谱型代理信号）自然响度可跨 −1.3…+15.6 dB
（≈2.8 档），而峰值几乎不动。所以每个预设在 `voice_fx.PRESETS` 里**声明**自己该有
多响（`Preset.loudness_db`：dB，相对干声的 RMS 变化），渲染链末尾按实测 RMS 把结果
拉回这个目标，`voice_fx.loudness_target(spec)` 是唯一入口。这不是「归一」（那会把
所有效果拉成一样响），而是把响度差变成**可声明、可守卫**的设计值：

- 全部声明值落在 `LOUDNESS_SPREAD_DB` = 9 dB（**= 1.5 个音量档**）的窗口里——这就是
  「保留 1.5 档响度差异」的编码处；实测自然跨度 16.9 dB 被收窄到 9.0 dB，每个预设的
  落点与声明值偏差 0.00 dB（`check_voice_fx_loudness.py` 守卫）。
- 声明值一律**不高于**该预设的自然响度，于是对齐永远是「降」——降幅总能做到；而抬响
  度要看峰值余量（`PEAK_CEILING`），余量不足时停在自然响度并在日志说明，**绝不削波**。
- RMS 按**直达声段**量（预延迟偏移 + 干声长度），回声尾巴不算：那是直达声的余韵，
  不是「这句话有多响」。
- 想调某个效果的响度就改 `loudness_db`，**别靠堆 `drive` / `low_gain_db`**——那改了
  音色，响度会被这条规则拉回目标。作者也可以在档里显式写 `loudness_db` 覆盖声明值
  （夹取到 `LOUDNESS_RANGE`）。

**两级引擎（互不叠加，按本机能力择优）**，实测数字见 [架构说明](architecture.md) §4.3：

| 引擎 | 依赖 | 能做什么 | 什么时候用 |
|---|---|---|---|
| 离线渲染（`render`） | 可选解码器 `miniaudio` **或** `soundfile`（+ numpy） | 全部环节：预延迟、空气吸收（低通）、躯体共振（低频增益）、次声隆隆、过载、缓抖、回声 | 有解码器时**一律走它**（采样级精确、跨平台一致） |
| 播放侧近似（`playback`） | **零依赖** | 稀疏延迟分拍 = 回声；音量包络 = 缓抖 | 没有解码器时（Windows MCI 同一文件多路实测可用） |
| 原声（`none`） | — | 无 | 没配效果；或预设纯渲染（`deafening_close`）而本机没解码器——此时日志留一条 |

> **音量烘进样本**：渲染路线产出 WAV，而 MCI 的 waveaudio 设备**不认 `setaudio`**
> （实测错误 261），播放层设不了音量，所以 `voice.level`（档位）与**效果响度**在渲染
> 时都直接换算成线性增益乘进样本。
> 播放侧路线的音量仍走 `track.set_volume`（mp3 设备支持，实测可用）。
> 顺带提醒：作者给 `bgm.path` 选了 `.wav` 时，Windows 上的 BGM 音量/淡入淡出同样
> 不生效（同一条驱动限制），选 `.mp3` 才正常。

> **两个引擎的响度锚点不同**：渲染路线能抬也能降（按声明值精确对齐）；播放侧近似
> **只能降**（峰值就是原声，`PlaybackFx` 抬不动），所以它按 `voice_fx.playback_trim`
> 衰减、目标 ≥ 0 时不衰减。于是两个引擎的**相对**摆幅一致（都是那 1.5 档），
> **绝对**锚点差一个常数——装了 vs 没装解码器的机器上，「巨躯轰鸣比高空遥语响多少」
> 一致，整体电平可能略有不同。

降级与其余能力一致：解码失败 / 缺 numpy / 缺音频后端都只是「少点效果」，
**不拖垮朗读**；效果渲染产物与合成音频同在会话临时目录里，收尾一起清掉。

## 3. 章节运行时规则

### 3.1 进出章节与超限

| 规则 | 行为 |
|---|---|
| 进入章节 | 重置节内计数（`DungeonState.chapter_steps`），清除上一章遗留的章节效果覆盖；应用本章的背景、背景音乐、语音效果与敏感效果 |
| 离开章节（跳到空目标） | 不清零属性、不切换背景，只是不再有章节效果；**背景音乐会停掉**（音乐是「身处其中」的一部分，留着上一章的曲子只会串味） |
| 音乐随章节走 | 跳到没配 `bgm` 的章节 = 停播；回放记录里带着当时的 `bgm` 配置，回放时按当时的曲目复现，不依赖当前方案 |
| 语音效果随章节走 | `_apply_chapter_voice_fx` 把 `voice_fx` 塞进语音导演（两档：她 / 其他人）；跳到没配效果的章节 = 清空效果（与音乐同一套语义）；回放记录与 `goto` 触发器记录都带着当时的配置复现 |
| 起始章节 | 唯一的自动进入点；它描述初始状态，不是判定，不违反「章节无条件字段」 |
| 超限跳转 | 节内计数达到 `max_paragraphs` 时，在 `_finish_step` 末尾（触发器判定**之后**）自动跳到 `overflow_target`；空串 = 离开章节 |
| 上限只是兜底 | 触发器的 `goto` / `option` 仍可在达到上限**之前**离开章节 |
| 回放顺序 | 跳转与结束都发生在同一步的 `_finish_step` 中，回放里是「步进记录 → `kind: "chapter"` 记录」 |

### 3.2 结束章节（`ending: true`，原「结束触发器」）

由 `chapters.is_terminating_chapter` / `overflow_jump_target` 统一判定：

1. **不允许跳出或弹选项**：处于结束章节时 `goto` 与 `option` 在 `check_triggers` 中被直接跳过（打印提示，不记入已触发集合）。
2. **所有段落类型一律被覆盖为「结局」，步进为 0**：`_generate_next_text` / `_display_inserted_paragraph` 强制以结局语气生成，显示前缀 `【结局】`；`rules.evolve_attributes(step_override=0.0)` 冻结坐标、自定义属性与步长演化，只累加计数，伤亡步进也为 0。
3. **达到 `max_paragraphs` 后终止**：`overflow_target` 恒为空串，调用 `_terminate_from_ending_chapter` 按章节自身的结算字段生成结局，复用结局生成管线（`EndingHandler._start_ending_generation`）。

旧配置里的 `ending`（结局）触发器**仍然可以运行**：`actions.ENDING_ACTION = "ending"` 保留，运行时照旧执行、回放照旧复现；
编辑器不再提供新建入口，既有这类触发器在列表里显示为「结局（旧配置）」。

## 4. 触发器字段索引

来源：`schema.TRIGGER_FIELDS`。

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `name` | str | — | 触发器名（**必填**；`选择:<名字>` 条件键用它） |
| `chapter` | str | `""` | 所在章节（条件的一部分）：空 = 任意章节，`__none__` = 无章节，章节名 = 仅在该章节内 |
| `precondition_names` | list | — | 前置触发器；列表内每个都至少触发过一次 |
| `condition` | dict | — | 条件规则组，见 §5 |
| `repeatable` | bool | `true` | 可重复触发 |
| `action` | enum | — | 动作类型，见 §6（旧配置键 `action_type` 兼容） |
| `action_data` | dict | — | 动作参数 |

判定顺序见 `TriggerHandler.check_triggers`：**所在章节 → 前置触发器 → 条件规则**，任何一项不满足即跳过。
`action_data["type"]` 与 `action_type` 冗余存在于旧配置中；`normalize_action_type` 只做
「动作字段缺失时回退 `action_data["type"]`」，不再做任何旧别名转换。

## 5. 条件键索引

来源：`schema.FIXED_CONDITION_KEYS` / `SERIES_CONDITION_KEY` / `CHOICE_CONDITION_PREFIX`，
求值在 `rules.TriggerRules.evaluate`。

| 键 | 含义 | 可用度量 |
|---|---|---|
| `介入度` / `破坏性` | 演化坐标 | — |
| `总伤亡` | 累计伤亡 | — |
| `总计数` | 副本总步数 | — |
| `间隔计数` | 距上次任意触发器触发后的步数 | — |
| `节内计数` | 进入当前章节后的步数 | — |
| `伤亡数组` | 每步伤亡序列 | `count` / `avg` / `trend` / `last` |
| `选择:<触发器名>` | 该选项触发器的选择记录 | `count` / `ratio` / `trend` / `last` |
| 自定义演化量名 | `evolution_attrs` 中 `type == "custom"` 的属性 | — |

- 比较符（`CONDITION_COMPARATORS`）：`>=` `<=` `>` `<` `==` `!=`
- 整体关系（`CONDITION_OPERATORS`）：`and` / `or`
- 一层组合：`rules` 的元素还可以是 `{"operator": ..., "rules": [...]}` 的子条件组
  （求值递归），如 `(a或b)且(c或d)` 写作 `{"operator": "and", "rules": [
  {"operator": "or", "rules": [a, b]}, {"operator": "or", "rules": [c, d]}]}`。
  编辑器按「连接符切换处自动加括号」生成该形状；更深嵌套运行时也能求值，
  但编辑器无法完整显示（校验器报 warning）。

## 6. 动作注册表

单一出处：`dungeon/actions.py`（`NEW_ACTIONS` / `ENDING_ACTION` / `ACTION_LABELS` / `VISUAL_FILTERS` / `WINDOW_FX`）。
编辑器、依赖图、回放导出共用，不再各写一份字符串常量。

| 类型 | 展示名 | `action_data` | 状态 |
|---|---|---|---|
| `insert` | 插入文本 | `text, text_type, highlight, delayed` | 运行时；`delayed` 表示等下一段 AI 段落返回后再显示 |
| `option` | 弹出选项 | `prompt, options[{id, prompt, text}]` | 运行时；选择按编号记入 `选择:<名字>` |
| `effect` | 效果覆盖 | `filter, bgm_level, voice_fx, sensitivity, duration` | 运行时；限时改写章节环境（背景滤镜 / 音乐档位 / 语音效果 / 敏感效果），`duration` 步后各维度交还章节默认值；只写出来的维度才被覆盖，详见 §7 |
| `fx` | 窗口视效 | `fx, duration, intensity` | 运行时；操作整个会话窗口的瞬时画面（闪光 / 振动 / 反色 / 色差分离），`duration` 为绝对时间（秒，0.1~10），按 `intensity`（0~100）定峰值亮度 / 振幅 / 错位幅度；详见 §7 |
| `goto` | 跳转章节 | `chapter`（空串 = 离开章节） | 运行时；进入目标章节，应用其背景与持续敏感效果 |
| `mini_game` | 小游戏挑战 | `game, target_level, win_goto, lose_goto` | 运行时；按包 manifest 的 `backend` 分派：`py` → 会话窗口内覆盖层舞台（`dungeon/window/minigame/`，ESC=无结果中止），`web` → 子进程 pywebview 独立窗口；步进暂停，结果经 `finish(won, result)` 回传后按 `win_goto`/`lose_goto` 跳转（空串=原地继续），无结果=原地继续。结果随触发器记录落盘，回放不进游戏直接复现 |
| `none` | 条件标记 | `{}` | 运行时；无动作，只标记条件成立，供其他触发器作前置条件 |
| `ending` | 结局（旧配置） | — | **已删除**：运行时直接跳过（与 `background` / `sensitivity` 同走未知动作路径），仅回放兼容（旧存档按记录复现结局）；编辑器显示「旧版·已失效」，新方案由「结束章节」承担 |
| `background` / `sensitivity`（早期拼写 `sensitive`） | 旧版·已失效 | — | **运行时不认识**：不在注册表里，落入「未知的触发器动作类型」通用路径（不执行、不记入已触发集合、不重置间隔计数，只打印一行提示） |

旧版动作（`background` / `sensitivity` / `ending`）的三处处理：运行时跳过、触发器列表
显示为「旧版·已失效」（无表单，只能删除）、校验器 `validate.LEGACY_ACTIONS` 在**编辑器
保存时与副本启动前**报专项 warning。

### 6.1 判定顺序与轮次

**一轮 = 一次步进收尾（或会话启动）+ 它派生的全部结算重判**（选项选择、小游戏结算）。
重判共享「本轮已触发」集合（`_round_fired`，见 `triggers._begin_trigger_round`），
**同一触发器一轮内至多生效一次**——结算后条件仍成立的触发器不会立刻重发（选项连弹、
视效 / 插入重复发放被结构性挡住），其他触发器可以接力；新一轮从下一次步进收尾开始。

派发按 `actions.ACTION_POSITIONS` 的固定顺序逐位进行，**同位置至多生效一个**
（同位置内按配置顺序取第一个满足条件且配置有效的触发器；配置无效被跳过不占配额）：

| 位置 | 动作 | 理由 |
|---|---|---|
| 1 | `fx` | 纯视觉、不等结果，画面反应永远最先 |
| 2 | `option` / `mini_game` | 模态动作先占位；同一次判定里两者至多其一 |
| 3 | `goto` | 之后的 effect / insert 落在新章节（effect 先于 goto 会被换章清除，等于白放） |
| 4 | `effect` | 章节效果短暂覆盖（背景滤镜 / 音乐音量 / 语音效果 / 敏感效果） |
| 5 | `insert` | 插入段落 |
| 6 | `none` | 条件标记最后才立：依赖它的触发器最早下一轮可见，**瞬时条件（如精确匹配）不要依赖 none 前置链** |

被接纳的触发器**立即清空 `间隔计数`**——同轮后续位置里 `间隔计数>=1` 的条件必然为假，
与"同位置至多一个"互相强化。回放不判定，只按记录复现。

## 7. 章节效果短暂覆盖（`effect`）

章节是「身处其中时的环境」（§3）；`effect` 动作给它加一层**限时覆盖**：触发后临时
接管其中若干维度，走完 `duration` 步再交还章节默认值。覆盖项的解析与合成规则集中在
`dungeon/overrides.py`（运行时、校验器、编辑器共用同一份口径），运行时状态在
`dungeon/window/triggers.py`。

| 维度 | 键 | 取值 |
|---|---|---|
| 背景滤镜 | `filter` | 滤镜键（`actions.VISUAL_FILTERS`）或空串（这段期间清除滤镜）；无背景图时忽略 |
| 音乐档位 | `bgm_level` | 0~4 整数档位（兼容旧 `bgm_volume` 的 0~100，按响度就近折算）；只改档位不换曲，章节没配音乐时忽略 |
| 语音效果 | `voice_fx` | 与章节 `voice_fx` 同形（`her` / `others` 两档）；空对象 = 这段期间不加效果 |
| 敏感效果 | `sensitivity` | 与章节 `sensitivity` 同形的数组；空数组 = 这段期间没有敏感效果 |

| 规则 | 行为 |
|---|---|
| 触发 | 入队 `effect_overrides`，每步在 `_finish_step` 里消耗 1 步（`_apply_effect_overrides`） |
| 逐维度 | 一条覆盖只改它写出来的维度，其余沿用章节（或更早的覆盖） |
| 后触发优先 | 同维度多条并存时取最后触发的那条；到期逐条回退 |
| 恢复 | 每次入队 / 到期都按当前生效集合整体重算，各维度落到章节默认值（不是抄旧值回去） |
| 换章 | 立即清空全部未到期覆盖，并落到新章节默认 |
| 回放 | 按记录原样复现覆盖（重新入队，不判定条件） |

滤镜键的单一出处是 `actions.VISUAL_FILTERS`（与 `DungeonBackground.apply_filter` 的键同步，
`background.py` 的 PIL 映射在导入时校验一致性）。

### 7.1 窗口视效（`fx`，`dungeon/window/fx.py`）

与章节效果覆盖的背景滤镜相对：这组视效操作**整个会话窗口**而非背景图，以**绝对时间**
（秒）消退，与步进无关。视效键的单一出处是 `actions.WINDOW_FX`
（`fx.py` 导入时校验，校验器按同清单检查配置）。

| 键 | 画法 |
|---|---|
| `flash` 闪光 | 顶层绘制列表一块全窗白色矩形，不透明度从峰值线性退到 0 |
| `shake` 振动 | 主窗口全部直接子项按基线位置叠加衰减正弦偏移，整窗内容一起抖 |
| `invert` 反色 | `dpg.output_frame_buffer` 抓一帧当前画面（整窗真实像素）→ 像素工作者反相 → 半透明覆盖层按时长退场 |
| `rgb_split` 色差分离 | 同一套快照管线：R/G/B 通道按强度错位相加成一张覆盖层图像 |

| 规则 | 行为 |
|---|---|
| 派发 | `fx` 在判定顺序中排第一位（§6.1）：纯视觉、不等结果，同轮其他动作照常 |
| 阻塞性 | 无：触发即播放、立即返回，期间流式生成与上屏、音频播放、切换背景等一切照常 |
| 时长 | `duration` 绝对时间（秒，解析失败 / 越界夹回 0.1~10），帧时钟按 `time.monotonic` 计时 |
| 强度 | `intensity` 0~100：闪光 / 反色峰值不透明度、振动幅度、色差错位幅度（低强度保底可见） |
| 重触发 | 同类互斥（重触发顶掉旧的），不同类可叠加（如闪光 + 振动） |
| 换章 | 不清除：视效本身只有亚秒到数秒寿命，到期自消 |
| 抓帧失败 | 反色 / 色差分离退回处理当前背景图；连背景都没有则放弃本次视效（打印提示） |
| 回放 | 回放记录原样重放一次（不判定条件，按时长重演） |

## 8. 编辑器文件索引

| 文件 | 职责 |
|---|---|
| `ui/scenario/chapter_trigger_mgr.py` | 章节与触发器**合并面板**：章节行加粗并按自定义颜色着色，其下缩进行是本章节触发器；「任意章节 / 无章节」的触发器排在最后。底部是新建章节 / 新建触发器与共用的编辑、删除、上移、下移 |
| `ui/scenario/chapter_dlg.py` | 章节编辑：配色色板 + 手填 hex、起始 / 结束标记、最大段落数与超限跳转、背景导入、背景音乐（曲目 / **档位** / 循环 / 淡入淡出）、**对话语音物理效果（她 / 其他人两档的预设 + 强度 + 只读「响度 ±dB」+ 预听）**、敏感效果增删、结束章节的结局结算与图标 |
| `ui/scenario/trigger_dlg.py` | 触发器编辑：所在章节下拉、`跳转章节` / `章节效果覆盖`（四个覆盖维度：滤镜 / 音乐档位 / 语音效果 / 敏感）/ `窗口视效` 表单、`节内计数` 条件键、旧版动作独立按钮行；`结局` 动作已从按钮行移除 |
| `ui/scenario/asset_import.py` | 背景 / 结局图标 / 背景音乐导入的共用实现（裁剪入库、相对路径；音频落 `<方案>/audio/`，不裁剪） |
| `ui/scenario/sc_frame.py` | 方案级配置：耦合等级与主角称呼、**对话语音**（总开关 + 她 / 主角 / 其他角色的音色选择 + 语速 / 档位 + 预听 / 其它音色试听）、进入消耗、显示组件 |
| `ui/scenario/component_mgr.py` | 显示组件卡片化管理（开关与参数改动自动保存） |

配色与调序：

- 章节配色以 `#RRGGBB` 存配置（浅色模式基准），深色模式下由 `chapters.shade_for_mode` 提亮 35%；触发器行沿用所属章节的颜色，无归属的用弱化色。
- 触发器只与同组内相邻的触发器交换，越过章节行即到达章节收尾（不跨章节移动）；章节与相邻章节交换，其下触发器按章节归组自动跟随。
- 新建触发器默认落在当前选中的章节里；编辑章节改名时，指向它的触发器作用域与 `跳转章节` 目标一并改名。
- 删除章节会在确认框里列出将被一并删除的触发器；确认后一起删除，仍指向该章节的 `跳转章节` 目标会收到警告（配置不会被自动改写）。

## 9. 校验、迁移与持久化

### 9.1 校验器

`validate.validate_scenario_config(config, scenario_dir=None)` 返回结构化诊断
（`Diagnostic(level, path, message)`，error / warning / info 三级）。

| 诊断级别 | 覆盖示例 |
|---|---|
| `error` | 悬空 goto / overflow_target、无起始章节、空选项列表 |
| `warning` | 多起始章节、结束章节内的 option / goto、废弃动作与旧版字段、条件键 / 比较符 / 度量非法、转移矩阵行缺失与权重越界、背景图 / 结局图标缺失（需传 `scenario_dir`） |
| `info` | 无结局路径（`ending_policy: "open"` 时不再提示）、旧版顶层字段、未知顶层键、`voice` 未知子键、Velum 等级配了对话语音（不生效） |

**`ending_policy`**（顶层，`required` / `open`，默认 `required`）：作者对「本方案要不要结局」的显式声明。
`required` 时没有结束章节、也没有「结局」触发器会给出 info；声明 `open`（开放式方案）即消除该提示。
它只影响校验提示，运行时行为仍由结束章节 / 结局触发器决定。

接入点：

| 时机 | 位置 | 力度 |
|---|---|---|
| 编辑器保存 | `ScenarioRepo.save_config()` | 诊断记入 `last_diagnostics`，编辑器弹窗汇总 error |
| 副本启动前 | `window/base.py::_load_session_config()` | error 级阻止进入 |
| 离线批量 / CI | `scripts/scenario_validate.py` | error 退出码 1 |

### 9.2 迁移与兼容

| 项 | 行为 |
|---|---|
| `ScenarioRepo._migrate` | 以原配置为底稿补齐字段，未知键（含 `components`）原样保留；缺失的 `chapters` 补成 `[]`，每个触发器补 `chapter: ""`；旧写法 `components` 列表里的文本家族成员提升到 `text_component` 字段并从列表移除 |
| `chapters.normalize_chapter` | 补齐 `ending` / `max_paragraphs` / `overflow_target` 与结束章节结算字段（旧章节默认 `ending=false`、上限 99、超限离开章节） |
| 旧配置行为 | 迁移后所有既有触发器都是「任意章节」，也不自动创建章节 |
| 旧版 `background` / `sensitivity` 动作 | **不再执行**，配置本身不被改写，需要清理时在编辑器里删除 |
| 旧 `ending` 动作 | **继续执行**；新做结局请改用「结束章节」 |
| 演化配置链路 | `window/base.py::_init_session` 把 `transition_matrix` 与 `section_steps` 传给 `EvolutionRules`（同时注入 `StateService.decay_step_rates`）；这条链路由 `check_scenario_schema.py` 守卫 |

### 9.3 回放记录

| 记录 | 说明 |
|---|---|
| 普通步进 | 读取处按 `not entry.get("kind")` 识别：`engine._replay_next_step`、`persistence`、`archive_export._render_replay_entries` |
| `kind: "chapter"` | 章节进入；`goto` 触发器记录额外带 `chapter_background`，回放时不依赖当前章节配置即可复现 |
| `kind: "trigger"` | 触发器触发，回放时走 `_replay_trigger` |
| `ending_chapter: true` | 结束章节产生的步进记录，回放时按「结局」类型展示 |

### 9.4 剧情压缩（提示词上下文）

`summary.StorySummarizer` 把冗长段落历史压成可注入提示词的「剧情概要」，由
`DungeonWindowBase._init_session` / `_init_replay` 创建（`story_summary`），`engine` 每步（含回放）调用
`_record_story_summary` 登记段落。

| 项 | 行为 |
|---|---|
| 压缩时机 | 缓冲达到 `block_size`（默认 20 段）压一个块；`_enter_chapter` 离开章节时 `flush_chapter` 强制压本章余量；结局生成前再 flush 一次 |
| 压缩方式 | 优先用 AI 生成不超过 100 字的第三人称概要；AI 不可用 / 失败时回退内部算法（首句 + 高频词 + 末句）；回放模式无 AI，走内部算法 |
| 提示词内容 | `build_user_prompt` 注入 `prompt_block()`：**截至此刻的全部压缩概要** + **最近 N 段原文**；N = `settings["story_recent_count"]`，默认 20，设置项「剧情概要保留段数」（1~200） |
| 存放 | 只存内存（`summaries`），不写入回放文件；回放按同样的块大小重新累计，不依赖保存时的概要内容 |

---

## 10. 对话语音（方案级 `voice`，`schema.VOICE_FIELDS`）

Solea / Bulla 耦合等级的对话分支会给每句台词标 `@说话人@`（见 window.md
「说话人标记」）。**只有带说话人的台词会念出来**，叙述段落保持安静。

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `enabled` | bool | `true` | 本方案是否朗读；此外还要过玩家侧的**语音总线**（设置 →「音量」→ 语音，拧到 0 即静音） |
| `voice_her` | str | `zh-CN-XiaoxiaoNeural` | 她的音色（按本局角色名 / 昵称认） |
| `voice_protagonist` | str | `zh-CN-YunxiNeural` | 主角音色（按方案 `protagonist_title`，留空按「主角」认） |
| `voice_other` | list | 三把备用嗓 | 其余角色的音色池，**按名字稳定映射**：同一个配角每局都是同一把嗓子 |
| `rate` | str | `+0%` | 语速（`+10%` / `-20%`；非法写法回退常速） |
| `level` | int | `3` | 音量**档位** 0-4（`0` 静音 / `1` 很轻 / `2` 轻 / `3` 中 / `4` 响；越界夹到 `MAX_VOICE_LEVEL`）。旧版 0-100 `volume` 仍能读入，折算后只写 `level`。语音是混音 0 dB 参考，同档位下比音乐高 6 dB（`audio/buses.BUS_ANCHOR_DB`） |

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

线程模型同样沿用 `audio.py`：唯一一条后台线程串行处理「合成 → 播放」，
`speak()` 只入队就返回，**新台词打断旧台词**（翻页时不该念上一页的），
超长台词（`MAX_SPEECH_CHARS`，300 字）跳过，会话收尾收工并清掉临时音频。

| 接线 | 位置 |
|---|---|
| 装配音色（按方案配置） | `window/base.py::_configure_voice`（`_init_session` / `_init_replay` 各调一次） |
| 合成端点 | `speech._endpoint_candidates()`：新主机 `api.msedgeservices.com` 优先、旧主机回退（edge-tts PR #491 未并入时本地补齐） |
| 合成代理 | `settings["net_proxy"]` → `core/netproxy`（**语音与 AI 共用**）→ `app/shell.py` 与 `window/base.py` 各设一次（子进程独立）；失败原因见 `speech.last_synth_error()` |
| 触发朗读 | `window/ui.py::_speak_item`（显示单元定稿时，叙述句无 `speaker` 直接返回） |

> **玩家侧还有一层**：设置页的四条总线滑条（`settings["audio_levels"]`，总音量 /
> 音乐 / 语音 / 音效）在方案档位之外再乘一次，装进混音层见
> `dungeon/audio/buses.py::set_bus_levels`（副本窗口开局时按 settings 装配）。
> 于是「她说什么」的响度 = **方案档位 × 总线电平 × 总控电平**。
>
> **「不想听语音」就是语音电平拧到 0**——原先独立总开关 `settings["dungeon_voice_enabled"]` 已撤掉。
> 这一档是**真静音**：`speech.SpeechDirector._silent()` 在档位 × 语音电平 × 总控
> 归零时直接返回，**连合成都不做**（不联网、不占工作线程、离线也不会刷告警），
> 所以「调到 0」等价于旧的「关掉开关」。旧设置文件里残留的键在
> `persistence/settings_repo.py::load` 迁移：关过的折算成语音电平 0，然后删键。

### 10.1 章节级物理效果（`voice_fx`）

音色是**谁在说话**（方案级 `voice`）；效果是**她在这个空间里说话会是什么声音**
（章节级 `voice_fx`，她 / 其他人两档）——字段、预设与两级引擎见 §2.4。接线：

| 环节 | 位置 |
|---|---|
| 换章时切效果（没配 = 清空） | `window/triggers.py::_apply_chapter_voice_fx`；配置挂在 `window/base.py::current_voice_fx` |
| 回放复现 | `kind: "chapter"` 记录带 `voice_fx`，`goto` 触发器记录带 `chapter_voice_fx` |
| 按说话人分档 | `speech.py::_speak_job`（`VoiceCaster.role_of` 是她 → `her` 档，否则 `others`） |
| 施加效果 | `speech.py::_play` → `voice_fx.render_wav`（离线渲染）/ `voice_fx.PlaybackFx`（分拍 + 音量包络）/ 原声 |
| 响度对齐 | `voice_fx.loudness_target`（预设声明的 dB）× `_match_loudness`（渲染链末尾按实测 RMS 对齐，再让 `_limit` 守峰值）；播放侧按 `playback_trim` 衰减 |
| 能力与降级 | `voice_fx.has_effect` / `decoder_name` / `playback_plan`；不可用时 `SpeechDirector._warn_fx_unavailable` 每个预设只提示一次 |
| 编辑器**预听** | `dungeon/audio/preview.py::VoicePreview`（零 UI 依赖，与副本同一套合成 + 效果）；章节对话框两档各一个「预听」按钮 + 只读「响度 ±dB」标签；方案编辑器的音色行另有「预听 / 其它音色」试听音色与档位 |

> 效果从属于朗读：方案关掉时 `enabled=False`、或玩家把语音电平拧到 0（见 §10），
> 都是一句都不念，效果自然也不生效。

---

## 历史

本文件只描述**现状**（字段以 `dungeon/schema.py` 为准）。以下档案记录这些形状是怎么来的：

| 档案 | 内容 |
|---|---|
| [副本模型与命名演进](history/model_evolution.md) | 章节 / 触发器模型拆分、动作类型退场（`background` / `sensitivity` / `ending`）、命名与目录迁移 |
