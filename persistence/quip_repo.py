import json
import os
from typing import Dict, List

from .json_store import load_json_with_backup, write_json_atomic
from .style_addr import (addr_path as _addr_path,
                         delete_address as _delete_addr,
                         load_address as _load_addr,
                         rename_address as _rename_addr,
                         save_address as _save_addr)
from .style_universe import merged_styles as _merged_styles
from .style_universe import style_read_dir as _style_read_dir

DEFAULT_QUIP_STYLE = "Events"


class QuipRepo:
    def __init__(self, data_dir: str = "data", world_state=None):
        self._data_dir = data_dir
        self._world_state = world_state
        self._free_dir = os.path.join(data_dir, "packs", "quips")
        os.makedirs(self._free_dir, exist_ok=True)

    def _owns_pack(self) -> bool:
        return self._world_state is not None and self._world_state.owns("quips")

    @property
    def default_style(self) -> str:
        if self._owns_pack():
            styles = self._world_state.manifest.resources.get("quips") or []
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
        styles = _merged_styles(self._world_state, "quips", self._free_dir)
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
        for key, value in data.items():
            if key == "_meta":
                continue
            quips[key] = {}
            for coord_key, quip_list in value.items():
                i_str, d_str = coord_key.split('_')
                i, d = int(i_str), int(d_str)
                normalized_list = []
                for item in quip_list:
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
                    else:
                        continue
                quips[key][(i, d)] = normalized_list
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
        filepath = self._free_filepath(style_name)
        existing_data = {}
        if os.path.exists(filepath):
            with open(filepath, 'r', encoding='utf-8') as f:
                try:
                    existing_data = json.load(f)
                except Exception:
                    pass
        meta = existing_data.get("_meta", self._default_meta())
        save_data = {}
        for category, matrix in quips_data.items():
            if category == "_meta":
                continue
            save_data[category] = {}
            for coord_key, quip_list in matrix.items():
                if isinstance(coord_key, tuple) and len(coord_key) == 2:
                    str_key = f"{coord_key[0]}_{coord_key[1]}"
                    save_data[category][str_key] = quip_list
                else:
                    save_data[category][coord_key] = quip_list
        save_data["_meta"] = meta
        # 原子写：save 先读旧文件保 _meta 再整体重写，直接截断写入的话
        # 崩溃时 _meta 和正文一起丢
        write_json_atomic(filepath, save_data)

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
        return _addr_path(self._free_dir, style_name)

    def load_style_address(self, style_name: str) -> str:
        """描述风格注册地址文本（世界观 + 风格注册的若干级）。空 = 未注册。"""
        read_dir = _style_read_dir(self._world_state, "quips",
                                   self._free_dir, style_name)
        return _load_addr(read_dir, style_name)

    def save_style_address(self, style_name: str, address_text: str):
        _save_addr(self._free_dir, style_name, address_text)

    def load_style_registers(self, style_names: List[str]) -> Dict[str, str]:
        """{描述风格名: 注册地址文本}。"""
        return {s: self.load_style_address(s) for s in (style_names or [])}

    def load_meta(self, style_name: str) -> dict:
        data = load_json_with_backup(self._filepath(style_name))
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
        read_dir = _style_read_dir(self._world_state, "quips",
                                   self._free_dir, style_name)
        return os.path.join(read_dir, f"{style_name}.json")

    def _free_filepath(self, style_name: str) -> str:
        return os.path.join(self._free_dir, f"{style_name}.json")

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
