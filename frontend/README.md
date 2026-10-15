# Climate Hazard Trend Analyzer

Pick a place and a year range, and it tells you how heatwaves there have changed:
how many, how long they lasted, how intense they were, and whether the trend is
statistically real or just noise. Built on ERA5 daily temperature data from
Open-Meteo, with a MongoDB cache so the second person to ask the same question
does not pay for the fetch.

![Heatwave trend dashboard for Dhaka, Heatwave, 1990 to 2025, showing summary cards, a frequency chart with trend line, the insights panel and the yearly data table](docs/dashboard.png)

The Location panel accepts a preset, a drawn box, or typed coordinates. The map
sets a box and the marker marks the single point actually queried:

![The location picker showing a custom area drawn on an OpenStreetMap base, with the centre point marker inside it](docs/map.png)

**Scope: heatwaves only.** No other hazard is modelled, and none is implied.

**Status: not deployed.** The application runs locally and is fully tested;
Phase 8 (deployment to Render/Vercel) is outstanding, so there is no live URL yet.

---

## Contents

1. [What it does](#what-it-does)
2. [Setup](#setup)
3. [Architecture and data flow](#architecture-and-data-flow)
4. [Data source and its limits](#data-source-and-its-limits)
5. [Heatwave logic](#heatwave-logic)
6. [Data quality handling](#data-quality-handling)
7. [Trend methods and significance](#trend-methods-and-significance)
8. [Assumptions](#assumptions)
9. [Limitations](#limitations)
10. [What I would improve](#what-i-would-improve)
11. [AI tools used](#ai-tools-used)
12. [Tests](#tests)

---

## What it does

Enter a location and a year range of at least ten years, and the app fetches the
daily maximum temperature series for that point, detects heatwaves, aggregates
them by year, tests for a trend, and writes the findings in plain language.

You get:

- **Four summary cards** — total events, total heatwave days, average duration,
  average peak intensity
- **A frequency chart** — events per year with the fitted trend line, low-quality
  years marked as hollow dots
- **A trend card** — direction, both slopes, the Mann-Kendall p-value, and the
  two periods being compared
- **An insights panel** — sentences generated from the numbers, plus the
  limitations of this particular analysis
- **A threshold card** — percentile, window, minimum run and baseline, read from
  the API rather than hard-coded
- **A yearly table** with a **Download CSV** button
- **A region panel** showing the point actually queried, the data-quality
  counts, and whether the response came from cache

Every number comes from one `AnalysisResponse`. There is no second request and no
client-side arithmetic on results, which is what keeps the cards, the chart, the
insights and the table telling the same story.

---

## Setup

Two repositories:

| | |
|---|---|
| **Frontend** (this one) | React 19 + TypeScript + Vite, served statically |
| **Backend** | <https://github.com/nasirmasud/climate-trend-backend> — FastAPI + MongoDB Atlas |

### Backend

Requires Python 3.11+ (developed on 3.14).

```bash
git clone https://github.com/nasirmasud/climate-trend-backend.git
cd climate-trend-backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt      # Windows
.venv\Scripts\activate
```

Create a `.env` file in that directory. There is deliberately no committed
template: a tracked `.env.example` is exactly where a real connection string
ends up pasted by accident, which happened once here already.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `MONGODB_URI` | **yes** | *(empty)* | Atlas connection string, e.g. `mongodb+srv://user:pass@cluster.mongodb.net` |
| `MONGODB_DB` | no | `climate` | Database name |
| `FRONTEND_ORIGIN` | no | `http://localhost:5173` | The single origin allowed to call the API from a browser |
| `LOGIC_VERSION` | no | `v1` | Part of the cache key — bump it when detection logic changes so old cached analyses are never reused |
| `PORT` | no | `8000` | Local server port |

The field names must match the keys **exactly**. pydantic-settings upper-cases
the field name to find its variable, so `mongodb_uri` reads `MONGODB_URI`. A
mismatch does not raise: the unknown key is silently ignored and the app starts
with an empty connection string, then fails later inside pymongo with a
confusing "empty host" error.

Atlas free tier requires Network Access set to allow your host, which means
allowing all IPs.

```bash
uvicorn app.main:app --reload --port 8000
```

### Frontend

Requires Node 20.19+ or 22.12+ (developed on 22.19).

```bash
git clone https://github.com/nasirmasud/climate-trend.git
cd climate-trend
npm install
```

Create a `.env` file:

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `VITE_API_URL` | no | `http://localhost:8000` | Where the API lives |

```bash
npm run dev        # http://localhost:5173
```

`.env` is git-ignored in both repositories and must never be committed.

### Scripts

| Command | Purpose |
|---|---|
| `npm run dev` | Dev server on port 5173, pinned with `strictPort` |
| `npm run build` | Type-check with `tsc`, then build to `dist/` |
| `npm run lint` | ESLint |
| `npm run format` | Prettier |
| `npm test` | Vitest |

---

## Architecture and data flow

Three MongoDB collections, each with a TTL index so nothing needs a cron job:

| Collection | Holds | TTL |
|---|---|---|
| `daily_temps` | one raw daily series per location | 90 days |
| `analyses` | one finished response per location + year range + logic version | 30 days |
| `place_names` | reverse-geocoded labels for map clicks | 180 days |

### Read path (the common case)

```
browser → GET /api/analysis → validate → rate limit (general)
        → cache lookup on analyses
        → hit?  return the stored response with cached: true
        → miss? → cache lookup on daily_temps
                 → hit?  run the pipeline from the stored series
                 → miss? rate limit (upstream) → Open-Meteo → store in daily_temps
                 → detect → aggregate → trend → insights
                 → store in analyses → return
```

The whole 1990-to-present series is fetched **once per location** and stored. A
user's year selection is applied later from that same series, which is why asking
for a different year range for a city you already queried costs a cache read and
no upstream request at all.

### Write path

There is no write path in the usual sense. The cache is populated lazily by
reads, and two concurrent cold requests for the same location are coalesced so
they produce exactly one upstream call.

### Why the cache is never load-bearing

Every cache call is wrapped. A database that is slow, unreachable or mid-failover
degrades to an uncached analysis rather than a 500. The cache is an optimisation,
and making it a hard dependency would tie availability to it.

### Rate limiting

Two budgets, not one — which is the point:

| Budget | Charged when | Limit |
|---|---|---|
| General | every analysis request, before any work | 60/min |
| Upstream | immediately before a call to Open-Meteo | 6/min |

A single limiter decorator cannot express this, because whether a request is
cached is only known *after* the lookup, while the decorator runs before it.
Charging the upstream budget at the point of use is what makes the cache
meaningful — otherwise a returning visitor is charged for the first visitor's
fetch. Both windows slide rather than resetting on a boundary, so an allowance
cannot be spent in full at 11:59:59 and again at 12:00:00.

### The contract

`src/types.ts` is a hand-maintained mirror of the backend's Pydantic models. A
backend test (`tests/test_contract.py`) reads this file and compares field names
against the models, so drift fails the test suite rather than surfacing as a
runtime bug.

---

## Data source and its limits

**Open-Meteo Historical Weather API** (ERA5-based), daily `temperature_2m_max`.
No API key. One request covers ~13,000 daily values for the full range.

Open-Meteo and Nominatim (map tiles and reverse geocoding only) are the only
external services. No other source is queried, named or implied.

### Limits that actually matter

- **ERA5 is a modelled reanalysis**, not a station reading. It interpolates
  observations onto a grid, so a "daily maximum" is a gridded estimate.
- **It is about five days behind real time**, which is why the current year is
  excluded entirely: a partial year drags any average down and biases a trend
  downwards.
- **It is not a global maximum everywhere.** At ~60°N and beyond it degrades,
  because it is derived mainly from satellite data that does not cover the
  polar night.
- **The grid is coarse** (~0.25°). A city is represented by the single grid cell
  containing the queried point, which may be tens of kilometres away.
- **The free tier is rate-limited.** Open-Meteo throttles unauthenticated use,
  which is why the upstream budget is 6/min and why the cache exists at all.

---

## Heatwave logic

A heatwave is **three or more consecutive days where the daily maximum
temperature exceeds the 95th percentile for that calendar day**, measured
against a fixed baseline.

All five constants live at the top of `backend/app/heatwave.py`, named, because
changing any of them changes every number the app produces:

| Constant | Value | Meaning |
|---|---|---|
| `BASELINE_START` / `BASELINE_END` | 1990 / 2020 | Fixed baseline period |
| `PERCENTILE` | 95 | Threshold percentile |
| `WINDOW_DAYS` | 7 | ±7 days of calendar days pooled per day |
| `MIN_RUN_DAYS` | 3 | Minimum consecutive days |
| `LOW_QUALITY_MISSING_FRACTION` | 0.05 | Above this share missing, a year is low quality |

### The calendar-day threshold

A single annual threshold would mark every July day hot and every January day
cold, which has nothing to do with heatwaves. So for each calendar day *d*, the
threshold is the 95th percentile of every baseline value within **±7 days of
*d***, wrapping around the year boundary. Pooling the window smooths the
threshold across the seasonal cycle; without it a threshold computed from one
exact date is noisy enough to invent heatwaves on ordinary warm days.

The window is what makes "the hottest days *for that time of year*" meaningful
rather than "the hottest days, full stop".

### Event year rule

A run crossing a year boundary is **one event, counted in the year it starts**.
The start year is derived from the start date rather than stored separately, so
the two can never disagree.

### What is measured per event

Duration in days, mean intensity (mean daily max above the threshold for that
calendar day), and peak temperature. Yearly aggregates carry events, heatwave
days, and — where events exist — average duration and average intensity.

---

## Data quality handling

Cleaning happens in a fixed order, and the governing rule is that **long gaps are
never filled with zeros or means**. Inventing a temperature fabricates evidence,
and a fabricated hot day can become a fabricated heatwave.

| Step | Rule |
|---|---|
| Fetch | Up to 3 attempts with 0.5 s backoff, 30 s timeout |
| Plausibility | Anything outside ±60 °C is dropped — no real daily maximum anywhere on Earth |
| Short gaps | Filled by interpolation, but **only as a whole block**, up to 2 days |
| Long gaps | Left missing and counted |
| Year flag | More than 5% of days still missing ⇒ that year is `low_quality` |

**The short-gap rule caught a real bug.** pandas `interpolate(limit=2)` bounds
how many consecutive NaNs may be filled, but it fills the first two of *every*
gap — so a seven-day hole got two days invented in it and the rest left missing,
a partially fabricated week. The brief is stricter: a gap is either short enough
to fill entirely, or left completely alone. Replaced with explicit run-length
logic.

### What low-quality means downstream

A low-quality year is **shown and counted in the totals** but **excluded from the
trend test**, because a year that is mostly interpolated should not be allowed to
pull a slope. The UI names those years in text and marks them as hollow dots, so
the meaning does not depend on colour alone.

The exclusion is verified to be exactly equivalent to re-running the trend on
good years only — direction, p-value, slope and every fitted value match.

---

## Trend methods and significance

**Mann-Kendall** (`pymannkendall.original_test`) for significance, and
**`scipy.stats.linregress`** for the slope. Both are reported, and both
Mann-Kendall and regression slopes surface in the UI.

**Significance is decided at p < 0.05.**

The direction label follows **significance, never the raw slope**. A series can
have a positive slope and no significant trend; calling that "Increasing" would
be a lie. Below the threshold the app reports **`No clear trend`** regardless of
which way the line leans, and the label is always text — never colour alone.

That case has explicit tests, because it is the easiest thing in the app to get
subtly wrong:

- a flat series never reports "Increasing"
- a positive slope **without** significance still reports "No clear trend"

### The small-denominator guard

When the earlier comparison period averages under about one event a year, a
percentage change describes nothing — going from 0.4 events/year to 0.6 is not a
50% increase in any meaningful sense. In that case the app reports **absolute
numbers and suppresses the percentage**.

---

## Assumptions

Recorded per §18.3 item 8. Each was a genuine ambiguity resolved with the
simplest reasonable option.

1. **A box is analysed at its centre point, not averaged across its area.**
   ERA5 is a grid; exactly one cell is read. The centre point is displayed in
   both the picker and the results panel, because this is the assumption most
   likely to mislead a user who draws a wide rectangle. A box straddling the
   antimeridian cannot be selected at all, because the backend's
   `min_lon < max_lon` rule has no way to express a wrapped box.
2. **Interpolation limit: 2 days, filled as a whole block.** Longer gaps are left
   missing. See above.
3. **Feb 29 keeps its own slot.** The day-of-year scheme runs to 366 so Feb 29 has
   its own number, rather than being folded into Feb 28. It falls inside the
   circular window of both late February and early March, so it stays well
   constrained by surrounding days either way.
4. **A preset region is a single point**, not a bounding box. `region=<id>`
   resolves to fixed coordinates from the brief.
5. **Place names are a nicety, never required.** A failed or slow reverse-geocode
   leaves the plain "Custom area" label. It can never block an analysis or
   disturb the coordinate inputs.
6. **Feb 29 in a non-leap year** simply does not exist, so the baseline has
   30 samples for that day rather than 31.
7. **The current year is excluded entirely**, not partially trimmed.

---

## Limitations

- **Single point, not an area.** A box reads one grid cell. This is the
  limitation most likely to be misread as "the whole rectangle was analysed".
- **ERA5 is modelled, not measured.** Every value is an interpolated gridded
  estimate.
- **No attribution of cause.** The app reports that heatwaves changed, never
  *why*. Attribution needs far more than a temperature series.
- **Significance is weak over short ranges.** Ten years is the minimum enforced,
  and a decade is thin for detecting a trend in a noisy climate series.
- **No station data, no regional averaging**, and no comparison between regions.
- **Rate-limit state is in-process memory**, so it resets on restart and is not
  shared across replicas. Honest for a single-process deployment; not a
  distributed guarantee.
- **Reverse geocoding depends on a third-party service** and degrades to no name.
- **Not deployed.** There is no live URL, so §19.1 of the brief is unmet.

---

## What I would improve

- **Deploy it** — Render for the backend, Vercel for the frontend. That closes
  §19.1 and makes the cold-start path testable in the wild.
- **Multi-point aggregation**, so a box means what a user expects: several grid
  cells averaged, with the spread reported.
- **Move rate-limit state to Redis** if the backend ever runs more than one
  replica.
- **A proper time-series test.** Mann-Kendall assumes a monotonic trend; on a
  series with a strong autocorrelation it overstates significance. A
  pre-whitening or autocorrelation-aware variant would be more honest.
- **More presets**, particularly for the global south, which §5.1's five
  South-Asian cities do not represent.
- **A trend-strength confidence band**, so "increasing" comes with a range rather
  than a point slope.

---

## AI tools used

An AI coding assistant (`opencode`) was used throughout the build, working
under my direction.

- **I** set the architecture, made every design decision, wrote the phase plan,
  and chose the trade-offs — including the two-budget rate limiter, the
  always-degrade cache policy, and rejecting a PDF library in favour of a print
  stylesheet.
- **The assistant** wrote and edited the code, ran the test suites, diagnosed
  failures, and drafted the documentation, including this file.

Every commit was reviewed and its tests run by me before it was pushed. Several
defects it introduced were caught by review rather than by the tests — the
`.env.example` instruction pointing at a deleted file, the one-decimal shift in
box centre rounding, and a partially-fabricated week caused by
`interpolate(limit=2)`.

The parts of this project that required real judgement — what counts as a
heatwave, when a trend is real, what to do when the database is down, and whether
a number should be shown at all — are mine.

---

## Tests

| Suite | Count | Command |
|---|---|---|
| Backend | 136 | `pytest` (from `climate-trend-backend`) |
| Frontend | 47 | `npm test` |

The backend suite runs against a **real Atlas instance** and clears its own
collections, because the behaviour worth testing is precisely what a fake
database cannot reproduce: that a second request is served from cache, that a new
year range reuses a stored series, and that two simultaneous cold requests cause
one upstream call.

The response is asserted against *itself* — `total_events` equals the sum of the
yearly rows equals the length of the events list — because every number in the UI
comes from that one response.

---

## Further reading

[`docs/walkthrough.md`](docs/walkthrough.md) — a plain-language paragraph per
module, plus the bugs and decisions worth remembering when explaining the code.