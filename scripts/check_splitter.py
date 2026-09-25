"""S3 自检：内置分句器（含说话人标记解析）（无 GUI，可进 CI 门禁）。

用法：``python scripts/check_splitter.py``
输出：控制台一行 ASCII 结论。

覆盖：
1. 无标记回归：正文无 ``@说话人@`` 时切分行为与解析说话人前一致；
2. 说话人标记：单元起点的完整 ``@X@`` 解析为 ``DisplayUnit.speaker`` 并从正文剥离；
3. 引号闭合断点：一段对话保持为单个显示段落；
4. 流式幂等：逐字累加重算 ``split_stream_units``，已完成单元集合单调一致；
   未闭合的半截标记在尾部被抑制显示；
5. 中间标记不解析（原样保留）；落盘净化 ``strip_speaker_markers``。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dungeon.splitter import (DisplayUnit, split_full_text, split_stream_units,
                              strip_speaker_markers)

failures = []
total = 0


def check(name, ok, extra=""):
    global total
    total += 1
    print(("  OK   " if ok else "  FAIL ") + name + (f"  <{extra}>" if extra and not ok else ""))
    if not ok:
        failures.append(name)


# ---------------- 1. 无标记回归 ----------------
units, tail = split_stream_units("你好世界，这里是平原。她转身离开了！")
check("无标记：句末标点切分",
      [u.text for u in units] == ["你好世界，这里是平原。", "她转身离开了！"] and tail == "",
      str((units, tail)))
check("无标记：speaker 全为 None", all(u.speaker is None for u in units))

units = split_full_text("第一条消息；第二条消息……")
check("无标记：分号归一为句号、破折号归一为省略号",
      [u.text for u in units] == ["第一条消息。", "第二条消息……"], str(units))

# ---------------- 2. 说话人标记 ----------------
text = "@李队长@“桥还没塌，先撤居民。”"
units, tail = split_stream_units(text)
check("标记：引号闭合切出对话单元",
      units == [DisplayUnit(speaker="李队长", text="“桥还没塌，先撤居民。”")],
      str((units, tail)))

units = split_full_text("@李队长@“先撤居民。”人群骚动了起来。她低声笑了。")
check("标记：对话与叙述混排",
      units == [DisplayUnit(speaker="李队长", text="“先撤居民。”"),
                DisplayUnit(speaker=None, text="人群骚动了起来。"),
                DisplayUnit(speaker=None, text="她低声笑了。")],
      str(units))

units = split_full_text("远处传来呼喊。\n@居民@“她来了！快跑！”")
check("标记：换行断点后接对话",
      units == [DisplayUnit(speaker=None, text="远处传来呼喊。"),
                DisplayUnit(speaker="居民", text="“她来了！快跑！”")],
      str(units))

# ---------------- 3. 引号闭合断点 ----------------
units = split_full_text("@李队长@“任务完成了，我们走吧。”随后的欢呼持续了很久。")
check("标记：单段对话不被拆成两截",
      len(units) == 2 and units[0].speaker == "李队长"
      and units[0].text == "“任务完成了，我们走吧。”",
      str(units))

# ---------------- 4. 流式幂等 ----------------
full = "@李队长@“桥还没塌，先撤居民。”人群骚动了起来。"
seen_units = []
consistent = True
for i in range(1, len(full) + 1):
    units, tail = split_stream_units(full[:i])
    # 已完成单元与此前观察到的前缀一致（重算幂等，不回退不重复）
    if [u.text for u in units[:len(seen_units)]] != seen_units:
        consistent = False
        break
    seen_units = [u.text for u in units]
check("流式：逐字累加重算幂等", consistent, str(seen_units))
check("流式：最终定格与 split_full_text 一致",
      seen_units == [u.text for u in split_full_text(full)], str(seen_units))

# 半截标记：起点 @ 未闭合时抑制显示，收全后解析
units, tail = split_stream_units("@李队长")
check("流式：未闭合标记抑制显示", units == [] and tail == "", str((units, tail)))
units, tail = split_stream_units("@李队长@“桥")
check("流式：标记闭合后解析并显示正文", tail == "“桥", str((units, tail)))

# ---------------- 5. 中间标记不解析 + 落盘净化 ----------------
units = split_full_text("有人在弹幕里写着@某人@，没人当真。")
check("中间标记不解析", units == [DisplayUnit(speaker=None,
                                               text="有人在弹幕里写着@某人@，没人当真。")],
      str(units))

check("净化：剥离全部标记",
      strip_speaker_markers("@李队长@“先撤。”人群骚动。") == "“先撤。”人群骚动。",
      strip_speaker_markers("@李队长@“先撤。”人群骚动。"))
check("净化：无标记原样返回",
      strip_speaker_markers("普通叙述。") == "普通叙述。")
check("净化：空串安全", strip_speaker_markers("") == "")

# ---------------- 结论 ----------------
if failures:
    print(f"[check_splitter] FAILED {len(failures)}/{total}: {failures}")
    sys.exit(1)
print(f"[check_splitter] PASSED {total}/{total}")
