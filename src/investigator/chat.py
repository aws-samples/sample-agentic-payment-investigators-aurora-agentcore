# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
"""Dev-loop entrypoint: the SAME agent, in a terminal (§4 build surface).

    uv run -m investigator.chat --agent dispute --analyst analyst_martha
    uv run -m investigator.chat --agent copilot --prompt "what's in my queue?"

Identity is explicit (never inferred from env); one connection + one agent per
run — the same request scope the adapter creates, minus the JWT edge.
"""
import argparse
import asyncio

from .agents import AGENTS
from .base_agent import new_session_id, new_trace_id
from .config import load_settings
from .identity import IdentityContext


def main() -> None:
    ap = argparse.ArgumentParser(description="investigator dev chat loop")
    ap.add_argument("--agent", choices=sorted(AGENTS), default="dispute")
    ap.add_argument("--analyst", default="analyst_martha",
                    help="local identity (deployed path derives this from the JWT sub)")
    ap.add_argument("--prompt", default=None,
                    help="one-shot prompt (omit for the interactive chat)")
    args = ap.parse_args()

    settings = load_settings()
    identity = IdentityContext.local_dev(
        analyst_id=args.analyst, session_id=new_session_id(), trace_id=new_trace_id())

    agent = AGENTS[args.agent](settings, identity)
    if args.prompt:
        result = asyncio.run(agent.invoke(args.prompt))
        print(result["text"])
        print(f"\n[trace_id: {result['trace_id']}]")
    else:
        agent.chat_loop()


if __name__ == "__main__":
    main()
