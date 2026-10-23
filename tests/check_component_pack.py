"""显示组件包守卫（无 GUI 可进 CI）：加载链、组件契约、参数声明与构建冒烟。

用法：``python tests/check_component_pack.py``（退出码 0 = 通过；看结论行）

为什么需要它：组件包曾在「从 ``assets/components`` 搬到 ``dungeon/window/component_pack``」
的重构里整整一轮没被加载——注册表静默返回空表，编辑器组件页空白、副本窗口退回内置
文本容器，而既有守卫都发现不了（分层守卫只看 import 白名单，GUI 冒烟要有显示器才跑）。
本脚本把「包确实能被加载 + 组件契约成立 + 组件真的能建起来」变成无 GUI 门禁。

L1 纯逻辑（无 DPG）：
- 注册表来源必须是常驻包 ``dungeon.window.component_pack``，必需 id 齐全；
- 文本家族（``owns_text_display``）与 ``dungeon.schema.TEXT_COMPONENT_IDS`` 一致；
- ``param_specs`` 结构、默认值、颜色四通道；
- ``resolve_ids`` 回退（未知 id 跳过 / 去重 / 缺文本组件补默认）与参数合并语义；
- 组件包 AST 约束：不直接 ``print``（一律 ``dungeon.process_log``）、
  所有定义了 ``id`` 的组件类都登记进 ``REGISTRY``（防「写了组件忘注册」）。

L2 组件冒烟：不建 GUI 上下文，在替身 ctx 上跑 ``build → layout → refresh → destroy``，
断言不抛异常、帧任务成对清理、文本组件声明 ``owns_text_display``。真正的每帧
``draw`` 需要 GL/ImGui 上下文，由 autopilot 的 GUI 场景覆盖。
"""

import ast
import importlib
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dungeon.schema import DEFAULT_TEXT_COMPONENT, TEXT_COMPONENT_IDS  # noqa: E402
from dungeon.window import component_registry as registry_mod  # noqa: E402
from dungeon.window.component_registry import (  # noqa: E402
    ComponentRegistry, DungeonComponent, build_components, default_params_for,
    merge_params, resolve_configured_params,
)

PACK_DIR = ROOT / "dungeon" / "window" / "component_pack"
#: 官方包必须提供的组件：文本家族三选一 + 其余常驻组件
REQUIRED_IDS = set(TEXT_COMPONENT_IDS) | {"attr_bar", "proc_log"}
#: param_specs 允许的参数类型（编辑器按此渲染控件）
PARAM_TYPES = {"int", "float", "bool", "color", "text"}

total = 0
failures = []


def check(name, ok, extra=""):
    global total
    total += 1
    print(("  OK   " if ok else "  FAIL ") + name
          + (f"  <{extra}>" if extra and not ok else ""))
    if not ok:
        failures.append(name)



# ---------------------------------------------------------------------------
# L1：注册表、契约与参数声明
# ---------------------------------------------------------------------------

def check_registry(registry):
    check("注册表来源是常驻包",
          registry.source == registry_mod._BUILTIN_PACKAGE,
          registry.source or "(空)")
    ids = set(registry.available_ids)
    check("必需组件 id 齐全", REQUIRED_IDS <= ids, sorted(ids))

    text_ids = {cid for cid in ids
                if getattr(registry.component_class(cid), "owns_text_display", False)}
    check("文本家族与 schema 一致", text_ids == set(TEXT_COMPONENT_IDS),
          sorted(text_ids))

    for cid in sorted(ids):
        cls = registry.component_class(cid)
        specs = list(getattr(cls, "param_specs", []) or [])
        keys = [s.get("key") for s in specs if isinstance(s, dict)]
        broken = [s.get("key") for s in specs
                  if not (isinstance(s, dict) and s.get("key") and s.get("label")
                          and s.get("type") in PARAM_TYPES and "default" in s)]
        check(f"{cid}：param_specs 结构完整", not broken, broken)
        check(f"{cid}：param_specs 无重复键", len(keys) == len(set(keys)), keys)
        check(f"{cid}：默认值与声明一致",
              set(default_params_for(cls)) == set(keys), sorted(keys))
        colors = [s for s in specs if isinstance(s, dict) and s.get("type") == "color"]
        bad_colors = [s.get("key") for s in colors
                      if not (isinstance(s.get("default"), (tuple, list))
                              and len(s["default"]) == 4
                              and all(isinstance(v, int) and 0 <= v <= 255
                                      for v in s["default"]))]
        check(f"{cid}：颜色默认值是 (r,g,b,a)", not bad_colors, bad_colors)


def check_resolution(registry):
    check("resolve_ids：未知组件被跳过",
          registry.resolve_ids(["text", "ghost", "attr_bar"]) == ["text", "attr_bar"],
          registry.resolve_ids(["text", "ghost", "attr_bar"]))
    check("resolve_ids：重复 id 去重",
          registry.resolve_ids(["text", "text"]) == ["text"],
          registry.resolve_ids(["text", "text"]))
    resolved = registry.resolve_ids(["attr_bar"])
    check("resolve_ids：缺文本组件时补默认",
          resolved == [DEFAULT_TEXT_COMPONENT, "attr_bar"], resolved)


