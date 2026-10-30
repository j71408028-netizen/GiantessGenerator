import csv
import os
from dataclasses import asdict

from core.models import Landmark
from core.resource_spec import spec_of
from .json_store import BACKUP_SUFFIX, write_text_atomic
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


DEFAULT_LANDMARK_STYLE = "chinese_mix"
_TYPE = "landmarks"

#: CSV 列清单取自资源声明（顺序即写出的表头顺序）
COLUMNS = tuple(spec_of(_TYPE).field_keys())


def _format_size(value: float) -> str:
    """尺寸写表：整数不带小数点，其余原样（读回 ``float()`` 不丢精度）。"""
    number = float(value)
    return str(int(number)) if number.is_integer() else str(number)


def row_to_landmark(row: dict):
    """CSV 行 → Landmark；名称或尺寸无效返回 None（坏行跳过）。"""
    name = str(row.get("name") or "").strip()
    if not name:
        return None
    try:
        size = float(str(row.get("size") or "").strip())
    except ValueError:
        return None
    return Landmark(
        name=name,
        size=size,
        dimension=str(row.get("dimension") or "").strip() or "vertical",
        frequency=str(row.get("frequency") or "").strip() or "unique",
        # 空单元格与 None 同义：水平地标在模型里必有类型，空值只出现在垂直条目
        horizontal_type=str(row.get("horizontal_type") or "").strip() or None,
        address=str(row.get("address") or "").strip(),
        address_mode=str(row.get("address_mode") or "").strip(),
    )


def _to_row(item) -> dict:
    data = item if isinstance(item, dict) else asdict(item)
    try:
        size_text = _format_size(data.get("size"))
    except (TypeError, ValueError):
        size_text = ""
    return {
        "name": str(data.get("name") or ""),
        "size": size_text,
        "dimension": str(data.get("dimension") or "vertical"),
        "frequency": str(data.get("frequency") or "unique"),
        "horizontal_type": str(data.get("horizontal_type") or ""),
        "address": str(data.get("address") or ""),
        "address_mode": str(data.get("address_mode") or ""),
    }


def write_csv(path: str, landmarks) -> None:
    """把地标列表写为 CSV（utf-8-sig，便于表格软件直接编辑），原子写并留 .bak。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    buffer = csv.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(COLUMNS))
    writer.writeheader()
    writer.writerows(_to_row(item) for item in landmarks)
    write_text_atomic(path, buffer.getvalue(), encoding="utf-8-sig")


def _read_rows(path: str) -> list:
    """读 CSV 行；主文件缺失或损坏时回退 .bak，都不可用返回空。"""
    for candidate in (path, path + BACKUP_SUFFIX):
        if not os.path.exists(candidate):
            continue
        try:
            with open(candidate, "r", encoding="utf-8-sig", newline="") as handle:
                return list(csv.DictReader(handle))
        except (OSError, csv.Error) as exc:
            print(f"[LandmarkRepo] 读取失败: {candidate} - {exc}")
    return []


class LandmarkRepo:
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
        return DEFAULT_LANDMARK_STYLE

    def get_styles(self) -> list:
        # 世界包声明 worlds 时合并自由目录素材（见 style_universe 模块说明）
        styles = _merged_styles(self._world_state, _TYPE, self._free_dir)
        if not styles and not self._owns_pack():
            self._create_defaults()
            styles = [DEFAULT_LANDMARK_STYLE]
        return styles

    def load(self, style_name: str = None) -> list:
        if style_name is None:
            style_name = DEFAULT_LANDMARK_STYLE
        landmarks = []
        for line_no, row in enumerate(_read_rows(self._filepath(style_name)),
                                      start=2):
            item = row_to_landmark(row)
            if item is not None:
                landmarks.append(item)
            elif any(str(cell or "").strip() for cell in row.values()):
                print(f"[LandmarkRepo] {style_name} 第 {line_no} 行被跳过: 数据无效")
        return landmarks

    def load_merged(self, style_names: list) -> list:
        merged = []
        for style in style_names:
            merged.extend(self.load(style))
        return merged

    def save(self, style_name: str, landmarks: list):
        write_csv(self._free_filepath(style_name), landmarks)

    # ---------- 风格注册地址（companion 文件，读写见 persistence.style_addr） ----------
    def addr_path(self, style_name: str) -> str:
        return _reg_addr_path(_TYPE, style_name, self._data_dir)

    def load_style_address(self, style_name: str) -> str:
        """读取风格注册地址文本（维度 + 风格注册的若干级）。空 = 未注册。"""
        read_dir = _style_read_dir(self._world_state, _TYPE,
                                   self._free_dir, style_name)
        return _load_addr(read_dir, style_name)

    def save_style_address(self, style_name: str, address_text: str):
        _save_addr(self._free_dir, style_name, address_text)

    def load_style_registers(self, style_names: list) -> dict:
        """{风格名: 注册地址文本}。"""
        return {s: self.load_style_address(s) for s in (style_names or [])}

    def create_style(self, style_name: str, copy_from: str = None):
        if style_name in self.get_styles():
            raise ValueError(f"风格 '{style_name}' 已存在")
        if copy_from:
            self.save(style_name, self.load(copy_from))
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
        read_dir = _style_read_dir(self._world_state, _TYPE,
                                   self._free_dir, style_name)
        return os.path.join(read_dir, _primary_relpath(_TYPE, style_name))

    def _free_filepath(self, style_name: str) -> str:
        return _free_path_of(_TYPE, style_name, self._data_dir)

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
