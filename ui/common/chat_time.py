# -*- coding: utf-8 -*-
"""聊天消息的时间标记：按相邻消息的间隔长短决定补不补、补到什么精度。

界面层的纯函数（只依赖 ``datetime``，不碰任何窗口框架），专业模式与挂件
模式的聊天列表都可以直接用：

- :func:`message_moment` 取一条消息在对话里的时间坐标——角色消息优先计划
  展示时间 ``available_at``：应用重启后离线到期的回复会被"延迟结算"成启动
  时刻投递（见 docs/chat_delivery.md），用实际投递时间会把几天的间隔压成
  一瞬，计划时间才是当时的回复时间；
- :func:`time_marker_text` 决定这条消息前要不要补标记：与上一条的间隔短于
  :data:`TIME_MARKER_GAP` 就不补（连续对话每句报时是噪音），超过则按
  :func:`format_time_marker` 分级补——今天只给 ``HH:MM``，跨天补日期，
  跨年再补年份；
- 恢复一段历史（重新打开聊天、跨会话载入）时没有"上一条"，第一条必然带
  标记，用来交代这段对话从什么时候开始。
"""

import datetime
from typing import Optional

#: 相邻消息间隔小于该值时不补时间标记（IM 惯例：几分钟内的连续对话不报时）。
TIME_MARKER_GAP = datetime.timedelta(minutes=5)


def _parse(raw) -> Optional[datetime.datetime]:
    """把 ISO 时间串解析为 datetime；空值或不合法一律返回 None。"""
    if not raw or not isinstance(raw, str):
        return None
    try:
        return datetime.datetime.fromisoformat(raw)
    except ValueError:
        return None


def message_moment(message) -> Optional[datetime.datetime]:
    """一条消息在对话里的时间坐标；取不到时返回 None。

    角色消息按 ``available_at``（计划展示时间）→ ``delivered_at``
    （实际投递）→ ``created_at``（生成）依次回退；玩家消息只有
    ``created_at`` 是准的（发送时刻），排在其后。旧档缺字段、时间串损坏
    都会退到下一个候选，全都取不到才返回 None。
    """
    if message is None:
        return None
    if getattr(message, "role", "") == "user":
        order = ("created_at", "available_at", "delivered_at")
    else:
        order = ("available_at", "delivered_at", "created_at")
    for field in order:
        moment = _parse(getattr(message, field, ""))
        if moment is not None:
            return moment
    return None


def format_time_marker(moment: Optional[datetime.datetime],
                       now: Optional[datetime.datetime] = None) -> str:
    """时间标记文案：今天 ``HH:MM``，昨天 ``昨天 HH:MM``，今年
    ``M月D日 HH:MM``，更早 ``YYYY年M月D日 HH:MM``。

    只给出与"现在"的距离必需的精度：同一天里日期是废话，跨年时不带年份
    又会被误读成今年。未来时间（时钟偏差 / 计划时间未到）按今天处理。
    """
    if moment is None:
        return ""
    now = now or datetime.datetime.now()
    days = (now.date() - moment.date()).days
    clock = moment.strftime("%H:%M")
    if days <= 0:
        return clock
    if days == 1:
        return f"昨天 {clock}"
    if moment.year == now.year:
        return f"{moment.month}月{moment.day}日 {clock}"
    return f"{moment.year}年{moment.month}月{moment.day}日 {clock}"


def time_marker_text(previous: Optional[datetime.datetime],
                     moment: Optional[datetime.datetime],
                     now: Optional[datetime.datetime] = None) -> str:
    """该给当前消息补的标记文案；不需要补时返回空串。

    ``previous`` 是上一条**已渲染**消息的时间坐标（None = 这段历史的开头，
    必然补标记）；``moment`` 是当前消息的时间坐标（None = 无从判断，不补）。
    """
    if moment is None:
        return ""
    if previous is not None and (moment - previous) < TIME_MARKER_GAP:
        return ""
    return format_time_marker(moment, now)
