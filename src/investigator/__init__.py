# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
"""investigator — ONE shared package, three instantiations (dispute, recon, ops copilot).

Local dev loop:   uv run -m investigator.chat --agent dispute --analyst analyst_martha
Deployed:         adapter.py is the AgentCore entrypoint (setup_deploy.sh bundles it)
"""
from .agents import AGENTS, DisputeInvestigator, OpsCopilot, ReconInvestigator
from .base_agent import BaseAgent, Chunk, new_session_id, new_trace_id
from .config import Settings, load_settings
from .identity import IdentityContext

__all__ = [
    "AGENTS", "BaseAgent", "Chunk", "DisputeInvestigator", "IdentityContext",
    "OpsCopilot", "ReconInvestigator", "Settings", "load_settings",
    "new_session_id", "new_trace_id",
]
