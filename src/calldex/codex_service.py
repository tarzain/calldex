from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from openai_codex import ApprovalMode, AsyncCodex, AsyncThread, Sandbox
from openai_codex.generated.v2_all import SortDirection, ThreadSortKey

MAX_EVENT_SUMMARY_CHARS = 1200
MAX_TIMELINE_EVENTS = 200
MAX_THREAD_NAME_CHARS = 160
MAX_THREAD_PREVIEW_CHARS = 500
MAX_EVENT_DETAILS_CHARS = 12_000


class CodexServiceError(RuntimeError):
    """The local Codex process could not satisfy a request."""


class ThreadNotFoundError(CodexServiceError):
    pass


class CodexClient(Protocol):
    async def thread_list(self, **kwargs: Any) -> Any: ...

    async def thread_resume(self, thread_id: str, **kwargs: Any) -> Any: ...


def _json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if hasattr(value, "value"):
        return value.value
    return value


def _bounded_json_value(value: Any, limit: int = MAX_EVENT_DETAILS_CHARS) -> Any:
    """Return JSON-safe diagnostic data without shipping unbounded SDK payloads."""
    remaining = [limit]

    def visit(item: Any, depth: int = 0) -> Any:
        if remaining[0] <= 0:
            return "[truncated]"
        if depth >= 8:
            remaining[0] -= 11
            return "[max depth]"
        if isinstance(item, dict):
            result: dict[str, Any] = {}
            for key, child in item.items():
                key = str(key)
                remaining[0] -= len(key)
                if remaining[0] <= 0:
                    result["__truncated__"] = True
                    break
                result[key] = visit(child, depth + 1)
            return result
        if isinstance(item, (list, tuple)):
            result: list[Any] = []
            for child in item:
                if remaining[0] <= 0:
                    result.append("[truncated]")
                    break
                result.append(visit(child, depth + 1))
            return result
        if item is None or isinstance(item, (bool, int, float)):
            remaining[0] -= len(str(item))
            return item
        text = str(item)
        allowed = max(0, remaining[0])
        if len(text) > allowed:
            marker = "… [truncated]"
            text = text[: max(0, allowed - len(marker))] + marker
        remaining[0] -= len(text)
        return text

    return visit(value)


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    return str(value)


def _shorten(text: str, limit: int = MAX_EVENT_SUMMARY_CHARS, *, preserve_lines: bool = False) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not preserve_lines:
        text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _text_from_user_content(content: Any) -> str:
    chunks: list[str] = []
    for item in content or []:
        data = _json_value(item)
        if isinstance(data, str):
            chunks.append(data)
        elif isinstance(data, dict):
            text = data.get("text") or data.get("prompt") or data.get("name")
            if text:
                chunks.append(str(text))
            elif data.get("type"):
                chunks.append(f"[{data['type']}]")
    text = "\n".join(chunks)
    request_marker = "## My request for Codex:"
    if request_marker in text:
        text = text.split(request_marker, 1)[1]
    return re.sub(r"(?:\s*\[localImage\]\s*)+$", "", text).strip()


def _item_summary(kind: str, item: Any, details: dict[str, Any]) -> str:
    if kind == "userMessage":
        return _shorten(
            _text_from_user_content(details.get("content", getattr(item, "content", []))),
            preserve_lines=True,
        )
    if kind == "agentMessage":
        return _shorten(str(details.get("text", getattr(item, "text", ""))), preserve_lines=True)
    if kind == "reasoning":
        values = details.get("summary") or details.get("content") or getattr(item, "summary", None) or getattr(item, "content", None) or []
        return _shorten(" ".join(str(value) for value in values))
    if kind == "plan":
        return _shorten(str(details.get("text", getattr(item, "text", ""))), preserve_lines=True)
    if kind == "commandExecution":
        command = str(details.get("command", getattr(item, "command", "Command")))
        exit_code = details.get("exitCode", getattr(item, "exit_code", None))
        suffix = "" if exit_code is None else f" (exit {exit_code})"
        return _shorten(command + suffix)
    if kind == "fileChange":
        changes = getattr(item, "changes", [])
        return f"{len(changes)} file change{'s' if len(changes) != 1 else ''}"
    for key in ("text", "query", "prompt", "tool", "name", "command", "summary"):
        value = details.get(key)
        if value:
            return _shorten(value if isinstance(value, str) else json.dumps(value))
    return _shorten(json.dumps(details, ensure_ascii=False, default=str))


