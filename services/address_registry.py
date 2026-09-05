"""在线注册地址仓库（GitHub Gist）客户端：下载、缓存与搜索。

注册表托管在 Gist：https://gist.github.com/j71408028-netizen/da0b6204e57a411462f2d72e3e0f1997

推荐树形式（``worlds`` 键），已申领地址按地域层级组织::

    {"version": 2,
     "worlds": {"ea1": {"description": "世界观描述", "owner": "...",
                  "regions": {"1": {"scale": ["1e4", "1e3", "1e2"], "chunk": "abc",
                                "description": "...", "owner": "...",
                                "regions": {"3": {"scale": ["1e3", "1e2"],
                                              "regions": {"5": {"scale": ["1e2"]}}}}}}}}}

申领规模规则：
- 一级地域**必须**带 3 个规模（一级/二级/三级边长，米）；
- 二级地域可选带 2 个规模（覆写二/三级边长）；
- 三级地域可选带 1 个规模（覆写三级边长）；
- 未覆写的级别沿路径继承上一级。
私有名（``chunk``）只取路径上**最先声明**的值，子地址的覆写无效；想在
其他私有名的语境下扩展，应申领平行地址。

客户端把树扁平化为"派生条目"（沿路径逐级覆写后的完整地址文本），供搜索、
逐字匹配与子地址下拉使用；每个节点（含世界观本身）都是可注册的申领记录。

兼容旧的扁平格式：列表式 ``{"addresses": [{address, description, ...}]}`` 与
映射式 ``{"<地址>": "描述"}``。

注册规则：文本管理器中把风格注册到某地址时，必须联网重新下载最新注册表，
且地址必须已在表中申领（规范化后逐字匹配）；离线或注册未申领的地址一律
拒绝。本地缓存仅供离线查询 / 搜索，不能用于注册。
"""

import json
import os
import urllib.error
import urllib.request
from datetime import datetime

from address_model import _REGION_RE, _WORLD_RE, cell_width_m, parse_full, split_address
from paths import data_dir

GIST_ID = "da0b6204e57a411462f2d72e3e0f1997"
GIST_PAGE_URL = f"https://gist.github.com/j71408028-netizen/{GIST_ID}"
GIST_API_URL = f"https://api.github.com/gists/{GIST_ID}"
REGISTRY_FILENAME = "registry.json"
DOWNLOAD_TIMEOUT = 15
CACHE_PATH = os.path.join(data_dir(), "user", "address_registry.json")


class RegistryOfflineError(RuntimeError):
    """网络不可达 / 仓库不可用时抛出（禁止离线注册）。"""


