"""副本方案（Scenario）仓库：读写 ``data/packs/scenarios/<方案 id>/``。

**术语提醒**：这里持久化的是**方案定义**（章节 / 触发器 / 提示词 / 演化属性 /
显示组件 / 素材），不是「一局」。本模块此前叫 ``dungeon_repo`` / ``DungeonRepo``，
名字取自「一局（run）」，与实际职责不符，故更名（见 ``dungeon/terms.py``）。

**读写根的区别**（世界包接管方案时最容易踩的坑）：

- ``read_root``：世界包声明拥有 ``scenarios`` 时指向世界包目录，否则同写根；
- ``write_root``：永远是用户可写的自由根 ``data/packs/scenarios``；
- ``list_all()`` / ``exists()`` / ``scenario_dir()`` 同时看两个根（世界包方案可读、
  用户新方案写在自由根），``create()`` / ``save_config()`` / ``delete()`` 只写写根。
  改动前是「读只读根、写自由根」两边不交圈，世界包接管时会读不到自己刚存的方案。
"""

import os
import shutil

from dungeon.chapters import normalize_chapters
from dungeon.coupling import normalize_coupling_level
from dungeon.schema import empty_scenario_config
from dungeon.validate import format_diagnostics, validate_scenario_config
from dungeon.terms import (DEFAULT_SCENARIO_ID, LEGACY_SCENARIO_RESOURCE_KEY,
                           SCENARIO_CONFIG_NAME, SCENARIO_RESOURCE_KEY,
                           is_default_scenario)
from .json_store import load_json_with_backup, write_json_atomic


