"""聊天会话预览：在控制台里跟角色实际对话，同时逐轮看"存档 → 提示词"的账本。

给改聊天提示词、话题视角（``services/chat/topic_lens.py``）或话题状态机的人用：
一边像玩家一样发消息，一边看到这一轮 AI 到底拿到了哪些存档字段、哪些被视角
挡掉、话题怎么变、属性怎么动、原始 JSON 长什么样。

会话在沙盒里跑，不碰 ``data/``：首次运行把角色档案复制到
``developer_tools/_out/chat_session_preview/sandbox/<角色ID>/``，聊天记录也写
在那里，下次运行可以接着聊（``--fake`` 用独立的 ``sandbox_fake/``，假回复不会
混进真会话）。

用法（路径相对于项目根目录）：
  python developer_tools/chat_session_preview.py                # 最近更新的角色
  python developer_tools/chat_session_preview.py 角色ID
  python developer_tools/chat_session_preview.py 角色ID --attitude 55 --stance alert
  python developer_tools/chat_session_preview.py 角色ID --topic flirt
  python developer_tools/chat_session_preview.py 角色ID --fake --script 对话.txt
  python developer_tools/chat_session_preview.py 角色ID --log   # 会话记录另存 _out/

进入会话后直接输入消息回车发送；``/help`` 看命令（``/lens`` 看本轮视角账本、
``/prompt`` 看整份系统提示词、``/state`` 看三轴坐标与话题、``/topic`` 手动指定
话题类型以单测某一套视角、``/attitude`` 与 ``/stance`` 拨动坐标、``/reroll`` 重掷
上一轮）。``/exit`` 或 Ctrl+C 退出。

需要真实的 AI 配置（``data/user/api_keys.json``）才会有回复；``--fake`` 用固定
假回复离线验证工具本身。日志里的字段名与 ``topic_lens.ARCHIVE_FIELDS`` 一一对应。
"""

from __future__ import annotations

import argparse
import copy
import datetime
import json
import os
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import services.chat as chat_mod  # noqa: E402
import services.chat.delivery as delivery_mod  # noqa: E402
from core.models import (DEFAULT_IDENTITY_ID, ChatState, Topic)  # noqa: E402
from paths import data_dir as real_data_dir  # noqa: E402
from services.chat import (ChatService, active_topic, chat_coord,  # noqa: E402
                           topic_candidates)
from services.chat.coordinates import (WORLD_STANCE_VALUES,  # noqa: E402
                                       describe_affection,
                                       describe_size_state,
                                       describe_tension)
from services.chat.topic_kinds import (active_kinds,  # noqa: E402
                                       find_topic_kind)
from services.chat.topic_lens import HIDDEN_INTRO_ATTITUDE  # noqa: E402

OUT_ROOT = Path(__file__).resolve().parent / "_out" / "chat_session_preview"
RULE_WIDTH = 56

# 视角字段被挡掉的原因码 → 人话（与 topic_lens.collect_fields 一致）
DROP_REASONS = {
    "unregistered": "字段未注册",
    "identity-only": "属于身份层",
    "denied": "视角未开放",
    "gated": "好感不足",
    "empty": "存档无内容",
    "group-cap": "同组已满",
    "budget": "超出预算",
}


# ---------- 沙盒 ----------

def _archives_dir(base: Path) -> Path:
    return base / "archives"


def list_character_ids(base: Path) -> list:
    root = _archives_dir(base)
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir()
                  if d.is_dir() and (d / "info.json").exists())


def pick_default_id(base: Path) -> str:
    ids = list_character_ids(base)
    if not ids:
        raise SystemExit(f"{_archives_dir(base)} 下没有可用角色。")
    return max(ids, key=lambda name: (
        _archives_dir(base) / name / "info.json").stat().st_mtime)


def _discard(path: Path) -> None:
    """清理产物：挪进系统临时目录，不就地删除（仓库约定）。"""
    if path.exists():
        shutil.move(str(path), os.path.join(tempfile.mkdtemp(), path.name))


def prepare_sandbox(char_id: str, source: Path, reset: bool = False,
                    sync: bool = False, variant: str = "sandbox") -> Path:
    """把角色档案复制到 ``_out`` 沙盒，返回可作为 data_dir 的沙盒根。

    ``--fake`` 用独立的 ``sandbox_fake/``：假回复不会混进真会话的历史里。
    """
    root = OUT_ROOT / variant / char_id
    if reset:
        _discard(root)
    target = _archives_dir(root) / char_id / "info.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    origin = _archives_dir(source) / char_id / "info.json"
    if not origin.exists():
        raise SystemExit(f"找不到角色档案：{origin}")
    if reset or sync or not target.exists():
        shutil.copy2(origin, target)
    return root


# ---------- 记录 AI 请求 ----------

def _request_kind(messages: list) -> str:
    """区分一次性聊天参数请求与正式对话请求。"""
    for message in messages:
        content = message.get("content") or ""
        if "在线强度" in content and "warmup" in content:
            return "参数"
    return "聊天"


