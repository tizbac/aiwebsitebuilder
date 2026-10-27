"""Rough token accounting used for context-usage display and compaction."""

from __future__ import annotations

import json


def estimate_text(text: str) -> int:
    if not text:
        return 0
    # A decent cross-model average for mixed prose/code/JSON.
    return max(1, int(len(text) / 3.6))


EXTRAS_MARKER = "\x00extras"


def estimate_message(message: dict) -> int:
    tokens = 5
    content = message.get("content")
    if isinstance(content, str):
        tokens += estimate_text(content)
    elif isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                tokens += estimate_text(str(part.get("text") or ""))
            elif part.get("type") == "image_url":
                tokens += 900
    for tc in message.get("tool_calls") or []:
        tokens += 15
        fn = tc.get("function") or {}
        tokens += estimate_text(str(fn.get("name") or ""))
        tokens += estimate_text(str(fn.get("arguments") or ""))
    if message.get("reasoning_content"):
        tokens += estimate_text(str(message["reasoning_content"]))
    return tokens


def estimate_messages(messages: list[dict], tool_tokens: int = 1400) -> int:
    return sum(estimate_message(m) for m in messages) + tool_tokens


def estimate_image_tokens(width: int, height: int) -> int:
    if width <= 0 or height <= 0:
        return 900
    return max(100, min(1600, int((width * height) / 800)))
