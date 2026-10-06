"""小游戏作者 API：写一个 Python 类就是一个小游戏。

本模块是**作者契约**，刻意不 import DPG——游戏作者只需要认识
:class:`MiniGame`（生命周期）和 :class:`GameAPI`（绘制 / 输入 / 服务面）：

    from dungeon.window.minigame import MiniGame

    class MyGame(MiniGame):
        id = "my_game"
        label = "我的游戏"

        def setup(self, cfg):
            self._x = 100

        def update(self, dt):
            if self.api.key_down("d"):
                self._x += 200 * dt
            self.api.draw_circle((self._x, 100), 20, (255, 200, 60, 255))

约束（``tests/check_minigame.py`` 强制）：

- **禁止 import dearpygui**：绘制一律走 :class:`GameAPI` 的薄包装，drawlist、
  纹理生命周期、帧时钟都由运行时（``stage.py``）管；
- **禁线程、禁 print**：``update`` 由帧时钟每帧调用，重活 / 输出都不该在这里；
- **不许碰私有面**：``self.api._`` 一律不许出现——运行时随时可能重构。

坐标系：画布左上角为原点，像素单位，尺寸见 ``api.width`` / ``api.height``
（随会话窗口缩放，游戏每帧读一次即可自适应）。颜色一律 ``(r, g, b, a)``。
"""


class GameAPI:
    """运行时提供给游戏的服务面（由 ``stage.py`` 实现；本类只承载文档与签名）。

    方法可能抛异常——``update`` 里的异常会被运行时捕获并按「无结果」收场，
    不会拖垮副本会话；游戏自身应尽量自洽。
    """

    # ---------------- 几何 ----------------
    @property
    def width(self) -> int:
        """画布宽（像素）。"""
        raise NotImplementedError

    @property
    def height(self) -> int:
        """画布高（像素）。"""
        raise NotImplementedError

    # ---------------- 绘制（每帧 update 内发出，运行时负责清屏与提交） ----------------
    def draw_rect(self, pmin, pmax, color, fill=True, thickness=1.0):
        """矩形。``pmin``/``pmax`` 为对角坐标。"""

    def draw_round_rect(self, pmin, pmax, radius, color, fill=True, thickness=1.0):
        """圆角矩形。"""

    def draw_circle(self, center, radius, color, fill=True, segments=0, thickness=1.0):
        """圆。``segments=0`` 表示运行时取默认细分。"""

    def draw_line(self, p1, p2, color, thickness=1.0):
        """线段。"""

    def draw_polygon(self, points, color):
        """多边形（点列表，自动闭合填充）。"""

    def draw_text(self, text, pos, color, size=18):
        """单行文本（``size`` 为像素字号）。"""

    def draw_image(self, source, pmin, pmax, uv=None):
        """贴图。``source`` 可为：

        - 文件路径（png/jpg，相对小游戏包目录或绝对路径）；
        - :meth:`offscreen` 返回的离屏画布（内容变化后自动重新上传）。

        ``uv=(u0, v0, u1, v1)``：可选的源区域裁剪（0~1 归一化坐标）。
        失败时静默跳过（不拖垮帧）。
        """

    def offscreen(self, width, height, bg=None):
        """创建一块离屏画布（:class:`MiniCanvas`），用于程序化预渲染。

        典型用法：关卡生成时把整张地形 / 织物画进离屏画布，此后每帧只
        ``draw_image`` 一次——drawlist 每帧的指令数量与地图复杂度无关。
        画布内容变化后再画会自动重新上传纹理（按内容版本判定）。
        """

    # ---------------- 输入（轮询式，配游戏循环） ----------------
    def key_down(self, key) -> bool:
        """键当前是否按住。``key``：``"a"``-``"z"``/``"0"``-``"9"``/``"space"``/
        ``"enter"``/``"escape"``/``"up"``/``"down"``/``"left"``/``"right"``/
        ``"shift"``/``"ctrl"``。"""

    def mouse_pos(self):
        """鼠标在画布上的坐标 ``(x, y)``。"""

    def mouse_down(self, button=0) -> bool:
        """鼠标键当前是否按住（``0`` 左键 / ``1`` 右键）。"""

    # ---------------- 服务 ----------------
    def finish(self, result=None):
        """结束小游戏并回传结果（重复调用无效）。

        小游戏**不定义成功与失败**，只回传一个整数返回值（语义由游戏自定，
        如达成关数、命中数）。两种传法：

        - ``api.finish(3)``：整数即返回值；
        - ``api.finish({"level": 3, "score": 120})``：``level`` 键是返回值，
          其余字段整体并入触发器记录（``game_result``）供叙事引用。

        返回值记入触发器的选择数组（与选项编号同一通道），跳转等分支由
        其他触发器用「选择:触发器名」条件按返回值判定（次数 / 占比 / 趋势 /
        最后一次）。用户按 ESC 或关掉会话窗口则按「无结果」收场，不记返回值。
        """

    def hud(self, text):
        """设置顶部 HUD 一行文本（空串隐藏）。"""

    def notify(self, message):
        """会话轻通知（3 秒自动消失）。"""

    def read_state(self, key):
        """只读副本状态快照。``key``：``"章节"``/``"身高"``（巨大娘身高米数）/
        ``"介入度"``/``"破坏性"``/``"总伤亡"``/``"总步数"``；未知键返回 ``None``。"""


