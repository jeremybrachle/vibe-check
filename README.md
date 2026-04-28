# The Vibe Check

News-aggregation digest pipeline and dashboard.

Currently wired to Hacker News only (free API), with source adapter architecture for adding Reddit, GitHub Trending, Product Hunt, RSS feeds, and more.

---

## ⚠️ Current state (2026-04-27)

**Backend is healthy. Frontend is in transition.**

- The full original frontend is quarantined in `web_backup_2026-04-27/`
  because it hangs the browser tab so hard that DevTools can't even attach
  (suspected infinite loop or runaway sync op in `app.js`).
- The live `web/index.html` is a placeholder "HELLO IT WORKS" page so the
  static mount keeps working while we rebuild incrementally.
- 19/19 backend tests pass. All `/api/v1/*` routes are unaffected.

**Read [HANDOFF_2026-04-27_PART_2.md](HANDOFF_2026-04-27_PART_2.md) for the
current rebuild plan, AWS deployment ordering, and portfolio framing.**

---

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

## How to run it

From a **WSL Ubuntu** terminal:

```bash
cd ~/programming/vibe-check
source .venv/bin/activate
python3 -m uvicorn app.main:app --host localhost --port 8000 --reload
```

Then open <http://localhost:8000/> in your browser. `Ctrl+C` to stop, then
`deactivate` to leave the venv.

Default mode is `heuristic` (no Ollama, no API keys, no network LLM).

> **Why `--host localhost` and not `0.0.0.0`?** `localhost` is correct for
> local dev and gives a clean log line. Use `0.0.0.0` only when deploying
> (e.g. AWS), where the load balancer needs to reach the process from
> outside the host.

### One-time setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # optional — see "Configuration" below
```

### Configuration

Settings can come from either a `.env` file or OS environment variables.
Env vars **override** `.env`. Both are optional — `app/config.py` has sane
defaults.

**Option A — `.env` file** (status quo, easy):
```bash
cp .env.example .env
# edit values as needed; uvicorn auto-loads them
```

**Option B — manual `export`** (more control, no file needed):
```bash
export APP_ENV=development
export LLM_PROVIDER=heuristic
export DATABASE_URL="sqlite:///./vibe_check.db"
export ALLOWED_ORIGINS="http://localhost:5173"
python -m uvicorn app.main:app --host localhost --port 8000 --reload
```

Mixing is fine: keep stable values in `.env`, override one with `export`
for a single run.

### Switching LLM mode

Edit `.env` (or use `export LLM_PROVIDER=...`):

| Value | What it does |
| --- | --- |
| `heuristic` | Local keyword-based summary. **Default.** No Ollama, no keys. |
| `none` | No `ai_summary` at all. Everything else works. |
| `ollama` | Use a running Ollama (set `OLLAMA_BASE_URL` + `OLLAMA_MODEL`). |
| `openai` | Use OpenAI (set `OPENAI_API_KEY`). |
| `auto` | OpenAI if key set, else Ollama if reachable, else none. |

Restart the server after changing the provider.

API docs are at <http://localhost:8000/docs>.

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

Unit tests (no server needed):

```bash
source .venv/bin/activate
python -m pytest tests/ -q
```

Live smoke tests against a running API:

```bash
source .venv/bin/activate

# local app
python tests/smoke_test_live_api.py

# deployed API
API_BASE=http://your-ec2-ip:8000 python tests/smoke_test_live_api.py

# validate CORS for a second frontend
API_BASE=http://your-ec2-ip:8000 CORS_ORIGIN=https://your-other-site.com \
  python tests/smoke_test_live_api.py

# validate protected admin queue endpoint
API_BASE=http://your-ec2-ip:8000 ADMIN_TOKEN=your-token \
  python tests/smoke_test_live_api.py
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
