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


__all__ = ["u32", "color", "with_alpha", "alpha_of", "v2", "v2_tuple",
           "lerp", "clamp01"]
