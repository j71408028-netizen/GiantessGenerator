"""演化模型二阶退化模拟脚本。

对**角点 + 默认性格表**两套性格预设持续模拟各类行为（报告步进、地标切换、副本演化、
闲置衰减、离线恢复、负向演化），逐步追踪介入度/破坏性坐标与二阶量（介入度/破坏性步长、
策略值），检测演化模型是否在特定情形下退化：

- 步长发散 / 步长坍缩（二阶量失稳）
- 坐标长期贴边（0.5 / 4.5）
- 步长符号高频振荡（极限环）
- 坐标静止（有步进但坐标不变）
- NaN / Inf

预设来源两套，用 ``--group`` 选择：

- **角点**（``--group corners``）：UI 可设定范围内的极值组合（步长/敏感/重力 ±3、
  个性强度 0 或 5、初始坐标贴边），每条压测单一机制；
- **默认性格表**（``--group table``）：直接读 ``data/static/personalities/<表>.csv``
  （默认 ``default``），逐条模拟游戏里真实存在的性格。

用 ``--pack`` / ``--world`` 可把**行为包**加载进进程内行为运行时后再模拟，用来直接查看
行为包对演化的覆盖效果（不必开局、不必先建世界包）：

- ``--pack <名>``：静态开发副本 ``data/static/behaviors/<名>``；
- ``--pack <目录/文件>``：任意行为包目录（或单个 ``.py``）——包还在别处开发时用；
- ``--world <id>``：已部署世界包 ``data/worlds/<id>`` 声明的行为包（与游戏同一加载路径）。

能力边界（报告里也会写明，别把它当成 bug）：副本演化主体公式
``dungeon/rules.py::EvolutionRules.evolve_attributes`` **没有**行为钩子，副本路径只通过
注入的 ``StateService.decay_step_rates`` 受行为包影响；报告路径用到的
``StateService`` 坐标/步长方法则全部可被覆盖。

用法：
    python developer_tools/evolution_dump.py                      # 角点+默认性格表，两种消耗习惯，400 步
    python developer_tools/evolution_dump.py --group corners       # 只跑角点
    python developer_tools/evolution_dump.py --group table --table default
    python developer_tools/evolution_dump.py --only corner_all_pos,温柔
    python developer_tools/evolution_dump.py --steps 2000 --seed 42
    python developer_tools/evolution_dump.py --pack my_pack        # 带静态行为包模拟
    python developer_tools/evolution_dump.py --world my_world      # 带世界包行为包模拟
    python developer_tools/evolution_dump.py --list-packs          # 看有哪些包 / 表

输出：坐标跟踪图（PNG）+ 退化检测报告（report.md），写入 ``--out`` 目录
（默认 ``developer_tools/_out/evolution/``；带 ``--pack`` / ``--world`` 时再嵌一层
``pack_<名>/``，便于与基线目录并排对比）。该目录不入库。

依赖：matplotlib（本工具私有，故意不在 requirements.txt）——缺了会提示安装命令。
"""

import argparse
import os
import random
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Windows 控制台 / 重定向管道的默认编码与中文多行输出不一致，显式改 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):  # pragma: no cover - 老解释器 / 特殊管道
    pass

# matplotlib 是本工具的私有依赖（只用来出 PNG），**故意不进 requirements.txt**：
# 运行时不需要它。缺了就直说怎么办，别抛一长串 ImportError 让人猜。
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError as exc:  # pragma: no cover - 环境缺失路径
    raise SystemExit(
        f"evolution_dump.py 需要 matplotlib 才能出图（不在 requirements.txt 里）：\n"
        f"    pip install matplotlib\n"
        f"（原始错误：{exc}）")

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False

from core.models import CharacterSnapshot, Personality       # noqa: E402
# _load_module / _python_files 是行为运行时的私有加载原语：借它们把「静态包 / 世界包」
# 之外的任意目录也接进来，行为（含 [BehaviorPack] 诊断）与游戏完全一致，不另写一套。
from core.behavior_runtime import (                           # noqa: E402
    _load_module, _python_files, get_runtime, resolve_behavior_source)
from paths import behaviors_dir, data_dir, worlds_dir         # noqa: E402
from persistence.personality_repo import (                   # noqa: E402
    DEFAULT_PERSONALITY_TABLE, PersonalityRepo)
from persistence.world_pack import (                          # noqa: E402
    WORLD_MANIFEST_NAME, WorldPackManifest, installed_dir,
    is_behavior_pack_name, load_manifest_file)
from services.state_service import StateService               # noqa: E402
from dungeon.models import DungeonState, DungeonTextType      # noqa: E402
from dungeon.rules import EvolutionRules                      # noqa: E402

# ==================== 性格预设 ====================
# ``Personality`` 的参数列（预设 dict 里除 key/name/source/... 之外，正好是这 7 个）。
PERSONALITY_FIELDS = ("init_intrusion", "init_destruction",
                      "step_intrusion", "step_destruction",
                      "sensitivity", "gravity", "skip_base_prob")

SOURCE_CORNER = "角点"
SOURCE_TABLE = "默认性格表"

