"""把存量资源改造成资源声明允许的形状（一次性，幂等）。

六步，每步都按「已经是目标形状就跳过」判断，因此跑第二遍必须输出 0 处变更：

1. 容器改名：中文/驼峰 id → 拼音全拼，伴随文件（``.addr.json`` / ``.meta.json`` /
   ``.bak``）跟着改；
2. 改引用：``resource_settings.json`` 的选中项、世界包 ``world.json`` 的 resources；
3. ``news`` 表头：中文列名 → 字段键（顺序不变）；
4. ``quips`` 坐标：``"1_1"`` → ``{"intrusion": 1, "destruction": 1, "items": [...]}``；
5. ``quips`` 简介：正文末尾的 ``[summary:…]`` 拆成条目自带的 ``summary`` 字段；
6. 补信封：为每份资源写 ``<id>.meta.json``（内容哈希一致则不重写）。

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
from persistence.json_store import write_json_atomic  # noqa: E402
from persistence.resources import envelope, registry  # noqa: E402

#: 旧 id → 新 id。改名映射只活在本脚本里，代码侧不认旧名。
RENAMES = {
    "landmarks": {"ChineseMix": "chinese_mix", "Extension": "extension"},
    "quips": {"Events": "events", "PixelGame": "pixel_game"},
    "scenarios": {"_default": "default", "巨影临城": "juying_lincheng"},
}

_SUMMARY_RE = re.compile(r"\s*\[summary:(.*?)\]\s*$")


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
    """一个资源名后面可能跟的尾巴（主文件扩展名、备份、伴随文件）。"""
    spec = spec_of(type_id)
    if spec.container in ("json_dir", "py_package"):
        return ("",)
    ext = os.path.splitext(spec.primary)[1]
    return ("", ext, ext + ".bak")


def step_rename(m: Migration):
    print("[1/6] 容器改名")
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
    print("[2/6] 改引用")
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
# 3. news 表头
# ---------------------------------------------------------------------------

def _news_header_map() -> dict:
    """中文列名 → 字段键，取自声明的字段标签。"""
    spec = spec_of("news")
    return {field.label: field.key for field in spec.fields if field.label}


def step_news_header(m: Migration):
    print("[3/6] news 表头")
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
    print("[4/6] quips 坐标与简介")
    directory = registry.free_dir("quips", m.data_dir)
    if not os.path.isdir(directory):
        return
    style_map = RENAMES.get("quips", {})
    for name in sorted(os.listdir(directory)):
        if name.endswith(registry.ADDR_SUFFIX) or name.endswith(envelope.ENVELOPE_SUFFIX):
            continue
        if not (name.endswith(".json") or name.endswith(".json.bak")):
            continue
        path = os.path.join(directory, name)
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
        m.note(f"quips/{name}: {detail}")
        if m.apply:
            write_json_atomic(path, converted, backup=False)


# ---------------------------------------------------------------------------
# 6. 信封
# ---------------------------------------------------------------------------

def step_envelopes(m: Migration):
    print("[5/6] 补信封")
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
    print("[6/6] 清理残留")
    legacy = os.path.join(m.data_dir, "packs", LEGACY_SCENARIO_RESOURCE_KEY)
    if not os.path.isdir(legacy):
        return
    stamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    target = os.path.join(m.data_dir, "migrated", f"dungeons-{stamp}")
    m.note(f"packs/dungeons → migrated/dungeons-{stamp}")
    if m.apply:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.move(legacy, target)


STEPS = (step_rename, step_references, step_news_header, step_quips,
         step_envelopes, step_cleanup)


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
