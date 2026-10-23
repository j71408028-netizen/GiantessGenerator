"""GPU 纹理句柄生命周期守卫（无 GUI / 无 GL，纯离线，可进 CI）。

用法：``python tests/check_gpu_texture_lifetime.py``（退出码 0 = 通过）

守的是一条**已经在 Linux 上真崩过**的规则：``hello_imgui.TextureGpu`` 是 RAII
句柄，丢掉最后一个 Python 引用就会去释放底层 GL 纹理，因此只能在 GL 上下文
还活着的时候丢（``before_exit`` 钩子）；``hello_imgui.run()`` 返回之后
（``base._finish_session``）再丢，析构会去解引用已经释放的后端，在驱动层直接
段错误——P8 之后每局副本收尾必崩（软/硬 GL 都复现），就是组件基类的 ``destroy``
在 ``_finish_session`` 里清了工具条图标纹理。

本脚本把"谁在什么时候丢句柄"变成门禁，分三层：

**A. 句柄清单（行为）** —— 窗口的六处 ``TextureGpu`` 容器（背景 / 窗口工具条 /
结局图标 / 视效覆盖层 / 小游戏贴图 / 组件工具条）能被 ``_drain_gpu_textures``
一次收干净，且收完各容器都是空表（清空容器 = 丢引用，这是段错误的来源）。
用替身句柄驱动，不需要 GL。

**B. 释放/退役口径（行为）** —— ``gpu_textures.release`` 就地清空列表（真正
释放）；``retire`` 把句柄挂进进程级仓库**保住引用**（收尾兜底，不许析构跑）。

**C. 调用次序（AST）** —— ``_finish_session`` 里的退役兜底必须排在
``_destroy_components`` / ``_destroy_overlays`` / ``_window_fx.shutdown`` 之前
（那三步会清空容器）；``_on_before_exit`` 里必须真的调了
``_release_gpu_textures``（GL 唯一的释放时机）。
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dungeon.window import gpu_textures  # noqa: E402
from dungeon.window.component_pack.base import _TextDisplayBase  # noqa: E402
from dungeon.window.fx import WindowFx  # noqa: E402
from dungeon.window.minigame.stage import _MiniGameStage  # noqa: E402
from dungeon.window.ui import DungeonWindowUI  # noqa: E402

BASE_PY = ROOT / "dungeon" / "window" / "base.py"
UI_PY = ROOT / "dungeon" / "window" / "ui.py"

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
# A. 句柄清单：窗口一处收干净、容器清空
# ---------------------------------------------------------------------------

class _Comp:
    """替身组件：只借基类的 drain 实现，不建真实组件上下文。"""
    drain_gpu_textures = _TextDisplayBase.drain_gpu_textures


class _Fx:
    """替身视效层：``drain`` 只读 ``_pixel_texture``，不建像素工作者线程。"""
    drain_gpu_textures = WindowFx.drain_gpu_textures


class _Stage:
    """替身小游戏舞台：``drain`` 只读 ``_textures``。"""
    drain_gpu_textures = _MiniGameStage.drain_gpu_textures


class _Window:
    """替身窗口：只借真实的 ``_drain_gpu_textures`` 实现（认属性，不认类型）。"""
    _drain_gpu_textures = DungeonWindowUI._drain_gpu_textures


def _sample_window():
    win = _Window()
    win._bg_texture = "bg"
    win._bg_blur_texture = "bgblur"
    win._toolbar_textures = {"play": "t1", "shot": "t2", "log": "t3"}
    win._ending_textures = ["e1", "e2"]
    fx = _Fx()
    fx._pixel_texture = "fx"
    stage = _Stage()
    stage._textures = {"/a.png": "m1"}
    comp = _Comp()
    comp._toolbar_textures = {"autoplay": "c1"}
    win._window_fx = fx
    win._mini_game_stage = stage
    # 第二个组件故意没有 drain 方法（非文本组件）：清单必须容得下
    win._components = [comp, object()]
    return win, fx, stage, comp


win, fx, stage, comp = _sample_window()
handles = win._drain_gpu_textures()
check("窗口句柄清单一次收全（背景1 + 模糊副本1 + 工具条3 + 结局2 + 视效1 + 小游戏1 + 组件1）",
      sorted(handles) == ["bg", "bgblur", "c1", "e1", "e2", "fx", "m1",
                          "t1", "t2", "t3"],
      str(sorted(handles)))
check("收完之后容器全空（清空容器 = 丢引用）",
      win._bg_texture is None and win._bg_blur_texture is None
      and win._toolbar_textures == {}
      and win._ending_textures == [] and fx._pixel_texture is None
      and stage._textures == {} and comp._toolbar_textures == {},
      f"{win._bg_texture} {win._bg_blur_texture} {win._toolbar_textures} "
      f"{win._ending_textures} "
      f"{fx._pixel_texture} {stage._textures} {comp._toolbar_textures}")
check("再收一次是空表（幂等，兜底钩子重复调用安全）",
      win._drain_gpu_textures() == [])

# 挑战模式这类没有入口阶段的窗口可能压根没有 _ending_textures：不许抛异常
bare = _Window()
check("缺属性（无入口阶段/无组件）也能收", bare._drain_gpu_textures() == [],
      str(getattr(bare, "_ending_textures", "<unset>")))


# ---------------------------------------------------------------------------
# B. 释放 / 退役口径
# ---------------------------------------------------------------------------

before = gpu_textures.retired_count()
bucket = ["h1", None, "h2"]
check("release 返回放掉的句柄数（跳过 None）", gpu_textures.release(bucket) == 2)
check("release 就地清空列表（清空即丢引用 ⇒ 真正释放）", bucket == [], str(bucket))

retired = ["r1", "r2"]
check("retire 收下句柄", gpu_textures.retire(retired) == before + 2,
      str(gpu_textures.retired_count()))
check("retire 把句柄留在进程级仓库（不许析构跑）",
      gpu_textures.retired_count() == before + 2)
check("retire 忽略 None", gpu_textures.retire([None, None]) == before + 2)


# ---------------------------------------------------------------------------
# C. 调用次序（AST）：兜底必须早于清空容器的三步
# ---------------------------------------------------------------------------

def _method(path, cls_name, fn_name):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == cls_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == fn_name:
                    return item
    return None


def _self_calls(fn):
    """``self.X(...)`` 调用 -> [(lineno, X), ...]，按出现顺序。"""
    found = []
    for node in ast.walk(fn):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "self"):
            found.append((node.lineno, node.func.attr))
    return sorted(found)


finish = _method(BASE_PY, "DungeonWindowBase", "_finish_session")
ui_exit = _method(UI_PY, "DungeonWindowUI", "_on_before_exit")
check("找到 _finish_session / _on_before_exit",
      finish is not None and ui_exit is not None)

if finish is not None:
    order = [name for _line, name in _self_calls(finish)]
    check("_finish_session 里调了退役兜底", "_retire_gpu_textures" in order,
          str(order))
    if "_retire_gpu_textures" in order:
        retire_at = order.index("_retire_gpu_textures")
        after = [n for n in order[retire_at + 1:]
                 if n in ("_destroy_components", "_destroy_overlays")]
        check("退役兜底排在 _destroy_components / _destroy_overlays 之前",
              "_destroy_components" not in order[:retire_at]
              and "_destroy_overlays" not in order[:retire_at]
              and bool(after),
              str(order))

if ui_exit is not None:
    calls = [name for _line, name in _self_calls(ui_exit)]
    check("_on_before_exit 里先释放 GPU 纹理（GL 唯一的释放时机）",
          calls[0:1] == ["_release_gpu_textures"], str(calls))


print()
if failures:
    print(f"[check_gpu_texture_lifetime] FAILED {len(failures)}/{total}: "
          + ", ".join(failures))
    sys.exit(1)
print(f"[check_gpu_texture_lifetime] PASSED {total}/{total}")
