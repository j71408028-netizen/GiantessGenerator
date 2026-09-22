"""批量校验副本方案配置（CLI）。

用法::

    python scripts/validate_scenarios.py [--data-dir <dir>] [--scenario <id>]
    python scripts/validate_scenarios.py --errors-only   # 只展示错误级

对每个方案跑 ``dungeon.validate.validate_scenario_config()``（含资产存在性检查），
输出诊断报告；有任何 error 级诊断时退出码为 1（可挂 CI / 自查）。

校验规则与接入点的关系：编辑器保存时（``ScenarioRepo.save_config`` 记录诊断）与
副本启动前（``window/base.py::_load_session_config``，error 阻止进入）用的是
同一份规则；本脚本用来离线批量排查。
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dungeon.validate import format_diagnostics, has_errors, validate_scenario_config  # noqa: E402
from paths import data_dir  # noqa: E402
from persistence.scenario_repo import ScenarioRepo  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="副本方案配置批量校验")
    parser.add_argument("--data-dir", default=data_dir(), help="数据根目录")
    parser.add_argument("--scenario", default=None, help="只校验指定方案 id")
    parser.add_argument("--errors-only", action="store_true", help="只展示错误级诊断")
    args = parser.parse_args()

    repo = ScenarioRepo(data_dir=args.data_dir)
    scenario_ids = [args.scenario] if args.scenario else repo.list_all()
    if not scenario_ids:
        print("没有找到任何副本方案。")
        return 0

    worst = 0
    for scenario_id in scenario_ids:
        config = repo.load_config(scenario_id)
        if config is None:
            print(f"[!] {scenario_id}: 无法读取配置")
            worst = 1
            continue
        diagnostics = validate_scenario_config(
            config, scenario_dir=repo.scenario_dir(scenario_id))
        errors = sum(1 for d in diagnostics if d.level == "error")
        warnings = sum(1 for d in diagnostics if d.level == "warning")
        infos = sum(1 for d in diagnostics if d.level == "info")
        print(f"=== {scenario_id}: 错误 {errors} / 警告 {warnings} / 提示 {infos} ===")
        if diagnostics:
            shown = [d for d in diagnostics if not args.errors_only or d.level == "error"]
            print(format_diagnostics(shown))
        if errors:
            worst = 1
    print("校验完成。" + ("存在错误级问题。" if worst else "没有错误级问题。"))
    return worst


if __name__ == "__main__":
    sys.exit(main())
