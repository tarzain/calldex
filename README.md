# Calldex

Calldex is a local React dashboard and LiveKit voice agent for browsing and continuing local Codex threads through Gemini Live.

## Setup

```bash
uv sync
npm install --prefix web
npm run build --prefix web
cp .env.example .env
```

Set `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, and `GEMINI_API_KEY` in `.env`. Codex authentication comes from the local Codex install; run `codex login` if needed.

## Run the dashboard

```bash
uv run calldex-dashboard
```

Open [http://127.0.0.1:3000](http://127.0.0.1:3000) on the host computer, or `http://<host-lan-ip>:3000` from another device on the same network. The command starts all three local processes together:

- the React/TypeScript dashboard on `0.0.0.0:3000` so it is reachable over the local network;
- the FastAPI thread/token API on `127.0.0.1:8765`;
- the LiveKit `calldex` agent worker.

Stopping the command shuts down the full stack. Override the ports with `CALLDEX_WEB_PORT` and `CALLDEX_API_PORT`.

The LAN dashboard currently has no application authentication. Anyone who can reach port 3000 can use Calldex's Codex controls, so expose it only on a trusted network and do not forward the port to the public internet.

The dashboard lists the 50 most recently updated non-archived Codex tasks from every working directory. Opening a task is read-only. The composer starts a streamed Codex turn with read-only, workspace-write, or explicitly confirmed full access; while a turn is active, new input steers it and the Stop control interrupts it. Plans, reasoning, command/tool output, file changes, diffs, usage, errors, and completion stream into the UI over reconnectable SSE. New task, rename, fork, and confirmed archive actions are available from the workbench.

Calldex reads Codex's local state database so recent Codex Desktop tasks are included. On
macOS it automatically uses the current runtime bundled with Codex Desktop, avoiding rollout
format mismatches with older Python SDK releases. Set `CALLDEX_CODEX_BIN` to an executable
Codex path to override that choice; when neither is available, the SDK's bundled runtime is
used.

When Codex Desktop is running, Calldex also detects its private same-user IPC socket and,
when compatible, follows desktop-owned tasks by default. Messages, steering, interruption,
and live task state then flow through the owning desktop window instead of a second Codex
process. Tasks with no desktop owner continue through the supported Python SDK. If desktop
ownership cannot be determined safely, Calldex rejects the send rather than risk starting a
competing turn. Set `CALLDEX_DESKTOP_IPC=off` to disable the experimental bridge or
`CALLDEX_DESKTOP_IPC=required` to make dashboard startup fail unless it is available.

The bridge is intentionally local and unsupported: it validates the current protocol
versions, requires a current-user private Unix socket, and never exposes IPC messages or
client identifiers to the browser. A Codex Desktop update can require a Calldex compatibility
update; `/api/health` reports the coarse bridge state without exposing local paths or task
content.

During a call, selecting a dashboard thread requests it as the voice thread. The agent validates and confirms the selection through LiveKit participant attributes. A selection made by voice opens the same thread in the browser.

## Voice tools

- `list_codex_threads`: list recent tasks for spoken or ordinal selection.
- `select_codex_thread`: validate and select an existing task.
- `inspect_codex_thread`: summarize recent messages, changes, and run state.
- `list_codex_projects`: list known working directories.
- `start_codex_task`: create workspace-write or read-only work asynchronously.
- `codex_reply`: start or steer work in the selected task.
- `control_codex_turn`: report progress or stop the latest selected run.
- `manage_codex_thread`: rename or fork the selected task.

Voice cannot grant full access or archive tasks. Completed asynchronous voice work is announced proactively, and LiveKit participant attributes keep the selected task and run status synchronized with the browser.

Browsing remains read-only. Repository changes happen only after an explicit message from the web composer or an explicit voice request.

## Future remote access

Calldex is intentionally loopback-only today. A deferred design for authenticated browser access through native host and device pairing is documented in [docs/remote-connectivity-plan.md](docs/remote-connectivity-plan.md).

## Agent-only development

```bash
uv run calldex dev
```

The default Gemini 2.5 Live configuration uses a zero thinking budget, proactive audio,
affective dialogue, non-blocking function calls with `WHEN_IDLE` result delivery, and a
500 ms server-VAD silence threshold.

## Test

```bash
uv run pytest
npm test --prefix web
```
