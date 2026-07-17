from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, AsyncIterator

from openai_codex import ApprovalMode, Sandbox
from openai_codex.generated.v2_all import SortDirection, ThreadSortKey

from .codex_service import (
    CodexService,
    CodexServiceError,
    ThreadNotFoundError,
    _bounded_json_value,
    _json_value,
    normalize_thread,
)

MAX_RUN_EVENTS = 500
COMPLETED_RUN_TTL_SECONDS = 30 * 60


class AccessMode(StrEnum):
    read_only = "read_only"
    workspace_write = "workspace_write"
    full_access = "full_access"

    @property
    def sandbox(self) -> Sandbox:
        return {
            AccessMode.read_only: Sandbox.read_only,
            AccessMode.workspace_write: Sandbox.workspace_write,
            AccessMode.full_access: Sandbox.full_access,
        }[self]


class ActiveRunError(CodexServiceError):
    def __init__(self, run_id: str) -> None:
        super().__init__("That Codex task already has an active run")
        self.run_id = run_id


class RunNotFoundError(CodexServiceError):
    pass


@dataclass(slots=True)
class RunState:
    id: str
    thread_id: str
    turn_id: str
    access_mode: AccessMode
    handle: Any
    status: str = "running"
    started_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    final_response: str | None = None
    error: str | None = None
    plan: list[dict[str, Any]] = field(default_factory=list)
    diff: str = ""
    events: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=MAX_RUN_EVENTS)
    )
    subscribers: set[asyncio.Queue[dict[str, Any]]] = field(default_factory=set)
    completion: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None
    _seq: int = 0

    def append(
        self,
        event_type: str,
        payload: Any,
        *,
        item_id: str | None = None,
    ) -> dict[str, Any]:
        self._seq += 1
        event = {
            "seq": self._seq,
            "run_id": self.id,
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
            "type": event_type,
            "item_id": item_id,
            "timestamp": time.time(),
            "payload": _bounded_json_value(_json_value(payload)),
        }
        self.events.append(event)
        for queue in tuple(self.subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)
        return event

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.id,
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
            "status": self.status,
            "access_mode": self.access_mode.value,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "final_response": self.final_response,
            "error": self.error,
            "plan": self.plan,
            "diff": self.diff,
            "last_seq": self._seq,
        }


