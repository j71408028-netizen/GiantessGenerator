"""属性条（attr_bar）：演化属性状态栏"""

import dearpygui.dearpygui as dpg

from dungeon.window.component_registry import DungeonComponent
from dungeon.window.fonts import UI_FONT_SIZE


_ATTR_TYPE_LABELS = {
    "intrusion": "介入度",
    "destruction": "破坏性",
    "casualty": "总伤亡",
}

_PANEL_TAG = "attr_bar_panel"


def _format_value(attr_type, value):
    if attr_type == "casualty":
        return f"{int(round(value)):,}"
    return f"{value:.2f}"


def _stat_value_color(value):
    """介入度/破坏性的数值颜色：0.5~4.5 绿→黄→红色带（与坐标夹取范围一致）。"""
    t = max(0.0, min(1.0, (value - 0.5) / 4.0))
    if t < 0.5:
        k = t / 0.5
        lo, hi = (80, 200, 120), (255, 200, 60)
    else:
        k = (t - 0.5) / 0.5
        lo, hi = (255, 200, 60), (255, 90, 70)
    return (round(lo[0] + (hi[0] - lo[0]) * k),
            round(lo[1] + (hi[1] - lo[1]) * k),
            round(lo[2] + (hi[2] - lo[2]) * k), 255)


class AttrBarComponent(DungeonComponent):
    """属性条：窗口顶部单行状态栏，实时显示 dungeon_state 演化属性。

    行首是角色名（窗口构造参数 ``name``），其后按 evolution_attrs 中
    display_state == "show" 的属性依次显示「名称 数值」；介入度/破坏性的
    数值按大小着色（绿→黄→红），其余属性用配置的数值颜色。未显式启用的
    属性（collapse/internal）不显示。

    可配置参数见 param_specs。
    """

    id = "attr_bar"
    label = "属性条"
    description = "在窗口左上角实时显示演化属性数值与总伤亡。"
    #: 本组件显式 tag 的统一前缀（守卫据此查命名空间冲突）
    TAG_PREFIX = "attr_bar"
    param_specs = [
        {"key": "name_color", "label": "角色名颜色", "type": "color",
         "default": (255, 225, 150, 255)},
        {"key": "text_color", "label": "数值颜色", "type": "color",
         "default": (230, 235, 245, 255)},
        {"key": "panel_width", "label": "面板宽度", "type": "int",
         "default": 480, "min": 200, "max": 1600},
    ]

    def __init__(self, ctx):
        super().__init__(ctx)
        self._items = []   # [(属性名, 类型, 标签tag, 数值tag)]
        self._name_tag = None
        self._panel_h = 0  # 面板实际高度（单行，build/layout 共用公式）

    def _resolved_attrs(self, ctx):
        """按配置顺序返回要展示的属性定义（display_state == "show"）。"""
        attrs = ctx.evolution_attrs if hasattr(ctx, "evolution_attrs") else []
        resolved = []
        for attr in attrs or []:
            if not isinstance(attr, dict):
                continue
            if attr.get("display_state") != "show":
                continue
            resolved.append(attr)
        return resolved

    def _panel_height(self, ctx, row_count=1):
        """面板高度：单行状态的字号行高预算 + 内边距（× dpi_scale）。"""
        s = ctx.component_viewport()[0]
        pad = round(12 * s)
        return 2 * pad + round((UI_FONT_SIZE + 10) * s)

    def top_inset(self, ctx):
        """本面板占用的顶部高度（外边距 + 面板 + 与下方内容的间距）。

        全屏类组件（如 text_nvl）经 ``ctx.component_top_inset()`` 拿到该值让位，
        组件之间不互读私有几何，也与构建顺序无关。
        """
        s = ctx.component_viewport()[0]
        return round(20 * s) + self._panel_height(ctx, len(self._resolved_attrs(ctx)) or 1) \
            + round(16 * s)

    def build(self, ctx):
        if dpg.does_item_exist(_PANEL_TAG):
            dpg.delete_item(_PANEL_TAG)
        self._items = []
        self._name_tag = None
        s = ctx.component_viewport()[0]
        margin = round(20 * s)
        pad = round(12 * s)
        params = self.params or {}
        panel_width = int(params.get("panel_width") or 480)
        name_color = params.get("name_color") or (255, 225, 150, 255)
        text_color = params.get("text_color") or (230, 235, 245, 255)

        # 面板：左上角单行，圆角半透明衬底（与功能工具条同一套视觉语言）
        self._panel_h = self._panel_height(ctx)
        dpg.add_child_window(
            tag=_PANEL_TAG, parent="main_window",
            pos=[margin, margin], width=round(panel_width * s),
            height=self._panel_h, no_scrollbar=True, no_scroll_with_mouse=True,
            border=True)
        with dpg.theme() as panel_theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (10, 12, 20, 150))
                dpg.add_theme_color(dpg.mvThemeCol_Border, (255, 255, 255, 32))
                dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, round(12 * s))
                dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing,
                                    round(10 * s), round(4 * s))
        dpg.bind_item_theme(_PANEL_TAG, panel_theme)
        # WindowPadding 对 child_window 不生效（DPG 2.3.1 怪癖）：顶部 spacer、
        # 行首 spacer 拼出内边距；整行放同一个水平组里
        dpg.add_spacer(height=pad, parent=_PANEL_TAG)
        with dpg.group(horizontal=True, parent=_PANEL_TAG):
            dpg.add_spacer(width=pad)
            name = str(getattr(ctx, "name", "") or "").strip()
            if name:
                self._name_tag = dpg.add_text(name, color=name_color)

            attrs = self._resolved_attrs(ctx)
            if not attrs:
                dpg.add_text("（未启用展示属性）", color=(160, 170, 185, 255))
                return

            for attr in attrs:
                label = attr.get("name") or _ATTR_TYPE_LABELS.get(attr.get("type"), "属性")
                label_tag = dpg.add_text(label, color=text_color)
                value_tag = dpg.add_text("", color=text_color)
                self._items.append((label, attr.get("type", "custom"),
                                    label_tag, value_tag))
        self.refresh(ctx)

    def _attr_value(self, ctx, attr_type, label):
        state = getattr(ctx, "dungeon_state", None)
        if attr_type == "intrusion":
            return state.intrusion if state else 0.0
        if attr_type == "destruction":
            return state.destruction if state else 0.0
        if attr_type == "casualty":
            return state.total_casualties if state else 0.0
        custom = state.custom_attrs if state else {}
        return custom.get(label, 0.0)

    def refresh(self, ctx):
        if not self._items:
            return
        params = self.params or {}
        text_color = params.get("text_color") or (230, 235, 245, 255)
        for label, attr_type, label_tag, value_tag in self._items:
            value = self._attr_value(ctx, attr_type, label)
            if attr_type in ("intrusion", "destruction"):
                color = _stat_value_color(value)
            else:
                color = text_color
            if dpg.does_item_exist(value_tag):
                dpg.configure_item(value_tag,
                                   default_value=_format_value(attr_type, value),
                                   color=color)

    def layout(self, ctx):
        if not dpg.does_item_exist(_PANEL_TAG):
            return
        s = ctx.component_viewport()[0]
        margin = round(20 * s)
        panel_width = int((self.params or {}).get("panel_width") or 480)
        self._panel_h = self._panel_height(ctx)
        dpg.configure_item(_PANEL_TAG, pos=[margin, margin],
                           width=round(panel_width * s), height=self._panel_h)

    def destroy(self, ctx):
        if dpg.does_item_exist(_PANEL_TAG):
            dpg.delete_item(_PANEL_TAG)
        self._items = []
        self._name_tag = None
