# -*- coding: utf-8 -*-
"""世界包命名空间自检（P3b）：manifest worlds 声明、按维度合并素材、
设置保留与一致性警告。

用法：``python tests/check_world_pack_worlds.py``
全部在系统临时目录构造数据，不触碰 data/、不联网。
"""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_report_path = os.path.join(tempfile.mkdtemp(prefix="worlds_ns_"), "report.txt")
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


from core.address_model import valid_world_id  # noqa: E402
from core.models import Landmark  # noqa: E402
from persistence.landmark_repo import LandmarkRepo  # noqa: E402
from persistence.quip_repo import QuipRepo  # noqa: E402
from persistence.world_pack import (  # noqa: E402
    WorldPackManifest, WorldState, load_manifest_file, save_manifest_file,
)
from services.worlds.service import (  # noqa: E402
    WorldManager, pack_worlds_warnings,
)

# ---------------- 1. 地址维度校验 ----------------
check("维度名校验接受合法值",
      valid_world_id("TH") and valid_world_id("ea1_x"))
check("维度名校验拒绝非法值",
      not valid_world_id("") and not valid_world_id("a b")
      and not valid_world_id("x" * 49) and not valid_world_id("TH-1"))

# ---------------- 2. manifest worlds 字段 ----------------
m = WorldPackManifest(world_id="t1", name="t", worlds=["TH", "RW"])
m2 = WorldPackManifest.from_dict(json.loads(json.dumps(m.to_dict())))
check("worlds 序列化往返", m2.worlds == ["TH", "RW"])
check("旧清单缺 worlds 时默认为空", WorldPackManifest.from_dict(
    {"world_id": "t1", "name": "t"}).worlds == [])
check("合法 worlds 通过校验", m.validate() == [])

bad = WorldPackManifest(world_id="t1", name="t", worlds="TH")
check("worlds 非列表报错", any("worlds" in e for e in bad.validate()))
bad = WorldPackManifest(world_id="t1", name="t", worlds=["TH", "TH-1", "TH"])
errs = bad.validate()
check("worlds 非法名与重复声明报错",
      any("非法维度名" in e for e in errs) and any("重复" in e for e in errs))

ws = WorldState(active_manifest=m)
check("声明 worlds 的状态可查询", ws.declared_worlds() == {"TH", "RW"})
check("未声明 worlds 归一为空集",
      WorldState(active_manifest=WorldPackManifest(world_id="t1", name="t")
                 ).declared_worlds() == set())
check("未激活时声明集为空", WorldState().declared_worlds() == set())

# ---------------- 3. 素材合并（landmarks / quips） ----------------
_DATA = tempfile.mkdtemp(prefix="worlds_data_")
_ADDR_TH = "1e4_1e3_1e2@*!TH-A-1"
_ADDR_RW = "1e4_1e3_1e2@*!RW-B-2"


