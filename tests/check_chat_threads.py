"""聊天多身份线程存储守卫。

覆盖设计文档 §11.2 的离线部分：
- 默认身份读取旧 chat.json，保存时写 chats/default.json 且不删除旧档；
- 非默认身份只读自己的线程，不串消息/态度；
- ids_with_queued 返回 (giantess_id, identity_id) 线程键；
- ChatService 的投递与已读只作用于指定线程。

用法：``python tests/check_chat_threads.py``
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.models import ChatMessage, ChatState  # noqa: E402
from persistence.chat_repo import CHAT_SCHEMA_VERSION, ChatRepo  # noqa: E402
from persistence.json_store import write_json_atomic  # noqa: E402
from services.chat import ChatService, events as chat_events  # noqa: E402
from services.chat.delivery import ChatDeliveryScheduler  # noqa: E402

PAST = (datetime.datetime.now() - datetime.timedelta(minutes=5)).isoformat()


def _check(problems, name, ok, extra=""):
    if not ok:
        problems.append(name + (f" <{extra}>" if extra else ""))


def _legacy_payload(text="旧消息", attitude=42, queued=False):
    message = {"role": "char", "text": text,
               "created_at": datetime.datetime.now().isoformat()}
    if queued:
        message.update({"status": "queued", "available_at": PAST})
    return {
        "schema_version": 2,
        "giantess_id": "g1",
        "messages": [message],
        "memory": "旧记忆",
        "attitude": attitude,
        "user_nick": "旧称呼",
        "last_active_at": "",
        "chat_params": {"online": 2, "warmup": 1, "boundary": 0},
        "topics": [],
        "active_topic_id": "",
        "consumed_event_ids": [],
    }


def _write_legacy(root, payload):
    path = os.path.join(root, "archives", "g1", "chat.json")
    write_json_atomic(path, payload)
    return path


def _thread_path(root, giantess_id, identity_id):
    return os.path.join(root, "archives", giantess_id, "chats",
                        identity_id + ".json")


def _raw(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _save(repo, giantess_id, identity_id, attitude, text, status="read"):
    state = repo.load(giantess_id, identity_id)
    state.attitude = attitude
    state.messages.append(ChatMessage(role="user", text=text, status=status))
    repo.save(state)
    return state


def check_legacy_fallback() -> list:
    problems = []
    root = tempfile.mkdtemp(prefix="chat_threads_legacy_")
    repo = ChatRepo(root)
    legacy_path = _write_legacy(root, _legacy_payload())

    state = repo.load("g1")
    _check(problems, "旧档应回退到默认身份",
           state.identity_id == "default" and state.attitude == 42
           and state.messages[0].text == "旧消息")
    _check(problems, "读取旧档不应立即生成新线程文件",
           not os.path.exists(_thread_path(root, "g1", "default")))
    _check(problems, "读取不应删除旧 chat.json", os.path.exists(legacy_path))

    other = repo.load("g1", "alice")
    _check(problems, "非默认身份不应读取旧档",
           other.identity_id == "alice" and not other.messages
           and other.attitude == 0)
    _check(problems, "非默认身份不应被创建为空文件",
           not os.path.exists(_thread_path(root, "g1", "alice")))

    _check(problems, "exists 应识别旧档", repo.exists("g1"))
    _check(problems, "exists 不应把旧档算到其他身份",
           not repo.exists("g1", "alice"))
    _check(problems, "不存在的角色应返回空线程",
           repo.load("missing").identity_id == "default"
           and not repo.load("missing").messages)
    return problems


def check_save_migration() -> list:
    problems = []
    root = tempfile.mkdtemp(prefix="chat_threads_save_")
    repo = ChatRepo(root)
    legacy_path = _write_legacy(root, _legacy_payload(attitude=42))
    state = repo.load("g1")
    state.messages.append(ChatMessage(role="user", text="迁移后"))
    state.attitude = 77
    repo.save(state)

    new_path = _thread_path(root, "g1", "default")
    _check(problems, "首次保存应写 chats/default.json",
           os.path.exists(new_path))
    _check(problems, "保存不应删除旧 chat.json", os.path.exists(legacy_path))
    _check(problems, "旧档内容应保持原样",
           _raw(legacy_path).get("attitude") == 42)
    new_payload = _raw(new_path)
    _check(problems, "新档应写 schema_version=3",
           new_payload.get("schema_version") == CHAT_SCHEMA_VERSION == 3)
    _check(problems, "新档应记录 identity_id",
           new_payload.get("identity_id") == "default")
    _check(problems, "新档应保留迁移后的态度与消息",
           new_payload.get("attitude") == 77
           and any(m.get("text") == "迁移后"
                   for m in new_payload.get("messages", [])))

    # 新文件存在后，旧档再变也不会影响默认线程的读路径
    legacy = _legacy_payload(attitude=-99)
    write_json_atomic(legacy_path, legacy)
    _check(problems, "迁移后默认线程应以新文件为准",
           repo.load("g1").attitude == 77)
    return problems


def check_thread_isolation() -> list:
    problems = []
    root = tempfile.mkdtemp(prefix="chat_threads_iso_")
    repo = ChatRepo(root)
    _save(repo, "g1", "alice", 30, "alice 的消息")
    _save(repo, "g1", "bob", -20, "bob 的消息")

    alice = repo.load("g1", "alice")
    bob = repo.load("g1", "bob")
    default = repo.load("g1")
    _check(problems, "alice 线程读回自己的身份",
           alice.identity_id == "alice" and alice.attitude == 30)
    _check(problems, "bob 线程读回自己的身份",
           bob.identity_id == "bob" and bob.attitude == -20)
    _check(problems, "默认身份不应看到其他身份的消息",
           default.identity_id == "default" and not default.messages)
    _check(problems, "线程消息不得串线",
           [m.text for m in alice.messages] == ["alice 的消息"]
           and [m.text for m in bob.messages] == ["bob 的消息"])
    _check(problems, "线程态度不得串线",
           alice.attitude == 30 and bob.attitude == -20
           and default.attitude == 0)

    # 身份 id 需要先归一，再作为文件名；分隔符不应逃出 chats/ 目录
    _save(repo, "g1", "  a/b  ", 1, "斜杠身份")
    _save(repo, "g1", "a_b", 2, "下划线身份")
    slash = repo.load("g1", "a/b")
    underscore = repo.load("g1", "a_b")
    _check(problems, "身份 id 应去掉首尾空白",
           slash.identity_id == "a/b")
    _check(problems, "带路径分隔符的身份不应与其他身份撞档",
           [m.text for m in slash.messages] == ["斜杠身份"]
           and [m.text for m in underscore.messages] == ["下划线身份"])
    _check(problems, "身份文件名不得包含路径分隔符",
           os.path.isfile(os.path.join(root, "archives", "g1", "chats",
                                        "a%2Fb.json")))
    _check(problems, "空身份应回退默认身份",
           repo.load("g1", "").identity_id == "default")
    return problems


def check_ids_with_queued() -> list:
    problems = []
    root = tempfile.mkdtemp(prefix="chat_threads_queued_")
    repo = ChatRepo(root)
    _write_legacy(root, _legacy_payload(queued=True))

    # 默认身份的新线程也留一条 queued：与旧档同一逻辑线程，只应返回一次
    state = repo.load("g1")
    state.messages.append(ChatMessage(role="char", text="新档排队",
                                      status="queued", available_at=PAST))
    repo.save(state)

    alice = repo.load("g1", "alice")
    alice.messages.append(ChatMessage(role="char", text="alice 排队",
                                      status="queued", available_at=PAST))
    repo.save(alice)

    _save(repo, "g1", "bob", 0, "bob 已读", status="read")
    bob = repo.load("g1", "bob")
    bob.messages.append(ChatMessage(role="char", text="bob 排队",
                                    status="queued", available_at=PAST))
    repo.save(bob)

    g2 = repo.load("g2", "alice")
    g2.messages.append(ChatMessage(role="char", text="g2 排队",
                                   status="queued", available_at=PAST))
    repo.save(g2)

    keys = repo.ids_with_queued()
    expected = {("g1", "default"), ("g1", "alice"),
                ("g1", "bob"), ("g2", "alice")}
    _check(problems, "ids_with_queued 应返回线程键",
           set(keys) == expected, str(keys))
    _check(problems, "ids_with_queued 结果应稳定有序",
           keys == sorted(keys), str(keys))

    # 投递完 alice 后，该线程从扫描结果里消失，其他线程不受影响
    service = ChatService(data_dir=root)
    service.deliver_due("g1", "alice")
    keys_after = set(repo.ids_with_queued())
    _check(problems, "已投递线程不应再报 queued",
           ("g1", "alice") not in keys_after)
    _check(problems, "其他线程的 queued 不应被连带处理",
           {("g1", "default"), ("g1", "bob"), ("g2", "alice")}
           <= keys_after, str(keys_after))
    return problems


def check_service_thread_delivery() -> list:
    problems = []
    root = tempfile.mkdtemp(prefix="chat_threads_service_")
    repo = ChatRepo(root)
    default = repo.load("g1")
    default.messages.append(ChatMessage(role="char", text="默认排队",
                                        status="queued", available_at=PAST))
    repo.save(default)
    alt = repo.load("g1", "alt")
    alt.messages.append(ChatMessage(role="char", text="alt 排队",
                                    status="queued", available_at=PAST))
    repo.save(alt)

    service = ChatService(data_dir=root)
    captured = []
    chat_events.subscribe(captured.append)
    try:
        promoted = service.deliver_due("g1", "alt")
    finally:
        chat_events.unsubscribe(captured.append)
    _check(problems, "deliver_due 应只提升指定线程",
           len(promoted) == 1 and promoted[0].text == "alt 排队")
    _check(problems, "投递事件应带线程键",
           any(event.get("type") == "message_delivered"
               and event.get("giantess_id") == "g1"
               and event.get("identity_id") == "alt"
               and event.get("message_ids") == [promoted[0].id]
               for event in captured))
    _check(problems, "指定线程应变为 delivered",
           repo.load("g1", "alt").messages[0].status == "delivered")
    _check(problems, "其他线程应保持 queued",
           repo.load("g1").messages[0].status == "queued")

    changed = service.mark_char_messages_read("g1", identity_id="alt")
    _check(problems, "mark_read 应只处理指定线程",
           changed == 1
           and repo.load("g1", "alt").messages[0].status == "read")
    _check(problems, "mark_read 不应影响其他线程",
           repo.load("g1").messages[0].status == "queued")

    loaded = service.load_chat("g1", identity_id="alt")
    _check(problems, "load_chat 应返回指定线程",
           loaded is not None and loaded.identity_id == "alt")
    _check(problems, "load_chat 缺省仍读默认身份",
           service.load_chat("g1").identity_id == "default")
    return problems


def check_scheduler_thread_keys() -> list:
    problems = []
    root = tempfile.mkdtemp(prefix="chat_threads_scheduler_")
    repo = ChatRepo(root)
    for giantess_id, identity_id in (("g1", "default"), ("g1", "alt"),
                                     ("g2", "alt")):
        state = repo.load(giantess_id, identity_id)
        state.messages.append(ChatMessage(role="char", text="排队",
                                          status="queued",
                                          available_at=PAST))
        repo.save(state)

    scheduler = ChatDeliveryScheduler(ChatService(data_dir=root))
    calls = []
    scheduler.notify_queued = lambda gid, iid, at: calls.append((gid, iid))
    scheduler._adopt_pending()
    _check(problems, "调度器接管时应按线程键登记",
           set(calls) == {("g1", "default"), ("g1", "alt"),
                          ("g2", "alt")}, str(calls))
    return problems


def main() -> int:
    problems = (check_legacy_fallback() + check_save_migration()
                + check_thread_isolation() + check_ids_with_queued()
                + check_service_thread_delivery()
                + check_scheduler_thread_keys())
    if problems:
        print("[check_chat_threads] FAILED")
        for item in problems:
            print("  - " + item)
        return 1
    print("[check_chat_threads] PASSED：旧档回退、线程隔离与线程级投递符合预期")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
