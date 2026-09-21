"""默认副本显示组件包：文本栏与属性条。

本文件由 dungeon.components 按注册表导入，暴露 ``REGISTRY``：
{组件 id: 组件类}。组件类实现 build/layout/refresh/destroy 生命周期钩子，
ctx 即副本会话窗口实例（dungeon.window.DungeonSessionWindow 的 mixin 组合）。

线程约束：所有钩子都只由主线程调用；组件内不得在后台线程操作 DPG，
刷新经窗口的 _dispatch 调度链（_schedule_text_update / _relayout）完成。
"""

import dearpygui.dearpygui as dpg

from dungeon.components import DungeonComponent


# ---------------------------------------------------------------------------
# 文本栏（text）：故事正文容器
# ---------------------------------------------------------------------------

class TextComponent(DungeonComponent):
    """文本栏组件：管理 text_container 子窗口与动态文本项。

    布局样式由 ctx.layout_style 决定：
      - "story"：文本区占据视口中部
      - "game" / "bottom"：文本区为视口底部矮栏
    旧的「副本窗口视图」设置已移除，layout_style 恒为 "story"；文本一律保留
    全历史，不再区分只显示当前段。
    """

    id = "text"
    param_specs = [
        {"key": "show_overlay", "label": "显示衬底", "type": "bool",
         "default": True},
    ]

    def build(self, ctx):
        if ctx._components_built:
            return
        # 文本容器与衬底已在主窗口构建阶段创建（_build_ui），此处仅作存在性确保
        for tag in ("bg_overlay_child", "text_container"):
            if dpg.does_item_exist(tag):
                dpg.show_item(tag)
        ctx._components_built = True

    def layout(self, ctx):
        style = getattr(ctx, "layout_style", "story") or "story"
        if style not in ("story", "game", "bottom"):
            style = "story"
        if not hasattr(ctx, "_text_wrap_width"):
            ctx._text_wrap_width = 1160
        # 委托给窗口现有布局逻辑（_relayout 内部调用 self._layout_text_container）
        ctx._layout_text_container(style)
        # 是否显示深色衬底
        show_overlay = bool((self.params or {}).get("show_overlay", True))
        if dpg.does_item_exist("bg_overlay_child"):
            if show_overlay:
                dpg.show_item("bg_overlay_child")
            else:
                dpg.hide_item("bg_overlay_child")

    def refresh(self, ctx):
        # 文本项更新由窗口 _update_text_display 统一处理，无需额外操作
        return

    def destroy(self, ctx):
        return


# ---------------------------------------------------------------------------
# 属性条（attr_bar）：演化属性状态栏
# ---------------------------------------------------------------------------

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


class AttrBarComponent(DungeonComponent):
    """属性条：按 evolution_attrs 中 display_state == "show" 的属性实时显示。

    渲染 dungeon_state 的介入度/破坏性/自定义属性/总伤亡；未显式启用的
    属性（collapse/internal）不显示。布局为窗口左上角窄栏。

    可配置参数见 param_specs。
    """

    id = "attr_bar"
    param_specs = [
        {"key": "title_text", "label": "标题文字", "type": "text",
         "default": "属性状态"},
        {"key": "title_color", "label": "标题颜色", "type": "color",
         "default": (255, 225, 150, 255)},
        {"key": "text_color", "label": "数值颜色", "type": "color",
         "default": (230, 235, 245, 255)},
        {"key": "panel_width", "label": "面板宽度", "type": "int",
         "default": 300},
        {"key": "show_title", "label": "显示标题", "type": "bool",
         "default": True},
    ]

    def __init__(self, ctx):
        super().__init__(ctx)
        self._items = []   # [(属性名, 类型, tag)]
        self._title_tag = None

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

    def build(self, ctx):
        if dpg.does_item_exist(_PANEL_TAG):
            dpg.delete_item(_PANEL_TAG)
        self._items = []
        self._title_tag = None
        s = ctx._dpi_scale if hasattr(ctx, "_dpi_scale") else 1.0
        margin = round(20 * s)
        params = self.params or {}
        panel_width = int(params.get("panel_width") or 300)
        title_text = str(params.get("title_text") or "属性状态")
        title_color = params.get("title_color") or (255, 225, 150, 255)
        text_color = params.get("text_color") or (230, 235, 245, 255)
        show_title = bool(params.get("show_title", True))

        # 面板：左上角
        dpg.add_child_window(
            tag=_PANEL_TAG, parent="main_window",
            pos=[margin, margin], width=round(panel_width * s), height=round(120 * s),
            no_scrollbar=True, no_scroll_with_mouse=True, border=False)

        if show_title:
            self._title_tag = dpg.add_text(title_text, parent=_PANEL_TAG, color=title_color)

        attrs = self._resolved_attrs(ctx)
        if not attrs:
            dpg.add_text("（未启用展示属性）", parent=_PANEL_TAG,
                         color=(160, 170, 185, 255), wrap=round((panel_width - 40) * s))
            return

        for attr in attrs:
            label = attr.get("name") or _ATTR_TYPE_LABELS.get(attr.get("type"), "属性")
            tag = dpg.add_text("", parent=_PANEL_TAG, color=text_color)
            self._items.append((label, attr.get("type", "custom"), tag))
        self.refresh(ctx)

    def refresh(self, ctx):
        if not self._items:
            return
        state = getattr(ctx, "dungeon_state", None)
        text_color = (self.params or {}).get("text_color") or (230, 235, 245, 255)
        for label, attr_type, tag in self._items:
            if attr_type == "intrusion":
                value = state.intrusion if state else 0.0
            elif attr_type == "destruction":
                value = state.destruction if state else 0.0
            elif attr_type == "casualty":
                value = state.total_casualties if state else 0.0
            else:
                custom = state.custom_attrs if state else {}
                value = custom.get(label, 0.0)
            if dpg.does_item_exist(tag):
                dpg.configure_item(tag, default_value=f"{label}：{_format_value(attr_type, value)}",
                                   color=text_color)

    def layout(self, ctx):
        if not dpg.does_item_exist(_PANEL_TAG):
            return
        s = ctx._dpi_scale if hasattr(ctx, "_dpi_scale") else 1.0
        margin = round(20 * s)
        panel_width = int((self.params or {}).get("panel_width") or 300)
        dpg.configure_item(_PANEL_TAG, pos=[margin, margin],
                           width=round(panel_width * s), height=round(120 * s))

    def destroy(self, ctx):
        if dpg.does_item_exist(_PANEL_TAG):
            dpg.delete_item(_PANEL_TAG)
        self._items = []
        self._title_tag = None


REGISTRY = {
    TextComponent.id: TextComponent,
    AttrBarComponent.id: AttrBarComponent,
}

__all__ = ["REGISTRY", "TextComponent", "AttrBarComponent"]
