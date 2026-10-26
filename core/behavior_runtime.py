"""行为包运行时：在世界包激活期间接管服务静态方法 / 逻辑函数的实现。

行为包位于世界包部署目录的 ``behaviors/<pack>/`` 下，包名由 world.json 的
``resources.behaviors`` 单元素列表声明。目录内每个 Python 模块需定义 ``register(runtime)``
入口，在函数内通过 ``runtime.override(target, impl)`` 注册对可覆盖目标
（``CreationService`` / ``StateService`` / ``logic`` / ``profile``）的实现覆盖。
静态开发副本存放在 ``data/static/behaviors/<pack>/``，只能通过世界包启用。

可覆盖目标分两种细度：

- **函数**：``@behavior_hook(scope, name)`` 注册；覆盖实现必须是可调用对象，
  且签名须接受默认实现的全部具名参数（或以 ``**kwargs`` 兜底）。
- **常量**：``behavior_const(scope, name, value)`` 注册；覆盖实现可注册同类型
  值，或返回该值的可调用对象；读取方用 ``runtime.resolve_value(key)`` 取有效值。

每个目标还带语义元数据：``layer``（infra / module / content）、``contract``
（point / strategy / data）、``since``、``deprecated``。``runtime.catalog()``
返回完整目录，供行为包作者判断“该不该接、接到什么程度”；层级不按目录划分，
按语义所有权划分。

``load_pack`` 在激活时逐条校验注册（未知 key / 签名不符 / 常量类型不符均拒绝
该条覆盖并记入诊断；同一 key 重复注册保留最后一条并给出警告），并产出
:class:`BehaviorLoadReport` 启用诊断报告，防止行为包静默失效。

行为模块示例（behaviors/my_pack/height_rules.py）：:

    from services.creation_service import CreationService

    def calculate_height(option, custom_val, min_slide, max_slide,
                         use_will, greed, rng, personality=None):
        # ...自定义身高算法...
        return 3.0, "within"

    def register(runtime):
        runtime.override(CreationService.calculate_height, calculate_height)

覆盖实现必须与被覆盖的默认实现保持相同签名与返回约定；如需复用默认实现，
可在覆盖函数内调用 ``runtime.default(target)(...)``。
"""

import functools
import importlib.util
import inspect
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

# 细度类别：函数钩子（可调用对象替换）与常量钩子（同类型值或返回值的可调用对象）。
KIND_FUNCTION = "function"
KIND_CONST = "const"

# 语义所有权层级：基础设施点 / 模块策略 / 模块内容。核心不变式不进入目录。
LAYER_INFRA = "infra"
LAYER_MODULE = "module"
LAYER_CONTENT = "content"

# 契约形态：基础设施算法点 / 模块决策策略 / 数据素材。
CONTRACT_POINT = "point"
CONTRACT_STRATEGY = "strategy"
CONTRACT_DATA = "data"

# 层级允许的契约；新增组合前先确认它没有把“机制”和“策略”混在一起。
_LAYER_CONTRACTS = {
    LAYER_INFRA: {CONTRACT_POINT, CONTRACT_DATA},
    LAYER_MODULE: {CONTRACT_STRATEGY, CONTRACT_DATA},
    LAYER_CONTENT: {CONTRACT_DATA},
}

# 目录契约基线版本：出身烙印基线（2026-10）在 2.0 alpha 窗口重定了整个
# 开放面，全目录 since 以此为基线；此后新增条目按实际版本显式标注。
_CATALOG_SINCE = "2.0"

# 行为包 API 契约版本：目录 key 开放面的分代号。2 = 2.0 出身烙印基线
# （删除 offline.model.* / spatial.TAG_* / recover_evolution，select 域
# 更名 report）。世界包在 world.json 里用 behavior_api 声明面向的版本，
# 与此不符时只在启用诊断里警告、不阻断（先软后硬）。
BEHAVIOR_API_VERSION = 2


@dataclass
class HookSpec:
    """注册表条目：``key -> 默认实现 + 细度 + 语义元数据``。

    ``layer`` / ``contract`` 说明这个目标属于基础设施点、模块策略还是内容；
    ``since`` / ``deprecated`` 供后续版本迁移。函数细度另记录默认实现的具名参数。
    """

    key: str
    default: Any
    kind: str
    param_names: Tuple[str, ...] = ()
    layer: str = LAYER_INFRA
    contract: str = CONTRACT_POINT
    since: str = _CATALOG_SINCE
    deprecated: bool = False


