# -*- coding: utf-8 -*-
"""资源层声明的离线守卫：类型清单、路径定位、信封格式。

用法：``python tests/check_resource_spec.py``

本守卫只查**结构事实**，不抄资源名、不查具体字段值，因此改文案、调参数、
迁移 id 都不会误报：

1. ``core.resource_spec`` 的声明自洽（类型 id 合规、容器合法、primary 含占位…）；
2. 从声明派生的三张映射与替换前的常量等价（``world_pack`` /
   ``resource_settings_repo`` 的现值）——这一条是「改派生不改行为」的证明；
3. ``data/`` 下每个资源目录都能在注册表里找到对应类型与存储根；
4. 信封读写往返：缺字段补齐不写回、未知字段保留、内容哈希稳定；
5. 打包成员核对与 ``world_pack`` 的旧实现在同一批输入上结论一致。

退出码：0 = 通过；1 = 存在断言失败。
"""

import io
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.resource_spec import (  # noqa: E402
    RESOURCE_TYPES,
    check_spec_consistency,
    packable_type_ids,
    spec_of,
    valid_resource_id,
    valid_type_id,
)
from persistence import world_pack  # noqa: E402
from persistence.resource_settings_repo import (  # noqa: E402
    RESOURCE_SETTINGS_KEYS as LEGACY_SETTING_KEYS,
)
from persistence.resources import envelope as envelope_mod  # noqa: E402
from persistence.resources import registry  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA = os.path.join(_ROOT, "data")

_checked = 0


def check(cond, message, detail=None):
    global _checked
    _checked += 1
    if not cond:
        suffix = "" if detail is None else f"  (got {detail!r})"
        print(f"[FAIL] {message}{suffix}")
        raise SystemExit(1)


# ---------------- 1. 声明自洽 ----------------
problems = check_spec_consistency()
check(not problems, "资源类型声明自洽", problems)
check(len({spec.type_id for spec in RESOURCE_TYPES}) == len(RESOURCE_TYPES),
      "类型 id 唯一")
for spec in RESOURCE_TYPES:
    check(valid_type_id(spec.type_id), f"类型 id 合规: {spec.type_id}")
    check(spec.field_keys() == tuple(dict.fromkeys(spec.field_keys())),
          f"{spec.type_id} 字段 key 不重复")


# ---------------- 2. 派生映射 ----------------
# 领域契约：资源域的设置键集合。改了就要写迁移，所以在这里钉住。
_EXPECTED_SETTING_KEYS = {
    "name_table", "news_table", "preset_table", "personality_table",
    "selected_styles", "selected_quip_styles", "active_world",
}

check(packable_type_ids() == tuple(registry.PACK_RESOURCE_PATHS),
      "packable_type_ids 与派生映射同序")
check(all(k == v for k, v in registry.PACK_RESOURCE_PATHS.items()),
      "世界包内目录名即类型 id", registry.PACK_RESOURCE_PATHS)
check(registry.PACK_RESOURCE_PATHS is world_pack.PACK_RESOURCE_PATHS,
      "world_pack 直接复用注册表的映射，不再自带副本")
check(registry.LIST_RESOURCE_TYPES is world_pack.LIST_RESOURCE_TYPES,
      "LIST_RESOURCE_TYPES 只有一份")
check(set(registry.RESOURCE_SETTINGS_KEYS) == _EXPECTED_SETTING_KEYS,
      "资源域设置键齐备",
      sorted(set(registry.RESOURCE_SETTINGS_KEYS) ^ _EXPECTED_SETTING_KEYS))
check(LEGACY_SETTING_KEYS is registry.RESOURCE_SETTINGS_KEYS,
      "resource_settings_repo 复用注册表的键集")
check(len(set(registry.RESOURCE_SETTINGS_KEYS)) == len(registry.RESOURCE_SETTINGS_KEYS),
      "RESOURCE_SETTINGS_KEYS 无重复")
check(tuple(registry.RESOURCE_TYPE_LABELS) == packable_type_ids() + ("minigames",),
      "显示名覆盖全部类型且与声明同序", sorted(registry.RESOURCE_TYPE_LABELS))
check(set(registry.MULTI_PICK_TYPES).isdisjoint(registry.SINGLE_PICK_TYPES)
      and set(registry.MULTI_PICK_TYPES) | set(registry.SINGLE_PICK_TYPES)
      == set(registry.PACK_RESOURCE_PATHS),
      "多选/单选类型互补且不重不漏")

