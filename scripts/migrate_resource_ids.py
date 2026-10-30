"""把存量资源改造成资源声明允许的形状（一次性，幂等）。

九步，每步都按「已经是目标形状就跳过」判断，因此跑第二遍必须输出 0 处变更：

1. 容器改名：中文/驼峰 id → 拼音全拼，伴随文件（``.addr.json`` / ``.meta.json`` /
   ``.bak``）跟着改；
2. 改引用：``resource_settings.json`` 的选中项、世界包 ``world.json`` 的 resources；
3. 去扩展名：无后缀容器（描述风格）的主文件 ``<id>.json`` → ``<id>``，
   备份随行为 ``<id>.bak``；自由目录与已部署的世界包都扫；
4. ``news`` 表头：中文列名 → 字段键（顺序不变）；
5. ``quips`` 坐标：``"1_1"`` → ``{"intrusion": 1, "destruction": 1, "items": [...]}``，
   正文末尾的 ``[summary:…]`` 同时拆成条目自带的 ``summary`` 字段；
6. ``landmarks`` 主文件：无后缀 JSON 数组 → ``<id>.csv``（声明容器已改为 csv）；
7. 补信封：为每份资源写 ``<id>.meta.json``（内容哈希一致则不重写）；
8. 清理 ``packs/dungeons/`` 残留；
9. 角色档案的耐久键：``名称@完整地址`` → ``Ref`` 文本（``landmarks:风格/名称``）。

顺带把 ``packs/dungeons/`` 残留挪进 ``migrated/``。

默认只打印计划；``--apply`` 才落盘，且先整目录备份到 ``<data>.rollback-<时间戳>/``。
回滚：把备份目录覆盖回 ``data/``。

用法::

    python scripts/migrate_resource_ids.py           # 预览
    python scripts/migrate_resource_ids.py --apply   # 落盘
"""

import argparse
import csv
import datetime
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.resource_spec import RESOURCE_TYPES, spec_of  # noqa: E402
from dungeon.terms import LEGACY_SCENARIO_RESOURCE_KEY  # noqa: E402
from persistence.json_store import BACKUP_SUFFIX, write_json_atomic  # noqa: E402
from persistence.landmark_repo import write_csv  # noqa: E402
from persistence.resources import envelope, registry  # noqa: E402

#: 旧 id → 新 id。改名映射只活在本脚本里，代码侧不认旧名。
RENAMES = {
    "landmarks": {"ChineseMix": "chinese_mix", "Extension": "extension"},
    "quips": {"Events": "events", "PixelGame": "pixel_game"},
    "scenarios": {"_default": "default", "巨影临城": "juying_lincheng"},
}

_SUMMARY_RE = re.compile(r"\s*\[summary:(.*?)\]\s*$")

#: 去扩展名前，单文件 JSON 容器的主文件后缀。只在本脚本里认。
LEGACY_MAIN_SUFFIX = ".json"


# ---------------------------------------------------------------------------
# 迁移上下文
# ---------------------------------------------------------------------------

class Migration:
    """一次迁移：``apply=False`` 时只记变更，不碰磁盘。"""

    def __init__(self, data_dir: str, apply: bool):
        self.data_dir = data_dir
        self.apply = apply
        self.changes = []
        self.backup_dir = ""

    def note(self, detail: str):
        self.changes.append(detail)
        print(("  ✓ " if self.apply else "  · ") + detail)

    def backup(self) -> str:
        stamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        target = f"{self.data_dir.rstrip(os.sep)}.rollback-{stamp}"
        if os.path.exists(target):
            raise SystemExit(f"备份目录已存在，拒绝覆盖：{target}")
        shutil.copytree(self.data_dir, target)
        self.backup_dir = target
        print(f"已备份到 {target}")
        return target


# ---------------------------------------------------------------------------
# 1. 容器改名
# ---------------------------------------------------------------------------

