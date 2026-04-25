"""
Live smoke tests — run these against a real deployed instance.

Usage:
  # Local dev:
  python tests/smoke_test_live_api.py

  # Against a deployed server:
  API_BASE=http://your-ec2-ip:8000 python tests/smoke_test_live_api.py

  # With admin token (tests the protected refresh endpoint):
  API_BASE=http://your-ec2-ip:8000 ADMIN_TOKEN=your-token python tests/smoke_test_live_api.py

  # With CORS check for a second frontend origin:
  API_BASE=http://your-ec2-ip:8000 CORS_ORIGIN=https://your-other-site.com python tests/smoke_test_live_api.py

These are NOT pytest tests — they run against a live HTTP server and
require only the standard library + requests.
"""

import os
import sys
import textwrap

try:
    import requests
except ImportError:
    print("ERROR: 'requests' not installed. Run: .venv/bin/pip install requests")
    sys.exit(1)

API_BASE = os.environ.get("API_BASE", "http://localhost:8000").rstrip("/")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
CORS_ORIGIN = os.environ.get("CORS_ORIGIN", "")
TIMEOUT = 15

_passed = 0
_failed = 0
_skipped = 0


def _ok(name: str) -> None:
    global _passed
    _passed += 1
    print(f"  PASS  {name}")


def _fail(name: str, reason: str) -> None:
    global _failed
    _failed += 1
    print(f"  FAIL  {name}")
    for line in textwrap.wrap(f"        {reason}", 80):
        print(line)


def _skip(name: str, reason: str) -> None:
    global _skipped
    _skipped += 1
    print(f"  SKIP  {name} — {reason}")


def _get(path: str, *, headers: dict | None = None, params: dict | None = None) -> requests.Response:
    return requests.get(f"{API_BASE}{path}", headers=headers, params=params, timeout=TIMEOUT)


def _post(path: str, *, headers: dict | None = None, json: dict | None = None) -> requests.Response:
    return requests.post(f"{API_BASE}{path}", headers=headers, json=json, timeout=TIMEOUT)


# ---------------------------------------------------------------------------
# Test definitions
# ---------------------------------------------------------------------------

def test_health():
    name = "GET /api/v1/health returns 200"
    try:
        r = _get("/api/v1/health")
        if r.status_code != 200:
            return _fail(name, f"Status {r.status_code}: {r.text[:200]}")
        data = r.json()
        if "status" not in data:
            return _fail(name, f"Missing 'status' key in response: {data}")
        _ok(name)
    except Exception as e:
        _fail(name, str(e))


def test_digest_latest():
    name = "GET /api/v1/digest/latest returns valid digest"
    try:
        r = _get("/api/v1/digest/latest")
        if r.status_code == 404:
            return _skip(name, "No snapshots in DB yet — run a refresh first")
        if r.status_code != 200:
            return _fail(name, f"Status {r.status_code}: {r.text[:200]}")
        data = r.json()
        for field in ("id", "excitement_score", "skepticism_score", "today_themes", "top_links"):
            if field not in data:
                return _fail(name, f"Missing field '{field}' in digest response")
        _ok(name)
    except Exception as e:
        _fail(name, str(e))


def test_digest_list():
    name = "GET /api/v1/digest?limit=3 returns a list"
    try:
        r = _get("/api/v1/digest", params={"limit": 3})
        if r.status_code != 200:
            return _fail(name, f"Status {r.status_code}: {r.text[:200]}")
        data = r.json()
        if not isinstance(data, list):
            return _fail(name, f"Expected list, got {type(data).__name__}: {str(data)[:100]}")
        _ok(name)
    except Exception as e:
        _fail(name, str(e))


def test_digest_by_id():
    name = "GET /api/v1/digest/{id} round-trip"
    try:
        latest = _get("/api/v1/digest/latest")
        if latest.status_code == 404:
            return _skip(name, "No snapshots in DB yet")
        snapshot_id = latest.json().get("id")
        if not snapshot_id:
            return _fail(name, "digest/latest response has no 'id'")
        r = _get(f"/api/v1/digest/{snapshot_id}")
        if r.status_code != 200:
            return _fail(name, f"Status {r.status_code}: {r.text[:200]}")
        if r.json().get("id") != snapshot_id:
            return _fail(name, f"Returned id {r.json().get('id')} != expected {snapshot_id}")
        _ok(name)
    except Exception as e:
        _fail(name, str(e))


def test_digest_404():
    name = "GET /api/v1/digest/99999999 returns 404"
    try:
        r = _get("/api/v1/digest/99999999")
        if r.status_code != 404:
            return _fail(name, f"Expected 404, got {r.status_code}")
        _ok(name)
    except Exception as e:
        _fail(name, str(e))


def test_daily_preview_latest():
    name = "GET /api/v1/digest/daily-preview/latest"
    try:
        r = _get("/api/v1/digest/daily-preview/latest")
        if r.status_code not in (200, 404):
            return _fail(name, f"Unexpected status {r.status_code}: {r.text[:200]}")
        _ok(name)
    except Exception as e:
        _fail(name, str(e))


