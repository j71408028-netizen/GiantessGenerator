# 小游戏框架

> **定位**：`mini_game` 触发器的运行时、小游戏包格式与作者 API。
> 触发器动作字段见[数据模型](../Dungeon/script.md) §6；宿主端口见[宿主边界](../Dungeon/window_host.md) §1。

一句话：**写一个 Python 类就是一个小游戏**——作者不接触窗口框架；
绘制 / 输入 / 帧时钟 / 纹理 / 结算 / 回放全部由框架（`dungeon/window/minigame/`）承担。

## 1. 两种后端

`mini_game` 触发器按 `data/packs/minigames/<game>/manifest.json` 的 `backend` 分派：

| backend | 运行形式 | 通道 |
|---|---|---|
| `py` | **会话窗口内的覆盖层舞台**：帧末绘制的一个铺满客户区的顶层窗口（压在工具条之上），步进挂起（阅读模态 `overlay_open()=="minigame"`），ESC 中止 | `dungeon/window/minigame/stage.py`（由 `ui._draw_frame` 帧末调用 `render`） |
| `web` | 独立顶层窗口（子进程 pywebview/WebView2） | 宿主端口 `launch_mini_game()` → `ui/hosting/mini_game_host.py` 子进程 |

两种后端的**结果契约一致**：`api.finish(result)` 的 `result`（整数标量 / dict）→
同一条 `_finish_mini_game` 管线（返回值记入 `trigger_choices` / 叙事行 / 回放记录
`game_result`）。小游戏**不区分胜负**，分支由其他触发器按「选择:触发器名」判定。
无结果（ESC / 用户关窗 / 游戏崩溃）= 不记返回值、挂起态解除、副本照常继续。

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
| 输入 | `key_down("a"…"z"/"space"/"up"/…)` / `mouse_pos()` / `mouse_down()`（游戏侧是轮询语义；底层是 imgui 的 `is_key_down` / `io.mouse_pos` / `is_mouse_down`，每帧状态新鲜，不需要自己维护状态表） |
| 服务 | `finish(won, result)`（结算，重复调用无效） / `hud(text)` / `notify(msg)` / `read_state("章节"/"介入度"/…)`（只读副本状态快照） |

**硬性约束**（`tests/check_minigame.py`，CI 门禁）：禁 import DPG / tkinter /
threading / subprocess / PIL；禁 `print`；禁触碰私有面 `api._`。
贴图加载是运行时职责（`api.draw_image("贴图.png", pmin, pmax)`，相对包目录）。

## 4. 运行时语义（stage.py）

- **帧驱动**：不再挂帧时钟——由 `ui._draw_frame` 在**所有顶层浮层之后**调用
  `stage.render(w, h)`（顶层窗口，铺满客户区，因而压在工具条 / 选项弹窗之上）；
  每帧 `game.update(dt)`（`dt` 夹取 ≤0.05）→ 边框 / HUD → 已结算则销毁；
  帧异常按「无结果」收场，不拖垮副本会话。
- **输入**：游戏侧轮询 `GameAPI`，底层直接问 imgui（`is_key_down` / `io.mouse_pos`
  / `is_mouse_down`）——立即模式下每帧状态都是新鲜的，不需要 handler 状态表。
  绘制走 `imgui.get_window_draw_list()`，裁剪矩形压在画面区域（play rect）上。
- **模态**：舞台存续期间 `overlay_open()` 返回 `"minigame"`——步进与自动播放
  挂起，但点击不关闭（鼠标是游戏输入）；舞台窗口铺满客户区，工具条不会被误点；
  ESC 走 `_minigame_escape()` 中止。
- **结算**：`api.finish()` → 销毁舞台 → `on_result`（与 web 后端同一条
  `_on_mini_game_result` → `_finish_mini_game`）→ 返回值记入 `trigger_choices` +
  回放记录（`game_result` 字段，回放不进游戏直接复现）。
- **离屏画布上传（单张 GL 纹理 + 脏矩形增量）**：`offscreen()` 出来的画布由宿主
  自持**一张 GL 纹理**（`glGenTextures` + `glTexImage2D`），每帧按绘制包围盒记脏，
  用 `glTexSubImage2D` 只重传变化的那几块矩形（整张 1088×768 重传实测 0.294 ms，
  见 `.workbuddy/p6_probe/probe_gltex.py`）。纹理经 `imgui.ImTextureRef(int(gl_name))`
  显示（OpenGL3 后端的 `ImTextureID` 就是 GLuint）；画布被整块换掉（换关）时旧纹理
  按弱引用自动回收。游戏侧无需任何改动，仍旧是「画布内容变化后下一次 `draw_image`
  自动重传」的语义。见 `dungeon/window/gl_texture.py` 与
  `.workbuddy/imgui-migration/52-analysis-gpu-texture-native.md` §6。
- **销毁**：随 `_destroy_overlays` 同批清理（此时帧时钟已停、GL 上下文已释放，
  因此删 GL 纹理按 `_frame.running` 跳过，只丢句柄；结果回传自然失效）。

## 5. 自检

| 脚本 | 覆盖 | 命令 |
|---|---|---|
| `check_minigame.py` | 包契约（manifest / py 代码 AST 扫描） | `python tests/check_minigame.py` |
| `dungeon_autopilot.py --scene mini-game` | web 后端链路（桩包）：结果回传 / 胜负分支 / 回放 | `python scripts/dungeon_autopilot.py --scene mini-game` |
| `dungeon_autopilot.py --scene mini-game-py` | py 后端链路：覆盖层舞台打开 / 结算分支 / ESC 中止 | `python scripts/dungeon_autopilot.py --scene mini-game-py` |
| `dungeon_autopilot.py --scene mini-game-escape` | escape 移植版真渲染冒烟：离屏 GL 纹理上传 / 每帧绘制 / 中止 | `python scripts/dungeon_autopilot.py --scene mini-game-escape` |
| `escape_sim.py` | escape 无头模拟（无显示器可跑）：地图生成 / 连通性 / bot 实跑结算 | `python scripts/escape_sim.py` |
| `smoke_mini_game.py` | web 后端真窗口桥接（需显示器 + pywebview） | `python tests/smoke_mini_game.py` |

内置参考实现：`data/packs/minigames/reaction/`（点击反应，~100 行）；
`data/packs/minigames/escape_giantess/`（逃离巨大娘 py 移植版，~2500 行，
演示离屏画布整层预渲染地形/织物 + 每帧少量动态图元的完整玩法移植，
目录里保留的 `session.html`/`js/` 是原网页版的存档，py 后端不使用）。
