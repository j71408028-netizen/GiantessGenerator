"""方案关系图：章节与触发器之间的关系建成一张可分析的有向图。

为什么要单独建这张图
--------------------
分章节之前触发器是平铺的，互相之间只有 ``precondition_names`` 一种关系。分章节
之后，决定一次副本能不能走通的实际上有**四条**独立路径，任何一条断了都不会报错，
只会静默不生效（详见 ``docs/Dungeon/scenario_graph.md`` §1）：

- 前置条件：触发器的 ``precondition_names``；
- 章节跳转：``goto`` 动作的 ``action_data.chapter``；
- 超限跳转：章节的 ``overflow_target``；
- 条件引用：条件键 ``选择:<触发器名>``。

再加上**归属**（触发器靠 ``chapter`` 字段声明自己属于哪一章），结构才完整。本模块
把这三类节点（起点 / 章节 / 触发器）与六类边（进入 / 归属 / 前置 / 跳转 / 超限 /
引用）建成一张 ``ScenarioGraph``，供图校验（``dungeon/graph_lint.py``）与编辑器
关系图（``ui/scenario/graph_view.py``）共用同一份结论。

两个可达性口径，别混用
----------------------
图校验要报 ``error``，就必须**宁可漏报不可误报**，因此有两条口径：

- :meth:`ScenarioGraph.chapter_reachability`：只走 ``entry`` / ``scope`` / ``goto`` /
  ``overflow``，**忽略一切条件与前置**。这是乐观估计——结构上到得了就算到得了，
  不会因为条件写错而误报。``error`` 级的规则只用这一条。
- :meth:`ScenarioGraph.firable_triggers`：在上面基础上加入 ``precond`` 的 AND 语义
  （前置边先收缩成强连通分量，再迭代到不动点）。整环互锁、没有「种子」成员的分量
  永远激活不了——这正是「循环依赖」的判据。

``choice`` 边两条口径都**不参与**：被引用的触发器没触发只意味着计数为 0，
那是可以被比较的合法值，不是"到不了"。

零外部依赖
----------
强连通分量（Tarjan）与可达性都是自实现的，不需要 ``networkx``；出图也不需要
``graphviz``（编辑器改为自绘画布）。两者已从 ``requirements.txt`` 移除。
"""

from dataclasses import dataclass

from dungeon import schema
from dungeon.actions import ACTION_POSITIONS, action_label, normalize_action_type
from dungeon.audio.chapters import (
    CHAPTER_ANY, CHAPTER_ANY_LABEL, CHAPTER_NONE, normalize_chapters,
)

# ---------------- 节点 ----------------

NODE_ROOT = "root"
NODE_CHAPTER = "chapter"
NODE_TRIGGER = "trigger"

#: 起点伪节点：「（无章节）」——副本刚开始、还没进入任何章节的状态。
#: 入口是伪节点而不是起始章节，因为运行时 ``_enter_start_chapter`` 是在「当前没有
#: 章节」时才进入起始章节的；从伪节点出发算可达性，才能同时覆盖「没有标记起始章节」
#: 和「某章节永远进不去」两种情形。
ROOT_ID = "root"
ROOT_LABEL = "（无章节）"

# 节点标记
FLAG_START = "start"        # 章节：起始章节
FLAG_ENDING = "ending"      # 章节：结束章节
FLAG_FIXED = "fixed"        # 章节：固定文本章节
FLAG_MISSING = "missing"    # 章节：配置里不存在的章节（被引用的残留名）
FLAG_LOOSE = "loose"        # 触发器：作用域是「任意章节 / 无章节」
FLAG_DANGLING = "dangling"  # 触发器：动作指向了不存在的章节

# ---------------- 边 ----------------

EDGE_ENTRY = "entry"        # root → 起始章节
EDGE_SCOPE = "scope"        # 章节（或 root）→ 其下的触发器
EDGE_PRECOND = "precond"    # 前置触发器 → 触发器
EDGE_GOTO = "goto"          # 触发器 → 目标章节
EDGE_OVERFLOW = "overflow"  # 章节 → 目标章节（超限跳转）
EDGE_CHOICE = "choice"      # 被引用触发器 → 触发器（条件键「选择:X」）

