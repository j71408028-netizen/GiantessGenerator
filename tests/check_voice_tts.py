"""语音合成（edge-tts）**代理支持、端点回退与失败原因**自检：离线、不联网。

用法：``python tests/check_voice_tts.py``
输出：控制台一行 ASCII 结论 + UTF-8 报告文件路径。

背景：2026-10 微软把朗读合成从旧主机 ``speech.platform.bing.com`` 迁到
``api.msedgeservices.com``，全球分批铺开期间旧主机普遍回 403（edge-tts
issue #490），合成直接失败、副本一句都不念。上游修法见 edge-tts PR #491
（7.2.8 尚未并入），本项目在本地补齐；同时把失败原因**说清楚**（原先只笼统报
「网络不可达」，既不准也无从下手）。

覆盖：
1. 代理优先级：显式设置 > 环境变量（DUNGEON_TTS_PROXY / EDGE_TTS_PROXY /
   HTTPS_PROXY / HTTP_PROXY）> 空串（直连）；
2. ``set_proxy(None/``"")`` 交还环境变量；
3. 代理**真的**传给了 ``edge_tts.Communicate``（用替身抓关键字参数；没配则不传）；
4. 失败原因分类：403 / 401 / 超时 / 时钟 / 代理参数不被支持 / 代理连不上 /
   地址非法 / 其它；
5. ``_edge_synth`` 每次先清空上次原因；没装 edge-tts 时给出可读原因且不抛异常；
6. 失败原因能被**预听**复用（预听提示就是副本不出声的原因）；
7. 接线：``net_proxy`` 默认值为空串（直连）；语音侧写的与 AI 文本生成读的是
   ``core.netproxy`` 里**同一份**覆盖值；三处装配点（启动 / 副本子进程 / 设置页
   保存）确实会写它——按 AST 认 ``.set_override(...)`` 调用，改引号、换行、空白
   都不会误报，删掉这行接线仍会红；
8. 代理写法整理：缺 scheme 补 ``http://``，SOCKS / 未知协议明确拒绝；
9. 端点候选首选新主机、旧主机回退；新主机失败会自动回落旧主机并记住可用端点；
   若已装版本已修好（不再指向旧主机）则不插手；
10. 两个端点都失败时仍给出可读原因（含 403）。
"""

import ast
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

# ---------------- 8. 代理写法整理（normalize_proxy） ----------------

check("空代理 / None = 直连",
      speech.normalize_proxy("") == "" and speech.normalize_proxy(None) == "")
check("缺 scheme 自动补 http://（否则 aiohttp 只会抛 InvalidURL）",
      speech.normalize_proxy("127.0.0.1:7890") == "http://127.0.0.1:7890",
      speech.normalize_proxy("127.0.0.1:7890"))
check("已有 scheme 原样返回",
      speech.normalize_proxy(" http://a:1 ") == "http://a:1",
      speech.normalize_proxy(" http://a:1 "))


def _raises_value_error(fn, needle):
    try:
        fn()
    except ValueError as exc:
        return needle in str(exc)
    return False


check("SOCKS 代理明确拒绝（aiohttp 不装 aiohttp-socks 走不了 SOCKS）",
      _raises_value_error(lambda: speech.normalize_proxy("socks5://127.0.0.1:1080"),
                          "SOCKS"))
check("不认识的协议明确拒绝",
      _raises_value_error(lambda: speech.normalize_proxy("ftp://x:1"), "http"))

# ---------------- 9. 端点上新主机（2026-10 微软换主机，edge-tts issue #490） ----------------

import edge_tts  # noqa: E402

_endpoints = speech.synthesis_endpoints()
check("端点候选：首选新主机 api.msedgeservices.com，旧主机作回退",
      bool(_endpoints) and "api.msedgeservices.com" in _endpoints[0]
      and any("speech.platform.bing.com" in u for u in _endpoints),
      _endpoints)
check("新主机沿用旧令牌、改挂 Ocp-Apim-Subscription-Key",
      "Ocp-Apim-Subscription-Key=" in _endpoints[0], _endpoints)


def _candidates_with(url):
    """把库的 WSS_URL 换成给定值后，看我们会尝试哪些端点。"""
    saved = edge_tts.communicate.WSS_URL
    try:
        edge_tts.communicate.WSS_URL = url
        speech._working_wss_url = ""
        return speech._endpoint_candidates(edge_tts)
    finally:
        edge_tts.communicate.WSS_URL = saved
        speech._working_wss_url = ""


check("上游哪天修好（不再指向旧主机）：不插手，只用它自己的端点",
      _candidates_with("wss://example.invalid/fixed/v2") == ["wss://example.invalid/fixed/v2"],
      _candidates_with("wss://example.invalid/fixed/v2"))


class _EndpointAware:
    """新主机上抛 403、旧主机上给音频——验证「新 → 旧」回退真的会走。"""

    def __init__(self, text, voice, *, rate=None, **kwargs):
        self.kwargs = kwargs

    async def stream(self):
        url = getattr(edge_tts.communicate, "WSS_URL", "")
        if "api.msedgeservices.com" in url:
            raise RuntimeError("403, message='Invalid response status'")
        yield {"type": "audio", "data": b"legacy-audio"}


_orig_communicate_2 = edge_tts.Communicate
speech._working_wss_url = ""
edge_tts.Communicate = _EndpointAware
try:
    _p6 = speech._edge_synth("zh-CN-XiaoxiaoNeural", "+0%", "hi",
                             tempfile.mkdtemp(prefix="voice_tts_probe_"))
    check("新主机失败 → 自动回退旧主机，成功且不报错",
          bool(_p6) and speech.last_synth_error() == "",
          f"{_p6} / {speech.last_synth_error()}")
    check("回退成功后记住可用端点",
          "speech.platform.bing.com" in speech._working_wss_url,
          speech._working_wss_url)
    check("记住端点后，它被排到候选首位",
          speech._endpoint_candidates(edge_tts)[0] == speech._working_wss_url,
          speech._endpoint_candidates(edge_tts))
