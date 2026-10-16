"""UI 绘制、文本显示、布局自适应与事件回调（imgui 立即模式）。

本文件现覆盖 P1「窗口骨架」/ P2「阅读主循环」/ P3「入口阶段」/ P4「组件包与
覆盖层」/ P5「视效与平台能力」/ P6「小游戏舞台接线」：
**建主窗口 + 背景铺满 + 视口尺寸/DPI + 字体两档 + 底部渐变式文本区 +
上屏游标 + 继续指示 ▼ + 三图标工具条 + 轻通知 + 输入接线 + 入口方案面板 +
结局图标轮播 + 窗口视效（闪光/振动/反色/色差）+ F2 截图 + F11 全屏 +
小游戏覆盖层舞台（帧末顶层窗口）**。
迁移的最后两阶段 P7（autopilot 改读值快照）与 P8（清场：删 dearpygui /
``dpg_state``）均已完成。

因此本文件里凡标 ``P<n> 占位`` 的地方都是**故意留空**的，不是遗漏。

## 从保留模式到立即模式：结构变化

DPG 是保留模式（建一次控件树，之后改属性），imgui 每帧重建。于是原来的
``_build_ui`` 拆成三块：

======================  ==================================================
DPG 时代                 立即模式
======================  ==================================================
``_build_ui`` 建上下文/视口/窗口   ``_build_ui`` 只准备参数 + 建 runner
（DPG 在 ``_build_ui`` 里建字体）   ``_load_fonts``（``load_additional_fonts``）装字体
                                  ``_setup_imgui``（post_init）定样式
                                  ``_draw_frame``（每帧）重建全部画面
``set_viewport_resize_callback``   每帧比对 ``io.display_size``
``dpg.get_item_state`` 读滚动量     ``imgui.get_scroll_max_y()``
``does_item_exist`` 探活控件        不再需要（没有常驻对象）
``dpg.add_text`` 逐条更新文本       直接遍历 ``story_history`` 绘制
======================  ==================================================

**字体为什么单独一个钩子**：hello_imgui 的调用顺序是 ``post_init`` →
``load_additional_fonts``。在 ``post_init`` 里 ``hello_imgui.load_font`` 得到的
``ImFont`` 没挂上图集，之后 ``imgui.push_font`` 会直接 access violation
（0xC0000005，整套窗口栈崩掉）。实测矩阵见 ``.workbuddy/p1_probe/_r2_report.txt``：
同一份代码，只把 ``load_font`` 从 ``post_init`` 挪到 ``load_additional_fonts``，
崩溃即消失。因此**字体加载与样式定标各占一个钩子**：
:meth:`DungeonWindowUI._load_fonts` 负责字体，:meth:`DungeonWindowUI._setup_imgui`
只负责样式与几何。

**上屏游标（``_reveal_*``）一行没改**：它本来就是原地改写
``story_history[i]["text"]``，立即模式每帧重画，天然就是"改写即上屏"。

**阅读区与浮层（P2）**：正文区按旧组件 ``text_gradient`` 口径重画成"底部渐变
衬底 + 最近 N 个显示段落 + 闪烁继续指示 ▼"，并补上三图标工具条（播放 / 截图 /
过程日志）。入口面板、工具条、选项弹窗这些浮层一律在根窗口 ``end()`` 之后作为
**顶层窗口**绘制，且必须用 :func:`_panel_flags`（不带
``no_bring_to_front_on_focus``）——否则会被铺满客户区的根窗口整个盖住。详见
:meth:`DungeonWindowUI._draw_frame`。

## 尺寸与 DPI 的口径（P1 初测、P5 修正：Windows @175%，imgui-bundle 1.92.900）

| 项 | 规则 |
|---|---|
| 建窗尺寸 | **宿主客户区（物理像素）÷ ``_dpi_scale``** —— 库内部会乘 ``dpi_window_size_factor()`` |
| 客户区 | == 请求的物理尺寸；不需要为标题栏/边框加余量（DPG 时代要 +16/+39） |
| ``io.display_size`` | == 客户区（物理像素）；1 imgui 单位 = 1 像素 |
| 字体 | ``push_font(font, base)`` 实际得到 ``base × dpi_factor``；**传基础字号** |
| 边距 / 内缩 | ``round(常量 × _dpi_scale)``，与 DPG 时代同一像素空间，原样沿用 |

**P5 修正的是第一行**。P1 写的是"直接给宿主客户区、不乘 ``_dpi_scale``"——那是
在**缩放 100%** 的机器上得到的结论（那时两个口径恰好相等）。在 175% 的机器上
实测（``.workbuddy/p5_probe/probe_dpi2.py``，显示器 2560×1600）：

| 建窗请求 | ``io.display_size`` | 判断 |
|---|---|---|
| ``(2240, 1260)`` = 宿主客户区物理值 | ``2476×1564`` | 超屏，被窗口系统夹到工作区 ❌ |
| ``(1280, 720)`` = 物理 ÷ 1.75 | ``2240×1260`` | 正好等于宿主客户区 ✅ |

换算所需的缩放系数取宿主报的那个（``viewport_metrics()`` 的第三项，即
``GetDpiForWindow / 96``）：它与 ``dpi_window_size_factor()`` 是同一个数，但
**建窗之前**库的那个函数恒返回 1.0（还没有窗口可问），所以只能用宿主这一份。
见 :meth:`DungeonWindowUI._window_create_size`。P1 的定标数据见
``.workbuddy/p1_probe/r0d_aware.py``。

## 线程约定

imgui 没有 DPG 那个"回调派发到工作线程"的坑：所有绘制与输入都在 runner 的帧回调
（单一线程）里发生，因此后台线程**只能**经 ``FrameScheduler.call`` 回到帧线程。
本文件的方法默认都在帧线程调用。
"""

import os
import time

from imgui_bundle import hello_imgui, imgui

from dungeon import process_log
from dungeon.models import DungeonTextType
from dungeon.window import gl_util
from dungeon.window import imgui_util as iu
from dungeon.window.background import PixelWorker
from dungeon.window.fonts import (BOLD_FONT_SIZE, TEXT_FONT_SIZE, UI_FONT_SIZE,
                                  resolve_font_files)
from dungeon.window.glfw_fullscreen import FULLSCREEN
from dungeon.window.runner import WindowRunner

# ---------- 颜色（沿用 DPG 时代的 0~255 四元组字面值，经 iu.u32 打包） ----------
_TEXT_COLOR = (255, 255, 255, 255)
_HIGHLIGHT_COLOR = (255, 200, 60, 255)
_DIM_COLOR = (200, 210, 220, 255)
_WARM_COLOR = (255, 220, 180, 255)

# ---------- 通知条（窗口级常驻，不受组件配置影响） ----------
_NOTIFY_WIDTH = 420
_NOTIFY_MARGIN = 8
_NOTIFY_DURATION = 3.0
_NOTIFY_COLOR = (245, 240, 225, 255)
_NOTIFY_BG = (30, 26, 18, 215)

# 上屏游标（仿流式输出）：唯一的速度真相源，真流式与仿流式共用一条限速。
# 30 字/秒——真流式下 chunk 到达即扩充目标文本，游标按此速率追赶，因此
# 无论 AI 吐字多快，玩家看到的上屏节奏恒定（分句器也因此始终领先于上屏）。
_REVEAL_CHARS_PER_SECOND = 30.0
_REVEAL_TICK_SECONDS = 0.05      # 帧任务周期（20 Hz，每 tick 1.5 字）
#: 点击推进的最小已上屏字数：不足则本次点击吞掉（防误触把整句瞬间吞掉）
_REVEAL_MIN_CHARS = 5
#: 上屏游标的帧任务 key（同一时刻只有一条，新的顶掉旧的）
_REVEAL_TASK = "text:reveal"

# 自动播放：等待输入时的推进节奏。_AUTOPLAY_INTERVAL 只是采样频率，真正的
# 节奏由 _advance_due（上屏收尾时刻 + 停留）决定；停留时长随句长微增。
_AUTOPLAY_INTERVAL = 0.2
_AUTOPLAY_DWELL = 0.5            # 上屏结束后额外停留（秒）
_AUTOPLAY_DWELL_PER_CHAR = 0.012  # 每个字符追加的停留（长句多停一点）
_AUTOPLAY_TASK = "autoplay:tick"

# ---------- 阅读区（"text" 底部渐变式）：与旧 DPG 组件 text_gradient 同口径 ----------
_GRADIENT_HEIGHT_PERCENT = 45         # 渐变衬底占视口高度的百分比（15~80）
_GRADIENT_COLOR = (10, 12, 22, 128)   # 渐变最底处的颜色（alpha 即最大不透明度）
_STORY_MAX_LINES = 1                  # 屏幕上保留的最近显示段落数（旧组件默认 1）
_STORY_MARGIN_X = 40                  # 文本框左右边距
_STORY_MARGIN_BOTTOM = 20             # 文本框底边距
_STORY_TOP_FLOOR = 60                 # 文本框顶边不越过窗口顶部这么多像素
_STORY_PAD = 15                       # 文本框内边距
_STORY_LINE_SPACING = 8               # 段落之间的额外间距
_STORY_LABEL_COLOR = (255, 170, 210, 255)   # 说话人标签（与旧组件一致）
_STORY_TEXT_COLOR = (240, 240, 245, 255)    # 正文
_STORY_OLD_ALPHA = 0.55               # 非最新行的透明度系数

# 继续指示 ▼：等待输入时闪烁（相位走帧任务，不开线程——窗口约束 C5）
_INDICATOR_TEXT = "▼"
_BLINK_SECONDS = 0.65
_BLINK_TASK = "text:blink"

# 文本功能工具条：圆角半透明容器 + 三个图标按钮（图标纹理程序化生成）
_TOOLBAR_MARGIN = 12
_TOOLBAR_ICON = 16        # 图标边长（px @ dpi=1）
_TOOLBAR_PAD = 4          # 按钮内边距
_TOOLBAR_GAP = 6          # 按钮间距
_TOOLBAR_FRAME_X = 9      # 容器左右内边距
_TOOLBAR_FRAME_Y = 6      # 容器上下内边距
_TOOLBAR_TINT = (232, 236, 244, 220)      # 图标常态着色
_TOOLBAR_TINT_ON = (255, 205, 100, 255)   # 自动播放中，播放图标转金色
_TOOLBAR_ACTIONS = (
    ("play", "自动播放（A）"),
    ("camera", "截图（F2）"),
    ("log", "过程日志（F12）"),
)