def check_params(registry):
    cls = registry.component_class("attr_bar")
    if cls is None:
        check("参数合并：attr_bar 可实例化", False, "组件包未加载")
        return
    defaults = default_params_for(cls)
    if not defaults:
        check("参数合并：attr_bar 声明了参数", False, "(param_specs 为空)")
        return
    key = sorted(defaults)[0]
    check("merge_params：默认值齐备", set(merge_params(cls, {})) == set(defaults))
    check("merge_params：未知键被忽略",
          "不存在" not in merge_params(cls, {"不存在": 1}),
          merge_params(cls, {"不存在": 1}))
    check("merge_params：None 不覆盖默认",
          merge_params(cls, {key: None})[key] == defaults[key])
    probe = 4242 if isinstance(defaults[key], int) else defaults[key]
    check("resolve_configured_params：按 id 取覆盖值",
          resolve_configured_params(
              cls, {"components_params": {"attr_bar": {key: probe}}})[key] == probe)
    check("resolve_configured_params：非法参数块回退默认",
          resolve_configured_params(cls, {"components_params": {"attr_bar": 5}})
          == defaults)
    check("resolve_configured_params：空配置回退默认",
          resolve_configured_params(cls, None) == defaults)


def check_pack_sources(registry):
    """组件包源码约束：不 print、组件类必须登记进 REGISTRY。"""
    files = sorted(PACK_DIR.glob("*.py"))
    check("组件包目录存在且含组件模块", len(files) >= 6, len(files))

    offenders = []
    unregistered = []
    private = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "print"):
                offenders.append(f"{path.name}:{node.lineno}")
            # 组件不得读窗口私有属性：ctx._xxx / self.ctx._xxx
            if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
                if isinstance(node.value, ast.Name) and node.value.id == "ctx":
                    private.append(f"{path.name}:{node.lineno} ctx.{node.attr}")
                elif (isinstance(node.value, ast.Attribute)
                      and node.value.attr == "ctx"
                      and isinstance(node.value.value, ast.Name)
                      and node.value.value.id == "self"):
                    private.append(f"{path.name}:{node.lineno} self.ctx.{node.attr}")
        if path.stem == "__init__":
            continue
        module = importlib.import_module(f"dungeon.window.component_pack.{path.stem}")
        for name, obj in vars(module).items():
            if (isinstance(obj, type) and obj is not DungeonComponent
                    and issubclass(obj, DungeonComponent)
                    and obj.__module__ == module.__name__
                    and (obj.__dict__.get("id") or "")):
                if registry.component_class(obj.id) is not obj:
                    unregistered.append(f"{path.stem}.{name}(id={obj.id!r})")
    check("组件包不直接 print（走 dungeon.process_log）", not offenders, offenders)
    check("组件不读窗口私有属性（走组件服务面）", not private, private)
    check("所有组件类都已登记进 REGISTRY", not unregistered, unregistered)


def check_contract(registry):
    """契约与元数据：展示文案、可选钩子、参数范围、tag 前缀。"""
    required_hooks = ("build", "layout", "draw", "refresh", "destroy",
                      "top_inset", "toggle", "toolbar_hovered")
    prefixes = {}
    reserved = {"main_window", "bg_overlay_child", "text_container",
                "notify_panel", "overlay_log"}
    clamp_cases = []

    for cid in sorted(registry.available_ids):
        cls = registry.component_class(cid)
        missing = [h for h in required_hooks if not callable(getattr(cls, h, None))]
        check(f"{cid}：必选与可选钩子齐备", not missing, missing)
        check(f"{cid}：声明展示名与说明",
              bool(str(getattr(cls, "label", "")).strip()
                   and str(getattr(cls, "description", "")).strip()),
              (getattr(cls, "label", ""), getattr(cls, "description", "")))
        prefix = str(getattr(cls, "TAG_PREFIX", "") or "")
        check(f"{cid}：声明 tag 前缀", bool(prefix), prefix)
        if prefix:
            check(f"{cid}：tag 前缀未与其它组件/窗口保留 tag 冲突",
                  prefix not in reserved and prefix not in prefixes,
                  (prefix, prefixes.get(prefix)))
            prefixes[prefix] = cid
        for spec in getattr(cls, "param_specs", []) or []:
            if (isinstance(spec, dict) and spec.get("type") == "int"
                    and ("min" in spec or "max" in spec)):
                clamp_cases.append((cid, cls, spec))

    # 参数范围：声明了 min/max 的整型参数必须在 merge_params 里被夹取
    for cid, cls, spec in clamp_cases:
        key = spec["key"]
        lo, hi = spec.get("min"), spec.get("max")
        if lo is not None:
            got = merge_params(cls, {key: lo - 1000})[key]
            check(f"{cid}.{key}：越界下界被夹到 min", got == lo, got)
        if hi is not None:
            got = merge_params(cls, {key: hi + 1000})[key]
            check(f"{cid}.{key}：越界上界被夹到 max", got == hi, got)
    check("存在带范围声明的参数（夹取链路在测）", bool(clamp_cases), len(clamp_cases))

    # 编辑器拿到的描述必须带文案（元数据单一真相源：文案来自组件类）
    described = {d["id"]: d for d in
                 registry_mod.available_component_descriptions()}
    check("available_component_descriptions 自带文案",
          all(described.get(cid, {}).get("label")
              and described.get(cid, {}).get("description")
              for cid in registry.available_ids),
          sorted(described))

    # 属性面板占位：top_inset 钩子必须真的返回正值（NVL 让位依赖它）
    ctx = _FakeCtx()
    attr_cls = registry.component_class("attr_bar")
    inset = int(attr_cls(ctx).top_inset(ctx) or 0) if attr_cls else 0
    check("attr_bar.top_inset 自报占位高度", inset > 0, inset)


