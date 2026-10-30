"""世界包数据结构：清单模型、目录布局与激活状态。

世界包为 zip 归档，扩展名固定为 ``.world.zip``，内部布局：

    world.json                  # 清单（唯一必需文件）
    landmarks/<style>           # 地标风格（主文件不带后缀，路径即地址）
    landmarks/<style>.addr.json # （可选）地标风格注册地址（维度 + 风格注册的若干级）
    quips/<style>               # 描述风格
    quips/<style>.addr.json     # （可选）描述风格注册地址
    presets/<table>.csv         # 身材表
    personalities/<table>.csv   # 性格表
    scenarios/<id>/config.json   # 副本方案
    challenges/<name>.chal      # 附带挑战包（keys.json 记录附带包秘钥）
    names/<table>.csv           # 姓名表
    news/<table>.csv            # 新闻表
    behaviors/<pack>/*.py       # 行为包目录（覆盖 creation/state 服务静态方法）
    topics/<table>              # 话题表（整表替换，主文件不带后缀）
    minigames/<id>/manifest.json# 小游戏数据包目录
    <类型>/<id>.meta.json       # 每份资源随行的兄弟信封

包内 settings 只允许“世界块”设置键：世界设定、种子、姓名表、新闻表、身材表、性格表。
"""

import datetime
import json
import os
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from core.address_model import valid_world_id
from core.resource_spec import RESOURCE_ID_RE, RESOURCE_TYPES, spec_of
from .json_store import write_json_atomic
# 类型清单全部来自注册表；这三个名字在这里保留，供旧的导入路径继续可用。
from .resources.registry import (  # noqa: F401
    BOOL_RESOURCE_TYPES,
    LIST_RESOURCE_TYPES,
    PACK_RESOURCE_PATHS,
    missing_members,
)

WORLD_PACK_EXT = ".world.zip"
WORLD_MANIFEST_NAME = "world.json"
WORLD_FORMAT_VERSION = 1

#: 设置键 → 资源类型：能作为「当前生效表」的静态表，由声明里的 settings_keys 派生。
TABLE_SETTING_KEYS = {
    spec.settings_keys[0]: spec.type_id
    for spec in RESOURCE_TYPES
    if spec.container == "csv" and spec.settings_keys
}
WORLD_SETTING_KEYS = ("world_setting", "seed", *TABLE_SETTING_KEYS)
WORLD_SETTING_VALUES = {"appear", "abs_giant", "rel_giant"}

_WORLD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def worlds_dir(data_dir: str = "data") -> str:
    """返回世界包根目录（默认为 <数据目录>/worlds）。"""
    return os.path.join(data_dir, "worlds")


def behaviors_dir(data_dir: str = "data") -> str:
    """返回静态行为包目录（默认为 <数据目录>/static/behaviors）。"""
    return os.path.join(data_dir, "static", "behaviors")


