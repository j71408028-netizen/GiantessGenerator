# -*- coding: utf-8 -*-
"""内置小游戏窗口的子进程宿主。

pywebview（Windows 上走系统自带的 WebView2）强制 ``webview.start()`` 必须跑在
**主线程**上，而主程序的主线程属于 Tk / 副本 DPG 帧循环——所以小游戏窗口放进
独立子进程，pywebview 独占子进程的主线程：

- 源码运行：``python ui/common/mini_game_host.py <session.html> <目标关数> <结果.json>``
- 打包运行：``<App>.exe --mini-game-host <session.html> <目标关数> <结果.json>``
  （由 ``main.main`` 的前置分支路由到 :func:`run`）

页面里的 ``bridge.js`` 在胜负结算时调用 ``js_api.report(json)``；本进程把结果
原样写到结果文件，随后关窗退出（``webview.start()`` 返回、进程结束）。用户直接
关掉游戏窗口 = 不写文件 = 父进程按「无结果」处理，不执行胜负分支。

``--auto-result <json>`` 是冒烟钩子：窗口就绪后自动调用一次 ``report``，用于
无人值守回归；正常玩法不会用到。
"""

import json
import sys
from pathlib import Path

_WINDOW_W = 1104
_WINDOW_H = 856


def _write_result(out_path, result):
    try:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False)
    except OSError:
        pass


def run(argv):
    """子进程入口。``argv`` 去掉 ``--mini-game-host`` 标记后为
    ``<session.html> <目标关数> <结果.json> [--auto-result <json>]``。"""
    args = [a for a in argv[1:] if a != "--mini-game-host"]
    html_path, target, out_path = args[0], args[1], args[2]
    auto = None
    if len(args) >= 5 and args[3] == "--auto-result":
        try:
            auto = json.loads(args[4])
        except ValueError:
            auto = None

    import webview

    state = {"window": None, "reported": False}

    class BridgeApi:
        """页面桥接：bridge.js 在结算时调 ``report(payload)`` 回传 JSON 结果。"""

        def report(self, payload):
            if state["reported"]:
                return
            state["reported"] = True
            try:
                result = json.loads(payload)
            except (TypeError, ValueError):
                result = {}
            _write_result(out_path, result)
            window = state["window"]
            if window is not None:
                try:
                    window.destroy()
                except Exception:
                    pass

    # 目标关数走 URL 片段：WebView2 对带查询参数的 file:// URL 会导航失败
    # （chrome-error 页），片段则完全在客户端解析，不受影响
    url = Path(html_path).absolute().as_uri() + f"#target={target}"
    window = webview.create_window(
        "小游戏", url=url, js_api=BridgeApi(),
        width=_WINDOW_W, height=_WINDOW_H, resizable=False)
    state["window"] = window

    def _auto():
        if auto is None:
            return
        import time
        for _ in range(60):
            try:
                ready = window.evaluate_js(
                    "!!(window.pywebview && window.pywebview.api"
                    " && window.pywebview.api.report)")
            except Exception:
                ready = False
            if ready:
                break
            time.sleep(0.5)
        payload = json.dumps(auto)
        # IPC 通道就绪初期仍可能抛 postMessage undefined，重试几次
        for _ in range(10):
            try:
                window.evaluate_js(
                    "window.pywebview.api.report(JSON.stringify(%s))" % payload)
                return
            except Exception:
                time.sleep(1.0)

    webview.start(func=_auto)


if __name__ == "__main__":
    run(sys.argv)
