"""副本剧情压缩器。

把冗长的段落历史压缩成可注入提示词的“剧情概要”。

- ``record``：每一步结束后登记段落文本与所在章节；
- 每当 ``total_steps`` 达到 ``block_size`` 的倍数、或即将离开当前章节
  （``flush_chapter``）时，对上一块/上一章节的剩余段落做一次压缩；
- ``prompt_block``：返回给 ``DungeonPromptBuilder`` 的概要文本，包含
  全部压缩段、事实卡与关键事件。近期原文不再注入——对话历史
  （窗口大小见 ``story_recent_count`` 设置）已逐字携带最近剧情，
  重复注入只会推高 prefill 与首字延迟。

压缩优先使用 AI 客户端生成概要并抽取事实卡；AI 不可用或失败时回退到
内部算法（截取首末句与高频词拼接），保证提示词始终有概要可用。

常驻记忆（不依赖 AI）：
- ``record_key_event``：代码路径直接登记的确定性关键事件（章节进出、
  选项选择、介入度/破坏性跨阈值），与事实卡一起常驻提示词；
- ``merge_facts``：压缩调用抽取的设定级事实，跨块去重合并。
"""

from __future__ import annotations

import json
from typing import Iterable

from . import process_log
from .coupling import coupling_prompts

DEFAULT_BLOCK_SIZE = 20
MAX_SUMMARY_CHARS = 120
MAX_KEY_EVENTS = 12      # 常驻提示词的确定性关键事件上限
MAX_FACTS = 12           # 常驻提示词的关键事实（事实卡）上限
KEY_EVENT_CHARS = 60
FACT_CHARS = 60


def _clip(text: str, limit: int = MAX_SUMMARY_CHARS) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


