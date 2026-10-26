"""行为包钩子目录速览：把 ``runtime.catalog()`` 按可读格式打到 stdout。

用法:
    python developer_tools/behavior_hooks_report.py            # 全目录，按 scope 分组
    python developer_tools/behavior_hooks_report.py --scope report   # 只看一个域
    python developer_tools/behavior_hooks_report.py --json     # 机器可读（catalog 原样）

写行为包前先看一眼：每个 key 的细度（函数 / 常量）、语义归属（层级 / 契约）、
进入版本、函数签名（具名参数）与常量默认值。与
``docs/designs/behaviors/`` 的钩子分组页同源（数据来自运行时注册表，
文档是给人读的展开说明）。
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import core.logic  # noqa: E402,F401  注册全部钩子模块
import services.character.offline  # noqa: E402,F401
import services.character.profile_text  # noqa: E402,F401
import services.exploration.arbiter  # noqa: E402,F401
import services.exploration.report  # noqa: E402,F401
import services.creation_service  # noqa: E402,F401
import services.state_service  # noqa: E402,F401
from core.behavior_runtime import (  # noqa: E402
    BEHAVIOR_API_VERSION, get_runtime,
)

_KIND_LABEL = {"function": "函数", "const": "常量"}


def _format_default(entry) -> str:
    value = entry["default"]
    if entry["kind"] == "function":
        return f"默认实现 {value}()"
    text = repr(value)
    return text if len(text) <= 48 else text[:45] + "..."


def print_catalog(scope: str = "") -> None:
    """按 scope 分组打印目录；scope 非空时只打印该域。"""
    catalog = [e for e in get_runtime().catalog()
               if not scope or e["scope"] == scope]
    if not catalog:
        print(f"没有匹配的 key（scope={scope!r}）")
        return
    n_func = sum(1 for e in catalog if e["kind"] == "function")
    n_const = len(catalog) - n_func
    print(f"行为包钩子目录（behavior_api {BEHAVIOR_API_VERSION}，"
          f"共 {len(catalog)} 项：{n_func} 函数 + {n_const} 常量）")
    print("注册方式：runtime.override(\"<key>\", <实现>)；"
          "常量覆盖可为同类型值或返回该值的可调用对象\n")
    scopes = []
    for entry in catalog:
        if entry["scope"] not in scopes:
            scopes.append(entry["scope"])
    for sc in scopes:
        entries = [e for e in catalog if e["scope"] == sc]
        print(f"[{sc}] {len(entries)} 项")
        for entry in entries:
            if entry["kind"] == "function":
                params = ", ".join(entry["params"])
                detail = f"({params})" if params else "（无参数）"
            else:
                detail = f"默认 {_format_default(entry)}"
            deprecated = "  [deprecated]" if entry["deprecated"] else ""
            print(f"  {entry['key']:<44} {_KIND_LABEL[entry['kind']]} "
                  f"{entry['layer']}/{entry['contract']} "
                  f"since {entry['since']}{deprecated}  {detail}")
        print()


def main():
    parser = argparse.ArgumentParser(
        description="行为包钩子目录速览（scope / 细度 / 层级 / 签名 / 默认值）")
    parser.add_argument("--scope", default="",
                        help="只看指定域（如 report / logic / StateService）")
    parser.add_argument("--json", action="store_true",
                        help="以 JSON 输出 catalog() 原始列表")
    args = parser.parse_args()

    if args.json:
        print(json.dumps(get_runtime().catalog(), ensure_ascii=False, indent=2))
    else:
        print_catalog(args.scope)


if __name__ == "__main__":
    main()