# ---------- 入口阶段面板（P3：与旧 DPG panel_controls / panel_endings 同口径） ----------
_ENTRY_PANEL_W = 380
_ENTRY_PANEL_H = 270
_ENTRY_ENDING_PANEL_W = 320
_ENTRY_ENDING_PANEL_H = 200
_ENTRY_ENDING_ICON = 96
_ENTRY_PANEL_MARGIN = 20
_ENTRY_PANEL_PAD = 12
_ENTRY_PANEL_BG = (12, 10, 18, 200)
_ENTRY_PANEL_BORDER = (255, 255, 255, 60)
_ENTRY_FRAME_BG = (30, 26, 44, 230)
_ENTRY_FRAME_BG_HOVERED = (44, 38, 64, 240)
_ENTRY_FRAME_BG_ACTIVE = (52, 44, 78, 245)
_ENTRY_POPUP_BG = (18, 15, 26, 245)
_ENTRY_HEADER = (58, 48, 86, 255)
_ENTRY_HEADER_HOVERED = (80, 66, 118, 255)
_ENTRY_HEADER_ACTIVE = (90, 74, 132, 255)
_ENTRY_TEXT = (238, 238, 244, 255)
_ENTRY_TITLE = (255, 255, 255, 255)
_ENTRY_HINT = (255, 220, 180, 255)
_ENTRY_INFO = (200, 210, 220, 255)
_ENTRY_START_BG = (90, 60, 150, 255)
_ENTRY_START_BG_HOVERED = (120, 82, 190, 255)
_ENTRY_START_BG_ACTIVE = (70, 45, 120, 255)
_ENTRY_GHOST_BG = (0, 0, 0, 0)
_ENTRY_GHOST_BG_HOVERED = (255, 255, 255, 28)
_ENTRY_GHOST_BG_ACTIVE = (255, 255, 255, 40)
_ENTRY_GHOST_BORDER = (255, 255, 255, 120)
_ENTRY_ENDING_HEAD = (255, 225, 150, 255)
_ENTRY_ENDING_TIME = (200, 205, 215, 255)
_ENTRY_ENDING_FOOT = (160, 170, 185, 255)

# ---------- imgui 窗口/子窗 ID（不进 ini，仅用于同层去重） ----------
_ROOT_WINDOW = "##dungeon_root"
_STORY_CHILD = "##dungeon_story"
_ENTRY_PANEL = "##dungeon_entry"
_ENDING_PANEL = "##dungeon_entry_endings"
_OPTION_DIALOG = "##dungeon_option"
_TOOLBAR = "##dungeon_toolbar"


def _dim_color(color, factor):
    """按系数调低 0~255 RGBA 的不透明度（保留颜色分量）。"""
    return (color[0], color[1], color[2], int(round(color[3] * factor)))


def _vec4(color):
    """0~255 RGBA 常量 → ``ImVec4``（``image_button`` 的 tint 等 float 口径）。"""
    r, g, b, a = color
    return imgui.ImVec4(r / 255.0, g / 255.0, b / 255.0, a / 255.0)


def _no_decoration_flags():
    """无标题栏、不可移动/缩放、不记忆设置的窗口标志（**根窗口**用）。"""
    flags = imgui.WindowFlags_
    return (flags.no_title_bar.value | flags.no_resize.value | flags.no_move.value
            | flags.no_collapse.value | flags.no_saved_settings.value
            | flags.no_bring_to_front_on_focus.value)


def _panel_flags():
    """浮层面板（入口面板 / 工具条 / 选项弹窗）的窗口标志。

    与 :func:`_no_decoration_flags` 的唯一差别是**不带** ``no_bring_to_front``：
    实测 1.92.900 下，带该标志的窗口会被铺满客户区的根窗口盖在下面（根窗口
    也是满屏无背景窗口，一旦排到前面就把面板整个挡住）。根窗口保留该标志，
    点背景时它不会抢到最前；面板去掉它才能稳定浮在根窗口之上。
    """
    return _no_decoration_flags() & ~imgui.WindowFlags_.no_bring_to_front_on_focus.value


