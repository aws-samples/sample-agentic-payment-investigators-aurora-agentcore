// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
// Sample code, non-production. See README.md for the full disclaimer.
// auth.js — Cognito ACCESS-token acquisition (identity = sub; access token,
// never ID token). InitiateAuth is a plain JSON POST — no SDK.
//
// AUTHENTIC LOGIN: participants sign in with the
// analyst username+password from the workshop instructions — the same ritual
// a real ops analyst performs. Credentials live in sessionStorage for the
// tab's life; config.js carries ONLY region/clientId (no secrets any more).
// Swapping this module for oidc-client-ts is the production path.

const cfg = window.WS_CONFIG;
let cached = { token: null, exp: 0 };

export function storedCreds() {
  try { return JSON.parse(sessionStorage.getItem("ws-creds") || "null"); }
  catch { return null; }
}

export function signOut() {
  sessionStorage.removeItem("ws-creds");
  cached = { token: null, exp: 0 };
  location.reload();
}

export async function signIn(username, password) {
  const r = await fetch(`https://cognito-idp.${cfg.region}.amazonaws.com/`, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-amz-json-1.1",
      "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
    },
    body: JSON.stringify({
      AuthFlow: "USER_PASSWORD_AUTH",
      ClientId: cfg.cognito.clientId,
      AuthParameters: { USERNAME: username, PASSWORD: password },
    }),
  });
  if (!r.ok) {
    const detail = r.status === 400 ? "wrong username or password" : `Cognito ${r.status}`;
    throw new Error(detail);
  }
  const j = await r.json();
  const token = j.AuthenticationResult?.AccessToken;
  if (!token) throw new Error("no access token in Cognito response");
  sessionStorage.setItem("ws-creds", JSON.stringify({ username, password }));
  const claims = JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
  cached = { token, exp: claims.exp };
  return token;
}

export async function getToken() {
  const now = Date.now() / 1000;
  if (cached.token && now < cached.exp - 60) return cached.token;   // refresh 60s early
  const creds = storedCreds();
  if (!creds) throw new Error("not signed in");
  return signIn(creds.username, creds.password);   // silent refresh with stored creds
}

export async function whoami() {
  const token = await getToken();
  const claims = JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
  return { sub: claims.sub, username: claims.username || storedCreds()?.username };
}
