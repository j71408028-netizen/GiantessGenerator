# -*- coding: utf-8 -*-
"""档案派生层文本化自检：作息/空间偏好的可读结论、实时提示与行为包覆写。

用法：``python tests/check_profile_text.py``
只覆盖纯逻辑，不建窗口、不联网。断言语义序关系与降级路径（空样本缄默、
稀疏样本"初步观察"、实时提示的活跃/休整分档），不锁具体数值——调参不应误报。
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

# 2026-10-19 为周一；_NOW 为周六（含周末分档断言）
_MON = datetime.datetime(2026, 10, 19, 0, 0, 0)
_NOW = _MON + datetime.timedelta(days=5, hours=12)
_HOT = "1e4_1e3_1e2@*!TH-East-1"
_COLD = "1e4_1e3_1e2@*!TH-West-1"


def _rows(n, at, step=1.0, addr="", env=None):
    return [EvolutionRecord(changed_at=at.isoformat(), step=step,
                            intrusion=1.0, destruction=1.0, casualties=0.0,
                            source="t", position=addr, env_factor=env)
            for _ in range(n)]


# ---------------- 1. 作息历史结论 ----------------
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
check("周末偏活跃的历史结论", "周末的强度明显更高" in r_text, r_text)
check("成熟样本不用初步观察措辞", "初步观察" not in r_text and "已较稳定" in r_text)
check("作息结论确定性", r_text == describe_rhythm(_weekend_rows, now=_NOW))

r_sparse = describe_rhythm(_rows(5, _NOW), now=_NOW)
check("稀疏样本降级为初步观察", "初步观察" in r_sparse, r_sparse)

# ---------------- 2. 作息实时提示 ----------------
# 周三 12-14 时大步进活动 + 凌晨小步进活动 → 午后活跃 / 凌晨休整
_hint_rows = (_rows(30, _MON + datetime.timedelta(days=2, hours=13), step=3.0)
              + _rows(10, _MON + datetime.timedelta(days=2, hours=3), step=0.2))
_wed_active = datetime.datetime(2026, 10, 21, 13, 0, 0)
_wed_rest = datetime.datetime(2026, 10, 21, 3, 30, 0)
h_active = rhythm_now_hint(_hint_rows, now=_wed_active)
h_rest = rhythm_now_hint(_hint_rows, now=_wed_rest)
check("活跃期提示", "活跃" in h_active and "周三 12-14 时" in h_active, h_active)
check("休整期提示", "歇息" in h_rest, h_rest)
check("稀疏样本实时提示缄默",
      rhythm_now_hint(_rows(5, _wed_active), now=_wed_active) == "")
check("平常时段不产生提示",
      rhythm_now_hint(_hint_rows, now=datetime.datetime(2026, 10, 21, 7, 0)) == "")

# ---------------- 3. 空间偏好历史结论 ----------------
_spatial_rows = _rows(30, _NOW, addr=_HOT) + _rows(10, _NOW, addr=_COLD)
_env_rows = _rows(12, _NOW, addr=_HOT, step=2.0, env=0.2)
state = CharacterSnapshot(giantess_id="g1", name="t",
                          activity_regions=["1e4_1e3_1e2@*!TH-East"],
                          position=_HOT)
state.evolution = _spatial_rows + _env_rows
profile = build_spatial_profile(state, now=_NOW)

s_text = describe_spatial(profile, state.position)
check("常活动地带结论", "常活动地带" in s_text and "TH·East" in s_text, s_text)
check("成熟样本不用初步观察措辞", "初步看来" not in s_text, s_text)
check("环境偏好结论", "偏爱荒僻" in s_text, s_text)
check("手工活动区域列出", "指定的活动区域" in s_text, s_text)

empty_profile = build_spatial_profile(CharacterSnapshot(giantess_id="g2", name="t"),
                                      now=_NOW)
check("零证据空间结论缄默", describe_spatial(empty_profile, "") == "")

# ---------------- 4. 空间实时提示 ----------------
hint_hot = spatial_now_hint(profile, _HOT)
check("活动区域内提示", "活动区域" in hint_hot, hint_hot)
check("活动区域提示带环境描述", "荒僻" in hint_hot, hint_hot)
hint_cold = spatial_now_hint(profile, _COLD)
check("陌生地带提示", "陌生" in hint_cold, hint_cold)
check("空地址实时提示缄默", spatial_now_hint(profile, "") == "")
check("零证据实时提示缄默", spatial_now_hint(empty_profile, _HOT) == "")

# ---------------- 5. 合并入口与下游接入 ----------------
live = build_live_context(state, now=_wed_active)
check("合并入口含作息与空间两路", "活跃" in live and "活动区域" in live, live)
check("合并入口空态缄默",
      build_live_context(CharacterSnapshot(giantess_id="g3", name="t")) == "")

from services.chat.persona import build_character_persona  # noqa: E402
p_text = build_character_persona(state)
check("人设注入作息结论", "作息（从活动记录学到的习惯）" in p_text, p_text[:200])
p_empty = build_character_persona(CharacterSnapshot(giantess_id="g4", name="t"))
check("无样本人设不注作息", "作息（从活动记录学到的习惯）" not in p_empty)

from services.chat import build_chat_system_prompt, live_context_block  # noqa: E402
check("实时情境段非空", "惯常活跃时段" in live_context_block(state, now=_wed_active))
prompt = build_chat_system_prompt(state, ChatState(giantess_id="g1"),
                                  now=_wed_active)
check("聊天系统提示含实时情境", "惯常活跃时段" in prompt, prompt[:120])
check("聊天系统提示含作息结论", "作息（从活动记录学到的习惯）" in prompt)
prompt_empty = build_chat_system_prompt(
    CharacterSnapshot(giantess_id="g5", name="t"), ChatState(giantess_id="g5"))
check("空态提示不缺槽位", "{live_context}" not in prompt_empty
      and "{persona}" not in prompt_empty)

from services.character.archive_export import _build_bio_section, _build_profile_para  # noqa: E402
check("导出观察段空态不渲染",
      _build_profile_para(CharacterSnapshot(giantess_id="g6", name="t")) == "")
para = _build_profile_para(state)
check("导出观察段渲染为简介段落", para.startswith("<p class='bio-p'>")
      and "以上观察由内部模型从活动记录推得" in para)
check("导出观察段含两路结论", "作息（" in para and "偏爱荒僻" in para)
bio = _build_bio_section(state)
check("观察段落位于简介栏", "作息（" in bio and para in bio)
check("简介栏观察段转义纯文本", "<script>" not in bio)

# ---------------- 6. 行为包覆写（scope "profile"） ----------------
get_runtime().override("profile.describe_rhythm",
                       lambda evolution, now=None: "覆写作息")
check("行为包覆写生效", describe_rhythm([]) == "覆写作息")
get_runtime().override("profile.rhythm_now_hint",
                       lambda evolution, now=None: "覆写提示")
check("字符串键覆写生效", rhythm_now_hint([]) == "覆写提示")
reset_runtime()
check("重置后恢复默认", describe_rhythm([]) == "" and rhythm_now_hint(_hint_rows, now=_wed_active) != "覆写提示")

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
