"""描述风格仓库：``quips/<id>/quips.csv`` + ``config.json``。

目录即一份风格，CSV 每行一个条目；同尺寸档、同坐标的多行在读取时合并为
一个条目列表。``config.json`` 存自定义类型与后续匹配配置，未知键原样保留。
注册地址与信封仍是目录的兄弟文件，路径规则由 ``style_addr`` / ``resources``
统一负责。
"""

import csv
import io
import json
import os
import shutil
from typing import Dict, List, Optional, Tuple

from core.resource_spec import spec_of
from .json_store import (BACKUP_SUFFIX, TEMP_SUFFIX, load_json_with_backup,
                         write_json_atomic, write_text_atomic)
from .resources.envelope import ENVELOPE_SUFFIX
from .resources.registry import addr_path as _reg_addr_path
from .resources.registry import envelope_path_of as _envelope_path_of
from .resources.registry import free_dir as _free_dir_of
from .style_addr import (ADDR_SUFFIX, delete_address as _delete_addr,
                         load_address as _load_addr,
                         rename_address as _rename_addr,
                         save_address as _save_addr)
from .style_universe import merged_styles as _merged_styles
from .style_universe import style_read_dir as _style_read_dir

DEFAULT_QUIP_STYLE = "events"
_TYPE = "quips"

QUIPS_CSV = "quips.csv"
CONFIG_JSON = "config.json"
CONFIG_SCHEMA = "gg.quips.config/1"
#: 仍可编辑的配置版本；更高版本留给新程序，旧程序只读不写。
SUPPORTED_CONFIG_SCHEMAS = (CONFIG_SCHEMA, "gg.quips/1")

#: CSV 列顺序与资源声明保持一致。
COLUMNS = tuple(spec_of(_TYPE).field_keys())
_KNOWN_SIZES = ("small", "medium", "large", "huge", "colossal")


def _coord_of(row) -> Optional[Tuple[int, int]]:
    """坐标行 → ``(介入度, 破坏性)``；形状不对返回 None。"""
    if not isinstance(row, dict):
        return None
    try:
        return int(row["intrusion"]), int(row["destruction"])
    except (KeyError, TypeError, ValueError):
        return None


def _as_int(value) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _as_float(value) -> Optional[float]:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _format_number(value) -> str:
    number = float(value)
    return str(int(number)) if number.is_integer() else str(number)


def _safe_default_step(intrusion: int, destruction: int) -> float:
    try:
        return QuipRepo.default_step(intrusion, destruction)
    except (IndexError, TypeError):
        return 0.0


def _ordered_categories(quips_data: dict):
    """已知尺寸档按固定顺序在前，其余键按字母序补在后面。"""
    known = [name for name in _KNOWN_SIZES if name in quips_data]
    extra = sorted(name for name in quips_data
                   if name not in _KNOWN_SIZES and name != "_meta")
    return [(name, quips_data[name]) for name in known + extra]


def _ordered_coords(matrix: dict):
    """``{(介入度, 破坏性): [条目]}`` 按坐标升序。"""
    pairs = []
    for coord, items in matrix.items():
        pair = _coord_of(coord) if isinstance(coord, dict) else (
            tuple(coord) if isinstance(coord, tuple) and len(coord) == 2 else None)
        if pair is None or not isinstance(items, (list, tuple)):
            continue
        pairs.append((int(pair[0]), int(pair[1]), items))
    return sorted(pairs, key=lambda row: (row[0], row[1]))


