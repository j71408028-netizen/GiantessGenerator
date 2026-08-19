import csv
import os
from typing import List, Optional, Tuple

from paths import names_dir

DEFAULT_NAME_TABLE = "default"


class NameRepo:
    """静态姓名表仓库：管理 <数据目录>/static/names/ 下的 CSV 表。"""

    def __init__(self, dir_path: Optional[str] = None, world_state=None):
        self._world_state = world_state
        self._free_dir = dir_path or names_dir()

    def _read_dir(self) -> str:
        if self._world_state is not None and self._world_state.owns("names"):
            return self._world_state.pack_path("names")
        return self._free_dir

    def get_tables(self) -> List[str]:
        """返回可用姓名表名（文件名不含 .csv 后缀），按名称排序。"""
        read_dir = self._read_dir()
        if not os.path.isdir(read_dir):
            return []
        return sorted(
            os.path.splitext(f)[0]
            for f in os.listdir(read_dir)
            if f.lower().endswith(".csv")
        )

    def resolve(self, table_name: str) -> str:
        name = table_name or DEFAULT_NAME_TABLE
        if not name.lower().endswith(".csv"):
            name += ".csv"
        return os.path.join(self._read_dir(), name)

    def load(self, table_name: str = DEFAULT_NAME_TABLE) -> Tuple[List[str], List[float], List[str], List[str]]:
        """加载姓名表，返回 (surnames, weights, names, nicks)。"""
        csv_path = self.resolve(table_name)
        surnames: List[str] = []
        weights: List[float] = []
        names: List[str] = []
        nicks: List[str] = []
        if not os.path.exists(csv_path):
            return surnames, weights, names, nicks
        try:
            with open(csv_path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    surname = row.get('surname', '').strip()
                    ppm_str = row.get('ppm', '').strip()
                    name = row.get('name', '').strip()
                    nick = row.get('nick', '').strip()
                    if surname:
                        surnames.append(surname)
                        try:
                            weights.append(float(ppm_str) if ppm_str else 1.0)
                        except ValueError:
                            weights.append(1.0)
                    if name:
                        names.append(name)
                    if nick:
                        nicks.append(nick)
        except Exception as e:
            print(f"加载姓名表 {table_name} 失败: {e}")
        return surnames, weights, names, nicks
