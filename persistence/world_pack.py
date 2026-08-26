"""世界包数据结构：清单模型、目录布局与激活状态。

世界包为 zip 归档，扩展名固定为 ``.world.zip``，内部布局：

    world.json                  # 清单（唯一必需文件）
    landmarks/<style>.json      # 地标风格
    quips/<style>.json          # 描述风格
    presets/<table>.csv         # 身材表
    personalities/<table>.csv   # 性格表
    dungeons/<id>/config.json   # 副本方案
    challenges/<name>.chal      # 附带挑战包（keys.json 记录附带包秘钥）
    names/<table>.csv           # 姓名表
    news/<table>.csv            # 新闻表
    behaviors/<pack>/*.py       # 行为包目录（覆盖 creation/state 服务静态方法）

包内 settings 只允许“世界块”设置键：世界设定、种子、姓名表、新闻表、身材表、性格表。
"""

import datetime
import json
import os
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

WORLD_PACK_EXT = ".world.zip"
WORLD_MANIFEST_NAME = "world.json"
WORLD_FORMAT_VERSION = 1

WORLD_SETTING_KEYS = ("world_setting", "seed", "name_table", "news_table",
                      "preset_table", "personality_table")
WORLD_SETTING_VALUES = {"appear", "abs_giant", "rel_giant"}

PACK_RESOURCE_PATHS = {
    "landmarks": "landmarks",
    "quips": "quips",
    "presets": "presets",
    "personalities": "personalities",
    "dungeons": "dungeons",
    "challenges": "challenges",
    "names": "names",
    "news": "news",
    "behaviors": "behaviors",
}

LIST_RESOURCE_TYPES = ("landmarks", "quips", "dungeons", "challenges",
                       "names", "news", "presets", "personalities",
                       "behaviors")
BOOL_RESOURCE_TYPES = ()

_WORLD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_BEHAVIOR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def worlds_dir(data_dir: str = "data") -> str:
    """返回世界包根目录（默认为 <数据目录>/worlds）。"""
    return os.path.join(data_dir, "worlds")


def behaviors_dir(data_dir: str = "data") -> str:
    """返回静态行为包目录（默认为 <数据目录>/static/behaviors）。"""
    return os.path.join(data_dir, "static", "behaviors")


def is_behavior_pack_name(name: str) -> bool:
    """行为包目录名是否合法（字母开头，仅含字母数字下划线）。"""
    return bool(name) and bool(_BEHAVIOR_NAME_RE.match(name))


def list_behavior_packs(data_dir: str = "data") -> List[str]:
    """列出静态行为包目录下的可用包名（子文件夹名，忽略缓存目录）。"""
    root = behaviors_dir(data_dir)
    if not os.path.isdir(root):
        return []
    names = []
    for name in os.listdir(root):
        if name.startswith(".") or name == "__pycache__":
            continue
        if not is_behavior_pack_name(name):
            continue
        if os.path.isdir(os.path.join(root, name)):
            names.append(name)
    return sorted(names)


def resolve_behavior_source(root: str, name: str) -> Optional[str]:
    """在 root 下解析行为包路径：优先目录，其次兼容旧版单文件 ``<name>.py``。"""
    if not name or not root:
        return None
    directory = os.path.join(root, name)
    if os.path.isdir(directory):
        return directory
    legacy = os.path.join(root, f"{name}.py")
    if os.path.isfile(legacy):
        return legacy
    return None


def installed_dir(data_dir: str, world_id: str) -> str:
    """返回世界包解压后的部署目录 <数据目录>/worlds/<world_id>/。"""
    return os.path.join(worlds_dir(data_dir), world_id)


