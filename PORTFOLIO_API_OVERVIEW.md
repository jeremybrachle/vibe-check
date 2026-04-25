# Portfolio API Overview — The Vibe Check

## What This Project Does

The Vibe Check is a scheduled intelligence backend that turns high-volume public tech signals into a structured, frontend-ready digest.

It ingests sources like Hacker News/Reddit, scores sentiment-like dimensions (excitement vs skepticism), builds ranked stories/themes/tools, and optionally layers in LLM narrative summaries.

The backend includes:

- Scheduled snapshot generation on strict cadence
- Historical snapshot archive in SQLite
- API-first contract for dashboards and external frontends
- Research endpoints for local/cloud LLM ranking + AI vibe summaries
- Admin-only controls for local development workflows

## Why This Is Portfolio-Ready

This is not just a UI project. It demonstrates:

- Backend API design with typed response contracts
- Data pipeline orchestration with scheduled jobs
- Snapshot/history modeling for time-based analytics
- Production-minded controls (CORS, admin token, environment policy)
- Separation of concerns between data, ranking, and presentation

## Recommended Demo Subset (What Your Portfolio Frontend Should Call)

Use this small set to prove end-to-end integration:

- `GET /api/v1/health`
- `GET /api/v1/digest/latest?source=hackernews`
- `GET /api/v1/digest?limit=10&source=hackernews`
- `GET /api/v1/digest/daily-preview/latest?source=hackernews`
- `GET /api/v1/digest/daily-summary/latest?source=hackernews`
- `GET /api/v1/research/overview?scope=local`

This gives you:

- Live backend reachability
- Current digest payload
- Snapshot history list
- Daily scheduled sections
- Research ranking + vibe summary in one call

## Full Endpoint Catalog

Base path for all routes: `/api/v1`

### Core Health + Digest

- `GET /health` — service health check
- `GET /digest/latest?source=hackernews|reddit` — latest full snapshot (`DigestOut`)
- `GET /digest/{digest_id}` — specific snapshot (`DigestOut`)
- `GET /digest/{digest_id}/full` — snapshot + raw data payload (`SnapshotDetailOut`)
- `GET /digest?limit=...&kind=...&source=...` — snapshot list (`DigestListItem[]`)
- `GET /digest/daily-preview/latest?source=...` — latest 9:01 preview section
- `GET /digest/daily-summary/latest?source=...` — latest 5:01 summary section

### Metrics

- `GET /metrics/timeseries?limit=...&kind=...&run_origin=...` — chart-ready trend points
- `GET /metrics/run-history?run_origin=...&limit=...` — run history by origin

### Research

- `GET /research/local-llms/live-ranking?force_refresh=...` — local ranking dataset
- `GET /research/cloud-llms/live-ranking?force_refresh=...` — cloud ranking dataset
- `GET /research/llm-vibe-check?scope=local|cloud&force_refresh=...` — narrative vibe summary
- `GET /research/overview?scope=local|cloud&force_refresh=...` — combined ranking + vibe in one response

### Admin + Scheduler

- `POST /admin/refresh?kind=...&source=...` — manual refresh trigger
- `POST /admin/refresh/override?source=...` — single-click admin override
- `POST /admin/refresh/queue` — legacy queue trigger
- `POST /admin/refresh/queue/cancel` — cancel queue
- `GET /admin/provider` — current provider mode
- `POST /admin/provider?provider=none|heuristic|openai|ollama|auto` — set provider mode
- `GET /admin/scheduler/jobs` — scheduler job list + next runs
- `GET /admin/scheduler/overview` — recent snapshots + upcoming schedule + override state

Admin notes:

- If `ADMIN_TOKEN` is configured, admin endpoints require `X-Admin-Token`.
- In deployed environments (`APP_ENV=production|prod|aws`), admin refresh endpoints are blocked by policy.

## Key Served Data Shapes

### DigestOut (`/digest/latest`, `/digest/{id}`)

- `id`, `kind`, `created_at`
- `sources`, `item_count`, `llm_provider`, `run_origin`
- `ai_summary`
- `excitement_score`, `skepticism_score`
- `today_themes[]`, `most_mentioned_tools[]`
- `excited_about[]`, `skeptical_about[]`, `top_links[]`, `best_rabbit_holes[]`
- `note`, `generated_at`

### DigestListItem (`/digest`)

- `id`, `kind`, `created_at`, `sources`, `item_count`, `llm_provider`, `run_origin`

### ResearchOverviewOut (`/research/overview`)

- `scope`
- `ranking.generated_at`, `ranking.methodology[]`, `ranking.legal_ethics[]`, `ranking.items[]`
- `vibe.generated_at`, `vibe.scope`, `vibe.llm_provider`, `vibe.ai_summary`

### Scheduler Overview (`/admin/scheduler/overview`)

- `running`
- `recent_snapshots[]`
- `upcoming_snapshots[]`
- `manual_queue`
- `admin_override.running`, `admin_override.started_at`

## CORS Setup for Portfolio Frontend

In backend `.env`, allow every frontend origin that will call the API:

`ALLOWED_ORIGINS=https://portfolio.example.com,https://mainapp.example.com,http://localhost:5173`

Then restart backend service.

The backend currently allows:

- Methods: `GET`, `POST`, `OPTIONS`
- Headers: `Authorization`, `Content-Type`, `X-Admin-Token`

## Suggested Portfolio Narrative (Short Version)

"The Vibe Check is an API-first market-signal backend that continuously converts noisy public tech chatter into structured intelligence snapshots. My portfolio frontend consumes selected endpoints (health, digest, history, daily sections, and research overview) to prove real integration against live backend data while also documenting the complete API surface and response contracts."
