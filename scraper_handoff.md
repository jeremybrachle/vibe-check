# Handoff — Move Web Scraping Out of `pvnp`, Call External API Instead

> **Audience:** the `pvnp` repo on one side, Kerry's *other* web-scraper
> project on the other. This doc describes the migration *and* the API
> contract so the two repos can be developed independently.

## Why

`pvnp` is a research-attempts toolkit. It should not own:
- HTTP client code
- Cache files for arXiv / reddit / HN
- Rate-limit logic
- Per-source parsers (Atom, JSON, RSS)
- Source-specific quirks (reddit blocks default UA, arXiv wants 3s delays, etc.)

Kerry already has a separate web-scraper project. That's the right home for
all of the above. `pvnp` should treat scraped feed data as a **read-only
external service**, the same way it treats Cremona's GitHub data.

## What the contract looks like

`pvnp` calls one HTTP endpoint and gets back a normalized JSON list of
"feed items" for a topic. That's it.

### Endpoint

```
GET {SCRAPER_BASE_URL}/v1/feed?topic=<topic>&since=<iso8601>
```

- `topic` — string, e.g. `navier_stokes`, `yang_mills`, `bsd`. The scraper
  project owns the topic config (which subreddits, which arXiv categories,
  which custom RSS, etc.). `pvnp` does not.
- `since` — optional ISO-8601 timestamp. If provided, only items newer than
  this are returned (deltas). Default = last 24h.

### Response shape

```json
{
  "topic": "navier_stokes",
  "generated_utc": "2026-04-30T12:34:56Z",
  "items": [
    {
      "title": "Spectral methods for 2D NS singularities",
      "link":  "https://arxiv.org/abs/2604.12345",
      "summary": "We present a pseudo-spectral solver…",
      "source": "arxiv:math.AP",
      "published_utc": "2026-04-30T08:12:00Z",
      "fingerprint": "a1b2c3d4e5f6"
    },
    {
      "title": "Has anyone tried to attack NS with a transformer?",
      "link":  "https://reddit.com/r/LLMPhysics/comments/abc123/...",
      "summary": "I've been training a model on…",
      "source": "reddit:LLMPhysics",
      "published_utc": "2026-04-30T11:02:00Z",
      "fingerprint": "f6e5d4c3b2a1"
    }
  ]
}
```

### Auth

Either:
- No auth — bind the API to localhost and rely on network isolation, OR
- A single bearer token in `Authorization: Bearer <token>` header, with the
  token in `pvnp`'s `.env` file.

Recommend the bearer-token version even for localhost; trivial to add and
makes the eventual move to a hosted version a no-op on the `pvnp` side.

### Errors

Standard HTTP. On any 5xx or timeout, `pvnp` falls back to its local cache
of the last successful response (kept under
`artifacts/cache/scraper/<topic>.json`). Mutation seeding still works, just
without fresh data.

---

## How the scraper would influence mutation (the "is that relevant?" question)

**Yes, very.** Here's the loop the user asked about:

```
1. scraper.fetch(topic="navier_stokes", since=24h_ago)
     → 30 new arxiv abstracts + 5 reddit threads
2. extract keywords from titles+abstracts
     → ["Burgers", "Tao Smoothness", "spectral truncation", ...]
3. seed = MutationSeed(rng_seed=hash(date), keywords=keywords)
4. each problem's attempt(seed) is free to *use* those keywords:
     - p_vs_np / hail_mary: bias creative_attack toward terms in `keywords`
     - bsd: pick conductor ranges mentioned in fresh papers
     - yang_mills: tune β toward values appearing in recent literature
     - riemann: prioritise t-ranges if a paper mentions specific zeros
5. result.details["keywords_used"] = keywords  (already done)
```

This is *already* the design — see [daily/mutation_seeds.py](daily/mutation_seeds.py)
and the `keywords` field on `AttemptResult`. The only thing changing is *who
fetches the keywords*. Today: `pvnp` does it locally. After this migration:
the scraper API does it.

### Concrete value of cross-pollination

- A new arXiv paper claiming "improved bound on rank-2 elliptic curves below
  conductor 50000" → next BSD run uses `MAX_COND=50000` for one focused sweep.
- A reddit thread titled "Anyone tried RG flow on 3D YM at β=2.4?" → next YM
  run includes β=2.4 in its sweep.
- An HN thread on a new SAT solver → `hail_mary` mutation seed prefers
  algorithms named after that solver's family.

