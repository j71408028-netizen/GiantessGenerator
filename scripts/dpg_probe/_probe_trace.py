"""带面包屑的启动器：跑 dev 窗口并打印时间戳/帧计数，崩溃时最后一行就是现场。

用法：PROBE_MODE=plain|veh python _probe_trace.py escape_giantess --target 1
"""
import os
import runpy
import sys
import time

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)

# 关掉 WER 崩溃上报：原生崩溃立即可见（省掉 5 秒报告延迟，便于批量实验）
if os.environ.get("PROBE_WER") != "1":
    try:
        import ctypes
        ctypes.WinDLL("kernel32").SetErrorMode(0x0001 | 0x0002)
    except Exception:
        pass

_t0 = time.monotonic()


def log(msg):
    print(f"[trace {time.monotonic() - _t0:7.3f}] {msg}", flush=True)


MODE = os.environ.get("PROBE_MODE", "plain")
log(f"mode={MODE}")

if MODE == "veh":
    import _probe_native
    _probe_native.install()

import dearpygui.dearpygui as dpg  # noqa: E402

_frames = [0]
_orig_render = dpg.render_dearpygui_frame
_last_frame_t = [time.monotonic()]


def _render():
    _frames[0] += 1
    now = time.monotonic()
    log(f"render frame {_frames[0]} begin (+{(now - _last_frame_t[0]) * 1000:.0f}ms, "
        f"updates={_counters['update']})")
    _last_frame_t[0] = now
    result = _orig_render()
    log(f"render frame {_frames[0]} end (+{(time.monotonic() - now) * 1000:.0f}ms)")
    return result


dpg.render_dearpygui_frame = _render

# ---- 宿主差异开关：dev 启动器 vs 副本 ----
_HOST = {p.strip() for p in os.environ.get("PROBE_HOST", "").split(",") if p.strip()}
log(f"host switches={sorted(_HOST)}")
if "nomax" in _HOST:
    dpg.maximize_viewport = lambda *a, **k: None
if "noresize" in _HOST:
    dpg.set_viewport_resize_callback = lambda *a, **k: None
if "nofont" in _HOST:
    from dungeon.window import fonts as _fonts_mod
    _fonts_mod.first_existing = lambda _paths: None
if "smallwin" in _HOST:
    _orig_viewport = dpg.create_viewport

    def _create_viewport(*a, **k):
        k["width"], k["height"] = 900, 700
        return _orig_viewport(*a, **k)

    dpg.create_viewport = _create_viewport

from dungeon.window.minigame import stage as stage_mod  # noqa: E402

_orig_build = stage_mod._MiniGameStage.build
_counters = {"update": 0, "slow": 0}

# 逐项开关（逗号分隔）：draws / game_draw / update / hud / metrics
_DISABLE = {p.strip() for p in os.environ.get("PROBE_DISABLE", "").split(",") if p.strip()}
log(f"disable={sorted(_DISABLE)}")
_GAME_MODE = (os.environ.get("PROBE_GAME") or "").strip()
if _GAME_MODE:
    log(f"game mode={_GAME_MODE}")
_PATCH_GLOBALS = [n.strip() for n in
                  (os.environ.get("PROBE_GLOBALS") or "").split(",") if n.strip()]
if _PATCH_GLOBALS:
    log(f"patch globals={_PATCH_GLOBALS}")

_DRAW_METHODS = ("draw_rect", "draw_round_rect", "draw_circle", "draw_line",
                 "draw_polygon", "draw_text", "draw_image")


def _noop(*_a, **_k):
    return None


def _apply_disable(stage):
    """把开关打到**实例**上：游戏持有同一个 api 对象，重新 exec_module 也躲不过。"""
    api = stage._api
    if "draws" in _DISABLE:
        for name in _DRAW_METHODS:
            setattr(api, name, _noop)
    if "hud" in _DISABLE:
        api.hud = _noop
        if "stage_hud" in _DISABLE:
            stage._hud = ""          # setup 期间已写入的 HUD 文本也清掉
    game = stage._game
    if game is None:
        return
    if "game_draw" in _DISABLE and hasattr(game, "_draw"):
        game._draw = _noop
    if "metrics" in _DISABLE and hasattr(game, "_metrics"):
        game._metrics = lambda: (1.0, 0.0, 0.0, 0.0, 0.0)
    if "update" in _DISABLE:
        game.update = _noop