@dataclass
class BehaviorLoadReport:
    """一次 ``load_pack`` 的启用诊断报告。"""

    pack_name: str = ""
    world_id: str = ""
    modules_loaded: int = 0
    overridden: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    # 世界包声明的 behavior_api（None = 未声明，按 1 处理）与本进程
    # 注册表可见的 key 数，供诊断展示（导航栏 tooltip / 日志）。
    declared_api: Optional[int] = None
    available_keys: int = 0

    def api_line(self) -> str:
        """API 版本诊断行（未启用行为包时为空串）。"""
        if not self.pack_name:
            return ""
        declared = (str(self.declared_api) if self.declared_api is not None
                    else "未声明（按 1 处理）")
        return (f"behavior_api：包声明 {declared}，"
                f"运行时 {BEHAVIOR_API_VERSION}；"
                f"本进程可接管 key 共 {self.available_keys} 项")

    def log_lines(self) -> List[str]:
        """供 ``[BehaviorPack]`` 通道打印的诊断行。"""
        lines = []
        if self.pack_name:
            if self.overridden:
                lines.append(f"已启用行为包 '{self.pack_name}'，"
                             f"接管 {len(self.overridden)} 项：{', '.join(self.overridden)}")
            elif not self.errors:
                lines.append(f"已启用行为包 '{self.pack_name}'，未接管任何行为")
            api = self.api_line()
            if api:
                lines.append(api)
        for e in self.errors:
            lines.append(f"诊断异常：{e}")
        for w in self.warnings:
            lines.append(f"警告：{w}")
        return lines


_DEFAULTS: Dict[str, HookSpec] = {}


def resolve_behavior_source(root: str, name: str) -> Optional[str]:
    """在 root 下解析行为包路径：优先目录，其次兼容旧版单文件 ``<name>.py``。

    纯 ``os.path`` 逻辑，原本住在 ``persistence/world_pack.py``；行为运行时在
    ``load_pack`` 里需要它，而那构成一条 ``core -> persistence`` 反向边（守卫例外 #3）。
    它描述的是「行为包在部署目录里怎么定位」，与行为运行时同属一件事，故下移到本模块；
    ``persistence`` 侧不再定义它（``services/world_service.py`` 改为从本模块 import——
    ``services -> core`` 是合法边）。
    """
    if not name or not root:
        return None
    directory = os.path.join(root, name)
    if os.path.isdir(directory):
        return directory
    legacy = os.path.join(root, f"{name}.py")
    if os.path.isfile(legacy):
        return legacy
    return None