@dataclass
class WorldPackManifest:
    """世界包清单（world.json）的数据模型。

    settings 仅允许 WORLD_SETTING_KEYS 中的世界块键；
    resources 声明包拥有的资源类型与具体名称。
    """

    format_version: int = WORLD_FORMAT_VERSION
    world_id: str = ""
    name: str = ""
    version: str = "1.0"
    author: str = ""
    description: str = ""
    app_min_version: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.datetime.now().isoformat(timespec="seconds"))
    settings: Dict[str, Any] = field(default_factory=dict)
    resources: Dict[str, Any] = field(default_factory=dict)

    # ---------- 序列化 ----------

    def to_dict(self) -> dict:
        return {
            "format_version": self.format_version,
            "world_id": self.world_id,
            "name": self.name,
            "version": self.version,
            "author": self.author,
            "description": self.description,
            "app_min_version": self.app_min_version,
            "created_at": self.created_at,
            "settings": dict(self.settings),
            "resources": dict(self.resources),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WorldPackManifest":
        if not isinstance(data, dict):
            raise ValueError("world.json 根节点必须是对象")
        return cls(
            format_version=data.get("format_version", WORLD_FORMAT_VERSION),
            world_id=data.get("world_id", ""),
            name=data.get("name", ""),
            version=data.get("version", "1.0"),
            author=data.get("author", ""),
            description=data.get("description", ""),
            app_min_version=data.get("app_min_version", ""),
            created_at=data.get("created_at", ""),
            settings=dict(data.get("settings") or {}),
            resources=dict(data.get("resources") or {}),
        )

    # ---------- 校验 ----------

    def validate(self) -> List[str]:
        """返回校验错误列表；为空表示清单合法。"""
        errors = []
        if not self.world_id or not _WORLD_ID_RE.match(self.world_id):
            errors.append(
                f"world_id 必须为字母数字开头、1-64 位的安全名称（实际: {self.world_id!r}）")
        if not isinstance(self.format_version, int) or not (
                1 <= self.format_version <= WORLD_FORMAT_VERSION):
            errors.append(
                f"不支持的清单版本 {self.format_version!r}（当前支持 {WORLD_FORMAT_VERSION}）")
        if not isinstance(self.name, str) or not self.name.strip():
            errors.append("name 不能为空")
        for key in self.settings:
            if key not in WORLD_SETTING_KEYS:
                errors.append(
                    f"settings 包含未允许的键 '{key}'（仅允许: {', '.join(WORLD_SETTING_KEYS)}）")
        world_setting = self.settings.get("world_setting")
        if world_setting is not None and world_setting not in WORLD_SETTING_VALUES:
            errors.append(
                f"world_setting 取值非法: {world_setting!r}"
                f"（应为 {sorted(WORLD_SETTING_VALUES)} 之一）")
        seed = self.settings.get("seed")
        if seed is not None and not isinstance(seed, int):
            errors.append(f"seed 必须为整数（实际: {seed!r}）")
        for key in ("name_table", "news_table", "preset_table", "personality_table"):
            value = self.settings.get(key)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                errors.append(f"{key} 必须为非空字符串")
        for rtype, value in self.resources.items():
            if rtype not in PACK_RESOURCE_PATHS:
                errors.append(
                    f"resources 包含未知类型 '{rtype}'"
                    f"（允许: {', '.join(PACK_RESOURCE_PATHS)}）")
                continue
            if rtype in BOOL_RESOURCE_TYPES:
                if not isinstance(value, bool):
                    errors.append(
                        f"resources.{rtype} 必须为布尔值（实际: {value!r}）")
            else:
                if (not isinstance(value, list) or not value
                        or any(not isinstance(x, str) or not x.strip() for x in value)):
                    errors.append(
                        f"resources.{rtype} 必须为非空字符串列表（实际: {value!r}）")
                if rtype == "behaviors":
                    if len(value) > 1:
                        errors.append("resources.behaviors 只能选择一个行为包")
                    for item in value:
                        if not _BEHAVIOR_NAME_RE.match(item):
                            errors.append(
                                f"resources.behaviors 含非法行为包名 {item!r}"
                                f"（仅允许字母开头、字母数字下划线组成）")
        return errors

    # ---------- 拥有关系 ----------

    def owns(self, resource_type: str) -> bool:
        """包是否拥有该资源类型（resources 声明非空）。"""
        if resource_type not in PACK_RESOURCE_PATHS:
            return False
        value = self.resources.get(resource_type)
        if isinstance(value, bool):
            return value
        return bool(value)

    def owned_types(self) -> Set[str]:
        """返回包实际拥有的全部资源类型集合。"""
        return {t for t in PACK_RESOURCE_PATHS if self.owns(t)}

    def locked_keys(self) -> Set[str]:
        """返回包锁定的设置键（仅世界块键，由 settings 中实际出现的键决定）。"""
        return set(self.settings.keys()) & set(WORLD_SETTING_KEYS)


def load_manifest_file(path: str) -> WorldPackManifest:
    """读取并校验 world.json；格式或内容错误时抛出 ValueError。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"无法读取世界包清单 {path}: {e}")
    manifest = WorldPackManifest.from_dict(data)
    errors = manifest.validate()
    if errors:
        raise ValueError("世界包清单无效:\n" + "\n".join("  - " + e for e in errors))
    return manifest


def save_manifest_file(path: str, manifest: WorldPackManifest) -> None:
    """将清单写入 world.json（先确保父目录存在）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest.to_dict(), f, ensure_ascii=False, indent=2)


