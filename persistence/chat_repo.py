import datetime
import os

from models import ChatState
from persistence.json_store import load_json_with_backup, write_json_atomic

# 聊天记录上限：超过后从最旧的开始丢弃（丢弃部分应先压缩进 memory，
# 压缩逻辑由聊天服务负责，repo 只做硬裁剪兜底）。
CHAT_HISTORY_LIMIT = 200
# 存档结构版本：2 = ChatMessage 带生命周期字段（id/status/available_at…，
# 见 docs/chat_delivery.md）；载入按字段名过滤，旧档自动补齐默认值。
CHAT_SCHEMA_VERSION = 2
# 话题列表容量与已消费事件 id 的保留上限（与聊天服务共同约束）
TOPIC_KEEP_LIMIT = 20
CONSUMED_EVENT_LIMIT = 200


class ChatRepo:
    """角色聊天域数据的存取（data/archives/<giantess_id>/chat.json）。

    与 CharacterRepo 共用存档目录：chat.json 位于角色目录内，
    角色删除时随目录一并移除，无需单独清理。
    """

    def __init__(self, data_dir: str = "data"):
        self._dir = os.path.join(data_dir, "archives")

    def _path(self, giantess_id: str) -> str:
        return os.path.join(self._dir, giantess_id, "chat.json")

    def load(self, giantess_id: str) -> ChatState:
        data = load_json_with_backup(self._path(giantess_id))
        if not isinstance(data, dict):
            return ChatState(giantess_id=giantess_id)
        data.setdefault("giantess_id", giantess_id)
        return ChatState.from_dict(data)

    def save(self, chat_state: ChatState):
        # 硬裁剪兜底：只保留最近 CHAT_HISTORY_LIMIT 条
        chat_state.messages = chat_state.messages[-CHAT_HISTORY_LIMIT:]
        # 话题列表与已消费事件 id 同样只留最近一段（防无限增长）
        chat_state.topics = chat_state.topics[-TOPIC_KEEP_LIMIT:]
        chat_state.consumed_event_ids = \
            chat_state.consumed_event_ids[-CONSUMED_EVENT_LIMIT:]
        chat_state.last_active_at = (chat_state.last_active_at
                                     or datetime.datetime.now().isoformat())
        write_json_atomic(self._path(chat_state.giantess_id), {
            "schema_version": CHAT_SCHEMA_VERSION,
            "giantess_id": chat_state.giantess_id,
            "messages": [m.__dict__ for m in chat_state.messages],
            "memory": chat_state.memory,
            "attitude": chat_state.attitude,
            "user_nick": chat_state.user_nick,
            "last_active_at": chat_state.last_active_at,
            "chat_params": (chat_state.chat_params.__dict__
                            if chat_state.chat_params is not None else None),
            "topics": [t.__dict__ for t in chat_state.topics],
            "active_topic_id": chat_state.active_topic_id,
            "consumed_event_ids": list(chat_state.consumed_event_ids),
        })

    def exists(self, giantess_id: str) -> bool:
        return os.path.exists(self._path(giantess_id))

    def ids_with_queued(self) -> list:
        """扫描全部角色存档，返回仍含 queued 角色消息的 giantess_id
        （应用启动时由投递调度器接管上次会话遗留的待投递消息）。"""
        result = []
        if not os.path.isdir(self._dir):
            return result
        for name in os.listdir(self._dir):
            path = os.path.join(self._dir, name, "chat.json")
            if not os.path.isfile(path):
                continue
            data = load_json_with_backup(path)
            messages = data.get("messages") if isinstance(data, dict) else None
            if any(isinstance(m, dict) and m.get("role") == "char"
                   and m.get("status") == "queued"
                   for m in (messages or [])):
                result.append(name)
        return result