class BehaviorRuntime:
    """行为覆盖注册表：保存行为包对服务静态方法的覆盖实现。

    - ``override(target, impl)``：注册覆盖；target 为 ``"Service.method"``
      字符串，或对应静态方法对象。未知 key、签名不符或常量类型不符的
      注册会被拒绝并记入诊断，不再静默存放永远不被命中的覆盖。
    - ``resolve(key)``：查询覆盖实现；未覆盖时返回 None。
    - ``resolve_value(key)``：常量细度目标的有效值（覆盖实现可调用则调用）。
    - ``catalog()``：列出全部目标的元数据（层级 / 契约 / 版本 / 废弃状态）。
    - ``default(target)``：返回被覆盖前的默认实现，便于覆盖函数内部委托。
    - ``load_pack(manifest, installed)``：按清单加载行为包模块并注册，
      产出启用诊断报告（``last_report``）。
    - ``reset()``：清空全部覆盖。
    """

    def __init__(self) -> None:
        self._overrides: Dict[str, Callable] = {}
        self.last_report: Optional[BehaviorLoadReport] = None
        self._pending: Optional[BehaviorLoadReport] = None
        # 常量覆盖返回值不符的 key：只诊断一次，避免每次读取都刷日志。
        self._value_errors: set = set()

    # ---------- 键解析 ----------

    @staticmethod
    def _key_of(target: Any) -> str:
        if isinstance(target, str):
            return target
        hook_key = getattr(target, "__hook_key__", "") or ""
        if hook_key:
            return hook_key
        qname = getattr(target, "__qualname__", "") or getattr(target, "__name__", "")
        return qname or ""

    # ---------- 注册 / 查询 ----------

    def override(self, target: Any, impl: Any) -> None:
        key = self._key_of(target)
        if not key or "." not in key:
            raise ValueError(
                f"无法识别的覆盖目标: {target!r}"
                f"（需要 'Service.method' / 'logic.function' 字符串，"
                f"或带 __hook_key__ 的函数对象）")
        spec = _DEFAULTS.get(key)
        if spec is None:
            self._reject(f"未知覆盖目标 '{key}'：不存在或已被改名，该注册被忽略")
            return
        ok, reason = _validate_impl(spec, impl)
        if not ok:
            self._reject(f"覆盖 '{key}' 被拒绝：{reason}")
            return
        if key in self._overrides:
            self._warn(f"覆盖目标 '{key}' 被重复注册，后注册的实现生效")
        self._overrides[key] = impl
        if (self._pending is not None
                and key not in self._pending.overridden):
            self._pending.overridden.append(key)

    def _reject(self, message: str) -> None:
        if self._pending is not None:
            self._pending.errors.append(message)
        else:
            print(f"[BehaviorPack] {message}")

    def _warn(self, message: str) -> None:
        if self._pending is not None:
            self._pending.warnings.append(message)
        else:
            print(f"[BehaviorPack] 警告：{message}")

    def _reject_value(self, key: str, got_type: str) -> None:
        """常量覆盖返回值类型不符：回退默认值并记入诊断（每个 key 只报一次）。"""
        if key in self._value_errors:
            return
        self._value_errors.add(key)
        spec = _DEFAULTS.get(key)
        want = type(spec.default).__name__ if spec is not None else "默认类型"
        message = (f"常量覆盖 '{key}' 的返回值类型不符："
                   f"需要 {want}，得到 {got_type}；已回退默认值")
        if self.last_report is not None:
            self.last_report.errors.append(message)
        print(f"[BehaviorPack] {message}")

    def resolve(self, key: str) -> Optional[Callable]:
        return self._overrides.get(key)

    def resolve_value(self, key: str) -> Any:
        """常量细度目标的有效值。

        覆盖实现为可调用对象时**每次读取都会重新调用**（需要稳定值时请在
        包内缓存，或直接注册值本身）；返回值与默认值类型不符时回退默认值
        并记入诊断，只报一次。
        """
        impl = self._overrides.get(key)
        spec = _DEFAULTS.get(key)
        if impl is not None:
            value = impl() if callable(impl) else impl
            if spec is not None and not _const_type_ok(spec.default, value):
                self._reject_value(key, type(value).__name__)
                return spec.default
            return value
        return spec.default if spec is not None else None

    def catalog(self) -> List[Dict[str, Any]]:
        """返回全部可覆盖目标的元数据，按 key 排序。

        每项含 ``key`` / ``scope`` / ``kind`` / ``layer`` / ``contract`` /
        ``since`` / ``deprecated`` / ``params`` / ``default``（常量为注册
        默认值，函数为默认实现的函数名）。行为包作者据此判断目标属于
        基础设施点、模块策略还是内容，以及它是否已废弃。
        """
        return [
            {
                "key": spec.key,
                "scope": spec.key.split(".", 1)[0],
                "kind": spec.kind,
                "layer": spec.layer,
                "contract": spec.contract,
                "since": spec.since,
                "deprecated": spec.deprecated,
                "params": list(spec.param_names),
                "default": (spec.default if spec.kind == KIND_CONST
                            else getattr(spec.default, "__name__", "")),
            }
            for spec in sorted(_DEFAULTS.values(), key=lambda s: s.key)
        ]

    def default(self, target: Any) -> Callable:
        key = self._key_of(target)
        spec = _DEFAULTS.get(key)
        if spec is not None:
            return spec.default
        wrapped = getattr(target, "__wrapped__", None)
        return wrapped or target

    def reset(self) -> None:
        self._overrides.clear()
        self._value_errors.clear()

    # ---------- 从世界包加载 ----------

    def load_pack(self, manifest, installed: str) -> BehaviorLoadReport:
        """清空现有覆盖，按清单声明加载行为包模块，产出启用诊断报告。"""
        self.reset()
        packs = manifest.resources.get("behaviors") or []
        declared_api = getattr(manifest, "behavior_api", None)
        report = BehaviorLoadReport(
            pack_name=packs[0] if packs else "",
            world_id=manifest.world_id or "unknown",
            declared_api=declared_api,
            available_keys=len(_DEFAULTS))
        self._pending = report
        try:
            if packs:
                _check_api_version(declared_api, report)
                self._load_pack_modules(packs[0], installed, report)
        finally:
            self._pending = None
            self.last_report = report
        for line in report.log_lines():
            print(f"[BehaviorPack] {line}")
        return report

    def _load_pack_modules(self, pack_name: str, installed: str,
                           report: BehaviorLoadReport) -> None:
        source = resolve_behavior_source(
            os.path.join(installed, "behaviors"), pack_name)
        if source is None:
            report.errors.append(
                f"行为包缺失: {os.path.join(installed, 'behaviors', pack_name)}")
            return
        paths = _python_files(source) if os.path.isdir(source) else [source]
        for path in paths:
            name = os.path.splitext(os.path.basename(path))[0]
            module, error = _load_module(
                f"_world_behavior_{report.world_id}_{pack_name}_{name}", path)
            if module is None:
                report.errors.append(
                    f"行为模块加载失败: {path}（{error}）")
                continue
            report.modules_loaded += 1
            register = getattr(module, "register", None)
            if not callable(register):
                report.errors.append(
                    f"行为模块 '{path}' 未定义 register(runtime) 入口")
                continue
            try:
                register(self)
            except Exception as e:
                report.errors.append(f"行为模块 '{path}' 注册失败: {e}")
        if not report.overridden and not report.errors:
            report.warnings.append(
                f"行为包 '{pack_name}' 未注册任何覆盖，激活后无效果")


