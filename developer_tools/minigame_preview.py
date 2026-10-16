# -*- coding: utf-8 -*-
"""小游戏调试启动器：不进副本，直接在独立 imgui 窗口里跑一个小游戏。

用法（仓库根目录）::

    python developer_tools/minigame_preview.py                    # 列出可用小游戏
    python developer_tools/minigame_preview.py escape_giantess    # 跑指定小游戏
    python developer_tools/minigame_preview.py reaction --target 2

窗口内：ESC 退出。结算（api.finish）后窗口打印结果并自动关闭。
实现上复用副本的小游戏舞台（``dungeon.window.minigame.stage``）与会话窗口的宿主
运行器（``dungeon.window.runner.WindowRunner``），因此调试所见即副本内所得——
帧时钟、输入层、纹理上传、字体全部同一套。

（迁移前这里是 Dear PyGui 手写帧循环；2026-10 imgui 迁移后改跑同一个
``WindowRunner``，不再有独立的后端代码。）
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from imgui_bundle import hello_imgui, imgui  # noqa: E402

from dungeon.window.fonts import (  # noqa: E402
    BOLD_FONT_SIZE, TEXT_FONT_SIZE, UI_FONT_SIZE, resolve_font_files)
from dungeon.window.frame import FrameScheduler  # noqa: E402
from dungeon.window.minigame import list_mini_games, resolve_mini_game  # noqa: E402
from dungeon.window.minigame.stage import MiniGameStageHandler  # noqa: E402
from dungeon.window.runner import WindowRunner  # noqa: E402

#: 建窗尺寸（96-PPI 逻辑点，与副本会话同一口径）。
_VIEWPORT_W = 1188
_VIEWPORT_H = 868
_MIN_W = 800
_MIN_H = 500


class DevMiniGameWindow(MiniGameStageHandler):
    """独立 imgui 宿主：只实现舞台依赖的最小窗口面。"""

    def __init__(self, game_id):
        self._game_id = game_id
        self._frame = FrameScheduler()
        self._closing = False
        self._dpi_scale = 1.0
        self._mini_game_stage = None
        self._result = None
        self._layout_w = float(_VIEWPORT_W)
        self._layout_h = float(_VIEWPORT_H)
        # 字体：家族名留空 → fonts.resolve_font_files 走平台兜底（与会话窗口一致）
        self.dungeon_font = ""
        self.dungeon_text_font = None
        self.dungeon_bold_font = None
        self._font_ui = None
        self._runner = WindowRunner(
            title=f"小游戏调试 · {game_id}",
            width=_VIEWPORT_W, height=_VIEWPORT_H,
            min_width=_MIN_W, min_height=_MIN_H,
            dpi_scale=self._dpi_scale,
            on_post_init=self._setup_imgui,
            on_load_fonts=self._load_fonts,
            on_frame_start=self._on_frame_start,
            on_draw=self._on_draw,
            on_before_exit=self._on_before_exit,
        )

    # ---- 舞台依赖的窗口面 ----
    def _notify(self, message):
        print(f"[notify] {message}")

    def request_close(self):
        self._closing = True
        self._runner.request_exit()

    # ---- imgui 回调 ----
    def _load_fonts(self):
        """``load_additional_fonts``：唯一能加载字体的时机（见 runner.ui 文档）。"""
        font_path, bold_path = resolve_font_files(
            getattr(self, "dungeon_font", "") or "")
        if not font_path:
            hello_imgui.imgui_default_settings.load_default_font_with_font_awesome_icons()
            return
        self._font_ui = hello_imgui.load_font(font_path, UI_FONT_SIZE)
        self.dungeon_text_font = hello_imgui.load_font(font_path, TEXT_FONT_SIZE)
        self.dungeon_bold_font = (hello_imgui.load_font(bold_path, BOLD_FONT_SIZE)
                                  if bold_path else None)

    def _setup_imgui(self):
        """``post_init``：GL 后端与上下文就绪、首帧之前。"""
        self._gl_ready = True

    def _on_frame_start(self):
        self._frame.tick()

    def _on_draw(self):
        self._sync_layout_size()
        if self._mini_game_stage is None:
            return
        if imgui.is_key_pressed(imgui.Key.escape):
            if self._minigame_escape():
                self.request_close()
            return
        # 帧末顶层窗口：与副本会话里同一条绘制路径（ui._draw_frame → 舞台）
        self._draw_mini_game_stage(self._layout_w, self._layout_h)

    def _on_before_exit(self):
        """收尾：GL 上下文此刻仍当前（``run()`` 尚未返回）。

        顺序与 :meth:`_MiniGameStage._gl_ops_ok` 的判据一致——先在帧时钟仍跑时
        释放舞台的 GPU 纹理，再停帧时钟。
        """
        self._destroy_mini_game_stage()
        self._frame.stop()

    def _sync_layout_size(self):
        """每帧比对 ``io.display_size``（等价会话窗口里的 resize 回调）。"""
        io = imgui.get_io()
        if io.display_size.x > 0 and io.display_size.y > 0:
            self._layout_w = float(io.display_size.x)
            self._layout_h = float(io.display_size.y)

    # ---- 生命周期 ----
    def run(self, game_id, config):
        resolved = resolve_mini_game(game_id)
        if resolved is None:
            print(f"找不到小游戏「{game_id}」，可用：")
            _print_games()
            return 1
        # 舞台在 run() 之前构建：build() 不碰 GPU / draw list，只用 _layout_*（已备好）。
        if not self._open_mini_game_stage(resolved, config, on_result=self._on_result):
            print("小游戏初始化失败，详见上方过程日志")
            return 1
        try:
            self._runner.run()
        except KeyboardInterrupt:
            pass
        finally:
            # 兜底：before_exit 未必走到（例如异常），保证舞台与帧时钟收尾
            self._destroy_mini_game_stage()
            self._frame.stop()
        if self._result is not None:
            print(f"结算结果：{self._result}")
        return 0

    def _on_result(self, result):
        """游戏结算：记下结果并关窗（立即模式下就在帧线程调用）。"""
        self._result = result if result is not None else {"won": None, "note": "中止"}
        self.request_close()


def _print_games():
    for gid, label, backend in list_mini_games():
        print(f"  {gid:20s} {label:10s} ({backend})")


def main():
    parser = argparse.ArgumentParser(description="小游戏调试启动器（独立 imgui 窗口）")
    parser.add_argument("game", nargs="?", default="",
                        help="小游戏 id（缺省时列出全部可用）")
    parser.add_argument("--target", type=int, default=None,
                        help="覆盖目标关数（action_data.target_level）")
    args = parser.parse_args()

    if not args.game:
        print("可用小游戏：")
        _print_games()
        print("\n用法：python developer_tools/minigame_preview.py <game_id> [--target N]")
        return 0

    config = {}
    if args.target is not None:
        config["target_level"] = max(1, args.target)
    return DevMiniGameWindow(args.game).run(args.game, config)


if __name__ == "__main__":
    sys.exit(main())
