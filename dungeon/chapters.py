"""副本章节模型。

章节不带任何条件字段：进入和离开都由触发器执行（``goto`` 动作）。
章节自身只描述“身处其中时的环境”：

- ``background``：本章节的特定背景（含平滑切换与滤镜）；
- ``sensitivity``：本章节持续生效的敏感效果；
- ``color``：编辑器里该章节的自定义颜色。

触发器通过 ``chapter`` 字段声明所在章节，与 ``precondition_names`` /
``condition`` 一起构成完整的触发条件；``节内计数``（进入本章节后的步数，
见 ``DungeonState.chapter_steps``）可在条件规则中判定。
"""

# 触发器 chapter 字段的特殊取值
CHAPTER_ANY = ""            # 任意章节（含无章节），不做章节限制
CHAPTER_NONE = "__none__"   # 仅当当前没有章节时判定
CHAPTER_ANY_LABEL = "任意章节"
CHAPTER_NONE_LABEL = "（无章节）"

# 章节配色预设：浅色模式基准色，深色模式自动提亮
CHAPTER_COLOR_PRESETS = (
    "#0F766E", "#B45309", "#1D4ED8", "#9D174D", "#3F6212",
    "#7E22CE", "#0E7490", "#B91C1C", "#4D7C0F", "#4338CA",
)

# 敏感效果默认作用于哪个属性；破坏性走性格“重力”，其余走“敏感值”
DEFAULT_SENSITIVITY_ATTR = "介入度"
DESTRUCTION_ATTR = "破坏性"


def default_chapter_color(index: int) -> str:
    return CHAPTER_COLOR_PRESETS[index % len(CHAPTER_COLOR_PRESETS)]


def _lighten(hex_color: str, ratio: float) -> str:
    text = (hex_color or "").lstrip("#")
    if len(text) != 6:
        return "#E0E0E0"
    try:
        channels = [int(text[i:i + 2], 16) for i in (0, 2, 4)]
    except ValueError:
        return "#E0E0E0"
    lifted = [round(c + (255 - c) * ratio) for c in channels]
    return "#" + "".join(f"{c:02X}" for c in lifted)


def shade_for_mode(hex_color: str, theme_mode: str) -> str:
    """深色模式下提亮章节色，保证 Treeview 行文字可读。"""
    if theme_mode == "Dark":
        return _lighten(hex_color, 0.35)
    return hex_color or "#E0E0E0"


def normalize_sensitivity_effect(raw: dict) -> dict:
    """把配置里的敏感效果补全为 ``{attr, strength, objective}``。"""
    raw = raw if isinstance(raw, dict) else {}
    try:
        strength = float(raw.get("strength", 1.0))
    except (TypeError, ValueError):
        strength = 1.0
    try:
        objective = float(raw.get("objective", 0.0))
    except (TypeError, ValueError):
        objective = 0.0
    return {
        "attr": str(raw.get("attr") or DEFAULT_SENSITIVITY_ATTR),
        "strength": strength,
        "objective": objective,
    }


def normalize_chapter(raw, index: int = 0) -> dict:
    """补全章节结构；保留未知字段以便向后兼容。"""
    chapter = dict(raw) if isinstance(raw, dict) else {}
    chapter["name"] = str(chapter.get("name") or "").strip()
    if not chapter.get("color"):
        chapter["color"] = default_chapter_color(index)
    chapter["start"] = bool(chapter.get("start", False))
    chapter["note"] = str(chapter.get("note") or "")

    background = chapter.get("background")
    if not isinstance(background, dict):
        background = {}
    clean_bg = {}
    for key in ("image_path", "smooth_transition", "filter_effect"):
        if background.get(key) is not None:
            clean_bg[key] = background[key]
    chapter["background"] = clean_bg

    sensitivity = chapter.get("sensitivity")
    if not isinstance(sensitivity, list):
        sensitivity = []
    chapter["sensitivity"] = [normalize_sensitivity_effect(e) for e in sensitivity
                              if isinstance(e, dict)]
    return chapter


def normalize_chapters(raw) -> list:
    """规范化章节列表，并保证名称唯一（重名时补序号）。"""
    if not isinstance(raw, list):
        return []
    chapters = []
    used = {}
    for index, item in enumerate(raw):
        chapter = normalize_chapter(item, index)
        if not chapter["name"]:
            continue
        base = chapter["name"]
        count = used.get(base, 0)
        used[base] = count + 1
        if count:
            chapter["name"] = f"{base}_{count}"
        chapters.append(chapter)
    return chapters


def chapter_names(chapters) -> list:
    return [c.get("name") for c in (chapters or []) if c.get("name")]


def find_chapter(chapters, name):
    """按名称查找章节，找不到返回 None。"""
    if not name:
        return None
    for chapter in chapters or []:
        if chapter.get("name") == name:
            return chapter
    return None


def scope_label(scope) -> str:
    """触发器 chapter 字段的展示名。"""
    if not scope:
        return CHAPTER_ANY_LABEL
    if scope == CHAPTER_NONE:
        return CHAPTER_NONE_LABEL
    return str(scope)


def matches_scope(scope, current_chapter) -> bool:
    """判断当前所在章节是否满足触发器的章节限制。"""
    if not scope:
        return True
    if scope == CHAPTER_NONE:
        return not current_chapter
    return current_chapter == scope


def sensitivity_amount(strength, objective, attr, personality) -> float:
    """敏感效果倍率 = 强度 ×（性格敏感值/重力 + 客观影响）。

    破坏性由性格“重力”调制，其余属性由“敏感值”调制；无性格时基底为 0。
    """
    try:
        strength = float(strength)
    except (TypeError, ValueError):
        strength = 1.0
    try:
        objective = float(objective)
    except (TypeError, ValueError):
        objective = 0.0
    if personality is None:
        base = 0.0
    elif attr == DESTRUCTION_ATTR:
        base = getattr(personality, "gravity", 0.0)
    else:
        base = getattr(personality, "sensitivity", 0.0)
    return strength * (base + objective)
