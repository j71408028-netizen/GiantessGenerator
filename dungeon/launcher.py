"""副本入口阶段（DearPyGui 混入）：与正式副本会话窗口共享同一个 DPG 生命周期。

- 动态背景：从副本资源目录随机抽取图片，轻微旋转/高斯模糊后循环交叉淡入淡出；
- 右下角：副本方案选择 + 开始副本 / 加载回放 / 返回；
- 左下角：若已加载角色，循环展示其已通关结局的 png 图标；
- 选择“开始副本”后：冻结背景（停止随机轮播），回到会话主界面，
  会话初始背景沿用冻结的入口背景，之后由背景触发器按需重设；
  选择“加载回放”后：同样冻结背景，并立即进入回放阶段；
  选择“返回”后：直接关闭窗口，不生成任何内容。
"""

import math
import os
import random
import re
import threading
import time

import dearpygui.dearpygui as dpg
from PIL import Image

from dungeon.dispatcher import _dispatch
from dungeon.terms import scenario_id_of
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")

# 结局记录里约定：icon_path 为空/缺失 = 该结局不重要，不展示
REPLAY_MARK = "__replay__"

# 入场动态背景的旋转/模糊参数范围（随机抽取）
_ROTATE_RANGE = (-8, 8)
_BLUR_RANGE = (1.2, 3.0)
# 轮播间隔（秒）
_BG_CYCLE_INTERVAL = (6.0, 10.0)


# ---------------------------------------------------------------------------
# 路径与资源收集工具
# ---------------------------------------------------------------------------

def _free_dungeons_root() -> str:
    from paths import data_dir
    return os.path.join(data_dir(), "packs", "scenarios")


def _split_rel(icon_path: str) -> list:
    """把相对路径里的 \\ 与 / 都当作分隔符，并忽略 . / .. 段。"""
    parts = []
    for piece in re.split(r"[\\/]+", str(icon_path)):
        if piece in ("", ".", ".."):
            continue
        parts.append(piece)
    return parts


def resolve_ending_icon(scenario_repo, scenario_id: str, icon_path: str):
    """解析结局图标实际文件路径。

    优先级：绝对路径 → 当前副本根目录 → 自由副本根目录（data/packs/scenarios）。
    返回 None 表示找不到文件。
    """
    icon_path = str(icon_path or "").strip()
    if not icon_path:
        return None
    candidates = []
    if os.path.isabs(icon_path):
        candidates.append(os.path.normpath(icon_path))
    rel_parts = _split_rel(icon_path)
    scenario_id = (scenario_id or "").strip("/\\")
    for base in (scenario_repo.root if scenario_repo is not None else None,
                 _free_dungeons_root()):
        if not base or not scenario_id:
            continue
        candidates.append(os.path.normpath(os.path.join(base, scenario_id, *rel_parts)))
    for path in dict.fromkeys(candidates):
        if os.path.isfile(path):
            return path
    return None


def collect_dungeon_images(scenario_repo) -> list:
    """递归收集各副本目录下的图片文件（含 images 子目录与触发器引用图）。"""
    found = []
    roots = []
    if scenario_repo is not None:
        roots.append(scenario_repo.root)
    free_root = _free_dungeons_root()
    if free_root not in roots:
        roots.append(free_root)
    for base in roots:
        if not base or not os.path.isdir(base):
            continue
        for dirpath, _dirnames, filenames in os.walk(base):
            for name in filenames:
                if name.lower().endswith(_IMAGE_EXTS):
                    found.append(os.path.join(dirpath, name))
    # 同一图片可能经相对/绝对两种根各采一次，按规范化路径去重，
    # 否则轮播会在同一张图的两个路径间来回切换、反复重载。
    return list(dict.fromkeys(os.path.normpath(p) for p in found))


_BYTE_TO_FLOAT = [value / 255.0 for value in range(256)]


# ---------------------------------------------------------------------------
# 入口阶段混入：附着在 DungeonSessionWindow 上，与正式会话共享同一窗口
# ---------------------------------------------------------------------------

