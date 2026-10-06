"""副本显示组件注册表与官方组件包加载。

副本会话窗口的显示组件（文本栏、属性条、伤亡记录等）由组件包提供：
- 官方组件包是窗口层常驻 Python 包 ``dungeon.window.component_pack``（随应用分发，
  经 importlib 惰性导入；与 ``data_dir`` 重定向、打包路径都无关）；
- 外部组件包（可选）由 ``ComponentRegistry(pack_dir=...)`` 显式指定目录，入口文件名
  固定为 ``components.py``；命中时覆盖常驻包，用于整体替换组件实现；
- 文本主组件由方案配置的 ``text_component`` 字段三选一声明（text / text_card /
  text_nvl，见 dungeon.schema），``components`` 列表只放其余组件；
- 组件类通过约定的 ``build/layout/refresh/destroy`` 生命周期钩子与窗口交互，
  根对象（ctx）即会话窗口实例，只读窗口现有状态（dungeon_state / story_history 等）。
- 文本组件的数据源是 ``ctx.story_history``：条目为
  ``{"type_str": 类型前缀, "text": 正文, "highlight": 高亮, "speaker": 说话人|None}``。
  ``speaker`` 由 Solea/Bulla 对话分支的 ``@说话人@`` 标记解析而来（见
  dungeon/splitter.py），None 表示叙述句；galgame 式组件可用它渲染名牌，
  并回退到 ``type_str``。
 """

import importlib.util
import os
import sys
import traceback
import weakref

from dungeon import process_log
from dungeon.schema import (DEFAULT_TEXT_COMPONENT, TEXT_COMPONENT_IDS)

# 文本显示家族（主组件层）：与 schema.TEXT_COMPONENT_IDS 同源，都声明
# owns_text_display 接管文本显示；配置层级的三选一由 text_component 字段承担
TEXT_FAMILY_IDS = frozenset(TEXT_COMPONENT_IDS)

# 官方组件包：窗口层常驻 Python 包（唯一默认来源）
_BUILTIN_PACKAGE = "dungeon.window.component_pack"

# 外部组件包入口文件名（外部包可以是单文件，也可在文件内相对 import 同目录模块）
_PACK_ENTRY = "components.py"
_PACK_MODULE = "_dungeon_components_pack"


def _load_builtin_package():
    """导入窗口层常驻组件包，返回 {id: 组件类} 或 None。

    常驻包随应用分发、位于 Python 包内，因此不受 ``data_dir`` 重定向与打包路径
    影响；导入失败（显式日志）时返回 None，由调用方决定降级行为。
    """
    try:
        module = importlib.import_module(_BUILTIN_PACKAGE)
    except Exception as exc:
        process_log.log(f"[Components] 常驻组件包导入失败: {_BUILTIN_PACKAGE}: {exc}\n"
                        f"{traceback.format_exc()}")
        return None
    registry = getattr(module, "REGISTRY", None)
    return registry if isinstance(registry, dict) else None


def _load_pack_directory(pack_dir):
    """从单个组件包目录导入入口模块，返回 {id: 组件类} 或 None。"""
    if not pack_dir or not os.path.isdir(pack_dir):
        return None
    entry = os.path.join(pack_dir, _PACK_ENTRY)
    if not os.path.isfile(entry):
        return None
    try:
        spec = importlib.util.spec_from_file_location(
            _PACK_MODULE, entry, submodule_search_locations=[pack_dir])
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        # 相对 import 的解析要求父包先进入 sys.modules（标准配方）
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        registry = getattr(module, "REGISTRY", None)
        if not isinstance(registry, dict):
            registry = {}
        return registry
    except Exception as exc:
        process_log.log(f"[Components] 加载外部组件包失败: {pack_dir}: {exc}\n"
                        f"{traceback.format_exc()}")
        return None


