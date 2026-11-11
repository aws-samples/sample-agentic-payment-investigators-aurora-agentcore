# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
"""runtime_manager.py — Custom::AgentRuntime Lambda handler.

Creates the provision-time investigator runtime via the SDK instead of
AWS::BedrockAgentCore::Runtime, so the resource is NOT a member of the
workshop CFN stack. That single property is what makes module 7 possible:
`agentcore import runtime` performs a real CloudFormation IMPORT, and CFN
refuses to import a resource already managed by another stack (it fails
with "already managed by stack <name>"). SDK-created =
unmanaged = adoptable by the attendee's CLI project.

Ships in provisioner.zip beside seeder.py with boto3 vendored (the Lambda
runtime's own boto3 may predate the bedrock-agentcore-control model).

Delete is tolerant: after module 7 the runtime belongs to the attendee's
AgentCore-* CDK stack; if it is already gone (or the account is being
reclaimed) we succeed anyway.
"""
import json
import time
import urllib.request

import boto3


def _respond(event, context, status, data=None, reason="", physical_id=None):
    body = json.dumps({
        "Status": status,
        "Reason": (reason or "")[:800] + f" (log: {context.log_stream_name})",
        "PhysicalResourceId": physical_id or event.get(
            "PhysicalResourceId", f"agent-runtime-{event['LogicalResourceId']}"),
        "StackId": event["StackId"],
        "RequestId": event["RequestId"],
        "LogicalResourceId": event["LogicalResourceId"],
        "Data": data or {},
    }).encode()
    req = urllib.request.Request(event["ResponseURL"], data=body, method="PUT",
                                 headers={"Content-Type": ""})
    urllib.request.urlopen(req, timeout=30)  # nosec B310 - CFN-supplied https ResponseURL


def _config_kwargs(p):
    """Shared Create/Update config from ResourceProperties."""
    return dict(
        agentRuntimeArtifact={
            "codeConfiguration": {
                "runtime": "PYTHON_3_14",
                "entryPoint": ["main.py"],
                "code": {"s3": {"bucket": p["CodeBucket"], "prefix": p["CodeKey"]}},
            }
        },
        roleArn=p["RoleArn"],
        networkConfiguration={
            "networkMode": "VPC",
            "networkModeConfig": {
                "subnets": p["Subnets"],
                "securityGroups": p["SecurityGroups"],
            },
        },
        authorizerConfiguration={
            "customJWTAuthorizer": {
                "discoveryUrl": p["DiscoveryUrl"],
                "allowedClients": p["AllowedClients"],
            }
        },
        requestHeaderConfiguration={"requestHeaderAllowlist": ["Authorization"]},
        environmentVariables=p["EnvironmentVariables"],
        # Platform version PINNED to V1 (2026-11-03). AWS Health notice: from
        # 2026-11-12 a CreateAgentRuntime without platformVersion lands on
        # Runtime V2 (snapshot/restore: create takes minutes, 120 s /ping
        # health rule, ~2 s restores). The workshop was certified on V1 —
        # explicit on Create AND Update (omitting it on Update would also
        # keep V1; explicit beats implicit). The module-9 attendee runtime is
        # pinned separately: the vendored project skeleton's
        # agentcore/cdk/lib/cdk-stack.ts sets the PlatformVersion property.
        platformVersion="V1",
    )


def handler(event, context):
    try:
        ctrl = boto3.client("bedrock-agentcore-control",
                            region_name=event["ResourceProperties"]["Region"])
        p = event["ResourceProperties"]
        req_type = event["RequestType"]

        if req_type == "Delete":
            rid = event.get("PhysicalResourceId", "")
            if rid.startswith("arn:"):
                rid = rid.rsplit("/", 1)[1]
            try:
                ctrl.delete_agent_runtime(agentRuntimeId=rid)
                # wait for deletion so VPC-mode ENIs release before the
                # stack tears down subnets/SGs (the AgentCore ENI tail)
                for _ in range(60):
                    try:
                        ctrl.get_agent_runtime(agentRuntimeId=rid)
                        time.sleep(10)
                    except ctrl.exceptions.ResourceNotFoundException:
                        break
            except Exception as e:  # already imported+deleted, or reclaimed
                print(f"delete tolerated: {type(e).__name__}: {e}")
            return _respond(event, context, "SUCCESS")

        if req_type == "Update":
            rid = event["PhysicalResourceId"].rsplit("/", 1)[1]
            ctrl.update_agent_runtime(agentRuntimeId=rid, **_config_kwargs(p))
        else:  # Create
            resp = ctrl.create_agent_runtime(
                agentRuntimeName=p["RuntimeName"],
                description="payment investigator agents (dispute/recon/copilot in one bundle)",
                tags={"workshop-id": "payment-investigation"},
                **_config_kwargs(p))
            rid = resp["agentRuntimeId"]

        # wait READY (create ~2-3 min in VPC mode)
        arn = None
        for _ in range(90):
            rt = ctrl.get_agent_runtime(agentRuntimeId=rid)
            arn = rt["agentRuntimeArn"]
            if rt["status"] == "READY":
                break
            if rt["status"].endswith("FAILED"):
                raise RuntimeError(f"runtime {rid} status {rt['status']}")
            time.sleep(10)
        else:
            raise RuntimeError(f"runtime {rid} not READY in 15 min")

        print(f"runtime {rid} READY ({req_type})")
        _respond(event, context, "SUCCESS",
                 data={"AgentRuntimeArn": arn, "AgentRuntimeId": rid},
                 physical_id=arn)
    except Exception as e:
        print(f"RUNTIME MANAGER FAILED: {type(e).__name__}: {e}")
        _respond(event, context, "FAILED", reason=f"{type(e).__name__}: {e}")
