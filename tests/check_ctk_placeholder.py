"""输入框 placeholder 守卫：带 ``textvariable`` 的 CTkEntry 必须能显示提示词。

用法：``python tests/check_ctk_placeholder.py``

**为什么需要**：上游 customtkinter ``CTkEntry._activate_placeholder`` 的判空条件写作::

    self._textvariable is None or self._textvariable == ""

本意是"变量为空才显示提示词"，但 ``tkinter.Variable.__eq__`` 只在与另一个
``Variable`` 比较时返回布尔，与字符串比较返回 ``NotImplemented``，Python 于是回退到
恒为 ``False`` 的默认比较。结果：**只要传了 ``textvariable``，提示词就永远不显示**；
不传变量的输入框却正常——这正是设置页"只有一种写法能显示"的由来。

第二个坑是上游把提示词直接 ``insert`` 进与变量绑定的原生 Entry：一旦显示成功，写入
回调就会把提示词写进用户变量（屏蔽词、网络代理都会被写脏），保存时读到的是提示词。

修法见 :mod:`ui.ctk.ctk_patch`：显示提示前把 ``textvariable`` 从原生 Entry 上摘下，
提示期间变量保持原值，失焦/输入时再挂回；并补上 ``configure(textvariable=...)``
重绑变量时上游各版本漏迁的写入监听。

本守卫三部分：

1. **装配**：``ui.ctk.ctk_patch`` import 时确实装上了 placeholder 补丁；
2. **契约**（无 Tk 的替身对象驱动）：提示能显示、变量不被污染、外部清空后失焦回提示、
   密码框掩码不被提示吃掉、无变量输入框行为不变；
3. **重绑**：换 ``textvariable`` 后监听只挂一份、提示状态与新变量一致。

退出码：0 = 通过；1 = 存在违规或行为不符。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

failures = []
total = 0


def check(name, ok, extra=""):
    global total
    total += 1
    print(("  OK   " if ok else "  FAIL ") + name + (f"  <{extra}>" if extra and not ok else ""))
    if not ok:
        failures.append(name)


# ---------------- 1. 装配：补丁已装上 ----------------

_SRC = os.path.join(ROOT, "ui", "ctk", "ctk_patch.py")
if not os.path.isfile(_SRC):
    print(f"[check_ctk_placeholder] FAILED 1/1: 缺少 {_SRC}")
    sys.exit(1)

import ui.ctk.ctk_patch as patch  # noqa: E402

from customtkinter.windows.widgets.ctk_entry import CTkEntry  # noqa: E402

check("CTkEntry 的 placeholder 方法已替换为仓库实现",
      getattr(CTkEntry, "_gg_patched_placeholder", False)
      and CTkEntry._activate_placeholder.__module__ == "ui.ctk.ctk_patch",
      CTkEntry._activate_placeholder.__module__)

# 设置页的「屏蔽词 / 网络代理」正是"变量 + 提示词"组合，改动这两处等于挪走回归样本。
_SETTINGS_SRC = open(os.path.join(ROOT, "ui", "settings", "__init__.py"),
                     encoding="utf-8").read()
check("设置页 _make_entry 同时支持 textvariable 与 placeholder_text",
      "def _make_entry(self, parent, variable" in _SETTINGS_SRC
      and "textvariable=variable" in _SETTINGS_SRC
      and "placeholder_text=placeholder_text or \"\"" in _SETTINGS_SRC)
check("设置页保留两个带提示词的输入框（屏蔽词 / 网络代理）",
      "placeholder_text=\"用逗号分隔\"" in _SETTINGS_SRC
      and "placeholder_text=\"留空 = 直连（语音 / AI 共用）\"" in _SETTINGS_SRC)


# ---------------- 2/3. 行为：替身对象驱动，无需显示器 ----------------

class FakeVar:
    """模拟 ``tkinter.StringVar``：get/set + write 监听登记表。"""

    def __init__(self, value=""):
        self._value = value
        self._traces = []
        self._seq = 0

    def get(self):
        return self._value

    def set(self, value):
        self._value = value

    def trace_info(self):
        return [(("write",), name) for name in self._traces]

    def trace_add(self, mode, callback):
        self._seq += 1
        name = f"{self._seq}_{getattr(callback, '__name__', 'cb')}"
        self._traces.append(name)
        return name

    def trace_remove(self, mode, name):
        if name not in self._traces:
            raise ValueError(f"bad trace name {name!r}")
        self._traces.remove(name)


class FakeTkEntry:
    """模拟原生 ``tkinter.Entry``：变量已挂时写入会回填变量（与 Tk 一致）。"""

    def __init__(self):
        self.text = ""
        self.show = ""
        self.var = None

    def cget(self, key):
        return self.show if key == "show" else ""

    def configure(self, **kwargs):
        if "textvariable" in kwargs:
            self.var = kwargs["textvariable"] or None
        if "show" in kwargs:
            self.show = kwargs["show"]

    config = configure

    def __setitem__(self, key, value):
        if key == "show":
            self.show = value
        else:
            raise KeyError(key)

    def delete(self, *args):
        self.text = ""

    def insert(self, index, text):
        self.text = text
        if self.var is not None:
            self.var.set(text)

    def get(self):
        return self.text


class FakeHost:
    """只实现补丁函数需要的那点接口。"""

    # 一律从 CTkEntry 上取（而非直接取 patch 模块的函数）：补丁没装上时跑的就是上游
    # 实现，本节行为断言会立刻变红——守卫盯的是实际生效的行为，不只是"函数被换过"。
    _activate_placeholder = CTkEntry._activate_placeholder
    _deactivate_placeholder = CTkEntry._deactivate_placeholder
    _textvariable_callback = CTkEntry._textvariable_callback
    # 这两个是补丁新增的，上游没有。
    _placeholder_sync = patch._placeholder_sync
    _placeholder_retrace = patch._placeholder_retrace

    def __init__(self, variable=None, placeholder="HINT", show="", focused=True):
        self._entry = FakeTkEntry()
        self._entry.show = show
        self._textvariable = variable
        self._placeholder_text = placeholder
        self._placeholder_text_active = False
        self._pre_placeholder_arguments = {}
        self._is_focused = focused
        self._text_color = "#111111"
        self._placeholder_text_color = "#999999"
        self._textvariable_callback_name = ""

    def _apply_appearance_mode(self, color):
        return color


# 2a. 变量 + 提示词：提示显示，且不写进变量
var = FakeVar("")
host = FakeHost(var)
host._activate_placeholder()
check("带 textvariable 的输入框能显示提示词",
      host._entry.get() == "HINT" and host._placeholder_text_active,
      repr(host._entry.get()))
check("显示提示不污染变量",
      var.get() == "" and host._entry.var is None, repr(var.get()))

# 2b. 有值的变量不显示提示（创建参数面板的身高框就是这种）
filled = FakeHost(FakeVar("1.6"))
filled._activate_placeholder()
check("变量有值时不动输入框",
      filled._entry.get() == "" and not filled._placeholder_text_active)

# 2c. 失焦后外部清空变量 → 补回提示
host._is_focused = False
var.set("http://127.0.0.1:7890")
host._textvariable_callback("name", "write", "write")
check("外部赋值后提示让位",
      host._entry.get() == "" and not host._placeholder_text_active,
      repr(host._entry.get()))
var.set("")
host._textvariable_callback("name", "write", "write")
check("外部清空且失焦后补回提示",
      host._entry.get() == "HINT" and host._placeholder_text_active
      and var.get() == "",
      repr(host._entry.get()))

# 2d. 聚焦中清空不插提示（正在输入时不该被提示词打断）
typing = FakeHost(FakeVar(""))
typing._is_focused = True
typing._textvariable_callback("name", "write", "write")
check("聚焦编辑中不插提示词",
      typing._entry.get() == "" and not typing._placeholder_text_active,
      repr(typing._entry.get()))

# 2e. 收起提示要还原 show 掩码（密码框）
secret = FakeHost(FakeVar(""), placeholder="密钥", show="*")
secret._activate_placeholder()
check("密码框显示提示时临时取消掩码",
      secret._entry.get() == "密钥" and secret._entry.show == "",
      repr(secret._entry.show))
secret._deactivate_placeholder()
check("收起提示后恢复掩码",
      secret._entry.show == "*" and not secret._placeholder_text_active,
      repr(secret._entry.show))

# 2f. 无变量的输入框行为不变（创建参数面板昵称框）
plain = FakeHost(None, placeholder="昵称")
plain._activate_placeholder()
check("无变量输入框仍显示提示", plain._entry.get() == "昵称")
plain._deactivate_placeholder()
plain._entry.insert(0, "风见")
check("无变量输入框正常输入", plain._entry.get() == "风见")

# 2g. 没配 placeholder_text 的输入框不受影响
bare = FakeHost(FakeVar(""), placeholder=None)
bare._activate_placeholder()
check("未配提示词的输入框不动", bare._entry.get() == "" and not bare._placeholder_text_active)


# 3. 重绑 textvariable：监听只挂一份，提示状态跟新变量走
rebind = FakeHost(FakeVar(""))
rebind._activate_placeholder()
new_var = FakeVar("")
patch._placeholder_retrace(rebind, rebind._textvariable, new_var)
rebind._textvariable = new_var
check("重绑后监听挂在新变量上",
      len(new_var.trace_info()) == 1, new_var.trace_info())

# 上游 6.0.0 自带迁移：新变量已有同一监听时不得重复挂载
preset = FakeVar("")
rebind2 = FakeHost(FakeVar(""))
preset.trace_add("write", rebind2._textvariable_callback)
patch._placeholder_retrace(rebind2, rebind2._textvariable, preset)
check("上游已挂监听时不重复挂载",
      len(preset.trace_info()) == 1, preset.trace_info())

# 重绑到空变量：提示保持
sync_host = FakeHost(FakeVar(""), placeholder="留空 = 直连")
sync_host._activate_placeholder()
sync_host._textvariable = FakeVar("")
sync_host._placeholder_sync()
check("重绑到空变量后提示仍在",
      sync_host._entry.get() == "留空 = 直连" and sync_host._placeholder_text_active,
      repr(sync_host._entry.get()))

# 重绑到有值变量：提示收起、显示真实值
sync_host._textvariable = FakeVar("proxy.local")
sync_host._placeholder_sync()
check("重绑到有值变量后收起提示",
      not sync_host._placeholder_text_active and sync_host._entry.get() == "",
      repr(sync_host._entry.get()))


print()
if failures:
    print(f"[check_ctk_placeholder] FAILED {len(failures)}/{total}: " + "; ".join(failures))
    sys.exit(1)
print(f"[check_ctk_placeholder] PASSED {total}/{total}")
