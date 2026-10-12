"""音频总线：副本所有发声的音量**单一真相源**。

副本的发声分三条内容总线，外加一条总控::

    master ─┬─ music   （章节背景音乐，``chapters.bgm``）
            ├─ voice   （对话朗读，``speech`` / ``voice_fx``）
            └─ sfx     （一次性音效，``SfxPlayer``）

**三层各管各的单位**（越往上越抽象，越往下越接近物理）：

| 层 | 谁在用 | 单位 | 范围 |
|---|---|---|---|
| 作者侧「档位」 | 章节 ``bgm.level`` / 方案 ``voice.level`` | **档** | ``0-4``（:data:`STEP_LABELS`） |
| 玩家侧「滑条」 | 设置页四条滑条（``settings.audio_levels``） | 电平 | ``0-100``（:func:`to_ratio`） |
| 内部「增益」 | 播放后端 | 线性 / dB | ``0.0-1.0``（:func:`db_gain`） |

**为什么作者侧用档位而不是 0-100**：0-100 看着精细，其实是**假精度**——
线性刻度下 70 与 90 只差 2.2 dB，人耳几乎听不出，作者却要为此做无意义的
微调决策。5 档每档约 6 dB，恰好是人耳能明确分辨的步长，既要选得动，也
不必选。

**档位怎么变成响度**：``档位 dB + 总线锚点 dB``（:data:`STEP_DB` +
:data:`BUS_ANCHOR_DB`）。

- 缺省档位是 **「中」**（``3`` = ``-6 dB``）——**注意 5 档的上限是 0 dB
  （数字满幅），所以「中」必然比满幅低 6 dB**；比它更响的那一档留给
  刻意强调。这也顺带为语音效果（实测最多 +11.5 dB，见
  ``.workbuddy/audio_volume_analysis.md``）留出了余量。
- **分层落在锚点上**：音乐锚点 −6 dB，于是「音乐中档」比「语音中档」
  天然低 6 dB，不必每章手填。这与业界（音乐 −6…−12 dB）一致。

**旧数据**：早期字段是 0-100 的 ``volume``。``normalize_*`` 仍接受它，
按响度就近折算成档位（:func:`step_from_level`）后**只写新键 ``level``**；
两套键并存会有歧义（``3`` 到底是「3% 音量」还是「中档」？），所以刻意
**改名**而不是复用 ``volume``。

**玩家侧电平**存在 ``settings["audio_levels"]``（设置页四条滑条）。副本窗口
开局时用 :func:`set_bus_levels` 把它装进来，播放路径**不必**逐处传参——
:func:`resolve_gain` 缺省取 :func:`effective_bus_level`（实时值 > 缺省），
所以滑条一改，三条内容总线同时跟着变。未接管的进程（没开副本）回退缺省
:data:`DEFAULT_BUS_LEVELS`（恒 100 = 不衰减）。

**尚未接入**：``SfxPlayer`` 仍按 0-1 直传（且当前无调用方）。
"""

import math

#: 电平上限（0-100 刻度，玩家滑条用）
MAX_LEVEL = 100

#: 总线标识
BUS_MASTER = "master"
BUS_MUSIC = "music"
BUS_VOICE = "voice"
BUS_SFX = "sfx"
#: 总线顺序（设置页 / 文档按此排列）
BUSES = (BUS_MASTER, BUS_MUSIC, BUS_VOICE, BUS_SFX)
BUS_LABELS = {
    BUS_MASTER: "总音量",
    BUS_MUSIC: "音乐",
    BUS_VOICE: "语音",
    BUS_SFX: "音效",
}

#: 静音下限（dB）：到这里及以下视为静音。
DB_FLOOR = -80.0

# ---------------- 作者侧：音量档位（0-4） ----------------

