# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
"""The three instantiations of BaseAgent: dispute, recon, ops copilot.

Each subclass is deliberately THIN — a name, a system prompt, a tool belt.
That thinness IS the workshop's thesis: the pattern is one implementation,
instantiated per domain. Construct one per request; discard after. Tools open
their own connections via db_factory — nothing here holds a connection.
"""
import os
import pathlib

from .base_agent import BaseAgent
from .config import Settings
from .db import db_factory
from .identity import IdentityContext
from .tools_copilot import COPILOT_SYSTEM_PROMPT, build_copilot_tools
from .tools_disputes import (STAGE_PROMPTS, build_dispute_tools,
                             prompt_for_tools)
from .tools_recon import RECON_SYSTEM_PROMPT, build_recon_tools

COMPANY = "AnyCompany"   # the fictional merchant in the workshop narrative

# Facilitator dial (catchup/demo on a COMPLETE workspace): {blind, evidence,
# naive, rerank, full} overrides DisputeInvestigator's belt with a module's
# starting state. Source: AGENT_STAGE env if set (deployed runtime, one-off
# runs), else ~/.agent-stage (`echo evidence > ~/.agent-stage` — read
# per-instantiation, so the NEXT REPL launch in the same terminal picks it
# up). Neither present (the attendee path): the belt is the LIST at the
# bottom of build_dispute_tools — the one you edit — and the system prompt
# follows whatever is wired into it (prompt_for_tools).
def _stage() -> str | None:
    stage = os.environ.get("AGENT_STAGE", "")
    if not stage:
        try:
            stage = (pathlib.Path.home() / ".agent-stage").read_text().strip()
        except (OSError, RuntimeError):   # no file / no resolvable HOME (runtime container)
            stage = ""
    if not stage:
        return None
    if stage not in STAGE_PROMPTS:
        raise ValueError(f"agent stage {stage!r} — expected one of {sorted(STAGE_PROMPTS)}")
    return stage


class DisputeInvestigator(BaseAgent):
    name = "dispute-investigator"
    example_prompt = "investigate case 4127"

    def __init__(self, settings: Settings, identity: IdentityContext):
        super().__init__(settings, identity)
        self.db = db_factory(settings, identity)

    def tools(self) -> list:
        # belt and prompt resolve TOGETHER, from one stage read: the prompt
        # must describe the belt the agent actually holds (a prompt step
        # whose tool isn't wired makes the run flail, not fail). build()
        # calls tools() before reading system_prompt, so the prompt set here
        # is the one the Agent gets.
        stage = _stage()
        belt = build_dispute_tools(self.db, self.settings, self.identity,
                                   stage=stage)
        if stage is not None:
            self.system_prompt = STAGE_PROMPTS[stage].format(company=COMPANY)
        else:
            self.system_prompt = prompt_for_tools(belt).format(company=COMPANY)
        return belt


class ReconInvestigator(BaseAgent):
    name = "recon-investigator"
    example_prompt = "investigate break 1"
    system_prompt = RECON_SYSTEM_PROMPT.format(company=COMPANY)

    def __init__(self, settings: Settings, identity: IdentityContext):
        super().__init__(settings, identity)
        self.db = db_factory(settings, identity)

    def tools(self) -> list:
        return build_recon_tools(self.db, self.settings, self.identity)


class OpsCopilot(BaseAgent):
    name = "ops-copilot"
    example_prompt = "what's in my queue today?"
    system_prompt = COPILOT_SYSTEM_PROMPT.format(company=COMPANY)

    def model_id(self) -> str:
        return self.settings.copilot_model_id

    def __init__(self, settings: Settings, identity: IdentityContext):
        super().__init__(settings, identity)
        self.db = db_factory(settings, identity)

    def tools(self) -> list:
        return build_copilot_tools(self.db, self.settings, self.identity)


AGENTS = {
    "dispute": DisputeInvestigator,
    "recon": ReconInvestigator,
    "copilot": OpsCopilot,
}
