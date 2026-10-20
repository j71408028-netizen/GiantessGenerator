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

本模块是纯数据/几何，不导入 imgui；绘制在 ``component_pack/base.py``。
"""
from dataclasses import dataclass

# 形状：横带 / 竖栏 / 块。组件声明自己支持哪些形状，窗口只做匹配。
SHAPE_BAND = "band"
SHAPE_RAIL = "rail"
SHAPE_TILE = "tile"

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


__all__ = [
    "SHAPE_BAND", "SHAPE_RAIL", "SHAPE_TILE",
    "SURFACE_HOST", "SURFACE_BARE", "SURFACE_OWN",
    "SlotStyle", "Slot", "SlotAssignment", "select_slot",
]
