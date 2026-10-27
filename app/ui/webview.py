"""QWebEngineView subclass that records JS console messages and load errors."""

from __future__ import annotations

import time
from collections import deque

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView


LEVEL_NAMES = {
    0: "info",
    1: "warning",
    2: "error",
}


class ConsolePage(QWebEnginePage):
    console_message = pyqtSignal(int, str, int, str)

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):  # noqa: N802
        try:
            value = int(level.value) if hasattr(level, "value") else int(level)
        except (TypeError, ValueError):
            value = 0
        self.console_message.emit(value, str(message), int(line_number), str(source_id))


class ConsoleWebView(QWebEngineView):
    console_message = pyqtSignal(int, str, int, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._page = ConsolePage(self)
        self.setPage(self._page)
        self._messages: deque[dict] = deque(maxlen=1500)
        self._page.console_message.connect(self._record)
        self._page.console_message.connect(self.console_message)
        settings = self.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.FocusOnNavigationEnabled, True)
        self._last_load_ok: bool | None = None
        self._last_url = ""
        self.loadFinished.connect(self._on_load_finished)

    def _record(self, level: int, message: str, line: int, source: str) -> None:
        self._messages.append(
            {
                "level": level,
                "level_name": LEVEL_NAMES.get(level, "info"),
                "message": message,
                "line": line,
                "source": source,
                "ts": time.time(),
            }
        )

    def _on_load_finished(self, ok: bool) -> None:
        self._last_load_ok = bool(ok)
        self._last_url = self.url().toString()

    def clear_console(self) -> None:
        self._messages.clear()

    @property
    def last_load_ok(self) -> bool | None:
        return self._last_load_ok

    def console_messages(self, *, errors_only: bool = False) -> list[dict]:
        messages = list(self._messages)
        if errors_only:
            messages = [m for m in messages if m["level"] >= 1]
        return messages

    def console_text(self, *, errors_only: bool = True, limit: int = 40) -> str:
        messages = self.console_messages(errors_only=errors_only)[-limit:]
        return format_console_messages(messages)

    def console_since(self, timestamp: float, *, errors_only: bool = True, limit: int = 30) -> str:
        messages = [
            m
            for m in list(self._messages)
            if m.get("ts", 0) >= timestamp and (not errors_only or m["level"] >= 1)
        ][-limit:]
        return format_console_messages(messages)

    def createWindow(self, window_type):  # noqa: N802
        # Keep target=_blank navigation inside the preview.
        return self


def format_console_messages(messages: list[dict]) -> str:
    lines = []
    for message in messages:
        source = message.get("source") or ""
        if source:
            source = source.split("?")[0]
            if len(source) > 120:
                source = "…" + source[-117:]
        location = f" ({source}:{message['line']})" if source else f" (line {message['line']})"
        lines.append(f"[{message['level_name']}] {message['message']}{location}")
    return "\n".join(lines)
