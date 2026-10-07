"""Report project-level Windows-specific API usage for cross-platform review.

Run this script before a macOS release. It does not fail merely because a
Windows API exists: platform-guarded calls are valid. Instead, it reports the
locations so that each new use can be reviewed deliberately.

它同时守一条**会返回非零退出码**的跨平台约束：模态对话框不得在构造期直接
``grab_set()``（见 ``check_modal_grabs``）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {".venv", "__pycache__", ".idea", "build", "dist"}
SAFE_PLATFORM_CONFIG_FILES = {Path("ui/common/fonts.py")}
PATTERN = re.compile(
    r"ctypes\.windll|ctypes\.WinDLL|os\.startfile|"
    r"(?:[A-Za-z]:[\\/].*?\.exe)|%LOCALAPPDATA%|"
    r"\bwin32\b|\bWindows\\\\"
)
GUARD_PATTERN = re.compile(
    r"sys\.platform\.startswith\([\"']win|"
    r"platform\.system\(\)\s*==\s*[\"']Windows|"
    r"(?:self\.)?_is_windows|"
    r"getattr\([^\n]*_is_windows"
)

#: 允许直接 ``grab_set()`` 的文件：都在 ``BaseDialog`` 的延迟抓取实现里
#: （``_grab_deferred`` / ``_try_pending_grab``）。其余界面代码一律走
#: ``BaseDialog._grab_deferred()``。
MODAL_GRAB_ALLOWED = {Path("ui/common/dialogs.py")}
GRAB_SET_PATTERN = re.compile(r"\.grab_set\s*\(")


def is_excluded(path: Path) -> bool:
    return any(part in EXCLUDED_PARTS for part in path.parts)


def is_platform_guarded(lines: list[str], index: int) -> bool:
    """Use nearby source as a lightweight guard heuristic."""
    start = max(0, index - 18)
    nearby = lines[start:index + 1]
    return any(GUARD_PATTERN.search(line) for line in nearby) or any(
        'shutil.which("dot")' in line for line in nearby
    )


def check_modal_grabs() -> list[str]:
    """模态对话框不得在构造期直接 ``grab_set()``，返回违规清单。

    为什么这是一条**跨平台**约束：``BaseDialog.__init__`` 会先 ``withdraw()``
    （避免默认位置 / 浅色标题栏闪现），此时窗口在 X 服务端还没映射。X11 的
    ``grab_set`` 要求窗口 viewable，直接抓会抛
    ``TclError: grab failed: window not viewable``（Windows 的实现不校验，
    所以这个 bug 只在 Linux 上现形）。
    正确写法是 ``BaseDialog._grab_deferred()``：先试一次即时抓取，失败就等
    ``<Map>`` 事件（窗口被映射时必然触发）再补做。
    """
    violations = []
    for path in sorted((ROOT / "ui").rglob("*.py")):
        relative_path = path.relative_to(ROOT)
        if relative_path in MODAL_GRAB_ALLOWED or is_excluded(path):
            continue
        for index, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if GRAB_SET_PATTERN.search(line) and not line.lstrip().startswith("#"):
                violations.append(f"{relative_path}:{index}: {line.strip()}")
    return violations


def main() -> int:
    findings = []
    for path in ROOT.rglob("*.py"):
        relative_path = path.relative_to(ROOT)
        if path == Path(__file__) or relative_path in SAFE_PLATFORM_CONFIG_FILES or is_excluded(path):
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for index, line in enumerate(lines):
            if PATTERN.search(line):
                findings.append((relative_path, index + 1, line.strip(),
                                 is_platform_guarded(lines, index)))

    violations = check_modal_grabs()

    if not findings:
        print("No Windows-specific API or path patterns found.")
    else:
        print("Windows-specific code review points:")
        for path, line, text, guarded in findings:
            status = "guarded" if guarded else "REVIEW"
            print(f"[{status}] {path}:{line}: {text}")
        review_count = sum(not guarded for _, _, _, guarded in findings)
        if review_count:
            print(f"\n{len(findings)} finding(s); {review_count} REVIEW item(s) need an explicit platform guard or fallback.")
        else:
            print(f"\n{len(findings)} finding(s); all have a nearby platform guard or fallback.")

    if violations:
        print("\n模态抓取违规（构造期直接 grab_set 会在 X11 上抛 window not viewable）：")
        for item in violations:
            print("  " + item)
        print("  正确写法：BaseDialog._grab_deferred()（见 ui/common/dialogs.py）")
        return 1
    print("模态抓取检查通过：界面代码一律走 BaseDialog._grab_deferred()。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
