# -*- coding: utf-8 -*-
"""世界履历（world_history）自检：记录 / 兼容 / 只写不读行为锁。

用法：``python tests/check_world_history.py``
只跑纯逻辑，不建窗口、不联网；档案读写走系统临时目录，不碰 data/。

行为锁（对应 .workbuddy/behavior-pack-baseline-v2.md 阶段 C）：
- 创建时写 born 条目；旅居更新只推进 last_at，新世界包追加 born=false 条目；
- 无激活世界包时不写；
- 履历不改写 position / 演化表行 / 离线明细（与地址、学习层正交）；
- 始终记录（只写）：world_history_enabled 关闭不影响写入，
  只影响导出读取（关闭时导出无 world_history，出身仍导出）；
- 旧档（无字段）回退空列表，坏条目读档时丢弃。
"""

import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_report_path = os.path.join(
    tempfile.mkdtemp(prefix="world_history_"), "report.txt")
_log = open(_report_path, "w", encoding="utf-8")
sys.stdout = _log
sys.stderr = _log

failures = []
total = 0


def check(name, ok, extra=""):
    global total
    total += 1
    print(("  OK   " if ok else "  FAIL ") + name
          + (f"  <{extra}>" if extra and not ok else ""))
    if not ok:
        failures.append(name)


from core.models import CharacterSnapshot, ModelTuning, Personality  # noqa: E402
from persistence.character_repo import CharacterRepo  # noqa: E402
from services.exploration.character import CharacterAssembler  # noqa: E402
from services.exploration.export import build_export_card_from_state  # noqa: E402

_WORLD_A = SimpleNamespace(
    active=True,
    manifest=SimpleNamespace(world_id="wA", name="世界甲", version="1.0"),
)
_WORLD_B = SimpleNamespace(
    active=True,
    manifest=SimpleNamespace(world_id="wB", name="世界乙", version="3.2"),
)


def _core(name="履历娘"):
    return {
        "name": name, "nick": "", "original_height": 1.6,
        "height": 30.0, "body_parts": {"身高": 30.0},
        "personality_obj": Personality(
            name="温和", init_intrusion=1.0, step_intrusion=0.1,
            init_destruction=1.0, step_destruction=0.1,
            sensitivity=1.0, gravity=0.5),
        "base_intrusion": 1.0, "base_destruction": 1.0,
        "greed": 0.0, "will": False, "will_status": None,
        "selected_tags": [], "intro_hidden": "", "intro_visible": "",
        "birthday": "", "uploaded_image": None,
        "total_casualties": 0.0, "position": "1e4_1e3@*!TH-East",
    }


def _assembler(world_state, data_dir=None):
    repo = CharacterRepo(data_dir or tempfile.mkdtemp(prefix="history_repo_"))
    return repo, CharacterAssembler(repo, {}, world_state=world_state)

# ---------------- 1. 创建时 born 条目 ----------------

repo, assembler = _assembler(_WORLD_A)
state = assembler.character_from_core_or_report(_core())
check("创建写入 born 条目", len(state.world_history) == 1 and (
    state.world_history[0]["pack_id"] == "wA"
    and state.world_history[0]["name"] == "世界甲"
    and state.world_history[0]["version"] == "1.0"
    and state.world_history[0]["born"] is True
    and state.world_history[0]["first_at"] == state.world_history[0]["last_at"]),
    str(state.world_history))
check("出身记录与 born 条目同源", (
    state.model_tuning.origin_world["pack_id"]
    == state.world_history[0]["pack_id"]))

repo_none, assembler_none = _assembler(None)
state_none = assembler_none.character_from_core_or_report(_core(name="无包娘"))
check("无激活包时不写履历", state_none.world_history == [])

# ---------------- 2. 旅居更新：只推进 last_at / 追加新条目 ----------------

before = dict(state.world_history[0])
updated = assembler.record_world_history(state)
check("同包旅居只更新既有条目", (
    updated and len(state.world_history) == 1
    and state.world_history[0]["first_at"] == before["first_at"]
    and state.world_history[0]["last_at"] >= before["last_at"]
    and state.world_history[0]["born"] is True))

repo_b, assembler_b = _assembler(_WORLD_B)
state._world_switch = assembler_b  # 换包后旅居 → 追加新条目
assembler_b.record_world_history(state)
check("换包旅居追加 born=false 条目", (
    len(state.world_history) == 2
    and state.world_history[1]["pack_id"] == "wB"
    and state.world_history[1]["born"] is False))
check("未激活包旅居不写",
      CharacterAssembler(repo, {}, world_state=None)
      .record_world_history(state) is False
      and len(state.world_history) == 2)
_inactive = SimpleNamespace(active=False, manifest=_WORLD_A.manifest)
check("停用状态的包不写履历",
      CharacterAssembler(repo, {}, world_state=_inactive)
      .record_world_history(state) is False
      and len(state.world_history) == 2)

# ---------------- 3. 履历不改写 position / 演化表 ----------------

snapshot_before = (len(state.evolution), state.position,
                   list(state.offline_details),
                   state.evolution[-1].changed_at if state.evolution else "")
assembler.record_world_history(state)
assembler_b.record_world_history(state)
snapshot_after = (len(state.evolution), state.position,
                  list(state.offline_details),
                  state.evolution[-1].changed_at if state.evolution else "")
check("履历记录不改写演化表 / position / 明细",
      snapshot_before == snapshot_after)

# ---------------- 4. 只写不读：开关不影响记录，只影响导出 ----------------

repo.save(state)
card_on = build_export_card_from_state(
    state, repo, world_history_enabled=True)
card_off = build_export_card_from_state(
    state, repo, world_history_enabled=False)
check("开启时导出携带履历与出身", (
    card_on.get("origin_world") == state.model_tuning.origin_world
    and card_on.get("world_history") == state.world_history))
check("关闭时导出读不到履历、出身不受影响", (
    "world_history" not in card_off
    and card_off.get("origin_world") == state.model_tuning.origin_world))
check("关闭开关不回撤已记录条目", len(state.world_history) == 2)

# ---------------- 5. 持久化与旧档兼容 ----------------

reloaded = CharacterRepo(os.path.dirname(repo.states_dir)).load(
    state.giantess_id)
check("读档还原履历", reloaded.world_history == state.world_history)

from dataclasses import asdict  # noqa: E402
import json  # noqa: E402
saved = json.loads(json.dumps(asdict(state)))
del saved["world_history"]
legacy = CharacterSnapshot.from_dict(saved)
check("旧档无履历字段回退空列表", legacy.world_history == [])
saved["world_history"] = [{"pack_id": "wA"}, "坏条目", 42]
dirty = CharacterSnapshot.from_dict(saved)
check("读档丢弃非字典履历条目", (
    len(dirty.world_history) == 1
    and dirty.world_history[0]["pack_id"] == "wA"))

# ---------------- 结论 ----------------

_log.flush()
sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__
if failures:
    print(f"[check_world_history] FAILED {len(failures)}/{total}: {failures}")
else:
    print(f"[check_world_history] PASSED {total}/{total}")
print(f"[check_world_history] report: {_report_path}")
sys.exit(1 if failures else 0)
