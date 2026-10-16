"""图校验：把关系图上的结构问题变成与字段校验同一套诊断。

``validate.py`` 管的是**字段级**问题（某个键写错了、某个文件不存在）；这里管的是
**结构级**问题——章节与触发器的依赖、可达性、跳转闭合、循环依赖。两者产出同一套
``Diagnostic``，接入点也同一个（``validate_scenario_config``），口径与判据见
``docs/Dungeon/scenario_graph.md`` §3。

**本模块不 import ``validate``**：它只产出 ``(level, path, message)`` 三元组，由
``validate.py`` 侧包成 ``Diagnostic``。这样避免
``validate → graph_lint → validate.Diagnostic`` 的循环 import，也不用在函数体里
写延迟 import。

五条规则
--------
========================  =======  ================================================
规则                      级别     判据
========================  =======  ================================================
章节走不到                error    不在 :meth:`chapter_reachability` 的章节
走不到任何结束章节        warning  结束章节存在，但一个都不在可达集合里
前置永远无法成立          warning  前置不在可触发集合里
自跳转                    warning  ``goto`` 目标就是自己所在的章节
循环依赖                  warning  前置分量没有「种子」成员（互锁）
========================  =======  ================================================

三处口径值得单独说明
--------------------
- **可达性规则只在方案真的会在章节间移动时才生效。** 一节都没接线（没有任何
  ``goto``、也没有指向章节的 ``overflow_target``）的方案，除起始章节外全部不可达，
  逐章刷一遍只是噪音——作者还在搭骨架，多半就是从 ``empty_scenario_config()`` 改
  出来的。因此 R1 / R2 都要求「至少有一条跳进章节的边」，且「至少有一个起始章节」
  （没有起始章节时顶层已有一条 error）。
- **没有可达的结束章节时，结束章节不再逐条报 R1。** 那种情况下 R2 的措辞更准确
  （「这一局收不了尾」），两条都报等于同一个原因刷两遍。
- **同位置动作竞争不进校验器。** 运行时 ``ACTION_POSITIONS`` 同位置至多生效一个，
  严格判断「两条的条件会不会撞车」需要求解器、不可行；`graph.same_position_groups()`
  只把同位置同作用域的成员分好组，交给关系图对话框按位呈现（看得见，但不刷屏）。
  本仓一贯的降噪口径：只有「无争议才报红」。详见 ``docs/Dungeon/scenario_graph.md`` §3.2。
"""

from dungeon.scenario_graph import (
    EDGE_GOTO, EDGE_OVERFLOW, EDGE_PRECOND, FLAG_ENDING, FLAG_START, NODE_CHAPTER,
    ROOT_ID,
)


def _chapter_path(node) -> str:
    return f"chapters[{node.index}].name" if node.index >= 0 else "chapters"


def _trigger_path(node, field: str = "") -> str:
    base = f"triggers[{node.index}]" if node.index >= 0 else "triggers"
    return f"{base}.{field}" if field else base


def _order_key(node):
    """报告顺序：按配置下标，无名条目排到最后。"""
    return node.index if node.index >= 0 else 1 << 30


def _has_chapter_jump(graph) -> bool:
    """方案里是否存在「跳进某个章节」的边（``goto`` 或指向章节的超限跳转）。

    空 ``overflow_target``（超限后离开章节、回到「无章节」）不算——那只是退出，
    不是接线。见模块说明的第一条口径。
    """
    for edge in graph.edges:
        if edge.kind not in (EDGE_GOTO, EDGE_OVERFLOW):
            continue
        target = graph.node(edge.dst)
        if target is not None and target.kind == NODE_CHAPTER:
            return True
    return False


def lint_graph(graph) -> list:
    """分析关系图，返回 ``[(level, path, message), ...]``（顺序即报告顺序）。"""
    found = []
    reached = graph.chapter_reachability()
    chapters = graph.chapters()
    starts = [c for c in chapters if c.has(FLAG_START)]
    endings = [c for c in chapters if c.has(FLAG_ENDING)]
    reachable = bool(starts) and _has_chapter_jump(graph)
    firable = graph.firable_triggers()
    deadlocks = graph.deadlock_sccs()
    deadlocked = {node_id for component in deadlocks for node_id in component}

    if reachable:
        _lint_unreachable_chapters(chapters, endings, reached, found)
    _lint_no_ending_reachable(chapters, endings, reached, reachable, found)
    _lint_broken_preconditions(graph, reached, firable, deadlocked, found)
    _lint_self_goto(graph, found)
    _lint_cycles(graph, deadlocks, found)
    return found


