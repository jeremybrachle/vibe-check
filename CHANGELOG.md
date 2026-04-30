# Changelog

## v2.0.0 - 2026-04-29

**Breaking changes**

- Backend is now API-only. The frontend has moved to its own repo (`vibe-check-frontend`, Vite).
- Removed the FastAPI `StaticFiles` mount at `/`. Anything that was hitting `/` for HTML now gets a 404 — point browsers at the new frontend instead.
- Removed `web/` static placeholder and the S3 + CloudFront `frontend-deploy.yml` GitHub workflow.

**New**

- Added research-feeds API surface at `/api/v1/feeds/*` (`/health`, `/topics`, `/feed?topic=&since=`) — normalized arXiv + Reddit + Hacker News items, server-side dedupe by 12-char SHA1 fingerprint, on-disk cache fallback when upstreams fail.
- Topic vocabulary is YAML-driven (`app/services/feeds/topics.yaml`); ships with `navier_stokes`, `p_vs_np`, `riemann`, `bsd`, `hodge`, `yang_mills`. Adding topics requires no code change.
- Added `feeds_*` settings to `app/config.py` (source toggles, request timeout, lookback window, arXiv min delay, cache dir, topics file, user agent).
- Added `pyyaml==6.0.2` to `requirements.txt`.
- Added 14 offline tests for the feeds module (parser, topic registry, cache roundtrip, API contract). Full suite: 33/33 passing.

## v1.0.0 - 2026-04-25

- Adopted strict refresh cadence: regular snapshots run every 2 hours.
- Daily preview remains scheduled at 9:01 AM ET.
- Daily summary remains scheduled at 5:01 PM PT.
- Removed manual refresh entry points from main user dashboards.
- Added local-only single-click admin override in Settings.
- Added header version/snapshot metadata and in-app changelog section.
