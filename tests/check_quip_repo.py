# -*- coding: utf-8 -*-
"""描述风格仓库自检：目录 CSV、配置合并、旧布局拒绝与生命周期。

用法：``python tests/check_quip_repo.py``
全部在系统临时目录构造数据，不触碰 data/、不联网。
"""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_report_path = os.path.join(tempfile.mkdtemp(prefix="quip_repo_"), "report.txt")
_log = open(_report_path, "w", encoding="utf-8")
sys.stdout = _log
sys.stderr = _log

failures = []
total = 0


def check(name, ok, extra=""):
    global total
    total += 1
    print(("  OK   " if ok else "  FAIL ") + name
          + (f"  <{extra}>" if extra and not ok else ""))
    if not ok:
        failures.append(name)


from persistence.quip_repo import QuipRepo  # noqa: E402

root = tempfile.mkdtemp(prefix="quip_repo_data_")
try:
    repo = QuipRepo(data_dir=root)
    repo.save("alpha", {
        "small": {(1, 1): [
            {"text": "甲", "style": "alpha", "step": 0.1, "summary": "摘要"},
            {"text": "乙", "style": "alpha", "step": 0.2},
        ]},
        "medium": {(2, 3): [{"text": "丙", "style": "alpha"}]},
    })
    repo.save_meta("alpha", {
        "custom_types": {"c": {"name": "自定义", "subtypes": ["1", "2"]}},
        "matching": {"future": True},
    })
    repo.save_meta("alpha", {
        "custom_types": {"c": {"name": "改名", "subtypes": ["1", "2"]}},
    })

    loaded = repo.load("alpha")
    check("CSV 读回同坐标多条目", len(loaded["small"][(1, 1)]) == 2)
    check("summary 与 step 保留",
          loaded["small"][(1, 1)][0].get("summary") == "摘要"
          and loaded["small"][(1, 1)][0].get("step") == 0.1)
    check("缺省 step 按坐标补齐",
          loaded["medium"][(2, 3)][0].get("step") == 0.6)

    csv_path = os.path.join(root, "packs", "quips", "alpha", "quips.csv")
    raw = open(csv_path, "rb").read()
    check("CSV 行尾只保留一份 CRLF", b"\r\r\n" not in raw)

    config_path = os.path.join(root, "packs", "quips", "alpha", "config.json")
    config = json.loads(open(config_path, encoding="utf-8").read())
    check("config 写入 schema", config.get("schema") == "gg.quips.config/1")
    check("config 保留未知键", config.get("matching") == {"future": True})
    check("save_meta 更新 custom_types",
          config["custom_types"]["c"]["name"] == "改名")

    config["schema"] = "gg.quips.config/2"
    with open(config_path, "w", encoding="utf-8") as handle:
        json.dump(config, handle)
    try:
        repo.save_meta("alpha", {"custom_types": config["custom_types"]})
    except ValueError as exc:
        check("更高配置版本拒绝写入", "升级应用" in str(exc))
    else:
        check("更高配置版本拒绝写入", False, "未抛 ValueError")
    config["schema"] = "gg.quips.config/1"
    with open(config_path, "w", encoding="utf-8") as handle:
        json.dump(config, handle)

    with open(os.path.join(root, "packs", "quips", "alpha", "notes.txt"),
              "w", encoding="utf-8") as handle:
        handle.write("保留\n")
    repo.create_style("beta", copy_from="alpha")
    check("复制风格带上正文与未知文件",
          repo.load("beta")["small"][(1, 1)][0]["text"] == "甲"
          and os.path.isfile(os.path.join(root, "packs", "quips", "beta",
                                          "notes.txt")))
    check("复制风格带上配置",
          repo.load_meta("beta")["custom_types"]["c"]["name"] == "改名")

    repo.save_style_address("beta", "1_2")
    with open(os.path.join(root, "packs", "quips", "beta.meta.json"),
              "w", encoding="utf-8") as handle:
        json.dump({"id": "beta"}, handle)
    repo.rename_style("beta", "gamma")
    check("改名同时移动目录、地址与信封",
          os.path.isdir(os.path.join(root, "packs", "quips", "gamma"))
          and os.path.isfile(os.path.join(root, "packs", "quips",
                                          "gamma.addr.json"))
          and os.path.isfile(os.path.join(root, "packs", "quips",
                                          "gamma.meta.json")))
    repo.delete_style("gamma")
    check("删除同时清掉目录、地址与信封",
          not os.path.exists(os.path.join(root, "packs", "quips", "gamma"))
          and not os.path.exists(os.path.join(root, "packs", "quips",
                                              "gamma.addr.json"))
          and not os.path.exists(os.path.join(root, "packs", "quips",
                                              "gamma.meta.json")))

    legacy_root = tempfile.mkdtemp(prefix="quip_repo_legacy_")
    try:
        legacy_dir = os.path.join(legacy_root, "packs", "quips")
        os.makedirs(legacy_dir)
        with open(os.path.join(legacy_dir, "old"), "w", encoding="utf-8") as handle:
            json.dump({"small": {"1_1": ["旧"]}}, handle)
        legacy = QuipRepo(data_dir=legacy_root)
        try:
            legacy.get_styles()
        except ValueError as exc:
            check("旧版单文件布局给出迁移提示", "migrate_resource_ids" in str(exc))
        else:
            check("旧版单文件布局给出迁移提示", False, "未抛 ValueError")
        try:
            legacy.load("old")
        except ValueError:
            check("旧版单文件读取被拒绝", True)
        else:
            check("旧版单文件读取被拒绝", False)
    finally:
        shutil.move(legacy_root, tempfile.mkdtemp(prefix="quip_repo_legacy_retired_"))
finally:
    shutil.move(root, tempfile.mkdtemp(prefix="quip_repo_data_retired_"))

_log.flush()
sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__
if failures:
    print(f"[check_quip_repo] FAILED {len(failures)}/{total}: {failures}")
else:
    print(f"[check_quip_repo] PASSED {total}/{total}")
print(f"[check_quip_repo] report: {_report_path}")
raise SystemExit(1 if failures else 0)
