import json
import os
import shutil


class DungeonRepo:
    def __init__(self, data_dir: str = "data", world_state=None):
        self._data_dir = data_dir
        self._world_state = world_state
        self._free_root = os.path.join(data_dir, "packs", "dungeons")
        os.makedirs(self._free_root, exist_ok=True)
        self._ensure_default()

    def _read_root(self) -> str:
        if self._world_state is not None and self._world_state.owns("dungeons"):
            return self._world_state.pack_path("dungeons")
        return self._free_root

    @property
    def root(self) -> str:
        return self._read_root()

    def list_all(self) -> list:
        self._ensure_default()
        read_root = self._read_root()
        dirs = [d for d in os.listdir(read_root) if os.path.isdir(os.path.join(read_root, d))]
        return sorted(dirs)

    def exists(self, dungeon_id: str) -> bool:
        return os.path.isdir(os.path.join(self._read_root(), dungeon_id))

    def create(self, dungeon_id: str, template_config: dict = None) -> bool:
        path = os.path.join(self._free_root, dungeon_id)
        if os.path.exists(path):
            return False
        os.makedirs(path)
        config = template_config if template_config is not None else self._empty_config()
        self.save_config(dungeon_id, config)
        return True

    def delete(self, dungeon_id: str):
        if dungeon_id == "_default":
            raise ValueError("不能删除默认副本")
        path = os.path.join(self._free_root, dungeon_id)
        if os.path.exists(path):
            shutil.rmtree(path)

    def copy(self, src_id: str, dst_id: str) -> bool:
        if self.exists(dst_id):
            return False
        src_config = self.load_config(src_id)
        if src_config is None:
            return False
        return self.create(dst_id, src_config)

    def load_config(self, dungeon_id: str) -> dict:
        path = os.path.join(self._read_root(), dungeon_id, "config.json")
        if not os.path.exists(path):
            return None
        with open(path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        return self._migrate(config)

    def save_config(self, dungeon_id: str, config: dict):
        dungeon_dir = os.path.join(self._free_root, dungeon_id)
        os.makedirs(dungeon_dir, exist_ok=True)
        path = os.path.join(dungeon_dir, "config.json")
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

    def _ensure_default(self):
        if not self.exists("_default"):
            config = self._empty_config()
            config["triggers"] = []
            self.create("_default", config)

    def _empty_config(self) -> dict:
        return {
            "initial_prompt": "",
            "view_mode": "story",
            "entry_action_cost": 0,
            "section_prompts": {
                "background": "", "branch": "", "dialog": "",
                "interaction": "", "action": ""
            },
            "evolution_attrs": [
                {"type": "intrusion", "name": "介入度", "display_state": "collapse"},
                {"type": "destruction", "name": "破坏性", "display_state": "collapse"},
                {"type": "casualty", "name": "总伤亡", "display_state": "collapse"},
            ],
            "triggers": []
        }

    @staticmethod
    def _migrate(config: dict) -> dict:
        new_config = {
            "initial_prompt": config.get("initial_prompt", ""),
            "view_mode": config.get("view_mode", "story"),
            "entry_action_cost": max(0, int(config.get("entry_action_cost", 0) or 0)),
            "section_prompts": config.get("section_prompts", {
                "background": "", "branch": "", "dialog": "",
                "interaction": "", "action": ""
            }),
            "section_steps": config.get("section_steps", {}),
            "transition_matrix": config.get("transition_matrix"),
            "triggers": config.get("triggers", []),
        }

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
            new_config["triggers"] = new_triggers
        else:
            new_config["triggers"] = []
        return new_config
