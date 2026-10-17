"""GPU 纹理句柄（``hello_imgui.TextureGpu``）的释放口径。

``TextureGpu`` 是 **RAII** 句柄：丢掉最后一个 Python 引用就会去释放底层 GL 纹理，
因此**只能在 GL 上下文还活着的时候丢**——帧回调里，或 ``before_exit`` 钩子里
（hello_imgui 已决定收尾，但后端与上下文仍在；见 ``runner.WindowRunner``）。

``hello_imgui.run()`` 返回之后，GL 后端与 ImGui 上下文已随库内部释放
（见 ``base.DungeonWindowBase._finish_session`` 的说明）。此时再丢引用，析构会去
解引用已经失效的后端，在驱动层直接段错误——P8 之后 Linux 上**每局副本收尾必崩**、
且与软/硬 GL 无关，就是这么来的（最小复现：回调里建纹理、``run()`` 返回后丢引用）。
坏味道在于容器：清空一个 ``{key: TextureGpu}`` / ``[TextureGpu]`` 就等于丢引用，
而"清空容器"看着人畜无害，很容易被写在 ``_finish_session`` 里。

于是约定两条出口，窗口层的六处句柄容器（背景 / 窗口工具条 / 结局图标 / 覆盖层 /
组件工具条 / 小游戏贴图）一律走它们：

``release(handles)``
    **正常路径**：在 GL 存活时把句柄交出去——返回后这些引用不再被任何容器持有，
    RAII 析构当场跑完，GPU 资源真正释放。

``retire(handles)``
    **兜底**：GL 已失效时才用（``before_exit`` 没走到的异常收尾）。把句柄挂进
    进程级仓库**保住引用不让析构跑**——宁可把这几张显存留到进程结束，也不要在
    收尾期段错误。正常路径不会用到它，真用到时窗口会记一条告警日志。
"""

#: 退役句柄的进程级仓库。只在"GL 已经没了"的兜底路径上增长；正常路径为空。
_RETIRED = []


def release(handles) -> int:
    """在 GL 存活时释放 ``handles`` 里的句柄（丢弃引用 ⇒ RAII 析构 ⇒ 释放显存）。

    传入的列表会被就地清空：清空之后本进程不再持有这些句柄，析构当场跑完。
    写成函数是为了给这条口径一个可 grep 的名字——调用点读起来是"这里在释放
    GPU 资源"，而不是"这里莫名清了个列表"。返回放掉的句柄数（0 = 本来就没有，
    调用方不必再判空）。
    """
    if not isinstance(handles, list):
        handles = list(handles or ())
    count = sum(1 for handle in handles if handle is not None)
    handles.clear()
    return count


def retire(handles) -> int:
    """GL 已失效时的兜底：把 ``handles`` 移进进程级仓库，保住引用不让析构跑。

    调用方**必须**已经把这些句柄从自己的容器里摘掉（否则容器之后清空时会再丢一次
    引用，照样崩）。返回仓库当前持有的句柄总数，供调用方判断是否真的走到了兜底。
    """
    for handle in handles or ():
        if handle is not None:
            _RETIRED.append(handle)
    return len(_RETIRED)


def retired_count() -> int:
    """当前退役仓库里的句柄数（自检与诊断用；正常路径恒为 0）。"""
    return len(_RETIRED)


__all__ = ["release", "retire", "retired_count"]
