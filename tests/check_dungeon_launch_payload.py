"""副本启动载荷的序列化往返检查（无 GUI）。

用法：``python tests/check_dungeon_launch_payload.py``

副本会话已搬进独立子进程运行，构造参数与结果对象都要跨进程往返。
本检查在纯数据层面锁住这条线：

- 三个调用方形态（探索模式带角色 / 探索模式无角色 / 挑战模式）的构造参数
  序列化后不含宿主侧概念（parent / host / gui），且全为 JSON 安全数据；
- 反序列化还原出等价的 kwargs（dataclass 字段逐一相等）；
- ``SessionResult`` 四种原因的往返；
- 坏结果数据按启动失败兜底。

退出码：0 = 通过；1 = 存在断言失败。
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.models import BodyPreset, Personality
from dungeon.window.launch_payload import (deserialize_launch, result_from_dict,
                                           result_to_dict, serialize_launch)
from dungeon.window.result import (REASON_ALREADY_RUNNING, REASON_ENTRY_CANCELLED,
                                   REASON_LAUNCH_FAILED, REASON_SESSION_ENDED,
                                   SessionResult)

_checked = 0


def check(cond, message):
    global _checked
    _checked += 1
    if not cond:
        print(f"[FAIL] {message}")
        raise SystemExit(1)


def _assert_roundtrip(kwargs, label):
    """序列化 → JSON 落盘读回 → 反序列化，并返回还原后的 kwargs。"""
    payload = serialize_launch(kwargs)
    for key in ("parent", "host", "gui"):
        check(key not in payload, f"{label}: 载荷不应含宿主侧参数 {key}")
    try:
        json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        check(False, f"{label}: 载荷不是 JSON 安全数据: {e}")
    restored = deserialize_launch(json.loads(json.dumps(payload)))
    check(set(restored) == set(payload), f"{label}: 还原后的参数键集合一致")
    return restored


def main():
    personality = Personality(
        name="测试性格", init_intrusion=1.5, step_intrusion=0.3,
        init_destruction=0.8, step_destruction=0.2, sensitivity=2.0,
        gravity=0.5, description="用于测试", skip_base_prob=3.0)
    preset = BodyPreset(name="标准", leg_ratio=0.5)
    character = _make_character(personality)

    # ---------- 形态一：探索模式带角色（exp_frame / mini 的主要形态） ----------
    kwargs = {
        "parent": object(),          # 宿主侧：必须被剔除
        "name": "测试娘", "nick": "小测",
        "height": 32.5, "personality": personality, "preset": preset,
        "greed": 2, "original_height": 1.62,
        "intro_hidden": "隐藏介绍", "intro_visible": "可见介绍",
        "tags": ["高挑", "温柔"],
        "uploaded_image": "data/archives/测试娘/avatar/avatar.png",
        "scenario_config": None, "scenario_repo": object(),   # 宿主侧：剔除
        "merged_landmarks": {"风格A": [{"name": "地标1", "size": 3.0}]},
        "merged_quips": {"风格A": ["台词1"]},
        "selected_styles": ["风格A"], "selected_quip_styles": ["风格A"],
        "detail_pools": {"细节池": ["条目"]},
        "ai_config": {"provider": "openai", "api_key": "sk-test",
                      "url": "", "model": "gpt-test"},
        "is_replay": False, "replay_data": None,
        "dungeon_font": "Microsoft YaHei",
        "body_parts": {"head": 1.0},
        "character": character, "character_repo": object(),
        "gui": object(),             # 宿主侧：剔除
        "settings": {"dungeon_voice_enabled": True, "story_recent_count": 20},
        "mode": "explore",
        "scenario_ids": ["scenario-a", "scenario-b"],
        "host": object(),            # 宿主侧：剔除
    }
    restored = _assert_roundtrip(kwargs, "探索模式带角色")
    check(restored["personality"] == personality, "探索模式: 性格往返相等")
    check(restored["preset"] == preset, "探索模式: 身材往返相等")
    check(restored["character"] == character, "探索模式: 角色往返相等")
    check(restored["character"].personality == personality,
          "探索模式: 角色内嵌性格往返相等")
    check(isinstance(restored["character"].evolution[0],
                     type(character.evolution[0])),
          "探索模式: 角色演化表还原为记录对象")
    for key in ("name", "height", "greed", "tags", "merged_landmarks",
                "merged_quips", "detail_pools", "ai_config", "settings",
                "scenario_ids", "mode", "dungeon_font", "body_parts",
                "uploaded_image"):
        check(restored[key] == kwargs[key], f"探索模式: {key} 往返相等")

    # ---------- 形态二：探索模式无角色（参数面板直接进副本） ----------
    kwargs2 = {
        "name": "临时娘", "nick": "", "height": 1.7,
        "personality": personality, "preset": None, "greed": 0,
        "original_height": 1.7, "intro_hidden": "", "intro_visible": "",
        "tags": [], "uploaded_image": None,
        "scenario_config": None, "scenario_repo": object(),
        "merged_landmarks": {}, "merged_quips": {},
        "selected_styles": [], "selected_quip_styles": [],
        "detail_pools": {}, "ai_config": {}, "is_replay": False,
        "replay_data": None, "body_parts": {}, "character": None,
        "character_repo": object(), "settings": {}, "mode": "explore",
        "scenario_ids": ["scenario-a"],
    }
    restored2 = _assert_roundtrip(kwargs2, "探索模式无角色")
    check(restored2["character"] is None, "无角色形态: character 保持 None")
    check(restored2["preset"] is None, "无角色形态: preset 保持 None")

    # ---------- 形态三：挑战模式（直接给方案配置，无入口阶段） ----------
    kwargs3 = {
        "parent": object(), "name": "挑战娘", "nick": "",
        "personality": personality, "preset": preset,
        "original_height": 1.6, "intro_hidden": "", "intro_visible": "",
        "tags": [], "uploaded_image": None,
        "scenario_config": {"triggers": [], "chapters": [],
                            "coupling_level": "velum"},
        "scenario_repo": object(), "merged_landmarks": {},
        "merged_quips": {}, "selected_styles": ["挑战风格"],
        "selected_quip_styles": [], "detail_pools": {}, "height": 20.0,
        "ai_config": {}, "greed": 0, "is_replay": False, "replay_data": None,
        "scenario_id": "challenge-pack", "dungeon_font": "",
        "body_parts": {}, "character": None, "character_repo": object(),
        "gui": object(), "mode": "challenge",
    }
    restored3 = _assert_roundtrip(kwargs3, "挑战模式")
    check(restored3["scenario_config"] == kwargs3["scenario_config"],
          "挑战模式: 方案配置往返相等")
    check(restored3["mode"] == "challenge", "挑战模式: mode 往返相等")

    # ---------- 结果对象往返 ----------
    samples = [
        SessionResult(REASON_SESSION_ENDED, launch_choice="scenario-a",
                      scenario_id="scenario-a", ended=True,
                      replay_path="data/archives/x/回放/a.replay.json",
                      report_path="data/archives/x/报告/a.md",
                      frames_rendered=1234),
        SessionResult(REASON_ENTRY_CANCELLED, launch_choice=None),
        SessionResult(REASON_LAUNCH_FAILED,
                      launch_error="行动点数不足", launch_choice="scenario-b"),
        SessionResult(REASON_ALREADY_RUNNING),
    ]
    for result in samples:
        data = result_to_dict(result)
        try:
            json.dumps(data, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            check(False, f"结果对象不是 JSON 安全数据: {e}")
        back = result_from_dict(json.loads(json.dumps(data)))
        for name in SessionResult.__slots__:
            check(getattr(back, name) == getattr(result, name),
                  f"结果往返 {result.reason}.{name}")

    # ---------- 坏数据兜底 ----------
    fallback = result_from_dict(None)
    check(fallback.failed and "没有返回结果" in fallback.launch_error,
          "空结果按启动失败兜底")
    fallback2 = result_from_dict({"nonsense": True})
    check(fallback2.failed, "缺 reason 的结果按启动失败兜底")

    print(f"PASSED ({_checked} assertions)")
    return 0


def _make_character(personality):
    from core.models import CharacterSnapshot, EvolutionRecord

    return CharacterSnapshot(
        giantess_id="test-giantess", name="测试娘", nick="小测",
        original_height=1.62, height=32.5,
        body_parts={"head": 1.0},
        evolution=[EvolutionRecord(changed_at="2026-10-11T00:00:00",
                                   step=0.3, intrusion=1.5, destruction=0.8,
                                   casualties=0.0, source="test")],
        action_points=88, personality=personality, greed=2,
        selected_tags=["高挑"], achieved_endings=[
            {"scenario_id": "scenario-a", "trigger_index": 0,
             "name": "结局A", "icon_path": "images/ending_a.png",
             "ending_text": "", "replay_path": "",
             "achieved_at": "2026-10-11T00:00:00"},
        ],
    )


if __name__ == "__main__":
    sys.exit(main())
