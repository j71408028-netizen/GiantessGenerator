"""属性条（attr_bar）：演化属性状态栏"""

from imgui_bundle import imgui

from dungeon.window import imgui_util as iu
from dungeon.window.component_registry import DungeonComponent
from dungeon.window.fonts import UI_FONT_SIZE


_ATTR_TYPE_LABELS = {
    "intrusion": "介入度",
    "destruction": "破坏性",
    "casualty": "总伤亡",
}
_TEXT_COLOR = (230, 235, 245, 255)
_NAME_COLOR = (255, 225, 150, 255)


def _format_value(attr_type, value):
    if attr_type == "casualty":
        return f"{int(round(value)):,}"
    return f"{value:.2f}"


def _stat_value_color(value):
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
    """属性条：窗口左上角单行状态栏，实时显示 dungeon_state 演化属性。"""

    id = "attr_bar"
    label = "属性条"
    description = "在窗口左上角实时显示演化属性数值与总伤亡。"
    TAG_PREFIX = "attr_bar"
    param_specs = [
        {"key": "name_color", "label": "角色名颜色", "type": "color",
         "default": _NAME_COLOR},
        {"key": "text_color", "label": "数值颜色", "type": "color",
         "default": _TEXT_COLOR},
        {"key": "panel_width", "label": "面板宽度", "type": "int",
         "default": 480, "min": 200, "max": 1600},
    ]

    def __init__(self, ctx):
        super().__init__(ctx)
        self._layout = None

    def _resolved_attrs(self, ctx):
        attrs = getattr(ctx, "evolution_attrs", None) or []
        return [attr for attr in attrs
                if isinstance(attr, dict) and attr.get("display_state") == "show"]

    def _panel_height(self, ctx, row_count=1):
        s = ctx.component_viewport()[0]
        pad = round(12 * s)
        return 2 * pad + round((UI_FONT_SIZE + 10) * s)

    def top_inset(self, ctx):
        s = ctx.component_viewport()[0]
        return (round(20 * s) + self._panel_height(ctx)
                + round(16 * s))

    def build(self, ctx):
        self.layout(ctx)

    def layout(self, ctx):
        s, _w, _h = ctx.component_viewport()
        self._layout = (s, round(20 * s), int((self.params or {}).get("panel_width") or 480),
                        self._panel_height(ctx))
        _s, margin, panel_w, panel_h = self._layout
        self._panel_rect = (margin, margin, panel_w * s, panel_h)

    def _attr_value(self, ctx, attr_type, label):
        state = getattr(ctx, "dungeon_state", None)
        if state is None:
            return 0.0
        if attr_type == "intrusion":
            return state.intrusion
        if attr_type == "destruction":
            return state.destruction
        if attr_type == "casualty":
            return state.total_casualties
        custom = getattr(state, "custom_attrs", {}) or {}
        return custom.get(label, 0.0)

    def draw(self, ctx):
        if self._layout is None:
            self.layout(ctx)
        s, margin, panel_w, panel_h = self._layout
        panel_w = round(panel_w * s)
        params = self.params or {}
        draw_list = imgui.get_window_draw_list()
        pmin = iu.v2(margin, margin)
        pmax = iu.v2(margin + panel_w, margin + panel_h)
        radius = round(12 * s)
        draw_list.add_rect_filled(pmin, pmax, iu.u32(10, 12, 20, 150), radius)
        draw_list.add_rect(pmin, pmax, iu.u32(255, 255, 255, 32), radius)
        name_color = params.get("name_color") or _NAME_COLOR
        text_color = params.get("text_color") or _TEXT_COLOR
        pad = round(12 * s)
        gap = round(10 * s)
        y = margin + pad
        x = margin + pad
        name = str(getattr(ctx, "name", "") or "").strip()
        if name:
            draw_list.add_text(iu.v2(x, y), iu.u32(*name_color), name)
            x += float(imgui.calc_text_size(name).x) + gap
        attrs = self._resolved_attrs(ctx)
        if not attrs:
            draw_list.add_text(iu.v2(x, y), iu.u32(160, 170, 185, 255),
                               "（未启用展示属性）")
            return
        for attr in attrs:
            label = attr.get("name") or _ATTR_TYPE_LABELS.get(attr.get("type"), "属性")
            attr_type = attr.get("type", "custom")
            value = self._attr_value(ctx, attr_type, label)
            label_text = f"{label} "
            value_text = _format_value(attr_type, value)
            draw_list.add_text(iu.v2(x, y), iu.u32(*text_color), label_text)
            x += float(imgui.calc_text_size(label_text).x)
            value_color = _stat_value_color(value) if attr_type in ("intrusion", "destruction") else text_color
            draw_list.add_text(iu.v2(x, y), iu.u32(*value_color), value_text)
            x += float(imgui.calc_text_size(value_text).x) + gap

    def refresh(self, ctx):
        self.layout(ctx)

    def destroy(self, ctx):
        self._layout = None
