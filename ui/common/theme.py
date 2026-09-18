"""统一界面配色加载器（语义化 token 版）。

配色数据集中在 ``assets/theme/``，一个文件即一套配色：``Slate.json``
（冷灰，默认）与 ``Classic.json``（旧暖棕），两者结构完全一致。

- ``primitives`` 原子色阶（slate/teal/emerald/...，唯一允许裸 hex 的地方）；
- ``roles`` 语义角色（bg-*/border-*/text-*/accent*/state-*/feature-*，
  亮暗成对 ``[light, dark]``，成员引用色阶）；
- ``vars`` 页面常量（对外接口），通过 ``{"$role": ...}`` / ``{"$pair": ...}``
  / ``{"$prim": ...}`` / ``{"$map": ...}`` 引用上层；内容调色板
  （preview/graph/intro/quip）刻意保留字面色值，不进语义层。

``vars`` 按界面模块分组，常量名前缀即所属模块：``NAV_`` 导航栏、
``CHALLENGE_`` 挑战模式、``PARAMS_DLG_`` 参数对话框……**一个常量只服务
一个面板或区域**，需要给某个区域单独调色时改它自己的常量即可，不会波及其它
界面；跨界面共用的语义色请改 ``roles`` 里的角色。用法：

``from ui.common.theme import CHALLENGE_PANEL_BG, CHALLENGE_TEXT``

本模块加载后把每个角色解析为 ``[light, dark]`` 列表、色阶引用解析为
hex 字符串，并按常量名暴露给各 UI 页面。改配色请编辑 JSON。

运行时换配色
------------
成对 token 解析为 **list 而不是 tuple**：customtkinter 的
``_apply_appearance_mode`` 对 tuple/list 一视同仁，而 list 可以就地改写。
``apply_palette()`` 把新配色的值**写回原有对象**（``list[:] = ...``、
``dict.update``），因此已经建好的控件持有的仍是同一对象，随后
``refresh_widgets()`` 触发一次全量重绘即可整体换色，无需重建界面、
无需重启。单色 token（``$prim``，如 ``LM_LINK``）不可变，只能在
``sys.modules`` 里替换各模块的同名全局量——新建的控件会用到新值，已建控件
需靠面板自身的 ``update_theme()`` 重新配置。
"""

import json
import os
import sys

# 运行时补丁：模式切换时同步刷新 CTk 控件的 Frame 底色，
# 避免主题切换后几何重排露出的缝隙闪现旧模式颜色（所有 UI 入口通用）。
import ui.common.ctk_patch  # noqa: E402,F401
from paths import assets_dir

#: 没有持久化配色记录时使用的默认配色。
DEFAULT_PALETTE = "Slate"

#: 配色显示名（设置页下拉框用）；未登记的配色直接显示文件名。
PALETTE_DISPLAY_NAMES = {
    "Slate": "Slate",
    "Classic": "Classic",
}

_MISSING = object()
# 当前配色导出的常量名集合，换配色时用于清理上一套配色独有的键
# （例如 Slate 的 SLATE 色阶在 Classic 里不存在）。
_PALETTE_KEYS = set()
_this_module = sys.modules[__name__]


def theme_dir():
    """配色文件目录 assets/theme/。"""
    return os.path.join(assets_dir(), "theme")


def available_palettes():
    """assets/theme/ 下可用的配色名（不含扩展名），至少包含默认配色。"""
    try:
        names = sorted(f[:-5] for f in os.listdir(theme_dir())
                       if f.endswith(".json") and not f.startswith("."))
    except OSError:
        names = []
    return names or [DEFAULT_PALETTE]


def palette_display_names():
    """{配色名: 显示名}，供设置页下拉框使用。"""
    return {name: PALETTE_DISPLAY_NAMES.get(name, name)
            for name in available_palettes()}


def current_palette():
    """当前生效的配色名。"""
    return globals().get("PALETTE_NAME", DEFAULT_PALETTE)


