from __future__ import annotations

import os
import asyncio
import contextlib
from pathlib import Path
from typing import Any, Protocol

from dotenv import load_dotenv
from google.genai import types
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    ToolError,
    cli,
    function_tool,
)
from livekit.plugins.google.realtime import RealtimeModel
from openai_codex import AsyncCodex, CodexConfig

from .codex_service import CodexService, CodexServiceError, ThreadNotFoundError
from .runtime import AccessMode, CodexRuntime, RunNotFoundError

DEFAULT_GEMINI_LIVE_MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"
DEFAULT_CODEX_DESKTOP_BIN = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
MAX_TOOL_RESULT_CHARS = 4000

AGENT_INSTRUCTIONS = """
You are Calldex, a voice agent that helps the user operate Codex during a LiveKit call.

Use list_codex_threads and select_codex_thread to find existing work. Use
list_codex_projects and start_codex_task for new work. codex_reply starts a run or steers the
active run and returns immediately; use control_codex_turn to check progress or stop it.
Interpret references like "the second one" using your latest list. Use inspect_codex_thread
for the latest messages, plan, changes, or run state. Voice may use read-only or workspace
write access, but never full access and never archive a task. Keep spoken responses concise.
""".strip()


def load_calldex_env() -> None:
    """Load the env file from the directory where Calldex was launched."""
    load_dotenv(Path.cwd() / ".env")


def _repo_cwd() -> Path:
    return Path(os.getenv("CALLDEX_CODEX_CWD", str(Path.cwd()))).expanduser().resolve()


def resolve_codex_bin() -> str | None:
    """Prefer a current local Codex runtime while retaining the SDK fallback."""
    configured = os.getenv("CALLDEX_CODEX_BIN")
    if configured:
        path = Path(configured).expanduser().resolve()
        if not path.is_file() or not os.access(path, os.X_OK):
            raise RuntimeError(f"CALLDEX_CODEX_BIN is not executable: {path}")
        return str(path)

    if DEFAULT_CODEX_DESKTOP_BIN.is_file() and os.access(DEFAULT_CODEX_DESKTOP_BIN, os.X_OK):
        return str(DEFAULT_CODEX_DESKTOP_BIN)
    return None


class CodexTurnResult(Protocol):
    final_response: str | None


class CodexThread(Protocol):
    id: str

    async def run(self, prompt: str, **kwargs: Any) -> CodexTurnResult: ...


class CodexClient(Protocol):
    async def thread_start(self, **kwargs: Any) -> CodexThread: ...

    async def thread_resume(self, thread_id: str, **kwargs: Any) -> CodexThread: ...


_dashboard_runtime: CodexRuntime | None = None


def set_dashboard_runtime(runtime: CodexRuntime | None) -> None:
    global _dashboard_runtime
    _dashboard_runtime = runtime


def _compact_tool_result(result: str) -> str:
    result = result.strip()
    if len(result) <= MAX_TOOL_RESULT_CHARS:
        return result
    return result[:MAX_TOOL_RESULT_CHARS].rstrip() + "\n...[truncated]"


