# EOD Handoff — 2026-04-29

> **Status:** ✅ Frontends wired to vibe-check backend successfully. Feeds API live.

---

## TL;DR

- vibe-check now exposes **two API surfaces** under one backend:
  - `/api/v1/digest/*` — existing daily-summary pipeline
  - `/api/v1/feeds/*` — NEW research-feed scraper (arXiv + Reddit + HN)
- Frontends are connected and reading from both. CORS already permits
  the dev origins.
- 33/33 tests passing (19 original + 14 new feeds tests).
- Standalone `scraper-api` project preserved as reference; not in any
  runtime path.

---

## What shipped today

### 1. Frontend separated into its own project
- Repo: `~/programming/vibe-check-frontend`
- Stack: Vite 8, port `5173`
- Builds clean, dev server runs via:
  ```bash
  source /home/kerry/.nvm/nvm.sh && nvm use 22
  cd ~/programming/vibe-check-frontend
  npm run dev
  ```
- ⚠️ Must use Linux Node (nvm) — Windows Node at `/mnt/c/Program Files/nodejs/`
  breaks Vite over WSL UNC paths.

### 2. Scraper integrated into vibe-check as `/api/v1/feeds/*`

New module: [app/services/feeds/](../vibe-check/app/services/feeds/)

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/feeds/health` | Sources + topic list |
| `GET /api/v1/feeds/topics` | Topic names only |
| `GET /api/v1/feeds/feed?topic=<name>&since=<iso8601>` | Normalized arXiv + Reddit + HN items |

Topics live in [app/services/feeds/topics.yaml](../vibe-check/app/services/feeds/topics.yaml)
— add new ones with a YAML edit, no code change.

Current vocabulary: `navier_stokes`, `p_vs_np`, `riemann`, `bsd`,
`hodge`, `yang_mills`.

### 3. Frontend ↔ backend wiring confirmed
- Both frontends successfully consume vibe-check at `http://localhost:8000`.
- Smoke test passed:
  ```bash
  curl http://localhost:8000/api/v1/feeds/health
  curl 'http://localhost:8000/api/v1/feeds/feed?topic=navier_stokes'
  ```
- Returned live items from arXiv + Reddit with valid fingerprints,
  dedupe working, cache fallback working.

### 4. pvnp consumer handoff written
- New doc: [pvnp/FEEDS_CONSUMER_HANDOFF.md](../pvnp/FEEDS_CONSUMER_HANDOFF.md)
- Supersedes the old `scraper-api/CONSUMER_HANDOFF.md` and pvnp's
  `HANDOFF_SCRAPER_MIGRATION.md`.
- Includes drop-in Python client, migration checklist, and full
  endpoint contract.

---

## Files touched in vibe-check

**New:**
- `app/services/feeds/__init__.py`
- `app/services/feeds/topics.yaml` (6 topics)
- `app/services/feeds/topics.py` (registry loader, lru_cached)
- `app/services/feeds/schemas.py` (`FeedItem`, `FeedResponse`, `FeedsHealthOut`)
- `app/services/feeds/cache.py` (read/write JSON cache per topic)
- `app/services/feeds/sources/{base,arxiv,reddit,hn}.py`
- `app/feeds_api.py` (router mounted at `/api/v1/feeds`)
- `tests/test_feeds_arxiv_parser.py`
- `tests/test_feeds_topics.py`
- `tests/test_feeds_cache.py`
- `tests/test_feeds_api.py` (14 tests, all offline via monkeypatched `_gather`)
- `data/feeds_cache/` (runtime, gitignored)

**Modified:**
- `app/config.py` — added 9 `feeds_*` settings + `_REPO_ROOT` constant
- `app/main.py` — mounts `feeds_router`
- `requirements.txt` — added `pyyaml==6.0.2`

---

## How to start the stack tomorrow

```bash
# Backend (terminal 1)
cd ~/programming/vibe-check
source .venv/bin/activate
python -m uvicorn app.main:app --host localhost --port 8000 --reload

# Frontend (terminal 2)
source /home/kerry/.nvm/nvm.sh && nvm use 22
cd ~/programming/vibe-check-frontend
npm run dev
# → http://localhost:5173
```

Smoke check:
```bash
curl -s http://localhost:8000/api/v1/feeds/health | python -m json.tool
```

---

## Architecture as of today

```
┌──────────────────────────┐         ┌──────────────────────────┐
│  vibe-check-frontend     │         │  pvnp daily pipeline     │
│  (Vite, :5173)           │         │  (feeds_client.py)       │
└────────────┬─────────────┘         └────────────┬─────────────┘
             │                                    │
             │   GET /api/v1/digest/*             │   GET /api/v1/feeds/feed?topic=...
             │   GET /api/v1/feeds/feed?topic=... │
             ▼                                    ▼
        ┌───────────────────────────────────────────────┐
        │            vibe-check (FastAPI, :8000)        │
        │  ┌─────────────────┐   ┌────────────────────┐ │
        │  │ /api/v1/digest  │   │ /api/v1/feeds      │ │
        │  │ (existing)      │   │ (NEW today)        │ │
        │  └─────────────────┘   └─────────┬──────────┘ │
        │                                  │            │
        │           ┌──────────────────────┼──────────┐ │
        │           ▼                      ▼          ▼ │
        │       arXiv API              Reddit JSON   HN Algolia
        └───────────────────────────────────────────────┘

  ~/programming/scraper-api  ← reference only, not in runtime path
```

---

## Open / next up

- [ ] **pvnp migration**: swap pvnp's daily pipeline from any embedded
      scraper code to the `feeds_client.py` from
      [FEEDS_CONSUMER_HANDOFF.md](../pvnp/FEEDS_CONSUMER_HANDOFF.md).
      Migration checklist is in that doc.
- [ ] **Mark obsolete handoffs**: `pvnp/HANDOFF_SCRAPER_MIGRATION.md`
      and `scraper-api/CONSUMER_HANDOFF.md` are now superseded — either
      delete or add a "SUPERSEDED" header.
- [ ] **Frontend feeds UI**: vibe-check-frontend currently only renders
      digest data. Adding a "Research feeds" tab/page is straightforward
      — same fetch pattern, new endpoint.
- [ ] **Production deploy**: when vibe-check ships beyond localhost,
      update consumers' `VIBE_CHECK_BASE_URL` env var.
- [ ] **Topic expansion**: any new topic = 3 lines of YAML in
      `app/services/feeds/topics.yaml`. Candidates discussed:
      `quantum_complexity`, `homotopy_type_theory`, `webdev`.

---

## Watch-outs / gotchas

- **WSL Node**: always `nvm use 22` before any npm command in WSL.
  Windows Node = broken UNC paths.
- **arXiv rate limit**: server enforces 3.1s between arXiv categories
  per request. A single `/feed` call can take ~10s in the worst case.
  Don't poll faster than once per topic per hour.
- **Cache fallback is silent-ish**: when upstream returns nothing, the
  response has `cached: true` and `cache_reason` set. Frontends should
  surface that to the user (e.g., "showing cached results from
  YYYY-MM-DD").
- **No auth on feeds**: matches the rest of the public read surface.
  If we ever add per-consumer quotas, that's a `/api/v2/feeds/...`
  breaking change.
- **`datetime.utcnow()` deprecation warnings** in pre-existing
  `app/scheduler.py` and `app/api.py`. Not blocking; cleanup task for
  another day.

---

## Test status

```
33 passed, 5 warnings in 3.03s
```

- 19 original vibe-check tests: still green
- 14 new feeds tests: green
- Warnings: all `datetime.utcnow()` deprecations in pre-existing code,
  unrelated to today's work.