class DungeonLaunchStages:
    """为副本会话窗口提供“入口阶段 → 会话阶段”的切换能力。

    base.DungeonWindowBase.__init__ 在 _build_ui() 后调用 self._enter_entry_phase()
    进入入口阶段；用户选择后由 _enter_dungeon_phase() 切换回会话阶段。
    所有 DPG 主线程操作均经 _dispatch 调度，后台线程只负责淡入淡出/轮播跳转。
    """

    # ------------------ 入口阶段 ------------------
    def _enter_entry_phase(self):
        """进入副本选择入口：初始化素材、构建面板、启动动态背景与结尾轮播。"""
        self._is_entry_phase = True
        self._entry_started = False   # 已完成一次进入选择
        self._launch_choice = None
        self._bg_thread = None
        self._ending_thread = None
        # Ken Burns 持续运动状态（图片切换间隙背景缓慢缩放/平移，增强动态感）
        self._kb_scale = 1.0
        self._kb_dx = 0.0
        self._kb_dy = 0.0
        self._kb_phase = 0.0
        self._kb_speed = random.uniform(0.02, 0.04)   # 每秒缩放幅度（放慢呼吸感）
        self._kb_dir = random.choice([-1, 1])          # 缓慢放大或缩小

        self._collect_bg_images()
        self._collect_ending_icons()

        self._build_entry_ui()
        dpg.hide_item("text_container")
        if dpg.does_item_exist("bg_overlay_child"):
            dpg.hide_item("bg_overlay_child")

        self._prime_background()
        self._start_background_cycle()
        self._start_ending_cycle()
        self._start_kb_motion()

    def _collect_bg_images(self):
        self._bg_images = collect_dungeon_images(self.scenario_repo)
        random.shuffle(self._bg_images)
        self._bg_index = -1

    # ------------------ 结局图标收集与展示 ------------------
    def _collect_ending_icons(self):
        self._ending_items = []
        self._ending_index = 0
        self._ending_hold = time.time()
        records = list(getattr(self.character, "achieved_endings", None) or [])
        for rec in records:
            if not isinstance(rec, dict):
                continue
            icon_rel = rec.get("icon_path") or ""
            if not icon_rel:
                continue
            full = resolve_ending_icon(self.scenario_repo, scenario_id_of(rec), icon_rel)
            if not full:
                continue
            try:
                pil = Image.open(full).convert("RGBA")
            except Exception:
                continue
            self._ending_items.append({
                "pil": pil,
                "name": rec.get("name", "未命名结局"),
                "achieved_at": str(rec.get("achieved_at", ""))[:16].replace("T", " "),
                "ending_text": rec.get("ending_text", ""),
                "scenario_id": scenario_id_of(rec),
            })

    # ------------------ 入口阶段 UI（挂载在同一 viewport） ------------------
    def _build_entry_ui(self):
        s = self._dpi_scale
        w = self._layout_w
        h = self._layout_h
        margin = round(20 * s)
        cw = round(380 * s)
        ch = round(270 * s)

        # 入口控件全部显式挂到 main_window（入口阶段在 _build_ui 的容器块结束后
        # 创建控件，DPG 无法从容器栈推断父级，必须显式指定 parent）
        dpg.add_child_window(
            tag="panel_controls", parent="main_window",
            pos=[w - cw - margin, h - ch - margin],
            width=cw, height=ch,
            no_scrollbar=True, no_scroll_with_mouse=True, border=False)
        dpg.add_text("进入副本", tag="ctl_title", parent="panel_controls",
                     color=(255, 255, 255, 255))
        dpg.add_text("选择副本方案", tag="ctl_hint", parent="panel_controls",
                     color=(255, 220, 180, 255))
        dpg.add_combo(
            items=self.scenario_ids,
            default_value=self.scenario_ids[0] if self.scenario_ids else "",
            width=cw - 24, tag="ctl_dungeon", parent="panel_controls",
            callback=self._on_dungeon_changed)
        info_text = "行动点消耗 0 AP"
        if self.scenario_ids and self.character is not None:
            info_text = (f"行动点消耗 0 AP　|　剩余 "
                         f"{getattr(self.character, 'action_points', 0)} AP")
        dpg.add_text(info_text, tag="ctl_info", parent="panel_controls",
                     color=(200, 210, 220, 255), wrap=cw - 24)
        dpg.add_spacer(height=6, parent="panel_controls")
        dpg.add_group(horizontal=True, tag="ctl_btn_start_row",
                      parent="panel_controls")
        dpg.add_button(label="开始副本", tag="btn_start", parent="ctl_btn_start_row",
                       width=cw - 24, height=round(40 * s),
                       callback=self._on_entry_start)
        dpg.add_group(horizontal=True, tag="ctl_btn_bottom_row",
                      parent="panel_controls")
        dpg.add_button(label="加载回放", tag="btn_replay",
                       parent="ctl_btn_bottom_row",
                       width=round((cw - 24) / 2 - 5), height=round(34 * s),
                       callback=self._on_entry_replay)
        dpg.add_button(label="返回", tag="btn_cancel",
                       parent="ctl_btn_bottom_row",
                       width=round((cw - 24) / 2 - 5), height=round(34 * s),
                       callback=self._on_entry_cancel)
        self._panel_controls_size = (cw, ch)

        if self._ending_items:
            self._build_endings_panel(w, h)

        self._apply_panel_themes()

        if not self.scenario_ids:
            dpg.configure_item("ctl_dungeon", items=["（无可用副本方案）"])
            dpg.configure_item("ctl_info", default_value="请先到“副本编辑”创建副本方案")
            dpg.configure_item("btn_start", label="无可用副本方案", enabled=False)

        # 面板构造完成后统一置为可见
        dpg.show_item("panel_controls")
        if dpg.does_item_exist("panel_endings"):
            dpg.show_item("panel_endings")

        # 初始化 AP 消耗显示（默认选中第一个副本方案）
        self._on_dungeon_changed()

    def _apply_panel_themes(self):
        """半透明深色面板：让文字/控件浮在动态背景之上且保持可读。"""
        s = self._dpi_scale
        with dpg.theme() as panel_theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (12, 10, 18, 200))
                dpg.add_theme_color(dpg.mvThemeCol_Border, (255, 255, 255, 60))
                dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (30, 26, 44, 230))
                dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (44, 38, 64, 240))
                dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive, (52, 44, 78, 245))
                dpg.add_theme_color(dpg.mvThemeCol_PopupBg, (18, 15, 26, 245))
                dpg.add_theme_color(dpg.mvThemeCol_Header, (58, 48, 86, 255))
                dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, (80, 66, 118, 255))
                dpg.add_theme_color(dpg.mvThemeCol_HeaderActive, (90, 74, 132, 255))
                dpg.add_theme_color(dpg.mvThemeCol_Text, (238, 238, 244, 255))
                dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 8)
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 12, 12)
                dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 6, 7)
        for tag in ("panel_controls", "panel_endings"):
            if dpg.does_item_exist(tag):
                dpg.bind_item_theme(tag, panel_theme)

        with dpg.theme() as start_theme:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (90, 60, 150, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (120, 82, 190, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (70, 45, 120, 255))
                dpg.add_theme_color(dpg.mvThemeCol_Text, (255, 255, 255, 255))
        dpg.bind_item_theme("btn_start", start_theme)

        with dpg.theme() as ghost_theme:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (0, 0, 0, 0))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (255, 255, 255, 28))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (255, 255, 255, 40))
                dpg.add_theme_color(dpg.mvThemeCol_Border, (255, 255, 255, 120))
                dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 1)
        for tag in ("btn_replay", "btn_cancel"):
            dpg.bind_item_theme(tag, ghost_theme)

    def _destroy_entry_ui(self):
        # 删除父容器会级联删除子项；仅需删除顶层容器与独立纹理
        for tag in ("panel_controls", "panel_endings"):
            if dpg.does_item_exist(tag):
                dpg.delete_item(tag)
        for tex in getattr(self, "_ending_textures", []) or []:
            if dpg.does_item_exist(tex):
                dpg.delete_item(tex)
        self._ending_items = []

    def _build_endings_panel(self, viewport_w, viewport_h):
        s = self._dpi_scale
        margin = round(20 * s)
        self._preload_ending_textures()
        pw = round(320 * s)
        ph = round(200 * s)
        dpg.add_child_window(
            tag="panel_endings", parent="main_window",
            pos=[margin, viewport_h - ph - margin],
            width=pw, height=ph,
            no_scrollbar=True, no_scroll_with_mouse=True, border=False)
        dpg.add_text(f"已通关结局（{len(self._ending_items)}）", tag="end_head",
                     parent="panel_endings", color=(255, 225, 150, 255))
        dpg.add_group(horizontal=True, tag="end_body", parent="panel_endings")
        dpg.add_image(texture_tag="end_tex_0", tag="end_icon_img",
                      parent="end_body",
                      width=round(96 * s), height=round(96 * s))
        dpg.add_group(tag="end_info_group", parent="end_body")
        dpg.add_text("", tag="end_name", parent="end_info_group",
                     color=(255, 255, 255, 255),
                     wrap=max(40, pw - round(130 * s)))
        dpg.add_text("", tag="end_time", parent="end_info_group",
                     color=(200, 205, 215, 255),
                     wrap=max(40, pw - round(130 * s)))
        dpg.add_spacer(height=2, parent="panel_endings")
        dpg.add_text("点击“开始副本”进入新的冒险", tag="end_foot",
                     parent="panel_endings", color=(160, 170, 185, 255))
        self._panel_endings_rect = (pw, ph)
        self._update_ending_display(0)

    def _preload_ending_textures(self):
        s = self._dpi_scale
        box = round(96 * s)
        self._ending_textures = []
        for idx, item in enumerate(self._ending_items):
            pil = item["pil"]
            w, h = pil.size
            ratio = min(box / w, box / h) if (w and h) else 0
            tw, th = pil.size
            if ratio and ratio < 1:
                tw, th = max(1, round(w * ratio)), max(1, round(h * ratio))
                pil = pil.resize((tw, th), Image.Resampling.LANCZOS)
            canvas = Image.new("RGBA", (box, box), (0, 0, 0, 0))
            canvas.paste(pil, ((box - tw) // 2, (box - th) // 2))
            data = [_BYTE_TO_FLOAT[v] for v in canvas.tobytes()]
            dpg.add_static_texture(width=box, height=box, default_value=data,
                                   tag=f"end_tex_{idx}",
                                   parent="dungeon_texture_registry")
            self._ending_textures.append(f"end_tex_{idx}")
        if not self._ending_textures:
            dpg.add_static_texture(width=1, height=1,
                                   default_value=[0.0, 0.0, 0.0, 1.0],
                                   tag="end_tex_0",
                                   parent="dungeon_texture_registry")
            self._ending_textures.append("end_tex_0")

    def _start_ending_cycle(self):
        if not self._ending_items:
            return
        self._ending_index = 0
        self._ending_hold = time.time()

        def cycle():
            n = len(self._ending_items)
            while not self._closing and self._is_entry_phase:
                time.sleep(3.2)
                if self._closing or not self._is_entry_phase:
                    break
                idx = (self._ending_index + 1) % n
                self._ending_index = idx
                _dispatch.enqueue(self._update_ending_display, idx)

        self._ending_thread = threading.Thread(target=cycle, daemon=True)
        self._ending_thread.start()

    def _update_ending_display(self, idx):
        if self._closing or not self._is_entry_phase or not self._ending_items:
            return
        if not (0 <= idx < len(self._ending_items)):
            return
        item = self._ending_items[idx]
        try:
            if dpg.does_item_exist("end_icon_img"):
                dpg.configure_item("end_icon_img", texture_tag=f"end_tex_{idx}")
            if dpg.does_item_exist("end_name"):
                dpg.configure_item("end_name", default_value=item["name"])
            if dpg.does_item_exist("end_time"):
                dungeon = item.get("scenario_id") or ""
                dpg.configure_item(
                    "end_time",
                    default_value=(f"{item['achieved_at']}　{dungeon}"
                                   if dungeon else item["achieved_at"]))
        except Exception as e:
            print(f"结局图标轮播更新失败: {e}")

    # ------------------ 动态背景（随机轮播，进入会话后冻结） ------------------
    def _prime_background(self):
        """打开一张随机副本图作为初始背景（应用随机旋转/模糊）。"""
        if not self._bg_images:
            return
        self._bg_index = 0
        path = self._bg_images[0]
        try:
            angle = random.uniform(*_ROTATE_RANGE)
            blur = random.uniform(*_BLUR_RANGE)
            self._background.change(path, smooth_transition=False,
                                    rotate_angle=angle, blur_radius=blur)
        except Exception as e:
            print(f"副本入口背景加载失败: {e}")

    def _start_background_cycle(self):
        if not self._bg_images:
            return

        def cycle():
            n = len(self._bg_images)
            while not self._closing and self._is_entry_phase:
                time.sleep(random.uniform(*_BG_CYCLE_INTERVAL))
                if self._closing or not self._is_entry_phase:
                    break
                # 只有一张图时 randrange 会因 low>=high 抛异常，直接保持不动
                if n > 1:
                    self._bg_index = (self._bg_index + random.randint(1, n - 1)) % n
                    path = self._bg_images[self._bg_index]
                else:
                    path = self._bg_images[0]
                angle = random.uniform(*_ROTATE_RANGE)
                blur = random.uniform(*_BLUR_RANGE)
                _dispatch.enqueue(self._switch_background, path, angle, blur)

        self._bg_thread = threading.Thread(target=cycle, daemon=True)
        self._bg_thread.start()

    # ------------------ Ken Burns 持续运动（帧级） ------------------
    def _start_kb_motion(self):
        """注册 DPG 帧回调：背景在图片切换间隙持续缓慢缩放/平移。

        不追求严格 60fps：帧回调每 tick 微调 draw_image 的 pmin/pmax，
        只触发重绘不重建纹理，开销很小。
        """
        dpg.set_frame_callback(dpg.get_frame_count() + 2, callback=self._kb_tick)

    def _kb_tick(self, *args):
        if self._closing or not self._is_entry_phase:
            return
        try:
            self._kb_phase += self._kb_speed
            scale = 1.0 + self._kb_dir * (0.015 * (1.0 - abs(self._kb_phase % 2.0 - 1.0)))
            self._kb_scale = scale
            w, h = self._layout_w, self._layout_h
            if w <= 1 or h <= 1:
                dpg.set_frame_callback(dpg.get_frame_count() + 2, callback=self._kb_tick)
                return
            # 以图中心为基准缓慢缩放 + 缓慢左右漂移，形成轻微“呼吸感”
            cw, chh = w * scale, h * scale
            dx = w * 0.02 * math.sin(self._kb_phase * 0.35)
            dy = h * 0.02 * math.cos(self._kb_phase * 0.23)
            pmin = [(w - cw) / 2 + dx, (h - chh) / 2 + dy]
            pmax = [pmin[0] + cw, pmin[1] + chh]
            if dpg.does_item_exist("bg_image_item"):
                dpg.configure_item("bg_image_item", pmin=pmin, pmax=pmax)
        except Exception as e:
            if not isinstance(e, SystemError):
                print(f"背景运动更新失败: {e}")
        finally:
            if not self._closing and self._is_entry_phase:
                dpg.set_frame_callback(dpg.get_frame_count() + 2, callback=self._kb_tick)

    def _switch_background(self, path, angle=0.0, blur=2.0):
        """主线程内切换到指定背景（淡入淡出，带旋转/模糊）。"""
        if self._closing or not self._is_entry_phase:
            return
        try:
            self._background.change(path, smooth_transition=True,
                                    rotate_angle=angle, blur_radius=blur)
        except Exception as e:
            print(f"副本入口背景切换失败: {e}")

    # ------------------ 阶段切换 ------------------
    def _freeze_background(self):
        """停止随机轮播并冻结当前背景（淡入淡出任务被 revision 检查自然丢弃）。"""
        if self._bg_thread is not None:
            try:
                self._bg_thread.join(timeout=0.3)
            except Exception:
                pass
            self._bg_thread = None
        if self._ending_thread is not None:
            try:
                self._ending_thread.join(timeout=0.3)
            except Exception:
                pass
            self._ending_thread = None
        # 取消挂起的 resize 定时器，避免其覆盖冻结画面
        if self._bg_resize_timer is not None:
            try:
                self._bg_resize_timer.cancel()
            except Exception:
                pass
        # 以当前显示的冻结帧为基准：后续 relayout 重新裁切时沿用冻结画面，
        # 而不是退回入口阶段最早启用的那张原图
        if self._bg_pil_original is not None:
            self._bg_pil_full = self._bg_pil_original

    def _enter_dungeon_phase(self):
        """由入口阶段切换到正式会话阶段（冻结背景后进入）。"""
        if self._entry_started:
            return
        self._entry_started = True
        self._freeze_background()
        self._is_entry_phase = False
        self._destroy_entry_ui()

        # 探索模式：入口选择副本方案后才加载配置并初始化会话
        if not self.is_replay and not self._session_initialized:
            choice = self._launch_choice
            if not choice or choice == REPLAY_MARK:
                self._exit_from_entry = True
                self._close_loop()
                return
            if not self._load_session_config(choice):
                self._exit_from_entry = True
                self._close_loop()
                return
            # 会话配置（含耦合等级）刚加载，需在新布局下重排
            self._init_components()
            self._build_components()
            self._relayout()

        # 显示正式会话界面（文本容器/衬底），背景沿用冻结的入口图
        if dpg.does_item_exist("text_container"):
            dpg.show_item("text_container")
        if dpg.does_item_exist("bg_overlay_child"):
            dpg.show_item("bg_overlay_child")

        if self.is_replay:
            self._init_components()
            self._build_components()
            self._relayout()
            self._replay_next_step()
        else:
            self._enter_start_chapter()
            self.check_triggers()
            self._schedule_text_update()

    # ------------------ 入口交互回调 ------------------
    def _on_dungeon_changed(self, sender=None, app_data=None):
        try:
            if not self.scenario_ids:
                return
            if not dpg.does_item_exist("ctl_dungeon"):
                return
            sel = dpg.get_value("ctl_dungeon")
            if not sel or sel == "（无可用副本方案）":
                return
            config = None
            if self.scenario_repo is not None:
                try:
                    config = self.scenario_repo.load_config(sel)
                except Exception:
                    config = None
            cost = 0
            if config:
                try:
                    cost = max(0, int(config.get("entry_action_cost", 0) or 0))
                except (TypeError, ValueError):
                    cost = 0
            text = f"行动点消耗 {cost} AP"
            if self.character is not None:
                text += f"　|　剩余 {getattr(self.character, 'action_points', 0)} AP"
            dpg.configure_item("ctl_info", default_value=text)
        except Exception as e:
            print(f"副本信息刷新失败: {e}")

    def _on_entry_start(self, sender=None, app_data=None, user_data=None):
        if not self.scenario_ids:
            return
        sel = dpg.get_value("ctl_dungeon")
        if not sel or sel == "（无可用副本方案）":
            # 无可用副本时“开始副本”按钮已禁用，此分支仅作保险
            return
        self._launch_choice = sel
        self._enter_dungeon_phase()

    def _on_entry_replay(self, sender=None, app_data=None, user_data=None):
        self._launch_choice = REPLAY_MARK
        self._enter_dungeon_phase()

    def _on_entry_cancel(self, sender=None, app_data=None, user_data=None):
        self._launch_choice = None
        self._exit_from_entry = True
        self._close_loop()


__all__ = ["DungeonLaunchStages", "REPLAY_MARK", "resolve_ending_icon"]