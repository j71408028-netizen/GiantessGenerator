"""副本子进程的 DPI 感知检查（仅 Windows 有信息量，其余平台直接通过）。

用法：``python tests/check_dungeon_child_dpi.py``

副本会话运行在独立子进程里（``ui.common.dungeon_spawner``），而 DPI 感知是
**进程级**的、不随 multiprocessing 继承：主进程里由 CTk 建根窗口时开启的感知
子进程拿不到。子进程若在父进程感知时停留在无感知状态，Windows 会把 DPG 视口
的 96-DPI 逻辑坐标按系统缩放再放大一次——窗口与字体整体巨大（175% 缩放的
机器上实测约 3 倍，2026-10-11）。反向也不行：独立挂件版的父进程刻意无感知
（``ui.mini.dpi``），子进程若擅自感知，视口会比主窗口小一圈。

因此子进程的感知档位**镜像父进程**（载荷 ``dpi_aware`` → 子进程
``_ensure_dpi_awareness``）。本检查拉起两个探针子进程验证这条链路：

- 无感知探针（``dpi_aware=False`` 的等价路径）：``GetDpiForSystem()`` 必为 96；
- 感知探针（``dpi_aware=True``）：``GetDpiForSystem()`` 必须等于注册表
  ``AppliedDPI``（系统级缩放真值，与进程自身感知无关）。

在 100% 缩放的机器上 AppliedDPI=96，两组探针值相同，检查退化为机制冒烟；
在 >100% 缩放的机器上（如本机 168），谁删掉了子进程的感知设置，感知探针会
跌回 96，本检查立即红——这就是回归锁。

退出码：0 = 通过；1 = 存在断言失败。
"""

import subprocess
import sys

if not sys.platform.startswith("win"):
    print("[check_dungeon_child_dpi] 非 Windows 平台，DPI 感知不适用，跳过")
    sys.exit(0)

import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_checked = 0


def check(cond, message):
    global _checked
    _checked += 1
    if not cond:
        print(f"[FAIL] {message}")
        raise SystemExit(1)


_PROBE = (
    "import sys, ctypes\n"
    "sys.path.insert(0, {root!r})\n"
    "{set_aware}"
    "print(ctypes.windll.user32.GetDpiForSystem())\n"
)

_SET_AWARE = (
    "from ui.common.dungeon_child_host import _ensure_dpi_awareness\n"
    "_ensure_dpi_awareness()\n"
)


def _probe(aware: bool) -> int:
    """拉起一个全新 Python 进程，返回其 ``GetDpiForSystem()``。"""
    code = _PROBE.format(root=os.path.dirname(os.path.abspath(__file__)) + "/..",
                         set_aware=_SET_AWARE if aware else "")
    proc = subprocess.run([sys.executable, "-X", "utf8", "-c", code],
                          capture_output=True, text=True, encoding="utf-8",
                          timeout=60)
    check(proc.returncode == 0,
          f"探针子进程异常退出 rc={proc.returncode}: {proc.stderr[-200:]}")
    try:
        return int(proc.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        check(False, f"探针子进程输出无法解析: {proc.stdout!r}")
        raise


def _applied_dpi() -> int:
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

    check(unaware == 96,
          f"无感知探针应报 96（实际 {unaware}）")
    check(aware == applied,
          f"感知探针应等于系统缩放真值 AppliedDPI={applied}（实际 {aware}）"
          "——子进程的 DPI 感知设置丢了？")
    if applied == 96:
        print("[check_dungeon_child_dpi] 本机缩放 100%，探针值无区分度"
              "（机制冒烟通过）；回归锁只在 >100% 缩放的机器上生效")
    else:
        print(f"[check_dungeon_child_dpi] 系统缩放 {applied}/96："
              f"无感知探针 96，感知探针 {aware}——子进程感知链路正常")
    print(f"PASSED ({_checked} assertions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
