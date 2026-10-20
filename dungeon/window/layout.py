"""组件代管区（slot）契约：几何 + 视觉 token + 角色/形状匹配。

文本主组件在 ``layout`` 阶段生成若干代管区（:class:`Slot`），声明自己能接纳
哪些角色（``roles``）、什么形状（``shape``）、能放几个（``capacity``），以及
底纹由谁画（``SlotStyle.surface``）。窗口的 ``ComponentHandler`` 只做机械匹配，
把内容矩形经 ``ctx.component_rect(cid)`` 发给子组件；子组件不再自己发明坐标。

三种底纹归属：

- ``host``：文本组件画 slot 的底纹/边框/分隔线，子组件只画内容（默认，用于
  “卡片延伸”这类视觉关系）；
- ``bare``：没有底纹（例如渐变区上半部），子组件只画内容；
- ``own``：子组件自己画面板，slot 只提供外框。

代管区还可以声明侧向语义（``SlotStyle.side``：``"left"`` / ``"right"``）：
贴边代管区会从对应屏幕边缘平移进入（时长 ``SlotStyle.enter_seconds``），
宿主在绘制阶段把自己的让位动画经 ``Slot.offset_x`` 传给子组件，几何分配
结果本身不变。``SlotStyle.enter_from`` 可覆盖入场方向：默认沿用 ``side``，
设为 ``"bottom"`` 时子组件从代管区下方升起（``enter_rise``），配合
``enter_fade`` 做淡入。``ease_out_cubic`` / ``enter_offset`` / ``enter_rise`` /
``enter_alpha`` 是各组共用的纯数学实现。

本模块是纯数据/几何，不导入 imgui；绘制在 ``component_pack/base.py``。
"""
from dataclasses import dataclass

# 形状：横带 / 竖栏 / 块。组件声明自己支持哪些形状，窗口只做匹配。
SHAPE_BAND = "band"
SHAPE_RAIL = "rail"
SHAPE_TILE = "tile"

# 侧向语义：贴视口哪一侧。空串表示不贴边（例如卡片内部的状态带）。
SIDE_LEFT = "left"
SIDE_RIGHT = "right"

# 入场方向覆盖：从代管区下方升起（配 enter_fade 做淡入）。空串沿用 side。
ENTER_FROM_BOTTOM = "bottom"

# 底纹归属。
SURFACE_HOST = "host"
SURFACE_BARE = "bare"
SURFACE_OWN = "own"


@dataclass(frozen=True)
class SlotStyle:
    """代管区的视觉契约（尺寸均为物理像素，已按 DPI 放大）。"""

    surface: str = SURFACE_HOST
    bg: tuple | None = None
    border: tuple | None = None
    separator: tuple | None = None
    #: 分隔线贴在代管区的哪条边：``top`` / ``bottom``
    separator_side: str = "top"
    radius: float = 0.0
    #: 内边距 (左, 上, 右, 下)；子组件拿到的是已扣掉内边距的内容矩形
    padding: tuple = (8.0, 6.0, 8.0, 6.0)
    label_color: tuple | None = None
    text_color: tuple | None = None
    highlight_color: tuple | None = None
    #: 侧向语义：``"left"`` / ``"right"`` 表示贴视口哪一侧；空串 = 不贴边
    side: str = ""
    #: 入场方向覆盖：空串沿用 ``side``；``"bottom"`` 从代管区下方升起
    enter_from: str = ""
    #: 入场时是否从透明淡入
    enter_fade: bool = False
    #: 入场动画时长（秒）；没有入场方向时不生效
    enter_seconds: float = 0.28


class Slot:
    """一块代管区：几何 + 样式 + 可接纳的角色/形状/容量。"""

    def __init__(self, sid, shape, rect, roles=(), capacity=1, gap=0.0,
                 style=None):
        self.id = str(sid)
        self.shape = str(shape)
        self.rect = tuple(float(v) for v in rect)
        self.roles = tuple(roles or ())
        self.capacity = max(1, int(capacity))
        self.gap = float(gap)
        self.style = style or SlotStyle()
        self.used = 0
        #: 宿主在绘制阶段叠加的临时水平位移（让位动画）；不参与几何分配
        self.offset_x = 0.0

    def content_rect(self):
        """扣掉内边距后的内容矩形。"""
        x, y, w, h = self.rect
        left, top, right, bottom = self.style.padding
        return (x + left, y + top,
                max(1.0, w - left - right), max(1.0, h - top - bottom))

    def accepts(self, roles, shapes):
        """角色与形状是否匹配；``roles`` 为空表示不限制角色。"""
        if self.shape not in shapes:
            return False
        if not self.roles:
            return True
        return bool(set(roles) & set(self.roles))

    def take(self):
        """占用一格，返回内容矩形；满了返回 ``None``。

        容量 > 1 时按形状分格：``band`` 横排、``rail`` 竖排，``tile`` 横排。
        """
        if self.used >= self.capacity:
            return None
        index = self.used
        self.used += 1
        x, y, w, h = self.content_rect()
        if self.capacity == 1:
            return (x, y, w, h)
        if self.shape == SHAPE_RAIL:
            cell = (h - self.gap * (self.capacity - 1)) / self.capacity
            return (x, y + index * (cell + self.gap), w, max(1.0, cell))
        cell = (w - self.gap * (self.capacity - 1)) / self.capacity
        return (x + index * (cell + self.gap), y, max(1.0, cell), h)


