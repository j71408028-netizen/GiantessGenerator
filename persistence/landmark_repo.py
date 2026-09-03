import json
import os
from dataclasses import asdict

from models import Landmark


DEFAULT_LANDMARK_STYLE = "ChineseMix"
# 风格“注册地址”companion 文件后缀（记录世界观及风格注册的若干级）
ADDR_SUFFIX = ".addr.json"


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
        filepath = self._filepath(style_name)
        if not os.path.exists(filepath):
            return []
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return [Landmark(**item) for item in data]

    def load_merged(self, style_names: list) -> list:
        merged = []
        for style in style_names:
            merged.extend(self.load(style))
        return merged

    def save(self, style_name: str, landmarks: list):
        filepath = self._free_filepath(style_name)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump([asdict(l) for l in landmarks], f, ensure_ascii=False, indent=2)

    # ---------- 风格注册地址（companion 文件） ----------
    def addr_path(self, style_name: str) -> str:
        return os.path.join(self._free_dir, f"{style_name}{ADDR_SUFFIX}")

    def load_style_address(self, style_name: str) -> str:
        """读取风格注册地址文本（世界观 + 该风格注册的若干上级级）。空 = 未注册。"""
        read_dir = self._read_dir()
        path = os.path.join(read_dir, f"{style_name}{ADDR_SUFFIX}")
        if not os.path.exists(path):
            return ""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            return ""
        return data.get("address", "") if isinstance(data, dict) else ""

    def save_style_address(self, style_name: str, address_text: str):
        with open(self.addr_path(style_name), 'w', encoding='utf-8') as f:
            json.dump({"address": address_text or ""}, f, ensure_ascii=False, indent=2)

    def load_style_registers(self, style_names: list) -> dict:
        """{风格名: 注册地址文本}。"""
        return {s: self.load_style_address(s) for s in (style_names or [])}

    def create_style(self, style_name: str, copy_from: str = None):
        if style_name in self.get_styles():
            raise ValueError(f"风格 '{style_name}' 已存在")
        if copy_from:
            source = self._filepath(copy_from)
            if os.path.exists(source):
                with open(source, 'r', encoding='utf-8') as sf:
                    data = json.load(sf)
                with open(self._free_filepath(style_name), 'w', encoding='utf-8') as df:
                    json.dump(data, df, ensure_ascii=False, indent=2)
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
        addr = self.addr_path(style_name)
        if os.path.exists(addr):
            os.remove(addr)

    def rename_style(self, old_name: str, new_name: str):
        if new_name in self.get_styles():
            raise ValueError(f"风格 '{new_name}' 已存在")
        old_path = self._free_filepath(old_name)
        new_path = self._free_filepath(new_name)
        if os.path.exists(old_path):
            os.rename(old_path, new_path)
        old_addr = self.addr_path(old_name)
        if os.path.exists(old_addr):
            os.rename(old_addr, self.addr_path(new_name))

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
