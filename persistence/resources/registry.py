"""资源注册表：类型清单的派生出口、路径定位与校验入口。

三件事，都只是「按声明办事」，不含业务判断：

1. **派生映射**——``PACK_RESOURCE_PATHS`` / ``LIST_RESOURCE_TYPES`` /
   ``RESOURCE_SETTINGS_KEYS`` / 打包成员路径，全部由 ``core.resource_spec`` 算出，
   替换原先散在 ``world_pack`` / ``resource_settings_repo`` / ``ui/resources``
   的六份副本；
2. **路径定位**——自由目录、读取目录（世界包接管时）、主文件、信封、注册地址，
   所有资源路径都从这里出，调用方不再自己拼 ``os.path.join``；
3. **清单与校验**——列出某类资源的 id（坏条目跳过并给诊断），
   ``validate_resource()`` 是唯一判定出口，等价于地址系统的 ``relate()``。

``world_state`` 只按鸭子类型使用（``owns()`` / ``pack_path()``），本模块不 import
``persistence.world_pack``：那个模块反过来要 import 这里的派生映射，形成环。
"""

import csv
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.resource_spec import (
    EXTRA_SETTING_KEYS,
    RESOURCE_TYPES,
    packable_type_ids,
    pick_mode,
    spec_of,
    type_labels,
)
from .envelope import (
    ENVELOPE_SUFFIX,
    content_hash,
    envelope_path,
    load_envelope,
    schema_for,
)

DEFAULT_DATA_DIR = "data"

#: 世界包内目录：类型 id 即包内目录名（旧 ``PACK_RESOURCE_PATHS`` 的等价物）。
PACK_RESOURCE_PATHS: Dict[str, str] = {tid: tid for tid in packable_type_ids()}

#: 可在界面上枚举的类型（旧 ``LIST_RESOURCE_TYPES`` 的等价物）。
LIST_RESOURCE_TYPES: Tuple[str, ...] = packable_type_ids()

#: 布尔型资源类型（``resources.<type>: true`` 表示整类型接管）。当前无此形态。
BOOL_RESOURCE_TYPES: Tuple[str, ...] = ()

#: 类型 → 关联的资源设置键；以及全部资源域设置键（含不属于任何类型的键）。
RESOURCE_TYPE_SETTINGS: Dict[str, Tuple[str, ...]] = {
    spec.type_id: spec.settings_keys for spec in RESOURCE_TYPES
}
RESOURCE_SETTINGS_KEYS: Tuple[str, ...] = tuple(dict.fromkeys(
    [key for spec in RESOURCE_TYPES for key in spec.settings_keys]
    + list(EXTRA_SETTING_KEYS)
))

#: 类型 → 界面显示名（世界包创建对话框等处的唯一来源）。
RESOURCE_TYPE_LABELS: Dict[str, str] = type_labels()

#: 界面上多选 / 单选打包的类型（按声明顺序，互补且不重叠）。
MULTI_PICK_TYPES: Tuple[str, ...] = tuple(
    tid for tid in PACK_RESOURCE_PATHS if pick_mode(tid) == "multi")
SINGLE_PICK_TYPES: Tuple[str, ...] = tuple(
    tid for tid in PACK_RESOURCE_PATHS if pick_mode(tid) != "multi")

#: 伴随文件后缀：注册地址（既有约定）与信封（本规范）。
ADDR_SUFFIX = ".addr.json"


# ---------------------------------------------------------------------------
# 路径定位
# ---------------------------------------------------------------------------

def _spec(type_id: str):
    spec = spec_of(type_id)
    if spec is None:
        raise KeyError(f"未知资源类型: {type_id!r}")
    return spec


def free_dir(type_id: str, data_dir: str = DEFAULT_DATA_DIR) -> str:
    """自由目录：``<data>/packs/<type>`` 或 ``<data>/static/<type>``。"""
    spec = _spec(type_id)
    return os.path.join(data_dir, spec.root, spec.type_id)


def pack_dir(type_id: str, world_state) -> Optional[str]:
    """世界包内该类型的目录；包未拥有该类型时返回 None。"""
    if world_state is None or not world_state.owns(type_id):
        return None
    return world_state.pack_path(type_id)


def read_dir(type_id: str, data_dir: str = DEFAULT_DATA_DIR, world_state=None) -> str:
    """读取目录：包接管该类型时用包内目录，否则用自由目录。"""
    return pack_dir(type_id, world_state) or free_dir(type_id, data_dir)


def primary_relpath(type_id: str, resource_id: str) -> str:
    """主文件相对路径（按声明的 ``primary`` 模板展开）。

    调用方传来的 id 可能已带容器扩展名（挑战包列表返回的就是 ``x.chal``），
    这里统一剥掉，避免拼出 ``x.chal.chal``。
    """
    spec = _spec(type_id)
    if spec.container not in ("json_dir", "py_package"):
        _, _, ext = spec.primary.rsplit("/", 1)[-1].partition("{id}")
        if ext and resource_id.endswith(ext):
            resource_id = resource_id[:-len(ext)]
    return spec.primary.replace("{id}", resource_id)


