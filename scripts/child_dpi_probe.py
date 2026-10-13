"""副本子进程 DPI 感知探针（手工诊断用，**不判红**）。

用法：``python scripts/child_dpi_probe.py``

**为什么在 scripts/ 而不是 tests/**：它原先那套断言的前提在真机上不成立，当门禁
只会长期报红（见文末"怎么读"）。留着是作为诊断现场的探针，不是回归锁。

背景：副本会话运行在独立子进程里（``ui.hosting.dungeon_spawner``），而 DPI 感知是
**进程级**的、不随 multiprocessing 继承：主进程里由 CTk 建根窗口时开启的感知，
子进程拿不到。子进程若在父进程感知时停留在无感知状态，Windows 会把 DPG 视口的
96-DPI 逻辑坐标按系统缩放再放大一次——窗口与字体整体巨大（175% 缩放的机器上实测
约 3 倍，2026-10-11）。反向也不行：独立挂件版的父进程刻意无感知（``ui.mini.dpi``），
子进程若擅自感知，视口会比主窗口小一圈。

因此子进程的感知档位**镜像父进程**（载荷 ``dpi_aware`` → 子进程
``_ensure_dpi_awareness``）。本探针拉起两个子进程，把三个数并排打印出来供人工对照：

- 无感知探针（``dpi_aware=False`` 的等价路径）：``GetDpiForSystem()`` 应为 96；
- 感知探针（``dpi_aware=True``，走 PER_MONITOR_AWARE_V2）：``GetDpiForSystem()``
  返回**当前监视器**的 DPI；
- 注册表 ``AppliedDPI``：系统级缩放的注册值。

**怎么读**：无感知探针恒为 96，感知探针在 >100% 缩放的机器上应明显大于 96——两者
拉开差距就说明子进程的感知档位确实跟着父进程走了。三者相等只意味着这台机器上把
感知开关删掉也不会有区别（100% 缩放，无区分度）。

⚠️ 三者并不总是相等：本机 ``AppliedDPI=168``（175%）而感知探针得 ``192``（200%），
所以"感知探针应等于 AppliedDPI"这个断言的前提本身不成立——那是它当初在 ``tests/``
里长期报红的原因，不是 bug。

退出码恒为 0（探针不判红）。
"""

import os
import subprocess
import sys

if not sys.platform.startswith("win"):
    print("[child_dpi_probe] 非 Windows 平台，DPI 感知不适用")
    sys.exit(0)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_PROBE = (
    "import sys, ctypes\n"
    "sys.path.insert(0, {root!r})\n"
    "{set_aware}"
    "print(ctypes.windll.user32.GetDpiForSystem())\n"
)

_SET_AWARE = (
    "from ui.hosting.dungeon_child_host import _ensure_dpi_awareness\n"
    "_ensure_dpi_awareness()\n"
)


def _probe(aware: bool):
    """拉起一个全新 Python 进程，返回其 ``GetDpiForSystem()``；失败返回 None。"""
    code = _PROBE.format(root=os.path.dirname(os.path.abspath(__file__)) + "/..",
                         set_aware=_SET_AWARE if aware else "")
    proc = subprocess.run([sys.executable, "-X", "utf8", "-c", code],
                          capture_output=True, text=True, encoding="utf-8",
                          timeout=60)
    if proc.returncode != 0:
        print(f"  [warn] 探针子进程异常退出 rc={proc.returncode}: "
              f"{proc.stderr[-200:]}")
        return None
    try:
        return int(proc.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        print(f"  [warn] 探针子进程输出无法解析: {proc.stdout!r}")
        return None


def _applied_dpi():
    """注册表里的系统级缩放真值（与查询进程自身的感知状态无关）。"""
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                        r"Control Panel\Desktop\WindowMetrics") as key:
        value, _ = winreg.QueryValueEx(key, "AppliedDPI")
    return int(value)


def main():
    applied = _applied_dpi()
    unaware = _probe(aware=False)
    aware = _probe(aware=True)

    print(f"[child_dpi_probe] 注册表 AppliedDPI         = {applied}"
          f"（{applied / 96:.0%} 缩放）")
    print(f"[child_dpi_probe] 无感知探针 GetDpiForSystem = {unaware}"
          "（应恒为 96）")
    print(f"[child_dpi_probe] 感知探针   GetDpiForSystem = {aware}")

    if applied == 96:
        print("[child_dpi_probe] 本机缩放 100%，三个值本就无区分度；这条链路"
              "只在 >100% 缩放的 Windows 上才看得出差别")
    elif unaware == 96 and aware is not None and aware > 96:
        print(f"[child_dpi_probe] 子进程感知档位跟随父进程：无感知 {unaware}，"
              f"感知 {aware}——链路正常")
    else:
        print("[child_dpi_probe] 两档探针没拉开差距：确认子进程是否丢了"
              " _ensure_dpi_awareness（对照 docs/Dungeon/window_host.md §3）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
