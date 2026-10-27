#!/usr/bin/env python3
"""AI Website Builder entry point.

Copyright (C) 2026 Tiziano Bacocco
SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Must be set before QApplication and before QtWebEngine initialises.
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

# Importing QtWebEngineWidgets before constructing QApplication is required.
import PyQt6.QtWebEngineWidgets  # noqa: E402, F401

from app.config import Settings  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402


APP_DIR = Path(__file__).resolve().parent


def _parse_args(argv: list[str]) -> tuple[str | None, list[str]]:
    project = None
    remaining = [argv[0]]
    index = 1
    while index < len(argv):
        arg = argv[index]
        if arg == "--project" and index + 1 < len(argv):
            project = argv[index + 1]
            index += 2
            continue
        if arg.startswith("--project="):
            project = arg.split("=", 1)[1]
            index += 1
            continue
        remaining.append(arg)
        index += 1
    return project, remaining


def _load_stylesheet(app: QApplication) -> None:
    path = APP_DIR / "app" / "ui" / "style.qss"
    if path.exists():
        try:
            app.setStyleSheet(path.read_text(encoding="utf-8"))
        except OSError:
            pass


def main() -> int:
    project, argv = _parse_args(sys.argv)
    if "--help" in argv or "-h" in argv:
        print("Usage: python main.py [--project PATH]")
        return 0
    settings = Settings.load()
    app = QApplication(argv)
    app.setApplicationName("AI Website Builder")
    app.setApplicationDisplayName("AI Website Builder")
    app.setOrganizationName("AIWebSiteBuilder")
    app.setStyle("Fusion")
    _load_stylesheet(app)
    window = MainWindow(settings, project)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
