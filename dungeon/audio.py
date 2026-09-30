"""章节背景音乐的播放（纯领域模块：不碰任何 UI 框架）。

曲目**怎么配**归 ``dungeon/chapters.py``（``normalize_bgm``：路径/音量/循环/
淡入淡出秒数）；本模块只管**怎么放出来**——给定一条配置，就把对应音频文件
循环播放到指定音量，换章节时能把上一首淡出去。

为什么不用现成的音频库：需要的能力只有「按给定音量循环播一个文件、可随时
换曲并淡入淡出」这么点。Windows 自带的 MCI（``winmm.dll`` 的
``mciSendStringW``）零依赖就能做，mp3/wav/wma 走同一套；非 Windows 平台退到
pygame（装了就用），两者都不可用时退到 :class:`NullTrack`——**没有声音不该
拖垮一局副本**，缺后端只在日志里留一条提示。

线程模型沿用 ``window/background.py`` 的像素工作者：唯一一条后台线程串行消费
命令队列。章节流调 :meth:`BgmPlayer.play` 只入队就返回，换曲与音量渐变都不占
帧循环的时间。
"""

import ctypes
import os
import queue
import threading
import time

from dungeon.chapters import DEFAULT_BGM_FADE_SECONDS, DEFAULT_BGM_VOLUME


class AudioTrack:
    """一路音频的接口：实例与一个已打开的文件一一对应。

    子类只需要实现 ``open``（类方法，失败返回 None）与下面几个动作方法。
    """

    #: 后端显示名（诊断/自检用）
    name = "none"

    @classmethod
    def supported(cls) -> bool:
        """当前环境能否使用这个后端。"""
        return False

    def tick(self):
        """周期性的续播钩子：后端自己能循环的（返回 False 的）留空即可。

        只有"驱动不认循环"才需要这里动手（见 :meth:`MciTrack.start` 的说明）；
        由播放器在唯一的工作线程上按固定节奏调用。
        """
        pass

    def finished(self) -> bool:
        """这一路音频是否已经播完（一次性语音播完要收句柄，用得上）。

        查不到状态时应返回 False（当作"还在播"）：宁可晚一点回收，也不要在
        刚起播时因为一次查询失败就把声音掐掉。
        """
        return True

    @classmethod
    def open(cls, path: str):
        """打开 ``path`` 准备播放；不可用/打不开时返回 None。"""
        return None

    def start(self, loop: bool, volume: float):
        raise NotImplementedError

    def set_volume(self, volume: float):
        """``volume`` 为 0.0~1.0 的线性音量。"""
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def close(self):
        raise NotImplementedError


# ---------------- Windows: MCI ----------------

class MciTrack(AudioTrack):
    """``winmm`` 的 MCI 后端：alias 标识一路音频，命令都是字符串。

    每个实例在打开时领一个唯一 alias（同一个 alias 反复 open/gclose 会让多条
    命令互相踩），关掉时一定要 ``close``，否则设备句柄会一直挂着。
    """

    name = "mci"
    _counter = 0
    _lock = threading.Lock()

    def __init__(self, alias: str, winmm):
        self._alias = alias
        self._winmm = winmm
        self._closed = False
        #: True 表示这个设备不认 ``repeat``，要靠 tick() 续播
        self._manual_loop = False

    # ---------- 后端自检 ----------
    @classmethod
    def supported(cls) -> bool:
        return _winmm() is not None

    @classmethod
    def open(cls, path: str):
        winmm = _winmm()
        if winmm is None:
            return None
        with cls._lock:
            cls._counter += 1
            index = cls._counter
        alias = f"ggbgm{index}"
        # 不写 type 让 MCI 按扩展名自行判定；认不出来的格式再显式试一遍常见两种
        for command in (f'open "{path}" alias {alias}',
                        f'open "{path}" type mpegvideo alias {alias}',
                        f'open "{path}" type waveaudio alias {alias}'):
            if winmm.mciSendStringW(command, None, 0, 0) == 0:
                return cls(alias, winmm)
        # 打不开：文件损坏或格式不被系统解码器支持——交给上层记录后降级
        return None

    # ---------- 命令 ----------
    def _send(self, command: str) -> bool:
        if self._closed:
            return False
        return self._winmm.mciSendStringW(command, None, 0, 0) == 0

    def start(self, loop: bool, volume: float) -> bool:
        """起播，返回是否真的放起来了。

        ``repeat`` 不是所有 MCI 设备都认：**waveaudio（wav）会直接把整条命令
        判为非法**，只有 mpegvideo（mp3 等）支持。所以先试 ``play ... repeat``，
        失败了退回普通 ``play``，并把自己标成"要手动续播"——循环就这么消失的
        话，作者写的 loop=True 会在第一遍播完后无声无息地静下去。
        """
        self.set_volume(volume)
        if loop and self._send(f"play {self._alias} repeat"):
            self._manual_loop = False
            return True
        started = self._send(f"play {self._alias}")
        self._manual_loop = bool(loop and started)
        return started

    def tick(self):
        """播完了就从头再来一次（仅在设备不支持 ``repeat`` 时生效）。"""
        if not self._manual_loop or self._closed:
            return
        if self._mode() == "playing":
            return
        self._send(f"seek {self._alias} to start")
        self._send(f"play {self._alias}")

    def finished(self) -> bool:
        mode = self._mode()
        return mode == "stopped"

    def _mode(self) -> str:
        """查询设备当前状态（playing / stopped / ...）；查不到返回空串。"""
        if self._closed:
            return ""
        buffer = ctypes.create_unicode_buffer(64)
        code = self._winmm.mciSendStringW(f"status {self._alias} mode",
                                          buffer, 64, 0)
        return (buffer.value or "").strip() if code == 0 else ""

    def set_volume(self, volume: float):
        # MCI 的音量是 0~1000 的整数
        level = int(max(0.0, min(1.0, volume)) * 1000)
        self._send(f"setaudio {self._alias} volume to {level}")

    def stop(self):
        self._send(f"stop {self._alias}")

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._send(f"stop {self._alias}")
        self._send(f"close {self._alias}")