def read_quips_csv(path: str, style_name: str) -> dict:
    """CSV → ``{尺寸档: {(介入度, 破坏性): [条目]}}``；坏行跳过并提示。"""
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
    except OSError as exc:
        print(f"[QuipRepo] 读取失败: {path} - {exc}")
        return {}

    quips = {}
    for line_no, row in enumerate(rows, start=2):
        category = str(row.get("size_category") or "").strip()
        intrusion = _as_int(row.get("intrusion"))
        destruction = _as_int(row.get("destruction"))
        text = str(row.get("text") or "")
        if not category or intrusion is None or destruction is None or not text:
            print(f"[QuipRepo] {style_name} 第 {line_no} 行被跳过: 坐标或正文无效")
            continue
        item = {
            "text": text,
            "style": str(row.get("style") or "").strip() or style_name,
        }
        step = _as_float(row.get("step"))
        item["step"] = (step if step is not None
                        else _safe_default_step(intrusion, destruction))
        summary = str(row.get("summary") or "").strip()
        if summary:
            item["summary"] = summary
        quips.setdefault(category, {}).setdefault(
            (intrusion, destruction), []).append(item)
    return quips


def csv_rows(quips_data: dict, style_name: str) -> list:
    """内存矩阵 → CSV 行；同坐标多条目按原顺序展开。"""
    rows = []
    for category, matrix in _ordered_categories(quips_data):
        if not isinstance(matrix, dict):
            continue
        for intrusion, destruction, items in _ordered_coords(matrix):
            for item in items or []:
                if isinstance(item, str):
                    item = {"text": item}
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text") or "")
                if not text:
                    continue
                step = item.get("step")
                if step is None:
                    step = _safe_default_step(intrusion, destruction)
                try:
                    step_text = _format_number(step)
                except (TypeError, ValueError):
                    step_text = _format_number(
                        _safe_default_step(intrusion, destruction))
                rows.append({
                    "size_category": category,
                    "intrusion": intrusion,
                    "destruction": destruction,
                    "text": text,
                    "style": str(item.get("style") or style_name),
                    "step": step_text,
                    "summary": str(item.get("summary") or ""),
                })
    return rows


def write_quips_csv(path: str, quips_data: dict, style_name: str) -> None:
    """原子写 CSV；CSV 自带 CRLF，写文件时关闭文本模式的行尾翻译。"""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(COLUMNS))
    writer.writeheader()
    writer.writerows(csv_rows(quips_data, style_name))
    write_text_atomic(path, buffer.getvalue(), encoding="utf-8-sig", newline="")


def merge_config(config: dict, meta: dict) -> dict:
    """把旧 ``_meta`` 或新配置合并进 config，保留未知键。"""
    result = dict(config) if isinstance(config, dict) else {}
    result.setdefault("schema", CONFIG_SCHEMA)
    for key, value in (meta or {}).items():
        if key == "_meta":
            continue
        result[key] = value
    return result


def write_style_dir(directory: str, quips_data: dict, meta: dict,
                    style_name: str) -> None:
    """把一份风格写进指定目录（迁移脚本与世界包导出共用）。"""
    os.makedirs(directory, exist_ok=True)
    write_quips_csv(os.path.join(directory, QUIPS_CSV), quips_data, style_name)
    write_json_atomic(os.path.join(directory, CONFIG_JSON),
                      merge_config({}, meta))


