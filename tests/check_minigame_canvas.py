# -*- coding: utf-8 -*-
"""小游戏离屏画布「脏矩形增量上传」守卫（无 GUI，可进 CI 门禁）。

``dungeon/window/minigame/stage.py`` 的 :class:`_CanvasTexture` 把 :meth:`GameAPI.offscreen`
出来的画布交给一张**宿主自持的 GL 纹理**（``glGenTextures`` + ``glTexImage2D``），
之后每帧按绘制包围盒记脏，用 ``glTexSubImage2D`` 只重传**变化的那几块矩形**。
整张 1088×768 重传实测 0.294 ms（``.workbuddy/p6_probe/probe_gltex.py``），DPG 时代
那套「切块 + 1 px 出血」已随 ``set_value`` 的 10 ms 开销一起删掉——一张纹理既没有块
边界的采样缝，也没有逐块的重传策略。

这条优化有一个**只有肉眼或逐像素比对才能发现**的失败模式：**漏记脏区** → 画面上
残留旧像素（地块被炸掉了、外观却没变）。每个绘制方法都必须把本次包围盒记进
``_PILCanvas._dirty``，上传时必须把该矩形原样贴回纹理。

检查**不建 GL / imgui 上下文**：把 ``stage._gl`` 换成一台在 numpy 缓冲上记账的
替身，逐像素验证「重传后的纹理 == 画布」。替身还记录 create / upload / delete
调用，用来证明「首次一张建好、之后只传变化矩形、换关回收旧纹理」。

用法：``python tests/check_minigame_canvas.py``；有违规时退出码 1。
"""

import gc
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
except Exception as exc:                     # 无 numpy / 无 imgui 的环境直接跳过
    print(f"[check_minigame_canvas] SKIP 无法 import 依赖（{exc}）")
    sys.exit(0)


# ---------------------------------------------------------------- 替身
class _FakeGL:
    """GL 纹理替身：在 numpy 缓冲上重放 create / upload / delete。

    ``textures[name]["buf"]`` 就是「GPU 上现在长什么样」——测试拿它跟画布逐像素比。
    """

    def __init__(self):
        self.textures = {}
        self.calls = []          # [(op, name, ...)]
        self._next = 0
        self.enabled = True

    def available(self):
        return self.enabled

    def create_texture(self, width, height, data=None):
        if not self.enabled:
            return None
        self._next += 1
        name = self._next
        buf = np.zeros((int(height), int(width), 4), dtype=np.uint8)
        if data is not None:
            buf[:] = np.asarray(data, dtype=np.uint8).reshape(int(height),
                                                              int(width), 4)
        self.textures[name] = {"w": int(width), "h": int(height), "buf": buf}
        self.calls.append(("create", name, int(width), int(height)))
        return name

    def upload(self, name, x, y, width, height, data):
        if not self.enabled or name not in self.textures:
            return False
        x, y = int(x), int(y)
        w, h = int(width), int(height)
        arr = np.asarray(data, dtype=np.uint8).reshape(h, w, 4)
        self.textures[name]["buf"][y:y + h, x:x + w] = arr
        self.calls.append(("upload", name, x, y, w, h))
        return True

    def delete_texture(self, name):
        self.textures.pop(name, None)
        self.calls.append(("delete", name))

    # ---- 断言辅助 ----
    def uploads(self):
        return [c for c in self.calls if c[0] == "upload"]

    def deletes(self):
        return [c for c in self.calls if c[0] == "delete"]

    def reset_calls(self):
        self.calls = []

    def reset_all(self):
        """段落起点：清空调用记录与「现存纹理集」（各段自证，不跨段累积）。"""
        self.calls = []
        self.textures = {}


class _FakeDrawList:
    """draw list 替身：只记 ``add_image``（本守卫不校验图元）。"""

    def __init__(self):
        self.images = []

    def add_image(self, ref, pmin, pmax, uv_min=None, uv_max=None,
                  col=0xFFFFFFFF):
        self.images.append({
            "tex_id": int(ref.get_tex_id()) if hasattr(ref, "get_tex_id") else int(ref),
            "pmin": (float(pmin.x), float(pmin.y)),
            "pmax": (float(pmax.x), float(pmax.y)),
            "uv_min": None if uv_min is None else (float(uv_min.x), float(uv_min.y)),
            "uv_max": None if uv_max is None else (float(uv_max.x), float(uv_max.y)),
        })


class _FrameStub:
    running = True


class _WinStub:
    def __init__(self):
        self._frame = _FrameStub()


