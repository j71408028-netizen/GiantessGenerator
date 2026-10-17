"""副本入口阶段（imgui 立即模式混入）：与正式副本会话窗口共享同一个窗口。

- 动态背景：从副本资源目录随机抽取图片，轻微旋转/高斯模糊后循环交叉淡入淡出；
- 右下角：副本方案选择 + 开始副本 / 加载回放 / 返回；
- 左下角：若已加载角色，循环展示其已通关结局的 png 图标；
- 选择“开始副本”后：冻结背景（停止随机轮播），回到会话主界面，
  会话初始背景沿用冻结的入口背景，之后由背景触发器按需重设；
  选择“加载回放”后：同样冻结背景，并立即进入回放阶段；
  选择“返回”后：直接关闭窗口，不生成任何内容。

## 迁移状态（P3）

P3 已补齐入口阶段的绘制与纹理链路：方案面板由 ``ui._draw_entry_panel`` 每帧重建，
结局图标由 ``_preload_ending_textures`` 在 post_init（GL 就绪）后上传为
``TextureGpu``，并在 ``ui._draw_ending_panel`` 左下角轮播。动态背景轮播、Ken Burns、
阶段切换与 ``_freeze_background`` 沿用帧任务接线；``_on_dungeon_changed`` 只维护
AP 消耗缓存，不再操作 retained 控件。
"""

import math
import os
import random
import re

from PIL import Image

from dungeon import process_log
from dungeon.audio.chapters import normalize_chapters
from dungeon.terms import scenario_id_of

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")

# 结局记录里约定：icon_path 为空/缺失 = 该结局不重要，不展示
REPLAY_MARK = "__replay__"

# 入场动态背景的旋转/模糊参数范围（随机抽取）
_ROTATE_RANGE = (-8, 8)
_BLUR_RANGE = (1.2, 3.0)
# 轮播间隔（秒）
_BG_CYCLE_INTERVAL = (6.0, 10.0)
# 结局图标轮播间隔（秒）
_ENDING_CYCLE_SECONDS = 3.2

# 帧任务 key：同一时刻各只有一条，新的顶掉旧的
_BG_CYCLE_TASK = "entry:bg-cycle"
_ENDING_CYCLE_TASK = "entry:ending-cycle"
_KB_TASK = "entry:ken-burns"


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


# ---------------------------------------------------------------------------
# 入口阶段混入：附着在 DungeonSessionWindow 上，与正式会话共享同一窗口
# ---------------------------------------------------------------------------

