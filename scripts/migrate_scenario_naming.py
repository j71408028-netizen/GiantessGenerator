"""S1.5 数据迁移：把「副本方案」的旧命名（dungeon*）统一为 scenario*。

用法::

    python scripts/migrate_scenario_naming.py [--data-dir <dir>] [--dry-run]

幂等：重复执行无副作用。所有写入走 ``persistence.json_store`` 的原子写 +
``.bak``。迁移内容：

1. 方案目录 ``packs/dungeons/`` → ``packs/scenarios/``（委托
   ``ScenarioRepo._migrate_legacy_root``，构造仓库时自动完成）；
2. ``data/user/endings.json``：记录里的 ``dungeon_id`` → ``scenario_id``；
3. 角色档案 ``data/archives/*/info.json``：``achieved_endings[].dungeon_id``
   → ``scenario_id``；
4. 世界包 ``*.world.zip``：manifest 的 ``resources.dungeons`` →
   ``resources.scenarios``，成员目录 ``dungeons/<id>/`` → ``scenarios/<id>/``。

加密的 ``.chal`` 挑战包不做改写（读取侧兼容旧 ``dungeon_id``/``dungeon_config``
字段，见 ``dungeon/terms.py``）。
"""

import argparse
import json
import os
import shutil
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dungeon.terms import (LEGACY_SCENARIO_ID_KEY, LEGACY_SCENARIO_RESOURCE_KEY,  # noqa: E402
                           SCENARIO_ID_KEY, SCENARIO_RESOURCE_KEY)
from paths import data_dir  # noqa: E402
from persistence.json_store import load_json_with_backup, write_json_atomic  # noqa: E402
from persistence.scenario_repo import ScenarioRepo  # noqa: E402
from persistence.world_pack import WORLD_MANIFEST_NAME  # noqa: E402


def migrate_json_records(path: str, list_path: str, dry_run: bool) -> int:
    """把 JSON 里指定路径列表元素的 ``dungeon_id`` 改写为 ``scenario_id``。"""
    data = load_json_with_backup(path)
    if not isinstance(data, dict):
        return 0
    records = data.get(list_path, [])
    if not isinstance(records, list):
        return 0
    changed = 0
    for record in records:
        if isinstance(record, dict) and LEGACY_SCENARIO_ID_KEY in record:
            record.setdefault(SCENARIO_ID_KEY, record.pop(LEGACY_SCENARIO_ID_KEY))
            changed += 1
    if changed and not dry_run:
        write_json_atomic(path, data)
    return changed


def migrate_world_pack(path: str, dry_run: bool) -> bool:
    """重写世界包 zip：manifest 资源键与方案成员目录改为 scenarios。"""
    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()
            if WORLD_MANIFEST_NAME not in names:
                return False
            manifest = json.loads(zf.read(WORLD_MANIFEST_NAME).decode("utf-8"))
            legacy_members = [n for n in names
                              if n.startswith(LEGACY_SCENARIO_RESOURCE_KEY + "/")]
            resources = manifest.get("resources") or {}
            has_legacy_key = LEGACY_SCENARIO_RESOURCE_KEY in resources
            if not legacy_members and not has_legacy_key:
                return False
            if dry_run:
                return True
            members = {n: zf.read(n) for n in names}
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError) as e:
        print(f"  跳过（无法读取）: {path} - {e}")
        return False

    # manifest：resources.dungeons -> resources.scenarios
    if has_legacy_key and SCENARIO_RESOURCE_KEY not in resources:
        resources[SCENARIO_RESOURCE_KEY] = resources.pop(LEGACY_SCENARIO_RESOURCE_KEY)
    manifest["resources"] = resources

    backup = path + ".bak"
    if not os.path.exists(backup):
        shutil.copy2(path, backup)
    tmp = path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, payload in members.items():
            if name == WORLD_MANIFEST_NAME:
                zf.writestr(name, json.dumps(manifest, ensure_ascii=False, indent=2))
                continue
            if name.startswith(LEGACY_SCENARIO_RESOURCE_KEY + "/"):
                name = SCENARIO_RESOURCE_KEY + name[len(LEGACY_SCENARIO_RESOURCE_KEY):]
            zf.writestr(name, payload)
    os.replace(tmp, path)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="副本方案命名迁移（S1.5）")
    parser.add_argument("--data-dir", default=data_dir(), help="数据根目录")
    parser.add_argument("--dry-run", action="store_true", help="只报告，不写入")
    args = parser.parse_args()
    data_root = args.data_dir
    print(f"数据目录: {data_root}" + ("（dry-run）" if args.dry_run else ""))

    # 1. 方案目录迁移（ScenarioRepo 构造时自动完成）
    repo = ScenarioRepo(data_dir=data_root)
    print(f"方案目录: {repo.write_root}（{len(repo.list_all())} 个方案）")

    # 2. data/user/endings.json
    endings_path = os.path.join(data_root, "user", "endings.json")
    if os.path.exists(endings_path):
        changed = migrate_json_records(endings_path, "records", args.dry_run)
        print(f"endings.json: 改写 {changed} 条记录的方案 id 字段")
    else:
        print("endings.json: 不存在，跳过")

    # 3. 角色档案
    archives_root = os.path.join(data_root, "archives")
    touched = 0
    if os.path.isdir(archives_root):
        for name in sorted(os.listdir(archives_root)):
            info_path = os.path.join(archives_root, name, "info.json")
            if not os.path.isfile(info_path):
                continue
            changed = migrate_json_records(info_path, "achieved_endings", args.dry_run)
            if changed:
                touched += 1
                print(f"{info_path}: 改写 {changed} 条结局索引")
    print(f"角色档案: {touched} 份需要改写")

    # 4. 世界包
    worlds_root = os.path.join(data_root, "worlds")
    packs = []
    if os.path.isdir(worlds_root):
        packs = [os.path.join(worlds_root, name) for name in sorted(os.listdir(worlds_root))
                 if name.endswith(".world.zip")]
    rewritten = 0
    for pack in packs:
        if migrate_world_pack(pack, args.dry_run):
            rewritten += 1
            print(f"世界包已改写: {os.path.basename(pack)}")
    print(f"世界包: {rewritten}/{len(packs)} 需要改写")

    print("完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
