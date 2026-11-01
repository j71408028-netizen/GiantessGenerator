import csv
import os
from dataclasses import asdict
from typing import List, Optional

from core.models import BodyPreset
from core.resource_spec import spec_of
from persistence.material_tables import (
    DEFAULT_MATERIAL_TABLE,
    group_dir,
    list_groups,
    member_path,
)
from persistence.static_table import format_float_parameter, parse_float_parameter

MATERIAL_TYPE = "materials"
MEMBER_KEY = "presets"

# 列清单取自资源声明（顺序即写出的表头顺序）；除 name 外均为 0~2 的比例值
PRESET_COLUMNS = [key for key in spec_of(MATERIAL_TYPE).member(MEMBER_KEY).field_keys()
                  if key != "weight"]
PRESET_COLUMNS_WITH_WEIGHT = ["weight", *PRESET_COLUMNS]


class PresetRepo:
    """身材表读取器：管理取材表组内的 ``presets.csv``，无内部管理器。"""

    def __init__(self, group: Optional[str] = None, world_state=None):
        self._world_state = world_state
        self._group = group or DEFAULT_MATERIAL_TABLE
        self._cache: Optional[List[BodyPreset]] = None

    def _read_dir(self) -> str:
        return group_dir(self._group, world_state=self._world_state)

    def get_groups(self) -> List[str]:
        """返回可用取材表组名。"""
        return list_groups(world_state=self._world_state)

    def resolve(self, group: Optional[str] = None) -> str:
        """返回指定组内身材表的完整路径（缺席时已回落到内置组）。"""
        return member_path(group or self._group, MEMBER_KEY,
                           world_state=self._world_state)

    def set_group(self, group: Optional[str]):
        self._group = group or DEFAULT_MATERIAL_TABLE
        self._cache = None

    def load(self, group: Optional[str] = None) -> List[BodyPreset]:
        """加载身材表，返回 BodyPreset 列表；坏行跳过并打印警告。

        group 为 None 时使用当前激活组（含缓存），显式传入组名时直读不缓存。
        """
        if group is None:
            if self._cache is not None:
                return self._cache
            group = self._group
            cache = True
        else:
            cache = False
        path = self.resolve(group)
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
                        print(f"身材表 {group} 第 {line_no} 行被跳过: 数据无效")
        except Exception as e:
            print(f"加载身材表 {group} 失败: {e}")
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
        ranges = {}
        for column in PRESET_COLUMNS:
            if column == "name":
                continue
            raw = (row.get(column) or "").strip()
            if raw == "":
                return None
            parsed = parse_float_parameter(raw)
            if parsed is None:
                return None
            values[column], ranges[column] = parsed
        defaults = {"name": name}
        defaults.update({column: getattr(BodyPreset(name=name), column)
                         for column in PRESET_COLUMNS if column != "name"})
        for column, value in values.items():
            defaults[column] = cls._clamp_ratio(value, defaults.get(column, 0.1))
        weight = parse_float_parameter(row.get("weight", row.get("权重", "1")))
        if weight is None or weight[0] < 0:
            weight_value = 1.0
        else:
            weight_value = weight[0]
        return BodyPreset(**defaults, weight=weight_value, parameter_ranges=ranges)

    @staticmethod
    def _to_row(item: BodyPreset) -> dict:
        data = asdict(item)
        ranges = data["parameter_ranges"]
        return {"weight": data["weight"],
                **{column: format_float_parameter(data[column], ranges.get(column, 0.0))
                   for column in PRESET_COLUMNS}}

    @classmethod
    def write_csv(cls, path: str, items: List[BodyPreset]) -> None:
        """把身材列表写为 CSV 表（utf-8-sig，便于 Excel 直接编辑）。"""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=PRESET_COLUMNS_WITH_WEIGHT)
            writer.writeheader()
            for item in items:
                writer.writerow(cls._to_row(item))