# UNVERIFIED: LiveKit Docs MCP was unavailable; signatures were checked against docs.livekit.io.
class CalldexAgent(Agent):
    def __init__(
        self,
        *,
        codex: CodexClient | None = None,
        runtime: CodexRuntime | None = None,
        cwd: str,
        room: Any | None = None,
    ) -> None:
        super().__init__(instructions=AGENT_INSTRUCTIONS)
        if runtime is None:
            if codex is None:
                raise ValueError("codex or runtime is required")
            runtime = CodexRuntime(codex, default_cwd=cwd)
            self._owns_runtime = True
        else:
            self._owns_runtime = False
        self._runtime = runtime
        self._codex = runtime.client
        self._service = runtime.service
        self._cwd = cwd
        self._room = room
        self._selected_thread_id: str | None = None
        self._selection_tasks: set[asyncio.Task[None]] = set()
        self._run_watchers: set[asyncio.Task[None]] = set()
        self._watched_run_ids: set[str] = set()
        self._latest_threads: list[dict[str, Any]] = []
        self._latest_projects: list[dict[str, Any]] = []
        self._last_run_by_thread: dict[str, str] = {}

    async def _publish_selection(self) -> None:
        if self._room is None or not self._selected_thread_id:
            return
        run = self._runtime.active_run(self._selected_thread_id)
        await self._room.local_participant.set_attributes(
            {
                "calldex.activeThreadId": self._selected_thread_id,
                "calldex.activeRunId": run.id if run else "",
                "calldex.runStatus": run.status if run else "idle",
            }
        )

    async def _select(self, thread_id: str) -> dict[str, str]:
        try:
            result = await self._service.require_thread(thread_id)
        except ThreadNotFoundError as exc:
            raise ToolError("That Codex thread does not exist.") from exc
        except CodexServiceError as exc:
            raise ToolError("Codex could not validate that thread.") from exc
        try:
            await self._runtime.observe_thread(thread_id)
        except CodexServiceError as exc:
            raise ToolError("Codex desktop task ownership is temporarily unavailable.") from exc
        self._selected_thread_id = thread_id
        await self._publish_selection()
        thread = result["thread"]
        return {
            "thread_id": thread_id,
            "name": thread["name"],
            "repository_path": thread["repository_path"],
            "status": "selected",
        }

    def _resolve_reference(self, reference: str, items: list[dict[str, Any]], id_key: str) -> str:
        value = reference.strip()
        lowered = value.lower()
        ordinals = {
            "first": 1,
            "second": 2,
            "third": 3,
            "fourth": 4,
            "fifth": 5,
        }
        position = int(value) if value.isdigit() else ordinals.get(lowered)
        if position is not None:
            if 1 <= position <= len(items):
                return str(items[position - 1][id_key])
            raise ToolError("That list position is not available.")
        for item in items:
            if str(item[id_key]) == value:
                return value
        matches = [
            item for item in items
            if lowered in str(item.get("name", "")).lower()
            or lowered in str(item.get("repository_path", item.get("path", ""))).lower()
        ]
        if len(matches) == 1:
            return str(matches[0][id_key])
        if len(matches) > 1:
            raise ToolError("That reference is ambiguous. Please name the repository or task more precisely.")
        raise ToolError("I could not find that item in the latest list.")

    def _watch(self, run_id: str) -> None:
        if run_id in self._watched_run_ids:
            return
        self._watched_run_ids.add(run_id)
        task = asyncio.create_task(self._announce_completion(run_id))
        self._run_watchers.add(task)
        def finished(completed: asyncio.Task[None]) -> None:
            self._run_watchers.discard(completed)
            self._watched_run_ids.discard(run_id)
        task.add_done_callback(finished)

    async def _announce_completion(self, run_id: str) -> None:
        try:
            run = await self._runtime.wait(run_id)
            await self._publish_selection()
            result = _compact_tool_result(run.final_response or run.error or f"The Codex run {run.status}.")
            with contextlib.suppress(Exception):
                self.session.generate_reply(
                    user_input=f"Codex run status: {run.status}. Result:\n{result}",
                    instructions="Briefly notify the user of the Codex run status and summarize the result in one or two sentences.",
                    tools=[],
                )
        except (RunNotFoundError, asyncio.CancelledError):
            return

    @function_tool(name="list_codex_threads")
    async def list_codex_threads(self, limit: int = 5) -> dict[str, Any]:
        """List recent Codex threads in a concise, speakable form.

        Args:
            limit: Number of recent threads to list, between 1 and 10.
        """
        if not 1 <= limit <= 10:
            raise ToolError("Choose between one and ten recent threads.")
        try:
            result = await self._service.list_threads(limit=limit)
        except CodexServiceError as exc:
            raise ToolError("Codex could not list recent threads.") from exc
        self._latest_threads = result["threads"]
        return {
            "threads": [
                {
                    "position": index,
                    "thread_id": thread["id"],
                    "name": thread["name"],
                    "repository": thread["repository_path"],
                    "preview": _compact_tool_result(thread["preview"][:240]),
                }
                for index, thread in enumerate(result["threads"], start=1)
            ]
        }

    @function_tool(name="select_codex_thread")
    async def select_codex_thread(self, thread_id: str) -> dict[str, str]:
        """Select an existing Codex thread for subsequent voice requests.

        Args:
            thread_id: Exact Codex thread ID from list_codex_threads.
        """
        selected = self._resolve_reference(thread_id, self._latest_threads, "id") if self._latest_threads else thread_id
        return await self._select(selected)

    @function_tool(name="inspect_codex_thread")
    async def inspect_codex_thread(self, thread_id: str | None = None) -> dict[str, Any]:
        """Inspect the selected task's latest messages, plan, changes, and run status."""
        selected = thread_id or self._selected_thread_id
        if not selected:
            raise ToolError("Select a Codex thread before inspecting it.")
        try:
            detail = await self._service.read_thread(selected)
        except CodexServiceError as exc:
            raise ToolError("Codex could not inspect that task.") from exc
        run = self._runtime.active_run(selected)
        if run is None and selected in self._last_run_by_thread:
            with contextlib.suppress(RunNotFoundError):
                run = self._runtime.get_run(self._last_run_by_thread[selected])
        messages = [event for event in detail["events"] if event["type"] in {"userMessage", "agentMessage"}]
        changes = [event for event in detail["events"] if event["type"] == "fileChange"]
        return {
            "thread": detail["thread"],
            "latest_messages": [event["summary"] for event in messages[-4:]],
            "recent_changes": [event["summary"] for event in changes[-8:]],
            "run": run.summary() if run else None,
        }

    @function_tool(name="list_codex_projects")
    async def list_codex_projects(self) -> dict[str, Any]:
        """List known repositories where a new Codex task may be started."""
        try:
            self._latest_projects = await self._runtime.list_projects()
        except CodexServiceError as exc:
            raise ToolError("Codex could not list known repositories.") from exc
        return {
            "projects": [
                {"position": index, **project}
                for index, project in enumerate(self._latest_projects[:10], start=1)
            ]
        }

    @function_tool(name="start_codex_task")
    async def start_codex_task(
        self,
        prompt: str,
        project: str,
        access_mode: str = "workspace_write",
    ) -> dict[str, Any]:
        """Start new Codex work in a known repository and return immediately.

        Args:
            prompt: Complete description of the repository work.
            project: Repository path, name, or position from list_codex_projects.
            access_mode: read_only or workspace_write. Full access is browser-only.
        """
        if access_mode not in {AccessMode.read_only.value, AccessMode.workspace_write.value}:
            raise ToolError("Voice can use read-only or workspace-write access, not full access.")
        if not self._latest_projects:
            self._latest_projects = await self._runtime.list_projects()
        path = self._resolve_reference(project, self._latest_projects, "path")
        try:
            thread, run = await self._runtime.create_thread(
                prompt,
                repository_path=path,
                access_mode=AccessMode(access_mode),
            )
        except (CodexServiceError, ValueError) as exc:
            raise ToolError("Codex could not start that task.") from exc
        self._selected_thread_id = thread["id"]
        self._last_run_by_thread[thread["id"]] = run.id
        await self._publish_selection()
        self._watch(run.id)
        return {"status": "started", "thread": thread, "run": run.summary()}

    @function_tool(name="codex_reply")
    async def codex_reply(
        self,
        prompt: str,
        thread_id: str | None = None,
        access_mode: str = "workspace_write",
    ) -> dict[str, Any]:
        """Continue an existing Codex repository task.

        Args:
            prompt: The user's follow-up request for that thread.
            thread_id: Optional Codex thread ID. Defaults to the selected thread.
        """
        selected = thread_id or self._selected_thread_id
        if not selected:
            raise ToolError("Select a Codex thread before continuing it.")
        if thread_id and self._latest_threads:
            selected = self._resolve_reference(thread_id, self._latest_threads, "id")
        if access_mode not in {AccessMode.read_only.value, AccessMode.workspace_write.value}:
            raise ToolError("Voice cannot enable full access.")
        self._selected_thread_id = selected
        await self._publish_selection()
        try:
            await self._runtime.observe_thread(selected)
        except CodexServiceError as exc:
            raise ToolError("Codex desktop task ownership is temporarily unavailable.") from exc
        await self._publish_selection()
        active = self._runtime.active_run(selected)
        if active:
            try:
                await self._runtime.steer(active.id, prompt)
            except CodexServiceError as exc:
                raise ToolError("Codex could not steer that run.") from exc
            self._last_run_by_thread[selected] = active.id
            self._watch(active.id)
            return {"status": "steered", "run": active.summary()}
        try:
            run = await self._runtime.start_run(selected, prompt, access_mode=AccessMode(access_mode))
        except (CodexServiceError, ValueError) as exc:
            raise ToolError("Codex could not start that request.") from exc
        await self._publish_selection()
        self._last_run_by_thread[selected] = run.id
        self._watch(run.id)
        return {"status": "started", "run": run.summary()}

    @function_tool(name="control_codex_turn")
    async def control_codex_turn(self, action: str = "status") -> dict[str, Any]:
        """Check or stop the selected task's active Codex run.

        Args:
            action: status or stop.
        """
        if action not in {"status", "stop"}:
            raise ToolError("The action must be status or stop.")
        if not self._selected_thread_id:
            raise ToolError("Select a Codex thread first.")
        try:
            await self._runtime.observe_thread(self._selected_thread_id)
        except CodexServiceError as exc:
            raise ToolError("Codex desktop task ownership is temporarily unavailable.") from exc
        await self._publish_selection()
        run = self._runtime.active_run(self._selected_thread_id)
        if run is None and self._selected_thread_id in self._last_run_by_thread:
            with contextlib.suppress(RunNotFoundError):
                run = self._runtime.get_run(self._last_run_by_thread[self._selected_thread_id])
        if not run:
            return {"status": "idle", "thread_id": self._selected_thread_id}
        if action == "stop" and run.status == "running":
            await self._runtime.interrupt(run.id)
        return {"status": run.status, "run": run.summary()}

    @function_tool(name="manage_codex_thread")
    async def manage_codex_thread(
        self,
        action: str,
        name: str | None = None,
    ) -> dict[str, Any]:
        """Rename or fork the selected Codex task. Archival is browser-only.

        Args:
            action: rename or fork.
            name: New name required for rename.
        """
        if not self._selected_thread_id:
            raise ToolError("Select a Codex thread first.")
        try:
            if action == "rename":
                if not name:
                    raise ToolError("Provide a new task name.")
                thread = await self._runtime.rename_thread(self._selected_thread_id, name)
            elif action == "fork":
                thread = await self._runtime.fork_thread(self._selected_thread_id)
                self._selected_thread_id = thread["id"]
                await self._publish_selection()
            else:
                raise ToolError("Voice can rename or fork a task; archive is browser-only.")
        except CodexServiceError as exc:
            raise ToolError("Codex could not manage that task.") from exc
        return {"status": "renamed" if action == "rename" else "forked", "thread": thread}

    async def _handle_requested_selection(self, thread_id: str) -> None:
        if not thread_id or thread_id == self._selected_thread_id:
            return
        try:
            await self._select(thread_id)
        except ToolError:
            return

    async def on_enter(self) -> None:
        if self._room is None:
            return

        def on_attributes_changed(changed: dict[str, str], participant: Any) -> None:
            if participant == self._room.local_participant:
                return
            requested = changed.get("calldex.requestedThreadId")
            if not requested and "calldex.requestedThreadNonce" in changed:
                requested = participant.attributes.get("calldex.requestedThreadId")
            if requested:
                task = asyncio.create_task(self._handle_requested_selection(requested))
                self._selection_tasks.add(task)
                task.add_done_callback(self._selection_tasks.discard)

        self._on_attributes_changed = on_attributes_changed
        self._room.on("participant_attributes_changed", on_attributes_changed)
        for participant in self._room.remote_participants.values():
            requested = participant.attributes.get("calldex.requestedThreadId")
            if requested:
                await self._handle_requested_selection(requested)

    async def on_exit(self) -> None:
        if self._room is not None and hasattr(self, "_on_attributes_changed"):
            self._room.off("participant_attributes_changed", self._on_attributes_changed)
        for task in self._selection_tasks:
            task.cancel()
        if self._selection_tasks:
            await asyncio.gather(*self._selection_tasks, return_exceptions=True)
        for task in self._run_watchers:
            task.cancel()
        if self._run_watchers:
            await asyncio.gather(*self._run_watchers, return_exceptions=True)
        self._watched_run_ids.clear()
        if self._owns_runtime:
            await self._runtime.close()


