# -*- coding: utf-8 -*-
"""小游戏离屏画布「分块纹理上传」守卫（无 GUI，可进 CI 门禁）。

``dungeon/window/minigame/stage.py`` 把 :meth:`GameAPI.offscreen` 出来的画布切成
多张块纹理（``_CANVAS_CHUNK`` 见方），每帧只重传「内容变了且可见」的块——这是
小游戏帧预算的关键：整张 1088×768 重传一次实测 ≈ 14.6 ms（RGBA→float32 平铺
6 ms + ``set_value`` 10 ms），等于一整帧，而每帧真正变化的往往只有几格地块。

这条优化有两个**只有肉眼才能发现**的失败模式，因此单独守在这里：

1. **漏记脏区** → 画面上残留旧像素（地块被炸掉了、外观却没变）。
   每个绘制方法都必须把本次包围盒记进 ``_PILCanvas._dirty``；
2. **块边界处理错** → 每块四边各出现一条 1 px 拉伸缝。DPG 不暴露纹理过滤开关
   （只能线性采样），只能靠「块纹理带 1 px 出血 + uv 按块内位置内缩」把采样点
   对齐到与整张纹理完全相同的texel 坐标。

检查**不建 DPG 上下文**（把纹理注册与绘制换成记录器），但需要 import ``stage``
（它 import dearpygui）；import 不了时打印 SKIP 并退 0——与
``check_component_pack.py`` 里的组件冒烟同一处理。

用法：``python tests/check_minigame_canvas.py``；有违规时退出码 1。
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

_FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print(f"  OK   {label}")
    else:
        print(f"  FAIL {label}" + (f"  <{detail}>" if detail else ""))
        _FAILURES.append(label)


try:
    import numpy as np
    from PIL import ImageChops
    from dungeon.window.minigame import stage as S
except Exception as exc:                     # 无 DPG / 无 numpy 的环境直接跳过
    print(f"[check_minigame_canvas] SKIP 无法 import 依赖（{exc}）")
    sys.exit(0)

from dungeon.window.minigame import base as _base  # noqa: E402

#: 画布的块尺寸与出血（与 stage 的常量对齐；改动它们是设计变更，这里跟着改）
CHUNK = S._CANVAS_CHUNK
BLEED = S._CANVAS_BLEED

# ---------------------------------------------------------------- 替身
_RECORDS = {}     # tag → 最近一次上传的纹理数据 (h, w, 4) float32
_DRAWN = []       # 最近一次 draw 画到的 tag


class _FakeDPG:
    """DPG 替身：只记录纹理与绘制调用，不打上下文（无显示器也能跑）。"""

    @staticmethod
    def add_dynamic_texture(w, h, data, tag=None, parent=None):
        assert len(data) == w * h * 4, f"纹理数据长度 {len(data)} != {w * h * 4}"
        _RECORDS[tag] = np.asarray(data, dtype=np.float32).reshape(h, w, 4)
        return tag

    @staticmethod
    def set_value(tag, data):
        h, w = _RECORDS[tag].shape[:2]
        assert len(data) == w * h * 4, f"重传长度 {len(data)} != {w * h * 4}"
        _RECORDS[tag] = np.asarray(data, dtype=np.float32).reshape(h, w, 4)

    @staticmethod
    def draw_image(tag, pmin, pmax, uv_min=None, uv_max=None, parent=None):
        assert tag in _RECORDS, f"画了从未上传过的纹理 {tag}"
        _DRAWN.append({"tag": tag, "pmin": (float(pmin[0]), float(pmin[1])),
                       "pmax": (float(pmax[0]), float(pmax[1])),
                       "uv_min": (float(uv_min[0]), float(uv_min[1])),
                       "uv_max": (float(uv_max[0]), float(uv_max[1]))})

    @staticmethod
    def does_item_exist(tag):
        return True

    @staticmethod
    def delete_item(tag):
        _RECORDS.pop(tag, None)


S.dpg.add_dynamic_texture = _FakeDPG.add_dynamic_texture
S.dpg.set_value = _FakeDPG.set_value
S.dpg.draw_image = _FakeDPG.draw_image
S.dpg.does_item_exist = _FakeDPG.does_item_exist
S.dpg.delete_item = _FakeDPG.delete_item
# 舞台侧的纹理集注册表在 __init__ 里建，这里只用到绘制路径


def expect_chunk_bounds(cx, cy, w, h):
    """块的**期望**覆盖范围（含出血、夹在画布内）——独立于实现写一遍。

    故意不复用 ``_CanvasTextures._chunk_bounds``：那是被检对象，复用它等于自己
    证明自己。这里只按「CHUNK 见方 + 四周 BLEED 像素、越界夹回去」的文字规则算。
    """
    x0, y0 = cx * CHUNK, cy * CHUNK
    x1, y1 = min(w, x0 + CHUNK), min(h, y0 + CHUNK)
    return (max(0, x0 - BLEED), max(0, y0 - BLEED),
            min(w, x1 + BLEED), min(h, y1 + BLEED))


# ---------------------------------------------------------------- 1) 脏区
print("[check_minigame_canvas] 1/4 脏区记录：每个绘制方法都要记脏，且脏区 ⊇ 实际改动")
_CANVAS_W, _CANVAS_H = 300, 200

_DRAW_CASES = [
    ("draw_rect 填充", lambda c: c.draw_rect((13.7, 9.2), (61.4, 44.9),
                                            (255, 0, 0, 255))),
    ("draw_rect 半透明", lambda c: c.draw_rect((13.7, 9.2), (61.4, 44.9),
                                              (255, 0, 0, 90))),
    ("draw_rect 粗描边不填", lambda c: c.draw_rect((20, 20), (80, 60),
                                                  (0, 255, 0, 255), fill=False,
                                                  thickness=5.0)),
    ("draw_round_rect", lambda c: c.draw_round_rect((20, 20), (90, 70), 9,
                                                    (0, 0, 255, 255))),
    ("draw_circle", lambda c: c.draw_circle((70.4, 60.9), 26.5, (255, 255, 0, 255))),
    ("draw_circle 粗边不填", lambda c: c.draw_circle((70.4, 60.9), 26.5,
                                                    (255, 255, 0, 255),
                                                    fill=False, thickness=4.0)),
    ("draw_line", lambda c: c.draw_line((5, 5), (130, 90), (255, 0, 255, 255))),
    ("draw_line 粗线", lambda c: c.draw_line((5, 5), (130, 90), (255, 0, 255, 255),
                                            thickness=6.0)),
    ("draw_image(另一块画布)",
     lambda c: c.draw_image(S._PILCanvas(20, 20), (100, 100), (120, 120))),
    ("draw_text 中文", lambda c: c.draw_text("测试文本ABC", (20, 30),
                                             (255, 255, 255, 255), size=18)),
    ("draw_text 大字号", lambda c: c.draw_text("HUD 关卡 3/5", (10, 40),
                                               (255, 255, 255, 255), size=48)),
]
for label, draw in _DRAW_CASES:
    canvas = S._PILCanvas(_CANVAS_W, _CANVAS_H)
    before = canvas._img.copy()
    try:
        draw(canvas)
    except Exception as exc:                  # 字体等环境问题不判失败
        print(f"  SKIP {label}（{exc}）")
        continue
    real = ImageChops.difference(before, canvas._img).getbbox()
    if real is None:
        print(f"  SKIP {label}（PIL 未算出差异）")
        continue
    if canvas._dirty_all:
        check(f"{label}：整张脏覆盖 {real}", True)
        continue
    covered = any(b[0] <= real[0] and b[1] <= real[1]
                  and b[2] >= real[2] and b[3] >= real[3] for b in canvas._dirty)
    check(f"{label}：脏区覆盖实际改动 {real}", covered, f"脏={canvas._dirty}")

# 一个「新增绘制方法忘了记脏」的兜底：所有公开绘制方法都要走 _bump
_public = [name for name in dir(S._PILCanvas)
           if name.startswith("draw_") and callable(getattr(S._PILCanvas, name))]
_source = open(os.path.join(_ROOT, "dungeon", "window", "minigame", "stage.py"),
               encoding="utf-8").read()
_mark_missing = []
for name in _public:
    start = _source.find(f"def {name}(")
    if start < 0:
        _mark_missing.append(name)               # 源码里找不到定义
        continue
    nxt = _source.find("\n    def ", start + 1)
    body = _source[start:nxt if nxt > 0 else len(_source)]
    if "_bump(" not in body:
        _mark_missing.append(name)
check("每个 _PILCanvas 绘制方法都调用了 _bump（记脏）", not _mark_missing, _mark_missing)
check("绘制方法清单与作者契约一致",
      set(_public) == {"draw_rect", "draw_round_rect", "draw_circle", "draw_line",
                       "draw_text", "draw_image"},
      sorted(_public))

# ---------------------------------------------------------------- 2) 几何
print("[check_minigame_canvas] 2/4 分块几何：uv 与屏幕映射落点一致、无缝无叠、块范围合规")
_GEOM_CASES = [
    (1088, 768, 200.0, 100.0, 512.0, 512.0, 1008.0, "视野居中"),
    (1088, 768, 0.0, 0.0, 512.0, 512.0, 1008.0, "视野贴左上（块在画布边）"),
    (1088, 768, 576.0, 256.0, 512.0, 512.0, 1008.0, "视野贴右下（块在画布边）"),
    (1088, 768, 100.37, 42.91, 512.0, 512.0, 1008.0, "小数相机（逐帧真实情形）"),
    (1088, 768, 255.0, 255.0, 512.0, 512.0, 1008.0, "视野起点压在块边界上"),
    (1088, 768, 0.0, 0.0, 1088.0, 768.0, 1008.0, "整张画布（uv = 全图）"),
    (300, 200, 13.5, 7.25, 128.0, 96.0, 640.0, "小于一块的画布"),
    (1088, 768, 511.5, 511.5, 1.0, 1.0, 100.0, "1 px 视野（极窄）"),
]
for cw, ch, rx0, ry0, rw, rh, scr, label in _GEOM_CASES:
    canvas = S._PILCanvas(cw, ch)
    ct = S._CanvasTextures(canvas)
    uv = (rx0 / cw, ry0 / ch, (rx0 + rw) / cw, (ry0 + rh) / ch)
    _DRAWN.clear()
    ct.draw((0.0, 0.0), (scr, scr * rh / rw), uv)
    rx1, ry1 = rx0 + rw, ry0 + rh
    sx = scr / rw
    sy = (scr * rh / rw) / rh
    problems = []
    want = set()
    for cy in range(int(ry0 // CHUNK), int((ry1 - 1e-6) // CHUNK) + 1):
        for cx in range(int(rx0 // CHUNK), int((rx1 - 1e-6) // CHUNK) + 1):
            want.add((cx, cy))
    if {(int(c["tag"].rsplit("_", 2)[-2]), int(c["tag"].rsplit("_", 2)[-1]))
            for c in _DRAWN} != want:
        problems.append("绘制的块集合与视野相交的块集合不一致")
    area = 0.0
    for call in _DRAWN:
        cx, cy = (int(v) for v in call["tag"].rsplit("_", 2)[-2:])
        bx0, by0, bx1, by1 = expect_chunk_bounds(cx, cy, cw, ch)
        tw, th = bx1 - bx0, by1 - by0
        from_uv = (call["uv_min"][0] * tw + bx0, call["uv_min"][1] * th + by0,
                   call["uv_max"][0] * tw + bx0, call["uv_max"][1] * th + by0)
        from_scr = (rx0 + call["pmin"][0] / sx, ry0 + call["pmin"][1] / sy,
                    rx0 + call["pmax"][0] / sx, ry0 + call["pmax"][1] / sy)
        if any(abs(a - b) > 1e-9 for a, b in zip(from_uv, from_scr)):
            problems.append(f"块({cx},{cy}) uv 落点与屏幕落点不一致："
                            f"{tuple(round(v, 4) for v in from_uv)} vs "
                            f"{tuple(round(v, 4) for v in from_scr)}")
        # 块的四条边不得跨过画布范围
        if bx0 < 0 or by0 < 0 or bx1 > cw or by1 > ch:
            problems.append(f"块({cx},{cy}) 纹理范围越出画布 {(bx0, by0, bx1, by1)}")
        area += (call["pmax"][0] - call["pmin"][0]) * (call["pmax"][1] - call["pmin"][1])
    span_x = scr * (rx1 - rx0) / rw
    span_y = (scr * rh / rw) * (ry1 - ry0) / rh
    if abs(area - span_x * span_y) > 1e-6 * span_x * span_y:
        problems.append(f"各块屏幕面积之和 {area:.3f} != 请求区域 {span_x * span_y:.3f}")
    check(f"{label}：块数/落点/无缝无叠", not problems, "；".join(problems))

# ---------------------------------------------------------------- 3) 像素
print("[check_minigame_canvas] 3/4 像素级：分块采样 == 整张纹理采样（否则就是可见的缝）")
_CW = _CH = 520
_rng = np.random.default_rng(3)
# 逐像素独立噪声是最坏情况内容：块边界只要错一点就立刻显形
_full = _rng.integers(0, 256, (_CH, _CW, 4)).astype(np.float32)
_canvas = S._PILCanvas(_CW, _CH)
_canvas._img.putdata([tuple(int(v) for v in px) for px in _full.reshape(-1, 4)])
_SCR = 400
_RX0, _RY0, _RW, _RH = 100.3, 80.7, 300.0, 300.0
_mesh = np.meshgrid(_RX0 + (np.arange(_SCR) + 0.5) * (_RW / _SCR),
                    _RY0 + (np.arange(_SCR) + 0.5) * (_RH / _SCR))
_CX, _CY = _mesh


def _bilinear(tex, tw, th, ox, oy, xs, ys):
    """GL 约定的双线性：画布坐标 c 的采样位置是 ``c - 原点 - 0.5``。"""
    x, y = xs - ox - 0.5, ys - oy - 0.5
    x0 = np.clip(np.floor(x).astype(np.int64), 0, tw - 1)
    y0 = np.clip(np.floor(y).astype(np.int64), 0, th - 1)
    fx, fy = x - x0, y - y0
    x1 = np.clip(x0 + 1, 0, tw - 1)
    y1 = np.clip(y0 + 1, 0, th - 1)
    return (tex[y0, x0] * ((1 - fx) * (1 - fy))[:, :, None]
            + tex[y0, x1] * (fx * (1 - fy))[:, :, None]
            + tex[y1, x0] * ((1 - fx) * fy)[:, :, None]
            + tex[y1, x1] * (fx * fy)[:, :, None])


print("  （用块自己记录的 uv 去采样「按独立规则裁出来的块图」，再与整张纹理比）")
ct = S._CanvasTextures(_canvas)
_DRAWN.clear()
ct.draw((0.0, 0.0), (float(_SCR), float(_SCR)),
        (_RX0 / _CW, _RY0 / _CH, (_RX0 + _RW) / _CW, (_RY0 + _RH) / _CH))
_ref = _bilinear(_full, _CW, _CH, 0, 0, _CX, _CY)
_worst = 0.0
_covered = np.zeros((_SCR, _SCR), dtype=bool)
for call in _DRAWN:
    cx, cy = (int(v) for v in call["tag"].rsplit("_", 2)[-2:])
    bx0, by0, bx1, by1 = expect_chunk_bounds(cx, cy, _CW, _CH)
    tex = np.asarray(_canvas._img.crop((bx0, by0, bx1, by1)), dtype=np.float32)
    tw, th = bx1 - bx0, by1 - by0
    # 屏幕矩形 → 像素索引（与实现无关：直接用 pmin/pmax，半开区间）
    ix0 = int(np.ceil(max(call["pmin"][0], 0)))
    ix1 = int(np.ceil(min(call["pmax"][0], _SCR)))
    iy0 = int(np.ceil(max(call["pmin"][1], 0)))
    iy1 = int(np.ceil(min(call["pmax"][1], _SCR)))
    if ix1 <= ix0 or iy1 <= iy0:
        continue
    # 屏幕像素中心 → 画布坐标 → **用实现记录的 uv** 换算到块内 texel 坐标。
    # 这一步是检查的要害：uv 若与块几何对不上，采样就会落到错误的 texel/越界。
    _px = _CX[iy0:iy1, ix0:ix1]
    _py = _CY[iy0:iy1, ix0:ix1]
    # uv 是「块内子矩形」的纹理局部归一化坐标（uv_min=(ix0-bx0)/tw），
    # 先反推出子矩形，再按线性映射取 u —— 不能拿它当整块的 [0,1] 端点。
    ux0 = call["uv_min"][0] * tw + bx0
    ux1 = call["uv_max"][0] * tw + bx0
    uy0 = call["uv_min"][1] * th + by0
    uy1 = call["uv_max"][1] * th + by0
    u = call["uv_min"][0] + (_px - ux0) / (ux1 - ux0) * (call["uv_max"][0] - call["uv_min"][0])
    v = call["uv_min"][1] + (_py - uy0) / (uy1 - uy0) * (call["uv_max"][1] - call["uv_min"][1])
    xs_ = u * tw
    ys_ = v * th
    got = _bilinear(tex, tw, th, 0.0, 0.0, xs_, ys_)
    _worst = max(_worst, float(np.abs(got - _ref[iy0:iy1, ix0:ix1]).max()))
    _covered[iy0:iy1, ix0:ix1] = True
check(f"分块采样与整张纹理采样一致（最大差 {_worst * 255:.4f}/255 ≤ 1）",
      _worst * 255 <= 1.0, f"{_worst * 255:.4f}/255")
check("覆盖了全部屏幕像素（没有空洞）", bool(_covered.all()),
      f"{int(_covered.sum())}/{_SCR * _SCR}")

# 敏感性对照：不做出血、uv 直接用 [0,1]（把每块当独立图片）→ 边界必然显形。
# 它证明上面那条检查真的能抓到「块边界处理错误」。
_naive = np.zeros((_SCR, _SCR, 4), dtype=np.float32)
for cy in range(0, (_CH + CHUNK - 1) // CHUNK):
    for cx in range(0, (_CW + CHUNK - 1) // CHUNK):
        x0, y0 = cx * CHUNK, cy * CHUNK
        x1, y1 = min(_CW, x0 + CHUNK), min(_CH, y0 + CHUNK)
        tex = np.asarray(_canvas._img.crop((x0, y0, x1, y1)), dtype=np.float32)
        sx = _SCR / _RW
        sy = _SCR / _RH
        ix0 = int(np.ceil(max((x0 - _RX0) * sx, 0)))
        ix1 = int(np.ceil(min((x1 - _RX0) * sx, _SCR)))
        iy0 = int(np.ceil(max((y0 - _RY0) * sy, 0)))
        iy1 = int(np.ceil(min((y1 - _RY0) * sy, _SCR)))
        if ix1 <= ix0 or iy1 <= iy0:
            continue
        u = (_CX[iy0:iy1, ix0:ix1] - x0) / (x1 - x0)
        v = (_CY[iy0:iy1, ix0:ix1] - y0) / (y1 - y0)
        _naive[iy0:iy1, ix0:ix1] = _bilinear(tex, x1 - x0, y1 - y0, 0.0, 0.0,
                                             u * (x1 - x0), v * (y1 - y0))
_naive_diff = float(np.abs(_naive - _ref).max()) * 255.0
check(f"敏感性对照：无出血写法确实会显出差异（{_naive_diff:.1f}/255 > 8）",
      _naive_diff > 8.0, f"{_naive_diff:.2f}/255")

# ---------------------------------------------------------------- 4) 回收
print("[check_minigame_canvas] 4/4 生命周期：换关整块换掉画布时，旧纹理集要被回收")
import gc  # noqa: E402

_deleted = []
S.dpg.does_item_exist = lambda tag: True
S.dpg.delete_item = lambda tag: _deleted.append(tag)


class _StageShell:
    """只带 ``_canvas_texes`` 的舞台外壳，用来直接调未绑定的 ``_draw_canvas``。"""

    def __init__(self):
        self._canvas_texes = {}


shell = _StageShell()
old_canvas = S._PILCanvas(600, 400)
S._MiniGameStage._draw_canvas(shell, old_canvas, (0.0, 0.0), (400.0, 400.0), None)
_old_tags = len(shell._canvas_texes[old_canvas._uid]._tags)
check("首块画布建出了纹理", _old_tags > 0, _old_tags)
del old_canvas
gc.collect()
new_canvas = S._PILCanvas(600, 400)
S._MiniGameStage._draw_canvas(shell, new_canvas, (0.0, 0.0), (400.0, 400.0), None)
check("旧画布的纹理集被回收（不随关卡累积）",
      len(shell._canvas_texes) == 1 and len(_deleted) == _old_tags,
      f"纹理集={len(shell._canvas_texes)} 已删={len(_deleted)}/{_old_tags}")

# ---------------------------------------------------------------- 汇总
print()
if _FAILURES:
    print(f"[check_minigame_canvas] FAILED {len(_FAILURES)} 项：")
    for item in _FAILURES:
        print(f"  - {item}")
    sys.exit(1)
print("[check_minigame_canvas] PASSED：离屏画布分块上传（脏区/几何/像素/回收）全部通过")
sys.exit(0)
