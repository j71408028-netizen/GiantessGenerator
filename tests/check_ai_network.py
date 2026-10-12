"""AI 文本生成**网络代理与失败原因**自检：离线、不联网。

用法：``python tests/check_ai_network.py``
输出：控制台一行 ASCII 结论 + UTF-8 报告文件路径。

背景：语音合成早已有「代理 + 可读失败原因」这一层（见 ``check_voice_tts``），
AI 文本生成（``core/ai.py``，OpenAI 兼容）却一直把 ``str(e)`` 原样透出——玩家
看到的是 ``Connection error.`` 或 ``Error code: 401 - ...``，既看不懂也不知道
改什么；代理更是只能靠环境变量，VPN 的系统代理（WinINET）Python 根本看不见。
本次把两者**对齐**：共用一份代理设置（设置页「网络代理」）与一套失败分类。

覆盖：
1. ``core.netproxy`` 的口径：显式设置 > ``extra_env`` > 通用环境变量 > 直连；
   代理整理（缺 scheme 补 ``http://``、SOCKS / 未知协议明确拒绝）；
2. 语音与 AI **共用同一份 override**：任一侧设置，另一侧立刻看得到；
3. AI 客户端**真的**注入了代理：配了代理时构造 ``DefaultHttpxClient(proxy=...)``
   注入 ``OpenAI(http_client=...)``，没配则不动（交 SDK 默认，照常读环境变量）；
4. AI 失败原因分类：401 / 403 / 404 / 429 / 400 / 5xx / 超时 / 连接 / 代理 /
   SOCKS / 未装 openai / 配置类 ValueError 原文；
5. ``test_connection`` 用可读原因；
6. settings 迁移：旧键 ``voice_proxy`` → ``net_proxy``（都设时新键胜、旧键被清）；
7. 接线守卫：设置、启动装配、副本子进程、以及各暴露点都接上。
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_report_path = os.path.join(tempfile.mkdtemp(prefix="ai_network_"), "report.txt")
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


from core import ai, netproxy                       # noqa: E402
from dungeon.audio import speech                    # noqa: E402
from persistence import SettingsRepo                 # noqa: E402

# ---------------- 1. netproxy 口径 ----------------

_saved_env = {k: os.environ.get(k) for k in netproxy.ENV_KEYS}
for _k in netproxy.ENV_KEYS:
    os.environ.pop(_k, None)

try:
    netproxy.set_override(None)
    check("都没设：空串（直连）", netproxy.resolve() == "")

    os.environ["HTTPS_PROXY"] = " http://env-proxy:1 "
    check("环境变量：取用并去空白",
          netproxy.resolve() == "http://env-proxy:1", netproxy.resolve())

    check("缺 scheme 补 http://",
          netproxy.normalize_proxy("127.0.0.1:7890") == "http://127.0.0.1:7890",
          netproxy.normalize_proxy("127.0.0.1:7890"))
    check("显式设置优先于环境变量",
          (netproxy.set_override("http://explicit:2"),
           netproxy.resolve() == "http://explicit:2")[1])
    check("显式设成空白 = 交还环境变量",
          (netproxy.set_override("   "), netproxy.resolve() == "http://env-proxy:1")[1])
    os.environ["DUNGEON_TTS_PROXY"] = "http://tts-only:3"
    check("extra_env 优先于通用环境变量",
          netproxy.resolve(extra_env=("DUNGEON_TTS_PROXY",)) == "http://tts-only:3",
          netproxy.resolve(extra_env=("DUNGEON_TTS_PROXY",)))
    check("不传 extra_env 时通用键仍可用",
          netproxy.resolve() == "http://env-proxy:1", netproxy.resolve())
    os.environ.pop("DUNGEON_TTS_PROXY", None)

    def _raises(fn, needle):
        try:
            fn()
            return False
        except ValueError as exc:
            return needle in str(exc)

    check("SOCKS 明确拒绝", _raises(lambda: netproxy.normalize_proxy("socks5://127.0.0.1:1080"), "SOCKS"))
    check("未知协议明确拒绝", _raises(lambda: netproxy.normalize_proxy("ftp://x:1"), "http"))

    # ---------------- 2. 语音与 AI 共用同一份 override ----------------

    netproxy.set_override("http://shared:9")
    check("语音侧读到共用 override",
          speech.resolve_proxy() == "http://shared:9", speech.resolve_proxy())
    speech.set_proxy("http://from-speech:8")
    check("语音侧设置 = 写同一份 override（AI 立刻可见）",
          netproxy.resolve() == "http://from-speech:8", netproxy.resolve())
    netproxy.set_override(None)

    # ---------------- 3. AI 客户端真的注入了代理 ----------------

    import openai

    class _FakeHttpxClient:
        last = None

        def __init__(self, **kwargs):
            _FakeHttpxClient.last = kwargs

    class _FakeOpenAI:
        last = None

        def __init__(self, **kwargs):
            _FakeOpenAI.last = kwargs

    _orig_httpx, _orig_openai = openai.DefaultHttpxClient, openai.OpenAI
    openai.DefaultHttpxClient, openai.OpenAI = _FakeHttpxClient, _FakeOpenAI
    try:
        for _k in netproxy.ENV_KEYS:
            os.environ.pop(_k, None)
        netproxy.set_override(None)
        ai.create_client("openai", "sk-test", "https://api.example/v1", "m")
        check("没配代理：不注入 http_client",
              "http_client" not in _FakeOpenAI.last, _FakeOpenAI.last)

        netproxy.set_override("127.0.0.1:7890")
        ai.create_client("openai", "sk-test", "https://api.example/v1", "m")
        _hc = _FakeOpenAI.last.get("http_client")
        check("配了代理：注入 DefaultHttpxClient(proxy=整理后的地址)",
              isinstance(_hc, _FakeHttpxClient)
              and _FakeHttpxClient.last.get("proxy") == "http://127.0.0.1:7890",
              (_FakeOpenAI.last, _FakeHttpxClient.last))

        check("AI 客户端仍带 timeout",
              _FakeOpenAI.last.get("timeout") == ai.TIMEOUT_SECONDS,
              _FakeOpenAI.last.get("timeout"))

        # ---------------- 4/5. 失败原因分类 + test_connection ----------------

        netproxy.set_override(None)
        _d = ai.describe_ai_error

        class _Status(Exception):
            def __init__(self, code):
                super().__init__(f"Error code: {code} - boom")
                self.status_code = code

        check("401 → 提到 API Key",
              "API Key" in _d(_Status(401)), _d(_Status(401)))
        check("403 → 提到权限/地区",
              "403" in _d(_Status(403)), _d(_Status(403)))
        check("404 → 提到模型/URL",
              "模型" in _d(_Status(404)) and "URL" in _d(_Status(404)), _d(_Status(404)))
        check("429 → 提到额度",
              "429" in _d(_Status(429)), _d(_Status(429)))
        check("400 → 提到模型名/提示",
              "400" in _d(_Status(400)), _d(_Status(400)))
        check("500 → 提到服务端",
              "500" in _d(_Status(500)), _d(_Status(500)))
        check("超时 → 提到超时与代理",
              "超时" in _d(TimeoutError("Connection timeout")),
              _d(TimeoutError("Connection timeout")))

        class _Conn(Exception):
            pass

        check("连接失败 → 提到网络/代理",
              "网络" in _d(_Conn("Connection error.")) and "代理" in _d(_Conn("Connection error.")),
              _d(_Conn("Connection error.")))

        netproxy.set_override("http://127.0.0.1:15556")
        _msg_proxy = _d(_Conn("Connection error."))
        netproxy.set_override(None)
        check("配了代理时的连接失败 → 点名该代理并给出直连出路",
              "127.0.0.1:15556" in _msg_proxy and "直连" in _msg_proxy, _msg_proxy)
        check("SOCKS → 明确说改 HTTP",
              "SOCKS" in _d(ValueError("SOCKS 代理不受支持，请改填 HTTP 代理")),
              _d(ValueError("SOCKS 代理不受支持，请改填 HTTP 代理")))

        class _NoMod(ModuleNotFoundError):
            pass

        check("未装 openai → 提到 pip 安装",
              "openai" in _d(_NoMod("No module named 'openai'")),
              _d(_NoMod("No module named 'openai'")))
        check("配置类 ValueError 原文透出",
              _d(ValueError("未配置模型名称")) == "未配置模型名称",
              _d(ValueError("未配置模型名称")))

        # test_connection：代理未生效时，异常也要是可读原因
        class _Raiser:
            class _Create:
                def create(self, **kwargs):
                    raise _Status(401)

            def __init__(self):
                self.chat = type("Chat", (), {"completions": _Raiser._Create()})()

        _client = ai.create_client("openai", "sk-test", "https://api.example/v1", "m")
        _client.client = _Raiser()
        _ok, _msg = _client.test_connection()
        check("test_connection 失败：可读原因而非 SDK 原文",
              _ok is False and "API Key" in _msg, (_ok, _msg))
    finally:
        openai.DefaultHttpxClient, openai.OpenAI = _orig_httpx, _orig_openai
        netproxy.set_override(None)

    # ---------------- 6. settings 迁移 ----------------

    _tmp = tempfile.mkdtemp(prefix="ai_net_settings_")
    os.makedirs(os.path.join(_tmp, "user"), exist_ok=True)
    _sp = os.path.join(_tmp, "user", "settings.json")

    with open(_sp, "w", encoding="utf-8") as _f:
        json.dump({"voice_proxy": "http://old:1"}, _f)
    _loaded = SettingsRepo(data_dir=_tmp).load()
    check("旧键 voice_proxy → net_proxy（值搬过来）",
          _loaded.get("net_proxy") == "http://old:1", _loaded.get("net_proxy"))
    check("迁移后旧键不再残留",
          "voice_proxy" not in _loaded, sorted(_loaded)[:6])

    with open(_sp, "w", encoding="utf-8") as _f:
        json.dump({"net_proxy": "http://new:2", "voice_proxy": "http://old:1"}, _f)
    _loaded = SettingsRepo(data_dir=_tmp).load()
    check("新旧键都在：新键胜出",
          _loaded.get("net_proxy") == "http://new:2", _loaded.get("net_proxy"))

    with open(_sp, "w", encoding="utf-8") as _f:
        json.dump({"voice_proxy": ""}, _f)
    _loaded = SettingsRepo(data_dir=_tmp).load()
    check("旧键为空：新键取默认空串（不误迁移）",
          _loaded.get("net_proxy") == "", repr(_loaded.get("net_proxy")))

    # ---------------- 7. 接线守卫 ----------------
    check("settings 默认含 net_proxy",
          SettingsRepo()._defaults.get("net_proxy") == "",
          SettingsRepo()._defaults.get("net_proxy"))

    def _src(*parts):
        return open(os.path.join(_PROJECT_ROOT, *parts), encoding="utf-8").read()

    check("设置页连接测试用可读原因",
          "describe_ai_error" in _src("ui", "settings", "dialogs.py"))
    check("挂件连接测试用可读原因",
          "describe_ai_error" in _src("ui", "mini", "screens.py"))
    check("副本生成失败用可读原因",
          "describe_ai_error" in _src("dungeon", "window", "engine.py"))
    check("聊天 AI 失败用可读原因",
          "describe_ai_error" in _src("services", "chat", "__init__.py"))
finally:
    _log.flush()
    sys.stdout = sys.__stdout__
    sys.stderr = sys.__stderr__
    for _k, _v in _saved_env.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v

if failures:
    print(f"[check_ai_network] FAILED {len(failures)}/{total}: {failures}")
else:
    print(f"[check_ai_network] PASSED {total}/{total}")
print(f"[check_ai_network] report: {_report_path}")
sys.exit(1 if failures else 0)
