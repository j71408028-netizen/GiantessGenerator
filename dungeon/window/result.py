"""会话结果对象（L4）。

``DungeonSessionWindow.run()`` 的返回值。在此之前调用方要"new 一个类 + 读私有
属性"（``window._launch_error`` / ``window._launch_choice``）才能知道这一局发生了
什么——控制流散在 UI 层，既不显眼也难测。现在一律返回 :class:`SessionResult`：

::

    result = DungeonSessionWindow(..., host=TkHost(self)).run()
    if result.failed:
        dialogs.showerror("错误", result.launch_error)
    elif result.succeeded:
        ...

``reason`` 是唯一的"发生了什么"，其余字段是细节；:attr:`succeeded` /
:attr:`failed` / :attr:`cancelled` 只是它的三组别名，避免到处写字符串比较。
"""

#: 会话真的跑起来了（走到结局 / 中途退出 / 回放播完都算）
REASON_SESSION_ENDED = "session-ended"
#: 入口页点了"返回"（或没选方案就关窗）：没进过会话，不应产生任何内容
REASON_ENTRY_CANCELLED = "entry-cancelled"
#: 进了入口页但没能进入会话：配置加载失败 / 校验有错误 / 行动点数不足
REASON_LAUNCH_FAILED = "launch-failed"
#: 同进程里已有会话在跑，这次启动被忽略（DPG 上下文是全局单例语义）
REASON_ALREADY_RUNNING = "already-running"


class SessionResult:
    """一局副本的结果快照。**只读**：构造完就不再变化。

    参数与属性一一对应。只有 ``reason`` 必填，其余都有缺省值，因此新的调用场景
    可以自由扩展字段而不必改动既有代码。
    """

    __slots__ = ("reason", "launch_error", "launch_choice", "scenario_id",
                 "ended", "is_replay", "replay_path", "report_path",
                 "frames_rendered")

    def __init__(self, reason, *, launch_error="", launch_choice=None,
                 scenario_id="", ended=False, is_replay=False,
                 replay_path="", report_path="", frames_rendered=0):
        #: 结果分类（``REASON_*``）
        self.reason = reason
        #: 启动失败的原因（配置缺失/校验错误/行动点数不足）；可直接弹给用户
        self.launch_error = launch_error or ""
        #: 入口页的选择：副本方案 id，或 ``REPLAY_MARK``（选了加载回放）
        self.launch_choice = launch_choice
        #: 实际进入的副本方案 id
        self.scenario_id = scenario_id or ""
        #: 是否走到了结局（结算过结局增量）
        self.ended = bool(ended)
        #: 本次是否为回放会话
        self.is_replay = bool(is_replay)
        #: 落盘的回放文件路径（未保存则为空串）
        self.replay_path = replay_path or ""
        #: 落盘的报告文件路径（未保存则为空串）
        self.report_path = report_path or ""
        #: 本会话渲染的帧数（自检/调试用）
        self.frames_rendered = int(frames_rendered)

    # ---------------- 便捷判定 ----------------
    @property
    def succeeded(self) -> bool:
        """会话阶段真的起来了（无论是否走到结局）。"""
        return self.reason == REASON_SESSION_ENDED

    @property
    def failed(self) -> bool:
        """入口选择失败：调用方一般要把 :attr:`launch_error` 弹出来。"""
        return self.reason == REASON_LAUNCH_FAILED

    @property
    def cancelled(self) -> bool:
        """入口页直接返回/关窗：没进会话，不该有任何副作用。"""
        return self.reason == REASON_ENTRY_CANCELLED

    def __bool__(self) -> bool:
        """``if result:`` 等价于 :attr:`succeeded`。"""
        return self.succeeded

    def __repr__(self) -> str:
        fields = " ".join(f"{name}={getattr(self, name)!r}"
                          for name in self.__slots__ if getattr(self, name))
        return f"<SessionResult {self.reason} {fields}>"


__all__ = ["SessionResult", "REASON_SESSION_ENDED", "REASON_ENTRY_CANCELLED",
           "REASON_LAUNCH_FAILED", "REASON_ALREADY_RUNNING"]
