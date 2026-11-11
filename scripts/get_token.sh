#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
# get_token.sh — fetch a Cognito ACCESS token for agent invokes.
# The CUSTOM_JWT authorizer validates ACCESS tokens (not ID tokens —
# no email claim, identity = sub). Reads COGNITO_CLIENT_ID/USER/PASSWORD env.
set -euo pipefail
: "${COGNITO_CLIENT_ID:?}"; : "${COGNITO_USER:?}"; : "${COGNITO_PASSWORD:?}"
aws cognito-idp initiate-auth \
  --auth-flow USER_PASSWORD_AUTH \
  --client-id "$COGNITO_CLIENT_ID" \
  --auth-parameters USERNAME="$COGNITO_USER",PASSWORD="$COGNITO_PASSWORD" \
  --region "${AWS_REGION:-us-east-1}" \
  --query 'AuthenticationResult.AccessToken' --output text