@dataclass(frozen=True)
class SlotAssignment:
    """一次分配结果：slot 本身 + 子组件可用的内容矩形。"""

    slot: Slot
    rect: tuple


def select_slot(slots, roles, shapes):
    """按声明顺序找第一个还能装下的匹配 slot；没有返回 ``None``。"""
    for slot in slots or ():
        if slot.used >= slot.capacity:
            continue
        if slot.accepts(roles, shapes):
            return slot
    return None


def ease_out_cubic(progress):
    """入场动画缓动：起步快、收尾慢；``progress`` 会先夹到 ``[0, 1]``。"""
    t = 0.0 if progress < 0.0 else (1.0 if progress > 1.0 else float(progress))
    return 1.0 - (1.0 - t) ** 3


def enter_direction(style):
    """实际入场方向：``enter_from`` 优先，空串沿用 ``side``。"""
    if style is None:
        return ""
    return str(getattr(style, "enter_from", "") or getattr(style, "side", "") or "")


def enter_offset(style, rect, viewport_w, progress):
    """入场动画的当前水平偏移（0 = 已就位）。

    偏移方向由入场方向决定：``left`` 从画面左端进入（起点右缘停在
    ``x = 0``），``right`` 从画面右端进入（起点左缘停在 ``x = viewport_w``）；
    没有水平入场语义（含 ``bottom`` 淡入）的 slot 恒为 0。子组件在自己的
    ``draw`` 里把偏移加到绘制坐标上——几何矩形的分配结果不变，动画纯属呈现层。
    """
    direction = enter_direction(style)
    if direction not in (SIDE_LEFT, SIDE_RIGHT):
        return 0.0
    x, _y, w, _h = rect
    if direction == SIDE_LEFT:
        travel, sign = float(x) + float(w), -1.0
    else:
        travel, sign = float(viewport_w) - float(x), 1.0
    return sign * travel * (1.0 - ease_out_cubic(progress))


def enter_rise(style, rect, viewport_h, progress):
    """入场动画的当前纵向偏移（正数 = 起点在最终位置下方）。

    只有入场方向为 ``bottom`` 时生效：起点上缘落在代管区下缘附近（最多再
    往下补满一屏可见高度），随进度升到最终位置。与 ``enter_offset`` 一样，
    几何分配结果不变。
    """
    if enter_direction(style) != ENTER_FROM_BOTTOM:
        return 0.0
    _x, y, _w, h = rect
    travel = max(0.0, min(float(h), float(viewport_h) - float(y)))
    return travel * (1.0 - ease_out_cubic(progress))


def enter_alpha(style, progress):
    """入场透明度（1 = 完全不透明）；只对 ``enter_fade`` 的代管区生效。"""
    if style is None or not getattr(style, "enter_fade", False):
        return 1.0
    if not enter_direction(style):
        return 1.0
    return ease_out_cubic(progress)


def enter_transform(style, rect, viewport_w, viewport_h, progress):
    """一次取齐入场呈现：``(水平位移, 纵向位移, 透明度)``。"""
    return (enter_offset(style, rect, viewport_w, progress),
            enter_rise(style, rect, viewport_h, progress),
            enter_alpha(style, progress))


__all__ = [
    "SHAPE_BAND", "SHAPE_RAIL", "SHAPE_TILE",
    "SIDE_LEFT", "SIDE_RIGHT", "ENTER_FROM_BOTTOM",
    "SURFACE_HOST", "SURFACE_BARE", "SURFACE_OWN",
    "SlotStyle", "Slot", "SlotAssignment", "select_slot",
    "ease_out_cubic", "enter_direction", "enter_offset", "enter_rise",
    "enter_alpha", "enter_transform",
]
