import csv
import os
from typing import List, Optional

from models import Personality
from paths import personalities_dir

DEFAULT_PERSONALITY_TABLE = "default"

# 与 Personality 字段一一对应
PERSONALITY_COLUMNS = [
    "name",
    "init_intrusion", "step_intrusion",
    "init_destruction", "step_destruction",
    "sensitivity", "skip_base_prob",
    "description",
]


class PersonalityRepo:
    """静态性格表仓库：管理 <数据目录>/static/personalities/ 下的 CSV 表，无内部管理器。"""

    def __init__(self, dir_path: Optional[str] = None,
                 table: str = DEFAULT_PERSONALITY_TABLE, world_state=None):
        self._world_state = world_state
        self._free_dir = dir_path or personalities_dir()
        self._table = table or DEFAULT_PERSONALITY_TABLE
        self._cache: Optional[List[Personality]] = None

    def _read_dir(self) -> str:
        if self._world_state is not None and self._world_state.owns("personalities"):
            return self._world_state.pack_path("personalities")
        return self._free_dir

    def get_tables(self) -> List[str]:
        """返回可用性格表名（文件名不含 .csv 后缀）。"""
        read_dir = self._read_dir()
        if not os.path.isdir(read_dir):
            return []
        return sorted(
            os.path.splitext(filename)[0]
            for filename in os.listdir(read_dir)
            if filename.lower().endswith(".csv")
        )

    def resolve(self, table_name: str) -> str:
        """返回指定性格表 CSV 的完整路径。"""
        name = table_name or DEFAULT_PERSONALITY_TABLE
        if not name.lower().endswith(".csv"):
            name += ".csv"
        return os.path.join(self._read_dir(), name)

    def set_table(self, table_name: str):
        self._table = table_name or DEFAULT_PERSONALITY_TABLE
        self._cache = None

    def load(self, table: Optional[str] = None) -> List[Personality]:
        """加载性格表，返回 Personality 列表；坏行跳过并打印警告。

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
                        print(f"性格表 {table} 第 {line_no} 行被跳过: 数据无效")
        except Exception as e:
            print(f"加载性格表 {table} 失败: {e}")
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
            "skip_base_prob": row.get("skip_base_prob"),
        }
        fields = {
            "init_intrusion": (0.0, 4.0, 1.0),
            "step_intrusion": (-5.0, 5.0, 0.5),
            "init_destruction": (0.0, 4.0, 1.0),
            "step_destruction": (-5.0, 5.0, 0.5),
            "sensitivity": (-5.0, 5.0, 1.0),
            "skip_base_prob": (0.0, 5.0, 3.0),
        }
        values = {}
        for field, (lo, hi, default) in fields.items():
            text = (raw.get(field) or "").strip()
            if text == "":
                return None
            try:
                values[field] = cls._clamp(float(text), lo, hi, default)
            except ValueError:
                return None
        item = Personality(name=name, **values,
                           description=(row.get("description") or "").strip())
        return item

    @staticmethod
    def _to_row(item: Personality) -> dict:
        return {
            "name": item.name,
            "init_intrusion": item.init_intrusion,
            "step_intrusion": item.step_intrusion,
            "init_destruction": item.init_destruction,
            "step_destruction": item.step_destruction,
            "sensitivity": item.sensitivity,
            "skip_base_prob": item.skip_base_prob,
            "description": item.description,
        }

    @classmethod
    def write_csv(cls, path: str, items: List[Personality]) -> None:
        """把性格列表写为 CSV 表（utf-8-sig，便于 Excel 直接编辑）。"""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=PERSONALITY_COLUMNS)
            writer.writeheader()
            for item in items:
                writer.writerow(cls._to_row(item))
