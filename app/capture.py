"""Full-page preview capture.

Desktop screenshots use the visible preview. Phone screenshots run in a
temporary off-screen 390x844 QWebEngineView so the main window layout is never
disturbed by responsive testing.
"""

from __future__ import annotations

import json
import time

from PyQt6.QtCore import QObject, QPoint, QTimer, Qt, QUrl
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtWebEngineCore import QWebEnginePage
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QWidget


PHONE_WIDTH = 390
PHONE_HEIGHT = 844

ELEMENT_JS = """
(function () {
  var selector = __SELECTOR__;
  var index = __INDEX__;
  try {
    var nodes = document.querySelectorAll(selector);
    if (!nodes.length) {
      return {found: false, error: "No element matches selector: " + selector};
    }
    if (index < 0 || index >= nodes.length) {
      return {found: false, error: "Index " + index + " is out of range; " +
        nodes.length + " element(s) matched."};
    }
    var el = nodes[index];
    var rect = el.getBoundingClientRect();
    return {
      found: true,
      tag: el.tagName ? el.tagName.toLowerCase() : '',
      text: (el.innerText || el.value || el.getAttribute('aria-label') || '')
        .trim().slice(0, 300),
      top: rect.top + window.scrollY,
      bottom: rect.bottom + window.scrollY,
      height: rect.height,
      width: rect.width,
      vw: window.innerWidth,
      vh: window.innerHeight,
      url: location.href,
      title: document.title
    };
  } catch (e) {
    return {found: false, error: String(e)};
  }
})()
"""

METRICS_JS = """
(function () {
  const de = document.documentElement;
  const body = document.body;
  const fullH = Math.max(
    de ? de.scrollHeight : 0,
    body ? body.scrollHeight : 0,
    de ? de.offsetHeight : 0,
    body ? body.offsetHeight : 0,
    window.innerHeight
  );
  const fullW = Math.max(
    de ? de.scrollWidth : 0,
    body ? body.scrollWidth : 0,
    window.innerWidth
  );
  return {
    height: fullH,
    width: fullW,
    vw: window.innerWidth,
    vh: window.innerHeight,
    title: document.title || '',
    url: location.href || '',
    y: window.scrollY,
    x: window.scrollX
  };
})()
"""

PREPARE_JS = """
(function () {
  window.__aiwbCapture = true;
  let style = document.getElementById('__aiwb_capture_style');
  if (!style) {
    style = document.createElement('style');
    style.id = '__aiwb_capture_style';
    (document.head || document.documentElement).appendChild(style);
  }
  style.textContent = [
    'html{scroll-behavior:auto !important;}',
    '::-webkit-scrollbar{display:none !important;width:0 !important;height:0 !important;}',
    'html{scrollbar-width:none !important;}',
    '*,*::before,*::after{animation-duration:0.001s !important;',
    'animation-delay:0s !important;transition-duration:0.001s !important;}'
  ].join('');
  return {width: window.innerWidth, height: window.innerHeight};
})()
"""

CLEANUP_JS = """
(function () {
  const style = document.getElementById('__aiwb_capture_style');
  if (style) style.remove();
  try { delete window.__aiwbCapture; } catch (e) { window.__aiwbCapture = false; }
})()
"""


