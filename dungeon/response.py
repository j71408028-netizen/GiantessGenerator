import json


def extract_stream_text(response_text: str):
    """从尚未完成的 JSON 流中提取 text，正确处理转义引号。"""
    marker = response_text.find('"text"')
    if marker == -1:
        return None
    colon = response_text.find(":", marker + 6)
    if colon == -1:
        return None
    start = response_text.find('"', colon + 1)
    if start == -1:
        return None

    raw = response_text[start + 1:]
    escaped = False
    end = None
    for index, char in enumerate(raw):
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            end = index
            break

    raw_value = raw if end is None else raw[:end]
    try:
        return json.loads('"' + raw_value + '"')
    except json.JSONDecodeError:
        return raw_value.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")


def canonical_history_entry(text: str, direction: int,
                            custom_directions: dict) -> str:
    """把解析结果规范化为协议 JSON 文本，用作对话历史中的 assistant 消息。

    历史里一旦混入纯文本的 assistant 回合（原始响应跑格式、或触发器插入段），
    模型会模仿历史继续输出纯文本，格式漂移自我强化——实测高介入度剧情下
    JSON 服从率会从 100% 跌到 20%。因此历史一律存规范形状，与原始响应
    是否合规无关。
    """
    return json.dumps({"text": text, "direction": direction,
                       "custom_directions": custom_directions or {}},
                      ensure_ascii=False)


def parse_final_json(response_text: str, custom_names: set[str]):
    cleaned = response_text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    start = cleaned.find("{")
    if start == -1:
        return cleaned, 0, {}

    try:
        data, _ = json.JSONDecoder().raw_decode(cleaned[start:])
        text = data.get("text", "").strip()
        direction = data.get("direction", 0)
        if direction not in (-1, 0, 1):
            direction = 0
        custom_directions = data.get("custom_directions", {})
        if not isinstance(custom_directions, dict):
            custom_directions = {}
        filtered = {}
        for key, value in custom_directions.items():
            if key not in custom_names:
                continue
            try:
                filtered[key] = max(-1, min(1, int(value)))
            except (TypeError, ValueError):
                filtered[key] = 0
        return text or "她微微一笑，继续前行。", direction, filtered
    except json.JSONDecodeError:
        return extract_stream_text(cleaned) or cleaned, 0, {}