# UI 可设定范围的角点（ui/exploration/creation_params_dlg.py + docs/character_evolution.md §1）：
# 步长 / 敏感 / 重力 ∈ [-3, 3]，个性强度 ∈ [0, 5]，初始坐标 ∈ [0.4, 4.5]。
# 每条只压一个机制，避免多个极值互相掩盖；``note`` 写清它压的是什么。
CORNER_PRESETS = [
    dict(key="corner_all_pos", name="全正角点", note="上界全正 + 满个性强度（恢复最快、衰减为 0）",
         init_intrusion=2.5, init_destruction=2.5,
         step_intrusion=3.0, step_destruction=3.0, sensitivity=3.0, gravity=3.0,
         skip_base_prob=5.0),
    dict(key="corner_all_neg", name="全负角点", note="下界全负 + 近零个性强度（衰减最强）",
         init_intrusion=3.5, init_destruction=3.5,
         step_intrusion=-3.0, step_destruction=-3.0, sensitivity=-3.0, gravity=-3.0,
         skip_base_prob=0.5),
    dict(key="corner_zero_step", name="零步长角点", note="二阶量从 0 起步（敏感/重力照常踢），坐标静止检测的对照",
         init_intrusion=2.5, init_destruction=2.5,
         step_intrusion=0.0, step_destruction=0.0, sensitivity=3.0, gravity=3.0,
         skip_base_prob=3.0),
    dict(key="corner_max_strength", name="满个性强度", note="衰减率 1-1=0：贴边也不衰减",
         init_intrusion=2.5, init_destruction=2.5,
         step_intrusion=1.5, step_destruction=1.5, sensitivity=3.0, gravity=-3.0,
         skip_base_prob=5.0),
    dict(key="corner_min_strength", name="零个性强度", note="衰减率最强：贴边步长几何收敛",
         init_intrusion=2.5, init_destruction=2.5,
         step_intrusion=3.0, step_destruction=3.0, sensitivity=-3.0, gravity=-3.0,
         skip_base_prob=0.0),
    dict(key="corner_opposite_sg", name="敏感重力反号", note="介入度与破坏性反向漂移",
         init_intrusion=2.5, init_destruction=2.5,
         step_intrusion=3.0, step_destruction=3.0, sensitivity=3.0, gravity=-3.0,
         skip_base_prob=5.0),
    dict(key="corner_edge_init", name="初始坐标贴边", note="开局即贴 4.5 / 0.5，立刻进入衰减路径",
         init_intrusion=4.5, init_destruction=0.5,
         step_intrusion=3.0, step_destruction=-3.0, sensitivity=3.0, gravity=-3.0,
         skip_base_prob=5.0),
]

# 行为阶段（权重为每步抽样概率；offline 为周期性整块插入）
PHASE_REPORT = "报告步进"
PHASE_DUNGEON = "副本演化"
PHASE_IDLE = "闲置衰减"
PHASE_OFFLINE = "离线恢复"
PHASE_WEIGHTS = {PHASE_REPORT: 0.45, PHASE_DUNGEON: 0.30, PHASE_IDLE: 0.25}
PHASE_COLORS = {PHASE_REPORT: "#d9e8fb", PHASE_DUNGEON: "#fde2e2",
                PHASE_IDLE: "#e2f7e2", PHASE_OFFLINE: "#fff3cc"}

CUSTOM_ATTR = {"name": "亲密", "rate": 1.0, "random_offset": 0.2}
OFFLINE_INTERVAL = 240   # 每隔多少步进入一次离线块
OFFLINE_BLOCK = 12       # 离线块持续步数（模拟步）

# 行动点消耗习惯：ap_short = 长期点数不足（负向演化常态化）；
# ap_moderate = 点数适中（每步回复，偶发低谷，负向演化仅偶发触发）
AP_PROFILES = {
    "ap_short": dict(label="长期点数不足", report_cost=(15, 30), dungeon_cost=25,
                     per_step_recovery=0),
    "ap_moderate": dict(label="点数适中", report_cost=(10, 20), dungeon_cost=25,
                        per_step_recovery=18),
}

BOUND_LO, BOUND_HI = 0.5, 4.5


def make_personality(preset: dict) -> Personality:
    """由预设 dict 构造 Personality（只取 7 个参数列，忽略 key/note/source 等元数据）。"""
    return Personality(name=preset["name"],
                       description=preset.get("description", ""),
                       **{k: preset[k] for k in PERSONALITY_FIELDS})


def make_snapshot(preset: dict) -> CharacterSnapshot:
    state = CharacterSnapshot(giantess_id=preset["key"], name=preset["name"],
                              personality=make_personality(preset))
    state.action_points = 100   # 满行动点开局，否则第一步就触发负向演化
    state.record_change(intrusion=preset["init_intrusion"],
                        destruction=preset["init_destruction"], source="init")
    return state


# ==================== 预设集合：角点 + 默认性格表 ====================

def build_corner_presets() -> list:
    """返回 UI 范围角点预设（拷贝一份，避免调用方就地改写模块常量）。"""
    return [dict(p, source=SOURCE_CORNER) for p in CORNER_PRESETS]


def build_table_presets(table: str) -> list:
    """从默认性格表（data/static/personalities/<table>.csv）逐条构造预设。

    读不到表时返回空列表——由调用方决定是报错还是跳过（--group table 显式要求时
    会在 select_presets 里报错并列出可用表名）。
    """
    repo = PersonalityRepo(table=table)
    presets = []
    for item in repo.load(table):
        preset = dict(key=item.name, name=item.name, source=SOURCE_TABLE,
                      note=f"{table}.csv", description=item.description)
        preset.update({k: getattr(item, k) for k in PERSONALITY_FIELDS})
        presets.append(preset)
    return presets


def _dedupe_keys(presets: list) -> list:
    """同名性格（表里重名）时把 key 去重，保证文件名与 --only 匹配都唯一。"""
    used = {}
    for preset in presets:
        base = preset["key"]
        used[base] = used.get(base, 0) + 1
        if used[base] > 1:
            preset["key"] = f"{base}_{used[base]}"
    return presets


def available_personality_tables() -> list:
    return PersonalityRepo(table=DEFAULT_PERSONALITY_TABLE).get_tables()


def select_presets(group: str, table: str, only: str) -> list:
    """按来源（角点 / 性格表 / 两者）与 --only 挑出本次要跑的预设。"""
    presets = []
    if group in ("corners", "all"):
        presets += build_corner_presets()
    if group in ("table", "all"):
        table_presets = build_table_presets(table)
        if not table_presets:
            message = (f"性格表 '{table}' 读不到或为空"
                       f"（目录 {os.path.dirname(PersonalityRepo(table=table).resolve(table))}），"
                       f"可用表：{available_personality_tables() or '（无）'}")
            if group == "table":
                raise SystemExit(message)
            print(f"⚠️  {message}\n    本次只跑角点。")
        presets += table_presets
    presets = _dedupe_keys(presets)

    if only:
        wanted = [k.strip() for k in only.split(",") if k.strip()]
        by_id = {}
        for preset in presets:
            by_id[preset["key"]] = preset
            by_id.setdefault(preset["name"], preset)
        missing = [k for k in wanted if k not in by_id]
        if missing:
            raise SystemExit(
                f"--only 无法匹配：{', '.join(missing)}\n"
                f"    可选：{', '.join(p['key'] for p in presets)}")
        picked, seen = [], set()
        for key in wanted:
            preset = by_id[key]
            if preset["key"] not in seen:
                seen.add(preset["key"])
                picked.append(preset)
        presets = picked

    if not presets:
        raise SystemExit("没有可模拟的预设（--group / --only 组合过窄）")
    return presets