def check_slots(registry):
    """代管区链路：三种文本组件都声明 status band，attr_bar 按分配矩形布局。"""
    from dungeon.window.layout import SHAPE_BAND, SlotAssignment

    for cid in sorted(TEXT_COMPONENT_IDS):
        ctx = _FakeCtx()
        text = registry.instantiate(ctx, cid, config={"components_params": {}})
        attr = registry.instantiate(ctx, "attr_bar", config={"components_params": {}})
        if text is None or attr is None:
            check(f"代管区 {cid}：组件可创建", False, (text, attr))
            continue
        # 真实窗口里所有组件先实例化，再逐个 build；文本组件的代管区要
        # 能看到同会话的 status 组件，所以这里也先挂进 _components。
        ctx._components.extend([text, attr])
        text.build(ctx)
        text.layout(ctx)
        slots = list(text.slots(ctx))
        check(f"代管区 {cid}：声明了代管区", bool(slots),
              [(s.id, s.shape, s.roles) for s in slots])
        status = next((s for s in slots
                       if s.shape == SHAPE_BAND and "status" in s.roles), None)
        check(f"代管区 {cid}：有 status band", status is not None,
              [(s.id, s.shape, s.roles) for s in slots])
        vw, vh = ctx.viewport[1], ctx.viewport[2]
        bad = [s.id for s in slots
               if not (s.rect[2] > 0 and s.rect[3] > 0
                       and s.rect[0] >= 0 and s.rect[1] >= 0
                       and s.rect[0] + s.rect[2] <= vw + 0.5
                       and s.rect[1] + s.rect[3] <= vh + 0.5)]
        check(f"代管区 {cid}：矩形在视口内", not bad, bad)

        attr.build(ctx)
        rect = None
        if status is not None:
            rect = status.take()
            ctx.assigned["attr_bar"] = SlotAssignment(status, rect)
        attr.layout(ctx)
        if rect is not None:
            got = tuple(round(float(v), 2) for v in attr._panel_rect)
            want = tuple(round(float(v), 2) for v in rect)
            check(f"代管区 {cid}：attr_bar 使用分配矩形", got == want,
                  (got, want))
        for comp in (attr, text):
            comp.destroy(ctx)
        ctx._components.remove(attr)
        ctx._components.remove(text)