# 未纳入打包的类型必须显式声明，不允许「登记了但悄悄不可打包」
for spec in RESOURCE_TYPES:
    if not spec.packable:
        check(spec.type_id not in registry.PACK_RESOURCE_PATHS,
              f"{spec.type_id} 声明不可打包时不应出现在 PACK_RESOURCE_PATHS")
        check(bool(spec.note), f"{spec.type_id} 不可打包，需在 note 里说明原因")


# ---------------- 2b. 类型清单不得再出现第二份 ----------------
# 只查四个对外契约名有没有被重新赋值：模块可以 import 它们，但不许自建。
import ast  # noqa: E402

_CONTRACT_NAMES = {"PACK_RESOURCE_PATHS", "LIST_RESOURCE_TYPES",
                   "BOOL_RESOURCE_TYPES", "RESOURCE_SETTINGS_KEYS"}
_OWNER = os.path.join("persistence", "resources", "registry.py")
_duplicates = []
for _dirpath, _dirnames, _filenames in os.walk(_ROOT):
    _dirnames[:] = [d for d in _dirnames
                    if d not in {"__pycache__", ".git", ".workbuddy", "data",
                                 "developer_tools", "build", "dist", "assets"}
                    and not d.startswith(".venv")]
    for _name in _filenames:
        if not _name.endswith(".py"):
            continue
        _path = os.path.join(_dirpath, _name)
        _rel = os.path.relpath(_path, _ROOT)
        if _rel == _OWNER:
            continue
        try:
            _tree = ast.parse(io.open(_path, encoding="utf-8").read(), filename=_rel)
        except (OSError, SyntaxError):
            continue
        for _node in ast.walk(_tree):
            _targets = []
            if isinstance(_node, ast.Assign):
                _targets = [_t.id for _t in _node.targets if isinstance(_t, ast.Name)]
            elif isinstance(_node, ast.AnnAssign) and isinstance(_node.target, ast.Name):
                _targets = [_node.target.id]
            for _target in _targets:
                if _target in _CONTRACT_NAMES:
                    _duplicates.append(f"{_rel}:{_node.lineno} {_target}")
check(not _duplicates, "类型清单只在注册表里定义一次", _duplicates)


# ---------------- 3. data/ 覆盖 ----------------
if os.path.isdir(_DATA):
    registered = {(spec.root, spec.type_id) for spec in RESOURCE_TYPES}
    for root in ("packs", "static"):
        base = os.path.join(_DATA, root)
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            if name.startswith(".") or name == "__pycache__":
                continue
            if not os.path.isdir(os.path.join(base, name)):
                continue
            check((root, name) in registered,
                  f"data/{root}/{name} 未在资源注册表中声明",
                  sorted(f"{r}/{t}" for r, t in registered))
    # 已声明的类型：列出的 id 必须真的能定位到主文件（不允许出现幻影 id）
    for spec in RESOURCE_TYPES:
        directory = os.path.join(_DATA, spec.root, spec.type_id)
        if not os.path.isdir(directory):
            continue
        ids = registry.list_ids(spec.type_id, _DATA)
        check(isinstance(ids, list), f"{spec.type_id}.list_ids 返回列表")
        for resource_id in ids:
            target = (os.path.join(directory, resource_id)
                      if spec.container in ("json_dir", "py_package")
                      else registry.free_path(spec.type_id, resource_id, _DATA))
            check(os.path.exists(target),
                  f"{spec.type_id}/{resource_id} 列出后定位不到主文件", target)


