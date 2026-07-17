from __future__ import annotations

import asyncio
import json
import struct
from pathlib import Path
from typing import Any

import pytest

from calldex.desktop_ipc import (
    DesktopIpcBridge,
    DesktopIpcIncompatible,
    DesktopIpcMode,
    DesktopIpcUnavailable,
    DesktopOwner,
    MAX_FRAME_BYTES,
    encode_frame,
    read_frame,
)
from calldex.runtime import AccessMode, CodexRuntime
from calldex.codex_service import CodexServiceError


@pytest.mark.asyncio
async def test_bridge_follows_and_routes_owner_requests() -> None:
    requests: list[dict[str, Any]] = []
    reader = asyncio.StreamReader()

    class Writer:
        closed = False

        def write(self, frame: bytes) -> None:
            length = struct.unpack("<I", frame[:4])[0]
            message = json.loads(frame[4:4 + length])
            asyncio.create_task(respond(message))

        async def drain(self) -> None:
            pass

        def is_closing(self) -> bool:
            return self.closed

        def close(self) -> None:
            self.closed = True
            reader.feed_eof()

        async def wait_closed(self) -> None:
            pass

    async def feed(message: dict[str, Any], *, split: bool = False) -> None:
        frame = encode_frame(message)
        if split:
            for byte in frame:
                reader.feed_data(bytes([byte]))
                await asyncio.sleep(0)
        else:
            reader.feed_data(frame)

    async def respond(message: dict[str, Any]) -> None:
        if message["type"] == "broadcast":
            assert message["method"] == "thread-stream-following-changed"
            return
        requests.append(message)
        if message["method"] == "thread-follower-load-complete-history":
            await feed({
            "type": "broadcast",
            "method": "thread-stream-state-changed",
            "version": 11,
            "sourceClientId": "desktop-owner",
            "params": {
                "hostId": "local",
                "conversationId": "thread-1",
                "change": {
                    "type": "snapshot",
                    "revision": 4,
                    "conversationState": {"id": "thread-1", "turns": []},
                },
            },
            }, split=True)
        await feed({
            "type": "response",
            "requestId": message["requestId"],
            "resultType": "success",
            "method": message["method"],
            "handledByClientId": "desktop-owner",
            "result": {"revision": 4} if message["method"] == "thread-follower-load-complete-history" else {"ok": True},
        })

    bridge = DesktopIpcBridge(request_timeout=1)
    bridge._reader = reader
    bridge._writer = Writer()  # type: ignore[assignment]
    bridge.client_id = "calldex-client"
    bridge.connection_state = "connected"
    bridge.compatible = True
    bridge._reader_task = asyncio.create_task(bridge._read_loop())
    try:
        owner = await bridge.follow("thread-1")
        assert owner and owner.client_id == "desktop-owner" and owner.revision == 4
        await bridge.update_thread_settings("thread-1", owner.client_id, {"sandboxPolicy": {"type": "readOnly"}})
        await bridge.start_turn("thread-1", owner.client_id, "Do it", turn_start_params={"input": []})
        await bridge.interrupt_turn("thread-1", owner.client_id)
        assert [request["method"] for request in requests] == [
            "thread-follower-load-complete-history",
            "thread-follower-update-thread-settings",
            "thread-follower-start-turn",
            "thread-follower-interrupt-turn",
        ]
        assert all(request.get("targetClientId") == "desktop-owner" for request in requests[1:])
    finally:
        await bridge.close()


def test_bridge_rejects_incompatible_socket_permissions(tmp_path: Path) -> None:
    insecure = tmp_path / "insecure"
    insecure.mkdir(mode=0o755)
    socket_path = insecure / "ipc.sock"
    socket_path.touch(mode=0o600)
    bridge = DesktopIpcBridge(mode="required", socket_path=socket_path, request_timeout=.1)
    with pytest.raises(DesktopIpcIncompatible):
        bridge._verify_socket()


def test_frame_size_is_bounded() -> None:
    with pytest.raises(Exception, match="allowed size"):
        encode_frame({"payload": "x" * MAX_FRAME_BYTES})
    length = struct.pack("<I", MAX_FRAME_BYTES + 1)

    async def oversized() -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(length)
        reader.feed_eof()
        with pytest.raises(Exception, match="frame length"):
            await read_frame(reader)

    asyncio.run(oversized())