# ==================== 行为包（把覆盖实现加载进进程内运行时） ====================

# 本模拟**实际调用**的可覆盖目标：只有这些被覆盖才会体现在结果里。
SIMULATED_HOOKS = (
    ("StateService.advance_coordinates", "报告步进：坐标推进"),
    ("StateService.evolve_step_rates", "报告 / 负向：步长演化"),
    ("StateService.decay_step_rates", "不适应性衰减（也注入副本 EvolutionRules）"),
    ("StateService.apply_landmark_switch", "地标切换"),
    ("StateService.decayed_coordinates", "闲置 / 离线衰减"),
    ("StateService.apply_step_decay", "面板逗留衰减"),
    ("StateService.apply_negative_evolution", "负向演化"),
    ("StateService.clamp_coordinates", "坐标夹取"),
    ("StateService.shift_coordinates", "坐标平移"),
    ("StateService.consume_action_points", "行动点消耗"),
    ("StateService.receive_action_points", "行动点自然回复"),
    ("StateService.recover_action_points", "面板行动点回复"),
)

# 同为可覆盖目标、但**本模拟不调用**：覆盖后不会体现在结果里（报告里点明，免得误判）。
NOT_SIMULATED_HOOKS = (
    "StateService.refund_action_points（返回副本结算才用）",
    "StateService.recover_evolution（真实离线结算走 services.character.offline）",
    "CreationService.*（身高生成，与演化无关）",
    "logic.*（尺寸格式化 / 描述 / 屏蔽词等显示层）",
)

EVOLUTION_UNCALLABLE = (
    "副本演化主体公式 dungeon/rules.py::EvolutionRules.evolve_attributes 未提供行为钩子；"
    "副本路径只通过注入的 StateService.decay_step_rates 受行为包影响"
)


@dataclass
class BehaviorPackInfo:
    """已加载行为包的标签与本模拟关心的钩子覆盖情况。"""

    label: str
    tag: str                     # 输出子目录名（pack_<名>）
    coverage: dict = field(default_factory=dict)

    @property
    def affected(self) -> list:
        return [key for key, _ in SIMULATED_HOOKS if self.coverage.get(key)]

    @property
    def untouched(self) -> list:
        return [key for key, _ in SIMULATED_HOOKS if not self.coverage.get(key)]


def _pack_tag(name: str) -> str:
    return "pack_" + re.sub(r"[^A-Za-z0-9_.-]", "_", name)


def list_static_packs() -> list:
    from persistence.world_pack import list_behavior_packs
    return list_behavior_packs()


def list_installed_worlds() -> list:
    """列出已部署且带 world.json 的世界包 id。"""
    root = worlds_dir()
    if not os.path.isdir(root):
        return []
    return sorted(name for name in os.listdir(root)
                  if os.path.isfile(os.path.join(root, name, WORLD_MANIFEST_NAME)))


def _load_pack_source(source: str, runtime) -> None:
    """加载任意目录（或单个 .py）里的行为模块并调用 register(runtime)。

    刻意复用行为运行时的私有加载原语，保证与 ``load_pack`` 同一套约定与
    ``[BehaviorPack]`` 诊断，不让两套加载逻辑漂移。
    """
    paths = _python_files(source) if os.path.isdir(source) else [source]
    for path in paths:
        name = os.path.splitext(os.path.basename(path))[0]
        module = _load_module(f"_evo_behavior_{name}", path)
        if module is None:
            continue
        register = getattr(module, "register", None)
        if not callable(register):
            print(f"[BehaviorPack] 行为模块 '{path}' 未定义 register(runtime) 入口")
            continue
        try:
            register(runtime)
        except Exception as e:
            print(f"[BehaviorPack] 行为模块 '{path}' 注册失败: {e}")


def activate_behavior_pack(pack_name: str, world_id: str) -> BehaviorPackInfo:
    """把行为包加载进进程内行为运行时，返回覆盖情况。

    - ``pack_name``：静态开发副本 ``data/static/behaviors/<名>``，或任意存在的
      目录 / 单个 ``.py``（走本地加载）；同名时静态目录优先；
    - ``world_id``：已部署世界包 ``data/worlds/<id>``，取其清单声明的行为包
      （与 ``WorldManager.load_active`` 同一条 ``load_pack`` 路径）。
    """
    runtime = get_runtime()
    runtime.reset()

    if world_id:
        installed = installed_dir(data_dir(), world_id)
        manifest_path = os.path.join(installed, WORLD_MANIFEST_NAME)
        if not os.path.isfile(manifest_path):
            raise SystemExit(
                f"未找到世界包 '{world_id}'（缺 {manifest_path}）\n"
                f"    已部署的世界包：{list_installed_worlds() or '（无）'}")
        manifest = load_manifest_file(manifest_path)
        if not manifest.owns("behaviors"):
            raise SystemExit(f"世界包 '{world_id}' 未声明行为包（resources.behaviors 为空）")
        runtime.load_pack(manifest, installed)
        declared = manifest.resources["behaviors"][0]
        label = f"世界包 {world_id} → 行为包 {declared}"
        tag = _pack_tag(f"{world_id}_{declared}")
    else:
        root = behaviors_dir()
        direct = Path(pack_name)
        # 先用「静态包名」解析，再退回路径：只看 pack_name 是否是合法包名，
        # 避免 Windows 上 os.path.join(root, "C:/abs/path") 直接吞掉 root 造成误判。
        source = (resolve_behavior_source(root, pack_name)
                  if is_behavior_pack_name(pack_name) else None)
        if source is not None:
            # 与 WorldManager 同一条加载路径：
            # manifest.resources['behaviors'] + <installed>/behaviors/<名>
            manifest = WorldPackManifest(world_id="evolution_dump",
                                         name=f"evolution_dump_{pack_name}",
                                         resources={"behaviors": [pack_name]})
            runtime.load_pack(manifest, os.path.dirname(root))
            label = f"静态行为包 {pack_name}（{root}）"
            tag = _pack_tag(pack_name)
        elif direct.exists():
            _load_pack_source(str(direct), runtime)
            label = f"行为包目录 {direct}"
            tag = _pack_tag(direct.stem if direct.is_file() else direct.name)
        else:
            raise SystemExit(
                f"未找到行为包 '{pack_name}'：既不是 {root} 下的包名，也不是存在的目录 / 文件\n"
                f"    可用静态包：{list_static_packs() or '（无）'}")

    coverage = {key: runtime.resolve(key) is not None for key, _ in SIMULATED_HOOKS}
    return BehaviorPackInfo(label=label, tag=tag, coverage=coverage)


