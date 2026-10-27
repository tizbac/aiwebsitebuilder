"""Main application window: chat, project management and live preview."""

from __future__ import annotations

import html
import json
import re
import time
import uuid
from collections import deque
from pathlib import Path
from urllib.parse import quote

from PyQt6 import uic
from PyQt6.QtCore import QEvent, QSettings, QSize, Qt, QTimer, QUrl
from PyQt6.QtGui import QColor, QDesktopServices, QFontMetrics, QIcon, QKeySequence, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..capture import PageCapture
from ..chat.agent import ChatAgent
from ..config import Settings
from ..php_server import PhpServer
from ..project import Project, ProjectError
from ..utils import human_tokens, import_attachment, save_clipboard_image
from .chat_view import MessageList
from .log_dialog import LogDialog
from .new_project_dialog import NewProjectDialog
from .search_view import DuckDuckGoSearch
from .settings_dialog import SettingsDialog
from .webview import ConsoleWebView, LEVEL_NAMES


UI_FILE = Path(__file__).with_name("main_window.ui")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}

WELCOME_PAGE = """
<!doctype html><html><head><meta charset="utf-8"><style>
  html, body { height: 100%; margin: 0; background: #0b0e13; color: #dfe4ec;
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
  .wrap { height: 100%; display: flex; flex-direction: column; align-items: center;
    justify-content: center; text-align: center; padding: 40px; box-sizing: border-box; }
  .mark { width: 54px; height: 54px; border-radius: 16px; display: flex;
    align-items: center; justify-content: center; font-size: 27px; margin-bottom: 22px;
    color: #0d1511; background: #63d2a4; box-shadow: 0 0 60px rgba(99,210,164,.25); }
  h1 { font-size: 22px; font-weight: 600; margin: 0 0 10px; color: #f3f5f8; }
  p { margin: 0 0 8px; color: #7d8b9f; font-size: 13px; line-height: 1.7; max-width: 430px; }
  .hint { margin-top: 22px; color: #566274; font-size: 11px; letter-spacing: .02em; }
  kbd { background: #151b24; border: 1px solid #293344; border-radius: 5px;
    padding: 2px 6px; color: #aeb9c9; font-family: ui-monospace, monospace; font-size: 10px; }
</style></head><body><div class="wrap">
  <div class="mark">◇</div>
  <h1>No preview yet</h1>
  <p>Create a new website project or open an existing one. The project gets a public
     <code>web/</code> folder, a PHP development server on a random port, and a live preview here.</p>
  <p>The agent can build the site, inspect PHP logs, drive the preview, and capture
     full-page desktop and phone screenshots for itself.</p>
  <p class="hint">File → New Project &nbsp;·&nbsp; or press <kbd>Ctrl</kbd> + <kbd>N</kbd></p>
</div></body></html>
"""

STARTING_PAGE = """
<!doctype html><html><head><meta charset="utf-8"><style>
  html, body { height: 100%; margin: 0; background: #0b0e13; color: #6f7f94;
    font-family: system-ui, sans-serif; }
  .wrap { height: 100%; display: flex; align-items: center; justify-content: center;
    flex-direction: column; gap: 10px; }
  .dot { width: 34px; height: 34px; border: 2px solid #26303d;
    border-top-color: #63d2a4; border-radius: 50%; animation: spin 1s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  p { font-size: 12px; letter-spacing: .04em; }
</style></head><body><div class="wrap"><div class="dot"></div><p>Starting the PHP preview…</p></div></body></html>
"""

ERROR_PAGE = """
<!doctype html><html><head><meta charset="utf-8"><style>
  body {{ margin:0; min-height:100vh; display:flex; align-items:center;
    justify-content:center; background:#0e1014; color:#dfe4ec;
    font-family:system-ui,-apple-system,sans-serif; }}
  .box {{ max-width:640px; padding:40px; }}
  h1 {{ font-size:22px; font-weight:600; margin:0 0 12px; color:#f3f5f8; }}
  pre {{ background:#12161d; border:1px solid #232b37; padding:14px; border-radius:10px;
    white-space:pre-wrap; color:#ffb3b3; font-size:12px; }}
  p {{ color:#8b95a6; line-height:1.6; }}
</style></head><body><div class="box">
  <h1>Preview unavailable</h1>
  <p>The PHP development server could not be started or the page failed to load.</p>
  <pre>{message}</pre>
  <p>Check that <code>php</code> is installed and on PATH, then use
  <b>Project → Restart PHP Server</b>.</p>
</div></body></html>
"""


CLICK_JS = """
(function () {
  var selector = __SELECTOR__;
  var index = __INDEX__;
  try {
    var nodes = document.querySelectorAll(selector);
    if (!nodes.length) {
      return {ok: false, error: "No element matches selector: " + selector};
    }
    if (index < 0 || index >= nodes.length) {
      return {ok: false, error: "Index " + index + " is out of range; " +
        nodes.length + " element(s) matched."};
    }
    var el = nodes[index];
    var before = location.href;
    try { el.scrollIntoView({block: 'center', inline: 'nearest'}); } catch (e) {}
    try { el.focus({preventScroll: true}); } catch (e) {}
    el.click();
    return {
      ok: true,
      tag: el.tagName ? el.tagName.toLowerCase() : 'unknown',
      element: (el.innerText || el.value || el.getAttribute('aria-label') || '')
        .trim().slice(0, 400),
      count: nodes.length,
      url: location.href,
      title: document.title,
      navigation: location.href !== before
    };
  } catch (e) {
    return {ok: false, error: String(e)};
  }
})()
"""


