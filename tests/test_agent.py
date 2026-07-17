from __future__ import annotations

from dataclasses import dataclass
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from google.genai import types
from livekit.agents import ToolError

import calldex.agent as agent_module
from calldex.agent import (
    AGENT_INSTRUCTIONS,
    DEFAULT_GEMINI_LIVE_MODEL,
    CalldexAgent,
    build_gemini_model,
    resolve_codex_bin,
)


@dataclass
class FakeResult:
    final_response: str | None


class FakeThread:
    def __init__(self, thread_id: str, response: str | None) -> None:
        self.id = thread_id
        self.response = response
        self.prompts: list[str] = []
        self.handles: list[FakeHandle] = []

    async def run(self, prompt: str, **_: Any) -> FakeResult:
        self.prompts.append(prompt)
        return FakeResult(final_response=self.response)

    async def turn(self, prompt: str, **_: Any) -> Any:
        self.prompts.append(prompt)
        handle = FakeHandle(f"turn-{len(self.handles) + 1}")
        self.handles.append(handle)
        return handle

    async def set_name(self, name: str) -> None:
        self.name = name

    async def read(self, *, include_turns: bool = False) -> Any:
        return SimpleNamespace(
            thread=SimpleNamespace(
                id=self.id,
                name=f"Thread {self.id}",
                preview="A recent task",
                cwd="/tmp/other-project",
                status="idle",
                created_at=1,
                updated_at=2,
                turns=[],
            )
        )


class FakeCodex:
    def __init__(self, response: str | None = "Done") -> None:
        self.response = response
        self.started: list[dict[str, Any]] = []
        self.resumed: list[dict[str, Any]] = []
        self.threads: list[FakeThread] = []

    async def thread_start(self, **kwargs: Any) -> FakeThread:
        self.started.append(kwargs)
        thread = FakeThread("thread-new", self.response)
        self.threads.append(thread)
        return thread

    async def thread_resume(self, thread_id: str, **kwargs: Any) -> FakeThread:
        self.resumed.append({"thread_id": thread_id, **kwargs})
        thread = FakeThread(thread_id, self.response)
        self.threads.append(thread)
        return thread

    async def thread_list(self, **_: Any) -> Any:
        return SimpleNamespace(
            data=[
                SimpleNamespace(
                    id="thread-existing",
                    name="Existing task",
                    preview="Continue the task",
                    cwd="/tmp/other-project",
                    status="idle",
                    created_at=1,
                    updated_at=2,
                )
            ],
            next_cursor=None,
        )

    async def close(self) -> None:
        return None


class FakeHandle:
    def __init__(self, turn_id: str) -> None:
        self.id = turn_id
        self.steered: list[str] = []
        self.done = asyncio.Event()

    async def steer(self, prompt: str) -> None:
        self.steered.append(prompt)

    async def interrupt(self) -> None:
        self.done.set()

    async def stream(self) -> Any:
        yield SimpleNamespace(method="turn/started", payload={"turn": {"id": self.id}})
        await self.done.wait()
        yield SimpleNamespace(
            method="turn/completed",
            payload={"turn": {"id": self.id, "status": "interrupted"}},
        )


def test_agent_has_a_small_voice_focused_tool_surface() -> None:
    agent = CalldexAgent(codex=FakeCodex(), cwd="/tmp/project")

    assert "Keep spoken responses concise" in AGENT_INSTRUCTIONS
    assert [tool.id for tool in agent.tools] == [
        "codex_reply",
        "control_codex_turn",
        "inspect_codex_thread",
        "list_codex_projects",
        "list_codex_threads",
        "manage_codex_thread",
        "select_codex_thread",
        "start_codex_task",
    ]


def test_gemini_model_uses_the_gemini_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_LIVE_MODEL", raising=False)

    model = build_gemini_model()

    assert model._opts.api_key == "test-key"
    assert model._opts.model == DEFAULT_GEMINI_LIVE_MODEL
    assert model._opts.thinking_config.thinking_budget == 0
    assert model._opts.thinking_config.thinking_level is None
    assert model._opts.thinking_config.include_thoughts is False
    assert model._opts.tool_behavior == types.Behavior.NON_BLOCKING
    assert model._opts.tool_response_scheduling == types.FunctionResponseScheduling.WHEN_IDLE
    assert model._opts.enable_affective_dialog is True
    assert model._opts.proactivity is True
    assert model._opts.api_version == "v1alpha"
    vad = model._opts.realtime_input_config.automatic_activity_detection
    assert vad is not None
    assert vad.disabled is False
    assert vad.silence_duration_ms == 500


