"""章节效果的「短暂覆盖」——``effect`` 动作的负载模型与合成规则。

章节（``dungeon/audio/chapters.py``）描述的是「身处其中时的环境」：背景滤镜、
背景音乐、对话语音效果、持续敏感效果。本模块描述这套环境的**限时覆盖**：
``effect`` 触发器的 ``action_data`` 声明要临时改哪些维度、改成什么，走完
``duration`` 步后把各维度交还给章节默认值。

三条规则：

- **逐维度**：一条覆盖只写它真要改的维度（见 :data:`OVERRIDE_KEYS`），没写的
  维度继续沿用章节（或更早的覆盖）。四条线互不干扰。
- **后触发优先**：同维度多条并存时取**最后触发**的那条；到期逐条弹出后，下面
  那条（或章节默认值）自然接管。所以「恢复」不是抄一份旧值回去，而是每次都按
  当前生效集合重算一次——中途换章也不会把上一章的值翻出来。
- **显式清除与不覆盖分开**：滤镜写 ``""``、语音效果写 ``{}``、敏感效果写
  ``[]`` 表示「这一段就是没有」，与「不覆盖（沿用章节默认）」是两回事。

本模块不碰运行时状态——覆盖条目怎么存、怎么倒计时在
``dungeon/window/triggers.py``；这里只把配置解析成稳定形状，供运行时、校验器
（``dungeon/validate.py``）与编辑器（``ui/scenario/trigger_dlg.py``）共用同一份口径。
"""

from dungeon.actions import VISUAL_FILTER_KEYS
from dungeon.audio import buses
from dungeon.audio.chapters import normalize_sensitivity_effect
from dungeon.audio.voice_fx import SLOT_LABELS, normalize_voice_fx

#: 可被短暂覆盖的四个维度：与 ``effect`` 动作 ``action_data`` 里的字段同名。
OVERRIDE_KEYS = ("filter", "bgm_level", "voice_fx", "sensitivity")

#: 维度展示名（日志、编辑器、诊断共用）。
OVERRIDE_LABELS = {
    "filter": "背景滤镜",
    "bgm_level": "音乐档位",
    "voice_fx": "语音效果",
    "sensitivity": "敏感效果",
}


def _int_or_none(value):
    """转 int；bool 不算数字（``True`` 当 1 用是笔误的常见来源）。"""
    if isinstance(value, bool):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _bgm_level_or_none(data) -> int:
    """取音乐音量档位的覆盖值；没有该维度或解析不出数字时返回 ``None``。

    优先新键 ``bgm_level``（0-4）；回退旧 0-100 ``bgm_volume``（按响度就近
    折算成档位）。
    """
    if "bgm_level" in data:
        value = _int_or_none(data.get("bgm_level"))
        return None if value is None else buses.clamp_step(value)
    if "bgm_volume" in data:
        value = _int_or_none(data.get("bgm_volume"))
        return None if value is None else buses.step_from_level(value)
    return None


def parse_overrides(action_data) -> dict:
    """从 ``effect`` 动作的 ``action_data`` 抽出覆盖项，只保留**显式写出**的维度。

    - ``filter``：``null`` / 空串 / ``"none"`` → ``""``（显式清除滤镜）；其余按
      原字符串返回，合法键由调用方按 :data:`dungeon.actions.VISUAL_FILTER_KEYS`
      判定（本模块不做取舍，免得把作者的笔误静默吞掉）。
    - ``bgm_level``：夹到 0~4 档的整数（旧键 ``bgm_volume`` 折算成档位）；
      解析不出数字则忽略该维度。
    - ``voice_fx``：``normalize_voice_fx`` 归一（``{}`` = 显式清除）。
    - ``sensitivity``：逐条 ``normalize_sensitivity_effect``（``[]`` = 显式清除）。
    """
    data = action_data if isinstance(action_data, dict) else {}
    out = {}
    if "filter" in data:
        raw = data.get("filter")
        text = "" if raw is None else str(raw).strip()
        out["filter"] = "" if text.lower() in ("", "none") else text
    level = _bgm_level_or_none(data)
    if level is not None:
        out["bgm_level"] = level
    if "voice_fx" in data:
        out["voice_fx"] = normalize_voice_fx(data.get("voice_fx"))
    if "sensitivity" in data:
        raw = data.get("sensitivity")
        items = raw if isinstance(raw, list) else []
        out["sensitivity"] = [normalize_sensitivity_effect(effect)
                              for effect in items if isinstance(effect, dict)]
    return out


def invalid_filter(overrides) -> str:
    """返回越界的滤镜键；没有问题时返回空串（空串本身是「显式清除」，合法）。"""
    key = str((overrides or {}).get("filter") or "")
    if key and key not in VISUAL_FILTER_KEYS:
        return key
    return ""


def active_override(entries, key):
    """取某维度当前生效的覆盖值；没有任何条目覆盖它时返回 ``None``。

    ``entries`` 是覆盖条目序列（先触发的在前），每条形如
    ``{"remaining": n, "overrides": {...}}``。倒序取第一条**写明了该维度**的条目
    ——这就是「后触发优先」。注意返回 ``""`` / ``{}`` / ``[]`` 也是有效覆盖值
    （显式清除），只有 ``None`` 才表示「不覆盖」。
    """
    for entry in reversed(list(entries or [])):
        overrides = (entry or {}).get("overrides") or {}
        if key in overrides:
            return overrides[key]
    return None


def describe(overrides) -> str:
    """覆盖项的一句话描述（日志用）。"""
    parts = []
    for key in OVERRIDE_KEYS:
        if key not in (overrides or {}):
            continue
        value = overrides[key]
        label = OVERRIDE_LABELS.get(key, key)
        if key == "filter":
            parts.append(f"{label}={value or '清除'}")
        elif key == "bgm_level":
            parts.append(f"{label}={buses.step_label(value)}")
        elif key == "voice_fx":
            slots = "、".join(SLOT_LABELS.get(slot, slot) for slot in value)
            parts.append(f"{label}={slots or '清除'}")
        else:
            parts.append(f"{label}={'清除' if not value else f'{len(value)}条'}")
    return "，".join(parts) if parts else "（无）"
