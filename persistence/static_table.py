import ast
import random
from typing import Iterable, Optional, Tuple


def parse_float_parameter(value: str) -> Optional[Tuple[float, float]]:
    """读取标量或 ``[value, spread]`` / ``(value, spread)`` 或 ``value, spread`` 参数。"""
    text = (value or "").strip()
    if not text:
        return None

    # 先尝试解析为 Python 字面量（处理 [..]、(...) 和纯数字）
    try:
        parsed = ast.literal_eval(text)
    except (SyntaxError, ValueError):
        parsed = None

    if parsed is not None:
        if isinstance(parsed, (list, tuple)):
            if len(parsed) != 2:
                return None
            try:
                base, spread = float(parsed[0]), float(parsed[1])
            except (TypeError, ValueError):
                return None
            return base, max(0.0, spread)
        try:
            return float(parsed), 0.0
        except (TypeError, ValueError):
            pass

    # 尝试解析无括号逗号分隔格式：value, spread
    if "," in text:
        parts = text.split(",")
        if len(parts) == 2:
            try:
                base = float(parts[0].strip())
                spread = float(parts[1].strip())
                return base, max(0.0, spread)
            except (TypeError, ValueError):
                pass

    return None


def weighted_choice(items: Iterable, rng=random):
    """按资源行的 weight 抽取；没有有效权重时退回等概率抽取。"""
    pool = list(items)
    if not pool:
        return None
    weights = [max(0.0, float(getattr(item, "weight", 1.0))) for item in pool]
    return rng.choices(pool, weights=weights, k=1)[0] if sum(weights) > 0 else rng.choice(pool)


def format_float_parameter(value: float, spread: float = 0.0):
    """序列化参数；无浮动范围时保持旧表的标量格式。"""
    if not spread:
        return value
    return f"[{value:g}, {spread:g}]"
