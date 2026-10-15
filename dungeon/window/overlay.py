"""窗口级覆盖层服务：文本主组件（及键位）可调出的模态浮层。

服务面（组件经 ctx 调用，见 component_registry.DungeonComponent 契约）：
- ``ctx.toggle_overlay(name)``  调出/关闭覆盖层（当前支持 ``"log"`` 对话记录）
- ``ctx.close_overlay()``       关闭当前覆盖层
- ``ctx.overlay_open()``        查询当前覆盖层名（None = 无覆盖层）
- ``ctx.component(cid)``        只读访问兄弟组件实例（不接管其生命周期）

覆盖层打开期间窗口进入「阅读模态」：点击只关闭覆盖层、不推进剧情，空格 /
回车的推进也一并挂起（`_on_next_step` 入口判定；H / ESC 随时可用）。

线程约束：全部方法只在帧线程调用。

## 迁移状态（P1）

渲染实现（``_build_log_overlay`` / ``_relayout_overlays``）是 **P4 占位**：
imgui 立即模式下"覆盖层"就是 ``ui._draw_frame`` 里多画一个面板，不需要建控件树。
P1 保留**状态与挂起语义**——``overlay_open()`` / ``toggle_overlay()`` /
``close_overlay()`` 照常维护 ``_overlay_name``，因此阅读模态在 P1 就已生效；
只是暂时还没有面板可见（P4 补 ``ui`` 侧的绘制）。
"""

from dungeon import process_log

_HIGHLIGHT_COLOR = (255, 200, 60, 255)
_LABEL_COLOR = (255, 170, 210, 255)
_TEXT_COLOR = (240, 240, 245, 255)
_DIM_COLOR = (150, 155, 170, 255)


class OverlayHandler:
    """覆盖层服务：模态浮层的构建、挂起语义与生命周期。"""

    # ---- 服务面（组件经 ctx 调用） ----
    def overlay_open(self):
        """当前打开的覆盖层名；None 表示无覆盖层。

        小游戏舞台（``dungeon.window.minigame``）存续期间返回 ``"minigame"``：
        阅读模态挂起步进与自动播放，但点击**不**触发关闭（鼠标是游戏输入）。"""
        if getattr(self, "_mini_game_stage", None) is not None:
            return "minigame"
        return getattr(self, "_overlay_name", None)

    def toggle_overlay(self, name="log"):
        """调出 / 关闭指定覆盖层（同名校正：打开时再次调用即关闭）。"""
        if self.overlay_open() == name:
            self.close_overlay()
            return
        self.close_overlay()
        if name == "log":
            self._build_log_overlay()
            self._overlay_name = name
        else:
            self._notify(f"未知覆盖层：{name}")

    def close_overlay(self):
        """关闭当前覆盖层（未打开时为无操作）。"""
        name = getattr(self, "_overlay_name", None)
        if not name:
            return
        self._overlay_name = None

    def component(self, cid):
        """只读访问兄弟组件实例（未知或未启用时返回 None）。

        仅用于读取参数与展示状态；组件生命周期（build/layout/destroy）仍由
        窗口集中管理，调用方不得自行构建或销毁组件。
        """
        for comp in getattr(self, "_components", []):
            if comp.id == cid:
                return comp
        return None

    # ---- 键位（ui._build_ui 注册） ----
    def _on_log_key(self, sender=None, app_data=None):
        """H 键：调出 / 关闭对话记录（入口阶段忽略）。"""
        if getattr(self, "_is_entry_phase", False) or self._closing:
            return
        self.toggle_overlay("log")

    def _on_escape_key(self, sender=None, app_data=None):
        """ESC 键：中止小游戏（无结果收场），否则关闭已打开的覆盖层。"""
        if getattr(self, "_mini_game_stage", None) is not None:
            self._minigame_escape()
            return
        if self.overlay_open():
            self.close_overlay()

    # ---- 覆盖层实现 ----
    # ---- 覆盖层实现（P4 占位） ----
    def _build_log_overlay(self):
        """P4 占位：对话记录面板改由 ``ui._draw_frame`` 立即模式绘制。

        原实现以 ``overlay_log`` 子窗 + 主题建控件树并快照 ``story_history``；
        imgui 立即模式下没有"建一次"这回事——P4 会在 ``ui`` 里按 ``_overlay_name``
        多画一个居中面板，每帧从 ``story_history`` 现画。P1 只需维护状态标记。
        """

    def _relayout_overlays(self):
        """P4 占位：立即模式覆盖层几何每帧现算，无需重排。"""

    def _destroy_overlays(self):
        """会话收尾时清理覆盖层模态标记（控件由立即模式自然消失）。"""
        self._overlay_name = None
        # 小游戏舞台同批清理（帧时钟已停，其中的结果回传自然失效）
        if getattr(self, "_mini_game_stage", None) is not None:
            self._destroy_mini_game_stage()