def _make_dummy(base_cls):
    from dungeon.window.minigame import MiniGame

    class _Dummy(MiniGame):
        id, label = "probe_dummy", "探针假游戏"

        def setup(self, cfg):
            log("dummy setup: allocating two big PIL canvases")
            t = time.monotonic()
            self.a = self.api.offscreen(1088, 768, bg=(1, 2, 3, 255))
            self.b = self.api.offscreen(1088, 768)
            for i in range(30000):
                x, y = i % 1040, (i * 7) % 730
                self.a.draw_rect((x, y), (x + 6, y + 6), (255, 0, 0, 255))
            self.b.draw_rect((0, 0), (1088, 768), (9, 9, 9, 200))
            extra_mb = float(os.environ.get("PROBE_ALLOC_MB", "0") or 0)
            if extra_mb:
                log(f"dummy setup: extra {extra_mb}MB of Python objects")
                self.blobs = [bytes(1024 * 1024) for _ in range(int(extra_mb))]
            log(f"dummy setup done in {time.monotonic() - t:.2f}s")

        def update(self, dt):
            self.api.draw_rect((0, 0), (10, 10), (255, 255, 255, 255))

    return _Dummy


def _wrap_game_cls(cls):
    if _GAME_MODE == "dummy":
        return _make_dummy(cls)
    if _GAME_MODE == "nosetup":
        return type(cls.__name__ + "_NoSetup", (cls,),
                    {"setup": lambda self, cfg: None})
    return cls


def _patch_module_globals(cls, names):
    """把游戏模块里的顶层函数换成 noop（模块字典挂在类的函数上，重载也躲不过）。"""
    globs = getattr(getattr(cls, "setup", None), "__globals__", None)
    if not globs:
        log("cannot reach module globals")
        return
    for name in names:
        if name in globs and callable(globs[name]):
            globs[name] = _noop
            log(f"patched module global {name}")
        else:
            log(f"module global {name} not found/skipped")


def _build(self):
    log(f"stage.build begin ({self._game_cls.__name__})")
    if _GAME_MODE:
        self._game_cls = _wrap_game_cls(self._game_cls)
        log(f"game class replaced by {self._game_cls.__name__}")
    if _PATCH_GLOBALS:
        _patch_module_globals(self._game_cls, _PATCH_GLOBALS)
    t = time.monotonic()
    try:
        ok = _orig_build(self)
    except Exception as exc:
        import traceback
        log(f"stage.build raised: {exc!r}")
        log(traceback.format_exc())
        raise
    log(f"stage.build -> {ok} in {time.monotonic() - t:.2f}s")
    _sleep = float(os.environ.get("PROBE_SLEEP", "0") or 0)
    if _sleep:
        log(f"sleeping {_sleep}s after build")
        time.sleep(_sleep)
    if ok and self._game is not None:
        _gu = self._game.update

        def _upd(dt, _gu=_gu):
            _counters["update"] += 1
            t2 = time.monotonic()
            out = _gu(dt)
            d = time.monotonic() - t2
            if d > 0.03:
                _counters["slow"] += 1
                log(f"slow update #{_counters['update']} {d * 1000:.0f}ms")
            return out

        self._game.update = _upd
        _apply_disable(self)
        if "update" in _DISABLE:
            self._game.update = _noop     # update 开关最后生效（覆盖上面的日志包装）
    return ok


stage_mod._MiniGameStage.build = _build

_orig_tick = stage_mod._MiniGameStage.tick


def _tick(self):
    r = _orig_tick(self)
    if _counters["update"] and _counters["update"] % 120 == 0:
        log(f"updates={_counters['update']} frames={_frames[0]} "
            f"hud={self._hud[:20]!r} finished={self._finished}")
    return r


stage_mod._MiniGameStage.tick = _tick

_script = os.path.join(_ROOT, "developer_tools", "minigame_preview.py")
sys.argv = [_script] + sys.argv[1:]
try:
    runpy.run_path(_script, run_name="__main__")
except SystemExit as exc:
    log(f"SystemExit {exc.code}")
log("script returned normally")
