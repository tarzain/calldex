from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import os
import stat
import struct
import time
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Awaitable, Callable


# Desktop snapshots can include complete canonical histories.  Keep a bound well
# below the router's private 256 MiB limit while accommodating large real tasks.
MAX_FRAME_BYTES = 64 * 1024 * 1024
DEFAULT_REQUEST_TIMEOUT = 5.0
FOLLOW_SNAPSHOT_TIMEOUT = 3.0
LOCAL_HOST_ID = "local"

# These versions are intentionally pinned to the installed desktop protocol.  The
# bridge must fail closed when the desktop changes them rather than guessing.
PROTOCOL_VERSIONS: dict[str, int] = {
    "initialize": 0,
    "client-status-changed": 0,
    "ipc-connection-reset": 1,
    "thread-stream-state-changed": 11,
    "thread-stream-following-changed": 1,
    "thread-stream-following-status-requested": 1,
    "thread-follower-start-turn": 1,
    "thread-follower-load-complete-history": 1,
    "thread-follower-steer-turn": 1,
    "thread-follower-interrupt-turn": 2,
    "thread-follower-update-thread-settings": 1,
}


class DesktopIpcMode(StrEnum):
    auto = "auto"
    off = "off"
    required = "required"


class DesktopIpcError(RuntimeError):
    pass


class DesktopIpcUnavailable(DesktopIpcError):
    pass


class DesktopIpcIncompatible(DesktopIpcError):
    pass


class DesktopOwnerNotFound(DesktopIpcError):
    pass


@dataclass(slots=True)
class DesktopOwner:
    client_id: str
    conversation_state: dict[str, Any]
    revision: int


BroadcastHandler = Callable[[dict[str, Any]], Awaitable[None] | None]


def desktop_socket_path() -> Path:
    codex_home = Path(os.getenv("CODEX_HOME", "~/.codex")).expanduser()
    return codex_home / "ipc" / "ipc.sock"


def parse_mode(value: str | None = None) -> DesktopIpcMode:
    raw = (value if value is not None else os.getenv("CALLDEX_DESKTOP_IPC", "auto")).strip().lower()
    try:
        return DesktopIpcMode(raw)
    except ValueError as exc:
        raise ValueError("CALLDEX_DESKTOP_IPC must be auto, off, or required") from exc


def encode_frame(message: dict[str, Any]) -> bytes:
    payload = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if not payload or len(payload) > MAX_FRAME_BYTES:
        raise DesktopIpcError("Desktop IPC frame is outside the allowed size")
    return struct.pack("<I", len(payload)) + payload


async def read_frame(reader: asyncio.StreamReader) -> dict[str, Any]:
    header = await reader.readexactly(4)
    length = struct.unpack("<I", header)[0]
    if length == 0 or length > MAX_FRAME_BYTES:
        raise DesktopIpcError(f"Invalid desktop IPC frame length: {length}")
    payload = await reader.readexactly(length)
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DesktopIpcError("Desktop IPC sent malformed JSON") from exc
    if not isinstance(value, dict):
        raise DesktopIpcError("Desktop IPC message must be an object")
    return value


def _apply_patch(document: Any, patch: dict[str, Any]) -> Any:
    path = patch.get("path")
    operation = patch.get("op")
    if not isinstance(path, list) or operation not in {"add", "replace", "remove"}:
        raise DesktopIpcError("Unsupported desktop state patch")
    if not path:
        if operation == "remove":
            return None
        return copy.deepcopy(patch.get("value"))
    target = document
    for component in path[:-1]:
        if isinstance(target, list) and isinstance(component, int):
            target = target[component]
        elif isinstance(target, dict) and isinstance(component, (str, int)):
            target = target[str(component)]
        else:
            raise DesktopIpcError("Invalid desktop state patch path")
    leaf = path[-1]
    if isinstance(target, list) and isinstance(leaf, int):
        if operation == "add":
            target.insert(leaf, copy.deepcopy(patch.get("value")))
        elif operation == "replace":
            target[leaf] = copy.deepcopy(patch.get("value"))
        else:
            target.pop(leaf)
    elif isinstance(target, dict) and isinstance(leaf, (str, int)):
        key = str(leaf)
        if operation == "remove":
            target.pop(key, None)
        else:
            target[key] = copy.deepcopy(patch.get("value"))
    else:
        raise DesktopIpcError("Invalid desktop state patch target")
    return document