def pack_report_lines(pack: Optional[BehaviorPackInfo]) -> list:
    """报告里的「行为包」小节（未加载时为说明性文字）。"""
    if pack is None:
        return [
            "## 行为包",
            "",
            "- 本次**未加载**行为包，模拟的是核心演化模型（默认实现）。",
            "- 加 `--pack <名>` / `--world <id>` 可加载行为包后模拟：结果写入"
            "`_out/evolution/pack_<名>/`，与基线目录并排对比。",
            "",
        ]
    lines = ["## 行为包", "", f"- 已加载：{pack.label}"]
    affected = pack.affected
    if affected:
        lines.append(f"- 覆盖了 {len(affected)}/{len(SIMULATED_HOOKS)} 个本模拟调用的演化钩子：")
        for key in affected:
            lines.append(f"  - `{key}`")
    else:
        lines.append(f"- ⚠️ **未覆盖**本模拟调用的 {len(SIMULATED_HOOKS)} 个演化钩子中的任何一个，"
                     "结果应与基线一致（该包大概只改了显示层 / 身高）。")
    if pack.untouched:
        lines.append(f"- 仍走默认实现（{len(pack.untouched)} 个）："
                     + "、".join(f"`{k}`" for k in pack.untouched))
    lines.append(f"- 本模拟不调用、覆盖后不体现在结果里的目标：{('；'.join(NOT_SIMULATED_HOOKS))}")
    lines.append(f"- 不可覆盖：{EVOLUTION_UNCALLABLE}")
    lines.append("")
    return lines


def print_pack_summary(pack: BehaviorPackInfo) -> None:
    print(f"\n[行为包] {pack.label}")
    covered = pack.affected
    if covered:
        print(f"    覆盖了 {len(covered)}/{len(SIMULATED_HOOKS)} 个本模拟调用的演化钩子："
              + "、".join(covered))
    else:
        print(f"    ⚠️ 未覆盖任何本模拟调用的演化钩子（{len(SIMULATED_HOOKS)} 个），"
              f"结果应与基线一致")


def print_available() -> None:
    print("静态行为包（data/static/behaviors/）：")
    for name in list_static_packs():
        print(f"    - {name}")
    if not list_static_packs():
        print("    （无）")
    print("已部署世界包（data/worlds/，含 world.json）：")
    for name in list_installed_worlds():
        print(f"    - {name}")
    if not list_installed_worlds():
        print("    （无）")
    print("性格表（data/static/personalities/）：")
    for name in available_personality_tables():
        print(f"    - {name}")
    if not available_personality_tables():
        print("    （无）")


# ==================== 行为模拟 ====================

def do_report_step(state, rng, profile):
    """报告步进：坐标推进 → 步长扣除并恢复，并消耗行动点。"""
    p = state.personality
    step = rng.choice((1.0, 1.0, 1.0, 2.0, 2.0, 3.0, -1.0))
    intrusion, destruction = StateService.advance_coordinates(
        p, state.intrusion, state.destruction, step,
        state.current_step_intrusion, state.current_step_destruction)
    si, sd = StateService.evolve_step_rates(
        p, state.current_step_intrusion, state.current_step_destruction, step,
        intrusion=intrusion, destruction=destruction)
    state.step_intrusion, state.step_destruction = si, sd
    state.record_change(step=step, intrusion=intrusion, destruction=destruction,
                        source="report")
    lo, hi = profile["report_cost"]
    StateService.consume_action_points(state, rng.randint(lo, hi))


def do_landmark_switch(state, rng):
    """地标切换（含首次匹配）：unique/common 随机。"""
    p = state.personality
    intrusion, destruction = StateService.apply_landmark_switch(
        p, state.intrusion, state.destruction,
        "unique" if rng.random() < 0.5 else "common")
    if (intrusion, destruction) != (state.intrusion, state.destruction):
        state.record_change(intrusion=intrusion, destruction=destruction,
                            source="landmark_switch")


def do_dungeon_delve(state, rules, rng, profile):
    """副本深入：每次进入都新建 DungeonState（自定义属性不保存不跟踪，
    每次开副本重新计算），逐段演化，每段把坐标与步长写回角色。

    注意：``EvolutionRules.evolve_attributes`` 不是行为钩子，副本路径里只有注入的
    ``StateService.decay_step_rates`` 会被行为包替换（见报告「行为包」小节）。
    """
    if not StateService.consume_action_points(state, profile["dungeon_cost"]):
        return 0
    dungeon_state = DungeonState(intrusion=state.intrusion,
                                 destruction=state.destruction)
    length = rng.randint(5, 12)
    for _ in range(length):
        text_type = rng.choice(list(DungeonTextType))
        direction = rng.choice((1, 1, 1, -1))
        dungeon_state = rules.evolve_attributes(
            dungeon_state, text_type, direction, state.personality,
            custom_attrs_def=[CUSTOM_ATTR],
            custom_directions={},
            action_points=state.action_points)
        state.step_intrusion = dungeon_state.step_intrusion
        state.step_destruction = dungeon_state.step_destruction
        state.record_change(step=text_type.step_value,
                            intrusion=dungeon_state.intrusion,
                            destruction=dungeon_state.destruction,
                            source="dungeon")
    return length


def do_offline_block(state, rng):
    """离线块：行动点完全恢复，坐标按较大 fraction 回落。"""
    StateService.receive_action_points(state, 100)
    intrusion, destruction = StateService.decayed_coordinates(
        state.personality, state.intrusion, state.destruction,
        rng.uniform(2.0, 5.0))
    if (intrusion, destruction) != (state.intrusion, state.destruction):
        state.record_change(intrusion=intrusion, destruction=destruction,
                            source="offline")


