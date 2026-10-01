# -*- coding: utf-8 -*-
"""逃离巨大娘 · Escape the Giantess —— py 后端移植版。

原版是桌面项目里的网页原型（原生 JS + Canvas2D，68×48 格、两主题、三类危险源、
织物层、视野迷雾），玩法设计与算法见原项目 `docs/玩法设计与算法总结.md`。
本文件按原版逐模块移植到小游戏框架 API 上：

  config/utils/state/mapgen/fabric/tiles/hazards/player/fx/render → 本文件分节
  main.js 的输入与主循环 → EscapeGiantessGame.update（帧时钟驱动）
  bridge.js 的「目标关数 + 胜负回传」→ api.finish（副本触发器管线）

与原版的差异（渲染近似，玩法一致）：
- 地形与织物仍整层预渲染（离屏画布 → 每帧一次 draw_image），
  单格重绘（炸弹清瓦砾）沿用原版 repaintTile 的做法；
- 径向渐变（出口光晕 / 呼吸血色 / 出口箭头辉光）用同心圆近似；
- 视野羽化用两级嵌套矩形近似；扫掠体的手/鞋为多边形剪影（判定框不变）；
- 标题屏去掉（副本内直接开局），结算横幅停留约 1.5s 后自动结算。

状态说明：框架保证同一时刻只有一个舞台（pending_mini_game 守卫），
因此本文件沿用原版的模块级单例状态（与 JS 的 window.GG.S 同构）。
"""

import math
import random
from array import array

from dungeon.window.minigame import MiniGame

# ========================================================================
# config.js —— 全局可调参数（数值与原版一致）
# ========================================================================
SCALE = 2
COLS = 34 * SCALE
ROWS = 24 * SCALE
TILE = 16
W = COLS * TILE          # 1088
H = ROWS * TILE          # 768

EMPTY, BUILDING, FLESH, RUBBLE, EXIT = 0, 1, 2, 3, 4
DIRS = ((0, -1), (1, 0), (0, 1), (-1, 0))

BODY = SCALE
MOVE_TIME = 0.2 / SCALE
FABRIC_MOVE_MULT = 2.4
BOMB_W, BOMB_L = 2, 5
BOMB_COOLDOWN = 0.32

FABRIC_ALPHA = 0.92
FABRIC_BASE_ALPHA = 0.68
FABRIC_DETAIL_ALPHA = 0.9
FABRIC_PALETTE = [
    {"base": "#3f5ec4", "mid": "#6b8bf0", "lite": "#b9caff", "dark": "#26397e"},
    {"base": "#c23f72", "mid": "#e06a9c", "lite": "#ffb0d0", "dark": "#85204b"},
    {"base": "#2f9a72", "mid": "#54c294", "lite": "#a9eed0", "dark": "#1b6449"},
    {"base": "#c8842b", "mid": "#e9a952", "lite": "#ffdca6", "dark": "#8a551a",
     "avoidOnSkin": True},
    {"base": "#7d55b0", "mid": "#a67ee0", "lite": "#dcc2ff", "dark": "#4d3178"},
    {"base": "#2f8f9c", "mid": "#54b8c4", "lite": "#a8e6ee", "dark": "#1b5c66"},
]

FABRIC_SKIRT_MIN, FABRIC_SKIRT_VAR = 1, 2
FABRIC_SKIRT_R_MIN, FABRIC_SKIRT_R_VAR = 4.2 * SCALE, 2.4 * SCALE
FABRIC_CLOTH_MIN, FABRIC_CLOTH_VAR = 3, 4
FABRIC_WEAVE_PERIOD = 4 * SCALE
FABRIC_LACE_PERIOD = 5 * SCALE
FABRIC_LACE_HOLE = 2.2 * SCALE

VIEW_TILES = 32
VIEW_FEATHER = 1.5 * SCALE
VIEW_FOG_ALPHA = 1

SWEEP_ACTIVE_TIME, SWEEP_HIDDEN_TIME, SWEEP_WARN_TIME = 6.5, 4.5, 1.6
SWEEP_MOVE_MIN, SWEEP_MOVE_VAR = 0.6 / SCALE, 0.2 / SCALE

CITY_MAIN_W, CITY_SIDE_W, CITY_STUB_W = 6, 3, 2
CITY_MAIN_H, CITY_MAIN_V = 1, 1
CITY_MIN_BLOCK = 5 * SCALE
CITY_BLOCK_MAX = 6 * SCALE
CITY_CUT_MIN = 4 * SCALE
CITY_SPLIT_DEPTH = 3
CITY_EXTRA_CUT_CHANCE = 0.3
CITY_STUB_MIN, CITY_STUB_VAR = 12, 8
CITY_STUB_LEN_MIN, CITY_STUB_LEN_VAR = 2 * SCALE, 3 * SCALE
CITY_COURT_CHANCE = 0.45
CITY_SEC_STUB_CHANCE = 0.7
CITY_DEBRIS_BASE = 6 * SCALE * SCALE
CITY_DEBRIS_PER_LV = 2 * SCALE * SCALE
CITY_DEBRIS_CHANCE = 0.65
CITY_PREFABS = [
    {"w": 3, "h": 3, "v": 0, "weight": 3},
    {"w": 3, "h": 3, "v": 1, "weight": 2},
    {"w": 3, "h": 3, "v": 2, "weight": 2},
    {"w": 2, "h": 2, "v": 3, "weight": 3},
    {"w": 2, "h": 2, "v": 4, "weight": 2},
    {"w": 2, "h": 1, "v": 5, "weight": 2},
    {"w": 1, "h": 2, "v": 5, "weight": 2},
]

RUBBLE_HEAT_FRESH = 2 * SCALE
EXPAND_INTERVAL = 0.75
EXPAND_CHANCE_BUILDING = 0.5
EXPAND_CHANCE_EMPTY = 0.25
EXPAND_BUDGET_PER_SOURCE = 0.6 * SCALE * SCALE
EXPAND_MIN_PER_TICK = 4 * SCALE * SCALE
EXPAND_MAX_PER_TICK = 24 * SCALE * SCALE
EXPAND_MAX_RATIO = 0.45

BREATH_PERIOD_MIN, BREATH_PERIOD_VAR = 13, 9
BREATH_BASE_R_MIN, BREATH_BASE_R_VAR = 1.5 * SCALE, 0.7 * SCALE
BREATH_AMP_MIN, BREATH_AMP_VAR = 1.1 * SCALE, 0.6 * SCALE

FLESH_AXIS_H_CHANCE = 0.5
FLESH_BAND_SPACING_MIN, FLESH_BAND_SPACING_VAR = 3 * SCALE, 2 * SCALE
FLESH_BAND_THICK = 3 * SCALE
FLESH_BAND_SEG_MIN, FLESH_BAND_SEG_VAR = 0.24, 0.52
FLESH_BAND_GAP_MIN, FLESH_BAND_GAP_VAR = 1 * SCALE, 4 * SCALE
FLESH_BAND_WOBBLE = 0.18
FLESH_SECOND_AXIS_CHANCE = 0.25
FLESH_NIBBLE_CORNER, FLESH_NIBBLE_EDGE = 0.55, 0.07
FLESH_PLAZAS = 3
FLESH_PLAZA_R_MIN, FLESH_PLAZA_R_VAR = 2.4 * SCALE, 1.8 * SCALE
FLESH_MIN_OPEN_BLOB = 3 * SCALE * SCALE
FLESH_RUINS_BASE = 9 * SCALE * SCALE
FLESH_RUINS_PER_LV = 1.3 * SCALE * SCALE

SKIN = {
    "bg": "#8b6051", "bgSpeck": "#96685a", "bgLine": "#7c5244",
    "base": "#f0c6a2", "hi": "#ffe4ca", "lo": "#d5a37a",
    "crease": "#bd875e", "edge": "#a26a4b", "pore": "#e0ab84",
}
SKIN_CITY_FALLBACK = {   # 城市模式画到 FLESH 格时的备用配色（tiles.js 内联）
    "base": "#8f2d54", "hi": "#b8436d", "lo": "#742346",
    "crease": "#5c1a36", "edge": "#5c1a36", "pore": "#e07aa3",
}

SHAKE_DECAY = 2.6
BG_COLOR = "#12131a"
BANNER_SECONDS = 1.5

# 目标关数参数（副本触发器 action_data.target_level 的语义：撑到第几关算胜利）
TARGET_DEFAULT = 3


def _rgb(hexstr, alpha=255):
    """'#rrggbb' → (r, g, b, a)。"""
    value = hexstr.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16), alpha)


# ========================================================================
# utils.js —— JS 位运算语义的随机数与哈希（移植后分布与原版一致）
# ========================================================================
def _u32(x):
    return x & 0xFFFFFFFF


def _i32(x):
    x &= 0xFFFFFFFF
    return x - 0x100000000 if x >= 0x80000000 else x


def _imul(a, b):
    r = (a * b) & 0xFFFFFFFF
    return r - 0x100000000 if r >= 0x80000000 else r


def js_hash(x, y, s):
    """utils.js 的 hash(x, y, s) ∈ [0,1)，按 JS 位运算语义逐行移植。"""
    h = _i32(_i32(_imul(x, 374761393)) + _i32(_imul(y, 668265263))
             + _i32(_imul(s, 1442695041)))
    h = _imul(_i32(h) ^ _i32(_u32(h) >> 13), 1274126177)
    h = _i32(h) ^ _i32(_u32(h) >> 16)
    return _u32(h) / 4294967296.0


def mulberry32(seed):
    """utils.js 的 mulberry32：closure 状态 + 每调用一次出一个 [0,1)。"""
    state = _i32(seed)

    def rng():
        nonlocal state
        state = _i32(state + 0x6D2B79F5)
        t = _imul(state ^ _i32(_u32(state) >> 15), _i32(1 | (state & 0xFFFFFFFF)))
        t2 = _imul(t ^ _i32(_u32(t) >> 7), _i32(61 | (t & 0xFFFFFFFF)))
        t = _i32(t + t2) ^ t
        return _u32(t ^ _i32(_u32(t) >> 14)) / 4294967296.0

    return rng


def _js_round(v):
    """JS Math.round：正数半值向上取（Python round 是银行家舍入）。"""
    return math.floor(v + 0.5)


def clamp(v, a, b):
    return a if v < a else (b if v > b else v)


# ========================================================================
# state.js —— 全局状态单例（模块级，与 JS window.GG.S 同构）
# ========================================================================
class _State:
    def __init__(self):
        n = COLS * ROWS
        self.grid = bytearray(n)
        self.fabric = bytearray(n)
        self.fabric_props = []
        self.fabric_full = False
        self.prefab_map = array("H", bytes(2 * n))
        self.city_prefabs = []
        self.rubble_heat = bytearray(n)

        self.state = "playing"       # playing | win | lose
        self.level = 1
        self.theme_name = "城市废墟"
        self.theme_kind = "city"
        self.lose_reason = ""
        self.rubble_expands = False
        self.time_acc = 0.0

        self.shake = 0.0
        self.bomb_cooldown = 0.0

        self.exit_pos = [2, 2]
        self.sweep = None
        self.expand_timer = 0.0
        self.breath_blocks = []
        self.base_flesh = set()

        self.player = {
            "px": 1.0, "py": 1.0, "sx": 1, "sy": 1, "tx": 1, "ty": 1,
            "t": 0.0, "moving": False, "facing": 2, "move_time": MOVE_TIME,
        }

    # ---- 地图查询 / 写入 ----
    def idx(self, x, y):
        return y * COLS + x

    def get_tile(self, x, y):
        if x < 0 or y < 0 or x >= COLS or y >= ROWS:
            return BUILDING
        return self.grid[y * COLS + x]

    def set_tile(self, x, y, t):
        if x < 0 or y < 0 or x >= COLS or y >= ROWS:
            return
        self.grid[y * COLS + x] = t

    def has_fabric(self, x, y):
        if x < 0 or y < 0 or x >= COLS or y >= ROWS:
            return False
        return self.fabric[y * COLS + x] > 0

    def clear_fabric(self):
        self.fabric = bytearray(COLS * ROWS)
        self.fabric_props = []
        self.fabric_full = False

    def in_sweep(self, x, y):
        sw = self.sweep
        if not sw or not sw["active"]:
            return False
        return sw["x"] <= x < sw["x"] + sw["w"] and sw["y"] <= y < sw["y"] + sw["h"]

    def can_walk(self, x, y):
        if self.in_sweep(x, y):
            return False
        t = self.get_tile(x, y)
        return t in (EMPTY, EXIT)

    # ---- 玩家身体（BODY×BODY 格） ----
    def body_stands(self, x, y):
        if x < 0 or y < 0 or x + BODY > COLS or y + BODY > ROWS:
            return False
        for dy in range(BODY):
            for dx in range(BODY):
                t = self.get_tile(x + dx, y + dy)
                if t not in (EMPTY, EXIT):
                    return False
        return True

    def body_fits(self, x, y):
        for dy in range(BODY):
            for dx in range(BODY):
                if self.in_sweep(x + dx, y + dy):
                    return False
                t = self.get_tile(x + dx, y + dy)
                if t not in (EMPTY, EXIT):
                    return False
        return True

    def body_on_exit(self, x, y):
        for dy in range(BODY):
            for dx in range(BODY):
                if self.get_tile(x + dx, y + dy) == EXIT:
                    return True
        return False

    def body_has_fabric(self, x, y):
        for dy in range(BODY):
            for dx in range(BODY):
                if self.has_fabric(x + dx, y + dy):
                    return True
        return False

    def body_in_rect(self, rx, ry, rw, rh):
        p = self.player
        ax = (p["tx"], math.floor(p["px"]))
        ay = (p["ty"], math.floor(p["py"]))
        for k in range(2):
            for dy in range(BODY):
                for dx in range(BODY):
                    bx, by = ax[k] + dx, ay[k] + dy
                    if rx <= bx < rx + rw and ry <= by < ry + rh:
                        return True
        return False

    def body_hits_cell(self, cx, cy):
        return self.body_in_rect(cx, cy, 1, 1)

    # ---- 瓦砾 ----
    def set_rubble(self, x, y, heat):
        if x < 0 or y < 0 or x >= COLS or y >= ROWS:
            return
        i = y * COLS + x
        self.grid[i] = RUBBLE
        self.rubble_heat[i] = int(heat) & 0xFF

    def is_live_rubble(self, x, y):
        if x < 0 or y < 0 or x >= COLS or y >= ROWS:
            return False
        i = y * COLS + x
        return self.grid[i] == RUBBLE and self.rubble_heat[i] > 0

    def clear_tile(self, x, y):
        if x < 0 or y < 0 or x >= COLS or y >= ROWS:
            return
        i = y * COLS + x
        self.grid[i] = EMPTY
        self.rubble_heat[i] = 0


