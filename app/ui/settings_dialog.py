"""Settings dialog backed by settings_dialog.ui."""

from __future__ import annotations

import copy

from PyQt6 import uic
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import QDialog, QMessageBox

from ..chat.client import CompleteWorker
from ..config import Settings


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        uic.loadUi(str(self._ui_path()), self)
        self.settings = settings
        self._test_worker: CompleteWorker | None = None
        self._load()

        self.buttonBox.accepted.connect(self._save)
        self.buttonBox.rejected.connect(self.reject)
        self.testButton.clicked.connect(self._test_connection)

    @staticmethod
    def _ui_path():
        from pathlib import Path

        return Path(__file__).with_name("settings_dialog.ui")

    def _load(self) -> None:
        s = self.settings
        self.apiBaseUrl.setText(s.api_base)
        self.apiKey.setText(s.api_key)
        self.model.setText(s.model)
        self.contextLength.setValue(int(s.context_limit))
        self.timeout.setValue(int(s.timeout))
        self.compactionThreshold.setValue(int(s.compaction_threshold))
        self.maxToolRounds.setValue(int(s.max_tool_rounds))
        self.visionMaxPx.setValue(int(s.vision_max_px))

    def refresh(self) -> None:
        self._load()
        self.testStatus.setText("")
        self.testStatus.setStyleSheet("")
        self.testButton.setEnabled(True)

    def _apply_to(self, target: Settings) -> None:
        target.api_base = self.apiBaseUrl.text().strip()
        target.api_key = self.apiKey.text().strip()
        target.model = self.model.text().strip()
        target.context_limit = int(self.contextLength.value())
        target.timeout = int(self.timeout.value())
        target.compaction_threshold = int(self.compactionThreshold.value())
        target.max_tool_rounds = int(self.maxToolRounds.value())
        target.vision_max_px = int(self.visionMaxPx.value())

    def _validate(self) -> bool:
        base = self.apiBaseUrl.text().strip()
        if not base:
            QMessageBox.warning(self, "Settings", "Enter an API base URL.")
            return False
        url = QUrl(base)
        if not url.isValid() or url.scheme() not in ("http", "https"):
            QMessageBox.warning(
                self,
                "Settings",
                "The API base URL must start with http:// or https://",
            )
            return False
        if not self.model.text().strip():
            QMessageBox.warning(self, "Settings", "Enter a model name.")
            return False
        return True

    def _save(self) -> None:
        if not self._validate():
            return
        self._apply_to(self.settings)
        try:
            self.settings.save()
        except OSError as exc:
            QMessageBox.warning(self, "Settings", f"Could not save settings: {exc}")
            return
        self.accept()

    def _test_connection(self) -> None:
        if self._test_worker is not None:
            return
        if not self._validate():
            return
        candidate = copy.deepcopy(self.settings)
        self._apply_to(candidate)
        self.testStatus.setStyleSheet("color:#8b95a6;")
        self.testStatus.setText("Testing connection…")
        self.testButton.setEnabled(False)
        worker = CompleteWorker(
            candidate,
            [
                {
                    "role": "user",
                    "content": "This is a connection test. Reply with a short greeting.",
                }
            ],
            self,
        )
        self._test_worker = worker
        worker.completed.connect(self._test_ok)
        worker.failed.connect(self._test_failed)
        worker.finished.connect(lambda w=worker: self._test_finished(w))
        worker.start()

    def _test_ok(self, text: str, _usage) -> None:
        preview = (text or "").strip().replace("\n", " ")[:160]
        self.testStatus.setStyleSheet("color:#63d2a4;")
        self.testStatus.setText(f"✓ Connected — model replied: {preview}")

    def _test_failed(self, message: str) -> None:
        self.testStatus.setStyleSheet("color:#ff8f8f;")
        self.testStatus.setText("✗ " + (message or "Connection failed.")[:300])

    def _test_finished(self, worker) -> None:
        if self._test_worker is worker:
            self._test_worker = None
        self.testButton.setEnabled(True)
        worker.deleteLater()

    def shutdown(self) -> None:
        worker = self._test_worker
        if worker is not None and worker.isRunning():
            worker.cancel()
            if not worker.wait(1500):
                try:
                    worker.terminate()
                    worker.wait(500)
                except RuntimeError:
                    pass
        self._test_worker = None
