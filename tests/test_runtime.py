from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from calldex.runtime import (
    MAX_RUN_EVENTS,
    AccessMode,
    ActiveRunError,
    CodexRuntime,
    RunState,
)


class Model:
    def __init__(self, data: dict[str, Any], *, item: Any = None) -> None:
        self.data = data
        self.item = item

    def model_dump(self, **_: Any) -> dict[str, Any]:
        return self.data


class FakeHandle:
    def __init__(self) -> None:
        self.id = "turn-1"
        self.release = asyncio.Event()
        self.steered: list[str] = []
        self.interrupted = False

    async def stream(self) -> Any:
        yield SimpleNamespace(
            method="turn/plan/updated",
            payload=Model({"plan": [{"step": "Inspect", "status": "inProgress"}]}),
        )
        yield SimpleNamespace(
            method="turn/diff/updated",
            payload=Model({"diff": "diff --git a/a.py b/a.py"}),
        )
        await self.release.wait()
        item = Model({"id": "message-1", "type": "agentMessage", "text": "Done", "phase": "finalAnswer"})
        yield SimpleNamespace(
            method="item/completed",
            payload=Model({"item": item.model_dump()}, item=item),
        )
        yield SimpleNamespace(
            method="turn/completed",
            payload=Model({"turn": {"id": self.id, "status": "completed"}}),
        )

    async def steer(self, prompt: str) -> None:
        self.steered.append(prompt)

    async def interrupt(self) -> None:
        self.interrupted = True
        self.release.set()


class FakeThread:
    def __init__(self, thread_id: str) -> None:
        self.id = thread_id
        self.handle = FakeHandle()

    async def turn(self, *_: Any, **__: Any) -> FakeHandle:
        return self.handle


class FakeClient:
    def __init__(self) -> None:
        self.thread = FakeThread("thread-1")
        self.closed = False

    async def thread_resume(self, thread_id: str, **_: Any) -> FakeThread:
        assert thread_id == "thread-1"
        return self.thread

    async def thread_list(self, **_: Any) -> Any:
        return SimpleNamespace(data=[], next_cursor=None)

    async def close(self) -> None:
        self.closed = True


class FakeDesktopBridge:
    def __init__(self) -> None:
        self.interrupts: list[tuple[str, str]] = []
        self.closed = False

    def add_broadcast_handler(self, *_: Any) -> Any:
        return lambda: None

    async def interrupt_turn(self, thread_id: str, owner_client_id: str) -> None:
        self.interrupts.append((thread_id, owner_client_id))

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_runtime_streams_steers_and_finishes_one_run_per_thread() -> None:
    client = FakeClient()
    runtime = CodexRuntime(client, default_cwd="/tmp/project")

    run = await runtime.start_run("thread-1", "Inspect it")
    with pytest.raises(ActiveRunError):
        await runtime.start_run("thread-1", "Do something else")

    await runtime.steer(run.id, "Focus on tests")
    assert client.thread.handle.steered == ["Focus on tests"]

    client.thread.handle.release.set()
    completed = await runtime.wait(run.id)
    assert completed.status == "completed"
    assert completed.final_response == "Done"
    assert completed.plan[0]["step"] == "Inspect"
    assert completed.diff.startswith("diff --git")
    assert runtime.active_run("thread-1") is None
    assert any(event["type"] == "run.finished" for event in completed.events)
    await runtime.close()
    assert client.closed is True


@pytest.mark.asyncio
async def test_runtime_replays_events_and_interrupts() -> None:
    client = FakeClient()
    runtime = CodexRuntime(client, default_cwd="/tmp/project")
    run = await runtime.start_run("thread-1", "Inspect it", access_mode=AccessMode.read_only)
    await asyncio.sleep(0)

    replay = runtime.events(run.id)
    first = await anext(replay)
    assert first["type"] == "run.started"
    await replay.aclose()

    await runtime.interrupt(run.id)
    await runtime.wait(run.id)
    assert client.thread.handle.interrupted is True
    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_close_detaches_without_interrupting_desktop_turn() -> None:
    client = FakeClient()
    bridge = FakeDesktopBridge()
    runtime = CodexRuntime(client, default_cwd="/tmp/project", desktop_bridge=bridge)  # type: ignore[arg-type]
    run = RunState(
        id="desktop-run",
        thread_id="thread-1",
        turn_id="turn-1",
        access_mode=AccessMode.workspace_write,
        handle=None,
        backend="desktop_ipc",
        owner_client_id="desktop-owner",
    )
    runtime.runs[run.id] = run
    runtime.active_by_thread[run.thread_id] = run.id

    await runtime.close()

    assert bridge.interrupts == []
    assert bridge.closed is True
    assert client.closed is True


def test_run_event_buffer_is_bounded() -> None:
    run = RunState(
        id="run-1",
        thread_id="thread-1",
        turn_id="turn-1",
        access_mode=AccessMode.workspace_write,
        handle=SimpleNamespace(),
    )
    for index in range(MAX_RUN_EVENTS + 25):
        run.append("test", {"index": index})
    assert len(run.events) == MAX_RUN_EVENTS
    assert run.events[0]["seq"] == 26
