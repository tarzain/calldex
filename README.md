# calldex

Calldex is a LiveKit voice-agent experiment that lets a Gemini Live agent operate
Codex through Codex's MCP server.

## Setup

```bash
uv sync
cp .env.example .env
```

Fill in `.env` with LiveKit and Google credentials:

```bash
LIVEKIT_URL=
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=
GOOGLE_API_KEY=
```

Codex authentication is handled by the local Codex install. Run `codex login` if
the machine is not already authenticated.

## Run

```bash
uv run calldex dev
```

The LiveKit worker starts a Gemini Live voice agent and exposes Codex via:

```bash
codex mcp-server -c 'sandbox_mode="workspace-write"' -c 'approval_policy="on-request"'
```

By default Codex works in `/workspace/calldex`. Override that with:

```bash
CALLDEX_CODEX_CWD=/path/to/repo
```

Codex currently appears in Gemini as two MCP tools:

- `codex`: start a Codex session from an initial prompt.
- `codex-reply`: continue an existing Codex session by thread ID.