def _missing_members(manifest: WorldPackManifest, members: Set[str]) -> List[str]:
    """对照清单声明的资源，返回归档中缺失的成员路径列表。"""
    missing = []
    for rtype, rel in PACK_RESOURCE_PATHS.items():
        if rtype in BOOL_RESOURCE_TYPES:
            if manifest.owns(rtype) and rel not in members:
                missing.append(rel)
            continue
        for item in manifest.resources.get(rtype, []):
            if rtype == "dungeons":
                member = f"{rel}/{item}/config.json"
            elif rtype == "challenges":
                member = f"{rel}/{item}"
            elif rtype in ("landmarks", "quips"):
                member = f"{rel}/{item}.json"
            elif rtype == "behaviors":
                prefix = f"{rel}/{item}/"
                legacy = f"{rel}/{item}.py"
                if (legacy not in members
                        and not any(m.startswith(prefix) and m.endswith(".py")
                                    for m in members)):
                    missing.append(f"{prefix}*.py")
                continue
            else:
                member = f"{rel}/{item}.csv"
            if member not in members:
                missing.append(member)
    return missing


def validate_archive(archive_path: str) -> WorldPackManifest:
    """打开 .world.zip，读取并校验清单，并核对声明的资源成员都存在。

    返回校验通过的清单；任何问题抛出 ValueError。
    """
    if not os.path.isfile(archive_path):
        raise ValueError(f"世界包文件不存在: {archive_path}")
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            members = set(zf.namelist())
            if WORLD_MANIFEST_NAME not in members:
                raise ValueError("世界包缺少 world.json")
            data = json.loads(zf.read(WORLD_MANIFEST_NAME).decode("utf-8"))
    except (OSError, zipfile.BadZipFile) as e:
        raise ValueError(f"无法打开世界包: {e}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("world.json 不是有效的 JSON")

    manifest = WorldPackManifest.from_dict(data)
    errors = list(manifest.validate())
    missing = _missing_members(manifest, members)
    if missing:
        errors.append("包内缺少声明的资源文件:\n"
                      + "\n".join("  - " + m for m in missing))
    if errors:
        raise ValueError("世界包无效:\n" + "\n".join(errors))
    return manifest


class WorldState:
    """世界包激活状态：决定各 repo 从包目录还是自由目录读取资源。

    - owns(rtype)：包是否拥有该资源类型（决定管理器禁用与路径接管）
    - locked_keys()：包锁定的设置键（仅世界块键）
    - effective_settings()：自由设置与包设置的内存叠加
    - pack_path()/resolve()：包内资源的读取路径
    """

    def __init__(self, data_dir: str = "data",
                 active_manifest: Optional[WorldPackManifest] = None):
        self._data_dir = data_dir
        self._manifest = active_manifest

    # ---------- 状态 ----------

    @property
    def active(self) -> bool:
        return self._manifest is not None

    @property
    def manifest(self) -> Optional[WorldPackManifest]:
        return self._manifest

    @property
    def world_id(self) -> str:
        return self._manifest.world_id if self._manifest else ""

    @property
    def pack_name(self) -> str:
        return self._manifest.name if self._manifest else ""

    def set_active(self, manifest: Optional[WorldPackManifest]) -> None:
        """激活或停用世界包（传入 None 表示停用）。"""
        self._manifest = manifest

    # ---------- 路径 ----------

    def pack_root(self) -> Optional[str]:
        """包部署目录 <数据目录>/worlds/<world_id>/；未激活时返回 None。"""
        if not self._manifest:
            return None
        return installed_dir(self._data_dir, self._manifest.world_id)

    def pack_path(self, resource_type: str) -> Optional[str]:
        """包内该资源类型的路径（目录或文件）；未拥有该类型时返回 None。"""
        if not self.owns(resource_type):
            return None
        return os.path.join(self.pack_root(), PACK_RESOURCE_PATHS[resource_type])

    def resolve(self, resource_type: str, *rel_parts: str) -> Optional[str]:
        """拼接包内资源路径；未拥有该类型时返回 None。"""
        base = self.pack_path(resource_type)
        if base is None:
            return None
        return os.path.join(base, *rel_parts)

    # ---------- 拥有关系 ----------

    def owns(self, resource_type: str) -> bool:
        return bool(self._manifest) and self._manifest.owns(resource_type)

    def owned_types(self) -> Set[str]:
        if not self._manifest:
            return set()
        return self._manifest.owned_types()

    # ---------- 设置 ----------

    def locked_keys(self) -> Set[str]:
        if not self._manifest:
            return set()
        return self._manifest.locked_keys()

    def effective_settings(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        """返回叠加包设置后的设置字典（不修改原字典）。"""
        if not self._manifest:
            return settings
        result = dict(settings)
        for key in WORLD_SETTING_KEYS:
            if key in self._manifest.settings:
                result[key] = self._manifest.settings[key]
        return result
