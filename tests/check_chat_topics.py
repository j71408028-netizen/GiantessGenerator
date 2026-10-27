"""话题类型选择与聊天模型字段守卫。

覆盖设计文档 §4 与 §8 的纯逻辑部分：类型注册表完整性、半径过滤、优先级、
重复惩罚、无候选兜底；以及 Topic / PlayerIdentity / Relationship 的默认值
与旧档兼容。

不锁具体类型集合、坐标与提示词文案：只验证排序规则、兜底 id 存在、模型
能读旧档。调整初始话题类型或坐标不应让本自检变红。

用法：``python tests/check_chat_topics.py``
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.models import (  # noqa: E402
    DEFAULT_IDENTITY_ID, DEFAULT_RELATIONSHIP_KIND, RELATIONSHIP_KINDS,
    PlayerIdentity, Relationship, Topic,
)
from services.chat.coordinates import (  # noqa: E402
    ChatCoord, RELATIONSHIP_TENSION_OFFSETS,
)
from services.chat.topic_kinds import (  # noqa: E402
    FALLBACK_KIND, NO_TOPIC_LENS, TOPIC_KINDS, TopicKind,
    candidate_topic_kinds, eligible_topic_kinds, fallback_topic_kind,
    find_topic_kind, kind_is_eligible, rank_topic_kinds, repeat_penalty,
    validate_topic_kinds,
)

CENTER = ChatCoord(0.5, 0.5, 0.5)


def _kind(kind_id, coord, radius=0.4, priority=0, prompt="提示"):
    return TopicKind(id=kind_id, label=kind_id, coord=ChatCoord(*coord),
                     radius=radius, priority=priority, prompt=prompt)


def check_registry() -> list:
    problems = [f"类型表结构问题：{item}"
                for item in validate_topic_kinds(TOPIC_KINDS, NO_TOPIC_LENS)]
    ids = [kind.id for kind in TOPIC_KINDS]
    if len(ids) != len(set(ids)):
        problems.append("初始类型集的 id 必须唯一")
    fallback = fallback_topic_kind()
    if fallback is None or fallback.id != FALLBACK_KIND:
        problems.append(f"注册表必须提供兜底类型 {FALLBACK_KIND!r}")
    if fallback_topic_kind([]) is not None:
        problems.append("空注册表不应凭空变出兜底类型")
    return problems


def check_filtering() -> list:
    problems = []
    kinds = [
        _kind("near", (0.5, 0.5, 0.5), radius=0.2),
        _kind("mid", (0.6, 0.5, 0.5), radius=0.4),
        _kind("far", (2.0, 2.0, 2.0), radius=0.2),
        _kind("overlap", (0.5, 0.5, 0.5), radius=0.2),
    ]
    eligible = eligible_topic_kinds(CENTER, kinds)
    eligible_ids = [kind.id for kind in eligible]
    if "far" in eligible_ids:
        problems.append("半径外的类型不应进入候选")
    if "near" not in eligible_ids or "overlap" not in eligible_ids:
        problems.append("坐标重叠的类型都应进入候选")
    if not eligible_ids.index("near") <= eligible_ids.index("mid"):
        problems.append("候选应按距离由近到远排序")

    if not kind_is_eligible("near", CENTER, kinds):
        problems.append("kind_is_eligible 未识别半径内的类型")
    if kind_is_eligible("far", CENTER, kinds):
        problems.append("kind_is_eligible 误判半径外的类型")
    if kind_is_eligible("missing", CENTER, kinds):
        problems.append("未知 kind 不应通过候选校验")
    if find_topic_kind("missing", kinds) is not None:
        problems.append("未知 kind 应返回 None")
    return problems


def check_ranking() -> list:
    problems = []
    kinds = [
        _kind("a", (0.5, 0.5, 0.5), radius=0.6, priority=0),
        _kind("b", (0.55, 0.5, 0.5), radius=0.6, priority=0),
        _kind("c", (0.60, 0.5, 0.5), radius=0.6, priority=0),
    ]
    order = [kind.id for kind in rank_topic_kinds(CENTER, kinds)]
    if order != ["a", "b", "c"]:
        problems.append(f"候选应按距离排序，实际 {order}")

    tie = [
        _kind("low", (0.5, 0.5, 0.5), priority=0),
        _kind("high", (0.5, 0.5, 0.5), priority=5),
    ]
    tie_order = [kind.id for kind in rank_topic_kinds(CENTER, tie)]
    if tie_order != ["high", "low"]:
        problems.append(f"同距离时优先级高的应在前，实际 {tie_order}")

    limited = candidate_topic_kinds(CENTER, kinds, limit=2)
    if [kind.id for kind in limited] != ["a", "b"]:
        problems.append("候选数量应受 limit 限制")
    if candidate_topic_kinds(CENTER, kinds, limit=0) != []:
        problems.append("limit=0 时应返回空候选")

    if repeat_penalty("a", ["a", "b", "a"], base=1.0, decay=0.5) != 1.25:
        problems.append("重复惩罚应按新到旧衰减累加")
    if repeat_penalty("a", [], base=1.0) != 0.0:
        problems.append("没有使用记录时不应有重复惩罚")
    return problems


def check_repeat_penalty_ranking() -> list:
    problems = []
    kinds = [
        _kind("recent", (0.500, 0.5, 0.5), radius=0.6),
        _kind("other", (0.501, 0.5, 0.5), radius=0.6),
    ]
    order = [kind.id for kind in rank_topic_kinds(
        CENTER, kinds, recent_kind_ids=["recent"] * 100)]
    if order != ["other", "recent"]:
        problems.append(f"反复使用的类型应被重复惩罚压到后面，实际 {order}")
    return problems


def check_no_candidates() -> list:
    problems = []
    kinds = [_kind("lonely", (2.0, 2.0, 2.0), radius=0.1)]
    if eligible_topic_kinds(CENTER, kinds) != []:
        problems.append("所有类型都在半径外时应返回空候选")
    if candidate_topic_kinds(CENTER, kinds) != []:
        problems.append("候选不足时不应随机硬塞一个远类型")
    if fallback_topic_kind(kinds) is not None:
        problems.append("自定义注册表没有 daily 时兜底应为 None")

    center = _kind(FALLBACK_KIND, (0.5, 0.5, 0.5), radius=0.2)
    if [kind.id for kind in candidate_topic_kinds(CENTER, [center])] \
            != [FALLBACK_KIND]:
        problems.append("兜底类型在半径内时应正常进入候选")
    return problems


def check_topic_model() -> list:
    problems = []
    old = Topic.from_dict({"title": "旧话题"})
    if old.kind_id != "" or old.coord != []:
        problems.append("旧话题应保持空 kind_id / coord")

    new = Topic.from_dict({
        "title": "新话题", "kind_id": "daily",
        "coord": [0.2, 0.3, 0.4], "unknown": "ignored"})
    if new.kind_id != "daily" or new.coord != [0.2, 0.3, 0.4]:
        problems.append("新话题的 kind_id / coord 未能读入")
    if "unknown" in new.__dict__:
        problems.append("Topic.from_dict 应忽略未知字段")

    clamped = Topic(title="夹取", coord=(2.0, -1.0, 0.5))
    if clamped.coord != [1.0, 0.0, 0.5]:
        problems.append("话题坐标快照应夹在 0~1")
    if Topic(title="坏长", coord=[0.1, 0.2]).coord != []:
        problems.append("长度不为 3 的坐标快照应为空")
    if Topic(title="坏值", coord=[0.1, float("nan"), 0.3]).coord != []:
        problems.append("含非有限数值的坐标快照应为空")
    return problems


def check_identity_models() -> list:
    problems = []
    identity = PlayerIdentity()
    if identity.id != DEFAULT_IDENTITY_ID:
        problems.append("默认身份的 id 应为 default")
    if not identity.label.strip():
        problems.append("默认身份应有显示名")
    if not identity.created_at or not identity.updated_at:
        problems.append("身份应补齐创建/更新时间")

    loaded = PlayerIdentity.from_dict(
        {"id": "", "label": "", "description": "路人", "unknown": 1})
    if loaded.id != DEFAULT_IDENTITY_ID or not loaded.label.strip():
        problems.append("空 id / label 应回退默认值")
    if loaded.description != "路人" or "unknown" in loaded.__dict__:
        problems.append("身份字段读取应过滤未知键")

    relation = Relationship()
    if relation.kind != DEFAULT_RELATIONSHIP_KIND:
        problems.append("关系认知默认应为陌生人")
    if not set(RELATIONSHIP_KINDS) <= set(RELATIONSHIP_TENSION_OFFSETS):
        problems.append("每个关系 kind 都应有紧张度偏移")
    if set(RELATIONSHIP_TENSION_OFFSETS) - set(RELATIONSHIP_KINDS):
        problems.append("紧张度偏移不应包含未登记的关系 kind")

    invalid = Relationship.from_dict({"kind": "not-a-kind", "note": "旧档"})
    if invalid.kind != DEFAULT_RELATIONSHIP_KIND or invalid.note != "旧档":
        problems.append("非法关系 kind 应回退默认值")
    return problems


def main() -> int:
    problems = (check_registry() + check_filtering() + check_ranking()
                + check_repeat_penalty_ranking() + check_no_candidates()
                + check_topic_model() + check_identity_models())
    if problems:
        print("[check_chat_topics] FAILED")
        for item in problems:
            print("  - " + item)
        return 1
    print("[check_chat_topics] PASSED：候选筛选、兜底与聊天模型兼容性符合预期")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