def free_path(type_id: str, resource_id: str, data_dir: str = DEFAULT_DATA_DIR) -> str:
    """自由目录下的主文件路径（写路径恒为可写层）。"""
    return os.path.join(free_dir(type_id, data_dir),
                        primary_relpath(type_id, resource_id).replace("/", os.sep))


def read_path(type_id: str, resource_id: str, data_dir: str = DEFAULT_DATA_DIR,
              world_state=None) -> str:
    """读取用主文件路径；包内有该资源时优先包内，否则自由目录。"""
    package = pack_dir(type_id, world_state)
    relative = primary_relpath(type_id, resource_id).replace("/", os.sep)
    if package:
        candidate = os.path.join(package, relative)
        if os.path.exists(candidate):
            return candidate
    return os.path.join(free_dir(type_id, data_dir), relative)


def addr_path(type_id: str, resource_id: str, data_dir: str = DEFAULT_DATA_DIR) -> str:
    """注册地址伴随文件路径（``<自由目录>/<id>.addr.json``）。"""
    return os.path.join(free_dir(type_id, data_dir), f"{resource_id}{ADDR_SUFFIX}")


def envelope_path_of(type_id: str, resource_id: str,
                     data_dir: str = DEFAULT_DATA_DIR) -> str:
    """信封路径（恒在自由目录，与主文件同目录）。"""
    return envelope_path(free_dir(type_id, data_dir), resource_id)


def resource_id_from_path(type_id: str, path: str) -> str:
    """从主文件路径反推资源 id（目录型容器取目录名）。"""
    spec = _spec(type_id)
    norm = os.path.normpath(path)
    if spec.container in ("json_dir", "py_package"):
        if not os.path.isdir(norm):
            depth = primary_relpath(type_id, "__id__").count("/")
            for _ in range(depth):
                norm = os.path.dirname(norm)
        return os.path.basename(norm)
    base = os.path.basename(norm)
    for suffix in (ADDR_SUFFIX, ENVELOPE_SUFFIX):
        if base.endswith(suffix):
            return base[:-len(suffix)]
    return os.path.splitext(base)[0]


# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------

def _is_container_name(type_id: str, name: str) -> bool:
    """目录项是否是该类型的一份资源（排除伴随文件、备份与缓存）。"""
    spec = _spec(type_id)
    if name.endswith(ADDR_SUFFIX) or name.endswith(ENVELOPE_SUFFIX):
        return False
    if name.endswith(".bak") or name == "__pycache__" or name.startswith("."):
        return False
    if spec.container in ("json_dir", "py_package"):
        return True  # 目录项即资源
    # 单文件容器：后缀取自 primary 模板，新增类型不必改这里
    return name.lower().endswith(os.path.splitext(spec.primary)[1].lower())


def _id_from_entry(type_id: str, name: str) -> str:
    """目录项名 → 资源 id（目录型保留目录名，其余去掉扩展名）。"""
    if _spec(type_id).container in ("json_dir", "py_package"):
        return name
    return os.path.splitext(name)[0]


def list_ids(type_id: str, data_dir: str = DEFAULT_DATA_DIR,
             world_state=None) -> List[str]:
    """某类资源的 id 列表（包内优先，自由目录补足，去重排序）。

    包接管该类型时只列包内（与 ``style_universe`` 的整类型接管语义一致）；
    目录不存在或不可读时返回空列表，不抛异常。
    """
    roots = [read_dir(type_id, data_dir, world_state)]
    free_root = free_dir(type_id, data_dir)
    if roots[0] != free_root:
        roots.append(free_root)
    found = set()
    for root in roots:
        try:
            entries = os.listdir(root)
        except OSError:
            continue
        for name in entries:
            if _is_container_name(type_id, name):
                found.add(_id_from_entry(type_id, name))
    return sorted(found)


# ---------------------------------------------------------------------------
# 世界包成员核对
# ---------------------------------------------------------------------------

def pack_member(type_id: str, resource_id: str) -> str:
    """世界包内该资源的成员路径（正斜杠，相对包根）。"""
    return f"{PACK_RESOURCE_PATHS[type_id]}/{primary_relpath(type_id, resource_id)}"


