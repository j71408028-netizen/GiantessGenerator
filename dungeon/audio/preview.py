"""编辑器里的语音**预听**：合成一句样例，按当前效果放出来（纯领域模块：不碰 UI）。

为什么归这一层而不是 UI：预听和副本里朗读走的是**同一份**合成（edge-tts）与
**同一套**效果（:mod:`dungeon.audio.voice_fx` 的两级引擎）。预听只是把这条链
「跑一次就丢」，所以它属于语音，不属于界面——UI 只负责按钮与状态文案。

与 :class:`~dungeon.audio.speech.SpeechDirector` 的分工：那个是「一局副本」的
朗读调度（音色映射、跨章效果、帧循环外排队）；预听只要「合成 → （有解码器就
渲染，否则播放侧近似）→ 播一次」，一次一句、新的打断旧的，编辑器关掉就
:meth:`VoicePreview.shutdown`。

线程：整条链（联网合成 + 渲染 + 播放）都在一条后台线程上，``play`` 立刻返回。
``on_done(ok, message)`` 在**工作线程**上回调——Tk 调用方要自己 ``after`` 回主线程。
"""

import os
import queue
import shutil
import tempfile
import threading
import time

from dungeon.audio import buses
from dungeon.audio import open_track
from dungeon.audio import speech as _speech
from dungeon.audio import voice_fx

#: 预听的样例台词：够短（合成快）又留得出回声尾巴与隆隆声的余地
SAMPLE_TEXT = "你跑不掉的，站住。"

#: 播完轮询的间隔（秒）
POLL_SECONDS = 0.05
#: 单次预听的最长等待（秒）：后端状态查不出来时不至于一直吊着
MAX_PLAY_SECONDS = 30.0