def test_daily_summary_latest():
    name = "GET /api/v1/digest/daily-summary/latest"
    try:
        r = _get("/api/v1/digest/daily-summary/latest")
        if r.status_code not in (200, 404):
            return _fail(name, f"Unexpected status {r.status_code}: {r.text[:200]}")
        _ok(name)
    except Exception as e:
        _fail(name, str(e))


def test_metrics_timeseries():
    name = "GET /api/v1/metrics/timeseries"
    try:
        r = _get("/api/v1/metrics/timeseries", params={"days": 3})
        if r.status_code != 200:
            return _fail(name, f"Status {r.status_code}: {r.text[:200]}")
        data = r.json()
        if "points" not in data:
            return _fail(name, f"Missing 'points' key: {data}")
        _ok(name)
    except Exception as e:
        _fail(name, str(e))


def test_scheduler_status():
    name = "GET /api/v1/admin/scheduler/jobs"
    try:
        r = _get("/api/v1/admin/scheduler/jobs")
        if r.status_code != 200:
            return _fail(name, f"Status {r.status_code}: {r.text[:200]}")
        data = r.json()
        if "jobs" not in data:
            return _fail(name, f"Missing 'jobs' key: {data}")
        _ok(name)
    except Exception as e:
        _fail(name, str(e))


def test_provider_status():
    name = "GET /api/v1/admin/provider"
    try:
        r = _get("/api/v1/admin/provider")
        if r.status_code != 200:
            return _fail(name, f"Status {r.status_code}: {r.text[:200]}")
        data = r.json()
        if "provider" not in data:
            return _fail(name, f"Missing 'provider' key: {data}")
        _ok(name)
    except Exception as e:
        _fail(name, str(e))


def test_refresh_requires_token():
    """Uses /admin/refresh/queue (non-blocking) to test token enforcement."""
    name = "POST /api/v1/admin/refresh/queue rejects missing token (when token configured)"
    try:
        r = _post("/api/v1/admin/refresh/queue")
        if r.status_code == 401:
            _ok(name)  # token is configured and enforced — good
        elif r.status_code in (200, 202, 303):
            # No token configured on server — endpoint is open (acceptable for dev)
            _skip(name, "ADMIN_TOKEN not set on server — endpoint is unprotected (set it before going public)")
        else:
            _fail(name, f"Unexpected status {r.status_code}: {r.text[:200]}")
    except Exception as e:
        _fail(name, str(e))


def test_refresh_with_valid_token():
    name = "POST /api/v1/admin/refresh/queue with valid token succeeds"
    if not ADMIN_TOKEN:
        return _skip(name, "Set ADMIN_TOKEN env var to test authenticated endpoints")
    try:
        r = _post("/api/v1/admin/refresh/queue", headers={"X-Admin-Token": ADMIN_TOKEN})
        if r.status_code in (200, 202):
            _ok(name)
        else:
            _fail(name, f"Status {r.status_code}: {r.text[:200]}")
    except Exception as e:
        _fail(name, str(e))


def test_cors_preflight():
    """
    Send a real GET with an Origin header and check the response includes
    access-control-allow-origin. This is what browsers actually care about.
    (OPTIONS preflight may not be handled by Starlette without a specific route.)
    """
    name = "CORS GET includes allow-origin header"
    origin = CORS_ORIGIN or "http://localhost:3000"
    try:
        r = _get("/api/v1/health", headers={"Origin": origin})
        allow_origin = r.headers.get("access-control-allow-origin", "")
        if allow_origin in ("*", origin):
            _ok(f"{name} (origin={origin})")
        else:
            _fail(name, f"allow-origin='{allow_origin}' for origin='{origin}'. "
                        f"Add '{origin}' to ALLOWED_ORIGINS in .env on the server.")
    except Exception as e:
        _fail(name, str(e))


def test_static_frontend():
    name = "GET / serves the frontend HTML"
    try:
        r = requests.get(f"{API_BASE}/", timeout=TIMEOUT)
        if r.status_code != 200:
            return _fail(name, f"Status {r.status_code}")
        if "vibe" not in r.text.lower() and "<!doctype" not in r.text.lower():
            return _fail(name, "Response doesn't look like the frontend HTML")
        _ok(name)
    except Exception as e:
        _fail(name, str(e))


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_health,
    test_digest_latest,
    test_digest_list,
    test_digest_by_id,
    test_digest_404,
    test_daily_preview_latest,
    test_daily_summary_latest,
    test_metrics_timeseries,
    test_scheduler_status,
    test_provider_status,
    test_refresh_requires_token,
    test_refresh_with_valid_token,
    test_cors_preflight,
    test_static_frontend,
]


if __name__ == "__main__":
    print(f"\nVibe Check — live smoke tests")
    print(f"Target: {API_BASE}")
    if CORS_ORIGIN:
        print(f"CORS origin under test: {CORS_ORIGIN}")
    if ADMIN_TOKEN:
        print(f"Admin token: set ({len(ADMIN_TOKEN)} chars)")
    print()

    for t in TESTS:
        try:
            t()
        except Exception as e:
            _fail(t.__name__, f"Unexpected exception: {e}")

    print()
    total = _passed + _failed + _skipped
    print(f"Results: {_passed} passed, {_failed} failed, {_skipped} skipped ({total} total)")

    if _failed:
        sys.exit(1)
