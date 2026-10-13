"""TriggerHandler / EndingHandler 的最小宿主替身（两支触发器守卫共用）。

真宿主（``dungeon/window/base.py``）加字段时只需要改这里一处。条件恒真，副作用
全部落进 ``*_calls`` 列表，不建任何窗口、不出声：

- ``check_trigger_timing.py`` 传 ``record_goto=True``：``_enter_chapter`` 只记录
  章节名（那个守卫要断言 goto 派发顺序）；
- ``check_effect_override.py`` 用缺省（``record_goto=False``）：走 EndingHandler
  的真实换章实现，把四个维度交还章节默认——那正是它要验的。

两个守卫各自的私有替身（``_Dummy`` / ``_Host``）曾各写一遍这二十来个属性，
真宿主加一个字段要同步两处；2026-10-13 合并到此处。
"""

from dungeon.window.ending import EndingHandler  # noqa: E402
from dungeon.window.triggers import TriggerHandler  # noqa: E402


class _State:
    """dungeon_state 的最小切片。"""

    total_steps = 0
    steps_since_trigger = 0
    chapter_steps = 0


class _Bgm:
    """BgmPlayer 替身：只记录 play() 收到的 spec。"""

    def __init__(self):
        self.calls = []

    def play(self, spec):
        self.calls.append(dict(spec or {}))


class _Speech:
    """SpeechDirector 替身：只记录 set_effect() 收到的效果。"""

    def __init__(self):
        self.effects = []

    def set_effect(self, spec=None):
        self.effects.append(dict(spec or {}))


#: effect_override 用的两章方案：A 带全默认，B 全空
CHAPTERS_AB = [
    {"name": "A",
     "background": {"image_path": "a.png", "filter_effect": "blur"},
     "bgm": {"path": "a.mp3", "level": 3},
     "voice_fx": {"her": {"preset": "giant_boom", "intensity": 60}}},
    {"name": "B"},
]

#: effect_override 的章节 A 敏感效果默认（性格缺省时 = 强度 × 客观影响）
SENSITIVITY_A = [{"attr": "介入度", "amount": 1.0}]


class TriggerHost(TriggerHandler, EndingHandler):
    """触发器判定的最小宿主：``__init__`` 参数见各调用方，字段含义对齐真宿主。"""

    def __init__(self, triggers=None, chapters=None, current_chapter="",
                 sensitivity_effects=None, *, record_goto=False):
        self.triggers = triggers or []
        self.triggered_names = set()
        self.fired_triggers = set()
        self._round_fired = set()
        self.dungeon_state = _State()
        self.chapters = chapters if chapters is not None else [{"name": "X"}]
        self.current_chapter = current_chapter
        self.chapter_sensitivity_effects = [dict(e) for e in (sensitivity_effects or [])]
        self.pending_insertions = []
        self.pending_option = None
        self.pending_mini_game = None
        self.pending_ending = None
        self.effect_overrides = []
        self.replay_data = []
        self.trigger_choices = {}
        self.personality = None
        self.character = None
        # 生效值先按空章节落；下面若指明了当前章节，则按该章默认播种
        self.current_background_path = ""
        self._applied_visual_filter = None
        self._applied_bgm_level = None
        self.chapter_voice_fx = {}
        self.current_voice_fx = {}
        self._applied_voice_fx = {}
        self._seed_applied_from_chapter()
        # 副作用记录位
        self.fx_calls = []
        self.goto_calls = []
        self.ending_calls = []
        self.bg_calls = []
        self._bgm = _Bgm()
        self._speech = _Speech()
        self._record_goto = record_goto
        self._window_fx = self      # 窗口视效打回自己，落进 fx_calls

    # ---------------- 播种：生效值镜像当前章节默认 ----------------
    def _seed_applied_from_chapter(self):
        chapter = next((c for c in self.chapters
                        if c.get("name") == self.current_chapter), None)
        if chapter is None:
            return
        background = chapter.get("background") or {}
        self.current_background_path = background.get("image_path", "")
        self._applied_visual_filter = background.get("filter_effect")
        bgm = chapter.get("bgm") or {}
        self._applied_bgm_level = bgm.get("level")
        self.chapter_voice_fx = dict(chapter.get("voice_fx") or {})
        self.current_voice_fx = dict(self.chapter_voice_fx)
        self._applied_voice_fx = dict(self.chapter_voice_fx)

    # ---------------- 替身：条件恒真、副作用只记录 ----------------
    def evaluate_condition(self, condition, state):
        return True

    def change_background(self, image_path, smooth, filter_effect):
        self.bg_calls.append((image_path, smooth, filter_effect))

    def _current_action_points(self):
        # 真宿主在 window/base.py 提供；挑战模式无角色时返回 None
        return getattr(self.character, "action_points", None)

    def _start_option_generation(self):
        pass

    def _start_ending_generation(self):
        self.ending_calls.append(True)

    def start(self, fx_key, duration, intensity):
        self.fx_calls.append((fx_key, duration, intensity))

    def _enter_chapter(self, name, *args, **kw):
        if self._record_goto:
            self.current_chapter = name
            self.goto_calls.append(name)
            return True
        return super()._enter_chapter(name, *args, **kw)