def check_side_dock(registry):
    """text_card 侧边代管区：左右语义、卡片让位方向与日志分配矩形。"""
    from dungeon.window.layout import (SIDE_LEFT, SIDE_RIGHT, SHAPE_RAIL,
                                       SlotAssignment, enter_alpha,
                                       enter_offset, enter_rise, select_slot)

    def run_case(side):
        ctx = _FakeCtx()
        # 停靠侧来自窗口视觉设置（component_dock_side 服务面），不是方案参数。
        ctx.dock_side = side
        text = registry.instantiate(ctx, "text_card",
                                    config={"components_params": {}})
        log = registry.instantiate(ctx, "proc_log",
                                   config={"components_params": {}})
        if text is None or log is None:
            check(f"侧边代管区[{side}]：组件可创建", False, (text, log))
            return
        ctx._components.extend([text, log])
        text.build(ctx)
        log.build(ctx)
        base_x = text._panel_rect[0]
        check(f"侧边代管区[{side}]：日志收起时不声明",
              not any(s.id in ("card_side", "card_side_low")
                      for s in text.slots(ctx)),
              [(s.id, s.shape) for s in text.slots(ctx)])

        log.toggle(ctx)
        text.layout(ctx)
        slots = list(text.slots(ctx))
        rails = [s for s in slots if s.shape == SHAPE_RAIL]
        rail = next((s for s in rails if "log" in s.roles), None)
        check(f"侧边代管区[{side}]：日志展开时声明 log rail", rail is not None,
              [(s.id, s.shape, s.roles) for s in slots])
        low = next((s for s in rails if s.id == "card_side_low"), None)
        check(f"侧边代管区[{side}]：拆成上区 + 与卡片对齐的下区",
              len(rails) == 2 and low is not None and rail is not None
              and rail.rect[1] + rail.rect[3] <= low.rect[1] - 0.5,
              [(s.id, s.rect) for s in rails])
        if low is not None:
            panel = text._panel_rect
            check(f"侧边代管区[{side}]：下区与卡片同高、底端对齐",
                  abs(low.rect[1] - panel[1]) < 0.5
                  and abs(low.rect[3] - panel[3]) < 0.5,
                  (low.rect, panel))
            if rail is not None:
                check(f"侧边代管区[{side}]：上区高于卡片且只接 log，下区接纳其它贴边组件",
                      rail.rect[3] >= panel[3] - 0.5
                      and select_slot(slots, ("portrait",), (SHAPE_RAIL,)) is low,
                      (rail.rect, low.rect, panel))
        if rail is not None:
            vw = ctx.viewport[1]
            inside = (rail.rect[0] >= 0 and rail.rect[1] >= 0
                      and rail.rect[0] + rail.rect[2] <= vw + 0.5)
            if side == SIDE_LEFT:
                adjacent = (rail.rect[0] + rail.rect[2]
                            <= text._panel_rect[0] + 0.5)
            else:
                adjacent = (text._panel_rect[0] + text._panel_rect[2]
                            <= rail.rect[0] + 0.5)
            check(f"侧边代管区[{side}]：紧贴卡片一侧且在视口内",
                  inside and adjacent and rail.style.side == side,
                  (rail.rect, text._panel_rect))
            check(f"侧边代管区[{side}]：入场是水平推入、不淡入",
                  enter_offset(rail.style, rail.rect, vw, 0.0) != 0
                  and enter_rise(rail.style, rail.rect, ctx.viewport[2], 0.0) == 0
                  and enter_alpha(rail.style, 0.0) == 1.0,
                  (rail.style.enter_from, rail.style.enter_fade))
            rect = rail.take()
            ctx.assigned["proc_log"] = SlotAssignment(rail, rect)
            log.layout(ctx)
            check(f"侧边代管区[{side}]：日志使用分配矩形",
                  tuple(round(float(v), 2) for v in log._panel_rect)
                  == tuple(round(float(v), 2) for v in rect),
                  (log._panel_rect, rect))
            if side == SIDE_LEFT:
                shifted = text._panel_rect[0] > base_x + 1
                disjoint = (rail.rect[0] + rail.rect[2]
                            <= text._panel_rect[0] + 0.5)
            else:
                shifted = text._panel_rect[0] < base_x - 1
                disjoint = (text._panel_rect[0] + text._panel_rect[2]
                            <= rail.rect[0] + 0.5)
            check(f"侧边代管区[{side}]：卡片往另一侧让位", shifted,
                  (base_x, text._panel_rect[0]))
            check(f"侧边代管区[{side}]：代管区与卡片不重叠", disjoint,
                  (rail.rect, text._panel_rect))

        log.toggle(ctx)
        text.layout(ctx)
        check(f"侧边代管区[{side}]：日志收起后卡片回到原位",
              abs(text._panel_rect[0] - base_x) < 0.5,
              (base_x, text._panel_rect[0]))
        for comp in (log, text):
            comp.destroy(ctx)
            ctx._components.remove(comp)

    for side in (SIDE_LEFT, SIDE_RIGHT):
        run_case(side)

    # 大屏：卡片宽度有可读上限，卡片 + 代管区整体居中，两者仍相邻。
    ctx = _FakeCtx()
    ctx.viewport = (1.0, 3200, 1600)
    ctx.dock_side = SIDE_LEFT
    text = registry.instantiate(ctx, "text_card",
                                config={"components_params": {}})
    log = registry.instantiate(ctx, "proc_log", config={"components_params": {}})
    ctx._components.extend([text, log])
    text.build(ctx)
    log.build(ctx)
    log.toggle(ctx)
    text.layout(ctx)
    rail = next((s for s in text.slots(ctx) if "log" in s.roles), None)
    panel = text._panel_rect
    vw = ctx.viewport[1]
    if rail is None:
        check("侧边代管区：大屏上仍声明上区", False, panel)
    else:
        group_left = min(panel[0], rail.rect[0])
        group_right = max(panel[0] + panel[2], rail.rect[0] + rail.rect[2])
        check("侧边代管区：大屏上卡片宽度不超过可读上限",
              panel[2] < round(vw * 0.7) - 1, (panel, vw))
        check("侧边代管区：大屏上卡片与代管区整体居中且相邻",
              abs((group_left + group_right) / 2.0 - vw / 2.0) < 2
              and rail.rect[0] + rail.rect[2] <= panel[0] + 0.5,
              (panel, rail.rect, vw))
    for comp in (log, text):
        comp.destroy(ctx)
        ctx._components.remove(comp)

    # 视口太矮：上区放不下时整列都不声明，日志回退自己的右上角面板。
    ctx = _FakeCtx()
    ctx.viewport = (1.0, 1280, 320)
    text = registry.instantiate(ctx, "text_card",
                                config={"components_params": {}})
    log = registry.instantiate(ctx, "proc_log", config={"components_params": {}})
    ctx._components.extend([text, log])
    text.build(ctx)
    log.build(ctx)
    log.toggle(ctx)
    text.layout(ctx)
    log.layout(ctx)
    check("侧边代管区：视口太矮时不声明上区 / 下区，日志回退面板",
          not any(s.shape == SHAPE_RAIL for s in text.slots(ctx))
          and log._slot_rect is None
          and log._panel_rect[2] > 0 and log._panel_rect[3] > 0,
          ([(s.id, s.rect) for s in text.slots(ctx)], log._slot_rect,
           log._panel_rect))
    for comp in (log, text):
        comp.destroy(ctx)
        ctx._components.remove(comp)


