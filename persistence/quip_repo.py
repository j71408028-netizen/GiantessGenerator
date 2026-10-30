import json
import os
from typing import Dict, List, Optional, Tuple

from .json_store import load_json_with_backup, write_json_atomic
from .resources.registry import addr_path as _reg_addr_path
from .resources.registry import free_dir as _free_dir_of
from .resources.registry import free_path as _free_path_of
from .resources.registry import primary_relpath as _primary_relpath
from .style_addr import (delete_address as _delete_addr,
                         load_address as _load_addr,
                         rename_address as _rename_addr,
                         save_address as _save_addr)
from .style_universe import merged_styles as _merged_styles
from .style_universe import style_read_dir as _style_read_dir

DEFAULT_QUIP_STYLE = "events"
_TYPE = "quips"


def _coord_of(row) -> Optional[Tuple[int, int]]:
    """坐标行 → ``(介入度, 破坏性)``；形状不对返回 None。"""
    if not isinstance(row, dict):
        return None
    try:
        return int(row["intrusion"]), int(row["destruction"])
    except (KeyError, TypeError, ValueError):
        return None


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
        styles = _merged_styles(self._world_state, _TYPE, self._free_dir)
        if not styles and not self._owns_pack():
            self._create_defaults()
            styles = [DEFAULT_QUIP_STYLE]
        return styles

    def load(self, style_name: str = None) -> dict:
        if style_name is None:
            style_name = DEFAULT_QUIP_STYLE
        # 主文件损坏时回退 .bak，风格列表不至于整表不可用
        data = load_json_with_backup(self._filepath(style_name))
        if not isinstance(data, dict):
            return {}
        quips = {}
        for size_category, rows in data.items():
            if size_category == "_meta" or not isinstance(rows, list):
                continue
            matrix = {}
            for row in rows:
                coord = _coord_of(row)
                if coord is None:
                    continue
                i, d = coord
                normalized_list = []
                for item in row.get("items") or []:
                    if isinstance(item, str):
                        normalized_list.append({
                            "text": item,
                            "style": style_name,
                            "step": self.default_step(i, d)
                        })
                    elif isinstance(item, dict):
                        if "step" not in item:
                            item["step"] = self.default_step(i, d)
                        normalized_list.append(item)
                matrix[coord] = normalized_list
            quips[size_category] = matrix
        return quips

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
        # 原子写：save 先读旧文件保 _meta 再整体重写，直接截断写入的话
        # 崩溃时 _meta 和正文一起丢。meta 取**写路径**上的旧值。
        write_json_atomic(
            self._free_filepath(style_name),
            self.dump_data(quips_data,
                           self._meta_at(self._free_filepath(style_name))))

    def dump_style(self, style_name: str) -> dict:
        """当前风格的落盘形状（``_meta`` + 行数组），供世界包导出等就地序列化。"""
        return self.dump_data(self.load(style_name), self.load_meta(style_name))

    @staticmethod
    def dump_data(quips_data: dict, meta: dict) -> dict:
        """``{(尺寸档, (介入度, 破坏性)): [条目]}`` → 落盘用的行数组。"""
        save_data = {}
        for category, matrix in quips_data.items():
            if category == "_meta":
                continue
            rows = []
            for coord, quip_list in matrix.items():
                pair = _coord_of(coord) if isinstance(coord, dict) else (
                    tuple(coord) if isinstance(coord, tuple) and len(coord) == 2
                    else None)
                if pair is None:
                    continue
                rows.append({"intrusion": int(pair[0]),
                             "destruction": int(pair[1]),
                             "items": list(quip_list)})
            # 坐标升序：落盘顺序稳定，diff 不会因内存顺序变化而抖动
            rows.sort(key=lambda row: (row["intrusion"], row["destruction"]))
            save_data[category] = rows
        save_data["_meta"] = meta
        return save_data

    def create_style(self, style_name: str, copy_from: str = None):
        if style_name in self.get_styles():
            raise ValueError(f"风格 '{style_name}' 已存在")
        if copy_from:
            source_data = self.load(copy_from)
            self.save(style_name, source_data)
            src_addr = self.load_style_address(copy_from)
            if src_addr:
                self.save_style_address(style_name, src_addr)
        else:
            self.save(style_name, {})

    def delete_style(self, style_name: str):
        filepath = self._free_filepath(style_name)
        if os.path.exists(filepath):
            os.remove(filepath)
        _delete_addr(self._free_dir, style_name)

    def rename_style(self, old_name: str, new_name: str):
        if new_name in self.get_styles():
            raise ValueError(f"风格 '{new_name}' 已存在")
        old_path = self._free_filepath(old_name)
        new_path = self._free_filepath(new_name)
        if os.path.exists(old_path):
            os.rename(old_path, new_path)
        _rename_addr(self._free_dir, old_name, new_name)

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
        return self._meta_at(self._filepath(style_name))

    def _meta_at(self, path: str) -> dict:
        data = load_json_with_backup(path)
        if not isinstance(data, dict):
            return self._default_meta()
        return data.get("_meta", self._default_meta())

    def save_meta(self, style_name: str, meta: dict):
        filepath = self._free_filepath(style_name)
        data = load_json_with_backup(filepath)
        if not isinstance(data, dict):
            data = {}
        data["_meta"] = meta
        write_json_atomic(filepath, data)

    def _filepath(self, style_name: str) -> str:
        read_dir = _style_read_dir(self._world_state, _TYPE,
                                   self._free_dir, style_name)
        return os.path.join(read_dir, _primary_relpath(_TYPE, style_name))

    def _free_filepath(self, style_name: str) -> str:
        return _free_path_of(_TYPE, style_name, self._data_dir)

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
                for size_cat, matrix in data.items():
                    new_quips[size_cat] = {}
                    for key, quip_list in matrix.items():
                        try:
                            i, d = map(int, key.split('_'))
                            new_quips[size_cat][(i, d)] = quip_list
                        except Exception:
                            continue
                self.save(DEFAULT_QUIP_STYLE, new_quips)
                os.rename(old_file, old_file + ".bak")
        else:
            default_categories = ["small", "medium", "large", "huge", "colossal"]
            defaults = {cat: {} for cat in default_categories}
            self.save(DEFAULT_QUIP_STYLE, defaults)