def missing_members(declared: Dict[str, Any],
                    members: Iterable[str]) -> List[str]:
    """对照清单声明的资源，返回归档中缺失的成员路径列表。

    替代 ``world_pack._missing_members`` 里的按类型分支：成员形状由
    ``ResourceTypeSpec.primary`` 决定，唯一例外是 ``py_package``（包内任意
    ``.py``，兼容改目录前的单文件写法）。
    """
    member_set = set(members)
    missing: List[str] = []
    for type_id, value in (declared or {}).items():
        if type_id not in PACK_RESOURCE_PATHS:
            continue
        spec = spec_of(type_id)
        if spec is None:
            continue
        if isinstance(value, bool):
            if not value:
                continue
            if PACK_RESOURCE_PATHS[type_id] not in member_set:
                missing.append(PACK_RESOURCE_PATHS[type_id])
            continue
        for resource_id in value or []:
            if spec.container == "py_package":
                prefix = f"{PACK_RESOURCE_PATHS[type_id]}/{resource_id}/"
                single = f"{PACK_RESOURCE_PATHS[type_id]}/{resource_id}.py"
                if single not in member_set and not any(
                        item.startswith(prefix) and item.endswith(".py")
                        for item in member_set):
                    missing.append(f"{prefix}*.py")
                continue
            member = pack_member(type_id, resource_id)
            if member not in member_set:
                missing.append(member)
    return missing


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Diagnostic:
    """一条资源诊断。``error`` 表示资源不可用，``warning`` 表示可用但有隐患。"""

    level: str
    code: str
    message: str

    def is_error(self) -> bool:
        return self.level == "error"

    def __str__(self) -> str:
        return f"[{self.level}] {self.code}: {self.message}"


def _error(code: str, message: str) -> Diagnostic:
    return Diagnostic("error", code, message)


def _warning(code: str, message: str) -> Diagnostic:
    return Diagnostic("warning", code, message)


_PER_ELEMENT_LIMIT = 5


def validate_resource(type_id: str, path: str) -> List[Diagnostic]:
    """校验一份资源：id、主文件、字段、信封一致性。唯一的判定出口。

    只报诊断，不改文件；调用方决定哪些是致命错误（``Diagnostic.is_error``）。
    """
    spec = spec_of(type_id)
    if spec is None:
        return [_error("unknown_type", f"未知资源类型 {type_id!r}")]
    if not path:
        return [_error("missing_path", "资源路径为空")]

    diagnostics: List[Diagnostic] = []
    resource_id = resource_id_from_path(type_id, path)
    if not spec.valid_id(resource_id):
        pattern = spec.id_pattern or r"^[a-z0-9][a-z0-9._-]{0,63}$"
        diagnostics.append(_error(
            "bad_id", f"资源 id {resource_id!r} 不合规（应为 {pattern}）"))
    if not os.path.exists(path):
        diagnostics.append(_error("not_found", f"主文件不存在: {path}"))
        return diagnostics

    if spec.container in ("json_array", "json_object"):
        diagnostics.extend(_validate_json(spec, path))
    elif spec.container == "csv":
        diagnostics.extend(_validate_csv(spec, path))
    elif spec.container == "json_dir":
        diagnostics.extend(_validate_dir_manifest(spec, path))
    elif spec.container == "py_package":
        diagnostics.extend(_validate_py_package(spec, path))
    else:  # opaque
        diagnostics.extend(_validate_opaque(spec, path))
    return diagnostics


def _validate_json(spec, path: str) -> List[Diagnostic]:
    diagnostics: List[Diagnostic] = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        return [_error("bad_json", f"JSON 无法解析: {exc}")]

    if spec.container == "json_array":
        if not isinstance(data, list):
            return [_error("bad_shape", "根节点应为数组")]
        for index, item in enumerate(data[:_PER_ELEMENT_LIMIT]):
            if not isinstance(item, dict):
                diagnostics.append(_error("bad_item", f"第 {index} 项不是对象"))
                continue
            diagnostics.extend(_missing_fields(spec, item, prefix=f"第 {index} 项"))
    else:
        if not isinstance(data, dict):
            return [_error("bad_shape", "根节点应为对象")]
        diagnostics.extend(_missing_fields(spec, data, skip_meta=True))
        if spec.id_required:
            declared = str(data.get("id") or "")
            if not declared:
                diagnostics.append(_error("missing_id", "载荷缺少 id 字段"))
            elif declared != resource_id_from_path(spec.type_id, path):
                diagnostics.append(_warning(
                    "id_mismatch",
                    f"载荷 id {declared!r} 与容器名不一致"))
    return diagnostics


def _missing_fields(spec, data: dict, prefix: str = "",
                   skip_meta: bool = False) -> List[Diagnostic]:
    diagnostics: List[Diagnostic] = []
    for field_spec in spec.fields:
        if field_spec.key in ("_meta", "size_category"):
            continue  # 描述风格的矩阵键：由 size_category 覆盖，不是固定字段
        if skip_meta and field_spec.key.startswith("_"):
            continue
        if field_spec.required and field_spec.key not in data:
            label = prefix + (" " if prefix else "")
            diagnostics.append(_error(
                "missing_field", f"{label}缺少必填字段 {field_spec.key!r}"))
    return diagnostics


