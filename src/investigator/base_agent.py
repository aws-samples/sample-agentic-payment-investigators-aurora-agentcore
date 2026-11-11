# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
"""BaseAgent — the framework-agnostic core: one implementation, N instantiations.

The SAME code path runs locally (chat_loop) and deployed (adapter calls invoke_stream);
one core, two entrypoints. Instances are PER-REQUEST by construction:
construct → use → discard; nothing here is module-level or shared
across requests, so parallel tool calls can never deadlock a lock we
never took.

Subclasses (DisputeInvestigator, ReconInvestigator, OpsCopilot) provide domain
identity: a system prompt and a tool list. Everything else — model wiring, streaming
event mapping, the chat loop — lands once, here.
"""
import asyncio
import json
import logging
import sys
import uuid
from dataclasses import dataclass
from typing import AsyncIterator

from strands import Agent
from strands.models import BedrockModel

from .config import Settings, load_settings
from .identity import IdentityContext

logging.basicConfig(stream=sys.stdout, level=logging.INFO)  # stdout only
logging.getLogger("botocore").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("strands.telemetry").setLevel(logging.WARNING)  # MetricsClient chatter
log = logging.getLogger("investigator.base")


# ---------------------------------------------------------------------------
# Typed stream chunks — the SSE vocabulary.
# Artifacts ride a TYPED chunk; nothing is ever regex-scraped from model text.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Chunk:
    type: str          # text | tool_start | tool_end | artifact | done | error
    data: dict

    def to_sse(self) -> str:
        return json.dumps({"type": self.type, **self.data})


def artifact_result(artifact: dict, summary: str) -> dict:
    """Return value for a tool that ships a typed artifact.

    Strands wraps a PLAIN dict return into a TEXT content block (json.dumps —
    verified in decorator._wrap_tool_result, strands 1.45.0), which the artifact
    scan deliberately ignores. Only this EXPLICIT ToolResult shape passes
    through with a `json` block intact — the model sees `summary`, the UI gets
    the payload from the typed chunk, and nothing is ever regex'd out of text.
    """
    return {"status": "success",
            "content": [{"text": summary}, {"json": {"artifact": artifact}}]}


