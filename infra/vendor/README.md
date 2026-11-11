# Vendored artifacts — provenance and scan-finding disposition

## cdk-bootstrap-template.yaml

Vendored from the **official AWS CDK bootstrap template** (`cdk bootstrap
--show-template`) so the AgentCore CLI's CDK-based deploy in module 9 works
without internet fetches from workshop accounts. The stack's CdkBootstrapFn
deploys it as the `CDKToolkit` stack, always passing
`PublicAccessBlockConfiguration=true` and
`FileAssetsBucketKmsKeyId=AWS_MANAGED_KEY`.

**This copy carries five documented deltas from upstream** (each marked
`DAT438 DELTA n` in-file). Every delta either removes a resource that can
never be created under the parameters above, flattens a conditional to the
branch those parameters always select, or adds a constraint — deployed
behavior is identical to upstream-at-our-parameters. Result: **zero Checkov
findings attributable to the five deltas**. cfn-guard/Checkov findings that
remain on UPSTREAM resources (ECR scan-on-push and KMS encryption on
`ContainerAssetsRepository`, inline role policies, `StagingBucket` object
lock, SSL-only bucket policy) inherit the upstream CDK bootstrap template's
dispositions and are not changed
in this copy.

| Delta | What changed | Why it cannot change behavior |
|---|---|---|
| 1 | `FileAssetsBucketEncryptionKey` + alias removed; its `Fn::If[CreateNewKey,…]` uses flattened to the false branch | `AWS_MANAGED_KEY` makes `CreateNewKey` permanently false — the key was dead code. Kills CKV_AWS_33 (wildcard KMS principal) and lets the workshop template drop `kms:CreateKey` (its own last finding — unscopeable in IAM since no key ARN exists before creation). |
| 2 | StagingBucket public-access block hardcoded (all four true) | The bootstrap invocation always passes `PublicAccessBlockConfiguration=true`; the scanner cannot evaluate the `Fn::If`. Kills CKV_AWS_53/54/55/56. |
| 3 | StagingBucket `LoggingConfiguration: {}` (self-targeted access logs) | Additive; bucket holds only CDK deployment assets. Kills CKV_AWS_18. |
| 4 | Example `CdkBoostrapPermissionsBoundaryPolicy` removed | Guarded by a condition that is false unless `UseExamplePermissionsBoundary=true` is passed — the workshop never does; upstream's own comment says it exists only as an example to edit before bootstrapping. Kills CKV_AWS_107/108/109/110/111 on that resource. |
| 5 | `aws:RequestedRegion == AWS::Region` conditions on DeploymentActionRole's three write-capable statements; `sts:GetCallerIdentity` split into its own unconditioned statement (the global STS endpoint would fail a region condition) | The workshop bootstraps exactly one region per account; `cdk deploy` only ever calls CloudFormation in that region. Kills CKV_AWS_111 on the deploy role. |

Re-verify at dependency-refresh windows: fetch `cdk bootstrap
--show-template` for the pinned CDK version, re-apply the five deltas (each
is a small, marked block), and diff — anything outside the marked blocks
must match upstream byte-for-byte. Then re-test module 9 (`agentcore
deploy`) in a test environment: the AWS-managed-key staging bucket and the
region-conditioned deploy role are exercised by every attendee deploy.
