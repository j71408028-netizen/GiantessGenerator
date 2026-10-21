"""副本独占设置的离线检查（存储拆分 / 迁移 / 归一化 / 接线）。

用法：``python tests/check_dungeon_settings.py``

副本独占设置（音量总线电平 / 段落字体 / 记忆窗口 / 自动保存回放 / 帧率上限）只有
副本引擎会用，已从共享的 ``settings.json`` 独立到 ``dungeon_settings.json``
（见 ``persistence/dungeon_settings_repo.py`` 与 ``dungeon/settings.py``）。本检查
锁住这条边界：

- 缺省取自副本侧单一真相源，且不再出现在共享设置里；
- 读写往返、合并更新、越界夹取；
- ``settings.json`` 旧值一次性搬家，且搬家幂等（不覆盖副本后来改的现值）；
- ``SettingsRepo.save`` 不再把副本独占键写回共享设置；
- 会话侧接线：窗口读副本设置、宿主传帧率、子进程自建仓库。

退出码：0 = 通过；1 = 存在断言失败。
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dungeon import settings as dungeon_settings  # noqa: E402
from dungeon.audio import buses  # noqa: E402
from persistence.dungeon_settings_repo import DungeonSettingsRepo  # noqa: E402
from persistence.settings_repo import SettingsRepo  # noqa: E402

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_checked = 0


def check(cond, message, detail=None):
    global _checked
    _checked += 1
    if not cond:
        suffix = "" if detail is None else f"  (got {detail!r})"
        print(f"[FAIL] {message}{suffix}")
        raise SystemExit(1)


def _fresh_dir():
    d = tempfile.mkdtemp(prefix="dungeon_settings_")
    os.makedirs(os.path.join(d, "user"), exist_ok=True)
    return d


def _write_shared(data_dir, payload):
    path = os.path.join(data_dir, "user", "settings.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)


def _read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _src(*parts):
    with open(os.path.join(_PROJECT_ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


# ---------------- 1. 缺省来自副本侧单一真相源 ----------------
_defaults = dungeon_settings.defaults()
check("缺省含全部副本独占键",
      set(_defaults) == {"audio_levels", "dungeon_font", "story_recent_count",
                         "auto_save_replay", "fps_max", "dock_side"},
      sorted(_defaults))
check("代管区停靠侧缺省合法、坏值回退缺省",
      dungeon_settings.normalize_dock_side(_defaults["dock_side"])
      == dungeon_settings.DEFAULT_DOCK_SIDE
      and dungeon_settings.normalize_dock_side("上下左右")
      == dungeon_settings.DEFAULT_DOCK_SIDE
      and dungeon_settings.normalize_dock_side("RIGHT")
      == dungeon_settings.DOCK_SIDE_RIGHT,
      _defaults["dock_side"])
check("音量缺省取自总线单一真相源",
      _defaults["audio_levels"] == buses.DEFAULT_BUS_LEVELS,
      _defaults["audio_levels"])
check("每次调用返回新 dict（可变缺省不共享引用）",
      dungeon_settings.defaults()["audio_levels"]
      is not dungeon_settings.defaults()["audio_levels"])

# ---------------- 2. 读写的往返与合并 ----------------
_dir = _fresh_dir()
_repo = DungeonSettingsRepo(data_dir=_dir)
check("全新安装 load() 返回缺省", _repo.load() == _defaults, _repo.load())

_merged = _repo.update({"dungeon_font": "Noto Sans CJK SC", "auto_save_replay": True})
check("update 合并后立即反映新值",
      _merged["dungeon_font"] == "Noto Sans CJK SC"
      and _merged["auto_save_replay"] is True
      and _merged["fps_max"] == _defaults["fps_max"],
      _merged)
check("update 已落盘（重新 load 仍是新值）",
      _repo.load()["dungeon_font"] == "Noto Sans CJK SC")
check("设置文件写在 user/dungeon_settings.json",
      os.path.exists(os.path.join(_dir, "user", "dungeon_settings.json")))

# ---------------- 3. 归一化：越界夹取、坏结构回退 ----------------
_bad = _repo.update({
    "audio_levels": {"master": 9999, "music": -5, "voice": "x", "sfx": 50},
    "story_recent_count": 10 ** 9,
    "fps_max": -100,
})
check("音量越界被夹取、坏值回退缺省",
      _bad["audio_levels"][buses.BUS_MASTER] == 100
      and _bad["audio_levels"][buses.BUS_MUSIC] == 0
      and _bad["audio_levels"][buses.BUS_VOICE] == 100
      and _bad["audio_levels"][buses.BUS_SFX] == 50,
      _bad["audio_levels"])
check("记忆窗口夹取到 1~200", _bad["story_recent_count"] == 200,
      _bad["story_recent_count"])
check("帧率夹取到 20~240", _bad["fps_max"] == 20.0, _bad["fps_max"])
check("代管区停靠侧坏值回退缺省",
      _repo.update({"dock_side": "斜着"})["dock_side"]
      == dungeon_settings.DEFAULT_DOCK_SIDE)
check("代管区停靠侧合法值落盘往返",
      _repo.update({"dock_side": "right"})["dock_side"]
      == dungeon_settings.DOCK_SIDE_RIGHT
      and _repo.load()["dock_side"] == dungeon_settings.DOCK_SIDE_RIGHT)

# ---------------- 4. 旧值迁移（settings.json → dungeon_settings.json） ----------------
_dir = _fresh_dir()
_write_shared(_dir, {"net_proxy": "http://proxy",
                     "audio_levels": {"master": 100, "music": 40,
                                      "voice": 30, "sfx": 100},
                     "dungeon_font": "SimHei",
                     "story_recent_count": 33,
                     "auto_save_replay": True})
_shared = SettingsRepo(data_dir=_dir).load()
_migrated = DungeonSettingsRepo(data_dir=_dir).load()
check("旧键搬进副本设置",
      _migrated["dungeon_font"] == "SimHei"
      and _migrated["story_recent_count"] == 33
      and _migrated["auto_save_replay"] is True
      and _migrated["audio_levels"]["music"] == 40,
      _migrated)
check("共享设置不再返回副本独占键",
      all(k not in _shared for k in dungeon_settings.LEGACY_KEYS),
      sorted(_shared))
check("共享设置保留共用键", _shared.get("net_proxy") == "http://proxy")

# 迁移幂等：settings.json 还残留旧值时（用户没再打开设置页保存），不覆盖现值
DungeonSettingsRepo(data_dir=_dir).update({"story_recent_count": 5})
SettingsRepo(data_dir=_dir).load()
check("再次迁移不覆盖副本已改的现值",
      DungeonSettingsRepo(data_dir=_dir).load()["story_recent_count"] == 5)

# ---------------- 5. save 不把副本独占键写回共享设置 ----------------
_dir = _fresh_dir()
SettingsRepo(data_dir=_dir).save({"net_proxy": "http://p",
                                  "audio_levels": {"master": 1, "music": 2,
                                                   "voice": 3, "sfx": 4},
                                  "story_recent_count": 7})
_raw = _read(os.path.join(_dir, "user", "settings.json"))
check("save 写出的 settings.json 不含副本独占键",
      "audio_levels" not in _raw and "story_recent_count" not in _raw,
      sorted(_raw))
check("save 保留共享键", _raw.get("net_proxy") == "http://p")

# ---------------- 6. 会话侧接线（源码级） ----------------
_base = _src("dungeon", "window", "base.py")
check("窗口从副本设置装总线电平",
      "set_bus_levels" in _base and "levels_from_settings(self.dungeon_settings)" in _base)
check("窗口记忆窗口读副本设置",
      'self.dungeon_settings or {}' in _base and "story_recent_count" in _base)
check("窗口提供窗口内改设置的入口",
      "def set_dungeon_settings" in _base and "dungeon_settings_repo" in _base)
check("窗口视觉设置变更后立即重排组件",
      '"dock_side" in patch' in _base and "_relayout_components" in _base)

_components = _src("dungeon", "window", "components.py")
check("停靠侧经组件服务面发布（组件不读方案参数）",
      "def component_dock_side" in _components)
for _pack in ("text_card.py", "text_gradient.py"):
    check(f"{_pack}：停靠侧取自窗口视觉设置",
          "component_dock_side" in _src("dungeon", "window", "component_pack", _pack))

_ui = _src("dungeon", "window", "ui.py")
check("运行器帧率来自副本设置",
      'fps_max=self.dungeon_settings.get("fps_max"' in _ui)

_runner = _src("dungeon", "window", "runner.py")
check("跑步器帧率缺省取自 dungeon/settings.py",
      "from dungeon.settings import DEFAULT_FPS_MAX" in _runner
      and "def set_fps_max" in _runner)

_child = _src("ui", "hosting", "dungeon_child_host.py")
check("子进程自建副本设置仓库并注入窗口",
      "DungeonSettingsRepo" in _child
      and 'kwargs["dungeon_settings"]' in _child
      and 'kwargs["dungeon_settings_repo"]' in _child)

_settings_page = _src("ui", "settings", "__init__.py")
check("设置页读写副本设置仓库",
      "DungeonSettingsRepo" in _settings_page
      and "self.dungeon_settings_repo.update(" in _settings_page)

# ---------------- 7. 副本窗口内的设置面板（覆盖层） ----------------
from dungeon.window import fonts as window_fonts  # noqa: E402
from dungeon.window import imgui_util as iu_panel  # noqa: E402
from dungeon.window.overlay import OverlayHandler  # noqa: E402


class _PanelWin(OverlayHandler):
    """只提供覆盖层服务面所需的最小替身（不碰 imgui 上下文 / GL）。"""

    def __init__(self):
        self.calls = []

    def _notify(self, text):
        self.calls.append(("notify", text))

    def set_dungeon_settings(self, patch, *, persist=True):
        self.calls.append(("set", dict(patch), persist))
        return {}


_win = _PanelWin()
check("初始没有覆盖层", _win.overlay_open() is None, _win.overlay_open())
_win.toggle_settings()
check("可调出副本设置覆盖层", _win.overlay_open() == "settings", _win.overlay_open())
_win.toggle_settings()
check("再次触发即关闭设置覆盖层", _win.overlay_open() is None, _win.overlay_open())
_win.toggle_overlay("log")
check("覆盖层白名单收下对话记录", _win.overlay_open() == "log", _win.overlay_open())
_win.close_overlay()

_win.toggle_settings()
_win._apply_settings_live({"fps_max": 120.0})
check("滑条式改动先只改内存（不逐帧落盘）",
      _win.calls[-1] == ("set", {"fps_max": 120.0}, False), _win.calls[-1])
check("连续改动被标脏", _win._settings_dirty is True, _win._settings_dirty)
_win.close_overlay()
check("关闭面板把攒下的改动落盘一次",
      ("set", {}, True) in _win.calls and _win.overlay_open() is None, _win.calls)
check("落盘后不再标脏", _win._settings_dirty is False, _win._settings_dirty)

check("工具条图标含设置齿轮", "settings" in iu_panel.ICON_SHAPES)
_gear = iu_panel.icon_rgba(iu_panel.ICON_SHAPES["settings"], 16)
check("齿轮图标有可见像素", int(_gear[:, :, 3].max()) > 0)
check("设置面板可循环挑选段落字体",
      bool(window_fonts.font_family_choices()))

_overlay_src = _src("dungeon", "window", "overlay.py")
check("覆盖层提供设置面板的根层/顶层绘制入口",
      "def _draw_overlay_root" in _overlay_src
      and "def _draw_overlay_top" in _overlay_src
      and "def _draw_settings_panel" in _overlay_src)
check("设置面板有组件代管区停靠侧开关（玩家实时决定）",
      '"dock_side"' in _overlay_src
      and "normalize_dock_side" in _overlay_src)

_pack_toolbar = _src("dungeon", "window", "component_pack", "base.py")
check("文本组件工具条也挂了设置入口",
      '"toggle_settings"' in _pack_toolbar)

print(f"PASSED ({_checked} assertions)")
sys.exit(0)
