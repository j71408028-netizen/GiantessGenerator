# 小游戏框架

> **定位**：`mini_game` 触发器的运行时、小游戏包格式与作者 API。
> 触发器动作字段见[数据模型](script.md) §6；宿主端口见[宿主边界](window_host.md) §1。

一句话：**写一个 Python 类就是一个小游戏**——作者不接触 Dear PyGui；
绘制 / 输入 / 帧时钟 / 纹理 / 结算 / 回放全部由框架（`dungeon/window/minigame/`）承担。

## 1. 两种后端

`mini_game` 触发器按 `data/packs/minigames/<game>/manifest.json` 的 `backend` 分派：

| backend | 运行形式 | 通道 |
|---|---|---|
| `py` | **会话窗口内的覆盖层舞台**：叠在背景上的全屏画布，步进挂起（阅读模态 `overlay_open()=="minigame"`），ESC 中止 | `dungeon/window/minigame/stage.py`（帧时钟驱动，唯一允许 DPG 的地方） |
| `web` | 独立顶层窗口（子进程 pywebview/WebView2） | 宿主端口 `launch_mini_game()` → `ui/common/mini_game_host.py` 子进程 |

两种后端的**结果契约一致**：`{"won": bool, ...}` → 同一条 `_finish_mini_game` 管线
（胜负分支 / 叙事行 / 回放记录）。无结果（ESC / 用户关窗 / 游戏崩溃）= 不执行
胜负分支、挂起态解除、副本照常继续。

## 2. 包格式

```
data/packs/minigames/<id>/
├── manifest.json   # {id, name, backend: "py"|"web", entry, params?}
├── game.py         # py 后端：恰一个 MiniGame 子类
└── session.html    # web 后端入口（pywebview 加载；目标关数走 URL 片段 #target=N）
```

内置注册表（`minigame.REGISTRY`）优先于数据包同名 id；`resolve_mini_game(game_id)`
是运行时的唯一解析入口，`list_mini_games()` 供编辑器做候选。

## 3. 作者 API（`dungeon/window/minigame/base.py`）

```python
from dungeon.window.minigame import MiniGame

class MyGame(MiniGame):
    id = "my_game"            # 与包目录 / manifest 一致（契约脚本校验）
    label = "我的游戏"
    description = "..."
    params = (...)            # 可配参数声明（编辑器后续自动生成表单）

    def setup(self, config): ...      # 一次；config = 触发器 action_data
    def update(self, dt): ...         # 每帧；发绘制指令 + 轮询输入
    def teardown(self): ...           # 一次
```

`self.api`（`GameAPI`）服务面：

| 类别 | 方法 |
|---|---|
| 几何 | `width` / `height`（画布像素尺寸，随会话窗口缩放） |
| 绘制 | `draw_rect` / `draw_circle` / `draw_line` / `draw_text` / `draw_image`（每帧发出，运行时负责清屏与提交；纹理按路径缓存） |
| 输入 | `key_down("a"…"z"/"space"/"up"/…)` / `mouse_pos()` / `mouse_down()`（游戏侧是轮询语义；底层由运行时的键鼠 down/release **handler 维护状态表**——DPG 的 `is_key_down` 在手动渲染下不更新，实测不可用，且字母/数字键常量非 ASCII 码） |
| 服务 | `finish(won, result)`（结算，重复调用无效） / `hud(text)` / `notify(msg)` / `read_state("章节"/"介入度"/…)`（只读副本状态快照） |

**硬性约束**（`scripts/check_minigame.py`，CI 门禁）：禁 import DPG / tkinter /
threading / subprocess / PIL；禁 `print`；禁触碰私有面 `api._`。
贴图加载是运行时职责（`api.draw_image("贴图.png", pmin, pmax)`，相对包目录）。

## 4. 运行时语义（stage.py）

- **帧驱动**：挂会话帧时钟（`FrameScheduler.every(EVERY_FRAME)`，key 唯一），
  每帧清屏 → `game.update(dt)`（`dt` 夹取 ≤0.05）→ HUD → 已结算则销毁；
  帧异常按「无结果」收场，不拖垮副本会话。
- **输入**：游戏侧轮询、底层 handler 维护状态表（stage 打开时注册，销毁时注销；键码一律走 `mvKey_*` 常量）。
- **模态**：舞台存续期间 `overlay_open()` 返回 `"minigame"`——步进与自动播放
  挂起，但点击不关闭（鼠标是游戏输入）；ESC 走 `_minigame_escape()` 中止。
- **结算**：`api.finish()` → 销毁舞台 → `on_result`（与 web 后端同一条
  `_on_mini_game_result` → `_finish_mini_game`）→ 胜负分支 + 回放记录
  （`game_result` 字段，回放不进游戏直接复现）。
- **销毁**：随 `_destroy_overlays` 同批清理（帧时钟已停，结果回传自然失效）。

## 5. 自检

| 脚本 | 覆盖 | 命令 |
|---|---|---|
| `check_minigame.py` | 包契约（manifest / py 代码 AST 扫描） | `python scripts/check_minigame.py` |
| `dungeon_autopilot.py --scene mini-game` | web 后端链路（桩包）：结果回传 / 胜负分支 / 回放 | `python scripts/dungeon_autopilot.py --scene mini-game` |
| `dungeon_autopilot.py --scene mini-game-py` | py 后端链路：覆盖层舞台打开 / 结算分支 / ESC 中止 | `python scripts/dungeon_autopilot.py --scene mini-game-py` |
| `dungeon_autopilot.py --scene mini-game-escape` | escape 移植版真渲染冒烟：离屏纹理上传 / 每帧绘制 / 中止 | `python scripts/dungeon_autopilot.py --scene mini-game-escape` |
| `escape_sim.py` | escape 无头模拟（无显示器可跑）：地图生成 / 连通性 / bot 实跑结算 | `python scripts/escape_sim.py` |
| `mini_game_smoke.py` | web 后端真窗口桥接（需显示器 + pywebview） | `python scripts/mini_game_smoke.py` |

内置参考实现：`data/packs/minigames/reaction/`（点击反应，~100 行）；
`data/packs/minigames/escape_giantess/`（逃离巨大娘 py 移植版，~2500 行，
演示离屏画布整层预渲染地形/织物 + 每帧少量动态图元的完整玩法移植，
目录里保留的 `session.html`/`js/` 是原网页版的存档，py 后端不使用）。
