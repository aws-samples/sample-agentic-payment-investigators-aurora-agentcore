One shared package for the three agents: dispute, recon and copilot.
Design rules baked in: per-request agent instances — no singletons or locks; stdout-only
logging; identity keyed on the validated JWT sub; strict pydantic config; async only at
the entrypoint adapter; typed artifact SSE chunks, no string sentinels; idempotency keys
derived in tool code.
The workshop exercises edit tools_disputes.py, tools_recon.py, retrieval.py and writes.py.
