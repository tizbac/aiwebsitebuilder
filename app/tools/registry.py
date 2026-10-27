"""Tool schemas and dispatch."""

from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from . import files
from .websearch import web_search


TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": (
                "List files and directories inside the project. Use it to understand "
                "the current structure before editing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory relative to the project root. Defaults to '.'.",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "Maximum recursion depth (1-6). Default 2.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a UTF-8 text file. Output includes line numbers for orientation. "
                "Large files are truncated to 2500 lines; use start_line/end_line for ranges."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to the project root."},
                    "start_line": {"type": "integer", "description": "First line (1-based)."},
                    "end_line": {"type": "integer", "description": "Last line (inclusive)."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Create a new file or completely overwrite an existing one inside the "
                "public web/ directory. This tool cannot write outside web/. Prefer "
                "edit_file for small changes to existing files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to the project root."},
                    "content": {"type": "string", "description": "Complete UTF-8 file content."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Replace an exact substring in an existing file inside the public web/ "
                "directory (no writes outside web/). old_string must match exactly, "
                "including whitespace, and must be unique unless replace_all is true. Do "
                "not include read_file line-number prefixes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to the project root."},
                    "old_string": {"type": "string", "description": "Exact text to replace."},
                    "new_string": {"type": "string", "description": "Replacement text."},
                    "replace_all": {
                        "type": "boolean",
                        "description": "Replace every occurrence instead of requiring a unique match.",
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete",
            "description": (
                "Delete a file or directory inside the public web/ directory. This tool "
                "is strictly restricted to web/. Deleting a non-empty directory requires "
                "recursive=true; the web/ root itself cannot be deleted."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File or directory path inside web/, relative to the project root.",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "Required for non-empty directories. Default false.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": (
                "Search file contents with a regular expression and report file paths, "
                "line numbers and matching lines."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regular expression to search for."},
                    "path": {"type": "string", "description": "File or directory to search. Default '.'."},
                    "include": {
                        "type": "string",
                        "description": "Optional glob filter, e.g. '*.php' or '*.{css,js}'.",
                    },
                    "case_sensitive": {"type": "boolean", "description": "Default true."},
                    "regex": {"type": "boolean", "description": "Treat pattern as regex. Default true."},
                    "context_lines": {"type": "integer", "description": "Lines of context (0-8)."},
                    "max_results": {"type": "integer", "description": "Maximum matches (default 100)."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": (
                "Find files or folders by name. Supports glob wildcards or a "
                "case-insensitive substring, e.g. 'hero' or '*.css'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Name, substring or glob pattern."},
                    "path": {"type": "string", "description": "Directory to search. Default '.'."},
                    "include": {"type": "string", "description": "Optional file glob filter."},
                    "max_results": {"type": "integer", "description": "Maximum results (default 200)."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "screenshot",
            "description": (
                "Capture the live preview of website. Captures the FULL page, including "
                "content far below the fold, and returns the images to you. It captures "
                "both the desktop layout and a 390x844 phone layout by default. Use it to "
                "verify design, spacing and responsive behavior after changes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["desktop", "phone", "both"],
                        "description": "Viewport mode. Default 'both' (desktop + phone).",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "screenslice",
            "description": (
                "Capture a full-width vertical slice of the live page around the element "
                "matching a CSS selector. The slice covers the element plus padding pixels "
                "above and below (default 300) and is returned as an image for vision. Use "
                "this for a readable close-up of a specific section or component, especially "
                "on long pages. mode='desktop', 'phone' or 'both' (default 'both')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector identifying the section/component.",
                    },
                    "index": {
                        "type": "integer",
                        "description": "Which matching element (0-based, default 0).",
                    },
                    "padding": {
                        "type": "integer",
                        "description": "Extra pixels above and below the element. Default 300.",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["desktop", "phone", "both"],
                        "description": "Viewport mode. Default 'both'.",
                    },
                },
                "required": ["selector"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "webview_load",
            "description": (
                "Navigate the live preview webview to a page/route, wait for it to finish "
                "loading, then return the browser console errors (including uncaught JS "
                "exceptions), the page title and a full-page screenshot. Use this to test a "
                "specific page or after adding client-side JavaScript. 'url' may be an "
                "absolute URL, a server path like '/about.php', or a project path like "
                "'web/about.php'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL or project path to open. Defaults to the preview root.",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["desktop", "phone", "both"],
                        "description": "Screenshot viewport mode. Default 'both' (desktop + phone).",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click",
            "description": (
                "Click the first element matching a CSS selector in the live preview. "
                "Use it to follow links, press buttons, open menus or exercise interactive "
                "UI. Returns the element and resulting page URL, plus any JS console "
                "errors triggered by the click. Use the screenshot tool afterwards to see "
                "the visual result."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector, e.g. 'a.nav-link' or '#submit'.",
                    },
                    "index": {
                        "type": "integer",
                        "description": "Which matching element to click (0-based, default 0).",
                    },
                },
                "required": ["selector"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fill",
            "description": (
                "Fill an input, textarea, select or contenteditable element in the live "
                "preview by CSS selector. Dispatches input/change events so frameworks "
                "notice the change. Set submit=true to submit the surrounding form after "
                "filling. Useful for testing forms, search boxes and interactive widgets."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector for the field.",
                    },
                    "value": {
                        "type": "string",
                        "description": "Value to enter. Example: 'Jane Doe' or 'hello@example.com'.",
                    },
                    "index": {
                        "type": "integer",
                        "description": "Which matching element to fill (0-based, default 0).",
                    },
                    "submit": {
                        "type": "boolean",
                        "description": "Submit the surrounding form after filling. Default false.",
                    },
                },
                "required": ["selector", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "server_log",
            "description": (
                "Read the PHP development server log. Contains HTTP requests, PHP warnings "
                "and fatal errors. Use it to diagnose broken pages, 500 errors and routing issues."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "lines": {"type": "integer", "description": "How many recent lines (1-1000). Default 120."},
                    "query": {"type": "string", "description": "Optional case-insensitive filter text."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the public web with DuckDuckGo. Returns titles, URLs and snippets. "
                "Use it for documentation, references, libraries and design inspiration."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query."},
                    "max_results": {"type": "integer", "description": "1-15 results. Default 8."},
                },
                "required": ["query"],
            },
        },
    },
]


class ToolRegistry:
    def __init__(self, project, settings, server=None) -> None:
        self.project = project
        self.settings = settings
        self.server = server

    def schemas(self) -> list[dict]:
        return TOOL_SCHEMAS

    def call(self, name: str, args: dict) -> dict:
        handlers = {
            "list_files": lambda: files.list_files(self.project, args),
            "read_file": lambda: files.read_file(self.project, args),
            "write_file": lambda: files.write_file(self.project, args),
            "edit_file": lambda: files.edit_file(self.project, args),
            "delete": lambda: files.delete(self.project, args),
            "grep": lambda: files.grep(self.project, args),
            "search": lambda: files.search(self.project, args),
            "web_search": lambda: web_search(self.project, args),
            "server_log": lambda: self._server_log(args),
            "screenshot": lambda: files.fail(
                "Screenshot must be captured by the application UI, not this worker."
            ),
            "screenslice": lambda: files.fail(
                "screenslice must be captured by the application UI, not this worker."
            ),
            "webview_load": lambda: files.fail(
                "webview_load must be driven by the application UI, not this worker."
            ),
            "click": lambda: files.fail(
                "click must be driven by the application UI, not this worker."
            ),
            "fill": lambda: files.fail(
                "fill must be driven by the application UI, not this worker."
            ),
        }
        try:
            handler = handlers.get(name)
            if handler is None:
                return files.fail(
                    f"Unknown tool '{name}'. Available tools: {', '.join(handlers)}."
                )
            return handler()
        except Exception as exc:  # keep tool failures textual for the model
            return files.fail(f"{type(exc).__name__}: {exc}")

    def _server_log(self, args: dict) -> dict:
        if not self.server:
            return files.fail("No PHP server instance is attached.")
        try:
            lines = max(1, min(1000, int(args.get("lines") or 120)))
        except (TypeError, ValueError):
            lines = 120
        query = str(args.get("query") or "")
        text = self.server.tail_logs(lines, query)
        if not text:
            state = "running" if self.server.running else "not running"
            return files.ok(
                f"PHP server is {state}; the log is empty"
                + (f" (filter {query!r})." if query else ".")
            )
        return files.ok(
            f"PHP server log (last {lines} lines"
            + (f" matching {query!r}" if query else "")
            + "):\n"
            + text
        )


class ToolWorker(QThread):
    """Executes a blocking tool off the GUI thread."""

    finished_tool = pyqtSignal(object)

    def __init__(self, registry: ToolRegistry, name: str, args: dict, parent=None) -> None:
        super().__init__(parent)
        self.registry = registry
        self.name = name
        self.args = args

    def run(self) -> None:
        try:
            result = self.registry.call(self.name, self.args)
        except Exception as exc:
            result = files.fail(f"{type(exc).__name__}: {exc}")
        self.finished_tool.emit(result)
