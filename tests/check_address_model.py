# -*- coding: utf-8 -*-
"""地址模型自检：解析、相对地址、严格组合、距离区间与覆盖关系。

用法：``python tests/check_address_model.py``
只覆盖纯逻辑，不建窗口、不联网。
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_report_path = os.path.join(tempfile.mkdtemp(prefix="address_model_"), "report.txt")
_log = open(_report_path, "w", encoding="utf-8")
sys.stdout = _log
sys.stderr = _log

failures = []
total = 0


def check(name, ok, extra=""):
    global total
    total += 1
    print(("  OK   " if ok else "  FAIL ") + name + (f"  <{extra}>" if extra and not ok else ""))
    if not ok:
        failures.append(name)


from core.address_model import (  # noqa: E402
    REL_CONTAINS, REL_DIVERGENT, REL_INSIDE, REL_INVALID, REL_SAME,
    REL_WORLD_MISMATCH, canonical_key, compose_address_ex, covers,
    distance_interval_m, distance_m, distance_uncertain, format_addr_compact,
    parse_address, parse_address_ex, parse_full, relate,
    resolve_full_address, resolve_full_address_ex,
)

# ---------------- 1. 解析 ----------------
_ABS = "1e4_1e3_1e2@RW-CN1-East-3"
_addr = parse_full(_ABS)
check("完整地址可解析",
      _addr is not None and _addr.world == "RW"
      and _addr.regions == ("CN1", "East", "3"))
check("parse_full 不接受相对地址", parse_full("East-3") is None)

_rel = parse_address("East-3", "1e4_1e3_1e2@RW-CN1")
check("相对地址在 base 下追加剩余级",
      _rel is not None and _rel.regions == ("CN1", "East", "3")
      and _rel.relative is True)
check("单段相对地址在 base 下作为一级地域",
      parse_address("East", "1e4_1e3_1e2@RW-CN1").regions == ("CN1", "East", ""))
check("相对地址无 base 时拒绝", parse_address("East-3") is None)
check("相对地址无 base 时有明确诊断",
      "基准" in parse_address_ex("East-3")[1])
check("纯世界观 base 无规模时相对地址拒绝",
      parse_address("East-3", "RW") is None
      and "规模" in parse_address_ex("East-3", "RW")[1])

check("漏写 @ 的完整地域段不被误当相对后缀",
      parse_address("RW-CN1", "1e4_1e3_1e2@RW") is None)
check("纯世界观可解析",
      parse_full("RW") is not None and parse_full("RW").world == "RW")

# ---------------- 2. 严格组合 ----------------
_ok, _err = resolve_full_address_ex("1e4_1e3_1e2@RW-CN1", "East-3")
_ok_addr = parse_full(_ok)
check("相对后缀组合成功",
      not _err and _ok_addr is not None
      and _ok_addr.regions == ("CN1", "East", "3"))

_ok2, _err2 = resolve_full_address_ex(
    "1e4_1e3_1e2@RW-CN1", "1e4_1e3_1e2@RW-CN1-East-3")
check("完整地址同前缀组合成功", not _err2 and parse_full(_ok2) is not None)

check("风格地址为空时继承地标完整地址",
      parse_full(resolve_full_address("", _ABS)) is not None)
check("风格注册地址写坏时报错",
      resolve_full_address_ex("坏风格", _ABS)[0] == "")
check("纯世界观风格不能组合相对后缀",
      resolve_full_address_ex("RW", "East-3")[0] == "")
check("风格地址为空时相对地址不可用",
      resolve_full_address("", "East-3") == "")
check("地标地址为空时继承风格地址",
      parse_full(resolve_full_address("1e4_1e3_1e2@RW-CN1", "")) is not None)

_cross = resolve_full_address_ex("1e4_1e3_1e2@RW-CN1",
                                 "1e4_1e3_1e2@TH-X")
check("跨世界观组合报错", _cross[0] == "" and "世界观" in _cross[1])
check("跨世界观不再产出 RW-TH-X", "TH-X" not in _cross[0])

_diverge = resolve_full_address_ex("1e4_1e3_1e2@RW-CN1",
                                   "1e4_1e3_1e2@RW-CN2")
check("分支分叉组合报错", _diverge[0] == "" and "之内" in _diverge[1])

_deep = resolve_full_address_ex("1e4_1e3_1e2@RW-CN1", "East-3-4")
check("相对后缀导致层级过深时报错",
      _deep[0] == "" and "三级" in _deep[1])

_chunk = resolve_full_address_ex("1e4_1e3_1e2@A!RW-CN1",
                                 "1e4_1e3_1e2@B!RW-CN1-East")
check("私有名冲突报错", _chunk[0] == "" and "私有名" in _chunk[1])

_chunk_ok, _chunk_err = resolve_full_address_ex("1e4_1e3_1e2@A!RW-CN1", "East-1")
_chunk_addr = parse_full(_chunk_ok)
check("私有名按风格优先继承",
      not _chunk_err and _chunk_addr is not None and _chunk_addr.chunk == "A")

_scale_ok, _ = resolve_full_address_ex(
    "1e4_1e3_1e2@RW-CN1", "2e4_2e3_2e2@RW-CN1-East")
_scale_addr = parse_full(_scale_ok) if _scale_ok else None
check("更细一方的绝对规模胜出",
      _scale_addr is not None and _scale_addr.scale[0] == 20000.0)

# ---------------- 3. 距离区间 ----------------
check("同址距离为 0",
      distance_interval_m("1e4_1e3_1e2@RW-A-B-1",
                          "1e4_1e3_1e2@RW-A-B-1") == (0.0, 0.0))
_interval = distance_interval_m("1e5_1e3_1e2@RW-A",
                                "1e5_1e3_1e2@RW-A-B")
check("位置比目标粗时距离有上界",
      _interval is not None and _interval[0] == 0.0 and _interval[1] == 1e5)
check("纯世界观位置用目标分歧级边长兜底",
      distance_interval_m("RW", "1e5_1e3_1e2@RW-A-B-C") == (0.0, 1e5))
check("位置比目标细时目标区域按 0 计",
      distance_interval_m("1e5_1e3_1e2@RW-A-B",
                          "1e5_1e3_1e2@RW-A") == (0.0, 0.0))
check("分歧级距离取平均边长",
      distance_interval_m("1e4_1e3_1e2@RW-A-B-1",
                          "1e4_1e3_1e2@RW-A-B-2") == (100.0, 100.0))
check("跨世界观距离未知",
      distance_interval_m("1e4_1e3_1e2@RW-A",
                          "1e4_1e3_1e2@TH-A") is None)
check("distance_m 保持区间下界",
      distance_m("1e5_1e3_1e2@RW-A", "1e5_1e3_1e2@RW-A-B") == 0.0)
check("distance_uncertain 识别粗定位",
      distance_uncertain("1e5_1e3_1e2@RW-A", "1e5_1e3_1e2@RW-A-B")
      and not distance_uncertain("1e5_1e3_1e2@RW-A-B",
                                 "1e5_1e3_1e2@RW-A"))

# ---------------- 4. 覆盖关系与规范键 ----------------
check("粗作用域覆盖细地址",
      covers("1e4_1e3_1e2@RW", "1e4_1e3_1e2@RW-A-B"))
check("细作用域不覆盖粗地址",
      not covers("1e4_1e3_1e2@RW-A-B", "1e4_1e3_1e2@RW-A"))
check("同址覆盖", covers("1e4_1e3_1e2@RW-A", "1e4_1e3_1e2@RW-A"))
check("跨世界观不覆盖",
      not covers("1e4_1e3_1e2@RW-A", "1e4_1e3_1e2@TH-A"))
check("规范键归一化规模写法",
      canonical_key("1e4_1e3_1e2@RW-A")
      == canonical_key("10000_1000_100@RW-A"))
check("相对地址规范键等价于完整地址",
      canonical_key("A-B", "1e4_1e3_1e2@RW")
      == canonical_key("1e4_1e3_1e2@RW-A-B"))
check("非法地址规范键为空", canonical_key("坏地址") is None)

# ---------------- 5. 关系代数（配对/距离的统一入口） ----------------
_rel = relate("1e4_1e3_1e2@RW-A", "1e4_1e3_1e2@RW-A-B-1")
check("relate 查询方更粗为 contains 且带区间上界",
      _rel.kind == REL_CONTAINS and _rel.interval == (0.0, 1e4)
      and _rel.lcp_depth == 2 and _rel.chunk_ok)
_rel_in = relate("1e4_1e3_1e2@RW-A-B", "1e4_1e3_1e2@RW-A")
check("relate 查询方更细为 inside 且目标区域按 0 计",
      _rel_in.kind == REL_INSIDE and _rel_in.interval == (0.0, 0.0))
check("relate 同址为 same",
      relate("1e4_1e3_1e2@RW-A-B", "1e4_1e3_1e2@RW-A-B").kind == REL_SAME)
_rel_div = relate("1e4_1e3_1e2@RW-A-B-1", "1e4_1e3_1e2@RW-A-B-2")
check("relate 分歧级给出层级与平均边长",
      _rel_div.kind == REL_DIVERGENT and _rel_div.diverge_level == 2
      and _rel_div.interval == (100.0, 100.0) and _rel_div.lcp_depth == 3)
check("relate 私有名冲突 chunk_ok 为 False",
      not relate("1e4_1e3_1e2@A!RW-A", "1e4_1e3_1e2@B!RW-A").chunk_ok)
check("relate 带私有名对未声明方不兼容",
      not relate("1e4_1e3_1e2@A!RW-A", "1e4_1e3_1e2@RW-A").chunk_ok)
check("relate 相同私有名兼容",
      relate("1e4_1e3_1e2@A!RW-A", "1e4_1e3_1e2@A!RW-A-B").chunk_ok)
check("relate 跨世界观 world_mismatch 且不可达",
      relate("1e4_1e3_1e2@RW-A", "1e4_1e3_1e2@TH-A").kind == REL_WORLD_MISMATCH
      and relate("1e4_1e3_1e2@RW-A", "1e4_1e3_1e2@TH-A").interval is None)
check("relate 坏地址 invalid",
      relate("坏地址", "RW").kind == REL_INVALID)

# ---------------- 6. 紧凑地址描述 ----------------
_compact = format_addr_compact("1e4_1e3_1e2@RW-CN1")
check("紧凑描述含级别标签与定位精度",
      "世界观 RW" in _compact and "一级地域 CN1" in _compact
      and "二级地域" not in _compact and "定位精度" in _compact)
_compact_full = format_addr_compact("1e4_1e3_1e2@RW-CN1-East-3")
check("三级地址紧凑描述逐级列出",
      all(s in _compact_full for s in ("世界观 RW", "一级地域 CN1",
                                       "二级地域 East", "三级地域 3")))
_pure_world = format_addr_compact("RW")
check("纯世界观紧凑描述无精度段",
      "世界观 RW" in _pure_world and "定位精度" not in _pure_world)
check("坏地址紧凑描述回退原文", format_addr_compact("坏地址") == "坏地址")

# ---------------- 结论 ----------------
_log.flush()
sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__
if failures:
    print(f"[check_address_model] FAILED {len(failures)}/{total}: {failures}")
else:
    print(f"[check_address_model] PASSED {total}/{total}")
print(f"[check_address_model] report: {_report_path}")
sys.exit(1 if failures else 0)
