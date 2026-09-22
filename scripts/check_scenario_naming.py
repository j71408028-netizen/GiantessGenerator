"""S1.5 命名守卫 + 兼容行为自检（无 GUI，不触碰真实 data/ 目录）。

用法：``python scripts/check_scenario_naming.py``
输出：控制台一行 ASCII 结论 + UTF-8 报告文件路径。

1. 静态守卫（AST）：仓库 .py 里不得再出现指代「副本方案」的旧标识符
   （``dungeon_id`` / ``DungeonRepo`` / ``ScriptManager`` 等），以及裸写的
   ``"dungeons"`` 资源键字符串；白名单见 ``ALLOWED_FILES``（terms.py 的
   旧契约常量与迁移脚本）。
2. 行为自检：``dungeon.terms`` 的兼容读、``ScenarioRepo`` 的旧目录自愈迁移
   与读写根语义、``WorldPackManifest`` 的旧资源键归一化与旧成员路径兼容。
"""

import ast
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 静态守卫：允许出现旧标识符/旧字面量的文件（相对路径，正斜杠）
ALLOWED_FILES = {
    "dungeon/terms.py",
    "scripts/migrate_scenario_naming.py",
    "scripts/check_scenario_naming.py",
    "scripts/check_dungeon_finalize.py",
}
FORBIDDEN_IDENTIFIERS = {
    "DungeonRepo", "dungeon_repo", "_dungeon_repo", "dungeon_id", "dungeon_ids",
    "dungeon_config", "dungeon_key", "dungeon_editor", "ScriptManager", "script_mgr",
}
SKIP_DIRS = {"__pycache__", ".git", ".idea", ".workbuddy", "data", "developer_tools",
             "docs", "build", "assets", ".venv"}

_report_path = os.path.join(tempfile.mkdtemp(prefix="scenario_naming_"), "report.txt")
_log = open(_report_path, "w", encoding="utf-8")
sys.stdout = _log
sys.stderr = _log

failures = []
total = 0


def check(name, ok, extra=""):
    global total
    total += 1
    print(("  OK   " if ok else "  FAIL ") + name + (f"  <{extra}>" if extra and not ok else ""))
    if not ok:
        failures.append(name)