def simulate(preset: dict, steps: int, seed: int, profile: dict,
             events: Optional[list] = None):
    """返回 history；每步一条 dict。提供 events 列表时逐事件记录
    演化表新增行（坐标、增量、来源），供短步数轨迹检查使用。"""
    rng = random.Random(seed)
    random.seed(seed)   # 副本规则内部（自定义属性噪声等）使用全局 random
    state = make_snapshot(preset)
    rules = EvolutionRules(step_decay=StateService.decay_step_rates)
    history = []
    phase = PHASE_REPORT
    for i in range(steps):
        prev_len = len(state.evolution)
        prev_row = state.evolution[-1]
        # 周期性离线块覆盖当步行为
        if i > 0 and i % OFFLINE_INTERVAL < OFFLINE_BLOCK:
            phase = PHASE_OFFLINE
            do_offline_block(state, rng)
        else:
            phase = rng.choices(list(PHASE_WEIGHTS), weights=PHASE_WEIGHTS.values())[0]
            if phase == PHASE_REPORT:
                do_report_step(state, rng, profile)
                if rng.random() < 0.15:
                    do_landmark_switch(state, rng)
            elif phase == PHASE_DUNGEON:
                do_dungeon_delve(state, rules, rng, profile)
            else:
                StateService.recover_action_points(state)
                StateService.apply_step_decay(state, 0.1)
            # 点数适中习惯：每步固定回复（ap_short 无此项）
            if profile["per_step_recovery"]:
                StateService.receive_action_points(state, profile["per_step_recovery"])
        # 负向演化每步自检（AP<50 时触发，内部自带守卫）
        StateService.apply_negative_evolution(state)
        neg_evolved = (len(state.evolution) > prev_len
                       and state.evolution[-1].source == "apply_negative_evolution")
        if events is not None:
            for row in state.evolution[prev_len:]:
                events.append({
                    "step_index": i,
                    "phase": phase,
                    "source": row.source,
                    "step_weight": row.step,
                    "intrusion": row.intrusion,
                    "destruction": row.destruction,
                    "d_intrusion": row.intrusion - prev_row.intrusion,
                    "d_destruction": row.destruction - prev_row.destruction,
                    "step_intrusion": state.current_step_intrusion,
                    "step_destruction": state.current_step_destruction,
                    "action_points": state.action_points,
                })
                prev_row = row
        history.append({
            "step": i,
            "phase": phase,
            "intrusion": state.intrusion,
            "destruction": state.destruction,
            "step_intrusion": state.current_step_intrusion,
            "step_destruction": state.current_step_destruction,
            "action_points": state.action_points,
            "strategy": state.personality.strategy_value(state.action_points),
            "neg_evolved": neg_evolved,
        })
    return history


# ==================== 退化检测 ====================

def _onset(flags, min_run=50):
    """返回首次连续满足 flags 达 min_run 步的起始下标；无则 None。"""
    run = 0
    for i, ok in enumerate(flags):
        run = run + 1 if ok else 0
        if run >= min_run:
            return i - min_run + 1
    return None


def _describe(history, idx):
    h = history[idx]
    return f"step {h['step']}（{h['phase']}）"


def detect_degeneracy(history) -> list:
    """返回问题描述列表；标注退化首次发生的位置与阶段。"""
    issues = []
    tail_len = min(200, len(history))

    si = [h["step_intrusion"] for h in history]
    sd = [h["step_destruction"] for h in history]
    intr = [h["intrusion"] for h in history]
    destr = [h["destruction"] for h in history]

    def bad(v):
        return v != v or abs(v) == float("inf")

    bads = [h["step"] for h in history
            if any(bad(h[k]) for k in ("intrusion", "destruction",
                                       "step_intrusion", "step_destruction"))]
    if bads:
        issues.append(f"出现 NaN/Inf（首次 step {bads[0]}）")

    for label, series, limit in (("介入度步长", si, 50.0), ("破坏性步长", sd, 50.0)):
        peak = max(abs(v) for v in series)
        if peak > limit:
            idx = next(i for i, v in enumerate(series) if abs(v) > limit)
            issues.append(f"{label}发散：|{label}| 峰值 {peak:.1f} > {limit}，"
                          f"始于 {_describe(history, idx)}")

    # 步长坍缩：连续 50 步步长接近 0（性格步长非零时才是异常）
    flags = [abs(a) < 1e-6 and abs(b) < 1e-6
             for a, b in zip(si, sd)]
    idx = _onset(flags)
    if idx is not None:
        issues.append(f"步长坍缩：步长持续为 0，始于 {_describe(history, idx)}")

    # 坐标贴边：连续 50 步贴在同一边界
    for label, series in (("介入度", intr), ("破坏性", destr)):
        for edge in (BOUND_LO, BOUND_HI):
            idx = _onset([abs(v - edge) < 1e-6 for v in series])
            if idx is not None:
                ratio = sum(1 for v in series[-tail_len:]
                            if abs(v - edge) < 1e-6) / tail_len
                issues.append(f"{label}长期贴边 {edge}（末段占比 {ratio:.0%}，"
                              f"始于 {_describe(history, idx)}）")
                break

    # 步长符号振荡：末段步长符号翻转次数
    for label, series in (("介入度步长", si[-tail_len:]), ("破坏性步长", sd[-tail_len:])):
        flips = sum(1 for a, b in zip(series, series[1:])
                    if a * b < 0 and abs(a) > 1e-6 and abs(b) > 1e-6)
        if flips > 60:
            issues.append(f"{label}高频振荡：末段符号翻转 {flips} 次")

    # 坐标静止：连续 50 步坐标完全不变但步长非零
    static = [i > 0 and abs(intr[i] - intr[i - 1]) < 1e-12
              and abs(destr[i] - destr[i - 1]) < 1e-12
              for i in range(len(history))]
    idx = _onset(static)
    if idx is not None:
        alive = max(abs(v) for v in si[idx:idx + 50] + sd[idx:idx + 50])
        if alive > 1e-6:
            issues.append(f"坐标静止：坐标不变但步长非零（|步长|≈{alive:.1f}），"
                          f"始于 {_describe(history, idx)}")
    return issues


