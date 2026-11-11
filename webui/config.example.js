// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
// Sample code, non-production. See README.md for the full disclaimer.
// Local-dev config: copy over config.js and fill from your dev environment
// (infra/cognito_fixture.sh prints these; api runs on :8080 via uvicorn).
window.WS_CONFIG = {
  apiBase: "http://localhost:8080",
  region: "us-east-1",
  cognito: {
    clientId: "<COGNITO_CLIENT_ID>",
    username: "analyst_martha",
    password: "<COGNITO_PASSWORD>",
  },
};
