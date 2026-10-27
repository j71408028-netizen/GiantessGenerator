import datetime
import os
from urllib.parse import quote, unquote

from core.models import (APPLIED_EFFECT_LIMIT, DEFAULT_IDENTITY_ID, ChatState,
                         normalize_identity_id)
from persistence.json_store import (BACKUP_SUFFIX, load_json_with_backup,
                                    write_json_atomic)

# 聊天记录上限：超过后从最旧的开始丢弃（丢弃部分应先压缩进 memory，
# 压缩逻辑由聊天服务负责，repo 只做硬裁剪兜底）。
CHAT_HISTORY_LIMIT = 200
# 存档结构版本：3 = 聊天按玩家身份拆分为独立线程（chats/<identity_id>.json），
# 默认身份可回退读取旧 chat.json；载入按字段名过滤，旧档自动补齐默认值。
CHAT_SCHEMA_VERSION = 3
# 话题列表容量与已消费事件 id 的保留上限（与聊天服务共同约束）
TOPIC_KEEP_LIMIT = 20
CONSUMED_EVENT_LIMIT = 200


class ChatRepo:
    """角色聊天域数据的存取（一条线程 = 角色 × 玩家身份）。

    新布局为 ``data/archives/<giantess_id>/chats/<identity_id>.json``；
    旧 ``chat.json`` 只在读取默认身份且新文件不存在时作为回退，保存时不
    删除它。identity_id 经 URL 编码后作为文件名，避免路径分隔符等问题。
    """

    def __init__(self, data_dir: str = "data"):
        self._dir = os.path.join(data_dir, "archives")

    def _legacy_path(self, giantess_id: str) -> str:
        return os.path.join(self._dir, giantess_id, "chat.json")

    def _threads_dir(self, giantess_id: str) -> str:
        return os.path.join(self._dir, giantess_id, "chats")

    def _thread_path(self, giantess_id: str, identity_id: str) -> str:
        name = quote(normalize_identity_id(identity_id), safe="")
        return os.path.join(self._threads_dir(giantess_id), name + ".json")

    @staticmethod
    def _path_exists(path: str) -> bool:
        return os.path.exists(path) or os.path.exists(path + BACKUP_SUFFIX)

    def _read_payload(self, giantess_id: str, identity_id: str):
        """按线程读取；默认身份在新文件缺失时回退旧 chat.json。"""
        thread_path = self._thread_path(giantess_id, identity_id)
        if self._path_exists(thread_path):
            return load_json_with_backup(thread_path)
        if identity_id == DEFAULT_IDENTITY_ID:
            legacy_path = self._legacy_path(giantess_id)
            if self._path_exists(legacy_path):
                return load_json_with_backup(legacy_path)
        return None

    def load(self, giantess_id: str,
             identity_id: str = DEFAULT_IDENTITY_ID) -> ChatState:
        identity_id = normalize_identity_id(identity_id)
        data = self._read_payload(giantess_id, identity_id)
        if not isinstance(data, dict):
            return ChatState(giantess_id=giantess_id, identity_id=identity_id)
        data = {**data, "giantess_id": giantess_id,
                "identity_id": identity_id}
        return ChatState.from_dict(data)

    def save(self, chat_state: ChatState):
        # 硬裁剪兜底：只保留最近 CHAT_HISTORY_LIMIT 条
        chat_state.messages = chat_state.messages[-CHAT_HISTORY_LIMIT:]
        # 话题列表与已消费事件 id 同样只留最近一段（防无限增长）
        chat_state.topics = chat_state.topics[-TOPIC_KEEP_LIMIT:]
        chat_state.consumed_event_ids = \
            chat_state.consumed_event_ids[-CONSUMED_EVENT_LIMIT:]
        chat_state.applied_effects = \
            chat_state.applied_effects[-APPLIED_EFFECT_LIMIT:]
        chat_state.last_active_at = (chat_state.last_active_at
                                     or datetime.datetime.now().isoformat())
        chat_state.identity_id = normalize_identity_id(chat_state.identity_id)
        write_json_atomic(self._thread_path(chat_state.giantess_id,
                                            chat_state.identity_id), {
            "schema_version": CHAT_SCHEMA_VERSION,
            "giantess_id": chat_state.giantess_id,
            "identity_id": chat_state.identity_id,
            "messages": [m.__dict__ for m in chat_state.messages],
            "memory": chat_state.memory,
            "attitude": chat_state.attitude,
            "user_nick": chat_state.user_nick,
            "relationship": chat_state.relationship.__dict__,
            "applied_effects": [e.__dict__ for e in chat_state.applied_effects],
            "last_active_at": chat_state.last_active_at,
            "chat_params": (chat_state.chat_params.__dict__
                            if chat_state.chat_params is not None else None),
            "topics": [t.__dict__ for t in chat_state.topics],
            "active_topic_id": chat_state.active_topic_id,
            "consumed_event_ids": list(chat_state.consumed_event_ids),
        })

    def exists(self, giantess_id: str,
               identity_id: str = DEFAULT_IDENTITY_ID) -> bool:
        identity_id = normalize_identity_id(identity_id)
        if self._path_exists(self._thread_path(giantess_id, identity_id)):
            return True
        return (identity_id == DEFAULT_IDENTITY_ID
                and self._path_exists(self._legacy_path(giantess_id)))

    def _thread_file_keys(self, giantess_id: str) -> set:
        """``chats/`` 下落过盘的身份文件名键（URL 编码后的形式）。"""
        threads_dir = self._threads_dir(giantess_id)
        keys = set()
        if not os.path.isdir(threads_dir):
            return keys
        for filename in os.listdir(threads_dir):
            if filename.endswith(".json"):
                keys.add(filename[:-len(".json")])
            elif filename.endswith(".json" + BACKUP_SUFFIX):
                keys.add(filename[:-len(".json" + BACKUP_SUFFIX)])
        return keys

    def thread_ids(self, giantess_id: str) -> list:
        """该角色已存在的聊天线程身份 id（默认身份排第一）。

        只报「落过盘」的线程：从没打开过的身份还没有存档，也就没有未读。
        旧 ``chat.json`` 计为默认身份的线程。
        """
        ids = {normalize_identity_id(unquote(key))
               for key in self._thread_file_keys(giantess_id)}
        if self._path_exists(self._legacy_path(giantess_id)):
            ids.add(DEFAULT_IDENTITY_ID)
        ordered = sorted(ids)
        if DEFAULT_IDENTITY_ID in ordered:
            ordered.remove(DEFAULT_IDENTITY_ID)
            ordered.insert(0, DEFAULT_IDENTITY_ID)
        return ordered

    @staticmethod
    def _has_queued(data) -> bool:
        messages = data.get("messages") if isinstance(data, dict) else None
        return any(isinstance(message, dict)
                   and message.get("role") == "char"
                   and message.get("status") == "queued"
                   for message in (messages or []))

    def ids_with_queued(self) -> list:
        """扫描全部角色存档，返回仍含 queued 角色消息的线程键。

        线程键为 ``(giantess_id, identity_id)``；旧 ``chat.json`` 与
        ``chats/*.json`` 都参与扫描，同一逻辑线程只返回一次。
        """
        result = set()
        if not os.path.isdir(self._dir):
            return []
        for name in os.listdir(self._dir):
            if not os.path.isdir(os.path.join(self._dir, name)):
                continue
            legacy_path = self._legacy_path(name)
            if self._path_exists(legacy_path):
                data = load_json_with_backup(legacy_path)
                if self._has_queued(data):
                    # 旧 chat.json 永远属于默认身份的线程
                    result.add((name, DEFAULT_IDENTITY_ID))
            threads_dir = self._threads_dir(name)
            if not os.path.isdir(threads_dir):
                continue
            for key in self._thread_file_keys(name):
                data = load_json_with_backup(
                    os.path.join(threads_dir, key + ".json"))
                if self._has_queued(data):
                    result.add((name, normalize_identity_id(unquote(key))))
        return sorted(result)