def check_gradient_dock(registry):
    """text 渐变栏的侧边代管区：对齐正文列一侧、停在正文上方，组件从下方升起淡入。"""
    from dungeon.window.layout import (ENTER_FROM_BOTTOM, SIDE_LEFT, SIDE_RIGHT,
                                       SHAPE_RAIL, SlotAssignment, enter_alpha,
                                       enter_offset, enter_rise)

    def run_case(side):
        ctx = _FakeCtx()
        # 停靠侧来自窗口视觉设置（component_dock_side 服务面），不是方案参数。
        ctx.dock_side = side
        text = registry.instantiate(ctx, "text",
                                    config={"components_params": {}})
        log = registry.instantiate(ctx, "proc_log",
                                   config={"components_params": {}})
        if text is None or log is None:
            check(f"渐变代管区[{side}]：组件可创建", False, (text, log))
            return
        ctx._components.extend([text, log])
        text.build(ctx)
        log.build(ctx)
        base_panel = tuple(text._panel_rect)
        check(f"渐变代管区[{side}]：日志收起时不声明",
              not any(s.id == "scrim_side" for s in text.slots(ctx)),
              [(s.id, s.shape) for s in text.slots(ctx)])

        log.toggle(ctx)
        text.layout(ctx)
        slots = list(text.slots(ctx))
        rail = next((s for s in slots
                     if s.shape == SHAPE_RAIL and "log" in s.roles), None)
        check(f"渐变代管区[{side}]：日志展开时声明 log rail", rail is not None,
              [(s.id, s.shape, s.roles) for s in slots])
        if rail is None:
            return
        vw, vh = ctx.viewport[1], ctx.viewport[2]
        inside = (rail.rect[0] >= 0 and rail.rect[1] >= 0
                  and rail.rect[0] + rail.rect[2] <= vw + 0.5
                  and rail.rect[1] + rail.rect[3] <= vh + 0.5)
        panel_x, panel_w = text._panel_rect[0], text._panel_rect[2]
        if side == SIDE_LEFT:
            aligned = abs(rail.rect[0] - panel_x) < 0.5
        else:
            aligned = abs(rail.rect[0] + rail.rect[2]
                          - (panel_x + panel_w)) < 0.5
        # 渐变栏不做横向让位，侧边代管区向上占用正文上方空间。
        above_text = rail.rect[1] + rail.rect[3] <= text._panel_rect[1] + 0.5
        check(f"渐变代管区[{side}]：与正文列同侧对齐且停在正文上方",
              inside and aligned and above_text and rail.style.side == side,
              (rail.rect, text._panel_rect, rail.style.side))
        check(f"渐变代管区[{side}]：正文不做横向让位",
              tuple(text._panel_rect) == base_panel,
              (base_panel, text._panel_rect))
        # 呈现契约：从下方升起 + 淡入，而不是水平推入。
        check(f"渐变代管区[{side}]：入场为下方升起并淡入",
              rail.style.enter_from == ENTER_FROM_BOTTOM
              and rail.style.enter_fade
              and enter_rise(rail.style, rail.rect, vh, 0.0) > 0
              and abs(enter_rise(rail.style, rail.rect, vh, 1.0)) < 1e-6
              and enter_offset(rail.style, rail.rect, vw, 0.0) == 0.0
              and abs(enter_alpha(rail.style, 0.0)) < 1e-6
              and abs(enter_alpha(rail.style, 1.0) - 1.0) < 1e-6,
              (rail.style.enter_from, rail.style.enter_fade))
        rect = rail.take()
        ctx.assigned["proc_log"] = SlotAssignment(rail, rect)
        log.layout(ctx)
        check(f"渐变代管区[{side}]：日志使用分配矩形",
              tuple(round(float(v), 2) for v in log._panel_rect)
              == tuple(round(float(v), 2) for v in rect),
              (log._panel_rect, rect))
        log.toggle(ctx)
        text.layout(ctx)
        check(f"渐变代管区[{side}]：收起即释放",
              not any(s.id == "scrim_side" for s in text.slots(ctx)),
              [(s.id, s.shape) for s in text.slots(ctx)])
        for comp in (log, text):
            comp.destroy(ctx)
            ctx._components.remove(comp)

    for side in (SIDE_LEFT, SIDE_RIGHT):
        run_case(side)

    # 大屏：正文列有可读宽度上限并居中，代管区对齐正文列一侧，不贴窗口角落。
    ctx = _FakeCtx()
    ctx.viewport = (1.0, 3200, 1600)
    ctx.dock_side = SIDE_RIGHT
    text = registry.instantiate(ctx, "text", config={"components_params": {}})
    log = registry.instantiate(ctx, "proc_log", config={"components_params": {}})
    ctx._components.extend([text, log])
    text.build(ctx)
    log.build(ctx)
    log.toggle(ctx)
    text.layout(ctx)
    rail = next((s for s in text.slots(ctx) if "log" in s.roles), None)
    panel = text._panel_rect
    vw = ctx.viewport[1]
    if rail is None:
        check("渐变代管区：大屏上仍声明", False, panel)
    else:
        check("渐变代管区：大屏上正文列居中且有可读宽度上限",
              panel[2] < vw - 2 * 40
              and abs(panel[0] + panel[2] / 2.0 - vw / 2.0) < 2,
              (panel, vw))
        check("渐变代管区：大屏上对齐正文列一侧（不贴窗口角落）",
              abs(rail.rect[0] + rail.rect[2]
                  - (panel[0] + panel[2])) < 0.5,
              (rail.rect, panel))
    for comp in (log, text):
        comp.destroy(ctx)
        ctx._components.remove(comp)

    # 渐变区占满视口（height_percent=80）时放不下侧边代管区，日志回退右上角面板。
    ctx = _FakeCtx()
    text = registry.instantiate(
        ctx, "text",
        config={"components_params": {"text": {"height_percent": 80}}})
    log = registry.instantiate(ctx, "proc_log", config={"components_params": {}})
    ctx._components.extend([text, log])
    text.build(ctx)
    log.build(ctx)
    log.toggle(ctx)
    text.layout(ctx)
    log.layout(ctx)
    check("渐变代管区：渐变区过高时不声明，日志回退面板",
          not any(s.id == "scrim_side" for s in text.slots(ctx))
          and log._slot_rect is None
          and log._panel_rect[2] > 0 and log._panel_rect[3] > 0,
          ([(s.id, s.shape) for s in text.slots(ctx)], log._slot_rect,
           log._panel_rect))
    for comp in (log, text):
        comp.destroy(ctx)
        ctx._components.remove(comp)