class RecordingClient:
    """包住真实 AI 客户端，记下每次请求的消息、耗时与原始输出。"""

    def __init__(self, inner, session: "Session"):
        self._inner = inner
        self._session = session
        self.provider = getattr(inner, "provider", "")
        self.model = getattr(inner, "model", "")

    def generate_once(self, messages, temperature=0.8, max_tokens=None):
        started = time.perf_counter()
        raw, error = "", ""
        try:
            raw = self._inner.generate_once(
                messages, temperature=temperature, max_tokens=max_tokens)
            return raw
        except Exception as exc:  # 原样抛出，由服务层翻译成错误文案
            from core.ai import describe_ai_error
            error = describe_ai_error(exc)
            raise
        finally:
            self._session.record_request(
                _request_kind(messages), messages, temperature, raw, error,
                time.perf_counter() - started)

    def generate(self, messages, temperature=0.8):
        return self._inner.generate(messages, temperature=temperature)

    def generate_stream(self, messages, temperature=0.8):
        return self._inner.generate_stream(messages, temperature=temperature)

    def test_connection(self):
        return self._inner.test_connection()


class FakeClient:
    """离线假客户端：只为验证工具与流程，不联网。"""

    provider = "fake"
    model = "fake"

    def generate_once(self, messages, temperature=0.8, max_tokens=None):
        joined = "\n".join(m.get("content") or "" for m in messages)
        if "在线强度" in joined and "warmup" in joined:
            return '{"online": 1, "warmup": 1, "boundary": 1}'
        system = messages[0].get("content") or ""
        pending = next((m.get("content") or "" for m in reversed(messages)
                        if m.get("role") == "user"), "")
        candidates = re.findall(r"^  - (\w+)（", system, re.M)
        topic = {"action": "continue"}
        # 只看【当前话题】段落里的行，别误匹配输出协议里的说明文字
        if "- 进行中的话题：" not in system and candidates:
            topic = {"action": "start", "kind": candidates[0],
                     "title": "（假）随便聊聊"}
        return json.dumps({
            "topic": topic,
            "messages": [f"[假] 收到：{pending[:24]}"],
            "ops": {"attitude": 1},
        }, ensure_ascii=False)


class _NoopScheduler:
    """控制台里不需要真实投递节奏：消息就地标记已读，免去后台线程。"""

    def notify_queued(self, *args, **kwargs):
        return None


# ---------- 单轮记录 ----------

@dataclass
class TurnLog:
    index: int
    text: str
    tag: str = ""                   # 例如「重掷」
    started: float = 0.0
    started_at: datetime.datetime = field(default_factory=datetime.datetime.now)
    elapsed: float = 0.0
    lens_text: str = ""
    lens_trace: object = None
    system_prompt: str = ""
    requests: list = field(default_factory=list)
    parsed: dict = field(default_factory=dict)
    result: dict = field(default_factory=dict)
    before: dict = field(default_factory=dict)
    after: dict = field(default_factory=dict)
    before_chat: object = None      # 发送前的 ChatState 深拷贝（供 /reroll 回滚）
    before_state: object = None     # 发送前的角色快照深拷贝
    error: str = ""


def _clip(text: str, limit: int = 200) -> str:
    text = text.replace("\n", " ").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _display_width(text: str) -> int:
    """控制台近似宽度：CJK 字符按两格算，用于对齐分隔线。"""
    return sum(2 if ord(char) > 0x2E80 else 1 for char in text)


def _join(items: list, limit: int = 6) -> str:
    if not items:
        return "无"
    shown = "、".join(items[:limit])
    return shown + (f" 等 {len(items)} 项" if len(items) > limit else "")