def _winmm():
    """取 ``winmm`` 动态库；非 Windows 或库不存在时返回 None（结果缓存）。"""
    global _WINMM_CACHE
    if _WINMM_CACHE is None:
        if os.name != "nt":
            _WINMM_CACHE = False
        else:
            try:
                _WINMM_CACHE = ctypes.windll.winmm  # type: ignore[attr-defined]
            except Exception:
                _WINMM_CACHE = False
    return _WINMM_CACHE or None


_WINMM_CACHE = None


# ---------------- 可选后端：pygame ----------------

class PygameTrack(AudioTrack):
    """``pygame.mixer`` 后端：非 Windows 平台（或 Windows 上没走到 MCI）时的兜底。

    pygame 不在依赖清单里，装了才用；``mixer`` 是进程级单例，init 失败就当没
    这个后端。
    """

    name = "pygame"

    def __init__(self, sound):
        self._sound = sound
        self._channel = None
        self._volume = 1.0

    @classmethod
    def supported(cls) -> bool:
        try:
            import pygame  # noqa: F401
        except Exception:
            return False
        return True

    @classmethod
    def open(cls, path: str):
        try:
            import pygame
        except Exception:
            return None
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            sound = pygame.mixer.Sound(path)
        except Exception:
            return None
        return cls(sound)

    def start(self, loop: bool, volume: float) -> bool:
        self._volume = max(0.0, min(1.0, volume))
        self._channel = None
        try:
            self._channel = self._sound.play(loops=-1 if loop else 0)
            if self._channel is not None:
                self._channel.set_volume(self._volume)
        except Exception:
            return False
        return self._channel is not None

    def set_volume(self, volume: float):
        self._volume = max(0.0, min(1.0, volume))
        if self._channel is not None:
            self._channel.set_volume(self._volume)

    def finished(self) -> bool:
        channel = self._channel
        if channel is None:
            return True
        try:
            return not channel.get_busy()
        except Exception:
            return True

    def stop(self):
        if self._channel is not None:
            self._channel.stop()

    def close(self):
        self.stop()
        self._channel = None
        self._sound = None


class NullTrack(AudioTrack):
    """没有可用后端时的占位：一切照做但发不出声，也不抛异常。"""

    name = "null"

    @classmethod
    def supported(cls) -> bool:
        return True

    def start(self, loop: bool, volume: float) -> bool:
        return False

    def set_volume(self, volume: float):
        pass

    def stop(self):
        pass

    def close(self):
        pass


#: 打开文件时依次尝试的后端（MCI 优先：零依赖）
_TRACK_BACKENDS = (MciTrack, PygameTrack)


def set_backends(backends):
    """替换后端序列（自检注入替身用），返回替换前的序列便于还原。

    自检要跑通「进入章节起播 / 跳到无音乐章节停播」这类接线，但**不该真的把
    声音放出来**——用替身后端既不出声，也不依赖机器上装了什么解码器。
    """
    global _TRACK_BACKENDS
    previous = _TRACK_BACKENDS
    _TRACK_BACKENDS = tuple(backends)
    return previous


def open_track(path: str):
    """按后端顺序尝试打开 ``path``；都失败返回 None。"""
    for backend in _TRACK_BACKENDS:
        try:
            track = backend.open(path)
        except Exception:
            track = None
        if track is not None:
            return track
    return None


