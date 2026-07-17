from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from calldex.codex_service import CodexServiceError, ThreadNotFoundError
from calldex.web import create_app


class Service:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    async def list_threads(self, **_: Any) -> dict[str, Any]:
        return {"threads": [], "next_cursor": None}

    async def read_thread(self, thread_id: str) -> dict[str, Any]:
        if thread_id == "missing":
            raise ThreadNotFoundError("missing")
        return {"thread": {"id": thread_id}, "events": []}

    async def send_message(self, thread_id: str, prompt: str) -> dict[str, str]:
        if thread_id == "missing":
            raise ThreadNotFoundError("missing")
        self.messages.append((thread_id, prompt))
        return {"thread_id": thread_id, "response": "Done"}


class Runtime:
    def __init__(self) -> None:
        self.service = Service()
        self.archived: list[str] = []
        self.run = SimpleNamespace(
            id="run-1",
            status="running",
            summary=lambda: {
                "run_id": "run-1",
                "thread_id": "thread-1",
                "turn_id": "turn-1",
                "status": "running",
                "access_mode": "workspace_write",
                "started_at": 1,
                "completed_at": None,
                "final_response": None,
                "error": None,
                "plan": [],
                "diff": "",
                "last_seq": 1,
            },
        )

    async def list_projects(self) -> list[dict[str, Any]]:
        return [{"path": "/tmp/project", "name": "project", "thread_count": 1, "updated_at": None, "is_default": True}]

    async def create_thread(self, *_: Any, **__: Any) -> tuple[dict[str, Any], Any]:
        return {"id": "thread-new", "name": "New task"}, self.run

    def active_run(self, _: str) -> None:
        return None

    async def rename_thread(self, thread_id: str, name: str) -> dict[str, Any]:
        return {"id": thread_id, "name": name}

    async def fork_thread(self, _: str) -> dict[str, Any]:
        return {"id": "thread-fork", "name": "Fork"}

    async def archive_thread(self, thread_id: str) -> None:
        self.archived.append(thread_id)

    async def start_run(self, *_: Any, **__: Any) -> Any:
        return self.run

    def get_run(self, _: str) -> Any:
        return self.run

    async def steer(self, *_: Any, **__: Any) -> dict[str, Any]:
        return self.run.summary()

    async def interrupt(self, *_: Any, **__: Any) -> dict[str, Any]:
        return self.run.summary()

    async def events(self, *_: Any, **__: Any) -> Any:
        yield {"seq": 1, "run_id": "run-1", "thread_id": "thread-1", "turn_id": "turn-1", "type": "run.started", "item_id": None, "timestamp": 1, "payload": {}}


def decode_payload(token: str) -> dict[str, Any]:
    encoded = token.split(".")[1]
    encoded += "=" * (-len(encoded) % 4)
    return json.loads(base64.urlsafe_b64decode(encoded))


def test_api_errors_and_health() -> None:
    service = Service()
    with TestClient(create_app(service=service)) as client:
        assert client.get("/api/threads?limit=0").status_code == 422
        assert client.get("/api/threads/missing").status_code == 404
        sent = client.post("/api/threads/thread-1/messages", json={"prompt": " Continue it "})
        assert sent.status_code == 200
        assert sent.json() == {"thread_id": "thread-1", "response": "Done"}
        assert service.messages == [("thread-1", "Continue it")]
        assert client.post("/api/threads/thread-1/messages", json={"prompt": "  "}).status_code == 422
        assert client.post("/api/threads/missing/messages", json={"prompt": "Hi"}).status_code == 404
        health = client.get("/api/health").json()
        assert health == {
            "status": "ok",
            "dashboard": True,
            "codex_sdk": True,
            "agent_worker": True,
            "desktop_ipc": {
                "mode": "off",
                "socket_present": False,
                "compatible": None,
                "connection_state": "disabled",
                "last_error": None,
            },
        }


def test_token_has_unique_rooms_explicit_dispatch_and_no_secrets(monkeypatch: Any) -> None:
    monkeypatch.setenv("LIVEKIT_URL", "wss://example.livekit.cloud")
    monkeypatch.setenv("LIVEKIT_API_KEY", "key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "secret")
    with TestClient(create_app(service=Service())) as client:
        first = client.post("/api/livekit/token")
        second = client.post("/api/livekit/token")

    assert first.status_code == 201
    assert "secret" not in first.text
    assert "key" not in first.text
    first_payload = decode_payload(first.json()["participant_token"])
    second_payload = decode_payload(second.json()["participant_token"])
    assert first_payload["video"]["room"] != second_payload["video"]["room"]
    assert first_payload["roomConfig"]["agents"][0]["agentName"] == "calldex"
    assert first_payload["video"]["canUpdateOwnMetadata"] is True


def test_live_runtime_routes_and_access_confirmation() -> None:
    runtime = Runtime()
    with TestClient(create_app(runtime=runtime)) as client:
        assert client.get("/api/projects").json()["projects"][0]["name"] == "project"
        assert client.post("/api/threads", json={
            "prompt": "Do it",
            "repository_path": "/tmp/project",
            "access_mode": "full_access",
        }).status_code == 422
        created = client.post("/api/threads", json={
            "prompt": "Do it",
            "repository_path": "/tmp/project",
            "access_mode": "workspace_write",
        })
        assert created.status_code == 202
        assert created.json()["run"]["run_id"] == "run-1"
        assert client.patch("/api/threads/thread-1", json={"name": "Renamed"}).json()["thread"]["name"] == "Renamed"
        assert client.post("/api/threads/thread-1/fork").status_code == 201
        assert client.post("/api/threads/thread-1/archive", json={"confirm": False}).status_code == 422
        assert client.post("/api/threads/thread-1/archive", json={"confirm": True}).status_code == 204
        assert runtime.archived == ["thread-1"]
        assert client.post("/api/threads/thread-1/turns", json={"prompt": "Continue"}).status_code == 202
        assert client.get("/api/runs/run-1").status_code == 200
        assert client.post("/api/runs/run-1/steer", json={"prompt": "Adjust"}).status_code == 200
        assert client.post("/api/runs/run-1/interrupt").status_code == 200
        events = client.get("/api/runs/run-1/events")
        assert events.status_code == 200
        assert "event: codex" in events.text
