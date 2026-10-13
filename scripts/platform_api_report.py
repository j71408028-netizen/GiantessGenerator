"""Report project-level Windows-specific API usage for cross-platform review.

用法：``python scripts/platform_api_report.py``

Run this script before a macOS release. It does not fail merely because a
Windows API exists: platform-guarded calls are valid. Instead, it reports the
locations so that each new use can be reviewed deliberately.

它**不判红**：只打印 Windows 专有 API 的落点清单，恒以 0 退出，合适时人工过一眼。
原先它还兼守一条"模态对话框不得在构造期直接 ``grab_set()``"的判红约束，现已移出
——同一份扫描在 ``tests/check_modal_grabs.py`` 里有专职守卫（AST 版，比这里的正则
更准，另带行为分支自检），留两处只会各报红一次。

正因为不判红，它已从 ``tests/`` 挪到 ``scripts/``：门禁只跑会给出结论的检查，
"打印一份清单供人看"属于开发者工具（对照 ``scripts/README.md``）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {"__pycache__", ".idea", "build", "dist"}
#: 虚拟环境目录按前缀判定：构建脚本按平台命名（.venv / .venv-build / .venv-macos /
#: .venv-buildlinux…），枚举必然漏。
VIRTUALENV_PREFIXES = (".venv", "venv")
VIRTUALENV_NAMES = {"env", "ENV"}
SAFE_PLATFORM_CONFIG_FILES = {Path("ui/ctk/fonts.py")}
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
    return any(part in EXCLUDED_PARTS or part in VIRTUALENV_NAMES
               or part.startswith(VIRTUALENV_PREFIXES) for part in path.parts)


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

    print("（模态抓取的判红约束由 tests/check_modal_grabs.py 负责，本脚本只看清单。）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
