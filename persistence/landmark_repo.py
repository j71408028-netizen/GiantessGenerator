import os
from dataclasses import asdict

from core.models import Landmark
from .json_store import load_json_with_backup, write_json_atomic
from .style_addr import (ADDR_SUFFIX, addr_path as _addr_path,
                         delete_address as _delete_addr,
                         load_address as _load_addr,
                         rename_address as _rename_addr,
                         save_address as _save_addr)


DEFAULT_LANDMARK_STYLE = "ChineseMix"


class LandmarkRepo:
    def __init__(self, data_dir: str = "data", world_state=None):
        self._data_dir = data_dir
        self._world_state = world_state
        self._free_dir = os.path.join(data_dir, "packs", "landmarks")
        os.makedirs(self._free_dir, exist_ok=True)

    def _read_dir(self) -> str:
        if self._world_state is not None and self._world_state.owns("landmarks"):
            return self._world_state.pack_path("landmarks")
        return self._free_dir

    @property
    def default_style(self) -> str:
        if self._world_state is not None and self._world_state.owns("landmarks"):
            styles = self._world_state.manifest.resources.get("landmarks") or []
            if styles:
                return styles[0]
        return DEFAULT_LANDMARK_STYLE

    def get_styles(self) -> list:
        styles = []
        read_dir = self._read_dir()
        if os.path.exists(read_dir):
            for f in os.listdir(read_dir):
                if f.endswith(".json") and not f.endswith(ADDR_SUFFIX):
                    styles.append(f[:-5])
        if not styles and read_dir == self._free_dir:
            self._create_defaults()
            styles = [DEFAULT_LANDMARK_STYLE]
        return sorted(styles)

    def load(self, style_name: str = None) -> list:
        if style_name is None:
            style_name = DEFAULT_LANDMARK_STYLE
        # 主文件损坏时回退 .bak，风格列表不至于整表不可用
        data = load_json_with_backup(self._filepath(style_name))
        if not isinstance(data, list):
            return []
        return [Landmark(**item) for item in data]

    def load_merged(self, style_names: list) -> list:
        merged = []
        for style in style_names:
            merged.extend(self.load(style))
        return merged

    def save(self, style_name: str, landmarks: list):
        write_json_atomic(self._free_filepath(style_name),
                          [asdict(l) for l in landmarks])

    # ---------- 风格注册地址（companion 文件，读写见 persistence.style_addr） ----------
    def addr_path(self, style_name: str) -> str:
        return _addr_path(self._free_dir, style_name)

    def load_style_address(self, style_name: str) -> str:
        """读取风格注册地址文本（世界观 + 该风格注册的若干上级级）。空 = 未注册。"""
        return _load_addr(self._read_dir(), style_name)

    def save_style_address(self, style_name: str, address_text: str):
        _save_addr(self._free_dir, style_name, address_text)

    def load_style_registers(self, style_names: list) -> dict:
        """{风格名: 注册地址文本}。"""
        return {s: self.load_style_address(s) for s in (style_names or [])}

    def create_style(self, style_name: str, copy_from: str = None):
        if style_name in self.get_styles():
            raise ValueError(f"风格 '{style_name}' 已存在")
        if copy_from:
            # 原样拷贝（保留未知字段），源损坏回退 .bak，仍不可用则建空风格
            data = load_json_with_backup(self._filepath(copy_from))
            if isinstance(data, list):
                write_json_atomic(self._free_filepath(style_name), data)
            else:
                self.save(style_name, [])
            src_addr = self.load_style_address(copy_from)
            if src_addr:
                self.save_style_address(style_name, src_addr)
        else:
            self.save(style_name, [])

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

    def _filepath(self, style_name: str) -> str:
        return os.path.join(self._read_dir(), f"{style_name}.json")

    def _free_filepath(self, style_name: str) -> str:
        return os.path.join(self._free_dir, f"{style_name}.json")

    def _create_defaults(self):
        default_landmarks = [
            Landmark("上海中心大厦", 632, "vertical", "unique"),
            Landmark("哈利法塔", 828, "vertical", "unique"),
            Landmark("埃菲尔铁塔", 330, "vertical", "unique"),
            Landmark("风力发电机", 80, "vertical", "common"),
            Landmark("金门大桥", 2737, "horizontal", "unique"),
            Landmark("足球场", 105, "horizontal", "common"),
        ]
        self.save(DEFAULT_LANDMARK_STYLE, default_landmarks)
