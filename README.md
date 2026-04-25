# The Vibe Check

News-aggregation digest pipeline and dashboard.

Currently wired to Hacker News only (free API), with source adapter architecture for adding Reddit, GitHub Trending, Product Hunt, RSS feeds, and more.

## What this includes

- Multi-source-ready ingestion layer (`app/services/sources/`)
- Hacker News feed ingest (`top`, `new`, `show`, `ask` stories)
- Snapshot archival in SQLite (`raw_items`, `snapshots`)
- Topic clustering and scoring (`AI`, `Programming`, `Security`, `Startups`, `Hardware`, `Science`)
- LLM abstraction:
  - `heuristic` local text generator — no key, no model required. Produces `ai_summary` from structured digest signals.
  - `openai` cloud LLM — drop in key later
  - `ollama` local LLM — point at a running Ollama instance
  - `auto` — picks OpenAI if key exists, otherwise Ollama if configured, otherwise `none`
- LLM-free deployment is fully supported: set `LLM_PROVIDER=heuristic` for local summaries without cloud/model calls, or `LLM_PROVIDER=none` to disable `ai_summary` entirely
- Scheduler:
  - Every 4 hours: regular refresh
  - Daily summary at `5:01 PM` Pacific
  - Daily preview at `9:01 AM` Eastern (uses prior summary + recent snapshots as context)
- API + dashboard UI with manual `Refresh now` button

## Quick start

1. Create and activate your Python virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Create env file:

```bash
cp .env.example .env
```

4. Optional LLM setup:
- For heuristic local summaries (no model), set `LLM_PROVIDER=heuristic`
- For local Ollama summaries, set `LLM_PROVIDER=ollama` and `OLLAMA_MODEL=openchat` (or another pulled model)
- For cloud LLM, set `LLM_PROVIDER=openai` and `OPENAI_API_KEY=...`
- To fully disable summaries, set `LLM_PROVIDER=none`

If using Ollama, start it in a separate terminal first:

```bash
OLLAMA_NOHISTORY=1 OLLAMA_KEEP_ALIVE=30m ollama serve
```

You can verify models with:

```bash
ollama list
```

5. Run app:

```bash
uvicorn app.main:app --reload
```

6. Open:
- Dashboard: `http://127.0.0.1:8000/`
- API docs: `http://127.0.0.1:8000/docs`

## Stop everything

Run these when you want a clean shutdown:

```bash
pkill -f "uvicorn app.main:app" || true
pkill -f "ollama serve" || true
```

Verify both are stopped:

```bash
ss -ltnp | grep -E ':(8000|11434)\b' || echo "no listeners"
```

## Testing

Run the unit tests locally:

```bash
cd /home/kerry/programming/vibe-check
.venv/bin/python -m pytest tests/ -q
```

Run the live smoke tests against a running API:

```bash
# local app
.venv/bin/python tests/smoke_test_live_api.py

# deployed API
API_BASE=http://your-ec2-ip:8000 .venv/bin/python tests/smoke_test_live_api.py

# validate CORS for a second frontend
API_BASE=http://your-ec2-ip:8000 CORS_ORIGIN=https://your-other-site.com \
  .venv/bin/python tests/smoke_test_live_api.py

# validate protected admin queue endpoint
API_BASE=http://your-ec2-ip:8000 ADMIN_TOKEN=your-token \
  .venv/bin/python tests/smoke_test_live_api.py
```

The smoke script checks the deployed API surface end-to-end: health, latest
digest, history, by-id lookup, 404 behavior, daily preview/summary endpoints,
metrics, provider status, scheduler jobs, static frontend delivery, and CORS.

If more than one frontend will call this API, set every allowed origin in
production:

```bash
ALLOWED_ORIGINS=https://your-main-site.com,https://your-other-site.com
```

## API

All routes live under `/api/v1/`. The API is designed to be consumed by any frontend — the hosted dashboard, a portfolio widget, a mobile app, etc.

Portfolio-facing API summary and endpoint catalog:

- `PORTFOLIO_API_OVERVIEW.md`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/health` | App health check |
| GET | `/api/v1/digest/latest` | Latest digest (full `DigestOut`) |
| GET | `/api/v1/digest/{id}` | Specific digest by ID |
| GET | `/api/v1/digest?limit=20&kind=regular` | Paginated history list (`DigestListItem[]`) |
| POST | `/api/v1/admin/refresh?kind=regular` | Trigger ad-hoc fetch and store |
| GET | `/api/v1/admin/provider` | Read current LLM provider |
| POST | `/api/v1/admin/provider?provider=heuristic|ollama|openai|none|auto` | Switch provider at runtime |

`kind` values: `regular`, `daily_summary`, `daily_preview`

### DigestOut shape

Every frontend can rely on these top-level fields:

```jsonc
{
  "id": 42,
  "kind": "regular",
  "created_at": "2026-04-23T17:01:00",
  "sources": ["hackernews"],
  "item_count": 87,
  "llm_provider": "openai",
  "ai_summary": "Today shows...",         // prose — empty only in none mode or failed provider calls
  "excitement_score": 1.42,
  "skepticism_score": 1.08,
  "today_themes": [{"topic": "AI", "count": 22, "signal": 4200.5, "headlines": [...]}],
  "excited_about": ["..."],
  "skeptical_about": ["..."],
  "most_mentioned_tools": [{"name": "ollama", "count": 7}],
  "top_links": [{"title": "...", "url": "...", "score": 312, "comments": 88, "source": "hackernews"}],
  "best_rabbit_holes": [...],            // same shape as top_links
  "note": "Demo uses HN data only...",
  "generated_at": "2026-04-23T17:01:05"
}
```

Interactive docs always available at `http://127.0.0.1:8000/docs`.

## Notes

- Demo uses Hacker News data only. The architecture supports additional sources like Reddit, GitHub Trending, Product Hunt, RSS feeds, and local/cloud LLM summarization.
- No data-cleaning cadence is applied yet; snapshots are archived indefinitely for now.

## Deployment

- Set `ADMIN_TOKEN` before publishing if you want admin refresh endpoints protected.
- Restrict `ALLOWED_ORIGINS` to your real frontend domains before going live.