def _suffix_tails(type_id: str) -> tuple:
    """一个资源名后面可能跟的尾巴（主文件扩展名、备份、伴随文件）。

    改名发生在去扩展名之前，所以这里用的是**迁移前**的后缀：无后缀容器此时
    还带着 ``.json``。
    """
    if spec_of(type_id).container in ("json_dir", "py_package"):
        return ("",)
    ext = registry.primary_suffix(type_id) or LEGACY_MAIN_SUFFIX
    return ("", ext, ext + ".bak")


def step_rename(m: Migration):
    print("[1/9] 容器改名")
    for type_id, mapping in RENAMES.items():
        directory = registry.free_dir(type_id, m.data_dir)
        if not os.path.isdir(directory):
            continue
        tails = _suffix_tails(type_id) + (registry.ADDR_SUFFIX, envelope.ENVELOPE_SUFFIX)
        # 用目录项名精确判断：大小写不敏感的文件系统上 os.path.exists 会把
        # 已改名的小写文件当成旧名依然存在，导致每次重跑都重复改名。
        names = set(os.listdir(directory))
        for old, new in mapping.items():
            for tail in tails:
                source_name = old + tail
                target_name = new + tail
                if source_name not in names:
                    continue
                same_file = os.path.normcase(source_name) == os.path.normcase(target_name)
                if target_name in names and not same_file:
                    continue
                m.note(f"{type_id}: {source_name} → {target_name}")
                if m.apply:
                    os.rename(os.path.join(directory, source_name),
                              os.path.join(directory, target_name))
                names.discard(source_name)
                names.add(target_name)


# ---------------------------------------------------------------------------
# 2. 改引用
# ---------------------------------------------------------------------------

def _load_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _remap(values, mapping):
    if not isinstance(values, list):
        return values
    return [mapping.get(item, item) for item in values]


def step_references(m: Migration):
    print("[2/9] 改引用")
    path = os.path.join(m.data_dir, "user", "resource_settings.json")
    data = _load_json(path)
    if isinstance(data, dict):
        touched = False
        for key, type_id in (("selected_styles", "landmarks"),
                             ("selected_quip_styles", "quips")):
            mapping = RENAMES.get(type_id, {})
            old = data.get(key)
            new = _remap(old, mapping)
            if new != old:
                data[key] = new
                touched = True
                m.note(f"resource_settings.{key}: {old} → {new}")
        if touched and m.apply:
            write_json_atomic(path, data, backup=False)

    worlds = os.path.join(m.data_dir, "worlds")
    if not os.path.isdir(worlds):
        return
    for world_id in sorted(os.listdir(worlds)):
        manifest_path = os.path.join(worlds, world_id, "world.json")
        manifest = _load_json(manifest_path)
        if not isinstance(manifest, dict):
            continue
        resources = manifest.get("resources")
        if not isinstance(resources, dict):
            continue
        touched = False
        for type_id, new_values in list(resources.items()):
            old_values = resources[type_id]
            mapped = _remap(old_values, RENAMES.get(type_id, {}))
            if mapped != old_values:
                resources[type_id] = mapped
                touched = True
                m.note(f"worlds/{world_id}: resources.{type_id} {old_values} → {mapped}")
        if touched and m.apply:
            write_json_atomic(manifest_path, manifest, backup=False)


# ---------------------------------------------------------------------------
# 3. 去扩展名
# ---------------------------------------------------------------------------

def _main_dirs(m: Migration, type_id: str) -> list:
    """该类型主文件可能出现的地方：自由目录 + 已部署世界包里的同名目录。"""
    dirs = [registry.free_dir(type_id, m.data_dir)]
    worlds = os.path.join(m.data_dir, "worlds")
    if os.path.isdir(worlds):
        for world_id in sorted(os.listdir(worlds)):
            candidate = os.path.join(worlds, world_id, type_id)
            if os.path.isdir(candidate):
                dirs.append(candidate)
    return dirs


