#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
# cognito_fixture.sh — stand up the workshop's identity shape for a dev environment.
# This is a hand-built prototype of what the CFN will own:
# one user pool + one app client + one seeded analyst user.
#
# Usage:  ./cognito_fixture.sh create   → prints COGNITO_* exports
#         ./cognito_fixture.sh delete <pool-id>
#
# Access-token notes: the CUSTOM_JWT authorizer validates the
# ACCESS token; identity is its `sub` claim (no email claim on access tokens).
# USER_PASSWORD_AUTH is enabled on the client for headless harness use — the
# workshop CFN keeps that (terminal invokes need InitiateAuth).
set -euo pipefail
REGION="${AWS_REGION:-us-east-1}"
POOL_NAME="${POOL_NAME:-payment-ws-dev-pool}"
ANALYST_USER="${ANALYST_USER:-analyst_martha}"

case "${1:-create}" in
create)
  POOL_ID=$(aws cognito-idp create-user-pool --pool-name "$POOL_NAME" --region "$REGION" \
    --policies 'PasswordPolicy={MinimumLength=16,RequireUppercase=true,RequireLowercase=true,RequireNumbers=true,RequireSymbols=false}' \
    --query 'UserPool.Id' --output text)
  CLIENT_ID=$(aws cognito-idp create-user-pool-client --user-pool-id "$POOL_ID" --region "$REGION" \
    --client-name workshop-app \
    --explicit-auth-flows ALLOW_USER_PASSWORD_AUTH ALLOW_REFRESH_TOKEN_AUTH \
    --query 'UserPoolClient.ClientId' --output text)
  # ≥16 chars, no leading symbol
  PASSWORD="Wk$(python3 -c "import secrets; print(secrets.token_hex(12))")A1"
  aws cognito-idp admin-create-user --user-pool-id "$POOL_ID" --username "$ANALYST_USER" \
    --message-action SUPPRESS --region "$REGION" >/dev/null
  aws cognito-idp admin-set-user-password --user-pool-id "$POOL_ID" --username "$ANALYST_USER" \
    --password "$PASSWORD" --permanent --region "$REGION"
  SUB=$(aws cognito-idp admin-get-user --user-pool-id "$POOL_ID" --username "$ANALYST_USER" \
    --region "$REGION" --query 'UserAttributes[?Name==`sub`].Value' --output text)
  cat <<OUT
export COGNITO_POOL_ID=$POOL_ID
export COGNITO_CLIENT_ID=$CLIENT_ID
export COGNITO_DISCOVERY_URL=https://cognito-idp.$REGION.amazonaws.com/$POOL_ID/.well-known/openid-configuration
export COGNITO_USER=$ANALYST_USER
export COGNITO_PASSWORD=$PASSWORD
export COGNITO_USER_SUB=$SUB
OUT
  ;;
delete)
  aws cognito-idp delete-user-pool --user-pool-id "${2:?pool id}" --region "$REGION"
  echo "deleted ${2}"
  ;;
esac