# ---------------- 4. 信封往返 ----------------
_tmp = tempfile.mkdtemp(prefix="resource_spec_")
try:
    landmarks_dir = os.path.join(_tmp, "packs", "landmarks")
    os.makedirs(landmarks_dir, exist_ok=True)
    primary = os.path.join(landmarks_dir, "chinese_mix")
    payload = [{"name": "上海中心大厦", "size": 632, "dimension": "vertical",
                "frequency": "unique", "horizontal_type": None,
                "address": "", "address_mode": ""}]
    with open(primary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)

    check(registry.resource_id_from_path("landmarks", primary) == "chinese_mix",
          "单文件容器能从路径反推 id")
    check(registry.resource_id_from_path(
              "minigames", os.path.join(_tmp, "packs", "minigames", "reaction",
                                        "manifest.json")) == "reaction",
          "目录型容器能穿过主文件取目录名")

    base = envelope_mod.default_envelope("landmarks", "chinese_mix")
    check(base["schema"] == "gg.landmarks/1", "信封 schema 由类型 id 派生", base["schema"])
    check(base["tags"] == [] and base["source"] == {},
          "缺省信封的可变字段互不共享")
    other = envelope_mod.default_envelope("landmarks", "extension")
    base["tags"].append("x")
    check(other["tags"] == [], "两份缺省信封不共享 tags")

    built = envelope_mod.build_envelope(landmarks_dir, "landmarks", "chinese_mix",
                                        primary)
    check(built["content_hash"].startswith("sha256:"), "内容哈希带算法前缀",
          built.get("content_hash"))
    check(built["updated_at"], "内容哈希之外补齐时间戳")
    envelope_mod.save_envelope(landmarks_dir, "landmarks", "chinese_mix",
                               dict(built, unknown_key={"a": 1}))
    path = envelope_mod.envelope_path(landmarks_dir, "chinese_mix")
    check(path.endswith("chinese_mix.meta.json"), "信封落在兄弟文件位置", path)

    raw_before = open(path, "rb").read()
    loaded = envelope_mod.load_envelope(landmarks_dir, "landmarks", "chinese_mix")
    check(loaded["unknown_key"] == {"a": 1}, "未知字段原样保留", sorted(loaded))
    check(loaded["status"] == "draft" and loaded["version"] == "0.0.0",
          "缺字段按默认值补齐（不落盘）", loaded)
    check(open(path, "rb").read() == raw_before, "读取不写回磁盘")

    check(not envelope_mod.looks_modified(landmarks_dir, "landmarks",
                                         "chinese_mix", primary),
          "未改动时哈希一致")
    with open(primary, "w", encoding="utf-8") as handle:
        json.dump(payload + [{"name": "新增", "size": 1}], handle, ensure_ascii=False)
    check(envelope_mod.looks_modified(landmarks_dir, "landmarks",
                                      "chinese_mix", primary),
          "载荷变化后哈希不同")

    # 重新缩进不算改动
    envelope_mod.save_envelope(landmarks_dir, "landmarks", "chinese_mix",
                               envelope_mod.build_envelope(
                                   landmarks_dir, "landmarks", "chinese_mix", primary))
    with open(primary, "w", encoding="utf-8") as handle:
        json.dump(payload + [{"name": "新增", "size": 1}], handle,
                  ensure_ascii=False, indent=4)
    check(not envelope_mod.looks_modified(landmarks_dir, "landmarks",
                                          "chinese_mix", primary),
          "仅缩进变化不算改动")

    # 缺信封视为改过；不可读容器缺信封给警告
    missing_dir = os.path.join(_tmp, "packs", "challenges")
    os.makedirs(missing_dir, exist_ok=True)
    chal = os.path.join(missing_dir, "example.chal")
    with open(chal, "wb") as handle:
        handle.write(b"opaque")
    check(envelope_mod.looks_modified(missing_dir, "challenges", "example", chal),
          "无信封视为本地改过")
    codes = [d.code for d in registry.validate_resource("challenges", chal)]
    check("missing_envelope" in codes, "不可读容器缺兄弟信封时给出诊断", codes)

    # 未知类型 / 不存在的路径
    check(registry.validate_resource("nope", chal)[0].code == "unknown_type",
          "未知类型给出诊断")
    check(registry.validate_resource(
              "landmarks", os.path.join(landmarks_dir, "nope"))[0].code
          == "not_found", "缺失主文件给出诊断")

    # id 规则：存量中文 id 明确不合规（改名交给迁移）
    check(valid_resource_id("chinese_mix") and valid_resource_id("default"),
          "规范 id 通过")
    check(not valid_resource_id("ChineseMix") and not valid_resource_id("巨影临城")
          and not valid_resource_id("../etc") and not valid_resource_id(""),
          "非法 id 被拒绝")
    bad = [d for d in registry.validate_resource(
               "landmarks", os.path.join(landmarks_dir, "ChineseMix"))
           if d.code == "not_found"]
    check(bad, "合法 id 之外不因 id 规则掩盖缺失文件")

    # 无后缀容器：条目名即资源名，只认文件，且要排除原子写残留
    with open(os.path.join(landmarks_dir, "chinese_mix.tmp"), "w",
              encoding="utf-8") as handle:
        handle.write("{}")
    with open(os.path.join(landmarks_dir, "chinese_mix.bak"), "w",
              encoding="utf-8") as handle:
        handle.write("[]")
    os.makedirs(os.path.join(landmarks_dir, "notes"), exist_ok=True)
    check(sorted(registry.list_ids("landmarks", _tmp)) == ["chinese_mix"],
          "无后缀容器只枚举主文件（临时文件、备份、子目录都不算）",
          registry.list_ids("landmarks", _tmp))
    check(registry.is_resource_entry("landmarks", landmarks_dir, "chinese_mix")
          and not registry.is_resource_entry("landmarks", landmarks_dir,
                                             "chinese_mix.meta.json"),
          "伴随文件与主文件可区分")
    # 带点号的 id 不能被当成扩展名截断
    with open(os.path.join(landmarks_dir, "v1.2"), "w", encoding="utf-8") as handle:
        handle.write("[]")
    check(registry.resource_id_from_entry("landmarks", "v1.2") == "v1.2",
          "无后缀容器的 id 原样保留，不按扩展名截断")
    check(registry.resource_id_from_path(
              "landmarks", os.path.join(landmarks_dir, "v1.2")) == "v1.2",
          "路径反推 id 同样保留点号")
    check(registry.resource_id_from_entry("presets", "v1.2.csv") == "v1.2",
          "带后缀容器只剥声明后缀")

    # 打包成员核对：成员形状全部由 primary 模板派生
    members = {"landmarks/chinese_mix", "quips/events",
               "presets/default.csv", "behaviors/imperial_units/x.py"}
    declared = {"landmarks": ["chinese_mix"], "quips": ["events"],
                "presets": ["default"], "behaviors": ["imperial_units"]}
    check(registry.missing_members(declared, members) == [],
          "声明齐全时无缺失", registry.missing_members(declared, members))
    for type_id, resource_id, expect in (
            ("landmarks", "absent", "landmarks/absent"),
            ("scenarios", "absent", "scenarios/absent/config.json"),
            ("challenges", "absent.chal", "challenges/absent.chal"),
            ("names", "absent", "names/absent.csv"),
            ("behaviors", "absent", "behaviors/absent/*.py")):
        got = registry.missing_members({type_id: [resource_id]}, members)
        check(got == [expect], f"{type_id} 缺失成员路径由 primary 模板派生", got)
    check(world_pack._missing_members(
              world_pack.WorldPackManifest(world_id="w", name="n",
                                           resources={"presets": ["absent"]}),
              members) == ["presets/absent.csv"],
          "world_pack 的成员核对委托给注册表")

    # 类型专属规则：行为包只能选一个、包名要能当 Python 标识符
    behaviors_spec = spec_of("behaviors")
    check(behaviors_spec.max_items == 1 and behaviors_spec.ui_pick == "single",
          "行为包在声明里就是单选")
    check(behaviors_spec.valid_id("imperial_units")
          and not behaviors_spec.valid_id("1bad"),
          "行为包 id 按专属规则校验")
    two_packs = world_pack.WorldPackManifest(
        world_id="w", name="n", resources={"behaviors": ["a_pack", "b_pack"]})
    check(any("最多 1 项" in e for e in two_packs.validate()),
          "清单里声明两个行为包被拒绝", two_packs.validate())
    bad_pack = world_pack.WorldPackManifest(
        world_id="w", name="n", resources={"behaviors": ["1bad"]})
    check(any("非法资源 id" in e for e in bad_pack.validate()),
          "非法行为包名被拒绝", bad_pack.validate())
    # 挑战包的清单里写的是带扩展名的文件名，不能拼成 x.chal.chal
    check(registry.primary_relpath("challenges", "example.chal") == "example.chal"
          and registry.pack_member("challenges", "example.chal")
          == "challenges/example.chal", "已带扩展名的 id 不重复拼接",
          registry.pack_member("challenges", "example.chal"))

    # 索引条目：线上形态 = 磁盘形态
    entry = registry.catalog_entry("landmarks", "chinese_mix", _tmp)
    check(set(entry) >= {"id", "author", "namespace", "version",
                         "content_hash", "status", "tags"},
          "索引条目字段齐全", sorted(entry))
    check(entry["id"] == "chinese_mix", "索引条目 id 取资源 id", entry)
    catalog = registry.build_catalog(_tmp)
    check(catalog["schema"] == "gg.catalog/1", "索引骨架带 schema")
    check(set(catalog["entries"]) == {spec.type_id for spec in RESOURCE_TYPES},
          "索引按类型分桶")
finally:
    shutil.rmtree(_tmp, ignore_errors=True)

print(f"PASSED ({_checked} assertions)")
sys.exit(0)
