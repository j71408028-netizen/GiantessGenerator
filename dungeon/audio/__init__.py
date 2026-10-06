"""章节背景音乐的播放（纯领域模块：不碰任何 UI 框架）。

曲目**怎么配**归 ``dungeon/audio/chapters.py``（``normalize_bgm``：路径/音量/循环/
淡入淡出秒数）；本模块只管**怎么放出来**——给定一条配置，就把对应音频文件
循环播放到指定音量，换章节时能把上一首淡出去。

为什么不用现成的音频库：需要的能力只有「按给定音量循环播一个文件、可随时
换曲并淡入淡出」这么点。Windows 自带的 MCI（``winmm.dll`` 的
``mciSendStringW``）零依赖就能做，mp3/wav/wma 走同一套；MCI 打不开的格式
（ogg/flac 这类系统解码器没有的）与非 Windows 平台退到 miniaudio（装了就
用，整段解码进内存、进程内播放），都不可用时退
到 :class:`NullTrack`——**没有声音不该拖垮一局副本**，缺后端只在日志里留
一条提示。

线程模型沿用 ``window/background.py`` 的像素工作者：唯一一条后台线程串行消费
命令队列。章节流调 :meth:`BgmPlayer.play` 只入队就返回，换曲与音量渐变都不占
帧循环的时间。
"""

import ctypes
import os
import queue
import threading
import time

from dungeon.audio.chapters import DEFAULT_BGM_FADE_SECONDS, DEFAULT_BGM_VOLUME


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


# ---------------- 可选后端：miniaudio ----------------

_MINIAUDIO_CACHE = None


def _miniaudio():
    """取 ``miniaudio`` 模块（可选依赖）；没装返回 None（结果缓存）。"""
    global _MINIAUDIO_CACHE
    if _MINIAUDIO_CACHE is None:
        try:
            import miniaudio as module
        except Exception:
            module = False
        _MINIAUDIO_CACHE = module
    return _MINIAUDIO_CACHE or None


class MiniaudioTrack(AudioTrack):
    """miniaudio 进程内解码播放：MCI 不认的格式与非 Windows 平台的兜底。

    整段解码进内存（重采样/声道转换在 C 层顺带完成），回调线程按设备索要的
    帧数供数——**必须喂满**：供少了设备等满一个周期再要，听感是时长被拉长、
    放放停停。音量在供数时逐块乘增益（numpy 缺席时音量旋钮失灵：0 当静音、
    其余直放，只记一条日志）。每个实例独占一个 ``PlaybackDevice``（系统的
    音频后端允许多实例并存），``close`` 时释放。

    回调协议的两个坑（``scripts/_test_miniaudio_playback.py`` 有实测）：
    generator 必须先 ``next()`` prime 再交给 ``device.start()``；以及所有
    供数都发生在绑定内部的回调线程上，本类只在起播前把数据准备好。
    """

    name = "miniaudio"

    def __init__(self, data: bytes, channels: int, rate: int):
        self._data = data              # 交错 S16 原始字节
        self._channels = channels
        self._rate = rate
        self._device = None
        self._volume = 1.0
        self._closed = False
        self._done = threading.Event()
        self._np = _numpy()
        self._samples = None
        if self._np is not None:
            self._samples = self._np.frombuffer(data, dtype="<i2")

    # ---------- 后端自检 ----------
    @classmethod
    def supported(cls) -> bool:
        return _miniaudio() is not None

    @classmethod
    def open(cls, path: str):
        module = _miniaudio()
        if module is None:
            return None
        try:
            decoded = module.decode_file(path)
        except Exception:
            return None
        return cls(bytes(decoded.samples), int(decoded.nchannels),
                   int(decoded.sample_rate))

    # ---------- 播放 ----------
    def start(self, loop: bool, volume: float) -> bool:
        module = _miniaudio()
        if self._closed or module is None:
            return False
        self._volume = _clamp01(volume)
        try:
            device = module.PlaybackDevice(
                output_format=module.SampleFormat.SIGNED16,
                nchannels=self._channels, sample_rate=self._rate,
                buffersize_msec=60)
            voice = self._voice(loop)
            next(voice)                     # 绑定要求：传入前必须已 prime
            device.start(voice)
        except Exception:
            self._release_device()
            return False
        self._device = device
        return True

    def _voice(self, loop: bool):
        """回调 generator：按索要帧数供数，播完（或循环）才让它耗尽。

        每次 ``yield`` 的返回值就是回调线程下一次索要的帧数——必须接住，
        不能固定吐块（供少了设备等满一个周期，时长会被拉长）。
        """
        width = 2 * self._channels          # S16 每帧字节数
        total = len(self._data)
        frames = yield                      # prime 后的第一个 send
        while True:
            cursor = 0
            while cursor < total:
                chunk = self._data[cursor:cursor + max(1, frames) * width]
                cursor += len(chunk)
                frames = yield self._apply_gain(chunk)
            if not loop:
                break
        self._done.set()

    def _apply_gain(self, chunk: bytes) -> bytes:
        volume = self._volume
        if volume >= 0.999:
            return chunk
        if self._samples is None:
            # 没有 numpy：0 当静音，其余直放（音量旋钮失灵但出声）
            return b"\x00" * len(chunk) if volume <= 0.001 else chunk
        count = len(chunk) // 2
        scaled = self._samples[:count] * volume
        return scaled.astype("<i2").tobytes()

    def finished(self) -> bool:
        return self._done.is_set()

    def set_volume(self, volume: float):
        self._volume = _clamp01(volume)

    def stop(self):
        self._release_device()
        self._done.set()

    def close(self):
        self._closed = True
        self._release_device()
        self._done.set()

    def _release_device(self):
        device, self._device = self._device, None
        if device is None:
            return
        try:
            device.stop()
        except Exception:
            pass
        try:
            device.close()
        except Exception:
            pass


