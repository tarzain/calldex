from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from openai_codex import ApprovalMode, Sandbox

from calldex.codex_service import CodexService, CodexServiceError, normalize_item, normalize_thread


def thread(thread_id: str, *, updated_at: int, cwd: str = "/repo") -> Any:
    return SimpleNamespace(
        id=thread_id,
        name=None,
        preview=f"Preview {thread_id}",
        cwd=cwd,
        status="idle",
        created_at=1,
        updated_at=updated_at,
        turns=[],
    )


class FakeReadThread:
    def __init__(self, value: Any) -> None:
        self.value = value
        self.run_calls: list[tuple[str, dict[str, Any]]] = []

    async def read(self, *, include_turns: bool) -> Any:
        assert include_turns is True
        return SimpleNamespace(thread=self.value)

    async def run(self, prompt: str, **kwargs: Any) -> Any:
        self.run_calls.append((prompt, kwargs))
        return SimpleNamespace(final_response="Codex response")


class FakeClient:
    def __init__(self, threads: list[Any]) -> None:
        self.threads = threads
        self.list_kwargs: dict[str, Any] = {}
        self.resume_kwargs: dict[str, Any] = {}
        self.resumed_thread: FakeReadThread | None = None

    async def thread_list(self, **kwargs: Any) -> Any:
        self.list_kwargs = kwargs
        return SimpleNamespace(data=self.threads, next_cursor="next")

    async def thread_resume(self, thread_id: str, **kwargs: Any) -> Any:
        self.resume_kwargs = kwargs
        match = next(value for value in self.threads if value.id == thread_id)
        self.resumed_thread = FakeReadThread(match)
        return self.resumed_thread


@pytest.mark.asyncio
async def test_threads_are_sorted_and_paginated_without_a_cwd_filter() -> None:
    client = FakeClient([thread("old", updated_at=5), thread("new", updated_at=10, cwd="/other")])
    result = await CodexService(client).list_threads(limit=50, cursor="cursor")

    assert [value["id"] for value in result["threads"]] == ["new", "old"]
    assert result["next_cursor"] == "next"
    assert client.list_kwargs["archived"] is False
    assert client.list_kwargs["cursor"] == "cursor"
    assert client.list_kwargs["use_state_db_only"] is True
    assert "cwd" not in client.list_kwargs


def test_normalizes_unknown_item_types_and_truncates_summary() -> None:
    item = SimpleNamespace(
        type="futureThing",
        id="item-1",
        model_dump=lambda **_: {"type": "futureThing", "id": "item-1", "payload": "x" * 3000},
    )
    event = normalize_item(item, turn=SimpleNamespace(id="turn-1", status="completed", started_at=1, completed_at=2))

    assert event["title"] == "Codex event"
    assert event["type"] == "futureThing"
    assert len(event["summary"]) <= 1200
    assert event["details"]["payload"].startswith("x")


def test_bounds_diagnostic_payloads_and_thread_labels() -> None:
    item = SimpleNamespace(
        type="mcpToolCall",
        model_dump=lambda **_: {
            "type": "mcpToolCall",
            "id": "large-tool",
            "result": "x" * 100_000,
            "tail": "must not expand the response",
        },
    )
    event = normalize_item(item, turn=SimpleNamespace(id="turn", status="completed"))
    encoded_details = str(event["details"])

    assert len(encoded_details) < 13_000
    assert "truncated" in encoded_details

    value = thread("long", updated_at=20)
    value.name = "n" * 1_000
    value.preview = "p" * 20_000
    summary = normalize_thread(value)

    assert len(summary["name"]) == 160
    assert len(summary["preview"]) == 500


def test_normalizes_new_sdk_root_paths_statuses_and_message_content() -> None:
    value = thread("new-shape", updated_at=20)
    value.cwd = SimpleNamespace(root="/repo/new-shape")
    value.status = {"type": "notLoaded"}
    value.turns = [
        SimpleNamespace(
            id="turn",
            status="completed",
            started_at=1,
            completed_at=2,
            items=[
                SimpleNamespace(
                    type="agentMessage",
                    model_dump=lambda **_: {
                        "id": "message",
                        "type": "agentMessage",
                        "text": "Useful response",
                    },
                )
            ],
        )
    ]

    normalized = normalize_item(value.turns[0].items[0], turn=value.turns[0])
    assert normalized["summary"] == "Useful response"

    summary = normalize_thread(value)
    assert summary["repository_path"] == "/repo/new-shape"
    assert summary["status"] == "notLoaded"


def test_preserves_message_markdown_and_turn_timestamps() -> None:
    item = SimpleNamespace(
        type="agentMessage",
        model_dump=lambda **_: {
            "id": "message",
            "type": "agentMessage",
            "phase": "final_answer",
            "text": "Result:\n\n- First\n- [Second](https://example.com)",
        },
    )
    event = normalize_item(
        item,
        turn=SimpleNamespace(id="turn", status="completed", started_at=10, completed_at=75),
    )

    assert event["summary"] == "Result:\n\n- First\n- [Second](https://example.com)"
    assert event["phase"] == "final_answer"
    assert event["started_at"] == "1970-01-01T00:00:10+00:00"
    assert event["completed_at"] == "1970-01-01T00:01:15+00:00"


def test_removes_sdk_attachment_preamble_from_user_messages() -> None:
    item = SimpleNamespace(
        type="userMessage",
        model_dump=lambda **_: {
            "id": "message",
            "type": "userMessage",
            "content": [
                {
                    "type": "text",
                    "text": "# Files mentioned by the user:\n\n## screenshot.png: /tmp/screenshot.png\n\n## My request for Codex:\nPlease fix the layout.\n",
                },
                {"type": "localImage", "path": "/tmp/screenshot.png"},
            ],
        },
    )

    event = normalize_item(item, turn=SimpleNamespace(id="turn", status="completed"))

    assert event["summary"] == "Please fix the layout."


@pytest.mark.asyncio
async def test_read_returns_only_latest_200_events() -> None:
    value = thread("many", updated_at=20)
    value.turns = [
        SimpleNamespace(
            id="turn",
            status="completed",
            started_at=1,
            completed_at=2,
            items=[SimpleNamespace(type="plan", id=f"item-{index}", text=str(index)) for index in range(205)],
        )
    ]
    result = await CodexService(FakeClient([value])).read_thread("many")

    assert len(result["events"]) == 200
    assert result["event_count"] == 205
    assert result["truncated"] is True
    assert result["events"][0]["summary"] == "5"


@pytest.mark.asyncio
async def test_send_message_resumes_and_runs_with_workspace_write() -> None:
    client = FakeClient([thread("existing", updated_at=20)])

    result = await CodexService(client).send_message("existing", "  Continue this task  ")

    assert result == {"thread_id": "existing", "response": "Codex response"}
    assert client.resume_kwargs == {
        "sandbox": Sandbox.workspace_write,
        "approval_mode": ApprovalMode.auto_review,
    }
    assert client.resumed_thread is not None
    assert client.resumed_thread.run_calls == [
        (
            "Continue this task",
            {"sandbox": Sandbox.workspace_write, "approval_mode": ApprovalMode.auto_review},
        )
    ]


@pytest.mark.asyncio
async def test_sdk_failures_are_wrapped() -> None:
    class Broken:
        async def thread_list(self, **_: Any) -> Any:
            raise RuntimeError("transport closed")

    with pytest.raises(CodexServiceError, match="unavailable"):
        await CodexService(Broken()).list_threads()