def _validate_csv(spec, path: str) -> List[Diagnostic]:
    diagnostics: List[Diagnostic] = []
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            header = next(csv.reader(handle), [])
    except OSError as exc:
        return [_error("unreadable", f"CSV 无法读取: {exc}")]
    if not header:
        return [_error("empty_csv", "CSV 没有表头")]
    present = {name.strip() for name in header}
    for key in spec.required_keys():
        if key not in present:
            diagnostics.append(_warning(
                "missing_column", f"表头缺少列 {key!r}"))
    return diagnostics


def _validate_dir_manifest(spec, path: str) -> List[Diagnostic]:
    """目录型容器：主文件在目录内，按 JSON 规则校验主文件。"""
    primary = path
    if not os.path.isfile(primary):
        tail = primary_relpath(spec.type_id, "__id__").split("/", 1)
        primary = os.path.join(path, tail[1] if len(tail) > 1 else "")
    if not os.path.isfile(primary):
        return [_error("not_found", f"目录内缺少主文件: {primary}")]
    return _validate_json(spec, primary)


def _validate_py_package(spec, path: str) -> List[Diagnostic]:
    if os.path.isdir(path):
        has_py = any(name.endswith(".py") for name in os.listdir(path))
        return [] if has_py else [_warning("empty_package", "目录内没有 .py 文件")]
    return [] if path.endswith(".py") else [_error("bad_shape", "行为包应为目录或 .py 文件")]


def _validate_opaque(spec, path: str) -> List[Diagnostic]:
    diagnostics: List[Diagnostic] = []
    try:
        if os.path.getsize(path) <= 0:
            diagnostics.append(_error("empty_file", "空文件"))
    except OSError as exc:
        diagnostics.append(_error("unreadable", f"无法读取: {exc}"))
    # 不可读容器：信封必须在外，否则无法索引（规范 §3.1 第 3 条）
    directory = os.path.dirname(path)
    resource_id = resource_id_from_path(spec.type_id, path)
    if not os.path.isfile(envelope_path(directory, resource_id)):
        diagnostics.append(_warning(
            "missing_envelope", "不可读容器缺少兄弟信封，服务端无法索引"))
    return diagnostics


# ---------------------------------------------------------------------------
# 索引条目
# ---------------------------------------------------------------------------

def catalog_entry(type_id: str, resource_id: str,
                  data_dir: str = DEFAULT_DATA_DIR) -> dict:
    """单个资源在 ``catalog.json`` 里的条目形状（线上形态 = 磁盘形态）。"""
    spec = _spec(type_id)
    directory = free_dir(type_id, data_dir)
    envelope = load_envelope(directory, type_id, resource_id)
    if not envelope.get("content_hash"):
        target = free_path(type_id, resource_id, data_dir)
        if not os.path.exists(target) and spec.container in ("json_dir", "py_package"):
            # 包型容器的主文件模板是通配，按目录算哈希
            target = os.path.join(directory, resource_id)
        if os.path.exists(target):
            envelope["content_hash"] = content_hash(target, spec.container)
    return {
        "id": resource_id,
        "author": envelope.get("author", ""),
        "namespace": envelope.get("namespace", ""),
        "version": envelope.get("version", ""),
        "content_hash": envelope.get("content_hash", ""),
        "status": envelope.get("status", "draft"),
        "tags": list(envelope.get("tags") or []),
        "schema": schema_for(type_id),
    }


def catalog_entries(type_id: str, data_dir: str = DEFAULT_DATA_DIR,
                    world_state=None) -> List[dict]:
    """某类资源的全部索引条目（坏条目跳过，不抛异常）。"""
    entries = []
    for resource_id in list_ids(type_id, data_dir, world_state):
        try:
            entries.append(catalog_entry(type_id, resource_id, data_dir))
        except (OSError, KeyError, ValueError) as exc:
            print(f"[ResourceRegistry] {type_id}/{resource_id} 索引失败: {exc}")
    return entries


def empty_catalog() -> dict:
    """空 ``catalog.json`` 骨架（缓存而非权威，可按需重建）。"""
    return {spec.type_id: [] for spec in RESOURCE_TYPES}


def build_catalog(data_dir: str = DEFAULT_DATA_DIR, world_state=None) -> dict:
    """从磁盘重建全量索引。``rev`` 留空由服务端接入后接管为远端游标。"""
    catalog = {"schema": "gg.catalog/1", "rev": "", "origin": "",
               "entries": empty_catalog()}
    for spec in RESOURCE_TYPES:
        catalog["entries"][spec.type_id] = catalog_entries(
            spec.type_id, data_dir, world_state)
    return catalog