class ScenarioRepo:
    def __init__(self, data_dir: str = "data", world_state=None):
        self._data_dir = data_dir
        self._world_state = world_state
        self._write_root = os.path.join(data_dir, "packs", SCENARIO_RESOURCE_KEY)
        # 最近一次 save_config 的校验诊断（编辑器/启动流程各取所需）
        self.last_diagnostics = []
        self._migrate_legacy_root()
        os.makedirs(self._write_root, exist_ok=True)
        self._ensure_default()

    # ---------- 根目录 ----------
    @property
    def write_root(self) -> str:
        """用户可写的自由根：``data/packs/scenarios``。"""
        return self._write_root

    @property
    def read_root(self) -> str:
        """读取根：世界包接管方案时为世界包目录，否则与写根相同。"""
        if self._world_state is not None and self._world_state.owns(SCENARIO_RESOURCE_KEY):
            return self._world_state.pack_path(SCENARIO_RESOURCE_KEY)
        return self._write_root

    @property
    def root(self) -> str:
        """素材/图标路径拼接用的根（可写根：编辑器导入素材写在这里）。"""
        return self._write_root

    def scenario_dir(self, scenario_id: str) -> str:
        """方案目录：优先读取根（世界包），其次写根；都不存在时返回写根下的路径。"""
        if not scenario_id:
            return ""
        for root in (self.read_root, self._write_root):
            candidate = os.path.join(root, scenario_id)
            if os.path.isdir(candidate):
                return candidate
        return os.path.join(self._write_root, scenario_id)


    def list_all(self) -> list:
        """全部方案 id（世界包 + 自由根，去重排序）。"""
        self._ensure_default()
        names = set()
        for root in (self.read_root, self._write_root):
            try:
                entries = os.listdir(root)
            except OSError:
                continue
            for name in entries:
                if os.path.isdir(os.path.join(root, name)):
                    names.add(name)
        return sorted(names)

    def exists(self, scenario_id: str) -> bool:
        if not scenario_id:
            return False
        return any(os.path.isdir(os.path.join(root, scenario_id))
                   for root in (self.read_root, self._write_root))

    def create(self, scenario_id: str, template_config: dict = None) -> bool:
        path = os.path.join(self._write_root, scenario_id)
        if os.path.exists(path):
            return False
        os.makedirs(path)
        config = template_config if template_config is not None else self._empty_config()
        self.save_config(scenario_id, config)
        return True

    def delete(self, scenario_id: str):
        if is_default_scenario(scenario_id):
            raise ValueError("不能删除默认副本方案")
        path = os.path.join(self._write_root, scenario_id)
        if os.path.exists(path):
            shutil.rmtree(path)

    def copy(self, src_id: str, dst_id: str) -> bool:
        if self.exists(dst_id):
            return False
        src_config = self.load_config(src_id)
        if src_config is None:
            return False
        return self.create(dst_id, src_config)

    def load_config(self, scenario_id: str) -> dict:
        if not scenario_id:
            return None
        # 世界包方案与自由方案都能读；主文件损坏时回退到同目录 .bak
        config = load_json_with_backup(
            os.path.join(self.scenario_dir(scenario_id), SCENARIO_CONFIG_NAME))
        if config is None:
            return None
        return self._migrate(config)

    def save_config(self, scenario_id: str, config: dict):
        # S2：保存即校验（不阻断写入——编辑器的增量自动保存可能经过中间态；
        # 诊断记入 last_diagnostics，由编辑器/启动流程决定如何展示）
        self.last_diagnostics = validate_scenario_config(
            config, scenario_dir=self.scenario_dir(scenario_id))
        if self.last_diagnostics:
            print(f"[ScenarioRepo] 方案「{scenario_id}」校验提示：\n"
                  + format_diagnostics(self.last_diagnostics))
        scenario_dir = os.path.join(self._write_root, scenario_id)
        os.makedirs(scenario_dir, exist_ok=True)
        # 原子写 + 覆盖前留 .bak：编辑器保存方案时崩溃/断电不会把配置截断清零
        write_json_atomic(os.path.join(scenario_dir, SCENARIO_CONFIG_NAME), config)

    def _ensure_default(self):
        if not self.exists(DEFAULT_SCENARIO_ID):
            config = self._empty_config()
            config["triggers"] = []
            self.create(DEFAULT_SCENARIO_ID, config)

    def _migrate_legacy_root(self):
        """把改名前的 ``packs/dungeons`` 就地并入 ``packs/scenarios``（幂等）。

        S1.5 之前方案目录与「一局」同名 ``dungeons``。这里只搬目录、不解析内容：
        同名方案两边都有时保留 config.json 较新的一份，旧的留作 ``.bak``。
        """
        legacy_root = os.path.join(self._data_dir, "packs", LEGACY_SCENARIO_RESOURCE_KEY)
        if not os.path.isdir(legacy_root):
            return
        try:
            names = sorted(n for n in os.listdir(legacy_root)
                           if os.path.isdir(os.path.join(legacy_root, n)))
        except OSError as e:
            print(f"[ScenarioRepo] 旧方案目录读取失败: {e}")
            return
        os.makedirs(self._write_root, exist_ok=True)
        moved = 0
        for name in names:
            src = os.path.join(legacy_root, name)
            dst = os.path.join(self._write_root, name)
            try:
                if not os.path.exists(dst):
                    shutil.move(src, dst)
                    moved += 1
                elif _config_mtime(src) > _config_mtime(dst):
                    _backup_config(dst)
                    shutil.rmtree(dst, ignore_errors=True)
                    shutil.move(src, dst)
                    moved += 1
                    print(f"[ScenarioRepo] 方案「{name}」旧目录较新，已用旧目录覆盖")
                else:
                    shutil.rmtree(src, ignore_errors=True)
                    print(f"[ScenarioRepo] 方案「{name}」新旧目录并存，保留新目录")
            except OSError as e:
                print(f"[ScenarioRepo] 方案「{name}」迁移失败: {e}")
        try:
            if not os.listdir(legacy_root):
                os.rmdir(legacy_root)
        except OSError:
            pass
        if moved:
            print(f"[ScenarioRepo] 旧方案目录 packs/{LEGACY_SCENARIO_RESOURCE_KEY} "
                  f"已迁移 {moved} 个方案到 packs/{SCENARIO_RESOURCE_KEY}")

    def _empty_config(self) -> dict:
        """空方案模板：字段与默认值以 ``dungeon/schema.py`` 的声明为准（单一真相源）。"""
        return empty_scenario_config()

    @staticmethod
    def _migrate(config: dict) -> dict:
        # 以原配置为底稿补齐/归一化字段：未知字段与 components 等
        # 未被本函数显式处理的键都原样保留，避免读写一轮后丢数据。
        new_config = dict(config)
        new_config.update({
            "initial_prompt": config.get("initial_prompt", ""),
            "coupling_level": normalize_coupling_level(config.get("coupling_level")),
            "protagonist_title": str(config.get("protagonist_title", "") or "").strip(),
            "entry_action_cost": max(0, int(config.get("entry_action_cost", 0) or 0)),
            "section_prompts": config.get("section_prompts", {
                "background": "", "branch": "", "dialog": "",
                "interaction": "", "action": ""
            }),
            "section_steps": config.get("section_steps", {}),
            "transition_matrix": config.get("transition_matrix"),
            "triggers": config.get("triggers", []),
            "chapters": normalize_chapters(config.get("chapters", [])),
        })
        # 旧配置把进化量写成 custom_attrs，迁移成 evolution_attrs 后移除旧键
        new_config.pop("custom_attrs", None)
        new_config.pop("custom_attrs_def", None)
        # 旧的「副本窗口视图」已改造为耦合等级，视图字段不再保留
        new_config.pop("view_mode", None)

        if "evolution_attrs" in config:
            new_config["evolution_attrs"] = config["evolution_attrs"]
        else:
            old_attrs = config.get("custom_attrs", [])
            evolution_attrs = [
                {"type": "intrusion", "name": "介入度", "display_state": "collapse"},
                {"type": "destruction", "name": "破坏性", "display_state": "collapse"},
            ]
            for attr in old_attrs:
                if isinstance(attr, dict):
                    evolution_attrs.append({
                        "type": "custom",
                        "name": attr.get("name", "未命名"),
                        "display_state": "show",
                        "init_value": attr.get("init_value", 0.0),
                        "rate": attr.get("rate", 1.0),
                        "random_offset": attr.get("random_offset", 0.0),
                    })
                elif isinstance(attr, str):
                    evolution_attrs.append({
                        "type": "custom",
                        "name": attr,
                        "display_state": "show",
                        "init_value": 0.0,
                        "rate": 1.0,
                        "random_offset": 0.0,
                    })
            new_config["evolution_attrs"] = evolution_attrs

        # 迁移：确保总伤亡条目存在（与介入度/破坏性同级）
        evolution_attrs = new_config["evolution_attrs"]
        if not any(attr.get("type") == "casualty" for attr in evolution_attrs):
            evolution_attrs.append({"type": "casualty", "name": "总伤亡", "display_state": "collapse"})

        triggers = new_config.get("triggers", [])
        if triggers:
            id_to_name = {}
            name_count = {}
            new_triggers = []
            for t in triggers:
                new_t = t.copy()
                if "id" in new_t and "name" not in new_t:
                    base_name = f"trigger_{new_t['id']}"
                elif "name" in new_t:
                    base_name = new_t["name"]
                else:
                    base_name = "trigger_unknown"
                count = name_count.get(base_name, 0)
                if count > 0:
                    name = f"{base_name}_{count}"
                else:
                    name = base_name
                name_count[base_name] = count + 1
                new_t["name"] = name
                if "id" in new_t:
                    id_to_name[new_t["id"]] = name
                    new_t.pop("id", None)
                new_triggers.append(new_t)

            for t in new_triggers:
                if "precondition_ids" in t:
                    pre_ids = t.pop("precondition_ids", [])
                    pre_names = []
                    for pid in pre_ids:
                        if pid in id_to_name:
                            pre_names.append(id_to_name[pid])
                        else:
                            pre_names.append(f"unknown_{pid}")
                    t["precondition_names"] = pre_names
                elif "precondition_names" not in t:
                    t["precondition_names"] = []
                # 所在章节：早期配置没有该字段，空值表示不限章节
                t.setdefault("chapter", "")
            new_config["triggers"] = new_triggers
        else:
            new_config["triggers"] = []
        return new_config


# ------------------ 旧目录迁移辅助 ------------------

def _config_mtime(scenario_dir: str) -> float:
    """方案 config.json 的修改时间；缺失返回 0（用于新旧目录比对）。"""
    try:
        return os.path.getmtime(os.path.join(scenario_dir, SCENARIO_CONFIG_NAME))
    except OSError:
        return 0.0


def _backup_config(scenario_dir: str) -> None:
    """覆盖迁移前把现有 config.json 另存为 .bak。"""
    path = os.path.join(scenario_dir, SCENARIO_CONFIG_NAME)
    try:
        if os.path.exists(path):
            shutil.copy2(path, path + ".bak")
    except OSError as e:
        print(f"[ScenarioRepo] 迁移备份失败（继续）: {e}")