class DungeonComponent:
    """组件基类：默认空实现，官方组件覆盖所需钩子。

    必选钩子（窗口在对应时机调用）：``build / layout / refresh / destroy``。
    可选钩子（未覆盖即视为不支持）：

    - ``top_inset(ctx)``：自报在窗口顶部占用的高度，全屏类组件据此让位；
    - ``toggle(ctx)``：面板类组件的展开 / 收起（F12 与工具条按钮入口）；
    - ``toolbar_hovered()``：光标是否悬停在组件工具条上，窗口据此不推进剧情。

    元数据：``id``（唯一 id，必须）、``label`` / ``description``（编辑器展示名与
    说明；未设置时编辑器回退为 id）、``param_specs``（可配置参数声明）。

        param_specs = [
            {"key": "text_color", "label": "文字颜色", "type": "color",
             "default": (255, 225, 150, 255)},
            {"key": "max_lines", "label": "显示行数", "type": "int",
             "default": 1, "min": 1, "max": 5, "note": "最近 N 个显示段落"},
        ]

    ``min`` / ``max`` 声明在 ``merge_params`` 里统一夹取（配置手改越界不会污染
    运行期），``note`` 只作为编辑器提示。

    **组件服务面**（组件访问窗口的唯一入口，实现见 dungeon/window/components.py
    的 ComponentHandler；替身实现见 tests/check_component_pack.py）：

    - 只读状态：``ctx.story_history`` / ``ctx.dungeon_state`` /
      ``ctx.evolution_attrs`` / ``ctx.name``；
    - 几何：``ctx.component_viewport()`` → (dpi_scale, 宽, 高)；
    - 帧时钟：``ctx.schedule(fn, *args)`` / ``ctx.schedule_every(interval, fn, key)`` /
      ``ctx.cancel_task(key)``（**不得**直接触碰窗口的 ``_frame``）；
    - 状态查询：``ctx.session_waiting_for_input()`` / ``ctx.component_autoplay_on()``；
    - 字体：``ctx.text_font_tag()`` / ``ctx.bold_font_tag()``；
    - 兄弟组件：``ctx.component(cid)`` 只读访问（不得自行构建 / 销毁兄弟组件，
      也不得直接推进剧情）；顶部让位用 ``ctx.component_top_inset()``
      而不是去读兄弟组件的私有几何；
    - 覆盖层（见 dungeon/window/overlay.py）：``ctx.toggle_overlay("log")`` /
      ``ctx.close_overlay()`` / ``ctx.overlay_open()``；覆盖层打开期间窗口进入
      阅读模态（点击只关闭覆盖层，剧情推进挂起）；
    - 工具服务：``ctx.toggle_autoplay()`` / ``ctx.take_screenshot()`` /
      ``ctx.toggle_proc_log()``。

    组件**不得**读窗口的私有属性（``_dpi_scale`` / ``_layout_w`` / ``_frame`` /
    ``_autoplay`` …）——守卫脚本的替身 ctx 只实现上面的服务面，越界即报错。
    """

    id = ""  # 组件唯一 id（子类必须设置）
    label = ""  # 编辑器展示名（留空回退为 id）
    description = ""  # 编辑器说明文案
    param_specs = []  # 可配置参数声明列表

    def __init__(self, ctx):
        self.ctx = ctx
        self.params = {}

    # ---- 必选钩子 ----
    def build(self, ctx):
        """主线程：创建 DPG 控件。ctx 为会话窗口实例。"""

    def layout(self, ctx):
        """主线程：窗口/视口尺寸或 DPI 变化时重排。"""

    def refresh(self, ctx):
        """主线程：状态变化后更新显示（由文本更新链调用）。"""

    def destroy(self, ctx):
        """主线程：会话退出前清理 DPG 控件。"""

    # ---- 可选钩子（默认不支持） ----
    def top_inset(self, ctx):
        """可选：本组件在窗口顶部占用的高度（px @ dpi=1）；默认 0。"""
        return 0

    def toggle(self, ctx):
        """可选：展开 / 收起（面板类组件，如过程日志）。"""

    def toolbar_hovered(self):
        """可选：光标是否悬停在组件自己的工具条上；默认 False。"""
        return False


def default_params_for(component_cls) -> dict:
    """按 param_specs 返回默认参数字典（无参数时为空 dict）。"""
    return {spec["key"]: spec.get("default") for spec in getattr(component_cls, "param_specs", [])}


def _clamp(value, spec):
    """按 spec 的 min / max 夹取数值（类型不符或未声明范围时原样返回）。"""
    lo, hi = spec.get("min"), spec.get("max")
    if lo is None and hi is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    if lo is not None and value < lo:
        return lo
    if hi is not None and value > hi:
        return hi
    return value


def merge_params(component_cls, overrides: dict) -> dict:
    """合并默认参数与配置覆盖值（忽略未知键；按 spec 的 min / max 夹取）。"""
    params = default_params_for(component_cls)
    overrides = overrides or {}
    for spec in getattr(component_cls, "param_specs", []):
        key = spec["key"]
        if key in overrides and overrides[key] is not None:
            params[key] = _clamp(overrides[key], spec)
    return params


def resolve_configured_params(component_cls, config: dict) -> dict:
    """从副本配置 components_params 中取出该组件的参数覆盖并合并。"""
    params_block = (config or {}).get("components_params") or {}
    overrides = params_block.get(component_cls.id) or {}
    if not isinstance(overrides, dict):
        overrides = {}
    return merge_params(component_cls, overrides)


