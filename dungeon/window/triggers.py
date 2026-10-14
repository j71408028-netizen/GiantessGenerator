"""触发器判定、章节进入、章节效果短暂覆盖与插入段落。

触发器 = 条件（所在章节 + 前置触发器 + 条件规则） + 一个动作。
章节自身没有条件：进入/离开都由触发器的 ``goto`` 动作执行，章节只描述
身处其中时的背景与敏感效果。``effect`` 动作则给这套环境加一层**限时覆盖**
（背景滤镜 / 音乐音量 / 语音效果 / 敏感效果），走完步数后交还章节默认值，
覆盖项的模型与合成规则见 ``dungeon/overrides.py``。
"""

import random

from dungeon import process_log
from dungeon.actions import (ACTION_POSITIONS, EMPTY_ACTIONS, ENDING_ACTION,
                             MINI_GAME_DEFAULT_ID, WINDOW_FX_KEYS,
                             normalize_action_type)
from dungeon.audio.chapters import (DEFAULT_BGM_LEVEL, bgm_level, chapter_bgm,
                              find_chapter, is_terminating_chapter,
                              matches_scope, sensitivity_amount)
from dungeon.overrides import (active_override, describe as describe_overrides,
                               invalid_filter, parse_overrides)
from dungeon.models import DungeonState, DungeonTextType
from dungeon.response import canonical_history_entry
from dungeon.rules import TriggerRules
from dungeon.window.fx import parse_fx_params
from dungeon.window.minigame import BACKEND_PY, resolve_mini_game


def _mini_game_return_value(result) -> int:
    """小游戏结算结果的整数返回值。

    小游戏不再区分胜负，结算只回传一个整数（语义由游戏自定，如达成关数 /
    命中数）：dict 取 ``level``（次选 ``value``），标量取本身；解析失败按 1。
    与选项编号同一通道，供「选择:触发器名」条件判定。
    """
    if isinstance(result, dict):
        raw = result["level"] if "level" in result else result.get("value")
    else:
        raw = result
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 1


