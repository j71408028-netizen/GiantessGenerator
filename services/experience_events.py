"""角色经历事件：把角色档案里的近况整理为聊天决策可消费的事件流。

收集范围（粗糙实现，够用为先，见 docs/chat_delivery.md 阶段四）：
- news      ：state.last_news（内容变化即新事件，同一条新闻只提一次）；
- position  ：state.position（位置注册/变化后提示一次）；
- evolution ：演化表最近若干条有效记录（步进非 0 或离线恢复/聊天写入）；
- ending    ：achieved_endings 中达成的重要结局。

幂等性：每条事件有确定性 id（kind + 内容摘要的 md5 前缀），消费与否
记录在 ChatState.consumed_event_ids（容量由 repo 侧截断兜底）。收集
是纯函数式的：不修改任何状态；消费由聊天服务在 AI 决策成功后统一
执行——失败则保持未消费，下次打开继续决策。
"""

import hashlib

from core.models import CharacterSnapshot, ChatState

# 演化表只回看最近这么多条记录（更早的变化视为已被消化）
EVOLUTION_LOOKBACK = 10
# 注入 AI 提示词的事件条数上限（超出部分只消费不再展示）
PROMPT_EVENT_LIMIT = 5


def _event_id(kind: str, key: str) -> str:
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()[:10]
    return f"{kind}:{digest}"


def collect_experience_events(state: CharacterSnapshot,
                              chat_state: ChatState) -> list:
    """收集未消费的经历事件，按时间感排序（新事件在后）。

    每条事件形如 {"id", "type", "summary"}；summary 以对角色本人的
    口吻书写（"你……"），直接注入她的决策提示词。
    """
    consumed = set(chat_state.consumed_event_ids or [])
    events = []
    if state.last_news:
        event_id = _event_id("news", state.last_news)
        if event_id not in consumed:
            events.append({
                "id": event_id, "type": "news",
                "summary": f"关于你的新闻：{state.last_news[:120]}",
            })
    if state.position:
        event_id = _event_id("position", state.position)
        if event_id not in consumed:
            events.append({
                "id": event_id, "type": "position",
                "summary": f"你目前在：{state.position}",
            })
    records = (state.evolution or [])[-EVOLUTION_LOOKBACK:]
    for record in records:
        if record.step == 0.0 and record.source not in ("recover_evolution",
                                                        "chat"):
            continue  # 非故事性调整（如加载期衰退）不当作经历
        event_id = _event_id("evo", record.changed_at)
        if event_id in consumed:
            continue
        events.append({
            "id": event_id, "type": "evolution",
            "summary": (f"你的状态发生了变化（{record.source or '日常'}，"
                        f"步进 {record.step:.1f}）"),
        })
    for ending in (state.achieved_endings or []):
        key = f"{ending.get('achieved_at', '')}|{ending.get('name', '')}"
        event_id = _event_id("ending", key)
        if event_id in consumed:
            continue
        events.append({
            "id": event_id, "type": "ending",
            "summary": f"你达成了重要事件：{ending.get('name', '未知')}",
        })
    return events


def events_for_prompt(events: list) -> list:
    """取最近几条用于注入提示词（其余仍会被消费，只是不再展示）。"""
    return events[-PROMPT_EVENT_LIMIT:]


def consume_experience_events(chat_state: ChatState, events: list):
    """把事件标记为已消费（AI 决策成功后调用；容量由 repo 截断兜底）。"""
    for event in events:
        if event.get("id") and event["id"] not in chat_state.consumed_event_ids:
            chat_state.consumed_event_ids.append(event["id"])
