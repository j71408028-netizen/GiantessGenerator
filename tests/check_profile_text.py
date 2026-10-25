# -*- coding: utf-8 -*-
"""档案派生层文本化自检：只锁结构契约与下游接线，不锁具体措辞。

用法：``python tests/check_profile_text.py``
只覆盖纯逻辑，不建窗口、不联网。

本模块的文案尚未定稿，因此这里刻意不匹配任何具体字句，只验证：

- 空态缄默 / 有证据时产出非空（消费方据此整行省略的契约）；
- 时钟注入生效（实时提示随注入的 now 分档，不同时段结论不同）；
- 四个行为包钩子的字面量字符串键可覆写并可在重置后复原；
- persona / 聊天系统提示词 / 档案导出三处下游确实接上了这两路结论。

调文案、改措辞不应让本自检变红；改动出口函数的签名、空态语义或
钩子键才应该变红。
"""

import datetime
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_report_path = os.path.join(tempfile.mkdtemp(prefix="profile_text_"), "report.txt")
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


from core.behavior_runtime import get_runtime, reset_runtime  # noqa: E402
from core.models import CharacterSnapshot, ChatState, EvolutionRecord  # noqa: E402
from services.character.profile_text import (  # noqa: E402
    build_live_context, describe_rhythm, describe_spatial,
    rhythm_now_hint, spatial_now_hint,
)
from services.character.spatial import build_spatial_profile  # noqa: E402

# 2026-10-19 为周一；_NOW 为周六（含周末分档样本）
_MON = datetime.datetime(2026, 10, 19, 0, 0, 0)
_NOW = _MON + datetime.timedelta(days=5, hours=12)
_HOT = "1e4_1e3_1e2@*!TH-East-1"
_COLD = "1e4_1e3_1e2@*!TH-West-1"


def _rows(n, at, step=1.0, addr="", env=None):
    return [EvolutionRecord(changed_at=at.isoformat(), step=step,
                            intrusion=1.0, destruction=1.0, casualties=0.0,
                            source="t", position=addr, env_factor=env)
            for _ in range(n)]


# ---------------- 1. 作息历史结论：空态与产出契约 ----------------
check("空演化表作息缄默", describe_rhythm([]) == "")

# 作息信号来自步进强度差异（不是频次）：周末大步进、工作日小步进
_weekend_rows = []
for day in (5, 6):   # 周六 / 周日
    for hour in (8, 10, 12, 14, 16, 18, 20):
        _weekend_rows += _rows(10, _MON + datetime.timedelta(days=day, hours=hour),
                               step=3.0)
for day in (0, 1, 2, 3, 4):
    for hour in (0, 4, 22):
        _weekend_rows += _rows(3, _MON + datetime.timedelta(days=day, hours=hour),
                               step=0.2)
r_text = describe_rhythm(_weekend_rows, now=_NOW)
check("有样本时作息结论非空", isinstance(r_text, str) and r_text.strip() != "")
check("作息结论确定性", r_text == describe_rhythm(_weekend_rows, now=_NOW))

# 稀疏样本只要求不抛异常、仍产出可用结论（成熟度措辞未定稿，不做断言）
r_sparse = describe_rhythm(_rows(5, _NOW), now=_NOW)
check("稀疏样本作息结论非空", isinstance(r_sparse, str) and r_sparse.strip() != "")
check("稀疏与成熟样本结论可区分", r_sparse != r_text)

# ---------------- 2. 作息实时提示：时钟注入与分档 ----------------
# 周三 12-14 时大步进活动 + 凌晨小步进活动 → 午后活跃 / 凌晨休整
_hint_rows = (_rows(30, _MON + datetime.timedelta(days=2, hours=13), step=3.0)
              + _rows(10, _MON + datetime.timedelta(days=2, hours=3), step=0.2))
_wed_active = datetime.datetime(2026, 10, 21, 13, 0, 0)
_wed_rest = datetime.datetime(2026, 10, 21, 3, 30, 0)
_wed_neutral = datetime.datetime(2026, 10, 21, 7, 0, 0)
h_active = rhythm_now_hint(_hint_rows, now=_wed_active)
h_rest = rhythm_now_hint(_hint_rows, now=_wed_rest)
check("活跃期产生提示", h_active.strip() != "", h_active)
check("休整期产生提示", h_rest.strip() != "", h_rest)
check("活跃与休整提示不同文", h_active != h_rest)
check("稀疏样本实时提示缄默",
      rhythm_now_hint(_rows(5, _wed_active), now=_wed_active) == "")
check("平常时段不产生提示", rhythm_now_hint(_hint_rows, now=_wed_neutral) == "")

# ---------------- 3. 空间偏好结论与实时提示 ----------------
_spatial_rows = _rows(30, _NOW, addr=_HOT) + _rows(10, _NOW, addr=_COLD)
_env_rows = _rows(12, _NOW, addr=_HOT, step=2.0, env=0.2)
state = CharacterSnapshot(giantess_id="g1", name="t",
                          activity_regions=["1e4_1e3_1e2@*!TH-East"],
                          position=_HOT)
