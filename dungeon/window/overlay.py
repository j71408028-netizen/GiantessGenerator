"""窗口级覆盖层服务：文本主组件（及键位）可调出的模态浮层。

服务面（组件经 ctx 调用）：
- ``ctx.toggle_overlay(name)``  调出/关闭覆盖层（``"log"`` 对话记录 / ``"settings"`` 副本设置）
- ``ctx.close_overlay()``       关闭当前覆盖层
- ``ctx.overlay_open()``        查询当前覆盖层名（None = 无覆盖层）
- ``ctx.toggle_settings()``     直接调出/关闭副本设置面板（O 键 / 工具条按钮入口）
- ``ctx.component(cid)``        只读访问兄弟组件实例

覆盖层打开期间窗口进入「阅读模态」：点击只关闭覆盖层、不推进剧情。

立即模式分两处绘制，z 序不同（见 ``ui._draw_frame`` 的图层说明）：

- **根窗口内**： :meth:`_draw_overlay_root` —— 对话记录整块（遮罩 + 面板 +
  滚动子窗）画在这里，压正文、压在小游戏舞台之下；设置面板的**全屏遮罩**也
  画在这里（遮罩必须落在满屏根窗口的 draw list 上才不被面板窗口裁掉）。
- **根窗口 end() 之后的顶层**： :meth:`_draw_overlay_top` —— 设置面板本身
  （卡片窗口，含滑条/勾选/按钮），必须用 ``_panel_flags``，否则会被铺满
  客户区的根窗口整个盖住（同入口面板的规矩）。
"""

from imgui_bundle import imgui

from dungeon import settings as dungeon_settings
from dungeon.audio import buses
from dungeon.window import fonts
from dungeon.window import imgui_util as iu
from dungeon.window.ui import _panel_flags


_HIGHLIGHT_COLOR = (255, 200, 60, 255)
_LABEL_COLOR = (255, 170, 210, 255)
_TEXT_COLOR = (240, 240, 245, 255)

# ---------- 副本设置面板（半透明全屏遮罩 + 居中卡片）----------
# 中性深色卡片 + 与正文一致的一点点暖色点缀：三种文本组件（底部渐变栏 /
# 卡片 / NVL）版式各不相同，面板唯一中立地「浮」在它们之上，所以配色只跟
# 全局标签色（粉）与高亮色（琥珀）呼应，不依赖任一版式。
_SETTINGS_SCRIM = (6, 8, 14, 172)
_SETTINGS_PANEL_BG = (16, 18, 28, 238)
_SETTINGS_PANEL_BORDER = (255, 255, 255, 46)
_SETTINGS_TITLE = (238, 242, 250, 255)
_SETTINGS_HINT = (150, 160, 176, 255)
_SETTINGS_SECTION = (255, 205, 120, 255)
_SETTINGS_LABEL = (212, 218, 228, 255)
_SETTINGS_MUTED = (168, 176, 190, 255)
_SETTINGS_FRAME_BG = (30, 34, 48, 236)
_SETTINGS_FRAME_BG_HOVERED = (44, 50, 68, 246)
_SETTINGS_FRAME_BG_ACTIVE = (54, 62, 84, 250)
_SETTINGS_SLIDER_GRAB = (150, 120, 220, 255)
_SETTINGS_SLIDER_GRAB_ACTIVE = (182, 152, 250, 255)
_SETTINGS_CHECK_MARK = (255, 255, 255, 255)
_SETTINGS_BUTTON = (58, 48, 86, 255)
_SETTINGS_BUTTON_HOVERED = (86, 70, 124, 255)
_SETTINGS_BUTTON_ACTIVE = (70, 56, 100, 255)
_SETTINGS_SEPARATOR = (255, 255, 255, 34)
_SETTINGS_PANEL = "##dungeon_settings"
_SETTINGS_PANEL_W = 560
#: 居中定位用的面板高度估算（实际高度由 ``always_auto_resize`` 按内容定；
#: 这里只决定卡片大致落在屏幕中部）。
_SETTINGS_PANEL_H = 470


def _normalize_family(name):
    """字体家族名归一化（去空白/连字符/下划线、小写），仅用于比对。"""
    return (str(name or "").strip()
            .replace(" ", "").replace("-", "").replace("_", "").lower())


