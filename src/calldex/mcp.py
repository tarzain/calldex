from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from livekit.agents import ToolError
from livekit.agents.llm.mcp import MCPServerStdio, MCPToolResultContext, MCPToolset


DEFAULT_CODEX_CWD = Path("/workspace/calldex")
MAX_TOOL_RESULT_CHARS = 4000


def _text_from_mcp_content(content: Any) -> str:
    text = getattr(content, "text", None)
    if isinstance(text, str):
        return text

    if hasattr(content, "model_dump"):
        return json.dumps(content.model_dump(), ensure_ascii=False)

    return str(content)


def codex_tool_result_resolver(ctx: MCPToolResultContext) -> str:
    """Return compact, voice-agent-friendly MCP tool results."""
    parts = [_text_from_mcp_content(item) for item in ctx.result.content]
    if not parts:
        raise ToolError(f"Codex tool '{ctx.tool_name}' returned no content.")

    result = "\n".join(parts).strip()
    if len(result) > MAX_TOOL_RESULT_CHARS:
        result = result[:MAX_TOOL_RESULT_CHARS].rstrip() + "\n...[truncated]"

    return result


def build_codex_mcp_server(*, cwd: Path = DEFAULT_CODEX_CWD) -> MCPServerStdio:
    env = os.environ.copy()
    env.setdefault("CODEX_HOME", str(Path.home() / ".codex"))

    return MCPServerStdio(
        command="codex",
        args=[
            "mcp-server",
            "-c",
            'sandbox_mode="workspace-write"',
            "-c",
            'approval_policy="on-request"',
        ],
        env=env,
        cwd=str(cwd),
        client_session_timeout_seconds=120,
        tool_result_resolver=codex_tool_result_resolver,
    )


def build_codex_toolset(*, cwd: Path = DEFAULT_CODEX_CWD) -> MCPToolset:
    return MCPToolset(
        id="codex",
        mcp_server=build_codex_mcp_server(cwd=cwd),
    )
