"""角色档案导出调试预览：改完 archive_export.py 后刷新浏览器即可看到新渲染。

启动一个小型本地服务器，每次收到请求都重新加载 services/archive_export.py
并渲染指定角色的快照，因此迭代样式时改完代码按 F5 即可，无需重启脚本。

用法（在项目根目录）：
  python debug_archive_preview.py                 # 默认展示最近更新的角色
  python debug_archive_preview.py 角色ID          # 展示指定角色
  python debug_archive_preview.py --list          # 仅列出可用角色后退出
  python debug_archive_preview.py --port 9000     # 换端口（默认 8940）
  python debug_archive_preview.py --no-browser    # 启动时不自动打开浏览器
  python debug_archive_preview.py --hide-casualties
                                                  # 按“不含伤亡”方式渲染

启动后在浏览器访问 http://127.0.0.1:8940/ ：
  · F5 刷新 = 重新加载 archive_export.py 并渲染当前角色；
  · /chars 页可切换角色（或用 ?char=角色ID 直接跳转）。
"""

import argparse
import datetime
import importlib
import json
import os
import tempfile
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote

from models import CharacterSnapshot
from paths import data_dir

DEFAULT_PORT = 8940


# -----------------------------------------------------------------
# 快照加载（不经 CharacterRepo，避免拖入 GUI 依赖）
# -----------------------------------------------------------------

def _archives_dir() -> str:
    return os.path.join(data_dir(), "archives")


def list_character_ids() -> list:
    root = _archives_dir()
    if not os.path.isdir(root):
        return []
    return sorted(
        d for d in os.listdir(root)
        if os.path.isdir(os.path.join(root, d))
        and os.path.exists(os.path.join(root, d, "info.json")))


def load_snapshot(giantess_id: str) -> CharacterSnapshot:
    path = os.path.join(_archives_dir(), giantess_id, "info.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return CharacterSnapshot.from_dict(data)


def pick_default_id() -> str:
    """默认取最近更新的角色快照。"""
    ids = list_character_ids()
    if not ids:
        raise SystemExit("data/archives 下没有可用的角色快照。")
    return max(ids, key=lambda d: os.path.getmtime(
        os.path.join(_archives_dir(), d, "info.json")))


# -----------------------------------------------------------------
# 渲染：每次都重新加载 archive_export，保证刷新即最新代码
# -----------------------------------------------------------------

def render_html(giantess_id: str, show_casualties: bool = True) -> bytes:
    import services.character_service.archive_export as archive_export
    importlib.reload(archive_export)
    state = load_snapshot(giantess_id)
    fd, tmp_path = tempfile.mkstemp(suffix=".html")
    os.close(fd)
    try:
        archive_export.export_character_mhtml(
            state, tmp_path, show_casualties=show_casualties)
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


# -----------------------------------------------------------------
# HTTP 服务
# -----------------------------------------------------------------

class PreviewHandler(BaseHTTPRequestHandler):
    current_id = pick_default_id()

    def log_message(self, fmt, *args):  # 安静一些
        pass

    def _send(self, status: int, body: bytes, ctype: str):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        qs = parse_qs(url.query)

        if url.path == "/chars":
            self._send_chars_page()
            return

        if url.path == "/":
            req_id = (qs.get("char") or [None])[0]
            if req_id and req_id in list_character_ids():
                PreviewHandler.current_id = req_id
            gid = PreviewHandler.current_id
            try:
                body = render_html(gid)
            except Exception as e:
                body = (f"<h1>渲染失败</h1><pre>{type(e).__name__}: {e}</pre>"
                        f"<p>修正 archive_export.py 后刷新本页。</p>"
                        f"<p><a href='/chars'>选择其他角色</a></p>").encode("utf-8")
                self._send(500, body, "text/html; charset=utf-8")
                return
            self._send(200, body, "text/html; charset=utf-8")
            return

        self._send(404, "not found".encode(), "text/plain; charset=utf-8")

    def _send_chars_page(self):
        ids = list_character_ids()
        rows = []
        for d in ids:
            try:
                mtime = datetime.datetime.fromtimestamp(os.path.getmtime(
                    os.path.join(_archives_dir(), d, "info.json")))
                mtime_str = mtime.strftime("%Y-%m-%d %H:%M")
            except OSError:
                mtime_str = "-"
            cur = "（当前）" if d == PreviewHandler.current_id else ""
            rows.append(
                f"<li><a href='/?char={quote(d)}'>{d}</a> {cur}"
                f"<span class='m'>更新于 {mtime_str}</span></li>")
        html_text = (
            "<meta charset='utf-8'><title>选择角色 - 档案调试预览</title>"
            "<style>body{font-family:sans-serif;max-width:640px;margin:40px auto;"
            "padding:0 16px;line-height:1.8}a{color:#1976D2;text-decoration:none}"
            "a:hover{text-decoration:underline}.m{color:#999;font-size:13px;"
            "margin-left:8px}</style>"
            "<h2>角色档案调试预览</h2><ul>" + "".join(rows) + "</ul>"
            "<p class='m'>每次刷新都会用当前 services/archive_export.py 重新渲染。</p>")
        self._send(200, html_text.encode("utf-8"), "text/html; charset=utf-8")


def main():
    parser = argparse.ArgumentParser(description="角色档案导出调试预览")
    parser.add_argument("char_id", nargs="?", default=None, help="角色快照目录名（默认取最近更新）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--list", action="store_true", help="仅列出可用角色")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--hide-casualties", action="store_true",
                        help="按导出对话框去掉伤亡行的方式渲染")
    args = parser.parse_args()

    ids = list_character_ids()
    if args.list:
        for d in ids:
            print(d)
        return
    if not ids:
        raise SystemExit("data/archives 下没有可用的角色快照。")

    if args.char_id:
        if args.char_id not in ids:
            raise SystemExit(f"找不到角色「{args.char_id}」。可用：\n  " + "\n  ".join(ids))
        PreviewHandler.current_id = args.char_id

    # 预渲染一次：把导入错误等提前暴露在启动阶段
    render_html(PreviewHandler.current_id, show_casualties=not args.hide_casualties)

    server = ThreadingHTTPServer(("127.0.0.1", args.port), PreviewHandler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"[档案调试预览] 当前角色：{PreviewHandler.current_id}")
    print(f"[档案调试预览] 请访问 {url} （F5 刷新即用最新 archive_export.py 重新渲染）")
    print(f"[档案调试预览] 角色列表：{url}chars")
    print("[档案调试预览] Ctrl+C 退出")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[档案调试预览] 已退出")


if __name__ == "__main__":
    main()