class Session:
    """一轮对话的编排与打印；提示词钩子把账面写进当前轮。"""

    def __init__(self, service: ChatService, state, chat_state: ChatState,
                 char_id: str, stance: str):
        self.service = service
        self.state = state
        self.chat_state = chat_state
        self.char_id = char_id
        self.stance = stance
        self.turns: list = []
        self.current: TurnLog = None
        self.verbose = False
        self.client = None
        self.client_name = ""
        self.transcript = None

    # —— 钩子回调 ——
    def record_request(self, kind, messages, temperature, raw, error, elapsed):
        if self.current is None:
            return
        self.current.requests.append({
            "kind": kind, "messages": messages, "temperature": temperature,
            "raw": raw, "error": error, "elapsed": elapsed,
        })

    def note_lens(self, text, trace):
        if self.current is not None:
            self.current.lens_text = text
            self.current.lens_trace = trace

    def note_parsed(self, parsed):
        if self.current is not None and isinstance(parsed, dict):
            self.current.parsed = parsed

    def note_messages(self, messages):
        if self.current is None:
            return
        if messages and messages[0].get("role") == "system":
            self.current.system_prompt = messages[0].get("content") or ""

    # —— 读数 ——
    def coord(self):
        return chat_coord(self.state, self.chat_state,
                          world_stance=self.stance)

    def snapshot(self) -> dict:
        topic = active_topic(self.chat_state)
        return {
            "attitude": self.chat_state.attitude,
            "action_points": self.state.action_points,
            "topic": ((topic.id, topic.title, topic.kind_id, topic.turn_count)
                      if topic is not None else None),
            "topics": len(self.chat_state.topics or []),
            "messages": len(self.chat_state.messages or []),
        }

    @staticmethod
    def kind_label(kind_id: str) -> str:
        kind = find_topic_kind(kind_id)
        return f"{kind.label}/{kind.id}" if kind else f"未知/{kind_id or '无'}"

    # —— 一轮对话 ——
    def send(self, text: str, tag: str = ""):
        turn = TurnLog(index=len(self.turns) + 1, text=text, tag=tag,
                       before=self.snapshot(),
                       before_chat=copy.deepcopy(self.chat_state),
                       before_state=copy.deepcopy(self.state))
        turn.started = time.perf_counter()
        self.current = turn
        self.turns.append(turn)
        try:
            result = self.service.send_message(self.state, self.chat_state, text)
        except Exception as exc:  # 服务层之外的意外（存档/装配错误）不终止会话
            result = {}
            turn.error = f"{type(exc).__name__}: {exc}"
        finally:
            turn.elapsed = time.perf_counter() - turn.started
            self.current = None
        turn.result = result or {}
        turn.after = self.snapshot()
        turn.error = turn.error or turn.result.get("error") or ""
        replies = turn.result.get("reply_messages") or []
        self._deliver_now(replies)
        self.print_turn(turn)
        self.log_turn(turn)
        return turn

    def _deliver_now(self, replies: list):
        """把 queued 直接标成已读：控制台不需要等打字延迟。"""
        if not replies:
            return
        now = datetime.datetime.now().isoformat()
        for message in replies:
            message.status = "read"
            message.delivered_at = now
            message.read_at = now
        self.service.chat_repo.save(self.chat_state)

    # —— 打印 ——
    def print_turn(self, turn: TurnLog):
        print()
        title = f"── 第 {turn.index} 轮" + (f"（{turn.tag}）" if turn.tag else "")
        print(f"{title} " + "─" * max(6, RULE_WIDTH - _display_width(title)))
        print(f"你｜{_clip(turn.text, 300)}")
        if turn.error:
            print(f"错误｜{turn.error}")
        for request in turn.requests:
            size = sum(len(m.get("content") or "") for m in request["messages"])
            history = sum(1 for m in request["messages"]
                          if m.get("role") in ("user", "assistant")) - 1
            detail = (f" · {len(request['messages'])} 条消息 / {size} 字"
                      f" · 历史 {max(0, history)} 条 · temp {request['temperature']:g}"
                      if request["kind"] == "聊天" else f" · {size} 字")
            print(f"请求｜{request['kind']} {request['elapsed']:.2f}s{detail}")
            if request["error"]:
                print(f"      ✗ {request['error']}")
        print(f"话题｜{self._topic_line(turn)}")
        self._print_lens(turn)
        self._print_state(turn)
        self._print_effect(turn)
        self._print_reply(turn)
        if self.verbose:
            for request in turn.requests:
                raw = (request.get("raw") or "").strip()
                if raw:
                    print("原始｜" + _clip(raw, 600))

    def _topic_line(self, turn: TurnLog) -> str:
        before, after = turn.before.get("topic"), turn.after.get("topic")
        decision = (turn.parsed or {}).get("topic") or {}
        action = decision.get("action") or "continue"
        chosen = decision.get("kind") or ""
        if after is None:
            if before is None:
                return "无（仍未开启）"
            return f"结束「{before[1]}」"
        title = after[1] or "（无标题）"
        base = (f"开启「{title}」（{self.kind_label(after[2])}）"
                if before is None or before[0] != after[0]
                else f"延续「{title}」（{self.kind_label(after[2])}，{after[3]} 轮）")
        if action in ("start", "switch") and chosen and chosen != after[2]:
            return base + f"；AI 选 {chosen} 未采纳，回退到 {after[2]}"
        if action in ("start", "switch") and chosen:
            return base + f"；AI 选 {chosen}"
        if before is not None and before[0] != after[0]:
            return base + "；AI 未指定类型，按坐标回退"
        return base

    def _print_lens(self, turn: TurnLog):
        trace = turn.lens_trace
        if trace is None:
            print("视角｜（本轮没有渲染视角）")
            return
        kept = _join([f"{fid}({weight:.1f})"
                      for fid, weight, _ in trace.included])
        dropped = _join([f"{fid}:{DROP_REASONS.get(why, why)}"
                         for fid, why in trace.dropped], limit=8)
        print(f"视角｜{trace.lens_id} · 字段 {trace.field_chars}/{trace.budget} 字"
              f"（整段 {trace.chars}）")
        print(f"     取用 {kept}")
        print(f"     挡掉 {dropped}")
        after_topic = turn.after.get("topic")
        if after_topic is not None and trace.lens_id != after_topic[2]:
            print(f"     · 视角按发送前的话题渲染（{trace.lens_id}），"
                  f"新话题 {after_topic[2]} 从下一轮起生效")
        if (turn.system_prompt and turn.lens_text
                and turn.lens_text.strip() not in turn.system_prompt):
            print("     ⚠ 视角渲染与系统提示词不一致，检查是否有第二处拼装")

    def _print_state(self, turn: TurnLog):
        before, after = turn.before, turn.after
        coord = self.coord()
        print(f"状态｜态度 {before['attitude']} → {after['attitude']} ｜ "
              f"行动点 {before['action_points']} → {after['action_points']}")
        print(f"     坐标 好感 {coord.affection:.2f} / 紧张 {coord.tension:.2f} / "
              f"状态 {coord.size_state:.2f}")
        if "user_nick" in (turn.result.get("ops_applied") or {}):
            print(f"     改称「{turn.result['ops_applied']['user_nick']}」")

    def _print_effect(self, turn: TurnLog):
        applied = turn.result.get("effect_applied")
        intended = (turn.parsed or {}).get("effect") or {}
        if applied:
            print(f"效果｜{applied['tool']} → {applied['result']}")
        elif intended:
            print(f"效果｜{intended.get('tool')} 未生效（参数非法或被忽略，"
                  f"审计见 /state）")
        else:
            print("效果｜无")

    def _print_reply(self, turn: TurnLog):
        replies = turn.result.get("reply_messages") or []
        if turn.error:
            return
        if not replies:
            why = "API 拒绝" if turn.result.get("refused") else "已读不回"
            reason = (turn.parsed or {}).get("silent_reason") or ""
            print(f"回复｜{why}" + (f"：{reason}" if reason else ""))
            return
        offsets = []
        for message in replies:
            try:
                moment = datetime.datetime.fromisoformat(message.available_at)
                offsets.append(max(0.0, (moment - turn.started_at).total_seconds()))
            except (TypeError, ValueError):
                continue
        planned = ("（按投递节奏应 "
                   + " / ".join(f"{value:.1f}s" for value in offsets)
                   + " 送达，控制台直接标记已读）") if offsets else ""
        print(f"回复｜{len(replies)} 条{planned}")
        for index, message in enumerate(replies, 1):
            print(f"  {index}│ {message.text}")

    # —— 收尾汇总 ——
    def summary(self) -> str:
        turns = self.turns
        if not turns:
            return "本次没有对话。"
        chats = [req for turn in turns for req in turn.requests
                 if req["kind"] == "聊天"]
        elapsed = sum(req["elapsed"] for req in chats)
        sizes = [sum(len(m.get("content") or "") for m in req["messages"])
                 for req in chats]
        replies = sum(len(turn.result.get("reply_messages") or [])
                      for turn in turns)
        silent = sum(1 for turn in turns
                     if not (turn.result.get("reply_messages") or [])
                     and not turn.error)
        refused = sum(1 for turn in turns if turn.result.get("refused"))
        errors = sum(1 for turn in turns if turn.error)
        first, last = turns[0].before["attitude"], turns[-1].after["attitude"]
        average_time = elapsed / len(chats) if chats else 0.0
        average_size = sum(sizes) / len(sizes) if sizes else 0
        parts = [f"本次 {len(turns)} 轮 · 聊天请求 {len(chats)} 次 · "
                 f"平均 {average_time:.2f}s · 提示词平均 {average_size:.0f} 字",
                 f"回复 {replies} 条 ｜ 已读不回 {silent} 次 ｜ "
                 f"API 拒绝 {refused} 次 ｜ 失败 {errors} 次 ｜ "
                 f"态度 {first} → {last}"]
        return "\n".join(parts)

    # —— 记录到文件 ——
    def log_turn(self, turn: TurnLog):
        if self.transcript is None:
            return
        lines = [f"## 第 {turn.index} 轮"
                 + (f"（{turn.tag}）" if turn.tag else ""), "",
                 f"你：{turn.text}", ""]
        for request in turn.requests:
            lines.append(f"- 请求 {request['kind']} {request['elapsed']:.2f}s"
                         f"（temp {request['temperature']:g}）")
            if request["error"]:
                lines.append(f"  - 失败：{request['error']}")
        trace = turn.lens_trace
        if trace is not None:
            lines.append(f"- 视角 {trace.summary()}")
        lines.append(f"- 话题：{self._topic_line(turn)}")
        lines.append(f"- 状态：态度 {turn.before['attitude']} → "
                     f"{turn.after['attitude']}，行动点 "
                     f"{turn.before['action_points']} → "
                     f"{turn.after['action_points']}")
        replies = turn.result.get("reply_messages") or []
        if replies:
            lines.extend(["", "她：", ""] + [f"- {m.text}" for m in replies])
        elif turn.error:
            lines.extend(["", f"（失败：{turn.error}）"])
        else:
            lines.extend(["", "（已读不回）"])
        if self.verbose:
            for request in turn.requests:
                raw = (request.get("raw") or "").strip()
                if raw:
                    lines.extend(["", "```json", raw, "```"])
        if self.verbose and turn.system_prompt:
            lines.extend(["", "<details><summary>系统提示词</summary>", "",
                          "```text", turn.system_prompt, "```", "",
                          "</details>"])
        self.transcript.write("\n".join(lines) + "\n\n")