_S = _State()


# ========================================================================
# fx.js —— 粒子与屏幕震动
# ========================================================================
_particles = []


def _spawn_burst(cx, cy, color, count):
    for _ in range(count):
        a = random.random() * math.pi * 2
        sp = (25 + random.random() * 130) * SCALE
        _particles.append({
            "x": cx, "y": cy,
            "vx": math.cos(a) * sp, "vy": math.sin(a) * sp,
            "life": 0.3 + random.random() * 0.5, "max": 0.8,
            "size": (2 + random.random() * 3) * SCALE, "color": color,
        })


def _spawn_death(cx, cy, count):
    for _ in range(count):
        a = random.random() * math.pi * 2
        sp = (40 + random.random() * 190) * SCALE
        _particles.append({
            "x": cx, "y": cy,
            "vx": math.cos(a) * sp, "vy": math.sin(a) * sp,
            "life": 0.6 + random.random() * 0.9, "max": 1.5,
            "size": (2 + random.random() * 3) * SCALE,
            "color": "#ff9478" if random.random() < 0.55 else "#ffe0cc",
        })


def _fx_clear():
    _particles.clear()


def _fx_update(dt):
    for i in range(len(_particles) - 1, -1, -1):
        p = _particles[i]
        p["life"] -= dt
        if p["life"] <= 0:
            _particles.pop(i)
            continue
        p["x"] += p["vx"] * dt
        p["y"] += p["vy"] * dt
        p["vx"] *= 0.93
        p["vy"] *= 0.93


def _shake(amount):
    _S.shake = max(_S.shake, amount)


# ========================================================================
# mapgen.js —— 关卡生成
# ========================================================================
def _reset_hazards():
    _S.sweep = None
    _S.breath_blocks = []
    _S.base_flesh = set()
    _S.expand_timer = 0.0
    _S.rubble_heat = bytearray(COLS * ROWS)
    _S.prefab_map = array("H", bytes(2 * COLS * ROWS))
    _S.city_prefabs = []


def _largest_open_area():
    seen = bytearray(COLS * ROWS)
    q = array("i", bytes(4 * COLS * ROWS))
    best = 0
    for i in range(len(_S.grid)):
        if seen[i]:
            continue
        x, y = i % COLS, i // COLS
        if not _S.body_stands(x, y):
            continue
        head = tail = 0
        n = 0
        q[tail] = i
        tail += 1
        seen[i] = 1
        while head < tail:
            cur = q[head]
            head += 1
            n += 1
            cx, cy = cur % COLS, cur // COLS
            for d in range(4):
                nx, ny = cx + DIRS[d][0], cy + DIRS[d][1]
                if nx < 0 or ny < 0 or nx >= COLS or ny >= ROWS:
                    continue
                ni = ny * COLS + nx
                if seen[ni] or not _S.body_stands(nx, ny):
                    continue
                seen[ni] = 1
                q[tail] = ni
                tail += 1
        best = max(best, n)
    return best


def _keeps_body_connected(before):
    return _largest_open_area() >= before - BODY * BODY


def _clean_components(tile, min_size, fill):
    seen = bytearray(COLS * ROWS)
    q = array("i", bytes(4 * COLS * ROWS))
    for i in range(len(_S.grid)):
        if seen[i] or _S.grid[i] != tile:
            continue
        head = tail = 0
        q[tail] = i
        tail += 1
        seen[i] = 1
        while head < tail:
            cur = q[head]
            head += 1
            x, y = cur % COLS, cur // COLS
            for d in range(4):
                nx, ny = x + DIRS[d][0], y + DIRS[d][1]
                if nx < 0 or ny < 0 or nx >= COLS or ny >= ROWS:
                    continue
                ni = ny * COLS + nx
                if seen[ni] or _S.grid[ni] != tile:
                    continue
                seen[ni] = 1
                q[tail] = ni
                tail += 1
        if tail < min_size:
            for k in range(tail):
                _S.grid[q[k]] = fill


def _keep_largest_open(fill):
    n = COLS * ROWS
    seen = bytearray(n)
    q = array("i", bytes(4 * n))

    def flood(start, mark):
        head = tail = 0
        q[tail] = start
        tail += 1
        mark[start] = 1
        count = 1
        while head < tail:
            cur = q[head]
            head += 1
            x, y = cur % COLS, cur // COLS
            for d in range(4):
                nx, ny = x + DIRS[d][0], y + DIRS[d][1]
                if nx < 0 or ny < 0 or nx >= COLS or ny >= ROWS:
                    continue
                ni = ny * COLS + nx
                if mark[ni] or _S.grid[ni] != EMPTY:
                    continue
                mark[ni] = 1
                q[tail] = ni
                tail += 1
                count += 1
        return count

    best, best_size = -1, 0
    for i in range(n):
        if seen[i] or _S.grid[i] != EMPTY:
            continue
        size = flood(i, seen)
        if size > best_size:
            best_size, best = size, i
    if best < 0:
        return
    keep = bytearray(n)
    flood(best, keep)
    for i in range(n):
        if _S.grid[i] == EMPTY and not keep[i]:
            _S.grid[i] = fill


def _fill_blocks_with_prefabs(rng):
    _S.city_prefabs = []
    _S.prefab_map = array("H", bytes(2 * COLS * ROWS))
    Wd = COLS
    n = Wd * ROWS

    block = array("h", b"\xff\xff" * n)   # -1 初始化
    blocks = []
    for start in range(n):
        if block[start] != -1 or _S.grid[start] != BUILDING:
            continue
        block_id = len(blocks)
        cells = []
        queue = [start]
        block[start] = block_id
        head = 0
        while head < len(queue):
            cur = queue[head]
            head += 1
            cells.append(cur)
            cx, cy = cur % Wd, cur // Wd
            for d in range(4):
                nx, ny = cx + DIRS[d][0], cy + DIRS[d][1]
                if nx < 0 or ny < 0 or nx >= Wd or ny >= ROWS:
                    continue
                ni = ny * Wd + nx
                if block[ni] != -1 or _S.grid[ni] != BUILDING:
                    continue
                block[ni] = block_id
                queue.append(ni)
        blocks.append(cells)

    used = bytearray(n)

    def fits(x, y, w, h, block_id):
        if x + w > Wd or y + h > ROWS:
            return False
        for j in range(h):
            for i in range(w):
                ni = (y + j) * Wd + (x + i)
                if used[ni] or block[ni] != block_id:
                    return False
        return True

    specs = sorted({(t["w"], t["h"]) for t in CITY_PREFABS},
                   key=lambda wh: wh[0] * wh[1], reverse=True)

    for cells in blocks:
        for w, h in specs:
            types = [t for t in CITY_PREFABS if t["w"] == w and t["h"] == h]
            if not types:
                continue
            order = list(cells)
            for i in range(len(order) - 1, 0, -1):
                j = int(rng() * (i + 1))
                order[i], order[j] = order[j], order[i]
            for a in order:
                if used[a]:
                    continue
                x, y = a % Wd, a // Wd
                candidates = [t for t in types if fits(x, y, w, h, block[a])]
                if not candidates:
                    continue
                total = sum(t["weight"] for t in candidates)
                r = rng() * total
                pick = candidates[0]
                for t in candidates:
                    r -= t["weight"]
                    if r < 0:
                        pick = t
                        break
                prop_id = len(_S.city_prefabs) + 1
                _S.city_prefabs.append({"x": x, "y": y, "w": w, "h": h, "v": pick["v"]})
                for j in range(h):
                    for i in range(w):
                        ni = (y + j) * Wd + (x + i)
                        used[ni] = 1
                        _S.prefab_map[ni] = prop_id


def _gen_city(rng, lv):
    _reset_hazards()
    _S.theme_kind = "city"
    _S.theme_name = "城市废墟"
    _S.grid = bytearray(b"\x01" * (COLS * ROWS))

    road = bytearray(COLS * ROWS)
    road_cells = []
    sec_cells = []
    MW = CITY_MAIN_W
    SIDE = CITY_SIDE_W
    STUBW = CITY_STUB_W
    EDGE = 2 * SCALE

    def carve(x, y, sec=False):
        if x < 1 or y < 1 or x > COLS - 2 or y > ROWS - 2:
            return False
        _S.set_tile(x, y, EMPTY)
        i = _S.idx(x, y)
        if not road[i]:
            road[i] = 1
            road_cells.append(i)
            if sec:
                sec_cells.append(i)
        return True

    def make_cuts(count, lo, hi):
        gap = CITY_MIN_BLOCK
        cuts = []
        for i in range(count):
            ideal = lo + (i + 1) * (hi - lo + 1) / (count + 1)
            cuts.append(_js_round(ideal + (rng() - 0.5) * 3 * SCALE))
        cuts.sort()
        for i in range(len(cuts)):
            low = lo if i == 0 else cuts[i - 1] + gap
            high = hi - (len(cuts) - 1 - i) * gap
            cuts[i] = max(low, min(high, cuts[i]))
        return cuts

    def spans(lo, hi, cuts):
        out = []
        s = lo
        for c in cuts:
            out.append((s, c - 1))
            s = c + MW
        out.append((s, hi))
        return [sp for sp in out if sp[1] >= sp[0]]

    h_cuts = make_cuts(CITY_MAIN_H, EDGE, ROWS - (EDGE + 1) - (MW - 1))
    v_cuts = make_cuts(CITY_MAIN_V, EDGE, COLS - (EDGE + 1) - (MW - 1))

    for y in h_cuts:
        for x in range(1, COLS - 1):
            for k in range(MW):
                carve(x, y + k)
    for x in v_cuts:
        for y in range(1, ROWS - 1):
            for k in range(MW):
                carve(x + k, y)

    y_spans = spans(1, ROWS - 2, h_cuts)
    x_spans = spans(1, COLS - 2, v_cuts)

    def pick_cut(a0, a1):
        length = a1 - a0 + 1
        margin = max(2 * SCALE, math.floor(length * 0.3))
        lo = a0 + margin
        hi = a1 - margin - (SIDE - 1)
        if hi < lo:
            return -1
        return lo + int(rng() * (hi - lo + 1))

    def carve_column(cx, y0, y1):
        for y in range(y0, y1 + 1):
            for k in range(SIDE):
                carve(cx + k, y, True)

    def carve_row(cy, x0, x1):
        for x in range(x0, x1 + 1):
            for k in range(SIDE):
                carve(x, cy + k, True)

    def subdivide(x0, x1, y0, y1, depth):
        w = x1 - x0 + 1
        h = y1 - y0 + 1
        big_w = w > CITY_BLOCK_MAX
        big_h = h > CITY_BLOCK_MAX
        extra = (depth > 0 and not big_w and not big_h
                 and max(w, h) >= CITY_CUT_MIN and rng() < CITY_EXTRA_CUT_CHANCE)
        if not big_w and not big_h and not extra:
            return
        if big_w or (not big_h and w >= h):
            if w < CITY_CUT_MIN:
                return
            cx = pick_cut(x0, x1)
            if cx < 0:
                return
            carve_column(cx, y0, y1)
            subdivide(x0, cx - 1, y0, y1, depth - 1)
            subdivide(cx + SIDE, x1, y0, y1, depth - 1)
        else:
            if h < CITY_CUT_MIN:
                return
            cy = pick_cut(y0, y1)
            if cy < 0:
                return
            carve_row(cy, x0, x1)
            subdivide(x0, x1, y0, cy - 1, depth - 1)
            subdivide(x0, x1, cy + SIDE, y1, depth - 1)

    for ys in y_spans:
        for xs in x_spans:
            if xs[1] - xs[0] < 2 * SCALE or ys[1] - ys[0] < 2 * SCALE:
                continue
            subdivide(xs[0], xs[1], ys[0], ys[1], CITY_SPLIT_DEPTH)

    def pick_into_block(x, y):
        for _ in range(4):
            d = DIRS[int(rng() * 4)]
            ok = True
            for k in range(1, 2 * SCALE + 1):
                if _S.get_tile(x + d[0] * k, y + d[1] * k) != BUILDING:
                    ok = False
                    break
            if ok:
                return d
        return None

    n_stub = CITY_STUB_MIN + math.floor(rng() * CITY_STUB_VAR + lv * 0.6)
    for _ in range(n_stub):
        pool = sec_cells if (sec_cells and rng() < CITY_SEC_STUB_CHANCE) else road_cells
        if not pool:
            continue
        start = pool[int(rng() * len(pool))]
        sx, sy = start % COLS, start // COLS
        d = pick_into_block(sx, sy)
        if d is None:
            continue
        px, py = -d[1], d[0]
        length = CITY_STUB_LEN_MIN + math.floor(rng() * CITY_STUB_LEN_VAR)
        cx, cy = sx, sy
        for _ in range(length):
            nx, ny = cx + d[0], cy + d[1]
            if nx < 1 or ny < 1 or nx > COLS - 2 or ny > ROWS - 2:
                break
            if road[_S.idx(nx, ny)]:
                break
            for k in range(STUBW):
                carve(nx + px * k, ny + py * k)
            cx, cy = nx, ny
        if (cx != sx or cy != sy) and rng() < CITY_COURT_CHANCE:
            for i in range(2 * SCALE):
                for j in range(2 * SCALE):
                    carve(cx + i, cy + j)

    _fill_blocks_with_prefabs(rng)

    debris = CITY_DEBRIS_BASE + math.floor(lv * CITY_DEBRIS_PER_LV)
    for _ in range(debris):
        x = 1 + math.floor(rng() * (COLS - 2))
        y = 1 + math.floor(rng() * (ROWS - 2))
        if _S.get_tile(x, y) != EMPTY:
            continue
        if rng() >= CITY_DEBRIS_CHANCE:
            continue
        before = _largest_open_area()
        _S.set_rubble(x, y, 0)
        if not _keeps_body_connected(before):
            _S.clear_tile(x, y)
    _keep_largest_open(BUILDING)

    sw = (6 + math.floor(rng() * 3)) * SCALE
    sh = (5 + math.floor(rng() * 3)) * SCALE
    _S.sweep = {
        "x": 1 + math.floor(rng() * (COLS - sw - 2)),
        "y": 1 + math.floor(rng() * (ROWS - sh - 2)),
        "w": sw, "h": sh,
        "dir": math.floor(rng() * 4),
        "kind": "hand" if rng() < 0.5 else "shoe",
        "active": True,
        "timer": SWEEP_ACTIVE_TIME,
        "move_timer": 0.0,
        "move_interval": SWEEP_MOVE_MIN + rng() * SWEEP_MOVE_VAR,
    }
    _S.rubble_expands = True