class MiniCanvas:
    """离屏画布（:meth:`GameAPI.offscreen` 的返回值）：程序化预渲染用。

    绘制方法与 :class:`GameAPI` 同名方法同签名，但坐标系是画布自己的
    （左上角原点），且**立即渲染进画布内容**（不是每帧指令）。画布内容
    变化后，下一次 :meth:`GameAPI.draw_image` 该画布时自动重新上传纹理。
    """

    @property
    def width(self) -> int:
        """画布宽（像素）。"""
        raise NotImplementedError

    @property
    def height(self) -> int:
        """画布高（像素）。"""
        raise NotImplementedError

    def draw_rect(self, pmin, pmax, color, fill=True, thickness=1.0):
        """矩形。"""

    def draw_round_rect(self, pmin, pmax, radius, color, fill=True, thickness=1.0):
        """圆角矩形。"""

    def draw_circle(self, center, radius, color, fill=True, thickness=1.0):
        """圆。"""

    def draw_line(self, p1, p2, color, thickness=1.0):
        """线段。"""

    def draw_text(self, text, pos, color, size=18):
        """单行文本。"""

    def draw_image(self, source, pmin, pmax, uv=None):
        """把贴图 / 另一块画布合成进来（缩放到 pmin~pmax）。"""


class MiniGame:
    """小游戏基类：子类写在 ``data/packs/minigames/<id>/game.py``（py 后端）。

    类属性 ``id`` / ``label`` / ``description`` 进注册表与编辑器候选；
    ``params`` 声明可配参数（schema 风格，编辑器后续可自动生成表单）。
    生命周期由运行时驱动：``setup`` 一次 → ``update`` 每帧 → ``teardown`` 一次；
    ``self.api`` 在 ``setup`` 前注入。结果经 ``self.api.finish()`` 回传——
    用户按 ESC 或关掉会话窗口则按「无结果」收场，副本照常继续。
    """

    #: 与包目录 / manifest 一致的游戏 id（contract 脚本校验一致性）
    id = ""
    label = ""
    description = ""
    params = ()
    #: 画面长宽比（宽 / 高，游戏硬编码）；None / ≤0 = 填满整个会话窗口。
    #: 声明后运行时把画面等比缩放进「窗口减去边缘留白」的区域内居中，
    #: 其余部分维持副本界面的压暗背景（小游戏覆盖层的半透明底色）。
    #: 游戏内容（含 HUD 与越界绘制）一律裁剪在该区域内，不会外溢。
    aspect = None

    def __init__(self):
        #: 运行时注入的服务面（:class:`GameAPI`）；setup 前不可用
        self.api = None

    def setup(self, config):
        """开局一次。``config`` 为触发器 ``mini_game`` 动作的 ``action_data``。"""

    def update(self, dt):
        """每帧一次：推进逻辑并发绘制指令。``dt`` 为距上一帧的秒数（≤0.05）。"""

    def teardown(self):
        """收尾一次（释放自持资源；纹理等由运行时回收，不必管）。"""


def clamp_dt(dt) -> float:
    """帧步长夹取：掉帧不补跑，也不允许负值 / 爆值进游戏逻辑。"""
    try:
        dt = float(dt)
    except (TypeError, ValueError):
        return 0.0
    if dt <= 0.0 or dt != dt:  # noqa: PLR0133  NaN 判定
        return 0.0
    return min(dt, 0.05)


__all__ = ["GameAPI", "MiniGame", "clamp_dt"]
