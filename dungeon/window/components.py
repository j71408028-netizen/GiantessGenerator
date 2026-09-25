"""副本显示组件生命周期（混入）。

组件包由 dungeon.window.component_registry 提供注册表；本 mixin 负责按副本配置
（``text_component`` 三选一 + ``components`` 列表）在会话阶段构建组件实例，并把
这些实例接入窗口现有的更新链：
- _relayout() → 各组件 layout(ctx)
- _update_text_display() → 有组件声明 ``owns_text_display`` 时转调各组件
  refresh(ctx)（如底部渐变式文本栏接管显示），否则走内置 text_container 管线
- 会话退出 _handle_exit() 前 → 各组件 destroy(ctx) + 注册表丢弃

组件的根对象 ctx 即窗口实例本身，组件只读窗口现有状态，不反向写状态。
"""

import traceback

import dearpygui.dearpygui as dpg

from dungeon import process_log
from dungeon.window import component_registry as _components_module


class ComponentHandler:
    def _init_components(self):
        """初始化组件实例列表与默认布局样式（在 _load_session_config 后调用）。"""
        self._components = []
        # 旧的「副本窗口视图」已移除，布局样式固定为保留全历史的故事布局
        self.layout_style = "story"
        self._components_built = False

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
        """按配置构建组件实例（主线程，会话阶段进入时调用）。"""
        self._components = []
        if getattr(self, "_closing", False):
            return
        try:
            self._components = _components_module.build_components(
                self, self._configured_component_ids(), self.scenario_config)
        except Exception as exc:
            process_log.log(f"[Components] 构建组件失败: {exc}\n{traceback.format_exc()}")
            self._components = []
        for comp in self._components:
            try:
                comp.build(self)
            except Exception as exc:
                process_log.log(f"[Components] 组件 build 失败 ({getattr(comp, 'id', '?')}): {exc}")
        self._components_built = True

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

    # ---- 供 UI 层调用的几何布局委托（保持单一入口） ----
    def _layout_text_container(self, style):
        """按布局样式重排文本容器。"""
        w = getattr(self, "_layout_w", 0) or dpg.get_viewport_client_width()
        h = getattr(self, "_layout_h", 0) or dpg.get_viewport_client_height()
        margin_x = round(40 * getattr(self, "_dpi_scale", 1.0))
        margin_top = round(40 * getattr(self, "_dpi_scale", 1.0))
        margin_bottom = round(20 * getattr(self, "_dpi_scale", 1.0))
        cw = max(1, w - 2 * margin_x)
        if style == "story":
            cpos, ch = [margin_x, margin_top], h - margin_top - margin_bottom
        else:  # game / bottom
            ch = round(190 * getattr(self, "_dpi_scale", 1.0))
            cpos, ch = [margin_x, h - ch - margin_bottom], ch

        if dpg.does_item_exist("text_container"):
            dpg.configure_item("text_container", pos=cpos, width=cw, height=ch)
        if dpg.does_item_exist("bg_overlay_child"):
            dpg.configure_item("bg_overlay_child", pos=cpos, width=cw,
                               height=max(1, h - cpos[1]))
        self._text_wrap_width = max(1, cw - round(40 * getattr(self, "_dpi_scale", 1.0)))
        for tag in self._text_item_tags:
            if dpg.does_item_exist(tag):
                dpg.configure_item(tag, wrap=self._text_wrap_width)


__all__ = ["ComponentHandler"]
