"""音频总线：副本所有发声的音量**单一真相源**。

副本的发声分三条内容总线，外加一条总控；一路声音的最终增益 = 内容电平 ×
总线电平 × 总控电平（各自归一后相乘）::

    master ─┬─ music   （章节背景音乐，``chapters.bgm``）
            ├─ voice   （对话朗读，``speech`` / ``voice_fx``）
            └─ sfx     （一次性音效，``SfxPlayer``）

**为什么要这一层**：此前「背景音乐音量」与「朗读音量」各写一份 0-100 常量、
各写一遍 ``volume / 100`` 的换算（``chapters.DEFAULT_BGM_VOLUME`` 与
``speech.DEFAULT_VOICE_VOLUME``），既没有总控，也没有任何玩家侧音量入口。
把电平换算收敛到本模块后，播放侧只问一句「这一路现在该多大增益」，将来给
总线加 dB 曲线、加玩家滑条都只动这一个文件。

**电平单位**：统一沿用 0-100 的整数——与旧常量、与作者在编辑器里看到的数字
一致。语义上电平是**相对本总线的线性刻度**：``100`` = 不衰减。

**本步（先做总线）保持行为逐位不变**：三条总线的总线电平与总控默认都是
``100``（乘 1.0），内容电平默认仍是音乐 70 / 语音 90，于是换算结果与旧的
``volume / 100`` 完全一致。第二步才把线性刻度换成 dB 曲线——``to_db`` /
``db_gain`` / ``RECOMMENDED_DB_DEFAULTS`` 是本步备好、**尚未接线**的接口，
免得「加总线」与「改听感」两件事挤在一个改动里。

**尚未接入**：``SfxPlayer`` 仍按 0-1 直传（且当前无调用方），待它被启用时再
折进 ``sfx`` 总线。
"""

import math

#: 电平上限（0-100 刻度）
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

#: 内容电平默认（作者侧）：章节 ``bgm.volume`` / 方案 ``voice.volume`` 的缺省。
#: 与旧常量同值（音乐 70、语音 90），保证接入总线后输出不变。
DEFAULT_ITEM_LEVELS = {
    BUS_MASTER: MAX_LEVEL,
    BUS_MUSIC: 70,
    BUS_VOICE: 90,
    BUS_SFX: MAX_LEVEL,
}
#: 总线电平默认（用户侧）：100 = 不衰减。玩家滑条尚未落地，先恒为 100。
DEFAULT_BUS_LEVELS = {bus: MAX_LEVEL for bus in BUSES}

#: 第二步备好的 dB 曲线接口（本步**未接线**）：推荐默认值——语音为 0 dB 参考、
#: 音乐比语音低 6 dB、音效低 3 dB；``DB_FLOOR`` 是静音下限。
DB_FLOOR = -80.0
RECOMMENDED_DB_DEFAULTS = {
    BUS_MASTER: 0.0, BUS_MUSIC: -6.0, BUS_VOICE: 0.0, BUS_SFX: -3.0,
}


def clamp_level(value, default=MAX_LEVEL) -> int:
    """把电平归一到 0-100 整数；非法值回退 ``default``。

    兜底口径与旧的 ``int(float(value))`` + 越界夹取同款：``None`` / 非数字 /
    越界都落到合法区间，行为不变。
    """
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        number = int(default)
    return max(0, min(MAX_LEVEL, number))


def item_default(bus) -> int:
    """某条总线的内容电平默认值（音乐 70 / 语音 90 / 其余 100）。"""
    return DEFAULT_ITEM_LEVELS.get(bus, MAX_LEVEL)


def bus_default(bus) -> int:
    """某条总线的总线电平默认值（当前都是 100）。"""
    return DEFAULT_BUS_LEVELS.get(bus, MAX_LEVEL)


def to_ratio(level) -> float:
    """0-100 电平 → 0.0-1.0 线性增益。"""
    return clamp_level(level) / float(MAX_LEVEL)


def resolve_gain(bus, item=None, bus_level=None, master=None) -> float:
    """算出一路声音的最终线性增益（0.0-1.0）。

    - ``item``：内容电平（章节 / 方案里配的那个数字），缺省取 :func:`item_default`；
    - ``bus_level``：这条总线的用户电平，缺省取 :func:`bus_default`；
    - ``master``：总控电平，缺省取总控默认。

    三者各是 0-100 整数，归一后**相乘**——这就是「总线」的含义：任一层拧小，
    整条链一起变小。
    """
    gain = to_ratio(clamp_level(item, item_default(bus)))
    gain *= to_ratio(clamp_level(bus_level, bus_default(bus)))
    gain *= to_ratio(clamp_level(master, bus_default(BUS_MASTER)))
    return max(0.0, min(1.0, gain))


def to_db(level) -> float:
    """0-100 电平 → dB（``0`` 归到 :data:`DB_FLOOR`）。第二步接 dB 曲线时用。"""
    ratio = to_ratio(level)
    if ratio <= 0.0:
        return DB_FLOOR
    return 20.0 * math.log10(ratio)


def db_gain(db) -> float:
    """dB → 线性增益（:data:`DB_FLOOR` 及以下视为静音）。第二步接 dB 曲线时用。"""
    try:
        value = float(db)
    except (TypeError, ValueError):
        return 1.0
    if value <= DB_FLOOR:
        return 0.0
    return 10.0 ** (value / 20.0)
