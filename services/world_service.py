"""世界包服务层：导入、导出、激活、卸载、解散、从当前世界创建。

世界包归档：``<world_id>.world.zip``；解压后部署目录：``data/worlds/<world_id>/``。
激活世界包时，包内资源按类型接管各数据源，自由数据不受影响。
"""

import json
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from persistence.world_pack import (
    WORLD_MANIFEST_NAME,
    WORLD_PACK_EXT,
    TABLE_SETTING_KEYS,
    BOOL_RESOURCE_TYPES,
    WorldPackManifest,
    WorldState,
    installed_dir,
    load_manifest_file,
    resolve_behavior_source,
    save_manifest_file,
    validate_archive,
    worlds_dir,
)
from behavior_runtime import get_runtime

_MEMBER_TRAVERSAL_RE = re.compile(r"(^|/)\s*\.\.\s*(/|$)")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def _safe_member(member: str) -> bool:
    normalized = member.replace("\\", "/")
    if normalized.startswith("/"):
        return False
    if _WINDOWS_DRIVE_RE.match(normalized):
        return False
    if _MEMBER_TRAVERSAL_RE.search(normalized):
        return False
    return True


def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


class WorldManager:
    """世界包生命周期管理：查询、导入、导出、激活、卸载、解散、创建。"""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir
        self.world_state = WorldState(data_dir)

    # ---------- 查询 ----------

    def get_manifest(self, world_id: str) -> WorldPackManifest:
        """读取并校验已部署世界包的清单；不存在或无效时抛出 ValueError。"""
        path = os.path.join(installed_dir(self.data_dir, world_id), WORLD_MANIFEST_NAME)
        if not os.path.isfile(path):
            raise ValueError(f"世界包 '{world_id}' 不存在（{path}）")
        return load_manifest_file(path)

    def list_packs(self) -> List[dict]:
        """列出 data/worlds/ 下所有已部署世界包的信息。"""
        root = worlds_dir(self.data_dir)
        result = []
        if not os.path.isdir(root):
            return result
        for entry in sorted(os.listdir(root)):
            entry_path = os.path.join(root, entry)
            if not os.path.isdir(entry_path):
                continue
            manifest_path = os.path.join(entry_path, WORLD_MANIFEST_NAME)
            if not os.path.isfile(manifest_path):
                continue
            try:
                manifest = load_manifest_file(manifest_path)
            except ValueError as e:
                result.append({
                    "world_id": entry, "name": entry, "version": "",
                    "description": "", "owned_types": [], "error": str(e),
                    "active": False, "manifest": None,
                })
                continue
            result.append({
                "world_id": manifest.world_id,
                "name": manifest.name,
                "version": manifest.version,
                "description": manifest.description,
                "owned_types": sorted(manifest.owned_types()),
                "error": None,
                "active": self.world_state.active
                           and self.world_state.world_id == manifest.world_id,
                "manifest": manifest,
            })
        return result

    # ---------- 导入 / 导出 / 删除 ----------

    def import_pack(self, archive_path: str) -> WorldPackManifest:
        """校验并解压 .world.zip 到 data/worlds/<world_id>/；失败时回滚。"""
        manifest = validate_archive(archive_path)
        target = installed_dir(self.data_dir, manifest.world_id)
        if os.path.exists(target):
            raise ValueError(
                f"世界包「{manifest.name}」已存在（world_id: {manifest.world_id}），"
                f"请先删除旧包")
        tmp = tempfile.mkdtemp(prefix="world_import_")
        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                for member in zf.namelist():
                    if not _safe_member(member):
                        raise ValueError(f"世界包包含不安全的成员路径: {member}")
                zf.extractall(tmp)
            load_manifest_file(os.path.join(tmp, WORLD_MANIFEST_NAME))
            os.makedirs(worlds_dir(self.data_dir), exist_ok=True)
            shutil.copytree(tmp, target)
        except Exception:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(target, ignore_errors=True)
            raise
        return manifest

    def export_pack(self, world_id: str, target_path: Optional[str] = None) -> str:
        """把已部署世界包打包为 .world.zip；返回目标文件路径。"""
        installed = installed_dir(self.data_dir, world_id)
        if not os.path.isdir(installed):
            raise ValueError(f"世界包 '{world_id}' 不存在（{installed}）")
        load_manifest_file(os.path.join(installed, WORLD_MANIFEST_NAME))
        target = target_path or os.path.join(
            worlds_dir(self.data_dir), f"{world_id}{WORLD_PACK_EXT}")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
            for dirpath, _dirs, files in os.walk(installed):
                for filename in files:
                    full = os.path.join(dirpath, filename)
                    rel = os.path.relpath(full, installed)
                    zf.write(full, rel)
        return target

    def delete_pack(self, world_id: str) -> None:
        """删除已部署世界包；激活中的包不可删除。"""
        if self.world_state.active and self.world_state.world_id == world_id:
            raise ValueError("请先卸载当前激活的世界包")
        target = installed_dir(self.data_dir, world_id)
        if not os.path.isdir(target):
            raise ValueError(f"世界包 '{world_id}' 不存在（{target}）")
        shutil.rmtree(target)

    # ---------- 激活 / 卸载 ----------

    def load_active(self, world_id: str) -> WorldPackManifest:
        """按已持久化的 active_world 恢复激活状态，并加载包内行为包。"""
        manifest = self.get_manifest(world_id)
        self.world_state.set_active(manifest)
        get_runtime().load_pack(manifest, installed_dir(self.data_dir, world_id))
        return manifest

    def apply_world_settings(self, settings: Dict[str, Any]) -> None:
        """把激活包的设置叠加到内存 settings（世界块键 + 风格接管 + 表名校正）。"""
        if not self.world_state.active:
            return
        manifest = self.world_state.manifest
        settings.update(self.world_state.effective_settings(settings))
        if manifest.owns("landmarks"):
            settings["selected_styles"] = list(manifest.resources.get("landmarks", []))
        if manifest.owns("quips"):
            settings["selected_quip_styles"] = list(manifest.resources.get("quips", []))
        if manifest.owns("names"):
            tables = list(manifest.resources.get("names", []))
            if settings.get("name_table") not in tables and tables:
                settings["name_table"] = tables[0]
        if manifest.owns("news"):
            tables = list(manifest.resources.get("news", []))
            if settings.get("news_table") not in tables and tables:
                settings["news_table"] = tables[0]
        if manifest.owns("presets"):
            tables = list(manifest.resources.get("presets", []))
            if settings.get("preset_table") not in tables and tables:
                settings["preset_table"] = tables[0]
        if manifest.owns("personalities"):
            tables = list(manifest.resources.get("personalities", []))
            if settings.get("personality_table") not in tables and tables:
                settings["personality_table"] = tables[0]

    def activate(self, world_id: str, settings: Dict[str, Any],
                 settings_repo=None) -> WorldPackManifest:
        """激活世界包：校验 → 接管 world_state → 内存叠加设置 → 持久化 active_world。"""
        if self.world_state.active:
            if self.world_state.world_id == world_id:
                return self.world_state.manifest
            raise ValueError(
                f"已有激活的世界包「{self.world_state.pack_name}」，请先卸载")
        manifest = self.load_active(world_id)
        settings["active_world"] = world_id
        self.apply_world_settings(settings)
        if settings_repo is not None:
            settings_repo.save(settings)
        return manifest

    def deactivate(self, settings: Dict[str, Any], settings_repo=None) -> None:
        """卸载世界包：从 settings_repo 重读自由设置并清空激活状态（持久化）。"""
        if not self.world_state.active:
            return
        if settings_repo is not None:
            settings.clear()
            settings.update(settings_repo.load())
        else:
            for key in self.world_state.locked_keys():
                settings.pop(key, None)
        settings.pop("active_world", None)
        self.world_state.set_active(None)
        get_runtime().reset()
        if settings_repo is not None:
            settings_repo.save(settings)

    # ---------- 解散 ----------

    def dissolve(self, world_id: str, settings: Dict[str, Any],
                 settings_repo=None, remove_pack: bool = True,
                 challenge_mgr=None) -> WorldPackManifest:
        """把世界包资源复制到自由数据目录，包设置并入自由设置，然后卸载并删除包。

        challenge_mgr 非空时，附带挑战包会复制到自由目录并注册对应秘钥。
        """
        manifest = self.get_manifest(world_id)
        installed = installed_dir(self.data_dir, world_id)
        was_active = (self.world_state.active
                      and self.world_state.world_id == world_id)
        if was_active:
            self.deactivate(settings, settings_repo)
        self._copy_resources(manifest, installed, challenge_mgr=challenge_mgr)
        if settings_repo is not None:
            free = settings_repo.load()
            free.update({k: v for k, v in manifest.settings.items()
                         if k in manifest.locked_keys()})
            settings.clear()
            settings.update(free)
            settings_repo.save(free)
        else:
            settings.update({k: v for k, v in manifest.settings.items()
                             if k in manifest.locked_keys()})
        if remove_pack:
            shutil.rmtree(installed, ignore_errors=True)
        return manifest

    @staticmethod
    def _safe_suffix(manifest: WorldPackManifest) -> str:
        """生成用于冲突重命名的后缀：世界包名称_版本号。"""
        name = re.sub(r'[\\/:*?"<>|\s]+', "_", manifest.name).strip("_")
        if not name:
            name = manifest.world_id or "world"
        return f"{name}_{manifest.version}"

    @staticmethod
    def _target_name(dst_dir: str, name: str, ext: str,
                     manifest: WorldPackManifest) -> str:
        """返回避免与自由资源冲突的目标资源名。

        目标已存在同名资源时，追加“世界包名称_版本号”；仍冲突则追加序号。
        """
        if not os.path.exists(os.path.join(dst_dir, f"{name}{ext}")):
            return name
        base = f"{name}_{WorldManager._safe_suffix(manifest)}"
        candidate = base
        counter = 2
        while os.path.exists(os.path.join(dst_dir, f"{candidate}{ext}")):
            candidate = f"{base}_{counter}"
            counter += 1
        return candidate

    def _copy_resources(self, manifest: WorldPackManifest, installed: str,
                        challenge_mgr=None) -> None:
        packs_root = os.path.join(self.data_dir, "packs")
        static_root = os.path.join(self.data_dir, "static")
        for style in manifest.resources.get("landmarks", []):
            dst_dir = os.path.join(packs_root, "landmarks")
            os.makedirs(dst_dir, exist_ok=True)
            dst = self._target_name(dst_dir, style, ".json", manifest)
            shutil.copy2(
                os.path.join(installed, "landmarks", f"{style}.json"),
                os.path.join(dst_dir, f"{dst}.json"))
        for style in manifest.resources.get("quips", []):
            dst_dir = os.path.join(packs_root, "quips")
            os.makedirs(dst_dir, exist_ok=True)
            dst = self._target_name(dst_dir, style, ".json", manifest)
            shutil.copy2(
                os.path.join(installed, "quips", f"{style}.json"),
                os.path.join(dst_dir, f"{dst}.json"))
        if manifest.owns("presets"):
            for table in manifest.resources.get("presets", []):
                dst_dir = os.path.join(static_root, "presets")
                os.makedirs(dst_dir, exist_ok=True)
                dst = self._target_name(dst_dir, table, ".csv", manifest)
                shutil.copy2(
                    os.path.join(installed, "presets", f"{table}.csv"),
                    os.path.join(dst_dir, f"{dst}.csv"))
                if dst != table:
                    manifest.settings["preset_table"] = dst
        if manifest.owns("personalities"):
            for table in manifest.resources.get("personalities", []):
                dst_dir = os.path.join(static_root, "personalities")
                os.makedirs(dst_dir, exist_ok=True)
                dst = self._target_name(dst_dir, table, ".csv", manifest)
                shutil.copy2(
                    os.path.join(installed, "personalities", f"{table}.csv"),
                    os.path.join(dst_dir, f"{dst}.csv"))
                if dst != table:
                    manifest.settings["personality_table"] = dst
        # 枚举包目录（而非仅清单声明），覆盖激活期间在包内新建的方案
        dungeons_root = os.path.join(installed, "dungeons")
        if os.path.isdir(dungeons_root):
            for dungeon_id in sorted(os.listdir(dungeons_root)):
                if dungeon_id == "_default":
                    continue
                src = os.path.join(dungeons_root, dungeon_id)
                if not os.path.isdir(src):
                    continue
                dst_dir = os.path.join(packs_root, "dungeons")
                os.makedirs(dst_dir, exist_ok=True)
                dst = self._target_name(dst_dir, dungeon_id, "", manifest)
                shutil.rmtree(os.path.join(dst_dir, dst), ignore_errors=True)
                shutil.copytree(src, os.path.join(dst_dir, dst))
        # 附带挑战包：复制到自由挑战目录并注册包内秘钥
        challenges_root = os.path.join(installed, "challenges")
        if os.path.isdir(challenges_root):
            pack_keys = {}
            keys_path = os.path.join(challenges_root, "keys.json")
            if os.path.isfile(keys_path):
                try:
                    with open(keys_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    pack_keys = data if isinstance(data, dict) else {}
                except (OSError, json.JSONDecodeError):
                    pass
            dst_dir = os.path.join(packs_root, "challenges")
            os.makedirs(dst_dir, exist_ok=True)
            for cf in sorted(os.listdir(challenges_root)):
                if not cf.endswith(".chal"):
                    continue
                dst = self._target_name(
                    dst_dir, os.path.splitext(cf)[0], ".chal", manifest)
                shutil.copy2(
                    os.path.join(challenges_root, cf),
                    os.path.join(dst_dir, f"{dst}.chal"))
                if challenge_mgr is not None:
                    key = pack_keys.get(os.path.splitext(cf)[0])
                    if key:
                        try:
                            challenge_mgr._register_key(f"{dst}.chal", key)
                        except Exception as e:
                            print(f"[WorldPack] 附带挑战包秘钥注册失败 {dst}.chal: {e}")
        for table in manifest.resources.get("names", []):
            dst_dir = os.path.join(static_root, "names")
            os.makedirs(dst_dir, exist_ok=True)
            dst = self._target_name(dst_dir, table, ".csv", manifest)
            shutil.copy2(
                os.path.join(installed, "names", f"{table}.csv"),
                os.path.join(dst_dir, f"{dst}.csv"))
            if dst != table:
                manifest.settings["name_table"] = dst
        for table in manifest.resources.get("news", []):
            dst_dir = os.path.join(static_root, "news")
            os.makedirs(dst_dir, exist_ok=True)
            dst = self._target_name(dst_dir, table, ".csv", manifest)
            shutil.copy2(
                os.path.join(installed, "news", f"{table}.csv"),
                os.path.join(dst_dir, f"{dst}.csv"))
            if dst != table:
                manifest.settings["news_table"] = dst
        # 行为包是静态资源目录，目录名冲突时整体改名。
        for behavior in manifest.resources.get("behaviors", []):
            src = resolve_behavior_source(
                os.path.join(installed, "behaviors"), behavior)
            if src is None:
                continue
            dst_dir = os.path.join(static_root, "behaviors")
            os.makedirs(dst_dir, exist_ok=True)
            dst = self._target_name(dst_dir, behavior, "", manifest)
            self._copy_behavior_pack(src, os.path.join(dst_dir, dst))

    # ---------- 从当前世界创建 ----------

    def create_from_current(
            self, world_id: str, name: str,
            settings: Dict[str, Any],
            landmark_repo=None, quip_repo=None,
            preset_repo=None, personality_repo=None,
            dungeon_repo=None, name_repo=None, news_service=None,
            challenge_mgr=None,
            version: str = "1.0", author: str = "", description: str = "",
            selected_resources: Optional[Dict[str, List[str]]] = None,
    ) -> WorldPackManifest:
        """把当前世界（设置 + 选中风格 + 全部自由资源）打包为世界包。

        selected_resources 为 {资源类型: [具体条目...]}，仅打包其中列出的条目；
        challenge_mgr 提供自由挑战包路径（附带挑战包默认不携带密钥）。
        """
        target = installed_dir(self.data_dir, world_id)
        if os.path.exists(target):
            raise ValueError(f"世界包 '{world_id}' 已存在（{target}）")
        manifest = WorldPackManifest(
            world_id=world_id, name=name, version=version,
            author=author, description=description)
        os.makedirs(target, exist_ok=True)
        try:
            self._collect_resources(manifest, target, settings,
                                    landmark_repo, quip_repo, preset_repo,
                                    personality_repo, dungeon_repo,
                                    name_repo, news_service,
                                    challenge_mgr,
                                    selected_resources)
            for key in ("world_setting", "seed"):
                if key in settings and settings.get(key) is not None:
                    manifest.settings[key] = settings[key]
            for setting_key, rtype in TABLE_SETTING_KEYS.items():
                if not manifest.owns(rtype):
                    continue
                tables = list(manifest.resources.get(rtype) or [])
                current = settings.get(setting_key)
                manifest.settings[setting_key] = (
                    current if current in tables else tables[0])
            save_manifest_file(os.path.join(target, WORLD_MANIFEST_NAME), manifest)
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise
        return manifest

    def _collect_resources(self, manifest: WorldPackManifest, target: str,
                           settings: Dict[str, Any],
                           landmark_repo, quip_repo, preset_repo,
                           personality_repo, dungeon_repo,
                           name_repo, news_service,
                           challenge_mgr,
                           selected_resources: Optional[Dict[str, List[str]]] = None) -> None:
        resources: Dict[str, Any] = {}
        selected = selected_resources or {}
        styles = [s for s in selected.get("landmarks", [])
                  if landmark_repo is not None and s in landmark_repo.get_styles()]
        if styles:
            os.makedirs(os.path.join(target, "landmarks"), exist_ok=True)
            for style in styles:
                _save_json(os.path.join(target, "landmarks", f"{style}.json"),
                           [asdict(lm) for lm in landmark_repo.load(style)])
            resources["landmarks"] = styles
        styles = [s for s in selected.get("quips", [])
                  if quip_repo is not None and s in quip_repo.get_styles()]
        if styles:
            os.makedirs(os.path.join(target, "quips"), exist_ok=True)
            for style in styles:
                serializable = {}
                for size_cat, matrix in quip_repo.load(style).items():
                    serializable[size_cat] = {}
                    for (i, d), quip_list in matrix.items():
                        serializable[size_cat][f"{i}_{d}"] = quip_list
                _save_json(os.path.join(target, "quips", f"{style}.json"),
                           serializable)
            resources["quips"] = styles
        tables = [t for t in selected.get("presets", [])
                  if preset_repo is not None and t in preset_repo.get_tables()]
        if tables:
            os.makedirs(os.path.join(target, "presets"), exist_ok=True)
            for table in tables:
                preset_repo.write_csv(
                    os.path.join(target, "presets", f"{table}.csv"),
                    preset_repo.load(table))
            resources["presets"] = tables
        tables = [t for t in selected.get("personalities", [])
                  if personality_repo is not None and t in personality_repo.get_tables()]
        if tables:
            os.makedirs(os.path.join(target, "personalities"), exist_ok=True)
            for table in tables:
                personality_repo.write_csv(
                    os.path.join(target, "personalities", f"{table}.csv"),
                    personality_repo.load(table))
            resources["personalities"] = tables
        dungeon_ids = [d for d in selected.get("dungeons", [])
                       if d != "_default"
                       and dungeon_repo is not None and dungeon_repo.exists(d)]
        if dungeon_ids:
            for dungeon_id in dungeon_ids:
                shutil.copytree(
                    os.path.join(dungeon_repo.root, dungeon_id),
                    os.path.join(target, "dungeons", dungeon_id))
            resources["dungeons"] = dungeon_ids
        # 附带挑战包：只复制 .chal 文件，默认不携带密钥（如需随包共享，
        # 由用户手动复制 keys.json 到包内 challenges 目录）
        challenge_root = (challenge_mgr.storage_dir if challenge_mgr is not None
                          else os.path.join(self.data_dir, "packs", "challenges"))
        challenge_files = [f for f in selected.get("challenges", [])
                           if f.endswith(".chal")]
        collected = []
        for filename in challenge_files:
            src = os.path.join(challenge_root, filename)
            if os.path.isfile(src):
                os.makedirs(os.path.join(target, "challenges"), exist_ok=True)
                shutil.copy2(src, os.path.join(target, "challenges", filename))
                collected.append(filename)
        if collected:
            resources["challenges"] = collected
        tables = [t for t in selected.get("names", [])
                  if name_repo is not None and t in name_repo.get_tables()]
        if tables:
            os.makedirs(os.path.join(target, "names"), exist_ok=True)
            for table in tables:
                shutil.copy2(name_repo.resolve(table),
                             os.path.join(target, "names", f"{table}.csv"))
            resources["names"] = tables
        tables = [t for t in selected.get("news", [])
                  if news_service is not None and t in news_service.get_tables()]
        if tables:
            os.makedirs(os.path.join(target, "news"), exist_ok=True)
            for table in tables:
                shutil.copy2(news_service.resolve(table),
                             os.path.join(target, "news", f"{table}.csv"))
            resources["news"] = tables
        # 行为包只能单选：优先静态目录，其次当前激活世界包内的同名目录。
        behavior_names = selected.get("behaviors", [])[:1]
        if behavior_names:
            name = behavior_names[0]
            src = resolve_behavior_source(
                os.path.join(self.data_dir, "static", "behaviors"), name)
            if src is None and self.world_state.active:
                pack_root = self.world_state.pack_root()
                if pack_root:
                    src = resolve_behavior_source(
                        os.path.join(pack_root, "behaviors"), name)
            if src is not None:
                os.makedirs(os.path.join(target, "behaviors"), exist_ok=True)
                self._copy_behavior_pack(
                    src, os.path.join(target, "behaviors", name))
                resources["behaviors"] = [name]
        manifest.resources = resources

    @staticmethod
    def _copy_behavior_pack(src: str, dst: str) -> None:
        """把行为包复制为目录。旧版单文件 ``.py`` 会包进同名文件夹。"""
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
        if os.path.isdir(src):
            shutil.copytree(src, dst, ignore=ignore)
            return
        os.makedirs(dst, exist_ok=True)
        shutil.copy2(src, os.path.join(dst, os.path.basename(src)))