def download_registry() -> tuple:
    """下载最新注册表。返回 (data, gist_updated_at)；失败抛 RegistryOfflineError。"""
    req = urllib.request.Request(
        GIST_API_URL,
        headers={"User-Agent": "GiantessGenerator",
                 "Accept": "application/vnd.github+json"})
    try:
        with _build_opener().open(req, timeout=DOWNLOAD_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise RegistryOfflineError(f"无法连接地址注册表仓库：{exc}") from exc
    entry = (payload.get("files") or {}).get(REGISTRY_FILENAME)
    if not isinstance(entry, dict) or "content" not in entry:
        raise RegistryOfflineError(f"仓库中缺少 {REGISTRY_FILENAME} 文件")
    try:
        data = json.loads(entry.get("content") or "{}")
    except ValueError as exc:
        raise RegistryOfflineError(f"{REGISTRY_FILENAME} 不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise RegistryOfflineError(f"{REGISTRY_FILENAME} 的顶层应为 JSON 对象")
    return data, payload.get("updated_at") or ""


def _build_opener() -> urllib.request.OpenerDirector:
    # 标准库不支持 SOCKS 代理：忽略 socks 环境变量，保留 http(s) 代理或直连。
    proxies = {k: v for k, v in urllib.request.getproxies().items()
               if v.lower().startswith(("http://", "https://"))}
    return urllib.request.build_opener(urllib.request.ProxyHandler(proxies))


def load_cache(cache_path: str = CACHE_PATH) -> dict:
    """读取本地缓存 {data, fetched_at, gist_updated_at}；无缓存返回空注册表。"""
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            cache = json.load(f)
        if isinstance(cache, dict) and isinstance(cache.get("data"), dict):
            return cache
    except (OSError, ValueError):
        pass
    return {"data": {}, "fetched_at": "", "gist_updated_at": ""}


def save_cache(data: dict, gist_updated_at: str = "", cache_path: str = CACHE_PATH) -> dict:
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    cache = {"data": data,
             "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             "gist_updated_at": gist_updated_at}
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    return cache


# ==================== 解析：树形式 → 派生条目 ====================

def _norm_num(text) -> str:
    """校验规模数值并返回规范字符串。

    显示保留申领者原文（如 1e4），匹配由 _address_key 按数值归一。
    """
    v = float(text)
    if not (v > 0):
        raise ValueError(f"规模值必须为正数：{text}")
    return str(text).strip()


def _node_meta(node: dict) -> dict:
    return {"description": str(node.get("description") or ""),
            "owner": str(node.get("owner") or ""),
            "claimed_at": str(node.get("claimed_at") or "")}


_NODE_KEYS = {"description", "owner", "claimed_at", "scale", "chunk", "regions"}


def _check_unknown_keys(node: dict, path: str, issues: list):
    """节点里的未知字段（常为编辑时漏嵌套一层）报告为问题，避免静默丢失。"""
    for key in node:
        if key not in _NODE_KEYS:
            issues.append(f"{path}：未知字段 '{key}'（疑似漏嵌套 regions），已忽略")


def _child_regions(node: dict, path: str, issues: list) -> dict:
    regions = node.get("regions")
    if regions is None:
        return {}
    if not isinstance(regions, dict):
        issues.append(f"{path}：regions 应为对象，已忽略")
        return {}
    return regions


def _node_scale(node: dict, expected: int, optional: bool,
                path: str, issues: list):
    """读取节点规模列表并规范化；返回规范化字符串列表或 None（缺省/非法）。"""
    raw = node.get("scale")
    if raw is None or raw == []:
        if optional:
            return None
        issues.append(f"{path}：必须带 {expected} 个规模，已跳过")
        return None
    if not isinstance(raw, list) or len(raw) != expected:
        issues.append(f"{path}：规模应为 {expected} 个数字，已跳过")
        return None
    try:
        return [_norm_num(x) for x in raw]
    except (TypeError, ValueError):
        issues.append(f"{path}：规模值 {raw} 不是数字，已跳过")
        return None


def _declared_chunk(node: dict) -> str:
    chunk = str(node.get("chunk") or "").strip()
    return "" if chunk in ("", "*") else chunk


def _make_entry(ids: list, scale: list, chunk: str, meta: dict) -> dict:
    scale_raw = "_".join(scale)
    ids_text = "-".join(ids)
    if chunk:
        address = f"{scale_raw}@{chunk}!{ids_text}"
    else:
        address = f"{scale_raw}@{ids_text}"
    entry = {"address": address, **meta}
    entry["scale"] = list(scale)
    entry["chunk"] = chunk
    return entry


def flatten_tree(data: dict) -> tuple:
    """把树形注册表扁平化为 (派生条目列表, 问题列表)。

    每个申领节点生成一条派生条目：规模沿路径逐级覆写（一级 3 个必填，
    二级可选 2 个，三级可选 1 个，缺省继承）；私有名取路径上最先声明的值。
    """
    entries, issues = [], []
    worlds = data.get("worlds")
    if not isinstance(worlds, dict):
        issues.append("树形注册表缺少 worlds 节点")
        return entries, issues
    for world, wnode in worlds.items():
        world = str(world).strip()
        if not _WORLD_RE.match(world):
            issues.append(f"世界观 '{world}' 名称不合法，已跳过")
            continue
        wnode = wnode if isinstance(wnode, dict) else {}
        _check_unknown_keys(wnode, world, issues)
        wmeta = _node_meta(wnode)
        # 世界观本身也是可注册的申领节点（纯世界观地址）
        entries.append({"address": world, **wmeta})
        for lid, lnode in _child_regions(wnode, world, issues).items():
            path1 = f"{world}-{lid}"
            if not _REGION_RE.match(str(lid)):
                issues.append(f"{path1}：一级地域 id 不合法，已跳过")
                continue
            lnode = lnode if isinstance(lnode, dict) else {}
            _check_unknown_keys(lnode, path1, issues)
            scale3 = _node_scale(lnode, 3, optional=False, path=path1, issues=issues)
            if scale3 is None:
                continue
            chunk1 = _declared_chunk(lnode)
            entries.append(_make_entry([world, str(lid)], scale3, chunk1,
                                       _node_meta(lnode)))
            for mid, mnode in _child_regions(lnode, path1, issues).items():
                path2 = f"{path1}-{mid}"
                if not _REGION_RE.match(str(mid)):
                    issues.append(f"{path2}：二级地域 id 不合法，已跳过")
                    continue
                mnode = mnode if isinstance(mnode, dict) else {}
                _check_unknown_keys(mnode, path2, issues)
                scale2 = _node_scale(mnode, 2, optional=True, path=path2, issues=issues)
                if scale2 is None and mnode.get("scale") is not None:
                    continue   # 覆写了但数量/格式非法：整节点跳过
                cur2 = list(scale3)
                if scale2 is not None:
                    cur2[1], cur2[2] = scale2
                chunk2 = chunk1 or _declared_chunk(mnode)
                entries.append(_make_entry([world, str(lid), str(mid)], cur2, chunk2,
                                           _node_meta(mnode)))
                for sid, snode in _child_regions(mnode, path2, issues).items():
                    path3 = f"{path2}-{sid}"
                    if not _REGION_RE.match(str(sid)):
                        issues.append(f"{path3}：三级地域 id 不合法，已跳过")
                        continue
                    snode = snode if isinstance(snode, dict) else {}
                    _check_unknown_keys(snode, path3, issues)
                    scale3rd = _node_scale(snode, 1, optional=True, path=path3, issues=issues)
                    if scale3rd is None and snode.get("scale") is not None:
                        continue
                    cur3 = list(cur2)
                    if scale3rd is not None:
                        cur3[2] = scale3rd[0]
                    entries.append(_make_entry([world, str(lid), str(mid), str(sid)],
                                               cur3, chunk2, _node_meta(snode)))
    return entries, issues


# ==================== 解析：旧扁平格式（兼容） ====================

def _parse_flat(data) -> list:
    entries = []
    if not isinstance(data, dict):
        return entries
    raw = data.get("addresses")
    if isinstance(raw, list):
        items = raw
    else:
        # 映射式：跳过元数据键，地址为键
        items = []
        for key, val in data.items():
            if key in ("version", "updated_at", "addresses", "worlds"):
                continue
            if isinstance(val, dict):
                item = dict(val)
                item.setdefault("address", key)
                items.append(item)
            elif isinstance(val, str):
                items.append({"address": key, "description": val})
            elif val is None:
                items.append({"address": key})
    for item in items:
        if isinstance(item, str):
            entries.append({"address": item.strip(), "description": "",
                            "owner": "", "claimed_at": ""})
        elif isinstance(item, dict) and (item.get("address") or "").strip():
            entries.append({"address": (item.get("address") or "").strip(),
                            "description": str(item.get("description") or ""),
                            "owner": str(item.get("owner") or ""),
                            "claimed_at": str(item.get("claimed_at") or "")})
    return entries


def parse_entries_ex(data) -> tuple:
    """解析注册表，返回 (条目列表, 问题列表)。树形式走扁平化，否则按旧格式。"""
    if isinstance(data, dict) and isinstance(data.get("worlds"), dict):
        return flatten_tree(data)
    return _parse_flat(data), []


def parse_entries(data) -> list:
    return parse_entries_ex(data)[0]


# ==================== 查询与搜索 ====================

def _address_key(text: str):
    """地址的规范化键（规模数值归一、私有名归一），用于逐字匹配。"""
    addr = parse_full(text or "")
    if addr is None:
        return None
    chunk = addr.chunk if addr.has_mark and addr.chunk not in ("", "*") else ""
    return (addr.world, addr.regions, addr.scale, chunk)


def find_entry(address_text: str, entries: list):
    """按规范化地址查找申领记录；未申领返回 None。"""
    key = _address_key(address_text)
    if key is None:
        return None
    for entry in entries:
        if _address_key(entry.get("address", "")) == key:
            return entry
    return None


def search_entries(keyword: str, entries: list) -> list:
    """按关键词过滤（匹配地址 / 描述 / 申领人，不区分大小写）。空关键词返回全部。"""
    kw = (keyword or "").strip().lower()
    if not kw:
        return list(entries)
    return [e for e in entries
            if any(kw in str(e.get(field) or "").lower()
                   for field in ("address", "description", "owner"))]


# ==================== 地址树（派生条目按地域前缀构成树） ====================

def ids_of(address_text: str) -> list:
    """地址的地域 id 段（世界观 + 各级地域）；非法地址返回 []。"""
    return split_address(address_text or "")


def claimed_descendants(style_address_text: str, entries: list) -> list:
    """已申领地址树中某地址的所有严格后代（各级细度都包含），按树序排列。

    树关系：后代的 id 段以祖先的 id 段为前缀（同一世界观）。
    """
    base = ids_of(style_address_text)
    if not base:
        return []
    pairs = []
    for entry in entries:
        ids = ids_of(entry.get("address", ""))
        if len(ids) > len(base) and ids[:len(base)] == base:
            pairs.append((ids, entry))
    pairs.sort(key=lambda pair: pair[0])
    return [entry for _, entry in pairs]


def child_options(style_address_text: str, entries: list,
                  landmark_size: float = None) -> list:
    """为地标地址下拉生成候选：[{display, value, recommended, entry}]。

    - ``value`` 为注册表派生的完整申领地址原文（直接回填地标地址）；
    - ``display`` 为去掉规模前缀的 id 段 + 描述，用于下拉展示；
    - ``landmark_size``（米）给出时，最细单元边长小于地标尺寸的子地址
      标记为不推荐（recommended=False，前端置灰）。
    """
    options = []
    for entry in claimed_descendants(style_address_text, entries):
        addr = entry.get("address", "")
        ids = ids_of(addr)
        display = "-".join(ids)
        desc = entry.get("description") or ""
        if desc:
            display = f"{display} ｜ {desc}"
        recommended = True
        if landmark_size and landmark_size > 0:
            width = cell_width_m(addr)
            if width and width < landmark_size:
                recommended = False
        options.append({"display": display, "value": addr,
                        "recommended": recommended, "entry": entry})
    return options