That's why the integration matters: **the scraper isn't decoration, it's the
mutation operator**. Without it the daily run is just deterministic noise on
yesterday's seed.

---

## Step-by-step migration

### In the *other* (scraper) project
- [ ] Add a `/v1/feed` endpoint. Wrap your existing fetchers.
- [ ] Define a `topics.yaml` with the six pvnp topic names (or whatever the
      scraper project calls them) mapped to source lists.
- [ ] Bearer-token auth (single token shared with pvnp).
- [ ] Deploy locally first (`uvicorn`, but the user starts it themselves —
      see preferences). Hosted later if/when needed.

### In `pvnp`
- [ ] Create `daily/scraper_client.py` — thin HTTP client, ~40 lines:
      `fetch_topic(topic: str, since: datetime | None = None) -> list[FeedItem]`.
      Reads `SCRAPER_BASE_URL` and `SCRAPER_TOKEN` from env.
- [ ] Cache last successful response per topic to
      `artifacts/cache/scraper/<topic>.json`.
- [ ] In `daily/mutation_seeds.py`, swap the call to `web_scraper.deltas_for(...)`
      for `scraper_client.fetch_topic(...)`.
- [ ] Delete (or comment out for one release) `daily/web_scraper.py`. Update
      `tests/test_web_scraper.py` accordingly.
- [ ] Update [README.md](README.md) "data sources" to say "we call an external
      scraper API; see HANDOFF_SCRAPER_MIGRATION.md".
- [ ] Update the dashboard's data-sources glossary entry to point at the
      scraper service URL.

### Test plan
- [ ] Unit test: `scraper_client.fetch_topic` correctly parses the documented
      response shape, falls back to cache on HTTP error.
- [ ] Integration test (manual): start the scraper, run
      `python -m daily.run --offline=False`, confirm fresh keywords appear
      in `details["keywords"]`.
- [ ] Failure test: stop the scraper, run again, confirm the daily run
      completes using cached keywords + emits a warning to logs (not an error).

---

## Things `pvnp` will *keep* doing locally (do NOT migrate these)
- Cremona elliptic-curve cache (it's a flat file, not a feed)
- Apocalypse signals (TLS / blockchain.info / HN-headline check) — these are
  one-off probes used by `apocalypse_run.py`, very different shape from feeds
- Anything in `verifier/` or `pipelines/`

## Things to think about before shipping
- **Versioning:** prefix the path `/v1/feed` so you can change shape without
  breaking the running pvnp deployment. Bump to `/v2/...` when the schema
  needs to change.
- **Schema source-of-truth:** put the JSON schema in *this* doc + a copy in
  the scraper repo. When you change one, change both. (Or use a JSON Schema
  file committed in both repos, but for two-repo setup that's overkill.)
- **Rate-budget:** the scraper project should expose a `Retry-After` header
  if it wants pvnp to back off. pvnp will respect it.
- **Privacy:** the scraper logs which topics pvnp asks for. If pvnp ever
  becomes multi-user (it won't, but in case), revisit before sharing logs.

## Open questions for the scraper project
1. What's the topic-name vocabulary? Do you want pvnp to send canonical
   names (`navier_stokes`) or arbitrary tags (`navier-stokes`, `NS`, etc.)?
2. Do you want pvnp to register its topics, or are topics defined entirely
   on the scraper side?
3. Hosted (one URL for many users) or per-user (each pvnp deploy points at
   its own scraper deploy)? Affects auth model.
4. Do you want the scraper to surface *enriched* items (e.g. with LLM-generated
   summaries) as a different endpoint, or keep it raw?

---

## What the file diff in `pvnp` will look like (rough size)

| File | Action | Δ lines |
|------|--------|---------|
| `daily/web_scraper.py` | delete | -180 |
| `daily/scraper_client.py` | create | +50 |
| `daily/mutation_seeds.py` | tiny edit | ±5 |
| `tests/test_web_scraper.py` | replace | -30 +20 |
| `README.md` | update sources note | +3 |
| `HANDOFF_SCRAPER_MIGRATION.md` | this file | (already here) |

Net `pvnp` shrinks by ~130 lines. Good.

## Done-when

- `python -m daily.run` passes with `SCRAPER_BASE_URL` set and the scraper
  running.
- `python -m daily.run` still works (with cached data + warning) when the
  scraper is down.
- `pytest -q` is green.
- `daily/web_scraper.py` no longer exists; `daily/scraper_client.py` is its
  ~40-line replacement.
