"""玩家身份列表与「当前身份」指针的存取（``data/user/identities.json``）。

一个玩家可以同时拥有多个身份；对角色而言这些身份是不同的人，聊天按
``(giantess_id, identity_id)`` 拆成互不相通的线程（见 :class:`ChatRepo`）。
本模块只管身份本身与当前选择，不碰聊天内容。

文件结构::

    {"identities": [{id, label, description, created_at, updated_at}, ...],
     "active_identity_id": "default"}

默认身份永远存在：读到的数据里没有它就补一个 ``default``，因此界面不用
额外处理「还没建过身份」的首次启动。写入走原子替换，读损坏时回退 ``.bak``。
"""

from __future__ import annotations

import datetime
import os
import uuid

from core.models import (DEFAULT_IDENTITY_ID, PlayerIdentity,
                         normalize_identity_id)
from persistence.json_store import load_json_with_backup, write_json_atomic

# 界面是下拉/菜单，身份数量与文本都设上限，避免出现无法辨认的条目
IDENTITY_LIMIT = 12
IDENTITY_LABEL_LIMIT = 24
IDENTITY_DESCRIPTION_LIMIT = 200
DEFAULT_LABEL = "默认身份"


class PlayerIdentityRepo:
    """身份列表的读写；默认身份固定排在列表第一位。"""

    def __init__(self, data_dir: str = "data"):
        self._file = os.path.join(data_dir, "user", "identities.json")

    # ---------- 读 ----------
    def load(self) -> list:
        """全部身份（默认身份恒在，排第一）。"""
        return self._parse(self._read_payload())[0]

    def active_id(self) -> str:
        """当前身份 id；指针指向不存在/为空时回退默认身份。"""
        return self._parse(self._read_payload())[1]

    def get(self, identity_id: str):
        """按 id 取身份；不存在返回 None。"""
        target = normalize_identity_id(identity_id)
        return next((item for item in self.load() if item.id == target), None)

    def _read_payload(self) -> dict:
        data = load_json_with_backup(self._file)
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _parse(payload: dict):
        """把原始 JSON 归一为 (身份列表, 当前 id)，坏条目静默跳过。"""
        identities = []
        seen = set()
        raw = payload.get("identities")
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            identity = PlayerIdentity.from_dict(item)
            if identity.id in seen:
                continue
            seen.add(identity.id)
            identities.append(identity)
        if DEFAULT_IDENTITY_ID in seen:
            identities.sort(key=lambda item: item.id != DEFAULT_IDENTITY_ID)
        else:
            identities.insert(0, PlayerIdentity())
        active = normalize_identity_id(payload.get("active_identity_id"))
        if active not in {item.id for item in identities}:
            active = DEFAULT_IDENTITY_ID
        return identities, active

    # ---------- 写 ----------
    def set_active(self, identity_id: str) -> str:
        """把当前身份指针指向 ``identity_id`` 并落盘；未知 id 保持原值。"""
        identities, active = self._parse(self._read_payload())
        target = normalize_identity_id(identity_id)
        if target not in {item.id for item in identities}:
            target = active
        if target != active:
            self._write(identities, target)
        return target

    def create(self, label: str, description: str = "") -> PlayerIdentity:
        """新建身份并把它设为当前身份（新建后通常就要以它开聊）。

        身份数量已达 ``IDENTITY_LIMIT`` 或名字全空白时返回 None，由界面提示。
        """
        identities, _ = self._parse(self._read_payload())
        if len(identities) >= IDENTITY_LIMIT:
            return None
        label = self._clean_label(label)
        if not label:
            return None
        identity = PlayerIdentity(
            id=self._new_id(identities), label=label,
            description=self._clean_description(description))
        identities.append(identity)
        self._write(identities, identity.id)
        return identity

    def rename(self, identity_id: str, label: str) -> bool:
        """改显示名（默认身份也可以改，但 id 不变）。"""
        target = normalize_identity_id(identity_id)
        label = self._clean_label(label)
        if not label:
            return False
        identities, active = self._parse(self._read_payload())
        identity = next((item for item in identities if item.id == target), None)
        if identity is None or identity.label == label:
            return False
        identity.label = label
        identity.updated_at = datetime.datetime.now().isoformat()
        self._write(identities, active)
        return True

    def _write(self, identities: list, active: str):
        write_json_atomic(self._file, {
            "identities": [item.__dict__ for item in identities],
            "active_identity_id": normalize_identity_id(active),
        })

    # ---------- 内部 ----------
    @staticmethod
    def _new_id(identities: list) -> str:
        """稳定的线程键：短 uuid，避开默认 id 与既有键。"""
        used = {item.id for item in identities}
        while True:
            candidate = uuid.uuid4().hex[:10]
            if candidate != DEFAULT_IDENTITY_ID and candidate not in used:
                return candidate

    @staticmethod
    def _clean_label(label) -> str:
        if not isinstance(label, str):
            return ""
        return " ".join(label.split())[:IDENTITY_LABEL_LIMIT]

    @staticmethod
    def _clean_description(description) -> str:
        if not isinstance(description, str):
            return ""
        return " ".join(description.split())[:IDENTITY_DESCRIPTION_LIMIT]
