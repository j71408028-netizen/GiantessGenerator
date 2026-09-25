"""副本显示组件注册表与默认组件包加载。

副本会话窗口的显示组件（文本栏、属性条、伤亡记录等）由组件包提供：
- 官方组件包位于数据根目录 ``packs/scenarios/_default/components/``（随副本/世界包分发）；
- 副本在其 ``config.json`` 的 ``components`` 字段中按 id 声明要使用的组件，
  未声明时回退到内置默认 ``["text"]``；
- 组件类通过约定的 ``build/layout/refresh/destroy`` 生命周期钩子与窗口交互，
  根对象（ctx）即会话窗口实例，只读窗口现有状态（dungeon_state / story_history 等）。
- 文本组件的数据源是 ``ctx.story_history``：条目为
  ``{"type_str": 类型前缀, "text": 正文, "highlight": 高亮, "speaker": 说话人|None}``。
  ``speaker`` 由 Solea/Bulla 对话分支的 ``@说话人@`` 标记解析而来（见
  dungeon/splitter.py），None 表示叙述句；galgame 式组件可用它渲染名牌，
  并回退到 ``type_str``。
- 文本显示家族（``TEXT_FAMILY_IDS``：text / text_card / text_nvl）互斥，
  方案配置同时出现多个时保留第一个（见 ``resolve_ids``）。
 """

import os
import traceback

from dungeon.terms import DEFAULT_SCENARIO_ID, SCENARIO_RESOURCE_KEY

DEFAULT_COMPONENT_IDS = ["text"]
_FALLBACK_IDS = ["text"]

# 文本显示家族：三种 galgame 式文本组件互斥（都声明 owns_text_display 接管
# 文本显示），方案配置同时出现多个时保留第一个、其余跳过并警告
TEXT_FAMILY_IDS = frozenset({"text", "text_card", "text_nvl"})

# 组件包内的入口文件名（官方包只含此文件，避免 import 同目录其它 py）
_PACK_ENTRY = "components.py"


def _data_roots():
    """返回按优先级排列的数据根目录列表（高优先级在前）。"""
    from paths import data_dir
    root = data_dir()
    return [root]


def find_component_pack_dir():
    """定位默认组件包目录（不存在时返回 None）。"""
    for base in _data_roots():
        candidate = os.path.join(base, "packs", SCENARIO_RESOURCE_KEY,
                                 DEFAULT_SCENARIO_ID, "components")
        if os.path.isdir(candidate):
            return candidate
    return None


def _load_pack_directory(pack_dir):
    """从单个组件包目录导入入口模块，返回 {id: 组件类} 或 None。"""
    if not pack_dir or not os.path.isdir(pack_dir):
        return None
    entry = os.path.join(pack_dir, _PACK_ENTRY)
    if not os.path.isfile(entry):
        return None
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("_dungeon_components_pack", entry)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        registry = getattr(module, "REGISTRY", None)
        if not isinstance(registry, dict):
            registry = {}
        return registry
    except Exception as exc:
        print(f"[Components] 加载组件包失败: {pack_dir}: {exc}")
        traceback.print_exc()
        return None


class DungeonComponent:
    """组件基类：默认空实现，官方组件覆盖所需钩子。

    可配置参数：子类通过 ``param_specs`` 声明（见下方示例），窗口构建时
    会把配置覆盖值合并进默认值，存为 ``self.params``（dict）。

        param_specs = [
            {"key": "text_color", "label": "文字颜色", "type": "color",
             "default": (255, 225, 150, 255)},
            {"key": "width", "label": "宽度", "type": "int", "default": 300},
        ]
    """

    id = ""  # 组件唯一 id（子类必须设置）
    param_specs = []  # 可配置参数声明列表

    def __init__(self, ctx):
        self.ctx = ctx
        self.params = {}

    def build(self, ctx):
        """主线程：创建 DPG 控件。ctx 为会话窗口实例。"""

    def layout(self, ctx):
        """主线程：窗口/视口尺寸或 DPI 变化时重排。"""

    def refresh(self, ctx):
        """主线程：状态变化后更新显示（由文本更新链调用）。"""

    def destroy(self, ctx):
        """主线程：会话退出前清理 DPG 控件。"""


