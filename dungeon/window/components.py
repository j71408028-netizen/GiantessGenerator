"""副本显示组件生命周期（混入）。

组件包由 dungeon.window.component_registry 提供注册表；本 mixin 负责按副本配置
（``text_component`` 三选一 + ``components`` 列表）在会话阶段构建组件实例，并把
这些实例接入窗口现有的更新链：
- _relayout() → 各组件 layout(ctx)
- _update_text_display() → 有组件声明 ``owns_text_display`` 时转调各组件
  refresh(ctx)（如底部渐变式文本栏接管显示），否则走内置 story 管线
- 会话退出 _handle_exit() 前 → 各组件 destroy(ctx) + 注册表丢弃

组件的根对象 ctx 即窗口实例；组件**只允许**经本 mixin 提供的组件服务面访问窗口
（`component_viewport / component_top_inset / session_waiting_for_input /
component_autoplay_on / text_font_tag / bold_font_tag / schedule / schedule_every /
cancel_task / component`，另有覆盖层与工具服务的 `toggle_overlay` 等）。
组件不得再读窗口私有属性（`_dpi_scale` / `_layout_w` / `_frame` / `_autoplay`…）；
契约由 ``tests/check_component_pack.py`` 的替身 ctx 强制——替身只实现服务面，
组件一旦伸手摸私有一律在守卫里报错。

## 迁移状态（P1）

``component_pack/*`` 仍是 DPG 实现（P4 迁移）。P1 的 ``_build_components`` 因此
是 **P4 占位**：不构建任何组件、``_components`` 保持空表，文本显示一律走
``ui._draw_story`` 内置管线。服务面方法（``component_viewport`` /
``text_font_tag`` / ``session_waiting_for_input`` …）已改为不碰 DPG，P4 接入
imgui 组件时它们仍是组件的唯一入口。
"""

from dungeon import process_log
from dungeon.window import component_registry as _components_module


