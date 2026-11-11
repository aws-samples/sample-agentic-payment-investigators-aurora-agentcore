// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
// Sample code, non-production. See README.md for the full disclaimer.
// config.js — written by CFN at provision time (template in infra/); this file
// is the ONLY thing that differs per account. Local dev: copy config.example.js.
window.WS_CONFIG = window.WS_CONFIG || {
  apiBase: "",
  region: "us-east-1",
  cognito: { clientId: "", username: "", password: "" },
};