#: 档位范围与缺省（「中」）
STEP_MIN = 0
STEP_MAX = 4
DEFAULT_STEP = 3
#: 档位顺序（编辑器下拉按此排列；自轻到响）
STEPS = (0, 1, 2, 3, 4)
#: 档位中文名（编辑器 / 文档 / 校验共用）
STEP_LABELS = {
    0: "静音",
    1: "很轻",
    2: "轻",
    3: "中",
    4: "响",
}
#: 档位 → dB（相对数字满幅）。``None`` = 静音。每档约 6 dB —— 人耳能明确
#: 分辨的步长。上限是 0 dB（满幅），所以「中」必然在满幅之下 6 dB。
STEP_DB = {
    0: None,
    1: -18.0,
    2: -12.0,
    3: -6.0,
    4: 0.0,
}
#: 各总线的锚点（dB）——**混音分层落在这一张表**：音乐比语音低 6 dB。
#: 改这里就改了全项目的「同样的档位，音乐该比语音轻多少」。
BUS_ANCHOR_DB = {
    BUS_MASTER: 0.0,
    BUS_MUSIC: -6.0,
    BUS_VOICE: 0.0,
    BUS_SFX: -3.0,
}
#: 旧 0-100 音量 → 档位 的折算边界（左闭右开，按响度就近）。
#: 顺序即升档顺序；``None`` 那一档是「> 边界上限」的兜底。
LEGACY_STEP_BOUNDS = ((25, 1), (50, 2), (80, 3), (None, 4))


def clamp_level(value, default=MAX_LEVEL) -> int:
    """把 0-100 电平归一到整数；非法值回退 ``default``。"""
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        number = int(default)
    return max(0, min(MAX_LEVEL, number))


def clamp_step(value, default=DEFAULT_STEP) -> int:
    """把音量档位归一到 0-4；非法值回退 ``default``。

    ``bool`` 不算数字（``True`` 当 1 用是笔误的常见来源）。
    """
    if isinstance(value, bool):
        return int(default)
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return int(default)
    return max(STEP_MIN, min(STEP_MAX, number))


def step_label(step) -> str:
    """档位 → 中文名（非法值回退缺省档的名字）。"""
    return STEP_LABELS[clamp_step(step)]


def step_options():
    """``((档位, 中文名), ...)``——需要「值 + 名字」成对的地方吃这个。"""
    return tuple((step, STEP_LABELS[step]) for step in STEPS)


def step_display(step) -> str:
    """档位 → 编辑器下拉的**显示文案**（``"3 · 中"``）。

    既给数字也给名字：数字让作者看得见这是一条 0-4 的档位轴，名字替他把
    「这个数到底多响」讲清楚。文案是**稳定契约**——:func:`step_from_display`
    按同一格式反解，改格式必须同步改两边。
    """
    value = clamp_step(step)
    return f"{value} · {STEP_LABELS[value]}"


def step_choices():
    """``((显示文案, 档位), ...)``——UI 下拉的候选列表（文案在前，便于直接当 ``values``）。"""
    return tuple((step_display(step), step) for step in STEPS)


def step_from_display(text, default=DEFAULT_STEP) -> int:
    """显示文案 → 档位（:func:`step_display` 的逆；也认纯数字与中文名）。

    认不出时回退 ``default``，绝不抛异常——UI 存盘路径不该因为一个脏字符串
    炸掉整个对话框。
    """
    if isinstance(text, bool):
        return int(default)
    if isinstance(text, (int, float)):
        return clamp_step(text, default)
    raw = str(text or "").strip()
    for step, label in STEP_LABELS.items():
        if raw in (label, step_display(step)):
            return step
    return clamp_step(raw, default)


def step_from_level(level, default=DEFAULT_STEP) -> int:
    """旧 0-100 音量 → 档位（按响度就近；``0`` 直接归静音；非法值回退 ``default``）。"""
    if isinstance(level, bool):
        return int(default)
    try:
        value = float(level)
    except (TypeError, ValueError):
        return int(default)
    value = max(0.0, min(float(MAX_LEVEL), value))
    if value <= 0.0:
        return STEP_MIN
    for bound, step in LEGACY_STEP_BOUNDS:
        if bound is None or value < bound:
            return step
    return STEP_MAX


def step_db(bus, step):
    """档位在本总线上的有效 dB；``None`` = 静音。"""
    offset = STEP_DB.get(clamp_step(step))
    if offset is None:
        return None
    return BUS_ANCHOR_DB.get(bus, 0.0) + offset