class QuipRepo:
    def __init__(self, data_dir: str = "data", world_state=None):
        self._data_dir = data_dir
        self._world_state = world_state
        self._free_dir = _free_dir_of(_TYPE, data_dir)
        os.makedirs(self._free_dir, exist_ok=True)

    def _owns_pack(self) -> bool:
        return self._world_state is not None and self._world_state.owns(_TYPE)

    @property
    def default_style(self) -> str:
        if self._owns_pack():
            styles = self._world_state.manifest.resources.get(_TYPE) or []
            if styles:
                return styles[0]
        return DEFAULT_QUIP_STYLE

    @staticmethod
    def default_step(intrusion: int, destruction: int) -> float:
        matrix = [
            [0.1, 0.2, 0.3, 0.4],
            [0.3, 0.4, 0.5, 0.6],
            [0.5, 0.6, 0.7, 0.8],
            [0.7, 0.8, 0.9, 1.0]
        ]
        return matrix[destruction - 1][intrusion - 1]

    def get_styles(self) -> list:
        # 世界包声明 worlds 时合并自由目录素材（见 style_universe 模块说明）
        legacy = self._legacy_style_names()
        if legacy:
            raise ValueError(
                "描述风格仍是旧版单文件布局: "
                + "、".join(legacy)
                + "；请运行 python scripts/migrate_resource_ids.py --apply 完成迁移")
        styles = _merged_styles(self._world_state, _TYPE, self._free_dir)
        if not styles and not self._owns_pack():
            self._create_defaults()
            styles = [DEFAULT_QUIP_STYLE]
        return styles

    def load(self, style_name: str = None) -> dict:
        if style_name is None:
            style_name = DEFAULT_QUIP_STYLE
        legacy = self._legacy_paths(style_name)
        if legacy:
            raise ValueError(
                f"描述风格 {style_name!r} 仍是旧版单文件布局: {legacy}；"
                "请运行 python scripts/migrate_resource_ids.py --apply 完成迁移")
        return read_quips_csv(self._csv_path(style_name), style_name)

    def load_merged(self, style_names: List[str]) -> Dict:
        unified = {}
        for style in style_names:
            quips = self.load(style)
            for size_cat, matrix in quips.items():
                if size_cat not in unified:
                    unified[size_cat] = {}
                for (i, d), quip_list in matrix.items():
                    if (i, d) not in unified[size_cat]:
                        unified[size_cat][(i, d)] = []
                    for q in quip_list:
                        if isinstance(q, dict):
                            unified[size_cat][(i, d)].append(q)
                        else:
                            unified[size_cat][(i, d)].append({"text": q, "style": style})
        return unified

    def save(self, style_name: str, quips_data: dict):
        """只写正文 CSV；``config.json`` 由 :meth:`save_meta` 独立维护。"""
        self._guard_config(self._load_config_at(self._free_config_path(style_name)))
        write_quips_csv(self._free_csv_path(style_name), quips_data, style_name)

    def dump_style(self, style_name: str) -> dict:
        """当前风格的旧式落盘形状（``_meta`` + 行数组），供兼容调用方使用。"""
        return self.dump_data(self.load(style_name), self.load_meta(style_name))

    @staticmethod
    def dump_data(quips_data: dict, meta: dict) -> dict:
        """``{(尺寸档, (介入度, 破坏性)): [条目]}`` → 行数组。"""
        save_data = {}
        for category, matrix in _ordered_categories(quips_data):
            rows = []
            for intrusion, destruction, quip_list in _ordered_coords(matrix):
                rows.append({"intrusion": intrusion,
                             "destruction": destruction,
                             "items": list(quip_list)})
            save_data[category] = rows
        save_data["_meta"] = meta
        return save_data

    def create_style(self, style_name: str, copy_from: str = None):
        if style_name in self.get_styles():
            raise ValueError(f"风格 '{style_name}' 已存在")
        target = self._free_style_dir(style_name)
        if os.path.exists(target) or self._free_legacy_paths(style_name):
            raise ValueError(f"风格 '{style_name}' 已存在")
        if copy_from:
            source = self.style_dir(copy_from)
            if not os.path.isdir(source):
                raise ValueError(f"风格 '{copy_from}' 不存在")
            shutil.copytree(source, target,
                            ignore=shutil.ignore_patterns(
                                "__pycache__", "*.pyc", "*.pyo"))
            if not os.path.isfile(os.path.join(target, CONFIG_JSON)):
                self.save_meta(style_name, self._default_meta())
            src_addr = self.load_style_address(copy_from)
            if src_addr:
                self.save_style_address(style_name, src_addr)
        else:
            os.makedirs(target, exist_ok=True)
            self.save(style_name, {})
            self.save_meta(style_name, self._default_meta())

    def delete_style(self, style_name: str):
        target = self._free_style_dir(style_name)
        if os.path.isdir(target):
            shutil.rmtree(target)
        for path in self._free_legacy_paths(style_name):
            os.remove(path)
        _delete_addr(self._free_dir, style_name)
        envelope = self._free_envelope_path(style_name)
        if os.path.exists(envelope):
            os.remove(envelope)

    def rename_style(self, old_name: str, new_name: str):
        if new_name in self.get_styles():
            raise ValueError(f"风格 '{new_name}' 已存在")
        old_path = self._free_style_dir(old_name)
        new_path = self._free_style_dir(new_name)
        if os.path.isdir(old_path):
            os.rename(old_path, new_path)
        elif self._free_legacy_paths(old_name):
            raise ValueError(
                f"描述风格 {old_name!r} 仍是旧版单文件布局；"
                "请运行 python scripts/migrate_resource_ids.py --apply 完成迁移")
        else:
            raise ValueError(f"风格 '{old_name}' 不存在")
        _rename_addr(self._free_dir, old_name, new_name)
        old_envelope = self._free_envelope_path(old_name)
        if os.path.exists(old_envelope):
            os.rename(old_envelope, self._free_envelope_path(new_name))

    # ---------- 风格注册地址（companion 文件，读写见 persistence.style_addr） ----------
    def addr_path(self, style_name: str) -> str:
        return _reg_addr_path(_TYPE, style_name, self._data_dir)

    def load_style_address(self, style_name: str) -> str:
        """描述风格注册地址文本（维度 + 风格注册的若干级）。空 = 未注册。"""
        read_dir = _style_read_dir(self._world_state, _TYPE,
                                   self._free_dir, style_name)
        return _load_addr(read_dir, style_name)

    def save_style_address(self, style_name: str, address_text: str):
        _save_addr(self._free_dir, style_name, address_text)

    def load_style_registers(self, style_names: List[str]) -> Dict[str, str]:
        """{描述风格名: 注册地址文本}。"""
        return {s: self.load_style_address(s) for s in (style_names or [])}

    def load_meta(self, style_name: str) -> dict:
        config = self._load_config_at(self._config_path(style_name))
        meta = {key: value for key, value in config.items() if key != "schema"}
        if "custom_types" not in meta:
            meta["custom_types"] = self._default_meta()["custom_types"]
        return meta

    def save_meta(self, style_name: str, meta: dict):
        path = self._free_config_path(style_name)
        config = self._load_config_at(path)
        self._guard_config(config)
        config = merge_config(config, meta or {})
        if "custom_types" not in config:
            config["custom_types"] = self._default_meta()["custom_types"]
        write_json_atomic(path, config)

    @staticmethod
    def _guard_config(config: dict) -> None:
        schema = config.get("schema") if isinstance(config, dict) else None
        if schema and schema not in SUPPORTED_CONFIG_SCHEMAS:
            raise ValueError(
                f"描述风格配置版本 {schema!r} 高于当前程序支持的 "
                f"{CONFIG_SCHEMA!r}，请升级应用后再编辑")

    def style_dir(self, style_name: str) -> str:
        """读取目录下的风格目录（世界包激活时可能是包内目录）。"""
        return os.path.join(
            _style_read_dir(self._world_state, _TYPE, self._free_dir, style_name),
            style_name)

    def copy_style_to(self, style_name: str, target_dir: str) -> None:
        """整目录复制一份风格（含配置与未知文件），供世界包导出使用。"""
        source = self.style_dir(style_name)
        if not os.path.isdir(source):
            raise ValueError(f"风格 '{style_name}' 不存在: {source}")
        shutil.copytree(source, target_dir,
                        ignore=shutil.ignore_patterns(
                            "__pycache__", "*.pyc", "*.pyo"))

    # ---------- 路径 ----------

    def _csv_path(self, style_name: str) -> str:
        return os.path.join(self.style_dir(style_name), QUIPS_CSV)

    def _config_path(self, style_name: str) -> str:
        return os.path.join(self.style_dir(style_name), CONFIG_JSON)

    def _free_style_dir(self, style_name: str) -> str:
        return os.path.join(self._free_dir, style_name)

    def _free_csv_path(self, style_name: str) -> str:
        return os.path.join(self._free_style_dir(style_name), QUIPS_CSV)

    def _free_config_path(self, style_name: str) -> str:
        return os.path.join(self._free_style_dir(style_name), CONFIG_JSON)

    def _free_envelope_path(self, style_name: str) -> str:
        return _envelope_path_of(_TYPE, style_name, self._data_dir)

    def _legacy_paths(self, style_name: str) -> list:
        """当前读取目录里该风格的旧版单文件路径（无后缀或 ``.json``）。"""
        read_dir = _style_read_dir(self._world_state, _TYPE,
                                   self._free_dir, style_name)
        return self._legacy_paths_in(read_dir, style_name)

    def _free_legacy_paths(self, style_name: str) -> list:
        return self._legacy_paths_in(self._free_dir, style_name)

    @staticmethod
    def _legacy_paths_in(directory: str, style_name: str) -> list:
        paths = []
        for name in (style_name, style_name + ".json"):
            path = os.path.join(directory, name)
            if os.path.isfile(path):
                paths.append(path)
        return paths

    def _legacy_style_names(self) -> list:
        roots = [self._free_dir]
        if self._owns_pack():
            pack = self._world_state.pack_path(_TYPE)
            if pack and pack not in roots:
                roots.append(pack)
        found = set()
        for root in roots:
            try:
                names = os.listdir(root)
            except OSError:
                continue
            for name in names:
                if name.startswith(".") or name == "__pycache__":
                    continue
                if name.endswith(ADDR_SUFFIX) or name.endswith(ENVELOPE_SUFFIX):
                    continue
                if name.endswith(BACKUP_SUFFIX) or name.endswith(TEMP_SUFFIX):
                    continue
                if os.path.isdir(os.path.join(root, name)):
                    continue
                if name.endswith(".json"):
                    found.add(name[:-5])
                elif "." not in name:
                    found.add(name)
        return sorted(found)

    @staticmethod
    def _load_config_at(path: str) -> dict:
        data = load_json_with_backup(path)
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _default_meta() -> dict:
        return {
            "custom_types": {
                "c": {"name": "自定义1", "subtypes": ["细分1", "细分2", "细分3", "细分4"]},
                "d": {"name": "自定义2", "subtypes": ["细分1", "细分2", "细分3", "细分4"]},
                "e": {"name": "自定义3", "subtypes": ["细分1", "细分2", "细分3", "细分4"]}
            }
        }

    def _create_defaults(self):
        old_file = os.path.join(os.path.dirname(os.path.dirname(self._free_dir)), "quips.json")
        if os.path.exists(old_file):
            with open(old_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            new_quips = {}
            meta = self._default_meta()
            for size_cat, matrix in data.items():
                if size_cat == "_meta":
                    meta = matrix if isinstance(matrix, dict) else meta
                    continue
                if not isinstance(matrix, dict):
                    continue
                new_quips[size_cat] = {}
                for key, quip_list in matrix.items():
                    try:
                        i, d = map(int, str(key).split('_'))
                    except ValueError:
                        continue
                    new_quips[size_cat][(i, d)] = quip_list
            self.save(DEFAULT_QUIP_STYLE, new_quips)
            self.save_meta(DEFAULT_QUIP_STYLE, meta)
            os.rename(old_file, old_file + ".bak")
        else:
            default_categories = ["small", "medium", "large", "huge", "colossal"]
            self.save(DEFAULT_QUIP_STYLE, {cat: {} for cat in default_categories})
            self.save_meta(DEFAULT_QUIP_STYLE, self._default_meta())