def step_strip_suffix(m: Migration):
    print("[3/9] 主文件去扩展名")
    for spec in RESOURCE_TYPES:
        if spec.container in ("json_dir", "py_package"):
            continue
        if registry.primary_suffix(spec.type_id):
            continue  # 声明里仍带后缀（.csv / .chal），不动
        for directory in _main_dirs(m, spec.type_id):
            if not os.path.isdir(directory):
                continue
            names = set(os.listdir(directory))
            for name in sorted(names):
                if name.endswith(registry.ADDR_SUFFIX) or name.endswith(
                        envelope.ENVELOPE_SUFFIX):
                    continue
                if name.endswith(LEGACY_MAIN_SUFFIX + BACKUP_SUFFIX):
                    # 主文件的备份跟着改，否则升级后第一次保存找不到旧档可回退
                    target = name[:-len(LEGACY_MAIN_SUFFIX + BACKUP_SUFFIX)] + BACKUP_SUFFIX
                elif name.endswith(LEGACY_MAIN_SUFFIX):
                    target = name[:-len(LEGACY_MAIN_SUFFIX)]
                else:
                    continue
                if target in names or not target:
                    continue
                rel = os.path.relpath(os.path.join(directory, name), m.data_dir)
                m.note(f"{rel} → {target}")
                if m.apply:
                    os.rename(os.path.join(directory, name),
                              os.path.join(directory, target))


# ---------------------------------------------------------------------------
# 4. news 表头
# ---------------------------------------------------------------------------

def _news_header_map() -> dict:
    """中文列名 → 字段键，取自声明的字段标签。"""
    spec = spec_of("news")
    return {field.label: field.key for field in spec.fields if field.label}


def step_news_header(m: Migration):
    print("[4/9] news 表头")
    directory = registry.free_dir("news", m.data_dir)
    if not os.path.isdir(directory):
        return
    mapping = _news_header_map()
    for name in sorted(os.listdir(directory)):
        if not name.lower().endswith(".csv"):
            continue
        path = os.path.join(directory, name)
        with open(path, "rb") as handle:
            raw = handle.read()
        has_bom = raw.startswith(b"\xef\xbb\xbf")
        text = raw.decode("utf-8-sig")
        head, separator, rest = text.partition("\n")
        fields = next(csv.reader([head.rstrip("\r")]), [])
        mapped = [mapping.get(field.strip(), field.strip()) for field in fields]
        if mapped == [field.strip() for field in fields]:
            continue
        m.note(f"news/{name}: 表头 {fields} → {mapped}")
        if m.apply:
            payload = (",".join(mapped) + separator + rest).encode("utf-8")
            with open(path, "wb") as handle:
                handle.write((b"\xef\xbb\xbf" if has_bom else b"") + payload)


# ---------------------------------------------------------------------------
# 4+5. quips 坐标与简介
# ---------------------------------------------------------------------------

def _convert_quips(data: dict, style_map: dict):
    """改写一份描述风格文件，返回 ``(新数据, 计数)``。"""
    counts = {"coords": 0, "summary": 0, "style": 0}
    result = {}
    for category, value in data.items():
        if category == "_meta" or not isinstance(value, (dict, list)):
            result[category] = value
            continue
        if isinstance(value, dict):
            rows = []
            for coord, items in value.items():
                try:
                    intrusion, destruction = (int(part) for part in str(coord).split("_"))
                except ValueError:
                    continue
                rows.append({"intrusion": intrusion, "destruction": destruction,
                             "items": items})
                counts["coords"] += 1
            value = sorted(rows, key=lambda row: (row["intrusion"], row["destruction"]))
        result[category] = value
        for row in value:
            if not isinstance(row, dict):
                continue
            for item in row.get("items") or []:
                if not isinstance(item, dict):
                    continue
                style = item.get("style")
                if style in style_map:
                    item["style"] = style_map[style]
                    counts["style"] += 1
                text = item.get("text")
                if not isinstance(text, str):
                    continue
                match = _SUMMARY_RE.search(text)
                if match:
                    item["summary"] = match.group(1).strip()
                    item["text"] = _SUMMARY_RE.sub("", text).strip()
                    counts["summary"] += 1
    return result, counts