# ==================== 绘图 ====================

def _preset_ident(preset: dict) -> str:
    """图表 / 打印用的预设标识：key 与 name 相同时不重复。"""
    if preset["key"] == preset["name"]:
        return preset["name"]
    return f"{preset['name']}·{preset['key']}"


def _source_tag(preset: dict) -> str:
    return preset.get("source", "")


def _shade_phases(ax, history):
    start = 0
    for i in range(1, len(history) + 1):
        if i == len(history) or history[i]["phase"] != history[start]["phase"]:
            ax.axvspan(history[start]["step"], history[i - 1]["step"],
                       color=PHASE_COLORS.get(history[start]["phase"], "#ffffff"),
                       alpha=0.6, lw=0)
            start = i


def warn_stale_outputs(out_dir: Path, presets: list, trajectory: bool) -> None:
    """提示输出目录里不属于本次预设的历史产物（换过预设集合时会留下）。

    只提示、不删除：`_out/` 是产物目录，但删文件一律交给用户，工具不越界。
    调用方在 `--only` 子集运行时应跳过（那会把本次没跑的预设全报成"历史"）。
    """
    if not out_dir.is_dir():
        return
    prefix = "traj_" if trajectory else "track_"
    expected = {f"{prefix}{p['key']}.png" for p in presets} | {"track_overview.png"}
    stale = sorted(name for name in os.listdir(out_dir)
                   if name.startswith(prefix) and name.endswith(".png")
                   and name not in expected)
    if stale:
        print(f"    提示：{out_dir} 里有 {len(stale)} 张不属于本次预设的历史图片"
              f"（预设集合改过？）——工具不自动删除，可自行清理：")
        print("        " + "、".join(stale))


def _savefig(fig, path: Path) -> Path:
    """保存图片；目标被占用（如看图软件锁定）时退避到带序号的文件名。"""
    for candidate in [path] + [path.with_name(f"{path.stem}_{i}{path.suffix}")
                               for i in range(1, 4)]:
        try:
            fig.savefig(candidate, dpi=130)
            return candidate
        except OSError as e:
            print(f"    写入 {candidate.name} 失败（{e.strerror}），尝试备选文件名")
    raise OSError(f"无法写入图片：{path}")


def plot_character(preset, history, out_dir: Path, profile: dict,
                   pack: Optional[BehaviorPackInfo] = None) -> Path:
    steps = [h["step"] for h in history]
    fig, axes = plt.subplots(3, 1, figsize=(12, 9.5), sharex=True,
                             gridspec_kw={"height_ratios": [3, 2, 1.2]})
    ax1, ax2, ax3 = axes

    _shade_phases(ax1, history)
    ax1.plot(steps, [h["intrusion"] for h in history], color="#c0392b", lw=1.2,
             label="介入度")
    ax1.plot(steps, [h["destruction"] for h in history], color="#2471a3", lw=1.2,
             label="破坏性")
    for bound in (BOUND_LO, BOUND_HI):
        ax1.axhline(bound, color="gray", ls="--", lw=0.8)
    ax1.set_ylim(0, 5)
    ax1.set_ylabel("坐标")
    pack_note = f"｜行为包 {pack.tag}" if pack else ""
    ax1.set_title(f"{_preset_ident(preset)}（{_source_tag(preset)}｜{profile['label']}"
                  f"{pack_note}）演化轨迹  "
                  f"敏感={preset['sensitivity']} 重力={preset['gravity']} "
                  f"步长=({preset['step_intrusion']},{preset['step_destruction']}) "
                  f"个性强度={preset['skip_base_prob']}")
    ax1.legend(loc="upper right", fontsize=9, ncols=2)

    _shade_phases(ax2, history)
    ax2.plot(steps, [h["step_intrusion"] for h in history], color="#e67e22", lw=1.1,
             label="介入度步长（二阶）")
    ax2.plot(steps, [h["step_destruction"] for h in history], color="#8e44ad", lw=1.1,
             label="破坏性步长（二阶）")
    ax2.plot(steps, [h["strategy"] for h in history], color="#7f8c8d", lw=1.0,
             ls=":", label="策略值")
    ax2.axhline(0, color="gray", ls="--", lw=0.8)
    ax2.set_ylabel("步长 / 策略值")
    ax2.legend(loc="upper right", fontsize=9, ncols=3)

    _shade_phases(ax3, history)
    ax3.plot(steps, [h["action_points"] for h in history], color="#2c3e50", lw=1.1)
    ax3.axhline(50, color="#c0392b", ls="--", lw=0.8)
    ax3.text(steps[-1], 52, "负向演化阈值 50", ha="right", fontsize=8, color="#c0392b")
    ax3.set_ylabel("行动点")
    ax3.set_xlabel("模拟步")
    ax3.set_ylim(0, max(105, max(h["action_points"] for h in history) + 5))

    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in PHASE_COLORS.values()]
    ax3.legend(handles, list(PHASE_COLORS), loc="upper left", fontsize=8, ncols=4)
    fig.tight_layout()
    out = _savefig(fig, out_dir / f"track_{preset['key']}.png")
    plt.close(fig)
    return out


def plot_overview(results, out_dir: Path, profile: dict,
                  pack: Optional[BehaviorPackInfo] = None, ncols: int = 3) -> Path:
    """各性格坐标轨迹总览。预设变多后按网格排布，避免一张图拉到几十英寸高。"""
    n = len(results)
    ncols = max(1, min(ncols, n))
    nrows = (n + ncols - 1) // ncols
    fig, _ = plt.subplots(nrows, ncols, figsize=(5.0 * ncols, 2.1 * nrows),
                          sharex=True, squeeze=False)
    axes = fig.axes
    for ax, (preset, history, _) in zip(axes, results):
        steps = [h["step"] for h in history]
        ax.plot(steps, [h["intrusion"] for h in history], color="#c0392b", lw=1.0,
                label="介入度")
        ax.plot(steps, [h["destruction"] for h in history], color="#2471a3", lw=1.0,
                label="破坏性")
        ax.set_ylim(0, 5)
        ax.set_title(f"{_preset_ident(preset)}（{_source_tag(preset)}）", fontsize=9)
        for bound in (BOUND_LO, BOUND_HI):
            ax.axhline(bound, color="gray", ls="--", lw=0.6)
    for ax in axes[n:]:      # 末行多余的空位
        ax.axis("off")
    axes[0].legend(loc="upper right", fontsize=8, ncols=2)
    for ax in axes[n - ncols:n]:   # 最后一行的横轴标签
        ax.set_xlabel("模拟步")
    pack_note = f"｜行为包 {pack.tag}" if pack else ""
    fig.suptitle(f"各性格坐标轨迹总览（{profile['label']}{pack_note}，共 {n} 条）")
    fig.tight_layout()
    out = _savefig(fig, out_dir / "track_overview.png")
    plt.close(fig)
    return out


