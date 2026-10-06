"""统一自检入口：自动发现并按顺序跑 tests/ 下的自检脚本，汇总退出码。

命名约定即分组规则，新增脚本放进对应类别即可，无需登记清单::

    check_*.py   守卫 / 行为自检，纯离线、无显示器要求，CI 门禁
    smoke_*.py   需要真实显示器 / 窗口环境的 GUI 冒烟

用法::

    python tests/run_checks.py            # 跑全部离线检查（check_*）
    python tests/run_checks.py --smoke    # 追加 GUI 冒烟（smoke_*；GUI 自检不可并发）
    python tests/run_checks.py check_dungeon_layering check_splitter
                                          # 只跑指定脚本（可省略前后缀）

每个脚本在独立子进程中运行（部分脚本会重定向 sys.stdout 写报告文件，
子进程隔离避免相互干扰），任一退出码非 0 则整体退出码为 1。
"""

import glob
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HERE = os.path.join(_ROOT, "tests")


def _discover(pattern):
    return sorted(os.path.basename(p) for p in glob.glob(os.path.join(_HERE, pattern)))


def main(argv):
    names = [a for a in argv if not a.startswith("-")]
    run_smoke = "--smoke" in argv

    if names:
        candidates = [n if n.endswith(".py") else n + ".py" for n in names]
    else:
        candidates = _discover("check_*.py") + (_discover("smoke_*.py") if run_smoke else [])

    unknown = [c for c in candidates if not os.path.isfile(os.path.join(_HERE, c))]
    if unknown:
        print("未找到的自检脚本: " + ", ".join(unknown))
        return 2

    failed = []
    for name in candidates:
        print(f"===== {name} =====", flush=True)
        proc = subprocess.run(
            [sys.executable, os.path.join(_HERE, name)],
            cwd=_ROOT,
        )
        if proc.returncode != 0:
            print(f"FAIL  {name} (exit {proc.returncode})")
            failed.append(name)
        else:
            print(f"OK    {name}")

    print()
    if failed:
        print(f"未通过 {len(failed)}/{len(candidates)}: " + ", ".join(failed))
        return 1
    print(f"全部通过（{len(candidates)} 项）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