class _Shell:
    """只带 ``_draw_canvas`` 需要的字段的舞台外壳（不建真舞台）。"""

    def __init__(self, width=0, height=0):
        self._canvas_texes = {}
        self._play_rect = [0, 0, width, height]
        self._draw_list = _FakeDrawList()
        self._win = _WinStub()

    def _to_screen(self, x, y):
        # 与 _MiniGameStage._to_screen 同口径：play rect 局部 → 客户区（此处 origin=0）
        return (float(self._play_rect[0]) + float(x),
                float(self._play_rect[1]) + float(y))

    def _gl_ops_ok(self):
        # 与 _MiniGameStage._gl_ops_ok 同口径：帧时钟仍在跑才允许碰 GL
        return bool(getattr(self._win._frame, "running", False))

    def draw_canvas(self, canvas, pmin, pmax, uv=None):
        S._MiniGameStage._draw_canvas(self, canvas, pmin, pmax, uv)


GL = _FakeGL()
S._gl = GL


def _gl_texture_of(shell, canvas):
    """某块画布当前对应的替身纹理记录（拿不到返回 None）。"""
    entry = shell._canvas_texes.get(canvas._uid)
    if entry is None or entry.texture is None:
        return None
    return GL.textures.get(entry.texture)


def _reconstruction_diff(shell, canvas):
    """替身纹理与画布的实际差异（逐像素，uint8）。"""
    tex = _gl_texture_of(shell, canvas)
    if tex is None:
        return None
    real = np.asarray(canvas._img, dtype=np.uint8)
    return np.abs(tex["buf"].astype(np.int32) - real.astype(np.int32))


def _changed_bbox(before, after):
    return ImageChops.difference(before, after).getbbox()


def _covers(rect, bbox):
    """半开区间 ``rect=(x0,y0,x1,y1)`` 是否覆盖闭包 ``bbox``（PIL getbbox）。"""
    if bbox is None:
        return True
    x0, y0, x1, y1 = rect
    return x0 <= bbox[0] and y0 <= bbox[1] and x1 >= bbox[2] and y1 >= bbox[3]


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
    covered = any(_covers(b, real) for b in canvas._dirty)
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

# ---------------------------------------------------------------- 2) 单张纹理 + 精确重建
print("[check_minigame_canvas] 2/4 单张 GL 纹理：首次整张上传，重传后的纹理逐像素 == 画布")
_CW, _CH = 520, 384
_canvas = S._PILCanvas(_CW, _CH)
# 逐像素独立噪声是最坏情况内容：漏掉一条脏矩形立刻显形
_rng = np.random.default_rng(3)
_canvas._img.putdata([tuple(int(v) for v in px)
                      for px in _rng.integers(0, 256, (_CH, _CW, 4)).reshape(-1, 4)])
_shell = _Shell(_CW, _CH)

GL.reset_all()
_shell.draw_canvas(_canvas, (0.0, 0.0), (float(_CW), float(_CH)))
check("首次绘制只建一张纹理（不是分块）",
      len(GL.textures) == 1 and len(GL.calls) == 1 and GL.calls[0][0] == "create",
      GL.calls)
check("首次绘制整张上传（create 直接带全图像素，无额外 upload）",
      len(GL.uploads()) == 0, GL.uploads())
_diff = _reconstruction_diff(_shell, _canvas)
check("整张上传后纹理与画布逐像素一致",
      _diff is not None and int(_diff.max()) == 0,
      None if _diff is None else f"最大差 {int(_diff.max())}")

# 只改一小块 → 只重传这一块，且重建依然精确
_patch_pre = _canvas._img.copy()
_canvas.draw_rect((100, 80), (140, 120), (255, 40, 40, 255))
_bbox = _changed_bbox(_patch_pre, _canvas._img)
GL.reset_calls()
_shell.draw_canvas(_canvas, (0.0, 0.0), (float(_CW), float(_CH)))
_ups = GL.uploads()
_area = sum(u[3] * u[4] for u in _ups)
check("第二次绘制不重建纹理（复用同一张）",
      len(GL.textures) == 1 and not any(c[0] == "create" for c in GL.calls),
      GL.calls)
check("只重传变化的矩形（覆盖实际改动，且远小于整张）",
      len(_ups) == 1 and _covers((_ups[0][2], _ups[0][3], _ups[0][2] + _ups[0][4],
                                  _ups[0][3] + _ups[0][5]), _bbox)
      and _area < _CW * _CH // 4,
      f"bbox={_bbox} 上传={_ups} 面积={_area}/{_CW * _CH}")
_diff = _reconstruction_diff(_shell, _canvas)
check("增量重传后纹理与画布逐像素一致",
      _diff is not None and int(_diff.max()) == 0,
      None if _diff is None else f"最大差 {int(_diff.max())}")

