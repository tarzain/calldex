from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
import uuid
import json
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any, AsyncIterator

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from livekit import api
from pydantic import BaseModel, Field, field_validator, model_validator

from .agent import build_codex_client, load_calldex_env, server, set_dashboard_runtime
from .codex_service import CodexService, CodexServiceError, ThreadNotFoundError
from .desktop_ipc import DesktopIpcBridge
from .runtime import AccessMode, ActiveRunError, CodexRuntime, RunNotFoundError

HOST = "127.0.0.1"
API_PORT = 8765
WEB_PORT = 3000


class ThreadMessageRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=100_000)

    @field_validator("prompt")
    @classmethod
    def prompt_must_have_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("prompt is required")
        return value


class RunRequest(ThreadMessageRequest):
    access_mode: AccessMode = AccessMode.workspace_write
    confirm_full_access: bool = False

    @model_validator(mode="after")
    def full_access_must_be_confirmed(self) -> "RunRequest":
        if self.access_mode == AccessMode.full_access and not self.confirm_full_access:
            raise ValueError("full access requires explicit confirmation")
        return self


class CreateThreadRequest(RunRequest):
    repository_path: str = Field(min_length=1, max_length=4096)


class RenameThreadRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)

    @field_validator("name")
    @classmethod
    def name_must_have_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name is required")
        return value


class ArchiveThreadRequest(BaseModel):
    confirm: bool


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    default_cwd = Path(os.getenv("CALLDEX_CODEX_CWD", str(Path.cwd()))).expanduser().resolve()
    client = build_codex_client(cwd=default_cwd)
    runtime = CodexRuntime(
        client,
        default_cwd=default_cwd,
        desktop_bridge=DesktopIpcBridge(),
    )
    await runtime.start()
    app.state.codex_client = client
    app.state.runtime = runtime
    app.state.codex = runtime.service
    set_dashboard_runtime(runtime)
    if not hasattr(app.state, "worker_ready"):
        app.state.worker_ready = False
    try:
        yield
    finally:
        set_dashboard_runtime(None)
        await runtime.close()