def check_nvl_dock(registry):
    """NVL 覆盖层：百分比边距、正文列可读宽度、侧边代管区贴正文列、背景融合。"""
    from dungeon.window.component_pack.text_nvl import _DOCK_GAP
    from dungeon.window.layout import (SHAPE_BAND, SHAPE_RAIL, SIDE_LEFT,
                                       SIDE_RIGHT, SURFACE_HOST, SURFACE_OWN,
                                       SlotAssignment)

    def make_nvl(ctx, **params):
        return registry.instantiate(
            ctx, "text_nvl",
            config={"components_params": {"text_nvl": dict(params)}})

    # 边距：0% 仍是旧版全屏；10% 按视口对应边长向内收。
    ctx = _FakeCtx()
    text = make_nvl(ctx)
    ctx._components.append(text)
    text.build(ctx)
    vw, vh = ctx.viewport[1], ctx.viewport[2]
    check("NVL 边距：0% 仍是全屏",
          tuple(text._panel_rect) == (0.0, 0.0, float(vw), float(vh)),
          text._panel_rect)

    ctx = _FakeCtx()
    attr = registry.instantiate(ctx, "attr_bar",
                                config={"components_params": {}})
    text = make_nvl(ctx, margin_percent=10)
    ctx._components.extend([text, attr])
    text.build(ctx)
    attr.build(ctx)
    text.layout(ctx)
    px, py, pw, ph = text._panel_rect
    margin_x, margin_y = round(vw * 0.1), round(vh * 0.1)
    check("NVL 边距：10% 按视口边长内缩",
          abs(px - margin_x) < 0.5 and abs(py - margin_y) < 0.5
          and abs(pw - (vw - 2 * margin_x)) < 0.5
          and abs(ph - (vh - 2 * margin_y)) < 0.5,
          (text._panel_rect,
           (margin_x, margin_y, vw - 2 * margin_x, vh - 2 * margin_y)))
    band = next((s for s in text.slots(ctx)
                 if s.shape == SHAPE_BAND and "status" in s.roles), None)
    check("NVL 边距：状态带落在覆盖层内",
          band is not None and band.rect[0] >= px - 0.5
          and band.rect[1] >= py - 0.5
          and band.rect[0] + band.rect[2] <= px + pw + 0.5
          and band.rect[1] + band.rect[3] <= py + ph + 0.5,
          (None if band is None else band.rect, text._panel_rect))
    for comp in (attr, text):
        comp.destroy(ctx)
        ctx._components.remove(comp)

    def run_case(side):
        ctx = _FakeCtx()
        ctx.dock_side = side
        text = make_nvl(ctx, margin_percent=8)
        log = registry.instantiate(ctx, "proc_log",
                                   config={"components_params": {}})
        ctx._components.extend([text, log])
        text.build(ctx)
        log.build(ctx)
        base_panel = tuple(text._panel_rect)
        check(f"NVL 代管区[{side}]：日志收起时不声明",
              not any(s.id == "nvl_side" for s in text.slots(ctx)),
              [(s.id, s.shape) for s in text.slots(ctx)])

        log.toggle(ctx)
        text.layout(ctx)
        slots = list(text.slots(ctx))
        rail = next((s for s in slots
                     if s.shape == SHAPE_RAIL and "log" in s.roles), None)
        check(f"NVL 代管区[{side}]：日志展开时声明 log rail", rail is not None,
              [(s.id, s.shape, s.roles) for s in slots])
        if rail is None:
            for comp in (log, text):
                comp.destroy(ctx)
                ctx._components.remove(comp)
            return
        _px, py, _pw, ph = text._panel_rect
        text_x, text_w = text._layout[-2], text._layout[-1]
        if side == SIDE_LEFT:
            gap = text_x - (rail.rect[0] + rail.rect[2])
        else:
            gap = rail.rect[0] - (text_x + text_w)
        flush = (abs(rail.rect[1] - py) < 0.5
                 and abs(rail.rect[3] - ph) < 0.5
                 and abs(gap - _DOCK_GAP * ctx.viewport[0]) < 0.5)
        check(f"NVL 代管区[{side}]：紧贴正文列且占满高度",
              flush and rail.style.side == side, (rail.rect, text._panel_rect))
        merged = (rail.style.surface == SURFACE_HOST
                  and rail.style.bg is None and rail.style.border is None
                  and rail.style.separator is None)
        check(f"NVL 代管区[{side}]：缺省不画底纹（与正文背景融合）", merged,
              (rail.style.surface, rail.style.bg, rail.style.border,
               rail.style.separator))
        if side == SIDE_LEFT:
            disjoint = text_x >= rail.rect[0] + rail.rect[2] - 0.5
        else:
            disjoint = text_x + text_w <= rail.rect[0] + 0.5
        check(f"NVL 代管区[{side}]：正文列让出日志列", disjoint,
              (text_x, text_w, rail.rect))

        rect = rail.take()
        ctx.assigned["proc_log"] = SlotAssignment(rail, rect)
        log.layout(ctx)
        check(f"NVL 代管区[{side}]：日志使用分配矩形",
              tuple(round(float(v), 2) for v in log._panel_rect)
              == tuple(round(float(v), 2) for v in rect),
              (log._panel_rect, rect))
        check(f"NVL 代管区[{side}]：日志跟随宿主底纹、不自绘",
              log._slot_style is not None
              and log._slot_style.surface == SURFACE_HOST,
              None if log._slot_style is None else log._slot_style.surface)
        check(f"NVL 代管区[{side}]：日志不改变覆盖层几何",
              tuple(text._panel_rect) == base_panel,
              (base_panel, text._panel_rect))

        # 子组件仍可否决宿主底纹，改成自绘独立面板。
        log.params["draw_own_surface"] = True
        log.layout(ctx)
        check(f"NVL 代管区[{side}]：日志可自绘底纹",
              log._slot_style is not None
              and log._slot_style.surface == SURFACE_OWN,
              None if log._slot_style is None else log._slot_style.surface)
        log.params["draw_own_surface"] = False
        log.layout(ctx)

        log.toggle(ctx)
        text.layout(ctx)
        check(f"NVL 代管区[{side}]：收起即释放",
              not any(s.id == "nvl_side" for s in text.slots(ctx)),
              [(s.id, s.shape) for s in text.slots(ctx)])
        for comp in (log, text):
            comp.destroy(ctx)
            ctx._components.remove(comp)

    for side in (SIDE_LEFT, SIDE_RIGHT):
        run_case(side)

    # 大屏：正文列有可读上限，日志 + 正文整体居中，两者仍相邻。
    ctx = _FakeCtx()
    ctx.viewport = (1.0, 3200, 1600)
    ctx.dock_side = SIDE_RIGHT
    text = make_nvl(ctx)
    log = registry.instantiate(ctx, "proc_log",
                               config={"components_params": {}})
    ctx._components.extend([text, log])
    text.build(ctx)
    log.build(ctx)
    log.toggle(ctx)
    text.layout(ctx)
    rail = next((s for s in text.slots(ctx) if s.id == "nvl_side"), None)
    px, _py, pw, _ph = text._panel_rect
    text_x, text_w = text._layout[-2], text._layout[-1]
    if rail is None:
        check("NVL 代管区：大屏上仍声明", False, text._panel_rect)
    else:
        group_left = min(rail.rect[0], text_x)
        group_right = max(rail.rect[0] + rail.rect[2], text_x + text_w)
        check("NVL 代管区：大屏上正文列不超过可读宽度",
              text_w < pw, (text_x, text_w, pw))
        check("NVL 代管区：大屏上日志与正文相邻且整体居中",
              text_x + text_w <= rail.rect[0] + 0.5
              and abs((group_left + group_right) / 2.0
                      - (px + pw / 2.0)) < 2,
              (rail.rect, text_x, text_w, text._panel_rect))
    for comp in (log, text):
        comp.destroy(ctx)
        ctx._components.remove(comp)

    # 覆盖层太窄（边距挤掉宽度）时放不下日志列，回退右上角面板。
    ctx = _FakeCtx()
    ctx.viewport = (1.0, 480, 760)
    text = make_nvl(ctx, margin_percent=10)
    log = registry.instantiate(ctx, "proc_log",
                               config={"components_params": {}})
    ctx._components.extend([text, log])
    text.build(ctx)
    log.build(ctx)
    log.toggle(ctx)
    text.layout(ctx)
    log.layout(ctx)
    check("NVL 代管区：覆盖层太窄时不声明，日志回退面板",
          not any(s.id == "nvl_side" for s in text.slots(ctx))
          and log._slot_rect is None
          and log._panel_rect[2] > 0 and log._panel_rect[3] > 0,
          ([(s.id, s.shape) for s in text.slots(ctx)], log._slot_rect,
           log._panel_rect))
    for comp in (log, text):
        comp.destroy(ctx)
        ctx._components.remove(comp)