EDGE_LABELS = {
    EDGE_ENTRY: "进入",
    EDGE_SCOPE: "归属",
    EDGE_PRECOND: "前置条件",
    EDGE_GOTO: "章节跳转",
    EDGE_OVERFLOW: "超限跳转",
    EDGE_CHOICE: "条件引用",
}

#: 章节层可达性只走这些边（忽略条件与前置，见模块说明）。
STRUCTURAL_EDGE_KINDS = (EDGE_ENTRY, EDGE_SCOPE, EDGE_GOTO, EDGE_OVERFLOW)

#: 界面上默认绘制的边（``scope`` 由泳道归属表达，不画箭头）。
DRAWABLE_EDGE_KINDS = (EDGE_ENTRY, EDGE_PRECOND, EDGE_GOTO, EDGE_OVERFLOW, EDGE_CHOICE)

#: 触发器所在章节的占位泳道后缀（章节名已不存在时）。
MISSING_LANE_SUFFIX = "（章节不存在）"

#: 动作类型 → 在 ``ACTION_POSITIONS`` 里的位次；同位置的触发器在一轮里争同一个名额。
ACTION_POSITION_OF = {}
#: 位次 → 展示名（把该位允许的动作类型名连起来，如「选项、小游戏」）。
ACTION_POSITION_LABELS = []
for _position, _group in enumerate(ACTION_POSITIONS):
    ACTION_POSITION_LABELS.append("、".join(action_label(_action) for _action in _group))
    for _action in _group:
        ACTION_POSITION_OF[_action] = _position


def chapter_node_id(name: str) -> str:
    return f"{NODE_CHAPTER}:{name}"


def trigger_node_id(name: str) -> str:
    return f"{NODE_TRIGGER}:{name}"


@dataclass(frozen=True)
class Node:
    """图里的一个节点（起点 / 章节 / 触发器）。"""

    id: str
    kind: str
    name: str
    chapter: str = ""        # 触发器：声明的所在章节；章节节点：自身名
    action_type: str = ""    # 触发器：归一化后的动作类型
    flags: tuple = ()        # FLAG_* 标记
    index: int = -1          # 在 config 列表里的下标（用于 path）；-1 = 无
    owner: str = ""          # 所属泳道的头节点（章节节点自身 / ROOT_ID）
    has_condition: bool = False   # 触发器：条件规则非空（空条件 = 恒真）

    def has(self, flag: str) -> bool:
        return flag in self.flags


@dataclass(frozen=True)
class Edge:
    """图里的一条边；``detail`` 是给界面用的副文本。"""

    kind: str
    src: str
    dst: str
    detail: str = ""


@dataclass(frozen=True)
class PositionGroup:
    """同 ``ACTION_POSITIONS`` 位、同作用域的一组触发器（一轮里至多一个能生效）。"""

    position: int
    label: str          # 该位的展示名（连接位内动作的展示名），如「跳转章节」
    scope: str          # 泳道头名字（章节名或「（无章节）」）
    members: tuple      # 组内触发器节点，按配置顺序

    def is_always_true(self) -> bool:
        """组内是否至少有一条条件恒真（哪条被挡住要看运行时顺序，不改判结论）。"""
        return any(not node.has_condition for node in self.members)

    def shadowed(self):
        """配置顺序靠后、且自己与前面某条一样恒真的成员——运行时永远轮不到它们。

        只报「两边都恒真」这一种无争议的情况，条件非空的可能竞争交给作者自己看
        （严格判断条件是否重叠需要求解器，见 ``docs/Dungeon/scenario_graph.md`` §3.2）。
        """
        seen_always_true = False
        losers = []
        for node in self.members:
            if node.has_condition:
                continue
            if seen_always_true:
                losers.append(node)
            seen_always_true = True
        return tuple(losers)


