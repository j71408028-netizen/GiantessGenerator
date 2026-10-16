"""自检：方案关系图模型（`dungeon/scenario_graph.py`）与图校验（`dungeon/graph_lint.py`）。

用法：``python tests/check_scenario_graph.py``

无 GUI、零外部依赖（这正是本模块的卖点，所以连"不许引回 networkx / graphviz"
也一并守）。覆盖：

1. 建图：三类节点、六类边、泳道顺序（章节 → 章节不存在的占位泳道 → 任意/无章节）；
   配置里类型不对（chapters 不是数组…）时不崩；
2. 两个可达性口径：章节可达**忽略条件与前置**（乐观），可触发集合**含**前置的 AND
   语义——这正是 error 级不误报的前提；
3. 强连通分量与「种子」判定：互锁环、自环都激活不了；链式前置能激活；
4. 五条图校验规则各命中一次，且**只在应该报的地方报**：
   - 骨架方案（章节齐、一条跳转都没接）不刷可达性诊断——这条是被真实门禁
     （``check_scenario_schema.py`` 的干净配置）逼出来的，别再退回去；
   - 走不到任何结束章节时，结束章节不再逐条报 R1（同一原因不刷两遍）；
   - 同位置动作竞争**不进校验器**，只由 ``same_position_groups()`` 分组呈现；
5. 正常方案图校验零诊断；
6. 接入一致：图校验的三元组经 ``validate_scenario_config`` 变成同一套 ``Diagnostic``，
   ``error`` 级被 ``has_errors`` 认到（保存 / 启动前的拦截因此自动生效）。
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_report_path = os.path.join(tempfile.mkdtemp(prefix="scenario_graph_"), "report.txt")
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


from dungeon import schema  # noqa: E402
from dungeon.graph_lint import lint_graph  # noqa: E402
from dungeon.scenario_graph import (  # noqa: E402
    DRAWABLE_EDGE_KINDS, EDGE_CHOICE, EDGE_ENTRY, EDGE_GOTO, EDGE_OVERFLOW, EDGE_PRECOND,
    EDGE_SCOPE, FLAG_ENDING, FLAG_MISSING, FLAG_START, NODE_CHAPTER, NODE_ROOT,
    NODE_TRIGGER, ROOT_ID, build_graph, chapter_node_id, trigger_node_id,
)
from dungeon.validate import (  # noqa: E402
    format_diagnostics, has_errors, validate_scenario_config,
)


def _rules(found, level=None, needle=None):
    """筛诊断：按级别与文案子串。"""
    picked = found
    if level:
        picked = [item for item in picked if item[0] == level]
    if needle:
        picked = [item for item in picked if needle in item[2]]
    return picked


def _paths(found):
    return [item[1] for item in found]


def _names(members):
    return [member.name for member in members]


# ---------------- 1. 建图 ----------------

NORMAL = {
    "chapters": [
        {"name": "序章", "start": True, "max_paragraphs": 6},
        {"name": "中庭", "overflow_target": ""},
        {"name": "终局", "ending": True},
    ],
    "triggers": [
        {"name": "开场", "chapter": "序章", "action": "insert",
         "action_data": {"text": "开篇"}},
        {"name": "入场", "chapter": "序章", "action": "goto",
         "action_data": {"chapter": "中庭"}, "precondition_names": ["开场"],
         "condition": {"operator": "and",
                       "rules": [{"key": "步数", "comparator": ">=", "value": 3}]}},
        {"name": "收尾", "chapter": "中庭", "action": "goto",
         "action_data": {"chapter": "终局"}},
        {"name": "旁白", "chapter": "", "action": "fx", "action_data": {"fx": "flash"}},
        {"name": "按旁白结果分支", "chapter": "中庭", "action": "none",
         "condition": {"operator": "and",
                       "rules": [{"key": "选择:旁白", "comparator": ">=", "value": 1}]}},
    ],
}

graph = build_graph(NORMAL)
check("节点种类齐全：起点 / 章节 / 触发器",
      graph.node(ROOT_ID).kind == NODE_ROOT
      and graph.node(chapter_node_id("序章")).kind == NODE_CHAPTER
      and graph.node(trigger_node_id("开场")).kind == NODE_TRIGGER)
check("章节标记：起始 / 结束",
      graph.node(chapter_node_id("序章")).has(FLAG_START)
      and graph.node(chapter_node_id("终局")).has(FLAG_ENDING))
check("节点携带配置下标（path 定位用）",
      graph.node(trigger_node_id("收尾")).index == 2
      and graph.node(chapter_node_id("中庭")).index == 1,
      str(graph.node(trigger_node_id("收尾")).index))

_edge_kinds = {edge.kind for edge in graph.edges}
check("六类边都建了出来",
      {EDGE_ENTRY, EDGE_SCOPE, EDGE_PRECOND, EDGE_GOTO, EDGE_OVERFLOW, EDGE_CHOICE}
      <= _edge_kinds,
      str(sorted(_edge_kinds)))
check("归属边从所在章节指向触发器",
      any(edge.kind == EDGE_SCOPE and edge.src == chapter_node_id("序章")
          and edge.dst == trigger_node_id("开场") for edge in graph.edges))
check("松散触发器（任意章节）归属到起点泳道",
      any(edge.kind == EDGE_SCOPE and edge.src == ROOT_ID
          and edge.dst == trigger_node_id("旁白") for edge in graph.edges))
check("归属边不参与绘制（由泳道表达）", EDGE_SCOPE not in DRAWABLE_EDGE_KINDS)
check("进入边从起点指向起始章节",
      any(edge.kind == EDGE_ENTRY and edge.src == ROOT_ID
          and edge.dst == chapter_node_id("序章") for edge in graph.edges))
check("超限跳转保留目标章节",
      any(edge.kind == EDGE_OVERFLOW and edge.src == chapter_node_id("中庭")
          and edge.dst == ROOT_ID for edge in graph.edges),
      "空 overflow_target = 离开章节，应指向起点")

check("泳道顺序：章节按配置顺序，任意/无章节排最后",
      [label for label, _ids in graph.lanes]
      == ["序章", "中庭", "终局", "任意章节 / （无章节）"],
      str([label for label, _ids in graph.lanes]))
check("泳道头是章节节点、其后是本章触发器",
      graph.lanes[0][1] == (chapter_node_id("序章"), trigger_node_id("开场"),
                            trigger_node_id("入场")),
      str(graph.lanes[0][1]))

# 宽容性：类型不对也不能崩（与 validate 同口径）
for junk in ({}, {"chapters": "x", "triggers": 3}, {"chapters": [None], "triggers": ["x"]}):
    try:
        build_graph(junk)
        ok = True
    except Exception as exc:      # noqa: BLE001
        ok = False
        print(f"    build_graph 崩溃: {junk!r} -> {exc}")
    check(f"类型不符的配置不崩：{junk!r}", ok)

# 章节被删除后仍有触发器 / 跳转指向它 → 占位泳道看得见
STALE = {
    "chapters": [{"name": "序章", "start": True}],
    "triggers": [
        {"name": "孤儿", "chapter": "已删章节", "action": "none"},
        {"name": "悬空跳", "chapter": "序章", "action": "goto",
         "action_data": {"chapter": "也不存在"}},
    ],
}
stale = build_graph(STALE)
check("指向不存在章节的占位泳道带「章节不存在」后缀",
      any(label.endswith("（章节不存在）") for label, _ids in stale.lanes),
      str([label for label, _ids in stale.lanes]))
check("占位章节节点带 missing 标记，且不算真实章节",
      any(node.has(FLAG_MISSING) for node in stale.nodes.values())
      and {c.name for c in stale.chapters()} == {"序章"},
      str(sorted(c.name for c in stale.chapters())))
check("占位章节不参与「章节走不到」的报错",
      not _rules(lint_graph(stale), "error", "章节不存在"),
      str(lint_graph(stale)))

# ---------------- 2. 两个可达性口径 ----------------

_conditional = build_graph({
    "chapters": [{"name": "甲", "start": True}, {"name": "乙"}],
    "triggers": [
        {"name": "有条件的跳转", "chapter": "甲", "action": "goto",
         "action_data": {"chapter": "乙"},
         "condition": {"operator": "and",
                       "rules": [{"key": "介入度", "comparator": ">=", "value": 99}]}},
    ],
})
check("章节可达忽略条件（乐观口径：error 级不因条件写错而误报）",
      "乙" in _conditional.reachable_chapter_names(),
      str(sorted(_conditional.reachable_chapter_names())))
check("章节层投影把 goto 折算成「触发器所在章节 → 目标章节」",
      any(edge.kind == EDGE_GOTO and edge.src == chapter_node_id("甲")
          and edge.dst == chapter_node_id("乙")
          for edge in _conditional.chapter_view_edges()))

# ---------------- 3. 强连通分量与种子 ----------------

CHAIN = {
    "chapters": [{"name": "序章", "start": True}],
    "triggers": [
        {"name": "甲", "chapter": "序章", "action": "none"},
        {"name": "乙", "chapter": "序章", "action": "none", "precondition_names": ["甲"]},
        {"name": "丙", "chapter": "序章", "action": "none", "precondition_names": ["乙"]},
    ],
}
chain = build_graph(CHAIN)
check("链式前置：三个都能触发",
      chain.firable_triggers()
      == frozenset(trigger_node_id(name) for name in ("甲", "乙", "丙")),
      str(sorted(chain.firable_triggers())))
check("链式前置没有循环依赖", chain.deadlock_sccs() == ())

LOCK = {
    "chapters": [{"name": "序章", "start": True}],
    "triggers": [
        {"name": "互锁甲", "chapter": "序章", "action": "none",
         "precondition_names": ["互锁乙"]},
        {"name": "互锁乙", "chapter": "序章", "action": "none",
         "precondition_names": ["互锁甲"]},
        {"name": "依赖互锁", "chapter": "序章", "action": "none",
         "precondition_names": ["互锁甲"]},
    ],
}
lock = build_graph(LOCK)
check("互锁分量被识别为强连通分量",
      (trigger_node_id("互锁甲"), trigger_node_id("互锁乙")) in lock.sccs()
      or (trigger_node_id("互锁乙"), trigger_node_id("互锁甲")) in lock.sccs(),
      str(lock.sccs()))
check("互锁分量没有种子 → 全部不可触发",
      lock.firable_triggers() == frozenset(),
      str(sorted(lock.firable_triggers())))
check("依赖互锁的第三方也不可触发",
      trigger_node_id("依赖互锁") not in lock.firable_triggers())

SELF = build_graph({
    "chapters": [{"name": "序章", "start": True}],
    "triggers": [{"name": "自环", "chapter": "序章", "action": "none",
                  "precondition_names": ["自环"]}],
})
check("自环（自己以自己为前置）算互锁",
      SELF.deadlock_sccs() == ((trigger_node_id("自环"),),)
      and SELF.firable_triggers() == frozenset(),
      str(SELF.deadlock_sccs()))

# ---------------- 4. 五条规则 ----------------

check("正常方案：图校验零诊断", lint_graph(graph) == [], str(lint_graph(graph)))

# 骨架方案：章节齐了但一条跳转都没接 —— 不许刷可达性诊断。
# 用 empty_scenario_config() 起底：字段级规则（耦合等级 / 演化属性 / 初始提示）都齐，
# 这样这条只验「图校验不给骨架方案刷可达性诊断」，不被无关的字段级警告带偏。
SKELETON = schema.empty_scenario_config()
SKELETON["chapters"] = [{"name": "开场", "start": True},
                        {"name": "终幕", "ending": True}]
check("骨架方案（没有任何章节跳转）不报可达性诊断",
      lint_graph(build_graph(SKELETON)) == [],
      str(lint_graph(build_graph(SKELETON))))
_skeleton_diags = validate_scenario_config(SKELETON)
check("骨架方案经 validate_scenario_config 不报 error / warning",
      not has_errors(_skeleton_diags)
      and not [d for d in _skeleton_diags if d.level == "warning"],
      format_diagnostics(_skeleton_diags, include_info=True))
check("骨架方案的 info 仍在（结束章节无图标这类提示不受影响）",
      any(d.level == "info" for d in _skeleton_diags),
      format_diagnostics(_skeleton_diags, include_info=True))

# R1 章节走不到（先接一条跳转，方案才算真的会在章节间移动）
R1 = {
    "chapters": [{"name": "序章", "start": True}, {"name": "中庭"}, {"name": "荒废"}],
    "triggers": [
        {"name": "去中庭", "chapter": "序章", "action": "goto",
         "action_data": {"chapter": "中庭"}},
    ],
}
r1 = lint_graph(build_graph(R1))
check("R1 不可达章节：error 级、定位到 chapters[i].name",
      _paths(_rules(r1, "error")) == ["chapters[2].name"]
      and "荒废" in _rules(r1, "error")[0][2],
      str(r1))

# R1 抑制一：没有起始章节时不报（顶层已有 error，逐章刷是噪音）
no_start = lint_graph(build_graph({
    "chapters": [{"name": "甲"}, {"name": "乙"}],
    "triggers": [{"name": "跳", "chapter": "甲", "action": "goto",
                  "action_data": {"chapter": "乙"}}],
}))
check("R1 抑制：没有起始章节时不逐章报不可达",
      not _rules(no_start, "error"), str(no_start))

# R2 走不到任何结束章节（此时结束章节不再逐条报 R1）
r2 = lint_graph(build_graph({
    "chapters": [{"name": "甲", "start": True}, {"name": "乙"},
                 {"name": "终局", "ending": True}],
    "triggers": [{"name": "绕开终局", "chapter": "甲", "action": "goto",
                  "action_data": {"chapter": "乙"}}],
}))
check("R2 结束章节一个都走不到：warning、定位到 chapters",
      _paths(_rules(r2, "warning", "走不到任何结束章节")) == ["chapters"],
      str(r2))
check("R2 生效时结束章节不再逐条报 R1（同一原因不刷两遍）",
      not _rules(r2, "error"), str(r2))

# 有可达的结束章节时，多余的结束章节照旧按 R1 报
r1b = lint_graph(build_graph({
    "chapters": [{"name": "甲", "start": True}, {"name": "乙", "ending": True},
                 {"name": "丙", "ending": True}],
    "triggers": [{"name": "去乙", "chapter": "甲", "action": "goto",
                  "action_data": {"chapter": "乙"}}],
}))
check("有可达结束章节时，多余的结束章节仍按 R1 报 error",
      _paths(_rules(r1b, "error")) == ["chapters[2].name"]
      and not _rules(r1b, "warning", "走不到任何结束章节"),
      str(r1b))

# R3 前置永远无法成立（前置自己陷在互锁里）
r3 = lint_graph(lock)
check("R3 前置永远无法成立：定位到 precondition_names、只报一次",
      _paths(_rules(r3, "warning", "永远无法触发")) == ["triggers[2].precondition_names"],
      str(r3))
check("R3 不给互锁分量成员重复报（由 R5 统一报）",
      all(trigger_node_id("互锁甲") not in str(item) for item in r3),
      str(r3))

# R4 自跳转
r4 = lint_graph(build_graph({
    "chapters": [{"name": "序章", "start": True}],
    "triggers": [
        {"name": "原地跳", "chapter": "序章", "action": "goto",
         "action_data": {"chapter": "序章"}},
        {"name": "离场", "chapter": "", "action": "goto", "action_data": {"chapter": ""}},
    ],
}))
check("R4 自跳转：warning、定位到 action_data.chapter",
      _paths(_rules(r4, "warning", "自身所在的章节")) == ["triggers[0].action_data.chapter"],
      str(r4))
check("R4 不把「离开章节」当自跳转（目标为空串）",
      not any("离场" in item[2] for item in r4), str(r4))

# R5 循环依赖
r5 = lint_graph(SELF)
check("R5 循环依赖：warning、文案点明「自己以自己为前置」",
      _paths(_rules(r5, "warning", "循环依赖")) == ["triggers[0].precondition_names"]
      and "把自己写成了自己的前置" in _rules(r5, "warning", "循环依赖")[0][2],
      str(r5))

# ---------------- 4b. 同位置竞争：只分组，不进校验器 ----------------

RACE = {
    "chapters": [{"name": "序章", "start": True}, {"name": "别处"}],
    "triggers": [
        {"name": "抢跳甲", "chapter": "序章", "action": "goto",
         "action_data": {"chapter": "别处"}},
        {"name": "抢跳乙", "chapter": "序章", "action": "goto",
         "action_data": {"chapter": "别处"}},
        {"name": "单独选项", "chapter": "序章", "action": "option",
         "action_data": {"options": [{"text": "a"}]}},
    ],
}
race = build_graph(RACE)
race_groups = race.same_position_groups()
check("同位置竞争不产生任何诊断（判不准的不报红）",
      lint_graph(race) == [], str(lint_graph(race)))
check("同位置同作用域才成组，单条不成组",
      [group.label for group in race_groups] == ["跳转章节"],
      str([(group.label, _names(group.members)) for group in race_groups]))
check("分组按配置顺序列出成员、作用域是泳道头",
      _names(race_groups[0].members) == ["抢跳甲", "抢跳乙"]
      and race_groups[0].scope == "序章",
      str((_names(race_groups[0].members), race_groups[0].scope)))
check("恒真且靠后的那条被标为永远轮不到",
      _names(race_groups[0].shadowed()) == ["抢跳乙"],
      str(_names(race_groups[0].shadowed())))

# 前一条带条件 → 后一条恒真也**不算**永远轮不到（免误报）
late = build_graph({
    "chapters": [{"name": "序章", "start": True}],
    "triggers": [
        {"name": "带条件", "chapter": "序章", "action": "option",
         "action_data": {"options": [{"text": "a"}]},
         "condition": {"operator": "and",
                       "rules": [{"key": "步数", "comparator": ">=", "value": 1}]}},
        {"name": "恒真", "chapter": "序章", "action": "option",
         "action_data": {"options": [{"text": "b"}]}},
    ],
})
late_group = late.same_position_groups()[0]
check("前一条带条件时不判「永远轮不到」",
      late_group.shadowed() == () and late_group.is_always_true(),
      str(_names(late_group.shadowed())))
check("两位动作（选项 / 小游戏）算同一个位次，标签点明两者",
      late_group.label == "弹出选项、小游戏挑战", late_group.label)

# 不同作用域（章节）各自成组，不互相干扰
scoped = build_graph({
    "chapters": [{"name": "甲", "start": True}, {"name": "乙"}],
    "triggers": [
        {"name": "甲跳", "chapter": "甲", "action": "goto",
         "action_data": {"chapter": "乙"}},
        {"name": "乙跳", "chapter": "乙", "action": "goto",
         "action_data": {"chapter": "甲"}},
    ],
})
check("同位置但不同章节不算竞争（各自成组，组内只有一条即不成组）",
      scoped.same_position_groups() == (),
      str([(g.scope, _names(g.members)) for g in scoped.same_position_groups()]))

# ---------------- 5. 与既有诊断体系一致 ----------------

merged = validate_scenario_config(R1)
check("图校验结果并入了 validate_scenario_config 的诊断列表",
      any(d.level == "error" and d.path == "chapters[2].name" for d in merged),
      str([str(d) for d in merged]))
check("图校验的 error 被 has_errors 认到（保存 / 启动前的拦截自动生效）",
      has_errors(merged))
check("图校验三元组与 Diagnostic 的 path 体系一致（可直接定位）",
      all(isinstance(item[0], str) and isinstance(item[1], str) and item[2]
          and item[1].startswith(("chapters", "triggers"))
          for item in lint_graph(build_graph(R1)) + lint_graph(build_graph(RACE))))

# ---------------- 6. 零外部依赖 ----------------

check("不引回 networkx（强连通分量是自实现的）", "networkx" not in sys.modules)
check("不引回 graphviz（不再出 PNG）", "graphviz" not in sys.modules)

print(f"[check_scenario_graph] {'PASSED' if not failures else 'FAILED'} "
      f"{total - len(failures)}/{total}")
print(f"[check_scenario_graph] report: {_report_path}")
sys.exit(1 if failures else 0)
