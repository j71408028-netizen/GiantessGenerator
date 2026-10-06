# -*- coding: utf-8 -*-
"""临时冒烟：小游戏子进程链路。

1) 直接以 --auto-result 跑子进程：验证窗口 → js_api.report → 结果文件 → 自动退出；
2) 经 TkHost.launch_mini_game 启动后杀掉子进程：验证 watch 回调 on_result(None)。
"""
import glob
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from paths import data_dir  # noqa: E402

entry = os.path.join(data_dir(), "packs", "minigames", "escape_giantess", "session.html")
assert os.path.isfile(entry), entry

# ---- 1) 子进程自动结算 ----
out1 = os.path.join(tempfile.gettempdir(), "smoke_mg_auto.json")
if os.path.exists(out1):
    os.remove(out1)
child = os.path.join(ROOT, "ui", "common", "mini_game_host.py")
proc = subprocess.Popen(
    [sys.executable, child, entry, "1", out1,
     "--auto-result", json.dumps({"won": True, "level": 1})],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
result = None
for _ in range(60):
    if os.path.isfile(out1):
        try:
            with open(out1, encoding="utf-8") as fh:
                result = json.load(fh)
            break
        except (OSError, ValueError):
            pass
    if proc.poll() is not None and not os.path.isfile(out1):
        break
    time.sleep(0.5)
print("child exit code:", proc.poll(), flush=True)
print("child result:", result, flush=True)
assert result == {"won": True, "level": 1}, "子进程结果回传失败"
for _ in range(10):
    if proc.poll() is not None:
        break
    time.sleep(0.5)
try:
    os.remove(out1)
except OSError:
    pass
print("STEP1_OK", flush=True)

# ---- 2) TkHost 启动 + 杀子进程 = 无结果 ----
from ui.common.tk_host import TkHost  # noqa: E402

got = []
h = TkHost()
assert h.launch_mini_game("escape_giantess", {"target_level": 3}, on_result=got.append)
time.sleep(8)  # 等子进程窗口起来（窗口会短暂出现，属预期）
# 找到刚才拉起的子进程：按命令行特征杀掉（结果文件未写 = 无结果路径）
killed = False
try:
    import ctypes
    from ctypes import wintypes
except Exception:
    ctypes = None
# 简单起见：向所有匹配命令行的 python 进程发 taskkill
ps = subprocess.run(
    ["powershell", "-NoProfile", "-Command",
     "Get-CimInstance Win32_Process | "
     "Where-Object { $_.CommandLine -like '*mini_game_host.py*' } | "
     "Select-Object -ExpandProperty ProcessId"],
    capture_output=True, text=True)
pids = [ln.strip() for ln in ps.stdout.splitlines() if ln.strip().isdigit()]
print("child pids:", pids, flush=True)
for pid in pids:
    subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)
    killed = True
for _ in range(20):
    if got:
        break
    time.sleep(0.5)
print("watch result:", got, flush=True)
assert got and got[0] is None, "杀掉子进程后应回传 None"
print("STEP2_OK", flush=True)
print("SMOKE_DONE", flush=True)
