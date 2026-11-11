# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
"""Config — strict pydantic settings. ONE place for every knob.

MODEL_REGION + MODEL_ID exist ONLY here (CFN param → env var → this class); zero
per-file region/model literals anywhere else (break-glass region flip).
"""
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WORKSHOP_", extra="forbid", frozen=True)

    # -- Bedrock (break-glass flip = change these two env vars, nothing else) --
    model_region: str = Field(default="us-east-1")
    model_id: str = Field(  # pinned GLOBAL inference profile (never "latest"/
        # alias; global > us. for re:Invent capacity resilience). Tool-loop
        # agents (dispute/recon): Haiku — dozens of tool iterations per case,
        # cost+latency sensitive.
        default="global.anthropic.claude-haiku-4-5-20251001-v1:0")
    copilot_model_id: str = Field(  # the in-app chatbot: Sonnet 5 — near-Opus
        # quality on conversational turns at 3/5 the price
        default="global.anthropic.claude-sonnet-5")
    embed_model_id: str = Field(default="amazon.titan-embed-text-v2:0")  # must match the build-time corpus embedding
    embed_dim: int = Field(default=1024)
    # Rerank (retrieval stage 2). The ARN is region-relative — verified available
    # in BOTH us-east-1 and us-west-2.
    # An explicit WORKSHOP_RERANK_MODEL_ARN env wins (break-glass, same policy
    # as the model ids); otherwise it derives from model_region after load.
    # env override: WORKSHOP_RERANK_MODEL_ID (CFN-injected; ':0' suffix optional)
    rerank_model_id: str = Field(default="cohere.rerank-v3-5:0")
    rerank_model_arn: str = Field(default="")
    rerank_overfetch: int = Field(default=25)   # stage-1 candidates fed to stage 2

    @model_validator(mode="after")
    def _derive_rerank_arn(self):
        if not self.rerank_model_arn:
            # tolerate the id with or without the ':0' revision suffix (the
            # CFN parameter injects the bare id; the foundation-model ARN
            # requires the revision)
            mid = self.rerank_model_id
            if not mid.endswith(":0"):
                mid += ":0"
            object.__setattr__(self, "rerank_model_arn",
                f"arn:aws:bedrock:{self.model_region}::foundation-model/{mid}")
        return self

    # -- Database (CFN Outputs → env). Password: CFN passes the SECRET ARN
    # (WORKSHOP_DB_SECRET_ARN), never the literal — load_settings() resolves it
    # via GetSecretValue at the edge. db_password env is the local/dev override.
    db_host: str = Field(default="localhost")
    db_port: int = Field(default=5432)
    db_name: str = Field(default="workshop")
    db_user: str = Field(default="workshop_app")
    db_password: str = Field(default="", repr=False)
    db_secret_arn: str = Field(default="")

    # -- Session memory (STM). Set by CFN on the deployed runtime; empty
    # locally, where the REPL's long-lived Agent already carries the turns.
    # With an id set, build() attaches AgentCoreMemorySessionManager so a
    # FRESH per-request agent restores this session's prior turns (restore
    # works across processes; per-request construction unchanged).
    memory_id: str = Field(default="")

    # -- Routing (τ given; recalibrated on the floor later) --
    confidence_tau: float = Field(default=0.7, ge=0.0, le=1.0)

    # -- Retrieval (production defaults; exercises vary them per-session via SET) --
    retrieval_top_k: int = Field(default=5)
    hnsw_ef_search: int = Field(default=40)                    # the default that overfilters
    hnsw_iterative_scan: str = Field(default="relaxed_order")  # the overfilter fix


def load_settings() -> Settings:
    """Fresh Settings per call — no module-level singleton.

    If WORKSHOP_DB_SECRET_ARN is set, resolve the password from Secrets Manager
    HERE (the edge) — the secret value never rides an env var or CFN template.
    Aurora managed secrets store JSON with a "password" key; a plain
    SecretString is accepted as the password itself.
    """
    s = Settings()
    if s.db_secret_arn and not s.db_password:
        import json

        import boto3
        # the ARN names its own region — never trust a default here
        # (a model_region default of us-east-1 + a us-west-2 secret ARN
        # = deterministic AccessDenied)
        arn_region = s.db_secret_arn.split(":")[3] if s.db_secret_arn.startswith("arn:") else s.model_region
        sm = boto3.client("secretsmanager", region_name=arn_region)
        raw = sm.get_secret_value(SecretId=s.db_secret_arn)["SecretString"]
        try:
            password = json.loads(raw).get("password", raw)
        except (json.JSONDecodeError, AttributeError):
            password = raw
        s = s.model_copy(update={"db_password": password})
    return s