def _font_choice_index(choices, current):
    """当前家族在候选表里的下标；认不出返回 -1。"""
    target = _normalize_family(current)
    if not target:
        return -1
    for index, (display, family) in enumerate(choices):
        if target in (_normalize_family(family), _normalize_family(display)):
            return index
    return -1


class OverlayHandler:
    """覆盖层服务：模态浮层的状态与立即模式绘制。"""

    # ---- 服务面（组件经 ctx 调用） ----
    def overlay_open(self):
        if getattr(self, "_mini_game_stage", None) is not None:
            return "minigame"
        return getattr(self, "_overlay_name", None)

    def toggle_overlay(self, name="log"):
        if self.overlay_open() == name:
            self.close_overlay()
            return
        self.close_overlay()
        if name in ("log", "settings"):
            self._overlay_name = name
        else:
            self._notify(f"未知覆盖层：{name}")

    def close_overlay(self):
        if getattr(self, "_overlay_name", None):
            # 关设置面板前把拖滑条攒下的改动落一次盘（拖拽中不会逐帧写文件）
            self._flush_settings_overlay()
            self._overlay_name = None

    def toggle_settings(self):
        """调出/关闭副本设置面板（O 键 / 工具条齿轮按钮入口）。"""
        if getattr(self, "_is_entry_phase", False) or getattr(self, "_closing", False):
            return
        if getattr(self, "_mini_game_stage", None) is not None:
            return
        if getattr(self, "pending_option", None) is not None:
            self._notify("请先处理当前选项")
            return
        self.toggle_overlay("settings")

    def component(self, cid):
        for comp in getattr(self, "_components", []):
            if comp.id == cid:
                return comp
        return None

    # ---- 键位 ----
    def _on_log_key(self, sender=None, app_data=None):
        if getattr(self, "_is_entry_phase", False) or self._closing:
            return
        self.toggle_overlay("log")

    def _on_settings_key(self, sender=None, app_data=None):
        self.toggle_settings()

    def _on_escape_key(self, sender=None, app_data=None):
        if getattr(self, "_mini_game_stage", None) is not None:
            self._minigame_escape()
            return
        if self.overlay_open():
            self.close_overlay()

    # ---- 立即模式绘制 ----
    def _draw_overlay_root(self, w, h):
        """根窗口内的覆盖层部分（遮罩；对话记录整块都在这里）。"""
        if self.overlay_open() == "settings":
            self._draw_settings_scrim(w, h)
        else:
            self._draw_log_overlay(w, h)

    def _draw_overlay_top(self, w, h):
        """根窗口 ``end()`` 之后的顶层覆盖层窗口（副本设置面板）。"""
        if self.overlay_open() == "settings":
            self._draw_settings_panel(w, h)

    def _draw_log_overlay(self, w, h):
        """对话记录覆盖层：半透明遮罩 + 居中面板 + 历史段落。"""
        if self.overlay_open() != "log":
            self._overlay_drawn = False
            return
        s = max(0.5, float(getattr(self, "_dpi_scale", 1.0) or 1.0))
        pad = round(16 * s)
        panel_w = min(max(320.0, w - 80 * s), 900 * s)
        panel_h = min(max(240.0, h - 120 * s), 600 * s)
        x = max(0.0, (w - panel_w) / 2.0)
        y = max(0.0, (h - panel_h) / 2.0)
        draw_list = imgui.get_window_draw_list()
        draw_list.add_rect_filled(iu.v2(0.0, 0.0), iu.v2(w, h), iu.u32(0, 0, 0, 120))
        radius = round(10 * s)
        draw_list.add_rect_filled(iu.v2(x, y), iu.v2(x + panel_w, y + panel_h),
                                  iu.u32(18, 20, 30, 245), radius)
        draw_list.add_rect(iu.v2(x, y), iu.v2(x + panel_w, y + panel_h),
                           iu.u32(255, 255, 255, 50), radius)
        draw_list.add_text(iu.v2(x + pad, y + pad), iu.u32(230, 235, 245, 255),
                           "对话记录（H / ESC 关闭）")
        title_h = float(imgui.calc_text_size("对话记录").y) + pad
        imgui.set_cursor_pos(iu.v2(x + pad, y + title_h))
        imgui.begin_child("##overlay_log_scroll",
                          iu.v2(max(1.0, panel_w - 2 * pad),
                                max(1.0, panel_h - title_h - pad)),
                          imgui.ChildFlags_.none.value,
                          imgui.WindowFlags_.none.value)
        imgui.push_text_wrap_pos(max(1.0, panel_w - 2 * pad))
        for item in (getattr(self, "story_history", None) or []):
            label = str(item.get("speaker") or "").strip()
            if label:
                imgui.push_style_color(imgui.Col_.text, iu.u32(*_LABEL_COLOR))
                imgui.text(label)
                imgui.pop_style_color()
            body = _HIGHLIGHT_COLOR if item.get("highlight") else _TEXT_COLOR
            imgui.push_style_color(imgui.Col_.text, iu.u32(*body))
            imgui.text_wrapped(str(item.get("text") or ""))
            imgui.pop_style_color()
            imgui.dummy(iu.v2(0.0, 6.0))
        imgui.pop_text_wrap_pos()
        imgui.set_scroll_here_y(1.0)
        imgui.end_child()
        self._overlay_rect = (x, y, panel_w, panel_h)
        self._overlay_drawn = True

    # ---------- 副本设置面板 ----------
    def _draw_settings_scrim(self, w, h):
        """半透明全屏遮罩（画在满屏根窗口的 draw list 上，故不被面板窗口裁掉）。"""
        imgui.get_window_draw_list().add_rect_filled(
            iu.v2(0.0, 0.0), iu.v2(w, h), iu.u32(*_SETTINGS_SCRIM))

    def _push_settings_style(self):
        """设置卡片的半透明深色主题（风格自定，与入口面板同一族但更中性）。"""
        s = max(0.5, float(getattr(self, "_dpi_scale", 1.0) or 1.0))
        imgui.push_style_var(imgui.StyleVar_.window_padding,
                             iu.v2(float(round(16 * s)), float(round(14 * s))))
        imgui.push_style_var(imgui.StyleVar_.item_spacing,
                             iu.v2(float(round(8 * s)), float(round(9 * s))))
        imgui.push_style_var(imgui.StyleVar_.window_rounding, float(round(12 * s)))
        imgui.push_style_var(imgui.StyleVar_.frame_rounding, float(round(6 * s)))
        imgui.push_style_var(imgui.StyleVar_.grab_rounding, float(round(6 * s)))
        imgui.push_style_color(imgui.Col_.window_bg, iu.u32(*_SETTINGS_PANEL_BG))
        imgui.push_style_color(imgui.Col_.border, iu.u32(*_SETTINGS_PANEL_BORDER))
        imgui.push_style_color(imgui.Col_.text, iu.u32(*_SETTINGS_LABEL))
        imgui.push_style_color(imgui.Col_.frame_bg, iu.u32(*_SETTINGS_FRAME_BG))
        imgui.push_style_color(imgui.Col_.frame_bg_hovered,
                               iu.u32(*_SETTINGS_FRAME_BG_HOVERED))
        imgui.push_style_color(imgui.Col_.frame_bg_active,
                               iu.u32(*_SETTINGS_FRAME_BG_ACTIVE))
        imgui.push_style_color(imgui.Col_.slider_grab,
                               iu.u32(*_SETTINGS_SLIDER_GRAB))
        imgui.push_style_color(imgui.Col_.slider_grab_active,
                               iu.u32(*_SETTINGS_SLIDER_GRAB_ACTIVE))
        imgui.push_style_color(imgui.Col_.check_mark,
                               iu.u32(*_SETTINGS_CHECK_MARK))
        imgui.push_style_color(imgui.Col_.button, iu.u32(*_SETTINGS_BUTTON))
        imgui.push_style_color(imgui.Col_.button_hovered,
                               iu.u32(*_SETTINGS_BUTTON_HOVERED))
        imgui.push_style_color(imgui.Col_.button_active,
                               iu.u32(*_SETTINGS_BUTTON_ACTIVE))
        imgui.push_style_color(imgui.Col_.separator,
                               iu.u32(*_SETTINGS_SEPARATOR))

    def _pop_settings_style(self):
        imgui.pop_style_color(13)
        imgui.pop_style_var(5)

    def _settings_section(self, text):
        imgui.push_style_color(imgui.Col_.text, iu.u32(*_SETTINGS_SECTION))
        imgui.text(text)
        imgui.pop_style_color()

    def _settings_int(self, key, default):
        try:
            return int(float((self.dungeon_settings or {}).get(key, default)))
        except (TypeError, ValueError):
            return int(default)

    def _settings_slider(self, label, item_id, value, lo, hi,
                         label_w, slider_w, fmt="%d"):
        """一行「标签 + 滑条」→ ``(changed, value, released)``。

        ``released`` 是这一帧用户松开了编辑（拖拽结束）——调用方先应用改动、
        再据它落盘，保证写盘的是最终值而不是上一帧的中间值。
        """
        imgui.push_style_color(imgui.Col_.text, iu.u32(*_SETTINGS_LABEL))
        imgui.text(label)
        imgui.pop_style_color()
        imgui.same_line(label_w)
        imgui.set_next_item_width(slider_w)
        changed, new_value = imgui.slider_int(item_id, int(value),
                                              int(lo), int(hi), fmt)
        released = bool(imgui.is_item_deactivated_after_edit())
        return changed, new_value, released

    def _apply_settings_live(self, patch, persist=False):
        """窗口内改设置：立即生效；离散改动直接落盘，连续改动先标脏。"""
        self.set_dungeon_settings(patch, persist=persist)
        self._settings_dirty = not persist

    def _flush_settings_overlay(self):
        """把拖滑条攒下的改动落一次盘（松手 / 关面板 / 收尾时调用）。"""
        if getattr(self, "_settings_dirty", False):
            self._settings_dirty = False
            self.set_dungeon_settings({})

    def _cycle_dungeon_font(self, choices, index, step):
        if not choices:
            return
        if index < 0:
            index = 0 if step > 0 else len(choices) - 1
        else:
            index = (index + step) % len(choices)
        display, family = choices[index]
        # 字体图集在加载期构建，运行中不重建：只写进设置，下次进入副本生效。
        self.set_dungeon_settings({"dungeon_font": family})
        self._settings_dirty = False
        self._notify(f"段落字体：{display}（下次进入副本生效）")

    def _draw_settings_panel(self, w, h):
        """副本设置面板：半透明全屏遮罩之上的居中卡片（顶层窗口）。

        只放副本身份的**独占设置**（音量 / 帧率 / 记忆窗口 / 自动回放 / 段落
        字体）。整块浮在三种文本组件之上、遮罩压暗正文，卡片是中性的深色，
        因此底部渐变栏 / 卡片 / NVL 三种版式下都不打架：玩家在哪一版里，
        它都只是「当前画面之上的一张设置卡」。
        """
        s = max(0.5, float(getattr(self, "_dpi_scale", 1.0) or 1.0))
        margin = float(round(24 * s))
        pad = float(round(16 * s))
        panel_w = min(float(round(_SETTINGS_PANEL_W * s)),
                      max(320.0, w - 2 * margin))
        est_h = float(round(_SETTINGS_PANEL_H * s))
        x = max(0.0, (w - panel_w) / 2.0)
        y = max(margin, (h - est_h) / 2.0)

        self._push_ui_font()
        self._push_settings_style()
        imgui.set_next_window_pos(iu.v2(x, y))
        # 宽度钉死、高度按内容自适应（always_auto_resize）
        imgui.set_next_window_size_constraints(
            iu.v2(panel_w, 0.0), iu.v2(panel_w, float(1 << 20)))
        imgui.begin(_SETTINGS_PANEL, None,
                    _panel_flags()
                    | imgui.WindowFlags_.no_scrollbar.value
                    | imgui.WindowFlags_.no_scroll_with_mouse.value
                    | imgui.WindowFlags_.always_auto_resize.value)

        inner_w = max(120.0, panel_w - 2 * pad)
        label_w = float(round(104 * s))
        slider_w = max(80.0, inner_w - label_w - float(round(10 * s)))

        imgui.push_style_color(imgui.Col_.text, iu.u32(*_SETTINGS_TITLE))
        imgui.text("副本设置")
        imgui.pop_style_color()
        imgui.same_line()
        imgui.push_style_color(imgui.Col_.text, iu.u32(*_SETTINGS_HINT))
        imgui.text("　ESC / 点击面板外关闭")
        imgui.pop_style_color()
        imgui.separator()

        # ---- 音量（四条总线，立即生效） ----
        self._settings_section("音量")
        levels = buses.levels_from_settings(self.dungeon_settings)
        for bus in buses.BUSES:
            level = int(levels.get(bus, buses.MAX_LEVEL))
            changed, new_level, released = self._settings_slider(
                buses.BUS_LABELS[bus], f"##dungeon_set_vol_{bus}",
                level, 0, buses.MAX_LEVEL, label_w, slider_w, "%d%%")
            if changed:
                levels[bus] = int(new_level)
                self._apply_settings_live({"audio_levels": dict(levels)})
            if released:
                self._flush_settings_overlay()

        imgui.spacing()
        # ---- 引擎 ----
        self._settings_section("引擎")
        changed, new_fps, released = self._settings_slider(
            "帧率上限", "##dungeon_set_fps",
            self._settings_int("fps_max", dungeon_settings.DEFAULT_FPS_MAX),
            20, 240, label_w, slider_w, "%d")
        if changed:
            self._apply_settings_live({"fps_max": float(new_fps)})
        if released:
            self._flush_settings_overlay()
        changed, new_recent, released = self._settings_slider(
            "记忆窗口", "##dungeon_set_recent",
            self._settings_int("story_recent_count",
                               dungeon_settings.DEFAULT_STORY_RECENT_COUNT),
            2, 200, label_w, slider_w, "%d")
        if changed:
            self._apply_settings_live({"story_recent_count": int(new_recent)})
        if released:
            self._flush_settings_overlay()
        changed, new_replay = imgui.checkbox(
            "自动保存副本回放##dungeon_set_replay",
            bool((self.dungeon_settings or {}).get("auto_save_replay", False)))
        if changed:
            self._apply_settings_live({"auto_save_replay": bool(new_replay)},
                                      persist=True)

        imgui.spacing()
        # ---- 段落字体（改了下一次进入生效） ----
        self._settings_section("段落字体")
        choices = fonts.font_family_choices()
        current = str((self.dungeon_settings or {}).get("dungeon_font")
                      or getattr(self, "dungeon_font", "") or "")
        index = _font_choice_index(choices, current)
        if 0 <= index < len(choices):
            display = choices[index][0]
        else:
            display = current or "跟随系统"
        arrow_w = float(round(34 * s))
        row_h = float(imgui.get_frame_height())
        if imgui.button("<##dungeon_font_prev", iu.v2(arrow_w, row_h)):
            self._cycle_dungeon_font(choices, index, -1)
        imgui.same_line()
        if imgui.button(">##dungeon_font_next", iu.v2(arrow_w, row_h)):
            self._cycle_dungeon_font(choices, index, 1)
        imgui.same_line()
        imgui.push_style_color(imgui.Col_.text, iu.u32(*_SETTINGS_LABEL))
        imgui.text(display)
        imgui.pop_style_color()
        imgui.same_line()
        imgui.push_style_color(imgui.Col_.text, iu.u32(*_SETTINGS_MUTED))
        imgui.text("· 下次进入副本生效")
        imgui.pop_style_color()

        imgui.spacing()
        if imgui.button("完成", iu.v2(inner_w, float(round(34 * s)))):
            self.close_overlay()

        size = imgui.get_window_size()
        self._overlay_rect = (x, y, float(size.x), float(size.y))
        self._overlay_drawn = True
        imgui.end()
        self._pop_settings_style()
        imgui.pop_font()

    def _point_in_overlay(self):
        """鼠标是否落在当前覆盖层面板内（设置卡片用）。"""
        rect = getattr(self, "_overlay_rect", None)
        if not rect:
            return False
        pos = imgui.get_mouse_pos()
        x, y, rw, rh = rect
        return x <= pos.x <= x + rw and y <= pos.y <= y + rh

    def _build_log_overlay(self):
        """P4：无 retained 控件可建；状态由 :meth:`_draw_log_overlay` 每帧读取。"""

    def _relayout_overlays(self):
        """立即模式覆盖层几何每帧按客户区现算，无需重排。"""

    def _destroy_overlays(self):
        self._flush_settings_overlay()
        self._settings_dirty = False
        self._overlay_name = None
        self._overlay_drawn = False
        self._overlay_rect = None
        if getattr(self, "_mini_game_stage", None) is not None:
            self._destroy_mini_game_stage()