class DesktopIpcBridge:
    """Experimental same-user bridge to a running Codex desktop client."""

    def __init__(
        self,
        *,
        mode: DesktopIpcMode | str | None = None,
        socket_path: str | Path | None = None,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> None:
        self.mode = parse_mode(str(mode) if mode is not None else None)
        self.socket_path = Path(socket_path) if socket_path is not None else desktop_socket_path()
        self.request_timeout = request_timeout
        self.client_id: str | None = None
        self.connection_state = "disabled" if self.mode == DesktopIpcMode.off else "disconnected"
        self.compatible: bool | None = None
        self.last_error: str | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._connect_lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._handlers: dict[str, set[BroadcastHandler]] = {}
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._revisions: dict[str, int] = {}
        self._owners: dict[str, str] = {}
        self._snapshot_waiters: dict[str, set[asyncio.Future[DesktopOwner]]] = {}
        self._followed: set[str] = set()
        self._closed = False

    @property
    def socket_present(self) -> bool:
        return self.socket_path.exists()

    @property
    def connected(self) -> bool:
        return self.client_id is not None and self._writer is not None and not self._writer.is_closing()

    def health(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "socket_present": self.socket_present,
            "compatible": self.compatible,
            "connection_state": self.connection_state,
            "last_error": self.last_error,
        }

    def add_broadcast_handler(self, method: str, handler: BroadcastHandler) -> Callable[[], None]:
        handlers = self._handlers.setdefault(method, set())
        handlers.add(handler)

        def remove() -> None:
            handlers.discard(handler)

        return remove

    def _verify_socket(self) -> None:
        try:
            directory = self.socket_path.parent.stat()
            socket_info = self.socket_path.stat()
        except FileNotFoundError as exc:
            raise DesktopIpcUnavailable("Codex desktop IPC socket is not available") from exc
        uid = os.getuid()
        if directory.st_uid != uid or socket_info.st_uid != uid:
            raise DesktopIpcIncompatible("Codex desktop IPC socket is not owned by the current user")
        if directory.st_mode & 0o077 or socket_info.st_mode & 0o077:
            raise DesktopIpcIncompatible("Codex desktop IPC permissions are not private")
        if not stat.S_ISSOCK(socket_info.st_mode):
            raise DesktopIpcIncompatible("Codex desktop IPC endpoint is not a Unix socket")

    async def start(self) -> bool:
        if self.mode == DesktopIpcMode.off:
            return False
        try:
            return await self.ensure_connected()
        except DesktopIpcError:
            if self.mode == DesktopIpcMode.required:
                raise
            return False

    async def ensure_connected(self) -> bool:
        if self.mode == DesktopIpcMode.off or self._closed:
            return False
        if self.connected:
            return True
        async with self._connect_lock:
            if self.connected:
                return True
            try:
                if self._writer is not None:
                    await self._disconnect()
                self._verify_socket()
                self.connection_state = "connecting"
                reader, writer = await asyncio.wait_for(
                    asyncio.open_unix_connection(self.socket_path), timeout=self.request_timeout
                )
                self._reader = reader
                self._writer = writer
                self._reader_task = asyncio.create_task(self._read_loop(), name="calldex-desktop-ipc")
                response = await self._send_request(
                    "initialize", {"clientType": "calldex"}, timeout=self.request_timeout
                )
                result = response.get("result")
                if not isinstance(result, dict) or not isinstance(result.get("clientId"), str):
                    raise DesktopIpcIncompatible("Codex desktop IPC initialization response changed")
                self.client_id = result["clientId"]
                self.compatible = True
                self.connection_state = "connected"
                self.last_error = None
                return True
            except DesktopIpcUnavailable as exc:
                self.compatible = None
                self.connection_state = "absent"
                self.last_error = None
                await self._disconnect()
                raise exc
            except Exception as exc:
                error = exc if isinstance(exc, DesktopIpcError) else DesktopIpcUnavailable(str(exc))
                self.compatible = False if isinstance(error, DesktopIpcIncompatible) else self.compatible
                self.connection_state = "incompatible" if isinstance(error, DesktopIpcIncompatible) else "disconnected"
                self.last_error = str(error)
                await self._disconnect()
                raise error

    async def _read_loop(self) -> None:
        assert self._reader is not None
        try:
            while True:
                await self._handle_message(await read_frame(self._reader))
        except (asyncio.IncompleteReadError, ConnectionError, DesktopIpcError) as exc:
            if not self._closed:
                self.last_error = str(exc)
                self.connection_state = "disconnected"
                self.client_id = None
                self._reject_pending(DesktopIpcUnavailable("Codex desktop IPC disconnected"))
                await self._dispatch({"type": "broadcast", "method": "ipc-connection-reset", "params": {}})

    async def _handle_message(self, message: dict[str, Any]) -> None:
        message_type = message.get("type")
        if message_type == "response":
            future = self._pending.pop(str(message.get("requestId", "")), None)
            if future is not None and not future.done():
                future.set_result(message)
            return
        if message_type == "broadcast":
            method = str(message.get("method", ""))
            expected = PROTOCOL_VERSIONS.get(method)
            if expected is not None and message.get("version", 0) != expected:
                self.compatible = False
                self.connection_state = "incompatible"
                self.last_error = f"Unsupported {method} protocol version"
                return
            if method == "thread-stream-state-changed":
                self._handle_stream_state(message)
            elif method == "thread-stream-following-status-requested":
                params = message.get("params")
                thread_id = params.get("conversationId") if isinstance(params, dict) else None
                source = message.get("sourceClientId")
                if isinstance(thread_id, str) and thread_id in self._followed:
                    await self.send_broadcast(
                        "thread-stream-following-changed",
                        {"conversationId": thread_id, "hostId": LOCAL_HOST_ID, "following": True},
                        target_client_ids=[source] if isinstance(source, str) else None,
                    )
            elif method == "client-status-changed":
                params = message.get("params")
                source = params.get("clientId") if isinstance(params, dict) else None
                status = params.get("status") if isinstance(params, dict) else None
                if status == "connected" and isinstance(source, str):
                    for thread_id in tuple(self._followed):
                        await self.send_broadcast(
                            "thread-stream-following-changed",
                            {"conversationId": thread_id, "hostId": LOCAL_HOST_ID, "following": True},
                            target_client_ids=[source],
                        )
            await self._dispatch(message)
            return
        if message_type == "client-discovery-request":
            await self._write({
                "type": "client-discovery-response",
                "requestId": message.get("requestId"),
                "response": {"canHandle": False},
            })
            return
        if message_type == "request":
            await self._write({
                "type": "response",
                "requestId": message.get("requestId"),
                "resultType": "error",
                "error": "no-handler-for-request",
            })

    async def _dispatch(self, message: dict[str, Any]) -> None:
        handlers = tuple(self._handlers.get(str(message.get("method", "")), ()))
        for handler in handlers:
            result = handler(message)
            if asyncio.iscoroutine(result):
                with contextlib.suppress(Exception):
                    await result

    def _handle_stream_state(self, message: dict[str, Any]) -> None:
        params = message.get("params")
        if not isinstance(params, dict) or params.get("hostId") != LOCAL_HOST_ID:
            return
        thread_id = params.get("conversationId")
        change = params.get("change")
        owner = message.get("sourceClientId")
        if not isinstance(thread_id, str) or not isinstance(change, dict) or not isinstance(owner, str):
            return
        change_type = change.get("type")
        if change_type == "snapshot":
            state = change.get("conversationState")
            revision = change.get("revision")
            if not isinstance(state, dict) or not isinstance(revision, int):
                return
            self._snapshots[thread_id] = copy.deepcopy(state)
            self._revisions[thread_id] = revision
        elif change_type == "patches":
            if change.get("baseRevision") != self._revisions.get(thread_id):
                return
            patches = change.get("patches")
            revision = change.get("revision")
            state = self._snapshots.get(thread_id)
            if not isinstance(patches, list) or not isinstance(revision, int) or state is None:
                return
            try:
                for patch in patches:
                    if not isinstance(patch, dict):
                        raise DesktopIpcError("Malformed desktop state patch")
                    state = _apply_patch(state, patch)
            except DesktopIpcError as exc:
                self.last_error = str(exc)
                return
            if not isinstance(state, dict):
                return
            self._snapshots[thread_id] = state
            self._revisions[thread_id] = revision
        else:
            return
        self._owners[thread_id] = owner
        snapshot = DesktopOwner(owner, copy.deepcopy(self._snapshots[thread_id]), self._revisions[thread_id])
        for waiter in tuple(self._snapshot_waiters.get(thread_id, ())):
            if not waiter.done():
                waiter.set_result(snapshot)

    async def _write(self, message: dict[str, Any]) -> None:
        writer = self._writer
        if writer is None or writer.is_closing():
            raise DesktopIpcUnavailable("Codex desktop IPC is not connected")
        writer.write(encode_frame(message))
        await writer.drain()

    async def _send_request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        target_client_id: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = future
        timeout = timeout or self.request_timeout
        message: dict[str, Any] = {
            "type": "request",
            "requestId": request_id,
            "sourceClientId": self.client_id or "",
            "version": PROTOCOL_VERSIONS[method],
            "method": method,
            "params": params,
            "timeoutMs": int(timeout * 1000),
        }
        if target_client_id:
            message["targetClientId"] = target_client_id
        try:
            await self._write(message)
            response = await asyncio.wait_for(future, timeout=timeout + 0.25)
        except TimeoutError as exc:
            raise DesktopIpcUnavailable(f"Codex desktop IPC timed out during {method}") from exc
        finally:
            self._pending.pop(request_id, None)
        if response.get("resultType") == "success":
            return response
        error = str(response.get("error") or "desktop-request-failed")
        if error == "no-client-found" or error.startswith("no-client-found:"):
            raise DesktopOwnerNotFound(error)
        if error == "request-version-mismatch":
            self.compatible = False
            self.connection_state = "incompatible"
            raise DesktopIpcIncompatible(f"Codex desktop rejected the {method} protocol version")
        raise DesktopIpcUnavailable(error)

    async def send_broadcast(
        self,
        method: str,
        params: dict[str, Any],
        *,
        target_client_ids: list[str] | None = None,
    ) -> None:
        if not await self.ensure_connected():
            raise DesktopIpcUnavailable("Codex desktop IPC is unavailable")
        message: dict[str, Any] = {
            "type": "broadcast",
            "sourceClientId": self.client_id,
            "version": PROTOCOL_VERSIONS[method],
            "method": method,
            "params": params,
        }
        if target_client_ids:
            message["targetClientIds"] = target_client_ids
        await self._write(message)

    async def follow(self, thread_id: str) -> DesktopOwner | None:
        try:
            connected = await self.ensure_connected()
        except DesktopIpcUnavailable:
            if not self.socket_present:
                return None
            raise
        if not connected:
            return None
        self._followed.add(thread_id)
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[DesktopOwner] = loop.create_future()
        self._snapshot_waiters.setdefault(thread_id, set()).add(waiter)
        try:
            await self.send_broadcast(
                "thread-stream-following-changed",
                {"conversationId": thread_id, "hostId": LOCAL_HOST_ID, "following": True},
            )
            try:
                response = await self._send_request(
                    "thread-follower-load-complete-history", {"conversationId": thread_id}
                )
            except DesktopOwnerNotFound:
                self._followed.discard(thread_id)
                return None
            handled_by = response.get("handledByClientId")
            if isinstance(handled_by, str):
                self._owners[thread_id] = handled_by
            try:
                return await asyncio.wait_for(waiter, timeout=FOLLOW_SNAPSHOT_TIMEOUT)
            except TimeoutError as exc:
                raise DesktopIpcUnavailable("Desktop owner did not publish a task snapshot") from exc
        finally:
            waiters = self._snapshot_waiters.get(thread_id)
            if waiters is not None:
                waiters.discard(waiter)
                if not waiters:
                    self._snapshot_waiters.pop(thread_id, None)

    async def update_thread_settings(
        self, thread_id: str, owner_client_id: str, settings: dict[str, Any]
    ) -> None:
        await self._send_request(
            "thread-follower-update-thread-settings",
            {"conversationId": thread_id, "threadSettings": settings},
            target_client_id=owner_client_id,
        )

    async def start_turn(
        self,
        thread_id: str,
        owner_client_id: str,
        prompt: str,
        *,
        turn_start_params: dict[str, Any],
    ) -> dict[str, Any]:
        response = await self._send_request(
            "thread-follower-start-turn",
            {"conversationId": thread_id, "turnStartParams": turn_start_params},
            target_client_id=owner_client_id,
        )
        result = response.get("result")
        return result if isinstance(result, dict) else {"result": result, "prompt": prompt}

    async def steer_turn(
        self, thread_id: str, owner_client_id: str, prompt: str
    ) -> dict[str, Any]:
        snapshot = self._snapshots.get(thread_id) or {}
        cwd = str(snapshot.get("cwd") or "/")
        response = await self._send_request(
            "thread-follower-steer-turn",
            {
                "conversationId": thread_id,
                "clientUserMessageId": str(uuid.uuid4()),
                "input": [{"type": "text", "text": prompt, "text_elements": []}],
                "serviceTier": None,
                "attachments": [],
                "restoreMessage": {
                    "id": str(uuid.uuid4()),
                    "text": prompt,
                    "context": {
                        "prompt": prompt,
                        "addedFiles": [],
                        "fileAttachments": [],
                        "ideContext": None,
                        "imageAttachments": [],
                        "workspaceRoots": [cwd],
                    },
                    "cwd": cwd,
                    "createdAt": int(time.time() * 1000),
                },
            },
            target_client_id=owner_client_id,
        )
        result = response.get("result")
        return result if isinstance(result, dict) else {"result": result}

    async def interrupt_turn(self, thread_id: str, owner_client_id: str) -> dict[str, Any]:
        response = await self._send_request(
            "thread-follower-interrupt-turn",
            {"conversationId": thread_id},
            target_client_id=owner_client_id,
        )
        result = response.get("result")
        return result if isinstance(result, dict) else {"result": result}

    def snapshot(self, thread_id: str) -> DesktopOwner | None:
        state = self._snapshots.get(thread_id)
        owner = self._owners.get(thread_id)
        revision = self._revisions.get(thread_id)
        if state is None or owner is None or revision is None:
            return None
        return DesktopOwner(owner, copy.deepcopy(state), revision)

    def _reject_pending(self, error: Exception) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

    async def _disconnect(self) -> None:
        current = asyncio.current_task()
        task = self._reader_task
        self._reader_task = None
        if task is not None and task is not current:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        writer = self._writer
        self._reader = None
        self._writer = None
        self.client_id = None
        if writer is not None:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def close(self) -> None:
        if self._closed:
            return
        if self.connected:
            for thread_id in tuple(self._followed):
                with contextlib.suppress(Exception):
                    await self.send_broadcast(
                        "thread-stream-following-changed",
                        {"conversationId": thread_id, "hostId": LOCAL_HOST_ID, "following": False},
                    )
        self._closed = True
        self.connection_state = "closed"
        self._reject_pending(DesktopIpcUnavailable("Desktop IPC bridge closed"))
        await self._disconnect()
