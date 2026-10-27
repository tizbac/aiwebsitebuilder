"""Chat history persistence (stored inside the project)."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path


def new_id() -> str:
    return "m" + uuid.uuid4().hex[:12]


def now() -> float:
    return time.time()


class ChatHistory:
    """Ordered message list plus compaction markers, persisted per project."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.messages: list[dict] = []
        self.version = 1
        self.error: str = ""

    # ------------------------------------------------------------------ io
    def load(self) -> bool:
        self.messages = []
        self.error = ""
        if not self.path.exists():
            return True
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.error = f"Could not read chat history: {exc}"
            return False
        if isinstance(data, list):  # tolerate a bare list
            self.messages = [m for m in data if isinstance(m, dict)]
            return True
        if not isinstance(data, dict):
            self.error = "Chat history has an unsupported format."
            return False
        messages = data.get("messages") or []
        self.messages = [m for m in messages if isinstance(m, dict)]
        self.version = int(data.get("version") or 1)
        return True

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updated": time.time(),
            "messages": self.messages,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        tmp.replace(self.path)

    # ------------------------------------------------------------- mutation
    def append(self, message: dict) -> dict:
        message.setdefault("id", new_id())
        message.setdefault("ts", now())
        self.messages.append(message)
        self.save()
        return message

    def remove(self, message_id: str) -> None:
        self.messages = [m for m in self.messages if m.get("id") != message_id]
        self.save()

    def clear(self) -> None:
        self.messages = []
        self.save()

    def find(self, message_id: str) -> dict | None:
        for message in self.messages:
            if message.get("id") == message_id:
                return message
        return None

    # ------------------------------------------------------------- helpers
    def last_compaction(self) -> tuple[int, dict | None]:
        for index in range(len(self.messages) - 1, -1, -1):
            message = self.messages[index]
            if message.get("role") == "compaction":
                return index, message
        return 0, None

    def insert_compaction(self, split_index: int, summary: str, count: int, tokens_before: int) -> dict:
        up_to = ""
        if 0 < split_index <= len(self.messages):
            up_to = self.messages[split_index - 1].get("id", "")
        marker = {
            "id": new_id(),
            "role": "compaction",
            "summary": summary,
            "up_to": up_to,
            "count": count,
            "tokens_before": tokens_before,
            "ts": now(),
        }
        self.messages.insert(split_index, marker)
        self.save()
        return marker
