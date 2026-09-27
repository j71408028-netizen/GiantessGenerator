"""显示组件包守卫（无 GUI 可进 CI）：加载链、组件契约、参数声明与构建冒烟。

用法：``python scripts/check_component_pack.py``（退出码 0 = 通过；看结论行）

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

L2 组件冒烟：在**隐藏的** DPG 上下文（``create_viewport`` 但不 ``show_viewport``，
不会弹窗）里对每个组件跑 ``build → layout → refresh → destroy``，断言不抛异常、
destroy 后 main_window 子项数回到基线、文本组件接管期间内置 ``text_container`` 被隐藏；
无显示环境（DPG 起不来）时打印 SKIP、不计失败。
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
    required_hooks = ("build", "layout", "refresh", "destroy",
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
# L2：隐藏 DPG 上下文里的组件冒烟
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
        self.waiting = True                   # session_waiting_for_input 返回值
        self.autoplay = False                 # component_autoplay_on 返回值
        self.font_tags = {}                   # text/bold 字体 tag（默认无 → 组件自建）
        self.frame_tasks = {}                 # 帧任务表（key → 回调）
        self.toolbar_calls = []
        self.overlay_calls = []
        self._components = []

    # ---- 几何 / 时钟 ----
    def component_viewport(self):
        return self.viewport

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


def _dpg_context():
    """建一个**不显示**的 DPG 上下文与组件所需容器；不可用时返回 None。"""
    try:
        import dearpygui.dearpygui as dpg
    except Exception as exc:
        print(f"  SKIP 组件冒烟：无法 import dearpygui（{exc}）")
        return None
    try:
        dpg.create_context()
        dpg.create_viewport(title="component_pack_check", width=1024, height=768)
        with dpg.texture_registry(tag="dungeon_texture_registry"):
            pass
        with dpg.window(tag="main_window", label="Main", no_title_bar=True,
                        no_scrollbar=True, no_scroll_with_mouse=True):
            # 内置文本管线容器：文本组件接管期间应被隐藏
            with dpg.child_window(tag="bg_overlay_child", width=200, height=200):
                pass
            with dpg.child_window(tag="text_container", width=200, height=200):
                pass
        return dpg
    except Exception as exc:
        print(f"  SKIP 组件冒烟：DPG 上下文不可用（{type(exc).__name__}: {exc}）")
        try:
            dpg.destroy_context()
        except Exception:
            pass
        return None




def check_smoke(registry):
    """隐藏上下文里跑 build→layout→refresh→destroy，并核对控件无残留。"""
    dpg = _dpg_context()
    if dpg is None:
        return
    try:
        ctx = _FakeCtx()
        baseline = len(dpg.get_item_children("main_window", 1) or [])
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
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                traceback.print_exc()
            check(f"冒烟 {cid}：build/layout/refresh 不抛异常", not error, error)
            if not error and cid in TEXT_COMPONENT_IDS:
                check(f"冒烟 {cid}：接管期间内置 text_container 被隐藏",
                      not dpg.get_item_configuration("text_container").get("show", True))
            try:
                comp.destroy(ctx)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                traceback.print_exc()
            check(f"冒烟 {cid}：destroy 不抛异常", not error, error)
            ctx._components.remove(comp)
            remaining = len(dpg.get_item_children("main_window", 1) or [])
            check(f"冒烟 {cid}：destroy 后无残留控件", remaining == baseline,
                  (baseline, remaining))
            if getattr(comp, "_BLINK_TASK", ""):
                check(f"冒烟 {cid}：帧任务经 schedule_every/cancel_task 配对",
                      comp._BLINK_TASK not in ctx.frame_tasks, ctx.frame_tasks)

        check("冒烟：全部 destroy 后内置 text_container 恢复可见",
              bool(dpg.get_item_configuration("text_container").get("show", True)))

        # 真实构建入口：build_components 保序 + 跳过未知 id
        ctx2 = _FakeCtx()
        comps = build_components(ctx2, ["text", "ghost", "attr_bar", "proc_log"],
                                 {"components_params": {}})
        check("冒烟：build_components 保序且跳过未知 id",
              [c.id for c in comps] == ["text", "attr_bar", "proc_log"],
              [c.id for c in comps])
        for comp in comps:
            comp.destroy(ctx2)
    finally:
        dpg.destroy_context()


def main() -> int:
    print("[check_component_pack] 组件包守卫开始")
    registry = registry_mod.get_registry()
    check_registry(registry)
    check_resolution(registry)
    check_params(registry)
    check_contract(registry)
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