class ComponentRegistry:
    """组件注册表：加载组件包并按键值复用类实例。"""

    def __init__(self, pack_dir=None):
        """pack_dir 为外部组件包目录（可选）；缺省只用窗口层常驻包。"""
        self._pack_dir = pack_dir or ""
        self.source = ""
        self._classes = {}
        # 每窗口一套实例：WeakKeyDictionary 保证窗口被回收后条目自动消失
        # （用 id(ctx) 作键会因 id 复用串到别的窗口，清理漏掉时还会常驻）
        self._instances = weakref.WeakKeyDictionary()
        self._load()

    def _load(self):
        """加载组件包：外部包（显式 pack_dir）优先，缺失时回退窗口层常驻包。

        ``source`` 记录实际生效的来源（外部目录路径或常驻包名），供诊断与守卫脚本
        断言「常驻包确实被加载」，避免再次出现「包在哪儿」的静默漂移。
        """
        self._classes = {}
        self.source = ""
        registry = _load_pack_directory(self._pack_dir) if self._pack_dir else None
        if registry:
            self.source = str(self._pack_dir)
        else:
            registry = _load_builtin_package()
            if registry:
                self.source = _BUILTIN_PACKAGE
        if registry:
            self._classes = {cid: cls for cid, cls in registry.items() if cls}
        if not self._classes:
            process_log.log("[Components] 未加载到任何显示组件包（常驻包导入失败？），"
                            "会话将退回内置文本容器")
        elif not set(self._classes) & TEXT_FAMILY_IDS:
            process_log.log("[Components] 组件包缺少文本组件（text/text_card/text_nvl），"
                  "会话将无文本栏")
        self._instances = weakref.WeakKeyDictionary()

    @property
    def available_ids(self):
        return sorted(self._classes)

    def component_class(self, cid):
        """返回组件 id 对应的类；未加载或未知 id 返回 None。

        供守卫脚本与诊断使用（编辑器只用 ``available_component_descriptions``）。
        """
        return self._classes.get(cid)

    def resolve_ids(self, ids):
        """把配置里的组件 id 列表解析为可实例化且去重的 id 列表。

        调用方保证首位是文本主组件 id（``text_component`` 字段，见窗口侧
        ``_configured_component_ids``）。未知 id 记录警告并跳过；解析结果里
        若没有任何文本主组件（配置的 id 不可用等），回退到默认文本组件——
        文本显示必须三选其一。
        """
        valid = []
        for cid in ids:
            if cid not in self._classes:
                process_log.log(f"[Components] 未知组件 {cid}，已跳过")
                continue
            if cid not in valid:
                valid.append(cid)
        if not any(cid in TEXT_FAMILY_IDS for cid in valid):
            fallback = next((c for c in TEXT_COMPONENT_IDS if c in self._classes), None)
            if fallback is None:
                process_log.log("[Components] 组件包缺少文本组件，会话将无文本栏")
            else:
                configured = str(ids[0]) if ids else ""
                if configured and configured != fallback:
                    process_log.log(f"[Components] 文本组件「{configured}」不可用，"
                                    f"回退为 {fallback}")
                valid.insert(0, fallback)
        return valid

    def instantiate(self, ctx, cid, config=None):
        """为给定会话窗口创建组件实例（每个 id 单例复用）。

        config 为副本配置 dict；组件的可配置参数会按 components_params
        合并覆盖值后注入实例。
        """
        cls = self._classes.get(cid)
        if cls is None:
            return None
        instance = self._instances.get(ctx)
        if instance is None:
            instance = {}
            self._instances[ctx] = instance
        if cid not in instance:
            try:
                component = cls(ctx)
                component.params = resolve_configured_params(cls, config)
                instance[cid] = component
            except Exception as exc:
                process_log.log(f"[Components] 实例化组件 {cid} 失败: {exc}\n"
                                f"{traceback.format_exc()}")
                instance[cid] = None
        return instance[cid]

    def discard(self, ctx):
        """会话窗口退出时丢弃该窗口的组件实例（配合 destroy 调用）。"""
        self._instances.pop(ctx, None)


# 全局共享注册表：一个进程只加载一次组件包
_registry = None


def get_registry():
    global _registry
    if _registry is None:
        _registry = ComponentRegistry()
    return _registry


def build_components(ctx, ids, config=None):
    """按 id 列表为会话窗口构建组件实例列表（跳过无效项）。

    config 为副本配置 dict，用于解析组件的可配置参数。
    """
    registry = get_registry()
    resolved = registry.resolve_ids(ids)
    components = []
    for cid in resolved:
        instance = registry.instantiate(ctx, cid, config=config)
        if instance is not None:
            components.append(instance)
    return components


def available_component_descriptions() -> list:
    """返回可用官方组件的信息列表（供编辑器展示）。

    每项: ``{"id", "label", "description", "param_specs": [...]}``——展示名与说明
    来自组件类自身的 ``label`` / ``description``（元数据单一真相源，留空回退 id），
    编辑器不再各自维护一份文案表。
    """
    registry = get_registry()
    descriptions = []
    for cid in registry._classes:
        cls = registry._classes[cid]
        descriptions.append({
            "id": cid,
            "label": str(getattr(cls, "label", "") or cid),
            "description": str(getattr(cls, "description", "") or ""),
            "param_specs": list(getattr(cls, "param_specs", [])),
        })
    descriptions.sort(key=lambda d: d["id"])
    return descriptions


__all__ = [
    "DungeonComponent", "ComponentRegistry",
    "get_registry", "build_components",
    "default_params_for", "merge_params", "resolve_configured_params",
    "available_component_descriptions",
]