def backend_name() -> str:
    """当前环境实际会用到的后端名（诊断/自检用）。"""
    for backend in _TRACK_BACKENDS:
        try:
            if backend.supported():
                return backend.name
        except Exception:
            continue
    return NullTrack.name


# ---------------- 播放器 ----------------

def _clamp01(value) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


class BgmPlayer:
    """章节背景音乐播放器（每个副本会话一个实例）。

    - :meth:`play` 传入 ``normalize_bgm`` 产出的 dict（空 dict/空 path = 停）；
    - 同一首曲子重复进入（章节跳转回到同一章）只把音量挪到目标值，
      **不从头重放**；
    - 换曲时先按旧曲的淡出秒数把音量推到 0，再打开新曲从 0 淡入；
    - 每条命令执行前都对一次 ``_generation``：后来的命令会让正在进行的
      渐变立刻让位，不会出现两首曲子的渐变互相打架。

    所有会改动音频设备的操作（打开文件、播放、渐变）都通过命令队列串行化，
    因此这里不需要任何锁——唯一会被别的线程读到的是日志回调与几个只读属性。
    """

    #: 音量渐变的分档步长（秒）
    FADE_STEP = 0.05
    #: 工作线程醒来的间隔（秒）：既承担渐变节拍，也承担"手动续播"的轮询
    TICK_SECONDS = 0.1

    def __init__(self, resolver=None, logger=None):
        self._resolver = resolver
        self._logger = logger
        self._jobs = queue.Queue()
        self._sentinel = object()
        self._thread = None
        self._generation = 0
        self._stopped = False
        # 当前在播的轨道与其状态（**只由工作线程写**，shutdown 兜底时例外）
        self._track = None
        self._key = None            # (解析后的绝对路径, 是否循环)
        self._volume = 0.0          # 当前音量 0.0~1.0
        self._fade_seconds = DEFAULT_BGM_FADE_SECONDS

    # ---------------- 对外 API（线程安全，立即返回） ----------------
    def play(self, spec) -> None:
        """切到 ``spec`` 描述的曲目；空 spec（或没有 path）表示停止播放。"""
        self._dispatch(self._apply, dict(spec or {}))

    def stop(self) -> None:
        """停止播放（保留最后一次的淡出时长）。"""
        self._dispatch(self._apply, {})

    def shutdown(self, timeout: float = 1.5) -> None:
        """会话收尾：停掉工作线程并关闭音频设备。

        工作线程是 daemon，``timeout`` 内没退干净（极少见：后端卡在打开文件）
        也不会拖住退出——此时直接在调用线程关设备，剩下的由解释器收摊。
        """
        self._stopped = True
        thread = self._thread
        if thread is None:
            # 没起过工作线程也可能还残留排队的命令（父线程创建失败时）——
            # 直接同步收尾，不留播放中的音频
            self._close_track()
            return
        self._generation += 1       # 让在跑的渐变立刻让位
        self._jobs.put(self._sentinel)
        try:
            thread.join(timeout=timeout)
        except Exception:
            pass
        if thread.is_alive():
            self._note(f"[BGM] 播放线程未在 {timeout}s 内退出，强制关闭音频设备")
        self._thread = None
        self._close_track()

    def wait_idle(self, timeout: float = 2.0) -> bool:
        """等到此前排队的命令全部处理完（自检/排障用，返回是否等到）。

        命令是异步的，连发两条时前一条可能还没开始——自检要断言"第二条报到
        了文件不存在"，就得有个能把异步拉直的点。
        """
        if self._thread is None or self._stopped:
            return True
        marker = threading.Event()
        # 用当前 generation 排队：不会被"后来者顶掉"的规则丢弃，轮到它时
        # 前面的命令一定已经执行完了
        self._jobs.put((self._generation, lambda _gen: marker.set(), ()))
        return marker.wait(timeout)

    @property
    def playing(self) -> bool:
        return self._track is not None

    @property
    def worker_alive(self) -> bool:
        """播放线程是否还在跑（自检用：会话收尾后应为 False）。"""
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def track_path(self) -> str:
        return (self._key or ("", False))[0]

    # ---------------- 内部：命令调度 ----------------
    def _dispatch(self, fn, *args):
        if self._stopped:
            return
        self._generation += 1
        generation = self._generation
        thread = self._ensure_thread()
        if thread is None:
            return
        self._jobs.put((generation, fn, args))

    def _ensure_thread(self):
        thread = self._thread
        if thread is not None and thread.is_alive():
            return thread
        try:
            thread = threading.Thread(target=self._loop, name="dungeon-bgm",
                                      daemon=True)
        except Exception:
            return None
        self._thread = thread
        thread.start()
        return thread

    def _loop(self):
        while True:
            try:
                item = self._jobs.get(timeout=self.TICK_SECONDS)
            except queue.Empty:
                # 空闲时也醒一下：不支持 repeat 的设备要在这里被续播
                if not self._stopped:
                    self._tick_track()
                continue
            if item is self._sentinel:
                return
            generation, fn, args = item
            # 被后来的命令顶掉：这一步已经没必要做（如连续跳两次章节）
            if generation != self._generation or self._stopped:
                continue
            try:
                fn(generation, *args)
            except Exception as exc:
                self._note(f"[BGM] 播放任务异常: {exc}")

    def _tick_track(self):
        track = self._track
        if track is None:
            return
        try:
            track.tick()
        except Exception as exc:
            self._note(f"[BGM] 续播检查失败: {exc}")

    def _note(self, message: str):
        logger = self._logger
        if logger is None:
            return
        try:
            logger(message)
        except Exception:
            pass

    # ---------------- 内部：播放逻辑（工作线程） ----------------
    def _apply(self, generation, spec: dict):
        path = str(spec.get("path") or "").strip()
        resolved = self._resolve(path) if path else None
        if path and resolved is None:
            self._note(f"[BGM] 音频文件不存在，已停止播放: {path}")
            self._fade_out(generation)
            return
        if resolved is None:
            # 本章节没有背景音乐：淡出并停掉（换到一个安静的章节也该安静）
            self._fade_out(generation)
            return

        loop = bool(spec.get("loop", True))
        try:
            volume = int(float(spec.get("volume", DEFAULT_BGM_VOLUME)))
        except (TypeError, ValueError):
            volume = DEFAULT_BGM_VOLUME
        target = max(0.0, min(1.0, volume / 100.0))
        try:
            fade = max(0.0, float(spec.get("fade_seconds",
                                           DEFAULT_BGM_FADE_SECONDS)))
        except (TypeError, ValueError):
            fade = DEFAULT_BGM_FADE_SECONDS

        key = (resolved, loop)
        if self._track is not None and self._key == key:
            # 同一首曲子：保持播放位置，只把音量挪过去（重复进入同一章节不该重放）
            if self._ramp(self._track, self._volume, target, fade, generation):
                self._volume = target
            return

        # 换曲：旧的淡出 → 关掉 → 新的从 0 淡入
        if self._track is not None:
            self._ramp(self._track, self._volume, 0.0, self._fade_seconds,
                       generation)
            self._close_track()

        track = open_track(resolved)
        if track is None:
            self._note(f"[BGM] 无法播放音频（没有可用的音频后端）: {resolved}")
            self._key = None
            self._volume = 0.0
            return
        self._track = track
        self._key = key
        self._fade_seconds = fade
        self._volume = 0.0
        if not track.start(loop=loop, volume=0.0):
            self._note(f"[BGM] 音频未能起播（格式不受支持或解码失败）: {resolved}")
            self._close_track()
            return
        if self._ramp(track, 0.0, target, fade, generation):
            self._volume = target

    def _fade_out(self, generation):
        """淡出并关掉当前曲目（新命令到达时中途让位，由它接手处理旧轨）。"""
        if self._track is None:
            return
        self._ramp(self._track, self._volume, 0.0, self._fade_seconds, generation)
        self._close_track()

    def _close_track(self):
        track = self._track
        self._track = None
        self._key = None
        self._volume = 0.0
        if track is None:
            return
        try:
            track.close()
        except Exception:
            pass

    def _ramp(self, track, start: float, target: float, seconds: float,
              generation) -> bool:
        """把 ``track`` 的音量在 ``seconds`` 内从 ``start`` 渐变到 ``target``。

        返回 False 表示中途被后来的命令打断（此时调用方不应继续后续步骤）。
        """
        if seconds <= 0:
            track.set_volume(target)
            return True
        steps = max(1, int(seconds / self.FADE_STEP))
        for index in range(1, steps + 1):
            if not self._sleep_one(generation):
                return False
            track.set_volume(start + (target - start) * (index / steps))
        return True

    def _sleep_one(self, generation) -> bool:
        """渐变的一小步；期内有新命令就让位（返回 False）。"""
        deadline = time.monotonic() + self.FADE_STEP
        while True:
            if generation != self._generation or self._stopped:
                return False
            remain = deadline - time.monotonic()
            if remain <= 0:
                return True
            time.sleep(min(self.FADE_STEP, remain))

    def _resolve(self, path: str):
        """相对路径按解析回调（窗口层注入的方案目录）还原；绝对路径直接用。"""
        if self._resolver is not None:
            try:
                resolved = self._resolver(path)
            except Exception:
                resolved = None
            if resolved:
                return resolved
        if os.path.isabs(path) and os.path.isfile(path):
            return path
        return None