def _numpy():
    """取 ``numpy`` 模块（可选依赖）；没装返回 None（结果缓存）。"""
    global _NUMPY_CACHE
    if _NUMPY_CACHE is None:
        try:
            import numpy as module
        except Exception:
            module = False
        _NUMPY_CACHE = module
    return _NUMPY_CACHE or None


_NUMPY_CACHE = None


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


#: 打开文件时依次尝试的后端：Windows 上 MCI 领头（零依赖、流式），miniaudio
#: 补上 MCI 不认的格式（ogg/flac 等）与非 Windows 平台。
if os.name == "nt":
    _TRACK_BACKENDS = (MciTrack, MiniaudioTrack)
else:
    _TRACK_BACKENDS = (MiniaudioTrack)


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


# ---------------- 一次性音效 ----------------

class SfxPlayer:
    """一次性音效播放器（小游戏 / 短暂视效的发声入口）。

    与 :class:`BgmPlayer` 的分工：BGM 是"常驻一路、换曲渐变"，音效是
    "触发一发、放完就走"。开轨、收割、停轨**全部收敛在一条工作线程**上——
    MCI 实测有线程亲和性（``status ... mode`` 查询在非打开线程上返回空，
    收割线程永远等不到"播完"），这也是 :class:`BgmPlayer` 单线程命令队列的
    同款原因。``play`` 只做满员判定并入队，立即返回。

    并发封顶：满员就拒绝本次请求——宁可这次不响，也不掐正在播的。
    任何失败只记日志：**音效不出声不该拖垮触发它的那件事**。播放走
    :func:`open_track`（MCI / miniaudio依次尝试），本类不管解码。
    """

    #: 同时在播的音效上限（voice_fx 实测 MCI 可同时开 8 路无压力）
    MAX_CONCURRENT = 8
    #: 单条音轨的强制回收上限（秒）：播完判定失灵的轨道不能永远占坑
    MAX_TRACK_SECONDS = 300.0
    #: 工作线程空转的收割间隔（秒）
    REAP_SECONDS = 0.5

    def __init__(self, logger=None):
        self._logger = logger
        self._jobs = queue.Queue()
        self._sentinel = object()
        self._lock = threading.Lock()   # 只护 _stopped/_count（工作线程外仅读计数）
        self._count = 0                 # 已接受请求 = 在播 + 排队未起播
        self._tracks = []               # [(track, monotonic 起点)]（仅工作线程碰）
        self._thread = None
        self._stopped = False

    @property
    def active_count(self) -> int:
        with self._lock:
            return self._count

    def play(self, path: str, volume: float = 1.0) -> bool:
        """排一个音效（异步起播）。返回 False = 已收尾或满员被拒。"""
        with self._lock:
            if self._stopped:
                return False
            if self._count >= self.MAX_CONCURRENT:
                self._note(f"[SFX] 音效并发已满（{self.MAX_CONCURRENT} 路），"
                           f"丢弃本次: {path}")
                return False
            self._count += 1
        if not self._ensure_worker():
            with self._lock:
                self._count -= 1
            return False
        self._jobs.put((path, volume))
        return True

    def stop_all(self):
        """停掉并回收所有在播/排队的音效（之后还能继续 play）。"""
        self._jobs.put((None, 0.0))    # 停轨作业：见 _loop 的 path 判定

    def shutdown(self, timeout: float = 1.5):
        """会话收尾：清空音轨、停掉工作线程，之后不再接受 play。"""
        with self._lock:
            self._stopped = True
        thread = self._thread
        self._jobs.put(self._sentinel)
        if thread is not None and thread is not threading.current_thread():
            try:
                thread.join(timeout=timeout)
            except Exception:
                pass

    # ---------------- 工作线程 ----------------
    def _ensure_worker(self) -> bool:
        thread = self._thread
        if thread is not None and thread.is_alive():
            return True
        try:
            thread = threading.Thread(target=self._loop, name="dungeon-sfx",
                                      daemon=True)
        except Exception:
            return False
        self._thread = thread
        thread.start()
        return True

    def _loop(self):
        while True:
            try:
                job = self._jobs.get(timeout=self.REAP_SECONDS)
            except queue.Empty:
                job = None
            if job is self._sentinel:
                self._close_all()
                return
            if job is not None:
                path, volume = job
                if path is None:        # stop_all 的停轨作业
                    self._close_all()
                else:
                    self._start_one(path, volume)
            self._reap()
            with self._lock:
                stopped = self._stopped
            if stopped and not self._tracks and self._jobs.empty():
                return

    def _start_one(self, path: str, volume: float):
        if self._stopped:
            with self._lock:
                self._count -= 1
            return
        track = open_track(path)
        started = False
        if track is not None:
            try:
                started = track.start(loop=False, volume=_clamp01(volume))
            except Exception:
                started = False
        if not started:
            if track is not None:
                try:
                    track.close()
                except Exception:
                    pass
            with self._lock:
                self._count -= 1
            self._note(f"[SFX] 音效未能起播: {path}")
            return
        self._tracks.append((track, time.monotonic()))

    def _reap(self):
        """回收播完（或超时）的音轨；仅工作线程调用。"""
        now = time.monotonic()
        alive = []
        for track, started_at in self._tracks:
            expired = now - started_at > self.MAX_TRACK_SECONDS
            try:
                finished = bool(track.finished())
            except Exception:
                finished = False
            if finished or expired:
                try:
                    track.close()
                except Exception:
                    pass
                with self._lock:
                    self._count -= 1
                continue
            alive.append((track, started_at))
        self._tracks = alive

    def _close_all(self):
        for track, _started in self._tracks:
            try:
                track.close()
            except Exception:
                pass
        with self._lock:
            self._count -= len(self._tracks)
        self._tracks = []

    def _note(self, message: str):
        logger = self._logger
        if logger is None:
            return
        try:
            logger(message)
        except Exception:
            pass
