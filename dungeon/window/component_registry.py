"""副本显示组件注册表与官方组件包加载。

副本会话窗口的显示组件（文本栏、属性条、伤亡记录等）由组件包提供：
- 官方组件包位于 assets 下的 ``components/``（随应用分发的只读资源，经
  ``paths.dungeon_components_dir()`` 定位，打包后随应用升级，不进用户数据区）；
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

import os
import traceback

from dungeon import process_log
from dungeon.schema import (DEFAULT_TEXT_COMPONENT, TEXT_COMPONENT_IDS)

DEFAULT_COMPONENT_IDS = [DEFAULT_TEXT_COMPONENT]
_FALLBACK_IDS = [DEFAULT_TEXT_COMPONENT]

# 文本显示家族（主组件层）：与 schema.TEXT_COMPONENT_IDS 同源，都声明
# owns_text_display 接管文本显示；配置层级的三选一由 text_component 字段承担
TEXT_FAMILY_IDS = frozenset(TEXT_COMPONENT_IDS)

# 组件包内的入口文件名（官方包只含此文件，避免 import 同目录其它 py）
_PACK_ENTRY = "components.py"


def find_component_pack_dir():
    """定位官方组件包目录（assets 随包分发；不存在时返回 None）。"""
    from paths import dungeon_components_dir
    candidate = dungeon_components_dir()
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
        process_log.log(f"[Components] 加载组件包失败: {pack_dir}: {exc}\n"
                        f"{traceback.format_exc()}")
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

    服务面：文本主组件（text_component 三选一）可经 ctx 调用窗口的
    覆盖层与查询服务（见 dungeon/window/overlay.py）——
    ``ctx.toggle_overlay("log")`` 调出/关闭对话记录、``ctx.close_overlay()``
    强制关闭、``ctx.overlay_open()`` 查询、``ctx.component(cid)`` 只读访问
    兄弟组件实例。覆盖层打开期间窗口进入阅读模态（点击只关闭覆盖层，
    剧情推进挂起）；组件不得自行构建/销毁兄弟组件或直接推进剧情。
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
            process_log.log("[Components] 组件包缺少文本组件（text/text_card/text_nvl），"
                  "会话将无文本栏")
        self._instances = {}

    @property
    def available_ids(self):
        return sorted(self._classes)

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
                process_log.log(f"[Components] 实例化组件 {cid} 失败: {exc}\n"
                                f"{traceback.format_exc()}")
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