def check_external_pack():
    """外部组件包（显式 pack_dir）覆盖常驻包：来源与类都取自外部目录。"""
    from dungeon.window.component_registry import get_registry
    with tempfile.TemporaryDirectory(prefix="dungeon_cp_ext_") as tmp:
        (Path(tmp) / "components.py").write_text(
            "from dungeon.window.component_registry import DungeonComponent\n"
            "\n\n"
            "class ExternalText(DungeonComponent):\n"
            "    id = \"text\"\n"
            "\n\n"
            "REGISTRY = {\"text\": ExternalText}\n",
            encoding="utf-8")
        external = ComponentRegistry(pack_dir=tmp)
        check("外部包：source 指向外部目录", external.source == tmp, external.source)
        check("外部包：类确实来自外部目录",
              external.component_class("text") is not None
              and external.component_class("text") is not
              get_registry().component_class("text"),
              getattr(external.component_class("text"), "__module__", None))
        check("外部包：未登记的常驻组件不可用",
              external.component_class("attr_bar") is None)
        check("外部包：常驻包本身不受影响",
              registry_mod.get_registry().source == registry_mod._BUILTIN_PACKAGE,
              registry_mod.get_registry().source)


# ---------------------------------------------------------------------------
# L2：替身 ctx 上的 build/layout/refresh/destroy 冒烟
# ---------------------------------------------------------------------------