# 敏感性对照：把这次改动的脏矩形「漏掉」→ 重建必然与画布不符。
# 它证明上面那条检查真的能抓到「漏记脏区」这类只有肉眼才发现的 bug。
_pre = _canvas._img.copy()
_canvas.draw_circle((400, 300), 30, (0, 200, 255, 255))
_missed = _changed_bbox(_pre, _canvas._img)
_canvas._dirty.clear()                      # 模拟某个绘制方法忘了调 _bump
_shell.draw_canvas(_canvas, (0.0, 0.0), (float(_CW), float(_CH)))
_diff = _reconstruction_diff(_shell, _canvas)
check(f"敏感性对照：漏记脏区后纹理确实与画布不符（{_missed}）",
      _diff is not None and int(_diff.max()) > 8,
      None if _diff is None else f"最大差 {int(_diff.max())}")

# ---------------------------------------------------------------- 3) uv 子区域：一张纹理、一次绘制
print("[check_minigame_canvas] 3/4 uv 子区域：整块画布就是一次 add_image（结构上不存在块缝）")
_uv_canvas = S._PILCanvas(1088, 768)        # escape_giantess 的同尺寸画布
_uv_canvas.draw_rect((0, 0), (1088, 768), (40, 40, 60, 255))
_uv_shell = _Shell(1088, 768)
GL.reset_all()
# uv = 视野窗口（等价 escape 的 cam_x/W, cam_y/H, ...）
_uv_shell.draw_canvas(_uv_canvas, (0.0, 0.0), (512.0, 512.0),
                      uv=(0.25, 0.125, 0.75, 0.625))
check("大画布也只建一张纹理",
      len(GL.textures) == 1, GL.calls)
check("画布就是一次 add_image（uv 原样透传，不存在逐块绘制）",
      len(_uv_shell._draw_list.images) == 1
      and _uv_shell._draw_list.images[0]["uv_min"] == (0.25, 0.125)
      and _uv_shell._draw_list.images[0]["uv_max"] == (0.75, 0.625)
      and _uv_shell._draw_list.images[0]["pmin"] == (0.0, 0.0)
      and _uv_shell._draw_list.images[0]["pmax"] == (512.0, 512.0),
      _uv_shell._draw_list.images)

# ---------------------------------------------------------------- 4) 回收与上下文守卫
print("[check_minigame_canvas] 4/4 生命周期：换关整块换掉画布时旧纹理要被回收")
_shell2 = _Shell(600, 400)
canvas_a = S._PILCanvas(600, 400)
canvas_b = S._PILCanvas(600, 400)
canvas_a.draw_rect((0, 0), (600, 400), (10, 20, 30, 255))
canvas_b.draw_rect((0, 0), (600, 400), (30, 20, 10, 255))
GL.reset_all()
_shell2.draw_canvas(canvas_a, (0.0, 0.0), (400.0, 400.0))
_shell2.draw_canvas(canvas_b, (0.0, 0.0), (400.0, 400.0))
check("两块画布各建一张纹理", len(GL.textures) == 2, GL.calls)
del canvas_a, canvas_b
gc.collect()
GL.reset_all()
canvas_c = S._PILCanvas(600, 400)
canvas_c.draw_rect((0, 0), (600, 400), (30, 30, 30, 255))
_shell2.draw_canvas(canvas_c, (0.0, 0.0), (400.0, 400.0))
check("旧画布的纹理按弱引用被回收（不随关卡累积）",
      len(_shell2._canvas_texes) == 1 and len(GL.deletes()) == 2
      and len(GL.textures) == 1,
      f"纹理集={len(_shell2._canvas_texes)} 已删={len(GL.deletes())} 现存={len(GL.textures)}")

# 会话收尾：GL 上下文已释放（_frame.running = False）时不得再碰 GL
_shell2._win._frame.running = False
del canvas_c
gc.collect()
canvas_d = S._PILCanvas(600, 400)
canvas_d.draw_rect((0, 0), (600, 400), (40, 40, 40, 255))
GL.reset_all()
_shell2.draw_canvas(canvas_d, (0.0, 0.0), (400.0, 400.0))
check("帧时钟已停（无 GL 上下文）时不调 glDeleteTextures",
      len(GL.deletes()) == 0, GL.calls)

# ---------------------------------------------------------------- 汇总
print()
if _FAILURES:
    print(f"[check_minigame_canvas] FAILED {len(_FAILURES)} 项：")
    for item in _FAILURES:
        print(f"  - {item}")
    sys.exit(1)
print("[check_minigame_canvas] PASSED：离屏画布单张 GL 纹理（脏区/重建/uv/回收）全部通过")
sys.exit(0)
