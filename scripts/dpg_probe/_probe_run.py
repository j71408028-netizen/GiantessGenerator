"""子进程探针：带超时跑一条命令，报告原生退出码（0xC0000005 等）。

用法：python _probe_run.py <超时秒> <命令...>

完整报告同时写入 _out/dpg_probe/_probe_last.txt（与 CWD 无关，_probe_report.py 读同一份）。
"""
import subprocess
import sys
import time

import _outdir  # 同目录探针共享的产物目录（_out/dpg_probe/）

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_REPORT = _outdir.out("_probe_last.txt")

timeout = float(sys.argv[1])
cmd = sys.argv[2:]

names = {
    0xC0000005: "ACCESS_VIOLATION",
    0xC0000409: "STACK_BUFFER_OVERRUN/fastfail",
    0xC00000FD: "STACK_OVERFLOW",
    0xC0000374: "HEAP_CORRUPTION",
}

t0 = time.time()
try:
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    rc = proc.returncode
    out = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace")
    timed_out = False
except subprocess.TimeoutExpired as exc:
    rc = "TIMEOUT"
    out = (exc.stdout or b"").decode("utf-8", "replace")
    err = (exc.stderr or b"").decode("utf-8", "replace")
    timed_out = True

elapsed = time.time() - t0
lines = [f"=== rc={rc} elapsed={elapsed:.1f}s timeout={timed_out} ==="]
if isinstance(rc, int) and rc < 0:
    code = rc & 0xFFFFFFFF
    lines.append(f"=== native=0x{code:08X} ({names.get(code, '?')}) ===")
lines.append("--- stdout ---")
lines.append(out[-4000:])
lines.append("--- stderr ---")
lines.append(err[-20000:])
report = "\n".join(lines)
print(report)
with open(_REPORT, "w", encoding="utf-8", errors="replace") as fh:
    fh.write(report)
print(f"[_probe_run] 报告已写入 {_REPORT}")
