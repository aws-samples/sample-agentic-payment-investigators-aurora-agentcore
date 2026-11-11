# Threat Model — Agentic Payment Investigators on Aurora and Bedrock AgentCore

This document describes the security design of the sample and the threats it
does and does not mitigate. This is **sample code, for non-production usage**.
You should work with your security and legal teams to meet your organizational
security, regulatory and compliance requirements before deployment; the final
section lists the hardening a production adoption would add.

## Scope

Two investigator agents (disputes, reconciliation) and one read-oriented
copilot run against an Amazon Aurora PostgreSQL database. Agents are deployed
to Amazon Bedrock AgentCore Runtime behind a JWT authorizer. A browser UI
served from a private S3 bucket and a streaming Lambda API bridge front them,
both behind one CloudFront distribution. All model calls go to Amazon Bedrock.
The data is synthetic: no cardholder data, card numbers, or real merchant
records exist anywhere in the schema or seeds.

## In-scope assets

| Asset | Where |
|---|---|
| Dispute/reconciliation records, precedent corpus | Aurora PostgreSQL (`workshop/sql/schema.sql`) |
| Agent write path (idempotency ledger + transition guard) | `src/investigator/writes.py`, `tools_recon.py` |
| Caller identity (JWT claims → per-request context) | `src/investigator/adapter.py`, `identity.py` |
| API bridge (browser bearer → AgentCore relay, SSE passthrough) | `api/app.py`, `BridgeFn` in `infra/workshop.yml` |
| Static UI | private S3 bucket, CloudFront origin access control |
| Review queue (human decisions) | `review_queue` table, RLS policies in schema |
| Database credentials | AWS Secrets Manager, resolved at request time |
| Model access | Amazon Bedrock via IAM role, no keys in code |

## Trust boundaries

```
Browser ──HTTPS──> CloudFront ─┬─> S3 (SPA)            private bucket, OAC only
   │ JWT minted by             ├─> Lambda bridge /api/* Function URL, AWS_IAM auth,
Amazon Cognito                 │        │               OAC-signed, private subnets
                               │        │ caller's bearer forwarded, never stored
                               │        v
                               │  Bedrock AgentCore Runtime (CUSTOM_JWT authorizer)
                               │        │ validated claims only
                               │        v
                               │  per-request agent ── tools ──> Aurora (VPC-private)
                               │                    └──────────> Amazon Bedrock (VPC endpoint)
                               └─> EC2 workbench /code/*  lab code editor; security group
                                                          admits only CloudFront's origin-
                                                          facing prefix list; not in the
                                                          production request path
```

- Every origin is reachable only through CloudFront: the SPA bucket blocks
  public access and grants read to the distribution's origin access control;
  the bridge's Function URL requires SigV4 (`AuthType: AWS_IAM`) that only
  the distribution's origin access control signs; the workbench security
  group admits only the CloudFront origin-facing managed prefix list.
- The bridge runs inside the VPC on private subnets, relays the caller's
  Cognito bearer to the AgentCore data plane on every request, and holds no
  session state. The runtime validates JWT issuer, signature, and expiry
  **before** the adapter runs; the adapter derives identity from validated
  claims only and never from the request payload.
- Aurora is reachable only inside the VPC. Database credentials resolve from
  AWS Secrets Manager per request, in both the bridge and the runtime.
- Every agent write carries the caller's identity and a per-invocation trace
  ID into an append-style audit table (`action_log`).

## Threats and mitigations

| # | Threat | Mitigation in this sample |
|---|---|---|
| T1 | Prompt injection via case free text (complaints, narrations) | The dispute and reconciliation system prompts frame free text as evidence, never instructions. The copilot prompt has no such line. Retrieval examples are wrapped in `<untrusted-evidence>` tags. The write path does not trust model output for authorization decisions. |
| T2 | SQL injection through tool arguments | All tool SQL uses parameterized queries (psycopg placeholders); no string-built SQL anywhere in the tool belt. |
| T3 | Identity spoofing (client asserts another analyst) | Identity is derived from runtime-validated JWT claims; payload-supplied identity fields are ignored. Row-level security scopes review-queue reads/writes to the validated identity. |
| T4 | Duplicate or replayed writes (retries, double-clicks, agent loops) | Deterministic idempotency keys with a database UNIQUE constraint; repeats become no-ops that report current state. |
| T5 | An agent acting outside its work item (cross-case interference, overriding human review) | A state-transition guard rejects illegal edges in the database; review states are a one-way door for agents — only the human verdict path advances them. |
| T6 | Credential leakage | No secrets in code or config files. Database credentials resolve from Secrets Manager per call. The lab sign-in is the exception: the stack Outputs show the generated analyst password, the UI keeps the user name and password in the browser's `sessionStorage` to renew its token, and the same password is the code editor's connection token. Anyone who can read the stack Outputs can sign in and open a terminal on the workbench. |
| T7 | Cost runaway (model invocation loops) | Investigators run a small, fixed tool belt. Deployment uses per-request agent construction with no self-spawning. The sample code sets no limit of its own on tool-loop iterations, and its runtime role allows every Amazon Bedrock foundation model and inference profile. Add a loop limit and scope the role to the model ids you use before you adapt it. |
| T8 | Reaching an origin directly, bypassing CloudFront | S3 origin access control on the bucket; SigV4 (`AWS_IAM`) on the bridge Function URL, signed only by the distribution's origin access control; managed prefix list on the workbench security group. |

## Shared responsibility

AWS operates the security *of* the cloud services used (Aurora, Bedrock,
AgentCore, Cognito, CloudFront). You are responsible for security *in* your
account when deploying this sample: IAM boundaries, network configuration,
secret rotation, monitoring, and deletion of resources when finished.

## Production hardening checklist (not implemented here)

- Per-invocation tool scoping: pass the claimed work-item ID in the payload
  and have every write tool refuse other targets.
- Secret rotation and shorter credential TTLs.
- The lab sign-in shortcuts: output plain URLs and the secret's ARN instead of
  the password, replace the stored password in `webui/js/auth.js` with the
  Amazon Cognito refresh-token flow, and give the code editor its own token.
- Model output content filtering appropriate to your compliance regime.
- Observability beyond the ledger: this sample deliberately declines the
  AgentCore Observability CloudWatch integration and keeps traces in the
  database ledger; enable it, plus metrics, alarms, and anomaly detection on
  `action_log` write patterns.
- Formal data classification and retention for dispute records.
- Web application firewall in front of the UI.
- A CloudFront VPC origin for the lab workbench: move the instance to a
  private subnet with no public IP so CloudFront is the only way in. The
  sample keeps a public-subnet origin over plain HTTP, reachable only from
  the CloudFront origin-facing prefix list, because a VPC origin adds a NAT
  gateway and up to 15 minutes to the deploy.
