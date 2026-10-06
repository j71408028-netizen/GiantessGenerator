"""外观模式（亮 / 暗）的唯一来源。

纯 tkinter 没有「外观模式」这回事：每个控件在创建时就把颜色定死了，切换主题必须
自己重刷。本模块因此只做两件事——记住当前模式、在模式变化时广播一次——具体怎么
重刷由订阅者决定（见 ``ui.mini.pixel`` 的配色登记表与 ``services.preview``）。
"""

MODE_LIGHT = "Light"
MODE_DARK = "Dark"

_MODE = {"value": MODE_DARK}
_subscribers = []


def get_mode() -> str:
    """当前模式（"Light" / "Dark"）。"""
    return _MODE["value"]


def is_dark() -> bool:
    return _MODE["value"] == MODE_DARK


def set_mode(mode: str):
    """设置模式；与当前一致时不广播，避免无谓的整窗重刷。"""
    mode = MODE_DARK if str(mode).lower() == "dark" else MODE_LIGHT
    if mode == _MODE["value"]:
        return
    _MODE["value"] = mode
    for callback in list(_subscribers):
        try:
            callback()
        except Exception:
            continue


def subscribe(callback):
    """登记一个模式变化回调（重复登记会去重）。"""
    if callback not in _subscribers:
        _subscribers.append(callback)


def resolve(color_value):
    """把 ``(亮色, 暗色)`` 二元组按当前模式取成单色；单色值原样返回。

    ``ui.common.theme`` 里的色表都是二元组，纯 tkinter 控件只接受具体颜色，
    取值时统一经由此函数。
    """
    if isinstance(color_value, (tuple, list)) and len(color_value) == 2:
        return color_value[1] if is_dark() else color_value[0]
    return color_value
