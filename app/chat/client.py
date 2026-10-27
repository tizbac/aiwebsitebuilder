"""Streaming OpenAI-compatible chat client (tools + reasoning support)."""

from __future__ import annotations

import json
import threading
import uuid

import requests
from PyQt6.QtCore import QThread, pyqtSignal

from ..config import Settings


THINK_OPEN = "<" + "think>"
THINK_CLOSE = "</" + "think>"


class ApiError(Exception):
    pass


class ThinkSplitter:
    """Splits inline think blocks out of streamed content."""

    def __init__(self) -> None:
        self._buffer = ""
        self._in_think = False

    def feed(self, text: str) -> list[tuple[str, str]]:
        if not text:
            return []
        self._buffer += text
        out: list[tuple[str, str]] = []
        while True:
            tag = THINK_CLOSE if self._in_think else THINK_OPEN
            idx = self._buffer.find(tag)
            if idx >= 0:
                if idx:
                    out.append(
                        ("reasoning" if self._in_think else "content", self._buffer[:idx])
                    )
                self._buffer = self._buffer[idx + len(tag):]
                self._in_think = not self._in_think
                continue
            hold = 0
            for k in range(min(len(tag) - 1, len(self._buffer)), 0, -1):
                if self._buffer.endswith(tag[:k]):
                    hold = k
                    break
            if hold:
                emit = self._buffer[:-hold]
                self._buffer = self._buffer[-hold:]
            else:
                emit = self._buffer
                self._buffer = ""
            if emit:
                out.append(("reasoning" if self._in_think else "content", emit))
            break
        return out

    def flush(self) -> list[tuple[str, str]]:
        if not self._buffer:
            return []
        kind = "reasoning" if self._in_think else "content"
        text = self._buffer
        self._buffer = ""
        return [(kind, text)]


def _normalise_usage(usage: dict | None) -> dict | None:
    if not isinstance(usage, dict):
        return None
    prompt = usage.get("prompt_tokens")
    if prompt is None:
        prompt = usage.get("input_tokens")
    completion = usage.get("completion_tokens")
    if completion is None:
        completion = usage.get("output_tokens")
    total = usage.get("total_tokens")
    if total is None and prompt is not None and completion is not None:
        total = int(prompt) + int(completion)
    if prompt is None and total is None:
        return None
    return {
        "prompt_tokens": int(prompt or 0),
        "completion_tokens": int(completion or 0),
        "total_tokens": int(total or 0),
    }


def request_headers(settings: Settings) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    if settings.api_key.strip():
        headers["Authorization"] = "Bearer " + settings.api_key.strip()
    low = (settings.api_base or "").lower()
    if "openrouter.ai" in low:
        headers.setdefault("HTTP-Referer", "https://localhost/ai-website-builder")
        headers.setdefault("X-Title", "AI Website Builder")
    return headers


def complete_sync(settings: Settings, messages: list[dict]) -> str:
    """Blocking non-streaming completion used by background summary workers."""
    payload = {
        "model": settings.model,
        "messages": messages,
        "stream": False,
    }
    resp = requests.post(
        settings.api_url(),
        headers=request_headers(settings),
        json=payload,
        timeout=(20, max(10, settings.timeout)),
    )
    if resp.status_code >= 400:
        raise ApiError(_format_http_error(resp.status_code, _read_error_body(resp)))
    try:
        obj = resp.json()
    except ValueError:
        raise ApiError("The summarisation endpoint returned invalid JSON.") from None
    if isinstance(obj, dict) and obj.get("error"):
        raise ApiError(_error_from_obj(obj["error"]))
    choices = obj.get("choices") or []
    if not choices:
        raise ApiError("The summarisation request returned no choices.")
    content = (choices[0].get("message") or {}).get("content")
    if isinstance(content, list):
        content = _extract_content_list(content)[0]
    text = _content_parts_to_text(content).strip()
    if not text:
        raise ApiError("The model returned an empty summary.")
    return text


def _content_parts_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks = []
        for part in content:
            if isinstance(part, str):
                chunks.append(part)
            elif isinstance(part, dict):
                if part.get("type") == "text":
                    chunks.append(str(part.get("text") or ""))
        return "".join(chunks)
    return ""


