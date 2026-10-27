"""New project dialog backed by new_project_dialog.ui."""

from __future__ import annotations

import re
from pathlib import Path

from PyQt6 import uic
from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QFileDialog, QMessageBox


TEMPLATE_KEYS = ["landing", "blank_php", "blank_html"]


class NewProjectDialog(QDialog):
    def __init__(self, default_location: str | None = None, parent=None) -> None:
        super().__init__(parent)
        uic.loadUi(str(Path(__file__).with_name("new_project_dialog.ui")), self)
        self.result_data: dict | None = None
        home = Path.home()
        preferred = default_location or str(home)
        self.location.setText(preferred)
        self.projectName.textChanged.connect(self._update_preview)
        self.location.textChanged.connect(self._update_preview)
        self.browseButton.clicked.connect(self._browse)
        self.buttonBox.accepted.connect(self._accept)
        self.buttonBox.rejected.connect(self.reject)
        ok = self.buttonBox.button(QDialogButtonBox.StandardButton.Ok)
        if ok:
            ok.setText("Create Project")
        self._update_preview()

    def _browse(self) -> None:
        start = self.location.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Choose a parent folder", start)
        if chosen:
            self.location.setText(chosen)

    def _slug(self, name: str) -> str:
        value = re.sub(r"[^\w\-. ]+", "", name, flags=re.UNICODE).strip()
        value = re.sub(r"\s+", "-", value)
        return value or "website"

    def _update_preview(self) -> None:
        name = self.projectName.text().strip()
        location = self.location.text().strip()
        if name and location:
            path = Path(location) / self._slug(name)
            self.pathPreviewLabel.setText(f"Project folder: {path}")
        else:
            self.pathPreviewLabel.setText("")

    def _accept(self) -> None:
        name = self.projectName.text().strip()
        location = self.location.text().strip()
        if not name:
            QMessageBox.warning(self, "New Project", "Enter a project name.")
            return
        if not location:
            QMessageBox.warning(self, "New Project", "Choose a location.")
            return
        parent = Path(location).expanduser()
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "New Project", f"Could not create the folder: {exc}")
            return
        target = parent / self._slug(name)
        if target.exists() and any(target.iterdir()):
            QMessageBox.warning(
                self,
                "New Project",
                f"{target} already exists and is not empty. Choose another name or location.",
            )
            return
        template = TEMPLATE_KEYS[max(0, min(2, self.template.currentIndex()))]
        self.result_data = {
            "name": name,
            "location": str(parent),
            "template": template,
            "slug": self._slug(name),
        }
        self.accept()
