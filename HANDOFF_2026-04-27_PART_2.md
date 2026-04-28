# Handoff — 2026-04-27 Part 2 — Post-Shrek Rebuild Plan

**Author**: Kerry + Copilot (in-session)
**Status**: Backend + static mount confirmed healthy. Old frontend
quarantined. Live `web/` is a placeholder shrek page. Frontend rebuild and
AWS deployment to follow.

---

## What we discovered today

### Confirmed working
- **Backend**: 19/19 pytest pass, `uvicorn` serves correctly, `/api/v1/health`
  responds, all asset routes (`/styles.css`, `/app.js`, etc.) return their
  bytes correctly when checked via `curl` from inside WSL.
- **Static mount**: A minimal `web/index.html` (the lime-green "HELLO IT WORKS"
  page) loads in **both** InPrivate and normal Edge windows at
  `http://localhost:8000/`. So FastAPI's `StaticFiles` mount, the WSL ↔
  Windows network bridge, and the browser cache pipeline are all fine.
- **Browser**: Renders fresh content immediately when given simple HTML.

### Confirmed broken
- The original `web/app.js` (or something in `web/index.html` that triggers it)
  hangs the browser tab so hard that DevTools won't even open and right-click
  is dead. This is consistent with an **infinite loop or runaway synchronous
  operation** in the JS init path. Could be a `while`/`for` that never
  terminates, a `MutationObserver` that fires its own mutation, or a
  `requestAnimationFrame` recursion with no exit condition. We did not pin it
  down because the browser locks up before any tooling can attach.

### Quarantined
The full original frontend (`app.js`, `index.html`, `styles.css`,
`runtime-config.js`, `favicon.svg`, `test.html`) is preserved untouched in
`web_backup_2026-04-27/`. That folder is **the source for the eventual
`vibe-check-web` repo and/or the rebuild target**.

---

## New conventions going forward

### How to run the backend (preferred style)

We're switching from `.venv/bin/python ...` to **activate + plain `python`**
for clarity and control:

```bash
cd ~/programming/vibe-check
source .venv/bin/activate         # activates the venv; prompt now shows (.venv)
python -m uvicorn app.main:app --host localhost --port 8000 --reload
```

When done:
```bash
deactivate                        # leaves the venv; prompt returns to normal
```

Why `--host localhost` instead of `0.0.0.0`?
- `localhost` makes the log line clean and matches how you actually use it.
- `0.0.0.0` is correct for AWS (the load balancer needs to reach the process
  from outside); use it then, not now.

### Configuration: `.env` vs manual `export`

Both work. The chain of precedence is:

1. OS environment variables (set via `export`) — **highest priority**
2. Values in `.env`
3. Defaults in `app/config.py` — lowest priority

So you can do **either** of these:

**Option A — keep `.env` (status quo):**
```bash
# .env contains:
LLM_PROVIDER=heuristic
APP_ENV=development
# ...etc
```
Pydantic auto-loads it. No action needed.

**Option B — manual exports, no `.env` file:**
```bash
export APP_NAME="The Vibe Check"
export APP_ENV=development
export LLM_PROVIDER=heuristic
export DATABASE_URL="sqlite:///./vibe_check.db"
export ALLOWED_ORIGINS="http://localhost:5173"
# then start uvicorn
python -m uvicorn app.main:app --host localhost --port 8000 --reload
```

You can also **mix** them: keep `.env` for stable settings, override
specifics with `export` when needed:
```bash
export LLM_PROVIDER=ollama   # this wins, even if .env says heuristic
python -m uvicorn app.main:app --host localhost --port 8000 --reload
```

### Stopping the server

`Ctrl+C` in the uvicorn terminal. If it doesn't fully release the port:
```bash
pkill -f "uvicorn app.main:app"
ss -tlnp | grep :8000 || echo "port clear"
```

---

## Plan: Incremental frontend rebuild

We'll add things back to `web/` one capability at a time. **Reload the page
between every step.** If a step breaks the page, you've found the culprit
without having to read 3000 lines of JS.

### Phase 0 — Where we are now
- `web/index.html` = lime-green shrek page, no JS, no fetches
- Hits: nothing external, nothing internal
- ✅ confirmed loading

### Phase 1 — Add a button (no JS yet)
Edit `web/index.html`, add a `<button>HELLO</button>` somewhere visible.
Reload. Should show. Confirms basic interactivity isn't blocked.

### Phase 2 — Inline JS, no API
Add a `<script>` tag at the bottom that wires the button to log to the
console. Confirms the browser will actually run our JS.

### Phase 3 — One fetch to one endpoint
Add a `<script>` that calls `fetch('/api/v1/health')` and writes the
response into the page. Confirms the frontend ↔ backend round-trip.

### Phase 4 — One real digest
Call `/api/v1/digest/latest`. Render `ai_summary` into a `<pre>` tag.
Confirms the real data shape is what the new frontend will need to handle.

### Phase 5 — Port pieces of the original UI
At this point the original `app.js` becomes a *reference*, not a *runtime*.
Pull over the styles/components you actually want, **piece by piece**, into a
clean small file. Skip anything you don't fully understand — you can always
come back. The original will still be sitting safely in
`web_backup_2026-04-27/`.

