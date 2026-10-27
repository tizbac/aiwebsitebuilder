"""DuckDuckGo web search through a hidden QtWebEngine browser.

DuckDuckGo blocks plain HTTP scrapers from many networks, while its public
web UI works normally in a real browser. The app therefore runs the search in
an off-screen QWebEngineView and extracts the rendered results.
"""

from __future__ import annotations

import time
from urllib.parse import quote_plus

from PyQt6.QtCore import QObject, QTimer, Qt, QUrl
from PyQt6.QtWebEngineCore import QWebEnginePage
from PyQt6.QtWebEngineWidgets import QWebEngineView


EXTRACT_JS = """
(function () {
  const results = [];
  const seen = new Set();
  const anchors = document.querySelectorAll(
    'a[data-testid="result-title-a"], a.result__a'
  );
  anchors.forEach(function (a) {
    const href = a.href || '';
    if (!href.startsWith('http')) return;
    if (href.indexOf('duckduckgo.com/?q=') !== -1) return;
    if (seen.has(href)) return;
    const article = a.closest('article')
      || a.closest('div[data-testid="result"]')
      || a.closest('div.result');
    if (!article && !a.getAttribute('data-testid')) return;
    let snippet = '';
    if (article) {
      const node = article.querySelector(
        '[data-result="snippet"], .result__snippet, [data-testid="result-snippet"]'
      );
      snippet = node ? node.innerText.trim() : '';
      if (!snippet) {
        const text = article.innerText || '';
        const title = a.innerText || '';
        const at = title ? text.indexOf(title) : -1;
        if (at >= 0) snippet = text.slice(at + title.length).trim().slice(0, 500);
      }
    }
    seen.add(href);
    results.push({
      title: (a.innerText || '').trim(),
      url: href,
      snippet: snippet.replace(/\\s+/g, ' ').trim().slice(0, 600)
    });
  });
  return {
    title: document.title || '',
    url: location.href || '',
    results: results.slice(0, 20),
    body: (document.body ? document.body.innerText : '').slice(0, 600)
  };
})()
"""


class DuckDuckGoSearch(QObject):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._view: QWebEngineView | None = None
        self._pending: dict | None = None
        self._timeout: QTimer | None = None

    # ------------------------------------------------------------- public
    def search(self, query: str, max_results: int, callback) -> None:
        if self._pending is not None:
            callback(
                {
                    "ok": False,
                    "error": "Another web search is already running.",
                }
            )
            return
        self._view = self._ensure_view()
        pending = {
            "query": query,
            "max_results": max_results,
            "callback": callback,
            "attempts": 0,
            "started": time.time(),
        }
        self._pending = pending
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.setInterval(30000)
        self._timeout.timeout.connect(lambda: self._finish(None, "Search timed out."))
        self._timeout.start()
        url = (
            "https://duckduckgo.com/?q="
            + quote_plus(query)
            + "&ia=web&kl=wt-wt"
        )
        self._view.load(QUrl(url))

    def close(self) -> None:
        if self._timeout:
            self._timeout.stop()
            self._timeout.deleteLater()
            self._timeout = None
        self._pending = None
        view = self._view
        self._view = None
        if view is not None:
            try:
                view.stop()
                view.close()
                view.deleteLater()
            except RuntimeError:
                pass

    # ------------------------------------------------------------- internals
    def _ensure_view(self) -> QWebEngineView:
        if self._view is not None:
            return self._view
        view = QWebEngineView()
        page = QWebEnginePage(view)
        view.setPage(page)
        view.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        view.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        view.resize(1280, 900)
        view.move(-20000, -20000)
        view.show()
        view.loadFinished.connect(self._on_load_finished)
        return view

    def _on_load_finished(self, _ok: bool) -> None:
        if self._pending is None:
            return
        # Results are rendered client-side after the initial document load.
        QTimer.singleShot(1900, self._extract)

    def _extract(self) -> None:
        pending = self._pending
        if pending is None or self._view is None:
            return
        try:
            self._view.page().runJavaScript(EXTRACT_JS, self._on_extracted)
        except RuntimeError as exc:
            self._finish(None, f"Search failed: {exc}")

    def _on_extracted(self, data) -> None:
        pending = self._pending
        if pending is None:
            return
        if not isinstance(data, dict):
            self._finish(None, "DuckDuckGo returned an unreadable page.")
            return
        results = data.get("results") or []
        if not results:
            pending["attempts"] += 1
            if pending["attempts"] < 3:
                QTimer.singleShot(1200, self._extract)
                return
            body = str(data.get("body") or "")
            detail = ""
            if "anomaly" in body.lower() or "challenge" in body.lower():
                detail = " DuckDuckGo served an anti-bot challenge."
            self._finish([], "No DuckDuckGo results were found." + detail)
            return
        limit = max(1, int(pending["max_results"]))
        self._finish(results[:limit], "")

    def _finish(self, results: list[dict] | None, error: str) -> None:
        pending = self._pending
        if pending is None:
            return
        self._pending = None
        if self._timeout:
            self._timeout.stop()
            self._timeout.deleteLater()
            self._timeout = None
        callback = pending.get("callback")
        if not callback:
            return
        if error or not results:
            callback({"ok": False, "error": error or "No results.", "text": ""})
            return
        query = pending["query"]
        lines = [f"Web search results for {query!r} (DuckDuckGo):"]
        for index, item in enumerate(results, 1):
            title = (item.get("title") or "").strip() or "(untitled)"
            url = (item.get("url") or "").strip()
            snippet = (item.get("snippet") or "").strip()
            lines.append(f"\n{index}. {title}\n   {url}")
            if snippet:
                lines.append(f"   {snippet}")
        callback(
            {
                "ok": True,
                "text": "\n".join(lines),
                "results": len(results),
                "query": query,
            }
        )
