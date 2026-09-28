"""巨大娘生成器 · 桌面挂件版入口（沉浸模式）。

与主入口 ``main.py``（专业模式）并列：两套界面共用同一份 ``data/``、同一套服务与
持久化层，只有界面层不同——这里是 ``ui/mini`` 那套低像素风单窗口挂件。

差别：没有启动屏、没有导航栏与多页面，主窗口直接就是挂件本体。初始化足够轻，
因此不再需要独立的加载进程与分步构建任务。

运行：``python main_mini.py``；打包见 ``build/windows/build_windows.ps1`` 的
``-Mini`` 开关。
"""

import multiprocessing
import tkinter as tk

from paths import ensure_cwd
from ui.common import appearance
from context import ExplorationContext
from persistence import (
    CharacterRepo, LandmarkRepo, PersonalityRepo, PresetRepo,
    QuipRepo, ScenarioRepo, SettingsRepo,
)
from services.world_service import WorldManager
from ui.mini import dpi
from ui.mini.app import MiniApp


# 挂件版的额外默认值：暗色像素主题、常驻置顶、自动用身材预览图当头像
# （挂件不提供立绘上传），以及报告自动归档（界面已没有保存键）。
# 只在用户没显式设过时补齐，不覆盖专业模式下改过的设置。
MINI_DEFAULTS = {
    "theme_mode": "Dark",
    "always_on_top": True,
    "use_preview_image_as_avatar": True,
    "auto_save_report": True,
}


def _build_repos(world_state):
    return {
        "landmark_repo": LandmarkRepo(world_state=world_state),
        "preset_repo": PresetRepo(world_state=world_state),
        "personality_repo": PersonalityRepo(world_state=world_state),
        "quip_repo": QuipRepo(world_state=world_state),
        "scenario_repo": ScenarioRepo(world_state=world_state),
        "character_repo": CharacterRepo(),
    }


def run_mini(boot):
    """启动挂件模式界面。

    ``boot`` 是 ``app_shell.bootstrap()`` 的结果。窗口关闭后返回下一次要切换
    的界面模式，无切换请求则返回 None（由外壳决定重建还是结束进程）。

    窗口创建与整个事件循环都包在 :func:`ui.mini.dpi.virtualized_dpi` 里：独立
    挂件版本就是 DPI 非感知进程，被 CTk 设成感知之后再切过来的必须显式切回线程
    非感知，否则同一份像素尺寸只剩一半大（详见 ``ui.mini.dpi``）。
    """
    # PyInstaller 打包 + multiprocessing 子进程必需。
    multiprocessing.freeze_support()

    # 从命令行、快捷方式或打包后的 exe 启动时都先把工作目录锚定到数据目录父级。
    ensure_cwd()

    settings = boot["settings"]
    settings_repo = boot["settings_repo"]
    world_manager = boot["world_manager"]
    world_state = boot["world_state"]

    for key, value in MINI_DEFAULTS.items():
        if key not in settings:
            settings[key] = value

    # 主题必须在创建任何控件之前确定：纯 tkinter 的配色在控件创建时落地，
    # 首帧之后再翻转会整窗重刷一次，看起来像闪动。
    appearance.set_mode(settings.get("theme_mode", "Light"))

    repos = _build_repos(world_state)
    context = ExplorationContext(
        settings=settings,
        landmark_repo=repos["landmark_repo"],
        quip_repo=repos["quip_repo"],
        preset_repo=repos["preset_repo"],
        personality_repo=repos["personality_repo"],
        character_repo=repos["character_repo"],
        settings_repo=settings_repo,
        scenario_repo=repos["scenario_repo"],
        world_state=world_state,
    )

    # 窗口必须在「线程非感知」的状态下创建：DPI 感知是**建窗口时**定下的，
    # 之后再切线程也改不了已存在的窗口。见 ui.mini.dpi 的说明。
    with dpi.virtualized_dpi():
        root = tk.Tk()
        MiniApp(root, context, world_manager, settings_repo)
        root.mainloop()

    # 窗口销毁后 mainloop 返回：取一次切换请求交给外壳处理。
    from app_shell import take_request
    return take_request()


def main():
    """入口：经应用外壳启动，因此支持运行时切到专业模式。"""
    from app_shell import MODE_MINI, run_app
    run_app(default_mode=MODE_MINI)


if __name__ == "__main__":
    main()
