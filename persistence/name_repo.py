import csv
import os
from typing import List, Optional, Tuple

from persistence.material_tables import (
    DEFAULT_MATERIAL_TABLE,
    list_groups,
    member_path,
)


class NameRepo:
    """姓名表读取器：定位当前取材表组内的 ``names.csv``。"""

    def __init__(self, world_state=None):
        self._world_state = world_state

    def get_groups(self) -> List[str]:
        """返回可用取材表组名（按名称排序）。"""
        return list_groups(world_state=self._world_state)

    def resolve(self, group: Optional[str] = None) -> str:
        """返回指定组内姓名表的完整路径（缺席时已回落到内置组）。"""
        return member_path(group, "names", world_state=self._world_state)

    def load(self, group: Optional[str] = None) -> Tuple[List[str], List[float], List[str], List[str]]:
        """加载姓名表，返回 (surnames, weights, names, nicks)。"""
        csv_path = self.resolve(group)
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
            print(f"加载姓名表 {group or DEFAULT_MATERIAL_TABLE} 失败: {e}")
        return surnames, weights, names, nicks
