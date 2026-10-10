"""副本启动载荷：``DungeonSessionWindow`` 构造参数的进程间序列化。

副本会话运行在独立子进程里（父进程侧见 ``ui.common.dungeon_spawner``，
子进程侧见 ``ui.common.dungeon_child_host``），构造参数必须跨进程传递。
本模块把窗口的构造参数 dict 规范化为**纯数据**（dataclass 落成 dict、
宿主侧概念剔除），并在子进程侧还原——还原后的 kwargs 可以直接

    DungeonSessionWindow(host=..., **deserialize_launch(payload))

构造出与父进程想要的那一个完全等价的窗口。

规则：

- ``parent`` / ``host`` / ``gui`` / ``scenario_repo`` / ``character_repo``
  是宿主侧概念：``parent`` 由子进程自行给 ``None``，``host`` 由子进程换成
  ``ChildHost``，两个仓库由子进程按数据目录自建（与世界包状态一致），
  ``gui`` 在子进程里没有对应物（其两项实际用途已分别化解——设置改经
  ``settings`` 参数传入，行动点数扣除走 ``StateService`` 的静态入口）；
- ``personality`` / ``preset`` / ``character`` 是 dataclass，落成 ``asdict``，
  还原时用各自的 ``from_dict`` / 字段过滤构造（与仓库的存档序列化同一套
  往返，见 ``persistence/character_repo.py``）；
- 其余参数本来就是 JSON 安全的纯数据，原样透传。

结果对象（:class:`~dungeon.window.result.SessionResult`）走同一条管道返回，
``result_to_dict`` / ``result_from_dict`` 负责往返。
"""

from dataclasses import asdict, fields

from core.models import BodyPreset, CharacterSnapshot, Personality

#: 宿主侧概念参数：序列化时剔除，由子进程侧自行提供（host/仓库）或不再需要
_HOST_SIDE_KEYS = ("parent", "host", "gui", "scenario_repo", "character_repo")

#: dataclass 参数 -> 还原构造器
_DATACLASS_KEYS = {
    "personality": Personality.from_dict,
    "character": CharacterSnapshot.from_dict,
}


def serialize_launch(kwargs: dict) -> dict:
    """把窗口构造参数 dict 序列化为跨进程安全的纯数据 dict。"""
    payload = {}
    for key, value in kwargs.items():
        if key in _HOST_SIDE_KEYS:
            continue
        if value is None:
            payload[key] = None
        elif key == "preset":
            payload[key] = asdict(value)
        elif key in _DATACLASS_KEYS:
            payload[key] = asdict(value)
        else:
            payload[key] = value
    return payload


def deserialize_launch(payload: dict) -> dict:
    """把载荷还原回窗口构造参数 dict（不含 ``parent`` / ``host`` / ``gui``）。"""
    kwargs = dict(payload)
    for key, restore in _DATACLASS_KEYS.items():
        if isinstance(kwargs.get(key), dict):
            kwargs[key] = restore(kwargs[key])
    if isinstance(kwargs.get("preset"), dict):
        kwargs["preset"] = _from_fields(BodyPreset, kwargs["preset"])
    return kwargs


def _from_fields(cls, data: dict):
    """按 dataclass 字段过滤构造（``BodyPreset`` 没有现成的 ``from_dict``）。"""
    allowed = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in allowed})


# ==================== 会话结果往返 ====================

def result_to_dict(result) -> dict:
    """:class:`SessionResult` -> 纯数据 dict（只带非缺省字段亦可，这里全量带）。"""
    return {name: getattr(result, name) for name in result.__slots__}


def result_from_dict(data: dict):
    """纯数据 dict -> :class:`SessionResult`；空/坏数据按启动失败处理。"""
    from dungeon.window.result import REASON_LAUNCH_FAILED, SessionResult

    if not isinstance(data, dict) or "reason" not in data:
        return SessionResult(REASON_LAUNCH_FAILED,
                             launch_error="副本子进程没有返回结果")
    known = {name: data[name] for name in SessionResult.__slots__
             if name in data}
    return SessionResult(known.pop("reason"), **known)


__all__ = ["serialize_launch", "deserialize_launch",
           "result_to_dict", "result_from_dict"]