def step_quips(m: Migration):
    print("[5/9] quips 坐标与简介")
    style_map = RENAMES.get("quips", {})
    for resource_id in registry.list_ids("quips", m.data_dir):
        path = registry.free_path("quips", resource_id, m.data_dir)
        data = _load_json(path)
        if not isinstance(data, dict):
            continue
        converted, counts = _convert_quips(data, style_map)
        if not any(counts.values()):
            continue
        detail = "、".join(f"{label} {counts[key]}"
                          for key, label in (("coords", "坐标"),
                                             ("summary", "简介"),
                                             ("style", "风格"))
                          if counts[key])
        m.note(f"quips/{resource_id}: {detail}")
        if m.apply:
            write_json_atomic(path, converted, backup=False)


# ---------------------------------------------------------------------------
# 6. landmarks 主文件转 CSV
# ---------------------------------------------------------------------------

def _landmark_target_name(name: str):
    """旧主文件名 → CSV 主文件名；伴随文件等不该动的返回 None。"""
    if name.endswith(registry.ADDR_SUFFIX) or name.endswith(envelope.ENVELOPE_SUFFIX):
        return None
    if name.endswith(".csv"):
        return None
    if name.endswith(LEGACY_MAIN_SUFFIX + BACKUP_SUFFIX):
        return name[:-len(LEGACY_MAIN_SUFFIX)] + ".csv" + BACKUP_SUFFIX
    if name.endswith(BACKUP_SUFFIX):
        return name[:-len(BACKUP_SUFFIX)] + ".csv" + BACKUP_SUFFIX
    if name.endswith(LEGACY_MAIN_SUFFIX):
        return name[:-len(LEGACY_MAIN_SUFFIX)] + ".csv"
    return name + ".csv"


def step_landmarks_csv(m: Migration):
    print("[6/9] landmarks 主文件转 CSV")
    for directory in _main_dirs(m, "landmarks"):
        if not os.path.isdir(directory):
            continue
        names = set(os.listdir(directory))
        for name in sorted(names):
            path = os.path.join(directory, name)
            target_name = _landmark_target_name(name)
            if target_name is None or target_name in names or not os.path.isfile(path):
                continue
            data = _load_json(path)
            if not isinstance(data, list):
                continue  # 非 JSON 数组的杂项文件不迁，转换后也不再被当作风格
            m.note(f"{os.path.relpath(path, m.data_dir)} → {target_name}")
            if m.apply:
                write_csv(os.path.join(directory, target_name),
                          [item for item in data if isinstance(item, dict)])
                os.remove(path)


# ---------------------------------------------------------------------------
# 7. 信封
# ---------------------------------------------------------------------------

def step_envelopes(m: Migration):
    print("[7/9] 补信封")
    for spec in RESOURCE_TYPES:
        directory = registry.free_dir(spec.type_id, m.data_dir)
        if not os.path.isdir(directory):
            continue
        for resource_id in registry.list_ids(spec.type_id, m.data_dir):
            primary = registry.free_path(spec.type_id, resource_id, m.data_dir)
            if not os.path.exists(primary):
                # 包型容器的主文件模板是通配（``{id}/*.py``），按目录算哈希
                primary = os.path.join(directory, resource_id)
            if not os.path.exists(primary):
                continue
            current = envelope.content_hash(primary, spec.container)
            recorded = envelope.load_envelope(
                directory, spec.type_id, resource_id).get("content_hash")
            if current and recorded == current:
                continue
            m.note(f"{spec.type_id}/{resource_id}: 写信封")
            if m.apply:
                data = envelope.build_envelope(
                    directory, spec.type_id, resource_id, primary)
                envelope.save_envelope(directory, spec.type_id, resource_id, data)