class VoicePreview:
    """一次一句的预听播放器（编辑器用，零 UI 依赖）。

    ``synth`` 是注入接缝（``(voice, rate, text, dest_dir) -> path|None``），
    自检用它避免联网。
    """

    def __init__(self, logger=None, synth=None):
        self._logger = logger
        self._synth = synth or _speech.synthesize
        self._jobs = queue.Queue()
        self._sentinel = object()
        self._lock = threading.Lock()
        self._generation = 0
        self._thread = None
        self._stopped = False
        self._tmp_dir = None
        #: 只由工作线程写的播放状态
        self._track = None
        self._fx = None
        self._notes = []
        #: 最近一次预听的结果（UI 与自检读它）
        self.last_ok = None
        self.last_message = ""
        #: 最近一次实际用的引擎：``render`` / ``playback``（诊断用）
        self.last_engine = "none"

    # ---------------- 对外 API ----------------

    @property
    def notes(self) -> list:
        return list(self._notes)

    @property
    def playing(self) -> bool:
        return self._track is not None

    @property
    def worker_alive(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def play(self, voice, slot=voice_fx.SLOT_HER, rate=None, level=None,
             effect=None, text=SAMPLE_TEXT, on_done=None) -> bool:
        """合成并播放一句；返回是否真的排上了队。

        参数都来自编辑器当前的选择：``voice`` 音色、``rate`` 语速、``level``
        音量档位、``effect`` 该档的效果配置（``{"preset": …, "intensity": …}``；
        给 ``None`` 就放**不带效果**的原声——方案级试听音色 / 语速 / 档位用）。

        没有音色、或给了效果但那一档其实没配效果时直接判失败——省一次联网合成。
        """
        voice_id = str(voice or "").strip()
        if not voice_id:
            self._finish(False, "没有可用的音色：先选一个音色", on_done)
            return False
        spec = voice_fx.normalize_slot(effect, slot) if effect else {}
        if effect and (not spec or not voice_fx.has_effect(spec)):
            self._finish(False, "这一档还没配效果：先选一个预设再预听", on_done)
            return False
        step = buses.clamp_step(level, buses.DEFAULT_STEP)
        body = str(text or SAMPLE_TEXT)
        with self._lock:
            self._generation += 1
            generation = self._generation
        if self._ensure_thread() is None:
            self._finish(False, "无法启动预听线程", on_done)
            return False
        self._jobs.put(("play", generation, voice_id, spec, step,
                        _speech.normalize_rate(rate), body, on_done))
        return True

    def stop(self) -> None:
        """立刻静音（丢队列里的、也停正在放的）。"""
        with self._lock:
            self._generation += 1
        self._jobs.put(("stop", 0, None, None, None, None, None, None))

    def wait_idle(self, timeout: float = 8.0) -> bool:
        """等到排队的预听都处理完（自检用；返回是否等到）。"""
        if self._thread is None or self._stopped:
            return True
        marker = threading.Event()
        self._jobs.put(("marker", 0, None, None, None, None, None, marker))
        return marker.wait(timeout)

    def shutdown(self, timeout: float = 1.5) -> None:
        """收尾：停播、收线程、清掉临时音频（编辑器关对话框时调）。"""
        self._stopped = True
        with self._lock:
            self._generation += 1
        thread = self._thread
        if thread is None:
            self._cleanup()
            return
        self._jobs.put(self._sentinel)
        try:
            thread.join(timeout=timeout)
        except Exception:
            pass
        self._thread = None
        self._cleanup()

    # ---------------- 工作线程 ----------------

    def _ensure_thread(self):
        thread = self._thread
        if thread is not None and thread.is_alive():
            return thread
        try:
            thread = threading.Thread(target=self._loop, name="voice-preview",
                                      daemon=True)
        except Exception:
            return None
        self._thread = thread
        thread.start()
        return thread

    def _loop(self):
        while True:
            try:
                item = self._jobs.get(timeout=POLL_SECONDS)
            except queue.Empty:
                continue
            if item is self._sentinel:
                return
            kind = item[0]
            if kind == "marker":
                item[7].set()
                continue
            if kind == "stop":
                self._close_track()
                continue
            _kind, generation, voice_id, spec, step, rate, body, on_done = item
            if generation != self._generation or self._stopped:
                self._finish(False, "已被新的预听取代", on_done)
                continue
            try:
                self._run_job(generation, voice_id, spec, step, rate, body, on_done)
            except Exception as exc:                      # 兜底：预听不该炸掉编辑器
                self._note(f"[Preview] 预听任务异常：{exc}")
                self._finish(False, f"预听失败：{exc}", on_done)

    def _run_job(self, generation, voice_id, spec, step, rate, body, on_done):
        """合成 → 渲染 / 播放侧近似 → 播一次（与副本里朗读同一套降级顺序）。"""
        self._close_track()
        tmp = self._ensure_tmp()
        if tmp is None:
            self._finish(False, "无法创建临时目录", on_done)
            return
        try:
            path = self._synth(voice_id, rate, body, tmp)
        except Exception as exc:
            self._note(f"[Preview] 合成失败：{exc}")
            path = None
        if generation != self._generation or self._stopped:
            self._finish(False, "已被新的预听取代", on_done)
            return
        if not path:
            self._finish(False, self._synth_failure_reason(), on_done)
            return
        gain = buses.resolve_gain(buses.BUS_VOICE, step=step)
        if not spec:
            # 不带效果：直接按总线音量放原声（试听音色 / 语速 / 档位）
            self._play_once(path, gain, generation, on_done)
            return
        if voice_fx.render_available(spec):
            rendered = self._render(path, spec, step, tmp)
            if rendered:
                track = self._start(rendered, 1.0)
                if track is not None:
                    status = self._wait(track, generation)
                    self._close_track()
                    if status == "done":
                        self.last_engine = "render"
                        self._finish(True, "", on_done)
                        return
        plan = voice_fx.playback_plan(spec)
        if plan is None:
            self._finish(False, self._render_only_reason(spec), on_done)
            return
        # 播放侧抬不起响度，只能按声明响度做衰减（见 voice_fx.playback_trim）
        volume = gain * voice_fx.playback_trim(spec)
        track = self._start(path, volume)
        if track is None:
            self._finish(False, "没有可用的音频后端，无法播放预听", on_done)
            return
        self.last_engine = "playback"
        effect = voice_fx.PlaybackFx(plan, path, volume, logger=self._note)
        self._fx = effect
        try:
            effect.run(track, lambda: generation != self._generation or self._stopped)
        except Exception as exc:
            self._note(f"[Preview] 播放效果异常：{exc}")
        self._close_track()
        self._finish(True, "", on_done)

    def _play_once(self, path, volume, generation, on_done) -> bool:
        """放一路不带效果的原声；成功/被打断都按结果回调。"""
        track = self._start(path, volume)
        if track is None:
            self._finish(False, "没有可用的音频后端，无法播放预听", on_done)
            return False
        self.last_engine = "none"
        status = self._wait(track, generation)
        self._close_track()
        if status == "stale":
            self._finish(False, "已被新的预听取代", on_done)
            return False
        self._finish(True, "", on_done)
        return True

    # ---------------- 内部 ----------------

    def _synth_failure_reason(self) -> str:
        """合成失败的原因：优先用 ``speech`` 记下的**真实**诊断（如 HTTP 403），
        拿不到再退回通用文案——预听看到的原因就是副本里不出声的原因。"""
        detail = _speech.last_synth_error()
        if detail:
            return f"合成失败：{detail}"
        if not _speech.edge_tts_available():
            return "合成不可用：未安装 edge-tts（pip install edge-tts）"
        return "合成失败：网络不可达或音色名有误"

    def _render_only_reason(self, spec) -> str:
        return (f"「{voice_fx.label_of(spec.get('preset'))}」只有离线渲染能做，"
                "本机缺解码器（pip install miniaudio 或 soundfile）")

    def _render(self, src, spec, step, tmp):
        try:
            return voice_fx.render_wav(src, tmp, spec, step, logger=self._note)
        except Exception as exc:
            self._note(f"[Preview] 渲染失败：{exc}")
            return None

    def _start(self, path, volume):
        track = open_track(path)
        if track is None:
            self._note("[Preview] 打不开音频（没有可用的后端）")
            return None
        if not track.start(loop=False, volume=volume):
            try:
                track.close()
            except Exception:
                pass
            self._note("[Preview] 音频未能起播")
            return None
        self._track = track
        return track

    def _wait(self, track, generation) -> str:
        """等这一路播完；返回 ``done`` / ``stale``（被新预听/关闭打断）。"""
        started = time.monotonic()
        while True:
            if generation != self._generation or self._stopped:
                return "stale"
            try:
                if track.finished():
                    return "done"
            except Exception:
                return "done"
            if time.monotonic() - started > MAX_PLAY_SECONDS:
                self._note("[Preview] 播放超时，提前收尾")
                return "done"
            time.sleep(POLL_SECONDS)

    def _close_track(self):
        effect, self._fx = self._fx, None
        if effect is not None:
            try:
                effect.close()
            except Exception:
                pass
        track, self._track = self._track, None
        if track is None:
            return
        try:
            track.close()
        except Exception:
            pass

    def _ensure_tmp(self):
        if self._tmp_dir is None:
            try:
                self._tmp_dir = tempfile.mkdtemp(prefix="voice_preview_")
            except Exception:
                return None
        return self._tmp_dir

    def _cleanup(self):
        self._close_track()
        tmp, self._tmp_dir = self._tmp_dir, None
        if tmp:
            try:
                shutil.rmtree(tmp, ignore_errors=True)
            except Exception:
                pass

    def _finish(self, ok, message, on_done):
        self.last_ok = bool(ok)
        self.last_message = str(message or "")
        if message:
            self._note(f"[Preview] {message}")
        if on_done is None:
            return
        try:
            on_done(bool(ok), self.last_message)
        except Exception:
            pass

    def _note(self, message: str):
        self._notes.append(message)
        logger = self._logger
        if logger is None:
            return
        try:
            logger(message)
        except Exception:
            pass