def _extract_reasoning(delta: dict) -> str:
    chunks: list[str] = []
    for key in ("reasoning_content", "reasoning", "thinking"):
        value = delta.get(key)
        if isinstance(value, str):
            chunks.append(value)
        elif isinstance(value, dict):
            if isinstance(value.get("text"), str):
                chunks.append(value["text"])
            elif isinstance(value.get("content"), str):
                chunks.append(value["content"])
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    if isinstance(item.get("text"), str):
                        chunks.append(item["text"])
                    elif isinstance(item.get("thinking"), str):
                        chunks.append(item["thinking"])
    details = delta.get("reasoning_details")
    if isinstance(details, list):
        for item in details:
            if isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    chunks.append(item["text"])
    return "".join(chunks)


def _extract_content_list(content) -> tuple[str, str]:
    """Return (text, reasoning) from an array-style content delta."""
    text = []
    reasoning = []
    for part in content:
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        if kind in ("text", "output_text"):
            text.append(str(part.get("text") or ""))
        elif kind in ("thinking", "reasoning", "reasoning_text"):
            reasoning.append(str(part.get("thinking") or part.get("text") or ""))
        elif kind == "redacted_thinking":
            reasoning.append("[redacted thinking]")
    return "".join(text), "".join(reasoning)


class StreamWorker(QThread):
    """Performs one streaming chat-completions request.

    Emits content/reasoning deltas while running, then exactly one ``completed``
    or ``failed`` signal.
    """

    content_delta = pyqtSignal(str)
    reasoning_delta = pyqtSignal(str)
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        settings: Settings,
        messages: list[dict],
        tools: list[dict] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.messages = messages
        self.tools = tools
        self._cancelled = threading.Event()
        self._lock = threading.Lock()
        self._response: requests.Response | None = None

    # ------------------------------------------------------------- control
    def cancel(self) -> None:
        self._cancelled.set()
        with self._lock:
            resp = self._response
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    # ----------------------------------------------------------------- run
    def run(self) -> None:
        try:
            self._run()
        except _Cancelled:
            self._finish(cancelled=True)
        except ApiError as exc:
            if self._cancelled.is_set():
                self._finish(cancelled=True)
            else:
                self.failed.emit(str(exc))
        except requests.exceptions.Timeout:
            if self._cancelled.is_set():
                self._finish(cancelled=True)
            else:
                self.failed.emit(
                    f"Request timed out after {self.settings.timeout}s. You can raise "
                    f"the timeout in Settings."
                )
        except requests.exceptions.RequestException as exc:
            if self._cancelled.is_set():
                self._finish(cancelled=True)
            else:
                self.failed.emit(f"Network error: {exc}")
        except Exception as exc:  # pragma: no cover - defensive
            if self._cancelled.is_set():
                self._finish(cancelled=True)
            else:
                self.failed.emit(f"Unexpected error: {exc}")

    def _finish(self, **extra) -> None:
        if getattr(self, "_emitted", False):
            return
        self._emitted = True
        payload = {
            "content": getattr(self, "_content", ""),
            "reasoning": getattr(self, "_reasoning", ""),
            "tool_calls": getattr(self, "_tool_calls", []),
            "usage": getattr(self, "_usage", None),
            "finish_reason": getattr(self, "_finish_reason", None),
            "cancelled": False,
        }
        payload.update(extra)
        self.completed.emit(payload)

    # ------------------------------------------------------------- request
    def _payload(self, include_stream_options: bool = True) -> dict:
        payload: dict = {
            "model": self.settings.model,
            "messages": self.messages,
            "stream": True,
        }
        if include_stream_options:
            payload["stream_options"] = {"include_usage": True}
        if self.tools:
            payload["tools"] = self.tools
        return payload

    def _post(self, payload: dict) -> requests.Response:
        resp = requests.post(
            self.settings.api_url(),
            headers=request_headers(self.settings),
            json=payload,
            stream=True,
            timeout=(20, max(10, self.settings.timeout)),
        )
        with self._lock:
            self._response = resp
        if self._cancelled.is_set():
            resp.close()
            raise _Cancelled()
        return resp

    def _run(self) -> None:
        if not self.settings.api_url():
            raise ApiError("No API endpoint configured. Open Settings and set the API base URL.")
        if not self.settings.model:
            raise ApiError("No model configured. Open Settings and set a model name.")

        self._content = ""
        self._reasoning = ""
        self._tool_calls: list[dict] = []
        self._usage: dict | None = None
        self._finish_reason: str | None = None
        self._emitted = False

        payload = self._payload(True)
        resp = self._post(payload)
        if resp.status_code in (400, 422) and "stream_options" in payload:
            body = _read_error_body(resp)
            resp.close()
            if "stream_options" in body.lower() or resp.status_code == 422:
                resp = self._post(self._payload(False))
            else:
                raise ApiError(_format_http_error(resp.status_code, body))
        if resp.status_code >= 400:
            raise ApiError(_format_http_error(resp.status_code, _read_error_body(resp)))

        ctype = (resp.headers.get("content-type") or "").lower()
        if "text/event-stream" not in ctype and "application/json" in ctype:
            self._consume_json(resp)
        else:
            self._consume_stream(resp)

        self._finish()

    # ------------------------------------------------------------- parsing
    def _consume_json(self, resp: requests.Response) -> None:
        try:
            obj = resp.json()
        except ValueError:
            raise ApiError("The server returned invalid JSON.") from None
        if isinstance(obj, dict) and obj.get("error"):
            raise ApiError(_error_from_obj(obj["error"]))
        choices = obj.get("choices") or []
        if not choices:
            raise ApiError("The server returned no choices.")
        message = choices[0].get("message") or {}
        self._finish_reason = choices[0].get("finish_reason")
        self._usage = _normalise_usage(obj.get("usage"))
        content = message.get("content")
        if isinstance(content, list):
            text, reasoning = _extract_content_list(content)
            content = text
            if reasoning:
                self._emit_reasoning(reasoning)
        if isinstance(content, str) and content:
            self._emit_content(content)
        extra_reason = _extract_reasoning(message)
        if extra_reason:
            self._emit_reasoning(extra_reason)
        tool_calls = message.get("tool_calls") or []
        self._tool_calls = _normalise_tool_calls(tool_calls)

    def _consume_stream(self, resp: requests.Response) -> None:
        splitter = ThinkSplitter()
        calls: dict[int, dict] = {}
        for raw in resp.iter_lines(chunk_size=2048):
            if self._cancelled.is_set():
                raise _Cancelled()
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
            line = line.strip()
            if not line or line.startswith(":") or line.startswith("event:"):
                continue
            if line.startswith("data:"):
                data = line[5:].strip()
            elif line.startswith("{"):
                data = line
            else:
                continue
            if not data:
                continue
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except ValueError:
                continue
            if isinstance(obj, dict) and obj.get("error"):
                raise ApiError(_error_from_obj(obj["error"]))
            usage = _normalise_usage(obj.get("usage"))
            if usage:
                self._usage = usage
            choices = obj.get("choices") or []
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                if choice.get("finish_reason"):
                    self._finish_reason = choice["finish_reason"]
                delta = choice.get("delta") or {}
                if not isinstance(delta, dict):
                    delta = {}
                reasoning = _extract_reasoning(delta)
                if reasoning:
                    self._emit_reasoning(reasoning)
                content = delta.get("content")
                if isinstance(content, list):
                    text, extra_reason = _extract_content_list(content)
                    if extra_reason:
                        self._emit_reasoning(extra_reason)
                    content = text
                if isinstance(content, str) and content:
                    for kind, text in splitter.feed(content):
                        if kind == "reasoning":
                            self._emit_reasoning(text)
                        else:
                            self._emit_content(text)
                for tc in delta.get("tool_calls") or []:
                    _merge_tool_call(calls, tc)
                legacy = delta.get("function_call")
                if isinstance(legacy, dict):
                    _merge_tool_call(
                        calls,
                        {
                            "index": 0,
                            "id": "call_" + uuid.uuid4().hex[:12],
                            "function": legacy,
                        },
                    )
        for kind, text in splitter.flush():
            if kind == "reasoning":
                self._emit_reasoning(text)
            else:
                self._emit_content(text)
        self._tool_calls = _finalise_tool_calls(calls)

    def _emit_content(self, text: str) -> None:
        if not text:
            return
        self._content += text
        self.content_delta.emit(text)

    def _emit_reasoning(self, text: str) -> None:
        if not text:
            return
        self._reasoning += text
        self.reasoning_delta.emit(text)