# ---------------------------------------------------------------------------
# 7. 清理
# ---------------------------------------------------------------------------

def step_cleanup(m: Migration):
    print("[8/9] 清理残留")
    legacy = os.path.join(m.data_dir, "packs", LEGACY_SCENARIO_RESOURCE_KEY)
    if not os.path.isdir(legacy):
        return
    stamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    target = os.path.join(m.data_dir, "migrated", f"dungeons-{stamp}")
    m.note(f"packs/dungeons → migrated/dungeons-{stamp}")
    if m.apply:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.move(legacy, target)


# ---------------------------------------------------------------------------
# 8. 角色档案的耐久键
# ---------------------------------------------------------------------------

_DURABILITY_FIELDS = ("landmark_durability", "landmark_addresses")


def _landmark_name_index(m: Migration) -> dict:
    """``显示名 → {风格 id}``：把旧耐久键里的名字折射回声明身份。"""
    index = {}
    for style in registry.list_ids("landmarks", m.data_dir):
        data = _load_json(registry.free_path("landmarks", style, m.data_dir))
        for item in data if isinstance(data, list) else []:
            name = (item or {}).get("name") if isinstance(item, dict) else None
            if name:
                index.setdefault(name, set()).add(style)
    return index


def _durability_ref(key: str, names: dict) -> str:
    """旧键（``名称@完整地址`` 或仅名称）→ ``Ref`` 文本；让步进已迁移的键。"""
    if ":" in key:
        return key
    name = key.split("@", 1)[0]
    styles = names.get(name) or set()
    if len(styles) == 1:
        return f"landmarks:{next(iter(styles))}/{name}"
    # 跨风格重名或对不上任何地标：不猜，留给人工
    return key


def step_durability_keys(m: Migration):
    print("[9/9] 角色档案耐久键")
    archives = os.path.join(m.data_dir, "archives")
    if not os.path.isdir(archives):
        return
    names = _landmark_name_index(m)
    for gid in sorted(os.listdir(archives)):
        path = os.path.join(archives, gid, "info.json")
        if not os.path.isfile(path):
            continue
        data = _load_json(path)
        if not isinstance(data, dict):
            continue
        touched = False
        for field in _DURABILITY_FIELDS:
            table = data.get(field)
            if not isinstance(table, dict):
                continue
            renamed = {}
            for key, value in table.items():
                new_key = _durability_ref(key, names)
                if new_key == key:
                    renamed[key] = value
                    continue
                m.note(f"archives/{gid}: {field} {key} → {new_key}")
                renamed[new_key] = value
                touched = True
            data[field] = renamed
        if touched and m.apply:
            write_json_atomic(path, data)


STEPS = (step_rename, step_references, step_strip_suffix, step_news_header,
         step_quips, step_landmarks_csv, step_envelopes, step_cleanup,
         step_durability_keys)


def run(data_dir: str, apply: bool) -> int:
    preview = Migration(data_dir, apply=False)
    for step in STEPS:
        step(preview)
    if not preview.changes:
        print("\n0 处变更（数据已是目标形状）")
        return 0
    if not apply:
        print(f"\n共 {len(preview.changes)} 处变更；未落盘，加 --apply 执行")
        return 0
    print(f"\n共 {len(preview.changes)} 处变更，开始落盘")
    worker = Migration(data_dir, apply=True)
    worker.backup()
    for step in STEPS:
        step(worker)
    print(f"\n完成：{len(worker.changes)} 处变更；备份在 {worker.backup_dir}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="资源 id 与数据格式迁移")
    parser.add_argument("--data-dir", default="data", help="数据目录（默认 data）")
    parser.add_argument("--apply", action="store_true", help="真正落盘（先整目录备份）")
    args = parser.parse_args(argv)
    return run(args.data_dir, args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
