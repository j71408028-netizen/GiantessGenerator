"""资源同步的状态判定：两份索引逐条比对，不联网。

本轮只定形状，不做传输：界面与自检拿到的是「这条资源现在处于什么状态」，
真正的拉取/推送、鉴权与增量协议留到服务端接入时再写。判据只有 ``content_hash``
与一条上次同步的基线，因此完全离线——「本地有没有改过」永远不需要服务端参与。

三方比对（规范 §4.2）：

============  ==================================================
状态           含义
============  ==================================================
``in_sync``    两侧哈希相同
``local_only`` 只有本地有（还没发布）
``remote_new`` 只有远端有（还没下载）
``local_changed``   本地改了，远端仍是基线
``remote_changed``  远端改了，本地仍是基线
``both_changed``    两边都改过（或没有基线可比），需要人工裁决
============  ==================================================
"""

from typing import Any, Dict, Iterable, Optional

IN_SYNC = "in_sync"
LOCAL_ONLY = "local_only"
REMOTE_NEW = "remote_new"
LOCAL_CHANGED = "local_changed"
REMOTE_CHANGED = "remote_changed"
BOTH_CHANGED = "both_changed"

SYNC_STATES = (IN_SYNC, LOCAL_ONLY, REMOTE_NEW, LOCAL_CHANGED,
               REMOTE_CHANGED, BOTH_CHANGED)

#: 需要人工处理的冲突态
CONFLICT_STATES = (BOTH_CHANGED,)


def _hash_of(entry: Optional[dict]) -> str:
    return str((entry or {}).get("content_hash") or "")


def compare_entry(local: Optional[dict], remote: Optional[dict],
                  base_hash: str = "") -> str:
    """单条资源的三方比对。

    ``base_hash`` 是上次同步时的内容哈希；没有基线时无法判断是谁改的，
    两侧哈希不同一律按 ``both_changed`` 报（宁可多问一次，也不覆盖别人的改动）。
    """
    if local is None and remote is None:
        return IN_SYNC
    if remote is None:
        return LOCAL_ONLY
    if local is None:
        return REMOTE_NEW
    local_hash, remote_hash = _hash_of(local), _hash_of(remote)
    if local_hash == remote_hash:
        return IN_SYNC
    if base_hash:
        if local_hash == base_hash:
            return REMOTE_CHANGED
        if remote_hash == base_hash:
            return LOCAL_CHANGED
    return BOTH_CHANGED


def _entries(catalog: Optional[dict], type_id: str) -> Dict[str, dict]:
    """索引里某类型的 ``{id: 条目}``；坏条目跳过。"""
    result: Dict[str, dict] = {}
    for entry in ((catalog or {}).get("entries") or {}).get(type_id) or []:
        if isinstance(entry, dict) and entry.get("id"):
            result[str(entry["id"])] = entry
    return result


def base_hash_for(base: Optional[dict], type_id: str, resource_id: str) -> str:
    """从基线快照里取某条资源的上次同步哈希。

    基线形状与索引同构（``{"entries": {类型: [{id, content_hash}, …]}}``），
    这样它既能写成文件，也能直接来自服务端上一次的 ``catalog.json``。
    """
    for entry in ((base or {}).get("entries") or {}).get(type_id) or []:
        if isinstance(entry, dict) and entry.get("id") == resource_id:
            return _hash_of(entry)
    return ""


def diff_catalogs(local: Optional[dict], remote: Optional[dict],
                  base: Optional[dict] = None,
                  type_ids: Optional[Iterable[str]] = None) -> Dict[str, Dict[str, str]]:
    """逐条比对两份索引，返回 ``{类型: {资源 id: 状态}}``。

    没有 ``remote`` 时全部按 ``local_only`` 处理——这就是"还没接服务端"的
    正常结果，不需要调用方写特例。
    """
    types = list(type_ids) if type_ids is not None else sorted(
        {*((local or {}).get("entries") or {}), *((remote or {}).get("entries") or {})})
    result: Dict[str, Dict[str, str]] = {}
    for type_id in types:
        local_entries = _entries(local, type_id)
        remote_entries = _entries(remote, type_id)
        states: Dict[str, str] = {}
        for resource_id in sorted({*local_entries, *remote_entries}):
            states[resource_id] = compare_entry(
                local_entries.get(resource_id),
                remote_entries.get(resource_id),
                base_hash_for(base, type_id, resource_id))
        if states:
            result[type_id] = states
    return result


def summarize(diff: Dict[str, Dict[str, str]]) -> Dict[str, int]:
    """按状态计数（含 ``in_sync``，便于判断"是否全部一致"）。"""
    counts = {state: 0 for state in SYNC_STATES}
    for states in (diff or {}).values():
        for state in states.values():
            counts[state] = counts.get(state, 0) + 1
    return counts


def pending_changes(diff: Dict[str, Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    """只保留需要动作的条目（去掉 ``in_sync``）。"""
    return {type_id: {rid: state for rid, state in states.items() if state != IN_SYNC}
            for type_id, states in (diff or {}).items()
            if any(state != IN_SYNC for state in states.values())}


def entry_state(diff: Dict[str, Dict[str, str]], type_id: str,
                resource_id: str) -> str:
    """取某条资源的状态；不在比对结果里时视为 ``in_sync``。"""
    return ((diff or {}).get(type_id) or {}).get(resource_id, IN_SYNC)


__all__ = [
    "BOTH_CHANGED", "CONFLICT_STATES", "IN_SYNC", "LOCAL_CHANGED", "LOCAL_ONLY",
    "REMOTE_CHANGED", "REMOTE_NEW", "SYNC_STATES", "base_hash_for", "compare_entry",
    "diff_catalogs", "entry_state", "pending_changes", "summarize",
]
