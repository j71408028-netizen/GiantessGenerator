# -*- coding: utf-8 -*-
"""点击反应：限时内点中足够数量的目标光点（py 后端小游戏参考实现）。

演示 MiniGame 契约的完整闭环：setup 读参数 → update 里轮询输入、发绘制指令 →
api.finish 回传结果（命中数即整数返回值，进触发器选择记录与回放记录）。
"""

import math
import random

from dungeon.window.minigame import MiniGame

_BACKGROUND = (24, 28, 40, 255)
_TARGET_COLOR = (120, 220, 160, 255)
_TARGET_EDGE = (235, 255, 240, 255)
_TEXT_COLOR = (235, 235, 240, 255)
_DIM_COLOR = (150, 155, 170, 255)
_TIME_LIMIT = 20.0
_TARGET_LIFETIME = 2.2
_TARGET_RADIUS = 26.0
_SPAWN_INTERVAL = 0.9


class ReactionGame(MiniGame):
    id = "reaction"
    label = "点击反应"
    description = "限时内点中目标数量的光点；点到空处记失误。命中数计入结果。"
    params = (
        {"key": "target_level", "label": "目标命中数", "type": "int",
         "default": 5, "min": 1, "max": 30},
    )

    def setup(self, config):
        try:
            self._need = max(1, int(float(config.get("target_level", 5) or 5)))
        except (TypeError, ValueError):
            self._need = 5
        self._elapsed = 0.0
        self._hits = 0
        self._misses = 0
        self._target = None        # (x, y, 诞生时刻)
        self._since_spawn = 0.0
        self._prev_mouse = False

    def update(self, dt):
        api = self.api
        self._elapsed += dt

        # 结算判定：命中数即整数返回值（level），进触发器的选择记录
        if self._hits >= self._need:
            api.finish({"level": self._hits, "hits": self._hits,
                        "need": self._need,
                        "time": round(self._elapsed, 2),
                        "misses": self._misses})
            return
        remaining = _TIME_LIMIT - self._elapsed
        if remaining <= 0:
            api.finish({"level": self._hits, "hits": self._hits,
                        "need": self._need,
                        "time": _TIME_LIMIT, "misses": self._misses})
            return

        # 目标生成与过期
        if self._target is None:
            self._since_spawn += dt
            if self._since_spawn >= _SPAWN_INTERVAL:
                margin = _TARGET_RADIUS + 10
                w, h = api.width, api.height
                self._target = (random.uniform(margin, max(margin + 1, w - margin)),
                                random.uniform(margin + 40, max(margin + 41, h - margin)),
                                self._elapsed)
                self._since_spawn = 0.0
        else:
            _x, _y, born = self._target
            if self._elapsed - born >= _TARGET_LIFETIME:
                self._target = None

        # 点击判定（按下沿触发一次）
        mouse = api.mouse_down(0)
        if mouse and not self._prev_mouse and self._target is not None:
            mx, my = api.mouse_pos()
            x, y, _born = self._target
            if math.hypot(mx - x, my - y) <= _TARGET_RADIUS:
                self._hits += 1
                self._target = None
            else:
                self._misses += 1
        self._prev_mouse = mouse

        self._draw(remaining)

    def _draw(self, remaining):
        api = self.api
        w, h = api.width, api.height
        api.draw_rect((0, 0), (w, h), _BACKGROUND)
        if self._target is not None:
            x, y, born = self._target
            age = self._elapsed - born
            r = _TARGET_RADIUS * (1.0 - 0.25 * (age / _TARGET_LIFETIME))
            api.draw_circle((x, y), r, _TARGET_COLOR)
            api.draw_circle((x, y), r, _TARGET_EDGE, fill=False, thickness=2.0)
        api.hud(f"命中 {self._hits}/{self._need} · 剩余 {max(0.0, remaining):.1f}s"
                + (f" · 失误 {self._misses}" if self._misses else ""))
        api.draw_text("点击亮起的光点（ESC 中止）",
                      (16, h - 34), _DIM_COLOR, size=15)
        api.draw_text(f"{max(0.0, remaining):.1f}", (w - 90, 14),
                      _TEXT_COLOR, size=30)
