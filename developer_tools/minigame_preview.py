# -*- coding: utf-8 -*-
"""小游戏调试启动器：不进副本，直接在独立 DPG 窗口里跑一个小游戏。

用法（仓库根目录）::

    python developer_tools/minigame_preview.py                    # 列出可用小游戏
    python developer_tools/minigame_preview.py escape_giantess    # 跑指定小游戏
    python developer_tools/minigame_preview.py reaction --target 2

窗口内：ESC 退出。结算（api.finish）后窗口打印结果并自动关闭。
实现上复用副本的小游戏舞台（``dungeon.window.minigame.stage``），
因此调试所见即副本内所得——帧时钟、输入层、纹理上传全部同一套。
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dearpygui.dearpygui as dpg  # noqa: E402

from dungeon import process_log  # noqa: E402
from dungeon.window.frame import FrameScheduler  # noqa: E402
from dungeon.window.fonts import first_existing, FONT_FALLBACK_PATHS  # noqa: E402
from dungeon.window.minigame import list_mini_games, resolve_mini_game  # noqa: E402
from dungeon.window.minigame.stage import MiniGameStageHandler  # noqa: E402

_FRAME_INTERVAL = 1.0 / 60.0


class DevMiniGameWindow(MiniGameStageHandler):
    """独立 DPG 宿主：只实现舞台依赖的最小窗口面。"""

    def __init__(self):
        self._frame = FrameScheduler()
        self._closing = False
        self._dpi_scale = 1.0
        self._mini_game_stage = None
        self._result = None

    # ---- 舞台依赖的窗口面 ----
    def _notify(self, message):
        print(f"[notify] {message}")

    def request_close(self):
        self._closing = True
        try:
            dpg.stop_dearpygui()
        except Exception:
            pass

    # ---- 生命周期 ----
    def run(self, game_id, config):
        resolved = resolve_mini_game(game_id)
        if resolved is None:
            print(f"找不到小游戏「{game_id}」，可用：")
            for gid, label, backend in list_mini_games():
                print(f"  {gid:20s} {label:10s} ({backend})")
            return 1

        dpg.create_context()
        # DPG 2.3.1 的 Windows wheel 是 ANSI(MBCS) 构建：标题字节会原样进
        # RegisterClassExA/CreateWindowExA，非 ASCII 标题（如「小游戏调试 · …」）
        # 的 UTF-8 字节按系统 ANSI 码页解码后建窗会**静默失败**——没有原生
        # 窗口，图形后端随之不初始化，首帧 render_dearpygui_frame 必现
        # 0xC0000005。因此先以 ASCII 标题建窗，首帧后再改回真名（与会话
        # 窗口同一做法，见 DungeonWindowUI._build_ui / base._fix_windows_title）。
        viewport_title = f"MiniGame Dev - {game_id}"
        real_title = f"小游戏调试 · {resolved.label}"
        viewport_w = 1188
        viewport_h = 868
        dpg.create_viewport(title=viewport_title,
                            width=viewport_w, height=viewport_h, resizable=True)
        with dpg.texture_registry(tag="dungeon_texture_registry"):
            pass
        # 与副本会话同款的主窗口：视口大小、无标题栏、primary 停靠铺满——
        # 舞台的子窗口（视口客户区尺寸）塞进普通浮动窗口会被裁剪到不可见
        with dpg.window(tag="main_window", width=viewport_w, height=viewport_h,
                        no_title_bar=True, no_move=True, no_resize=True,
                        no_scrollbar=True, no_background=True):
            pass
        dpg.set_primary_window("main_window", True)
        # DPG 2.3.1 怪癖：创建时传 no_scrollbar 不生效，创建后再 configure
        dpg.configure_item("main_window", no_scrollbar=True,
                           no_scroll_with_mouse=True, horizontal_scrollbar=False)

        def _on_resize(sender, app_data):
            try:
                dpg.configure_item("main_window",
                                   width=dpg.get_viewport_client_width(),
                                   height=dpg.get_viewport_client_height())
            except Exception:
                pass

        dpg.set_viewport_resize_callback(_on_resize)
        # 中文字体（draw_text / HUD 的 CJK 字形）
        font_path = first_existing(FONT_FALLBACK_PATHS)
        if font_path:
            try:
                with dpg.font_registry():
                    font = dpg.add_font(font_path, 18)
                dpg.bind_font(font)
            except Exception as exc:
                print(f"[font] 字体加载失败（退回默认）: {exc}")
        dpg.setup_dearpygui()
        dpg.show_viewport()
        dpg.maximize_viewport()

        with dpg.handler_registry():
            dpg.add_key_press_handler(key=dpg.mvKey_Escape, callback=self._on_escape)

        if not self._open_mini_game_stage(resolved, config,
                                          on_result=self._on_result):
            print("小游戏初始化失败，详见上方过程日志")
            dpg.destroy_context()
            return 1

        exit_code = 0
        title_fixed = False
        try:
            while not self._closing:
                self._frame.tick()
                dpg.run_callbacks(dpg.get_callback_queue())
                if not dpg.is_dearpygui_running():
                    break
                dpg.render_dearpygui_frame()
                if not title_fixed:
                    title_fixed = True
                    self._fix_viewport_title(viewport_title, real_title)
                time.sleep(_FRAME_INTERVAL)
        except KeyboardInterrupt:
            pass
        finally:
            self._destroy_mini_game_stage()
            self._frame.stop()
            try:
                dpg.destroy_context()
            except Exception:
                pass
        if self._result is not None:
            print(f"结算结果：{self._result}")
        return exit_code

    # ---- 回调 ----
    @staticmethod
    def _fix_viewport_title(temp_title, real_title):
        """原生窗口建好后把标题改回中文（create_viewport 只能安全用 ASCII）。"""
        try:
            import ctypes
            hwnd = ctypes.windll.user32.FindWindowW(None, temp_title)
            if hwnd:
                ctypes.windll.user32.SetWindowTextW(hwnd, real_title)
        except Exception:
            pass

    def _on_escape(self, sender=None, app_data=None):
        if self._mini_game_stage is not None:
            process_log.log("[MiniGame] ESC 中止小游戏（无结果）")
            self._mini_game_stage._abort()
            self._destroy_mini_game_stage()
        self.request_close()

    def _on_result(self, result):
        """游戏结算：记下结果并关窗（结果可能来自帧线程，这里就在帧线程）。"""
        self._result = result if result is not None else {"won": None, "note": "中止"}
        self.request_close()


def main():
    parser = argparse.ArgumentParser(description="小游戏调试启动器（独立 DPG 窗口）")
    parser.add_argument("game", nargs="?", default="",
                        help="小游戏 id（缺省时列出全部可用）")
    parser.add_argument("--target", type=int, default=None,
                        help="覆盖目标关数（action_data.target_level）")
    args = parser.parse_args()

    if not args.game:
        print("可用小游戏：")
        for gid, label, backend in list_mini_games():
            print(f"  {gid:20s} {label:10s} ({backend})")
        print("\n用法：python developer_tools/minigame_preview.py <game_id> [--target N]")
        return 0

    config = {}
    if args.target is not None:
        config["target_level"] = max(1, args.target)
    return DevMiniGameWindow().run(args.game, config)


if __name__ == "__main__":
    sys.exit(main())