class BaseAgent:
    """Construct one per request. `settings` and `identity` arrive from the edge
    (adapter or chat_loop) — the core never reads env or parses JWTs itself."""

    name: str = "base"
    system_prompt: str = "You are an investigator."
    example_prompt: str = ""     # shown in the REPL banner so nobody stares at a bare `>`

    def __init__(self, settings: Settings, identity: IdentityContext):
        self.settings = settings
        self.identity = identity

    # -- subclass surface ---------------------------------------------------
    def model_id(self) -> str:
        """Which inference profile this agent runs on (subclass hook)."""
        return self.settings.model_id

    def tools(self) -> list:
        """Domain tool list. Tools close over (settings, identity) — built fresh
        per request, so identity can never leak across requests."""
        return []

    # -- lifecycle ------------------------------------------------------------
    def build(self) -> Agent:
        """Fresh Strands Agent per request (constructor surface used:
        model/tools/system_prompt/callback_handler).

        With WORKSHOP_MEMORY_ID set (the deployed runtime), the platform's
        session manager restores this session's prior turns into the fresh
        agent and persists new ones — session CONTINUITY without giving up
        per-request construction. Memory is identity-scoped: the
        actor is the validated JWT sub, so two analysts' sessions can never
        hydrate each other. agent_id partitions the three agents within a
        session (default would collide if one session ever spoke to two)."""
        model = BedrockModel(model_id=self.model_id(),
                             region_name=self.settings.model_region)
        session_manager = None
        if self.settings.memory_id:
            from bedrock_agentcore.memory.integrations.strands.config import \
                AgentCoreMemoryConfig
            from bedrock_agentcore.memory.integrations.strands.session_manager import \
                AgentCoreMemorySessionManager
            session_manager = AgentCoreMemorySessionManager(
                agentcore_memory_config=AgentCoreMemoryConfig(
                    memory_id=self.settings.memory_id,
                    session_id=self.identity.session_id,
                    actor_id=self.identity.sub),
                region_name=self.settings.model_region)
        return Agent(model=model, tools=self.tools(),
                     system_prompt=self.system_prompt, callback_handler=None,
                     agent_id=self.name, session_manager=session_manager)

    async def invoke_stream(self, prompt: str, agent: Agent | None = None) -> AsyncIterator[Chunk]:
        """The one streaming path both entrypoints share. Maps Strands events to
        the typed chunk vocabulary; tool exceptions surface as `error` chunks and
        are re-raised — never swallowed.

        `agent` lets a REPL session reuse one Agent across turns (conversation
        history lives in the Agent); each runtime REQUEST still builds fresh."""
        agent = agent or self.build()
        seen_tool_use_ids: set[str] = set()   # current_tool_use streams one event
        try:                                  # per input DELTA — dedupe to one start
            async for event in agent.stream_async(prompt):
                if "data" in event:                      # model text delta
                    yield Chunk("text", {"text": event["data"]})
                elif "current_tool_use" in event:        # tool call progress
                    tu = event["current_tool_use"]
                    tu_id = tu.get("toolUseId", "")
                    if tu.get("name") and tu_id not in seen_tool_use_ids:
                        seen_tool_use_ids.add(tu_id)
                        yield Chunk("tool_start", {"tool": tu["name"],
                                                   "tool_use_id": tu_id})
                elif "message" in event:
                    msg = event["message"]
                    for block in msg.get("content", []) if isinstance(msg, dict) else []:
                        if isinstance(block, dict) and "toolResult" in block:
                            tr = block["toolResult"]
                            yield Chunk("tool_end", {
                                "tool_use_id": tr.get("toolUseId", ""),
                                "status": tr.get("status", "success")})
                            # typed artifact side-channel: tools return
                            # artifact_result(...) — the explicit ToolResult
                            # shape whose json block survives wrapping
                            for c in tr.get("content", []):
                                doc = c.get("json") if isinstance(c, dict) else None
                                if isinstance(doc, dict) and "artifact" in doc:
                                    yield Chunk("artifact", {"artifact": doc["artifact"]})
                elif "result" in event:
                    yield Chunk("done", {"trace_id": self.identity.trace_id,
                                         "text": str(event["result"])})
        except Exception as exc:
            log.exception("agent stream failed")
            # yield the typed error then END the stream (no re-raise): re-raising
            # from an async-gen entrypoint makes AgentCore's outer handler emit a
            # SECOND, differently-shaped error event (a double emit). The
            # typed chunk carries the trace_id; callers detect failure by chunk type.
            yield Chunk("error", {"error": f"{type(exc).__name__}: {exc}",
                                  "trace_id": self.identity.trace_id})

    async def invoke(self, prompt: str) -> dict:
        """Non-streaming convenience (queue runners, harness): drain the stream,
        return the final text + collected artifacts as a structured payload.
        On stream failure, raises with the trace_id attached — queue runners
        correlate failures against action_log by trace."""
        text_parts, artifacts, final, error = [], [], "", None
        async for chunk in self.invoke_stream(prompt):
            if chunk.type == "text":
                text_parts.append(chunk.data["text"])
            elif chunk.type == "artifact":
                artifacts.append(chunk.data["artifact"])
            elif chunk.type == "done":
                final = chunk.data["text"]
            elif chunk.type == "error":
                error = chunk.data["error"]
        if error is not None:
            raise RuntimeError(f"agent invocation failed "
                               f"(trace_id={self.identity.trace_id}): {error}")
        return {"text": final or "".join(text_parts),
                "artifacts": artifacts, "trace_id": self.identity.trace_id}

    # -- dev loop (the primary build surface) ---------------------------------
    def chat_loop(self) -> None:
        """Terminal REPL against the SAME invoke_stream the runtime uses.
        ONE Agent for the whole session — conversation history persists across
        turns ("watch it reason" is a multi-turn interrogation)."""
        print(f"[{self.name}] chat — analyst={self.identity.sub} "
              f"trace={self.identity.trace_id}")
        hint = f'try: "{self.example_prompt}" · ' if self.example_prompt else ""
        print(f"  ({hint}exit, empty line, or Ctrl+C ends the chat)")
        agent = self.build()
        while True:
            try:
                prompt = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()   # newline after ^C/^D so the exit line starts clean
                break
            if not prompt or prompt.lower() in ("exit", "quit", "q"):
                break
            try:
                asyncio.run(self._chat_turn(agent, prompt))
            except KeyboardInterrupt:   # ^C mid-answer: drop the turn, not the REPL
                print("\n  (interrupted — the chat is still open; exit ends it)")
            # production parity: the adapter mints a trace per invocation, so the
            # REPL re-mints between turns — writes from different prompts must not
            # share a trace. The tool closures hold this same (frozen) object, so
            # this is the one sanctioned in-place re-mint.
            object.__setattr__(self.identity, "trace_id", new_trace_id())
        # unmistakable seam: people type shell commands into agent chats
        print(f"[{self.name}] chat ended — you're back at the shell.")

    async def _chat_turn(self, agent: Agent, prompt: str) -> None:
        async for chunk in self.invoke_stream(prompt, agent=agent):
            if chunk.type == "text":
                print(chunk.data["text"], end="", flush=True)
            elif chunk.type == "tool_start":
                print(f"\n  ⚙ {chunk.data['tool']} …", flush=True)
            elif chunk.type == "artifact":
                print(f"\n  📎 artifact: {json.dumps(chunk.data['artifact'])[:120]}", flush=True)
            elif chunk.type == "done":
                print()


def new_trace_id() -> str:
    return f"trace-{uuid.uuid4()}"


def new_session_id() -> str:
    """AgentCore runtimeSessionId must be ≥33 chars (verified: shorter 400s)."""
    return f"local-session-{uuid.uuid4()}"
