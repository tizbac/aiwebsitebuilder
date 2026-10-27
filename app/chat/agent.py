"""Agent loop: streaming chat, tool calls, screenshots, compaction."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from ..config import Settings
from ..project import Project
from ..tools.registry import ToolRegistry, ToolWorker
from ..utils import image_data_uri, image_dimensions, import_attachment
from .client import StreamWorker
from .compaction import CompactionPlan, CompactionWorker, plan_compaction
from .history import ChatHistory, new_id, now
from .prompts import system_prompt
from .tokens import estimate_message, estimate_text


class ChatAgent(QObject):
    message_added = pyqtSignal(object)
    message_delta = pyqtSignal(str, str, str)
    message_updated = pyqtSignal(object)
    busy_changed = pyqtSignal(bool)
    status = pyqtSignal(str)
    error = pyqtSignal(str)
    usage_changed = pyqtSignal(object)
    compaction_started = pyqtSignal()
    compaction_finished = pyqtSignal(object)
    history_reloaded = pyqtSignal()
    configuration_required = pyqtSignal()

    def __init__(self, settings: Settings, project: Project, server, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.project = project
        self.server = server
        self.history = ChatHistory(project.history_path)
        self.history.load()
        self.registry = ToolRegistry(project, settings, server)
        self.capture_handler = None  # callable(modes, callback)
        self.slice_handler = None  # callable(modes, selector, index, padding, callback)
        self.webview_load_handler = None  # callable(target, modes, callback)
        self.browser_action_handler = None  # callable(action, args, callback)
        self.search_handler = None  # callable(query, max_results, callback)
        self.js_handler = None  # callable(code, callback)
        self.ask_user_handler = None  # callable(question, choices, callback)

        self._busy = False
        self._stop_requested = False
        self._closed = False
        self._stream: StreamWorker | None = None
        self._tool_worker: ToolWorker | None = None
        self._compaction: CompactionWorker | None = None
        self._compaction_blocked = False
        self._compaction_marker: dict | None = None
        self._active_assistant: dict | None = None
        self._rounds = 0
        self._last_usage: dict | None = None
        self._tool_index = 0
        self._sanitize_history()

    # --------------------------------------------------------------- state
    @property
    def busy(self) -> bool:
        return self._busy

    def _set_busy(self, value: bool) -> None:
        if self._busy != value:
            self._busy = value
            self.busy_changed.emit(value)

    def usage_snapshot(self) -> dict:
        used = self._estimate_used()
        limit = max(1, int(self.settings.context_limit or 1))
        return {
            "used": used,
            "limit": limit,
            "ratio": used / limit,
            "threshold": self.settings.compaction_threshold,
            "last_api": self._last_usage,
        }

    def _emit_usage(self) -> None:
        self.usage_changed.emit(self.usage_snapshot())

    def _system_prompt(self) -> str:
        return system_prompt(self.project, self.settings)

    def _estimate_used(self) -> int:
        start, marker = self.history.last_compaction()
        tokens = estimate_text(self._system_prompt()) + 2400
        if marker:
            tokens += estimate_text(str(marker.get("summary") or ""))
        for message in self.history.messages[start:]:
            tokens += estimate_message(message)
            tokens += 900 * len(message.get("images") or [])
        return tokens

    def _needs_compaction(self) -> bool:
        if self._compaction_blocked:
            return False
        limit = int(self.settings.context_limit or 0)
        if limit <= 0:
            return False
        threshold = max(30, min(95, int(self.settings.compaction_threshold or 80)))
        return self._estimate_used() >= limit * threshold / 100.0

    # --------------------------------------------------------------- send
    def send(self, text: str, image_paths: list[str] | None = None) -> None:
        if self._busy:
            self.error.emit("The agent is still working. Stop it or wait for it to finish.")
            return
        if not self.settings.is_configured():
            self.configuration_required.emit()
            return
        text = (text or "").strip()
        images = self._prepare_user_images(image_paths or [])
        if not text and not images:
            return
        self._compaction_blocked = False
        self._stop_requested = False
        self._rounds = 0
        message = {
            "id": new_id(),
            "role": "user",
            "content": text,
            "images": images,
            "ts": now(),
        }
        self.history.append(message)
        self.message_added.emit(message)
        self._emit_usage()
        self._set_busy(True)
        self._request()

    def _prepare_user_images(self, paths: list[str]) -> list[dict]:
        images: list[dict] = []
        for raw in paths:
            path = Path(raw)
            if not path.exists():
                self.error.emit(f"Attachment not found: {raw}")
                continue
            try:
                resolved = path.resolve()
                try:
                    resolved.relative_to(self.project.root)
                    dest = resolved
                except ValueError:
                    dest = Path(
                        import_attachment(
                            resolved, self.project.attachments_dir, max_px=2048
                        )
                    )
                width, height = image_dimensions(dest)
                images.append(
                    {
                        "path": self.project.rel(dest),
                        "name": dest.name,
                        "width": width,
                        "height": height,
                    }
                )
            except (OSError, ValueError) as exc:
                self.error.emit(f"Could not attach {path.name}: {exc}")
        return images

    # ---------------------------------------------------------- API payload
    def _api_messages(self) -> list[dict]:
        start, marker = self.history.last_compaction()
        system_content = self._system_prompt()
        if marker:
            system_content += (
                "\n\nEARLIER CONVERSATION SUMMARY (older messages were compacted to "
                "save context; treat this as accurate history):\n\n"
                + str(marker.get("summary") or "")
            )
        messages: list[dict] = [{"role": "system", "content": system_content}]

        pending_images: list[tuple[str, list[dict]]] = []

        def flush_images() -> None:
            if not pending_images:
                return
            texts = [ctx for ctx, _ in pending_images if ctx]
            parts: list[dict] = []
            if texts:
                parts.append({"type": "text", "text": "\n\n".join(texts)})
            for _, imgs in pending_images:
                parts.extend(imgs)
            if parts:
                messages.append({"role": "user", "content": parts})
            pending_images.clear()

        for message in self.history.messages[start:]:
            role = message.get("role")
            if role == "compaction":
                continue
            if role != "tool":
                flush_images()
            if role == "user":
                text = str(message.get("content") or "")
                image_parts: list[dict] = []
                missing: list[str] = []
                for image in message.get("images") or []:
                    part, err = self._image_part(image)
                    if part:
                        image_parts.append(part)
                    elif err:
                        missing.append(err)
                if missing:
                    text = (text + "\n" + "\n".join(missing)).strip()
                if image_parts:
                    parts = []
                    if text:
                        parts.append({"type": "text", "text": text})
                    else:
                        parts.append(
                            {"type": "text", "text": "Please use the attached reference image(s)."}
                        )
                    parts.extend(image_parts)
                    messages.append({"role": "user", "content": parts})
                elif text:
                    messages.append({"role": "user", "content": text})
            elif role == "assistant":
                content = str(message.get("content") or "")
                tool_calls = message.get("tool_calls") or []
                if message.get("error") and not content:
                    continue
                if not content and not tool_calls:
                    continue
                api_message: dict = {"role": "assistant", "content": content or ""}
                if tool_calls:
                    api_message["tool_calls"] = tool_calls
                messages.append(api_message)
            elif role == "tool":
                call_id = message.get("tool_call_id")
                if not call_id:
                    continue
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": message.get("name") or "tool",
                        "content": str(message.get("content") or "(no output)"),
                    }
                )
                image_parts = []
                for image in message.get("images") or []:
                    part, _err = self._image_part(image)
                    if part:
                        image_parts.append(part)
                if image_parts:
                    context = str(message.get("image_context") or "").strip()
                    if not context:
                        context = f"Image(s) produced by the {message.get('name') or 'tool'} tool."
                    pending_images.append((context, image_parts))
        flush_images()
        return messages

    def _image_part(self, image: dict) -> tuple[dict | None, str | None]:
        rel = str(image.get("path") or "")
        if not rel:
            return None, None
        path = Path(rel)
        if not path.is_absolute():
            path = self.project.root / path
        if not path.exists():
            return None, f"[missing image: {rel}]"
        try:
            uri = image_data_uri(path, max(512, int(self.settings.vision_max_px or 1568)))
        except (OSError, ValueError) as exc:
            return None, f"[could not load image {rel}: {exc}]"
        return {"type": "image_url", "image_url": {"url": uri}}, None

    # ------------------------------------------------------------- request
    def _request(self) -> None:
        if self._closed:
            return
        if self._stop_requested:
            self._finish_turn(allow_compaction=False)
            return
        if not self.settings.is_configured():
            self.configuration_required.emit()
            self._finish_turn(allow_compaction=False)
            return
        if self._maybe_compact(then_request=True):
            return
        try:
            api_messages = self._api_messages()
        except Exception as exc:  # defensive: never crash the UI
            self.error.emit(f"Could not prepare the request: {exc}")
            self._finish_turn(allow_compaction=False)
            return

        assistant = {
            "id": new_id(),
            "role": "assistant",
            "content": "",
            "reasoning": "",
            "tool_calls": [],
            "ts": now(),
            "streaming": True,
        }
        self._active_assistant = assistant
        self.history.messages.append(assistant)
        self.message_added.emit(assistant)
        self.status.emit("Waiting for the model…")

        worker = StreamWorker(self.settings, api_messages, self.registry.schemas(), self)
        self._stream = worker
        worker.content_delta.connect(lambda text: self._on_delta("content", text))
        worker.reasoning_delta.connect(lambda text: self._on_delta("reasoning", text))
        worker.completed.connect(self._on_stream_completed)
        worker.failed.connect(self._on_stream_failed)
        worker.finished.connect(lambda w=worker: self._on_stream_thread_finished(w))
        worker.start()

    def _on_delta(self, kind: str, text: str) -> None:
        assistant = self._active_assistant
        if not assistant or self._closed:
            return
        if kind == "content":
            assistant["content"] = str(assistant.get("content") or "") + text
        else:
            assistant["reasoning"] = str(assistant.get("reasoning") or "") + text
            self.status.emit("Thinking…")
        self.message_delta.emit(assistant["id"], kind, text)

    def _on_stream_thread_finished(self, worker) -> None:
        if self._stream is worker:
            self._stream = None
        worker.deleteLater()

    def _on_stream_completed(self, result: dict) -> None:
        if self._closed:
            return
        assistant = self._active_assistant
        if not assistant:
            return
        assistant["content"] = str(result.get("content") or assistant.get("content") or "")
        assistant["reasoning"] = str(result.get("reasoning") or assistant.get("reasoning") or "")
        assistant.pop("streaming", None)
        usage = result.get("usage")
        if usage:
            self._last_usage = usage
            assistant["usage"] = usage
        finish_reason = result.get("finish_reason")
        if finish_reason:
            assistant["finish_reason"] = finish_reason
        cancelled = bool(result.get("cancelled")) or self._stop_requested
        tool_calls = result.get("tool_calls") or []

        if cancelled:
            assistant["tool_calls"] = []
            assistant["stopped"] = True
            if not assistant["content"]:
                assistant["content"] = "*[Stopped]*"
            self.history.save()
            self.message_updated.emit(assistant)
            self._finish_turn(allow_compaction=False)
            return

        self._active_assistant["tool_calls"] = tool_calls
        self.history.save()
        self.message_updated.emit(assistant)
        self._emit_usage()

        if tool_calls:
            if self._rounds >= max(1, self.settings.max_tool_rounds):
                self._complete_missing_tools(
                    tool_calls,
                    "Not executed: the maximum number of tool rounds was reached.",
                )
                self.error.emit(
                    "The agent reached the maximum number of tool rounds. "
                    "Send another message to continue."
                )
                self._finish_turn(allow_compaction=True)
                return
            self._rounds += 1
            self._tool_index = 0
            self.status.emit("Running tools…")
            self._run_tool(0)
            return

        self._finish_turn(allow_compaction=True)

    def _on_stream_failed(self, message: str) -> None:
        if self._closed:
            return
        assistant = self._active_assistant
        if assistant:
            assistant.pop("streaming", None)
            assistant["error"] = message
            assistant["tool_calls"] = []
            self.history.save()
            self.message_updated.emit(assistant)
        self.error.emit(message)
        self._finish_turn(allow_compaction=False)

    def _finish_turn(self, allow_compaction: bool = True) -> None:
        if self._closed:
            return
        if allow_compaction and not self._stop_requested and not self._compaction_blocked:
            if self._maybe_compact(then_request=False):
                return
        self._active_assistant = None
        self._set_busy(False)
        self.status.emit("Ready")
        self._emit_usage()

    def stop(self) -> None:
        if not self._busy:
            return
        self._stop_requested = True
        self.status.emit("Stopping…")
        if self._stream:
            self._stream.cancel()
        if self._compaction:
            self._compaction.cancel()
        # Tool threads cannot be interrupted safely; their callback will finish the turn.

    def shutdown(self) -> None:
        self._closed = True
        self._stop_requested = True
        if self._stream:
            self._stream.cancel()
        if self._compaction:
            self._compaction.cancel()
        assistant = self._active_assistant
        if assistant is not None:
            assistant.pop("streaming", None)
            assistant["stopped"] = True
            if not assistant.get("content") and not assistant.get("tool_calls"):
                self.history.messages = [
                    m for m in self.history.messages if m is not assistant
                ]
            self.history.save()
            self._active_assistant = None

    def refresh_usage(self) -> None:
        self._emit_usage()

    def has_running_workers(self) -> bool:
        return bool(
            (self._stream and self._stream.isRunning())
            or (self._tool_worker and self._tool_worker.isRunning())
            or (self._compaction and self._compaction.isRunning())
        )

    def wait_for_workers(self, timeout_ms: int = 20_000) -> bool:
        """Wait for background work to finish. Returns False if one is still running."""
        for worker in (self._stream, self._tool_worker, self._compaction):
            if worker is not None and worker.isRunning():
                if not worker.wait(timeout_ms):
                    return False
        return True

    def force_stop_workers(self) -> None:
        for worker in (self._stream, self._tool_worker, self._compaction):
            if worker is not None and worker.isRunning():
                try:
                    worker.terminate()
                    worker.wait(500)
                except RuntimeError:
                    pass

    # ---------------------------------------------------------------- tools
    def _run_tool(self, index: int) -> None:
        assistant = self._active_assistant
        if not assistant:
            return
        calls = assistant.get("tool_calls") or []
        if index >= len(calls):
            self._request()
            return
        if self._stop_requested:
            self._complete_missing_tools(
                calls[index:], "Cancelled by the user before execution.", start_index=index
            )
            self._finish_turn(allow_compaction=False)
            return

        call = calls[index]
        function = call.get("function") or {}
        name = str(function.get("name") or "")
        raw_args = str(function.get("arguments") or "{}")
        args, parse_error = _parse_arguments(raw_args)
        self.status.emit(f"Running {name}…")

        if parse_error:
            self._tool_completed(
                index,
                call,
                {"ok": False, "text": f"Invalid tool arguments: {parse_error}", "images": []},
            )
            return

        if name == "screenshot":
            modes = _modes_from_args(args)
            if not self.capture_handler:
                self._tool_completed(
                    index, call, {"ok": False, "text": "Screenshot is not available.", "images": []}
                )
                return
            self.capture_handler(
                modes,
                lambda payload: self._tool_completed(
                    index, call, self._screenshot_result(payload)
                ),
            )
            return

        if name == "screenslice":
            selector = str(args.get("selector") or "").strip()
            if not selector:
                self._tool_completed(
                    index, call, {"ok": False, "text": "screenslice requires a selector.", "images": []}
                )
                return
            if not self.slice_handler:
                self._tool_completed(
                    index, call, {"ok": False, "text": "screenslice is not available.", "images": []}
                )
                return
            modes = _modes_from_args(args, default="both")
            try:
                slice_index = max(0, int(args.get("index") or 0))
            except (TypeError, ValueError):
                slice_index = 0
            try:
                padding = max(0, min(3000, int(args.get("padding") or 300)))
            except (TypeError, ValueError):
                padding = 300
            self.slice_handler(
                modes,
                selector,
                slice_index,
                padding,
                lambda payload: self._tool_completed(
                    index, call, self._slice_result(payload)
                ),
            )
            return

        if name == "webview_load":
            target = str(args.get("url") or "")
            modes = _modes_from_args(args, default="both")
            if not self.webview_load_handler:
                self._tool_completed(
                    index, call, {"ok": False, "text": "webview_load is not available.", "images": []}
                )
                return
            self.webview_load_handler(
                target,
                modes,
                lambda payload: self._tool_completed(
                    index, call, self._webview_result(payload, target)
                ),
            )
            return

        if name == "run_js":
            code = str(args.get("code") or "")
            if not code.strip():
                self._tool_completed(
                    index, call, {"ok": False, "text": "run_js requires non-empty code.", "images": []}
                )
                return
            if not self.js_handler:
                self._tool_completed(
                    index, call, {"ok": False, "text": "run_js is not available.", "images": []}
                )
                return
            self.js_handler(
                code,
                lambda payload: self._tool_completed(
                    index, call, self._js_result(payload)
                ),
            )
            return

        if name == "ask_user":
            question = str(args.get("question") or "").strip()
            if not question:
                self._tool_completed(
                    index, call, {"ok": False, "text": "ask_user requires a question.", "images": []}
                )
                return
            if not self.ask_user_handler:
                self._tool_completed(
                    index, call, {"ok": False, "text": "ask_user is not available.", "images": []}
                )
                return
            raw_choices = args.get("choices")
            choices: list[str] = []
            if isinstance(raw_choices, str):
                choices = [raw_choices.strip()] if raw_choices.strip() else []
            elif isinstance(raw_choices, list):
                choices = [str(choice).strip() for choice in raw_choices if str(choice).strip()]
            choices = choices[:8]
            self.ask_user_handler(
                question,
                choices,
                lambda payload: self._tool_completed(
                    index, call, self._ask_result(payload, question)
                ),
            )
            return

        if name == "web_search" and self.search_handler:
            query = str(args.get("query") or "").strip()
            try:
                max_results = max(1, min(15, int(args.get("max_results") or 8)))
            except (TypeError, ValueError):
                max_results = 8
            self.search_handler(
                query,
                max_results,
                lambda payload: self._tool_completed(
                    index, call, self._search_result(payload)
                ),
            )
            return

        if name in ("click", "fill"):
            if not self.browser_action_handler:
                self._tool_completed(
                    index,
                    call,
                    {"ok": False, "text": f"{name} is not available.", "images": []},
                )
                return
            self.browser_action_handler(
                name,
                args,
                lambda payload, action=name: self._tool_completed(
                    index, call, self._browser_result(payload, action)
                ),
            )
            return

        worker = ToolWorker(self.registry, name, args, self)
        self._tool_worker = worker
        worker.finished_tool.connect(
            lambda result, idx=index, c=call: self._tool_completed(idx, c, result)
        )
        worker.finished.connect(lambda w=worker: self._on_tool_thread_finished(w))
        worker.start()

    def _on_tool_thread_finished(self, worker) -> None:
        if self._tool_worker is worker:
            self._tool_worker = None
        worker.deleteLater()

    def _tool_completed(self, index: int, call: dict, result: dict) -> None:
        if self._closed:
            return
        assistant = self._active_assistant
        if not assistant:
            return
        calls = assistant.get("tool_calls") or []
        name = str((call.get("function") or {}).get("name") or "tool")
        arguments = str((call.get("function") or {}).get("arguments") or "")
        images = self._store_tool_images(result.get("images") or [])
        call_id = call.get("id") or "call_" + uuid.uuid4().hex[:10]
        message = {
            "id": new_id(),
            "role": "tool",
            "tool_call_id": call_id,
            "name": name,
            "arguments": arguments,
            "content": str(result.get("text") or ""),
            "images": images,
            "image_context": str(result.get("image_context") or ""),
            "error": not bool(result.get("ok", True)),
            "ts": now(),
        }
        self.history.append(message)
        self.message_added.emit(message)
        self._emit_usage()

        if self._stop_requested:
            self._complete_missing_tools(
                calls[index + 1:], "Cancelled by the user before execution.", start_index=index + 1
            )
            self._finish_turn(allow_compaction=False)
            return
        if index + 1 < len(calls):
            self._run_tool(index + 1)
        else:
            self._request()

    def _store_tool_images(self, paths: list[str]) -> list[dict]:
        images: list[dict] = []
        for raw in paths:
            try:
                path = Path(raw).resolve()
                try:
                    path.relative_to(self.project.root)
                    rel = self.project.rel(path)
                except ValueError:
                    dest = Path(
                        import_attachment(path, self.project.attachments_dir, max_px=2048)
                    )
                    path = dest
                    rel = self.project.rel(dest)
                width, height = image_dimensions(path)
                images.append({"path": rel, "name": path.name, "width": width, "height": height})
            except (OSError, ValueError):
                continue
        return images

    def _complete_missing_tools(
        self, calls: list[dict], reason: str, start_index: int | None = None
    ) -> None:
        assistant = self._active_assistant
        if not assistant:
            return
        for call in calls:
            call_id = call.get("id") or "call_" + uuid.uuid4().hex[:10]
            name = str((call.get("function") or {}).get("name") or "tool")
            message = {
                "id": new_id(),
                "role": "tool",
                "tool_call_id": call_id,
                "name": name,
                "arguments": str((call.get("function") or {}).get("arguments") or ""),
                "content": reason,
                "images": [],
                "error": True,
                "ts": now(),
            }
            self.history.append(message)
            self.message_added.emit(message)

    def _screenshot_result(self, payload: dict) -> dict:
        results = (payload or {}).get("results") or []
        errors = (payload or {}).get("errors") or []
        if not results:
            detail = "; ".join(str(e) for e in errors) or "unknown error"
            return {"ok": False, "text": f"Screenshot failed: {detail}", "images": []}
        lines = [
            f"Captured {len(results)} full-page screenshot(s) of the live preview "
            f"(the image(s) below show the entire page, including content below the fold)."
        ]
        images: list[str] = []
        console_lines: list[str] = []
        for item in results:
            rel = self.project.rel(item.get("path", ""))
            viewport = item.get("viewport") or [0, 0]
            lines.append(
                f"- {item.get('mode')}: {rel} | image {item.get('width')}x{item.get('height')} px "
                f"(page height {item.get('page_height')} css px, viewport {viewport[0]}x{viewport[1]}) "
                f"| title: {item.get('title') or '(none)'} | url: {item.get('url') or ''}"
            )
            images.append(str(item.get("path") or ""))
            for log in item.get("console") or []:
                console_lines.append(str(log))
        if console_lines:
            lines.append("Browser console / JS messages:\n" + "\n".join(console_lines[-20:]))
        if errors:
            lines.append("Warnings: " + "; ".join(str(e) for e in errors))
        return {
            "ok": True,
            "text": "\n".join(lines),
            "images": images,
            "image_context": (
                "Full-page preview screenshot(s) attached. Examine layout, typography, "
                "spacing and responsive behavior carefully; fix anything that looks broken."
            ),
        }

    def _slice_result(self, payload: dict) -> dict:
        payload = payload or {}
        results = payload.get("results") or []
        errors = payload.get("errors") or []
        if not results:
            detail = "; ".join(str(e) for e in errors) or "unknown error"
            return {"ok": False, "text": f"Screenslice failed: {detail}", "images": []}
        lines = []
        images = []
        selector = ""
        for item in results:
            slice_info = item.get("slice") or {}
            selector = str(slice_info.get("selector") or selector)
            rel = self.project.rel(item.get("path", ""))
            lines.append(
                f"- {item.get('mode')}: {rel} | image {item.get('width')}x{item.get('height')} px "
                f"| page slice y={slice_info.get('top')}..{slice_info.get('bottom')} "
                f"of {item.get('page_height')} css px "
                f"| element <{slice_info.get('tag') or '?'}> "
                f"y={slice_info.get('element_top')}..{slice_info.get('element_bottom')} "
                f"(height {slice_info.get('element_height')} css px) "
                f"| title: {item.get('title') or '(none)'} | url: {item.get('url') or ''}"
            )
            if slice_info.get("text"):
                lines.append(f"  element text: {str(slice_info['text'])[:240]!r}")
            images.append(str(item.get("path") or ""))
        header = (
            f"Captured {len(results)} close-up page slice(s) around selector "
            f"{selector!r} (element position plus the requested padding)."
        )
        if errors:
            lines.append("Warnings: " + "; ".join(str(e) for e in errors))
        return {
            "ok": True,
            "text": header + "\n" + "\n".join(lines),
            "images": images,
            "image_context": (
                "Close-up vertical slice(s) of the live page around the requested "
                "element. Inspect typography, spacing, alignment and responsive layout "
                "at readable resolution."
            ),
        }

    def _webview_result(self, payload: dict, requested: str) -> dict:
        payload = payload or {}
        results = payload.get("results") or []
        errors = payload.get("errors") or []
        load_ok = payload.get("load_ok")
        console = str(payload.get("console") or "").strip()
        actual_url = payload.get("url") or requested
        title = payload.get("title") or ""
        if payload.get("error"):
            return {"ok": False, "text": f"webview_load failed: {payload['error']}", "images": []}
        lines = [
            f"Loaded: {actual_url}",
            f"Title: {title or '(none)'}",
            f"Load status: {'OK' if load_ok is not False else 'FAILED / shown error page'}",
        ]
        if console:
            lines.append("JavaScript console errors/warnings:\n" + console)
        else:
            lines.append("JavaScript console errors/warnings: none reported.")
        if errors:
            lines.append("Screenshot warnings: " + "; ".join(str(e) for e in errors))
        if results:
            for item in results:
                lines.append(
                    f"- screenshot {item.get('mode')}: {self.project.rel(item.get('path', ''))} "
                    f"({item.get('width')}x{item.get('height')} px)"
                )
        images = [str(item.get("path") or "") for item in results]
        return {
            "ok": load_ok is not False and bool(not payload.get("error")),
            "text": "\n".join(lines),
            "images": images,
            "image_context": (
                "Screenshot(s) of the page you just opened. Check console errors above and "
                "the rendered output; fix problems and reload if needed."
            ),
        }

    def _js_result(self, payload: dict) -> dict:
        payload = payload or {}
        if payload.get("error") and not payload.get("text"):
            return {
                "ok": False,
                "text": "JavaScript failed: " + str(payload.get("error")),
                "images": [],
            }
        return {
            "ok": bool(payload.get("ok", True)),
            "text": str(payload.get("text") or "JavaScript returned no value."),
            "images": [],
        }

    def _ask_result(self, payload: dict, question: str) -> dict:
        payload = payload or {}
        if not payload.get("ok") or payload.get("answer") is None:
            return {
                "ok": False,
                "text": "The user did not answer: "
                + str(payload.get("error") or "the question was dismissed."),
                "images": [],
            }
        answer = str(payload.get("answer") or "").strip()
        if not answer:
            return {
                "ok": False,
                "text": "The user did not provide an answer.",
                "images": [],
            }
        source = str(payload.get("source") or "")
        suffix = " (selected suggestion)" if source == "choice" else ""
        return {
            "ok": True,
            "text": f"The user answered your question.\nQuestion: {question}\nAnswer: {answer}{suffix}",
            "images": [],
        }

    def _search_result(self, payload: dict) -> dict:
        payload = payload or {}
        if payload.get("error"):
            return {
                "ok": False,
                "text": "Web search failed: " + str(payload["error"]),
                "images": [],
            }
        return {
            "ok": True,
            "text": str(payload.get("text") or "No results."),
            "images": [],
        }

    def _browser_result(self, payload: dict, action: str) -> dict:
        payload = payload or {}
        if payload.get("error") and not payload.get("text"):
            return {
                "ok": False,
                "text": f"{action} failed: {payload['error']}",
                "images": [],
            }
        text = str(payload.get("text") or f"{action} completed.").strip()
        return {
            "ok": bool(payload.get("ok", True)),
            "text": text,
            "images": [],
        }

    # ----------------------------------------------------------- compaction
    def _maybe_compact(self, then_request: bool) -> bool:
        if not self._needs_compaction():
            return False
        plan = plan_compaction(self.history, self.settings.context_limit)
        if not plan:
            return False
        self._start_compaction(plan, then_request)
        return True

    def _start_compaction(self, plan: CompactionPlan, then_request: bool) -> None:
        self._compaction_marker = None
        self._set_busy(True)
        self.compaction_started.emit()
        self.status.emit("Compacting conversation context…")
        worker = CompactionWorker(self.settings, plan, self)
        self._compaction = worker
        worker.progress.connect(self.status.emit)
        worker.done.connect(
            lambda summary: self._compaction_done(plan, summary, then_request)
        )
        worker.failed.connect(
            lambda message: self._compaction_failed(message, then_request)
        )
        worker.cancelled.connect(lambda: self._finish_turn(allow_compaction=False))
        worker.finished.connect(lambda w=worker: self._on_compaction_thread_finished(w))
        worker.start()

    def _on_compaction_thread_finished(self, worker) -> None:
        if self._compaction is worker:
            self._compaction = None
        worker.deleteLater()

    def _compaction_done(self, plan: CompactionPlan, summary: str, then_request: bool) -> None:
        if self._closed:
            return
        tokens_before = self._estimate_used()
        marker = self.history.insert_compaction(
            plan.split_index, summary, plan.summarised_count, tokens_before
        )
        self._compaction_marker = marker
        self._compaction_blocked = False
        self.compaction_finished.emit(marker)
        self.history_reloaded.emit()
        self._emit_usage()
        self.status.emit("Context compacted.")
        if self._stop_requested:
            self._finish_turn(allow_compaction=False)
        elif then_request:
            self._request()
        else:
            self._finish_turn(allow_compaction=False)

    def _compaction_failed(self, message: str, then_request: bool) -> None:
        if self._closed:
            return
        self._compaction_blocked = True
        self.error.emit(f"Context compaction failed: {message}")
        if self._stop_requested:
            self._finish_turn(allow_compaction=False)
        elif then_request:
            self._request()
        else:
            self._finish_turn(allow_compaction=False)

    def compact_now(self) -> None:
        if self._busy:
            self.error.emit("Wait for the current response to finish before compacting.")
            return
        plan = plan_compaction(self.history, self.settings.context_limit)
        if not plan:
            self.status.emit("Not enough conversation to compact yet.")
            return
        self._compaction_blocked = False
        self._start_compaction(plan, then_request=False)

    # ------------------------------------------------------------ integrity
    def _sanitize_history(self) -> None:
        """Make sure tool-call turns always have matching tool results."""
        source = self.history.messages
        output: list[dict] = []
        changed = False
        for message in source:
            if message.get("streaming"):
                message.pop("streaming", None)
                message["stopped"] = True
                changed = True
        index = 0
        while index < len(source):
            message = source[index]
            role = message.get("role")
            if role == "tool":
                changed = True
                index += 1
                continue
            output.append(message)
            index += 1
            if role != "assistant":
                continue
            calls = message.get("tool_calls") or []
            if not calls:
                continue
            call_ids = [str(call.get("id") or "") for call in calls]
            seen: set[str] = set()
            while index < len(source) and source[index].get("role") == "tool":
                tool_message = source[index]
                call_id = str(tool_message.get("tool_call_id") or "")
                if call_id in call_ids and call_id not in seen:
                    seen.add(call_id)
                    output.append(tool_message)
                else:
                    changed = True
                index += 1
            for call in calls:
                call_id = str(call.get("id") or "")
                if call_id and call_id in seen:
                    continue
                function = call.get("function") or {}
                output.append(
                    {
                        "id": new_id(),
                        "role": "tool",
                        "tool_call_id": call_id or "call_" + uuid.uuid4().hex[:10],
                        "name": str(function.get("name") or "tool"),
                        "arguments": str(function.get("arguments") or ""),
                        "content": "This tool call was interrupted before it produced a result.",
                        "images": [],
                        "error": True,
                        "interrupted": True,
                        "ts": now(),
                    }
                )
                changed = True
        if changed:
            self.history.messages = output
            self.history.save()


def _parse_arguments(raw: str) -> tuple[dict, str | None]:
    text = (raw or "").strip()
    if not text:
        return {}, None
    try:
        value = json.loads(text)
    except ValueError as exc:
        return {}, str(exc)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}, "arguments must be a JSON object"
    if not isinstance(value, dict):
        return {}, "arguments must be a JSON object"
    return value, None


def _modes_from_args(args: dict, default: str = "both") -> list[str]:
    mode = str(args.get("mode") or default).lower().strip()
    if mode == "both":
        return ["desktop", "phone"]
    if mode == "phone":
        return ["phone"]
    return ["desktop"]