def _gen_flesh(rng, lv):
    _reset_hazards()
    _S.theme_kind = "flesh"
    _S.theme_name = "肌肤秘境"
    _S.grid = bytearray(COLS * ROWS)

    axis_h = rng() < FLESH_AXIS_H_CHANCE

    for x in range(COLS):
        _S.set_tile(x, 0, FLESH)
        _S.set_tile(x, ROWS - 1, FLESH)
    for y in range(ROWS):
        _S.set_tile(0, y, FLESH)
        _S.set_tile(COLS - 1, y, FLESH)

    def put(x, y):
        if 0 < x < COLS - 1 and 0 < y < ROWS - 1:
            _S.set_tile(x, y, FLESH)

    def lay_bands(horizontal, density):
        perp_max = (ROWS if horizontal else COLS) - 2
        along_max = (COLS if horizontal else ROWS) - 2
        p = 2 * SCALE + math.floor(rng() * 2) * SCALE
        while p < perp_max - 1:
            th = 1 + math.floor(rng() * FLESH_BAND_THICK)
            a = SCALE + math.floor(rng() * 2) * SCALE
            while a < along_max:
                seg_len = max(2 * SCALE, _js_round(
                    along_max * (FLESH_BAND_SEG_MIN + rng() * FLESH_BAND_SEG_VAR) * density))
                a1 = min(along_max + 1, a + seg_len)
                off = 0
                for t in range(a, a1):
                    if rng() < FLESH_BAND_WOBBLE:
                        off += (-1 if rng() < 0.5 else 1) * SCALE
                    off = clamp(off, -SCALE, SCALE)
                    for k in range(th):
                        q = p + k + off
                        if horizontal:
                            put(t, q)
                        else:
                            put(q, t)
                a = a1 + FLESH_BAND_GAP_MIN + math.floor(rng() * FLESH_BAND_GAP_VAR)
            p += th + FLESH_BAND_SPACING_MIN + math.floor(rng() * FLESH_BAND_SPACING_VAR)

    lay_bands(axis_h, 1)
    if rng() < FLESH_SECOND_AXIS_CHANCE:
        lay_bands(not axis_h, 0.45)

    for y in range(1, ROWS - 1):
        for x in range(1, COLS - 1):
            if _S.get_tile(x, y) != FLESH:
                continue
            open_n = 0
            for d in range(4):
                if _S.get_tile(x + DIRS[d][0], y + DIRS[d][1]) == EMPTY:
                    open_n += 1
            if open_n >= 3:
                if rng() < FLESH_NIBBLE_CORNER:
                    _S.set_tile(x, y, EMPTY)
            elif open_n == 2 and rng() < FLESH_NIBBLE_EDGE:
                _S.set_tile(x, y, EMPTY)

    plazas = FLESH_PLAZAS + math.floor(rng() * 2)
    for _ in range(plazas):
        cx = 3 * SCALE + math.floor(rng() * (COLS - 6 * SCALE))
        cy = 3 * SCALE + math.floor(rng() * (ROWS - 6 * SCALE))
        r = FLESH_PLAZA_R_MIN + rng() * FLESH_PLAZA_R_VAR
        big_r = math.ceil(r + 1)
        for y in range(max(1, cy - big_r), min(ROWS - 2, cy + big_r) + 1):
            for x in range(max(1, cx - big_r), min(COLS - 2, cx + big_r) + 1):
                dx, dy = x - cx, y - cy
                if dx * dx + dy * dy <= r * r + (rng() - 0.5) * 1.4:
                    _S.set_tile(x, y, EMPTY)

    if FLESH_MIN_OPEN_BLOB > 1:
        _clean_components(EMPTY, FLESH_MIN_OPEN_BLOB, FLESH)
    _keep_largest_open(FLESH)

    ruins = FLESH_RUINS_BASE + math.floor(lv * FLESH_RUINS_PER_LV)
    for _ in range(ruins):
        x = 1 + math.floor(rng() * (COLS - 2))
        y = 1 + math.floor(rng() * (ROWS - 2))
        if _S.get_tile(x, y) != EMPTY:
            continue
        before = _largest_open_area()
        _S.set_tile(x, y, BUILDING if rng() < 0.45 else RUBBLE)
        if not _keeps_body_connected(before):
            _S.set_tile(x, y, EMPTY)
    _keep_largest_open(FLESH)

    _S.base_flesh = {i for i in range(len(_S.grid)) if _S.grid[i] == FLESH}

    _S.breath_blocks = []
    num_blocks = 3 + math.floor(lv * 0.6)
    for _ in range(num_blocks):
        cx = cy = 0
        ok = False
        for _ in range(30):
            cx = 3 * SCALE + math.floor(rng() * (COLS - 6 * SCALE))
            cy = 3 * SCALE + math.floor(rng() * (ROWS - 6 * SCALE))
            if _S.grid[_S.idx(cx, cy)] == EMPTY:
                ok = True
                break
        if not ok:
            continue
        period = BREATH_PERIOD_MIN + rng() * BREATH_PERIOD_VAR
        _S.breath_blocks.append({
            "cx": cx, "cy": cy,
            "phase": (0.5 + rng() * 0.5) * period,
            "period": period,
            "base_r": BREATH_BASE_R_MIN + rng() * BREATH_BASE_R_VAR,
            "amp": BREATH_AMP_MIN + rng() * BREATH_AMP_VAR,
        })

    _S.rubble_expands = False


def _guarded_by_breath(x, y):
    if not _S.breath_blocks:
        return False
    for b in _S.breath_blocks:
        r = _breathe_radius(b) + BODY
        dx, dy = x - b["cx"], y - b["cy"]
        if dx * dx + dy * dy <= r * r:
            return True
    return False


