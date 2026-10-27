"""PHP server log dialog backed by log_dialog.ui."""

from __future__ import annotations

from pathlib import Path

from PyQt6 import uic
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QDialog


class LogDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        uic.loadUi(str(Path(__file__).with_name("log_dialog.ui")), self)
        self.serverLogEdit.setMaximumBlockCount(6000)
        self.clearButton.clicked.connect(self._clear)
        self.buttonBox.rejected.connect(self.close)
        self.buttonBox.accepted.connect(self.close)
        self._clear_callback = None

    def set_clear_callback(self, callback) -> None:
        self._clear_callback = callback

    def _clear(self) -> None:
        self.serverLogEdit.clear()
        if self._clear_callback:
            try:
                self._clear_callback()
            except Exception:
                pass

    def append(self, text: str) -> None:
        if not text:
            return
        bar = self.serverLogEdit.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 20
        self.serverLogEdit.appendPlainText(text)
        if at_bottom:
            bar.setValue(bar.maximum())

    def set_lines(self, lines: list[str]) -> None:
        self.serverLogEdit.setPlainText("\n".join(lines))
        self.serverLogEdit.moveCursor(QTextCursor.MoveOperation.End)

    def set_status(self, text: str) -> None:
        self.logStatusLabel.setText(text)
