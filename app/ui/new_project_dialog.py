"""New project dialog backed by new_project_dialog.ui."""

from __future__ import annotations

import os
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
        self.webDir.textChanged.connect(self._update_preview)
        self.useExistingWeb.toggled.connect(self._toggle_web)
        self.browseButton.clicked.connect(self._browse)
        self.browseWebButton.clicked.connect(self._browse_web)
        self.buttonBox.accepted.connect(self._accept)
        self.buttonBox.rejected.connect(self.reject)
        ok = self.buttonBox.button(QDialogButtonBox.StandardButton.Ok)
        if ok:
            ok.setText("Create Project")
        self._toggle_web(False)
        self._update_preview()

    def _toggle_web(self, checked: bool) -> None:
        self.webDir.setEnabled(checked)
        self.browseWebButton.setEnabled(checked)
        self.template.setEnabled(not checked)
        self._update_preview()

    def _browse_web(self) -> None:
        start = self.webDir.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose an existing public web directory", start
        )
        if chosen:
            self.webDir.setText(chosen)
            self.useExistingWeb.setChecked(True)
            self._update_preview()

    def _browse(self) -> None:
        start = self.location.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Choose a parent folder", start)
        if chosen:
            self.location.setText(chosen)

    def _slug(self, name: str) -> str:
        value = re.sub(r"[^\w\-. ]+", "", name, flags=re.UNICODE).strip()
        value = re.sub(r"\s+", "-", value).strip(".")
        if not value or set(value) <= {"."}:
            return "website"
        return value

    def _update_preview(self) -> None:
        name = self.projectName.text().strip()
        location = self.location.text().strip()
        if name and location:
            path = Path(location) / self._slug(name)
            text = f"Project folder: {path}"
            if self.useExistingWeb.isChecked():
                web = self.webDir.text().strip()
                text += "\nPublic root: " + (web or "(choose an existing directory)")
            self.pathPreviewLabel.setText(text)
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
        target = (parent / self._slug(name)).resolve()
        if target.exists() and any(target.iterdir()):
            QMessageBox.warning(
                self,
                "New Project",
                f"{target} already exists and is not empty. Choose another name or location.",
            )
            return
        web_dir = None
        if self.useExistingWeb.isChecked():
            raw_web = self.webDir.text().strip()
            if not raw_web:
                QMessageBox.warning(
                    self, "New Project", "Choose an existing public web directory."
                )
                return
            candidate = Path(raw_web).expanduser().resolve()
            if not candidate.is_dir():
                QMessageBox.warning(
                    self,
                    "New Project",
                    f"The public web directory does not exist:\n{candidate}",
                )
                return
            if not os.access(candidate, os.W_OK):
                QMessageBox.warning(
                    self,
                    "New Project",
                    f"The public web directory is not writable:\n{candidate}",
                )
                return
            if candidate == target or target.is_relative_to(candidate):
                QMessageBox.warning(
                    self,
                    "New Project",
                    "The public web directory cannot be the project folder or contain it.",
                )
                return
            if candidate.is_relative_to(target) and candidate != (target / "web"):
                QMessageBox.warning(
                    self,
                    "New Project",
                    "A custom public web directory must be outside the project folder "
                    "(or use the default web/ directory).",
                )
                return
            web_dir = str(candidate)
        template = TEMPLATE_KEYS[max(0, min(2, self.template.currentIndex()))]
        self.result_data = {
            "name": name,
            "location": str(parent),
            "template": template,
            "slug": self._slug(name),
            "web_dir": web_dir,
        }
        self.accept()
