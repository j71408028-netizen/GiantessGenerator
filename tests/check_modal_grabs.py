"""模态抓取守卫：对话框不得在构造期直接 ``grab_set()``。

用法：``python tests/check_modal_grabs.py``

**为什么需要**（Linux 实测 bug，Windows 不复现）：``BaseDialog.__init__`` 会先
``withdraw()``（避免默认位置 / 浅色标题栏闪现），此时窗口在 X 服务端还没被映射。
X11 的 ``grab_set`` 要求窗口 viewable，于是

    _tkinter.TclError: grab failed: window not viewable

在点击「文本管理器 → 二级卡片 → 地标编辑框」时直接抛出；Windows 的 grab 实现不校验
viewable，所以同样的代码在 Windows 上一切正常，直到有人在 Linux 上点。

正确写法是 :meth:`ui.common.dialogs.BaseDialog._grab_deferred`：先试一次即时抓取
（Windows/macOS 保持原有行为），失败就等 ``<Map>`` 事件补做——窗口被映射时必然触发。

本守卫两部分：

1. **静态**：``ui/`` 下除 ``ui/common/dialogs.py``（延迟抓取的实现处）外，不得出现
   裸 ``.grab_set(``；
2. **行为**：用不依赖 Tk 的桩对象验证 ``_grab_deferred`` / ``_try_pending_grab`` 的
   判定分支——失败要挂起、不可见不消耗重试、可见后成功即停、连续失败有上限。

退出码：0 = 通过；1 = 存在违规或行为不符。
"""
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_DIR = os.path.join(ROOT, "ui")
#: 延迟抓取的实现处：只有这里可以直接调 grab_set
ALLOWED_FILES = {os.path.join("ui", "common", "dialogs.py")}

failures = []
total = 0


def check(name, ok, extra=""):
    global total
    total += 1
    print(("  OK   " if ok else "  FAIL ") + name + (f"  <{extra}>" if extra and not ok else ""))
    if not ok:
        failures.append(name)


# ---------------- 1. 静态：ui/ 下不得裸调 grab_set ----------------

def scan_raw_grabs():
    """返回 [(相对路径, 行号, 源码行)]：直接调用 grab_set 的位置。"""
    found = []
    for dirpath, dirnames, filenames in os.walk(UI_DIR):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            path = os.path.join(dirpath, filename)
            relative = os.path.relpath(path, ROOT)
            try:
                source = open(path, encoding="utf-8").read()
            except OSError:
                continue
            if relative in ALLOWED_FILES:
                continue
            tree = ast.parse(source, filename=path)
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "grab_set"):
                    found.append((relative, node.lineno))
    return found


raw = scan_raw_grabs()
check("ui/ 下没有裸 grab_set（构造期直接抓会在 X11 抛 window not viewable）",
      not raw, raw)

# ---------------- 2. 行为：_grab_deferred 的判定分支 ----------------

import tkinter as tk  # noqa: E402

from ui.common.dialogs import BaseDialog  # noqa: E402


class FakeDialog:
    """只实现 _grab_deferred / _try_pending_grab 需要的那点接口。"""

    _GRAB_MAX_ATTEMPTS = BaseDialog._GRAB_MAX_ATTEMPTS
    _grab_deferred = BaseDialog._grab_deferred
    _try_pending_grab = BaseDialog._try_pending_grab

    def __init__(self, *, viewable=True, exists=True, grab_ok=True):
        self._grab_pending = False
        self._grab_attempts = 0
        self._viewable = viewable
        self._exists = exists
        self._grab_ok = grab_ok
        self.grab_calls = 0

    def grab_set(self):
        self.grab_calls += 1
        if not self._grab_ok:
            raise tk.TclError("grab failed: window not viewable")

    def winfo_exists(self):
        return self._exists

    def winfo_viewable(self):
        return self._viewable


# 2a. 即时抓取成功：不挂起、不再重试
ok_case = FakeDialog(grab_ok=True)
ok_case._grab_deferred()
check("即时抓取成功即结束（不挂起）",
      ok_case.grab_calls == 1 and ok_case._grab_pending is False)

# 2b. 即时失败 + 窗口可见：<Map> 回补成功即停
retry = FakeDialog(grab_ok=False)
retry._grab_attempts = 0
try:
    retry.grab_set()                     # 先失败一次（等价于构造期那次）
except tk.TclError:
    pass
retry._grab_pending = True
retry._grab_ok = True
retry._try_pending_grab()
check("失败挂起后，可见时补抓成功即停",
      retry._grab_pending is False and retry.grab_calls == 2)

# 2c. 还没映射：不抓、也不消耗重试次数
pending = FakeDialog(viewable=False, grab_ok=False)
pending._grab_pending = True
pending._try_pending_grab()
check("<Map> 时若仍不可见：不抓取、不消耗重试",
      pending.grab_calls == 0 and pending._grab_attempts == 0
      and pending._grab_pending is True)

# 2d. 可见但一直抓不上：有上限，最终放弃（不再无限重试）
stuck = FakeDialog(viewable=True, grab_ok=False)
stuck._grab_pending = True
for _ in range(BaseDialog._GRAB_MAX_ATTEMPTS + 3):
    stuck._try_pending_grab()
check(f"连续失败最多试 {BaseDialog._GRAB_MAX_ATTEMPTS} 次后放弃",
      stuck.grab_calls == BaseDialog._GRAB_MAX_ATTEMPTS
      and stuck._grab_pending is False)

# 2e. 窗口已销毁：直接放弃
gone = FakeDialog(exists=False, grab_ok=False)
gone._grab_pending = True
gone._try_pending_grab()
check("窗口已销毁则放弃挂起抓取",
      gone._grab_pending is False and gone.grab_calls == 0)

# 2f. 没有挂起时是空操作（<Map> 每次都会调它，不能有副作用）
idle = FakeDialog()
idle._try_pending_grab()
check("未挂起时 _try_pending_grab 是空操作", idle.grab_calls == 0)


print()
if failures:
    print(f"[check_modal_grabs] FAILED {len(failures)}/{total}: " + "; ".join(failures))
    sys.exit(1)
print(f"[check_modal_grabs] PASSED {total}/{total}")
