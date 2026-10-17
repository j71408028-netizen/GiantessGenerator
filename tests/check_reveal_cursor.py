"""自检：上屏游标（仿流式 / 真流式共用的限速真相源）。

用法：``python tests/check_reveal_cursor.py``

无 GUI：不建任何窗口、不碰 DPG、不碰 tkinter。把 ``DungeonWindowUI`` 的游标
三件套（``_reveal_start`` / ``_reveal_extend`` / ``_reveal_snap``）与点击机
（``_try_advance_paragraph``）挂到一支最小宿主上，用真 ``FrameScheduler``
的 ``tick(now=...)`` 显式推时钟，断言：

1. **限速恒定**：不论目标文本一次给全（仿流式）还是分多次 chunk 到达（真流式），
   游标都恰好按 ``_REVEAL_CHARS_PER_SECOND`` 推进——真流式下 chunk 再密也不超速；
2. **真流式解耦**：chunk 只延长 target，不直接写 item；游标位置由时钟决定；
3. **三态点击机**：① 上屏不足 ``_REVEAL_MIN_CHARS`` 字 → 吞掉点击、游标不动、
   队列不减；② 未补满 → 补满到 100% 且**不切段**；③ 已补满 → 才切下一段，
   且每切一段推进一个单元（狂点不会掀翻队列）；
4. **收尾登记**：游标走到 target 末尾时清空 ``self._reveal`` 并登记
   ``_advance_due``（自动播放读它决定何时推进）。

宿主替身只实现游标路径真正触到的成员：``_frame``、``story_history``、
``_pending_units``、``_schedule_text_update``、``_update_text_display``、
``_speak_item``。真宿主加字段时同步这里一处即可。
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_report_path = os.path.join(tempfile.mkdtemp(prefix="reveal_cursor_"), "report.txt")
_log = open(_report_path, "w", encoding="utf-8")
sys.stdout = _log
sys.stderr = _log

failures = []
total = 0


def check(name, ok, extra=""):
    global total
    total += 1
    print(("  OK   " if ok else "  FAIL ") + name
          + (f"  <{extra}>" if extra and not ok else ""))
    if not ok:
        failures.append(name)


from dungeon.window.frame import FrameScheduler  # noqa: E402
from dungeon.window.ui import (  # noqa: E402
    DungeonWindowUI,
    _AUTOPLAY_DWELL,
    _AUTOPLAY_DWELL_PER_CHAR,
    _REVEAL_CHARS_PER_SECOND,
    _REVEAL_MIN_CHARS,
    _REVEAL_TICK_SECONDS,
)


class _Unit:
    """分句器切出的显示段落替身（只带游标路径读到的字段）。"""

    def __init__(self, text, speaker=None):
        self.text = text
        self.speaker = speaker


class _CursorHost(DungeonWindowUI):
    """只装配游标相关成员的最小宿主；不调 ``__init__``（那会建窗口）。"""

    def __init__(self):
        self._frame = FrameScheduler()
        self.story_history = []
        self._pending_units = []
        self._reveal = None
        self._advance_due = 0.0
        self._closing = False
        self._text_updates = 0
        self._spoken = []

    # 上屏投递：真实现走帧队列，这里只记次数（不碰 DPG）
    def _schedule_text_update(self):
        self._text_updates += 1

    # 这两个真实现会碰 DPG，替身里空转
    def _update_text_display(self):
        pass

    def _speak_item(self, item):
        if item.get("speaker"):
            self._spoken.append((item.get("speaker"), item.get("text")))


def _clock_base():
    """帧任务用真 ``time.monotonic()`` 做 due_at 基准，推进时必须与之同源。

    不能从 0 起（0 对 monotonic 而言是远古过去，任务永远不到期）。
    """
    import time
    return time.monotonic()


def _advance_clock(host, seconds, start=None):
    """把帧时钟推进 ``seconds`` 秒（按 ``_REVEAL_TICK_SECONDS`` 步进）。

    ``start`` 是上一次返回的合成时刻；不传则锚到当前真 monotonic。

    **推时钟一律"接着上一次返回的时刻累加"**：``FrameScheduler`` 的周期任务走
    ``due_at += interval`` 这条浮点路径，测试若改用重算式 ``base + i * 步长`` 生成
    时刻，两条路径会差 1 ULP，个别 tick 被判成"未到期"而跳过，1 秒里少打一次字
    （实测 20 次触发变 19 次，游标 30 字变 28 字）。累加式让两次比较的浮点结果
    只差一个恒定的起点偏移，不会误判。
    """
    t = _clock_base() if start is None else start
    steps = int(round(seconds / _REVEAL_TICK_SECONDS))
    for _ in range(steps):
        t += _REVEAL_TICK_SECONDS
        host._frame.tick(now=t)
    return t


# ---------------- 1. 仿流式：一次给全，速率恒定 ----------------
# 150 字文本，30 字/秒 → 5 秒走完。断言 1 秒后恰好 ~30 字。
TEXT = "".join(chr(0x4e00 + i % 200) for i in range(150))

h = _CursorHost()
item = {"type_str": "", "text": ""}
h.story_history.append(item)
h._reveal_start(item, TEXT)

check("起手：游标已激活且从 0 开始",
      h._reveal_active() and h._reveal["pos"] == 0 and h._reveal["target"] == TEXT)

_t1 = _advance_clock(h, 1.0)
pos_1s = h._reveal["pos"]
check("仿流式 1 秒后推进约 30 字（±1 字）",
      abs(pos_1s - int(_REVEAL_CHARS_PER_SECOND)) <= 1, f"pos={pos_1s}")

# 第二秒接着第一秒的合成时刻累加，而不是重读真 monotonic 再 +1（见 _advance_clock）。
_advance_clock(h, 1.0, start=_t1)
pos_2s = h._reveal["pos"]
check("仿流式 2 秒后约 60 字（±1 字）",
      abs(pos_2s - 2 * int(_REVEAL_CHARS_PER_SECOND)) <= 1, f"pos={pos_2s}")
check("item 文本随游标增长（上屏的是前缀）",
      item["text"] == TEXT[:pos_2s], f"len={len(item['text'])} pos={pos_2s}")

# 走完剩余：150 字 / 30 = 5 秒
_advance_clock(h, 4.0, start=_t1 + 1.0)
finish_pos = 150
check("走完：item 全文上屏", item["text"] == TEXT)
check("走完：游标已注销（self._reveal 清空）", h._reveal is None)
check("走完：登记了自动播放到期时刻（_advance_due 被设过）",
      h._advance_due > 0.0)

# 收尾时刻的语义：DWELL + 每字追加
expected_due_delta = _AUTOPLAY_DWELL + _AUTOPLAY_DWELL_PER_CHAR * len(TEXT)
import time as _time  # noqa: E402
check("_advance_due ≈ now + DWELL + PER_CHAR×len",
      abs((h._advance_due - _time.monotonic()) - expected_due_delta) < 0.5,
      f"delta={h._advance_due - _time.monotonic():.3f} want={expected_due_delta:.3f}")

# ---------------- 2. 真流式：chunk 只延长 target，不超速 ----------------
h2 = _CursorHost()
item2 = {"type_str": "", "text": ""}
h2.story_history.append(item2)
head = "甲"
h2._reveal_start(item2, head)

# 玩家/分句器在 1 秒内连推 10 个 chunk，每次塞进 30 字（总计 300 字，远超速率）
full = head
_tb = _clock_base()
for i in range(10):
    full += "".join(chr(0x4e00 + (i * 30 + j) % 200) for j in range(30))
    h2._reveal_extend(item2, full)
    # 累加式推时钟（``t += tick``），与 due_at 的 ``+= interval`` 同一条浮点路径。
    # 原先的 ``_tb + i * 0.1`` 重算式差 1 ULP：10 个 chunk 里会有一个 tick 被判未到期。
    _tb = _advance_clock(h2, 0.1, start=_tb)

check("真流式：target 被延长到最新全长",
      h2._reveal["target"] == full, f"target={len(h2._reveal['target'])} want={len(full)}")
pos_stream = h2._reveal["pos"]
check("真流式 1 秒后仍只推进约 30 字（不被快 chunk 带飞）",
      abs(pos_stream - int(_REVEAL_CHARS_PER_SECOND)) <= 1, f"pos={pos_stream}")
check("真流式：已上屏是 target 前缀、且落后于 target",
      item2["text"] == full[:pos_stream] and pos_stream < len(full),
      f"pos={pos_stream} len={len(full)}")

# ---------------- 3. 三态点击机 ----------------
# ① 不足 _REVEAL_MIN_CHARS → 吞掉
h3 = _CursorHost()
it3 = {"type_str": "", "text": ""}
h3.story_history.append(it3)
h3._pending_units.append(_Unit("下一段"))
h3._reveal_start(it3, "这是一段足够长的显示段落文本内容用于测试点击推进")

# 只推 2 tick（约 3 字 < 5）
_t3 = _advance_clock(h3, 2 * _REVEAL_TICK_SECONDS)
pos_before = h3._reveal["pos"]
consumed = h3._try_advance_paragraph()
check("① 上屏不足 %d 字：点击被吞掉（返回 True）" % _REVEAL_MIN_CHARS, consumed)
check("① 游标未被补满（pos 不变、仍激活）",
      h3._reveal is not None and h3._reveal["pos"] == pos_before,
      f"pos={h3._reveal['pos'] if h3._reveal else None}")
check("① 队列未减（没切段）", len(h3._pending_units) == 1)

# ② 已过阈值但未补满 → 补满，不切段
_advance_clock(h3, 0.5, start=_t3)
pos_mid = h3._reveal["pos"]
check("② 前置：已超过阈值且未补满",
      pos_mid >= _REVEAL_MIN_CHARS and pos_mid < len(h3._reveal["target"]),
      f"pos={pos_mid} len={len(h3._reveal['target'])}")
consumed = h3._try_advance_paragraph()
check("② 点击被消化（返回 True）", consumed)
check("② 段落已补满 100%", it3["text"] == h3._reveal["target"]
      if h3._reveal else it3["text"] == "这是一段足够长的显示段落文本内容用于测试点击推进")
check("② 补满后游标注销", h3._reveal is None)
check("② 队列仍为 1（本次不切段）", len(h3._pending_units) == 1)

# ③ 已补满 → 再点才切段
consumed = h3._try_advance_paragraph()
check("③ 已补满：点击切到下一段（消化）", consumed)
check("③ 队列减 1", len(h3._pending_units) == 0)
check("③ 新段成为当前游标",
      h3._reveal is not None and h3._reveal["item"] is h3.story_history[-1] and
      len(h3.story_history) == 2)
check("③ 新段已起手（target 为下一段全文）",
      h3._reveal["target"] == "下一段")

# ③b 队列空时放行给生成链
h4 = _CursorHost()
it4 = {"type_str": "", "text": ""}
h4.story_history.append(it4)
h4._reveal_start(it4, "短")
_advance_clock(h4, 1.0)
# 走完后 _reveal 已空、队列也空 → 应放行（返回 False）
check("③b 游标收尾 + 队列空：点击放行给生成链（返回 False）",
      h4._try_advance_paragraph() is False,
      f"reveal={h4._reveal} pending={h4._pending_units}")

# ---------------- 4. 狂点不掀翻队列 ----------------
h5 = _CursorHost()
units = [_Unit("段落%d" % i) for i in range(5)]
h5._pending_units.extend(units)

# 先起第一段（模拟生成链已上屏首段）
first = {"type_str": "", "text": ""}
h5.story_history.append(first)
h5._reveal_start(first, "第一段")

# 推过阈值（≥5 字）但远未补满，然后连点 20 次
_advance_clock(h5, 0.5)
for _ in range(20):
    h5._try_advance_paragraph()

# 预期：第一次点击补满；之后每点一次切一段，但每切一段新段是空的（pos=0），
# 不足 5 字 → 后续点击全被吞掉。所以队列最多只被推进 1 个。
check("狂点 20 次：队列最多减 1（新段未起手前吞掉后续点击）",
      len(h5._pending_units) >= len(units) - 1,
      f"remaining={len(h5._pending_units)}")
check("狂点 20 次：story_history 未爆量（≤ 2 段）",
      len(h5.story_history) <= 2, f"n={len(h5.story_history)}")

# ---------------- 5. _reveal_snap(item) 条件生效 ----------------
h6 = _CursorHost()
a = {"type_str": "", "text": ""}
b = {"type_str": "", "text": ""}
h6._reveal_start(a, "AAAAAAA")
_advance_clock(h6, _REVEAL_TICK_SECONDS)
h6._reveal_snap(b)   # 不属于当前游标 → 不应生效
check("_reveal_snap(别的 item) 不生效（游标仍在）", h6._reveal is not None)
h6._reveal_snap(a)   # 属于当前游标 → 补满
check("_reveal_snap(当前 item) 补满并注销",
      a["text"] == "AAAAAAA" and h6._reveal is None)

# ---------------- 6. 常量契约 ----------------
check("_REVEAL_CHARS_PER_SECOND 为 30（设计值）", _REVEAL_CHARS_PER_SECOND == 30.0)
check("_REVEAL_MIN_CHARS 为 5（0.17 秒 @30 字/秒）", _REVEAL_MIN_CHARS == 5)
check("_REVEAL_TICK_SECONDS 让每 tick 推进约 1.5 字",
      abs(_REVEAL_CHARS_PER_SECOND * _REVEAL_TICK_SECONDS - 1.5) < 0.01)

# ---------------- 收尾 ----------------
_log.flush()
print("\n[check_reveal_cursor] %s %d/%d"
      % ("PASSED" if not failures else "FAILED", total - len(failures), total))
print("[check_reveal_cursor] report: %s" % _report_path)
sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__
if failures:
    print("FAILURES: %s" % ", ".join(failures))
    sys.exit(1)
print("check_reveal_cursor.py OK")