# ---------------- R1 ----------------

def _lint_unreachable_chapters(chapters, endings, reached, found) -> None:
    """章节走不到。

    没有任何结束章节可达时跳过结束章节（由 R2 统一表达），避免同一个原因报两遍。
    """
    has_reachable_ending = any(chapter.id in reached for chapter in endings)
    for chapter in chapters:
        if chapter.id in reached:
            continue
        if chapter.has(FLAG_ENDING) and not has_reachable_ending:
            continue
        found.append((
            "error", _chapter_path(chapter),
            f"章节「{chapter.name}」走不到：没有任何可触发的跳转或超限跳转进入它，"
            f"这段内容永远不会播放"))


# ---------------- R2 ----------------

def _lint_no_ending_reachable(chapters, endings, reached, reachable, found) -> None:
    """结束章节存在，但一个都走不到。

    「压根没有结束章节」是另一回事，由既有规则报 info；这里说的是**有结局、但够不着**。
    """
    if not chapters or not endings or not reachable:
        return
    if any(chapter.id in reached for chapter in endings):
        return
    names = "、".join(chapter.name for chapter in endings)
    found.append((
        "warning", "chapters",
        f"走不到任何结束章节（结束章节：{names}）：这一局无法正常收尾"))


# ---------------- R3 ----------------

def _lint_broken_preconditions(graph, reached, firable, deadlocked, found) -> None:
    """前置永远无法成立。

    跳过两类：触发器自己就在互锁分量里（由 R5 统一报），以及触发器自身所在章节本就
    不可达（由 R1 覆盖）——否则同一个问题会以三种面目刷屏。
    """
    for node in sorted(graph.triggers(), key=_order_key):
        if node.id in deadlocked:
            continue
        if node.owner != ROOT_ID and node.owner not in reached:
            continue
        broken = []
        for edge in graph.in_edges(node.id):
            if edge.kind != EDGE_PRECOND or edge.src in firable:
                continue
            source = graph.node(edge.src)
            if source is not None:
                broken.append(source.name)
        if not broken:
            continue
        found.append((
            "warning", _trigger_path(node, "precondition_names"),
            f"前置触发器 {'、'.join(broken)} 永远无法触发"
            f"（所在章节不可达，或受循环依赖牵连），「{node.name}」的条件永远无法满足"))


# ---------------- R4 ----------------

def _lint_self_goto(graph, found) -> None:
    """``goto`` 目标等于自身所在章节。

    运行时判为「已经处于目标章节」直接跳过，触发器不会产生任何效果。
    作用域是「任意章节 / 无章节」的不算——那种触发器的泳道头是起点，不指向某个章节。
    """
    for node in sorted(graph.triggers(), key=_order_key):
        if node.action_type != EDGE_GOTO or node.owner == ROOT_ID:
            continue
        if any(edge.kind == EDGE_GOTO and edge.dst == node.owner
               for edge in graph.out_edges(node.id)):
            found.append((
                "warning", _trigger_path(node, "action_data.chapter"),
                f"跳转目标就是自身所在的章节「{node.chapter}」：运行时判为"
                f"「已经处于目标章节」直接跳过，这个触发器不会产生任何效果"))


# ---------------- R5 ----------------

def _lint_cycles(graph, deadlocks, found) -> None:
    """前置分量没有「种子」成员（互锁、或自己以自己为前置）。"""
    for component in deadlocks:
        nodes = [graph.node(node_id) for node_id in component]
        nodes = sorted((n for n in nodes if n is not None), key=_order_key)
        if not nodes:
            continue
        if len(nodes) == 1:
            detail = f"「{nodes[0].name}」把自己写成了自己的前置"
        else:
            detail = " → ".join(node.name for node in nodes) + " 互为前置"
        found.append((
            "warning", _trigger_path(nodes[0], "precondition_names"),
            f"循环依赖：{detail}，没有任何一个能先触发，这些触发器都不会生效"))
