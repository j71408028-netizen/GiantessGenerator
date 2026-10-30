"""资源信封：``<容器名>.meta.json`` 的唯一读写实现。

资源文件本身只有内容，作者不关心元数据；信封是**兄弟文件**，与既有的
``<id>.addr.json``（注册地址）同构，因此两者共用一套路径拼接与原子写。

三条硬规则（见 ``.workbuddy/resource-spec-plan.md`` §3.1）：

1. 字段全部可选，缺失由读取侧用默认值补齐，**不写回磁盘**；
2. 未知字段原样保留（读写一轮不丢数据）；
3. 信封不得藏进不可读容器——``opaque`` 类型（如 ``.chal``）必须外加兄弟信封，
   否则服务端连索引都做不了。

``content_hash`` 是「本地有没有改过」的唯一依据：规范化载荷哈希，纯本地计算，
不需要服务端参与。信封与索引之间只通过它耦合。
"""

import datetime
import hashlib
import json
import os
from typing import Any, Dict, Iterable, Optional

from core.resource_spec import spec_of
from persistence.json_store import load_json_with_backup, write_json_atomic

ENVELOPE_SUFFIX = ".meta.json"

#: 信封字段的缺省值。读取侧补齐后返回，不落盘。
ENVELOPE_DEFAULTS: Dict[str, Any] = {
    "schema": "",
    "id": "",
    "author": "",
    "namespace": "",
    "version": "0.0.0",
    "content_hash": "",
    "updated_at": "",
    "source": {},
    "status": "draft",
    "tags": [],
}

#: ``status`` 的合法取值（本地草稿 / 已发布 / 已停用）
STATUS_VALUES = ("draft", "published", "deprecated")

#: ``source.kind`` 的合法取值
SOURCE_KINDS = ("local", "import", "server")


def schema_for(type_id: str) -> str:
    """信封 schema 标识，如 ``gg.landmarks/1``。"""
    return f"gg.{type_id}/1"


def split_schema(schema: str):
    """``gg.landmarks/1`` → ``("landmarks", 1)``；不合法返回 ``(None, None)``。"""
    text = str(schema or "")
    if not text.startswith("gg.") or "/" not in text:
        return None, None
    body, _, version = text[3:].rpartition("/")
    try:
        return body, int(version)
    except ValueError:
        return None, None


def envelope_path(directory: str, resource_id: str) -> str:
    """信封路径：``<类型目录>/<资源 id>.meta.json``。"""
    return os.path.join(directory, f"{resource_id}{ENVELOPE_SUFFIX}")


def default_envelope(type_id: str, resource_id: str) -> dict:
    """一份最小信封：只填 schema 与 id，其余取默认值。

    ``tags`` / ``source`` 每次新建实例，不复用 ``ENVELOPE_DEFAULTS`` 里的可变对象。
    """
    data = dict(ENVELOPE_DEFAULTS)
    data["schema"] = schema_for(type_id)
    data["id"] = resource_id
    data["tags"] = []
    data["source"] = {}
    return data


def normalize_envelope(data: Any, type_id: str, resource_id: str) -> dict:
    """补齐缺省字段、保留未知字段；非对象输入按空信封处理。"""
    result = default_envelope(type_id, resource_id)
    if isinstance(data, dict):
        result.update(data)
    if not isinstance(result.get("tags"), list):
        result["tags"] = []
    if not isinstance(result.get("source"), dict):
        result["source"] = {}
    if not isinstance(result.get("version"), str) or not result["version"]:
        result["version"] = ENVELOPE_DEFAULTS["version"]
    if result.get("status") not in STATUS_VALUES:
        result["status"] = ENVELOPE_DEFAULTS["status"]
    if not result.get("schema"):
        result["schema"] = schema_for(type_id)
    if not result.get("id"):
        result["id"] = resource_id
    return result


def load_envelope(directory: str, type_id: str, resource_id: str) -> dict:
    """读取信封；缺失/损坏时返回缺省信封（不写盘，不抛异常）。"""
    path = envelope_path(directory, resource_id)
    data = load_json_with_backup(path)
    return normalize_envelope(data, type_id, resource_id)


def save_envelope(directory: str, type_id: str, resource_id: str, data: dict) -> str:
    """原子写信封，返回写入路径。缺省字段不补——写什么就是什么。"""
    path = envelope_path(directory, resource_id)
    payload = dict(data) if isinstance(data, dict) else {}
    payload.setdefault("schema", schema_for(type_id))
    payload.setdefault("id", resource_id)
    write_json_atomic(path, payload)
    return path