class _Cancelled(Exception):
    pass


def _read_error_body(resp: requests.Response, limit: int = 6000) -> str:
    try:
        return resp.text[:limit]
    except Exception:
        return ""


def _format_http_error(status: int, body: str) -> str:
    body = (body or "").strip()
    try:
        obj = json.loads(body)
        if isinstance(obj, dict) and obj.get("error"):
            return f"API error {status}: {_error_from_obj(obj['error'])}"
    except ValueError:
        pass
    if body.startswith("<"):
        body = "Server returned an HTML error page (check the endpoint URL)."
    return f"API error {status}: {body[:800] or 'empty response'}"


def _error_from_obj(error) -> str:
    if isinstance(error, str):
        return error
    if isinstance(error, dict):
        message = error.get("message") or error.get("detail") or json.dumps(error)[:500]
        return str(message)
    return str(error)


def _merge_tool_call(calls: dict[int, dict], tc: dict) -> None:
    if not isinstance(tc, dict):
        return
    try:
        index = int(tc.get("index", 0))
    except (TypeError, ValueError):
        index = 0
    slot = calls.setdefault(
        index,
        {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
    )
    if tc.get("id"):
        slot["id"] = str(tc["id"])
    fn = tc.get("function") or {}
    if isinstance(fn, dict):
        name = fn.get("name")
        if name:
            slot["function"]["name"] = str(name)
        args = fn.get("arguments")
        if isinstance(args, str):
            slot["function"]["arguments"] += args
        elif isinstance(args, dict):
            slot["function"]["arguments"] += json.dumps(args)


def _normalise_tool_calls(tool_calls: list) -> list[dict]:
    calls: dict[int, dict] = {}
    for i, tc in enumerate(tool_calls):
        if isinstance(tc, dict):
            tc = dict(tc)
            tc.setdefault("index", i)
            _merge_tool_call(calls, tc)
    return _finalise_tool_calls(calls)


def _finalise_tool_calls(calls: dict[int, dict]) -> list[dict]:
    result = []
    for index in sorted(calls):
        call = calls[index]
        if not call["function"]["name"]:
            continue
        if not call["id"]:
            call["id"] = "call_" + uuid.uuid4().hex[:12]
        if not call["function"]["arguments"].strip():
            call["function"]["arguments"] = "{}"
        result.append(call)
    return result


class CompleteWorker(QThread):
    """One non-streaming completion, used for compaction and connection tests."""

    completed = pyqtSignal(str, object)
    failed = pyqtSignal(str)

    def __init__(self, settings: Settings, messages: list[dict], parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.messages = messages
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def run(self) -> None:
        try:
            payload = {
                "model": self.settings.model,
                "messages": self.messages,
                "stream": False,
            }
            resp = requests.post(
                self.settings.api_url(),
                headers=request_headers(self.settings),
                json=payload,
                timeout=(20, max(10, self.settings.timeout)),
            )
            if self._cancelled.is_set():
                return
            if resp.status_code >= 400:
                raise ApiError(_format_http_error(resp.status_code, _read_error_body(resp)))
            obj = resp.json()
            if isinstance(obj, dict) and obj.get("error"):
                raise ApiError(_error_from_obj(obj["error"]))
            choices = obj.get("choices") or []
            if not choices:
                raise ApiError("The server returned no choices.")
            message = choices[0].get("message") or {}
            content = message.get("content")
            if isinstance(content, list):
                content = _extract_content_list(content)[0]
            text = _content_parts_to_text(content).strip()
            if not text:
                raise ApiError("The model returned an empty response.")
            self.completed.emit(text, _normalise_usage(obj.get("usage")))
        except ApiError as exc:
            if not self._cancelled.is_set():
                self.failed.emit(str(exc))
        except requests.exceptions.Timeout:
            if not self._cancelled.is_set():
                self.failed.emit(f"Request timed out after {self.settings.timeout}s.")
        except requests.exceptions.RequestException as exc:
            if not self._cancelled.is_set():
                self.failed.emit(f"Network error: {exc}")
        except Exception as exc:  # pragma: no cover
            if not self._cancelled.is_set():
                self.failed.emit(f"Unexpected error: {exc}")
