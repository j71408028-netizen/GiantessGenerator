"""话题视角预览：看某个存档在各个话题类型下，实际会给 AI 什么信息。

话题类型（坐标 / 注意力框架 / 体裁 / 开放字段 / 预算）定义在 ``services/chat/topic_kinds.py``，
字段目录与渲染在 ``services/chat/topic_lens.py``。改完任一处，用这个脚本对真实
存档"看一遍账"：每个类型渲染出哪几段、多少字、哪些字段被挡掉、为什么。

用法（路径相对于项目根目录）：
  python developer_tools/lens_preview.py                # 最近更新的角色 + 默认态度
  python developer_tools/lens_preview.py 角色ID
  python developer_tools/lens_preview.py 角色ID --attitude 60
  python developer_tools/lens_preview.py 角色ID --kind flirt --full
                                                   # 只渲染该类型，并打印整份系统提示词
  python developer_tools/lens_preview.py --list      # 仅列出可用角色
  python developer_tools/lens_preview.py --out a.txt # 另存到 _out/lens_preview/a.txt
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

from core.models import ChatState, CharacterSnapshot, Topic  # noqa: E402
from paths import data_dir  # noqa: E402
from services.chat import build_chat_system_prompt  # noqa: E402
from services.chat.coordinates import ChatCoord  # noqa: E402
from services.chat.topic_kinds import TOPIC_KINDS, lens_for  # noqa: E402
from services.chat.topic_lens import LensContext, render_lens  # noqa: E402


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
    import json
    path = os.path.join(_archives_dir(), giantess_id, "info.json")
    with open(path, "r", encoding="utf-8") as f:
        return CharacterSnapshot.from_dict(json.load(f))


def pick_default_id() -> str:
    ids = list_character_ids()
    if not ids:
        raise SystemExit("data/archives 下没有可用的角色快照。")
    return max(ids, key=lambda d: os.path.getmtime(
        os.path.join(_archives_dir(), d, "info.json")))


def build_context(state: CharacterSnapshot, kind_id: str,
                  attitude: int) -> LensContext:
    chat = ChatState(giantess_id=state.giantess_id, attitude=attitude)
    topic = None
    if kind_id:
        topic = Topic(title="预览话题", kind_id=kind_id, status="active",
                      turn_count=2)
        chat.topics.append(topic)
        chat.active_topic_id = topic.id
    return LensContext(state=state, chat_state=chat,
                       coord=ChatCoord(0.5, 0.5, 0.5),
                       world_stance="coexist", topic=topic), chat


def show(state: CharacterSnapshot, kind_id: str, attitude: int) -> None:
    ctx, _chat = build_context(state, kind_id, attitude)
    kind = lens_for(kind_id)
    text, trace = render_lens(kind, ctx)
    print("=" * 72)
    print(f"# {kind.label or '（无活跃话题）'}  <{kind.id}>")
    print(text)
    print("-" * 72)
    print(trace.summary())


def main() -> int:
    parser = argparse.ArgumentParser(description="话题视角（存档→提示词）预览")
    parser.add_argument("char", nargs="?", default=None, help="角色 ID（存档目录名）")
    parser.add_argument("--attitude", type=int, default=0, help="聊天态度 -100~100")
    parser.add_argument("--kind", default=None, help="只渲染指定话题类型")
    parser.add_argument("--full", action="store_true",
                        help="打印该类型下的整份系统提示词")
    parser.add_argument("--list", action="store_true", help="仅列出可用角色")
    parser.add_argument("--out", default=None,
                        help="把输出写入该文件（UTF-8），便于在编辑器里对照阅读")
    args = parser.parse_args()

    if args.out:
        import contextlib
        import io
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = _run(args)
        target = Path(args.out)
        if not target.is_absolute():
            target = Path(__file__).resolve().parent / "_out" / "lens_preview" / target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(buffer.getvalue(), encoding="utf-8")
        print(f"已写入 {target}")
        return code
    return _run(args)


def _run(args) -> int:

    if args.list:
        for cid in list_character_ids():
            print(cid)
        return 0

    char_id = args.char or pick_default_id()
    state = load_snapshot(char_id)
    print(f"角色：{state.name}（{char_id}），态度 {args.attitude}，"
          f"身高 {state.height:g}，隐藏介绍 {'有' if state.intro_hidden else '无'}")
    if args.full:
        _, chat = build_context(state, args.kind or "", args.attitude)
        print(build_chat_system_prompt(state, chat))
        return 0
    if args.kind:
        show(state, args.kind, args.attitude)
        return 0
    show(state, "", args.attitude)
    for kind in TOPIC_KINDS:
        show(state, kind.id, args.attitude)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