class CodexRuntime:
    """One Codex client shared by the dashboard and all voice sessions."""

    def __init__(self, client: Any, *, default_cwd: str | Path) -> None:
        self.client = client
        self.default_cwd = str(Path(default_cwd).expanduser().resolve())
        self.service = CodexService(client)
        self.runs: dict[str, RunState] = {}
        self.active_by_thread: dict[str, str] = {}
        self._closed = False

    def _cleanup(self) -> None:
        cutoff = time.time() - COMPLETED_RUN_TTL_SECONDS
        expired = [
            run_id
            for run_id, run in self.runs.items()
            if run.completed_at is not None and run.completed_at < cutoff
        ]
        for run_id in expired:
            self.runs.pop(run_id, None)

    def get_run(self, run_id: str) -> RunState:
        self._cleanup()
        run = self.runs.get(run_id)
        if run is None:
            raise RunNotFoundError(f"Run {run_id!r} was not found")
        return run

    def active_run(self, thread_id: str) -> RunState | None:
        run_id = self.active_by_thread.get(thread_id)
        if not run_id:
            return None
        run = self.runs.get(run_id)
        if run is None or run.status != "running":
            self.active_by_thread.pop(thread_id, None)
            return None
        return run

    async def list_projects(self) -> list[dict[str, Any]]:
        projects: dict[str, dict[str, Any]] = {
            self.default_cwd: {
                "path": self.default_cwd,
                "name": Path(self.default_cwd).name or self.default_cwd,
                "thread_count": 0,
                "updated_at": None,
                "is_default": True,
            }
        }
        cursor: str | None = None
        try:
            for _ in range(5):
                response = await self.client.thread_list(
                    archived=False,
                    cursor=cursor,
                    limit=100,
                    sort_key=ThreadSortKey.updated_at,
                    sort_direction=SortDirection.desc,
                    use_state_db_only=True,
                )
                for raw in response.data:
                    thread = normalize_thread(raw)
                    path = thread["repository_path"]
                    if not path:
                        continue
                    project = projects.setdefault(
                        path,
                        {
                            "path": path,
                            "name": Path(path).name or path,
                            "thread_count": 0,
                            "updated_at": None,
                            "is_default": path == self.default_cwd,
                        },
                    )
                    project["thread_count"] += 1
                    if not project["updated_at"]:
                        project["updated_at"] = thread["updated_at"]
                cursor = response.next_cursor
                if not cursor:
                    break
        except Exception as exc:
            raise CodexServiceError("Codex projects are currently unavailable") from exc
        return sorted(
            projects.values(),
            key=lambda project: (not project["is_default"], project["name"].lower()),
        )

    async def require_project(self, repository_path: str) -> str:
        requested = str(Path(repository_path).expanduser().resolve())
        projects = await self.list_projects()
        if requested not in {project["path"] for project in projects}:
            raise ValueError("repository_path must be a known Codex project")
        return requested

    async def _start_on_thread(
        self,
        thread: Any,
        prompt: str,
        access_mode: AccessMode,
    ) -> RunState:
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("prompt is required")
        active = self.active_run(thread.id)
        if active is not None:
            raise ActiveRunError(active.id)
        try:
            handle = await thread.turn(
                prompt,
                sandbox=access_mode.sandbox,
                approval_mode=ApprovalMode.auto_review,
            )
        except Exception as exc:
            raise CodexServiceError("Codex could not start that run") from exc
        run = RunState(
            id=uuid.uuid4().hex,
            thread_id=thread.id,
            turn_id=handle.id,
            access_mode=access_mode,
            handle=handle,
        )
        self.runs[run.id] = run
        self.active_by_thread[thread.id] = run.id
        run.append("run.started", {"prompt": prompt, "access_mode": access_mode.value})
        run.task = asyncio.create_task(self._consume(run), name=f"codex-run-{run.id}")
        return run

    async def start_run(
        self,
        thread_id: str,
        prompt: str,
        *,
        access_mode: AccessMode = AccessMode.workspace_write,
    ) -> RunState:
        if self.active_run(thread_id) is not None:
            raise ActiveRunError(self.active_by_thread[thread_id])
        try:
            thread = await self.client.thread_resume(
                thread_id,
                sandbox=access_mode.sandbox,
                approval_mode=ApprovalMode.auto_review,
            )
        except Exception as exc:
            message = str(exc).lower()
            if "not found" in message or "unknown thread" in message:
                raise ThreadNotFoundError(f"Thread {thread_id!r} was not found") from exc
            raise CodexServiceError("Codex could not resume that task") from exc
        return await self._start_on_thread(thread, prompt, access_mode)

    async def create_thread(
        self,
        prompt: str,
        *,
        repository_path: str,
        access_mode: AccessMode = AccessMode.workspace_write,
    ) -> tuple[dict[str, Any], RunState]:
        repository_path = await self.require_project(repository_path)
        try:
            thread = await self.client.thread_start(
                cwd=repository_path,
                sandbox=access_mode.sandbox,
                approval_mode=ApprovalMode.auto_review,
            )
        except Exception as exc:
            raise CodexServiceError("Codex could not create that task") from exc
        run = await self._start_on_thread(thread, prompt, access_mode)
        return {
            "id": thread.id,
            "name": "New task",
            "preview": prompt.strip()[:500],
            "repository_path": repository_path,
            "status": "active",
            "created_at": None,
            "updated_at": None,
        }, run

    async def _consume(self, run: RunState) -> None:
        terminal_status = "completed"
        try:
            async for notification in run.handle.stream():
                method = notification.method.replace("/", ".")
                payload = notification.payload
                data = _json_value(payload)
                item = getattr(payload, "item", None)
                item_data = _json_value(item) if item is not None else None
                item_id = item_data.get("id") if isinstance(item_data, dict) else None
                if method == "turn.plan.updated" and isinstance(data, dict):
                    run.plan = list(data.get("plan") or [])
                elif method == "turn.diff.updated" and isinstance(data, dict):
                    run.diff = str(data.get("diff") or "")
                elif method == "item.completed" and isinstance(item_data, dict):
                    if item_data.get("type") == "agentMessage":
                        phase = item_data.get("phase")
                        if phase in (None, "finalAnswer", "final_answer"):
                            run.final_response = str(item_data.get("text") or "").strip() or None
                elif method == "turn.completed" and isinstance(data, dict):
                    turn_data = data.get("turn")
                    if isinstance(turn_data, dict):
                        terminal_status = str(turn_data.get("status") or terminal_status)
                run.append(method, data, item_id=item_id)
            run.status = terminal_status
        except asyncio.CancelledError:
            run.status = "interrupted"
            raise
        except Exception as exc:
            run.status = "failed"
            run.error = str(exc)
            run.append("run.failed", {"message": str(exc)})
        finally:
            run.completed_at = time.time()
            if self.active_by_thread.get(run.thread_id) == run.id:
                self.active_by_thread.pop(run.thread_id, None)
            run.append(
                "run.finished",
                {
                    "status": run.status,
                    "final_response": run.final_response,
                    "error": run.error,
                },
            )
            run.completion.set()

    async def steer(self, run_id: str, prompt: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        if run.status != "running":
            raise ValueError("Only an active run can be steered")
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("prompt is required")
        try:
            await run.handle.steer(prompt)
        except Exception as exc:
            raise CodexServiceError("Codex could not steer that run") from exc
        run.append("run.steered", {"prompt": prompt})
        return run.summary()

    async def interrupt(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        if run.status != "running":
            return run.summary()
        try:
            await run.handle.interrupt()
        except Exception as exc:
            raise CodexServiceError("Codex could not interrupt that run") from exc
        run.append("run.interrupt.requested", {})
        return run.summary()

    async def wait(self, run_id: str) -> RunState:
        run = self.get_run(run_id)
        await run.completion.wait()
        return run

    async def events(self, run_id: str, *, after: int = 0) -> AsyncIterator[dict[str, Any]]:
        run = self.get_run(run_id)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=MAX_RUN_EVENTS)
        run.subscribers.add(queue)
        try:
            cursor = after
            for event in tuple(run.events):
                if event["seq"] > cursor:
                    cursor = event["seq"]
                    yield event
            while run.status == "running" or not queue.empty():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    if event["seq"] <= cursor:
                        continue
                    cursor = event["seq"]
                    yield event
                except TimeoutError:
                    yield {"type": "heartbeat", "run_id": run.id, "seq": run._seq}
        finally:
            run.subscribers.discard(queue)

    async def rename_thread(self, thread_id: str, name: str) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ValueError("name is required")
        try:
            thread = await self.client.thread_resume(thread_id)
            await thread.set_name(name)
        except Exception as exc:
            raise CodexServiceError("Codex could not rename that task") from exc
        detail = await self.service.read_thread(thread_id)
        return detail["thread"]

    async def fork_thread(self, thread_id: str) -> dict[str, Any]:
        try:
            thread = await self.client.thread_fork(thread_id)
            detail = await self.service.read_thread(thread.id)
        except Exception as exc:
            raise CodexServiceError("Codex could not fork that task") from exc
        return detail["thread"]

    async def archive_thread(self, thread_id: str) -> None:
        if self.active_run(thread_id) is not None:
            raise ActiveRunError(self.active_by_thread[thread_id])
        try:
            await self.client.thread_archive(thread_id)
        except Exception as exc:
            raise CodexServiceError("Codex could not archive that task") from exc

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        active = [run for run in self.runs.values() if run.status == "running"]
        for run in active:
            with contextlib.suppress(Exception):
                await run.handle.interrupt()
        tasks = [run.task for run in active if run.task is not None]
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=10)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                with contextlib.suppress(Exception):
                    task.result()
        close = getattr(self.client, "close", None)
        if close is not None:
            await close()