FILL_JS = """
(function () {
  var selector = __SELECTOR__;
  var index = __INDEX__;
  var value = __VALUE__;
  var submit = __SUBMIT__;
  try {
    var nodes = document.querySelectorAll(selector);
    if (!nodes.length) {
      return {ok: false, error: "No element matches selector: " + selector};
    }
    if (index < 0 || index >= nodes.length) {
      return {ok: false, error: "Index " + index + " is out of range; " +
        nodes.length + " element(s) matched."};
    }
    var el = nodes[index];
    var tag = el.tagName ? el.tagName.toLowerCase() : '';
    var kind = el.type || '';
    if (kind === 'checkbox' || kind === 'radio') {
      var desired = value === true || /^(true|1|yes|on|checked)$/i.test(String(value));
      if (kind === 'radio') {
        if (desired) {
          if (!el.checked) el.click();
        } else if (el.checked) {
          el.checked = false;
        }
      } else if (el.checked !== desired) {
        el.click();
      }
    } else if (tag === 'select') {
      el.value = String(value);
      if (el.value !== String(value)) {
        for (var i = 0; i < el.options.length; i++) {
          if (el.options[i].text.trim() === String(value)) {
            el.selectedIndex = i;
            break;
          }
        }
      }
    } else if (el.isContentEditable) {
      el.textContent = String(value);
    } else {
      var proto = (tag === 'textarea') ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype;
      var desc = Object.getOwnPropertyDescriptor(proto, 'value');
      if (desc && desc.set) { desc.set.call(el, String(value)); }
      else { el.value = String(value); }
    }
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
    try { el.focus({preventScroll: true}); } catch (e) {}
    var submitted = false;
    if (submit) {
      var form = el.form || el.closest('form');
      if (form && form.requestSubmit) { form.requestSubmit(); submitted = true; }
      else if (form) { form.submit(); submitted = true; }
      else {
        el.dispatchEvent(new KeyboardEvent('keydown',
          {key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true}));
      }
    }
    return {
      ok: true,
      tag: tag,
      type: kind,
      count: nodes.length,
      submitted: submitted,
      value: (el.value !== undefined && el.value !== null)
        ? String(el.value) : String(value),
      checked: (typeof el.checked === 'boolean') ? el.checked : null,
      url: location.href,
      title: document.title
    };
  } catch (e) {
    return {ok: false, error: String(e)};
  }
})()
"""


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, project_path: str | None = None) -> None:
        super().__init__()
        uic.loadUi(str(UI_FILE), self)
        self.settings = settings
        self._startup_project = project_path
        self.project: Project | None = None
        self.agent: ChatAgent | None = None
        self.chat_view: MessageList | None = None
        self.page_capture: PageCapture | None = None
        self.log_dialog: LogDialog | None = None
        self.server = PhpServer(self)
        self.search_engine = DuckDuckGoSearch(self)
        self._pending_attachments: list[str] = []
        self._pending_load: dict | None = None
        self._reload_pending = False
        self._capture_after_reload = None
        self._reload_fallback: QTimer | None = None
        self._retired_agents: list[tuple[ChatAgent, float]] = []
        self._app_logs: deque[str] = deque(maxlen=400)
        self._settings_open = False
        self._settings_dialog: SettingsDialog | None = None
        self._current_url = ""
        self._qt_settings = QSettings("AIWebSiteBuilder", "AIWebSiteBuilder")

        self._build_webview()
        self._wire_actions()
        self._setup_statusbar()
        self._restore_window_state()
        self._refresh_recent_menu()
        self._set_project_ui(False)
        self._show_welcome()
        self._on_usage(None)
        self._set_icons()

        QTimer.singleShot(40, self._startup_open)

    # ------------------------------------------------------------ construction
    def _build_webview(self) -> None:
        self.webview = ConsoleWebView(self)
        try:
            self.webview.page().setBackgroundColor(QColor("#0b0e13"))
        except AttributeError:
            pass
        self.webview.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.webContainerLayout.addWidget(self.webview)
        self.webLayout.setStretch(0, 0)
        self.webLayout.setStretch(1, 1)
        self.webview.urlChanged.connect(self._on_url_changed)
        self.webview.loadFinished.connect(self._on_load_finished)
        self.webview.console_message.connect(self._on_console_message)
        self.webview.titleChanged.connect(self._on_title_changed)
        try:
            self.webview.page().renderProcessTerminated.connect(
                self._on_render_process_terminated
            )
        except (AttributeError, TypeError):
            pass

    def _wire_actions(self) -> None:
        self.actionNewProject.triggered.connect(self.new_project)
        self.actionOpenProject.triggered.connect(self.open_project_dialog)
        self.actionExit.triggered.connect(self.close)
        self.actionSettings.triggered.connect(self.open_settings)
        self.actionReload.triggered.connect(self.reload_preview)
        self.actionOpenExternal.triggered.connect(self.open_external)
        self.actionRestartServer.triggered.connect(self.restart_server)
        self.actionServerLog.triggered.connect(self.show_server_log)
        self.actionCompact.triggered.connect(self.compact_context)
        self.actionClearChat.triggered.connect(self.clear_chat)
        self.actionStop.triggered.connect(self.stop_agent)
        self.actionAbout.triggered.connect(self.show_about)

        self.sendButton.clicked.connect(self.send_message)
        self.stopButton.clicked.connect(self.stop_agent)
        self.attachButton.clicked.connect(self.attach_images)
        self.backButton.clicked.connect(self.webview.back)
        self.forwardButton.clicked.connect(self.webview.forward)
        self.reloadButton.clicked.connect(self.reload_preview)
        self.openExternalButton.clicked.connect(self.open_external)

        self.addressEdit.returnPressed.connect(self._navigate_address)
        self.inputEdit.installEventFilter(self)
        self.setAcceptDrops(True)

        self.server.started.connect(self._on_server_started)
        self.server.failed.connect(self._on_server_failed)
        self.server.stopped.connect(self._on_server_stopped)
        self.server.log_line.connect(self._on_server_log)

    def _setup_statusbar(self) -> None:
        self.activityLabel = QLabel("Ready", self)
        self.serverStatusLabel = QLabel("PHP: not running", self)
        self.modelStatusLabel = QLabel(self.settings.model or "No model", self)
        self.serverStatusLabel.setToolTip("PHP built-in development server")
        self.modelStatusLabel.setToolTip("Configured model (Settings)")
        self.statusbar.addWidget(self.activityLabel, 1)
        self.statusbar.addPermanentWidget(self.modelStatusLabel)
        self.statusbar.addPermanentWidget(self.serverStatusLabel)

    def _set_icons(self) -> None:
        style = self.style()
        mapping = {
            self.actionNewProject: QStyle.StandardPixmap.SP_FileDialogNewFolder,
            self.actionOpenProject: QStyle.StandardPixmap.SP_DirOpenIcon,
            self.actionReload: QStyle.StandardPixmap.SP_BrowserReload,
            self.actionSettings: QStyle.StandardPixmap.SP_FileDialogDetailedView,
            self.actionCompact: QStyle.StandardPixmap.SP_DialogResetButton,
            self.actionStop: QStyle.StandardPixmap.SP_MediaStop,
        }
        for action, pixmap in mapping.items():
            try:
                action.setIcon(style.standardIcon(pixmap))
            except Exception:
                pass

    def _restore_window_state(self) -> None:
        geometry = self._qt_settings.value("geometry")
        if geometry is not None:
            try:
                self.restoreGeometry(geometry)
            except TypeError:
                pass
        state = self._qt_settings.value("windowState")
        if state is not None:
            try:
                self.restoreState(state)
            except TypeError:
                pass
        sizes = self._qt_settings.value("splitter")
        if sizes:
            try:
                self.mainSplitter.setSizes([int(v) for v in sizes])
            except (TypeError, ValueError):
                self.mainSplitter.setSizes([580, 900])
        else:
            self.mainSplitter.setSizes([580, 900])

    def _save_window_state(self) -> None:
        self._qt_settings.setValue("geometry", self.saveGeometry())
        self._qt_settings.setValue("windowState", self.saveState())
        self._qt_settings.setValue("splitter", self.mainSplitter.sizes())

    # --------------------------------------------------------------- startup
    def _startup_open(self) -> None:
        if self._startup_project:
            self.open_project_path(self._startup_project)
            return
        for path in list(self.settings.recent_projects):
            if Path(path).is_dir():
                self.open_project_path(path, silent=True)
                return
            self.settings.remove_recent(path)
        self._refresh_recent_menu()
        self._show_welcome()

    def _show_welcome(self) -> None:
        host = QWidget(self)
        host.setObjectName("messagesHost")
        host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(host)
        layout.setContentsMargins(32, 40, 32, 40)
        label = QLabel(
            "Welcome to AI Website Builder.\n\n"
            "Create a new project with File → New Project, or open an existing project.\n\n"
            "The agent can read and edit files, search the web, inspect PHP server logs, "
            "navigate the preview and capture full-page desktop + phone screenshots for itself.\n\n"
            "Configure your OpenAI-compatible endpoint in Settings (Ctrl+,).",
            host,
        )
        label.setObjectName("emptyChatLabel")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        layout.addStretch(1)
        layout.addWidget(label)
        layout.addStretch(2)
        self.messagesScroll.setWidget(host)
        self.chat_view = None
        self.projectNameLabel.setText("No project open")
        self.projectPathLabel.setText("Create or open a project to begin")
        self.projectPathLabel.setToolTip("")
        self.addressEdit.clear()
        self.addressEdit.setPlaceholderText("No preview — open a project")
        self.addressEdit.setToolTip("")
        self.setWindowTitle("AI Website Builder")
        self.contextLabel.setText("Context —")
        self.contextBar.setValue(0)
        self._current_url = ""
        self.webview.setHtml(WELCOME_PAGE)

    # --------------------------------------------------------------- projects
    def new_project(self) -> None:
        if not self._confirm_stop("create a new project"):
            return
        default_location = self._qt_settings.value("lastParent", str(Path.home()))
        dialog = NewProjectDialog(str(default_location), self)
        if dialog.exec() != NewProjectDialog.DialogCode.Accepted or not dialog.result_data:
            return
        data = dialog.result_data
        try:
            project = Project.create(data["location"], data["name"], data["template"])
        except (ProjectError, OSError) as exc:
            QMessageBox.critical(self, "New Project", f"Could not create the project:\n{exc}")
            return
        self._qt_settings.setValue("lastParent", data["location"])
        self.open_project_path(str(project.root))

    def open_project_dialog(self) -> None:
        if not self._confirm_stop("open another project"):
            return
        start = str(self._qt_settings.value("lastParent", str(Path.home())))
        chosen = QFileDialog.getExistingDirectory(
            self, "Open project folder (must contain web/)", start
        )
        if chosen:
            self._qt_settings.setValue("lastParent", str(Path(chosen).parent))
            self.open_project_path(chosen)

    def open_project_path(self, path: str, silent: bool = False) -> None:
        try:
            project = Project(path)
            project.load_meta()
            project.ensure_dirs()
        except (ProjectError, OSError) as exc:
            self.settings.remove_recent(path)
            self._refresh_recent_menu()
            if silent:
                self._append_log(f"[app] Could not open recent project: {exc}")
                self._show_welcome()
            else:
                QMessageBox.warning(self, "Open Project", str(exc))
            return

        self._close_current_project()
        self.project = project
        self.settings.add_recent(str(project.root))
        self._refresh_recent_menu()
        self._qt_settings.setValue("lastParent", str(project.root.parent))

        self._setup_agent()
        self._setup_capture()
        self._set_project_ui(True)
        self._update_project_header()
        self.setWindowTitle(f"{project.name} — AI Website Builder")
        self.webview.clear_console()
        self.webview.setHtml(STARTING_PAGE)
        self._current_url = ""
        self.addressEdit.setText("Starting preview…")
        self.serverStatusLabel.setText("PHP: starting…")
        self._set_activity("Starting PHP server…")
        self.server.start(project.root, project.web_dir)
        self.inputEdit.setFocus()

    def _setup_agent(self) -> None:
        if not self.project:
            return
        self.agent = ChatAgent(self.settings, self.project, self.server, self)
        self.agent.capture_handler = self._capture_for_agent
        self.agent.slice_handler = self._slice_for_agent
        self.agent.webview_load_handler = self._load_for_agent
        self.agent.browser_action_handler = self._browser_action_for_agent
        self.agent.search_handler = self._search_for_agent
        self.agent.message_added.connect(self._on_message_added)
        self.agent.message_delta.connect(self._on_message_delta)
        self.agent.message_updated.connect(self._on_message_updated)
        self.agent.busy_changed.connect(self._on_busy_changed)
        self.agent.status.connect(self._on_agent_status)
        self.agent.error.connect(self._on_agent_error)
        self.agent.usage_changed.connect(self._on_usage)
        self.agent.compaction_started.connect(self._on_compaction_started)
        self.agent.compaction_finished.connect(self._on_compaction_finished)
        self.agent.history_reloaded.connect(self._rebuild_chat)
        self.agent.configuration_required.connect(self._on_configuration_required)

        self.chat_view = MessageList(self.messagesScroll, self.project, self)
        self.chat_view.rebuild(self.agent.history.messages)
        if self.agent.history.error:
            self._on_agent_error(self.agent.history.error)
        self._on_usage(self.agent.usage_snapshot())
        self._on_busy_changed(False)

    def _setup_capture(self) -> None:
        if not self.project:
            return
        self.page_capture = PageCapture(
            self.webview,
            self.project,
            self.settings,
            console_provider=lambda: self.webview.console_text(
                errors_only=True, limit=30
            ),
            parent=self,
        )

    def _close_current_project(self) -> None:
        self._reload_pending = False
        self._capture_after_reload = None
        if self._reload_fallback is not None:
            self._reload_fallback.stop()
            self._reload_fallback.deleteLater()
            self._reload_fallback = None
        self._reload_pending = False
        self._capture_after_reload = None
        if self._reload_fallback is not None:
            self._reload_fallback.stop()
            self._reload_fallback.deleteLater()
            self._reload_fallback = None
        if self._pending_load is not None:
            timer = self._pending_load.get("timer")
            if timer:
                timer.stop()
            self._pending_load = None
        if self.page_capture is not None:
            self.page_capture.abort()
            QTimer.singleShot(3000, self.page_capture.deleteLater)
            self.page_capture = None
        self.search_engine.close()
        if self.agent is not None:
            try:
                self.agent.disconnect()
            except (TypeError, RuntimeError):
                pass
            self.agent.shutdown()
            self._retired_agents.append((self.agent, time.monotonic()))
            self.agent = None
        self.server.stop(emit=False)
        self._clear_attachments()
        self._prune_retired_agents()
        self.chat_view = None
        self.project = None
        self._current_url = ""

    def _prune_retired_agents(self) -> None:
        remaining: list[tuple[ChatAgent, float]] = []
        now = time.monotonic()
        for agent, retired_at in self._retired_agents:
            # Keep a grace period for main-thread callbacks (click/fill/search)
            # that are not represented by a QThread.
            if agent.has_running_workers() or (now - retired_at) < 35.0:
                remaining.append((agent, retired_at))
            else:
                agent.deleteLater()
        self._retired_agents = remaining

    def _update_project_header(self) -> None:
        if not self.project:
            return
        self.projectNameLabel.setText(self.project.name)
        self._elide_labels()

    def _elide_labels(self) -> None:
        if self.project:
            full = str(self.project.root)
            elided = QFontMetrics(self.projectPathLabel.font()).elidedText(
                full, Qt.TextElideMode.ElideMiddle, 360
            )
            self.projectPathLabel.setText(elided)
            self.projectPathLabel.setToolTip(full)
        if self._current_url:
            if not self.addressEdit.hasFocus():
                self.addressEdit.setText(self._current_url)
                self.addressEdit.setCursorPosition(0)
            self.addressEdit.setToolTip(self._current_url)

    def _refresh_recent_menu(self) -> None:
        self.menuRecent.clear()
        recent = [p for p in self.settings.recent_projects if Path(p).is_dir()]
        if not recent:
            action = self.menuRecent.addAction("No recent projects")
            action.setEnabled(False)
            return
        for path in recent:
            label = f"{Path(path).name}  —  {path}"
            if len(label) > 100:
                label = label[:97] + "…"
            action = self.menuRecent.addAction(label)
            action.setStatusTip(path)
            action.triggered.connect(
                lambda _checked=False, p=path: self._open_recent(p)
            )
        self.menuRecent.addSeparator()
        clear = self.menuRecent.addAction("Clear Recent Projects")
        clear.triggered.connect(self._clear_recent)

    def _open_recent(self, path: str) -> None:
        if self._confirm_stop("open another project"):
            self.open_project_path(path)

    def _clear_recent(self) -> None:
        self.settings.recent_projects = []
        self.settings.save()
        self._refresh_recent_menu()

    def _set_project_ui(self, enabled: bool) -> None:
        self.inputEdit.setEnabled(enabled)
        self.attachButton.setEnabled(enabled)
        self.sendButton.setEnabled(enabled and not (self.agent and self.agent.busy))
        self.stopButton.setEnabled(False)
        self.actionCompact.setEnabled(enabled)
        self.actionClearChat.setEnabled(enabled)
        self.actionReload.setEnabled(enabled)
        self.actionOpenExternal.setEnabled(enabled)
        self.actionRestartServer.setEnabled(enabled)
        self.actionStop.setEnabled(False)
        if not enabled:
            self.inputEdit.setPlaceholderText(
                "Open or create a project to start building…"
            )
        else:
            self.inputEdit.setPlaceholderText(
                "Describe the website or the change you want…"
            )

    # ---------------------------------------------------------------- server
    def _on_server_started(self, url: str, port: int) -> None:
        if self.project:
            self.project.server_url = url
            self.project.server_port = port
        self.serverStatusLabel.setText(f"PHP: :{port}")
        self.serverStatusLabel.setToolTip(url)
        if self.log_dialog:
            self.log_dialog.set_status(f"PHP dev server running at {url}")
        self._set_activity(f"Preview running at {url}")
        self._append_log(f"[app] PHP server ready at {url}")
        self.webview.setUrl(QUrl(url))

    def _on_server_failed(self, message: str) -> None:
        self.serverStatusLabel.setText("PHP: offline")
        if self.project:
            self.project.server_url = ""
        self._on_agent_error(message)
        self._append_log("[app] " + message)
        if self.log_dialog:
            self.log_dialog.set_status("PHP server is not running")
        self.webview.setHtml(ERROR_PAGE.format(message=html.escape(message)))
        self.addressEdit.setText("Preview unavailable")
        self._current_url = ""

    def _on_server_stopped(self) -> None:
        self.serverStatusLabel.setText("PHP: stopped")
        if self.log_dialog:
            self.log_dialog.set_status("PHP server stopped")

    def _on_server_log(self, line: str) -> None:
        if self.log_dialog:
            self.log_dialog.append(line)

    def show_server_log(self) -> None:
        if self.log_dialog is None:
            self.log_dialog = LogDialog(self)
            self.log_dialog.set_clear_callback(self.server.clear_logs)
        lines = self.server.log_lines[-3000:]
        if self._app_logs:
            lines = lines + ["", "[app] --- application messages ---"] + list(self._app_logs)
        console = self.webview.console_text(errors_only=False, limit=300)
        if console:
            lines = lines + ["", "[console] --- browser console ---"] + console.splitlines()
        self.log_dialog.set_lines(lines)
        if self.server.running:
            self.log_dialog.set_status(
                f"PHP dev server running at {self.server.url}"
            )
        else:
            self.log_dialog.set_status("PHP server is not running")
        self.log_dialog.show()
        self.log_dialog.raise_()
        self.log_dialog.activateWindow()

    def restart_server(self) -> None:
        if not self.project:
            return
        self._set_activity("Restarting PHP server…")
        self.server.stop(emit=False)
        self.serverStatusLabel.setText("PHP: starting…")
        self.server.start(self.project.root, self.project.web_dir)

    def reload_preview(self) -> None:
        if not self.project:
            return
        if self.server.running:
            self.webview.reload()
        else:
            self.restart_server()

    def open_external(self) -> None:
        target = self._current_url
        if not target and self.project:
            target = self.project.server_url
        if target:
            QDesktopServices.openUrl(QUrl(target))
        else:
            self._set_status_message("No preview URL to open.")

    # ---------------------------------------------------------------- config
    def open_settings(self) -> None:
        if self._settings_open:
            return
        self._settings_open = True
        try:
            if self._settings_dialog is None:
                self._settings_dialog = SettingsDialog(self.settings, self)
            else:
                self._settings_dialog.refresh()
            result = self._settings_dialog.exec()
        finally:
            self._settings_open = False
        if result == SettingsDialog.DialogCode.Accepted:
            self.modelStatusLabel.setText(self.settings.model or "No model")
            if self.agent:
                self.agent.refresh_usage()
            self._set_activity("Settings saved")
            self._set_status_message("Settings saved.")

    def _on_configuration_required(self) -> None:
        self._set_status_message("Configure the API endpoint and model in Settings.")
        QTimer.singleShot(0, self.open_settings)

    # ---------------------------------------------------------------- chat
    def send_message(self) -> None:
        if not self.project or not self.agent:
            self._set_status_message("Open or create a project first.")
            return
        if self.agent.busy:
            return
        if not self.settings.is_configured():
            self.open_settings()
            return
        text = self.inputEdit.toPlainText().strip()
        images = list(self._pending_attachments)
        if not text and not images:
            return
        self.agent.send(text, images)
        self.inputEdit.clear()
        self._clear_attachments()
        self.inputEdit.setFocus()

    def stop_agent(self) -> None:
        if self.agent and self.agent.busy:
            self.agent.stop()

    def _on_message_added(self, message: dict) -> None:
        if not self.chat_view:
            return
        self.chat_view.add_message(message)
        if message.get("role") in ("user", "tool"):
            self.chat_view.scroll_to_bottom(force=True)
        if (
            message.get("role") == "tool"
            and not message.get("error")
            and message.get("name") in ("write_file", "edit_file")
        ):
            self._reload_preview_after_edit()

    def _reload_preview_after_edit(self) -> None:
        """Refresh the live preview after the agent changes a served file."""
        if not self.project or not self.server.running or not self._current_url:
            return
        if not self._current_url.startswith(self.project.server_url):
            return
        self._reload_pending = True
        if self._reload_fallback is not None:
            self._reload_fallback.stop()
            self._reload_fallback.deleteLater()
        fallback = QTimer(self)
        fallback.setSingleShot(True)
        fallback.setInterval(8000)
        fallback.timeout.connect(self._reload_fallback_fired)
        fallback.start()
        self._reload_fallback = fallback
        self.webview.reload()

    def _reload_fallback_fired(self) -> None:
        if self._reload_fallback is not None:
            self._reload_fallback.deleteLater()
            self._reload_fallback = None
        if not self._reload_pending:
            return
        self._reload_pending = False
        callback = self._capture_after_reload
        self._capture_after_reload = None
        if callback:
            callback()

    def _run_after_preview_ready(self, callback) -> None:
        if not self._reload_pending:
            callback()
            return
        self._capture_after_reload = callback

    def _on_message_delta(self, message_id: str, kind: str, text: str) -> None:
        if self.chat_view:
            self.chat_view.append_delta(message_id, kind, text)

    def _on_message_updated(self, message: dict) -> None:
        if self.chat_view:
            self.chat_view.update_message(message)

    def _rebuild_chat(self) -> None:
        if self.chat_view and self.agent:
            self.chat_view.rebuild(self.agent.history.messages)

    def clear_chat(self) -> None:
        if not self.agent:
            return
        if self.agent.busy:
            self._set_status_message("Stop the agent before clearing the chat.")
            return
        reply = QMessageBox.question(
            self,
            "Clear chat",
            "Delete the saved chat history for this project? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.agent.history.clear()
        if self.chat_view:
            self.chat_view.rebuild([])
        self.agent.refresh_usage()
        self._set_activity("Chat cleared")

    def compact_context(self) -> None:
        if self.agent:
            self.agent.compact_now()

    def _on_compaction_started(self) -> None:
        self._set_activity("Compacting context…")
        self._set_status_message("Summarising older messages to free context…")

    def _on_compaction_finished(self, _marker: dict) -> None:
        self._set_activity("Context compacted")

    def _on_busy_changed(self, busy: bool) -> None:
        enabled = self.project is not None
        self.sendButton.setEnabled(enabled and not busy)
        self.sendButton.setText("Working…" if busy else "Send")
        self.stopButton.setEnabled(busy)
        self.actionStop.setEnabled(busy)
        self.attachButton.setEnabled(enabled)
        if busy:
            self.inputHint.setText("The agent is working — you can draft your next message")
            self._set_activity("Working…")
        else:
            self.inputHint.setText("Enter to send · Shift+Enter for a new line")
            self._set_activity("Ready")
            if enabled:
                self.inputEdit.setFocus()

    def _on_usage(self, info: dict | None) -> None:
        if not info:
            self.contextLabel.setText("Context —")
            self.contextBar.setValue(0)
            return
        used = int(info.get("used") or 0)
        limit = int(info.get("limit") or 1)
        ratio = float(info.get("ratio") or 0)
        percent = max(0, min(100, int(round(ratio * 100))))
        self.contextLabel.setText(
            f"Context ≈{human_tokens(used)} / {human_tokens(limit)} · {percent}%"
        )
        self.contextBar.setValue(percent)
        threshold = int(info.get("threshold") or 80)
        if percent >= 92:
            color = "#ff6b6b"
        elif percent >= threshold:
            color = "#e0a458"
        else:
            color = "#63d2a4"
        self.contextBar.setStyleSheet(
            f"QProgressBar::chunk {{ background: {color}; border-radius: 3px; }}"
        )
        api_usage = info.get("last_api") or {}
        tooltip = (
            f"Estimated tokens: {used:,}\nContext limit: {limit:,}\n"
            f"Auto-compaction threshold: {threshold}%"
        )
        if api_usage:
            tooltip += (
                f"\nLast API prompt tokens: {api_usage.get('prompt_tokens', 0):,}"
                f"\nLast completion tokens: {api_usage.get('completion_tokens', 0):,}"
            )
        self.contextLabel.setToolTip(tooltip)
        self.contextBar.setToolTip(tooltip)

    def _on_agent_status(self, text: str) -> None:
        self._set_activity(text)

    def _on_agent_error(self, message: str) -> None:
        self.activityLabel.setStyleSheet("color:#ff8f8f;")
        self.activityLabel.setText("⚠ Error")
        self.statusbar.showMessage(message, 20000)
        self._append_log("[app] ERROR: " + message)

    def _set_activity(self, text: str) -> None:
        self.activityLabel.setStyleSheet("color:#8b95a6;")
        self.activityLabel.setText(text)

    def _set_status_message(self, text: str) -> None:
        self.statusbar.showMessage(text, 12000)

    def _append_log(self, text: str) -> None:
        self._app_logs.append(text)
        if self.log_dialog:
            self.log_dialog.append(text)

    # ------------------------------------------------------------ attachments
    def attach_images(self) -> None:
        if not self.project:
            self._set_status_message("Open a project first.")
            return
        start = str(self._qt_settings.value("lastImageDir", str(Path.home())))
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Attach reference images",
            start,
            "Images (*.png *.jpg *.jpeg *.webp *.gif *.bmp);;All files (*)",
        )
        if files:
            self._qt_settings.setValue("lastImageDir", str(Path(files[0]).parent))
            self._attach_paths(files)

    def _attach_paths(self, paths: list[str]) -> None:
        if not self.project:
            self._set_status_message("Open a project first.")
            return
        added = 0
        for raw in paths:
            if len(self._pending_attachments) >= 8:
                self._set_status_message("You can attach up to 8 images per message.")
                break
            path = Path(raw)
            if path.suffix.lower() not in IMAGE_SUFFIXES or not path.exists():
                continue
            try:
                dest = import_attachment(path, self.project.attachments_dir, max_px=2048)
            except (OSError, ValueError) as exc:
                self._on_agent_error(f"Could not attach {path.name}: {exc}")
                continue
            if dest not in self._pending_attachments:
                self._pending_attachments.append(dest)
                added += 1
        if added:
            self._render_attachments()
            self._set_activity(f"Attached {added} image(s)")

    def _attach_clipboard_image(self) -> bool:
        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()
        if mime.hasImage():
            if not self.project:
                self._set_status_message("Open a project first.")
                return True
            image = clipboard.image()
            if image.isNull():
                return False
            try:
                path = save_clipboard_image(image, self.project.attachments_dir)
            except OSError as exc:
                self._on_agent_error(f"Could not save pasted image: {exc}")
                return True
            if len(self._pending_attachments) >= 8:
                self._set_status_message("You can attach up to 8 images per message.")
                return True
            self._pending_attachments.append(path)
            self._render_attachments()
            self._set_activity("Pasted image attached")
            return True
        if mime.hasUrls():
            paths = [
                url.toLocalFile()
                for url in mime.urls()
                if url.isLocalFile()
                and Path(url.toLocalFile()).suffix.lower() in IMAGE_SUFFIXES
            ]
            if paths:
                self._attach_paths(paths)
                return True
        return False

    def _render_attachments(self) -> None:
        while self.attachmentsLayout.count():
            item = self.attachmentsLayout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for path in self._pending_attachments:
            card = QWidget(self.attachmentsContents)
            vbox = QVBoxLayout(card)
            vbox.setContentsMargins(0, 0, 0, 0)
            vbox.setSpacing(2)
            thumb = QToolButton(card)
            thumb.setObjectName("attachmentThumb")
            thumb.setFixedSize(62, 48)
            thumb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            thumb.setIconSize(QSize(58, 44))
            pixmap = QPixmap(path)
            if not pixmap.isNull():
                thumb.setIcon(
                    QIcon(
                        pixmap.scaled(
                            58,
                            44,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )
                )
            else:
                thumb.setText("?")
            thumb.setToolTip(path)
            thumb.setCursor(Qt.CursorShape.PointingHandCursor)
            thumb.clicked.connect(
                lambda _checked=False, p=path: QDesktopServices.openUrl(
                    QUrl.fromLocalFile(p)
                )
            )
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(3)
            name = Path(path).name
            label = QLabel(name if len(name) <= 12 else name[:11] + "…", card)
            label.setObjectName("attachmentName")
            label.setToolTip(name)
            remove = QPushButton("×", card)
            remove.setObjectName("attachmentRemove")
            remove.setFixedSize(18, 18)
            remove.setToolTip("Remove attachment")
            remove.clicked.connect(
                lambda _checked=False, p=path: self._remove_attachment(p)
            )
            row.addWidget(label)
            row.addWidget(remove)
            vbox.addWidget(thumb, 0, Qt.AlignmentFlag.AlignHCenter)
            vbox.addLayout(row)
            self.attachmentsLayout.addWidget(card)
        self.attachmentsLayout.addStretch(1)
        self.attachmentsScroll.setVisible(bool(self._pending_attachments))
        self.attachButton.setEnabled(self.project is not None)

    def _remove_attachment(self, path: str) -> None:
        self._pending_attachments = [p for p in self._pending_attachments if p != path]
        self._render_attachments()

    def _clear_attachments(self) -> None:
        self._pending_attachments = []
        self._render_attachments()

    # --------------------------------------------------------------- capture
    def _capture_for_agent(self, modes: list[str], callback) -> None:
        if not self.page_capture or not self.project:
            callback({"results": [], "errors": ["No preview is available."]})
            return
        capture = self.page_capture

        def done(payload: dict) -> None:
            if self.page_capture is not capture:
                return
            self._set_activity("Working…" if self.agent and self.agent.busy else "Ready")
            callback(payload)

        def start() -> None:
            if self.page_capture is not capture:
                return
            self._set_activity("Capturing full-page preview…")
            capture.capture(modes, done)

        self._run_after_preview_ready(start)

    def _slice_for_agent(
        self, modes: list[str], selector: str, index: int, padding: int, callback
    ) -> None:
        if not self.page_capture or not self.project:
            callback({"results": [], "errors": ["No preview is available."]})
            return
        capture = self.page_capture

        def done(payload: dict) -> None:
            if self.page_capture is not capture:
                return
            self._set_activity("Working…" if self.agent and self.agent.busy else "Ready")
            callback(payload)

        def start() -> None:
            if self.page_capture is not capture:
                return
            self._set_activity(f"Capturing page slice around {selector}…")
            capture.capture_slice(modes, selector, index, padding, done)

        self._run_after_preview_ready(start)

    def _resolve_target(self, target: str) -> str:
        target = (target or "").strip()
        if target.startswith(("http://", "https://")):
            return target
        if target.startswith("//"):
            return "http:" + target
        if target.startswith("about:"):
            return target
        if re.match(r"^(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(:\d+)?([/?#]|$)", target):
            return "http://" + target
        if not self.project or not self.project.server_url:
            return ""
        base = self.project.server_url.rstrip("/")
        if not target:
            return base + "/"
        if target.startswith("web/"):
            target = target[4:]
        if target.startswith("/"):
            return base + target
        return base + "/" + quote(target.lstrip("./"), safe="/?&=#%+:;,@!$'()*~")

    def _load_for_agent(self, target: str, modes: list[str], callback) -> None:
        if self._pending_load is not None:
            callback({"error": "Another page load is already in progress.", "results": []})
            return
        url = self._resolve_target(target)
        if not url:
            callback(
                {
                    "error": "The PHP preview server is not ready yet. Try again in a moment.",
                    "results": [],
                }
            )
            return
        self.webview.clear_console()
        pending = {
            "id": uuid.uuid4().hex,
            "callback": callback,
            "modes": modes,
            "url": url,
            "title": "",
            "load_ok": None,
            "timer": None,
        }
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(20000)
        timer.timeout.connect(lambda p=pending: self._pending_load_timeout(p))
        pending["timer"] = timer
        self._pending_load = pending
        timer.start()
        self._set_activity(f"Loading {url}…")
        self.webview.load(QUrl(url))

    def _pending_load_timeout(self, pending: dict) -> None:
        if self._pending_load is not pending:
            return
        self._pending_load = None
        timer = pending.get("timer")
        if timer:
            timer.stop()
            timer.deleteLater()
        callback = pending.get("callback")
        if callback:
            callback(
                {
                    "error": f"Timed out while loading {pending.get('url')}.",
                    "results": [],
                }
            )
        self._set_activity("Ready")

    def _on_load_finished(self, ok: bool) -> None:
        if not self.project:
            self.backButton.setEnabled(False)
            self.forwardButton.setEnabled(False)
            return
        target = self.webview.url().toString()
        if target not in ("", "about:blank") and not target.startswith("data:"):
            self._current_url = target
        self._elide_labels()
        self.backButton.setEnabled(self.webview.history().canGoBack())
        self.forwardButton.setEnabled(self.webview.history().canGoForward())
        if self._reload_pending:
            self._reload_pending = False
            if self._reload_fallback is not None:
                self._reload_fallback.stop()
                self._reload_fallback.deleteLater()
                self._reload_fallback = None
            callback = self._capture_after_reload
            self._capture_after_reload = None
            if callback:
                QTimer.singleShot(300, callback)
        pending = self._pending_load
        if pending is None:
            if not ok:
                self._set_activity("Preview page failed to load")
            return
        timer = pending.get("timer")
        if timer:
            timer.stop()
            timer.deleteLater()
        pending["load_ok"] = bool(ok)
        pending["title"] = self.webview.title()
        pending["url"] = self._current_url or pending.get("url")
        QTimer.singleShot(450, lambda p=pending: self._finish_pending_load(p))

    def _finish_pending_load(self, pending: dict) -> None:
        if self._pending_load is not pending:
            return
        self._pending_load = None
        console = self.webview.console_text(errors_only=True, limit=50)
        callback = pending.get("callback")
        if not callback:
            return
        if not self.page_capture:
            callback(
                {
                    "error": "Preview capture is not available.",
                    "results": [],
                    "console": console,
                    "title": pending.get("title"),
                    "url": pending.get("url"),
                    "load_ok": pending.get("load_ok"),
                }
            )
            return
        self._set_activity("Capturing loaded page…")

        def done(payload: dict) -> None:
            payload = dict(payload or {})
            payload.update(
                {
                    "console": console,
                    "title": pending.get("title"),
                    "url": pending.get("url"),
                    "load_ok": pending.get("load_ok"),
                }
            )
            self._set_activity("Working…" if self.agent and self.agent.busy else "Ready")
            callback(payload)

        self.page_capture.capture(pending.get("modes") or ["desktop"], done)

    def _browser_action_for_agent(self, action: str, args: dict, callback) -> None:
        if not self.project:
            callback({"ok": False, "error": "No project is open."})
            return
        selector = str(args.get("selector") or "").strip()
        if not selector:
            callback({"ok": False, "error": "A CSS selector is required."})
            return
        try:
            index = max(0, int(args.get("index") or 0))
        except (TypeError, ValueError):
            index = 0
        started = time.time()
        state = {"done": False}
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(7000)

        def finish(result) -> None:
            if state["done"]:
                return
            state["done"] = True
            timer.stop()
            timer.deleteLater()
            payload = dict(result) if isinstance(result, dict) else {}
            if not payload:
                payload = {
                    "ok": True,
                    "warning": "The page did not return a result; it may be navigating.",
                }
            current_url = self.webview.url().toString() or self._current_url
            title = self.webview.title()
            console = self.webview.console_since(started, errors_only=True)
            if payload.get("error"):
                lines = [
                    f"{action.capitalize()} failed: {payload['error']}",
                    f"Page: {title or '(untitled)'} — {current_url}",
                ]
                if console:
                    lines.append("JS console errors/warnings:\n" + console)
                payload["ok"] = False
                payload["text"] = "\n".join(lines)
            else:
                tag = payload.get("tag") or "element"
                count = int(payload.get("count") or 1)
                lines = []
                if action == "click":
                    element = str(payload.get("element") or "").strip()
                    lines.append(f"Clicked <{tag}> matching {selector!r}.")
                    if element:
                        lines.append(f"Element text: {element!r}")
                elif action == "fill":
                    lines.append(f"Filled <{tag}> matching {selector!r}.")
                    kind = str(payload.get("type") or "").strip()
                    if kind:
                        lines.append(f"Input type: {kind}")
                    lines.append(f"Value now: {payload.get('value')!r}")
                    if payload.get("type") in ("checkbox", "radio"):
                        lines.append(f"Checked: {payload.get('checked')}")
                    if payload.get("submitted"):
                        lines.append("The surrounding form was submitted.")
                else:
                    lines.append(f"{action.capitalize()} dispatched on <{tag}>.")
                if count > 1:
                    lines.append(
                        f"{count} elements matched; used index {index}. "
                        f"Increase index to target another one."
                    )
                if payload.get("navigation"):
                    lines.append("The click navigated to a new page.")
                if payload.get("warning"):
                    lines.append("Note: " + str(payload["warning"]))
                lines.append(f"Page: {title or '(untitled)'} — {current_url}")
                if console:
                    lines.append("JS console errors/warnings since the action:\n" + console)
                else:
                    lines.append("No new JS console errors were reported.")
                lines.append("Use the screenshot tool to inspect the visual result.")
                payload["ok"] = True
                payload["text"] = "\n".join(lines)
            self._set_activity("Working…" if self.agent and self.agent.busy else "Ready")
            callback(payload)

        def on_js(result) -> None:
            QTimer.singleShot(450, lambda r=result: finish(r))

        timer.timeout.connect(
            lambda: finish(
                {
                    "ok": True,
                    "warning": (
                        "The page did not answer within 7 seconds; the action was sent "
                        "and the page may still be navigating."
                    ),
                }
            )
        )
        timer.start()
        try:
            if action == "fill":
                submit_arg = args.get("submit")
                if isinstance(submit_arg, str):
                    submit_flag = submit_arg.strip().lower() in ("1", "true", "yes", "on")
                else:
                    submit_flag = bool(submit_arg)
                code = (
                    FILL_JS.replace("__SELECTOR__", json.dumps(selector))
                    .replace("__INDEX__", str(index))
                    .replace("__VALUE__", json.dumps(str(args.get("value") or "")))
                    .replace("__SUBMIT__", "true" if submit_flag else "false")
                )
            else:
                code = CLICK_JS.replace("__SELECTOR__", json.dumps(selector)).replace(
                    "__INDEX__", str(index)
                )
            self._set_activity(f"{action.capitalize()} {selector}…")
            self.webview.page().runJavaScript(code, on_js)
        except (RuntimeError, AttributeError) as exc:
            finish({"ok": False, "error": str(exc)})

    def _navigate_address(self) -> None:
        text = self.addressEdit.text().strip()
        if not text:
            return
        url = self._resolve_target(text)
        if not url:
            self._set_status_message("Enter a URL or start the preview server first.")
            return
        self.webview.load(QUrl(url))
        self.webview.setFocus()

    def _search_for_agent(self, query: str, max_results: int, callback) -> None:
        if not query:
            callback({"ok": False, "error": "A search query is required."})
            return
        self._set_activity("Searching the web…")
        self.search_engine.search(query, max_results, callback)

    def _on_url_changed(self, url: QUrl) -> None:
        target = url.toString()
        if target in ("", "about:blank") or target.startswith("data:"):
            return
        self._current_url = target
        self._elide_labels()
        self.backButton.setEnabled(self.webview.history().canGoBack())
        self.forwardButton.setEnabled(self.webview.history().canGoForward())

    def _on_title_changed(self, title: str) -> None:
        if self.project and title.strip() and title.strip() != "about:blank":
            self.setWindowTitle(f"{title} — {self.project.name} — AI Website Builder")

    def _on_console_message(self, level: int, message: str, line: int, source: str) -> None:
        if self.log_dialog and level >= 1:
            name = LEVEL_NAMES.get(level, "info")
            self.log_dialog.append(f"[console:{name}] {message} ({source}:{line})")

    def _on_render_process_terminated(self, level, status) -> None:
        self._on_agent_error(f"The web preview renderer terminated ({status}).")

    # --------------------------------------------------------------- input
    def eventFilter(self, obj, event) -> bool:
        if obj is self.inputEdit:
            if event.type() == QEvent.Type.KeyPress:
                if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    if not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                        self.send_message()
                        return True
                if event.matches(QKeySequence.StandardKey.Paste):
                    if self._attach_clipboard_image():
                        return True
        return super().eventFilter(obj, event)

    def dragEnterEvent(self, event) -> None:
        mime = event.mimeData()
        if mime.hasImage():
            event.acceptProposedAction()
            return
        if mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() in IMAGE_SUFFIXES:
                    event.acceptProposedAction()
                    return
        super().dragEnterEvent(event)

    def dropEvent(self, event) -> None:
        mime = event.mimeData()
        if mime.hasUrls():
            paths = [
                url.toLocalFile()
                for url in mime.urls()
                if url.isLocalFile()
                and Path(url.toLocalFile()).suffix.lower() in IMAGE_SUFFIXES
            ]
            if paths:
                self._attach_paths(paths)
                event.acceptProposedAction()
                return
        if mime.hasImage():
            self._attach_clipboard_image()
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    # ---------------------------------------------------------------- misc
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide_labels()

    def _confirm_stop(self, action: str) -> bool:
        if self.agent and self.agent.busy:
            reply = QMessageBox.question(
                self,
                "Agent is working",
                f"The agent is still working. Stop it and {action}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return False
            self.agent.stop()
        return True

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            "About AI Website Builder",
            "<h3>AI Website Builder</h3>"
            "<p>A PyQt6 agentic website builder with an OpenAI-compatible chat backend, "
            "tool calling, reasoning display, full-page desktop/phone screenshots and a live "
            "PHP preview.</p>"
            "<p>Chat history is stored in each project's <code>chat_history.json</code>.</p>",
        )

    def closeEvent(self, event) -> None:
        if self.agent and self.agent.busy:
            reply = QMessageBox.question(
                self,
                "Agent is working",
                "The agent is still working. Stop it and quit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.agent.stop()
        self._reload_pending = False
        self._capture_after_reload = None
        if self._reload_fallback is not None:
            self._reload_fallback.stop()
            self._reload_fallback.deleteLater()
            self._reload_fallback = None
        if self._pending_load is not None:
            timer = self._pending_load.get("timer")
            if timer:
                timer.stop()
            self._pending_load = None
        if self.page_capture is not None:
            self.page_capture.abort()
        self._save_window_state()
        if self.agent is not None:
            self.agent.shutdown()
            if not self.agent.wait_for_workers(20000):
                self.agent.force_stop_workers()
        for agent, _retired_at in list(self._retired_agents):
            agent.shutdown()
            if not agent.wait_for_workers(20000):
                agent.force_stop_workers()
        self.search_engine.close()
        self.server.stop(emit=False)
        if self.log_dialog is not None:
            self.log_dialog.close()
        if self._settings_dialog is not None:
            self._settings_dialog.shutdown()
        event.accept()