class ComponentHandler:
    def _init_components(self):
        """初始化组件实例列表与默认布局样式（在 _load_session_config 后调用）。"""
        self._components = []
        self._components_built = False
        # 自动播放开关（ui.toggle_autoplay 切换；帧任务随会话收尾一并丢弃）
        self._autoplay = False

    def _configured_component_ids(self):
        """把副本配置解析成组件 id 列表（首位恒为文本主组件）。

        文本主组件取 ``text_component`` 字段（三选一，见 dungeon.schema）；
        未写该字段的旧配置从 ``components`` 列表里的家族成员提升（读侧兼容，
        落盘归一由 scenario_repo._migrate 承担）。其余组件按配置顺序跟在
        主组件之后——主组件先建、z 序在底。
        """
        from dungeon.schema import (DEFAULT_TEXT_COMPONENT,
                                    normalize_text_component, TEXT_COMPONENT_IDS)
        config = getattr(self, "scenario_config", None) or {}
        text_cid = normalize_text_component(config.get("text_component"))
        ids = config.get("components")
        others = []
        for cid in (ids if isinstance(ids, (list, tuple)) else []):
            cid = str(cid).strip()
            if not cid:
                continue
            if cid in TEXT_COMPONENT_IDS:
                # 旧写法残留：text_component 缺失时以列表里的家族成员为准
                if "text_component" not in config:
                    text_cid = normalize_text_component(cid)
                continue
            others.append(cid)
        return [text_cid or DEFAULT_TEXT_COMPONENT] + others

    def _build_components(self):
        """按配置构建组件实例（主线程，会话阶段进入时调用）。

        **P4 占位**：``component_pack/*`` 还是 DPG 实现，P1 不构建任何组件——
        ``_components`` 保持空表，``_components_built`` 保持假，文本显示因此走
        ``ui._draw_story`` 内置管线。P4 会把 ``build_components`` 接回来。
        """
        self._components = []
        self._components_built = False
        process_log.log("[Components] P1：显示组件待 P4 迁移，本次会话走内置文本区")

    def _relayout_components(self):
        """窗口重排后调用各组件 layout（主线程）。"""
        for comp in self._components:
            try:
                comp.layout(self)
            except Exception as exc:
                process_log.log(f"[Components] 组件 layout 失败 ({getattr(comp, 'id', '?')}): {exc}")

    def _refresh_components(self):
        """状态更新后调用各组件 refresh（主线程，经调度器）。"""
        for comp in self._components:
            try:
                comp.refresh(self)
            except Exception as exc:
                process_log.log(f"[Components] 组件 refresh 失败 ({getattr(comp, 'id', '?')}): {exc}")

    def _destroy_components(self):
        """会话退出时清理组件（主线程，_handle_exit 前调用）。"""
        for comp in self._components:
            try:
                comp.destroy(self)
            except Exception as exc:
                process_log.log(f"[Components] 组件 destroy 失败 ({getattr(comp, 'id', '?')}): {exc}")
        self._components = []
        self._components_built = False
        try:
            _components_module.get_registry().discard(self)
        except Exception:
            pass

    # ---- 组件服务面（组件访问窗口的唯一入口，见 DungeonComponent 契约） ----
    def component_viewport(self):
        """返回 ``(dpi_scale, 布局宽, 布局高)``：组件的唯一几何来源。

        立即模式下窗口尺寸由 ``ui._draw_frame`` 每帧从 ``io.display_size`` 现算，
        缓存到 ``_layout_w/_layout_h``，因此这里直接读缓存即可（不再回头问后端）。
        """
        s = getattr(self, "_dpi_scale", 1.0) or 1.0
        w = getattr(self, "_layout_w", 0) or 1.0
        h = getattr(self, "_layout_h", 0) or 1.0
        return s, w, h

    def component_top_inset(self):
        """返回其它组件已占用的顶部高度（全屏类组件据此让位）。

        各组件用可选的 ``top_inset(ctx)`` 钩子自报占位，组件之间不互读私有几何，
        因此与构建顺序无关（主组件先建时也能算出后面属性面板的占位）。
        """
        inset = 0
        for comp in getattr(self, "_components", []):
            hook = getattr(comp, "top_inset", None)
            if not callable(hook):
                continue
            try:
                inset = max(inset, int(hook(self) or 0))
            except Exception as exc:
                process_log.log(f"[Components] 组件 top_inset 失败 "
                                f"({getattr(comp, 'id', '?')}): {exc}")
        return inset

    def session_waiting_for_input(self):
        """当前是否在等玩家输入（继续指示与自动播放共用同一判定）。

        生成中 / 上屏游标进行中 / 结局已达成 / 有待选选项或待触发结局都不算等待。
        """
        return (not getattr(self, "_generating", False)
                and not self._reveal_active()
                and not getattr(self, "_pending_units", None)
                and not getattr(self, "dungeon_ended", False)
                and getattr(self, "pending_option", None) is None
                and getattr(self, "pending_ending", None) is None)

    def component_autoplay_on(self):
        """自动播放开关是否打开（工具条播放按钮着色用）。"""
        return bool(getattr(self, "_autoplay", False))

    def text_font_tag(self):
        """窗口创建的正文（``TEXT_FONT_SIZE``）字体对象（组件绑正文用；未建时为 None）。

        imgui 的 ``push_font(font, size)`` 收字体句柄而不是 DPG 的 tag，因此这里
        直接返回 ``post_init`` 里 ``hello_imgui.load_font`` 拿到的对象。
        """
        return getattr(self, "dungeon_text_font", None)

    def bold_font_tag(self):
        """窗口创建的粗体字体对象（可能为 None：解析不到粗体文件时回退正文）。"""
        return getattr(self, "dungeon_bold_font", None)

    # ---- 帧时钟（组件不得直接触碰 self._frame） ----
    def schedule(self, fn, *args):
        """把一次调用投递到主线程帧泵（跨线程回主线程的唯一入口）。"""
        return self._frame.call(fn, *args)

    def schedule_every(self, interval, fn, key):
        """登记周期帧任务（同 key 互斥覆盖）。"""
        self._frame.every(interval, fn, key=key)

    def cancel_task(self, key):
        """取消帧任务（幂等）。"""
        self._frame.cancel(key)


__all__ = ["ComponentHandler"]