def default_params_for(component_cls) -> dict:
    """按 param_specs 返回默认参数字典（无参数时为空 dict）。"""
    return {spec["key"]: spec.get("default") for spec in getattr(component_cls, "param_specs", [])}


def merge_params(component_cls, overrides: dict) -> dict:
    """合并默认参数与配置覆盖值（忽略未知键）。"""
    params = default_params_for(component_cls)
    overrides = overrides or {}
    for spec in getattr(component_cls, "param_specs", []):
        key = spec["key"]
        if key in overrides and overrides[key] is not None:
            params[key] = overrides[key]
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
        self._pack_dir = pack_dir or find_component_pack_dir()
        self._classes = {}
        self._instances = {}
        self._load()

    def _load(self):
        self._classes = {}
        registry = _load_pack_directory(self._pack_dir)
        if registry:
            self._classes = {cid: cls for cid, cls in registry.items() if cls}
        if not set(self._classes) & TEXT_FAMILY_IDS:
            print("[Components] 组件包缺少文本组件（text/text_card/text_nvl），"
                  "会话将无文本栏")
        self._instances = {}

    @property
    def available_ids(self):
        return sorted(self._classes)

    def resolve_ids(self, ids):
        """把配置里的组件 id 列表解析为可实例化且去重的 id 列表。

        空/非法配置回退到默认组件；未知 id 记录警告并跳过。文本显示家族
        （TEXT_FAMILY_IDS）互斥：同时出现多个时保留第一个。
        """
        if not ids:
            return list(DEFAULT_COMPONENT_IDS)
        valid = []
        family_seen = None
        for cid in ids:
            if cid not in self._classes:
                print(f"[Components] 未知组件 {cid}，已跳过")
                continue
            if cid in TEXT_FAMILY_IDS:
                if family_seen is not None:
                    print(f"[Components] 文本组件互斥：{family_seen} 与 {cid} "
                          f"同时配置，保留 {family_seen}")
                    continue
                family_seen = cid
            if cid not in valid:
                valid.append(cid)
        if not valid:
            return list(_FALLBACK_IDS)
        return valid

    def instantiate(self, ctx, cid, config=None):
        """为给定会话窗口创建组件实例（每个 id 单例复用）。

        config 为副本配置 dict；组件的可配置参数会按 components_params
        合并覆盖值后注入实例。
        """
        cls = self._classes.get(cid)
        if cls is None:
            return None
        instance = self._instances.get(id(ctx))
        if instance is None:
            instance = {}
            self._instances[id(ctx)] = instance
        if cid not in instance:
            try:
                component = cls(ctx)
                component.params = resolve_configured_params(cls, config)
                instance[cid] = component
            except Exception as exc:
                print(f"[Components] 实例化组件 {cid} 失败: {exc}")
                traceback.print_exc()
                instance[cid] = None
        return instance[cid]

    def discard(self, ctx):
        """会话窗口退出时丢弃该窗口的组件实例（配合 destroy 调用）。"""
        self._instances.pop(id(ctx), None)


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

    每项: {"id": str, "param_specs": [ {key,label,type,default}, ... ]}
    """
    registry = get_registry()
    descriptions = []
    for cid in registry._classes:
        cls = registry._classes[cid]
        descriptions.append({
            "id": cid,
            "param_specs": list(getattr(cls, "param_specs", [])),
        })
    descriptions.sort(key=lambda d: d["id"])
    return descriptions


__all__ = [
    "DungeonComponent", "ComponentRegistry", "DEFAULT_COMPONENT_IDS",
    "get_registry", "build_components", "find_component_pack_dir",
    "default_params_for", "merge_params", "resolve_configured_params",
    "available_component_descriptions",
]