def _check_api_version(declared: Optional[int],
                       report: BehaviorLoadReport) -> None:
    """核对包声明的 behavior_api 与运行时契约版本，只警告不阻断。"""
    current = BEHAVIOR_API_VERSION
    if declared is None:
        report.warnings.append(
            f"包未声明 behavior_api（按 1 处理）；当前运行时契约为 {current}，"
            f"旧版 key 可能已被删除或改名，建议在 world.json 声明"
            f" \"behavior_api\": {current}")
    elif declared > current:
        report.warnings.append(
            f"包声明 behavior_api {declared}，高于当前运行时 {current}；"
            f"请升级程序后再启用本包")
    elif declared < current:
        report.warnings.append(
            f"包声明 behavior_api {declared}，低于当前运行时 {current}；"
            f"旧版 key 可能已被删除或改名，未知 key 会在激活时被拒绝并记入诊断")


def _python_files(root: str):
    """返回行为包目录内的 Python 模块，忽略缓存目录。"""
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for filename in filenames:
            if filename.endswith(".py"):
                files.append(os.path.join(dirpath, filename))
    return sorted(files)


def _load_module(module_name: str, path: str):
    """加载行为模块；返回 ``(module, error)``，失败时 module 为 None、error 为原因。"""
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        return None, "无法解析模块路径"
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"
    return module, ""