class PageCapture(QObject):
    """Captures the live preview page as full-page desktop and phone images."""

    def __init__(self, view: QWidget, project, settings, console_provider=None, parent=None) -> None:
        super().__init__(parent)
        self.view = view
        self.project = project
        self.settings = settings
        self.console_provider = console_provider
        self._active = False
        self._callback = None
        self._modes: list[str] = []
        self._results: list[dict] = []
        self._errors: list[str] = []
        self._mode = ""
        self._view: QWidget = view
        self._phone_view: QWebEngineView | None = None
        self._metrics: dict = {}
        self._canvas: QImage | None = None
        self._painter: QPainter | None = None
        self._scale = 1.0
        self._previous_y = -1
        self._orig_scroll = 0
        self._full_height_css = 0
        self._slice_spec: dict | None = None
        self._slice_rect: dict | None = None

    # ------------------------------------------------------------- public
    def capture(self, modes: list[str], callback) -> None:
        self._slice_spec = None
        self._capture(modes, callback)

    def capture_slice(
        self,
        modes: list[str],
        selector: str,
        index: int,
        padding: int,
        callback,
    ) -> None:
        self._slice_spec = {
            "selector": selector,
            "index": int(index),
            "padding": max(0, int(padding)),
        }
        self._capture(modes, callback)

    def _capture(self, modes: list[str], callback) -> None:
        if self._active:
            callback(
                {
                    "results": [],
                    "errors": ["A screenshot is already being captured."],
                }
            )
            return
        modes = [m for m in modes if m in ("desktop", "phone")]
        if not modes:
            modes = ["desktop"]
        self._active = True
        self._callback = callback
        self._modes = modes
        self._results = []
        self._errors = []
        self._orig_scroll = 0
        self._view = self.view
        self._slice_rect = None
        self._run_js("window.scrollY", self._remember_scroll)
        QTimer.singleShot(40, self._next_mode)

    def abort(self) -> None:
        """Stop an in-flight capture without invoking the callback."""
        self._active = False
        self._callback = None
        self._modes = []
        self._slice_spec = None
        self._slice_rect = None
        if self._painter is not None:
            self._painter.end()
            self._painter = None
        self._canvas = None
        self._close_phone_view()
        self._view = self.view
        try:
            self._run_js(CLEANUP_JS, lambda _: None)
            self._run_js(f"window.scrollTo(0, {self._orig_scroll})", lambda _: None)
        except RuntimeError:
            pass

    # ------------------------------------------------------------ pipeline
    def _remember_scroll(self, value) -> None:
        try:
            self._orig_scroll = int(float(value or 0))
        except (TypeError, ValueError):
            self._orig_scroll = 0

    def _next_mode(self) -> None:
        if not self._active:
            return
        if not self._modes:
            self._finish()
            return
        self._mode = self._modes.pop(0)
        self._slice_rect = None
        if self._mode == "phone":
            self._start_phone_view()
        else:
            self._view = self.view
            self._view.setFocus()
            QTimer.singleShot(80, self._begin_mode)

    def _start_phone_view(self) -> None:
        url = self.view.url()
        phone = QWebEngineView()
        page = QWebEnginePage(self.view.page().profile(), phone)
        phone.setPage(page)
        phone.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        phone.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        phone.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        phone.resize(PHONE_WIDTH, PHONE_HEIGHT)
        phone.move(-20000, -20000)
        phone.show()
        self._phone_view = phone
        self._view = phone

        state = {"done": False}

        def loaded(_ok: bool) -> None:
            if state["done"]:
                return
            state["done"] = True
            QTimer.singleShot(750, self._begin_mode)

        phone.loadFinished.connect(loaded)
        QTimer.singleShot(12000, lambda: loaded(False))
        if url.isValid() and url.toString() and url.toString() != "about:blank":
            phone.load(url)
        else:
            phone.setHtml(
                "<!doctype html><html><body style='background:#ffffff'></body></html>",
                QUrl("about:blank"),
            )

    def _begin_mode(self) -> None:
        if not self._active:
            return
        self._run_js(
            "window.scrollTo(0, 0);",
            lambda _: self._run_js(
                PREPARE_JS, lambda _: QTimer.singleShot(250, self._after_prepare)
            ),
        )

    def _after_prepare(self) -> None:
        if not self._active:
            return
        if self._slice_spec:
            selector = str(self._slice_spec.get("selector") or "")
            index = int(self._slice_spec.get("index") or 0)
            code = (
                ELEMENT_JS.replace("__SELECTOR__", json.dumps(selector))
                .replace("__INDEX__", str(index))
            )
            self._run_js(code, self._on_slice_measured)
        else:
            self._read_metrics()

    def _on_slice_measured(self, result) -> None:
        if not self._active:
            return
        if not isinstance(result, dict) or not result.get("found"):
            error = "element not found"
            if isinstance(result, dict) and result.get("error"):
                error = str(result["error"])
            self._errors.append(f"{self._mode}: {error}")
            self._end_mode()
            return
        self._slice_rect = result
        self._read_metrics()

    def _read_metrics(self) -> None:
        self._run_js(METRICS_JS, self._on_metrics)

    def _on_metrics(self, metrics) -> None:
        if not self._active:
            return
        if not isinstance(metrics, dict):
            self._errors.append(f"{self._mode}: could not measure the page.")
            self._end_mode()
            return
        self._metrics = metrics
        self._full_height_css = max(1, int(metrics.get("height") or 1))
        self._previous_y = -1
        self._canvas = None
        self._painter = None
        self._scale = 1.0
        self._run_js(
            "window.scrollTo(0, 0); window.scrollY",
            lambda _y: QTimer.singleShot(180, self._grab_strip),
        )

    def _grab_strip(self) -> None:
        if not self._active:
            return
        pixmap = self._view.grab()
        if pixmap.isNull():
            self._errors.append(f"{self._mode}: captured an empty image.")
            self._end_mode()
            return
        img = pixmap.toImage()
        img.setDevicePixelRatio(1.0)
        vh = max(1, int(self._metrics.get("vh") or self._view.height()))
        scale = img.height() / vh if vh else 1.0
        if self._canvas is None:
            self._scale = scale
            total_h = max(1, int(round(self._full_height_css * scale)))
            self._canvas = QImage(img.width(), total_h, QImage.Format.Format_RGB32)
            self._canvas.fill(Qt.GlobalColor.white)
            self._painter = QPainter(self._canvas)
        scroll_y = int(round(float(self._metrics.get("_scroll_y") or 0)))
        y_px = int(round(scroll_y * self._scale))
        if self._painter is not None:
            self._painter.drawImage(QPoint(0, y_px), img)
        self._run_js("window.scrollY", self._after_grab)

    def _after_grab(self, value) -> None:
        if not self._active:
            return
        try:
            scroll_y = int(round(float(value or 0)))
        except (TypeError, ValueError):
            scroll_y = 0
        vh = max(1, int(self._metrics.get("vh") or self._view.height()))
        next_y = scroll_y + vh
        if next_y >= self._full_height_css or scroll_y <= self._previous_y:
            self._end_mode()
            return
        self._previous_y = scroll_y
        self._run_js(
            f"window.scrollTo(0, {next_y}); window.scrollY",
            self._store_scroll,
        )

    def _store_scroll(self, value) -> None:
        try:
            self._metrics["_scroll_y"] = int(round(float(value or 0)))
        except (TypeError, ValueError):
            self._metrics["_scroll_y"] = 0
        QTimer.singleShot(190, self._grab_strip)

    def _end_mode(self) -> None:
        if self._painter is not None:
            self._painter.end()
            self._painter = None
        canvas = self._canvas
        self._canvas = None
        if canvas is not None and not canvas.isNull():
            self._save_image(canvas)
        self._run_js(CLEANUP_JS, lambda _: QTimer.singleShot(60, self._after_mode))

    def _after_mode(self) -> None:
        if self._mode == "phone":
            self._close_phone_view()
            QTimer.singleShot(150, self._next_mode)
        else:
            QTimer.singleShot(50, self._next_mode)

    def _close_phone_view(self) -> None:
        phone = self._phone_view
        self._phone_view = None
        self._view = self.view
        if phone is not None:
            try:
                phone.stop()
                phone.close()
                phone.deleteLater()
            except RuntimeError:
                pass

    def _save_image(self, image: QImage) -> None:
        try:
            self.project.screenshots_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            prefix = "slice" if self._slice_rect else self._mode
            path = (
                self.project.screenshots_dir
                / f"{prefix}_{self._mode}_{stamp}_{int(time.time() * 1000) % 1000:03d}.jpg"
            )
            slice_meta = None
            if self._slice_rect and self._slice_spec:
                padding = int(self._slice_spec.get("padding") or 0)
                element_top = float(self._slice_rect.get("top") or 0.0)
                element_bottom = float(self._slice_rect.get("bottom") or element_top)
                top_css = max(0.0, element_top - padding)
                bottom_css = min(
                    float(self._full_height_css),
                    max(element_top, element_bottom) + padding,
                )
                if bottom_css <= top_css:
                    bottom_css = min(float(self._full_height_css), top_css + 1.0)
                y0 = max(0, int(round(top_css * self._scale)))
                y1 = min(image.height(), int(round(bottom_css * self._scale)))
                if y1 <= y0:
                    y1 = min(image.height(), y0 + 1)
                image = image.copy(0, y0, image.width(), max(1, y1 - y0))
                slice_meta = {
                    "selector": self._slice_spec.get("selector"),
                    "index": self._slice_spec.get("index"),
                    "padding": padding,
                    "top": round(top_css, 1),
                    "bottom": round(bottom_css, 1),
                    "element_top": round(element_top, 1),
                    "element_bottom": round(element_bottom, 1),
                    "element_height": round(float(self._slice_rect.get("height") or 0), 1),
                    "tag": self._slice_rect.get("tag") or "",
                    "text": self._slice_rect.get("text") or "",
                }
            original_w, original_h = image.width(), image.height()
            max_px = max(512, int(self.settings.vision_max_px or 1568))
            scaled = image
            if max(original_w, original_h) > max_px:
                scaled = image.scaled(
                    max_px,
                    max_px,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            if scaled.save(str(path), "JPEG", 86):
                result = {
                    "mode": self._mode,
                    "path": str(path),
                    "width": scaled.width(),
                    "height": scaled.height(),
                    "original_width": original_w,
                    "original_height": original_h,
                    "page_height": self._full_height_css,
                    "viewport": [
                        int(self._metrics.get("vw") or 0),
                        int(self._metrics.get("vh") or 0),
                    ],
                    "title": self._metrics.get("title") or "",
                    "url": self._metrics.get("url") or "",
                }
                if slice_meta:
                    result["slice"] = slice_meta
                if self.console_provider:
                    try:
                        logs = self.console_provider()
                        if logs:
                            if isinstance(logs, str):
                                logs = [line for line in logs.splitlines() if line]
                            result["console"] = list(logs)[-30:]
                    except Exception:
                        pass
                self._results.append(result)
            else:
                self._errors.append(f"{self._mode}: could not save the screenshot.")
        except OSError as exc:
            self._errors.append(f"{self._mode}: could not save the screenshot ({exc}).")

    # ------------------------------------------------------------- finish
    def _finish(self) -> None:
        self._close_phone_view()
        self._view = self.view
        self._slice_spec = None
        self._slice_rect = None
        self._run_js(f"window.scrollTo(0, {self._orig_scroll})", lambda _: None)
        self._run_js(CLEANUP_JS, lambda _: None)
        self._active = False
        callback = self._callback
        self._callback = None
        if callback:
            callback({"results": self._results, "errors": self._errors})

    # -------------------------------------------------------------- utils
    def _run_js(self, code: str, callback) -> None:
        try:
            page = self._view.page()
            if page is None:
                callback(None)
                return
            page.runJavaScript(code, callback)
        except RuntimeError:
            callback(None)