def has_envelope(directory: str, resource_id: str) -> bool:
    return os.path.isfile(envelope_path(directory, resource_id))


# ---------------------------------------------------------------------------
# 内容哈希
# ---------------------------------------------------------------------------

def _file_digest(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_bytes(data: Any) -> bytes:
    """规范化 JSON：排序键 + 不转义非 ASCII + 紧凑分隔符。"""
    return json.dumps(data, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def content_hash(path: str, container: str) -> str:
    """载荷的规范化哈希，形如 ``sha256:9f2c...``。

    - JSON 容器：按规范化 JSON 计算（重新缩进/换行不算改动）；
    - CSV / opaque：取文件字节；
    - 目录容器：按「相对路径 + 文件哈希」排序串接，忽略 ``__pycache__``。
    """
    if container in ("json_array", "json_object"):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return ""
        return "sha256:" + hashlib.sha256(canonical_json_bytes(data)).hexdigest()
    if container == "json_dir" and os.path.isdir(path):
        return "sha256:" + hashlib.sha256(
            _dir_digest_bytes(path)).hexdigest()
    if os.path.isfile(path):
        return "sha256:" + _file_digest(path)
    return ""


def _dir_digest_bytes(root: str) -> bytes:
    digest = hashlib.sha256()
    for rel, full in _walk_files(root):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_file_digest(full).encode("ascii"))
        digest.update(b"\n")
    return digest.digest()


def _walk_files(root: str) -> Iterable[tuple]:
    """``(相对路径, 绝对路径)`` 排序迭代，跳过缓存目录与信封/备份文件。"""
    collected = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for name in filenames:
            if name.endswith(ENVELOPE_SUFFIX) or name.endswith(".bak"):
                continue
            full = os.path.join(dirpath, name)
            collected.append((os.path.relpath(full, root).replace(os.sep, "/"), full))
    return sorted(collected)


def build_envelope(directory: str, type_id: str, resource_id: str,
                   primary_path: str) -> dict:
    """按磁盘现状生成一份信封（含 ``content_hash`` 与 ``updated_at``）。

    已有信封的 ``author`` / ``namespace`` / ``version`` / ``tags`` 等作者声明
    会被保留，只刷新内容哈希与时间戳。
    """
    spec = spec_of(type_id)
    envelope = load_envelope(directory, type_id, resource_id)
    if spec is not None:
        envelope["content_hash"] = content_hash(primary_path, spec.container)
    envelope["updated_at"] = _now()
    return envelope


def looks_modified(directory: str, type_id: str, resource_id: str,
                   primary_path: str) -> bool:
    """本地载荷是否与信封记录的哈希不一致（无信封视为「改过」）。"""
    if not has_envelope(directory, resource_id):
        return True
    spec = spec_of(type_id)
    if spec is None:
        return True
    recorded = load_envelope(directory, type_id, resource_id).get("content_hash") or ""
    if not recorded:
        return True
    return recorded != content_hash(primary_path, spec.container)


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def envelope_summary(data: dict) -> str:
    """一行诊断用摘要（``id@namespace 版本 状态``）。"""
    envelope = data if isinstance(data, dict) else {}
    parts: list = [str(envelope.get("id") or "")]
    namespace = str(envelope.get("namespace") or "")
    if namespace:
        parts[0] = f"{parts[0]}@{namespace}"
    version = str(envelope.get("version") or "")
    if version:
        parts.append(version)
    status = str(envelope.get("status") or "")
    if status:
        parts.append(status)
    return " ".join(p for p in parts if p)


def read_envelope_file(path: str, type_id: str = "", resource_id: str = "") -> dict:
    """直接读一个信封文件（诊断/迁移脚本用）；缺失返回空 dict。"""
    data = load_json_with_backup(path)
    if not isinstance(data, dict):
        return {}
    if type_id:
        return normalize_envelope(data, type_id, resource_id)
    return dict(data)


def envelope_source(kind: str = "local", origin: str = "", rev: str = "") -> Optional[dict]:
    """``source`` 字段的构造器；``kind`` 不在 ``SOURCE_KINDS`` 时返回 None。"""
    if kind not in SOURCE_KINDS:
        return None
    return {"kind": kind, "origin": origin, "rev": rev}