class Transcript:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._handle = open(path, "a", encoding="utf-8", buffering=1)

    def header(self, text: str):
        self._handle.write(text + "\n\n")

    def write(self, text: str):
        self._handle.write(text)

    def close(self):
        try:
            self._handle.close()
        except Exception:
            pass


# ---------- 钩子 ----------

def install_hooks(session: Session):
    """把视角渲染、消息组装、协议解析与 AI 客户端接到会话记录上；返回恢复函数。"""
    original = {
        "render": chat_mod.render_topic_lens,
        "build": chat_mod.build_chat_messages,
        "parse": chat_mod.parse_chat_response,
        "client": chat_mod.get_ai_client,
        "scheduler": delivery_mod.get_scheduler,
    }

    def render_spy(ctx):
        text, trace = original["render"](ctx)
        session.note_lens(text, trace)
        return text, trace

    def build_spy(*args, **kwargs):
        messages = original["build"](*args, **kwargs)
        session.note_messages(messages)
        return messages

    def parse_spy(text):
        parsed = original["parse"](text)
        session.note_parsed(parsed)
        return parsed

    chat_mod.render_topic_lens = render_spy
    chat_mod.build_chat_messages = build_spy
    chat_mod.parse_chat_response = parse_spy
    chat_mod.get_ai_client = lambda settings=None: (session.client,
                                                    session.client_name)
    delivery_mod.get_scheduler = lambda *args, **kwargs: _NoopScheduler()

    def restore():
        chat_mod.render_topic_lens = original["render"]
        chat_mod.build_chat_messages = original["build"]
        chat_mod.parse_chat_response = original["parse"]
        chat_mod.get_ai_client = original["client"]
        delivery_mod.get_scheduler = original["scheduler"]

    return restore


