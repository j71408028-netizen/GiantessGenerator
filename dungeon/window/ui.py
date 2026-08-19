"""UI 构建、文本显示、布局自适应与事件回调。"""

import os

import dearpygui.dearpygui as dpg

from dungeon.dispatcher import _dispatch
from dungeon.models import DungeonTextType


_TEXT_COLOR = (255, 255, 255, 255)
_HIGHLIGHT_COLOR = (255, 200, 60, 255)


class DungeonWindowUI:
    # ---------- UI 构建 ----------
    def _build_ui(self):
        dpg.create_context()
        with dpg.texture_registry(tag="dungeon_texture_registry"):
            dpg.add_dynamic_texture(width=1, height=1, default_value=[0.0, 0.0, 0.0, 0.0], tag="bg_texture")

        self.is_fullscreen = False
        (viewport_w, viewport_h, self._dpi_scale,
         self._main_client_w, self._main_client_h) = self._get_initial_viewport_size()
        self._layout_w = viewport_w
        self._layout_h = viewport_h

        font_path = None
        possible_paths = [
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simhei.ttf",
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/STHeiti Medium.ttc",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
        for path in possible_paths:
            if os.path.exists(path):
                font_path = path
                break
        if font_path:
            with dpg.font_registry():
                default_font = dpg.add_font(font_path, round(20 * self._dpi_scale))
            dpg.bind_font(default_font)

        dpg.create_viewport(
            title=self._temp_title,
            width=viewport_w, height=viewport_h, resizable=True,
            min_width=round(800 * self._dpi_scale), min_height=round(500 * self._dpi_scale),
        )

        with dpg.window(label="Main", tag="main_window",
                        width=viewport_w, height=viewport_h,
                        no_title_bar=True, no_move=True, no_resize=True,
                        no_scrollbar=True, no_scroll_with_mouse=True,
                        no_background=True):

            # 背景置于窗口内容流最底部：本 DPG 版本的 viewport_drawlist 不渲染
            # draw_image，背景只能放回主窗口 drawlist（窗口已关闭滚动且内边距为 0，
            # 尺寸与客户区一致时不会产生滚动条）。
            with dpg.drawlist(tag="bg_drawlist", width=viewport_w, height=viewport_h):
                dpg.draw_image(
                    texture_tag="bg_texture", pmin=[0, 0], pmax=[viewport_w, viewport_h],
                    tag="bg_image_item"
                )

            # 文本区深色衬底用独立的无内容子窗口实现（位于文字子窗口后方）：
            # 无内容的子窗口不会触发 DPG 的内容溢出渲染怪癖，而文字子窗口设为
            # 透明，其溢出怪癖不可见。
            with dpg.child_window(
                    tag="bg_overlay_child",
                    pos=[round(40 * self._dpi_scale),
                         round(40 * self._dpi_scale) if self.view_mode == "story"
                         else viewport_h - round(230 * self._dpi_scale)],
                    width=max(1, viewport_w - round(80 * self._dpi_scale)),
                    height=max(1, viewport_h - round(60 * self._dpi_scale))
                    if self.view_mode == "story" else round(190 * self._dpi_scale),
                    no_scrollbar=True, no_scroll_with_mouse=True,
                    border=False,
            ):
                pass

            with dpg.child_window(
                    tag="text_container",
                    pos=[round(40 * self._dpi_scale),
                         round(40 * self._dpi_scale) if self.view_mode == "story"
                         else viewport_h - round(230 * self._dpi_scale)],
                    width=max(1, viewport_w - round(80 * self._dpi_scale)),
                    height=max(1, viewport_h - round(60 * self._dpi_scale))
                    if self.view_mode == "story" else round(190 * self._dpi_scale),
                    horizontal_scrollbar=False,
                    no_scrollbar=False,
                    border=False,
            ):
                pass

        dpg.set_primary_window("main_window", True)

        # DPG 2.3.1 兼容：创建窗口时传入 no_scrollbar/no_scroll_with_mouse 不生效，
        # 必须创建后再 configure_item 应用，否则主窗口右侧会出现滚动条且可滚动。
        dpg.configure_item("main_window", no_scrollbar=True, no_scroll_with_mouse=True,
                           horizontal_scrollbar=False)

        with dpg.theme() as root_theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 0, 0)
        dpg.bind_item_theme("main_window", root_theme)

        with dpg.theme() as container_theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (0, 0, 0, 0))
                padding = round(15 * self._dpi_scale)
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, padding, padding)
        dpg.bind_item_theme("text_container", container_theme)

        with dpg.theme() as overlay_theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (0, 0, 0, 140))
        dpg.bind_item_theme("bg_overlay_child", overlay_theme)

        with dpg.handler_registry():
            dpg.add_mouse_click_handler(button=dpg.mvMouseButton_Left, callback=self._on_mouse_click)
            dpg.add_key_press_handler(key=dpg.mvKey_Spacebar, callback=self._on_key_down)
            dpg.add_key_press_handler(key=dpg.mvKey_Return, callback=self._on_key_down)
            dpg.add_key_press_handler(key=dpg.mvKey_NumPadEnter, callback=self._on_key_down)
            dpg.add_key_press_handler(key=dpg.mvKey_F11, callback=self._toggle_fullscreen)

        dpg.set_viewport_resize_callback(self._on_viewport_resize)
        dpg.set_exit_callback(self._on_close)

        dpg.setup_dearpygui()
        dpg.show_viewport()

        self._correct_viewport_size_to_main()
        self._relayout()
        self._update_text_display()

    # ---------- 文本更新（仅主线程调用） ----------
    def _update_text_display(self):
        if self.view_mode == "game":
            items = self.story_history[-1:]
        else:
            items = self.story_history
        tags = self._text_item_tags
        if len(tags) < len(items):
            for _ in range(len(tags), len(items)):
                tags.append(dpg.add_text(parent="text_container", default_value=""))
        elif len(tags) > len(items):
            for tag in tags[len(items):]:
                dpg.delete_item(tag)
            del tags[len(items):]

        wrap_width = getattr(self, "_text_wrap_width", 0) or 1160
        for tag, item in zip(tags, items):
            dpg.configure_item(
                tag,
                default_value=item["type_str"] + item["text"] + "\n\n",
                wrap=wrap_width,
                color=_HIGHLIGHT_COLOR if item.get("highlight") else _TEXT_COLOR,
            )

        if self.view_mode == "story":
            state = dpg.get_item_state("text_container")
            max_scroll = state.get("y_scroll_max") if state else None
            if max_scroll is not None:
                dpg.set_y_scroll("text_container", max_scroll)

    def _schedule_text_update(self):
        """合并 AI 流式响应产生的密集刷新，避免挤占 resize 布局任务。"""
        if self._text_update_pending or self._closing:
            return
        self._text_update_pending = True
        _dispatch.enqueue(self._flush_text_update)

    def _flush_text_update(self):
        self._text_update_pending = False
        if not self._closing:
            self._update_text_display()

    def _display_text(self, text: str, text_type: DungeonTextType, highlight: bool = False):
        prefix = self._type_prefix(text_type)
        if self.view_mode == "game":
            self.story_history.clear()
        self.story_history.append({"type_str": prefix, "text": text, "highlight": highlight})
        self._update_text_display()

    # ---------- 布局自适应 ----------
    def _update_dpi_scale(self):
        """窗口移动到不同 DPI 的显示器后，重新获取窗口 DPI 并更新边距比例。"""
        if not getattr(self, "_is_windows", False):
            return
        try:
            import ctypes
            hwnd = getattr(self, "_dpg_hwnd", None)
            if not hwnd:
                hwnd = ctypes.windll.user32.FindWindowW(None, self._temp_title)
                self._dpg_hwnd = hwnd
            if hwnd:
                dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
                if dpi > 0:
                    self._dpi_scale = max(0.5, dpi / 96.0)
        except Exception:
            pass

    def _schedule_relayout(self):
        """通过调度器在下一帧执行重排，避免多路 frame callback 相互覆盖。"""
        if getattr(self, "_relayout_pending", False):
            return
        self._relayout_pending = True
        _dispatch.enqueue(self._flush_relayout)

    def _flush_relayout(self):
        self._relayout_pending = False
        self._relayout()

    def _relayout(self):
        """按当前视口客户区大小重排背景与文本区域（窗口缩放/DPI 变化时自动触发）。"""
        self._update_dpi_scale()
        w = dpg.get_viewport_client_width()
        h = dpg.get_viewport_client_height()
        if w <= 0 or h <= 0:
            return
        self._layout_w, self._layout_h = w, h

        if dpg.does_item_exist("main_window"):
            dpg.configure_item("main_window", width=w, height=h)
            # 彻底禁用主窗口滚动：内容区比窗口小约 4px（边框），不归零会被键盘滚动
            dpg.set_x_scroll("main_window", 0)
            dpg.set_y_scroll("main_window", 0)
        if dpg.does_item_exist("bg_drawlist"):
            dpg.configure_item("bg_drawlist", width=w, height=h)
        if dpg.does_item_exist("bg_image_item"):
            dpg.configure_item("bg_image_item", pmin=[0, 0], pmax=[w, h])

        margin_x = round(40 * self._dpi_scale)
        margin_top = round(40 * self._dpi_scale)
        margin_bottom = round(20 * self._dpi_scale)
        cw = max(1, w - 2 * margin_x)
        if self.view_mode == "story":
            cpos, ch = [margin_x, margin_top], h - margin_top - margin_bottom
        else:
            ch = round(190 * self._dpi_scale)
            cpos, ch = [margin_x, h - ch - margin_bottom], ch
        if dpg.does_item_exist("text_container"):
            dpg.configure_item("text_container", pos=cpos, width=cw, height=ch)
        if dpg.does_item_exist("bg_overlay_child"):
            # 衬底延伸到窗口底部：DPG 会把子窗口 ChildBg 在矩形下方约 12~19px 处
            # 重复绘制一截（超大窗口下的渲染怪癖），延伸到底部后该副本被窗口边界
            # 裁剪，不可见。
            dpg.configure_item("bg_overlay_child", pos=cpos, width=cw,
                               height=max(1, h - cpos[1]))
        self._text_wrap_width = max(1, cw - round(40 * self._dpi_scale))
        for tag in self._text_item_tags:
            if dpg.does_item_exist(tag):
                dpg.configure_item(tag, wrap=self._text_wrap_width)

        self._refresh_background()
        self._schedule_text_update()

    def _refresh_background(self, delay=0.08):
        self._background.refresh(delay)

    def _apply_prepared_bg(self, pil_img, dpg_data, w, h, revision):
        if self._closing or revision != self._bg_revision:
            return
        if (w, h) != (self._layout_w, self._layout_h):
            return
        self._apply_bg_data(pil_img, dpg_data, w, h)

    # ---------- 事件回调 ----------
    def _on_mouse_click(self, sender, app_data):
        self._on_next_step()

    def _on_key_down(self, sender, app_data):
        self._on_next_step()

    def _toggle_fullscreen(self, sender, app_data):
        self.is_fullscreen = not self.is_fullscreen
        dpg.toggle_viewport_fullscreen()

    def _on_viewport_resize(self, sender, app_data):
        self._schedule_relayout()

    # ---------- 背景切换 ----------
    def change_background(self, image_path, smooth_transition=False, filter_effect=None):
        self._background.change(image_path, smooth_transition, filter_effect)

    def _apply_bg_data(self, pil_img, dpg_data, w, h):
        self._background.apply_data(pil_img, dpg_data, w, h)

    def _set_bg_texture(self, dpg_data, w, h, revision):
        """仅由主线程通过调度器调用。"""
        if revision != self._bg_revision or (w, h) != (self._layout_w, self._layout_h):
            return
        if dpg.does_item_exist("bg_texture"):
            cur = dpg.get_item_configuration("bg_texture")
            if cur.get("width") == w and cur.get("height") == h:
                dpg.set_value("bg_texture", dpg_data)

    def _finish_bg_fade(self, pil_img, w, h, revision):
        if revision == self._bg_revision and (w, h) == (self._layout_w, self._layout_h):
            self._bg_pil_original = pil_img

    # ---------- 结局图标 ----------
    def _update_ending_icon(self):
        """把结局图标显示在故事区（仅由主线程经调度器调用）。"""
        icon_path = getattr(self, "ending_icon_path", "") or ""
        if not icon_path:
            return
        full_path = self._background.resolve_path(icon_path)
        if not full_path or not os.path.exists(full_path):
            print(f"[Ending] 结局图标文件不存在: {icon_path}")
            return
        try:
            from PIL import Image
            img = Image.open(full_path).convert("RGBA")
            max_side = round(160 * getattr(self, "_dpi_scale", 1.0))
            w, h = img.size
            if max(w, h) > max_side:
                scale = max_side / max(w, h)
                w, h = max(1, int(w * scale)), max(1, int(h * scale))
                img = img.resize((w, h), Image.Resampling.LANCZOS)
            data = list(img.tobytes())
            data = [v / 255.0 for v in data]
        except Exception as e:
            print(f"[Ending] 结局图标加载失败: {e}")
            return
        if dpg.does_alias_exist("ending_icon_texture"):
            dpg.remove_alias("ending_icon_texture")
        if dpg.does_item_exist("ending_icon_texture"):
            dpg.delete_item("ending_icon_texture")
        dpg.add_static_texture(
            width=w, height=h, default_value=data,
            tag="ending_icon_texture", parent="dungeon_texture_registry")
        if dpg.does_item_exist("ending_icon_item"):
            dpg.delete_item("ending_icon_item")
        dpg.add_image(texture_tag="ending_icon_texture", tag="ending_icon_item",
                      parent="text_container")