state.evolution = _spatial_rows + _env_rows
profile = build_spatial_profile(state, now=_NOW)

s_text = describe_spatial(profile, state.position)
check("有证据时空间结论非空", s_text.strip() != "", s_text)
check("空间结论含高频地名", "TH" in s_text and "East" in s_text, s_text)
check("空间结论含手工活动区域", any(r in s_text for r in state.activity_regions), s_text)

empty_profile = build_spatial_profile(CharacterSnapshot(giantess_id="g2", name="t"),
                                      now=_NOW)
check("零证据空间结论缄默", describe_spatial(empty_profile, "") == "")

hint_hot = spatial_now_hint(profile, _HOT)
check("活动区域内提示非空", hint_hot.strip() != "", hint_hot)
check("陌生地带提示与活动区域不同文",
      spatial_now_hint(profile, _COLD) != hint_hot)
check("空地址实时提示缄默", spatial_now_hint(profile, "") == "")
check("零证据实时提示缄默", spatial_now_hint(empty_profile, _HOT) == "")

# ---------------- 4. 合并入口 ----------------
live = build_live_context(state, now=_wed_active)
check("合并入口含两路提示", live.strip() != "" and "\n" in live, live)
check("合并入口保留各路子串",
      rhythm_now_hint(state.evolution, now=_wed_active) in live
      and spatial_now_hint(build_spatial_profile(state, now=_wed_active),
                           state.position) in live, live)
check("合并入口空态缄默",
      build_live_context(CharacterSnapshot(giantess_id="g3", name="t")) == "")

# ---------------- 5. 下游接线：人设 / 系统提示词 / 档案导出 ----------------
from services.chat.persona import build_character_persona  # noqa: E402
p_text = build_character_persona(state)
check("人设注入作息结论", describe_rhythm(state.evolution) in p_text, p_text[:200])
check("人设注入空间结论", describe_spatial(build_spatial_profile(state),
                                          state.position) in p_text, p_text[:200])
p_empty = build_character_persona(CharacterSnapshot(giantess_id="g4", name="t"))
check("无样本人设可生成", isinstance(p_empty, str) and p_empty.strip() != "")

from services.chat import build_chat_system_prompt, live_context_block  # noqa: E402
ctx_block = live_context_block(state, now=_wed_active)
check("实时情境段非空", ctx_block.strip() != "")
prompt = build_chat_system_prompt(state, ChatState(giantess_id="g1"),
                                  now=_wed_active)
check("聊天系统提示含实时情境", ctx_block in prompt, prompt[:120])
check("聊天系统提示已替换占位符",
      "{live_context}" not in prompt and "{persona}" not in prompt)
prompt_empty = build_chat_system_prompt(
    CharacterSnapshot(giantess_id="g5", name="t"), ChatState(giantess_id="g5"))
check("空态提示不缺槽位", "{live_context}" not in prompt_empty
      and "{persona}" not in prompt_empty)

from services.character.archive_export import _build_bio_section, _build_profile_para  # noqa: E402
check("导出观察段空态不渲染",
      _build_profile_para(CharacterSnapshot(giantess_id="g6", name="t")) == "")
para = _build_profile_para(state)
check("导出观察段渲染为简介段落",
      para.startswith("<p class='bio-p'>") and para.endswith("</p>"), para[:80])
check("导出观察段无换行（已连缀成段）", "\n" not in para)
bio = _build_bio_section(state)
check("观察段落位于简介栏", para in bio)
check("简介栏观察段转义纯文本", "<script>" not in bio)

# ---------------- 6. 行为包覆写（scope "profile"，字面量键） ----------------
get_runtime().override("profile.describe_rhythm",
                       lambda evolution, now=None: "覆写作息")
check("覆写 profile.describe_rhythm", describe_rhythm([]) == "覆写作息")
get_runtime().override("profile.rhythm_now_hint",
                       lambda evolution, now=None: "覆写提示")
check("覆写 profile.rhythm_now_hint", rhythm_now_hint([]) == "覆写提示")
get_runtime().override("profile.describe_spatial",
                       lambda profile, position="": "覆写空间")
check("覆写 profile.describe_spatial", describe_spatial(empty_profile, "") == "覆写空间")
get_runtime().override("profile.spatial_now_hint",
                       lambda profile, position="": "覆写空间提示")
check("覆写 profile.spatial_now_hint", spatial_now_hint(empty_profile, _HOT) == "覆写空间提示")
reset_runtime()
check("重置后恢复默认",
      describe_rhythm([]) == "" and rhythm_now_hint(_hint_rows, now=_wed_active) != "覆写提示"
      and describe_spatial(empty_profile, "") == ""
      and spatial_now_hint(empty_profile, _HOT) == "")

# ---------------- 结论 ----------------
_log.flush()
sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__
if failures:
    print(f"[check_profile_text] FAILED {len(failures)}/{total}: {failures}")
else:
    print(f"[check_profile_text] PASSED {total}/{total}")
print(f"[check_profile_text] report: {_report_path}")
sys.exit(1 if failures else 0)