def scan_forbidden():
    """AST 扫描：返回 {相对路径: [(行号, 描述), ...]}。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    hits = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for filename in filenames:
            if not filename.endswith(".py") or filename.startswith("_rename"):
                continue
            path = os.path.join(dirpath, filename)
            rel = os.path.relpath(path, root).replace("\\", "/")
            if rel in ALLOWED_FILES:
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=rel)
            except (OSError, SyntaxError) as e:
                hits[rel] = [(0, f"无法解析: {e}")]
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id in FORBIDDEN_IDENTIFIERS:
                    hits.setdefault(rel, []).append((node.lineno, node.id))
                elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_IDENTIFIERS:
                    hits.setdefault(rel, []).append((node.lineno, f".{node.attr}"))
                elif isinstance(node, ast.arg) and node.arg in FORBIDDEN_IDENTIFIERS:
                    hits.setdefault(rel, []).append((node.lineno, f"参数 {node.arg}"))
                elif isinstance(node, ast.Constant) and node.value == "dungeons":
                    hits.setdefault(rel, []).append((node.lineno, '字面量 "dungeons"'))
    return hits


# ---------------- 1. 静态守卫 ----------------
hits = scan_forbidden()
check("旧标识符/资源键已清零", not hits,
      "; ".join(f"{k}:{v}" for k, v in list(hits.items())[:5]))

# ---------------- 2. terms 兼容读 ----------------
from dungeon.terms import (  # noqa: E402
    DEFAULT_SCENARIO_ID, LEGACY_SCENARIO_ID_KEY, SCENARIO_ID_KEY,
    is_default_scenario, scenario_config_of, scenario_id_of)

check("scenario_id_of 读新键", scenario_id_of({SCENARIO_ID_KEY: "a"}) == "a")
check("scenario_id_of 兼容旧键", scenario_id_of({LEGACY_SCENARIO_ID_KEY: "b"}) == "b")
check("scenario_id_of 新键优先",
      scenario_id_of({SCENARIO_ID_KEY: "a", LEGACY_SCENARIO_ID_KEY: "b"}) == "a")
check("scenario_id_of 空记录", scenario_id_of({}) == "" and scenario_id_of(None) == "")
check("scenario_config_of 兼容旧键",
      scenario_config_of({"dungeon_config": {"x": 1}}) == {"x": 1})
check("is_default_scenario",
      is_default_scenario("_default") and not is_default_scenario("other")
      and not is_default_scenario(None))

# ---------------- 3. ScenarioRepo：旧目录自愈迁移 + 读写根 ----------------
from persistence.scenario_repo import ScenarioRepo  # noqa: E402


def _missing(manifest, members):
    from persistence.world_pack import _missing_members
    return _missing_members(manifest, members)


tmp = tempfile.mkdtemp(prefix="scenario_naming_")
legacy_root = os.path.join(tmp, "packs", "dungeons")
for scenario_id, prompt in (("_default", "legacy"), ("alpha", "alpha")):
    os.makedirs(os.path.join(legacy_root, scenario_id), exist_ok=True)
    with open(os.path.join(legacy_root, scenario_id, "config.json"), "w",
              encoding="utf-8") as f:
        json.dump({"initial_prompt": prompt}, f, ensure_ascii=False)

repo = ScenarioRepo(data_dir=tmp)
new_root = os.path.join(tmp, "packs", "scenarios")
check("旧方案目录已自动迁移", os.path.isdir(new_root) and not os.path.isdir(legacy_root))
check("迁移后可读旧配置", repo.load_config("_default")["initial_prompt"] == "legacy")
check("list_all 覆盖全部方案", repo.list_all() == ["_default", "alpha"], repo.list_all())

check("新建方案写入写根",
      repo.create("beta") and os.path.isdir(os.path.join(new_root, "beta")))
check("exists 覆盖两个根", repo.exists("beta") and repo.exists("_default"))
repo.delete("beta")
check("delete 作用于写根", not repo.exists("beta"))
try:
    repo.delete("_default")
    check("默认方案禁止删除", False)
except ValueError:
    check("默认方案禁止删除", True)


# 读写根分离：世界包接管时读取走包目录、写入仍走自由根
class FakeWorldState:
    def __init__(self, pack_root):
        self._pack_root = pack_root

    def owns(self, resource_type):
        return resource_type == "scenarios"

    def pack_path(self, resource_type):
        return self._pack_root


pack_root = os.path.join(tmp, "worlds", "w1", "packs", "scenarios")
os.makedirs(os.path.join(pack_root, "gamma"), exist_ok=True)
with open(os.path.join(pack_root, "gamma", "config.json"), "w", encoding="utf-8") as f:
    json.dump({"initial_prompt": "gamma"}, f, ensure_ascii=False)
repo = ScenarioRepo(data_dir=tmp, world_state=FakeWorldState(pack_root))
check("世界包接管时读取根指向包目录",
      repo.read_root == pack_root and repo.read_root != repo.write_root)
check("世界包方案可读", repo.load_config("gamma")["initial_prompt"] == "gamma")
check("自由方案在世界包接管时仍可见", repo.exists("alpha"))
check("新方案仍写入自由根",
      repo.create("delta") and os.path.isdir(os.path.join(repo.write_root, "delta")))
check("list_all 汇总两个根",
      all(name in repo.list_all() for name in ("_default", "alpha", "gamma", "delta")),
      repo.list_all())

# ---------------- 4. 世界包清单：旧资源键归一化 ----------------
from persistence.world_pack import WorldPackManifest  # noqa: E402

legacy_manifest = WorldPackManifest.from_dict({
    "world_id": "w1", "name": "测试世界",
    "resources": {"dungeons": ["gamma"], "landmarks": ["L1"]},
})
check("旧资源键归一化为 scenarios",
      legacy_manifest.owns("scenarios") and not legacy_manifest.owns("dungeons"))
check("归一化后校验通过", legacy_manifest.validate() == [])
check("归一化清单用新路径核对成员",
      not _missing(legacy_manifest, {"world.json", "scenarios/gamma/config.json",
                                     "landmarks/L1.json"}))
check("旧包内 dungeons/ 成员路径同样通过核对",
      not _missing(legacy_manifest, {"world.json", "dungeons/gamma/config.json",
                                     "landmarks/L1.json"}))
check("缺成员仍会被发现",
      _missing(legacy_manifest, {"world.json", "landmarks/L1.json"}) ==
      ["scenarios/gamma/config.json"])

# ---------------- 5. 分层守卫（S4）：dungeon/ 根目录必须是纯领域层 ----------------

FORBIDDEN_IMPORT_ROOTS = ("dearpygui", "services", "ui")


def scan_layering():
    """dungeon/ 根目录（不含 window/）不得导入 UI 框架、服务层或 ui 包。

    S4 之前 ``rules.py`` 在函数内 ``import services.state_service``（领域层反向依赖
    服务层），``background/dispatcher/launcher/components`` 四个 DPG 模块混在
    根目录里。现在：根目录 = 纯领域，``dungeon/window/`` = DPG 会话层。
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    domain_dir = os.path.join(root, "dungeon")
    hits = {}
    for name in sorted(os.listdir(domain_dir)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(domain_dir, name)
        rel = os.path.relpath(path, root).replace("\\", "/")
        with open(path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                # from .x import ... 是包内导入（domain 层内部），不算越界
                if node.level:
                    continue
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                if module and module.split(".")[0] in FORBIDDEN_IMPORT_ROOTS:
                    hits.setdefault(rel, []).append((node.lineno, module))
    return hits


layer_hits = scan_layering()
check("dungeon/ 根目录无 UI/服务层依赖", not layer_hits,
      "; ".join(f"{k}:{v}" for k, v in list(layer_hits.items())[:5]))

# ---------------- 结论 ----------------
_log.flush()
sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__
if failures:
    print(f"[check_scenario_naming] FAILED {len(failures)}/{total}: {failures}")
else:
    print(f"[check_scenario_naming] PASSED {total}/{total}")
print(f"[check_scenario_naming] report: {_report_path}")
sys.exit(1 if failures else 0)

