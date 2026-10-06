"""模拟计算：伤亡增量、环境系数与部位解锁信息更新。

自 ``core/logic.py`` 按职责拆出（原文件现为薄壳 ``core/logic/__init__.py``）。
``@behavior_hook`` 的 scope ``"logic"`` 是已部署行为包的公开契约，与文件位置无关，
不得改动（见 ``docs/world_pack_behaviors.md``）。
"""

import math
import random
from typing import Dict, Optional

from core.behavior_runtime import behavior_hook


@behavior_hook("logic", "apply_size_unlock_updates")
def apply_size_unlock_updates(unlocks: Dict[str, str], updates: Dict[str, str],
                              info_update_rate: float = 0.5) -> Dict[str, str]:
    """按报告正文描述规则写入部位解锁信息，返回新的解锁字典。

    原解锁为空或 MEASURED 时直接写入新描述；原为详细文本且新描述非空时，
    以 info_update_rate 概率覆写。
    """
    unlocks = dict(unlocks or {})
    for part, new_desc in (updates or {}).items():
        new_desc = (new_desc or "").strip()
        if not new_desc:
            continue
        old = unlocks.get(part, "")
        if old in ("", "MEASURED"):
            unlocks[part] = new_desc
        elif random.random() < info_update_rate:
            unlocks[part] = new_desc
    return unlocks


@behavior_hook("logic", "compute_environment_factor")
def compute_environment_factor(text: str) -> float:
    """按文本中的环境关键词给出伤亡环境系数。"""
    _HIGH_RISK_WORDS = ["城市", "街道", "楼", "建筑", "住宅", "市中心", "广场",
                        "马路", "公路", "桥梁", "车站", "机场", "港口", "城镇",
                        "村庄", "居民", "人群", "交通", "地铁", "铁路", "商场"]
    _LOW_RISK_WORDS = ["野外", "森林", "山", "山脉", "海", "海洋", "湖", "河",
                       "沙漠", "草原", "荒野", "丛林", "岛屿", "海岸", "山谷",
                       "田野", "农田", "自然", "无人区"]
    has_high = any(kw in text for kw in _HIGH_RISK_WORDS)
    has_low = any(kw in text for kw in _LOW_RISK_WORDS)
    if has_high and not has_low:
        return 1.0 + 0.5 * random.random()
    elif has_low and not has_high:
        return 0.1 + 0.1 * random.random()
    else:
        return 0.3 + 0.3 * random.random()


@behavior_hook("logic", "compute_casualty")
def compute_casualty(height: float, step: float, destruction: float, text: str,
                     env_factor: Optional[float] = None) -> float:
    """计算一段文本的伤亡增量：0.015 × 身高² × 故事步长 × 破坏性 × 环境系数 × 碰撞系数。"""
    if env_factor is None:
        env_factor = compute_environment_factor(text)
    collision_factor = max(0.0, math.log10(height))
    return 0.01 * height * height * step * destruction * env_factor * collision_factor