def test_codex_binary_can_be_overridden(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    codex_bin = tmp_path / "codex"
    codex_bin.touch(mode=0o755)
    monkeypatch.setenv("CALLDEX_CODEX_BIN", str(codex_bin))

    assert resolve_codex_bin() == str(codex_bin.resolve())


def test_invalid_codex_binary_override_fails_clearly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    missing = tmp_path / "missing-codex"
    monkeypatch.setenv("CALLDEX_CODEX_BIN", str(missing))

    with pytest.raises(RuntimeError, match="CALLDEX_CODEX_BIN is not executable"):
        resolve_codex_bin()


def test_codex_desktop_binary_is_preferred_with_sdk_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    desktop_bin = tmp_path / "desktop-codex"
    desktop_bin.touch(mode=0o755)
    monkeypatch.delenv("CALLDEX_CODEX_BIN", raising=False)
    monkeypatch.setattr(agent_module, "DEFAULT_CODEX_DESKTOP_BIN", desktop_bin)

    assert resolve_codex_bin() == str(desktop_bin)

    desktop_bin.unlink()
    assert resolve_codex_bin() is None


@pytest.mark.asyncio
async def test_codex_starts_and_steers_sdk_threads_asynchronously() -> None:
    codex = FakeCodex()
    agent = CalldexAgent(codex=codex, cwd="/tmp/project")

    started = await agent.start_codex_task(
        prompt="Inspect the repository",
        project="/tmp/project",
    )
    continued = await agent.codex_reply(prompt="Now run the tests")

    assert started["status"] == "started"
    assert continued["status"] == "steered"
    assert codex.threads[0].prompts == ["Inspect the repository"]
    assert codex.started[0]["cwd"] == str(Path("/tmp/project").resolve())
    assert codex.threads[0].handles[0].steered == ["Now run the tests"]
    await agent.control_codex_turn("stop")


@pytest.mark.asyncio
async def test_voice_can_list_select_and_reply_to_the_selected_thread() -> None:
    codex = FakeCodex()
    agent = CalldexAgent(codex=codex, cwd="/tmp/project")

    recent = await agent.list_codex_threads(limit=5)
    selected = await agent.select_codex_thread(thread_id="thread-existing")
    continued = await agent.codex_reply(prompt="Continue it")

    assert recent["threads"][0]["position"] == 1
    assert recent["threads"][0]["thread_id"] == "thread-existing"
    assert selected["status"] == "selected"
    assert continued["status"] == "started"
    await agent.control_codex_turn("stop")


@pytest.mark.asyncio
async def test_reply_requires_a_selection() -> None:
    agent = CalldexAgent(codex=FakeCodex(), cwd="/tmp/project")

    with pytest.raises(ToolError, match="Select a Codex thread"):
        await agent.codex_reply(prompt="Continue it")


@pytest.mark.asyncio
async def test_browser_attribute_selects_and_agent_confirms_thread() -> None:
    class LocalParticipant:
        def __init__(self) -> None:
            self.attributes: dict[str, str] = {}

        async def set_attributes(self, attributes: dict[str, str]) -> None:
            self.attributes.update(attributes)

    class Room:
        def __init__(self) -> None:
            self.local_participant = LocalParticipant()
            self.remote_participants: dict[str, Any] = {}
            self.callback: Any = None

        def on(self, _: str, callback: Any) -> None:
            self.callback = callback

        def off(self, _: str, callback: Any) -> None:
            assert callback is self.callback

    room = Room()
    agent = CalldexAgent(codex=FakeCodex(), cwd="/tmp/project", room=room)
    await agent.on_enter()

    room.callback(
        {"calldex.requestedThreadNonce": "request-1"},
        SimpleNamespace(attributes={"calldex.requestedThreadId": "thread-existing"}),
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert room.local_participant.attributes["calldex.activeThreadId"] == "thread-existing"
    await agent.on_exit()


@pytest.mark.asyncio
async def test_voice_rejects_full_access() -> None:
    agent = CalldexAgent(codex=FakeCodex(), cwd="/tmp/project")

    with pytest.raises(ToolError, match="full access"):
        await agent.start_codex_task(
            prompt="Do some work",
            project="/tmp/project",
            access_mode="full_access",
        )
