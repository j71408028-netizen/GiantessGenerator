# -*- coding: utf-8 -*-
"""临时无头模拟：escape_giantess py 版逻辑链回归（无 GUI、无 DPG 上下文）。

- 连续生成 N 关（两主题都会出现），校验出生点/出口/连通性约束；
- 用桩 API 实跑 update()（随机走位 + 投弹），断言不抛异常、能正常结算。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "data", "packs", "minigames", "escape_giantess"))

import game as g  # noqa: E402
from dungeon.window.minigame.stage import _PILCanvas  # noqa: E402


class StubApi:
    """headless 桩：offscreen 真画（PIL），draw_* 全 no-op，按键可注入。"""

    def __init__(self):
        self.keys = set()
        self.finished = None
        self.hud_text = ""

    width = 1280
    height = 800

    def key_down(self, key):
        return key in self.keys

    def mouse_pos(self):
        return (0, 0)

    def mouse_down(self, button=0):
        return False

    def finish(self, won, result=None):
        if self.finished is None:
            self.finished = dict(result or {"won": bool(won)})

    def hud(self, text):
        self.hud_text = text

    def notify(self, message):
        pass

    def read_state(self, key):
        return None

    def draw_rect(self, *a, **k):
        pass

    def draw_round_rect(self, *a, **k):
        pass

    def draw_circle(self, *a, **k):
        pass

    def draw_line(self, *a, **k):
        pass

    def draw_polygon(self, *a, **k):
        pass

    def draw_text(self, *a, **k):
        pass

    def draw_image(self, *a, **k):
        pass

    def offscreen(self, width, height, bg=None):
        return _PILCanvas(width, height, bg=bg, root=os.path.dirname(
            os.path.join(ROOT, "data", "packs", "minigames", "escape_giantess")))


def main():
    # ---- 1) 地图生成统计（两主题、多关卡；偶发不达标按原版逻辑重试） ----
    themes = {"city": 0, "flesh": 0}
    for lv in (1, 3, 5, 8):
        for _ in range(6):
            for _retry in range(120):     # 与 _start_level 同款重试
                rng = g.mulberry32(os.urandom(4)[0] * 16777213 + lv)
                if rng() < 0.5:
                    g._gen_city(rng, lv)
                    themes["city"] += 1
                else:
                    g._gen_flesh(rng, lv)
                    themes["flesh"] += 1
                if g._find_spawn_and_exit(rng):
                    break
            else:
                raise AssertionError(f"lv{lv}: 120 次重试内无可用关卡")
            exit_t = g._S.get_tile(*g._S.exit_pos)
            assert exit_t == g.EXIT, "出口格必须是 EXIT"
            # 连通性：出生点身体站得下
            p = g._S.player
            assert g._S.body_stands(p["tx"], p["ty"]), "出生点身体站不下"
            # 大开区面积下限（原版 findSpawnAndExit 的 0.6 可达比例已保证，
            # 这里再验一次 largestOpenArea 不塌）
            assert g._largest_open_area() > 100 * g.SCALE, "连通区异常小"
    print("gen ok, themes:", themes, flush=True)

    # ---- 2) 实跑：随机走位 + 偶尔投弹，直到自然结算 ----
    api = StubApi()
    game_inst = g.EscapeGiantessGame()
    game_inst.api = api
    game_inst.setup({"target_level": 30})

    outcomes = {"won": 0, "lost": 0, "frames": 0}
    levels_played = 0
    dt = 1 / 60.0
    keys = ["w", "a", "s", "d"]
    for step in range(60 * 60 * 10):      # 最多 10 分钟模拟
        # 简单 bot：每 0.4s 换一个随机方向，偶尔投弹
        if step % 24 == 0:
            api.keys.clear()
            api.keys.add(keys[int(os.urandom(1)[0]) % 4])
        if step % 97 == 0:
            api.keys.add("space")
        else:
            api.keys.discard("space")
        game_inst.update(dt)
        outcomes["frames"] += 1
        if api.finished is not None:
            result = api.finished
            if result.get("won"):
                outcomes["won"] += 1
                assert result.get("level") == 30, f"胜利时关卡应为目标关：{result}"
                print("finish:", result, flush=True)
                break
            outcomes["lost"] += 1
            levels_played += 1
            print("lose:", result, flush=True)
            api.finished = None
            game_inst._reset_run()
            game_inst._start_level(result.get("level", 1) + 1)
            game_inst._sync_hud()
        if game_inst._banner is not None:
            game_inst._end_timer = 0.0   # 跳过横幅等待
    assert outcomes["frames"] > 60 * 10, "模拟帧数异常少（过早卡死）"
    assert outcomes["won"] + outcomes["lost"] > 0, "从未自然结算"
    print("sim ok:", outcomes, flush=True)
    print("SIM_DONE", flush=True)


if __name__ == "__main__":
    main()