def step_gain(bus, step) -> float:
    """档位 → 线性增益（静音为 0.0）。"""
    db = step_db(bus, step)
    if db is None:
        return 0.0
    return db_gain(db)


def to_ratio(level) -> float:
    """0-100 电平 → 0.0-1.0 线性增益（玩家滑条用）。"""
    return clamp_level(level) / float(MAX_LEVEL)


def to_db(level) -> float:
    """0-100 电平 → dB（``0`` 归到 :data:`DB_FLOOR`）。"""
    ratio = to_ratio(level)
    if ratio <= 0.0:
        return DB_FLOOR
    return 20.0 * math.log10(ratio)


def db_gain(db) -> float:
    """dB → 线性增益（:data:`DB_FLOOR` 及以下视为静音）。"""
    try:
        value = float(db)
    except (TypeError, ValueError):
        return 1.0
    if value <= DB_FLOOR:
        return 0.0
    return 10.0 ** (value / 20.0)


#: 总线电平默认（玩家侧）：100 = 不衰减。玩家没调过滑条时用它。
DEFAULT_BUS_LEVELS = {bus: MAX_LEVEL for bus in BUSES}

#: 设置页里总线电平存的键（settings 字典）
SETTINGS_KEY = "audio_levels"


def bus_default(bus) -> int:
    """某条总线的玩家电平**缺省**（当前都是 100 = 不衰减）。"""
    return DEFAULT_BUS_LEVELS.get(bus, MAX_LEVEL)


def levels_from_settings(settings) -> dict:
    """从 settings 字典取出四条总线的玩家电平（缺项 / 非法值回退缺省）。

    认不出结构时整体回退缺省——设置文件被手改坏不该让副本发不出声。
    """
    raw = (settings or {}).get(SETTINGS_KEY) or {}
    if not isinstance(raw, dict):
        return dict(DEFAULT_BUS_LEVELS)
    return {bus: clamp_level(raw.get(bus), bus_default(bus)) for bus in BUSES}


#: 玩家侧实时电平：副本窗口按 settings 装进来后用；``None`` = 未接管，回退缺省。
_live_levels = None


def set_bus_levels(levels=None):
    """接管玩家侧总线电平（副本窗口装配时调用；``None`` = 交还缺省）。

    四条总线一次给全，缺项回退缺省；传 ``None`` 清空，回到 :data:`DEFAULT_BUS_LEVELS`。
    之所以用「进程内实时值」而不是给每个播放调用加参数：接线点（BgmPlayer /
    SpeechDirector / voice_fx）散在三处、又被多条路径复用，逐处透传总线电平会
    把玩家设置一路漏下去；音量本就该是全局混音状态，收在这里最省。
    """
    global _live_levels
    if levels is None:
        _live_levels = None
    else:
        _live_levels = {bus: clamp_level(levels.get(bus), bus_default(bus))
                        for bus in BUSES}


def effective_bus_level(bus) -> int:
    """这条总线**当前生效**的玩家电平：实时值 > 缺省。"""
    if _live_levels is None:
        return bus_default(bus)
    return _live_levels.get(bus, bus_default(bus))


def resolve_gain(bus, step=None, bus_level=None, master=None) -> float:
    """算出一路声音的最终线性增益（0.0-1.0）。

    - ``step``：作者配的音量档位（``0-4``），缺省 :data:`DEFAULT_STEP`；
    - ``bus_level``：这条总线的玩家电平（``0-100``），缺省 :func:`effective_bus_level`；
    - ``master``：总控电平（``0-100``），缺省总控当前电平。

    档位先经 :func:`step_gain` 变成「含总线锚点」的增益，再乘玩家侧两层——
    这就是「总线」的含义：任一层拧小，整条链一起变小。
    """
    gain = step_gain(bus, DEFAULT_STEP if step is None else step)
    gain *= to_ratio(clamp_level(bus_level, effective_bus_level(bus)))
    gain *= to_ratio(clamp_level(master, effective_bus_level(BUS_MASTER)))
    return max(0.0, min(1.0, gain))
