"""资源引用（``Ref``）：某一类资源下的**某一条**内容的稳定身份。

命名层（``core/resource_spec.py``）区分「类型 id」与「资源 id」；本模块补上第三层：
「类型 + 类型内身份」打包成一个可比较、可哈希、可直接当 JSON 键用的值。

    Ref.scoped("landmarks", "chinese_mix", "上海中心大厦").text
    == "landmarks:chinese_mix/上海中心大厦"

落盘形态是 ``text``（JSON 的键只能是字符串），``parse()`` 是其逆运算，
``scope`` / ``name`` 是读写两端各自的语义视图：

- ``scope``：容纳它的容器 id（地标 = 风格 id）。资源放在哪个容器里属于声明，
  不写进内容字段；
- ``name``：容器内的身份（地标 = 显示名，同一风格内唯一）。

为什么不用内存地址（``id(obj)``）当身份：``dataclasses.replace()`` 产生的副本
会换一个内存地址，归属随即丢失；而 ``name@地址`` 这类拼接键在改名或改地址时断档。
``Ref`` 的分数只来自声明，与地址、与对象生命周期都无关。
"""

from dataclasses import dataclass
from typing import Optional

#: 类型与身份的分隔符（``landmarks:chinese_mix/塔``）
SEP_KIND = ":"
#: 容器与身份的之间的分隔符（``chinese_mix/塔``）
SEP_SCOPE = "/"


@dataclass(frozen=True, order=True)
class Ref:
    """一类资源下的一条内容。``kind`` 是类型 id，``key`` 是类型内身份。"""

    kind: str
    key: str

    def __post_init__(self):
        kind = (self.kind or "").strip()
        key = (self.key or "").strip()
        if not kind or SEP_KIND in kind or SEP_SCOPE in kind:
            raise ValueError(f"非法的资源类型 id: {self.kind!r}")
        if not key or SEP_KIND in key:
            raise ValueError(f"非法的资源身份: {self.key!r}")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "key", key)

    @classmethod
    def scoped(cls, kind: str, scope: str, name: str) -> "Ref":
        """按「类型 + 容器 + 容器内身份」构造。容器为空时退化为仅身份。"""
        scope = (scope or "").strip()
        key = f"{scope}{SEP_SCOPE}{name}" if scope else name
        return cls(kind, key)

    @classmethod
    def parse(cls, text) -> Optional["Ref"]:
        """``text`` → ``Ref``；已是 ``Ref`` 原样返回，无法解析返回 None。"""
        if isinstance(text, cls):
            return text
        kind, sep, key = (text or "").partition(SEP_KIND)
        if not sep:
            return None
        try:
            return cls(kind, key)
        except ValueError:
            return None

    @property
    def text(self) -> str:
        """落盘/比较用的字符串形态。"""
        return f"{self.kind}{SEP_KIND}{self.key}"

    @property
    def scope(self) -> str:
        """容器 id；未带容器时为空串。"""
        head, sep, _ = self.key.partition(SEP_SCOPE)
        return head if sep else ""

    @property
    def name(self) -> str:
        """容器内身份；未带容器时就是整个 key。"""
        _, sep, tail = self.key.partition(SEP_SCOPE)
        return tail if sep else self.key

    def __str__(self) -> str:
        return self.text
