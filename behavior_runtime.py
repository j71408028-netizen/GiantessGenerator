"""行为包运行时：在世界包激活期间接管服务静态方法 / 逻辑函数的实现。

行为包位于世界包部署目录的 ``behaviors/`` 下，模块名由 world.json 的
``resources.behaviors`` 列表声明。每个行为模块需定义 ``register(runtime)``
入口，在函数内通过 ``runtime.override(target, impl)`` 注册对可覆盖目标
（``CreationService`` / ``StateService`` / ``logic`` / ``ExplorationContext``）
的实现覆盖。

行为模块示例（behaviors/height_rules.py）：:

    from services.creation_service import CreationService

    def calculate_height(option, custom_val, min_slide, max_slide,
                         use_will, greed, rng, personality=None):
        # ...自定义身高算法...
        return 3.0, "within"

    def register(runtime):
        runtime.override(CreationService.calculate_height, calculate_height)

覆盖实现必须与被覆盖的静态方法保持相同签名与返回约定；如需复用默认实现，
可在覆盖函数内调用 ``runtime.default(target)(...)``。
"""

import functools
import importlib.util
import os
from typing import Any, Callable, Dict, Optional

_DEFAULTS: Dict[str, Callable] = {}


class BehaviorRuntime:
    """行为覆盖注册表：保存行为包对服务静态方法的覆盖实现。

    - ``override(target, impl)``：注册覆盖；target 为 ``"Service.method"``
      字符串，或对应静态方法对象。
    - ``resolve(key)``：查询覆盖实现；未覆盖时返回 None。
    - ``default(target)``：返回被覆盖前的默认实现，便于覆盖函数内部委托。
    - ``load_pack(manifest, installed)``：按清单加载行为包模块并注册。
    - ``reset()``：清空全部覆盖。
    """

    def __init__(self) -> None:
        self._overrides: Dict[str, Callable] = {}

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
        self._overrides[key] = impl

    def resolve(self, key: str) -> Optional[Callable]:
        return self._overrides.get(key)

    def default(self, target: Any) -> Callable:
        key = self._key_of(target)
        if key in _DEFAULTS:
            return _DEFAULTS[key]
        wrapped = getattr(target, "__wrapped__", None)
        return wrapped or target

    def reset(self) -> None:
        self._overrides.clear()

    # ---------- 从世界包加载 ----------

    def load_pack(self, manifest, installed: str) -> None:
        """清空现有覆盖，并按清单声明加载行为包模块。"""
        self.reset()
        names = manifest.resources.get("behaviors") or []
        if not names:
            return
        root = os.path.join(installed, "behaviors")
        world_id = manifest.world_id or "unknown"
        for name in names:
            path = os.path.join(root, f"{name}.py")
            if not os.path.isfile(path):
                print(f"[BehaviorPack] 行为包模块缺失: {path}")
                continue
            module = _load_module(f"_world_behavior_{world_id}_{name}", path)
            if module is None:
                continue
            register = getattr(module, "register", None)
            if not callable(register):
                print(f"[BehaviorPack] 行为包 '{name}' 未定义 register(runtime) 入口")
                continue
            try:
                register(self)
            except Exception as e:
                print(f"[BehaviorPack] 行为包 '{name}' 注册失败: {e}")


def _load_module(module_name: str, path: str):
    """通过 importlib 加载行为包模块；失败时打印警告并返回 None。"""
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        print(f"[BehaviorPack] 无法解析行为包模块: {path}")
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        print(f"[BehaviorPack] 行为包 '{path}' 加载失败: {e}")
        return None
    return module


# 进程内唯一运行时：服务静态方法钩子与 WorldManager 共用同一实例。
_runtime = BehaviorRuntime()


def get_runtime() -> BehaviorRuntime:
    """返回进程级行为运行时单例。"""
    return _runtime


def reset_runtime() -> None:
    """清空全部行为覆盖（例如停用世界包时）。"""
    _runtime.reset()


def behavior_hook(scope: str, name: str) -> Callable:
    """把模块函数或静态方法包装为可被行为包覆盖的钩子。

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
    字符串注册，也可直接传被包装的函数对象。
    """
    key = f"{scope}.{name}"

    def decorator(func: Callable) -> Callable:
        _DEFAULTS[key] = func

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            impl = _runtime.resolve(key)
            if impl is not None:
                return impl(*args, **kwargs)
            return func(*args, **kwargs)

        wrapper.__hook_key__ = key
        return wrapper

    return decorator
