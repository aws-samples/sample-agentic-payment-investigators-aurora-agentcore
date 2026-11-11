# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
"""IdentityContext — typed, derived from VALIDATED JWT claims, keyed on sub.

The AgentCore runtime's CUSTOM_JWT authorizer validates issuer/signature/expiry
BEFORE the adapter runs (§9), so claims here are trusted. Cognito ACCESS tokens
carry no email claim (§8c AVOID#4) — identity is the `sub`, full stop. The context
is carried opaquely through the agent chain (§8c KEEP) and lands in Postgres as
`SET LOCAL app.analyst_id` (RLS) + `actor_sub` on action_log rows.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class IdentityContext:
    sub: str                    # the ONLY identity key (AVOID#4)
    session_id: str             # AgentCore runtimeSessionId (≥33 chars) or local session
    trace_id: str               # stamped on every row written (pattern step 7)

    @classmethod
    def from_claims(cls, claims: dict, session_id: str, trace_id: str) -> "IdentityContext":
        """From the runtime-validated JWT claims dict. No fallbacks: a missing sub is
        a hard error, never an anonymous write."""
        sub = claims.get("sub")
        if not sub:
            raise ValueError("validated JWT carries no sub — refusing anonymous identity")
        return cls(sub=sub, session_id=session_id, trace_id=trace_id)

    @classmethod
    def local_dev(cls, analyst_id: str, session_id: str, trace_id: str) -> "IdentityContext":
        """Dev-loop identity (chat_loop, harness): explicit, never inferred from env."""
        return cls(sub=analyst_id, session_id=session_id, trace_id=trace_id)