class TriggerHandler:
    # ---------- 触发器 ----------
    def _check_unlock_coord(self):
        intrusion = self.dungeon_state.intrusion
        destruction = self.dungeon_state.destruction
        if (4, 4) not in self.locked_coords:
            return
        if intrusion > 3.5 and destruction > 3.5:
            if "涩涩" in self.tags:
                self.locked_coords.remove((4, 4))
            else:
                prob = min(1.0, (self.greed / 100.0) * (self.breakthrough_attempts + 1))
                if random.random() < prob:
                    self.locked_coords.remove((4, 4))
                else:
                    self.breakthrough_attempts += 1

    def evaluate_condition(self, condition: dict, state: DungeonState) -> bool:
        return TriggerRules.evaluate(condition, state, self.trigger_choices)

    # ------------------ 章节 ------------------
    def _build_chapter_effect(self, effect) -> dict:
        """章节敏感效果：无持续步数，离开章节即失效。"""
        return {
            "attr": effect.get("attr", ""),
            "amount": sensitivity_amount(effect.get("strength", 1.0),
                                         effect.get("objective", 0.0),
                                         effect.get("attr", ""), self.personality,
                                         action_points=self._current_action_points()),
        }

    def _enter_chapter(self, name, record: bool = False, background=None,
                       bgm=None, voice_fx=None, allow_unknown: bool = False) -> bool:
        """进入章节（``""`` 表示离开章节）。

        ``record`` 为真时写入一条回放记录；``background`` / ``bgm`` / ``voice_fx``
        用于回放时直接复现当时的环境，避免依赖当前加载的章节配置。
        """
        name = str(name or "").strip()
        chapter = find_chapter(self.chapters, name) if name else None
        if name and chapter is None and not allow_unknown:
            process_log.log(f"[Chapter] 章节不存在：{name}")
            return False

        # 离开当前章节：压缩本章剩余段落（作为提示词概要的一部分）
        summarizer = getattr(self, "story_summary", None)
        if summarizer is not None and self.current_chapter != (name or None):
            summarizer.flush_chapter(self.current_chapter or "",
                                     ai_client=getattr(self, "ai_client", None))
            # 确定性关键事件：章节进出常驻提示词，保证 AI 对章节流转有感知
            if self.current_chapter:
                summarizer.record_key_event(f"离开章节「{self.current_chapter}」")
            if name:
                summarizer.record_key_event(f"进入章节「{name}」")

        self.current_chapter = name or None
        self.dungeon_state.chapter_steps = 0
        self.chapter_sensitivity_effects = [
            self._build_chapter_effect(effect)
            for effect in (chapter or {}).get("sensitivity", [])
        ]
        # 换章清除上一章遗留的短暂覆盖，并让各维度回到本章节默认值
        self.effect_overrides = []
        self._applied_visual_filter = None
        # 预生成的段落是按上一章的环境写的：换章即作废，免得带着旧章节的提示词上屏
        self._pregen = None

        bg = background if background is not None else (chapter or {}).get("background")
        # 回放记录里同样带上音乐配置：回放时照当时的曲目复现，不依赖当前方案
        music = bgm if bgm is not None else (chapter or {}).get("bgm")
        # 语音效果同理：当时配的两档效果跟着记录走
        effect = voice_fx if voice_fx is not None else (chapter or {}).get("voice_fx")
        if record:
            self.replay_data.append({
                "kind": "chapter",
                "name": self.current_chapter or "",
                "background": dict(bg or {}),
                "bgm": dict(music or {}),
                "voice_fx": dict(effect or {}),
                "step": self.dungeon_state.total_steps,
            })
        self._apply_chapter_background(bg)
        self._apply_chapter_bgm(music)
        self._apply_chapter_voice_fx(effect)
        return True

    def _enter_start_chapter(self):
        """进入标记为起始的章节：章节不带条件，起始章节是唯一的自动进入点。"""
        if self.current_chapter or not self.chapters:
            return
        start = next((c for c in self.chapters if c.get("start") and c.get("name")), None)
        if start is None:
            return
        if self._enter_chapter(start["name"], record=True):
            process_log.log(f"[Chapter] 起始章节：{start['name']}")

    def _apply_chapter_background(self, background: dict = None):
        """应用章节的特定背景（未配置背景时保持当前背景不变）。"""
        if background is None:
            chapter = (find_chapter(self.chapters, self.current_chapter)
                       if self.current_chapter else None)
            background = (chapter or {}).get("background") or {}
        image_path = (background or {}).get("image_path")
        if not image_path:
            return
        filter_key = background.get("filter_effect")
        self._set_background(image_path,
                             bool(background.get("smooth_transition", True)),
                             filter_key)
        # 章节默认滤镜已经随背景落地，刷新缓存口径（覆盖到期时据此判断是否要重刷）
        self._applied_visual_filter = filter_key or ""

    def _apply_chapter_bgm(self, bgm: dict = None):
        """应用章节的背景音乐。

        与背景图不同，**没有配 bgm 的章节会停掉音乐**：音乐是「身处其中时的环境」
        的一部分，跳到一个没配音乐的章节还放着上一章的曲子只会串味。
        """
        if bgm is None:
            chapter = (find_chapter(self.chapters, self.current_chapter)
                       if self.current_chapter else None)
            bgm = (chapter or {}).get("bgm") or {}
        bgm = bgm or {}
        self.current_bgm_path = str(bgm.get("path") or "")
        self._bgm.play(bgm)
        # 记录实际落地的音量档位，供「音乐音量覆盖」到期时判断是否需要挪回去
        # （bgm_level 兼容旧 volume，未存盘迁移的老方案也能取到正确档位）
        self._applied_bgm_level = bgm_level(bgm) if bgm.get("path") else None

    def _apply_chapter_voice_fx(self, voice_fx: dict = None):
        """应用章节的对话语音物理效果（两档：她 / 其他人）。

        与 bgm 同一套语义：章节是「身处其中时的环境」，**没配就是不加效果**——
        走到没配效果的章节会把上一章的效果清掉，不会串味。具体哪一档作用于谁
        由语音层按说话人认（她走「她」档，主角与其余角色走「其他人」档）。

        章节默认值存进 ``chapter_voice_fx``；实际生效值由 :meth:`_refresh_voice_fx`
        按「覆盖优先、其次章节默认」算出来，覆盖到期后据此回退。
        """
        if voice_fx is None:
            chapter = (find_chapter(self.chapters, self.current_chapter)
                       if self.current_chapter else None)
            voice_fx = (chapter or {}).get("voice_fx") or {}
        self.chapter_voice_fx = dict(voice_fx or {})
        self._refresh_voice_fx()

    def _set_background(self, image_path, smooth: bool = False, filter_effect=None):
        """切换背景并记录当前图路径，供短暂覆盖重刷滤镜使用。"""
        self.current_background_path = image_path or ""
        self.change_background(image_path, smooth, filter_effect)

    # ------------------ 章节效果短暂覆盖（effect） ------------------
    def _active_override(self, key):
        """某维度当前生效的覆盖值（后触发优先）；未被覆盖时返回 None。"""
        return active_override(self.effect_overrides, key)

    def _push_effect_override(self, overrides, duration):
        """入队一条限时覆盖并立即刷新各维度。"""
        self.effect_overrides.append({
            "remaining": max(1, int(duration)),
            "overrides": dict(overrides or {}),
        })
        self._refresh_effect_overrides()

    def _refresh_effect_overrides(self):
        """按当前生效的覆盖集合重算各维度，未覆盖的维度回到章节默认值。

        每次覆盖入队/到期都整体重算，而不是「记录旧值、到期抄回去」：中途换章、
        多条覆盖叠加时都不会把上一章或已失效的值翻出来。敏感效果无副作用需要
        落地，读取时按 :meth:`_effective_sensitivity_effects` 现算。
        """
        self._refresh_visual_filter()
        self._refresh_bgm_level()
        self._refresh_voice_fx()

    def _refresh_visual_filter(self):
        """按当前生效的滤镜覆盖重刷背景；无覆盖则恢复章节默认滤镜。"""
        override = self._active_override("filter")
        if override is None:
            chapter = (find_chapter(self.chapters, self.current_chapter)
                       if self.current_chapter else None)
            target = ((chapter or {}).get("background") or {}).get("filter_effect") or ""
        else:
            target = override or ""
        if target == self._applied_visual_filter:
            return
        self._applied_visual_filter = target
        if not self.current_background_path:
            if target:
                process_log.log(f"[Trigger] 章节效果覆盖的背景滤镜 {target} 没有可用的背景，已忽略")
            return
        self._set_background(self.current_background_path, False, target)

    def _refresh_bgm_level(self):
        """按当前生效的音量覆盖调整音乐；无覆盖则回到章节配置的档位。

        同一首曲子只挪音量、不从头重放（``BgmPlayer._apply`` 的既有语义）；
        章节本就没有音乐时音量覆盖无处施加，直接忽略。
        """
        chapter = (find_chapter(self.chapters, self.current_chapter)
                   if self.current_chapter else None)
        bgm = chapter_bgm(chapter) if chapter else {}
        if not bgm.get("path"):
            self._applied_bgm_level = None
            return
        override = self._active_override("bgm_level")
        level = (int(bgm.get("level", DEFAULT_BGM_LEVEL))
                 if override is None else int(override))
        if level == self._applied_bgm_level:
            return
        self._applied_bgm_level = level
        self._bgm.play(dict(bgm, level=level))

    def _refresh_voice_fx(self):
        """按当前生效的语音覆盖重设效果；无覆盖则回到章节默认（可能是不加效果）。"""
        override = self._active_override("voice_fx")
        if override is None:
            effective = dict(getattr(self, "chapter_voice_fx", None) or {})
        else:
            effective = dict(override or {})
        if effective == getattr(self, "_applied_voice_fx", None):
            return
        self._applied_voice_fx = dict(effective)
        self.current_voice_fx = dict(effective)
        speech = getattr(self, "_speech", None)
        if speech is not None:
            speech.set_effect(self.current_voice_fx)

    def _effective_sensitivity_effects(self):
        """当前生效的持续敏感效果：有覆盖用覆盖，否则用章节默认（离开章节即空）。"""
        override = self._active_override("sensitivity")
        if override is None:
            return list(self.chapter_sensitivity_effects)
        return [self._build_chapter_effect(effect) for effect in override]

    def _apply_effect_overrides(self):
        """每步收尾：消耗覆盖时长，到期后把各维度交还章节默认值。"""
        if not self.effect_overrides:
            return
        for entry in self.effect_overrides:
            entry["remaining"] = entry.get("remaining", 0) - 1
        self.effect_overrides = [e for e in self.effect_overrides
                                 if e.get("remaining", 0) > 0]
        self._refresh_effect_overrides()

    # ------------------ 触发判定 ------------------
    def _begin_trigger_round(self):
        """开启新一轮判定：清空「本轮已触发」集合。

        一轮 = 一次步进收尾（或会话启动）+ 它派生的全部结算重判（选项选择、
        小游戏结算）。重判共享同一集合，**同一触发器一轮内至多生效一次**：
        结算后条件仍成立的触发器不会立刻重发（选项连弹、视效/插入重复发放
        被结构性挡住），其他触发器可以接力。
        """
        self._round_fired = set()

    def check_triggers(self):
        """判定所有触发器（一轮）：按 ``actions.ACTION_POSITIONS`` 的固定顺序
        逐位派发，**同位置至多生效一个**（同位置内按配置顺序取第一个满足条件
        且配置有效的触发器；配置无效被跳过时不占配额）。

        被接纳的触发器立即清空 ``间隔计数``，并记入 ``_round_fired``——本轮
        （含派生的结算重判）内不会再触发。回放不判定，只按记录复现。"""
        if not self.triggers:
            return
        for position in ACTION_POSITIONS:
            for trigger_index, trigger in enumerate(self.triggers):
                name = trigger.get("name")
                if name in self.triggered_names or name in self._round_fired:
                    continue
                action_data = trigger.get("action_data", {})
                action_type = normalize_action_type(trigger.get("action_type"), action_data)
                if action_type not in position:
                    continue
                if self._dispatch_trigger(trigger, trigger_index, action_type):
                    break   # 同位置至多一个生效

    def _dispatch_trigger(self, trigger, trigger_index, action_type) -> bool:
        """派发单个触发器动作；返回是否生效（通过门槛且动作被接纳）。"""
        action_data = trigger.get("action_data", {})
        # 所在章节：触发器只在指定章节内参与判定
        if not matches_scope(trigger.get("chapter"), self.current_chapter):
            return False
        pre_names = trigger.get("precondition_names", [])
        if not all(name in self.fired_triggers for name in pre_names):
            return False
        cond = trigger.get("condition", {})
        if not self.evaluate_condition(cond, self.dungeon_state):
            return False
        action_accepted = True
        if action_type == "insert":
            text = str(action_data.get("text", "")).strip()
            if not text:
                process_log.log(f"[Trigger] 插入触发器 {trigger['name']} 缺少插入文本，已跳过")
                action_accepted = False
            else:
                delayed = bool(action_data.get("delayed", False))
                self.pending_insertions.append({
                    "text": text,
                    "text_type": action_data.get("text_type") or "background",
                    "highlight": bool(action_data.get("highlight", False)),
                    "delayed": delayed,
                })
                process_log.log(f"[Trigger] 已触发: {trigger['name']}，排队插入段落（延迟={delayed}）")
                self._record_trigger_action(trigger, action_type, action_data)
        elif action_type == "option":
            # 结束章节：不允许弹出选项
            if is_terminating_chapter(
                    find_chapter(self.chapters, self.current_chapter)):
                process_log.log(f"[Trigger] 选项触发器 {trigger['name']} 位于结束章节内，已跳过")
                return False
            options = action_data.get("options") or []
            if not options:
                process_log.log(f"[Trigger] 选项触发器 {trigger['name']} 未配置选项，已跳过")
                action_accepted = False
            elif self.pending_option is not None:
                process_log.log(f"[Trigger] 选项触发器 {trigger['name']} 触发时已有选项弹窗，已跳过")
                action_accepted = False
            else:
                self.pending_option = {
                    "name": trigger["name"],
                    "prompt": action_data.get("prompt", ""),
                    "options": [
                        {"id": o.get("id", i), "prompt": o.get("prompt", ""), "text": o.get("text", "")}
                        for i, o in enumerate(options)
                    ],
                }
                process_log.log(f"[Trigger] 已触发: {trigger['name']}，弹出选项")
                self._start_option_generation()
                self._last_option_record = self._record_trigger_action(trigger, action_type, action_data)
        elif action_type == "mini_game":
            # 结束章节：不允许弹小游戏
            if is_terminating_chapter(
                    find_chapter(self.chapters, self.current_chapter)):
                process_log.log(f"[Trigger] 小游戏触发器 {trigger['name']} 位于结束章节内，已跳过")
                action_accepted = False
            elif self.pending_mini_game is not None:
                process_log.log(f"[Trigger] 小游戏触发器 {trigger['name']} 触发时已有小游戏进行中，已跳过")
                action_accepted = False
            else:
                game_id = str(action_data.get("game") or MINI_GAME_DEFAULT_ID).strip()
                resolved = resolve_mini_game(game_id)
                if resolved is None:
                    process_log.log(f"[Trigger] 找不到小游戏「{game_id}」，"
                                    f"触发器 {trigger['name']} 已跳过")
                    action_accepted = False
                elif resolved.backend == BACKEND_PY:
                    # py 后端：会话窗口内的覆盖层舞台（dungeon.window.minigame.stage）
                    try:
                        launched = self._open_mini_game_stage(
                            resolved, action_data,
                            on_result=self._on_mini_game_result)
                    except Exception as exc:
                        process_log.log(f"[Trigger] 小游戏「{game_id}」打开异常: {exc}")
                        launched = False
                    if not launched:
                        action_accepted = False
                    else:
                        self.pending_mini_game = {
                            "name": trigger["name"],
                            "action_data": action_data,
                            "trigger_index": trigger_index,
                        }
                        process_log.log(f"[Trigger] 已触发: {trigger['name']}，"
                                        f"小游戏「{game_id}」开始")
                        self._last_mini_game_record = self._record_trigger_action(
                            trigger, action_type, action_data)
                else:
                    # web 后端：子进程 pywebview 独立窗口（宿主端口能力）
                    try:
                        launched = self.host.launch_mini_game(
                            game_id, action_data,
                            on_result=self._on_mini_game_result)
                    except Exception as exc:
                        process_log.log(f"[Trigger] 小游戏「{game_id}」启动异常: {exc}")
                        launched = False
                    if not launched:
                        # 宿主不支持 / 目录缺失：触发器不生效，副本流程照常
                        process_log.log(f"[Trigger] 小游戏「{game_id}」不可用，"
                                        f"触发器 {trigger['name']} 已跳过")
                        action_accepted = False
                    else:
                        self.pending_mini_game = {
                            "name": trigger["name"],
                            "action_data": action_data,
                            "trigger_index": trigger_index,
                        }
                        process_log.log(f"[Trigger] 已触发: {trigger['name']}，"
                                        f"小游戏「{game_id}」开始")
                        self._last_mini_game_record = self._record_trigger_action(
                            trigger, action_type, action_data)
        elif action_type == "effect":
            overrides = parse_overrides(action_data)
            bad_filter = invalid_filter(overrides)
            if bad_filter:
                process_log.log(f"[Trigger] 章节效果覆盖触发器 {trigger['name']} 的滤镜无效："
                                f"{bad_filter}，已跳过")
                action_accepted = False
            elif not overrides:
                process_log.log(f"[Trigger] 章节效果覆盖触发器 {trigger['name']} "
                                f"未指定任何覆盖维度，已跳过")
                action_accepted = False
            else:
                try:
                    duration = max(1, int(float(action_data.get("duration", 1))))
                except (TypeError, ValueError):
                    duration = 1
                self._push_effect_override(overrides, duration)
                process_log.log(f"[Trigger] 已触发: {trigger['name']}，章节效果覆盖"
                                f"（{describe_overrides(overrides)}）{duration} 步")
                self._record_trigger_action(trigger, action_type, action_data)
        elif action_type == "fx":
            # 窗口视效：整窗瞬时画面，按绝对时间消退；不阻塞任何动作
            fx_key, duration, intensity = parse_fx_params(action_data)
            if fx_key not in WINDOW_FX_KEYS:
                process_log.log(f"[Trigger] 视效触发器 {trigger['name']} 的窗口视效无效：{fx_key}，已跳过")
                action_accepted = False
            else:
                self._window_fx.start(fx_key, duration, intensity)
                process_log.log(f"[Trigger] 已触发: {trigger['name']}，窗口视效 {fx_key}"
                                f"（{duration:.1f} 秒，强度 {intensity:.0f}%）")
                self._record_trigger_action(trigger, action_type, action_data)
        elif action_type == "goto":
            # 结束章节：不允许通过触发器跳出
            if is_terminating_chapter(
                    find_chapter(self.chapters, self.current_chapter)):
                process_log.log(f"[Trigger] 跳转触发器 {trigger['name']} 位于结束章节内，已跳过")
                return False
            target = str(action_data.get("chapter") or "").strip()
            chapter = find_chapter(self.chapters, target) if target else None
            if target and chapter is None:
                process_log.log(f"[Trigger] 跳转触发器 {trigger['name']} 的目标章节不存在：{target}，已跳过")
                action_accepted = False
            elif self.current_chapter == (target or None):
                process_log.log(f"[Trigger] 跳转触发器 {trigger['name']} 已处于章节「{target or '无章节'}」，已跳过")
                action_accepted = False
            else:
                self._enter_chapter(target)
                process_log.log(f"[Trigger] 已触发: {trigger['name']}，"
                      f"跳转章节：{target or '离开章节'}")
                self._record_trigger_action(
                    trigger, action_type, action_data,
                    chapter_background=dict((chapter or {}).get("background") or {}),
                    chapter_bgm=dict((chapter or {}).get("bgm") or {}),
                    chapter_voice_fx=dict((chapter or {}).get("voice_fx") or {}))
        elif action_type == ENDING_ACTION:
            # 旧版结局触发器已退场：新方案由「结束章节」承担，这里只留一行提示
            process_log.log(f"[Trigger] 结局触发器 {trigger['name']} 是旧版配置，"
                            "运行时已不再执行，请改用「结束章节」")
            action_accepted = False
        elif action_type in EMPTY_ACTIONS:
            # 空触发器：无动作，仅用于标记条件成立，供其他触发器作为前置条件
            process_log.log(f"[Trigger] 空触发器 {trigger['name']} 条件成立（无动作）")
            self._record_trigger_action(trigger, action_type, action_data)
        else:
            process_log.log(f"未知的触发器动作类型：{action_type}")
            action_accepted = False

        if not action_accepted:
            return False
        # 可再次触发（默认）：不加入已触发集合，条件满足时可重复触发
        self.fired_triggers.add(trigger["name"])
        if not trigger.get("repeatable", True):
            self.triggered_names.add(trigger["name"])
        # 生效即清空间隔计数，并记入本轮已触发（派生的结算重判不再重发）
        self.dungeon_state.steps_since_trigger = 0
        self._round_fired.add(trigger["name"])
        return True

    def _on_mini_game_result(self, result):
        """小游戏结果回调（宿主线程）：并入帧线程后执行结算。"""
        self._frame.call(lambda: self._finish_mini_game(result))

    def _finish_mini_game(self, result):
        """小游戏结算（帧线程）：写叙事行、回填记录，并立即重跑触发判定。

        小游戏是选项触发器的特殊形态，不再区分胜负：结算把整数返回值记入
        ``trigger_choices``，跳转等分支由其他触发器用「选择:本触发器名」条件
        完成。这里记录之后立即重跑 ``check_triggers``（与选项选择同构），让
        依赖本局结果的关联触发器在同一步内生效。

        ``result=None`` 表示用户提前关掉了游戏窗口：只解除挂起，不记返回值，
        副本照常继续。"""
        pending = self.pending_mini_game
        self.pending_mini_game = None
        if pending is None:
            return
        record = self._last_mini_game_record
        self._last_mini_game_record = None
        if not result:
            process_log.log(f"[MiniGame] 小游戏 {pending['name']} 被中断，不记录返回值")
            return
        value = _mini_game_return_value(result)
        # 整数返回值进选择记录（与选项编号同一通道、同一套条件判定）
        self.trigger_choices.setdefault(pending["name"], []).append(value)
        summary = f"小游戏「{pending['name']}」结束（返回值 {value}）"
        self.story_history.append(
            {"type_str": "【小游戏】", "text": summary, "highlight": True, "speaker": None})
        self._update_text_display()
        if record is not None:
            record["game_result"] = dict(result) if isinstance(result, dict) \
                else {"value": result}
            record["choice_value"] = value
        process_log.log(f"[MiniGame] {summary}，重跑触发判定")
        # 结算后立即重判（同一轮，共享本轮已触发集合）：按返回值配置的跳转等
        # 关联触发器同一步生效；刚结算的触发器已在集合里，条件仍成立也不会
        # 原地重启游戏，玩家回得到副本主页面。
        self.check_triggers()
        self._maybe_pregen_next()

    def _record_trigger_action(self, trigger, action_type, action_data, **extra) -> dict:
        """触发时把动作与时机写入回放记录，回放时不再判定条件，直接按记录复现。"""
        record = {
            "kind": "trigger",
            "name": trigger["name"],
            "action_type": action_type,
            "action_data": dict(action_data or {}),
            "step": self.dungeon_state.total_steps,
        }
        record.update(extra)
        self.replay_data.append(record)
        return record

    def _replay_chapter(self, record):
        """回放时直接复现章节进入（含当时的环境，不依赖当前章节配置）。"""
        name = record.get("name") or ""
        self._enter_chapter(name, record=False,
                            background=record.get("background"),
                            bgm=record.get("bgm"),
                            voice_fx=record.get("voice_fx"),
                            allow_unknown=True)
        process_log.log(f"[Replay] 复现章节进入: {name or '无章节'}")

    def _replay_trigger(self, record):
        """回放时复现触发器动作（不判定条件、不弹选项，选择作为一步直接展示）。"""
        action_type = record.get("action_type")
        action_data = record.get("action_data", {}) or {}
        name = record.get("name", "")
        if action_type == "goto":
            self._enter_chapter(str(action_data.get("chapter") or ""),
                                record=False,
                                background=record.get("chapter_background"),
                                bgm=record.get("chapter_bgm"),
                                voice_fx=record.get("chapter_voice_fx"),
                                allow_unknown=True)
            process_log.log(f"[Replay] 复现章节跳转: {name}")
        elif action_type == "effect":
            overrides = parse_overrides(action_data)
            if overrides and not invalid_filter(overrides):
                try:
                    duration = max(1, int(float(action_data.get("duration", 1))))
                except (TypeError, ValueError):
                    duration = 1
                self._push_effect_override(overrides, duration)
            process_log.log(f"[Replay] 复现章节效果覆盖: {name}")
        elif action_type == "fx":
            # 窗口视效按时长自消，回放按记录原样重放一次
            fx_key, duration, intensity = parse_fx_params(action_data)
            if fx_key in WINDOW_FX_KEYS:
                self._window_fx.start(fx_key, duration, intensity)
            process_log.log(f"[Replay] 复现窗口视效: {name}")
        elif action_type == "option":
            choice_index = record.get("choice_index")
            if choice_index is None:
                return  # 未完成选择，跳过
            idx = int(choice_index)
            options = action_data.get("options") or []
            chosen = options[idx] if 0 <= idx < len(options) else {}
            text = (record.get("choice_text")
                    or chosen.get("text") or chosen.get("prompt") or f"选项 {idx + 1}")
            self.trigger_choices.setdefault(name, []).append(idx)
            self.option_choice = {
                "name": name,
                "index": idx,
                "prompt": record.get("option_prompt") or chosen.get("prompt", ""),
                "text": chosen.get("text", ""),
            }
            self.story_history.append({"type_str": "【选择】", "text": text,
                                       "highlight": True, "speaker": None})
            self._update_text_display()
            process_log.log(f"[Replay] 复现选项触发器: {name} → 选择 {idx}")
        elif action_type == "mini_game":
            result = record.get("game_result") or {}
            if not result:
                process_log.log(f"[Replay] 复现小游戏触发器: {name}（无结果，跳过）")
                return
            value = record.get("choice_value")
            if value is None:
                value = _mini_game_return_value(result)
            # 返回值进选择记录，与实时结算同构；跳转由回放的关联触发器完成
            self.trigger_choices.setdefault(name, []).append(value)
            self.story_history.append({
                "type_str": "【小游戏】",
                "text": f"小游戏「{name}」结束（返回值 {value}）",
                "highlight": True, "speaker": None})
            self._update_text_display()
            process_log.log(f"[Replay] 复现小游戏触发器: {name} → 返回值 {value}")
        elif action_type == "ending":
            ending_text = record.get("ending_text", "")
            # 回放时同样显示结局图标（图标路径来自结局动作配置）
            icon_path = (action_data or {}).get("icon_path", "") or ""
            if icon_path:
                self.ending_icon_path = icon_path
                self._frame.call(self._update_ending_icon)
            if ending_text:
                self.ending_text = ending_text
                self.story_history.append({"type_str": "【结局】", "text": ending_text,
                                           "highlight": True, "speaker": None})
                self._update_text_display()
            self.pending_ending = None
            self.dungeon_ended = True
            process_log.log(f"[Replay] 复现结局触发器: {name}")
        else:
            # insert / sensitivity / none：效果已随文本步骤与属性快照复现
            process_log.log(f"[Replay] 触发器动作无需额外复现: {name} [{action_type}]")

    # ------------------ 插入段落 ------------------
    def _peek_pending_insertion(self):
        if not self.pending_insertions:
            return None
        return self.pending_insertions[0]

    def _consume_pending_insertion(self):
        item = self.pending_insertions.popleft()
        self._display_inserted_paragraph(item)

    def _display_inserted_paragraph(self, item):
        try:
            text_type = DungeonTextType(item["text_type"])
        except (KeyError, ValueError):
            text_type = DungeonTextType.BACKGROUND
        # 结束章节：段落类型被覆盖为「结局」，步进为 0
        in_ending = self._in_terminating_chapter()
        if in_ending:
            text_type = DungeonTextType.BACKGROUND
        highlight = bool(item.get("highlight"))

        # 插入段同样走限速上屏：先空文本上屏，再由游标逐字揭开
        inserted_text = item["text"]
        anim_item = {
            "type_str": self._display_type_prefix(text_type),
            "text": "",
            "highlight": highlight,
            "speaker": None,
        }
        self.story_history.append(anim_item)
        self._update_text_display()
        self._reveal_start(anim_item, inserted_text)

        # 与一般段落一样进入对话历史，后续 AI 生成时可见。
        # 用规范 JSON 形状（dungeon.response.canonical_history_entry）：
        # 纯文本的 assistant 回合会让模型模仿，引发格式漂移
        self.messages.append({"role": "assistant", "content": canonical_history_entry(
            inserted_text, 0, {})})
        window = self._message_window_size()
        if len(self.messages) > window + 1:
            self.messages = [self.messages[0]] + self.messages[-window:]

        before_state = self.dungeon_state
        self.dungeon_state = self.dungeon_logic.evolve_attributes(
            before_state, text_type, 0, self.personality,
            custom_attrs_def=self.evolution_attrs,
            custom_directions={},
            sensitivity_mods=self._apply_sensitivity_mods(),
            action_points=self._current_action_points(),
            step_override=0.0 if in_ending else None,
        )
        self.step_num = self.dungeon_state.total_steps

        step_info = {
            "step": self.step_num,
            "type": text_type.value,
            "text": item["text"],
            "highlight": highlight,
            "intrusion_before": before_state.intrusion,
            "destruction_before": before_state.destruction,
            "custom_before": before_state.custom_attrs.copy(),
            "direction": 0,
            "custom_directions": {},
        }
        if in_ending:
            step_info["ending_chapter"] = True

        self._finish_step(text_type, item["text"], step_info)
        # 插入段消费后下一次点击必然生成：立即补射预生成
        self._maybe_pregen_next()
