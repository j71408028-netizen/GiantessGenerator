"""UI 构建、文本显示、布局自适应与事件回调。"""

import os
import sys
import time

import dearpygui.dearpygui as dpg

from dungeon import process_log
from dungeon.models import DungeonTextType
from dungeon.window.fonts import (BOLD_FONT_SIZE, TEXT_FONT_SIZE, UI_FONT_SIZE,
                                  resolve_font_files)


_TEXT_COLOR = (255, 255, 255, 255)
_HIGHLIGHT_COLOR = (255, 200, 60, 255)

# ---------- 通知窗（窗口级常驻，不受组件配置影响） ----------
_NOTIFY_WIDTH = 420
_NOTIFY_MARGIN = 8
_NOTIFY_DURATION = 3.0
_NOTIFY_COLOR = (245, 240, 225, 255)

# 仿流式输出：每次推进的字符数与间隔（约 65 字/秒）
_ANIM_CHARS_PER_TICK = 2
_ANIM_TICK_SECONDS = 0.03
#: 仿流式动画的帧任务 key（同一时刻只有一条动画，新的顶掉旧的）
_TEXT_ANIM_TASK = "text:anim"

# 自动播放：等待输入时的推进节奏（秒）；帧任务 key 与播放开关配套
_AUTOPLAY_INTERVAL = 1.2
_AUTOPLAY_TASK = "autoplay:tick"