def _deploy_pack(worlds):
    """部署一个拥有 landmarks/quips 的包到临时数据目录，返回 world_id。"""
    wid = "tpkNS"
    root = os.path.join(_DATA, "worlds", wid)
    lm_dir = os.path.join(root, "landmarks")
    qp_dir = os.path.join(root, "quips")
    os.makedirs(lm_dir, exist_ok=True)
    os.makedirs(qp_dir, exist_ok=True)
    # 包内风格：p1 注册在 TH；f_dup 与自由目录同名（遮蔽检验）
    for style, addr in (("p1", _ADDR_TH), ("f_dup", _ADDR_RW)):
        with open(os.path.join(lm_dir, style), "w", encoding="utf-8") as f:
            json.dump([{"name": f"包内{style}", "size": 100,
                        "dimension": "vertical", "frequency": "common"}], f)
        with open(os.path.join(lm_dir, f"{style}.addr.json"), "w", encoding="utf-8") as f:
            json.dump({"address": addr}, f)
    with open(os.path.join(qp_dir, "q1"), "w", encoding="utf-8") as f:
        json.dump({}, f)
    with open(os.path.join(qp_dir, "q1.addr.json"), "w", encoding="utf-8") as f:
        json.dump({"address": _ADDR_TH}, f)
    manifest = {
        "world_id": wid, "name": "命名空间测试包", "version": "1.0",
        "resources": {"landmarks": ["p1", "f_dup"], "quips": ["q1"]},
    }
    if worlds is not None:
        manifest["worlds"] = worlds
    with open(os.path.join(root, "world.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f)
    return wid


def _deploy_free():
    """自由目录风格：f_in 注册在 TH（会被声明 TH 的包遮蔽）、f_out 注册在 RW、
    f_port 未注册、f_dup 与包同名。"""
    repo = LandmarkRepo(data_dir=_DATA)
    for style, addr in (("f_in", _ADDR_TH), ("f_out", _ADDR_RW),
                        ("f_port", ""), ("f_dup", _ADDR_RW)):
        repo.save(style, [Landmark(f"自由{style}", 50, "vertical", "common")])
        if addr:
            repo.save_style_address(style, addr)
    qrepo = QuipRepo(data_dir=_DATA)
    qrepo.save("q_free", {})
    qrepo.save_style_address("q_free", _ADDR_RW)
    return repo, qrepo


_free_repo, _free_qrepo = _deploy_free()
_mgr = WorldManager(_DATA)

# 3a. 未激活包：全部来自自由目录
check("未激活时风格全集为自由目录",
      set(_free_repo.get_styles()) == {"f_in", "f_out", "f_port", "f_dup"})

# 3b. 旧式包（未声明 worlds）：整类型接管
_wid_old = _deploy_pack(worlds=None)
_mgr.load_active(_wid_old)
_old_repo = LandmarkRepo(data_dir=_DATA, world_state=_mgr.world_state)
check("未声明 worlds 的包整类型接管",
      set(_old_repo.get_styles()) == {"p1", "f_dup"})
check("接管下包外风格不可见（读包目录为空）", _old_repo.load("f_out") == [])
check("接管下风格注册地址读包内",
      _old_repo.load_style_address("p1") == _ADDR_TH)
_mgr.world_state.set_active(None)

# 3c. 声明 worlds 的包：按维度合并
_wid = _deploy_pack(worlds=["TH"])
_mgr.load_active(_wid)
_repo = LandmarkRepo(data_dir=_DATA, world_state=_mgr.world_state)
check("声明 worlds 时合并自由目录其它维度素材",
      set(_repo.get_styles()) == {"p1", "f_dup", "f_out", "f_port"},
      str(sorted(_repo.get_styles())))
check("声明世界内的自由风格被遮蔽", "f_in" not in _repo.get_styles())
check("同名冲突时包优先（自由 f_dup 被遮蔽）",
      [lm.name for lm in _repo.load("f_dup")] == ["包内f_dup"])
check("合并的自由风格读自由目录",
      [lm.name for lm in _repo.load("f_out")] == ["自由f_out"])
check("包内风格读包目录", [lm.name for lm in _repo.load("p1")] == ["包内p1"])
check("合并的自由风格注册地址读自由目录",
      _repo.load_style_address("f_out") == _ADDR_RW)
check("未注册地址的自由风格保持可用（portable）",
      _repo.load_style_address("f_port") == "" and "f_port" in _repo.get_styles())
check("合并的自由风格不在包目录时读自由目录",
      _repo._filepath("f_port").startswith(os.path.join(_DATA, "packs")))

_qrepo = QuipRepo(data_dir=_DATA, world_state=_mgr.world_state)
check("描述风格同样按维度合并",
      set(_qrepo.get_styles()) == {"q1", "q_free"},
      str(sorted(_qrepo.get_styles())))
check("描述风格注册地址路由正确",
      _qrepo.load_style_address("q1") == _ADDR_TH
      and _qrepo.load_style_address("q_free") == _ADDR_RW)

# ---------------- 4. 激活时的选取保留 ----------------
_settings = {"selected_styles": ["f_in", "f_out", "f_port", "已删除"],
             "selected_quip_styles": ["q_free"]}
_mgr.apply_world_settings(_settings)
check("包风格全选且保留未遮蔽的已选自由风格",
      _settings["selected_styles"] == ["p1", "f_dup", "f_out", "f_port"],
      str(_settings["selected_styles"]))
check("描述风格选取同样保留",
      _settings["selected_quip_styles"] == ["q1", "q_free"])

# 重复应用幂等（启动恢复会再次调用）
_mgr.apply_world_settings(_settings)
check("选取合并幂等",
      _settings["selected_styles"] == ["p1", "f_dup", "f_out", "f_port"])

# ---------------- 5. 一致性警告（先 warning） ----------------
_pack_root = os.path.join(_DATA, "worlds", _wid)
# 包内 p1 在 TH、f_dup 在 RW，包声明只含 TH → f_dup 应告警
_warns = pack_worlds_warnings(_mgr.world_state.manifest, _pack_root)
check("注册地址在声明世界外给出警告",
      len(_warns) == 1 and "f_dup" in _warns[0] and "RW" in _warns[0],
      str(_warns))
check("未声明 worlds 的包不检查",
      pack_worlds_warnings(WorldPackManifest(world_id="t", name="t"), _pack_root) == [])
check("列表信息携带 worlds 与警告",
      any(p.get("worlds") == ["TH"] and p.get("warnings")
          for p in _mgr.list_packs() if p["world_id"] == _wid))

# ---------------- 6. 从当前世界创建：worlds 派生 ----------------
_mgr2 = WorldManager(_DATA)          # 未激活状态，读自由目录
_repo2 = LandmarkRepo(data_dir=_DATA)
_created = _mgr2.create_from_current(
    "tpk_create", "派生测试", {},
    landmark_repo=_repo2,
    selected_resources={"landmarks": ["f_in", "f_out", "f_port"]})
check("create_from_current 派生 worlds 并集",
      _created.worlds == ["RW", "TH"], str(_created.worlds))
_reloaded = load_manifest_file(os.path.join(
    _DATA, "worlds", "tpk_create", "world.json"))
check("派生 worlds 持久化且清单可复载", _reloaded.worlds == ["RW", "TH"])

# 全部未注册地址时不声明 worlds
for style in ("f_in", "f_out"):
    _repo2.save_style_address(style, "")
_created2 = _mgr2.create_from_current(
    "tpk_create2", "无地址派生", {},
    landmark_repo=_repo2,
    selected_resources={"landmarks": ["f_in", "f_out"]})
check("全部未注册地址时 worlds 保持为空",
      _created2.worlds == [] and _created2.validate() == [])

# ---------------- 7. 导出的描述文件能被读回 ----------------
_qrepo = QuipRepo(data_dir=_DATA)
_qrepo.save("q_rt", {"small": {(1, 1): [{"text": "包内正文", "style": "q_rt",
                                          "step": 0.1, "summary": "摘要"}]}})
_mgr2.create_from_current(
    "tpk_quip", "描述往返", {},
    landmark_repo=_repo2, quip_repo=_qrepo,
    selected_resources={"quips": ["q_rt"]})
_pack_quip = os.path.join(_DATA, "worlds", "tpk_quip", "quips", "q_rt")
check("包内描述主文件不带后缀", os.path.isfile(_pack_quip), _pack_quip)
with open(_pack_quip, encoding="utf-8") as _f:
    _back = json.load(_f)
check("包内描述是落盘形状（行数组 + _meta）",
      isinstance(_back.get("small"), list) and "_meta" in _back, sorted(_back))
_quip_tmp = tempfile.mkdtemp(prefix="quip_pack_")
os.makedirs(os.path.join(_quip_tmp, "packs", "quips"))
shutil.copy2(_pack_quip, os.path.join(_quip_tmp, "packs", "quips", "q_rt"))
_reloaded_quip = QuipRepo(data_dir=_quip_tmp).load("q_rt")
check("包内描述能被 QuipRepo 读回",
      _reloaded_quip["small"][(1, 1)][0]["text"] == "包内正文"
      and _reloaded_quip["small"][(1, 1)][0]["summary"] == "摘要",
      _reloaded_quip)
shutil.move(_quip_tmp, tempfile.mkdtemp(prefix="quip_pack_retired_"))

# ---------------- 清理 ----------------
shutil.move(_DATA, tempfile.mkdtemp(prefix="worlds_retired_"))
_log.flush()
sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__
if failures:
    print(f"[check_world_pack_worlds] FAILED {len(failures)}/{total}: {failures}")
else:
    print(f"[check_world_pack_worlds] PASSED {total}/{total}")
print(f"[check_world_pack_worlds] report: {_report_path}")
sys.exit(1 if failures else 0)
