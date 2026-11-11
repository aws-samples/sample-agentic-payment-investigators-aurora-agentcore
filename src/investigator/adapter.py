# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
"""AgentCore adapter — the ONLY deployment-specific file, kept deliberately thin.

@app.entrypoint receives the payload, derives IdentityContext from the
runtime-VALIDATED JWT claims (CUSTOM_JWT authorizer ran before us), opens
a request-scoped DB connection, constructs a fresh agent, streams typed chunks.
Async only at this entrypoint; the agent core never knows it's deployed.

Payload contract (UI/api and the terminal scripts both speak it):
  {"prompt": "...", "agent": "dispute" | "recon" | "copilot", "tau": 0.78?}
`tau` (optional) overrides confidence_tau for THIS request — the module-7
recalibration rep (TAU=x m4_queue.sh) without a runtime env update. Clamped
to [0,1]; routing thresholds are per-request state, not deploy state.
Streamed output: JSON chunks {"type": text|tool_start|tool_end|artifact|done|error, ...}
"""
import base64
import binascii
import json
import logging
import sys
import uuid

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.runtime.models import PingStatus

from .agents import AGENTS
from .config import load_settings
from .identity import IdentityContext

logging.basicConfig(stream=sys.stdout, level=logging.INFO)   # stdout only
logging.getLogger("botocore").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("strands.telemetry").setLevel(logging.WARNING)  # MetricsClient chatter
log = logging.getLogger("investigator.adapter")

app = BedrockAgentCoreApp()


def _claims_from_bearer(auth_header: str) -> dict:
    """Decode the JWT payload from the Authorization header.

    NO signature check here BY DESIGN: the runtime's CUSTOM_JWT authorizer
    already validated issuer/signature/expiry before this code ran — a
    request with a bad token never reaches the entrypoint. We only READ the
    validated token's claims. (bedrock-agentcore SDK shape: RequestContext
    = {session_id, request_headers}; Authorization forwarded unconditionally.)

    Malformed tokens still reach here when the authorizer ISN'T configured yet
    (pre-CFN dev, misconfigured runtime) — fail with a directive message, not
    an opaque IndexError→500 that eats the real diagnostic."""
    token = auth_header.split(" ", 1)[1] if " " in auth_header else auth_header
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        return json.loads(base64.urlsafe_b64decode(payload_b64))
    except (IndexError, binascii.Error, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(
            "Authorization header is not a decodable JWT — is the CUSTOM_JWT "
            "authorizer configured on this runtime (discoveryUrl + allowedClients), "
            "and is the client sending a Cognito ACCESS token?") from exc


def _identity_from_context(context) -> IdentityContext:
    """Claims from the runtime-validated JWT; session id from the runtime;
    a fresh trace id per invocation (stamped on every row written).
    Never derived from the payload — identity is not client-asserted."""
    headers = getattr(context, "request_headers", None) or {}
    auth = headers.get("Authorization", "")
    if not auth:
        raise ValueError("no Authorization header on request context — "
                         "is the CUSTOM_JWT authorizer configured on this runtime?")
    claims = _claims_from_bearer(auth)
    session_id = getattr(context, "session_id", "") or f"unknown-session-{uuid.uuid4()}"
    trace_id = f"trace-{uuid.uuid4()}"
    return IdentityContext.from_claims(claims, session_id=session_id, trace_id=trace_id)


@app.entrypoint
async def invoke(payload: dict, context):
    settings = load_settings()
    if payload.get("tau") is not None:      # per-request τ (module 10 recalibration)
        try:
            tau = min(1.0, max(0.0, float(payload["tau"])))
            settings = settings.model_copy(update={"confidence_tau": tau})
        except (TypeError, ValueError):
            yield {"type": "error", "error": f"tau must be a number in [0,1], got {payload['tau']!r}"}
            return
    identity = _identity_from_context(context)
    agent_key = payload.get("agent", "dispute")
    agent_cls = AGENTS.get(agent_key)
    if agent_cls is None:
        yield {"type": "error", "error": f"unknown agent '{agent_key}' "
               f"(expected one of {sorted(AGENTS)})"}
        return
    prompt = payload.get("prompt", "")
    if not prompt:
        yield {"type": "error", "error": "payload.prompt is required"}
        return

    log.info("invoke agent=%s sub=%s trace=%s", agent_key, identity.sub, identity.trace_id)
    agent = agent_cls(settings, identity)   # per-request instance;
    async for chunk in agent.invoke_stream(prompt):   # tools open per-call conns
        yield {"type": chunk.type, **chunk.data}


@app.ping
def ping():
    # PingStatus enum, not a bare string: the SDK coerces via PingStatus(value),
    # and the enum's VALUES are "Healthy"/"HealthyBusy" — "HEALTHY" would raise
    # and silently fall back to automatic status on every health check.
    return PingStatus.HEALTHY


if __name__ == "__main__":
    app.run()