def normalize_item(item: Any, *, turn: Any) -> dict[str, Any]:
    raw_details = _json_value(item)
    if not isinstance(raw_details, dict):
        raw_details = {"value": raw_details}
    kind = str(raw_details.get("type") or getattr(item, "type", "unknown"))
    status = raw_details.get("status") or _json_value(getattr(turn, "status", None)) or "completed"
    phase = raw_details.get("phase")
    title = {
        "userMessage": "User message",
        "agentMessage": "Agent message",
        "reasoning": "Reasoning",
        "plan": "Plan",
        "commandExecution": "Command",
        "fileChange": "File change",
        "mcpToolCall": "MCP tool",
        "dynamicToolCall": "Tool call",
        "collabAgentToolCall": "Agent collaboration",
        "webSearch": "Web search",
        "imageView": "Image view",
        "imageGeneration": "Image generation",
        "enteredReviewMode": "Review mode",
        "exitedReviewMode": "Review mode",
        "contextCompaction": "Context compacted",
    }.get(kind, "Codex event")
    return {
        "id": str(raw_details.get("id") or f"{getattr(turn, 'id', 'turn')}:{kind}"),
        "turn_id": str(getattr(turn, "id", "")),
        "type": kind,
        "title": title,
        "summary": _item_summary(kind, item, raw_details) or title,
        "status": str(status),
        "phase": str(phase) if phase is not None else None,
        "timestamp": _timestamp(getattr(turn, "completed_at", None) or getattr(turn, "started_at", None)),
        "started_at": _timestamp(getattr(turn, "started_at", None)),
        "completed_at": _timestamp(getattr(turn, "completed_at", None)),
        "details": _bounded_json_value(raw_details),
    }


def normalize_thread(thread: Any) -> dict[str, Any]:
    status = _json_value(getattr(thread, "status", "unknown"))
    if isinstance(status, dict):
        status = status.get("type") or status.get("status") or "unknown"
    cwd = getattr(thread, "cwd", "")
    cwd = getattr(cwd, "root", cwd)
    preview = _shorten(str(getattr(thread, "preview", "")), MAX_THREAD_PREVIEW_CHARS, preserve_lines=True)
    name = getattr(thread, "name", None) or preview or "Untitled thread"
    return {
        "id": str(thread.id),
        "name": _shorten(str(name), MAX_THREAD_NAME_CHARS),
        "preview": preview,
        "repository_path": str(cwd),
        "status": str(status),
        "created_at": _timestamp(getattr(thread, "created_at", None)),
        "updated_at": _timestamp(getattr(thread, "updated_at", None)),
    }


@dataclass(slots=True)
class CodexService:
    client: CodexClient
    _send_locks: dict[str, asyncio.Lock] = field(default_factory=dict, init=False, repr=False)

    async def list_threads(self, *, limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        try:
            response = await self.client.thread_list(
                archived=False,
                cursor=cursor,
                limit=limit,
                sort_key=ThreadSortKey.updated_at,
                sort_direction=SortDirection.desc,
                use_state_db_only=True,
            )
        except Exception as exc:
            raise CodexServiceError("Codex threads are currently unavailable") from exc
        threads = [normalize_thread(thread) for thread in response.data]
        threads.sort(key=lambda item: item["updated_at"] or "", reverse=True)
        return {"threads": threads, "next_cursor": response.next_cursor}

    async def read_thread(self, thread_id: str) -> dict[str, Any]:
        if not thread_id.strip():
            raise ValueError("thread_id is required")
        try:
            if isinstance(self.client, AsyncCodex):
                # Reading through an AsyncThread handle avoids a thread/resume RPC, so
                # dashboard browsing never changes the underlying Codex thread.
                thread = AsyncThread(self.client, thread_id)
            else:
                thread = await self.client.thread_resume(
                    thread_id,
                    sandbox=Sandbox.read_only,
                    approval_mode=ApprovalMode.deny_all,
                )
            response = await thread.read(include_turns=True)
        except Exception as exc:
            message = str(exc).lower()
            if "not found" in message or "unknown thread" in message:
                raise ThreadNotFoundError(f"Thread {thread_id!r} was not found") from exc
            raise CodexServiceError("Codex thread history is currently unavailable") from exc

        events = [
            normalize_item(item, turn=turn)
            for turn in getattr(response.thread, "turns", [])
            for item in getattr(turn, "items", [])
        ]
        total = len(events)
        events = events[-MAX_TIMELINE_EVENTS:]
        return {
            "thread": normalize_thread(response.thread),
            "events": events,
            "event_count": total,
            "truncated": total > MAX_TIMELINE_EVENTS,
        }

    async def require_thread(self, thread_id: str) -> dict[str, Any]:
        return await self.read_thread(thread_id)

    async def send_message(self, thread_id: str, prompt: str) -> dict[str, str]:
        thread_id = thread_id.strip()
        prompt = prompt.strip()
        if not thread_id:
            raise ValueError("thread_id is required")
        if not prompt:
            raise ValueError("prompt is required")
        if len(prompt) > 100_000:
            raise ValueError("prompt must be at most 100000 characters")

        lock = self._send_locks.setdefault(thread_id, asyncio.Lock())
        async with lock:
            try:
                thread = await self.client.thread_resume(
                    thread_id,
                    sandbox=Sandbox.workspace_write,
                    approval_mode=ApprovalMode.auto_review,
                )
                result = await thread.run(
                    prompt,
                    sandbox=Sandbox.workspace_write,
                    approval_mode=ApprovalMode.auto_review,
                )
            except Exception as exc:
                message = str(exc).lower()
                if "not found" in message or "unknown thread" in message:
                    raise ThreadNotFoundError(f"Thread {thread_id!r} was not found") from exc
                raise CodexServiceError("Codex could not complete that message") from exc

        response = (getattr(result, "final_response", None) or "").strip()
        if not response:
            raise CodexServiceError("Codex finished without a final response")
        return {"thread_id": thread_id, "response": response}
