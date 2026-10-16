"""imgui 侧的小工具：不用起 GL 上下文就能用的颜色与几何换算。

为什么单独一个模块：颜色打包、文本测量这类东西 ``ui`` / ``overlay`` / ``fx`` /
``component_pack`` 都要用，且必须在**任何 ImGui 上下文之外**也能调用（例如模块级
常量、替身测试、离线守卫）。放在这里只依赖 ``imgui_bundle.imgui`` 的类型，不碰
运行期状态。

颜色口径：DPG 时代全仓库的常量都写成 ``(r, g, b, a)`` 且分量是 0~255 的整数
（如 ``_TEXT_COLOR = (255, 255, 255, 255)``）。ImGui 要的是打包成 u32 的 ABGR。
:func:`u32` 就做这一件事，于是那些常量的字面值**可以原样搬过来**，不必逐个换算。
"""

from imgui_bundle import imgui


def u32(r, g, b, a=255) -> int:
    """``(r, g, b, a)``（0~255 整数，与旧 DPG 常量同形）→ ImGui 打包色。

    等价于 C 侧的 ``IM_COL32(r, g, b, a)``：低位字节是红，最高字节是不透明度。
    """
    return (((int(a) & 0xFF) << 24) | ((int(b) & 0xFF) << 16)
            | ((int(g) & 0xFF) << 8) | (int(r) & 0xFF))


def color(triple, alpha=255) -> int:
    """三元组 ``(r, g, b)`` + 单独的不透明度 → 打包色（省得每处写四元组）。"""
    r, g, b = triple
    return u32(r, g, b, alpha)


def with_alpha(packed: int, alpha) -> int:
    """换掉一个打包色的不透明度（0~255），保留颜色分量。

    视效消退（闪光/反色覆盖层按时长退到 0）全靠它。
    """
    return (int(packed) & 0x00FFFFFF) | ((int(alpha) & 0xFF) << 24)


def alpha_of(packed: int) -> int:
    """取打包色的不透明度（0~255）。"""
    return (int(packed) >> 24) & 0xFF


def v2(x, y) -> imgui.ImVec2:
    """``(x, y)`` → ``ImVec2``。"""
    return imgui.ImVec2(float(x), float(y))


def v2_tuple(vec) -> tuple:
    """``ImVec2`` → ``(x, y)`` 元组（便于比较与打印）。"""
    return (float(vec.x), float(vec.y))


def lerp(a, b, t):
    """线性插值（``t`` 不夹紧，调用方自己保证范围）。"""
    return a + (b - a) * t


def clamp01(value):
    """夹到 ``[0, 1]``。"""
    return 0.0 if value < 0.0 else (1.0 if value > 1.0 else float(value))


def _shape_play(u, v):
    """右向实心三角（播放）。"""
    if not (0.15 <= v <= 0.85):
        return 0.0
    t = (u - 0.25) / 0.6          # 0 at 左缘顶点边，1 at 右侧尖角
    if not 0.0 <= t <= 1.0:
        return 0.0
    half = 0.35 * (1.0 - t)
    return 1.0 if abs(v - 0.5) <= half else 0.0


def _shape_camera(u, v):
    """相机机身 + 顶视窗 + 镜头圆孔。"""
    if 0.35 <= u <= 0.65 and 0.18 <= v <= 0.32:
        return 1.0
    if 0.12 <= u <= 0.88 and 0.32 <= v <= 0.82:
        du, dv = u - 0.5, v - 0.57
        return 0.0 if du * du + dv * dv <= 0.16 ** 2 else 1.0
    return 0.0


def _shape_log(u, v):
    """三条横线（日志列表）。"""
    for cy in (0.24, 0.5, 0.76):
        if 0.16 <= u <= 0.84 and abs(v - cy) <= 0.055:
            return 1.0
    return 0.0


#: 工具条图标形状函数（归一化 uv → 0~1 不透明度）。与旧 DPG 组件包同一套
#: 形状，P4 迁移组件包时从这里取，避免两处漂移。
ICON_SHAPES = {"play": _shape_play, "camera": _shape_camera, "log": _shape_log}


def icon_rgba(shape, size):
    """按形状函数生成 ``size×size`` 的 RGBA ``numpy`` 图（白色 + alpha 形状）。

    颜色由按钮的 ``tint_col`` 注入，因此 RGB 恒为白；返回 C 连续 ``uint8``，
    可直接交给 ``hello_imgui.create_texture_gpu_from_rgba_data``。不需要 GL
    上下文即可调用（上传才需要）。
    """
    import numpy as np
    size = max(1, int(size))
    img = np.zeros((size, size, 4), dtype=np.uint8)
    step = 1.0 / size
    for y in range(size):
        v = (y + 0.5) * step
        for x in range(size):
            u = (x + 0.5) * step
            alpha = max(0.0, min(1.0, float(shape(u, v))))
            img[y, x, 0] = 255
            img[y, x, 1] = 255
            img[y, x, 2] = 255
            img[y, x, 3] = int(round(255 * alpha))
    return img


__all__ = ["u32", "color", "with_alpha", "alpha_of", "v2", "v2_tuple",
           "lerp", "clamp01", "ICON_SHAPES", "icon_rgba"]
