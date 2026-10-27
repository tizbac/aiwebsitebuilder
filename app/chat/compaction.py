"""Context compaction: plan, summarise and persist handoff summaries."""

from __future__ import annotations

from dataclasses import dataclass, field

from PyQt6.QtCore import QThread, pyqtSignal

from .client import ApiError, complete_sync
from .history import ChatHistory
from .prompts import MERGE_SYSTEM, SUMMARY_SYSTEM, merge_user, summary_user
from .tokens import estimate_message, estimate_text


MAX_SUMMARY_CALLS = 10
MAX_TRANSCRIPT_CHARS = 260_000


@dataclass
class CompactionPlan:
    split_index: int
    old_messages: list[dict] = field(default_factory=list)
    previous_summary: str = ""
    keep_count: int = 0
    tokens_before: int = 0

    @property
    def summarised_count(self) -> int:
        return len([m for m in self.old_messages if m.get("role") != "compaction"])


def plan_compaction(history: ChatHistory, context_limit: int, *, keep_ratio: float = 0.15) -> CompactionPlan | None:
    """Choose a safe boundary between summarised and retained messages.

    The split never separates an assistant tool-call turn from its tool results.
    """
    messages = history.messages
    start, marker = history.last_compaction()
    body = messages[start:]
    if len(body) < 4:
        return None

    keep_tokens = max(1800, int(context_limit * keep_ratio))
    tokens = 0
    split = len(body)
    for index in range(len(body) - 1, -1, -1):
        message = body[index]
        if message.get("role") == "compaction":
            continue
        tokens += estimate_message(message)
        split = index
        if (len(body) - index) >= 6 and tokens >= keep_tokens:
            break

    # Keep whole tool-call groups together.
    while split > 0 and body[split].get("role") == "tool":
        split -= 1
    while split > 0 and body[split].get("role") == "compaction":
        split += 1

    if split <= 0:
        return None
    old = body[:split]
    old_real = [m for m in old if m.get("role") != "compaction"]
    if len(old_real) < 2:
        return None
    previous = (marker or {}).get("summary", "")
    return CompactionPlan(
        split_index=start + split,
        old_messages=old,
        previous_summary=previous,
        keep_count=len(body) - split,
        tokens_before=estimate_message_sum(old),
    )


def estimate_message_sum(messages: list[dict]) -> int:
    return sum(estimate_message(m) for m in messages)


def _shorten(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…[truncated {len(text) - limit} chars]"


def format_message(message: dict) -> str:
    role = message.get("role")
    if role == "user":
        text = _shorten(str(message.get("content") or ""), 5000)
        images = message.get("images") or []
        note = ""
        if images:
            names = ", ".join(str(img.get("name") or img.get("path") or "image") for img in images)
            note = f"\n[user attached reference image(s): {names}]"
        return f"USER:\n{text}{note}"
    if role == "assistant":
        text = _shorten(str(message.get("content") or ""), 5000)
        parts = [f"ASSISTANT:\n{text}"] if text else []
        for call in message.get("tool_calls") or []:
            fn = call.get("function") or {}
            args = _shorten(str(fn.get("arguments") or ""), 1400)
            parts.append(f"[called tool {fn.get('name')} with arguments {args}]")
        return "\n".join(parts) if parts else ""
    if role == "tool":
        name = message.get("name") or "tool"
        args = _shorten(str(message.get("arguments") or ""), 1000)
        content = _shorten(str(message.get("content") or ""), 3000)
        images = message.get("images") or []
        note = f"\n[{len(images)} image(s) produced]" if images else ""
        return f"TOOL {name}({args}) RESULT:\n{content}{note}"
    if role == "compaction":
        return ""
    return ""


def build_transcript(messages: list[dict], previous_summary: str = "") -> str:
    blocks = [block for block in (format_message(m) for m in messages) if block]
    text = "\n\n".join(blocks)
    if previous_summary:
        text = "PREVIOUS HANDOFF SUMMARY:\n" + previous_summary + "\n\nNEW MESSAGES:\n" + text
    if len(text) > MAX_TRANSCRIPT_CHARS:
        # Keep the most recent portion; earlier facts are usually already in the summary.
        text = "…[earlier transcript truncated]…\n\n" + text[-MAX_TRANSCRIPT_CHARS:]
    return text


def chunk_blocks(messages: list[dict], budget_tokens: int) -> list[list[dict]]:
    chunks: list[list[dict]] = []
    current: list[dict] = []
    current_tokens = 0
    for message in messages:
        block = format_message(message)
        if not block:
            continue
        size = max(1, estimate_text(block))
        if current and current_tokens + size > budget_tokens:
            chunks.append(current)
            current = []
            current_tokens = 0
        current.append(message)
        current_tokens += size
    if current:
        chunks.append(current)
    return chunks


def _summarise_messages(settings, messages: list[dict], previous_summary: str, progress) -> str:
    budget = max(1800, min(36_000, int(settings.context_limit * 0.35)))
    chunks = chunk_blocks(messages, budget)
    if not chunks:
        return ""
    if len(chunks) > MAX_SUMMARY_CALLS:
        # Keep the most recent chunks; older material is normally covered by the
        # previous summary. Flag the gap explicitly.
        dropped = sum(len(c) for c in chunks[:-MAX_SUMMARY_CALLS])
        chunks = chunks[-MAX_SUMMARY_CALLS:]
        previous_summary += f"\n\n[{dropped} older messages were omitted during compaction.]"

    summaries: list[str] = []
    for index, chunk in enumerate(chunks):
        progress.emit(
            f"Summarising context ({index + 1}/{len(chunks)})…" if len(chunks) > 1 else "Summarising context…"
        )
        transcript = build_transcript(
            chunk, previous_summary if index == 0 else ""
        )
        messages_payload = [
            {"role": "system", "content": SUMMARY_SYSTEM},
            {"role": "user", "content": summary_user(transcript)},
        ]
        summaries.append(complete_sync(settings, messages_payload))

    if len(summaries) == 1:
        return summaries[0]
    progress.emit("Merging summaries…")
    merged = complete_sync(
        settings,
        [
            {"role": "system", "content": MERGE_SYSTEM},
            {"role": "user", "content": merge_user(summaries)},
        ],
    )
    return merged


class CompactionWorker(QThread):
    done = pyqtSignal(str)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()
    progress = pyqtSignal(str)

    def __init__(self, settings, plan: CompactionPlan, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.plan = plan
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            summary = _summarise_messages(
                self.settings,
                self.plan.old_messages,
                self.plan.previous_summary,
                self.progress,
            )
            if self._cancelled:
                self.cancelled.emit()
                return
            if not summary.strip():
                raise ApiError("The model returned an empty summary.")
            self.done.emit(summary.strip())
        except ApiError as exc:
            if not self._cancelled:
                self.failed.emit(str(exc))
        except Exception as exc:
            if not self._cancelled:
                self.failed.emit(f"Compaction failed: {exc}")
