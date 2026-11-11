# Third-Party Software Notices

## Included third-party file

- **AWS CDK bootstrap template** (`infra/vendor/cdk-bootstrap-template.yaml`),
  copied from the AWS CDK CLI (`aws/aws-cdk-cli`,
  `packages/aws-cdk/lib/api/bootstrap/bootstrap-template.yaml`, bootstrap
  version 32) and modified as documented in `infra/vendor/README.md`.
  Apache License 2.0. Copyright Amazon.com, Inc. or its affiliates.

## Referenced dependencies

The packages below are installed from PyPI at build time by `uv` and are
**not redistributed in this repository**. `pyproject.toml` and `uv.lock` pin
exact versions; the full transitive inventory can be regenerated with
`uv export --format requirements-txt` followed by `pip-licenses`.

### Apache License 2.0

- **strands-agents** — model-driven agent framework (the agent loop)
- **bedrock-agentcore** — Amazon Bedrock AgentCore runtime SDK
- **boto3 / botocore** — AWS SDK for Python
- **cryptography** — cryptographic primitives used for JWT verification
  (dual licensed, Apache-2.0 OR BSD-3-Clause)

### MIT License

- **pydantic / pydantic-settings** — typed settings and models
- **pgvector** — pgvector type adapter for psycopg
- **fastapi** — the SSE API bridge
- **PyJWT** — RS256 JWT verification at the API layer

### BSD 3-Clause License

- **uvicorn** — ASGI server
- **starlette** — ASGI toolkit used by FastAPI
- **httpx** — HTTP client

### GNU Lesser General Public License v3.0 only

- **psycopg / psycopg-binary** — PostgreSQL driver
  (https://github.com/psycopg/psycopg). Used unmodified as a library
  dependency and referenced by version only; no psycopg source or binaries
  are included in this repository.

### Mozilla Public License 2.0

- **certifi** — CA bundle, a transitive dependency of httpx and httpcore.
  Used unmodified.
