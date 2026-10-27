"""Chat transcript widgets (message cards, attachments, streaming)."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QSize, Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QFont, QIcon, QPixmap, QTextCursor
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..utils import format_seconds


MARKDOWN_CSS = """
body { color: #dfe4ec; }
a { color: #7fd6b0; text-decoration: none; }
p { margin: 4px 0; }
h1, h2, h3, h4 { color: #f3f5f8; margin: 10px 0 4px 0; }
code { font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace; }
pre { background-color: #0b0e13; padding: 8px; margin: 6px 0; }
table { border-collapse: collapse; }
th, td { border: 1px solid #2a3341; padding: 4px 8px; }
blockquote { color: #9aa6b8; margin-left: 8px; }
"""

ROLE_TITLES = {
    "user": "YOU",
    "assistant": "ASSISTANT",
    "tool": "TOOL",
    "compaction": "CONTEXT",
}


def _browser(parent: QWidget, *, mono: bool = False, css: str = MARKDOWN_CSS) -> QTextBrowser:
    browser = QTextBrowser(parent)
    browser.setOpenExternalLinks(False)
    browser.setFrameShape(QFrame.Shape.NoFrame)
    browser.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse
        | Qt.TextInteractionFlag.TextSelectableByKeyboard
        | Qt.TextInteractionFlag.LinksAccessibleByMouse
    )
    browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    browser.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    font_name = "JetBrains Mono" if mono else "Inter"
    size = 9 if mono else 10
    browser.document().setDefaultFont(QFont(font_name, size))
    browser.document().setDefaultStyleSheet(css)
    browser.setMinimumHeight(16)
    browser.setText("")
    browser.anchorClicked.connect(lambda url: QDesktopServices.openUrl(url))
    return browser


def _truncate(text: str, limit: int) -> str:
    text = (text or "").replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


class MessageWidget(QFrame):
    def __init__(self, message: dict, project, parent=None) -> None:
        super().__init__(parent)
        self.message = message
        self.project = project
        self.role = str(message.get("role") or "assistant")
        self.setObjectName("messageCard")
        self.setProperty("role", self.role)
        if message.get("error"):
            self.setProperty("error", "true")
        self._content_text = str(message.get("content") or "")
        self._reasoning_text = str(message.get("reasoning") or "")
        self._needs_markdown = bool(message.get("streaming"))
        self._collapsed = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 10, 13, 10)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(8)
        self.role_label = QLabel(self._role_title(), self)
        self.role_label.setObjectName("messageRole")
        self.meta_label = QLabel(self._meta_text(), self)
        self.meta_label.setObjectName("messageMeta")
        header.addWidget(self.role_label)
        header.addWidget(self.meta_label)
        header.addStretch(1)
        layout.addLayout(header)

        # Reasoning / thinking panel
        self.reason_toggle = QToolButton(self)
        self.reason_toggle.setObjectName("reasonButton")
        self.reason_toggle.setText("▾ Thinking")
        self.reason_toggle.setCheckable(True)
        self.reason_toggle.setChecked(True)
        self.reason_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.reason_toggle.setVisible(False)
        self.reason_toggle.toggled.connect(self._toggle_reasoning)
        layout.addWidget(self.reason_toggle, 0, Qt.AlignmentFlag.AlignLeft)

        self.reasoning_browser = _browser(self, mono=True)
        self.reasoning_browser.setObjectName("reasoningBrowser")
        self.reasoning_browser.setVisible(False)
        self.reasoning_browser.setFixedHeight(0)
        layout.addWidget(self.reasoning_browser)

        self.content_browser = _browser(self)
        self.content_browser.setObjectName("messageContent")
        layout.addWidget(self.content_browser)

        self.tool_calls_label = QLabel(self)
        self.tool_calls_label.setObjectName("toolArgs")
        self.tool_calls_label.setWordWrap(True)
        self.tool_calls_label.setVisible(False)
        layout.addWidget(self.tool_calls_label)

        # Tool specifics
        self.output_toggle: QPushButton | None = None
        self.tool_output: QTextBrowser | None = None
        if self.role == "tool":
            self.content_browser.setVisible(False)
            args = str(message.get("arguments") or "").strip()
            if args:
                self.tool_calls_label.setText("args: " + _truncate(args, 300))
                self.tool_calls_label.setVisible(True)
            output_text = str(message.get("content") or "")
            self.tool_output = _browser(self, mono=True)
            self.tool_output.setObjectName("toolOutput")
            self.tool_output.setPlainText(output_text)
            layout.addWidget(self.tool_output)
            long_output = len(output_text) > 1100
            self.output_toggle = QPushButton(self)
            self.output_toggle.setObjectName("attachmentRemove")
            self.output_toggle.setStyleSheet(
                "QPushButton{background:transparent;border:1px solid #2b3543;"
                "color:#9fb0c3;padding:3px 8px;border-radius:5px;font-size:10px;}"
                "QPushButton:hover{background:#1a212b;}"
            )
            self.output_toggle.clicked.connect(self._toggle_output)
            layout.addWidget(self.output_toggle, 0, Qt.AlignmentFlag.AlignLeft)
            if long_output:
                self._collapsed = True
                self.output_toggle.setText(f"Show output ({len(output_text):,} chars)")
                self.tool_output.setVisible(False)
            else:
                self.output_toggle.setVisible(False)
                self._fit_tool_output()

        self.error_label = QLabel(self)
        self.error_label.setObjectName("messageError")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        self.images_widget = None
        if message.get("images"):
            self.images_widget = self._build_images(message["images"])
            layout.addWidget(self.images_widget)

        self._render()

        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(20)
        self._fit_timer.timeout.connect(self._fit_all)
        if self._needs_markdown:
            self.meta_label.setText(self._meta_text() + " · streaming…")

    # ------------------------------------------------------------- render
    def _role_title(self) -> str:
        if self.role == "tool":
            return f"TOOL · {self.message.get('name') or 'tool'}"
        return ROLE_TITLES.get(self.role, self.role.upper())

    def _meta_text(self) -> str:
        parts = []
        ts = self.message.get("ts")
        if ts:
            parts.append(format_seconds(ts))
        usage = self.message.get("usage") or {}
        if usage.get("total_tokens"):
            parts.append(f"{int(usage['total_tokens']):,} tok")
        if self.message.get("stopped"):
            parts.append("stopped")
        return " · ".join(parts)

    def _render(self) -> None:
        text = self._content_text
        if self.role == "assistant" and self._needs_markdown:
            self.content_browser.setPlainText(text)
        elif text:
            self.content_browser.setMarkdown(text)
        else:
            self.content_browser.setPlainText("")
        self.content_browser.setVisible(bool(text) or self._needs_markdown)
        if self.role == "tool":
            self.content_browser.setVisible(False)
        if self._reasoning_text:
            self._show_reasoning(self._reasoning_text)
        else:
            self.reason_toggle.setVisible(False)
            self.reasoning_browser.setVisible(False)
        self._update_tool_calls()
        self._update_error()
        self._schedule_fit()

    def _update_tool_calls(self) -> None:
        calls = self.message.get("tool_calls") or []
        if not calls or self.role != "assistant":
            self.tool_calls_label.setVisible(False)
            return
        names = []
        for call in calls:
            fn = call.get("function") or {}
            names.append(str(fn.get("name") or "tool"))
        self.tool_calls_label.setText("→ " + ", ".join(names))
        self.tool_calls_label.setVisible(True)

    def _update_error(self) -> None:
        error = str(self.message.get("error") or "").strip()
        if error:
            self.error_label.setText(error)
            self.error_label.setVisible(True)
            self.setProperty("error", "true")
            self.style().unpolish(self)
            self.style().polish(self)
        else:
            self.error_label.setVisible(False)

    def _show_reasoning(self, text: str) -> None:
        self.reason_toggle.setVisible(True)
        self.reasoning_browser.setPlainText(text)
        checked = self.reason_toggle.isChecked()
        self.reasoning_browser.setVisible(checked)
        self.reason_toggle.setText(("▾ " if checked else "▸ ") + "Thinking")
        if checked:
            self._fit_reasoning()

    def _toggle_reasoning(self, checked: bool) -> None:
        self.reason_toggle.setText(("▾ " if checked else "▸ ") + "Thinking")
        self.reasoning_browser.setVisible(checked)
        if checked:
            self._fit_reasoning()
        self._schedule_fit()

    def _toggle_output(self) -> None:
        if not self.tool_output or not self.output_toggle:
            return
        self._collapsed = not self._collapsed
        self.tool_output.setVisible(not self._collapsed)
        size = len(str(self.message.get("content") or ""))
        self.output_toggle.setText(
            f"Show output ({size:,} chars)" if self._collapsed else "Hide output"
        )
        if not self._collapsed:
            self._fit_tool_output()
        self._schedule_fit()

    # ------------------------------------------------------------- images
    def _build_images(self, images: list[dict]) -> QWidget:
        holder = QWidget(self)
        grid = QGridLayout(holder)
        grid.setContentsMargins(0, 4, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        thumb_w, thumb_h = (140, 100) if self.role == "user" else (180, 120)
        columns = 3 if self.role == "user" else 2
        for index, image in enumerate(images):
            card = QWidget(holder)
            vbox = QVBoxLayout(card)
            vbox.setContentsMargins(0, 0, 0, 0)
            vbox.setSpacing(3)
            button = QToolButton(card)
            button.setObjectName("attachmentThumb")
            button.setFixedSize(thumb_w, thumb_h)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            button.setIconSize(QSize(thumb_w - 4, thumb_h - 4))
            path = self._image_path(image)
            pixmap = QPixmap(path) if path else QPixmap()
            if pixmap.isNull():
                button.setText("missing\nimage")
            else:
                button.setIcon(QIcon(QPixmap(pixmap).scaled(
                    thumb_w - 6,
                    thumb_h - 6,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )))
            if path:
                button.setToolTip(path)
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.clicked.connect(
                    lambda _checked=False, p=path: QDesktopServices.openUrl(
                        QUrl.fromLocalFile(p)
                    )
                )
            name = str(image.get("name") or Path(str(image.get("path") or "")).name)
            label = QLabel(_truncate(name, 26), card)
            label.setObjectName("attachmentName")
            vbox.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
            vbox.addWidget(label, 0, Qt.AlignmentFlag.AlignLeft)
            grid.addWidget(card, index // columns, index % columns)
        return holder

    def _image_path(self, image: dict) -> str:
        rel = str(image.get("path") or "")
        if not rel:
            return ""
        path = Path(rel)
        if not path.is_absolute():
            path = self.project.root / path
        return str(path) if path.exists() else ""

    # ----------------------------------------------------------- streaming
    def append_delta(self, kind: str, text: str) -> None:
        if not text:
            return
        if kind == "reasoning":
            if not self._reasoning_text:
                self.reason_toggle.setChecked(True)
                self.reason_toggle.setVisible(True)
                self.reasoning_browser.setVisible(True)
            self._reasoning_text += text
            self._append_plain(self.reasoning_browser, text)
            self._fit_reasoning()
        else:
            self._content_text += text
            if not self.content_browser.isVisible():
                self.content_browser.setVisible(True)
            self._append_plain(self.content_browser, text)
            self._fit_content()
        self._schedule_fit()

    def _append_plain(self, browser: QTextBrowser, text: str) -> None:
        cursor = QTextCursor(browser.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        browser.setTextCursor(cursor)
        browser.ensureCursorVisible()

    def finalize(self, message: dict) -> None:
        self.message = message
        self._content_text = str(message.get("content") or "")
        self._reasoning_text = str(message.get("reasoning") or "")
        self._needs_markdown = False
        self.meta_label.setText(self._meta_text())
        self._render()
        if self.role == "tool" and self.tool_output is not None:
            text = str(message.get("content") or "")
            self.tool_output.setPlainText(text)
            if self.output_toggle and not self._collapsed:
                self.output_toggle.setVisible(len(text) > 1100)
                self.output_toggle.setText(
                    f"Show output ({len(text):,} chars)" if self._collapsed else "Hide output"
                )
            self._fit_tool_output()
        self._schedule_fit()

    # ---------------------------------------------------------------- fit
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._schedule_fit()

    def _schedule_fit(self) -> None:
        if hasattr(self, "_fit_timer"):
            self._fit_timer.start()

    def _available_width(self) -> int:
        return max(120, self.width() - 30)

    def _fit_all(self) -> None:
        if self.content_browser.isVisible():
            self._fit_content()
        if self.reasoning_browser.isVisible():
            self._fit_reasoning()
        if self.tool_output is not None and self.tool_output.isVisible():
            self._fit_tool_output()

    def _fit_content(self) -> None:
        self._fit(self.content_browser, max_height=None)

    def _fit_reasoning(self) -> None:
        self._fit(self.reasoning_browser, max_height=320)

    def _fit_tool_output(self) -> None:
        if self.tool_output is not None:
            self._fit(self.tool_output, max_height=420)

    def _fit(self, browser: QTextBrowser, max_height: int | None) -> None:
        if not browser.isVisible():
            return
        width = self._available_width()
        document = browser.document()
        document.setTextWidth(width)
        height = document.size().height()
        if max_height:
            wanted = int(min(max_height, height + 8))
            browser.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAsNeeded if height + 8 > max_height
                else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
        else:
            wanted = int(height + 8)
            browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        wanted = max(18, wanted)
        if abs(browser.height() - wanted) > 1:
            browser.setFixedHeight(wanted)


class CompactionWidget(QFrame):
    def __init__(self, message: dict, project, parent=None) -> None:
        super().__init__(parent)
        self.message = message
        self.setObjectName("messageCard")
        self.setProperty("role", "compaction")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(13, 8, 13, 8)
        layout.setSpacing(8)
        label = QLabel(
            f"◆ Context compacted — {int(message.get('count') or 0)} earlier messages summarised",
            self,
        )
        label.setObjectName("compactionLabel")
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addStretch(1)
        button = QPushButton("View summary", self)
        button.setObjectName("compactionButton")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(self._show_summary)
        layout.addWidget(button)
        self.setToolTip(str(message.get("summary") or ""))

    def _show_summary(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Compacted context")
        box.setText("Earlier messages were summarised to save context.")
        box.setDetailedText(str(self.message.get("summary") or ""))
        box.exec()


class MessageList(QWidget):
    def __init__(self, scroll_area: QScrollArea, project, parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self.setObjectName("messagesHost")
        self._scroll = scroll_area.verticalScrollBar()
        self._stick = True
        self._widgets: dict[str, QWidget] = {}

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(14, 14, 14, 14)
        self._layout.setSpacing(10)
        self._layout.addStretch(1)
        self._empty = QLabel(
            "Describe the website you want to build.\n\n"
            "The agent can read and edit project files, search the web, run the PHP preview, "
            "inspect server logs and look at full-page desktop and phone screenshots.",
            self,
        )
        self._empty.setObjectName("emptyChatLabel")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)
        self._layout.insertWidget(0, self._empty)

        scroll_area.setWidget(self)
        scroll_area.setWidgetResizable(True)
        self._scroll.valueChanged.connect(self._on_scroll_value)
        self._scroll.rangeChanged.connect(self._on_range_changed)

    # ------------------------------------------------------------ population
    def clear(self) -> None:
        self._widgets.clear()
        while self._layout.count() > 2:
            item = self._layout.takeAt(1)
            widget = item.widget()
            if widget is not None and widget is not self._empty:
                widget.deleteLater()
        self._empty.setVisible(True)
        self._stick = True

    def rebuild(self, messages: list[dict]) -> None:
        self.clear()
        for message in messages:
            self.add_message(message)
        self.scroll_to_bottom(force=True)

    def add_message(self, message: dict) -> QWidget | None:
        role = message.get("role")
        if role == "compaction":
            widget: QWidget = CompactionWidget(message, self.project, self)
        else:
            widget = MessageWidget(message, self.project, self)
        index = max(1, self._layout.count() - 1)
        self._layout.insertWidget(index, widget)
        if message.get("id"):
            self._widgets[str(message["id"])] = widget
        self._empty.setVisible(False)
        if self._stick:
            QTimer.singleShot(0, self.scroll_to_bottom)
        return widget

    def update_message(self, message: dict) -> None:
        widget = self._widgets.get(str(message.get("id") or ""))
        if isinstance(widget, MessageWidget):
            widget.finalize(message)
            if self._stick:
                QTimer.singleShot(0, self.scroll_to_bottom)

    def append_delta(self, message_id: str, kind: str, text: str) -> None:
        widget = self._widgets.get(message_id)
        if isinstance(widget, MessageWidget):
            widget.append_delta(kind, text)

    # ------------------------------------------------------------- scrolling
    def _on_scroll_value(self, value: int) -> None:
        self._stick = value >= self._scroll.maximum() - 60

    def _on_range_changed(self, _minimum: int, maximum: int) -> None:
        if self._stick:
            self._scroll.setValue(maximum)

    def scroll_to_bottom(self, force: bool = False) -> None:
        if force:
            self._stick = True
        self._scroll.setValue(self._scroll.maximum())
