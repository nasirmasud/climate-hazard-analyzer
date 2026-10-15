# Climate Hazard Trend Analyzer

Pick a place and a year range, and it tells you how heatwaves there have
changed: how many, how long they lasted, how intense they were, and whether the
trend is statistically real or just noise. Built on ERA5 daily temperature data
from Open-Meteo, with a MongoDB cache so the second person to ask the same
question does not pay for the fetch.

**Scope: heatwaves only.** No other hazard is modelled, and none is implied.

**Status: not deployed.** The application runs locally and is fully tested. There
is no live URL yet.

This repository is a monorepo. The two applications were built as separate
repositories and their histories are preserved here under `backend/` and
`frontend/`, grafted in with `git subtree`, so every original commit is still
reachable.

```
climate-hazard-analyzer/
  backend/     FastAPI service, MongoDB cache, heatwave detection
  frontend/    React + TypeScript dashboard (Vite)
```

---

## Contents

1. [Architecture](#architecture)
2. [Data source](#data-source)
3. [Heatwave logic](#heatwave-logic)
4. [Setup](#setup)
5. [API](#api)
6. [Assumptions](#assumptions)
7. [AI tools used](#ai-tools-used)
8. [Future improvements](#future-improvements)
9. [Deployed URLs](#deployed-urls)

---

## Architecture

The database is a cache, not a system of record. The read path and the write
path are separate, and every request starts on the read path.

**Read path (the common case).** `GET /api/analysis` looks up the cache key
`"<lat>_<lon>:<start_year>:<end_year>:<hazard>:<LOGIC_VERSION>"` in the
`analyses` collection. A hit returns the stored response body as JSON, and
Open-Meteo is never called.

**Write path (once per location).** On a miss, the stored daily series for that
location is read. Only if *that* is missing does the service call Open-Meteo
once, for the full supported period, clean it, and store it in `daily_temps`.
Analysis is then computed from the stored series and written to `analyses`.

Two consequences worth stating plainly:

- A new year range for an already-fetched location does **not** re-fetch. The
  full daily series is already in `daily_temps`, so the new range is computed
  from it.
- `LOGIC_VERSION` is part of the cache key. Bump it in `backend/.env` whenever
  detection logic changes, and stale results are never reused.

**If the database is down, the analysis still succeeds.** The cache is never
load-bearing: a database failure degrades to recomputing from Open-Meteo rather
than returning an error. Rate limits and stored entries are lost, but the user
still gets an answer.

Concurrent cold requests for the same uncached location are serialised by a
per-key `asyncio.Lock`, so two simultaneous requests produce exactly one
upstream call.

| Layer | Choice |
|---|---|
| Backend | Python 3.11+ (developed on 3.14.7), FastAPI, Pydantic v2 |
| Database | MongoDB Atlas free tier, `pymongo` via `run_in_threadpool` |
| Frontend | React 19, TypeScript, Vite, Recharts, TanStack Query, Tailwind CSS 4 |
| Data | Open-Meteo Historical Weather API (ERA5), no API key |

Blocking pymongo calls and CPU-heavy pandas work are pushed off the event loop
with `run_in_threadpool`. The blocking style is used consistently; the async
`motor` driver is not mixed in.

Per-module detail lives in [`frontend/docs/walkthrough.md`](frontend/docs/walkthrough.md)
— one plain-language paragraph per module, plus the decisions and bugs worth
remembering when explaining the code out loud. The per-application READMEs
([`backend/README.md`](backend/README.md),
[`frontend/README.md`](frontend/README.md)) go deeper still.

---

## Data source

**Open-Meteo Historical Weather API**, ERA5 reanalysis. One source, named once,
used everywhere.

```
https://archive-api.open-meteo.com/v1/archive
  ?latitude=…&longitude=…&start_date=…&end_date=…
  &daily=temperature_2m_max&timezone=auto
```

It requires no API key, which is why it was chosen.

**Limits that actually matter.** ERA5 is *modelled, not measured*: every value
is an interpolated estimate on a grid of roughly 25 km. There is no station data
and no regional averaging. Urban heat effects are not modelled at all, so a city
centre will read cooler than a person standing there feels it.

Cleaning runs in a fixed order: nulls become `NaN`; values above 60 °C or below
−60 °C are treated as impossible and dropped; the series is reindexed so missing
days are explicit; gaps of up to 2 days are linearly interpolated and flagged;
longer gaps are left missing. Gaps are never filled with zeros or means. A year
with more than 5 % of its days still missing is flagged `low_quality` and
excluded from the trend tests and the chart.

---

## Heatwave logic

A heatwave is **3 or more consecutive days** where daily maximum temperature is
above the **95th percentile for that calendar day**.

Constants, at the top of `backend/app/heatwave.py`:

```python
BASELINE_START = 1990
BASELINE_END = 2020
PERCENTILE = 95
WINDOW_DAYS = 7     # half-window: +/- 7 days around each calendar day
MIN_RUN_DAYS = 3
```

**Baseline.** Thresholds come from the fixed 1990–2020 baseline, never from the
user's selected range. If the range set the threshold, choosing different years
would move the goalposts and distort the trend. The baseline is used even when
the user selects a range outside it.

**Calendar-day threshold.** For each of ~366 calendar days, take all baseline
values within ±7 days of that day across all baseline years and take the 95th
percentile. The window wraps across the year boundary, so late December draws on
early January values. Feb 29 keeps its own slot in the day-of-year scheme.

**Detection.** `above = tmax > threshold`, where a `NaN` day is never above.
Consecutive `True` runs are grouped with `(above != above.shift()).cumsum()`,
and runs of at least 3 days are kept.

**Event year rule.** A run crossing a year boundary is *one* event, counted in
the year it **starts**. A run is never extended across a long `NaN` gap.

**Trends.** Mann-Kendall (`pymannkendall.original_test`) on yearly event counts
gives direction, p-value and Sen's slope; `scipy.stats.linregress` gives the
regression slope and the fitted values that draw the chart's trend line. A trend
is called significant only when **p < 0.05**. The labels are `increasing`,
`decreasing` or `no_clear_trend` — never "Increasing" without significance.

**Small-denominator guard.** When the earlier comparison period averages below
about 1 event per year, the UI reports absolute numbers and suppresses the
percentage headline, because a percentage off a base of 0.4 misleads.

---

## Setup

Both applications run locally. Start the backend first.

### Backend

Python 3.11 or newer (developed on 3.14.7).

```bash
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt      # Windows
.venv\Scripts\activate                              # Windows
source .venv/bin/activate                           # macOS / Linux
```

Create `backend/.env` with these five variables. There is deliberately **no**
`.env.example` here: a tracked template is where a real connection string ends
up pasted by accident, which is exactly what happened once already. The names
are listed instead, with no values.

```
MONGODB_URI        # the only secret — a MongoDB Atlas connection string
MONGODB_DB=climate # safe default
FRONTEND_ORIGIN=http://localhost:5173
LOGIC_VERSION=v1
PORT=8000
```

`.env` is git-ignored and must never be committed. Atlas Network Access must
allow your host; on the free tier that means allowing all IPs.

```bash
uvicorn app.main:app --reload --port 8000
```

- API docs: <http://localhost:8000/docs>
- Health: <http://localhost:8000/api/health>

### Frontend

Node 22 or newer.

```bash
cd frontend
npm ci
cp .env.example .env          # Windows: copy .env.example .env
npm run dev
```

`frontend/.env.example` holds `VITE_API_URL=http://localhost:8000`. It is not a
secret — the value is compiled into the bundle and is readable by anyone who
loads the page.

Open <http://localhost:5173>.

### Tests

```bash
cd backend  && pytest            # 136 tests, against a real Atlas instance
cd frontend && npm test          # 47 tests, vitest
```

The backend suite runs against a **real Atlas instance** and clears its own
collections, because the behaviour worth testing is precisely what a fake
database cannot reproduce: that a second request is served from cache, that a new
year range reuses a stored series, and that two simultaneous cold requests cause
exactly one upstream call.

The response is asserted against *itself* — `total_events` equals the sum of the
yearly rows equals the length of the events list — because every number in the UI
comes from that one response.

`backend/tests/test_contract.py` diffs the Pydantic response models against
`frontend/src/types.ts` field by field, so the two sides cannot drift apart
unnoticed. It skips with an explanatory message if `frontend/` is absent.

---

## API

Base path `/api`. All responses are JSON unless stated.

| Endpoint | Returns |
|---|---|
| `GET /api/health` | `{"status": "ok"}` |
| `GET /api/regions` | The five preset regions |
| `GET /api/place-name` | Reverse-geocoded name for a point, for the map |
| `GET /api/analysis` | Full analysis, from cache or computed |
| `GET /api/analysis/export.csv` | The yearly table as CSV |

`GET /api/analysis` takes either `region=<preset id>` or `lat`/`lon`, or a box
of `min_lat`, `max_lat`, `min_lon`, `max_lon`. Plus `start_year`, `end_year` and
`hazard` (default `heatwave`; anything else is a 422).

Preset regions: Dhaka, Chattogram, New Delhi, Mumbai, Karachi.

Boxes are limited to 2° on each side. Years must start at 1990 or later, end no
later than the last complete calendar year, and span at least 10 years, because
a decade is the minimum for a trend to mean anything.

**Errors** all use one body, `{"error": {"code": "…", "message": "…"}}`:

| Code | When |
|---|---|
| 422 | Invalid input, with field-level messages |
| 429 | Rate limited — 60 req/min per IP on cached reads, 6 req/min for requests that can trigger a fetch. Sends `Retry-After` |
| 502 | Open-Meteo failed after retries |
| 500 | Unexpected. Details are logged server-side; no stack trace is ever returned |

---

## Assumptions

Each was a genuine ambiguity in the brief, resolved with the simplest reasonable
option.

1. **A box is analysed at its centre point, not averaged across its area.**
   ERA5 is a grid and exactly one cell is read. The centre point is displayed in
   both the picker and the results panel, because this is the assumption most
   likely to mislead someone who draws a wide rectangle. A box straddling the
   antimeridian cannot be selected at all.
2. **Interpolation is capped at 2 days, filled as a whole block.** Longer gaps
   are left missing rather than guessed at.
3. **Feb 29 keeps its own slot.** The day-of-year scheme runs to 366 so Feb 29
   has its own number rather than folding into Feb 28.
4. **A preset region is a single point**, not a bounding box.
5. **Coordinates are rounded to 2 decimals** before building the cache key, so
   near-identical requests share one cache entry.
6. **The current year is excluded entirely**, not partially trimmed, because an
   incomplete year would drag every average down.
7. **Place names are a nicety, never required.** A failed or slow reverse-geocode
   leaves the plain "Custom area" label. It can never block an analysis.
8. **The database is never load-bearing.** An outage degrades to recomputing,
   not to failing.

---

## AI tools used

An AI coding assistant (`opencode`) was used throughout the build, working under
my direction.

- **I** set the architecture, made every design decision, wrote the phase plan,
  and chose the trade-offs — including the two-budget rate limiter, the
  always-degrade cache policy, and rejecting a PDF library in favour of a print
  stylesheet.
- **The assistant** wrote and edited the code, ran the test suites, diagnosed
  failures, and drafted the documentation, including this file.

Every commit was reviewed and its tests run before it was pushed. Several defects
the assistant introduced were caught by review rather than by the tests — an
`.env.example` instruction pointing at a deleted file, a one-decimal shift in box
centre rounding, and a partially-fabricated week caused by
`interpolate(limit=2)`.

The parts of this project that required real judgement — what counts as a
heatwave, when a trend is real, what to do when the database is down, and whether
a number should be shown at all — are mine.

---

## Future improvements

- **Deploy it.** Render for the backend, Vercel or Netlify for the frontend.
  That is the single biggest gap: there is no live URL.
- **Multi-point aggregation**, so a box means what a user expects: several grid
  cells averaged, with the spread reported.
- **A proper time-series test.** Mann-Kendall assumes a monotonic trend; on a
  series with strong autocorrelation it overstates significance. A pre-whitening
  or autocorrelation-aware variant would be more honest.
- **Move rate-limit state to Redis** if the backend ever runs more than one
  replica.
- **A trend-strength confidence band**, so "increasing" arrives with a range
  rather than a point slope.
- **More presets**, particularly for the global south, which the current five
  South-Asian cities do not represent.

---

## Deployed URLs

**None. Nothing is deployed yet.** The application runs locally and is fully
tested; Phase 8 (deployment to Render/Vercel) is outstanding.

Once deployed, the live links go here, and the cold-start behaviour — the free
tier takes about a minute to wake up, which is why the UI says so — gets
confirmed on the real URL before this section is filled in.
