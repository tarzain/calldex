from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins.google.realtime import RealtimeModel

from .mcp import DEFAULT_CODEX_CWD, build_codex_toolset


DEFAULT_GEMINI_LIVE_MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"

AGENT_INSTRUCTIONS = """
You are Calldex, a voice agent that helps the user operate Codex during a LiveKit call.

Use the Codex tools when the user asks you to inspect, modify, explain, review, or run
work in the repository. Keep spoken responses concise. Say the Codex task or thread ID
when it is useful for continuity. Before destructive or broad edits, confirm the user's
intent in natural language.
""".strip()


def _repo_cwd() -> Path:
    return Path(os.getenv("CALLDEX_CODEX_CWD", str(DEFAULT_CODEX_CWD))).expanduser().resolve()


def build_agent() -> Agent:
    codex_tools = build_codex_toolset(cwd=_repo_cwd())
    gemini_model = RealtimeModel(
        model=os.getenv("GEMINI_LIVE_MODEL", DEFAULT_GEMINI_LIVE_MODEL),
        voice=os.getenv("GEMINI_LIVE_VOICE", "Puck"),
        api_key=os.getenv("GOOGLE_API_KEY"),
        instructions=AGENT_INSTRUCTIONS,
    )

    return Agent(
        instructions=AGENT_INSTRUCTIONS,
        llm=gemini_model,
        tools=[codex_tools],
    )


async def entrypoint(ctx: JobContext) -> None:
    load_dotenv()

    await ctx.connect()

    session = AgentSession(max_tool_steps=int(os.getenv("CALLDEX_MAX_TOOL_STEPS", "6")))
    await session.start(
        agent=build_agent(),
        room=ctx.room,
    )


def main() -> None:
    load_dotenv()
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
        )
    )