class DungeonWindowUI:
    # ---------- UI 构建 ----------
    def _build_ui(self):
        dpg.create_context()

        self.is_fullscreen = False
        (viewport_w, viewport_h, self._dpi_scale,
         self._main_client_w, self._main_client_h) = self._get_initial_viewport_size()
        self._layout_w = viewport_w
        self._layout_h = viewport_h

        # 背景纹理一次建到「客户区 ∨ 主显示器物理尺寸」的较大者：全屏客户区
        # 即显示器尺寸，切换窗口/全屏因此永远走 set_value 快速路径、不重建
        # 纹理（DPG 对动态纹理改尺寸有存储复用怪癖，见 background.apply_data）。
        # 数据贴进纹理左上角，由 draw_image 的 uv 子区域映射铺到窗口矩形。
        tex_w, tex_h = self._main_client_w, self._main_client_h
        if sys.platform.startswith("win"):
            try:
                import ctypes
                user32 = ctypes.windll.user32
                tex_w = max(tex_w, user32.GetSystemMetrics(0))    # SM_CXSCREEN
                tex_h = max(tex_h, user32.GetSystemMetrics(1))    # SM_CYSCREEN
            except Exception:
                pass
        with dpg.texture_registry(tag="dungeon_texture_registry"):
            dpg.add_dynamic_texture(
                width=tex_w, height=tex_h,
                default_value=[0.0] * (tex_w * tex_h * 4),
                tag="bg_texture")

        # 布局尺寸以主窗口客户区为基准；视口外框（标题栏/边框）尺寸只用于
        # 创建视口，参与布局会因客户区查询时机不同造成首图尺寸不匹配。
        self._layout_w = self._main_client_w
        self._layout_h = self._main_client_h

        # 副本字体：主程序设置 dungeon_font（家族名，经构造参数 / 宿主端口缺省）
        # → DPG 需要的字体文件路径；字号与候选链的唯一真相源是 dungeon/window/fonts.py。
        # 全局默认 UI_FONT_SIZE（工具条/属性栏/通知等），文本组件正文 TEXT_FONT_SIZE
        # （经 dungeon_text_font 绑到组件文本项上），dungeon_bold_font 是粗体（可能 None）。
        font_path, bold_path = self._resolve_dungeon_font_files()
        if font_path:
            with dpg.font_registry():
                default_font = dpg.add_font(font_path, round(UI_FONT_SIZE * self._dpi_scale))
                text_font = dpg.add_font(font_path, round(TEXT_FONT_SIZE * self._dpi_scale))
                bold_font = (dpg.add_font(bold_path, round(BOLD_FONT_SIZE * self._dpi_scale))
                             if bold_path else None)
            dpg.bind_font(default_font)
            self.dungeon_text_font = text_font
            self.dungeon_bold_font = bold_font

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
                    pos=[round(40 * self._dpi_scale), round(40 * self._dpi_scale)],
                    width=max(1, viewport_w - round(80 * self._dpi_scale)),
                    height=max(1, viewport_h - round(60 * self._dpi_scale)),
                    no_scrollbar=True, no_scroll_with_mouse=True,
                    border=False,
            ):
                pass

            with dpg.child_window(
                    tag="text_container",
                    pos=[round(40 * self._dpi_scale), round(40 * self._dpi_scale)],
                    width=max(1, viewport_w - round(80 * self._dpi_scale)),
                    height=max(1, viewport_h - round(60 * self._dpi_scale)),
                    horizontal_scrollbar=False,
                    no_scrollbar=False,
                    border=False,
            ):
                pass

            # 通知窗：窗口级常驻（_notify 显示数秒自动隐藏），不受组件配置影响
            self._build_notification(viewport_w)

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
            # F12：过程日志面板开关（proc_log 组件未配置时给禁用通知）
            dpg.add_key_press_handler(key=dpg.mvKey_F12, callback=self._on_proc_log_hotkey)
            # H：对话记录覆盖层开关；ESC：关闭已打开的覆盖层（overlay.py 服务面）
            dpg.add_key_press_handler(key=dpg.mvKey_H, callback=self._on_log_key)
            dpg.add_key_press_handler(key=dpg.mvKey_Escape, callback=self._on_escape_key)
            # A：自动播放开关；F2：截图（与文本组件工具条按钮共用同一入口）
            dpg.add_key_press_handler(key=dpg.mvKey_A, callback=self._on_autoplay_hotkey)
            dpg.add_key_press_handler(key=dpg.mvKey_F2, callback=self._on_screenshot_hotkey)

        dpg.set_viewport_resize_callback(self._on_viewport_resize)
        # 不注册 set_exit_callback：手动渲染模式下它在 destroy_context() 内部才
        # 触发（太晚，清理已无从下手）。关闭改由帧循环的 is_dearpygui_running()
        # 判定，并在退出分支里调用 _on_close（见 base._run_frame_loop）。

        dpg.setup_dearpygui()
        dpg.show_viewport()

        self._correct_viewport_size_to_main()
        self._relayout()
        self._update_text_display()

    def _resolve_dungeon_font_files(self):
        """把设置的副本字体家族名解析成 DPG 用的 (常规文件, 粗体文件|None)。

        具体映射与候选链在 ``dungeon/window/fonts.py``（窗口层唯一真相源），
        组件包也从那里取同一份，避免两处漂移。
        """
        return resolve_font_files(getattr(self, "dungeon_font", ""))

    # ---------- 通知窗（窗口级常驻，不受组件配置影响） ----------
    def _build_notification(self, viewport_w):
        """顶部居中的轻通知条：_notify(text) 显示数秒后自动隐藏。"""
        s = self._dpi_scale
        margin = round(_NOTIFY_MARGIN * s)
        self._notify_width = min(round(_NOTIFY_WIDTH * s),
                                 max(1, viewport_w - 2 * margin))
        with dpg.child_window(
                tag="notify_panel", parent="main_window",
                pos=[max(0, (viewport_w - self._notify_width) // 2), margin],
                width=self._notify_width, height=round(40 * s),
                show=False, no_scrollbar=True, no_scroll_with_mouse=True,
                border=False):
            dpg.add_text(tag="notify_text", default_value="",
                         color=_NOTIFY_COLOR, wrap=max(1, self._notify_width - 2 * round(8 * s)))

        with dpg.theme() as notify_theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (30, 26, 18, 215))
                pad = round(8 * s)
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, pad, pad)
        dpg.bind_item_theme("notify_panel", notify_theme)

    def _notify(self, text):
        """显示一条轻通知（仅主线程调用），约 3 秒后自动隐藏。

        重复调用覆盖前一条文字并重置隐藏计时（``after`` 的同 key 任务互斥）。
        """
        if not dpg.does_item_exist("notify_panel"):
            return
        dpg.configure_item("notify_text", default_value=str(text))
        dpg.configure_item("notify_panel", show=True)
        self._frame.after(_NOTIFY_DURATION, self._hide_notification,
                          key="notify:autohide")

    def _hide_notification(self):
        if dpg.does_item_exist("notify_panel"):
            dpg.configure_item("notify_panel", show=False)

    def _layout_notification(self, w):
        """视口重排时让通知条回到顶部居中。"""
        if not dpg.does_item_exist("notify_panel"):
            return
        dpg.configure_item(
            "notify_panel",
            pos=[max(0, (w - self._notify_width) // 2),
                 round(_NOTIFY_MARGIN * self._dpi_scale)])

    # ---------- 过程日志开关（F12） ----------
    def _proc_log_component(self):
        """当前会话已构建的 proc_log 组件；方案未配置时返回 None。"""
        for comp in getattr(self, "_components", []):
            if getattr(comp, "id", "") == "proc_log":
                return comp
        return None

    def _on_proc_log_hotkey(self, sender=None, app_data=None):
        """F12：切换过程日志面板；日志组件未配置时给出禁用通知。"""
        comp = self._proc_log_component()
        if comp is None:
            self._notify("日志查看已禁用")
            return
        comp.toggle(self)   # toggle 是基类声明的可选钩子（见契约）

    def toggle_proc_log(self):
        """调出 / 收起过程日志面板（文本组件工具条的按钮入口，与 F12 等效）。"""
        self._on_proc_log_hotkey()

    # ---------- 工具功能：自动播放 / 截图（文本组件工具条与快捷键共用） ----------
    def toggle_autoplay(self):
        """切换自动播放：等待输入时按固定节奏自动推进（会话与回放均适用）。

        选项弹窗 / 结局 / 生成 / 动画期间自动暂停，无需人工干预；再次切换关闭。
        """
        if getattr(self, "_is_entry_phase", False):
            return
        self._autoplay = not getattr(self, "_autoplay", False)
        if self._autoplay:
            self._frame.every(_AUTOPLAY_INTERVAL, self._autoplay_tick,
                              key=_AUTOPLAY_TASK)
        else:
            self._frame.cancel(_AUTOPLAY_TASK)
        self._notify("自动播放已开启" if self._autoplay else "自动播放已关闭")
        self._refresh_components()   # 工具条按钮同步「自动中」状态标签

    def _autoplay_tick(self):
        if self._closing or not getattr(self, "_autoplay", False):
            return
        if self.overlay_open():
            return
        # 与继续指示的等待条件同源（组件服务面 session_waiting_for_input）
        if self.session_waiting_for_input():
            self._on_next_step()

    def _on_autoplay_hotkey(self, sender=None, app_data=None):
        self.toggle_autoplay()

    def take_screenshot(self):
        """把当前帧保存为 PNG 到用户数据区 screenshots/ 下（下一帧落盘）。"""
        from paths import data_dir
        folder = os.path.join(data_dir(), "user", "screenshots")
        try:
            os.makedirs(folder, exist_ok=True)
            filename = time.strftime("dungeon_%Y%m%d_%H%M%S.png")
            path = os.path.join(folder, filename)
            dpg.output_frame_buffer(file=path)
        except Exception as exc:
            process_log.log(f"[Screenshot] 截图失败: {exc}")
            self._notify(f"截图失败：{exc}")
            return
        # 通知延后到截图落盘之后弹出，避免通知条被截进画面
        self._frame.after(0.3, self._notify, f"截图已保存：{filename}")

    def _on_screenshot_hotkey(self, sender=None, app_data=None):
        self.take_screenshot()

    # ---------- 组件工具条（toolbar_hovered 是基类声明的可选钩子，见契约） ----------
    def _component_toolbar_hovered(self):
        """光标是否悬停在某个组件的功能按钮工具条上。"""
        return any(comp.toolbar_hovered()
                   for comp in getattr(self, "_components", []))

    # ---------- 文本更新（仅主线程调用） ----------
    def _text_owned_by_component(self) -> bool:
        """是否有组件接管文本显示（owns_text_display，如底部渐变式文本栏）。

        接管期间内置 text_container 管线整体跳过，显示由组件 refresh 完成。
        """
        return (getattr(self, "_components_built", False)
                and any(getattr(c, "owns_text_display", False)
                        for c in getattr(self, "_components", [])))

    def _update_text_display(self):
        # 组件接管文本显示：转调组件刷新链后返回（不维护 text_container）
        if self._text_owned_by_component():
            self._refresh_components()
            return
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
            speaker = item.get("speaker")
            dpg.configure_item(
                tag,
                default_value=(item["type_str"]
                               + (f"【{speaker}】" if speaker else "")
                               + item["text"] + "\n\n"),
                wrap=wrap_width,
                color=_HIGHLIGHT_COLOR if item.get("highlight") else _TEXT_COLOR,
            )

        # 保留全历史：新段落上屏后自动滚到底部
        state = dpg.get_item_state("text_container")
        max_scroll = state.get("y_scroll_max") if state else None
        if max_scroll is not None:
            dpg.set_y_scroll("text_container", max_scroll)
        self._refresh_components()

    def _schedule_text_update(self):
        """合并 AI 流式响应产生的密集刷新，避免挤占 resize 布局任务。"""
        if self._text_update_pending or self._closing:
            return
        self._text_update_pending = True
        self._frame.call(self._flush_text_update)

    def _flush_text_update(self):
        self._text_update_pending = False
        if not self._closing:
            # 两条分支（组件接管 / 内置容器）都会转调 _refresh_components
            self._update_text_display()

    def _display_text(self, text: str, text_type: DungeonTextType, highlight: bool = False,
                      speaker: str = None):
        prefix = self._display_type_prefix(text_type)
        self.story_history.append({"type_str": prefix, "text": text,
                                   "highlight": highlight, "speaker": speaker})
        self._update_text_display()

    def _reveal_pending_unit(self):
        """揭示当前逻辑段落的下一个显示段落（内置分句器切出的后续句）。"""
        if not self._pending_units:
            return
        unit = self._pending_units.pop(0)
        # 首句已带段落类型前缀，后续句是同一逻辑段落的延续，不再加前缀；
        # 说话人按单元携带的标记解析结果（Solea/Bulla 对话分支）
        item = {"type_str": "", "text": unit.text, "speaker": unit.speaker}
        self.story_history.append(item)
        self._update_text_display()
        self._animate_reveal(item, unit.text)

    # ---------- 仿流式输出 ----------
    def _animate_reveal(self, item, text: str):
        """让新上屏的显示段落逐字增长，模拟 AI 流式输出的节奏。

        动画由帧时钟驱动（L3）：一条 repeating 帧任务每 ``_ANIM_TICK_SECONDS``
        推进两个字，取代过去"每句话新起一条线程 + ``sleep`` 轮询 ``_closing``"。
        动画期间再点击一次由 ``_finish_text_animation`` 立即补完。
        """
        self._finish_text_animation()
        if not text:
            return
        state = {"item": item, "text": text, "pos": 0}
        self._text_anim_state = state

        def step():
            # 会话关闭 / 已被后续动画或跳过接管：收摊
            if self._closing or self._text_anim_state is not state:
                self._frame.cancel(_TEXT_ANIM_TASK)
                return
            state["pos"] = min(len(text), state["pos"] + _ANIM_CHARS_PER_TICK)
            item["text"] = text[:state["pos"]]
            if state["pos"] >= len(text):
                self._text_anim_state = None
                self._frame.cancel(_TEXT_ANIM_TASK)
            # _schedule_text_update 经帧队列投递，本帧末尾就会上屏
            self._schedule_text_update()

        self._frame.every(_ANIM_TICK_SECONDS, step, key=_TEXT_ANIM_TASK)

    def _finish_text_animation(self):
        """立即补完进行中的仿流式动画（点击跳过逐字过程）。"""
        state = getattr(self, "_text_anim_state", None)
        if not state:
            return
        state["item"]["text"] = state["text"]
        self._text_anim_state = None
        frame = getattr(self, "_frame", None)
        if frame is not None:
            frame.cancel(_TEXT_ANIM_TASK)
        self._schedule_text_update()

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
        self._frame.call(self._flush_relayout)

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
        # 入口阶段背景几何由 Ken Burns 运动接管，避免 resize 时与运动基准冲突
        if dpg.does_item_exist("bg_image_item") and not getattr(self, "_is_entry_phase", False):
            dpg.configure_item("bg_image_item", pmin=[0, 0], pmax=[w, h])

        if getattr(self, "_is_entry_phase", False):
            self._relayout_entry_panels(w, h)
        else:
            # 会话阶段：组件接管布局；未构建组件时回退到内置几何
            if getattr(self, "_components_built", False):
                self._relayout_components()
            else:
                margin_x = round(40 * self._dpi_scale)
                margin_top = round(40 * self._dpi_scale)
                margin_bottom = round(20 * self._dpi_scale)
                cw = max(1, w - 2 * margin_x)
                cpos, ch = [margin_x, margin_top], h - margin_top - margin_bottom
                if dpg.does_item_exist("text_container"):
                    dpg.configure_item("text_container", pos=cpos, width=cw, height=ch)
                if dpg.does_item_exist("bg_overlay_child"):
                    dpg.configure_item("bg_overlay_child", pos=cpos, width=cw,
                                       height=max(1, h - cpos[1]))
                self._text_wrap_width = max(1, cw - round(40 * self._dpi_scale))
                for tag in self._text_item_tags:
                    if dpg.does_item_exist(tag):
                        dpg.configure_item(tag, wrap=self._text_wrap_width)

        self._layout_notification(w)
        self._relayout_overlays()
        self._refresh_background()
        self._schedule_text_update()

    def _relayout_entry_panels(self, w, h):
        """入口阶段：右下角方案选择面板与左下角结局图标面板跟随窗口尺寸。"""
        s = self._dpi_scale
        margin = round(20 * s)
        if dpg.does_item_exist("panel_controls"):
            cw, ch = getattr(self, "_panel_controls_size", (round(360 * s), round(230 * s)))
            dpg.configure_item("panel_controls", pos=[w - cw - margin, h - ch - margin],
                               width=cw, height=ch)
        if dpg.does_item_exist("panel_endings"):
            pw, ph = getattr(self, "_panel_endings_rect", (round(300 * s), round(210 * s)))
            dpg.configure_item("panel_endings", pos=[margin, h - ph - margin],
                               width=pw, height=ph)

    def _refresh_background(self, delay=0.08):
        if getattr(self, "_bg_refresh_fast", False):
            # 全屏切换等离散跳变：防抖归零，重采样立即上工作者
            self._bg_refresh_fast = False
            delay = 0.0
        self._background.refresh(delay)

    def _apply_prepared_bg(self, pil_img, dpg_data, w, h, revision):
        if self._closing or revision != self._bg_revision:
            return
        if (w, h) != (self._layout_w, self._layout_h):
            return
        self._apply_bg_data(pil_img, dpg_data, w, h)

    # ---------- 事件回调 ----------
    def _on_mouse_click(self, sender, app_data):
        if getattr(self, "_is_entry_phase", False):
            return
        # 点击落在组件工具条上：只触发按钮本身，不当作"点击推进剧情"
        if self._component_toolbar_hovered():
            return
        if self.overlay_open():
            # 阅读模态：点击只关闭覆盖层，不推进剧情
            self.close_overlay()
            return
        self._on_next_step()

    def _on_key_down(self, sender, app_data):
        if getattr(self, "_is_entry_phase", False):
            return
        self._on_next_step()

    def _toggle_fullscreen(self, sender, app_data):
        self.is_fullscreen = not self.is_fullscreen
        dpg.toggle_viewport_fullscreen()
        # 离散尺寸跳变：立刻重排 + 背景重采样绕过防抖（连续拖拽缩放的
        # 防抖仍由 resize 回调路径保留）
        self._bg_refresh_fast = True
        self._schedule_relayout()

    def _on_viewport_resize(self, sender, app_data):
        self._schedule_relayout()

    # ---------- 背景切换 ----------
    def change_background(self, image_path, smooth_transition=False,
                          filter_effect=None, rotate_angle=None, blur_radius=None):
        self._background.change(image_path, smooth_transition, filter_effect,
                                rotate_angle, blur_radius)

    def _apply_bg_data(self, pil_img, dpg_data, w, h):
        self._background.apply_data(pil_img, dpg_data, w, h)

    def _set_bg_texture(self, dpg_data, w, h, revision):
        """淡入淡出中间帧上纹理（仅由主线程通过调度器调用）。

        pil_img 传 None：中间帧不更新 ``_bg_pil_original``（终帧由
        ``_finish_bg_fade`` 更新）。走 apply_data 统一处理常驻纹理的
        贴图与 uv 子区域映射。
        """
        if revision != self._bg_revision or (w, h) != (self._layout_w, self._layout_h):
            return
        self._apply_bg_data(None, dpg_data, w, h)

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
            process_log.log(f"[Ending] 结局图标文件不存在: {icon_path}")
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
            process_log.log(f"[Ending] 结局图标加载失败: {e}")
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
        # 文本组件接管显示时 text_container 隐藏：图标挂到接管组件的容器，
        # 否则维持挂在内置文本容器
        if dpg.does_item_exist("text_gradient_box"):
            parent = "text_gradient_box"
        elif dpg.does_item_exist("text_nvl_overlay"):
            parent = "text_nvl_overlay"
        else:
            parent = "text_container"
        dpg.add_image(texture_tag="ending_icon_texture", tag="ending_icon_item",
                      parent=parent)
