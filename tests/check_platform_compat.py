"""Report project-level Windows-specific API usage for cross-platform review.

Run this script before a macOS release. It does not fail merely because a
Windows API exists: platform-guarded calls are valid. Instead, it reports the
locations so that each new use can be reviewed deliberately.
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


def is_excluded(path: Path) -> bool:
    return any(part in EXCLUDED_PARTS for part in path.parts)


def is_platform_guarded(lines: list[str], index: int) -> bool:
    """Use nearby source as a lightweight guard heuristic."""
    start = max(0, index - 18)
    nearby = lines[start:index + 1]
    return any(GUARD_PATTERN.search(line) for line in nearby) or any(
        'shutil.which("dot")' in line for line in nearby
    )


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

    if not findings:
        print("No Windows-specific API or path patterns found.")
        return 0

    print("Windows-specific code review points:")
    for path, line, text, guarded in findings:
        status = "guarded" if guarded else "REVIEW"
        print(f"[{status}] {path}:{line}: {text}")
    review_count = sum(not guarded for _, _, _, guarded in findings)
    if review_count:
        print(f"\n{len(findings)} finding(s); {review_count} REVIEW item(s) need an explicit platform guard or fallback.")
    else:
        print(f"\n{len(findings)} finding(s); all have a nearby platform guard or fallback.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
