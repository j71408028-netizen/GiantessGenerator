"""语音合成（edge-tts）**代理支持与失败原因**自检：离线、不联网。

用法：``python tests/check_voice_tts.py``
输出：控制台一行 ASCII 结论 + UTF-8 报告文件路径。

背景：微软的朗读接口会对部分网络（典型是国区）返回 403，合成直接失败、
副本一句都不念。修法不是改渲染链，而是（a）能走代理，（b）把失败原因**说清楚**
（原先只笼统报「网络不可达」，既不准也无从下手）。

覆盖：
1. 代理优先级：显式设置 > 环境变量（DUNGEON_TTS_PROXY / EDGE_TTS_PROXY /
   HTTPS_PROXY / HTTP_PROXY）> 空串（直连）；
2. ``set_proxy(None/``"")`` 交还环境变量；
3. 代理**真的**传给了 ``edge_tts.Communicate``（用替身抓关键字参数；没配则不传）；
4. 失败原因分类：403 / 超时 / 代理参数不被支持 / 其它；
5. ``_edge_synth`` 每次先清空上次原因；没装 edge-tts 时给出可读原因且不抛异常；
6. 失败原因能被**预听**复用（预听提示就是副本不出声的原因）；
7. 接线守卫：settings 默认含 ``voice_proxy``；启动装配、副本子进程、设置页
   保存三处都会 ``set_proxy``。
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_report_path = os.path.join(tempfile.mkdtemp(prefix="voice_tts_"), "report.txt")
_log = open(_report_path, "w", encoding="utf-8")
sys.stdout = _log
sys.stderr = _log

failures = []
total = 0
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check(name, ok, extra=""):
    global total
    total += 1
    print(("  OK   " if ok else "  FAIL ") + name
          + (f"  <{extra}>" if extra and not ok else ""))
    if not ok:
        failures.append(name)


from dungeon.audio import preview as preview_mod       # noqa: E402
from dungeon.audio import speech                       # noqa: E402
from persistence import SettingsRepo                   # noqa: E402

# ---------------- 1/2. 代理优先级 ----------------

_ENV_KEYS = speech._PROXY_ENV_KEYS
_saved_env = {k: os.environ.get(k) for k in _ENV_KEYS}
for _k in _ENV_KEYS:
    os.environ.pop(_k, None)

try:
    speech.set_proxy(None)
    check("都没设：resolve_proxy 为空（直连）", speech.resolve_proxy() == "")

    os.environ["HTTPS_PROXY"] = " http://env-proxy:1 "
    check("只有环境变量：取环境变量并去空白",
          speech.resolve_proxy() == "http://env-proxy:1", speech.resolve_proxy())

    os.environ["DUNGEON_TTS_PROXY"] = "http://dungeon-proxy:2"
    check("专用键优先于通用键",
          speech.resolve_proxy() == "http://dungeon-proxy:2", speech.resolve_proxy())

    speech.set_proxy("http://explicit-proxy:3")
    check("显式设置优先于环境变量",
          speech.resolve_proxy() == "http://explicit-proxy:3", speech.resolve_proxy())

    speech.set_proxy("   ")
    check("显式设成空白 = 交还环境变量",
          speech.resolve_proxy() == "http://dungeon-proxy:2", speech.resolve_proxy())

    for _k in _ENV_KEYS:
        os.environ.pop(_k, None)
    speech.set_proxy(None)
    check("清空后回到直连", speech.resolve_proxy() == "")

    # ---------------- 3. 代理真的传给了 Communicate ----------------

    class _FakeCommunicate:
        calls = []

        def __init__(self, text, voice, *, rate=None, **kwargs):
            _FakeCommunicate.calls.append({"voice": voice, "rate": rate,
                                           "kwargs": kwargs})

        async def stream(self):
            yield {"type": "audio", "data": b"abc"}
            yield {"type": "WordBoundary", "data": b""}

    import edge_tts
    _orig_communicate = edge_tts.Communicate
    edge_tts.Communicate = _FakeCommunicate
    try:
        tmp = tempfile.mkdtemp(prefix="voice_tts_probe_")
        speech.set_proxy(None)
        _FakeCommunicate.calls = []
        _p1 = speech._edge_synth("zh-CN-XiaoxiaoNeural", "+0%", "hi", tmp)
        _kwargs_none = dict(_FakeCommunicate.calls[-1]["kwargs"])
        check("没配代理：不传 proxy 关键字（老版本 edge-tts 也不会炸）",
              _kwargs_none == {} and _p1 and os.path.exists(_p1), _kwargs_none)

        speech.set_proxy("http://my-proxy:8080")
        _FakeCommunicate.calls = []
        _p2 = speech._edge_synth("zh-CN-XiaoxiaoNeural", "+0%", "hi", tmp)
        _kwargs_proxy = dict(_FakeCommunicate.calls[-1]["kwargs"])
        check("配了代理：proxy 传到 Communicate",
              _kwargs_proxy.get("proxy") == "http://my-proxy:8080" and _p2,
              _kwargs_proxy)

        check("合成成功时清空失败原因", speech.last_synth_error() == "",
              speech.last_synth_error())

        # ---------------- 4/5. 失败原因分类 ----------------

        _d = speech._describe_synth_error

        class _WSS403(Exception):
            pass

        check("403 → 提到限流与「代理」",
              "403" in _d(_WSS403("403, message='Invalid response status'"))
              and "代理" in _d(_WSS403("403, message='Invalid response status'")))
        check("超时 → 提到超时",
              "超时" in _d(TimeoutError("Connection timeout")))
        check("edge-tts 不认 proxy → 提示升级",
              "升级" in _d(TypeError("Communicate() got an unexpected keyword "
                                     "argument 'proxy'")))
        check("其它异常 → 至少给出异常名",
              "ValueError" in _d(ValueError("boom"))
              and "网络" in _d(ValueError("boom")))

        # 失败路径要把原因记下来
        class _BoomCommunicate:
            def __init__(self, *a, **k):
                raise RuntimeError("403 forbidden")

        edge_tts.Communicate = _BoomCommunicate
        _p3 = speech._edge_synth("zh-CN-XiaoxiaoNeural", "+0%", "hi", tmp)
        check("合成抛异常：返回 None 且记下可读原因",
              _p3 is None and "403" in speech.last_synth_error(),
              speech.last_synth_error())

        # 6. 预听复用同一条原因
        _msg = preview_mod.VoicePreview(synth=lambda *a: None)._synth_failure_reason()
        check("预听复用同一条失败原因（提示里有 403）",
              "403" in _msg and "合成" in _msg, _msg)

        # 成功一次要把上次的失败原因清掉
        edge_tts.Communicate = _FakeCommunicate
        _FakeCommunicate.calls = []
        _p4 = speech._edge_synth("zh-CN-XiaoxiaoNeural", "+0%", "hi", tmp)
        check("下一次尝试先清空上次原因", _p4 and speech.last_synth_error() == "",
              speech.last_synth_error())
    finally:
        edge_tts.Communicate = _orig_communicate
        speech.set_proxy(None)

    # 没装 edge-tts：给出可读原因、返回 None、不抛异常
    _mod = sys.modules.get("edge_tts")
    sys.modules["edge_tts"] = None
    try:
        _p5 = speech._edge_synth("zh-CN-XiaoxiaoNeural", "+0%", "hi",
                                 tempfile.mkdtemp(prefix="voice_tts_probe_"))
        check("没装 edge-tts：返回 None + 可读原因（含安装提示）",
              _p5 is None and "edge-tts" in speech.last_synth_error(),
              speech.last_synth_error())
    finally:
        if _mod is not None:
            sys.modules["edge_tts"] = _mod
        else:
            sys.modules.pop("edge_tts", None)
        speech.set_proxy(None)
finally:
    for _k, _v in _saved_env.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v

# ---------------- 7. 接线守卫 ----------------

check("settings 默认含 voice_proxy（空串 = 直连）",
      SettingsRepo()._defaults.get("voice_proxy") == "",
      SettingsRepo()._defaults.get("voice_proxy"))

_shell_src = open(os.path.join(_PROJECT_ROOT, "app", "shell.py"),
                  encoding="utf-8").read()
_base_src = open(os.path.join(_PROJECT_ROOT, "dungeon", "window", "base.py"),
                 encoding="utf-8").read()
_settings_src = open(os.path.join(_PROJECT_ROOT, "ui", "settings", "__init__.py"),
                     encoding="utf-8").read()

check("启动装配设置代理（app/shell.py）",
      "speech_audio.set_proxy(settings.get(\"voice_proxy\"))" in _shell_src)
check("副本子进程设置代理（dungeon/window/base.py，独立进程带不过来）",
      "set_speech_proxy(" in _base_src
      and "voice_proxy" in _base_src)
check("设置页：有输入框、写进保存字典、保存后立即生效",
      "voice_proxy_var" in _settings_src
      and "\"voice_proxy\": self.voice_proxy_var.get().strip()" in _settings_src
      and "speech_audio.set_proxy(self.settings.get(\"voice_proxy\"))" in _settings_src)

# ---------------- 结论 ----------------
_log.flush()
sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__
if failures:
    print(f"[check_voice_tts] FAILED {len(failures)}/{total}: {failures}")
else:
    print(f"[check_voice_tts] PASSED {total}/{total}")
print(f"[check_voice_tts] report: {_report_path}")
sys.exit(1 if failures else 0)
