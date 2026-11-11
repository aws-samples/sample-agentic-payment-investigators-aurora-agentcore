# Agentic Payment Investigators on Amazon Aurora PostgreSQL and Amazon Bedrock AgentCore

Build-and-run sample for a payment operations floor where AI agents
investigate chargeback disputes and reconciliation breaks against a live
Aurora PostgreSQL database — and every write they make is exactly-once,
state-guarded, and traceable to the row.

This is sample code, for non-production usage. You should work with your
security and legal teams to meet your organizational security, regulatory and
compliance requirements before deployment. See [THREAT_MODEL.md](THREAT_MODEL.md)
and its hardening checklist before adapting it for real workloads.

## What's in this repo

```
infra/          CloudFormation stack (Aurora, AgentCore runtime, Cognito,
                CloudFront with an S3 site, a Lambda API bridge and an EC2
                lab workbench) + bootstrap and deploy tooling
src/            the investigator package: three agents (dispute, recon,
                copilot) on Strands Agents, one shared BaseAgent core
api/            FastAPI SSE bridge between the UI and the agents, deployed
                as a streaming Lambda function behind CloudFront
webui/          the Payment Operations UI (no-framework JS)
workshop/sql/   schema: work queues, precedent corpus (pgvector), state
                machine, append-style audit ledger, RLS review queue
seeds/          corpus generators + Titan embedding pipeline (data is
                generated, not stored in git)
scripts/        runnable proofs: doublefire (exactly-once), storm
                (SERIALIZABLE collisions), retrieval comparisons, checks
```

## Architecture

```
Browser ──> Amazon CloudFront ─┬─> Amazon S3            floor UI (private bucket, OAC)
     │ sign-in                 ├─> AWS Lambda  /api/*   streaming API bridge, in-VPC
  Amazon Cognito (JWT)         │      │ relays the caller's JWT
                               │      v
                               │   Bedrock AgentCore Runtime (CUSTOM_JWT, VPC mode)
                               │      │
                               │   per-request agent (Strands) ──> Amazon Bedrock (Claude,
                               │      │                     Titan, Cohere Rerank via VPC endpoints)
                               │      v
                               │   Aurora PostgreSQL: queues · evidence · pgvector precedent
                               │   memory · state machine · action_log audit trail
                               └─> Amazon EC2   /code/*  browser code editor for the lab
                                                         (not in the production request path)
```

Agents never talk to each other; they coordinate through the database. The
copilot reads the investigators' work product from the shared ledger and can
only delegate by writing durable task rows.

## Prerequisites

- An AWS account that can use Amazon Aurora PostgreSQL and these Amazon Bedrock
  models: Anthropic Claude Haiku 4.5 and Claude Sonnet 5 (both through global
  inference profiles), Amazon Titan Text Embeddings V2 and Cohere Rerank 3.5.
  Deployment does not check model access, so confirm it first. If the account
  has never used an Anthropic model, submit the one-time use case form in the
  Amazon Bedrock console. The stack's roles hold `aws-marketplace:Subscribe` so
  that the first Cohere Rerank call can subscribe the account to that model.
- Python 3.14+ and [uv](https://docs.astral.sh/uv/)
- Node.js 22+ and the `@aws/agentcore` CLI, pinned at 0.25.0 by
  `infra/setup_deploy.sh` (npm `latest` is the incompatible 1.0 rewrite; install `@aws/agentcore@0.25.0`) (for runtime deployment)

## Running the sample

**Guided (recommended):** this repository is the code behind an AWS
workshop. The workshop provisions the complete environment (Aurora, the
deployed runtime, the UI, a browser IDE) and walks the build tool by tool:
[Build agentic payment investigators on Aurora PostgreSQL & AgentCore](https://catalog.us-east-1.prod.workshops.aws/workshops/658429e1-f014-4b4a-a0a5-84d83f85dc73).

**Dev loop.** The database has no public endpoint, so the agents run from a
host inside the stack's VPC. In a deployed stack that host is the browser code
editor. Its terminal opens in the agent workspace with the stack's settings
loaded:

```bash
uv run -m investigator.chat --agent dispute
```

To run this repository's copy instead, use a host that can reach the database.
Set `WORKSHOP_DB_HOST`, `WORKSHOP_DB_SECRET_ARN` and `WORKSHOP_MODEL_REGION`,
and use AWS credentials that can call Amazon Bedrock and read the secret:

```bash
cd src && uv sync --frozen
uv run -m investigator.chat --agent dispute
```

## The judgments this sample encodes

| Judgment | Where to look |
|---|---|
| Exactly-once = deterministic key + UNIQUE, in schema not prompts | `src/investigator/writes.py` |
| Illegal transitions refused by the database, not the model | `workshop/sql/schema.sql` (`dispute_transitions`) |
| Retrieval: hard WHERE on keys, vector rank on free text, rerank on the field you use | `src/investigator/retrieval.py` |
| Identity from validated JWT claims, never the payload | `src/investigator/adapter.py`, `identity.py` |
| Human review is a one-way door for agents | `dispute_transitions` has no agent edge out of REVIEW; status guards in `writes.py` |
| Confidence is a deterministic rubric, not model self-assessment | `src/investigator/confidence.py` |

## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for security
issue notifications, and [THREAT_MODEL.md](THREAT_MODEL.md) for the security
design. Deployments follow the AWS Shared Responsibility Model: AWS secures
the underlying services; you are responsible for account-level configuration,
IAM, secrets, monitoring, and resource cleanup.

Known advisories on the pinned Python dependencies, as of 2026-11-11: `pip-audit`
reports two for `strands-agents` 1.45.0 (GHSA-3w55-933p-8pv5, rated high, and
GHSA-763v-5pjm-fx36, rated medium). Both are in `FileSessionManager`, the library's
file-based session storage, both need another local account on the same host, and
both are fixed in 1.48.0. This sample never constructs that class. The only session
manager it attaches is the AgentCore Memory one, when `WORKSHOP_MEMORY_ID` is set,
as it is on the runtime the CloudFormation stack deploys. Otherwise agents run with
no session manager. The pins are the versions this sample was validated on, so they
stay. If you add file-based sessions, upgrade `strands-agents` to 1.48.0 first:
change the pin in `src/pyproject.toml` and `api/pyproject.toml`, then run `uv lock`
in each directory. Do not go to 1.56.0 or later without also upgrading
`bedrock-agentcore`: with the pinned 1.22.0, the AgentCore Memory session manager
fails to import and agents stop building wherever `WORKSHOP_MEMORY_ID` is set.

## License

This sample code is licensed under the MIT-0 License. See the
[LICENSE](LICENSE) file for details.