# ==================== 报告 ====================

def write_report(results, out_dir: Path, args, profile_key: str,
                 pack: Optional[BehaviorPackInfo] = None) -> Path:
    profile = AP_PROFILES[profile_key]
    by_source = {}
    for preset, _, _ in results:
        by_source[_source_tag(preset)] = by_source.get(_source_tag(preset), 0) + 1
    source_summary = "、".join(f"{k} {v} 条" for k, v in by_source.items())
    lines = ["# 演化模型二阶退化检测报告", "",
             f"- 消耗习惯：{profile['label']}（报告 {profile['report_cost']} 点/次，"
             f"副本 {profile['dungeon_cost']} 点/次，"
             f"每步回复 {profile['per_step_recovery']} 点）",
             f"- 预设构成：{source_summary}，共 {len(results)} 条",
             f"- 模拟步数：{args.steps}，随机种子：{args.seed}",
             f"- 离线块：每 {OFFLINE_INTERVAL} 步插入 {OFFLINE_BLOCK} 步",
             "- 行为构成：报告步进 45%（含 15% 概率地标切换）、副本演化 30%、闲置衰减 25%",
             ""]
    lines += pack_report_lines(pack)
    lines += [
        "| 角色 | 来源 | 末段介入度 | 末段破坏性 | 末段介入度步长 | 末段破坏性步长 | 峰值|步长| | 负向演化触发率 | 结论 |",
        "|---|---|---|---|---|---|---|---|---|"]
    for preset, history, issues in results:
        last = history[-1]
        peak = max(max(abs(h["step_intrusion"]) for h in history),
                   max(abs(h["step_destruction"]) for h in history))
        neg_rate = sum(1 for h in history if h["neg_evolved"]) / len(history)
        verdict = "✅ 未检出退化" if not issues else "⚠️ " + "；".join(issues)
        lines.append(
            f"| {_preset_ident(preset)} | {_source_tag(preset)} "
            f"| {last['intrusion']:.2f} | {last['destruction']:.2f} "
            f"| {last['step_intrusion']:.2f} | {last['step_destruction']:.2f} "
            f"| {peak:.1f} | {neg_rate:.0%} | {verdict} |")
    lines += ["",
              "> 判定口径：步长发散（|步长|>50）、步长坍缩（末段持续为 0）、",
              "> 坐标长期贴边（末段 60% 以上贴 0.5/4.5）、步长高频振荡（末段符号翻转>60 次）、",
              "> 坐标静止（步长非零但坐标不变）、NaN/Inf。末段 = 最后 200 步。",
              ">",
              "> 注意：某一路径的步长与对应性格值同时为 0 的性格（`疏离` 两步长全 0、",
              "> `适应` 破坏性步长 0、`游戏` 介入度步长 0）会命中「步长坍缩」/「长期贴边」——",
              "> 那是**性格数据本身**决定的静止，不是模型退化。"]
    out = out_dir / "report.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


SOURCE_COLORS = {
    "init": "#000000",
    "report": "#c0392b",
    "landmark_switch": "#e67e22",
    "dungeon": "#8e44ad",
    "apply_negative_evolution": "#7f8c8d",
    "offline": "#2980b9",
}
SOURCE_LABELS = {
    "init": "初始",
    "report": "报告步进",
    "landmark_switch": "地标切换",
    "dungeon": "副本演化",
    "apply_negative_evolution": "负向演化",
    "offline": "离线恢复",
}


def plot_trajectory(preset, events, out_dir: Path, profile: dict,
                    pack: Optional[BehaviorPackInfo] = None) -> Path:
    """短步数轨迹图：左=相平面（介入度×破坏性移动路径，按来源着色），
    右=每事件坐标增量。"""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.6),
                                   gridspec_kw={"width_ratios": [1, 1.3]})
    # 相平面路径
    ax1.plot([e["intrusion"] for e in events], [e["destruction"] for e in events],
             color="#bdc3c7", lw=0.8, zorder=1)
    for source in SOURCE_COLORS:
        pts = [(e["intrusion"], e["destruction"]) for e in events
               if e["source"] == source]
        if pts:
            ax1.scatter(*zip(*pts), s=22, color=SOURCE_COLORS[source],
                        label=SOURCE_LABELS.get(source, source), zorder=2,
                        alpha=0.85)
    first, last = events[0], events[-1]
    ax1.scatter([first["intrusion"]], [first["destruction"]], marker="*",
                s=220, color="#2c3e50", zorder=3, label="起点")
    ax1.scatter([last["intrusion"]], [last["destruction"]], marker="X",
                s=110, color="#16a085", zorder=3, label="终点")
    ax1.add_patch(plt.Rectangle((BOUND_LO, BOUND_LO), BOUND_HI - BOUND_LO,
                                BOUND_HI - BOUND_LO, fill=False,
                                ls="--", ec="gray", lw=0.9))
    ax1.set_xlim(0, 5)
    ax1.set_ylim(0, 5)
    ax1.set_xlabel("介入度")
    ax1.set_ylabel("破坏性")
    pack_note = f"｜行为包 {pack.tag}" if pack else ""
    ax1.set_title(f"{_preset_ident(preset)}（{profile['label']}{pack_note}）坐标移动路径，"
                  f"共 {len(events)} 个事件")
    ax1.legend(fontsize=8, loc="upper left")
    ax1.set_aspect("equal")

    # 每事件增量
    idx = range(1, len(events) + 1)
    ax2.bar(idx, [e["d_intrusion"] for e in events], width=0.75,
            color="#c0392b", alpha=0.75, label="Δ介入度")
    ax2.bar(idx, [e["d_destruction"] for e in events], width=0.4,
            color="#2471a3", alpha=0.75, label="Δ破坏性")
    ax2.axhline(0, color="gray", lw=0.8)
    ax2.set_xlabel("事件序号")
    ax2.set_ylabel("坐标增量")
    ax2.set_title("每事件坐标增量（宽=介入度，窄=破坏性）")
    ax2.legend(fontsize=9)
    fig.tight_layout()
    out = _savefig(fig, out_dir / f"traj_{preset['key']}.png")
    plt.close(fig)
    return out


