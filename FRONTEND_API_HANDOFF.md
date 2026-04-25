# Frontend API Handoff — Integrating The Vibe Check Backend

This document explains how another frontend project can consume the API running from this backend.

## Base URL

Use your backend origin as the base URL, for example:

- Local: http://127.0.0.1:8000
- AWS EC2: http://YOUR_EC2_IP:8000
- Domain: https://api.yourdomain.com

All endpoints are under:

- /api/v1

## CORS Configuration (Required)

Set all frontend origins in the backend .env:

ALLOWED_ORIGINS=https://app-one.example.com,https://app-two.example.com,http://localhost:3000

Notes:

- Do not use * in production.
- Origins must include scheme (http/https).
- Separate entries with commas.
- Restart backend after changing .env.

Backend CORS currently allows:

- Methods: GET, POST, OPTIONS
- Headers: Authorization, Content-Type, X-Admin-Token

## Auth / Admin Endpoints

Most read endpoints are public.
Admin endpoints require X-Admin-Token when ADMIN_TOKEN is configured.

Example admin header:

X-Admin-Token: <your-token>

Important:

- Admin refresh operations are local-only by policy when APP_ENV is production/prod/aws.
- In deployed environments, admin refresh endpoints return 403.

## Most Common Endpoints for Frontends

### Health
- GET /api/v1/health

### Latest snapshot digest for a source
- GET /api/v1/digest/latest?source=hackernews
- GET /api/v1/digest/latest?source=reddit

### Snapshot list/history
- GET /api/v1/digest?limit=20&source=hackernews
- Optional kind filter: kind=regular|daily_preview|daily_summary

### Snapshot by id
- GET /api/v1/digest/{id}
- GET /api/v1/digest/{id}/full

### Daily sections
- GET /api/v1/digest/daily-preview/latest?source=hackernews
- GET /api/v1/digest/daily-summary/latest?source=hackernews

### Metrics
- GET /api/v1/metrics/timeseries?limit=30
- GET /api/v1/metrics/run-history?run_origin=manual&limit=120

### Scheduler status (for admin/status pages)
- GET /api/v1/admin/scheduler/jobs
- GET /api/v1/admin/scheduler/overview

### Provider status
- GET /api/v1/admin/provider
- POST /api/v1/admin/provider?provider=none|heuristic|ollama|openai|auto

## Example fetch calls

### 1) Read latest digest

```js
const API_BASE = "https://api.yourdomain.com";

async function getLatestDigest(source = "hackernews") {
  const res = await fetch(`${API_BASE}/api/v1/digest/latest?source=${encodeURIComponent(source)}`);
  if (!res.ok) throw new Error(`Failed latest digest: ${res.status}`);
  return res.json();
}
```

### 2) Read digest history

```js
async function getDigestHistory(source = "hackernews", limit = 20) {
  const url = `${API_BASE}/api/v1/digest?source=${encodeURIComponent(source)}&limit=${limit}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed digest history: ${res.status}`);
  return res.json();
}
```

### 3) Admin call with token header

```js
async function triggerAdminOverride(source = "hackernews", adminToken) {
  const url = `${API_BASE}/api/v1/admin/refresh/override?source=${encodeURIComponent(source)}`;
  const res = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Admin-Token": adminToken,
    },
  });

  if (!res.ok) {
    const body = await res.text();
    throw new Error(`Admin override failed: ${res.status} ${body}`);
  }
  return res.json();
}
```

## Frontend integration checklist

1. Confirm backend is reachable from your frontend environment.
2. Add frontend origin to ALLOWED_ORIGINS in backend .env.
3. Restart backend service.
4. Call /api/v1/health from browser and confirm 200.
5. Call /api/v1/digest/latest?source=hackernews and render summary fields.
6. If using admin endpoints, send X-Admin-Token header and verify 401/403 behavior as expected.

## Troubleshooting

### CORS error in browser console

- Ensure exact frontend origin is listed in ALLOWED_ORIGINS.
- Ensure scheme and port match exactly.
- Restart backend after .env changes.
- Verify preflight uses only allowed headers.

### Works in curl/Postman but not in browser

- That is usually CORS policy mismatch.
- Re-check ALLOWED_ORIGINS and allowed headers.

### Admin endpoint returns 403 in deployed env

- Expected when APP_ENV is production/prod/aws.
- Admin refresh operations are intentionally local-only.

## Optional smoke test command

Run the provided live smoke test against a frontend origin:

API_BASE=http://your-ec2-ip:8000 CORS_ORIGIN=https://your-other-site.com python tests/smoke_test_live_api.py