def _find_spawn_and_exit(rng):
    open_cells = []
    for y in range(1, ROWS - 1):
        for x in range(1, COLS - 1):
            if not _S.body_fits(x, y):
                continue
            if not _S.fabric_full and _S.body_has_fabric(x, y):
                continue
            if _guarded_by_breath(x, y):
                continue
            open_cells.append((x, y))

    if len(open_cells) < 40 * SCALE:
        return False

    sx, sy = open_cells[int(rng() * len(open_cells))]

    n = COLS * ROWS
    dist = array("i", b"\xff\xff\xff\xff" * n)   # -1
    dist[sy * COLS + sx] = 0
    queue = [sy * COLS + sx]
    head = 0
    best = sy * COLS + sx
    count = 1
    while head < len(queue):
        cur = queue[head]
        head += 1
        cx, cy = cur % COLS, cur // COLS
        if dist[cur] > dist[best] and not _guarded_by_breath(cx, cy):
            best = cur
        for d in range(4):
            nx, ny = cx + DIRS[d][0], cy + DIRS[d][1]
            if nx < 0 or ny < 0 or nx >= COLS or ny >= ROWS:
                continue
            ni = ny * COLS + nx
            if dist[ni] != -1:
                continue
            if not _S.body_fits(nx, ny):
                continue
            dist[ni] = dist[cur] + 1
            count += 1
            queue.append(ni)

    if count < len(open_cells) * 0.6:
        return False
    if dist[best] < 22 * SCALE:
        return False

    p = _S.player
    p["tx"], p["ty"] = sx, sy
    p["px"], p["py"] = float(sx), float(sy)
    p["sx"], p["sy"] = sx, sy
    p["moving"] = False
    p["t"] = 0.0
    p["facing"] = 2

    _S.exit_pos = [best % COLS, best // COLS]
    _S.set_tile(_S.exit_pos[0], _S.exit_pos[1], EXIT)

    reach = bytearray(n)
    rq = [sy * COLS + sx]
    reach[sy * COLS + sx] = 1
    head = 0
    while head < len(rq):
        cur = rq[head]
        head += 1
        cx, cy = cur % COLS, cur // COLS
        for d in range(4):
            nx, ny = cx + DIRS[d][0], cy + DIRS[d][1]
            if nx < 0 or ny < 0 or nx >= COLS or ny >= ROWS:
                continue
            ni = ny * COLS + nx
            if reach[ni] or not _S.body_stands(nx, ny):
                continue
            reach[ni] = 1
            rq.append(ni)
    for idx in rq:
        x, y = idx % COLS, idx // COLS
        for dy in range(BODY):
            for dx in range(BODY):
                mx, my = x + dx, y + dy
                if mx < COLS and my < ROWS:
                    reach[my * COLS + mx] = 1
    wall = FLESH if _S.theme_kind == "flesh" else BUILDING
    for i in range(n):
        if _S.grid[i] != EMPTY or reach[i]:
            continue
        _S.grid[i] = wall
        if wall == FLESH:
            _S.base_flesh.add(i)

    return True


# ========================================================================
# fabric.js —— 织物层（形状 / 上色 / 生成）
# ========================================================================
def _band_axis(cx, cy, ang, length, half_w, cb):
    dx, dy = math.cos(ang), math.sin(ang)
    nx, ny = -dy, dx
    big_r = length / 2 + half_w + 2
    x0 = max(0, math.floor(cx - big_r))
    x1 = min(COLS - 1, math.ceil(cx + big_r))
    y0 = max(0, math.floor(cy - big_r))
    y1 = min(ROWS - 1, math.ceil(cy + big_r))
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            rx, ry = x + 0.5 - cx, y + 0.5 - cy
            along = rx * dx + ry * dy
            across = rx * nx + ry * ny
            u = along / length + 0.5
            if u < 0 or u > 1:
                continue
            v = across / half_w
            if v < -1 or v > 1:
                continue
            cb(x, y, u, v)


def _arc_band(cx, cy, r, thick, a0, a1, cb):
    r0, r1 = r - thick / 2, r + thick / 2
    x0 = max(0, math.floor(cx - r1 - 1))
    x1 = min(COLS - 1, math.ceil(cx + r1 + 1))
    y0 = max(0, math.floor(cy - r1 - 1))
    y1 = min(ROWS - 1, math.ceil(cy + r1 + 1))
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            rx, ry = x + 0.5 - cx, y + 0.5 - cy
            d = math.sqrt(rx * rx + ry * ry)
            if d < r0 or d > r1:
                continue
            a = math.atan2(ry, rx)
            while a < a0:
                a += math.pi * 2
            if a > a1:
                continue
            cb(x, y, (a - a0) / (a1 - a0), (d - r) / (thick / 2))


def _lace_hole_dist(length, half_w, lace, u, v):
    al = (u - 0.5) * length
    ac = v * half_w
    qa = _js_round((al - lace["pa"]) / lace["pP"]) * lace["pP"] + lace["pa"]
    qb = _js_round((ac - lace["pb"]) / lace["pP"]) * lace["pP"] + lace["pb"]
    return abs(al - qa) + abs(ac - qb)


def _builder_cuff(rng):
    horiz = rng() < 0.5
    length = (COLS if horiz else ROWS) + 2
    half_w = ((ROWS if horiz else COLS) / 4) * (0.85 + rng() * 0.3)
    ang = (0 if horiz else math.pi / 2) + (rng() - 0.5) * 0.06
    cx = COLS / 2 + (0 if horiz else (rng() - 0.5) * 10)
    cy = ROWS / 2 + ((rng() - 0.5) * 10 if horiz else 0)
    flip = rng() < 0.5

    def cells(cb):
        _band_axis(cx, cy, ang, length, half_w,
                   lambda x, y, u, v: cb(x, y, 1 - u if flip else u, v))

    return {"ang": ang, "len": length, "half_w": half_w, "cells": cells}


def _builder_lace(rng):
    ang = (0 if rng() < 0.5 else math.pi / 2) + (rng() - 0.5) * 0.16
    diag = math.sqrt(COLS * COLS + ROWS * ROWS)
    length = diag * 1.8
    half_w = diag * 0.65
    lace = {
        "pP": FABRIC_LACE_PERIOD,
        "rH": FABRIC_LACE_HOLE,
        "pa": rng() * FABRIC_LACE_PERIOD,
        "pb": rng() * FABRIC_LACE_PERIOD,
    }

    def cells(cb):
        def hit(x, y, u, v):
            if _lace_hole_dist(length, half_w, lace, u, v) < lace["rH"]:
                return
            cb(x, y, u, v)
        _band_axis(COLS / 2, ROWS / 2, ang, length, half_w, hit)

    return {"ang": ang, "len": length, "half_w": half_w, "lace": lace, "cells": cells}


def _builder_strap(rng):
    ang = rng() * math.pi
    length = math.sqrt(COLS * COLS + ROWS * ROWS) * (0.95 + rng() * 0.4)
    half_w = (1.1 + rng() * 0.7) * SCALE
    cx = COLS / 2 + (rng() - 0.5) * 9 * SCALE
    cy = ROWS / 2 + (rng() - 0.5) * 7 * SCALE

    def cells(cb):
        _band_axis(cx, cy, ang, length, half_w, cb)

    return {"ang": ang, "len": length, "half_w": half_w, "cells": cells}


def _builder_skirt(rng):
    r = FABRIC_SKIRT_R_MIN + rng() * FABRIC_SKIRT_R_VAR
    thick = (1.5 + rng() * 0.9) * SCALE
    a_span = math.pi * (0.85 + rng() * 0.75)
    a0 = rng() * math.pi * 2
    cx = 3 * SCALE + rng() * (COLS - 6 * SCALE)
    cy = 3 * SCALE + rng() * (ROWS - 6 * SCALE)

    def cells(cb):
        _arc_band(cx, cy, r, thick, a0, a0 + a_span, cb)

    return {"ang": None, "len": r * a_span, "half_w": thick / 2,
            "a0": a0, "aSpan": a_span, "cells": cells}


def _builder_cloth(rng):
    ang = math.pi / 4 + (rng() - 0.5) * 1.2
    length = (3.6 + rng() * 3.2) * SCALE
    half_w = (0.62 + rng() * 0.42) * SCALE
    cx = 3 * SCALE + rng() * (COLS - 6 * SCALE)
    cy = 3 * SCALE + rng() * (ROWS - 6 * SCALE)

    def cells(cb):
        _band_axis(cx, cy, ang, length, half_w, cb)

    return {"ang": ang, "len": length, "half_w": half_w, "cells": cells}


_FABRIC_BUILDERS = {"cuff": _builder_cuff, "lace": _builder_lace,
                    "strap": _builder_strap, "skirt": _builder_skirt,
                    "cloth": _builder_cloth}


def _contour_color(kind, pal):
    return pal["mid"] if kind == "lace" else pal["dark"]


def _fabric_paint_cell(canvas, prop, x, y, u, v, own):
    pal = prop["pal"]
    a_base = FABRIC_BASE_ALPHA
    a_detail = FABRIC_DETAIL_ALPHA
    edge = abs(v) > 0.8
    px, py = x * TILE, y * TILE
    kind = prop["kind"]

    def cell(color, alpha):
        if isinstance(color, str):
            color = _rgb(color, 255)
        canvas.draw_rect((px, py), (px + TILE, py + TILE),
                         (color[0], color[1], color[2],
                          max(0, min(255, round(255 * clamp(alpha * FABRIC_ALPHA, 0, 1))))))

    if kind == "cuff":
        if u > 0.84:
            rib = math.floor(u * 44) & 1
            cell(pal["mid"] if rib else pal["dark"], a_base + 0.06)
        elif edge:
            cell(pal["lite"], a_base + 0.04)
        else:
            cell(pal["base"], a_base)
            if abs(v) < 0.2:
                cell(pal["lite"], a_detail * 0.42)
    elif kind == "lace":
        if edge:
            cell(pal["lite"], a_detail)
        else:
            cell(pal["base"], a_base * 0.86)
            if prop.get("lace"):
                d = _lace_hole_dist(prop["len"], prop["half_w"], prop["lace"], u, v)
                if d < prop["lace"]["rH"] + 1.4:
                    cell(pal["mid"], a_detail * 0.55)
                elif (math.floor(u * prop["len"] + v * 3) & 3) == 0:
                    cell(pal["mid"], a_detail * 0.3)
            else:
                k = math.floor(u * 40)
                if k % 2 == 0 and abs(v) < 0.5:
                    cell(pal["mid"], a_detail * 0.5)
                if abs(v) > 0.55:
                    cell(pal["mid"], a_detail * 0.55)
    elif kind == "strap":
        k = math.floor(u * 60)
        if 0.2 < u < 0.28 and abs(v) < 0.72:
            cell(pal["lite"], a_detail)
            if abs(v) < 0.3:
                cell("#f4f7ff", a_detail)
        elif edge:
            cell(pal["dark"], a_base + 0.05)
        else:
            cell(pal["base"], a_base)
            if abs(v) < 0.26 and k % 2 == 0:
                cell(pal["lite"], a_detail * 0.62)
    elif kind == "skirt":
        pleats = 9
        f = (u * pleats) - math.floor(u * pleats)
        if v > 0.55:
            cell(pal["lite"], a_detail * 0.9)
        elif f < 0.14:
            cell(pal["dark"], a_base + 0.05)
        elif v < -0.5:
            cell(pal["dark"], a_base * 0.85)
        else:
            cell(pal["base"], a_base)
    elif kind == "cloth":
        ring = math.floor(u * 8)
        if u < 0.18:
            cell(pal["dark"], a_detail * 0.92)
        elif ring % 3 == 1:
            cell(pal["mid"], a_base + 0.04)
        else:
            cell(pal["base"], a_base)
        if abs(v) < 0.22 and u >= 0.18:
            cell(pal["lite"], a_detail * 0.4)

    # 编织纹理：经纬线交替（旋转到布纹方向；裙摆按格现算切线角）
    w_per = FABRIC_WEAVE_PERIOD
    w_th = w_per * 0.42
    ang_w = (prop["a0"] + u * prop["aSpan"] + math.pi / 2
             if kind == "skirt" and prop.get("a0") is not None else prop["ang"])
    if ang_w is not None and w_per > 0:
        al = (u - 0.5) * prop["len"] * TILE
        ac = v * prop["half_w"] * TILE
        ccx, ccy = px + TILE / 2, py + TILE / 2
        cos_a, sin_a = math.cos(ang_w), math.sin(ang_w)

        def wpoint(lx, ly):
            rx = lx * cos_a - ly * sin_a
            ry = lx * sin_a + ly * cos_a
            return (ccx + rx, ccy + ry)

        half = TILE * 0.75
        k0, k1 = _js_round((al - TILE) / w_per), _js_round((al + TILE) / w_per)
        m0, m1 = _js_round((ac - TILE) / w_per), _js_round((ac + TILE) / w_per)
        mc, kc = _js_round(ac / w_per), _js_round(al / w_per)
        for k in range(k0, k1 + 1):
            over = ((k + mc) % 2 + 2) % 2 == 0
            col = (255, 255, 255, 22) if over else (0, 0, 0, 28)
            p1 = wpoint(k * w_per - w_th / 2 - al, -half)
            p2 = wpoint(k * w_per - w_th / 2 - al, half)
            canvas.draw_line(p1, p2, col, thickness=max(1, round(w_th)))
        for m in range(m0, m1 + 1):
            over = ((kc + m) % 2 + 2) % 2 == 1
            col = (255, 255, 255, 17) if over else (0, 0, 0, 23)
            p1 = wpoint(-half, m * w_per - w_th / 2 - ac)
            p2 = wpoint(half, m * w_per - w_th / 2 - ac)
            canvas.draw_line(p1, p2, col, thickness=max(1, round(w_th)))

    # 布面明暗
    nz = js_hash(x * 5 + prop["id"], y * 3, 31)
    if nz < 0.5:
        alpha = 0.045 if nz < 0.25 else 0.05
        col = (255, 255, 255, 255) if nz < 0.25 else (0, 0, 0, 255)
        canvas.draw_rect((px, py), (px + TILE, py + TILE),
                         (col[0], col[1], col[2], round(255 * FABRIC_ALPHA * alpha)))

    # 轮廓
    contour = _contour_color(kind, pal)
    lw = 2 * SCALE
    if _S.idx(x, y - 1) not in own:
        canvas.draw_rect((px, py), (px + TILE, py + lw), contour)
    if _S.idx(x, y + 1) not in own:
        canvas.draw_rect((px, py + TILE - lw), (px + TILE, py + TILE), contour)
    if _S.idx(x - 1, y) not in own:
        canvas.draw_rect((px, py), (px + lw, py + TILE), contour)
    if _S.idx(x + 1, y) not in own:
        canvas.draw_rect((px + TILE - lw, py), (px + TILE, py + TILE), contour)


def _palette_for(rng, used, avoid_warm):
    n = len(FABRIC_PALETTE)

    def pick(exclude):
        for _ in range(20):
            i = int(rng() * n)
            p = FABRIC_PALETTE[i]
            if i in used:
                continue
            if exclude and p.get("avoidOnSkin"):
                continue
            used.add(i)
            return p
        return None

    result = pick(True) or pick(False) or FABRIC_PALETTE[int(rng() * n)]
    return {key: _rgb(value) for key, value in result.items() if key != "avoidOnSkin"}


def _fabric_generate(rng, lv, theme_kind):
    _S.clear_fabric()
    props = _S.fabric_props
    used = set()
    skin = theme_kind == "flesh"
    placed = []

    def overlap_fraction(bbox):
        area = (bbox["x1"] - bbox["x0"] + 1) * (bbox["y1"] - bbox["y0"] + 1)
        worst = 0.0
        for o in placed:
            w = min(bbox["x1"], o["x1"]) - max(bbox["x0"], o["x0"]) + 1
            h = min(bbox["y1"], o["y1"]) - max(bbox["y0"], o["y0"]) + 1
            if w <= 0 or h <= 0:
                continue
            small = min(area, (o["x1"] - o["x0"] + 1) * (o["y1"] - o["y0"] + 1))
            worst = max(worst, (w * h) / max(1, small))
        return worst

    def add(kind):
        make = _FABRIC_BUILDERS.get(kind)
        if make is None:
            return
        shape = hit = bbox = None
        for _ in range(7):
            s = make(rng)
            cells_hit = []
            x0, y0, x1, y1 = COLS, ROWS, -1, -1

            def collect(x, y, _u, _v):
                nonlocal x0, y0, x1, y1
                cells_hit.append(_S.idx(x, y))
                x0, y0 = min(x0, x), min(y0, y)
                x1, y1 = max(x1, x), max(y1, y)

            s["cells"](collect)
            if not cells_hit:
                continue
            b = {"x0": x0, "y0": y0, "x1": x1, "y1": y1}
            if shape is None or overlap_fraction(b) < 0.45:
                shape, hit, bbox = s, cells_hit, b
                if not placed or overlap_fraction(b) < 0.2:
                    break
            elif shape is None:
                shape, hit, bbox = s, cells_hit, b
        if shape is None:
            return
        prop = {
            "id": len(props) + 1,
            "kind": kind,
            "pal": _palette_for(rng, used, skin),
            "bbox": bbox,
            "ang": shape["ang"],
            "len": shape["len"],
            "half_w": shape["half_w"],
            "lace": shape.get("lace"),
            "a0": shape.get("a0"),
            "aSpan": shape.get("aSpan"),
        }

        def cells(cb):
            shape["cells"](cb)

        prop["cells"] = cells
        for i in hit:
            _S.fabric[i] = prop["id"]
        props.append(prop)
        placed.append(bbox)
        if kind == "lace":
            _S.fabric_full = True

    if skin:
        kinds = ["cuff", "lace", "strap"]
        add(kinds[int(rng() * len(kinds))])
    else:
        n_skirt = FABRIC_SKIRT_MIN + math.floor(rng() * FABRIC_SKIRT_VAR)
        n_cloth = FABRIC_CLOTH_MIN + math.floor(rng() * FABRIC_CLOTH_VAR)
        for _ in range(n_skirt):
            add("skirt")
        for _ in range(n_cloth):
            add("cloth")


def _fabric_build(canvas):
    """织物整层预渲染（fabric.js build）。"""
    for prop in _S.fabric_props:
        own = set()

        def collect(x, y, _u=None, _v=None):
            own.add(_S.idx(x, y))

        prop["cells"](collect)
        prop["cells"](lambda x, y, u, v: _fabric_paint_cell(canvas, prop, x, y, u, v, own))


# ========================================================================
# tiles.js —— 地形绘制（画进离屏画布，只在地图变化时重绘）
# ========================================================================
PREFAB_PALETTES = [
    {"wall": "#3a445f", "hi": "#556284", "lo": "#252c42", "roof": "#2c3350"},
    {"wall": "#443f58", "hi": "#5e5778", "lo": "#2b2739", "roof": "#332e46"},
    {"wall": "#3c4d52", "hi": "#566d74", "lo": "#263238", "roof": "#2a3a40"},
    {"wall": "#57454e", "hi": "#73606b", "lo": "#382b31", "roof": "#3e3038"},
    {"wall": "#4d5342", "hi": "#6a7258", "lo": "#31362a", "roof": "#3a4030"},
]
SIGN_COLORS = ["#e0567a", "#4da6e0", "#e0a33f", "#66c793"]


def _lit_window(canvas, x, y, px, py, seed, wx, wy, w, h):
    lit = js_hash(x * 3 + wx, y * 5 + wy + seed, 7) < 0.3
    canvas.draw_rect((px + wx, py + wy), (px + wx + w, py + wy + h),
                     _rgb("#ffd97a") if lit else _rgb("#151b2b"))


def _prefab_art_0(canvas, x, y, px, py, dx, dy, prop, pal):
    if dy == 0:
        canvas.draw_rect((px, py), (px + TILE, py + 4), pal["roof"])
        canvas.draw_rect((px, py), (px + TILE, py + 1), pal["hi"])
        canvas.draw_rect((px, py + 4), (px + TILE, py + 5), pal["lo"])
    for wy in ([8] if dy == 0 else [3, 9]):
        for i in range(2):
            _lit_window(canvas, x, y, px, py, 0, 3 + i * 6, wy, 4, 4)
    if dy == prop["h"] - 1 and dx == (prop["w"] >> 1):
        canvas.draw_rect((px + 5, py + 5), (px + 11, py + 16), _rgb("#1a2030"))
        canvas.draw_rect((px + 5, py + 5), (px + 11, py + 6), pal["hi"])


def _prefab_art_1(canvas, x, y, px, py, dx, dy, prop, pal):
    for yy in range(1, TILE, 4):
        canvas.draw_rect((px, py + yy), (px + TILE, py + yy + 1), pal["lo"])
    if dy == 0:
        canvas.draw_rect((px, py), (px + TILE, py + 5), pal["roof"])
        canvas.draw_rect((px, py), (px + TILE, py + 1), pal["hi"])
        for i in range(3):
            canvas.draw_rect((px + 2 + i * 5, py + 2), (px + 3 + i * 5, py + 5),
                             pal["lo"])
    if dy == prop["h"] - 1 and dx == 0:
        canvas.draw_rect((px + 2, py + 4), (px + 12, py + 16), _rgb("#1a2030"))
        canvas.draw_rect((px + 2, py + 4), (px + 12, py + 5), pal["hi"])
    elif dy == prop["h"] - 1 and dx == prop["w"] - 1:
        canvas.draw_rect((px + 9, py + 8), (px + 14, py + 13), _rgb("#1a2030"))
        canvas.draw_rect((px + 9, py + 8), (px + 14, py + 9), pal["hi"])


def _prefab_art_2(canvas, x, y, px, py, dx, dy, prop, pal):
    top = 6 if dy == 0 else 1
    for i in range(3):
        wx = 2 + i * 5
        lit = js_hash(x * 7 + i, y * 11, 8) < 0.22
        canvas.draw_rect((px + wx, py + top), (px + wx + 3, py + TILE - 1),
                         _rgb("#9fd8ff") if lit else _rgb("#141a2c"))
    if dy == 0:
        canvas.draw_rect((px, py), (px + TILE, py + 5), pal["roof"])
        canvas.draw_rect((px, py + 5), (px + TILE, py + 6), pal["lo"])
        if dx == (prop["w"] >> 1):
            canvas.draw_rect((px + 7, py + 1), (px + 9, py + 11), _rgb("#8fa0c4"))
            canvas.draw_rect((px + 6, py + 1), (px + 10, py + 4), _rgb("#ff6b81"))
    if dx == 1 and dy == 1:
        canvas.draw_rect((px + 3, py + 3), (px + 13, py + 13), pal["hi"])
        for i in range(4):
            canvas.draw_rect((px + 3, py + 4 + i * 3), (px + 13, py + 5 + i * 3),
                             pal["lo"])


def _prefab_art_3(canvas, x, y, px, py, dx, dy, prop, pal):
    if dy == 0:
        canvas.draw_rect((px, py), (px + TILE, py + TILE), pal["roof"])
        canvas.draw_rect((px, py), (px + TILE, py + 1), pal["hi"])
        for i in range(4):
            canvas.draw_rect((px + 2 + i * 4, py + 4), (px + 3 + i * 4, py + 13),
                             pal["lo"])
        canvas.draw_rect((px, py + TILE - 3), (px + TILE, py + TILE), pal["lo"])
        if dx == prop["w"] - 1:
            canvas.draw_rect((px + 9, py + 2), (px + 13, py + 10), _rgb("#7a5244"))
            canvas.draw_rect((px + 8, py + 2), (px + 14, py + 4), _rgb("#3a2f28"))
    else:
        canvas.draw_rect((px, py), (px + TILE, py + 1), pal["hi"])
        door_left = js_hash(prop["x"], prop["y"], 9) < 0.5
        door_dx = 0 if door_left else prop["w"] - 1
        if dx == door_dx:
            canvas.draw_rect((px + 5, py + 4), (px + 11, py + 16), _rgb("#1a2030"))
            canvas.draw_rect((px + 5, py + 4), (px + 11, py + 5), pal["hi"])
        else:
            canvas.draw_rect((px + 4, py + 5), (px + 11, py + 11), _rgb("#20293e"))
            _lit_window(canvas, x, y, px, py, 1, 5, 6, 5, 4)


def _prefab_art_4(canvas, x, y, px, py, dx, dy, prop, pal):
    sc = _rgb(SIGN_COLORS[int(js_hash(prop["x"], prop["y"], 11) * len(SIGN_COLORS))])
    if dy == 0:
        if dx == 0:
            canvas.draw_rect((px + 2, py + 3), (px + 14, py + 10), sc)
            canvas.draw_rect((px + 4, py + 5), (px + 12, py + 6), (20, 22, 32, 204))
            canvas.draw_rect((px + 4, py + 7), (px + 9, py + 8), (20, 22, 32, 204))
        else:
            canvas.draw_rect((px + 3, py + 3), (px + 13, py + 10), _rgb("#20293e"))
            canvas.draw_rect((px + 4, py + 4), (px + 12, py + 9), _rgb("#ffe9b0"))
    else:
        for i in range(4):
            canvas.draw_rect((px + i * 4, py), (px + i * 4 + 4, py + 6),
                             _rgb("#f2e9dc") if i % 2 else sc)
        canvas.draw_rect((px, py + 6), (px + TILE, py + 7), (0, 0, 0, 89))
        canvas.draw_rect((px + 1, py + 8), (px + TILE - 1, py + 15), _rgb("#20293e"))
        canvas.draw_rect((px + 2, py + 9), (px + TILE - 2, py + 14), _rgb("#ffe9b0"))
        canvas.draw_rect((px + TILE // 2 - 1, py + 9), (px + TILE // 2 + 1, py + 14),
                         _rgb("#20293e"))


def _prefab_art_5(canvas, x, y, px, py, dx, dy, prop, pal):
    if prop["w"] >= prop["h"]:
        canvas.draw_rect((px, py), (px + TILE, py + 3), pal["roof"])
        canvas.draw_rect((px, py), (px + TILE, py + 1), pal["hi"])
        _lit_window(canvas, x, y, px, py, 2, 3, 6, 4, 4)
        if dx == prop["w"] - 1:
            canvas.draw_rect((px + 9, py + 6), (px + 14, py + 16), _rgb("#1a2030"))
            canvas.draw_rect((px + 9, py + 6), (px + 14, py + 7), pal["hi"])
        if dx == 0:
            canvas.draw_rect((px + TILE - 1, py), (px + TILE, py + TILE), pal["lo"])
    else:
        if dy == 0:
            canvas.draw_rect((px, py), (px + TILE, py + 3), pal["roof"])
            canvas.draw_rect((px, py), (px + TILE, py + 1), pal["hi"])
            canvas.draw_rect((px, py + TILE - 1), (px + TILE, py + TILE), pal["lo"])
            _lit_window(canvas, x, y, px, py, 2, 5, 6, 5, 4)
        else:
            canvas.draw_rect((px + 5, py + 4), (px + 11, py + 16), _rgb("#1a2030"))
            canvas.draw_rect((px + 5, py + 4), (px + 11, py + 5), pal["hi"])


PREFAB_ARTS = {0: _prefab_art_0, 1: _prefab_art_1, 2: _prefab_art_2,
               3: _prefab_art_3, 4: _prefab_art_4, 5: _prefab_art_5}


def _draw_prefab_cell(canvas, x, y):
    prop = _S.city_prefabs[_S.prefab_map[_S.idx(x, y)] - 1] \
        if _S.prefab_map[_S.idx(x, y)] else None
    if prop is None:
        return
    px, py = x * TILE, y * TILE
    pal = PREFAB_PALETTES[int(js_hash(prop["x"], prop["y"], 12) * len(PREFAB_PALETTES))]
    pal = {k: _rgb(v) for k, v in pal.items()}
    dx, dy = x - prop["x"], y - prop["y"]
    canvas.draw_rect((px, py), (px + TILE, py + TILE), pal["wall"])
    PREFAB_ARTS[prop["v"]](canvas, x, y, px, py, dx, dy, prop, pal)
    if dx == 0:
        canvas.draw_rect((px, py), (px + 1, py + TILE), (255, 255, 255, 18))
    if dx == prop["w"] - 1:
        canvas.draw_rect((px + TILE - 2, py), (px + TILE, py + TILE), (0, 0, 0, 64))
    if dy == prop["h"] - 1:
        canvas.draw_rect((px, py + TILE - 2), (px + TILE, py + TILE), pal["lo"])


def _draw_tile(canvas, x, y, t):
    px, py = x * TILE, y * TILE
    skin = _S.theme_kind == "flesh"

    if t == EMPTY:
        if skin:
            canvas.draw_rect((px, py), (px + TILE, py + TILE), _rgb(SKIN["bg"]))
            bx, by = x >> 1, y >> 1
            if js_hash(bx, by, 6) < 0.38:
                col = (255, 214, 186, 14) if js_hash(bx, by, 7) < 0.5 else (72, 36, 22, 18)
                canvas.draw_rect((px, py), (px + TILE, py + TILE), col)
            if js_hash(x, y, 1) < 0.34:
                canvas.draw_rect((px + int(js_hash(x, y, 2) * 12), py + int(js_hash(x, y, 3) * 12)),
                                 (px + int(js_hash(x, y, 2) * 12) + 2,
                                  py + int(js_hash(x, y, 3) * 12) + 2), _rgb(SKIN["bgSpeck"]))
            if js_hash(x, y, 4) < 0.22:
                canvas.draw_rect((px, py + int(js_hash(x, y, 5) * 14)),
                                 (px + TILE, py + int(js_hash(x, y, 5) * 14) + 1),
                                 _rgb(SKIN["bgLine"]))
            return
        canvas.draw_rect((px, py), (px + TILE, py + TILE), _rgb("#1d1f2a"))
        if js_hash(x, y, 1) < 0.28:
            sx, sy = int(js_hash(x, y, 2) * 12), int(js_hash(x, y, 3) * 12)
            canvas.draw_rect((px + sx, py + sy), (px + sx + 2, py + sy + 2),
                             _rgb("#232633"))
        canvas.draw_rect((px, py), (px + TILE, py + 1), (120, 150, 220, 9))
        canvas.draw_rect((px, py), (px + 1, py + TILE), (120, 150, 220, 9))
        return

    if t == BUILDING:
        if skin:
            canvas.draw_rect((px, py), (px + TILE, py + TILE), _rgb("#54453c"))
            canvas.draw_rect((px + int(js_hash(x, y, 7) * 4), py + int(js_hash(x, y, 8) * 4)),
                             (px + int(js_hash(x, y, 7) * 4) + 8,
                              py + int(js_hash(x, y, 8) * 4) + 6), _rgb("#6b5a4e"))
            canvas.draw_rect((px + 2, py + TILE - 4), (px + TILE - 3, py + TILE - 2),
                             _rgb("#3a2f28"))
            canvas.draw_rect((px + 3 + int(js_hash(x, y, 9) * 6),
                              py + 3 + int(js_hash(x, y, 11) * 6)),
                             (px + 5 + int(js_hash(x, y, 9) * 6),
                              py + 5 + int(js_hash(x, y, 11) * 6)), _rgb("#7d6b5c"))
            return
        if _S.prefab_map[_S.idx(x, y)]:
            _draw_prefab_cell(canvas, x, y)
            return
        canvas.draw_rect((px, py), (px + TILE, py + TILE), _rgb("#333c53"))
        canvas.draw_rect((px, py), (px + TILE, py + 3), _rgb("#4b5773"))
        canvas.draw_rect((px, py + TILE - 2), (px + TILE, py + TILE), _rgb("#1f2433"))
        canvas.draw_rect((px + TILE - 2, py), (px + TILE, py + TILE), (0, 0, 0, 56))
        for i in range(2):
            for j in range(2):
                wx, wy = px + 3 + i * 6, py + 5 + j * 5
                lit = js_hash(x * 4 + i, y * 4 + j, 5) < 0.28
                canvas.draw_rect((wx, wy), (wx + 4, wy + 3),
                                 _rgb("#ffd97a") if lit else _rgb("#161b28"))
        return

    if t == FLESH:
        k = SKIN if skin else SKIN_CITY_FALLBACK

        def solid(nx, ny):
            return _S.get_tile(nx, ny) == FLESH

        canvas.draw_rect((px, py), (px + TILE, py + TILE), _rgb(k["base"]))
        up, dn = solid(x, y - 1), solid(x, y + 1)
        lf, rt = solid(x - 1, y), solid(x + 1, y)
        if not up:
            canvas.draw_rect((px, py), (px + TILE, py + 3), _rgb(k["hi"]))
        if not dn:
            canvas.draw_rect((px, py + TILE - 3), (px + TILE, py + TILE), _rgb(k["lo"]))
        if not lf:
            canvas.draw_rect((px, py), (px + 2, py + TILE), _rgb(k["hi"]))
        if not rt:
            canvas.draw_rect((px + TILE - 2, py), (px + TILE, py + TILE), _rgb(k["lo"]))
        for i in range(3):
            rx = int(js_hash(x, y, 10 + i) * 11)
            ry = int(js_hash(x, y, 20 + i) * 11)
            key = "crease" if js_hash(x, y, 30 + i) < 0.55 else "pore"
            canvas.draw_rect((px + rx, py + ry), (px + rx + 4, py + ry + 2), _rgb(k[key]))
        if not up:
            canvas.draw_rect((px, py), (px + TILE, py + 1), _rgb(k["edge"]))
        if not dn:
            canvas.draw_rect((px, py + TILE - 1), (px + TILE, py + TILE), _rgb(k["edge"]))
        if not lf:
            canvas.draw_rect((px, py), (px + 1, py + TILE), _rgb(k["edge"]))
        if not rt:
            canvas.draw_rect((px + TILE - 1, py), (px + TILE, py + TILE), _rgb(k["edge"]))
        return

    if t == RUBBLE:
        canvas.draw_rect((px, py), (px + TILE, py + TILE), _rgb("#2e2c26"))
        rubble_cols = ["#6b6355", "#7d7466", "#565045", "#8a8070"]
        for i in range(6):
            rx = px + int(js_hash(x, y, 50 + i) * 12)
            ry = py + int(js_hash(x, y, 60 + i) * 12)
            sz = 2 + int(js_hash(x, y, 70 + i) * 3)
            canvas.draw_rect((rx, ry), (rx + sz, ry + sz), _rgb(rubble_cols[i % 4]))
        return

    if t == EXIT:
        canvas.draw_rect((px, py), (px + TILE, py + TILE),
                         _rgb(SKIN["bg"]) if skin else _rgb("#1a1f2a"))
        canvas.draw_rect((px + 3, py + 2), (px + 13, py + 14), _rgb("#134e3a"))
        canvas.draw_rect((px + 4, py + 3), (px + 12, py + 14), _rgb("#4ade80"))
        canvas.draw_rect((px + 6, py + 5), (px + 10, py + 12), _rgb("#d9ffe9"))
        return


def _render_map(canvas):
    for y in range(ROWS):
        for x in range(COLS):
            _draw_tile(canvas, x, y, _S.grid[_S.idx(x, y)])


# ========================================================================
# hazards.js —— 三种危险源
# ========================================================================
def _pick_new_sweep_position():
    sw = _S.sweep
    if sw is None:
        return
    sw["x"] = 1 + int(random.random() * (COLS - sw["w"] - 2))
    sw["y"] = 1 + int(random.random() * (ROWS - sw["h"] - 2))
    sw["dir"] = int(random.random() * 4)
    sw["move_timer"] = 0.0


def _move_sweep():
    sw = _S.sweep
    if _S.state != "playing" or not sw or not sw["active"]:
        return

    def step():
        d = DIRS[sw["dir"]]
        nx, ny = sw["x"] + d[0], sw["y"] + d[1]
        inside = (nx >= 0 and ny >= 0 and nx + sw["w"] <= COLS and ny + sw["h"] <= ROWS)
        return (nx, ny) if inside else None

    pos = step()
    tries = 0
    while pos is None and tries < 3:
        sw["dir"] = (sw["dir"] + 1) % 4
        pos = step()
        tries += 1
    if pos is None:
        return

    sw["x"], sw["y"] = pos
    changed = []
    for y in range(sw["y"], sw["y"] + sw["h"]):
        for x in range(sw["x"], sw["x"] + sw["w"]):
            if x < 0 or y < 0 or x >= COLS or y >= ROWS:
                continue
            if _S.grid[_S.idx(x, y)] == BUILDING:
                _S.set_rubble(x, y, RUBBLE_HEAT_FRESH)
                changed.append((x, y))
    if changed and _GAME_REPAINT_TILE is not None:
        for x, y in changed:
            _GAME_REPAINT_TILE(x, y)


def _update_sweep(dt, game):
    sw = _S.sweep
    if sw is None or _S.state != "playing":
        return
    if sw["active"]:
        sw["timer"] -= dt
        sw["move_timer"] += dt
        if sw["move_timer"] >= sw["move_interval"]:
            sw["move_timer"] -= sw["move_interval"]
            _move_sweep()
        if _S.body_in_rect(sw["x"], sw["y"], sw["w"], sw["h"]):
            game.lose("被扫过的肌肤碾过……")
            return
        if sw["timer"] <= 0:
            sw["active"] = False
            sw["timer"] = SWEEP_HIDDEN_TIME
            _pick_new_sweep_position()
    else:
        sw["timer"] -= dt
        if sw["timer"] <= 0:
            sw["active"] = True
            sw["timer"] = SWEEP_ACTIVE_TIME
            sw["move_timer"] = 0.0
            if _S.body_in_rect(sw["x"], sw["y"], sw["w"], sw["h"]):
                game.lose("被扫过的肌肤碾过……")


def _count_rubble():
    return sum(1 for t in _S.grid if t == RUBBLE)


def _count_live_rubble():
    return sum(1 for i in range(len(_S.rubble_heat))
               if _S.rubble_heat[i] > 0 and _S.grid[i] == RUBBLE)


def _repaint_cells(cells):
    """变化的格子逐个重绘进离屏地形（同帧多次只触发一次纹理上传）。"""
    if not cells or _GAME_REPAINT_TILE is None:
        return
    for ci in cells:
        _GAME_REPAINT_TILE(ci % COLS, ci // COLS)


def _expand_rubble(game):
    if _S.state != "playing" or not _S.rubble_expands:
        return
    heat = _S.rubble_heat
    cap = math.floor(COLS * ROWS * EXPAND_MAX_RATIO)
    if _count_rubble() >= cap:
        return

    srcs, guts = [], []
    for i in range(len(heat)):
        if heat[i] == 0:
            continue
        if _S.grid[i] != RUBBLE:
            heat[i] = 0
            continue
        x, y = i % COLS, i // COLS
        dst = []
        for d in range(4):
            nx, ny = x + DIRS[d][0], y + DIRS[d][1]
            if nx < 0 or ny < 0 or nx >= COLS or ny >= ROWS:
                continue
            ni = _S.idx(nx, ny)
            t = _S.grid[ni]
            if t == BUILDING:
                if random.random() < EXPAND_CHANCE_BUILDING:
                    dst.append(ni)
            elif t == EMPTY:
                if random.random() < EXPAND_CHANCE_EMPTY:
                    dst.append(ni)
        if dst:
            srcs.append(i)
            guts.append(dst)
    if not srcs:
        return

    order = list(range(len(srcs)))
    random.shuffle(order)
    srcs = [srcs[i] for i in order]
    guts = [guts[i] for i in order]

    budget = min(EXPAND_MAX_PER_TICK,
                 max(EXPAND_MIN_PER_TICK,
                     math.ceil(len(srcs) * EXPAND_BUDGET_PER_SOURCE)))
    changed = []
    for s in range(len(srcs)):
        if budget <= 0:
            break
        src = srcs[s]
        next_heat = heat[src] - 1
        heat[src] = 0
        for ni in guts[s]:
            if budget <= 0:
                break
            if _S.grid[ni] != BUILDING and _S.grid[ni] != EMPTY:
                continue
            _S.grid[ni] = RUBBLE
            heat[ni] = next_heat & 0xFF
            budget -= 1
            changed.append(ni)
            x, y = ni % COLS, ni // COLS
            if _S.body_hits_cell(x, y):
                _repaint_cells(changed)
                game.lose("被扩张的瓦砾埋住了……")
                return
    _repaint_cells(changed)


def _update_rubble(dt, game):
    if not _S.rubble_expands:
        return
    if _count_live_rubble() == 0:
        _S.expand_timer = 0.0
        return
    _S.expand_timer += dt
    while _S.expand_timer >= EXPAND_INTERVAL:
        _S.expand_timer -= EXPAND_INTERVAL
        _expand_rubble(game)
        if _S.state != "playing":
            return


def _breathe_curve(b):
    t = (b["phase"] % b["period"]) / b["period"]
    if t < 0.10:
        v = t / 0.10
    elif t < 0.22:
        v = 1.0
    elif t < 0.35:
        v = 1 - (t - 0.22) / 0.13
    else:
        v = 0.0
    return v * v * (3 - 2 * v)


def _breathe_radius(b):
    return max(0.6 * SCALE, b["base_r"] + b["amp"] * _breathe_curve(b))


def _update_breath(dt, game):
    if _S.state != "playing" or not _S.breath_blocks:
        return
    for b in _S.breath_blocks:
        b["phase"] += dt

    target = set()
    for b in _S.breath_blocks:
        r = _breathe_radius(b)
        big_r = math.ceil(r)
        for y in range(max(1, b["cy"] - big_r), min(ROWS - 2, b["cy"] + big_r) + 1):
            for x in range(max(1, b["cx"] - big_r), min(COLS - 2, b["cx"] + big_r) + 1):
                dx, dy = x - b["cx"], y - b["cy"]
                if dx * dx + dy * dy <= r * r:
                    cell_id = _S.idx(x, y)
                    if _S.grid[cell_id] in (EMPTY, FLESH):
                        target.add(cell_id)

    changed = []
    for i in range(len(_S.grid)):
        t = _S.grid[i]
        if t == FLESH and i not in _S.base_flesh and i not in target:
            _S.grid[i] = EMPTY
            changed.append(i)
        elif t == EMPTY and i in target:
            _S.grid[i] = FLESH
            changed.append(i)
            x, y = i % COLS, i // COLS
            if _S.body_hits_cell(x, y):
                _repaint_cells(changed)
                game.lose("被扩张的肌肤吞噬……")
                return
    _repaint_cells(changed)


# ========================================================================
# player.js —— 玩家移动与投弹
# ========================================================================
def _update_player(dt, game):
    p = _S.player
    if p["moving"]:
        p["t"] += dt / p["move_time"]
        if p["t"] >= 1:
            p["t"] = 1.0
            p["px"], p["py"] = float(p["tx"]), float(p["ty"])
            p["moving"] = False
            if _S.body_on_exit(p["tx"], p["ty"]):
                game.win_level()
                return
        else:
            p["px"] = p["sx"] + (p["tx"] - p["sx"]) * p["t"]
            p["py"] = p["sy"] + (p["ty"] - p["sy"]) * p["t"]

    if p["moving"] or _S.state != "playing":
        return

    api = game.api
    dx = dy = 0
    if api.key_down("left") or api.key_down("a"):
        dx = -1
    elif api.key_down("right") or api.key_down("d"):
        dx = 1
    elif api.key_down("up") or api.key_down("w"):
        dy = -1
    elif api.key_down("down") or api.key_down("s"):
        dy = 1
    if not dx and not dy:
        return

    if dx == 1:
        p["facing"] = 1
    elif dx == -1:
        p["facing"] = 3
    elif dy == -1:
        p["facing"] = 0
    elif dy == 1:
        p["facing"] = 2

    nx, ny = p["tx"] + dx, p["ty"] + dy
    if not _S.body_fits(nx, ny):
        return

    p["sx"], p["sy"] = p["px"], p["py"]
    p["tx"], p["ty"] = nx, ny
    p["t"] = 0.0
    p["moving"] = True
    p["move_time"] = (MOVE_TIME * FABRIC_MOVE_MULT
                      if _S.body_has_fabric(nx, ny) else MOVE_TIME)


def _first_ahead(d):
    return BODY if (d[0] or d[1]) > 0 else 1


def _try_bomb(game):
    if _S.state != "playing" or _S.bomb_cooldown > 0:
        return
    _S.bomb_cooldown = BOMB_COOLDOWN

    p = _S.player
    d = DIRS[p["facing"]]
    i0 = _first_ahead(d)
    horiz = d[0] != 0
    hit_something = False

    for i in range(i0, i0 + BOMB_L):
        blocked = False
        for w in range(BOMB_W):
            nx = p["tx"] + d[0] * i + (0 if horiz else w)
            ny = p["ty"] + d[1] * i + (w if horiz else 0)

            if _S.in_sweep(nx, ny):
                _spawn_burst(nx * TILE + 8, ny * TILE + 8, "#ffb08a", 30)
                _shake(1)
                game.lose("炸弹烫到了肌肤……你被发现了")
                return
            t = _S.get_tile(nx, ny)
            if t == FLESH:
                _spawn_burst(nx * TILE + 8, ny * TILE + 8, "#ffb08a", 30)
                _shake(1)
                game.lose("炸弹烫到了肌肤……你被发现了")
                return
            if t == BUILDING:
                blocked = True
                break
            if t == RUBBLE:
                _S.clear_tile(nx, ny)
                if _GAME_REPAINT_TILE is not None:
                    _GAME_REPAINT_TILE(nx, ny)
                _spawn_burst(nx * TILE + 8, ny * TILE + 8, "#c9b48a", 14)
                _shake(0.28)
                hit_something = True
        if blocked:
            break

    if not hit_something:
        nx = p["tx"] + d[0] * (i0 + BOMB_L - 1) + (0 if horiz else BOMB_W - 1)
        ny = p["ty"] + d[1] * (i0 + BOMB_L - 1) + (BOMB_W - 1 if horiz else 0)
        _spawn_burst(nx * TILE + 8, ny * TILE + 8, "#556080", 5)


# 单例游戏的按格重绘钩子（mapgen/hazards/player 模块函数经由它回调舞台重绘）
_GAME_REPAINT_TILE = None


# ========================================================================
# EscapeGiantessGame —— 主流程（game.js/main.js/bridge.js）
# ========================================================================
class EscapeGiantessGame(MiniGame):
    id = "escape_giantess"
    label = "逃离巨大娘"
    description = ("在 68×48 的废墟/秘境里躲开巨大娘的手掌与鞋底，"
                   "炸碎瓦砾、穿过织物，撑到目标关数即胜利；被抓到即失败。")
    params = (
        {"key": "target_level", "label": "目标关数", "type": "int",
         "default": 3, "min": 1, "max": 30},
    )

    def setup(self, config):
        global _GAME_REPAINT_TILE
        try:
            self._target = max(1, int(float(config.get("target_level", TARGET_DEFAULT)
                                          or TARGET_DEFAULT)))
        except (TypeError, ValueError):
            self._target = TARGET_DEFAULT

        self._prev_space = False
        self._banner = None          # {"won":..., "lines":[(text,size,color)], "next":bool}
        self._end_timer = 0.0
        self._terrain = None
        self._fabric_layer = None

        _GAME_REPAINT_TILE = self._repaint_tile

        self._reset_run()
        self._start_level(1)
        self._sync_hud()

    def teardown(self):
        global _GAME_REPAINT_TILE
        _GAME_REPAINT_TILE = None

    # ---------- 关卡流程 ----------
    def _reset_run(self):
        _S.state = "playing"
        _S.level = 1
        _S.shake = 0.0
        _S.bomb_cooldown = 0.0
        _S.time_acc = 0.0
        _fx_clear()

    def _start_level(self, n):
        _S.level = n
        _S.shake = 0.0
        _S.bomb_cooldown = 0.0
        _fx_clear()

        self._terrain = self.api.offscreen(W, H, bg=_rgb(BG_COLOR))
        self._fabric_layer = self.api.offscreen(W, H)

        ok = False
        for _ in range(120):
            rng = mulberry32(int(random.random() * 1e9))
            if rng() < 0.5:
                _gen_city(rng, n)
            else:
                _gen_flesh(rng, n)
            _fabric_generate(rng, n, _S.theme_kind)
            ok = _find_spawn_and_exit(rng)
            if ok:
                break

        if not ok:   # 兜底：极端情况下给一张一定能通关的空房间
            _S.grid = bytearray(COLS * ROWS)
            _S.rubble_heat = bytearray(COLS * ROWS)
            for x in range(COLS):
                _S.set_tile(x, 0, BUILDING)
                _S.set_tile(x, ROWS - 1, BUILDING)
            for y in range(ROWS):
                _S.set_tile(0, y, BUILDING)
                _S.set_tile(COLS - 1, y, BUILDING)
            p = _S.player
            p["tx"], p["ty"] = 2, 2
            p["px"], p["py"] = 2.0, 2.0
            p["moving"] = False
            p["t"] = 0.0
            _S.exit_pos = [COLS - 3, ROWS - 3]
            _S.set_tile(_S.exit_pos[0], _S.exit_pos[1], EXIT)
            _S.theme_name = "城市废墟"
            _S.theme_kind = "city"
            _reset_hazards()
            _S.rubble_expands = True
            _S.clear_fabric()

        _S.player["move_time"] = MOVE_TIME
        _render_map(self._terrain)
        _fabric_build(self._fabric_layer)
        _S.state = "playing"
        self._banner = None
        self._sync_hud()

    def _repaint_tile(self, x, y):
        if self._terrain is not None:
            _draw_tile(self._terrain, x, y, _S.grid[_S.idx(x, y)])

    def _sync_hud(self):
        self.api.hud(f"关卡 {_S.level} · {_S.theme_name} · 目标 {self._target} 关 · 炸弹 ∞")

    # ---------- 结算 ----------
    def win_level(self):
        if _S.state != "playing":
            return
        _S.state = "win"
        cx = _S.exit_pos[0] * TILE + 8
        cy = _S.exit_pos[1] * TILE + 8
        for _ in range(45):
            a = random.random() * math.pi * 2
            sp = (30 + random.random() * 140) * SCALE
            _particles.append({
                "x": cx, "y": cy,
                "vx": math.cos(a) * sp, "vy": math.sin(a) * sp,
                "life": 0.7 + random.random() * 0.8, "max": 1.5,
                "size": (2 + random.random() * 3) * SCALE,
                "color": _rgb("#7dffb0") if random.random() < 0.6 else _rgb("#d9ffe9"),
            })
        if _S.level >= self._target:
            self._banner = {
                "won": True,
                "lines": [("挑战成功", 34, "#7dffb0"),
                          (f"你逃出了第 {_S.level} 层", 16, "#c8d0e0")],
                "next": False,
                "result": {"won": True, "level": _S.level},
            }
        else:
            self._banner = {
                "won": None,
                "lines": [("脱 逃 成 功", 30, "#7dffb0"),
                          (f"你逃出了第 {_S.level} 层废墟", 14, "#c8d0e0"),
                          ("即将进入下一层…", 14, "#ffd97a")],
                "next": True,
            }
        self._end_timer = BANNER_SECONDS

    def lose(self, reason):
        if _S.state != "playing":
            return
        _S.state = "lose"
        _S.lose_reason = reason
        _S.shake = 1.0
        _spawn_death(_S.player["px"] * TILE + TILE, _S.player["py"] * TILE + TILE, 60)
        self._banner = {
            "won": False,
            "lines": [("被 抓 住 了", 32, "#ff4d7d"),
                      (reason, 14, "#ffb3c9"),
                      ("巨大娘的身影吞没了一切。", 14, "#ffb3c9")],
            "next": False,
            "result": {"won": False, "level": _S.level, "reason": reason},
        }
        self._end_timer = BANNER_SECONDS

    # ---------- 主循环 ----------
    def update(self, dt):
        _S.time_acc += dt
        if _S.shake > 0:
            _S.shake = max(0.0, _S.shake - dt * SHAKE_DECAY)
        if _S.bomb_cooldown > 0:
            _S.bomb_cooldown -= dt

        if self._banner is not None:
            self._end_timer -= dt
            _fx_update(dt)
            self._draw()
            if self._end_timer <= 0:
                banner = self._banner
                self._banner = None
                if banner.get("next"):
                    self._start_level(_S.level + 1)
                else:
                    self.api.finish(banner["result"].get("won", False), banner["result"])
            return

        if _S.state == "playing":
            # 投弹（按下沿触发一次）
            space = self.api.key_down("space")
            if space and not self._prev_space:
                _try_bomb(self)
            self._prev_space = space
            if _S.state != "playing":
                self._draw()
                return

            _update_player(dt, self)
            if _S.state == "playing":
                _update_sweep(dt, self)
            if _S.state == "playing":
                _update_rubble(dt, self)
            if _S.state == "playing":
                _update_breath(dt, self)
            _fx_update(dt)
            self._sync_hud()

        self._draw()

    # ---------- 渲染（render.js；画布等比缩放 + 居中 + 镜头抖动） ----------
    def _metrics(self):
        api = self.api
        s = min(api.width / W, api.height / H)
        ox = (api.width - W * s) / 2
        oy = (api.height - H * s) / 2
        sh = _S.shake * 7 * SCALE * s
        shx = (random.random() - 0.5) * sh if _S.shake > 0 else 0.0
        shy = (random.random() - 0.5) * sh if _S.shake > 0 else 0.0
        return s, ox, oy, ox + shx, oy + shy

    def _draw(self):
        api = self.api
        s, _, _, wx, wy = self._metrics()

        def p(x, y):
            return (wx + x * s, wy + y * s)

        def pt(x, y):          # 不随镜头抖（迷雾 / 出口箭头）
            return (wx + x * s, wy + y * s)

        def line_len(v):
            return v * s

        api.draw_rect((0, 0), (api.width, api.height), _rgb(BG_COLOR))
        if self._terrain is not None:
            api.draw_image(self._terrain, p(0, 0), p(W, H))

        # 呼吸血色（径向渐变 → 同心圆近似）
        for b in _S.breath_blocks:
            r = _breathe_radius(b) * TILE + 4 * SCALE
            cx, cy = b["cx"] * TILE + 8, b["cy"] * TILE + 8
            for k, alpha in ((1.0, 30), (0.66, 22), (0.36, 14)):
                api.draw_circle(p(cx, cy), line_len(r * k), (255, 150, 116, alpha))

        if self._fabric_layer is not None:
            api.draw_image(self._fabric_layer, p(0, 0), p(W, H))

        # 出口光晕
        if _S.state in ("playing", "win"):
            ex, ey = _S.exit_pos[0] * TILE + 8, _S.exit_pos[1] * TILE + 8
            r = (10 + math.sin(_S.time_acc * 4.2) * 2.5) * SCALE
            big_r = r + 8 * SCALE
            for k, alpha in ((1.0, 60), (0.62, 40), (0.32, 24)):
                api.draw_circle(p(ex, ey), line_len(big_r * k), (120, 255, 180, alpha))

        if _S.state == "playing":
            self._draw_aim_line(api, p, line_len)
        self._draw_particles(api, p, line_len)
        self._draw_sweep(api, p, line_len)
        if _S.state != "lose":
            self._draw_player(api, p, line_len)

        self._draw_fog(api, pt, line_len)
        if _S.state == "playing":
            self._draw_exit_arrow(api, pt, line_len)
        if self._banner is not None:
            self._draw_banner(api)

    def _draw_aim_line(self, api, p, line_len):
        player = _S.player
        d = DIRS[player["facing"]]
        i0 = _first_ahead(d)
        horiz = d[0] != 0
        danger = False
        stop_at = i0 + BOMB_L - 1
        for i in range(i0, stop_at + 1):
            hit = False
            for w in range(BOMB_W):
                nx = player["tx"] + d[0] * i + (0 if horiz else w)
                ny = player["ty"] + d[1] * i + (w if horiz else 0)
                if _S.in_sweep(nx, ny) or _S.get_tile(nx, ny) == FLESH:
                    danger = True
                    stop_at = i
                    hit = True
                    break
                if _S.get_tile(nx, ny) == BUILDING:
                    stop_at = i - 1
                    hit = True
                    break
            if hit:
                break
        if stop_at < i0:
            return
        dot = 4 * SCALE
        off = TILE / 2 - dot / 2
        color = (255, 77, 109, 140) if danger else (95, 199, 255, 140)
        for i in range(i0, stop_at + 1):
            for w in range(BOMB_W):
                x = (player["tx"] + d[0] * i + (0 if horiz else w)) * TILE + off
                y = (player["ty"] + d[1] * i + (w if horiz else 0)) * TILE + off
                api.draw_rect(p(x, y), (p(x, y)[0] + line_len(dot),
                                        p(x, y)[1] + line_len(dot)), color)

    def _draw_particles(self, api, p, line_len):
        for particle in _particles:
            alpha = min(1.0, particle["life"] / 0.45)
            color = particle["color"]
            if isinstance(color, str):
                color = _rgb(color, round(255 * alpha))
            else:
                color = (color[0], color[1], color[2], round(255 * alpha))
            size = particle["size"]
            x, y = particle["x"] - size / 2, particle["y"] - size / 2
            api.draw_rect(p(x, y), (p(x, y)[0] + line_len(size),
                                    p(x, y)[1] + line_len(size)), color)

    # ---- 扫掠体（多边形剪影；判定框不变） ----
    @staticmethod
    def _rr_points(x, y, w, h, r, seg=4):
        """圆角矩形轮廓采样点（局部坐标）。"""
        r = min(r, w / 2, h / 2)
        pts = []

        def corner(cx, cy, a0):
            for i in range(seg + 1):
                a = a0 + (math.pi / 2) * i / seg
                pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))

        corner(x + r, y + r, math.pi)
        corner(x + w - r, y + r, -math.pi / 2)
        corner(x + w - r, y + h - r, 0)
        corner(x + r, y + h - r, math.pi / 2)
        return pts

    def _hand_paths(self, sw, length, width):
        pw = length * 0.42
        gap = width * 0.06
        fh = (width - gap * 3) / 4
        lens = [0.8, 1.0, 0.92, 0.7]
        flip = (sw["x"] + sw["y"]) % 2 == 0
        paths = [self._rr_points(-length / 2, -width / 2, pw + fh, width, width * 0.26)]
        base = -length / 2 + pw * 0.55
        for i in range(4):
            y0 = -width / 2 + i * (fh + gap)
            seg_len = length / 2 * lens[i if flip else 3 - i] - base
            paths.append(self._rr_points(base, y0, seg_len, fh, fh / 2))
        return paths

    def _shoe_path(self, length, width):
        """鞋底印轮廓：按 render.js 的贝塞尔曲线采样成多边形。"""

        def cubic(p0, p1, p2, p3, n=8):
            return [(p0[0] + (p1[0] - p0[0]) * t / n + (p2[0] - 2 * p1[0] + p0[0]) * t * t / (n * n)
                     + (p3[0] - 3 * p2[0] + 3 * p1[0] - p0[0]) * t ** 3 / n ** 3,
                     p0[1] + (p1[1] - p0[1]) * t / n + (p2[1] - 2 * p1[1] + p0[1]) * t * t / (n * n)
                     + (p3[1] - 3 * p2[1] + 3 * p1[1] - p0[1]) * t ** 3 / n ** 3)
                    for t in range(1, n + 1)]

        def quad(p0, p1, p2, n=8):
            return [(p0[0] + 2 * (p1[0] - p0[0]) * t / n + (p2[0] - 2 * p1[0] + p0[0]) * t * t / (n * n),
                     p0[1] + 2 * (p1[1] - p0[1]) * t / n + (p2[1] - 2 * p1[1] + p0[1]) * t * t / (n * n))
                    for t in range(1, n + 1)]

        def w(fx):
            return fx * length

        def h(fy):
            return fy * width

        pts = [(w(-0.5), 0)]
        pts += cubic((w(-0.5), 0), (w(-0.5), h(-0.20)), (w(-0.42), h(-0.28)),
                     (w(-0.30), h(-0.28)))
        pts += cubic((w(-0.30), h(-0.28)), (w(-0.18), h(-0.28)), (w(-0.14), h(-0.32)),
                     (w(0.00), h(-0.36)))
        pts += cubic((w(0.00), h(-0.36)), (w(0.18), h(-0.43)), (w(0.32), h(-0.50)),
                     (w(0.40), h(-0.50)))
        pts += quad((w(0.40), h(-0.50)), (w(0.50), h(-0.46)), (w(0.50), 0))
        pts += quad((w(0.50), 0), (w(0.50), h(0.46)), (w(0.40), h(0.50)))
        pts += cubic((w(0.40), h(0.50)), (w(0.32), h(0.50)), (w(0.18), h(0.43)),
                     (w(0.00), h(0.36)))
        pts += cubic((w(0.00), h(0.36)), (w(-0.14), h(0.32)), (w(-0.18), h(0.28)),
                     (w(-0.30), h(0.28)))
        pts += cubic((w(-0.30), h(0.28)), (w(-0.42), h(0.28)), (w(-0.50), h(0.20)),
                     (w(-0.5), 0))
        return [pts]

    def _sweep_paths(self, sw):
        """手/鞋的多边形路径（局部坐标，+x = 前进方向）。

        沿轴尺寸 = 世界坐标里**运动方向**上的维度（render.js sweepFrame）：
        横向移动（dir 1/3）用 w，纵向移动（dir 0/2）用 h。"""
        along, across = ((sw["w"], sw["h"]) if sw["dir"] in (1, 3)
                         else (sw["h"], sw["w"]))
        if (sw.get("kind") or "hand") == "shoe":
            return self._shoe_path(along * TILE, across * TILE)
        return self._hand_paths(sw, along * TILE, across * TILE)

    @staticmethod
    def _transform_paths(paths, cx, cy, ang, scale=1.0):
        cos_a, sin_a = math.cos(ang), math.sin(ang)
        out = []
        for path in paths:
            out.append([(cx + (lx * scale) * cos_a - (ly * scale) * sin_a,
                         cy + (lx * scale) * sin_a + (ly * scale) * cos_a)
                        for lx, ly in path])
        return out

    def _draw_sweep(self, api, p, line_len):
        sw = _S.sweep
        if sw is None or _S.state == "win":
            return
        cx = (sw["x"] + sw["w"] / 2) * TILE
        cy = (sw["y"] + sw["h"] / 2) * TILE
        ang = [-math.pi / 2, 0, math.pi / 2, math.pi][sw["dir"]] or 0.0

        if sw["active"]:
            paths = self._sweep_paths(sw)
            screen = self._transform_paths(paths, cx, cy, ang)
            skin_base, skin_edge, skin_hi = _rgb(SKIN["base"]), _rgb(SKIN["edge"]), _rgb(SKIN["hi"])
            # 外扩一圈做剪影描边（同色填充互相覆盖，只留外缘轮廓）
            span = max(sw["w"], sw["h"]) * TILE
            halo = self._transform_paths(paths, cx, cy, ang,
                                         scale=1.0 + 2 * 6.0 * SCALE / span)
            for path in halo:
                api.draw_polygon([p(x, y) for x, y in path],
                                 (skin_edge[0], skin_edge[1], skin_edge[2], 255))
            for path in screen:
                api.draw_polygon([p(x, y) for x, y in path],
                                 (skin_base[0], skin_base[1], skin_base[2], 255))
            # 受光后缘：把整组路径沿 -x 平移小半格再画一层浅色（超出剪影的部分
            # 会被后画的前缘多边形盖住的部分有限，保留轻微的高光感即可）
            back = 4.0 * SCALE
            light = self._transform_paths(paths, cx - back * math.cos(ang),
                                          cy - back * math.sin(ang), ang,
                                          scale=0.96)
            for path in light:
                api.draw_polygon([p(x, y) for x, y in path],
                                 (skin_hi[0], skin_hi[1], skin_hi[2], 90))
        elif sw["timer"] < SWEEP_WARN_TIME:
            t = sw["timer"] / SWEEP_WARN_TIME      # 1 → 0
            paths = self._sweep_paths(sw)
            scale = 0.86 + 0.14 * (1 - t)
            alpha = 0.26 + 0.48 * (max(0.0, 1 - t) ** 1.6)
            for path in self._transform_paths(paths, cx, cy, ang, scale=scale):
                api.draw_polygon([p(x, y) for x, y in path], (8, 7, 12, round(255 * alpha)))

    def _draw_player(self, api, p, line_len):
        player = _S.player
        k = SCALE
        x = round(player["px"] * TILE)
        y = round(player["py"] * TILE)

        def rect(rx, ry, rw, rh, color):
            api.draw_rect(p(x + rx * k, y + ry * k),
                          (p(x + rx * k, y + ry * k)[0] + line_len(rw * k),
                           p(x + rx * k, y + ry * k)[1] + line_len(rh * k)), color)

        rect(3, 13, 10, 3, (0, 0, 0, 102))
        rect(4, 11, 3, 4, _rgb("#1d4ed8"))
        rect(9, 11, 3, 4, _rgb("#1d4ed8"))
        rect(4, 5, 8, 7, _rgb("#38bdf8"))
        rect(4, 10, 8, 2, _rgb("#0284c7"))
        rect(4, 1, 8, 6, _rgb("#fed7aa"))
        rect(4, 1, 8, 2, _rgb("#3f2a20"))
        rect(4, 3, 2, 2, _rgb("#3f2a20"))
        rect(10, 3, 2, 2, _rgb("#3f2a20"))
        ox = 2 * k if player["facing"] == 1 else (-2 * k if player["facing"] == 3 else 0)
        rect(5 + ox / k, 4, 2, 2, _rgb("#1e293b"))
        rect(9 + ox / k, 4, 2, 2, _rgb("#1e293b"))

    def _draw_fog(self, api, pt, line_len):
        player = _S.player
        cx = (player["px"] + BODY / 2) * TILE
        cy = (player["py"] + BODY / 2) * TILE
        half = VIEW_TILES / 2 * TILE
        f = VIEW_FEATHER * TILE
        l = max(0, cx - half)
        r = min(W, cx + half)
        t = max(0, cy - half)
        b = min(H, cy + half)
        fog = (9, 10, 16, round(255 * VIEW_FOG_ALPHA))

        def rect(x0, y0, x1, y1, color):
            api.draw_rect(pt(x0, y0), pt(x1, y1), color)

        if t > 0:
            rect(0, 0, W, t, fog)
        if b < H:
            rect(0, b, W, H, fog)
        if l > 0:
            rect(0, t, l, b, fog)
        if r < W:
            rect(r, t, W, b, fog)
        # 羽化带（两级近似：贴窗缘最暗，向窗内渐亮）
        if f > 0:
            near = (9, 10, 16, round(255 * VIEW_FOG_ALPHA * 0.7))
            far = (9, 10, 16, round(255 * VIEW_FOG_ALPHA * 0.35))
            half_f = f / 2
            rect(l, t, l + half_f, b, near)
            rect(l + half_f, t, l + f, b, far)
            rect(r - f, t, r - half_f, b, far)
            rect(r - half_f, t, r, b, near)
            rect(l + f, t, r - f, t + half_f, far)
            rect(l + f, t + half_f, r - f, t + f, near)
            rect(l + f, b - f, r - f, b - half_f, near)
            rect(l + f, b - half_f, r - f, b, far)

    def _draw_exit_arrow(self, api, pt, line_len):
        player = _S.player
        cx = (player["px"] + BODY / 2) * TILE
        cy = (player["py"] + BODY / 2) * TILE
        half = VIEW_TILES / 2 * TILE
        l = max(0, cx - half)
        r = min(W, cx + half)
        t = max(0, cy - half)
        b = min(H, cy + half)
        ex = (_S.exit_pos[0] + 0.5) * TILE
        ey = (_S.exit_pos[1] + 0.5) * TILE
        if l <= ex <= r and t <= ey <= b:
            return
        inset = 2.5 * TILE
        dx, dy = ex - cx, ey - cy
        tx = (r - inset - cx) / dx if dx > 0 else ((l + inset - cx) / dx if dx < 0
                                                   else math.inf)
        ty = (b - inset - cy) / dy if dy > 0 else ((t + inset - cy) / dy if dy < 0
                                                   else math.inf)
        k = min(tx, ty)
        ax, ay = cx + dx * k, cy + dy * k
        pulse = 0.55 + 0.45 * math.sin(_S.time_acc * 5)
        api.draw_circle(pt(ax, ay), line_len(13 * SCALE * pulse),
                        (125, 255, 176, round(127 * pulse)))
        rot = math.atan2(dy, dx)
        cos_a, sin_a = math.cos(rot), math.sin(rot)
        s = 5 * SCALE

        def chevron(offset):
            pts = [(-s * 0.7 + offset, -s), (s * 0.6 + offset, 0), (-s * 0.7 + offset, s)]
            screen = [(pt(ax + lx * cos_a - ly * sin_a, ay + lx * sin_a + ly * cos_a))
                      for lx, ly in pts]
            alpha = round(255 * (0.45 + 0.55 * pulse))
            api.draw_line(screen[0], screen[1], (74, 222, 128, alpha),
                          thickness=line_len(2.5 * SCALE))
            api.draw_line(screen[1], screen[2], (74, 222, 128, alpha),
                          thickness=line_len(2.5 * SCALE))

        chevron(0)
        chevron(s * 1.3)

    def _draw_banner(self, api):
        banner = self._banner
        alpha = 212 if banner.get("won") is not False else 217
        if banner.get("won"):
            bg = (8, 20, 14, alpha)
        elif banner.get("won") is False:
            bg = (30, 6, 16, alpha)
        else:
            bg = (8, 9, 15, alpha)
        api.draw_rect((0, 0), (api.width, api.height), bg)
        cx = api.width / 2
        cy = api.height / 2
        total = len(banner["lines"])
        for i, (text, size, color) in enumerate(banner["lines"]):
            est_w = len(text) * size * 0.62
            api.draw_text(text, (cx - est_w / 2, cy - (total * 30) / 2 + i * 42),
                          _rgb(color), size=size)
