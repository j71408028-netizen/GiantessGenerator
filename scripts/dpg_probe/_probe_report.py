"""从 _probe_last.txt 给出紧凑结论：rc + 是否越过 render#1 + 帧数。

读的是 _out/dpg_probe/_probe_last.txt（由 _probe_run.py 写），与 CWD 无关。

用法：python _probe_report.py [标签]
"""
import re
import sys

import _outdir  # 同目录探针共享的产物目录（_out/dpg_probe/）

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_REPORT = _outdir.out("_probe_last.txt")
text = open(_REPORT, encoding="utf-8", errors="replace").read()
label = sys.argv[1] if len(sys.argv) > 1 else ""
rc = re.search(r"rc=(\S+)", text)
elapsed = re.search(r"elapsed=(\S+)", text)
frames = len(re.findall(r"render frame \d+ end", text))
begins = len(re.findall(r"render frame \d+ begin", text))
tail = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("[trace")][-1:]
verdict = "CRASH" if "3221225477" in text else (
    "TIMEOUT" if "timeout=True" in text else "EXIT")
print(f"{label:42s} rc={rc.group(1) if rc else '?':>12s} "
      f"t={elapsed.group(1) if elapsed else '?':>6s} "
      f"frames_done={frames:4d} begins={begins:4d} -> {verdict}")
print(f"    last: {tail[0] if tail else '(none)'}")
