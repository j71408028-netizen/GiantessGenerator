"""把 data/archives 下角色档案的旧演化字段迁移为演化表（evolution）。

旧字段：intrusion / destruction / total_casualties 三个标量，
加 intrusion_evolution / destruction_evolution / casualties_evolution 三条平行列表。
新字段：evolution 列表，每行依次记录更改时间、步进、介入度、破坏性、
累计伤亡与更改标签（source，本脚本合成的行标注为 migrate_snapshot_evolution）。

迁移策略（尽力而为，历史数据未记录时间与步进）：
- 介入度/破坏性两条列表成对记录，按事件一一配对；
- 伤亡列表尾部对齐到事件；多出的前段是创建报告的逐段累计伤亡，折成一行创建记录；
- 历史行的更改时间在 created_at 与 updated_at 之间线性插值，步进记 0.0；
- 当前标量与最后一行不一致时（加载期衰退等未被记录的更改），追加一条当前值行。

原文件备份为同目录下的 info.json.bak；已含 evolution 字段的档案自动跳过，
但若其演化行缺少 source 标签，则回填 migrate_snapshot_evolution。

用法：python scripts/migrate_snapshot_evolution.py [--data-dir data]
"""

import argparse
import datetime
import json
import os
import shutil

OLD_SCALARS = ("intrusion", "destruction", "total_casualties")
OLD_LISTS = ("intrusion_evolution", "destruction_evolution", "casualties_evolution")


def _parse_time(value, fallback):
    try:
        return datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return fallback


def _interpolate_times(count, start, end):
    """在 start 与 end 之间线性插值出 count 个时间点；count 为 1 时取 start。"""
    if count <= 1:
        return [start.isoformat()]
    span = (end - start).total_seconds()
    return [(start + datetime.timedelta(seconds=span * i / (count - 1))).isoformat()
            for i in range(count)]


MIGRATION_SOURCE = "migrate_snapshot_evolution"


def build_evolution_rows(data):
    """按旧字段构造演化表行（不含 changed_at，时间在写入前统一插值）。"""
    intr = float(data.get("intrusion") or 0.0)
    dest = float(data.get("destruction") or 0.0)
    cas = float(data.get("total_casualties") or 0.0)
    intr_evo = [float(v) for v in (data.get("intrusion_evolution") or [])]
    dest_evo = [float(v) for v in (data.get("destruction_evolution") or [])]
    cas_evo = [float(v) for v in (data.get("casualties_evolution") or [])]

    rows = []

    def add_row(intrusion, destruction, casualties):
        rows.append({"step": 0.0, "intrusion": intrusion,
                     "destruction": destruction, "casualties": casualties,
                     "source": MIGRATION_SOURCE})

    # 伤亡列表多出的前段是创建报告的逐段累计伤亡，折成一行；
    # 介入度/破坏性的逐段值未存档，以首个事件值近似
    n_events = min(len(intr_evo), len(dest_evo))
    cas_offset = len(cas_evo) - n_events
    if cas_offset > 0:
        add_row(intr_evo[0] if intr_evo else intr,
                dest_evo[0] if dest_evo else dest,
                cas_evo[cas_offset - 1])

    last_cas = cas_evo[cas_offset - 1] if cas_offset > 0 else 0.0
    for i in range(n_events):
        cas_idx = cas_offset + i
        if 0 <= cas_idx < len(cas_evo):
            last_cas = cas_evo[cas_idx]
        add_row(intr_evo[i], dest_evo[i], last_cas)

    # 当前标量与最后记录不一致（加载期衰退等未被记录的更改）时补一条当前值行
    if rows:
        last = rows[-1]
        if (abs(last["intrusion"] - intr) > 1e-9
                or abs(last["destruction"] - dest) > 1e-9
                or abs(last["casualties"] - cas) > 1e-9):
            add_row(intr, dest, cas)
    else:
        add_row(intr, dest, cas)
    return rows


def migrate_file(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if "evolution" in data:
        # 已迁移过：仅当演化行缺少 source 标签时回填
        missing = [row for row in data["evolution"]
                   if isinstance(row, dict) and not row.get("source")]
        if not missing:
            return "跳过（已含 evolution 字段）"
        backup = os.path.join(os.path.dirname(path), "info.json.bak")
        if not os.path.exists(backup):
            shutil.copy2(path, backup)
        for row in missing:
            row["source"] = MIGRATION_SOURCE
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return f"回填 {len(missing)} 行的 source 标签（备份：info.json.bak）"

    rows = build_evolution_rows(data)
    start = _parse_time(data.get("created_at"), datetime.datetime.now())
    end = max(start, _parse_time(data.get("updated_at"), start))
    times = _interpolate_times(len(rows), start, end)
    evolution = [dict(changed_at=t, **row) for t, row in zip(times, rows)]

    for key in OLD_SCALARS + OLD_LISTS:
        data.pop(key, None)
    # evolution 放在 body_parts 之后，保持与模型字段一致的顺序
    new_data = {}
    for key, value in data.items():
        new_data[key] = value
        if key == "body_parts":
            new_data["evolution"] = evolution
    new_data.setdefault("evolution", evolution)

    backup = os.path.join(os.path.dirname(path), "info.json.bak")
    if not os.path.exists(backup):
        shutil.copy2(path, backup)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(new_data, f, ensure_ascii=False, indent=2)
    return f"迁移完成，共 {len(evolution)} 行（备份：info.json.bak）"


def main():
    parser = argparse.ArgumentParser(
        description="把角色档案的旧演化字段迁移为演化表 evolution")
    parser.add_argument("--data-dir", default="data", help="数据目录（默认 data）")
    args = parser.parse_args()
    archives = os.path.join(args.data_dir, "archives")
    if not os.path.isdir(archives):
        print(f"未找到角色档案目录：{archives}")
        return
    for name in sorted(os.listdir(archives)):
        path = os.path.join(archives, name, "info.json")
        if not os.path.isfile(path):
            continue
        try:
            print(f"{name}: {migrate_file(path)}")
        except Exception as e:
            print(f"{name}: 迁移失败 - {e}")


if __name__ == "__main__":
    main()