class StorySummarizer:
    """按块压缩剧情，并向提示词构建器提供“概要 + 最近段落”。"""

    def __init__(self, block_size: int = DEFAULT_BLOCK_SIZE, coupling_level: str = None):
        self.block_size = max(1, int(block_size))
        # 概要与事实卡的收集口径随耦合等级变化（见 dungeon.coupling）
        self.coupling_level = coupling_level
        # 已完成压缩的段落：[(chapter_name, summary_text), ...]
        self.summaries: list[tuple[str, str]] = []
        # 事实卡：跨块合并去重的设定级关键事实（人物、地点、伏笔、承诺）
        self.facts: list[str] = []
        # 确定性关键事件（章节进出、选项选择、阈值跨越），代码路径直接登记
        self.key_events: list[str] = []
        # 尚未压缩的缓冲：[(text_type_value, text, chapter_name), ...]
        self._buffer: list[tuple[str, str, str]] = []

    # ---------------- 写入 ----------------
    def record(self, text_type_value: str, text: str, chapter_name: str = ""):
        """登记一段已展示的正文（AI 段落、插入段、回放步进共用）。"""
        text = str(text or "").strip()
        if not text:
            return
        self._buffer.append((text_type_value, text, chapter_name or ""))

    def flush_chapter(self, chapter_name: str, ai_client=None):
        """离开章节时强制压缩本章缓冲（不满一个块也压缩）。"""
        self._compress(chapter_name=chapter_name, ai_client=ai_client,
                       allow_partial=True)

    def maybe_compress(self, total_steps: int, chapter_name: str = "",
                       ai_client=None):
        """缓冲达到 block_size 时压缩一个块。"""
        if self.block_size <= 0:
            return
        if len(self._buffer) >= self.block_size:
            self._compress(chapter_name=chapter_name, ai_client=ai_client,
                           allow_partial=False)

    # ---------------- 关键事件与事实卡 ----------------
    def record_key_event(self, text: str):
        """登记一条确定性关键事件，常驻提示词（有界，超出丢弃最旧）。

        只记定性里程碑（章节进出、选项选择、介入度/破坏性跨阈值），
        不记敏感效果等纯数值信息。
        """
        text = _clip(text, KEY_EVENT_CHARS)
        if not text:
            return
        if self.key_events and self.key_events[-1] == text:
            return
        self.key_events.append(text)
        if len(self.key_events) > MAX_KEY_EVENTS:
            del self.key_events[:len(self.key_events) - MAX_KEY_EVENTS]

    def merge_facts(self, facts):
        """把压缩调用提取的关键事实合并进事实卡（去重、有界）。"""
        for fact in facts or []:
            fact = _clip(str(fact or ""), FACT_CHARS)
            if fact and fact not in self.facts:
                self.facts.append(fact)
        if len(self.facts) > MAX_FACTS:
            del self.facts[:len(self.facts) - MAX_FACTS]

    # ---------------- 压缩 ----------------
    def _compress(self, chapter_name: str = "", ai_client=None,
                  allow_partial: bool = False):
        if not self._buffer:
            return
        if not allow_partial and len(self._buffer) < self.block_size:
            return
        chunk = self._buffer if allow_partial else self._buffer[:self.block_size]
        self._buffer = [] if allow_partial else self._buffer[len(chunk):]
        summary, facts = self._summarize_chunk(chunk, ai_client)
        label = chapter_name or (chunk[0][2] if chunk else "")
        self.summaries.append((label, summary))
        self.merge_facts(facts)

    def _summarize_chunk(self, chunk: list, ai_client=None) -> tuple:
        """压缩一个块，返回 (概要, 关键事实列表)。"""
        texts = [t for _type, t, _ch in chunk if t]
        if not texts:
            return "", []
        if ai_client is not None:
            try:
                joined = "\n".join(texts)
                messages = [
                    {"role": "system", "content": coupling_prompts(
                        self.coupling_level)["summary_system"]},
                    {"role": "user", "content":
                        f"请概括以下 {len(texts)} 段剧情：\n{joined}"},
                ]
                raw = ai_client.generate(messages, temperature=0.3)
                summary, facts = self._parse_summary_json(raw)
                if summary:
                    return summary, facts
            except Exception as e:
                process_log.log(f"[Summary] AI 压缩失败，改用内部算法: {e}")
        return self._fallback_summary(texts), []

    @staticmethod
    def _parse_summary_json(raw) -> tuple:
        """解析压缩调用返回的 {summary, facts}；解析失败时整体当概要。"""
        raw = str(raw or "").strip()
        if raw.startswith("```"):
            raw = raw.strip("`").lstrip("json").strip()
        start = raw.find("{")
        if start != -1:
            try:
                data, _ = json.JSONDecoder().raw_decode(raw[start:])
                summary = str(data.get("summary") or "").strip()
                facts = data.get("facts")
                if summary:
                    return _clip(summary), facts if isinstance(facts, list) else []
            except json.JSONDecodeError:
                pass
        return (_clip(raw) if raw else ""), []

    @staticmethod
    def _fallback_summary(texts: Iterable[str]) -> str:
        """内部算法：首句 + 末句 + 高频关键词拼成概要。"""
        texts = [t for t in texts if t]
        if not texts:
            return ""
        first = _clip(texts[0], 40)
        last = _clip(texts[-1], 40)
        keywords = StorySummarizer._keywords(texts)
        parts = []
        if first:
            parts.append(f"起：{first}")
        if keywords:
            parts.append(f"关键：{'、'.join(keywords)}")
        if last and last != first:
            parts.append(f"末：{last}")
        return _clip("；".join(parts) or f"共{len(texts)}段剧情")

    @staticmethod
    def _keywords(texts: Iterable[str], top: int = 3) -> list:
        """简单词频统计：取长度≥2 的高频连续中文词作为关键词。"""
        import re
        counter: dict[str, int] = {}
        for text in texts:
            for token in re.findall(r"[一-鿿]{2,4}", text):
                counter[token] = counter.get(token, 0) + 1
        ranked = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
        return [word for word, _count in ranked[:top]]

    # ---------------- 提示词 ----------------
    def _memory_lines(self) -> list:
        """概要之外的常驻记忆：事实卡与关键事件（紧凑注入）。"""
        lines = []
        if self.facts:
            lines.append("关键事实：" + "；".join(self.facts))
        if self.key_events:
            lines.append("关键事件（按时间顺序）：")
            lines.extend(f"- {event}" for event in self.key_events)
        return lines

    def prompt_block(self) -> str:
        """注入提示词的剧情块：压缩概要 + 常驻记忆（近期原文由对话历史携带）。"""
        lines = []
        if self.summaries:
            lines.append("剧情概要（按时间顺序）：")
            for i, (chapter, summary) in enumerate(self.summaries, 1):
                prefix = f"{i}. " + (f"【{chapter}】" if chapter else "")
                lines.append(prefix + summary)
        lines.extend(self._memory_lines())
        return "\n".join(lines)

    def summary_only(self) -> str:
        """仅压缩概要与常驻记忆（结局生成等不需要原文的场景使用）。"""
        lines = []
        for i, (chapter, summary) in enumerate(self.summaries, 1):
            lines.append(f"{i}. 【{chapter}】{summary}" if chapter else f"{i}. {summary}")
        lines.extend(self._memory_lines())
        return "\n".join(lines)