### Phase 6 — Lock it in
Once Phase 5 reaches feature parity with what you actually use, rename
`web_backup_2026-04-27/` to `web_legacy/` (or delete it, it's in git history)
and commit.

---

## How to prevent the "blank page from JS bug" class of regression

Without adding linting (per your preference), the cheapest regression net is
a tiny **post-deploy smoke check**:

```bash
# After starting the server:
curl -sS http://localhost:8000/ | grep -q "HELLO IT WORKS\|Vibe Check" && echo "page returns OK"
```

That confirms the HTML is being served. It does NOT confirm the page renders
in a browser — that needs a headless browser smoke (Playwright, ~1 line of
code). When the new frontend is far enough along, we can add a single
Playwright check that asserts `document.body.children.length > 0` after
2 seconds. That single check would have caught today's bug.

We're not adding it today; flagged for after Phase 4.

---

## Portfolio framing decision

Going forward this is **two products**:

1. **`vibe-check-api`** — the public-facing portfolio repo.
   - Standalone FastAPI service.
   - Great README, OpenAPI docs at `/docs`, smoke tests, deployment notes.
   - Anyone can consume it from any frontend.
   - Pitched as "data API for builder/news signal."

2. **`vibe-check-web`** — the *example consumer*.
   - Clearly labeled as "one possible UI for the API."
   - Demonstrates client-side caching, polite refresh cadence, etc.
   - Lives in its own repo so frontend changes never risk the API.

This is a stronger portfolio story than a bundled monolith — it shows you
think about API design as a product, not just a backend for one UI.

---

## AWS deployment ordering (for when we get there)

Your instinct to "deploy frontend first with no data, then API separately"
is the right call. Recommended order:

1. **Pick the lightest possible frontend host first** (S3 + CloudFront, or
   Netlify, or Vercel free tier). Deploy the current Phase-0 shrek page.
   Confirms the deploy pipeline works without involving Python at all.
2. **Then deploy the API** — likely an EC2 instance or App Runner. Bind to
   `0.0.0.0`, put nginx or ALB in front for TLS. Set:
   - `APP_ENV=production` (this turns OFF the local-only admin override,
     which is already coded in `app/api.py` `_require_local_admin_runtime`)
   - `ALLOWED_ORIGINS=https://your-frontend-url.com`
   - `LLM_PROVIDER=heuristic` (no Ollama on AWS, no OpenAI bill)
   - `ADMIN_TOKEN=<some-strong-secret>` (just in case)
3. **Update the frontend's `runtime-config.js`** with the deployed API URL,
   redeploy frontend.
4. **Run smoke tests** against the deployed API:
   ```bash
   API_BASE=https://your-api.com python tests/smoke_test_live_api.py
   ```

The ordering matters because: deploying static files is essentially
unbreakable, so it gives you a "yes I can deploy something" win before you
touch any of the EC2 / IAM / VPC / nginx complexity that the API will bring.

---

## Refresh cadence — recommendation

Current scheduler runs every 4 hours. For a news aggregator, that's fine but
arguably overkill. Consider dropping to **every 6 hours** or **3x/day at
fixed clock times** (8am / 2pm / 8pm ET). Two reasons:

1. Hacker News doesn't change *that* fast for top-of-day signal.
2. Less cron activity = simpler logs = less to explain in a portfolio writeup.

On the browser side, you can short-circuit most page loads:
```js
// Pseudo-pattern for the rebuilt frontend:
const CACHE_KEY = "vibe-check-latest";
const CACHE_TTL_MS = 30 * 60 * 1000;  // 30 minutes
const cached = JSON.parse(localStorage.getItem(CACHE_KEY) || "null");
if (cached && Date.now() - cached.savedAt < CACHE_TTL_MS) {
  render(cached.data);
} else {
  const data = await fetch("/api/v1/digest/latest").then(r => r.json());
  localStorage.setItem(CACHE_KEY, JSON.stringify({ data, savedAt: Date.now() }));
  render(data);
}
```

That alone takes the API call out of 90% of page loads.

---

## Files to know about right now

| Path | Status |
| --- | --- |
| `web/index.html` | Minimal shrek page. Live. Loads. |
| `web_backup_2026-04-27/` | Frozen original frontend. Reference for rebuild. |
| `app/` | Backend untouched. 19/19 tests pass. |
| `.env` | Working. `LLM_PROVIDER=heuristic`. Optional going forward. |
| `HANDOFF_2026-04-27.md` | Original Step 1/2/3 plan from earlier today. Step 1 succeeded after font fix; Steps 2/3 superseded by this doc. |
| `HANDOFF_2026-04-27_PART_2.md` | This file. The current source of truth. |
| `HANDOFF_BROKEN_BROWSER.md` | Outdated. Safe to delete. |
| `HANDOFF_FRONTEND_BLANK.md` | Outdated. Safe to delete. |
| `README.md` | Updated to point at this doc + new run conventions. |

---

## Quick reference — daily workflow

```bash
# Start
cd ~/programming/vibe-check
source .venv/bin/activate
python -m uvicorn app.main:app --host localhost --port 8000 --reload

# In another terminal: smoke check
curl -sSI http://localhost:8000/ | head -1                # HTTP 200
curl -sS http://localhost:8000/api/v1/health              # {"status":"ok",...}

# In browser
http://localhost:8000/                                    # see the shrek page

# Stop
# (Ctrl+C in uvicorn terminal)
deactivate
```
