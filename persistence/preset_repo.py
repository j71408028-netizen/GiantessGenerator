import csv
import os
from dataclasses import asdict
from typing import List, Optional

from models import BodyPreset
from paths import presets_dir

DEFAULT_PRESET_TABLE = "default"

# 与 BodyPreset 字段一一对应；除 name/enabled 外均为 0~2 的比例值
PRESET_COLUMNS = [
    "name",
    "height_ratio", "leg_ratio", "foot_length_ratio", "arm_span_ratio",
    "index_finger_ratio", "palm_length_ratio", "chest_width_ratio",
    "thigh_diameter_ratio", "forearm_diameter_ratio",
    "index_finger_diameter_ratio", "fingerprint_width_ratio", "finger_gap_ratio",
    "knee_height_ratio", "ankle_height_ratio", "stride_ratio",
]


class PresetRepo:
    """静态身材表仓库：管理 <数据目录>/static/presets/ 下的 CSV 表，无内部管理器。"""

    def __init__(self, dir_path: Optional[str] = None,
                 table: str = DEFAULT_PRESET_TABLE, world_state=None):
        self._world_state = world_state
        self._free_dir = dir_path or presets_dir()
        self._table = table or DEFAULT_PRESET_TABLE
        self._cache: Optional[List[BodyPreset]] = None

    def _read_dir(self) -> str:
        if self._world_state is not None and self._world_state.owns("presets"):
            return self._world_state.pack_path("presets")
        return self._free_dir

    def get_tables(self) -> List[str]:
        """返回可用身材表名（文件名不含 .csv 后缀）。"""
        read_dir = self._read_dir()
        if not os.path.isdir(read_dir):
            return []
        return sorted(
            os.path.splitext(filename)[0]
            for filename in os.listdir(read_dir)
            if filename.lower().endswith(".csv")
        )

    def resolve(self, table_name: str) -> str:
        """返回指定身材表 CSV 的完整路径。"""
        name = table_name or DEFAULT_PRESET_TABLE
        if not name.lower().endswith(".csv"):
            name += ".csv"
        return os.path.join(self._read_dir(), name)

    def set_table(self, table_name: str):
        self._table = table_name or DEFAULT_PRESET_TABLE
        self._cache = None

    def load(self, table: Optional[str] = None) -> List[BodyPreset]:
        """加载身材表，返回 BodyPreset 列表；坏行跳过并打印警告。

        table 为 None 时使用当前激活表（含缓存），显式传入表名时直读不缓存。
        """
        if table is None:
            if self._cache is not None:
                return self._cache
            table = self._table
            cache = True
        else:
            cache = False
        path = self.resolve(table)
        items: List[BodyPreset] = []
        if not os.path.isfile(path):
            self._cache = items if cache else self._cache
            return items
        try:
            with open(path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for line_no, raw in enumerate(reader, start=2):
                    item = self._row_to_preset(raw)
                    if item is not None:
                        items.append(item)
                    else:
                        print(f"身材表 {table} 第 {line_no} 行被跳过: 数据无效")
        except Exception as e:
            print(f"加载身材表 {table} 失败: {e}")
        if cache:
            self._cache = items
        return items

    # ---------- 行解析 / 序列化 ----------

    @staticmethod
    def _clamp_ratio(value: float, default: float) -> float:
        return default if not (0 < value < 2) else value

    @classmethod
    def _row_to_preset(cls, row) -> Optional[BodyPreset]:
        name = (row.get("name") or "").strip()
        if not name:
            return None
        values = {}
        for column in PRESET_COLUMNS:
            if column == "name":
                continue
            raw = (row.get(column) or "").strip()
            if raw == "":
                return None
            try:
                values[column] = float(raw)
            except ValueError:
                return None
        defaults = asdict(BodyPreset(name=name))
        for column, value in values.items():
            defaults[column] = cls._clamp_ratio(value, defaults.get(column, 0.1))
        return BodyPreset(**defaults)

    @staticmethod
    def _to_row(item: BodyPreset) -> dict:
        data = asdict(item)
        return {column: data[column] for column in PRESET_COLUMNS}

    @classmethod
    def write_csv(cls, path: str, items: List[BodyPreset]) -> None:
        """把身材列表写为 CSV 表（utf-8-sig，便于 Excel 直接编辑）。"""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=PRESET_COLUMNS)
            writer.writeheader()
            for item in items:
                writer.writerow(cls._to_row(item))
