"""独立复现：DPG 窗口 + 真实游戏模块的织物绘制（不建舞台、不跑游戏类）。

用法：python _probe_fabric.py [帧数]
"""
import importlib.util
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dearpygui.dearpygui as dpg  # noqa: E402
from dungeon.window.minigame.stage import _PILCanvas  # noqa: E402

_t0 = time.monotonic()


def log(msg):
    print(f"[fab {time.monotonic() - _t0:6.3f}] {msg}", flush=True)


game_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "data", "packs", "minigames", "escape_giantess", "game.py")
spec = importlib.util.spec_from_file_location("_probe_escape_module", game_path)
game = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = game
spec.loader.exec_module(game)
log(f"game module loaded (W={game.W} H={game.H})")

dpg.create_context()
dpg.create_viewport(title="fabric probe", width=1188, height=868, resizable=True)
with dpg.texture_registry(tag="dungeon_texture_registry"):
    pass
with dpg.window(tag="main_window", width=1188, height=868, no_title_bar=True,
                no_move=True, no_resize=True, no_scrollbar=True, no_background=True):
    pass
dpg.set_primary_window("main_window", True)
dpg.configure_item("main_window", no_scrollbar=True, no_scroll_with_mouse=True,
                   horizontal_scrollbar=False)
dpg.setup_dearpygui()
dpg.show_viewport()

mode = os.environ.get("PROBE_FAB", "full")
log(f"mode={mode}")

canvas = _PILCanvas(game.W, game.H)
terrain = _PILCanvas(game.W, game.H, bg=game._rgb(game.BG_COLOR))

if mode != "none":
    rng = game.mulberry32(123456789)
    game._fabric_generate(rng, 1, "city")
    log(f"fabric props={len(game._S.fabric_props)}")
    if mode in ("full", "norect"):
        if mode == "norect":
            _orig_rect = _PILCanvas.draw_rect
            _PILCanvas.draw_rect = lambda *a, **k: None
        game._fabric_build(canvas)
        if mode == "norect":
            _PILCanvas.draw_rect = _orig_rect
    elif mode == "noline":
        _orig_line = _PILCanvas.draw_line
        _PILCanvas.draw_line = lambda *a, **k: None
        game._fabric_build(canvas)
        _PILCanvas.draw_line = _orig_line
    log("fabric built")

frames = int(sys.argv[1]) if len(sys.argv) > 1 else 90
for i in range(frames):
    dpg.run_callbacks(dpg.get_callback_queue())
    if not dpg.is_dearpygui_running():
        break
    if i % 10 == 0:
        log(f"frame {i}")
    dpg.render_dearpygui_frame()
    time.sleep(1.0 / 60.0)
log("frames done")
dpg.destroy_context()
log("ctx destroyed")