def build_codex_client(*, cwd: Path) -> AsyncCodex:
    return AsyncCodex(
        CodexConfig(
            codex_bin=resolve_codex_bin(),
            cwd=str(cwd),
            env=os.environ.copy(),
        )
    )


def build_gemini_model() -> RealtimeModel:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is required")

    return RealtimeModel(
        model=os.getenv("GEMINI_LIVE_MODEL", DEFAULT_GEMINI_LIVE_MODEL),
        voice=os.getenv("GEMINI_LIVE_VOICE", "Puck"),
        api_key=api_key,
        api_version="v1alpha",
        instructions=AGENT_INSTRUCTIONS,
        enable_affective_dialog=True,
        proactivity=True,
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                disabled=False,
                silence_duration_ms=500,
            ),
        ),
        thinking_config=types.ThinkingConfig(
            thinking_budget=0,
            include_thoughts=False,
        ),
        tool_behavior=types.Behavior.NON_BLOCKING,
        tool_response_scheduling=types.FunctionResponseScheduling.WHEN_IDLE,
    )


def build_agent(*, room: Any | None = None, runtime: CodexRuntime | None = None) -> Agent:
    cwd = _repo_cwd()
    return CalldexAgent(
        codex=None if runtime else build_codex_client(cwd=cwd),
        runtime=runtime,
        cwd=str(cwd),
        room=room,
    )


# AgentServer captures its LiveKit configuration when it is constructed.
load_calldex_env()

# UNVERIFIED: LiveKit Docs MCP was unavailable; signatures were checked against docs.livekit.io.
server = AgentServer()


@server.rtc_session(agent_name="calldex")
async def entrypoint(ctx: JobContext) -> None:
    load_calldex_env()
    session = AgentSession(
        llm=build_gemini_model(),
        max_tool_steps=int(os.getenv("CALLDEX_MAX_TOOL_STEPS", "6")),
    )
    await session.start(
        agent=build_agent(room=ctx.room, runtime=_dashboard_runtime),
        room=ctx.room,
    )


def main() -> None:
    load_calldex_env()
    cli.run_app(server)
