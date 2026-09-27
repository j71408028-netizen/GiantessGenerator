"""全屏 NVL（text_nvl）：全屏半透明覆盖层上堆叠全部历史（阅读模式）"""

import os

import dearpygui.dearpygui as dpg

from dungeon import process_log

from .base import (_DEFAULT_LABEL_COLOR, _DEFAULT_TEXT_COLOR,
                   _HIGHLIGHT_COLOR, _TOOLBAR_MARGIN, _TextDisplayBase, _dim)
from dungeon.window.fonts import SERIF_FONT_PATHS, TEXT_FONT_SIZE


class NvlTextComponent(_TextDisplayBase):
    """全屏 NVL 式文本栏：全屏半透明覆盖层上堆叠显示全部历史段落。

    Fate/魔法使之夜式的阅读模式：正文按时间顺序纵向堆叠，旧行降透明度、
    当前（动画中）行全亮，可选衬线字体，覆盖层内可滚轮回看，新段落上屏后
    自动跟随到底部（仅当此前已在本就位于底部时跟随，避免打断回看）。
    """

    id = "text_nvl"
    label = "全屏 NVL 文本栏"
    description = "全屏半透明覆盖层上堆叠全部历史段落，可滚轮回看（阅读模式）。"
    _BLINK_TASK = "text_nvl:blink"
    #: 本组件显式 tag 的统一前缀（守卫据此查命名空间冲突）
    TAG_PREFIX = "text_nvl"
    param_specs = [
        {"key": "bg_color", "label": "覆盖层底色", "type": "color",
         "default": (8, 10, 18, 190)},
        {"key": "label_color", "label": "标签颜色", "type": "color",
         "default": _DEFAULT_LABEL_COLOR},
        {"key": "highlight_color", "label": "高亮颜色", "type": "color",
         "default": _HIGHLIGHT_COLOR,
         "note": "highlight 段落的正文颜色"},
        {"key": "use_serif", "label": "衬线字体", "type": "bool",
         "default": True, "note": "宋体等衬线字体（找不到时回退默认字体）"},
        {"key": "show_indicator", "label": "显示继续指示", "type": "bool",
         "default": True},
    ]

    _OVERLAY = "text_nvl_overlay"     # 全屏覆盖层子窗口
    _INDICATOR = "text_nvl_indicator"
    _TOOLBAR = "text_nvl_toolbar"

    def __init__(self, ctx):
        super().__init__(ctx)
        self._font_tag = None
        self._wrap = 800
        self._pad = 24

    def _create_font(self, ctx):
        """衬线字体只绑到 NVL 文本项，不动全局字体（找不到候选时返回 None）。

        ``add_font`` 必须建在字体注册表容器内（与 ``base._create_bold_font`` 同一
        配方）：否则 DPG 抛错被吞掉，衬线选项静默失效。
        """
        if not bool((self.params or {}).get("use_serif", True)):
            return None
        s = ctx.component_viewport()[0]
        for path in SERIF_FONT_PATHS:
            if os.path.exists(path):
                try:
                    with dpg.font_registry():
                        return dpg.add_font(path, round(TEXT_FONT_SIZE * s))
                except Exception as exc:
                    process_log.log(f"[Components] 衬线字体加载失败 {path}: {exc}")
        return None

    def build(self, ctx):
        self._hide_builtin()
        s, w, h = self._viewport(ctx)
        pad = round(24 * s)
        # 顶部让出属性面板（attr_bar 固定在左上角，全屏覆盖层会与其重叠）：
        # 占位由窗口服务面汇总各组件的 top_inset 钩子，与构建顺序无关
        pad_top = max(pad, ctx.component_top_inset())
        for tag in (self._OVERLAY, self._INDICATOR):
            if dpg.does_item_exist(tag):
                dpg.delete_item(tag)
        if self._font_tag and dpg.does_item_exist(self._font_tag):
            dpg.delete_item(self._font_tag)
        self._font_tag = None

        self._wrap = max(1, w - 2 * pad - round(14 * s))   # 右侧留滚动条宽
        dpg.add_child_window(tag=self._OVERLAY, parent="main_window",
                             pos=[0, 0], width=w, height=h, border=False)
        with dpg.theme() as overlay_theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(
                    dpg.mvThemeCol_ChildBg,
                    (self.params or {}).get("bg_color") or (8, 10, 18, 190))
                dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing,
                                    round(2 * s), round(9 * s))
        dpg.bind_item_theme(self._OVERLAY, overlay_theme)

        self._font_tag = self._create_font(ctx)
        # 衬线字体没有粗体文件，启用衬线时不另建粗体（保持衬线原样）
        self._bold_font = None if self._font_tag else self._create_bold_font(ctx)
        # WindowPadding 对 child_window 不生效（DPG 2.3.1 怪癖）：顶部 spacer 让出
        # 属性面板，行项用 indent 缩进
        dpg.add_spacer(height=pad_top, parent=self._OVERLAY)
        self._pad = pad
        self._line_tags = []

        dpg.add_text("▼ 点击继续 ▼", tag=self._INDICATOR, parent="main_window",
                     color=_dim(_DEFAULT_TEXT_COLOR, 0.8), show=False)
        # 功能工具条：顶部居中（右上角让给过程日志面板）
        self._build_toolbar(ctx)
        self._place_toolbar(ctx, w / 2 + self._toolbar_size[0] / 2,
                            _TOOLBAR_MARGIN * s)
        self._start_blink(ctx)
        self.refresh(ctx)

    def layout(self, ctx):
        if not dpg.does_item_exist(self._OVERLAY):
            return
        s, w, h = self._viewport(ctx)
        pad = round(24 * s)
        self._wrap = max(1, w - 2 * pad - round(14 * s))
        dpg.configure_item(self._OVERLAY, pos=[0, 0], width=w, height=h)
        for _, text_tag in self._line_tags:
            if dpg.does_item_exist(text_tag):
                dpg.configure_item(text_tag, wrap=self._wrap)
        self._place_toolbar(ctx, w / 2 + self._toolbar_size[0] / 2,
                            _TOOLBAR_MARGIN * s)
        self.refresh(ctx)

    def refresh(self, ctx):
        if not dpg.does_item_exist(self._OVERLAY):
            return
        items = getattr(ctx, "story_history", None) or []

        # 跟随判定取自更新前的滚动状态：此前已在底部才自动跟随，回看不被打断
        state = dpg.get_item_state(self._OVERLAY) or {}
        pos = state.get("y_scroll_pos")
        max_scroll = state.get("y_scroll_max")
        follow = pos is None or max_scroll is None or pos >= max_scroll - 6

        # 标签池按需增删（与内置 text_container 同样的池化管理，不每帧重建）
        pad = self._pad
        font = self._font_tag or self._bold_or_text_font(ctx)
        while len(self._line_tags) < len(items):
            label_tag = dpg.add_text("", parent=self._OVERLAY, indent=pad, show=False)
            text_tag = dpg.add_text("", parent=self._OVERLAY,
                                    wrap=self._wrap, indent=pad, show=False)
            if font:
                dpg.bind_item_font(label_tag, font)
                dpg.bind_item_font(text_tag, font)
            self._line_tags.append((label_tag, text_tag))
        while len(self._line_tags) > len(items):
            label_tag, text_tag = self._line_tags.pop()
            for tag in (label_tag, text_tag):
                if dpg.does_item_exist(tag):
                    dpg.delete_item(tag)

        params = self.params or {}
        self._display_line_pairs(items,
                                 params.get("label_color") or _DEFAULT_LABEL_COLOR,
                                 _DEFAULT_TEXT_COLOR,
                                 params.get("highlight_color"),
                                 wrap=self._wrap, label_fmt="{label}")

        self._update_indicator_state(ctx)
        self._sync_toolbar(ctx)
        s, w, h = self._viewport(ctx)
        self._place_indicator(round(w / 2 - 80 * s), h - round(44 * s))

        if follow:
            state = dpg.get_item_state(self._OVERLAY) or {}
            max_scroll = state.get("y_scroll_max")
            if max_scroll:
                dpg.set_y_scroll(self._OVERLAY, max_scroll)

    def destroy(self, ctx):
        self._destroy_toolbar()
        for tag in (self._OVERLAY, self._INDICATOR):
            if dpg.does_item_exist(tag):
                dpg.delete_item(tag)
        if self._font_tag and dpg.does_item_exist(self._font_tag):
            dpg.delete_item(self._font_tag)
        self._font_tag = None
        super().destroy(ctx)
