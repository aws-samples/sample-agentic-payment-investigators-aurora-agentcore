# api/ — the browser ↔ AgentCore bridge

One FastAPI app (`app.py`), two jobs:
1. `POST /api/chat` — relays to `InvokeAgentRuntime` with the **caller's** Cognito
   ACCESS token; streams the runtime's typed SSE chunks back verbatim
   (`text/tool_start/tool_end/artifact/done/error` + a trailing `session` chunk
   carrying the runtimeSessionId for multi-turn continuity).
2. `GET/POST /api/*` — thin reads + the review-verdict write, as `workshop_app`;
   the verified JWT's `sub` → `SET app.analyst_id` → **RLS scopes rows server-side**
   (review_queue endpoints return only the caller's items with no WHERE clause).

Identity rules (frozen): access token verified at the edge (JWKS: issuer/signature/
expiry/client_id/token_use); `sub` is the only identity key; downstream carries it
opaquely. The relay path adds NO authority — AgentCore's CUSTOM_JWT authorizer
re-validates the same bearer.

## Local dev
```bash
cd api && uv venv && uv pip install -e . -e ../src
export WORKSHOP_DB_* COGNITO_POOL_ID COGNITO_CLIENT_ID RUNTIME_ARN AWS_REGION
uvicorn app:app --reload --port 8080
# then: curl -H "Authorization: Bearer $(../scripts/get_token.sh)" localhost:8080/api/queues
```

## Deploy shape
`infra/workshop.yml` runs this app as an AWS Lambda function (`BridgeFn`) in the private subnets.
Amazon CloudFront routes `/api/*` to its streaming function URL, which uses `AWS_IAM` auth.
The browser sends its token in the `x-ws-auth` header. The function's environment variables come from the template.
`load_settings` reads the database password from the secret in `WORKSHOP_DB_SECRET_ARN` on each request.