def create_app(
    *,
    service: CodexService | None = None,
    runtime: CodexRuntime | None = None,
) -> FastAPI:
    app = FastAPI(
        title="Calldex Local Dashboard",
        lifespan=None if service or runtime else lifespan,
    )
    if service:
        app.state.codex = service
        app.state.worker_ready = True
    if runtime:
        app.state.runtime = runtime
        app.state.codex = runtime.service
        app.state.worker_ready = True

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:3000", "http://localhost:3000"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["content-type"],
    )

    def codex(request: Request) -> CodexService:
        return request.app.state.codex

    def codex_runtime(request: Request) -> CodexRuntime:
        runtime = getattr(request.app.state, "runtime", None)
        if runtime is None:
            raise HTTPException(status_code=503, detail="Live Codex runtime is unavailable")
        return runtime

    def raise_runtime_error(exc: Exception) -> None:
        if isinstance(exc, ValueError):
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if isinstance(exc, (ThreadNotFoundError, RunNotFoundError)):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if isinstance(exc, ActiveRunError):
            raise HTTPException(
                status_code=409,
                detail={"message": str(exc), "run_id": exc.run_id},
            ) from exc
        if isinstance(exc, CodexServiceError):
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        raise exc

    @app.get("/api/threads")
    async def list_threads(
        request: Request,
        limit: int = Query(50, ge=1, le=100),
        cursor: str | None = Query(None, min_length=1),
    ) -> dict[str, Any]:
        try:
            return await codex(request).list_threads(limit=limit, cursor=cursor)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except CodexServiceError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/projects")
    async def list_projects(request: Request) -> dict[str, Any]:
        try:
            return {"projects": await codex_runtime(request).list_projects()}
        except Exception as exc:
            raise_runtime_error(exc)
        raise AssertionError("unreachable")

    @app.post("/api/threads", status_code=status.HTTP_202_ACCEPTED)
    async def create_thread(message: CreateThreadRequest, request: Request) -> dict[str, Any]:
        try:
            thread, run = await codex_runtime(request).create_thread(
                message.prompt,
                repository_path=message.repository_path,
                access_mode=message.access_mode,
            )
            return {"thread": thread, "run": run.summary()}
        except Exception as exc:
            raise_runtime_error(exc)
        raise AssertionError("unreachable")

    @app.get("/api/threads/{thread_id}")
    async def read_thread(thread_id: str, request: Request) -> dict[str, Any]:
        try:
            result = await codex(request).read_thread(thread_id)
            runtime = getattr(request.app.state, "runtime", None)
            if runtime:
                await runtime.observe_thread(thread_id)
            active = runtime.active_run(thread_id) if runtime else None
            result["active_run"] = active.summary() if active else None
            return result
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ThreadNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except CodexServiceError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.patch("/api/threads/{thread_id}")
    async def rename_thread(
        thread_id: str,
        message: RenameThreadRequest,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return {"thread": await codex_runtime(request).rename_thread(thread_id, message.name)}
        except Exception as exc:
            raise_runtime_error(exc)
        raise AssertionError("unreachable")

    @app.post("/api/threads/{thread_id}/fork", status_code=status.HTTP_201_CREATED)
    async def fork_thread(thread_id: str, request: Request) -> dict[str, Any]:
        try:
            return {"thread": await codex_runtime(request).fork_thread(thread_id)}
        except Exception as exc:
            raise_runtime_error(exc)
        raise AssertionError("unreachable")

    @app.post("/api/threads/{thread_id}/archive", status_code=status.HTTP_204_NO_CONTENT)
    async def archive_thread(
        thread_id: str,
        message: ArchiveThreadRequest,
        request: Request,
    ) -> Response:
        if not message.confirm:
            raise HTTPException(status_code=422, detail="archive requires explicit confirmation")
        try:
            await codex_runtime(request).archive_thread(thread_id)
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        except Exception as exc:
            raise_runtime_error(exc)
        raise AssertionError("unreachable")

    @app.post("/api/threads/{thread_id}/turns", status_code=status.HTTP_202_ACCEPTED)
    async def start_turn(
        thread_id: str,
        message: RunRequest,
        request: Request,
    ) -> dict[str, Any]:
        try:
            runtime = codex_runtime(request)
            try:
                run = await runtime.start_run(
                    thread_id,
                    message.prompt,
                    access_mode=message.access_mode,
                )
            except ActiveRunError as active:
                await runtime.steer(active.run_id, message.prompt)
                run = runtime.get_run(active.run_id)
            return {"run": run.summary()}
        except Exception as exc:
            raise_runtime_error(exc)
        raise AssertionError("unreachable")

    @app.get("/api/runs/{run_id}")
    async def read_run(run_id: str, request: Request) -> dict[str, Any]:
        try:
            return {"run": codex_runtime(request).get_run(run_id).summary()}
        except Exception as exc:
            raise_runtime_error(exc)
        raise AssertionError("unreachable")

    @app.get("/api/runs/{run_id}/events")
    async def stream_run_events(
        run_id: str,
        request: Request,
        after: int = Query(0, ge=0),
        last_event_id: str | None = Header(None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        if last_event_id and last_event_id.isdigit():
            after = max(after, int(last_event_id))
        runtime = codex_runtime(request)
        try:
            runtime.get_run(run_id)
        except Exception as exc:
            raise_runtime_error(exc)

        async def body() -> AsyncIterator[str]:
            async for event in runtime.events(run_id, after=after):
                if event.get("type") == "heartbeat":
                    yield ": keep-alive\n\n"
                    continue
                yield f"id: {event['seq']}\nevent: codex\ndata: {json.dumps(event)}\n\n"

        return StreamingResponse(
            body(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/runs/{run_id}/steer")
    async def steer_run(
        run_id: str,
        message: ThreadMessageRequest,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return {"run": await codex_runtime(request).steer(run_id, message.prompt)}
        except Exception as exc:
            raise_runtime_error(exc)
        raise AssertionError("unreachable")

    @app.post("/api/runs/{run_id}/interrupt")
    async def interrupt_run(run_id: str, request: Request) -> dict[str, Any]:
        try:
            return {"run": await codex_runtime(request).interrupt(run_id)}
        except Exception as exc:
            raise_runtime_error(exc)
        raise AssertionError("unreachable")

    @app.post("/api/threads/{thread_id}/messages")
    async def send_thread_message(
        thread_id: str,
        message: ThreadMessageRequest,
        request: Request,
    ) -> dict[str, str]:
        try:
            runtime = getattr(request.app.state, "runtime", None)
            if runtime is not None:
                try:
                    run = await runtime.start_run(thread_id, message.prompt)
                except ActiveRunError as active:
                    await runtime.steer(active.run_id, message.prompt)
                    run = runtime.get_run(active.run_id)
                run = await runtime.wait(run.id)
                if run.status != "completed" or not run.final_response:
                    raise CodexServiceError(run.error or "Codex finished without a final response")
                return {"thread_id": thread_id, "response": run.final_response}
            return await codex(request).send_message(thread_id, message.prompt)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ThreadNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except CodexServiceError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/livekit/token", status_code=status.HTTP_201_CREATED)
    async def livekit_token() -> dict[str, str]:
        url = os.getenv("LIVEKIT_URL")
        key = os.getenv("LIVEKIT_API_KEY")
        secret = os.getenv("LIVEKIT_API_SECRET")
        if not url or not key or not secret:
            raise HTTPException(status_code=503, detail="LiveKit credentials are unavailable")

        room_name = f"calldex-{uuid.uuid4().hex}"
        identity = f"web-{uuid.uuid4().hex}"
        token = (
            api.AccessToken(key, secret)
            .with_identity(identity)
            .with_name("Calldex dashboard")
            .with_ttl(timedelta(minutes=10))
            .with_grants(
                api.VideoGrants(
                    room_join=True,
                    room=room_name,
                    can_publish=True,
                    can_subscribe=True,
                    can_publish_data=True,
                    can_update_own_metadata=True,
                )
            )
            .with_room_config(
                api.RoomConfiguration(
                    agents=[api.RoomAgentDispatch(agent_name="calldex")]
                )
            )
            .to_jwt()
        )
        return {"server_url": url, "participant_token": token}

    @app.get("/api/health")
    async def health(request: Request) -> dict[str, Any]:
        runtime = getattr(request.app.state, "runtime", None)
        return {
            "status": "ok",
            "dashboard": True,
            "codex_sdk": hasattr(request.app.state, "codex"),
            "agent_worker": bool(getattr(request.app.state, "worker_ready", False)),
            "desktop_ipc": runtime.desktop_health() if runtime else {
                "mode": "off",
                "socket_present": False,
                "compatible": None,
                "connection_state": "disabled",
                "last_error": None,
            },
        }

    return app


app = create_app()


async def _run_dashboard() -> None:
    load_calldex_env()
    root = Path(__file__).resolve().parents[2]
    web_dir = root / "web"
    api_port = int(os.getenv("CALLDEX_API_PORT", str(API_PORT)))
    web_port = int(os.getenv("CALLDEX_WEB_PORT", str(WEB_PORT)))

    if not (web_dir / "dist" / "server" / "index.js").exists():
        raise RuntimeError("Dashboard frontend is not built; run `cd web && npm run build`")

    web_process = subprocess.Popen(
        ["npm", "run", "start", "--", "--hostname", HOST, "--port", str(web_port)],
        cwd=web_dir,
        start_new_session=True,
    )
    config = uvicorn.Config(app, host=HOST, port=api_port, log_level="info")
    api_server = uvicorn.Server(config)

    async def run_worker() -> None:
        while not hasattr(app.state, "runtime"):
            await asyncio.sleep(0.05)
        set_dashboard_runtime(app.state.runtime)
        app.state.worker_ready = True
        try:
            await server.run(devmode=True)
        finally:
            app.state.worker_ready = False

    async def watch_web() -> None:
        return_code = await asyncio.to_thread(web_process.wait)
        if return_code:
            raise RuntimeError(f"Dashboard frontend exited with status {return_code}")

    tasks = [
        asyncio.create_task(api_server.serve(), name="calldex-api"),
        asyncio.create_task(run_worker(), name="calldex-agent"),
        asyncio.create_task(watch_web(), name="calldex-web"),
    ]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            error = task.exception()
            if error:
                raise error
    finally:
        api_server.should_exit = True
        with contextlib.suppress(Exception):
            await server.aclose()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if web_process.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(web_process.pid, signal.SIGTERM)
            with contextlib.suppress(subprocess.TimeoutExpired):
                web_process.wait(timeout=5)
            if web_process.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(web_process.pid, signal.SIGKILL)


def main() -> None:
    try:
        asyncio.run(_run_dashboard())
    except KeyboardInterrupt:
        pass