class _FakeCtx:
    """组件契约替身：**只**实现组件服务面与只读窗口状态。

    这里就是 ``dungeon.window.component_registry.DungeonComponent`` 文档里那份契约
    的可执行版本：组件想访问窗口只能走本类上的方法/属性，而窗口私有属性
    （``_dpi_scale`` / ``_layout_w`` / ``_frame`` / ``_autoplay`` / ``_generating`` /
    ``pending_option`` …）在本类上**不存在**——组件一旦伸手摸私有，L2 冒烟立刻
    AttributeError 判 FAIL；静态检查（``check_pack_sources``）再补一道 AST 扫描。
    增删服务面时先改 ``DungeonComponent`` 契约文档，再同步本类。
    """

    def __init__(self):
        # ---- 只读窗口状态 ----
        self.name = "组件自检"
        self.story_history = [
            {"type_str": "【对话】", "text": "你终于来了。", "highlight": False,
             "speaker": "Solea"},
            {"type_str": "【背景】", "text": "城市在脚下震颤。", "highlight": True,
             "speaker": None},
        ]
        self.dungeon_state = SimpleNamespace(
            intrusion=1.25, destruction=2.5, total_casualties=123456.0,
            custom_attrs={"狂气": 0.75})
        self.evolution_attrs = [
            {"type": "intrusion", "name": "介入度", "display_state": "show"},
            {"type": "destruction", "name": "破坏性", "display_state": "show"},
            {"type": "casualty", "name": "总伤亡", "display_state": "show"},
            {"type": "狂气", "name": "狂气", "display_state": "collapse"},
        ]
        # ---- 服务面状态（非下划线开头：替身自身状态，不是窗口私有属性）----
        self.viewport = (1.0, 1280, 800)      # (dpi_scale, 宽, 高)
        self.dock_side = "left"               # component_dock_side 返回值
        self.waiting = True                   # session_waiting_for_input 返回值
        self.autoplay = False                 # component_autoplay_on 返回值
        self.font_tags = {}                   # text/bold 字体 tag（默认无 → 组件自建）
        self.frame_tasks = {}                 # 帧任务表（key → 回调）
        self.toolbar_calls = []
        self.overlay_calls = []
        self._components = []
        # 代管区分配（check_slots 直接填；真实窗口由 ComponentHandler 发布）
        self.assigned = {}

    # ---- 几何 / 时钟 ----
    def component_viewport(self):
        return self.viewport

    def component_rect(self, cid):
        assignment = self.assigned.get(cid)
        return assignment.rect if assignment else None

    def component_slot(self, cid):
        assignment = self.assigned.get(cid)
        return assignment.slot if assignment else None

    def component_roles(self):
        roles = set()
        for comp in self._components:
            roles.update(getattr(comp, "roles", ()) or ())
        return tuple(sorted(roles))

    def component_dock_side(self):
        return self.dock_side

    def component_backdrop_blur(self):
        # 离屏自检没有 GL / 背景纹理：毛玻璃走"无副本"回退路径。
        return None

    def schedule(self, fn, *args):
        return fn(*args)

    def schedule_every(self, interval, fn, key):
        self.frame_tasks[key] = fn

    def cancel_task(self, key):
        self.frame_tasks.pop(key, None)

    # ---- 状态查询 ----
    def session_waiting_for_input(self):
        return self.waiting

    def component_autoplay_on(self):
        return self.autoplay

    # ---- 字体 ----
    def text_font_tag(self):
        return self.font_tags.get("text")

    def bold_font_tag(self):
        return self.font_tags.get("bold")

    # ---- 兄弟组件（与真实窗口同一语义：按 top_inset 钩子汇总占位）----
    def component(self, cid):
        for comp in self._components:
            if comp.id == cid:
                return comp
        return None

    def component_top_inset(self):
        inset = 0
        for comp in self._components:
            inset = max(inset, int(comp.top_inset(self) or 0))
        return inset

    # ---- 覆盖层 ----
    def toggle_overlay(self, name="log"):
        self.overlay_calls.append(("toggle", name))

    def close_overlay(self):
        self.overlay_calls.append(("close", None))

    def overlay_open(self):
        return None

    # ---- 工具服务 ----
    def toggle_autoplay(self):
        self.toolbar_calls.append("autoplay")

    def take_screenshot(self):
        self.toolbar_calls.append("screenshot")

    def toggle_proc_log(self):
        self.toolbar_calls.append("proc_log")

    def toggle_settings(self):
        self.toolbar_calls.append("settings")


def check_smoke(registry):
    """无 GUI 替身 ctx 上跑 build/layout/refresh/destroy，核对帧任务成对清理。"""
    ctx = _FakeCtx()
    for cid in registry.available_ids:
        comp = registry.instantiate(ctx, cid, config={"components_params": {}})
        if comp is None:
            check(f"冒烟 {cid}：实例可创建", False, "instantiate 返回 None")
            continue
        ctx._components.append(comp)
        error = ""
        try:
            comp.build(ctx)
            comp.layout(ctx)
            comp.refresh(ctx)
            if cid in TEXT_COMPONENT_IDS:
                assert getattr(comp, "owns_text_display", False), "文本组件应声明接管显示"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            traceback.print_exc()
        check(f"冒烟 {cid}：build/layout/refresh 不抛异常", not error, error)
        try:
            comp.destroy(ctx)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            traceback.print_exc()
        check(f"冒烟 {cid}：destroy 不抛异常", not error, error)
        ctx._components.remove(comp)
        blink_key = getattr(comp, "_BLINK_TASK", "")
        if blink_key:
            check(f"冒烟 {cid}：帧任务经 schedule_every/cancel_task 配对",
                  blink_key not in ctx.frame_tasks, ctx.frame_tasks)

    # 真实构建入口：build_components 保序 + 跳过未知 id
    ctx2 = _FakeCtx()
    comps = build_components(ctx2, ["text", "ghost", "attr_bar", "proc_log"],
                             {"components_params": {}})
    check("冒烟：build_components 保序且跳过未知 id",
          [c.id for c in comps] == ["text", "attr_bar", "proc_log"],
          [c.id for c in comps])
    for comp in comps:
        comp.build(ctx2)
        comp.layout(ctx2)
        comp.destroy(ctx2)


def main() -> int:
    print("[check_component_pack] 组件包守卫开始")
    registry = registry_mod.get_registry()
    check_registry(registry)
    check_resolution(registry)
    check_params(registry)
    check_contract(registry)
    check_slots(registry)
    check_side_dock(registry)
    check_gradient_dock(registry)
    check_nvl_dock(registry)
    check_pack_sources(registry)
    check_external_pack()
    check_smoke(registry)

    if failures:
        print(f"[check_component_pack] FAILED {len(failures)}/{total}: {failures}")
        return 1
    print(f"[check_component_pack] PASSED {total}/{total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