def write_trajectory_report(results, out_dir: Path, args, profile_key: str,
                            pack: Optional[BehaviorPackInfo] = None) -> Path:
    profile = AP_PROFILES[profile_key]
    lines = ["# 短步数坐标移动轨迹报告", "",
             f"- 消耗习惯：{profile['label']}",
             f"- 预设：{'、'.join(_preset_ident(p) for p, _ in results)}",
             f"- 模拟步数：{args.trajectory}，随机种子：{args.seed}",
             f"- 行为包：{pack.label if pack else '（未加载，走核心默认实现）'}",
             "- 注意：副本演化事件的坐标处于副本自身 0~5 量程（虚线框为角色 0.5~4.5 边界），",
             "",
             "| 事件 | 角色 | 步 | 来源 | 步进权重 | 介入度 | 破坏性 | Δ介入度 | Δ破坏性 | 步长(i/s) | AP |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for preset, events in results:
        for n, e in enumerate(events, 1):
            lines.append(
                f"| {n} | {_preset_ident(preset)} | {e['step_index']} "
                f"| {SOURCE_LABELS.get(e['source'], e['source'])} "
                f"| {e['step_weight']:.2f} | {e['intrusion']:.2f} "
                f"| {e['destruction']:.2f} | {e['d_intrusion']:+.2f} "
                f"| {e['d_destruction']:+.2f} "
                f"| {e['step_intrusion']:.2f}/{e['step_destruction']:.2f} "
                f"| {e['action_points']} |")
    out = out_dir / "trajectory.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def main():
    parser = argparse.ArgumentParser(
        description="演化模型二阶退化模拟（角点 + 默认性格表；可加载行为包）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--steps", type=int, default=400)
    parser.add_argument("--seed", type=int, default=20260919)
    parser.add_argument("--out", type=Path, default=Path(__file__).parent /
                        "_out" / "evolution")
    parser.add_argument("--only", type=str, default="",
                        help="仅模拟指定预设（逗号分隔；角点用其 key，性格表用性格名）")
    parser.add_argument("--group", type=str, default="all",
                        choices=("all", "corners", "table"),
                        help="预设来源：角点 / 默认性格表 / 两者（默认）")
    parser.add_argument("--table", type=str, default=DEFAULT_PERSONALITY_TABLE,
                        help="性格表名（data/static/personalities/<名>.csv）")
    parser.add_argument("--profile", type=str, default="both",
                        choices=list(AP_PROFILES) + ["both"],
                        help="行动点消耗习惯（both=两种都跑并分目录输出）")
    parser.add_argument("--trajectory", type=int, default=0,
                        help="短步数轨迹模式：每角色模拟指定步数，逐事件输出"
                             "坐标移动路径图与逐步数据表（0=关闭）")
    pack_group = parser.add_mutually_exclusive_group()
    pack_group.add_argument("--pack", type=str, default="",
                            help="先加载行为包再模拟：给 data/static/behaviors/<名> 下的包名，"
                                 "或任意行为包目录 / 单个 .py 的路径")
    pack_group.add_argument("--world", type=str, default="",
                            help="先加载已部署世界包 data/worlds/<id> 声明的行为包再模拟")
    parser.add_argument("--list-packs", action="store_true",
                        help="列出可用的静态行为包 / 世界包 / 性格表后退出")
    args = parser.parse_args()

    if args.list_packs:
        print_available()
        return

    presets = select_presets(args.group, args.table, args.only)
    pack = None
    if args.pack or args.world:
        pack = activate_behavior_pack(args.pack, args.world)
        print_pack_summary(pack)

    profile_keys = (list(AP_PROFILES) if args.profile == "both"
                    else [args.profile])
    out_root = args.out / pack.tag if pack else args.out

    print(f"\n预设 {len(presets)} 条：" + "、".join(_preset_ident(p) for p in presets))

    for profile_key in profile_keys:
        profile = AP_PROFILES[profile_key]
        out_dir = out_root / profile_key
        out_dir.mkdir(parents=True, exist_ok=True)

        if args.trajectory:
            print(f"\n===== 短步数轨迹（{profile['label']}，{args.trajectory} 步，"
                  f"输出 {out_dir}）=====")
            results = []
            for preset in presets:
                events = []
                simulate(preset, args.trajectory, args.seed, profile, events)
                img = plot_trajectory(preset, events, out_dir, profile, pack)
                results.append((preset, events))
                print(f"[{_source_tag(preset)}｜{_preset_ident(preset)}] "
                      f"{len(events)} 个事件，图: {img}")
            report = write_trajectory_report(results, out_dir, args, profile_key, pack)
            print(f"\n逐步数据表: {report}")
            if not args.only:
                warn_stale_outputs(out_dir, presets, trajectory=True)
            continue

        print(f"\n===== 消耗习惯：{profile['label']}（输出 {out_dir}）=====")
        results = []
        for preset in presets:
            history = simulate(preset, args.steps, args.seed, profile)
            issues = detect_degeneracy(history)
            img = plot_character(preset, history, out_dir, profile, pack)
            results.append((preset, history, issues))
            status = "未检出退化" if not issues else "；".join(issues)
            print(f"[{_source_tag(preset)}｜{_preset_ident(preset)}] "
                  f"{'✅ ' + status if not issues else '⚠️ ' + status}")
            print(f"    图: {img}")

        overview = plot_overview(results, out_dir, profile, pack)
        report = write_report(results, out_dir, args, profile_key, pack)
        print(f"\n总览图: {overview}\n报告: {report}")
        if not args.only:
            warn_stale_outputs(out_dir, presets, trajectory=False)


if __name__ == "__main__":
    main()
