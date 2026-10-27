"""PHP built-in development server integration."""

from __future__ import annotations

import shutil
import socket
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _LogReader(QThread):
    line = pyqtSignal(str)

    def __init__(self, stream, parent=None) -> None:
        super().__init__(parent)
        self._stream = stream

    def run(self) -> None:
        try:
            for raw in iter(self._stream.readline, ""):
                text = raw.rstrip("\r\n")
                if text:
                    self.line.emit(text)
        except (ValueError, OSError):
            pass


class PhpServer(QObject):
    """Runs ``php -S`` against a project's public ``web/`` directory."""

    started = pyqtSignal(str, int)  # url, port
    stopped = pyqtSignal()
    failed = pyqtSignal(str)
    log_line = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._proc: subprocess.Popen | None = None
        self._reader: _LogReader | None = None
        self._timer: QTimer | None = None
        self._port = 0
        self._web_dir: Path | None = None
        self._logs: deque[str] = deque(maxlen=4000)
        self._lock = threading.Lock()
        self._start_deadline = 0.0

    # ------------------------------------------------------------- state
    @property
    def port(self) -> int:
        return self._port

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._port}/" if self._port else ""

    @property
    def running(self) -> bool:
        return bool(self._proc and self._proc.poll() is None)

    @property
    def log_lines(self) -> list[str]:
        with self._lock:
            return list(self._logs)

    def tail_logs(self, lines: int = 120, query: str = "") -> str:
        with self._lock:
            data = list(self._logs)
        if query:
            needle = query.lower()
            data = [ln for ln in data if needle in ln.lower()]
        data = data[-max(1, min(lines, 1000)):]
        return "\n".join(data)

    # ------------------------------------------------------------- control
    def start(self, project_root: str | Path, web_dir: str | Path) -> None:
        self.stop(emit=False)
        php = shutil.which("php")
        if not php:
            self.failed.emit(
                "PHP was not found on PATH. Install PHP to preview the site; the "
                "editor and chat still work."
            )
            return
        self._web_dir = Path(web_dir)
        self._web_dir.mkdir(parents=True, exist_ok=True)
        self._port = _free_port()
        cmd = [
            php,
            "-S",
            f"127.0.0.1:{self._port}",
            "-t",
            str(self._web_dir),
            "-d",
            "display_errors=1",
            "-d",
            "error_reporting=E_ALL",
        ]
        try:
            self._proc = subprocess.Popen(
                cmd,
                cwd=str(project_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            self._proc = None
            self.failed.emit(f"Could not start PHP server: {exc}")
            return

        self._append_log(f"[app] Started: {' '.join(cmd)}")
        self._reader = _LogReader(self._proc.stdout, self)
        self._reader.line.connect(self._on_log)
        self._reader.start()

        self._start_deadline = time.monotonic() + 6.0
        self._timer = QTimer(self)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._poll_ready)
        self._timer.start()

    def _poll_ready(self) -> None:
        if not self._proc:
            self._stop_timer()
            return
        if self._proc.poll() is not None:
            self._stop_timer()
            self.failed.emit(
                "PHP server exited during startup. Check the server log for details.\n"
                + self.tail_logs(30)
            )
            return
        try:
            with socket.create_connection(("127.0.0.1", self._port), timeout=0.25):
                self._stop_timer()
                self.started.emit(self.url, self._port)
                return
        except OSError:
            pass
        if time.monotonic() > self._start_deadline:
            self._stop_timer()
            self.failed.emit(
                f"PHP server did not become ready on port {self._port}.\n"
                + self.tail_logs(30)
            )

    def _stop_timer(self) -> None:
        if self._timer:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None

    def stop(self, emit: bool = True) -> None:
        self._stop_timer()
        proc = self._proc
        self._proc = None
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2.0)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    proc.kill()
                except OSError:
                    pass
        reader = self._reader
        self._reader = None
        if reader:
            reader.wait(1500)
        if self._port:
            self._append_log(f"[app] Stopped server on port {self._port}")
        self._port = 0
        if emit:
            self.stopped.emit()

    # -------------------------------------------------------------- logging
    def _on_log(self, text: str) -> None:
        self._append_log(text)
        self.log_line.emit(text)

    def _append_log(self, text: str) -> None:
        with self._lock:
            self._logs.append(text.rstrip("\n"))

    def clear_logs(self) -> None:
        with self._lock:
            self._logs.clear()