def _is_number(value: Any) -> bool:
    """数值判定：排除布尔（它是 int 的子类）。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _const_type_ok(default: Any, value: Any) -> bool:
    """常量类型判定：数值细度允许 int / float 互通，其余按默认值类型精确匹配。"""
    if _is_number(default) and _is_number(value):
        return True
    return isinstance(value, type(default))


def _validate_impl(spec: HookSpec, impl: Any) -> Tuple[bool, str]:
    """按细度校验覆盖实现；返回 ``(是否接受, 拒绝原因)``。"""
    if spec.kind == KIND_CONST:
        if callable(impl) or _const_type_ok(spec.default, impl):
            return True, ""
        return False, (f"需要 {type(spec.default).__name__} 值或返回该类型的"
                       f"可调用对象，得到 {type(impl).__name__}")
    if not callable(impl):
        return False, "覆盖实现必须是可调用对象"
    try:
        params = inspect.signature(impl).parameters
    except (TypeError, ValueError):
        return False, "无法读取覆盖实现的签名"
    has_kwargs = any(p.kind is p.VAR_KEYWORD for p in params.values())
    missing = [n for n in spec.param_names if n not in params and not has_kwargs]
    if missing:
        return False, (f"签名缺少默认实现要求的参数: {', '.join(missing)}"
                       f"（以 **kwargs 兜底亦可）")
    extra = [n for n, p in params.items()
             if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD,
                           p.KEYWORD_ONLY)
             and p.default is p.empty
             and n not in spec.param_names]
    if extra:
        return False, (f"签名多出默认实现不提供的必填参数: "
                       f"{', '.join(extra)}")
    return True, ""


def _named_params(func: Any) -> Tuple[str, ...]:
    """默认实现的具名参数（位置/关键字与仅关键字），用于覆盖签名校验。"""
    try:
        params = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return ()
    return tuple(n for n, p in params.items()
                 if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD,
                               p.KEYWORD_ONLY))


def _check_metadata(layer: str, contract: str, since: str) -> None:
    """注册时校验层级 / 契约组合，避免目录里出现语义含混的条目。"""
    if layer not in _LAYER_CONTRACTS:
        raise ValueError(
            f"未知的钩子层级: {layer!r}（可选 {', '.join(_LAYER_CONTRACTS)}）")
    allowed = _LAYER_CONTRACTS[layer]
    if contract not in allowed:
        raise ValueError(
            f"层级 {layer!r} 不支持契约 {contract!r}"
            f"（可选 {', '.join(sorted(allowed))}）")
    if not since:
        raise ValueError("since 不能为空")


# 进程内唯一运行时：服务静态方法钩子与 WorldManager 共用同一实例。
_runtime = BehaviorRuntime()


def get_runtime() -> BehaviorRuntime:
    """返回进程级行为运行时单例。"""
    return _runtime


def reset_runtime() -> None:
    """清空全部行为覆盖（例如停用世界包时）。"""
    _runtime.reset()


def behavior_hook(scope: str, name: str, *, layer: str = LAYER_INFRA,
                  contract: str = CONTRACT_POINT, since: str = _CATALOG_SINCE,
                  deprecated: bool = False) -> Callable:
    """把模块函数或静态方法包装为可被行为包覆盖的钩子（函数细度）。

    用法：对类静态方法在 ``@staticmethod`` 下方叠放；对模块函数直接叠放，例如::

        @staticmethod
        @behavior_hook("CreationService", "calculate_height")
        def calculate_height(...):
            ...

        @behavior_hook("logic", "compute_casualty")
        def compute_casualty(...):
            ...

    包装后的函数/方法会先查询运行时；存在覆盖实现时调用覆盖，否则回退默认实现。
    包装对象带有 ``__hook_key__``，因此行为包既可用 ``"logic.compute_casualty"``
    字符串注册，也可直接传被包装的函数对象。默认实现的具名参数在注册时记录，
    行为包覆盖时据此做签名校验。

    ``layer`` / ``contract`` 描述语义所有权：基础设施算法点默认
    ``infra/point``；模块决策策略显式传 ``module/strategy``；内容数据用
    ``behavior_const``。元数据会进入 ``runtime.catalog()``。
    """
    _check_metadata(layer, contract, since)
    key = f"{scope}.{name}"

    def decorator(func: Callable) -> Callable:
        _DEFAULTS[key] = HookSpec(
            key=key, default=func, kind=KIND_FUNCTION,
            param_names=_named_params(func), layer=layer, contract=contract,
            since=since, deprecated=deprecated)

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            impl = _runtime.resolve(key)
            if impl is not None:
                return impl(*args, **kwargs)
            return func(*args, **kwargs)

        wrapper.__hook_key__ = key
        return wrapper

    return decorator


def behavior_const(scope: str, name: str, value: Any, *,
                   layer: str = LAYER_CONTENT, contract: str = CONTRACT_DATA,
                   since: str = _CATALOG_SINCE, deprecated: bool = False) -> Any:
    """注册常量细度的可覆盖目标，原样返回 ``value`` 便于模块内继续使用::

        PREDEFINED_TAGS = behavior_const("logic", "PREDEFINED_TAGS", [...])

    行为包可覆盖为同类型值或返回该值的可调用对象；数值细度允许 int / float
    互通，可调用对象在每次 ``resolve_value`` 时重新求值（需要稳定值请在包内
    缓存）。key 是已部署行为包的公开契约，不得改名。

    常量默认按 ``content/data`` 登记；基础设施数值常量显式传
    ``layer="infra", contract="data"``。元数据会进入 ``runtime.catalog()``。
    """
    _check_metadata(layer, contract, since)
    key = f"{scope}.{name}"
    _DEFAULTS[key] = HookSpec(
        key=key, default=value, kind=KIND_CONST, layer=layer,
        contract=contract, since=since, deprecated=deprecated)
    return value
