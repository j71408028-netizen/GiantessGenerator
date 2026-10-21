"""官方副本显示组件包：组装入口（REGISTRY 单一出口）。

位于 ``dungeon/window/component_pack/``（窗口层常驻 Python 包，随应用分发），
由 ``dungeon.window.component_registry`` 惰性导入（外部包经显式 ``pack_dir`` 覆盖）。
包内按组件拆分：

- ``base.py``          共享常量、工具条图标、文本组件公共基类 ``_TextDisplayBase``
- ``text_gradient.py`` ``text``       底部渐变式文本栏
- ``text_card.py``     ``text_card``  底部卡片式文本栏
- ``text_nvl.py``      ``text_nvl``   全屏 NVL 文本栏
- ``attr_bar.py``      ``attr_bar``   属性状态条
- ``proc_log.py``      ``proc_log``   过程日志面板

组件类实现 build/layout/draw/destroy 生命周期钩子，ctx 即副本会话窗口
实例（dungeon.window.DungeonSessionWindow 的 mixin 组合）。

文本组件三选一（方案配置 ``text_component`` 字段声明，主组件层）：
- ``text``      底部渐变式：视口底部向上淡出的深色衬底上显示最近几句（ADV 风格）；
- ``text_card`` 底部卡片式：底部居中的圆角半透明卡片，浮现最近几句；
- ``text_nvl``  全屏 NVL 式：全屏半透明覆盖层上堆叠全部历史（阅读模式）。

三者都在 ``layout`` 阶段声明代管区（``dungeon.window.layout.Slot``）：
``text_card`` 提供卡片底部状态带、``text`` 提供渐变区状态带、``text_nvl``
提供顶部状态带；``attr_bar`` 声明 ``roles=("status",)`` 优先落入，没有可用
代管区时回退左上角独立面板。``text_card`` 与 ``text`` 还各提供一个贴视口
一侧的代管区（``card_side`` / ``scrim_side``，``log`` / ``rail``）：
``proc_log`` 仅在展开时声明 ``log`` 角色，收起即释放。停靠哪一侧是**窗口视觉
设置**（`dungeon_settings.json` 的 ``dock_side``，玩家在副本设置面板里实时改，
组件经 ``ctx.component_dock_side()`` 读取），不是组件方案参数。入场呈现随宿主
不同：卡片侧边按 ``SlotStyle.side`` 从
所贴的屏幕边缘平移进入、卡片同时往另一侧让位；渐变正文是全宽的，侧边代管区
停在正文上方（不做横向让位），组件按 ``SlotStyle.enter_from="bottom"`` +
``enter_fade`` 从下方升起并淡入。

三者都声明 ``owns_text_display = True`` 接管窗口文本显示（内置 text_container
管线被 _update_text_display 的所有权守卫跳过）；仿流式动画仍由窗口帧时钟驱动
（item["text"] 就地增长），组件 refresh 只读取最新值。完整历史一律保留在回放
与报告中，屏幕呈现因组件而异。

三者共享一条悬浮功能工具条（自动播放 / 截图 / 日志面板，基类 _build_toolbar
统一创建，各组件只负责按自己的几何定位；功能按钮常驻显示，不再提供开关）。
按钮只转调窗口同名服务（toggle_autoplay / take_screenshot / toggle_proc_log），
与 A / F2 / F12 快捷键等效；窗口的 _on_mouse_click 靠 toolbar_hovered()
区分「点按钮」与「点屏幕推进剧情」。

线程约束：所有钩子都只由主线程调用；组件内不得在后台线程操作 imgui，
需要回主线程的更新走 ctx.schedule / schedule_every。
"""

from .attr_bar import AttrBarComponent
from .proc_log import ProcLogComponent
from .text_card import CardTextComponent
from .text_gradient import TextComponent
from .text_nvl import NvlTextComponent

REGISTRY = {
    TextComponent.id: TextComponent,
    CardTextComponent.id: CardTextComponent,
    NvlTextComponent.id: NvlTextComponent,
    AttrBarComponent.id: AttrBarComponent,
    ProcLogComponent.id: ProcLogComponent,
}

__all__ = ["REGISTRY", "TextComponent", "CardTextComponent", "NvlTextComponent",
           "AttrBarComponent", "ProcLogComponent"]
