"""界面模式的取值与「启动界面模式」偏好的读写。

两套界面（专业 ``ui/*``、挂件 ``ui/mini/*``）与外壳 ``app.shell`` 共用这一份词表。
它住在 ``services/`` 是为了让 **ui 层能直接读写这个偏好，而不必 import 应用外壳**
（``ui -> app`` 是越界边，2026-10-06 阶段 3.2.2 前由设置页与挂件标题栏各踩一次）。

为什么不是「把值从外壳注入 ui」：设置页上的这个选项与同页的主题、字体、伤亡开关
一样，本质是**一个设置项**——读 ``settings``、写 ``settings``；而 ``app_shell`` 里
真正属于外壳的是**切换机制**（销毁根窗口 + 登记请求，见 :func:`app.shell.switch_to`），
那个仍然由外壳注入（挂件标题栏的「⇄」走的是注入回调）。

两个键的语义不同，不要混用：

- ``UI_MODE_KEY``（``ui_mode``）：**实际所在**的界面模式，由 ``app.shell.switch_to``
  每次切换时落盘；「退出时模式」这个启动偏好读的就是它。
- ``STARTUP_MODE_KEY``（``ui_startup``）：**启动偏好**，取值
  ``MODE_PRO`` / ``MODE_MINI`` / ``STARTUP_EXIT``，只由设置页写入。
"""

MODE_PRO = "pro"
MODE_MINI = "mini"

#: 「退出时模式」：启动偏好的一种——跟随上次退出时所在的界面
STARTUP_EXIT = "exit"

#: 界面模式在 settings 里的键；记录的是**实际所在**的模式（切换界面时落盘）。
UI_MODE_KEY = "ui_mode"

#: 启动偏好在 settings 里的键；取值 MODE_PRO / MODE_MINI / STARTUP_EXIT。
STARTUP_MODE_KEY = "ui_startup"

#: 设置页「启动界面模式」的下拉项：显示文案 -> 落盘取值。界面层用它渲染，
#: 外壳用它解析，两边共用一份，避免文案与取值各写一处而漂移。
STARTUP_CHOICES = (
    ("专业模式", MODE_PRO),
    ("ME模式", MODE_MINI),
    ("退出时模式", STARTUP_EXIT),
)

_BY_LABEL = dict(STARTUP_CHOICES)
_BY_PREF = {pref: label for label, pref in STARTUP_CHOICES}


def label_of(pref: str) -> str:
    """把落盘取值翻成设置页显示文案；未知取值按「退出时模式」显示。"""
    return _BY_PREF.get(pref, _BY_PREF[STARTUP_EXIT])


def pref_of_label(label: str) -> str:
    """把设置页显示文案翻成落盘取值；未知文案按「退出时模式」处理。"""
    return _BY_LABEL.get(label, STARTUP_EXIT)


def load_mode(default: str = MODE_PRO) -> str:
    """读取设置里记录的界面模式。"""
    from persistence import SettingsRepo
    try:
        mode = (SettingsRepo().load() or {}).get(UI_MODE_KEY)
    except Exception:
        mode = None
    return mode if mode in (MODE_PRO, MODE_MINI) else default


def save_mode(mode: str) -> None:
    """把界面模式写入设置，下次启动沿用。失败只打印告警，不阻断切换。"""
    from persistence import SettingsRepo
    try:
        repo = SettingsRepo()
        settings = repo.load()
        settings[UI_MODE_KEY] = mode
        repo.save(settings)
    except Exception as e:
        print(f"[Warning] 保存界面模式失败: {e}")


def load_startup_mode() -> str:
    """读取设置里的启动偏好；未设置或缺省按「退出时模式」处理。"""
    from persistence import SettingsRepo
    try:
        pref = (SettingsRepo().load() or {}).get(STARTUP_MODE_KEY)
    except Exception:
        pref = None
    return pref if pref in (MODE_PRO, MODE_MINI, STARTUP_EXIT) else STARTUP_EXIT


def save_startup_mode(pref: str) -> None:
    """把启动偏好写入设置。失败只打印告警，不阻断。"""
    from persistence import SettingsRepo
    try:
        repo = SettingsRepo()
        settings = repo.load()
        settings[STARTUP_MODE_KEY] = pref
        repo.save(settings)
    except Exception as e:
        print(f"[Warning] 保存启动界面模式失败: {e}")


def resolve_startup_mode(default: str = MODE_PRO) -> str:
    """按启动偏好解析出本次启动的界面模式。

    - 偏好是固定模式（专业/ME）：直接用它，**不看** ``ui_mode``——这是固定
      偏好的意义所在，中途切换过界面也不影响下次启动；
    - 偏好是「退出时模式」（含未设置的老存档）：读 ``ui_mode``。它由
      ``app.shell.switch_to`` 在每次切换时落盘，正常退出后保留的正是退出时所在的
      界面，无需在关闭流程里再补一次（两个关闭协议都以 ``os._exit(0)`` 收尾，
      关闭钩子里落盘本就不可靠）。

    两套都解析不出来时回退到 ``default``。
    """
    pref = load_startup_mode()
    if pref in (MODE_PRO, MODE_MINI):
        return pref
    mode = load_mode(default)
    return mode if mode in (MODE_PRO, MODE_MINI) else default