# ---------- 命令 ----------

HELP_TEXT = """\
直接输入消息回车发送。命令：
  /help              这张表
  /lens              上一轮的视角账本（取用/挡掉、预算）
  /prompt [轮次]     整份系统提示词（默认最后一轮；还没发过消息时用当前状态现拼）
  /json [轮次]       该轮 AI 的原始输出（含被拒绝的非 JSON 文本）
  /state             态度、行动点、三轴坐标、聊天参数、话题、记忆
  /kinds             全部话题类型与各自的关注点、本轮候选
  /topic KIND|off    手动指定/关闭活跃话题（单独验证某套视角）
  /attitude N        直接改聊天态度（验证隐藏介绍的好感门槛）
  /stance NAME       改世界态势（worship/accept/coexist/alert/hostile）
  /nick 名字         改她对你的称呼
  /again             把上一条重发一遍
  /reroll            从历史里撤掉上一轮并重发（对比两次输出）
  /verbose           切换是否打印原始 JSON
  /exit              退出（Ctrl+C / Ctrl+D 同）"""


class Commands:
    """斜杠命令；返回值 False 表示退出。"""

    def __init__(self, session: Session):
        self.session = session

    def handle(self, line: str) -> bool:
        name, _, rest = line[1:].partition(" ")
        rest = rest.strip()
        handler = {
            "help": self.help, "h": self.help, "?": self.help,
            "lens": self.lens, "prompt": self.prompt, "json": self.json,
            "state": self.state, "kinds": self.kinds, "topic": self.topic,
            "attitude": self.attitude, "stance": self.stance,
            "nick": self.nick, "again": self.again,
            "reroll": self.reroll, "verbose": self.verbose,
            "exit": self.exit_cmd, "quit": self.exit_cmd, "q": self.exit_cmd,
        }.get(name.lower())
        if handler is None:
            print(f"未知命令 /{name}，/help 看列表。")
            return True
        return handler(rest)

    # —— 各命令 ——
    def help(self, _rest: str) -> bool:
        print(HELP_TEXT)
        return True

    def _turn(self, rest: str) -> TurnLog:
        turns = self.session.turns
        if rest.isdigit():
            index = int(rest)
            if 1 <= index <= len(turns):
                return turns[index - 1]
            if turns:
                print(f"没有第 {index} 轮，改用第 {len(turns)} 轮。")
                return turns[-1]
            return None
        return turns[-1] if turns else None

    def lens(self, rest: str) -> bool:
        turn = self._turn(rest)
        if turn is not None and turn.lens_text:
            print(turn.lens_text)
            print("-" * 60)
            self.session._print_lens(turn)
            return True
        text, trace = self._render_now()
        print(text)
        print("-" * 60)
        print(trace.summary())
        return True

    def _render_now(self):
        from services.chat.topic_lens import LensContext, render_topic_lens
        session = self.session
        ctx = LensContext(state=session.state, chat_state=session.chat_state,
                          coord=session.coord(), world_stance=session.stance,
                          topic=active_topic(session.chat_state))
        return render_topic_lens(ctx)

    def prompt(self, rest: str) -> bool:
        turn = self._turn(rest)
        if turn is not None and turn.system_prompt:
            print(turn.system_prompt)
            return True
        from services.chat import build_chat_system_prompt
        print(build_chat_system_prompt(self.session.state,
                                       self.session.chat_state,
                                       world_stance=self.session.stance))
        return True

    def json(self, rest: str) -> bool:
        turn = self._turn(rest)
        if turn is None or not turn.requests:
            print("还没有请求记录。")
            return True
        for request in turn.requests:
            print(f"—— {request['kind']}（{request['elapsed']:.2f}s）")
            raw = (request.get("raw") or "").strip()
            if request["error"]:
                print(f"失败：{request['error']}")
            elif not raw:
                print("（空输出）")
            else:
                try:
                    print(json.dumps(json.loads(raw), ensure_ascii=False,
                                     indent=2))
                except (ValueError, TypeError):
                    print(raw)
        print("—— 解析后的协议")
        print(json.dumps(turn.parsed or {}, ensure_ascii=False, indent=2))
        return True

    def state(self, _rest: str) -> bool:
        session = self.session
        state, chat_state = session.state, session.chat_state
        coord = session.coord()
        params = chat_state.chat_params
        topic = active_topic(chat_state)
        print(f"角色：{state.name}（{session.char_id}） 身高 {state.height:g} "
              f"｜世界态势 {session.stance}")
        print(f"态度：{chat_state.attitude}（隐藏介绍门槛 "
              f"{HIDDEN_INTRO_ATTITUDE}）｜行动点 {state.action_points}"
              f"｜称呼「{chat_state.user_nick or '（默认）'}」")
        print(f"关系：{getattr(chat_state.relationship, 'kind', '')}"
              f"｜记忆 {len(chat_state.memory or '')} 字"
              f"｜消息 {len(chat_state.messages)} 条")
        if params is not None:
            print(f"聊天参数：online={params.online} warmup={params.warmup} "
                  f"boundary={params.boundary}")
        else:
            print("聊天参数：未决定（首次对话时由 AI 定档）")
        print(f"坐标：好感 {coord.affection:.3f}（{describe_affection(coord.affection)}）"
              f"｜紧张度 {coord.tension:.3f}（{describe_tension(coord.tension)}）"
              f"｜状态 {coord.size_state:.3f}"
              f"（{describe_size_state(coord.size_state)}）")
        if topic is not None:
            print(f"活跃话题：{topic.title}（{session.kind_label(topic.kind_id)}，"
                  f"{topic.turn_count} 轮，来源 {topic.source or '?'}）")
        else:
            print("活跃话题：无")
        for item in (chat_state.topics or [])[-4:]:
            mark = "→" if item.id == chat_state.active_topic_id else " "
            print(f"  {mark} {item.title or '（无标题）'}"
                  f"（{session.kind_label(item.kind_id)}，{item.turn_count} 轮，"
                  f"{item.status}/{item.end_reason or '-'}）")
        effects = chat_state.applied_effects or []
        if effects:
            for record in effects[-3:]:
                print(f"  效果 {record.tool}：{record.result}")
        if chat_state.memory:
            print("记忆摘要：" + _clip(chat_state.memory, 300))
        return True

    def kinds(self, _rest: str) -> bool:
        session = self.session
        candidates = {kind.id for kind in
                      topic_candidates(session.chat_state, session.coord())}
        print("话题类型（★=本轮候选）：")
        for kind in active_kinds():
            mark = "★" if kind.id in candidates else " "
            print(f" {mark} {kind.id:<10} {kind.label}｜{kind.focus}")
        return True

    def topic(self, rest: str) -> bool:
        session = self.session
        chat_state = session.chat_state
        if not rest:
            print("用法：/topic KIND 或 /topic off（/kinds 看可选类型）")
            return True
        now = datetime.datetime.now().isoformat()
        for item in chat_state.topics or []:
            if item.status == "active":
                item.status = "ended"
                item.end_reason = "switched"
                item.ended_at = now
        if rest.lower() in ("off", "none"):
            chat_state.active_topic_id = ""
            session.service.chat_repo.save(chat_state)
            print("已关闭活跃话题；下一轮走无话题视角（NO_TOPIC_LENS）。")
            return True
        kind = find_topic_kind(rest)
        if kind is None:
            print(f"未知类型 {rest}；/kinds 看可选值。")
            return True
        coord = session.coord()
        topic = Topic(title=f"手动·{kind.label}", kind_id=kind.id,
                      status="active", source="user", started_at=now,
                      last_activity_at=now, coord=coord.as_list())
        chat_state.topics.append(topic)
        chat_state.active_topic_id = topic.id
        session.service.chat_repo.save(chat_state)
        print(f"已切到「{kind.label}」（{kind.id}）；下一轮按这套视角取信息。")
        return True

    def attitude(self, rest: str) -> bool:
        session = self.session
        try:
            value = max(-100, min(100, int(rest)))
        except (TypeError, ValueError):
            print(f"当前态度 {session.chat_state.attitude}；用法 /attitude N")
            return True
        session.chat_state.attitude = value
        session.service.chat_repo.save(session.chat_state)
        note = "（达到隐藏介绍门槛）" if value >= HIDDEN_INTRO_ATTITUDE else ""
        print(f"态度已设为 {value}{note}。")
        return True

    def stance(self, rest: str) -> bool:
        session = self.session
        value = (rest or "").strip().lower()
        if not value:
            print(f"当前世界态势 {session.stance}；可选 "
                  + " / ".join(sorted(WORLD_STANCE_VALUES)))
            return True
        if value not in WORLD_STANCE_VALUES:
            print(f"未知态势 {value}；可选 "
                  + " / ".join(sorted(WORLD_STANCE_VALUES)))
            return True
        session.stance = value
        print(f"世界态势改为 {value}（影响紧张度与视角里的世界信息）。")
        return True

    def nick(self, rest: str) -> bool:
        session = self.session
        if not rest:
            print(f"当前称呼：{session.chat_state.user_nick or '（默认）'}")
            return True
        session.chat_state.user_nick = rest[:24]
        session.service.chat_repo.save(session.chat_state)
        print(f"称呼改为「{session.chat_state.user_nick}」。")
        return True

    def again(self, _rest: str) -> bool:
        if not self.session.turns:
            print("还没有发过消息。")
            return True
        last = self.session.turns[-1]
        print(f"（重发）{_clip(last.text, 100)}")
        self.session.send(last.text)
        return True

    def reroll(self, _rest: str) -> bool:
        turns = self.session.turns
        if not turns:
            print("还没有可重掷的一轮。")
            return True
        turn = turns[-1]
        if turn.before_chat is None:
            print("这一轮没有可回滚的快照。")
            return True
        # 整条线程回滚到发送前：消息、话题、态度、记忆、效果与角色属性一起还原，
        # 否则重掷会带着上一轮的副作用（例如话题已被切换），无法对比同一前提。
        self.session.chat_state.__dict__.update(
            copy.deepcopy(turn.before_chat).__dict__)
        self.session.state.__dict__.update(
            copy.deepcopy(turn.before_state).__dict__)
        self.session.turns.pop()
        self.session.service.chat_repo.save(self.session.chat_state)
        self.session.service.character_repo.save(self.session.state)
        print(f"已回滚到发送前（含话题与属性），重发：{_clip(turn.text, 100)}")
        self.session.send(turn.text, tag="重掷")
        return True

    def verbose(self, rest: str) -> bool:
        if rest in ("on", "1", "true"):
            self.session.verbose = True
        elif rest in ("off", "0", "false"):
            self.session.verbose = False
        else:
            self.session.verbose = not self.session.verbose
        print("原始 JSON：" + ("打印" if self.session.verbose else "不打印"))
        return True

    def exit_cmd(self, _rest: str) -> bool:
        return False