@pytest.mark.asyncio
async def test_stream_patches_apply_by_revision_and_versions_fail_closed() -> None:
    bridge = DesktopIpcBridge(mode="off")
    await bridge._handle_message({
        "type": "broadcast",
        "method": "thread-stream-state-changed",
        "version": 11,
        "sourceClientId": "owner",
        "params": {
            "hostId": "local",
            "conversationId": "thread-1",
            "change": {
                "type": "snapshot",
                "revision": 1,
                "conversationState": {"turns": [{"status": "inProgress"}]},
            },
        },
    })
    await bridge._handle_message({
        "type": "broadcast",
        "method": "thread-stream-state-changed",
        "version": 11,
        "sourceClientId": "owner",
        "params": {
            "hostId": "local",
            "conversationId": "thread-1",
            "change": {
                "type": "patches",
                "baseRevision": 1,
                "revision": 2,
                "patches": [{"op": "replace", "path": ["turns", 0, "status"], "value": "completed"}],
            },
        },
    })
    snapshot = bridge.snapshot("thread-1")
    assert snapshot and snapshot.revision == 2
    assert snapshot.conversation_state["turns"][0]["status"] == "completed"

    await bridge._handle_message({
        "type": "broadcast",
        "method": "thread-stream-state-changed",
        "version": 12,
        "params": {},
    })
    assert bridge.compatible is False
    assert bridge.connection_state == "incompatible"


class FakeDesktopBridge:
    mode = DesktopIpcMode.auto
    socket_present = True

    def __init__(self, owner: DesktopOwner | Exception | None) -> None:
        self.owner = owner
        self.started: list[dict[str, Any]] = []
        self.steered: list[str] = []
        self.interrupted = False
        self.handlers: dict[str, Any] = {}

    def add_broadcast_handler(self, method: str, handler: Any) -> Any:
        self.handlers[method] = handler
        return lambda: None

    async def start(self) -> bool:
        return True

    async def follow(self, _: str) -> DesktopOwner | None:
        if isinstance(self.owner, Exception):
            raise self.owner
        return self.owner

    async def update_thread_settings(self, _thread: str, _owner: str, settings: dict[str, Any]) -> None:
        self.started.append({"settings": settings})

    async def start_turn(self, _thread: str, _owner: str, prompt: str, *, turn_start_params: dict[str, Any]) -> dict[str, Any]:
        self.started.append({"prompt": prompt, "params": turn_start_params})
        return {"ok": True}

    async def steer_turn(self, _thread: str, _owner: str, prompt: str) -> dict[str, Any]:
        self.steered.append(prompt)
        return {"ok": True}

    async def interrupt_turn(self, _thread: str, _owner: str) -> dict[str, Any]:
        self.interrupted = True
        return {"ok": True}

    def snapshot(self, _: str) -> DesktopOwner | None:
        return self.owner if isinstance(self.owner, DesktopOwner) else None

    def health(self) -> dict[str, Any]:
        return {"mode": "auto", "socket_present": True, "compatible": True, "connection_state": "connected", "last_error": None}

    async def close(self) -> None:
        pass


class FakeSdkClient:
    def __init__(self) -> None:
        self.resumed = 0

    async def thread_resume(self, *_: Any, **__: Any) -> Any:
        self.resumed += 1
        raise AssertionError("SDK fallback must not run")

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_runtime_prefers_desktop_owner_and_controls_same_run() -> None:
    owner = DesktopOwner("desktop-owner", {"cwd": "/repo", "turns": []}, 1)
    bridge = FakeDesktopBridge(owner)
    client = FakeSdkClient()
    runtime = CodexRuntime(client, default_cwd="/repo", desktop_bridge=bridge)  # type: ignore[arg-type]
    run = await runtime.start_run("thread-1", "Do it", access_mode=AccessMode.read_only)
    assert run.backend == "desktop_ipc"
    assert run.owner_client_id == "desktop-owner"
    assert bridge.started[0]["settings"]["sandboxPolicy"]["type"] == "readOnly"
    await runtime.steer(run.id, "Focus on tests")
    await runtime.interrupt(run.id)
    assert bridge.steered == ["Focus on tests"]
    assert bridge.interrupted is True
    assert client.resumed == 0
    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_fails_closed_when_desktop_ownership_is_ambiguous() -> None:
    bridge = FakeDesktopBridge(DesktopIpcUnavailable("timeout"))
    client = FakeSdkClient()
    runtime = CodexRuntime(client, default_cwd="/repo", desktop_bridge=bridge)  # type: ignore[arg-type]
    with pytest.raises(CodexServiceError, match="no fallback run"):
        await runtime.start_run("thread-1", "Do it")
    assert client.resumed == 0
    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_attaches_to_desktop_active_turn_and_normalizes_items() -> None:
    turn = {
            "turnId": "turn-desktop",
            "status": "inProgress",
            "items": [{"id": "tool-1", "type": "commandExecution", "status": "inProgress", "command": "pytest"}],
        }
    state = {
        "cwd": "/repo",
        "turns": [],
        "turnHistory": {
            "kind": "canonical",
            "history": {"entitiesByKey": {"turn:turn-desktop": turn}},
        },
    }
    owner = DesktopOwner("desktop-owner", state, 2)
    runtime = CodexRuntime(FakeSdkClient(), default_cwd="/repo", desktop_bridge=FakeDesktopBridge(owner))  # type: ignore[arg-type]
    run = await runtime.observe_thread("thread-1")
    assert run and run.turn_id == "turn-desktop" and run.backend == "desktop_ipc"
    assert any(event["type"] == "item.started" and event["item_id"] == "tool-1" for event in run.events)
    await runtime.close()
