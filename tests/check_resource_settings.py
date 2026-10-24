"""资源设置的离线检查（存储拆分 / 迁移 / 锁键保护 / 分流）。

用法：``python tests/check_resource_settings.py``

资源域设置（静态表选择 / 风格选择 / active_world）已从共享的 ``settings.json``
独立到 ``data/user/resource_settings.json``（见 ``persistence/resource_settings_repo.py``），
由资源中心页面即时落盘。本检查锁住这条边界：

- 缺省值归属资源侧，且共享缺省不再含资源键；
- 读写往返、未知键过滤、空值不落盘；
- ``settings.json`` 旧值一次性搬家，且搬家幂等（不覆盖现值）；
- ``SettingsRepo.save`` 把资源键分流到新文件、不写回共享设置；
- 世界包锁定键在新文件上生效（磁盘现值优先），``world_setting``/``seed`` 留守共享文件。

退出码：0 = 通过；1 = 存在断言失败。
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from persistence.resource_settings_repo import (  # noqa: E402
    RESOURCE_SETTINGS_KEYS,
    ResourceSettingsRepo,
)
from persistence.settings_repo import SettingsRepo  # noqa: E402

_checked = 0


def check(cond, message, detail=None):
    global _checked
    _checked += 1
    if not cond:
        suffix = "" if detail is None else f"  (got {detail!r})"
        print(f"[FAIL] {message}{suffix}")
        raise SystemExit(1)


class _FakeWorldState:
    """最小 WorldState 替身：只提供 locked_keys()。"""

    def __init__(self, locked=()):
        self._locked = set(locked)

    def locked_keys(self):
        return set(self._locked)


def _fresh_dir():
    d = tempfile.mkdtemp(prefix="resource_settings_")
    os.makedirs(os.path.join(d, "user"), exist_ok=True)
    return d


def _read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


# ---------------- 1. 缺省归属资源侧 ----------------
_defaults = ResourceSettingsRepo(data_dir=_fresh_dir()).load()
check("缺省含四个带缺省值的资源键",
      {k: _defaults[k] for k in ("name_table", "news_table",
                                 "selected_styles", "selected_quip_styles")}
      == {"name_table": "default", "news_table": "default",
          "selected_styles": ["ChineseMix"], "selected_quip_styles": ["Events"]},
      _defaults)
check("active_world / preset_table / personality_table 无缺省（读取方各自回退）",
      "active_world" not in _defaults
      and "preset_table" not in _defaults
      and "personality_table" not in _defaults,
      sorted(_defaults))
_shared_repo = SettingsRepo(data_dir=_fresh_dir())
check("SettingsRepo._defaults 不含资源键",
      all(k not in _shared_repo._defaults for k in RESOURCE_SETTINGS_KEYS),
      sorted(_shared_repo._defaults))

# ---------------- 2. 读写往返与过滤 ----------------
_dir = _fresh_dir()
_repo = ResourceSettingsRepo(data_dir=_dir)
_repo.save({"name_table": "wuxia", "selected_styles": ["A", "B"],
            "active_world": "pack-1", "bogus_key": 123})
_loaded = _repo.load()
check("save/load 往返且未知键被过滤",
      _loaded["name_table"] == "wuxia"
      and _loaded["selected_styles"] == ["A", "B"]
      and _loaded["active_world"] == "pack-1"
      and "bogus_key" not in _loaded,
      _loaded)

_repo.save({})
check("空值不落盘（不清空已有文件）", _repo.load()["name_table"] == "wuxia")

# ---------------- 3. 旧值迁移（settings.json → resource_settings.json） ----------------
_dir = _fresh_dir()
_legacy = {"name_table": "western", "preset_table": "fantasy",
           "selected_quip_styles": ["Legacy"], "active_world": "old-pack",
           "world_setting": "appear", "theme_mode": "Dark"}
with open(os.path.join(_dir, "user", "settings.json"), "w", encoding="utf-8") as fh:
    json.dump(dict(_legacy), fh)
_shared = SettingsRepo(data_dir=_dir).load()
_migrated = ResourceSettingsRepo(data_dir=_dir).load()
check("旧资源键搬进 resource_settings.json",
      _migrated["name_table"] == "western"
      and _migrated["preset_table"] == "fantasy"
      and _migrated["selected_quip_styles"] == ["Legacy"]
      and _migrated["active_world"] == "old-pack",
      _migrated)
check("load 返回的 dict 仍含资源键（对外形状不变）",
      _shared["name_table"] == "western" and _shared["theme_mode"] == "Dark",
      {k: _shared.get(k) for k in ("name_table", "theme_mode")})
check("迁移补上缺省资源键",
      _migrated["news_table"] == "default"
      and _migrated["selected_styles"] == ["ChineseMix"])

# 迁移幂等：settings.json 残留旧值时不覆盖现值
_repo.save({"name_table": "newer"})
SettingsRepo(data_dir=_dir).load()
check("再次迁移不覆盖资源侧已改的现值",
      ResourceSettingsRepo(data_dir=_dir).load()["name_table"] == "newer")

# ---------------- 4. save 分流：资源键不写回共享设置 ----------------
_dir = _fresh_dir()
_full = dict(_legacy)
_full["name_table"] = "split-test"
SettingsRepo(data_dir=_dir).save(_full)
_raw_shared = _read(os.path.join(_dir, "user", "settings.json"))
check("save 写出的 settings.json 不含资源键",
      all(k not in _raw_shared for k in RESOURCE_SETTINGS_KEYS),
      sorted(_raw_shared))
check("save 保留共享键", _raw_shared.get("world_setting") == "appear"
      and _raw_shared.get("theme_mode") == "Dark")
_raw_resource = _read(os.path.join(_dir, "user", "resource_settings.json"))
check("资源键落到 resource_settings.json",
      _raw_resource.get("name_table") == "split-test"
      and _raw_resource.get("active_world") == "old-pack",
      _raw_resource)

# ---------------- 5. 锁键保护 ----------------
_dir = _fresh_dir()
ResourceSettingsRepo(data_dir=_dir).save({"name_table": "disk-value",
                                          "selected_styles": ["Keep"]})
_locked_shared = SettingsRepo(data_dir=_dir,
                              world_state=_FakeWorldState(
                                  locked=("name_table", "world_setting")))
_locked_shared.save({"name_table": "attempted",
                     "selected_styles": ["Changed"],
                     "world_setting": "attempted"})
check("锁定的资源键保持磁盘现值",
      _read(os.path.join(_dir, "user", "resource_settings.json"))["name_table"]
      == "disk-value")
check("未锁定的资源键正常更新",
      ResourceSettingsRepo(data_dir=_dir).load()["selected_styles"] == ["Changed"])
check("锁定的共享键（world_setting）保持磁盘现值",
      "world_setting" not in _read(os.path.join(_dir, "user", "settings.json"))
      or _read(os.path.join(_dir, "user", "settings.json"))["world_setting"] == "appear")
check("active_world 不在锁定键内、正常落盘",
      "active_world" not in _read(os.path.join(_dir, "user", "resource_settings.json")))

# 磁盘上没有的锁定键：不得写入
_dir = _fresh_dir()
ResourceSettingsRepo(data_dir=_dir).save({"name_table": "x"})
SettingsRepo(data_dir=_dir,
             world_state=_FakeWorldState(locked=("preset_table",))).save(
    {"name_table": "y", "preset_table": "z"})
check("磁盘上没有的锁定键被丢弃",
      "preset_table" not in _read(os.path.join(_dir, "user", "resource_settings.json")),
      _read(os.path.join(_dir, "user", "resource_settings.json")))
check("锁定键不影响其他资源键落盘",
      _read(os.path.join(_dir, "user", "resource_settings.json"))["name_table"] == "y")

# ---------------- 6. 无锁时一切照常 ----------------
_dir = _fresh_dir()
SettingsRepo(data_dir=_dir).save({"name_table": "free",
                                  "active_world": "w1",
                                  "world_setting": "giant"})
check("无 world_state 时资源键自由落盘",
      _read(os.path.join(_dir, "user", "resource_settings.json"))["name_table"] == "free"
      and _read(os.path.join(_dir, "user", "resource_settings.json"))["active_world"] == "w1")

print(f"PASSED ({_checked} assertions)")
sys.exit(0)