def _load_palette(name=DEFAULT_PALETTE):
    """读取并解析 assets/theme/<name>.json 为 {常量名: 值} 扁平字典。

    解析规则：色阶引用形如 "family.step"（字符串含点号即视为引用，
    引用不到会直接报错，便于检查拼写）；亮暗对还原为 **list**（可就地
    改写，见模块文档）；``//`` 开头的键是注释说明，加载时跳过。
    """
    path = os.path.join(theme_dir(), f"{name}.json")
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    primitives = doc["primitives"]
    roles = doc["roles"]

    def _ref(spec):
        fam, _, step = spec.partition(".")
        try:
            return primitives[fam][step]
        except KeyError:
            raise ValueError(f"未知的色阶引用 {spec!r}") from None

    def _pair(items):
        return [_ref(x) if isinstance(x, str) and "." in x else x for x in items]

    def _resolve(v):
        if isinstance(v, dict):
            if "$role" in v:
                return _pair(roles[v["$role"]])
            if "$pair" in v:
                return _pair(v["$pair"])
            if "$prim" in v:
                return _ref(v["$prim"])
            if "$map" in v:
                fam = primitives[v["$map"]]
                # 数字档位（如 SLATE 色阶）还原为 int 键
                return {int(k) if k.isdigit() else k: val for k, val in fam.items()}
            return {k: _resolve(x) for k, x in v.items() if not k.startswith("//")}
        if isinstance(v, list):
            return _pair(v)
        if isinstance(v, str) and "." in v:
            return _ref(v)
        return v

    return {name_: _resolve(v) for name_, v in doc["vars"].items()
            if not name_.startswith("//")}


def _merge(target, source):
    """把 source 的值就地写入 target，保持 target 的对象身份。

    返回 True 表示已就地更新；False 表示类型不可就地改写
    （例如字符串），调用方需要重新绑定各模块的全局名。
    """
    if isinstance(target, list) and isinstance(source, list):
        target[:] = source
        return True
    if isinstance(target, dict) and isinstance(source, dict):
        for key in list(target):
            if key not in source:
                del target[key]
        for key, value in source.items():
            current = target.get(key, _MISSING)
            if current is _MISSING or not _merge(current, value):
                target[key] = value
        return True
    return False


def _rebind_imports(name, old, new):
    """把 ``from ui.common.theme import name`` 得到的旧值换成新值。

    只替换「当前值就是旧对象」的模块（``is`` 判定），避免误伤其它模块里
    同名的无关全局量。
    """
    for module in list(sys.modules.values()):
        if module is None or module is _this_module:
            continue
        namespace = getattr(module, "__dict__", None)
        if namespace is None:
            continue
        try:
            if namespace.get(name, _MISSING) is old:
                namespace[name] = new
        except Exception:
            continue


def _drop_imports(name, old):
    """删除各模块中由本模块导入、且仍指向旧对象的同名全局量。"""
    for module in list(sys.modules.values()):
        if module is None or module is _this_module:
            continue
        namespace = getattr(module, "__dict__", None)
        if namespace is None:
            continue
        try:
            if namespace.get(name, _MISSING) is old:
                del namespace[name]
        except Exception:
            continue


def apply_palette(name):
    """切换到指定配色，就地更新本模块与所有导入方持有的常量。

    成对 token 原地改写（已建控件随之生效），单色 token 重新绑定到各模块
    的全局名（新建控件生效）。调用方随后应执行 ``refresh_widgets()``
    与各面板的 ``update_theme()``。
    """
    new_palette = _load_palette(name)
    current = globals()

    # 上一套配色的独有常量（如 Slate 的 SLATE 色阶）：从本模块与各导入方移除。
    for key in _PALETTE_KEYS - set(new_palette):
        old = current.pop(key, _MISSING)
        if old is not _MISSING:
            _drop_imports(key, old)

    for key, value in new_palette.items():
        old = current.get(key, _MISSING)
        if old is _MISSING:
            current[key] = value
            continue
        if not _merge(old, value):
            current[key] = value
            _rebind_imports(key, old, value)

    _PALETTE_KEYS.clear()
    _PALETTE_KEYS.update(new_palette)
    current["PALETTE_NAME"] = name
    globals()["__all__"] = sorted(_PALETTE_KEYS)
    return name


def refresh_widgets():
    """让所有 customtkinter 控件按当前 token 值重绘一次。

    明暗模式没变时 ``ctk.set_appearance_mode()`` 不会触发回调，这里直接
    调用（已被 ctk_patch 批量化处理的）``update_callbacks()``，逐个控件
    重走 ``_apply_appearance_mode``，从而拾取就地改写后的新颜色。
    """
    try:
        from customtkinter.windows.widgets.appearance_mode.appearance_mode_tracker import (
            AppearanceModeTracker,
        )
        AppearanceModeTracker.update_callbacks()
    except Exception:
        pass


palette = _load_palette(DEFAULT_PALETTE)
_PALETTE_KEYS.update(palette)
PALETTE_NAME = DEFAULT_PALETTE
globals().update(palette)

__all__ = sorted(_PALETTE_KEYS)