class DungeonLaunchStages:
    """为副本会话窗口提供“入口阶段 → 会话阶段”的切换能力。

    base.DungeonWindowBase.__init__ 在 _build_ui() 后调用 self._enter_entry_phase()
    进入入口阶段；用户选择后由 _enter_dungeon_phase() 切换回会话阶段。

    入口阶段的动态部分（背景轮播、结局图标轮播、Ken Burns 运动）全部挂在窗口的
    帧时钟 ``self._frame`` 上（L3）：它们本质上都只是"每隔 N 秒/每帧动一下"，
    跟着帧循环生、跟着收尾死，不再需要各自的线程轮询 ``_closing`` 与 join。
    只有真正吃 CPU 的重采样/淡入淡出由 ``DungeonBackground`` 的单一像素工作者承担。
    """

    # ------------------ 入口阶段 ------------------
    def _enter_entry_phase(self):
        """进入副本选择入口：初始化素材、准备面板状态、启动动态背景与结尾轮播。"""
        self._is_entry_phase = True
        self._entry_started = False   # 已完成一次进入选择
        self._launch_choice = None
        # Ken Burns 持续运动状态（图片切换间隙背景缓慢缩放/平移，增强动态感）
        self._kb_scale = 1.0
        self._kb_dx = 0.0
        self._kb_dy = 0.0
        self._kb_phase = 0.0
        self._kb_speed = random.uniform(0.02, 0.04)   # 每秒缩放幅度（放慢呼吸感）
        self._kb_dir = random.choice([-1, 1])          # 缓慢放大或缩小

        self._collect_bg_images()
        self._collect_ending_icons()

        # 准备入口绘制状态；面板由 ui._draw_entry_panel 立即模式每帧绘制。
        self._build_entry_ui()

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

    # ------------------ 入口阶段 UI：状态与纹理预载（P3） ------------------
    def _build_entry_ui(self):
        """准备入口绘制状态；纹理上传由 post_init 后的钩子负责。

        ``_enter_entry_phase`` 发生在 ``runner.run()`` 之前，此时还没有 GL
        上下文，不能调用 ``create_texture_gpu_from_rgba_data``。因此这里只清零
        状态，真正的图标纹理在 :meth:`_preload_ending_textures` 里、由
        ``ui._setup_imgui``（post_init）在 GL 就绪后调用。
        """
        self._ending_textures = []
        # AP 消耗按方案缓存，避免立即模式下每帧读一次配置。
        self._entry_cost_cache = {}

    def _preload_ending_textures(self):
        """把已收集的结局图标上传为 ``TextureGpu``（仅 GL 就绪后调用）。

        与旧 DPG 版 ``_preload_ending_textures`` 相同的缩放/正方形留白口径；
        区别只是 DPG 静态纹理换成了 hello_imgui 的 RAII 纹理句柄。句柄由
        ``_ending_textures`` 列表持有，离开入口阶段时清空即可释放。
        """
        self._ending_textures = []
        if not getattr(self, "_ending_items", None):
            return

        from imgui_bundle import hello_imgui
        import numpy as np

        s = getattr(self, "_dpi_scale", 1.0) or 1.0
        box = max(1, round(96 * s))
        textures = []
        for item in self._ending_items:
            try:
                pil = item.get("pil")
                if pil is None:
                    textures.append(None)
                    continue
                w, h = pil.size
                ratio = min(box / w, box / h) if (w and h) else 0.0
                tw, th = w, h
                if ratio and ratio < 1.0:
                    tw, th = max(1, round(w * ratio)), max(1, round(h * ratio))
                    pil = pil.resize((tw, th), Image.Resampling.LANCZOS)
                canvas = Image.new("RGBA", (box, box), (0, 0, 0, 0))
                canvas.paste(pil, ((box - tw) // 2, (box - th) // 2))
                rgba = np.frombuffer(canvas.tobytes(), dtype=np.uint8).reshape(box, box, 4)
                textures.append(
                    hello_imgui.create_texture_gpu_from_rgba_data(rgba))
            except Exception as exc:
                process_log.log(f"结局图标纹理创建失败: {exc}")
                textures.append(None)
        self._ending_textures = textures

    def _destroy_entry_ui(self):
        """离开入口阶段：释放图标纹理句柄并清掉入口专用状态。

        立即模式没有常驻控件可删；``TextureGpu`` 是 RAII 句柄，丢掉最后一个
        Python 引用就会释放 GPU 资源，因此清空列表即可。
        """
        self._ending_items = []
        self._ending_textures = []
        self._ending_index = 0

    def _start_ending_cycle(self):
        """结局图标轮播：每 3.2s 翻一页（帧任务，取代原先的休眠线程）。

        只推进 ``_ending_index``；左下角绘制由 ``ui._draw_ending_panel`` 每帧
        按 GL 就绪后预载的纹理完成。
        """
        if not self._ending_items:
            return
        self._ending_index = 0

        def advance():
            # 帧任务随 entry 阶段一起取消：进入会话/关闭都不必再 join 线程
            if self._closing or not self._is_entry_phase:
                self._frame.cancel(_ENDING_CYCLE_TASK)
                return
            self._ending_index = (self._ending_index + 1) % len(self._ending_items)
            self._update_ending_display(self._ending_index)

        self._frame.every(_ENDING_CYCLE_SECONDS, advance, key=_ENDING_CYCLE_TASK)

    def _update_ending_display(self, idx):
        """记录当前轮播到第几页；绘制由 ``ui._draw_ending_panel`` 每帧负责。"""
        if self._closing or not self._is_entry_phase or not self._ending_items:
            return
        if 0 <= idx < len(self._ending_items):
            self._ending_index = idx


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
            process_log.log(f"副本入口背景加载失败: {e}")

    def _start_background_cycle(self):
        """背景轮播：随机 6~10s 换一张（帧任务自续链，取代原先的休眠线程）。"""
        if not self._bg_images:
            return
        self._bg_index = 0

        def switch():
            if self._closing or not self._is_entry_phase:
                self._frame.cancel(_BG_CYCLE_TASK)
                return
            n = len(self._bg_images)
            # 只有一张图时保持不动（多张时才重新抽一张，避免抽到当前这张）
            if n > 1:
                self._bg_index = (self._bg_index + random.randint(1, n - 1)) % n
            path = self._bg_images[self._bg_index]
            angle = random.uniform(*_ROTATE_RANGE)
            blur = random.uniform(*_BLUR_RANGE)
            self._switch_background(path, angle, blur)
            schedule_next()

        def schedule_next():
            # 每次重新抽间隔：同 key 顶掉上一条，天然只有一条等待
            self._frame.after(random.uniform(*_BG_CYCLE_INTERVAL), switch,
                              key=_BG_CYCLE_TASK)

        schedule_next()

    # ------------------ Ken Burns 持续运动（帧级） ------------------
    def _start_kb_motion(self):
        """Ken Burns 持续运动：背景在图片切换间隙缓慢缩放/平移。

        L3 之前走 DPG 的 ``set_frame_callback``（每 2 帧一跳），是独立于帧循环的
        第二条时间线；现在统一挂到帧时钟上，每帧一跳——行为一致，但整层只剩一个
        时间源，入口阶段结束时随 key 一并取消。
        """
        self._frame.every(0.0, self._kb_tick, key=_KB_TASK)

    def _kb_tick(self, *args):
        if self._closing or not self._is_entry_phase:
            self._frame.cancel(_KB_TASK)
            return
        try:
            self._kb_phase += self._kb_speed
            scale = 1.0 + self._kb_dir * (0.015 * (1.0 - abs(self._kb_phase % 2.0 - 1.0)))
            self._kb_scale = scale
            w, h = self._layout_w, self._layout_h
            if w <= 1 or h <= 1:
                return
            # 以图中心为基准缓慢缩放 + 缓慢左右漂移，形成轻微“呼吸感”
            cw, chh = w * scale, h * scale
            dx = w * 0.02 * math.sin(self._kb_phase * 0.35)
            dy = h * 0.02 * math.cos(self._kb_phase * 0.23)
            pmin = ((w - cw) / 2 + dx, (h - chh) / 2 + dy)
            pmax = (pmin[0] + cw, pmin[1] + chh)
            # 立即模式：不做"改控件属性"，只把本帧该用的矩形交给 ui._draw_background
            self._kb_rect = (pmin, pmax)
        except Exception as e:
            if not isinstance(e, SystemError):
                process_log.log(f"背景运动更新失败: {e}")

    def _switch_background(self, path, angle=0.0, blur=2.0):
        """主线程内切换到指定背景（淡入淡出，带旋转/模糊）。"""
        if self._closing or not self._is_entry_phase:
            return
        try:
            self._background.change(path, smooth_transition=True,
                                    rotate_angle=angle, blur_radius=blur)
        except Exception as e:
            process_log.log(f"副本入口背景切换失败: {e}")

    # ------------------ 窗口内切换回放（L4） ------------------
    def _pick_replay_file(self):
        """让宿主弹文件选择框并读取回放数据；取消/宿主不支持返回 None。"""
        picker = getattr(self.host, "open_replay_file", None)
        if not callable(picker):
            return []
        try:
            return picker()
        except Exception as e:
            process_log.log(f"[Replay] 读取回放文件失败: {e}")
            return []

    def _enter_replay_phase(self, replay_data):
        """入口页直接切到回放：**同一个窗口、同一次窗口生命周期**。

        L4 之前入口页选"加载回放"只能把窗口关掉，把 choice 交给调用方，再由调用
        方 ``new`` 第二个 ``DungeonSessionWindow(is_replay=True)``——一次完整的
        窗口拆建。现在只需把 is_replay 切上、初始化回放会话，然后走和"开始副本"
        完全相同的 :meth:`_enter_dungeon_phase`。
        """
        self.is_replay = True
        self.loaded_replay = list(replay_data or [])
        self.current_replay_index = 0
        self.replay_data = []
        self.pending_insertions.clear()
        self.triggers = []
        self.chapters = normalize_chapters([])
        self._init_replay(self.loaded_replay)
        # 标题随之改为回放模式。hello_imgui 的窗口标题在建窗时给定，运行中改
        # ``_real_title`` 只影响下次建窗（P1 不重建窗口，故此处仅记状态）。
        self._real_title = f"回放模式 - {self.name}"
        self._enter_dungeon_phase()

    # ------------------ 阶段切换 ------------------
    def _freeze_background(self):
        """停止随机轮播并冻结当前背景（入口阶段的帧任务一并取消）。

        轮播/图标翻页/Ken Burns 都是帧任务：这里只需按 key 取消，不再需要
        ``join(timeout=0.3)`` 去等休眠线程醒来。淡入淡出被 revision 检查自然丢弃。
        """
        for key in (_BG_CYCLE_TASK, _ENDING_CYCLE_TASK, _KB_TASK):
            self._frame.cancel(key)
        # 取消挂起的重采样/淡入淡出任务，避免其覆盖冻结画面
        self._background.cancel_pending_refresh()
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

        # 立即模式下没有"显示正式会话界面"这一步：``_is_entry_phase`` 已置假，
        # ``ui._draw_frame`` 下一帧自然改画 ``_draw_story``（组件接管文本显示时，
        # ``_draw_story`` 内部会跳过）。背景沿用冻结的入口图。

        if self.is_replay:
            self._init_components()
            self._build_components()
            self._relayout()
            self._replay_next_step()
        else:
            self._enter_start_chapter()
            # 会话启动是第一轮判定的开轮点（见 triggers._begin_trigger_round）
            self._begin_trigger_round()
            self.check_triggers()
            self._schedule_text_update()

    # ------------------ 入口交互回调 ------------------
    def _on_dungeon_changed(self, sender=None, app_data=None):
        """选择副本方案变了：清空 AP 消耗缓存，下一帧按新选择重算。

        立即模式没有 retained 控件要刷新；``ui._draw_entry_panel`` 每帧从
        ``_entry_selection`` 与 ``_entry_info_text()`` 现算显示。
        """
        self._entry_cost_cache = {}
        refresh = getattr(self, "_entry_info_text", None)
        if callable(refresh):
            try:
                refresh()
            except Exception as e:
                process_log.log(f"副本信息刷新失败: {e}")

    def _on_entry_start(self, sender=None, app_data=None, user_data=None):
        if not self.scenario_ids:
            return
        index = getattr(self, "_entry_selection", 0)
        if not 0 <= index < len(self.scenario_ids):
            # 无可用副本时“开始副本”按钮已禁用，此分支仅作保险
            return
        self._launch_choice = self.scenario_ids[index]
        self._enter_dungeon_phase()

    def _on_entry_replay(self, sender=None, app_data=None, user_data=None):
        """入口页"加载回放"：在同一窗口内切换到回放，不再让调用方重开窗口。

        用户取消选择时不关闭入口页——以前是把窗口关掉再让 UI 层判断，代价是整段
        窗口生命周期；现在只是"什么都没发生"。
        """
        self._launch_choice = REPLAY_MARK
        replay_data = self._pick_replay_file()
        if not replay_data:
            self._launch_choice = None
            return
        self._enter_replay_phase(replay_data)

    def _on_entry_cancel(self, sender=None, app_data=None, user_data=None):
        self._launch_choice = None
        self._exit_from_entry = True
        self._close_loop()


__all__ = ["DungeonLaunchStages", "REPLAY_MARK", "resolve_ending_icon"]