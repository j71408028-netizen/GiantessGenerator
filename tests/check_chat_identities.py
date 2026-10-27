"""玩家身份列表与身份选择器的领域守卫。

覆盖设计文档 §11.6 中不依赖界面的部分：
- 身份仓库：空档即只有默认身份、active 指向默认；
- 新建身份自动成为当前身份并落盘，空白名字/超限被拒；
- active 指针指向不存在的身份时回退默认身份；
- 改名只改显示名，id 不变；坏档回退默认身份而不崩；
- ChatRepo.thread_ids 只报落过盘的线程，旧 chat.json 计为默认身份；
- unread_by_identity / unread_total 按线程统计已投递未读，跨身份聚合只相加。

界面（下拉/菜单、切换线程）需要显示环境，由 .temp 的冒烟脚本覆盖。

用法：``python tests/check_chat_identities.py``
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.models import ChatMessage  # noqa: E402
from persistence.chat_repo import ChatRepo  # noqa: E402
from persistence.player_identity_repo import (  # noqa: E402
    IDENTITY_LIMIT, PlayerIdentityRepo,
)
from services.chat import ChatService  # noqa: E402


def _check(problems, name, ok, extra=""):
    if not ok:
        problems.append(name + (f" <{extra}>" if extra else ""))


def _identities_path(root):
    return os.path.join(root, "user", "identities.json")


def _raw(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def check_repo_defaults() -> list:
    problems = []
    root = tempfile.mkdtemp(prefix="ident_repo_")
    repo = PlayerIdentityRepo(root)
    identities = repo.load()
    _check(problems, "空档应只有一个默认身份",
           [item.id for item in identities] == ["default"],
           [item.id for item in identities])
    _check(problems, "空档的当前身份应是默认身份",
           repo.active_id() == "default", repo.active_id())
    _check(problems, "只读不应写盘", not os.path.exists(_identities_path(root)))

    created = repo.create("  调查员  ")
    _check(problems, "新建身份应去空白并落盘",
           created is not None and created.label == "调查员", created)
    _check(problems, "新建后当前身份指向新身份",
           repo.active_id() == created.id, repo.active_id())
    payload = _raw(_identities_path(root))
    _check(problems, "落盘应含 identities 与 active_identity_id",
           len(payload.get("identities", [])) == 2
           and payload.get("active_identity_id") == created.id, payload)

    reloaded = PlayerIdentityRepo(root)
    _check(problems, "重新打开应保留新建的身份与当前指针",
           [item.label for item in reloaded.load()] == ["默认身份", "调查员"]
           and reloaded.active_id() == created.id,
           reloaded.active_id())

    _check(problems, "空白名字应被拒绝", repo.create("   ") is None)
    _check(problems, "未知身份的 active 应保持原值",
           repo.set_active("not-exist") == created.id)
    return problems


def check_repo_edges() -> list:
    problems = []
    root = tempfile.mkdtemp(prefix="ident_edge_")
    repo = PlayerIdentityRepo(root)
    created = repo.create("调查员")
    _check(problems, "改名应成功", repo.rename(created.id, "旧调查员"))
    renamed = next(item for item in repo.load() if item.id == created.id)
    _check(problems, "改名只改显示名、id 不变", renamed.label == "旧调查员",
           renamed.label)
    _check(problems, "空名字改名应被拒绝", not repo.rename(created.id, "  "))

    # 数量上限
    while repo.create(f"身份{len(repo.load())}") is not None:
        if len(repo.load()) >= IDENTITY_LIMIT:
            break
    _check(problems, "身份数量达到上限后应拒绝新建",
           len(repo.load()) == IDENTITY_LIMIT
           and repo.create("再一个") is None, len(repo.load()))

    # active 指向不存在的身份 → 回退默认
    payload = _raw(_identities_path(root))
    payload["active_identity_id"] = "ghost"
    with open(_identities_path(root), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    _check(problems, "active 指向不存在的身份应回退默认",
           PlayerIdentityRepo(root).active_id() == "default")

    # 坏档
    broken = tempfile.mkdtemp(prefix="ident_broken_")
    os.makedirs(os.path.join(broken, "user"), exist_ok=True)
    with open(_identities_path(broken), "w", encoding="utf-8") as handle:
        handle.write("{ not json")
    _check(problems, "坏档应回退默认身份而不崩",
           [item.id for item in PlayerIdentityRepo(broken).load()] == ["default"])
    return problems


def check_thread_ids() -> list:
    problems = []
    root = tempfile.mkdtemp(prefix="ident_threads_")
    repo = ChatRepo(root)
    _check(problems, "没有存档时线程列表应为空", repo.thread_ids("g1") == [])

    # 旧 chat.json → 默认身份
    legacy = os.path.join(root, "archives", "g1", "chat.json")
    os.makedirs(os.path.dirname(legacy), exist_ok=True)
    with open(legacy, "w", encoding="utf-8") as handle:
        handle.write("{}")
    _check(problems, "旧 chat.json 应计为默认身份线程",
           repo.thread_ids("g1") == ["default"], repo.thread_ids("g1"))

    # 新布局线程，默认身份排第一
    threads = os.path.join(root, "archives", "g1", "chats")
    os.makedirs(threads, exist_ok=True)
    with open(os.path.join(threads, "abc123.json"), "w", encoding="utf-8") as h:
        h.write("{}")
    _check(problems, "默认身份应排在其它身份之前",
           repo.thread_ids("g1") == ["default", "abc123"],
           repo.thread_ids("g1"))

    other_root = tempfile.mkdtemp(prefix="ident_threads2_")
    other = ChatRepo(other_root)
    other_threads = os.path.join(other_root, "archives", "g1", "chats")
    os.makedirs(other_threads, exist_ok=True)
    with open(os.path.join(other_threads, "abc123.json"), "w",
              encoding="utf-8") as h:
        h.write("{}")
    _check(problems, "没有旧档时只报新布局线程",
           other.thread_ids("g1") == ["abc123"], other.thread_ids("g1"))
    return problems


def check_unread_aggregation() -> list:
    problems = []
    root = tempfile.mkdtemp(prefix="ident_unread_")
    service = ChatService(data_dir=root,
                          world_stance_provider=lambda: "coexist")
    second = service.create_identity("调查员")

    for identity_id, char_count, user_count in (
            ("default", 2, 1), (second.id, 1, 0)):
        thread = service.chat_repo.load("g1", identity_id)
        for _ in range(char_count):
            thread.messages.append(ChatMessage(role="char", text="回复",
                                               status="delivered"))
        for _ in range(user_count):
            thread.messages.append(ChatMessage(role="user", text="我说"))
        service.chat_repo.save(thread)

    counts = service.unread_by_identity("g1")
    _check(problems, "未读应按线程分别统计且只算已投递角色消息",
           counts == {"default": 2, second.id: 1}, counts)
    _check(problems, "总量应为各线程之和", service.unread_total("g1") == 3)

    # 读掉一条线程后，另一条不受影响
    service.mark_char_messages_read("g1", identity_id=second.id)
    _check(problems, "标记已读只作用于指定线程",
           service.unread_total("g1") == 2
           and service.unread_by_identity("g1") == {"default": 2},
           service.unread_by_identity("g1"))

    # 没落盘的身份不产生未读
    third = service.create_identity("邻居")
    _check(problems, "没有存档的身份不应出现在未读里",
           third.id not in service.unread_by_identity("g1"))
    return problems


def check_identity_in_thread() -> list:
    problems = []
    root = tempfile.mkdtemp(prefix="ident_persist_")
    service = ChatService(data_dir=root,
                          world_stance_provider=lambda: "coexist")
    other = service.create_identity("邻居")
    thread = service.chat_repo.load("g1", other.id)
    thread.attitude = 30
    thread.messages.append(ChatMessage(role="char", text="你好",
                                       status="delivered"))
    service.chat_repo.save(thread)

    payload = _raw(os.path.join(root, "archives", "g1", "chats",
                                other.id + ".json"))
    _check(problems, "线程存档应记录 identity_id",
           payload.get("identity_id") == other.id, payload.get("identity_id"))
    _check(problems, "默认身份线程不受影响",
           service.chat_repo.load("g1", "default").attitude == 0)
    return problems


def main() -> int:
    problems = (check_repo_defaults() + check_repo_edges() + check_thread_ids()
                + check_unread_aggregation() + check_identity_in_thread())
    if problems:
        print("[check_chat_identities] FAILED")
        for item in problems:
            print("  - " + item)
        return 1
    print("[check_chat_identities] PASSED：身份列表、线程枚举与未读聚合符合预期")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
