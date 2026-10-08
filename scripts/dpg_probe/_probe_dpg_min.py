"""最小 DPG 渲染探针：N 帧后正常退出。"""
import faulthandler
import sys
import time

faulthandler.enable()

import dearpygui.dearpygui as dpg

dpg.create_context()
dpg.create_viewport(title="probe", width=800, height=600)
with dpg.texture_registry(tag="dungeon_texture_registry"):
    pass
with dpg.window(tag="main_window", width=800, height=600, no_title_bar=True,
                no_move=True, no_resize=True, no_scrollbar=True,
                no_background=True):
    pass
dpg.set_primary_window("main_window", True)
dpg.setup_dearpygui()
dpg.show_viewport()

for i in range(int(sys.argv[1]) if len(sys.argv) > 1 else 120):
    if not dpg.is_dearpygui_running():
        break
    dpg.render_dearpygui_frame()
    time.sleep(1.0 / 60.0)
print("FRAMES-OK")
dpg.destroy_context()
print("DONE")
