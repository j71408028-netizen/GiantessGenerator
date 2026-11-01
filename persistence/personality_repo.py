import csv
import os
from typing import List, Optional

from core.models import Personality
from core.resource_spec import spec_of
from persistence.material_tables import (
    DEFAULT_MATERIAL_TABLE,
    group_dir,
    list_groups,
    member_path,
)
from persistence.static_table import format_float_parameter, parse_float_parameter

MATERIAL_TYPE = "materials"
MEMBER_KEY = "personalities"

# 列清单取自资源声明（顺序即写出的表头顺序）
PERSONALITY_COLUMNS = [
    key for key in spec_of(MATERIAL_TYPE).member(MEMBER_KEY).field_keys()
    if key != "weight"]
PERSONALITY_COLUMNS_WITH_WEIGHT = ["weight", *PERSONALITY_COLUMNS]


class PersonalityRepo:
    """性格表读取器：管理取材表组内的 ``personalities.csv``，无内部管理器。"""

    def __init__(self, group: Optional[str] = None, world_state=None):
        self._world_state = world_state
        self._group = group or DEFAULT_MATERIAL_TABLE
        self._cache: Optional[List[Personality]] = None

    def _read_dir(self) -> str:
        return group_dir(self._group, world_state=self._world_state)

    def get_groups(self) -> List[str]:
        """返回可用取材表组名。"""
        return list_groups(world_state=self._world_state)

    def resolve(self, group: Optional[str] = None) -> str:
        """返回指定组内性格表的完整路径（缺席时已回落到内置组）。"""
        return member_path(group or self._group, MEMBER_KEY,
                           world_state=self._world_state)

    def set_group(self, group: Optional[str]):
        self._group = group or DEFAULT_MATERIAL_TABLE
        self._cache = None

    def load(self, group: Optional[str] = None) -> List[Personality]:
        """加载性格表，返回 Personality 列表；坏行跳过并打印警告。

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
        items: List[Personality] = []
        if not os.path.isfile(path):
            self._cache = items if cache else self._cache
            return items
        try:
            with open(path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for line_no, raw in enumerate(reader, start=2):
                    item = self._row_to_personality(raw)
                    if item is not None:
                        items.append(item)
                    else:
                        print(f"性格表 {group} 第 {line_no} 行被跳过: 数据无效")
        except Exception as e:
            print(f"加载性格表 {group} 失败: {e}")
        if cache:
            self._cache = items
        return items

    # ---------- 行解析 / 序列化 ----------

    @staticmethod
    def _clamp(value: float, lo: float, hi: float, default: float) -> float:
        return default if not (lo <= value <= hi) else value

    @classmethod
    def _row_to_personality(cls, row) -> Optional[Personality]:
        name = (row.get("name") or "").strip()
        if not name:
            return None
        raw = {
            "init_intrusion": row.get("init_intrusion"),
            "step_intrusion": row.get("step_intrusion"),
            "init_destruction": row.get("init_destruction"),
            "step_destruction": row.get("step_destruction"),
            "sensitivity": row.get("sensitivity"),
            "gravity": row.get("gravity"),
            "skip_base_prob": row.get("skip_base_prob"),
        }
        fields = {
            "init_intrusion": (0.0, 4.0, 1.0),
            "step_intrusion": (-5.0, 5.0, 0.5),
            "init_destruction": (0.0, 4.0, 1.0),
            "step_destruction": (-5.0, 5.0, 0.5),
            "sensitivity": (-5.0, 5.0, 1.0),
            "gravity": (-5.0, 5.0, 0.0),
            "skip_base_prob": (0.0, 5.0, 3.0),
        }
        values = {}
        ranges = {}
        for field, (lo, hi, default) in fields.items():
            text = (raw.get(field) or "").strip()
            if text == "":
                # gravity 为新增列：旧表没有该列时按默认值 0 处理，不拒绝整行
                if field == "gravity":
                    values[field] = default
                    ranges[field] = 0.0
                    continue
                return None
            parsed = parse_float_parameter(text)
            if parsed is None:
                return None
            value, spread = parsed
            values[field] = cls._clamp(value, lo, hi, default)
            ranges[field] = spread
        weight = parse_float_parameter(row.get("weight", row.get("权重", "1")))
        weight_value = 1.0 if weight is None or weight[0] < 0 else weight[0]
        item = Personality(name=name, **values,
                           description=(row.get("description") or "").strip(),
                           weight=weight_value, parameter_ranges=ranges)
        return item

    @staticmethod
    def _to_row(item: Personality) -> dict:
        ranges = item.parameter_ranges
        return {
            "weight": item.weight,
            "name": item.name,
            "init_intrusion": format_float_parameter(
                item.init_intrusion, ranges.get("init_intrusion", 0.0)),
            "step_intrusion": format_float_parameter(
                item.step_intrusion, ranges.get("step_intrusion", 0.0)),
            "init_destruction": format_float_parameter(
                item.init_destruction, ranges.get("init_destruction", 0.0)),
            "step_destruction": format_float_parameter(
                item.step_destruction, ranges.get("step_destruction", 0.0)),
            "sensitivity": format_float_parameter(
                item.sensitivity, ranges.get("sensitivity", 0.0)),
            "gravity": format_float_parameter(
                item.gravity, ranges.get("gravity", 0.0)),
            "skip_base_prob": format_float_parameter(
                item.skip_base_prob, ranges.get("skip_base_prob", 0.0)),
            "description": item.description,
        }

    @classmethod
    def write_csv(cls, path: str, items: List[Personality]) -> None:
        """把性格列表写为 CSV 表（utf-8-sig，便于 Excel 直接编辑）。"""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=PERSONALITY_COLUMNS_WITH_WEIGHT)
            writer.writeheader()
            for item in items:
                writer.writerow(cls._to_row(item))