class ScenarioGraph:
    """不可变的方案关系图；``lanes`` 是泳道顺序（渲染与遍历共用一套顺序）。"""

    def __init__(self, nodes, edges, lanes):
        self.nodes = {node.id: node for node in nodes}
        self.edges = tuple(edges)
        self.lanes = tuple((label, tuple(ids)) for label, ids in lanes)
        self._out = {}
        self._in = {}
        for edge in self.edges:
            self._out.setdefault(edge.src, []).append(edge)
            self._in.setdefault(edge.dst, []).append(edge)

    # ---------- 查询 ----------

    def node(self, node_id):
        return self.nodes.get(node_id)

    def out_edges(self, node_id):
        return tuple(self._out.get(node_id, ()))

    def in_edges(self, node_id):
        return tuple(self._in.get(node_id, ()))

    def nodes_of_kind(self, kind):
        return tuple(n for n in self.nodes.values() if n.kind == kind)

    def chapters(self):
        """真实章节（不含 ``missing`` 占位节点）。"""
        return tuple(n for n in self.nodes.values()
                     if n.kind == NODE_CHAPTER and not n.has(FLAG_MISSING))

    def triggers(self):
        return tuple(n for n in self.nodes.values() if n.kind == NODE_TRIGGER)

    def trigger_by_name(self, name):
        return self.nodes.get(trigger_node_id(name))

    def trigger_node(self, name):
        return self.trigger_by_name(name)

    def chapter_node(self, name):
        return self.nodes.get(chapter_node_id(name))

    def edges_of(self, kinds):
        wanted = set(kinds)
        return tuple(e for e in self.edges if e.kind in wanted)

    def same_position_groups(self):
        """同 ``ACTION_POSITIONS`` 位、同作用域、且组内不止一条触发器的分组。

        运行时一轮从先到后逐位派发，**同位置至多生效一个**（按配置顺序取第一个满足
        条件且配置有效的）。这里只**分组**，不下「谁挡住谁」的结论——条件非空的
        可能竞争需要求解器，因此由关系图对话框按位呈现给作者看，不进校验器
        （见 ``docs/Dungeon/scenario_graph.md`` §3.2）。
        """
        buckets = {}
        for node in self.triggers():
            position = ACTION_POSITION_OF.get(node.action_type)
            if position is None:
                continue
            buckets.setdefault((position, node.owner), []).append(node)

        groups = []
        for (position, owner), members in buckets.items():
            if len(members) < 2:
                continue
            head = self.node(owner)
            groups.append(PositionGroup(
                position=position,
                label=ACTION_POSITION_LABELS[position],
                scope=head.name if head is not None else "",
                members=tuple(sorted(
                    members, key=lambda n: n.index if n.index >= 0 else 1 << 30)),
            ))
        groups.sort(key=lambda group: (group.position, group.scope))
        return tuple(groups)

    # ---------- 可达性 ----------

    def reachable(self, seeds, kinds=STRUCTURAL_EDGE_KINDS):
        """沿指定边种类做前向可达（含种子自身）。"""
        wanted = set(kinds)
        seen = set(seeds)
        stack = list(seeds)
        while stack:
            current = stack.pop()
            for edge in self._out.get(current, ()):
                if edge.kind not in wanted or edge.dst in seen:
                    continue
                seen.add(edge.dst)
                stack.append(edge.dst)
        return frozenset(seen)

    def chapter_reachability(self):
        """乐观口径：从「无章节」起点出发、忽略条件与前置的可达节点集合。"""
        return self.reachable((ROOT_ID,), STRUCTURAL_EDGE_KINDS)

    def reachable_chapter_names(self):
        return frozenset(self.nodes[nid].name for nid in self.chapter_reachability()
                         if self.nodes[nid].kind == NODE_CHAPTER)

    # ---------- 强连通分量（前置边） ----------

    def sccs(self, kinds=(EDGE_PRECOND,)):
        """触发器在指定边上的强连通分量（Tarjan，自实现）。"""
        wanted = set(kinds)
        index = {}
        low = {}
        on_stack = {}
        stack = []
        counter = 0
        result = []

        def strong(node_id):
            nonlocal counter
            index[node_id] = low[node_id] = counter
            counter += 1
            stack.append(node_id)
            on_stack[node_id] = True
            for edge in self._out.get(node_id, ()):
                if edge.kind not in wanted or edge.dst not in self.nodes:
                    continue
                target = edge.dst
                if target not in index:
                    strong(target)
                    low[node_id] = min(low[node_id], low[target])
                elif on_stack.get(target):
                    low[node_id] = min(low[node_id], index[target])
            if low[node_id] == index[node_id]:
                component = []
                while True:
                    popped = stack.pop()
                    on_stack[popped] = False
                    component.append(popped)
                    if popped == node_id:
                        break
                result.append(tuple(sorted(component)))

        for node in self.triggers():
            if node.id not in index:
                strong(node.id)
        return tuple(result)

    def deadlock_sccs(self):
        """互锁的强连通分量：**没有一个成员缺少分量内的前置**，永远激活不了。

        单点分量带自环（触发器把自己写成自己的前置）同样算互锁。
        """
        deadlocked = []
        for component in self.sccs():
            members = set(component)
            for member in component:
                # 分量内的前置看的是**入边**：有谁在本分量里等着它先触发
                intra = any(edge.kind == EDGE_PRECOND and edge.src in members
                            for edge in self._in.get(member, ()))
                if not intra:
                    break   # 有「种子」成员，整个分量能依次激活
            else:
                deadlocked.append(component)
        return tuple(deadlocked)

    def firable_triggers(self):
        """能触发的触发器节点 id（章节可达性 + 前置的 AND 语义）。

        口径见模块说明：作用域取乐观的章节可达集合，只把「前置永远凑不齐」的触发器
        排除在外（互锁分量、前置位于不可达章节）。
        """
        reached = self.chapter_reachability()
        components = self.sccs()
        comp_of = {}
        for position, component in enumerate(components):
            for node_id in component:
                comp_of[node_id] = position

        has_seed = [False] * len(components)
        external = [set() for _ in components]
        for edge in self.edges:
            if edge.kind != EDGE_PRECOND:
                continue
            source = comp_of.get(edge.src)
            target = comp_of.get(edge.dst)
            if source is None or target is None:
                continue
            if source == target:
                continue
            external[target].add(source)
        for position, component in enumerate(components):
            members = set(component)
            for member in component:
                # 分量内的前置看**入边**（同 deadlock_sccs 的口径）
                intra = any(edge.kind == EDGE_PRECOND and edge.src in members
                            for edge in self._in.get(member, ()))
                if not intra:
                    has_seed[position] = True
                    break

        # 作用域是否可达：分量内至少一个成员的泳道头在可达集合里
        scope_ok = []
        for component in components:
            scope_ok.append(any(
                (self.nodes[nid].owner == ROOT_ID) or (self.nodes[nid].owner in reached)
                for nid in component))

        active = [False] * len(components)
        changed = True
        while changed:
            changed = False
            for position in range(len(components)):
                if active[position] or not has_seed[position] or not scope_ok[position]:
                    continue
                if all(active[other] for other in external[position]):
                    active[position] = True
                    changed = True

        return frozenset(node_id
                         for position, component in enumerate(components) if active[position]
                         for node_id in component)

    # ---------- 投影 ----------

    def chapter_view_edges(self):
        """投影到章节层：``goto`` 折算成「触发器所在泳道头 → 目标章节」。

        「章节总览」视图用它画章与章之间的流程图。
        """
        projected = []
        for edge in self.edges:
            if edge.kind in (EDGE_ENTRY, EDGE_OVERFLOW):
                projected.append(edge)
            elif edge.kind == EDGE_GOTO:
                source = self.nodes.get(edge.src)
                head = source.owner if source is not None else ROOT_ID
                label = source.name if source is not None else ""
                projected.append(Edge(EDGE_GOTO, head, edge.dst, detail=label))
        return tuple(projected)