class DungeonWindowUI:
    # ==================== 构建（尚未起 GL） ====================
    def _build_ui(self):
        """准备与后端无关的几何/状态，并建好 runner（窗口在 ``run()`` 里才创建）。

        与旧实现的关键差异：这里**不再创建视口与控件树**。hello_imgui 在
        ``run()`` 内部完成窗口创建、GL 后端与上下文初始化；本方法只把参数算齐。
        """
        self.is_fullscreen = False
        (_vp_w, _vp_h, self._dpi_scale,
         self._main_client_w, self._main_client_h) = self._get_initial_viewport_size()

        # 布局尺寸 = 宿主客户区（**物理像素**，与 io.display_size 同口径）：
        # 边距 / 内缩都按 _dpi_scale 放大过，落在这个像素空间里。
        # 建窗尺寸另算：96-PPI 逻辑点 = 物理客户区 ÷ _dpi_scale，见 _window_create_size()。
        self._layout_w = float(self._main_client_w)
        self._layout_h = float(self._main_client_h)
        self._text_wrap_width = 0.0

        # 轻通知（状态在窗口上，绘制在每帧；取代 DPG 的常驻通知子窗）
        self._notify_text = ""
        self._notify_until = 0.0
        self._notify_width = round(_NOTIFY_WIDTH * self._dpi_scale)

        # 背景纹理句柄（hello_imgui.TextureGpu）。持有 Python 引用即持有 GPU
        # 资源；换图时先建新句柄再丢旧句柄，避免中间帧采样到已释放纹理。
        self._bg_texture = None
        #: GL/ImGui 上下文是否已就绪（``post_init`` 之后为真）。在此之前创建
        #: GPU 资源会直接 0xC0000005——入口/章节背景可能在 ``_start_session``
        #: 里、窗口创建之前就被设置，因此背景先暂存，首帧再上传（见
        #: :meth:`_apply_bg_data` 与 :meth:`_draw_frame`）。
        self._gl_ready = False
        #: 待上传的背景 ``(pil_img, rgba, width, height)``（仅 GL 就绪前使用）
        self._pending_bg = None
        # 入口阶段 Ken Burns 运动给出的背景矩形（None = 铺满窗口）
        self._kb_rect = None

        # 入口面板状态（P3：方案下拉 + 结局图标轮播）
        self._entry_selection = 0
        self._entry_panel_rect = None
        self._ending_panel_rect = None

        # 字体句柄（load_additional_fonts 里填；post_init 时还是 None）
        self.dungeon_text_font = None
        self.dungeon_bold_font = None
        self._font_ui = None

        # P2 阅读主循环：继续指示相位 + 工具条状态
        self._indicator_on = True
        self._indicator_shown = False
        self._toolbar_textures = {}     # post_init（GL 就绪）里上传
        self._toolbar_hovered = False
        # 闪烁相位走帧任务：跟随帧循环生灭，收尾时随 _frame.stop() 一并清掉
        self._frame.every(_BLINK_SECONDS, self._toggle_indicator, key=_BLINK_TASK)

        # 截图落盘用的像素工作者（PNG 编码 + 磁盘 IO 不占帧线程）；
        # 会话收尾时 shutdown（见 `_finish_session` 之外的 `_shutdown_screenshot_worker`）
        self._shot_worker = PixelWorker(name="dungeon-shot-worker")
        #: 截图意图位（F2 置位，下一帧 after_swap 抓帧）
        self._screenshot_pending = False

        self._runner = WindowRunner(
            title=self._real_title,
            width=self._window_create_width(),
            height=self._window_create_height(),
            min_width=round(800 * self._dpi_scale),
            min_height=round(500 * self._dpi_scale),
            dpi_scale=self._dpi_scale,
            on_post_init=self._setup_imgui,
            on_load_fonts=self._load_fonts,
            on_frame_start=self._on_frame_start,
            on_draw=self._draw_frame,
            on_frame_end=self._on_frame_end,
            on_capture=self._capture_frame,
            on_before_exit=self._on_before_exit,
        )
        self._relayout()

    # ---------- 建窗口径（R7：物理像素 vs 96-PPI 逻辑点） ----------
    def _window_create_width(self):
        """建窗宽度（见 :meth:`_window_create_size`）。"""
        return self._window_create_size()[0]

    def _window_create_height(self):
        return self._window_create_size()[1]

    def _window_create_size(self):
        """把宿主客户区（物理像素）换算成 hello_imgui 的建窗尺寸（96-PPI 逻辑点）。

        R7 的两个口径：``window_geometry.size`` 是 **96-PPI 逻辑点**（库内部乘
        ``dpi_window_size_factor()``），而 ``HostPort.viewport_metrics()`` 给的是
        **物理像素**（Tk 用 ``GetClientRect`` 量）。直接拿物理像素当逻辑点传，
        窗口就会是想要的两倍——P5 实测（``.workbuddy/p5_probe/probe_dpi2.py``，
        175% 缩放，显示器 2560×1600）：

        ===================  ==================  ==========================
        建窗请求              ``io.display_size``  判断
        ===================  ==================  ==========================
        ``(2240, 1260)`` 物理  ``2476x1564``        超屏，被窗口系统夹到工作区
        ``(1280, 720)`` 逻辑   ``2240x1260``        正好等于宿主客户区 ✅
        ``(1600, 900)`` 半吊子 ``2476x1564``        同样被夹
        ===================  ==================  ==========================

        缩放系数取宿主报的那个（``viewport_metrics()`` 的第三项，即
        ``GetDpiForWindow / 96``）：它与 ``dpi_window_size_factor()`` 是同一个数，
        但**建窗之前**库的那个函数恒返回 1.0（还没有窗口可问），所以只能用宿主这
        一份。``scale <= 1`` 或宿主没报尺寸时退回原值，行为与 P1 一致。
        """
        cw, ch = int(self._main_client_w), int(self._main_client_h)
        scale = float(getattr(self, "_dpi_scale", 1.0) or 1.0)
        if cw <= 0 or ch <= 0 or scale <= 1.0:
            return cw, ch
        return max(1, round(cw / scale)), max(1, round(ch / scale))

    def _load_fonts(self):
        """``load_additional_fonts``：**唯一**能加载字体的时机。

        库在这里按序加载字体，并保证它**在 ``post_init`` 之后、第一帧之前**
        跑一次（跑完即把该回调置 None）。第一个加载的字体自动成为默认字体
        （``io.fonts.fonts[0]``）；后续 ``push_font`` 只有对这里加载的字体才安全。

        放在 ``post_init`` 里会拿到一个没挂图集的 ``ImFont``，``push_font`` 时
        直接 access violation——见模块文档与 ``_r2_report.txt``。

        覆盖这个回调的代价：库缺省的「DroidSans + FontAwesome 图标」不再加载。
        因此没解析到字体文件时要显式调一次库的缺省加载器，否则图集为空，
        退出时会在 ``IM_ASSERT(Pixels != 0)`` 上炸。
        """
        # 家族名 → (常规文件, 粗体文件)：解析规则全在 dungeon/window/fonts.py。
        # 家族名来自设置的「副本字体」（base.__init__ 存进 self.dungeon_font，
        # 无设置时是宿主的平台兜底家族名）。
        font_path, bold_path = resolve_font_files(
            getattr(self, "dungeon_font", "") or "")
        if not font_path:
            # 没有可用字体文件：退回库的缺省字体（否则图集为空 → 退出时断言失败）
            process_log.log("[Dungeon] 未解析到副本字体文件，回退 imgui 缺省字体"
                            "（中文可能显示为方块）")
            try:
                hello_imgui.imgui_default_settings.load_default_font_with_font_awesome_icons()
            except Exception as exc:
                process_log.log(f"[Dungeon] 缺省字体加载失败: {exc}")
            return

        # 记进过程日志：Linux 上「中文变豆腐块」多半是这里解析到了没有中文字形的
        # 兜底字体（见 dungeon/window/fonts.py 的 Linux 说明），留一行便于定位。
        process_log.log(f"[Dungeon] 副本字体 {font_path}"
                        + (f"（粗体 {bold_path}）" if bold_path else ""))
        # 字号传**基础值**：hello_imgui 会按 dpi_factor 放大（实测
        # load_font(p,24)+push_font(f,24) → 48px @200%），再乘一次就是双倍。
        # 加载顺序 = 图集顺序：第一个加载的字体成为默认字体（io.fonts.fonts[0]）。
        # 面板 / 工具条 / 通知用 UI 档（21），正文用 24（有粗体文件时用粗体 24）
        # ——与 DPG 时代 dpg.bind_font(UI) + 正文单独绑定的口径一致。
        self._font_ui = hello_imgui.load_font(font_path, UI_FONT_SIZE)
        self.dungeon_text_font = hello_imgui.load_font(font_path, TEXT_FONT_SIZE)
        self.dungeon_bold_font = (hello_imgui.load_font(bold_path, BOLD_FONT_SIZE)
                                  if bold_path else None)

    def _setup_imgui(self):
        """``post_init``：GL 后端与 ImGui 上下文就绪、首帧之前。**只定样式与几何**。

        字体不在这里——那时候还没有图集，加载出来的字体 ``push_font`` 会崩
        （见 :meth:`_load_fonts` 与模块文档）。本方法因此也不持有字体句柄的
        任何依赖：``_apply_imgui_style`` / ``_relayout`` / ``_update_text_display``
        都只用 ``_dpi_scale`` 与 ``_layout_*``，此时已由 ``_build_ui`` 备好。
        """
        self._gl_ready = True
        self._apply_imgui_style()
        # 工具条图标是 GPU 资源，必须等到 GL 就绪（post_init）之后才能上传
        self._make_toolbar_textures()
        # 结局图标同样是 GPU 资源；入口阶段在 run() 前收集 PIL，这里才上传
        preload_endings = getattr(self, "_preload_ending_textures", None)
        if callable(preload_endings):
            try:
                preload_endings()
            except Exception as exc:
                process_log.log(f"[Dungeon] 结局图标预载失败: {exc}")
        # 窗口视效层：P5 才实现（imgui 侧要为闪光/振动/反色/色差重画一遍），
        # 这里的 build 是保持调用链完整的 P5 占位（fx.py 里为空实现）。
        self._window_fx.build(self._layout_w, self._layout_h)
        self._relayout()
        self._update_text_display()

    def _apply_imgui_style(self):
        """把 imgui 的圆角/边框口径调到与旧界面接近（颜色用缺省暗色主题）。

        P1 只做几何口径对齐；面板配色与主题在 P3（入口页）与 P5（视效）细化。
        """
        try:
            style = imgui.get_style()
            s = self._dpi_scale
            style.window_rounding = float(round(6 * s))
            style.child_rounding = float(round(8 * s))
            style.frame_rounding = float(round(6 * s))
            style.popup_rounding = float(round(8 * s))
            style.window_border_size = 1.0
            style.frame_border_size = 1.0
            style.item_spacing = imgui.ImVec2(float(round(6 * s)), float(round(7 * s)))
            style.frame_padding = imgui.ImVec2(float(round(6 * s)), float(round(4 * s)))
        except Exception as exc:
            process_log.log(f"[Dungeon] 应用 imgui 样式失败: {exc}")

    # ==================== 帧回调 ====================
    def _on_frame_start(self):
        """帧最前：泵宿主事件 + tick 帧时钟（旧 ``_run_frame_loop`` 的前两行）。"""
        self._pump_host_events()
        self._frame.tick()

    def _draw_frame(self):
        """构建整帧画面（每帧调用一次）。

        图层（imgui 1.92.900 实测）：

        - ``_ROOT_WINDOW``：铺满客户区、无装饰无背景的根窗口，只放背景 / 渐变 /
          正文子窗 / 继续指示 / 轻通知（draw list 与 ``begin_child`` 在根窗口内
          都正常绘制）。
        - 浮层（入口面板 / 工具条 / 选项弹窗）：在根窗口 ``end()`` 之后作为
          **顶层窗口**绘制，并且必须用 :func:`_panel_flags`。带
          ``no_bring_to_front_on_focus`` 的面板会被铺满客户区的根窗口整个盖住，
          屏幕上完全不可见——P1 的入口面板就这样一直没显示出来，P2 截图取证时
          才发现；根窗口保留该标志，点背景时不会抢到最前。
        - 小游戏舞台（P6，``minigame/stage.py``）：同样在根窗口 ``end()`` 之后
          绘制，但是**最后**一个顶层窗口，铺满客户区、压在工具条与选项弹窗之上。
        """
        io = imgui.get_io()
        w, h = float(io.display_size.x), float(io.display_size.y)
        if w <= 1.0 or h <= 1.0:
            return
        if abs(w - self._layout_w) > 0.5 or abs(h - self._layout_h) > 0.5:
            # 立即模式没有 resize 回调：每帧比对客户区，变了就重算派生几何。
            # 这与旧 ``set_viewport_resize_callback`` 等价，但不依赖回调被正确派发。
            self._layout_w, self._layout_h = w, h
            self._relayout()

        imgui.push_style_var(imgui.StyleVar_.window_padding, iu.v2(0.0, 0.0))
        imgui.push_style_var(imgui.StyleVar_.window_border_size, 0.0)
        imgui.push_style_var(imgui.StyleVar_.window_rounding, 0.0)
        # 振动（fx）：整窗内容一起偏移 = 把**根窗口原点**挪到 -offset、尺寸各加
        # 2×振幅；窗口内部的绘制坐标完全不动（`_draw_background` 收到反向补偿画
        # 满整个可见区，因此屏幕边缘不会露出底色）。实测见
        # `.workbuddy/p5_probe/probe_shake.py`：负坐标不会被 imgui 夹回去。
        shake_x, shake_y = self._window_fx.shake_offset()
        bleed_x, bleed_y = abs(shake_x), abs(shake_y)
        imgui.set_next_window_pos(iu.v2(-shake_x, -shake_y))
        imgui.set_next_window_size(iu.v2(w + 2 * bleed_x, h + 2 * bleed_y))
        imgui.begin(_ROOT_WINDOW, None,
                    _no_decoration_flags()
                    | imgui.WindowFlags_.no_scrollbar.value
                    | imgui.WindowFlags_.no_scroll_with_mouse.value
                    | imgui.WindowFlags_.no_background.value)

        # GL 就绪前暂存的背景在这里首次上传（会话初始化可能早于窗口创建）
        if self._pending_bg is not None:
            pending, self._pending_bg = self._pending_bg, None
            self._apply_bg_data(*pending)
        self._draw_background(w, h, shake_x, shake_y)
        draw_components = getattr(self, "_draw_components", None)
        if not getattr(self, "_is_entry_phase", False):
            # 文本主组件接管显示时，内置 story / 工具条整体跳过；
            # 其余组件（attr_bar / proc_log）仍每帧绘制在正文之上。
            if self._text_owned_by_component():
                if callable(draw_components):
                    draw_components()
            else:
                self._draw_story(w, h)
                if callable(draw_components):
                    draw_components()
            draw_overlay = getattr(self, "_draw_log_overlay", None)
            if callable(draw_overlay):
                draw_overlay(w, h)
        self._draw_notification(w, h)
        # 视效覆盖层（反色 / 色差分离）：压在所有界面元素之上、闪光之下
        self._draw_pixel_overlay(w, h, shake_x, shake_y)
        # 闪光永远是最上面的一层（与 DPG 版"视效层提到最上"同序）
        self._draw_flash(w, h, bleed_x, bleed_y)
        imgui.end()
        imgui.pop_style_var(3)

        # 顶层浮层：root end() 之后再 begin()（见方法文档）
        if getattr(self, "_is_entry_phase", False):
            self._draw_entry_panel(w, h)
            self._draw_ending_panel(w, h)
        else:
            # 工具条随文本组件走：组件接管显示时由组件自己画（P4）
            if not self._text_owned_by_component():
                self._draw_toolbar(w, h)
            self._draw_option_dialog(w, h)

        # 小游戏舞台（P6）：帧末最后绘制的顶层窗口，铺满客户区，因而压在工具条与
        # 选项弹窗之上（与浮层同一条 z 序规矩，见 50-analysis §4）。无舞台时是空判。
        draw_stage = getattr(self, "_draw_mini_game_stage", None)
        if callable(draw_stage):
            draw_stage(w, h)

        # 输入放在所有控件绘制之后：``is_any_item_hovered`` 才是本帧的悬停真值
        self._handle_input()

    def _on_frame_end(self):
        """换缓冲之后：计帧 + 自检限帧（等价于用户点了副本视口的关闭键）。"""
        self.frames_rendered += 1
        limit = getattr(self, "_test_frame_limit", 0)
        if limit and self.frames_rendered >= limit and not self._closing:
            # 自检限帧到点：走与用户点 X 完全相同的收尾链（_finalize_close_once
            # 保证 `_on_close` 只跑一次），再请求关闭。
            self._finalize_close_once()
            self._request_close()

    def _on_before_exit(self):
        """即将退出：等价于旧帧循环跳出后的 ``_on_close()``。

        无论关闭来自本程序（``app_shall_exit``）还是用户点 X（GLFW 关窗标志），
        hello_imgui 都会走到这里，因此不需要再区分两条路径。收尾只跑一次，
        交给 :meth:`_finalize_close_once` 把关——它**不能**用 ``_closing`` 当判据，
        否则入口页"返回"（先置 ``_closing`` 再请求关闭）会漏掉 ``_exit_from_entry``。
        """
        self._finalize_close_once()

    # ==================== 绘制：背景 ====================
    def _draw_background(self, w: float, h: float, shake_x: float = 0.0,
                         shake_y: float = 0.0):
        """背景铺满窗口：一张 ``TextureGpu`` 拉满整个客户区。

        ``shake_*`` 是振动偏移（``fx.WindowFx.shake_offset()``）。根窗口原点此时
        已被挪到 ``-offset``，所以这里把坐标平移 ``+offset``——背景的**屏幕位置**
        因此纹丝不动（与 DPG 版"背景跟着抖"不同，但那本来就不是设计意图，
        只是动子项的副作用）。
        """
        draw_list = imgui.get_window_draw_list()
        texture = getattr(self, "_bg_texture", None)
        if texture is None:
            # 首帧或没有背景素材：铺纯色。这里要盖住**整个加宽后的窗口**，
            # 否则振动时屏幕边缘会闪出底色（根窗口是 no_background 的）。
            bleed_x, bleed_y = abs(shake_x), abs(shake_y)
            draw_list.add_rect_filled(
                iu.v2(0.0, 0.0), iu.v2(w + 2 * bleed_x, h + 2 * bleed_y),
                iu.u32(12, 12, 18, 255))
            return
        pmin, pmax = iu.v2(shake_x, shake_y), iu.v2(shake_x + w, shake_y + h)
        rect = getattr(self, "_kb_rect", None)
        if getattr(self, "_is_entry_phase", False) and rect:
            # 入口阶段的 Ken Burns 运动接管几何（launcher._kb_tick 提供矩形）
            pmin = iu.v2(rect[0][0] + shake_x, rect[0][1] + shake_y)
            pmax = iu.v2(rect[1][0] + shake_x, rect[1][1] + shake_y)
        ref = imgui.ImTextureRef(int(texture.texture_id()))
        draw_list.add_image(ref, pmin, pmax, iu.v2(0.0, 0.0), iu.v2(1.0, 1.0))

    # ==================== 绘制：窗口视效（P5） ====================
    def _draw_pixel_overlay(self, w: float, h: float, shake_x: float,
                            shake_y: float):
        """反色 / 色差分离：把抓到的整帧染色后当全窗覆盖层压回来。

        覆盖层是"这一瞬间的画面"，消退靠不透明度（由 ``fx`` 的帧任务按
        时长推进），因此下层画面在消退期间照常活动——与 DPG 版同一观感。

        几何必须**补偿振动偏移**：抓到的整帧画在客户区坐标上，而根窗口原点此刻
        在 ``-offset``；直接画在窗口局部 ``(0,0)-(w,h)`` 会让覆盖层整体偏
        ``offset``，看上去就是"画面变成两份"（P5 探针实测过这一版的观感）。
        平移 ``+offset`` 之后覆盖层与它捕捉的内容逐像素对齐，同时盖住偏移让出来
        的边缘。
        """
        overlay = self._window_fx.pixel_overlay()
        if overlay is None:
            return
        texture, alpha = overlay
        draw_list = imgui.get_window_draw_list()
        ref = imgui.ImTextureRef(int(texture.texture_id()))
        draw_list.add_image(ref, iu.v2(shake_x, shake_y),
                            iu.v2(shake_x + w, shake_y + h),
                            iu.v2(0.0, 0.0), iu.v2(1.0, 1.0),
                            iu.u32(255, 255, 255, alpha))

    def _draw_flash(self, w: float, h: float, bleed_x: float, bleed_y: float):
        """闪光：全窗白色矩形，不透明度由 ``fx`` 的帧任务推进。"""
        color = self._window_fx.flash_color()
        if color is None:
            return
        imgui.get_window_draw_list().add_rect_filled(
            iu.v2(0.0, 0.0), iu.v2(w + 2 * bleed_x, h + 2 * bleed_y), color)

    # ==================== 绘制：会话正文（text 底部渐变式） ====================
    #
    # P2 阅读主循环：底部渐变衬底 + 最近若干显示段落 + 闪烁继续指示 ▼ + 三图标
    # 工具条。与旧 DPG 组件包 ``text_gradient`` 同口径（渐变占视口底部 45%、
    # 只呈现最近 N 个显示段落）；区别只在实现——立即模式没有控件树，正文每帧
    # 从 ``story_history`` 现画，上屏游标"原地改写 text"天然就是"改写即上屏"。
    def _story_geometry(self, w: float, h: float):
        """阅读区几何：``(左边距, 渐变高, 文本宽, 内边距)``。

        与旧 DPG 组件包 ``text_gradient`` 同口径：渐变衬底占视口底部 45%
        （上下限 15%~80%），文本框左右各留 40×dpi、底部留 20×dpi。
        """
        s = self._dpi_scale
        margin_x = round(_STORY_MARGIN_X * s)
        percent = min(80, max(15, _GRADIENT_HEIGHT_PERCENT))
        grad_h = max(round(h * 0.15), round(h * percent / 100.0))
        return (float(margin_x), float(grad_h),
                max(1.0, w - 2 * margin_x), float(round(_STORY_PAD * s)))

    def _story_visible_items(self):
        """屏幕上保留的最近若干个显示段落（空段落与纯标签段落跳过）。"""
        items = [it for it in (getattr(self, "story_history", None) or [])
                 if (it.get("text") or str(it.get("speaker") or "").strip())]
        return items[-max(1, _STORY_MAX_LINES):]

    def _story_font(self):
        """正文档字体：解析到粗体文件时用粗体（旧组件把行对绑到粗体上）。"""
        return (getattr(self, "dungeon_bold_font", None)
                or getattr(self, "dungeon_text_font", None))

    def _push_story_font(self):
        """正文档字体（字号传基础值，库按 DPI 放大——见模块文档）。"""
        font = self._story_font()
        if font is not None:
            imgui.push_font(font, TEXT_FONT_SIZE)

    def _pop_story_font(self):
        if self._story_font() is not None:
            imgui.pop_font()

    def _story_box_geometry(self, w: float, h: float):
        """阅读区完整几何：``(x, 渐变高, 文本宽, 文本高, 内边距, 文本 y)``。"""
        x, grad_h, text_w, pad = self._story_geometry(w, h)
        box_h = self._story_box_height(text_w, pad, h)
        s = self._dpi_scale
        box_y = max(0.0, h - float(round(_STORY_MARGIN_BOTTOM * s)) - box_h)
        return x, grad_h, text_w, box_h, pad, box_y

    def _draw_story(self, w: float, h: float):
        """阅读主循环：渐变衬底 + 最近段落 + 继续指示 ▼（工具条是顶层浮层）。

        只呈现最近 ``_STORY_MAX_LINES`` 个显示段落（旧 ``text_gradient`` 同
        口径）；完整历史归回放/报告，回看面板在 P4。上屏游标原地改写
        ``story_history[i]["text"]``，立即模式每帧重画即"改写即上屏"。
        """
        x, grad_h, text_w, box_h, pad, box_y = self._story_box_geometry(w, h)
        self._text_wrap_width = max(1.0, text_w - 2 * pad)
        self._draw_gradient_band(w, h, grad_h)
        self._draw_story_lines(x, box_y, text_w, box_h, pad)
        self._draw_continue_indicator(x, box_y, text_w, box_h)

    def _draw_gradient_band(self, w: float, h: float, grad_h: float):
        """底部向上淡出的深色衬底：imgui 的四角渐变矩形一步到位（不用纹理）。"""
        r, g, b, a = _GRADIENT_COLOR
        draw_list = imgui.get_window_draw_list()
        top = iu.u32(r, g, b, 0)
        bottom = iu.u32(r, g, b, a)
        draw_list.add_rect_filled_multi_color(iu.v2(0.0, h - grad_h), iu.v2(w, h),
                                              top, top, bottom, bottom)

    def _story_box_height(self, text_w: float, pad: float, h: float) -> float:
        """文本框高度：按可见段落估算内容高度，再夹进几何预算。

        估算是保守的（行高按字号 + 10，换行用 ``calc_text_size`` 实测宽度），
        只多不少、不会裁字；顶边不越过窗口顶部 ``_STORY_TOP_FLOOR``。
        """
        s = self._dpi_scale
        floor_h = float(round(60 * s))
        wrap = max(1.0, text_w - 2 * pad)
        line_h = float(round((TEXT_FONT_SIZE + 10) * s))
        content = 2.0 * pad
        for item in self._story_visible_items():
            if str(item.get("speaker") or "").strip():
                content += line_h
            size = imgui.calc_text_size(item.get("text") or "", None, False, wrap)
            content += max(line_h, float(size.y))
            content += float(round(_STORY_LINE_SPACING * s))
        cap = max(floor_h, h - float(round(_STORY_MARGIN_BOTTOM * s))
                  - float(round(_STORY_TOP_FLOOR * s)))
        return max(floor_h, min(cap, content))

    def _draw_story_lines(self, x, box_y, text_w, box_h, pad):
        """文本框：透明子窗里的最近段落；最新一行全亮、旧行降透明度。"""
        s = self._dpi_scale
        pad_v = float(round(pad))
        flags = (imgui.WindowFlags_.no_scrollbar.value
                 | imgui.WindowFlags_.no_scroll_with_mouse.value)
        imgui.set_cursor_pos(iu.v2(x, max(0.0, box_y)))
        # 内边距交给子窗自己的 WindowPadding：不能用 set_cursor_pos 往里推——
        # 空历史（首帧）时子窗里没有条目接着提交 Item，imgui 会断言
        # "SetCursorPos extends window boundaries"。
        imgui.push_style_var(imgui.StyleVar_.window_padding, iu.v2(pad_v, pad_v))
        imgui.push_style_color(imgui.Col_.child_bg, iu.u32(0, 0, 0, 0))
        imgui.begin_child(_STORY_CHILD, iu.v2(text_w, box_h),
                          imgui.ChildFlags_.none.value, flags)
        self._push_story_font()
        items = self._story_visible_items()
        for index, item in enumerate(items):
            newest = index == len(items) - 1
            alpha = 1.0 if newest else _STORY_OLD_ALPHA
            label = str(item.get("speaker") or "").strip()
            if label:
                imgui.push_style_color(
                    imgui.Col_.text, iu.u32(*_dim_color(_STORY_LABEL_COLOR, alpha)))
                imgui.text(label)
                imgui.pop_style_color()
            body = _HIGHLIGHT_COLOR if item.get("highlight") else _STORY_TEXT_COLOR
            imgui.push_style_color(imgui.Col_.text, iu.u32(*_dim_color(body, alpha)))
            imgui.push_text_wrap_pos(imgui.get_cursor_pos_x()
                                     + max(1.0, text_w - 2 * pad_v))
            imgui.text_wrapped(item.get("text") or "")
            imgui.pop_text_wrap_pos()
            imgui.pop_style_color()
            imgui.dummy(iu.v2(0.0, float(round(_STORY_LINE_SPACING * s))))
        self._pop_story_font()
        imgui.end_child()
        imgui.pop_style_color()
        imgui.pop_style_var()

    # ---------- 继续指示（等待输入时右下角闪烁 ▼） ----------
    def _toggle_indicator(self):
        """继续指示的闪烁相位（帧任务；不开线程——窗口约束 C5）。"""
        self._indicator_on = not getattr(self, "_indicator_on", True)

    def _waiting_for_continue(self) -> bool:
        """是否该显示继续指示：等待输入，且入口页/覆盖层没有挡着。"""
        if getattr(self, "_is_entry_phase", False) or self.overlay_open():
            return False
        try:
            return bool(self.session_waiting_for_input())
        except Exception:
            return False

    def _draw_continue_indicator(self, x, box_y, text_w, box_h):
        """继续指示与自动播放同源：``session_waiting_for_input`` 为真才闪。"""
        self._indicator_shown = False
        if not getattr(self, "_indicator_on", True) or not self._waiting_for_continue():
            return
        self._indicator_shown = True
        s = self._dpi_scale
        px = (x + text_w - float(round(46 * s)) - self._toolbar_width()
              - float(round(16 * s)))
        py = box_y + box_h - float(round(44 * s))
        imgui.get_window_draw_list().add_text(
            iu.v2(max(0.0, px), max(0.0, py)),
            iu.u32(*_STORY_LABEL_COLOR), _INDICATOR_TEXT)

    # ---------- 文本功能工具条（播放 / 截图 / 过程日志） ----------
    def _toolbar_width(self) -> float:
        s = self._dpi_scale
        icon = max(6, round(_TOOLBAR_ICON * s))
        pad = round(_TOOLBAR_PAD * s)
        gap = round(_TOOLBAR_GAP * s)
        frame_x = round(_TOOLBAR_FRAME_X * s)
        n = len(_TOOLBAR_ACTIONS)
        return float(n * (icon + 2 * pad) + (n - 1) * gap + 2 * frame_x)

    def _toolbar_height(self) -> float:
        s = self._dpi_scale
        icon = max(6, round(_TOOLBAR_ICON * s))
        pad = round(_TOOLBAR_PAD * s)
        frame_y = round(_TOOLBAR_FRAME_Y * s)
        return float(icon + 2 * pad + 2 * frame_y)

    def _make_toolbar_textures(self):
        """``post_init``（GL 就绪）后把三个程序化图标上传成 ``TextureGpu``。"""
        s = self._dpi_scale
        icon = max(6, round(_TOOLBAR_ICON * s))
        textures = {}
        try:
            for key, _tip in _TOOLBAR_ACTIONS:
                rgba = iu.icon_rgba(iu.ICON_SHAPES[key], icon)
                textures[key] = hello_imgui.create_texture_gpu_from_rgba_data(rgba)
        except Exception as exc:
            process_log.log(f"[Dungeon] 工具条图标创建失败: {exc}")
            textures = {}
        self._toolbar_textures = textures

    def _draw_toolbar(self, w, h):
        """文本框右下角的三个图标按钮：播放 / 截图 / 过程日志。

        只转调窗口同名服务（与 A / F2 / F12 等效），按钮本身不推进剧情；悬停
        状态写进 ``_toolbar_hovered``，供 ``_component_toolbar_hovered`` 判定
        "点在按钮上"而不是"点屏推进"。
        """
        self._toolbar_hovered = False
        if getattr(self, "_is_entry_phase", False):
            return
        textures = getattr(self, "_toolbar_textures", None) or {}
        if not textures:
            return
        s = self._dpi_scale
        icon = max(6, round(_TOOLBAR_ICON * s))
        pad = round(_TOOLBAR_PAD * s)
        gap = round(_TOOLBAR_GAP * s)
        frame_x = round(_TOOLBAR_FRAME_X * s)
        frame_y = round(_TOOLBAR_FRAME_Y * s)
        bar_w, bar_h = self._toolbar_width(), self._toolbar_height()
        margin = round(_TOOLBAR_MARGIN * s)
        _x, _grad_h, _text_w, box_h, _pad, box_y = self._story_box_geometry(w, h)
        x = max(0.0, w - bar_w - margin)
        y = max(0.0, box_y + box_h - bar_h - float(round(10 * s)))
        imgui.set_next_window_pos(iu.v2(x, y))
        imgui.set_next_window_size(iu.v2(bar_w, bar_h))
        imgui.push_style_var(imgui.StyleVar_.window_padding, iu.v2(0.0, 0.0))
        imgui.push_style_var(imgui.StyleVar_.item_spacing, iu.v2(float(gap), 0.0))
        imgui.push_style_var(imgui.StyleVar_.frame_padding,
                             iu.v2(float(pad), float(pad)))
        imgui.push_style_color(imgui.Col_.window_bg, iu.u32(10, 12, 20, 100))
        imgui.push_style_color(imgui.Col_.border, iu.u32(255, 255, 255, 32))
        imgui.push_style_color(imgui.Col_.button, iu.u32(0, 0, 0, 0))
        imgui.push_style_color(imgui.Col_.button_hovered, iu.u32(255, 255, 255, 46))
        imgui.push_style_color(imgui.Col_.button_active, iu.u32(255, 255, 255, 72))
        #: 供自检/后续阶段读的"UI 状态快照"（P7 的闸门会用到）
        self._toolbar_rect = (x, y, bar_w, bar_h)
        imgui.begin(_TOOLBAR, None,
                    _panel_flags()
                    | imgui.WindowFlags_.no_scrollbar.value
                    | imgui.WindowFlags_.no_scroll_with_mouse.value
                    | imgui.WindowFlags_.always_auto_resize.value)
        imgui.set_cursor_pos(iu.v2(float(frame_x), float(frame_y)))
        autoplay_on = bool(getattr(self, "_autoplay", False))
        for index, (key, tip) in enumerate(_TOOLBAR_ACTIONS):
            texture = textures.get(key)
            if texture is None:
                continue
            if index:
                imgui.same_line()
            tint = _vec4(_TOOLBAR_TINT_ON if (key == "play" and autoplay_on)
                         else _TOOLBAR_TINT)
            ref = imgui.ImTextureRef(int(texture.texture_id()))
            clicked = imgui.image_button(
                f"##dungeon_toolbar_{key}", ref, iu.v2(float(icon), float(icon)),
                None, None, None, tint)
            if imgui.is_item_hovered():
                self._toolbar_hovered = True
                imgui.set_tooltip(tip)
            if clicked:
                self._on_toolbar_action(key)
        imgui.end()
        imgui.pop_style_color(5)
        imgui.pop_style_var(3)

    def _on_toolbar_action(self, key):
        """工具条按钮 → 与快捷键等效的窗口服务。"""
        handler = {"play": self.toggle_autoplay,
                   "camera": self.take_screenshot,
                   "log": self.toggle_proc_log}.get(key)
        if callable(handler):
            handler()

    def _push_ui_font(self):
        """界面档字体（工具条/面板/通知）。"""
        imgui.push_font(getattr(self, "_font_ui", None), UI_FONT_SIZE)

    # ==================== 绘制：轻通知 ====================
    def _notify(self, text):
        """显示一条轻通知（仅帧线程调用），约 3 秒后自动消失。

        立即模式下"显示"只是置状态；到期处理在 :meth:`_draw_notification` 里
        按 ``time.monotonic()`` 判定，因此不需要帧任务来隐藏它。
        """
        self._notify_text = str(text)
        self._notify_until = time.monotonic() + _NOTIFY_DURATION

    def _hide_notification(self):
        """提前收起通知（保留接口：旧调用点与自检会用到）。"""
        self._notify_until = 0.0

    def _draw_notification(self, w: float, h: float):
        text = getattr(self, "_notify_text", "")
        if not text or time.monotonic() >= getattr(self, "_notify_until", 0.0):
            return
        s = self._dpi_scale
        margin = round(_NOTIFY_MARGIN * s)
        pad = round(8 * s)
        box_w = min(float(self._notify_width), max(1.0, w - 2 * margin))
        wrap = max(1.0, box_w - 2 * pad)
        size = imgui.calc_text_size(text, None, False, wrap)
        box_h = max(float(round(40 * s)), float(size.y) + 2 * pad)
        x0 = max(0.0, (w - box_w) / 2.0)
        y0 = float(margin)
        draw_list = imgui.get_window_draw_list()
        draw_list.add_rect_filled(iu.v2(x0, y0), iu.v2(x0 + box_w, y0 + box_h),
                                  iu.u32(*_NOTIFY_BG), float(round(6 * s)))
        # 立即模式没有"带换行的 drawlist 文本"（``add_text`` 不收 wrap_width 参数）：
        # 把光标挪进框内，用 `text_wrapped` 画，换行宽度由 push_text_wrap_pos 给出。
        self._push_ui_font()
        imgui.set_cursor_pos(iu.v2(x0 + pad, y0 + pad))
        imgui.push_text_wrap_pos(x0 + box_w - pad)
        imgui.push_style_color(imgui.Col_.text, iu.u32(*_NOTIFY_COLOR))
        imgui.text_wrapped(text)
        imgui.pop_style_color()
        imgui.pop_text_wrap_pos()
        imgui.pop_font()

    # ==================== 绘制：入口面板（P3 完整版） ====================
    def _push_entry_panel_style(self):
        """入口面板/结局面板共用的半透明深色主题（对齐旧 DPG panel theme）。"""
        s = self._dpi_scale
        pad = float(round(_ENTRY_PANEL_PAD * s))
        imgui.push_style_var(imgui.StyleVar_.window_padding, iu.v2(pad, pad))
        imgui.push_style_var(imgui.StyleVar_.item_spacing,
                             iu.v2(float(round(6 * s)), float(round(7 * s))))
        imgui.push_style_var(imgui.StyleVar_.window_rounding,
                             float(round(8 * s)))
        imgui.push_style_color(imgui.Col_.window_bg, iu.u32(*_ENTRY_PANEL_BG))
        imgui.push_style_color(imgui.Col_.border, iu.u32(*_ENTRY_PANEL_BORDER))
        imgui.push_style_color(imgui.Col_.text, iu.u32(*_ENTRY_TEXT))
        imgui.push_style_color(imgui.Col_.frame_bg, iu.u32(*_ENTRY_FRAME_BG))
        imgui.push_style_color(imgui.Col_.frame_bg_hovered,
                               iu.u32(*_ENTRY_FRAME_BG_HOVERED))
        imgui.push_style_color(imgui.Col_.frame_bg_active,
                               iu.u32(*_ENTRY_FRAME_BG_ACTIVE))
        imgui.push_style_color(imgui.Col_.popup_bg, iu.u32(*_ENTRY_POPUP_BG))
        imgui.push_style_color(imgui.Col_.header, iu.u32(*_ENTRY_HEADER))
        imgui.push_style_color(imgui.Col_.header_hovered,
                               iu.u32(*_ENTRY_HEADER_HOVERED))
        imgui.push_style_color(imgui.Col_.header_active,
                               iu.u32(*_ENTRY_HEADER_ACTIVE))

    def _pop_entry_panel_style(self):
        imgui.pop_style_color(10)
        imgui.pop_style_var(3)

    def _draw_entry_panel(self, w: float, h: float):
        """右下角方案选择面板（P3：与旧 DPG ``panel_controls`` 同口径）。

        立即模式每帧重建；尺寸/配色/按钮层级都对齐旧版。方案 AP 消耗走
        ``launcher._entry_cost_cache``，不再逐帧读配置。
        """
        s = self._dpi_scale
        margin = round(_ENTRY_PANEL_MARGIN * s)
        cw = float(round(_ENTRY_PANEL_W * s))
        ch = float(round(_ENTRY_PANEL_H * s))
        x = max(0.0, w - cw - margin)
        y = max(0.0, h - ch - margin)

        items = list(self.scenario_ids) or ["（无可用副本方案）"]
        if not 0 <= self._entry_selection < len(items):
            self._entry_selection = 0

        self._entry_panel_rect = (x, y, cw, ch)
        self._push_entry_panel_style()
        imgui.set_next_window_pos(iu.v2(x, y))
        imgui.set_next_window_size(iu.v2(cw, ch))
        imgui.begin(_ENTRY_PANEL, None,
                    _panel_flags()
                    | imgui.WindowFlags_.no_scrollbar.value
                    | imgui.WindowFlags_.no_scroll_with_mouse.value)
        inner_w = max(1.0, cw - 2 * float(round(_ENTRY_PANEL_PAD * s)))

        imgui.push_style_color(imgui.Col_.text, iu.u32(*_ENTRY_TITLE))
        imgui.text("进入副本")
        imgui.pop_style_color()
        imgui.push_style_color(imgui.Col_.text, iu.u32(*_ENTRY_HINT))
        imgui.text("选择副本方案")
        imgui.pop_style_color()

        imgui.set_next_item_width(inner_w)
        changed, index = imgui.combo("##dungeon", self._entry_selection, items)
        if changed and 0 <= index < len(items):
            self._entry_selection = index
            self._on_dungeon_changed()

        imgui.push_style_color(imgui.Col_.text, iu.u32(*_ENTRY_INFO))
        imgui.text_wrapped(self._entry_info_text())
        imgui.pop_style_color()
        imgui.spacing()

        enabled = bool(self.scenario_ids)
        if not enabled:
            imgui.begin_disabled()
        imgui.push_style_color(imgui.Col_.button, iu.u32(*_ENTRY_START_BG))
        imgui.push_style_color(imgui.Col_.button_hovered,
                               iu.u32(*_ENTRY_START_BG_HOVERED))
        imgui.push_style_color(imgui.Col_.button_active,
                               iu.u32(*_ENTRY_START_BG_ACTIVE))
        imgui.push_style_color(imgui.Col_.text, iu.u32(255, 255, 255, 255))
        if imgui.button("开始副本", iu.v2(inner_w, float(round(40 * s)))):
            self._on_entry_start()
        imgui.pop_style_color(4)
        if not enabled:
            imgui.end_disabled()

        half = inner_w / 2.0 - 5.0
        row_h = float(round(34 * s))
        imgui.push_style_color(imgui.Col_.button, iu.u32(*_ENTRY_GHOST_BG))
        imgui.push_style_color(imgui.Col_.button_hovered,
                               iu.u32(*_ENTRY_GHOST_BG_HOVERED))
        imgui.push_style_color(imgui.Col_.button_active,
                               iu.u32(*_ENTRY_GHOST_BG_ACTIVE))
        imgui.push_style_color(imgui.Col_.border, iu.u32(*_ENTRY_GHOST_BORDER))
        imgui.push_style_var(imgui.StyleVar_.frame_border_size, 1.0)
        if imgui.button("加载回放", iu.v2(half, row_h)):
            self._on_entry_replay()
        imgui.same_line()
        if imgui.button("返回", iu.v2(half, row_h)):
            self._on_entry_cancel()
        imgui.pop_style_var()
        imgui.pop_style_color(4)

        imgui.end()
        self._pop_entry_panel_style()

    def _entry_info_text(self) -> str:
        """面板里的行动点说明（AP 消耗按方案缓存，避免逐帧读配置）。"""
        if not self.scenario_ids:
            return "请先到「副本编辑」创建副本方案"
        selection = (self.scenario_ids[self._entry_selection]
                     if 0 <= self._entry_selection < len(self.scenario_ids)
                     else "")
        if not selection:
            return "行动点消耗 0 AP"
        cache = getattr(self, "_entry_cost_cache", None)
        if cache is None:
            cache = {}
            self._entry_cost_cache = cache
        if selection not in cache:
            cost = 0
            if self.scenario_repo is not None:
                try:
                    config = self.scenario_repo.load_config(selection)
                    cost = max(0, int((config or {}).get("entry_action_cost", 0) or 0))
                except (TypeError, ValueError):
                    cost = 0
                except Exception:
                    cost = 0
            cache[selection] = cost
        text = f"行动点消耗 {cache[selection]} AP"
        if self.character is not None:
            text += f"　|　剩余 {getattr(self.character, 'action_points', 0)} AP"
        return text

    # ==================== 绘制：已通关结局面板（P3） ====================
    def _draw_ending_panel(self, w: float, h: float):
        """左下角结局图标轮播：图标 + 名称 + 达成时间/副本。

        没有结局记录时不绘制（与旧实现一致）；纹理由
        ``launcher._preload_ending_textures`` 在 GL 就绪后预载，轮播下标由
        ``launcher._start_ending_cycle`` 的帧任务推进。
        """
        items = getattr(self, "_ending_items", None) or []
        textures = getattr(self, "_ending_textures", None) or []
        if not items or not textures:
            return
        idx = getattr(self, "_ending_index", 0)
        if not 0 <= idx < len(items):
            idx = 0
        item = items[idx]
        texture = textures[idx] if 0 <= idx < len(textures) else None

        s = self._dpi_scale
        margin = round(_ENTRY_PANEL_MARGIN * s)
        pw = float(round(_ENTRY_ENDING_PANEL_W * s))
        ph = float(round(_ENTRY_ENDING_PANEL_H * s))
        x = float(margin)
        y = max(0.0, h - ph - margin)
        self._ending_panel_rect = (x, y, pw, ph)

        self._push_entry_panel_style()
        imgui.set_next_window_pos(iu.v2(x, y))
        imgui.set_next_window_size(iu.v2(pw, ph))
        imgui.begin(_ENDING_PANEL, None,
                    _panel_flags()
                    | imgui.WindowFlags_.no_scrollbar.value
                    | imgui.WindowFlags_.no_scroll_with_mouse.value)
        inner_w = max(1.0, pw - 2 * float(round(_ENTRY_PANEL_PAD * s)))
        imgui.push_style_color(imgui.Col_.text, iu.u32(*_ENTRY_ENDING_HEAD))
        imgui.text(f"已通关结局（{len(items)}）")
        imgui.pop_style_color()

        icon = float(round(_ENTRY_ENDING_ICON * s))
        if texture is not None:
            ref = imgui.ImTextureRef(int(texture.texture_id()))
            imgui.image(ref, iu.v2(icon, icon))
        else:
            imgui.dummy(iu.v2(icon, icon))
        imgui.same_line()

        available = max(40.0, inner_w - icon - float(round(12 * s)))
        imgui.push_text_wrap_pos(imgui.get_cursor_pos_x() + available)
        imgui.text_wrapped(str(item.get("name") or "未命名结局"))
        imgui.push_style_color(imgui.Col_.text, iu.u32(*_ENTRY_ENDING_TIME))
        achieved = str(item.get("achieved_at") or "")
        scenario = str(item.get("scenario_id") or "")
        meta = f"{achieved}　{scenario}" if scenario else achieved
        imgui.text_wrapped(meta)
        imgui.pop_style_color()
        imgui.pop_text_wrap_pos()
        imgui.spacing()
        imgui.push_style_color(imgui.Col_.text, iu.u32(*_ENTRY_ENDING_FOOT))
        imgui.text_wrapped("点击「开始副本」进入新的冒险")
        imgui.pop_style_color()

        imgui.end()
        self._pop_entry_panel_style()

    # ==================== 绘制：选项弹窗 ====================
    def _draw_option_dialog(self, w: float, h: float):
        """待选选项的居中面板（选项触发器经 ``OptionHandler`` 填 labels）。"""
        pending = getattr(self, "pending_option", None)
        if not pending:
            return
        labels = list(pending.get("labels") or [])
        if not labels:
            return
        s = self._dpi_scale
        dw = float(round(440 * s))
        row_h = float(round(46 * s))
        dh = float(round(70 * s)) + row_h * len(labels)
        imgui.set_next_window_pos(iu.v2(max(0.0, (w - dw) / 2.0),
                                       max(0.0, (h - dh) / 2.0)))
        imgui.set_next_window_size(iu.v2(dw, dh))
        imgui.begin(_OPTION_DIALOG, None, _panel_flags())
        self._push_ui_font()
        imgui.text_wrapped(self._option_dialog_title())
        imgui.spacing()
        for index, label in enumerate(labels):
            if imgui.button(f"{label}###opt{index}",
                            iu.v2(dw - 2 * float(round(12 * s)), row_h - 8.0)):
                self._on_option_chosen(None, None, index)
                break
        imgui.pop_font()
        imgui.end()

    def _option_dialog_title(self) -> str:
        from dungeon.coupling import coupling_prompts, normalize_coupling_level
        try:
            pack = coupling_prompts(normalize_coupling_level(
                getattr(self, "coupling_level", None)))
            return str(pack.get("option_dialog_title") or "请选择")
        except Exception:
            return "请选择"

    # ==================== 输入接线 ====================
    def _handle_input(self):
        """立即模式的键鼠接线（取代 DPG 的 ``handler_registry``）。

        ``is_key_pressed`` 天然是"本帧按下"，不需要自己做边沿检测；鼠标点击要
        排除落在控件上的那一次（``is_any_item_hovered``），否则点按钮会顺带
        推进一句剧情。
        """
        key = imgui.Key
        # F11 在入口页也生效（与 DPG 时代的全局 handler 等价）；其余键位只属于会话
        if imgui.is_key_pressed(key.f11):
            self._toggle_fullscreen()
        if getattr(self, "_is_entry_phase", False):
            return
        if imgui.is_key_pressed(key.f12):
            self._on_proc_log_hotkey()
        if imgui.is_key_pressed(key.f2):
            self._on_screenshot_hotkey()
        if imgui.is_key_pressed(key.h):
            self._on_log_key()
        if imgui.is_key_pressed(key.a):
            self._on_autoplay_hotkey()
        if imgui.is_key_pressed(key.escape):
            self._on_escape_key()
        if (imgui.is_key_pressed(key.space) or imgui.is_key_pressed(key.enter)
                or imgui.is_key_pressed(key.keypad_enter)):
            self._on_key_down()
        if (imgui.is_mouse_clicked(imgui.MouseButton_.left)
                and not imgui.is_any_item_hovered()
                and not imgui.is_any_item_active()):
            self._on_mouse_click()

    def _on_mouse_click(self, sender=None, app_data=None):
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

    def _on_key_down(self, sender=None, app_data=None):
        if getattr(self, "_is_entry_phase", False):
            return
        self._on_next_step()

    def ui_snapshot(self) -> dict:
        """窗口的 **UI 状态快照**：自检/探针/AI 驾驶读窗口状态的唯一入口。

        立即模式没有 retained 控件树可以内省——DPG 时代的
        ``does_item_exist`` / ``get_item_configuration`` 在这里没有对应物，
        P7 的 autopilot 改造正需要一份"窗口现在长什么样"的稳定契约。因此凡是
        自检需要判定的东西都在这里出：阶段、尺寸、正文与上屏游标、视效是否在播。

        返回的是**值快照**（数字/布尔/元组/字符串），调用方不必知道窗口内部字段名，
        窗口也能自由重构内部实现。
        """
        reveal = getattr(self, "_reveal", None)
        fx = self._window_fx
        overlay = fx.pixel_overlay()
        story = list(getattr(self, "story_history", None) or [])
        reveal_target = reveal.get("target") if reveal else ""
        return {
            "phase": "entry" if getattr(self, "_is_entry_phase", False) else "session",
            "closing": bool(getattr(self, "_closing", False)),
            "fullscreen": bool(getattr(self, "is_fullscreen", False)),
            "dpi_scale": round(float(getattr(self, "_dpi_scale", 1.0) or 1.0), 4),
            "layout": (float(self._layout_w), float(self._layout_h)),
            "create_size": (int(self._runner.width), int(self._runner.height)),
            "frames": int(getattr(self, "frames_rendered", 0)),
            "history": len(story),
            "visible_items": len(self._story_visible_items()),
            "reveal_active": reveal is not None,
            "reveal_pos": int(reveal["pos"]) if reveal else 0,
            "reveal_len": len(reveal_target or ""),
            "waiting_input": bool(self._waiting_for_continue()),
            "autoplay": bool(getattr(self, "_autoplay", False)),
            "overlay_open": bool(self.overlay_open()),
            "option_open": bool(getattr(self, "pending_option", None)),
            "toolbar_rect": tuple(getattr(self, "_toolbar_rect", ()) or ()),
            "entry_panel_rect": tuple(getattr(self, "_entry_panel_rect", ()) or ()),
            "ending_panel_rect": tuple(getattr(self, "_ending_panel_rect", ()) or ()),
            "components": sorted(getattr(c, "id", "?")
                                 for c in getattr(self, "_components", [])),
            # 视效（P5）
            "fx_active": sorted(getattr(fx, "_active", ()) or ()),
            "fx_flash_alpha": round(float(getattr(fx, "_flash_alpha", 0.0)), 2),
            "fx_shake": tuple(round(float(v), 2)
                              for v in getattr(fx, "_shake", (0.0, 0.0))),
            "fx_overlay": (int(overlay[0].width), int(overlay[0].height), overlay[1])
            if overlay is not None else None,
        }

    def _toggle_fullscreen(self, sender=None, app_data=None):
        """F11：切换整屏（走 GLFW 窗口，见 ``glfw_fullscreen`` 模块文档）。

        P1 走的 ``window_geometry.full_screen_mode`` 在 1.92.900 上是坏的（实测把
        窗口变成 420×34 且回不去），``glfw_utils.GlfwHelpers`` 本版本不存在。可用
        的是 ``hello_imgui.get_glfw_window_address()`` + imgui_bundle 自带的
        ``glfw3.dll``：零新增依赖，实测进/出全屏的位置与尺寸都能原样还原。
        """
        if not FULLSCREEN.available:
            self._notify("全屏切换不可用（未找到 glfw3 运行库）")
            return
        result = FULLSCREEN.toggle()
        if result is None:
            self._notify("全屏切换失败")
            return
        self.is_fullscreen = bool(result)
        # 离散尺寸跳变：下一帧立刻按新客户区重排 + 背景绕过防抖重采样
        self._bg_refresh_fast = True

    def _on_viewport_resize(self, sender=None, app_data=None):
        """保留接口：立即模式下尺寸变化由 :meth:`_draw_frame` 每帧自检。"""
        self._schedule_relayout()

    # ==================== 过程日志 / 自动播放 / 截图 ====================
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

    def toggle_autoplay(self):
        """切换自动播放：上屏结束并停留一段时间后自动推进（会话与回放均适用）。

        选项弹窗 / 结局 / 生成 / 上屏期间自动暂停，无需人工干预；再次切换关闭。
        """
        if getattr(self, "_is_entry_phase", False):
            return
        self._autoplay = not getattr(self, "_autoplay", False)
        if self._autoplay:
            # 开启即刻给一个到期时刻，避免开启瞬间把还没上完的段落推过去
            self._advance_due = max(getattr(self, "_advance_due", 0.0),
                                    time.monotonic() + _AUTOPLAY_DWELL)
            self._frame.every(_AUTOPLAY_INTERVAL, self._autoplay_tick,
                              key=_AUTOPLAY_TASK)
        else:
            self._frame.cancel(_AUTOPLAY_TASK)
        self._notify("自动播放已开启" if self._autoplay else "自动播放已关闭")
        self._refresh_components()   # 工具条按钮同步「自动中」状态标签

    def _autoplay_tick(self):
        """自动播放采样：真正的节奏由 _advance_due（上屏收尾 + 停留）决定。

        本 tick 只是廉价采样，三重复查后才推进：
        1. 会话 / 覆盖层 / 等待条件（与继续指示同源，含生成中、结局、选项）；
        2. 上屏游标必须已收尾——游标还在推进说明本段没上完屏，等它自己走完；
        3. 上屏收尾后的停留期已过——长句多停一点，避免刚上完屏就翻页。
        """
        if self._closing or not getattr(self, "_autoplay", False):
            return
        if self.overlay_open():
            return
        # 与继续指示的等待条件同源（组件服务面 session_waiting_for_input）
        if not self.session_waiting_for_input():
            return
        state = getattr(self, "_reveal", None)
        if state is not None and state["pos"] < len(state["target"]):
            return   # 上屏未结束：等游标走完
        if time.monotonic() < getattr(self, "_advance_due", 0.0):
            return   # 停留期内：再等等
        self._on_next_step()

    def _on_autoplay_hotkey(self, sender=None, app_data=None):
        self.toggle_autoplay()

    def take_screenshot(self):
        """把当前帧保存为 PNG 到用户数据区 ``screenshots/`` 下（下一帧落盘）。

        实际的整帧抓取发生在 :meth:`_capture_frame`（``after_swap``，GL 上下文
        仍然当前）；这里只置「下一帧抓一张」的意图位。
        """
        self._screenshot_pending = True

    def _capture_frame(self):
        """（帧线程，``after_swap``）整帧抓取：F2 截图与 ``invert``/``rgb_split``
        视效共用同一条通路。

        平时这里是几条布尔判定，没有开销；只有真的要看画面时才回读后备缓冲。
        """
        # 视效优先：反色/色差只需要一帧，抓完就让给截图（同帧不会两件都做）
        self._window_fx.capture()
        if not getattr(self, "_screenshot_pending", False):
            return
        self._screenshot_pending = False
        self._save_screenshot()

    def _save_screenshot(self):
        """回读整帧并交给像素工作者落盘（PNG 编码 + 磁盘 IO 不占帧）。"""
        io = imgui.get_io()
        w = int(round(io.display_size.x * io.display_framebuffer_scale.x))
        h = int(round(io.display_size.y * io.display_framebuffer_scale.y))
        rgba = gl_util.read_pixels(w, h)
        if rgba is None:
            process_log.log("[Screenshot] 抓帧失败（GL 回调不可用）")
            self._notify("截图失败：无法读取画面")
            return
        from paths import data_dir
        folder = os.path.join(data_dir(), "user", "screenshots")
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as exc:
            process_log.log(f"[Screenshot] 截图目录创建失败: {exc}")
            self._notify(f"截图失败：{exc}")
            return
        filename = time.strftime("dungeon_%Y%m%d_%H%M%S.png")
        path = os.path.join(folder, filename)
        worker = getattr(self, "_shot_worker", None)
        # GL 的通道序是 RGBA，PIL 按 "RGBA" 读没问题；截图落盘用 RGB 更省
        # （画面本来就不透明），转换与 PNG 编码都在工作者线程里做。
        if worker is None or not worker.submit(
                lambda: self._write_screenshot(rgba, path, filename)):
            self._notify("截图失败：像素工作者不可用")

    def _write_screenshot(self, rgba, path, filename):
        """（工作者线程）PNG 编码 + 落盘，完成后回帧线程弹通知。"""
        try:
            from PIL import Image
            Image.fromarray(rgba, "RGBA").convert("RGB").save(path)
        except Exception as exc:
            process_log.log(f"[Screenshot] 截图失败: {exc}")
            self._frame.call(self._notify, f"截图失败：{exc}")
            return
        process_log.log(f"[Screenshot] 已保存: {path}")
        # 通知延后到截图落盘之后弹出，避免通知条被截进画面
        self._frame.after(0.3, self._notify, f"截图已保存：{filename}")

    def _on_screenshot_hotkey(self, sender=None, app_data=None):
        self.take_screenshot()

    def _shutdown_screenshot_worker(self):
        """会话收尾（``base._finish_session``）：收掉截图落盘线程。

        等一小会儿让正在进行的 PNG 写入落地即可——写不完的那张图留着半截也不算
        事故（用户能在 ``screenshots/`` 里看到并自己删掉）。
        """
        worker = getattr(self, "_shot_worker", None)
        if worker is not None:
            worker.shutdown()
            self._shot_worker = None

    # ==================== 组件工具条（P4 起才有组件） ====================
    def _component_toolbar_hovered(self):
        """光标是否悬停在功能按钮工具条上（内置工具条或组件工具条）。

        内置工具条由 ``_draw_frame`` 先画（顶层浮层），``_handle_input`` 后跑，
        因此本帧的悬停真值已经写进 ``_toolbar_hovered``。
        """
        if getattr(self, "_toolbar_hovered", False):
            return True
        return any(comp.toolbar_hovered()
                   for comp in getattr(self, "_components", []))

    # ==================== 文本更新 ====================
    def _text_owned_by_component(self) -> bool:
        """是否有组件接管文本显示（owns_text_display，如底部渐变式文本栏）。

        接管期间内置文本区整体跳过，显示由组件 refresh 完成。
        """
        return (getattr(self, "_components_built", False)
                and any(getattr(c, "owns_text_display", False)
                        for c in getattr(self, "_components", [])))

    def _update_text_display(self):
        """立即模式下没有"控件树要更新"这回事——正文每帧从 ``story_history`` 重画。

        这里只保留两件仍然有意义的事：把"滚到底"的意图位置起，并驱动组件刷新。
        """
        if self._closing:
            return
        if self._text_owned_by_component():
            self._refresh_components()
            return
        self._refresh_components()

    def _schedule_text_update(self):
        """合并密集刷新：AI 流式会高频调用，转成"下一帧滚到底"的意图位。

        立即模式下面板内容本来就每帧重画，这里保留该入口只为维持旧调用链
        （``engine`` 的流式回调会调它）。
        """
        if self._closing:
            return

    def _flush_text_update(self):
        self._text_update_pending = False
        if not self._closing:
            self._update_text_display()

    def _display_text(self, text: str, text_type: DungeonTextType, highlight: bool = False,
                      speaker: str = None, prefix: str = None):
        # 固定文本段落自带类型前缀（没写类型的段落前缀为空串，回放按记录复现）；
        # 其余调用方不传 prefix，仍按段落类型算
        prefix = self._display_type_prefix(text_type) if prefix is None else prefix
        self.story_history.append({"type_str": prefix, "text": text,
                                   "highlight": highlight, "speaker": speaker})
        self._update_text_display()

    def _try_advance_paragraph(self) -> bool:
        """点击驱动的显示段落推进：三态判定，返回 True 表示本次点击已消化。

        1. 已上屏不足 ``_REVEAL_MIN_CHARS`` 字 → 吞掉本次点击（防误触瞬间吞掉整句，
           也防抢在游标起步前切段）；
        2. 当前段未补满 100% → 补满并停住，**不切段**（「再点一次才切段」）；
        3. 已补满（自然播完或上次点击补满）→ 切到队列里的下一段。

        无游标时（固定文本收尾、插入段、队列已空）直接放行，交给原有生成链。
        """
        state = getattr(self, "_reveal", None)
        if state is not None:
            if state["pos"] < _REVEAL_MIN_CHARS:
                return True                       # ① 吞掉
            if state["pos"] < len(state["target"]):
                self._reveal_snap(state["item"])  # ② 补满，不切段
                return True
        if not self._pending_units:
            return False                          # ③ 无货：放行给生成链
        self._reveal_pending_unit()
        return True

    def _reveal_pending_unit(self):
        """揭示当前逻辑段落的下一个显示段落（内置分句器切出的后续句）。"""
        if not self._pending_units:
            return
        unit = self._pending_units.pop(0)
        # 首句已带段落类型前缀，后续句是同一逻辑段落的延续，不再加前缀；
        # 说话人按单元携带的标记解析结果（Solea/Bulla 对话分支）
        item = {"type_str": "", "text": unit.text, "speaker": unit.speaker}
        self.story_history.append(item)
        self._reveal_start(item, unit.text)
        self._speak_item(item)

    def _speak_item(self, item: dict):
        """带说话人的显示单元定稿：交给语音导演朗读。

        只有 Solea/Bulla 对话分支里带 ``speaker`` 的台词会念（叙述句不念）；
        合成与播放都在语音线程上，这里只入队，不占帧循环。
        """
        director = getattr(self, "_speech", None)
        if director is None or not item.get("speaker"):
            return
        director.speak(item.get("speaker"), item.get("text"))

    # ---------- 上屏游标（仿流式输出） ----------
    #
    # 一个游标同时服务两条路径，因此节奏恒定在 _REVEAL_CHARS_PER_SECOND：
    # - 仿流式（预生成 / 固定文本 / 插入段）：目标文本一次给定，游标追上即完。
    # - 真流式：chunk 到达只延长目标文本（_reveal_extend），游标按固定速率追赶，
    #   上屏速度与 AI 吐字速度彻底解耦；分句器因此始终有一份领先的缓冲可用，
    #   不需要为上屏等待——这也是「限制真流式速度」的全部含义。
    #
    # 状态字典（self._reveal）：
    #   item   —— story_history 里的条目，其 text 被原地改写为已上屏前缀
    #   target —— 目标全文，真流式下会随 chunk 增长
    #   pos    —— 已上屏字数（唯一进度真相源，点击判定读它）
    #   frac   —— 本 tick 的字符小数累加器，避免 1/1/2 的抖动感
    #
    # 迁移后**一行未改**：立即模式每帧重画 story_history，因此"原地改写 text"
    # 天然就是"改写即上屏"，不再需要旧实现里那句 _schedule_text_update 收尾。
    def _reveal_active(self) -> bool:
        """是否有进行中的上屏游标。"""
        return getattr(self, "_reveal", None) is not None

    def _reveal_start(self, item, text: str):
        """新起一段上屏：接管游标，从头逐字揭开 text。"""
        self._reveal_snap()          # 旧游标若有，先封口（它已不再是当前段）
        if not text:
            return
        state = {"item": item, "target": text, "pos": 0, "frac": 0.0}
        self._reveal = state

        def step():
            if self._closing or self._reveal is not state:
                self._frame.cancel(_REVEAL_TASK)
                return
            state["frac"] += _REVEAL_CHARS_PER_SECOND * _REVEAL_TICK_SECONDS
            advance = int(state["frac"])
            state["frac"] -= advance
            if advance <= 0:
                return
            state["pos"] = min(len(state["target"]), state["pos"] + advance)
            item["text"] = state["target"][:state["pos"]]
            if state["pos"] >= len(state["target"]):
                # 自然走完：游标必须注销（否则 _reveal_active 永远为真，
                # 继续指示与自动播放都会被这条已走完的游标挡住）
                self._reveal = None
                self._frame.cancel(_REVEAL_TASK)
                self._on_reveal_finished(state["target"])
            self._schedule_text_update()

        self._frame.every(_REVEAL_TICK_SECONDS, step, key=_REVEAL_TASK)

    def _reveal_extend(self, item, text: str):
        """真流式的目标文本增长：只延长 target，游标位置不动。

        新增字符由运行中的 step 循环自然打出；item 与当前段不一致
        （分句器已切走或动画被接管）时忽略，避免续写错误的对象。
        """
        state = getattr(self, "_reveal", None)
        if state is None or state["item"] is not item:
            return
        if len(text) > len(state["target"]):
            state["target"] = text

    def _reveal_snap(self, item=None):
        """把当前段立即补满到 100%（点击加速 / 段落收尾）。

        传入 item 时只在该游标属于该段落时生效；不传则无条件补满。
        """
        state = getattr(self, "_reveal", None)
        if not state:
            return
        if item is not None and state["item"] is not item:
            return
        state["item"]["text"] = state["target"]
        state["pos"] = len(state["target"])
        self._reveal = None
        frame = getattr(self, "_frame", None)
        if frame is not None:
            frame.cancel(_REVEAL_TASK)
        self._schedule_text_update()

    def _on_reveal_finished(self, text: str):
        """上屏收尾钩子：为自动播放登记「本段停留到何时」。"""
        self._advance_due = (time.monotonic() + _AUTOPLAY_DWELL
                             + _AUTOPLAY_DWELL_PER_CHAR * len(text))

    # ==================== 布局与 DPI ====================
    def _update_dpi_scale(self):
        """窗口移动到不同 DPI 的显示器后，重新取窗口 DPI 并更新边距比例。

        注：建窗尺寸与字体缩放都由 hello_imgui 自己按 DPI 处理，这里更新的是
        **本层手写边距**所用的 ``_dpi_scale``（与 DPG 时代的语义一致）。
        """
        if not getattr(self, "_is_windows", False):
            return
        try:
            import ctypes
            title = getattr(self, "_real_title", "") or ""
            if not title:
                return
            hwnd = ctypes.windll.user32.FindWindowW(None, title)
            if not hwnd:
                return
            dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
            if dpi > 0:
                self._dpi_scale = max(0.5, dpi / 96.0)
        except Exception:
            pass

    def _schedule_relayout(self):
        """通过调度器在下一帧执行重排，避免多路调用相互覆盖。"""
        if getattr(self, "_relayout_pending", False):
            return
        self._relayout_pending = True
        self._frame.call(self._flush_relayout)

    def _flush_relayout(self):
        self._relayout_pending = False
        self._relayout()

    def _relayout(self):
        """重算**派生几何**。

        立即模式里没有"重排控件树"这一步——所有几何在 ``_draw_frame`` 里现算。
        本方法因此只做三件仍然必要的事：刷新边距比例、重算文本换行宽度与通知条
        宽度、触发背景按新尺寸重采样。
        """
        self._update_dpi_scale()
        w, h = self._layout_w, self._layout_h
        if w <= 1 or h <= 1:
            return
        s = self._dpi_scale
        margin = round(_NOTIFY_MARGIN * s)
        self._notify_width = min(round(_NOTIFY_WIDTH * s), max(1, int(w) - 2 * margin))
        _x, _grad_h, cw, pad = self._story_geometry(w, h)
        self._text_wrap_width = max(1.0, cw - 2 * pad)

        if getattr(self, "_components_built", False):
            relayout_components = getattr(self, "_relayout_components", None)
            if callable(relayout_components):
                relayout_components()
        self._window_fx.relayout(w, h)
        self._refresh_background()

    def _relayout_entry_panels(self, w, h):
        """保留接口：入口面板几何在 :meth:`_draw_entry_panel` 里按客户区现算。"""

    # ---------- 背景接线（供 background.py 回调） ----------
    def change_background(self, image_path, smooth_transition=False,
                          filter_effect=None, rotate_angle=None, blur_radius=None):
        self._background.change(image_path, smooth_transition, filter_effect,
                                rotate_angle, blur_radius)

    def _refresh_background(self, delay=0.08):
        if getattr(self, "_bg_refresh_fast", False):
            # 全屏切换等离散跳变：防抖归零，重采样立即上工作者
            self._bg_refresh_fast = False
            delay = 0.0
        self._background.refresh(delay)

    def _apply_prepared_bg(self, pil_img, rgba, w, h, revision):
        """（帧线程）把工作者备好的背景上纹理（``_prepare_refresh`` 的回调）。"""
        if self._closing or revision != self._bg_revision:
            return
        if (w, h) != (self._layout_w, self._layout_h):
            return
        self._apply_bg_data(pil_img, rgba, w, h)

    def _set_bg_texture(self, rgba, w, h, revision):
        """（帧线程）淡入淡出中间帧上纹理。

        ``pil_img`` 传 None：中间帧不更新 ``_bg_pil_original``（终帧由
        ``_finish_bg_fade`` 更新）。
        """
        if revision != self._bg_revision or (w, h) != (self._layout_w, self._layout_h):
            return
        self._apply_bg_data(None, rgba, w, h)

    def _finish_bg_fade(self, pil_img, w, h, revision):
        if revision == self._bg_revision and (w, h) == (self._layout_w, self._layout_h):
            self._bg_pil_original = pil_img

    def _apply_bg_data(self, pil_img, rgba, width, height):
        """把 ``width × height`` 的 RGBA 数据上到背景纹理（仅帧线程）。

        与 DPG 时代的关键差异：``TextureGpu`` **不能原地改像素**（P0-S3 实测），
        所以每次都是"新建一个精确尺寸的纹理句柄 + 丢掉旧的"。实测 720p 上传
        1.97 ms/帧且不泄漏，代价可接受；换来的是彻底删掉旧实现里那套
        「纹理只增不减 + uv 子区域映射 + 组合缓冲」（DPG 对动态纹理改尺寸有
        存储复用怪癖才需要它们）。
        """
        if rgba is None:
            return
        if not getattr(self, "_gl_ready", False):
            # 窗口/GL 上下文尚未创建（入口页与挑战模式会在 ``runner.run()``
            # 之前就设置背景）。``create_texture_gpu_from_rgba_data`` 在无
            # 当前上下文时是 access violation（不是 Python 异常，try 拦不住），
            # 因此先暂存，首帧由 ``_draw_frame`` 上传。
            self._pending_bg = (pil_img, rgba, width, height)
            return
        try:
            texture = hello_imgui.create_texture_gpu_from_rgba_data(rgba)
        except Exception as exc:
            process_log.log(f"背景纹理上传失败: {exc}")
            return
        # 先建后丢：中间不留"没有纹理"的帧，避免闪黑
        self._bg_texture = texture
        if pil_img is not None:
            # 只有终帧才更新"当前原始图"（淡入淡出的中间帧传 None）
            self._bg_pil_original = pil_img

    # ==================== 结局图标（P4 占位） ====================
    def _update_ending_icon(self):
        """把结局图标显示在故事区。

        P4 占位：旧实现走 DPG 的静态纹理 + ``add_image``；imgui 侧改为
        ``create_texture_gpu_from_rgba_data`` + ``add_image``，与结局图标轮播
        （P3）一并处理。
        """
        process_log.log("[Ending] 结局图标显示待 P4 迁移")
