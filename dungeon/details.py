"""细节探究：AI 在后台提出「想了解的细节」，下次生成前用回放缓存解答。

流式输出完成后（``engine._generate_next_text`` 尾部），立即用刚生成的
段落组装提问，后台线程询问 AI 还想了解哪些重要细节（最多 3 条简短问题）；
玩家点击触发生成后续段落时，``build_user_prompt`` 用问题中的二元组关键词
在 replay 缓存（全部历史段落原文）中检索最相关段落，把命中原文作为
「细节补充」注入提示词，随后消费掉这批问题（未命中的问题直接
丢弃，避免过期疑问残留）。
"""

import json
import re

MAX_QUERIES = 3
QUERY_CHARS = 24
MATCH_CLIP_CHARS = 80
# 二元组含这些字时区分度过低，不作为检索关键词
_STOP_CHARS = "的了是在吗呢吧么啊"


def _clip(text, limit):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def build_detail_query_prompt(paragraph_text, recent_texts=(), chapter_name="",
                              coupling_level=None):
    """组装细节提问调用：刚生成的段落 + 少量近期上下文。

    提问的系统提示按耦合等级取用：Velum 收集叙事细节、Solea 收集任务情况、
    Bulla 收集她的想法与活动。
    """
    from .coupling import coupling_prompts, normalize_coupling_level
    pack = coupling_prompts(normalize_coupling_level(coupling_level))
    lines = []
    if chapter_name:
        lines.append(f"当前章节：{chapter_name}")
    recent = [str(t) for t in recent_texts if t][-3:]
    if recent:
        lines.append("近期剧情：")
        lines.extend(f"- {_clip(t, 100)}" for t in recent)
    lines.append("刚生成的段落：")
    lines.append(_clip(paragraph_text, 200))
    return [
        {"role": "system", "content": pack["detail_system"]},
        {"role": "user", "content": "\n".join(lines)},
    ]


def parse_detail_queries(raw) -> list:
    """解析提问调用返回的问题列表；非 JSON 时按行拆分兜底。"""
    raw = str(raw or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").lstrip("json").strip()
    start = raw.find("{")
    if start != -1:
        try:
            data, _ = json.JSONDecoder().raw_decode(raw[start:])
            queries = data.get("queries")
            if isinstance(queries, list):
                return _clean(queries)
        except json.JSONDecodeError:
            pass
    return _clean(raw.splitlines())


def _clean(items) -> list:
    out = []
    for item in items:
        q = _clip(item, QUERY_CHARS)
        q = re.sub(r"^[\d]+[.、)）]\s*", "", q).strip("-• ")
        if q and q not in out:
            out.append(q)
        if len(out) >= MAX_QUERIES:
            break
    return out


def _keywords(query: str) -> set:
    cleaned = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", query)
    if len(cleaned) < 2:
        return {cleaned} if cleaned else set()
    return {cleaned[i:i + 2] for i in range(len(cleaned) - 1)
            if not any(ch in _STOP_CHARS for ch in cleaned[i:i + 2])}


def search_replay_details(queries, replay_data) -> list:
    """在回放缓存中为每条问题检索最相关段落，返回命中的解释行。

    按问题关键词（二元组）在全部历史段落原文中计分，取最高分段落；
    得分不足（无真正相关内容）的问题直接丢弃，不注入提示词。
    """
    entries = [str(e.get("text") or "") for e in (replay_data or [])
               if isinstance(e, dict) and e.get("text")]
    if not entries:
        return []
    lines = []
    for query in queries:
        kws = _keywords(query)
        if not kws:
            continue
        best_text, best_score = "", 0
        for text in entries:
            score = sum(1 for kw in kws if kw in text)
            if score > best_score:
                best_text, best_score = text, score
        if best_score >= max(2, len(kws) // 3):
            lines.append(f"{query}——{_clip(best_text, MATCH_CLIP_CHARS)}")
    return lines