# ---------------- 建图 ----------------


def _condition_keys(condition):
    """递归产出条件里所有规则键（含嵌套条件组）。"""
    if not isinstance(condition, dict):
        return
    for rule in condition.get("rules") or []:
        if not isinstance(rule, dict):
            continue
        if isinstance(rule.get("rules"), list):
            yield from _condition_keys(rule)
            continue
        key = rule.get("key")
        if isinstance(key, str):
            yield key


def _raw_index(items, name):
    for position, item in enumerate(items or []):
        if isinstance(item, dict) and str(item.get("name") or "").strip() == name:
            return position
    return -1


def build_graph(config) -> ScenarioGraph:
    """把方案配置建成关系图；对缺失 / 类型不符的字段一律宽容（同 ``validate``）。"""
    config = config if isinstance(config, dict) else {}
    chapters_raw = config.get("chapters")
    triggers_raw = config.get("triggers")
    chapters_raw = chapters_raw if isinstance(chapters_raw, list) else []
    triggers_raw = triggers_raw if isinstance(triggers_raw, list) else []

    nodes = [Node(id=ROOT_ID, kind=NODE_ROOT, name=ROOT_LABEL,
                  flags=(FLAG_LOOSE,), owner=ROOT_ID)]
    edges = []
    known = {chapter["name"] for chapter in normalize_chapters(chapters_raw)}
    missing = {}          # 不存在的章节名 → 占位节点 id（惰性创建）
    missing_lanes = []    # 需要排进泳道的占位节点（按出现顺序）

    def ensure_missing(name):
        """为「配置里不存在的章节」建一个占位节点，让悬空引用在图上看得见。"""
        name = str(name or "").strip()
        if not name or name in known or name in missing:
            return chapter_node_id(name) if name else ROOT_ID
        node_id = chapter_node_id(name)
        missing[name] = node_id
        missing_lanes.append(node_id)
        nodes.append(Node(id=node_id, kind=NODE_CHAPTER, name=name, chapter=name,
                          flags=(FLAG_MISSING,), owner=node_id))
        return node_id

    # ---------- 章节 ----------
    chapter_ids = []
    for chapter in normalize_chapters(chapters_raw):
        name = chapter["name"]
        node_id = chapter_node_id(name)
        flags = []
        if chapter.get("start"):
            flags.append(FLAG_START)
        if chapter.get("ending"):
            flags.append(FLAG_ENDING)
        if (chapter.get("fixed_text") or {}).get("path"):
            flags.append(FLAG_FIXED)
        nodes.append(Node(id=node_id, kind=NODE_CHAPTER, name=name, chapter=name,
                          flags=tuple(flags), owner=node_id,
                          index=_raw_index(chapters_raw, name)))
        chapter_ids.append(node_id)
        if chapter.get("start"):
            edges.append(Edge(EDGE_ENTRY, ROOT_ID, node_id))
        if chapter.get("ending"):
            # 结束章节超限即终止，配置里的 overflow_target 运行时被忽略
            continue
        target = str(chapter.get("overflow_target") or "").strip()
        if target in known:
            edges.append(Edge(EDGE_OVERFLOW, node_id, chapter_node_id(target),
                              detail=target))
        elif target:
            dangling_id = ensure_missing(target)
            edges.append(Edge(EDGE_OVERFLOW, node_id, dangling_id, detail=target))
        else:
            # 空串 = 超限后离开章节，回到「无章节」状态
            edges.append(Edge(EDGE_OVERFLOW, node_id, ROOT_ID, detail="离开章节"))

    # ---------- 触发器 ----------
    trigger_names = set()
    for trigger in triggers_raw:
        if not isinstance(trigger, dict):
            continue
        name = str(trigger.get("name") or "").strip()
        if not name or name in trigger_names:
            # 没名字的画不出来；重名的按首个为准（重名本身由既有校验规则报出）
            continue
        trigger_names.add(name)
        scope = str(trigger.get("chapter") or "").strip()
        action_data = trigger.get("action_data")
        action_data = action_data if isinstance(action_data, dict) else {}
        action_type = normalize_action_type(trigger.get("action"), action_data)

        flags = []
        if scope in (CHAPTER_ANY, CHAPTER_NONE):
            owner = ROOT_ID
            flags.append(FLAG_LOOSE)
        elif scope in known:
            owner = chapter_node_id(scope)
        else:
            owner = ensure_missing(scope)
            flags.append(FLAG_DANGLING)

        node_id = trigger_node_id(name)
        if action_type == EDGE_GOTO:
            target = str(action_data.get("chapter") or "").strip()
            if target not in known and target:
                flags.append(FLAG_DANGLING)
        condition = trigger.get("condition")
        has_condition = bool(isinstance(condition, dict) and condition.get("rules"))
        nodes.append(Node(id=node_id, kind=NODE_TRIGGER, name=name, chapter=scope,
                          action_type=action_type, flags=tuple(flags), owner=owner,
                          index=_raw_index(triggers_raw, name),
                          has_condition=has_condition))
        edges.append(Edge(EDGE_SCOPE, owner, node_id))

    # ---------- 前置 / 跳转 / 条件引用 ----------
    for trigger in triggers_raw:
        if not isinstance(trigger, dict):
            continue
        name = str(trigger.get("name") or "").strip()
        if name not in trigger_names:
            continue
        node_id = trigger_node_id(name)
        for precondition in (trigger.get("precondition_names") or []):
            precondition = str(precondition or "").strip()
            if precondition in trigger_names:
                edges.append(Edge(EDGE_PRECOND, trigger_node_id(precondition), node_id,
                                  detail=precondition))
        action_data = trigger.get("action_data")
        action_data = action_data if isinstance(action_data, dict) else {}
        if normalize_action_type(trigger.get("action"), action_data) == EDGE_GOTO:
            target = str(action_data.get("chapter") or "").strip()
            if target in known:
                edges.append(Edge(EDGE_GOTO, node_id, chapter_node_id(target), detail=target))
            elif target:
                edges.append(Edge(EDGE_GOTO, node_id, ensure_missing(target), detail=target))
            else:
                edges.append(Edge(EDGE_GOTO, node_id, ROOT_ID, detail="离开章节"))
        prefix = schema.CHOICE_CONDITION_PREFIX
        # 同一（被引用触发器 → 触发器）对上的多条条件规则合并为一条边：画布上
        # 不叠画重复箭头，详情面板按「谁依赖它」一触器一行
        referenced_names = []
        for key in _condition_keys(trigger.get("condition")):
            if not key.startswith(prefix):
                continue
            referenced = key[len(prefix):].strip()
            if referenced in trigger_names and referenced not in referenced_names:
                referenced_names.append(referenced)
        for referenced in referenced_names:
            edges.append(Edge(EDGE_CHOICE, trigger_node_id(referenced), node_id,
                              detail=referenced))

    # ---------- 泳道 ----------
    # 顺序与编辑器面板一致：章节按配置顺序，然后是「章节不存在」的占位泳道，
    # 最后才是「任意章节 / 无章节」。
    by_id = {node.id: node for node in nodes}
    grouped = {}
    for node in nodes:
        if node.kind == NODE_TRIGGER:
            grouped.setdefault(node.owner, []).append(node.id)
    lanes = []
    for node_id in chapter_ids:
        lanes.append((by_id[node_id].name, [node_id] + grouped.get(node_id, [])))
    for node_id in missing_lanes:
        lanes.append((f"{by_id[node_id].name}{MISSING_LANE_SUFFIX}",
                      [node_id] + grouped.get(node_id, [])))
    lanes.append((f"{CHAPTER_ANY_LABEL} / {ROOT_LABEL}",
                  [ROOT_ID] + grouped.get(ROOT_ID, [])))
    return ScenarioGraph(nodes, edges, lanes)