# ---------- 入口 ----------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="聊天会话预览：在控制台与角色实际对话，同时看每轮提示词账本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="会话默认在 developer_tools/_out/chat_session_preview/ 沙盒里跑，"
               "不改 data/。进入会话后 /help 看命令（/lens /prompt /state /topic "
               "/attitude /stance /reroll）。真实回复需要 data/user/api_keys.json；"
               "--fake 用固定假回复离线验证工具本身。")
    parser.add_argument("char", nargs="?", default=None,
                        help="角色 ID（存档目录名）")
    parser.add_argument("--list", action="store_true", help="列出可用角色")
    parser.add_argument("--stance", default="coexist",
                        choices=sorted(WORLD_STANCE_VALUES),
                        help="世界态势（影响紧张度与视角里的世界信息）")
    parser.add_argument("--attitude", type=int, default=None,
                        help="开局把聊天态度设为该值 -100~100")
    parser.add_argument("--nick", default=None, help="她对你的初始称呼")
    parser.add_argument("--identity", default=DEFAULT_IDENTITY_ID,
                        help="玩家身份 id（不同身份是不同聊天线程）")
    parser.add_argument("--topic", default=None, metavar="KIND",
                        help="开局前指定活跃话题类型（如 flirt）")
    parser.add_argument("--reset", action="store_true",
                        help="清空沙盒（档案与聊天历史）重建")
    parser.add_argument("--sync", action="store_true",
                        help="重新复制真实档案，保留沙盒聊天历史")
    parser.add_argument("--data-dir", default=None,
                        help="直接使用该 data 目录（默认复制到 _out 沙盒）")
    parser.add_argument("--script", default=None, metavar="FILE",
                        help="从文件逐行读输入（可含 / 命令），行首 # 为注释")
    parser.add_argument("--fake", action="store_true",
                        help="不联网：用固定假回复验证工具与流程")
    parser.add_argument("--verbose", action="store_true",
                        help="开局即打印原始 JSON")
    parser.add_argument("--log", nargs="?", const="auto", default=None,
                        metavar="FILE",
                        help="把会话记录写到文件（默认 _out/chat_session_preview/logs）")
    parser.add_argument("--prompt-on-start", action="store_true",
                        help="启动时打印整份系统提示词")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    source = Path(real_data_dir())

    if args.list:
        for char_id in list_character_ids(source):
            print(char_id)
        return 0

    char_id = args.char or pick_default_id(source)

    if args.data_dir:
        data_root = Path(args.data_dir).expanduser().resolve()
        if not (_archives_dir(data_root) / char_id / "info.json").exists():
            print(f"该目录下没有角色 {char_id}。")
            return 2
        print(f"⚠ 直接读写 {data_root}（--data-dir），会话会写进这个目录。")
    else:
        data_root = prepare_sandbox(
            char_id, source, reset=args.reset, sync=args.sync,
            variant="sandbox_fake" if args.fake else "sandbox")

    service = ChatService(data_dir=str(data_root))
    state = service.character_repo.load(char_id)
    if state is None:
        print(f"读不到角色档案：{char_id}")
        return 2
    chat_state = service.load_chat(char_id, identity_id=args.identity)

    session = Session(service, state, chat_state, char_id, args.stance)
    session.verbose = args.verbose
    # 服务层每轮都问一次世界态势：接到会话上，/stance 改了立即生效
    service.world_stance_provider = lambda: session.stance

    if args.attitude is not None:
        chat_state.attitude = max(-100, min(100, args.attitude))
        service.chat_repo.save(chat_state)
    if args.nick:
        chat_state.user_nick = args.nick[:24]
        service.chat_repo.save(chat_state)

    inner, client_name = chat_mod.get_ai_client()
    if args.fake:
        inner, client_name = FakeClient(), "fake（离线假回复，不联网）"
    if inner is None:
        print("未配置可用的 AI（data/user/api_keys.json）——"
              "先在设置里填 API，或加 --fake 离线验证工具。")
        return 2
    session.client = RecordingClient(inner, session)
    session.client_name = client_name or "未知后端"

    if args.log:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        target = (OUT_ROOT / "logs" / f"{char_id}_{stamp}.md"
                  if args.log == "auto" else Path(args.log))
        if not target.is_absolute():
            target = OUT_ROOT / "logs" / target
        session.transcript = Transcript(target)

    commands = Commands(session)
    model = getattr(session.client, "model", "") or "?"
    provider = getattr(session.client, "provider", "") or "?"
    print("=" * 68)
    print(f"聊天会话预览 · {state.name}（{char_id}）")
    print(f"模型  {session.client_name} ｜ {provider} / {model}")
    if args.data_dir:
        print(f"目录  {data_root}（真实数据）")
    else:
        print(f"沙盒  {data_root}")
    print(f"世界态势 {args.stance} ｜ 身份 {chat_state.identity_id} ｜ "
          f"历史 {len(chat_state.messages)} 条 ｜ 态度 {chat_state.attitude}")
    print("直接输入消息回车发送；/help 看命令，/exit 退出。")
    if session.transcript is not None:
        print(f"记录  {session.transcript.path}")
        session.transcript.header(
            f"# 聊天会话预览 · {state.name}（{char_id}）\n\n"
            f"- 模型：{session.client_name}（{provider} / {model}）\n"
            f"- 世界态势：{args.stance}　身份：{chat_state.identity_id}"
            f"　态度：{chat_state.attitude}　身高：{state.height:g}\n"
            f"- 数据：{data_root}\n"
            f"- 开始：{datetime.datetime.now():%Y-%m-%d %H:%M:%S}")
    print("=" * 68)

    restore = install_hooks(session)
    try:
        if args.topic:
            commands.handle(f"/topic {args.topic}")
        if args.prompt_on_start:
            commands.handle("/prompt")
        if args.script:
            _run_script(session, commands, Path(args.script))
        else:
            _run_console(session, commands)
    except KeyboardInterrupt:
        print()
    finally:
        restore()
        summary = session.summary()
        print()
        print(summary)
        if session.transcript is not None:
            body = "\n".join("- " + line for line in summary.splitlines())
            session.transcript.write(
                "## 汇总\n\n" + body
                + f"\n\n_会话结束于 {datetime.datetime.now():%Y-%m-%d %H:%M:%S}_\n")
            session.transcript.close()
    print(f"会话结束。沙盒与记录：{data_root}")
    return 0


def _run_console(session: Session, commands: Commands):
    while True:
        try:
            line = input("你> ")
        except EOFError:
            print()
            return
        if not _handle_line(session, commands, line):
            return


def _run_script(session: Session, commands: Commands, path: Path):
    if not path.exists():
        raise SystemExit(f"找不到脚本文件：{path}")
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        print(f"你> {line}")
        if not _handle_line(session, commands, line):
            return


def _handle_line(session: Session, commands: Commands, line: str) -> bool:
    text = (line or "").strip()
    if not text:
        return True
    if text.startswith("/"):
        return commands.handle(text)
    session.send(text)
    return True


if __name__ == "__main__":
    raise SystemExit(main())