finally:
    edge_tts.Communicate = _orig_communicate_2
    speech._working_wss_url = ""

# 两个端点都失败时，原因仍要说清（403）
class _Always403:
    def __init__(self, *a, **k):
        raise RuntimeError("403, message='Invalid response status'")


_speech_probe_dir = tempfile.mkdtemp(prefix="voice_tts_probe_")
edge_tts.Communicate = _Always403
try:
    _p7 = speech._edge_synth("zh-CN-XiaoxiaoNeural", "+0%", "hi", _speech_probe_dir)
    check("两个端点都失败：返回 None 且原因含 403",
          _p7 is None and "403" in speech.last_synth_error(),
          speech.last_synth_error())
finally:
    edge_tts.Communicate = _orig_communicate_2
    speech._last_synth_error = ""

# SOCKS 代理：直接给可读原因，且不发起合成
_orig_communicate_3 = edge_tts.Communicate
_called = []


class _NeverCall:
    def __init__(self, *a, **k):
        _called.append(1)


os.environ["DUNGEON_TTS_PROXY"] = "socks5://127.0.0.1:1080"
try:
    speech.set_proxy(None)
    edge_tts.Communicate = _NeverCall
    _p8 = speech._edge_synth("zh-CN-XiaoxiaoNeural", "+0%", "hi",
                            tempfile.mkdtemp(prefix="voice_tts_probe_"))
    check("SOCKS 代理：不发起合成、直接给可读原因",
          _p8 is None and not _called and "SOCKS" in speech.last_synth_error(),
          f"{_p8} / {_called} / {speech.last_synth_error()}")
finally:
    edge_tts.Communicate = _orig_communicate_3
    os.environ.pop("DUNGEON_TTS_PROXY", None)
    speech.set_proxy(None)
    speech._last_synth_error = ""

# ---------------- 10. 失败原因的新分类 ----------------


class _InvalidURL(Exception):
    pass


class _ClientProxyConnectionError(Exception):
    pass


class _SkewAdjustmentError(Exception):
    pass


_d2 = speech._describe_synth_error
check("代理地址不合法 → 提示写成 http://主机:端口",
      "http://" in _d2(_InvalidURL("127.0.0.1:7890")),
      _d2(_InvalidURL("127.0.0.1:7890")))
check("连不上代理 → 提到代理",
      "代理" in _d2(_ClientProxyConnectionError("Cannot connect to host 127.0.0.1:1")),
      _d2(_ClientProxyConnectionError("boom")))
check("时钟校验失败 → 提到时间",
      "时间" in _d2(_SkewAdjustmentError("No server date in headers")),
      _d2(_SkewAdjustmentError("boom")))

# ---------------- 7. 接线 ----------------

# 默认值走公开的 load()，并给一个空的 data_dir：既证明"新装默认是直连"，又不会
# 碰到真实 data/ 里用户自己填过的代理。
check("settings 默认 net_proxy 为空串（直连；语音与 AI 共用同一个键）",
      SettingsRepo(data_dir=tempfile.mkdtemp(prefix="voice_tts_settings_"))
      .load().get("net_proxy") == "")

# "共用"的实测口径：语音侧写的、AI 侧读的都是 core.netproxy 里同一份覆盖值
from core import netproxy as _netproxy  # noqa: E402

speech.set_proxy("http://shared-proxy:9")
check("语音设置代理 = 写进 core.netproxy 的共用覆盖值",
      _netproxy.resolve() == "http://shared-proxy:9", _netproxy.resolve())
_netproxy.set_override("http://set-elsewhere:1")
check("别处写进共用覆盖值，语音也认（三处装配点写的都是它）",
      speech.resolve_proxy() == "http://set-elsewhere:1", speech.resolve_proxy())
speech.set_proxy(None)
_netproxy.set_override("")


def _module_tree(rel_path):
    """解析仓库里的模块，返回 AST；读不到 / 解析不了返回 None。"""
    try:
        with open(os.path.join(_PROJECT_ROOT, rel_path), encoding="utf-8") as handle:
            return ast.parse(handle.read())
    except (OSError, SyntaxError):
        return None


def _writes_shared_proxy(tree):
    """AST 扫：模块里有没有 ``<某对象>.set_override(...)`` 调用。

    刻意用 AST 而不是源码子串——改引号、换行、动空白都不会误报，而删掉这处装配
    仍会红。要守的只是"这里还在写代理"，没必要连那行字面量长什么样一起钉死。
    """
    return any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
               and node.func.attr == "set_override" for node in ast.walk(tree))


for _rel, _why in (
    ("app/shell.py", "启动装配"),
    ("dungeon/window/base.py", "副本子进程（独立进程，主进程设的带不过来）"),
    ("ui/settings/__init__.py", "设置页保存后立即生效"),
):
    _tree = _module_tree(_rel)
    if _tree is None:
        check(f"{_why}：{_rel} 可读可解析", False, "源码读取/解析失败")
        continue
    check(f"{_why}：{_rel} 会写共用代理（.set_override）", _writes_shared_proxy(_tree))

_settings_tree = _module_tree("ui/settings/__init__.py")
_settings_strings = ({
    node.value for node in ast.walk(_settings_tree)
    if isinstance(node, ast.Constant) and isinstance(node.value, str)
} if _settings_tree is not None else set())
check("设置页把 net_proxy 存进设置字典（否则重启即失效）",
      "net_proxy" in _settings_strings)

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