def is_behavior_pack_name(name: str) -> bool:
    """行为包目录名是否合法（按 ``behaviors`` 声明的 id 规则，即 Python 标识符）。"""
    spec = spec_of("behaviors")
    return bool(name) and spec is not None and spec.valid_id(name)


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
    # 行为包 API 契约版本（整数，见 core.behavior_runtime.BEHAVIOR_API_VERSION）。
    # None = 未声明（按 1 处理）；激活时与运行时版本不符只警告不阻断。
    behavior_api: Optional[int] = None
    world_id: str = ""
    name: str = ""
    version: str = "1.0"
    author: str = ""
    description: str = ""
    app_min_version: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.datetime.now().isoformat(timespec="seconds"))
    # 声明包素材所属的地址维度命名空间（core.address_model 维度段）。
    # 空 = 未声明：包整类型接管资源（旧行为）；非空 = 包只接管这些维度，
    # 自由目录中其它维度的同名类型素材保持可用（按维度合并）。
    worlds: List[str] = field(default_factory=list)
    settings: Dict[str, Any] = field(default_factory=dict)
    resources: Dict[str, Any] = field(default_factory=dict)

    # ---------- 序列化 ----------

    def to_dict(self) -> dict:
        data = {
            "format_version": self.format_version,
            "world_id": self.world_id,
            "name": self.name,
            "version": self.version,
            "author": self.author,
            "description": self.description,
            "app_min_version": self.app_min_version,
            "created_at": self.created_at,
            "worlds": list(self.worlds),
            "settings": dict(self.settings),
            "resources": dict(self.resources),
        }
        if self.behavior_api is not None:
            data["behavior_api"] = self.behavior_api
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "WorldPackManifest":
        if not isinstance(data, dict):
            raise ValueError("world.json 根节点必须是对象")
        return cls(
            format_version=data.get("format_version", WORLD_FORMAT_VERSION),
            behavior_api=data.get("behavior_api"),
            world_id=data.get("world_id", ""),
            name=data.get("name", ""),
            version=data.get("version", "1.0"),
            author=data.get("author", ""),
            description=data.get("description", ""),
            app_min_version=data.get("app_min_version", ""),
            created_at=data.get("created_at", ""),
            worlds=data.get("worlds", []),
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
        if self.behavior_api is not None and (
                not isinstance(self.behavior_api, int)
                or isinstance(self.behavior_api, bool)
                or self.behavior_api < 1):
            errors.append(
                f"behavior_api 必须为正整数或省略（实际: {self.behavior_api!r}）")
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
        for key in TABLE_SETTING_KEYS:
            value = self.settings.get(key)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                errors.append(f"{key} 必须为非空字符串")
        if not isinstance(self.worlds, list):
            errors.append(f"worlds 必须为字符串列表（实际: {self.worlds!r}）")
        else:
            seen_worlds = set()
            for w in self.worlds:
                if not isinstance(w, str) or not valid_world_id(w):
                    errors.append(
                        f"worlds 含非法维度名 {w!r}"
                        "（应为字母/数字/下划线，1-48 位，与地址维度段同口径）")
                elif w in seen_worlds:
                    errors.append(f"worlds 重复声明 '{w}'")
                else:
                    seen_worlds.add(w)
        for rtype, value in self.resources.items():
            spec = spec_of(rtype)
            if spec is None or rtype not in PACK_RESOURCE_PATHS:
                errors.append(
                    f"resources 包含未知类型 '{rtype}'"
                    f"（允许: {', '.join(PACK_RESOURCE_PATHS)}）")
                continue
            if rtype in BOOL_RESOURCE_TYPES:
                if not isinstance(value, bool):
                    errors.append(
                        f"resources.{rtype} 必须为布尔值（实际: {value!r}）")
                continue
            if (not isinstance(value, list) or not value
                    or any(not isinstance(x, str) or not x.strip() for x in value)):
                errors.append(
                    f"resources.{rtype} 必须为非空字符串列表（实际: {value!r}）")
                continue
            if spec.max_items and len(value) > spec.max_items:
                errors.append(
                    f"resources.{rtype} 最多 {spec.max_items} 项（实际: {len(value)} 项）")
            # id 规则一律来自声明：存量的大写/中文 id 已由迁移脚本改完，
            # 加载期不再为旧包留宽松分支。
            for item in value:
                if not spec.valid_id(item):
                    errors.append(
                        f"resources.{rtype} 含非法资源 id {item!r}"
                        f"（应为 {spec.id_pattern or RESOURCE_ID_RE.pattern}）")
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
        """返回包锁定的设置键。

        世界设定 / 种子：出现在 settings 中即锁定。
        静态表（姓名 / 新闻 / 身材 / 性格）：仅当包实际配置了对应资源类型时锁定。
        """
        keys = set()
        for key in WORLD_SETTING_KEYS:
            rtype = TABLE_SETTING_KEYS.get(key)
            if rtype is not None:
                if self.owns(rtype):
                    keys.add(key)
            elif key in self.settings:
                keys.add(key)
        return keys


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
    """将清单写入 world.json（原子写：清单一坏，整包的导入/激活/导出全部失败）。"""
    write_json_atomic(path, manifest.to_dict())


def _missing_members(manifest: WorldPackManifest, members: Set[str]) -> List[str]:
    """对照清单声明的资源，返回归档中缺失的成员路径列表。

    成员形状由类型声明决定，实现在 :func:`persistence.resources.missing_members`。
    """
    return missing_members(manifest.resources, members)


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
    - locked_keys()：包锁定的设置键（世界设定/种子以 settings 为准；静态表以 owns 为准）
    - effective_settings()：仅叠加锁定键
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

    def declared_worlds(self) -> Set[str]:
        """包声明的地址维度命名空间；未激活 / 未声明返回空集。

        非空时，拥有着的资源类型（目前只有地标与描述风格有意义）按世界
        观合并自由目录素材；空集保持整类型接管的旧行为。
        """
        if not self._manifest:
            return set()
        worlds = getattr(self._manifest, "worlds", None)
        if not isinstance(worlds, list):
            return set()
        return {w for w in worlds if isinstance(w, str)}

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
        """返回叠加包设置后的设置字典（不修改原字典）。仅叠加锁定键。"""
        if not self._manifest:
            return settings
        result = dict(settings)
        pack_settings = self._manifest.settings
        for key in self.locked_keys():
            if key in pack_settings:
                result[key] = pack_settings[key]
        return result
