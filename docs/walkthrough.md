# Build walkthrough

One plain-language paragraph per module, per §20.2 of the brief, plus the
decisions and bugs worth remembering when explaining the code live. Built up
phase by phase rather than written at the end, so it records decisions as they
were made rather than as they look in hindsight.

**This file lives in the frontend repository** so that it can be committed and
read alongside the README. Paths below are written from the workspace root, so
`backend/app/heatwave.py` is one file in the
[backend repository](https://github.com/nasirmasud/climate-trend-backend) and
`frontend/src/App.tsx` is in this one.

The authoritative specification is `case_study.md`; `plan.md` holds the phase
breakdown and the open decisions. Neither is tracked — they are working
documents, not deliverables.

## D4 — trend maths moved into Phase 2

`AnalysisResponse` declares `trend`, `period_comparison`, `insights` and
`limitations` as **required** fields. The original plan had trend maths in
Phase 4, after the Phase 2 endpoint existed, which left three options:

1. return placeholder values — rejected, the UI would render them as real
2. make the fields nullable — rejected, that deviates from the §9.3 shape
3. build the trend maths before the endpoint that needs it — chosen

So Phase 2 gained a 2E block (`trends.py`, `insights.py`, their tests) and
Phase 4 shrank to the two decisions that need real numbers on screen first:
excluding `low_quality` years from the tests, and the §8.6 small-denominator
guard.

**Consequence carried deliberately:** Phase 2 trend results include
`low_quality` years. Phase 4 recomputes without them. Phase 2 numbers are not
final and should not be presented as such.

---

## Backend modules at a glance

One paragraph per file, per §20.2. The deeper sections below cover the bugs and
the reasoning; this is the map.

### `backend/app/main.py`

Builds the FastAPI app: mounts the router under `/api`, sets CORS, and installs
the four exception handlers that make every failure look the same. Two of those
handlers exist for reasons that are easy to miss. A `ValueError` raised inside a
Pydantic `model_validator` does **not** arrive as a validation error — it bypasses
FastAPI's handler and lands in the generic 500, which meant every rule in
`AnalysisRequest` reported "something went wrong on our side" when the user had
simply typed an invalid year. And a deployed instance allows exactly one origin,
while a local one allows any port from 5173 to 5191, because Vite silently takes
the next free port and a hard-coded 5173 makes the page load while every API call
is rejected — a failure that looks like a dead backend but is a port number.

### `backend/app/config.py`

Five settings read from the environment, every one with a safe default except the
MongoDB URI. The field names must match the `.env` keys exactly:
pydantic-settings upper-cases the field name to find its variable, so
`mongodb_uri` reads `MONGODB_URI`. A mismatch does not raise — the unknown key is
silently ignored and the app starts with an empty connection string, failing later
inside pymongo with an "empty host" error that points at the URI rather than the
field name. That is the bug this file exists to prevent.

### `backend/app/regions.py`

Five preset regions as a frozen dataclass list, with a `BY_ID` lookup. The brief
fixed both the names and the coordinates, so this file holds data and nothing
else. `get_region` returns `None` for an unknown id rather than raising, which
lets the route turn it into a clean 422 naming the regions that do exist.

### `backend/app/schemas.py`

The contract. Every request and response body is a Pydantic model here, and
nothing outside this file may add a field — §9.3 is frozen and
`backend/tests/test_contract.py` fails the suite if the two repositories drift
apart. It is the only module both repositories must agree on.

### `backend/app/routes.py`

The endpoints, all under the `/api` prefix: `/api/health`, `/api/regions`,
`/api/place-name`, `/api/analysis` and `/api/analysis/export.csv`. Almost all of
its logic is defensive on purpose.
Every cache call is wrapped so a slow, unreachable or failing database degrades to
an uncached analysis instead of a 500 — the cache is an optimisation, and making
it a hard dependency ties availability to it. A box is analysed at its **centre
point**, and both coordinates are rounded to two decimals before reaching the
cache key, because ERA5 is a grid: the rectangle is an area but exactly one point
in it is read. The CSV export calls the same `_analyse` as the JSON endpoint, so
the downloaded file and the on-screen table can never disagree.

### `backend/app/analysis.py`

Owns the order of operations — fetch, clean, detect, aggregate, trend, narrate —
because each step depends on the one before. It exists so `routes.py` does not
become a wall of orchestration, and so the CSV export can reuse the whole pipeline
by calling one function.

### `backend/app/ingest.py`

Fetches the daily maximum temperature series from Open-Meteo and cleans it in a
fixed order. The rule that matters: long gaps are never filled with zeros or
means, because inventing a temperature fabricates evidence. Gaps of two days or
fewer are interpolated as a unit; longer ones stay missing and are counted. A
60 °C plausibility ceiling drops obvious sensor garbage. Deeper in this file is
the pandas bug that made `limit=2` fabricate part of a seven-day hole.

### `backend/app/heatwave.py`

The core of the evaluation. A heatwave is three or more consecutive days above the
95th percentile **for that calendar day**, measured against a fixed 1990–2020
baseline. All five constants sit at the top, named, because changing any of them
changes every number in the app. Feb 29 keeps its own day-of-year slot, and a run
crossing a year boundary is one event counted in the year it starts.

### `backend/app/trends.py`

Mann-Kendall for significance and `scipy.stats.linregress` for the slope. The
direction label follows **significance, never the raw slope**: a series can have a
positive slope and no significant trend, and calling that "Increasing" would be a
lie. Below 0.05 it reports `no_clear_trend` regardless of which way the line leans.

### `backend/app/insights.py`

Turns numbers into the sentences the UI shows, so the wording cannot drift from
what was computed. It also enforces the small-denominator guard: when the earlier
period averages under about one event a year, a percentage change describes
nothing, so it reports absolute numbers instead.

### `backend/app/db.py`

The MongoDB layer: three collections — `daily_temps` holds one raw series per
location, `analyses` holds a finished response per location and year range, and
`place_names` holds reverse-geocoded labels added in Phase 7. Each carries a TTL
index so old documents expire without a cron job. Everything blocking runs in
`run_in_threadpool` so the event loop is never held, `ensure_indexes` is called
once at boot so a slow first request is avoided, and the helpers swallow
`PyMongoError` internally so an unreachable database degrades to no cache at all.

### `backend/app/ratelimit.py`

Two budgets rather than one, which is the whole point of the file. The general
60/min is charged on every analysis request; the upstream 6/min is charged only
immediately before a call to Open-Meteo. Charging at the point of use is what
makes the cache meaningful — otherwise a returning visitor pays for the first
visitor's fetch. Both windows slide rather than reset on a boundary, so an
allowance cannot be spent in full at 11:59:59 and again at 12:00:00.

### `backend/app/places.py`

Reverse-geocodes a clicked point into a short name via OpenStreetMap Nominatim, so
a box selection reads as "Dhaka, Bangladesh" rather than "Custom area". Three rules
shape it: it never raises (every failure returns `None`, and the guarantee is
structural — one outer guard rather than a promise at each call site), it is never
slow (a three second timeout that nothing waits on), and it never leaks
coordinates (the label comes only from Nominatim's address fields, with no branch
that prints "23.81, 90.41" as if it were a name).

---

## 2A — Ingest (`backend/app/ingest.py`)

### Bugs found while building

**1. pandas `interpolate(limit=2)` does the opposite of what §6.6 asks.**
`limit` bounds how many consecutive NaNs may be filled, but it fills the first
N of *every* gap. A seven-day hole with `limit=2` had two days invented in it
and the rest left missing — a partially fabricated week. The brief is stricter:
a gap is either short enough to fill entirely, or left completely alone.

Replaced with explicit run-length logic: measure the length of each NaN block,
then keep or drop the whole block as a unit.

```python
missing = frame["tmax"].isna()
block_id = (missing != missing.shift(fill_value=False)).cumsum()
block_length = missing.groupby(block_id).transform("sum")
short_gap = missing & (block_length <= MAX_INTERPOLATED_GAP_DAYS)
```

**2. Two of my own test expectations were wrong**, worth recording because they
are easy traps:

- A six-day run (`2020-05-02` to `2020-05-07`) was asserted as five days.
- `frame.drop(index=...)` *removes rows*, it does not make them missing. The
  data-quality test needed the gap injected into the payload upstream, or the
  denominator changed and the percentage stopped meaning anything.

### Why retries skip 4xx

`5xx` and network errors are worth retrying: the request was right and the
service was not. A `4xx` means the parameters are wrong, so repeating it
cannot help and only delays the error. Rate limiting (`429`) is in this group
too — the answer will not improve by asking again immediately.

### Why long gaps are never filled

Inventing a temperature lets a fabricated day influence whether a heatwave was
detected. A gap of unknown length could hide a three-day heatwave entirely, or
split one real event into two. Leaving it `NaN` admits the day is unknown; the
day then counts as "not above threshold", which can under-count but can never
manufacture an event.

---

## 2B — Heatwave logic (`backend/app/heatwave.py`)

### Bugs found while building

**3. `groupby(run_id)` returns the `False` regions too.** The brief's
`run_id = (above != above.shift()).cumsum()` produces a group id for *every*
run of days, including the stretches that were below threshold. Grouping the
whole frame by it turned a long quiet or missing spell into one enormous fake
event (this surfaced as an all-NaN slice warning from `nanmax`).

Fix: filter to above-threshold days before grouping, so only candidate runs
are considered.

```python
above_days = series.loc[above_series]
for _, group in above_days.groupby(run_id.loc[above_series]):
```

### `avg_max_temp_during_events_c` — a distinction worth defending

§7.7.5 says "average maximum temperature during heatwave days". That is the
mean of the **daily maxima across every event day**, not the mean of the
per-event **peaks**. These differ: a six-day event contributes six values in
the first reading and one in the second.

My first implementation took the mean of the peaks. It is the easier number to
compute and it is the wrong one. `mean_max_temp_on_event_days()` is now shared
by the yearly rows and the summary card, so the two cannot drift apart.

### Feb 29

Mapped to day-of-year 60 in a leap year via `dayofyear`, which gives it its own
slot rather than folding into Feb 28. Its circular ±7-day window reaches both
late February and early March, so it stays well constrained either way.

In a leap year the target day contributes 32 values to its own percentile
instead of 31. A minor asymmetry the brief explicitly accepts.

### Why thresholds come from the full series, not the slice

`compute_thresholds` runs on the whole 1990-onward frame *before*
`slice_range` narrows it to the user's years. If thresholds followed the
user's selection, choosing a different start year would move the threshold and
silently change the trend being measured — the user would be comparing two
different heatwave definitions.

### The 366-day loop is left as-is

`compute_thresholds` loops over all 366 calendar days, using `np.isin` per day.
That is 366 scans of the baseline. It runs in about a second and Phase 3's cache
makes it a once-per-location cost, so it stays readable rather than optimised
without a measurement to justify it.

---

## 2E — Trends and insights

### Why Mann-Kendall decides the label and regression only draws the line

Mann-Kendall is rank-based: it compares every year against every other and asks
whether they agree on the order. Event counts always have spikes, and a
single extreme year moves least squares a long way without changing anything
real. Sen's slope (the median of all pairwise slopes) is robust for the same
reason.

Linear regression is kept for one job: drawing the dashed trend line. It is
pulled around by outliers, so it does not decide the wording.

**The direction label requires p < 0.05.** A positive slope with p = 0.9 is
still `no_clear_trend`. This is the single easiest way to overstate this data,
and §8.3 forbids it directly.

### scipy has no `fittedvalues`

`linregress` returns only `slope`, `intercept`, `rvalue`, `pvalue` — not the
fitted values the brief asks for. Rebuilt as `intercept + slope * year`. A test
asserts one fitted value per input year so it cannot silently drift.

### NaN cannot reach JSON

`linregress` returns NaN for p-value and correlation on a constant series.
JSON has no NaN literal, so a NaN in the response body would produce something
no browser can parse. `_finite()` substitutes zero for the "nothing measurable"
case.

### The period sentence stays even when the trend is insignificant

A descriptive contrast and an inferential claim are different things. The
period comparison answers "what are the two rates?"; the trend test answers "is
there a consistent change?". Reporting both is more informative, even though a
reader may find a "4% more events" line next to a "no clear trend" headline
odd. Decision: keep it.

### §8.6 guard is live, not deferred

`insights.py` suppresses the percentage when the earlier period is under 1
event/year. Without it, 0.1 → 0.2 events/year renders as **+100%**, which
describes nothing. Absolute numbers are shown instead.

---

## 2C — The endpoint

### Why `backend/app/analysis.py` exists

`routes.py` was going to become an unreadable wall of orchestration. The
pipeline also has an ordering that needs to read as deliberate: thresholds from
the full series, *then* slice to the user's years. That belongs in a module
whose job is to be read top to bottom.

### The plan's cold-start estimate was wrong

`plan.md` predicted 15–40 s for a first analysis. The real Dhaka request takes
**~1.5 s**: Open-Meteo returns all 13,149 days in a single request and the
threshold loop is fast.

Consequence: the `LoadingState` copy was softened. Warning users about "up to a
minute" for a 1.5 s request is worse than saying nothing. The free-tier cold
start is still real once deployed — it is the *data fetch* that is not slow.

---

## Verified numbers — Dhaka, 1990–2024, live ERA5 data

```
HTTP 200 in 1.55s

summary.total_events                  88
summary.avg_duration_days            4.25
summary.avg_intensity_c             0.83
summary.avg_max_temp_during_events_c 33.94
summary.events_per_year              2.51

trend.direction                      increasing
trend.significant                     True
trend.mann_kendall_p                 0.007326
trend.sen_slope_per_year             0.100
trend.regression_slope_per_year      0.148
trend.fitted                         35 values

period_comparison.early   1990-2000   1.64 events/yr   4.00 days
period_comparison.late    2014-2024   4.73 events/yr   4.07 days

data_quality   13149 days, 0 missing, 0 interpolated, 0 low-quality years
```

Reconciliation, all passing:

- `total_events == sum(yearly.events) == len(events) == 88`
- `sum(yearly.heatwave_days) == sum(event.duration_days) == 374`
- 35 contiguous yearly rows, `len(fitted) == len(yearly)`
- shortest event is exactly 3 days
- every `mean_intensity_c > 0`

### What the data shows

Frequency rose sharply: 2009 had 5 events, 2023 had 9, 2024 had 13. Average
duration barely moved (4.00 → 4.07 days), so the change is in **how often**
heatwaves happen, not how long they last. The insight text reports that rather
than implying heatwaves got longer.

### Two deliberate reporting choices

- **`avg_duration_days` is reported even though it barely changed.** 4.00 → 4.07
  is within noise of flat. Reporting it is honest; hiding it would be a
  judgement call the data does not support.
- **Threshold card is built entirely from the API `threshold` object.** No
  component hard-codes "95th percentile / ±7 days / 3+ days", so changing
  `heatwave.py` changes the UI with no frontend edit.

---

## 2D — Frontend

### Files

| File | Responsibility |
|---|---|
| `frontend/src/validation.ts` | Client-side mirrors of the backend's §11.1 rules |
| `frontend/src/hooks/useAnalysis.ts` | The one hook that fetches an analysis |
| `frontend/src/components/ControlBar.tsx` | Region, hazard and year pickers |
| `frontend/src/components/SummaryCards.tsx` | Four cards, skeleton until the response lands |
| `frontend/src/components/FrequencyChart.tsx` | Recharts chart plus fitted trend line |
| `frontend/src/components/TrendCard.tsx` | Three-state trend, both slopes, period comparison |
| `frontend/src/components/ThresholdCard.tsx` | Method description, built from `threshold` |
| `frontend/src/components/InsightsPanel.tsx` | Insight sentences and limitations |
| `frontend/src/components/RegionPanel.tsx` | Location, quality strip, cache flag |
| `frontend/src/components/DataTable.tsx` | Yearly rows, sticky header |
| `frontend/src/components/States.tsx` | Loading, cold-start hint, error |

### The one design decision that matters: no query on keystroke

`useAnalysis` takes `params: AnalysisParams | null`. `App` holds that in state
and only sets it from `ControlBar`'s submit handler. React Query's `enabled`
gates on it being non-null.

The consequence: typing in the year field changes nothing until Analyze is
pressed. Without this, every keystroke would fire a request that fetches 13,000
days of data and then gets thrown away. The form and the query are deliberately
separate pieces of state — `startYear` in the component, `params` in the parent.

### Skeleton vs em dash — two different meanings

The plan's rule is "no hard-coded numbers, skeleton or `—` only", but those two
are not interchangeable and the code treats them differently:

- **Skeleton** = no response yet. We don't know the value.
- **Em dash** = the API returned `null`. We know the value is undefined, and
  specifically we know *why*: a year with no heatwave events has no average
  duration, because a mean over no events is unknown rather than zero.

Rendering a null as `0` would say "heatwaves lasted zero days", which is a claim
about the world rather than about our knowledge of it.

### The chart's two axis decisions

**Dynamic y-domain.** A hard-coded 0–12 range would flatten a quiet city's
variation into a line along the bottom and exaggerate a busy one's. The
domain is derived from the data with proportional headroom, and the floor is
always 0 because event counts cannot be negative.

**Numeric x-axis, not categorical.** Years are evenly spaced intervals. On a
category axis Recharts treats 1990 and 1991 as unrelated labels, which puts
them the same distance apart as 1990 and 2020 and bows the fitted line
incorrectly. `type="number"` with `domain` set to the real year range is what
makes the trend line straight, which is the point of drawing it.

### Low-quality years are drawn, not hidden

Hollow dots rather than filled, so a flagged year is still plotted and still
visible. Dropping the point entirely would hide a data problem; filling it
normally would hide the flag. The table carries the same information as the word
"Low quality", because colour alone means nothing to a screen reader.

### Accessibility decisions

- The trend direction is **always words** — "Increasing", "Decreasing",
  "No clear trend" — with colour only reinforcing. An arrow or a red tint tells a
  sighted user something and tells a screen reader user nothing.
- Every control has a visible `<label>`, not a placeholder.
- The inline validation error uses `role="alert"` so it is announced when it
  appears rather than left to be found.
- Loading uses `role="status"` with `aria-live="polite"`.

### Errors are shown verbatim from the API

`ApiError` carries the backend's own `code` and `message` through, and the
components render that message unchanged. The frontend does not invent its own
wording for a failure, because the backend already wrote those messages for a
person rather than a developer.

The one place the UI branches is `status === 502`, which means the weather
service failed rather than the request being wrong — so the copy changes and the
retry button is replaced by the cold-start hint, because retrying immediately
would not help.

### Client-side validation is a convenience, never the authority

`validation.ts` duplicates the backend's rules so an impossible range produces
an inline message instead of a round trip and a 422. The constants are
deliberately duplicated rather than fetched: they are part of the request
contract, and a validation rule that arrives from the network cannot be trusted
to gate a request. The backend still validates every one of them.

### Toolchain fixes needed while building

Recharts 3 types `Tooltip`'s `formatter` and `labelFormatter` against
`ValueType` / `ReactNode`, which include `undefined` and `ReactNode`
respectively — narrower parameter annotations do not typecheck. Left the
parameters unannotated and narrowed inside the function body.

`SummaryCards` hit TS18048 on `summary.events_per_year` inside a ternary.
Reworked so a single `pending` flag drives all four cards, which also removes
the possibility of them disagreeing about whether a response has arrived.

### Bundle size

625 kB JS (185 kB gzipped), up from 255 kB in Phase 1. That jump is Recharts
entirely. Acceptable at this scale. If it ever matters, a dynamic import on the
chart splits it out without touching any code above it.

### Not yet verified

The chart's custom `dot` render function, the numeric x-axis behaviour and the
1×3 desktop grid have been compiled but **not seen rendered**. A visual bug in
those would pass every automated check in place.

*(Resolved: browser pass confirmed clean. Vite was not broken either — it binds
to `::1` only, so `127.0.0.1` refuses the connection and a naive health check
looks like a failure.)*

---

## 3A/3B — MongoDB cache (`backend/app/db.py`)

### Two bugs that only a live cache could expose

**1. `store_analysis` saved an incomplete body.** The route passed `body` —
the analysis part from `analysis.run_analysis` — to `store_analysis`. But a
cache hit rebuilds the response from that document *alone*, and `body` has no
`region`, `params` or `threshold`. Every cached response would have failed
validation with "field required".

This was invisible until Phase 3 because the cache path was dead code in
Phase 2. It is the clearest argument for actually exercising a code path rather
than reasoning that it works.

The fix stores `response.model_dump(mode="json")` — the complete body — with
`cached` popped off. The flag is never persisted: a stored `cached: false`
would be read back as a lie.

**2. The lock was released before the computation.** The lock was held across
the fetch but released before `run_analysis`. Two concurrent cold requests
therefore caused one fetch but **two** computations, and both responses came
back with `cached: false`. The stampede test caught it.

The lock now spans the entire body, including the CPU-bound pandas work. This
is a real trade-off: a request for the same location and range now queues
behind another rather than duplicating the work. For this endpoint that is
correct — duplicate work serves nobody, and the queue is short.

### The lock bookkeeping bug that would have been invisible

`release_lock_if_idle` checked `not lock.locked()` before dropping the entry.
That is not sufficient. An `asyncio.Lock` can be **unlocked while another
coroutine is still queued behind it**, so the entry could be removed with a
waiter still pending, letting a third caller create a fresh lock and run
concurrently with the queued one.

`asyncio.Lock` exposes no waiter count, so each entry now carries one of its
own, and the entry is only dropped when the last holder *and* the last waiter
are gone.

### Why the cache never fails a request

Every pymongo call goes through a wrapper that logs and returns `None` on
failure. `db.py` already swallows `PyMongoError` inside its own helpers, but a
driver-level failure (unreachable host, pool timeout, DNS) can surface before
that. Either way the answer is the same: behave as if it were a miss.

A cache outage must degrade **performance**, not availability. The user still
gets their analysis; it is just recomputed.

### Storage shape: flat arrays, not nested documents

The daily series is stored as three parallel arrays (`dates`, `tmax`,
`interpolated`) rather than as 13,000 nested sub-documents. BSON repeats the
field name on every value, so the flat form is substantially smaller, well
under the 16 MB document limit, and much cheaper to decode straight back into
a DataFrame.

`NaN` has no BSON representation, so missing days are stored as `null`. The
parallel `interpolated` array is what distinguishes "filled in" from "unknown"
on the way back.

### Cache keys and the two-collection split

- `daily_temps` — `23.81_90.41`, coordinates rounded to 2 dp **before** the
  key is built. Reusable across any year range.
- `analyses` — `23.81_90.41:1990:2024:heatwave:v1`. One finished answer.

The split is what makes a new year range free: `analyses` always misses, but
`daily_temps` hits and Open-Meteo is never called.

`LOGIC_VERSION` is in the analysis key on purpose. When the detection logic
changes, every stored analysis becomes wrong; bumping the version means the old
ones are never read rather than being served as if still correct.

### The two TTLs (D5, D6)

| Collection | TTL | Why different |
|---|---|---|
| `analyses` | 30 days | A finished answer to one question. A month later, recompute it (~1.5 s) rather than serve a frozen result |
| `daily_temps` | 90 days | The evidence behind *many* answers. Correcting it on a 30-day cycle would penalise every returning user for a revision most will never notice, but leaving it unbounded means a corrected upstream dataset never arrives |

Both are TTL indexes in Atlas, confirmed present:
`created_at_ttl = 2592000`, `fetched_at_ttl = 7776000`.

### Measured cache behaviour

| Check | Result |
|---|---|
| Second identical request | `cached: true`, 132 ms vs 3221 ms cold |
| Different range, same city | `cached: false`, 3221 ms, **zero** upstream calls |
| Karachi, uncached | `cached: false`, 2950 ms — genuine cold miss |
| 6 concurrent cold requests | **1** upstream call, 1 computed, 5 served cached, all bodies byte-identical apart from the flag |

The six-way stampede test needed the upstream mock to sleep 50 ms to widen the
window a real stampede would hit. Without that pause the requests serialise by
accident and the test would pass even with a broken lock.

### Surfacing `cached` in the UI

`CacheStatus.tsx` is a strip above the summary cards with three states:
"Computed just now", "Served from cache", and "Refreshing these results…".

The flag is read verbatim off the response and **never inferred from timing**. A
cache hit and a cold hit can take the same wall-clock time, so guessing from
elapsed milliseconds would eventually display a lie.

---

## Config bug worth remembering

`Settings.mongo_uri` made pydantic-settings look for `MONGO_URI`, while `.env`
and `.env.example` both said `MONGODB_URI`. Unknown keys are **silently
ignored**, so the app started with an empty connection string and only failed
later at pymongo with `Empty host` — a message that points at the connection
string rather than at the field name.

Fixed by renaming the fields to `mongodb_uri` / `mongodb_db`, plus a startup
warning when the URI is absent. `pydantic-settings` upper-cases the field name
to find the variable, so the two must match exactly.

**Security note:** the Atlas password was sitting in `.env.example`, which is a
tracked file meant for placeholders. It has been replaced, but the credential
must be treated as exposed and rotated.

### One stray document, removed: `23.81_90.41:1990:2025:heatwave:v1` was a test artifact
from a PowerShell command where a single-quoted string did not interpolate
`$base`, producing a URL with no host. Deleted. Recorded here because a
PowerShell single-quoted string does not expand `$variables`.

---

## Phase 4 — Trend quality guards

Small in code, but the reasoning is the most defensible part of the trend work.

### Why exclude rather than merely mark

Section 8.4 allows either. Exclusion is the stricter reading and the more
honest one.

A year with more than 5 percent of its days missing has an event count built on
an unknown amount of data. **A count that is too low because the data is missing
is not evidence that fewer heatwaves happened.** Including such a year drags
both tests toward a spurious "no clear trend" and can invent a slope in either
direction.

Measured on a flat city where one early year under-reports to zero:

| | direction | p | regression slope |
|---|---|---|---|
| bad year trusted | `no_clear_trend` | 0.1653 | **+0.0639** — a fake incline |
| excluded | `no_clear_trend` | 1.0000 | **+0.0000** — correctly flat |

The regression slope is what draws the line on the chart. Trusted, it shows a
rise that does not exist. This is the concrete argument for Phase 4 existing at
all.

On a steeply rising series the effect is smaller in relative terms but still
real: trusting three bad early years gave a +4% slope error against truth,
excluding them gave 0%.

### Scoped to the tests, not the totals

The exclusion is confined to the two statistical tests, the fitted line and the
period comparison. `yearly` rows are untouched, so the table still shows every
year the user asked about — marked — and `summary.total_events` still counts
every detected event.

The alternative was to drop flagged years from `yearly` entirely. That would
make reconciliation trivially easy, but the user chose a range and it should not
silently shrink. The table showing a flagged row next to an unflagged one is
more informative than the row vanishing.

### The join that would have failed silently

`schemas.py` fixes `fitted: list[float]` and section 9.3 forbids adding fields,
so `fitted` carries **no year key**. It has to align positionally with the
included years.

The chart previously did `trend: data.trend.fitted[index]` against
`data.yearly[index]`. Before Phase 4 those arrays were the same length and that
was correct. After it they are not: 35 yearly rows, 34 fitted values. The
positional index would have drawn the line against the wrong years, and nothing
in the response would have looked wrong.

```tsx
let fittedIndex = 0
return data.yearly.map((row) => {
  const trend = row.low_quality ? null : (data.trend.fitted[fittedIndex++] ?? null)
  // ...
})
```

The invariant is now documented on `trends.analyse_trend` and asserted in two
places: `len(fitted) == len(included)` in `test_trends.py`, and the same check
end to end in `test_api.py`.

### A period label that changes shape

With 1999–2003 excluded, the early period reports `1990-1997`, not `1990-1999`.

My first test expected the latter and failed. The implementation was right: the
label must name the years actually measured, not the slots they occupied.
Reporting `1990-1999` would describe a span that was never analysed. The
exclusion is recorded in the low-quality insight sentence so the reader can see
why the span looks unusual.

### The insight wording had to change

The sentence used to say these years are "included in these results but marked
low quality". That was true of the event counts and **false** of everything the
trend is built from. A reader deciding whether to trust the trend would have
been misled by the softer claim. It now reads:

> N year(s) had more than 5% of days missing (…). They are excluded from the
> trend test and the period comparison, but their events are still counted in
> the totals above.

### Guard when exclusion empties the series

Excluding can legitimately drop a series below three usable years. The result is
`no_clear_trend` with an empty `fitted`, not a crash and not a line through zero
points. Logged, because a real region reaching that state is worth knowing about.

### A cache trap that nearly misled me

Testing this needed a payload with a genuine data gap. I first truncated it at
2020, and got **4562 missing days and 15 phantom low-quality years** from the
endpoint — while the same pipeline run in isolation correctly reported 240
missing days and one flagged year.

The cause was my own fixture. `fetch_period()` requests 1990 → the last complete
year, and `clean_series` reindexes across whatever comes back. A payload ending
in 2020 is not missing data; it is a short response, and the reindex fabricates
the gap.

The second issue is the one worth remembering. Even after fixing the fixture,
the numbers did not change — because `daily_temps` is keyed by coordinates
alone and had already cached the truncated series. Every later request replayed
it. **The cache was working exactly as designed and still produced a
misleading result**, because the cache has no way to know the stored data is
wrong.

Both collections were cleared and the run repeated to confirm the real
behaviour: 240 missing of 13149, one flagged year, 35 rows, 34 fitted.

---

## Phase 4 verification

Live, with 2015 losing 240 of 365 days:

```
low_quality_years : [2015]
missing_days      : 240 of 13149
yearly 35 rows | included 34 | len(fitted) 34 -> match True
total_events == sum(yearly.events) == len(events) : True
2015: flagged=True, events=0 — still counted in the totals
```

Tests: 74 → 82. New coverage includes exclusion matching a good-years-only run
exactly (direction, p-value, slope and fitted values all equal), `fitted` length
against included years, the all-years-flagged fallback, the sub-three-usable
fallback, period comparison exclusion, and an end-to-end exclusion test.

---

## Phase 5 — Region handling and data quality

This phase had no section, which is why `plan.md` carried an open box against it.
It is the phase where the app stopped accepting a place name and started
accepting a **box**, and every awkward question in the brief about boxes is
answered somewhere in these files.

### The presets were wrong in the original plan

§5.1 names five regions exactly. The earlier draft listed London and Phoenix,
which are not among them, and omitted Chattogram. `backend/app/regions.py` now
holds Dhaka, Chattogram, New Delhi, Mumbai and Karachi with the coordinates the
brief specifies and nothing else.

### A box is reduced to its centre point, and the UI says so

ERA5 is a grid. A box is **not** averaged across its area — the backend queries
the single cell at the box's centre. That is the assumption §18.3 item 8 asks to
be recorded, and it is also the one most likely to mislead a user, because
drawing a wide rectangle reasonably suggests the whole area was analysed.

So the centre point is displayed rather than merely documented: `ControlBar`
shows it while the box is being drawn, and `RegionPanel` prints it again as a row
in the results. `boxCenter` in `frontend/src/boxGeometry.ts` computes it once and
both surfaces read from there, so they cannot drift apart.

### Rounding happens before the cache key, not after

Coordinates are rounded to two decimals before the request goes out and before the
cache key is built. Rounding afterwards would mean two users clicking 23.811 and
23.81 compute different cache keys and each pays for their own Open-Meteo fetch.
The rule is stated in one place, `round2` in `frontend/src/validation.ts`, because
three call sites need it identical.

### Box bounds are rounded to three decimals, not two

This one is a genuine trap. The latitude half-span is 0.375, so bounds land on a
third decimal. Rounding them to two moved each bound by up to 0.005 in the
*opposite* direction — rounding a low bound up while rounding a high bound down —
which silently shifted the centre, so a click at 23.81 produced a box centred on
23.815. Both bounds of a pair must round to the same precision to stay symmetric
about their midpoint.

### Client-side validation mirrors the backend, and is never the authority

`frontend/src/validation.ts` duplicates §11.1's rules so an impossible request is
caught before it is sent. The backend validates all of it again, because a client
that skips the file is slower but no less correct. The constants are duplicated
deliberately rather than fetched: they are part of the request contract, and a
validation rule that arrived from the network could not be trusted to block
anything.

Errors render inline and block the Analyze button — no `alert()`, which the brief
rules out and which would be unusable on a phone anyway.

### Year bounds follow the last complete calendar year

The current year is excluded, because it is only partway through and a partial
year drags any average down and biases a trend downwards. A minimum ten-year span
is enforced, since a Mann-Kendall test over five points describes nothing.

### Data quality is shown, not buried

`RegionPanel` prints total days, days still missing, days interpolated, and names
the low-quality years. `DataTable` gained `heatwave_days` and `avg_intensity_c`
so the table carries the same numbers the cards do, and low-quality years are
marked in text ("Low quality") rather than by colour alone, so the meaning
survives a greyscale print and a screen reader. The same rule applies in the
chart, where those years render as hollow dots.

---

## Phase 6 — Robustness and polish

### Why the rate limiter is two budgets, not one

The brief asks for 60/min on cached reads and 6/min on requests that can trigger
a fetch. A single limiter decorator cannot express that, because whether a
request is cached is only known **after** the lookup, and the decorator runs
before it.

The resolution:

| Budget | Charged when | Limit |
|---|---|---|
| General | every analysis request, before any work | 60/min |
| Upstream | immediately before a call to Open-Meteo | 6/min |

The upstream budget is charged at the point of use rather than at the edge.
That is the whole point of the cache: charging a cache hit for a fetch it never
made would make the second visitor pay for the first visitor's cost, and 60
returning users a minute would exhaust a 6/min upstream budget between them.

Verified directly — 70 consecutive cache reads produced **zero** upstream calls.

### Moving-window, not fixed-window

Both budgets use a moving window. A fixed window lets a client spend its entire
allowance at 11:59:59 and again at 12:00:00, which is a burst of 2× in a moment.
The moving window spreads the allowance evenly across the interval.

### Two `limits` API traps

Both fail in confusing ways, and both are now commented at the call site.

**`WindowStats.count` is not a count.** `WindowStats` is a namedtuple, so
`.count` resolves to `tuple.count`, the built-in method. Reading it gave
`TypeError: '<' not supported between instances of
'builtin_function_or_method' and 'int'`. The real field is `remaining`.

**`item.amount` is a plain int, not a method** — verified rather than assumed
after the first version guessed wrong.

`limiter.test(item, key)` reports whether a hit would succeed *without*
recording one, which is what a pre-flight check needs. `hit()` records.

### `Retry-After` is not optional

A 429 without it gives a well-behaved client no idea how long to wait, so it
retries immediately, which is precisely what a rate limit is trying to prevent.
The value comes from the window's `reset_time`, floored at 1 second so a client
is never told to retry instantly.

### Why the CSV endpoint calls `_analyse`, not `get_analysis`

The first version called the endpoint function and checked whether it got back
a model or an error. That was wrong: once cache headers were added,
`get_analysis` returns a `Response`, so the check passed the **entire JSON body**
through as the "CSV" with `Content-Disposition: None`.

Fixed by splitting out `_analyse()`, which returns `AnalysisResponse | Response`
and is called by both endpoints. This is not just tidiness — it is what
guarantees the downloaded file and the on-screen table can never disagree,
which section 8.7 requires. Duplicating the logic would have made that a
latent inconsistency.

### CSV injection and why it is guarded

A cell beginning `=`, `+`, `-` or `@` is executed as a formula by Excel and
LibreOffice when the file is opened. Every value here comes from our own
pipeline rather than user text, so the risk is low today — but the failure mode
is code execution on an analyst's machine, and the guard is one line. `_csv_cell`
prefixes an apostrophe; a test pins it.

The `csv` module is used rather than string joins, so a value containing a
comma or quote cannot shift every column after it.

### The filename cannot contain user input

Built from the preset region name and validated years only, filtered to
alphanumerics, hyphens and spaces. Anything unexpected falls back to a
constant rather than being echoed into a header. A query string in a
`Content-Disposition` is a header-injection vector.

### The no-leak sweep

`backend/tests/test_no_leaks.py` forces every failure path and asserts the body contains
none of: `Traceback`, `File "`, `site-packages`, driver and framework names,
`mongodb+srv`, `cluster0`, or any exception class name.

Covered: 422, 429, 502, 404, a genuine 500 (an exception carrying a fake
credential string), a total cache outage, and a 422 on the CSV endpoint.

**Cross-test contamination is real here.** Both rate-limit budgets and both
caches are shared, so the first version of these tests failed through no fault
of the code under test: a test that exhausted the 6/min budget made the *next*
test receive a 429, and a warm `daily_temps` meant the 502 path never called
Open-Meteo at all. An autouse fixture now resets both budgets and clears both
collections before every test in the file.

This is the third time shared state has produced a misleading test result
(earlier: the cached truncated payload in Phase 4, and the 5-region test that
only spent 5 of 6 upstream charges). Worth remembering when a test fails for no
visible reason.

### Why health is `no-store`

Every other read endpoint sends `max-age=300`. Health does not, and must not: a
cached health check would report "ok" for a service that has since died, which
is the exact opposite of the endpoint's purpose.

### Test suite runtime

97 → 104 tests, roughly 5 minutes, nearly all of it Atlas round trips.

The burst tests were initially slow enough to look hung. Profiling ruled out the
obvious suspect: the 366-day threshold loop takes **0.05 s** for 11,315 days, so
it is not a candidate. The real cost is that every cold request writes a
`daily_temps` document containing ~11,000 dates plus 11,000 floats, and a burst
makes one write per request.

Tests that only assert on status codes now use `small_payload()` (3 years
instead of 31). The burst test dropped to 15 s.

**Standing caveat:** the suite is slow because it runs against a real Atlas,
which was a deliberate choice — a mocked Mongo would not exercise TTL indexes,
upsert semantics or real round-trip cost. If that trade stops being worth it,
`-m "not atlas"` with a marker on those tests is the obvious next step.

---

## Phase 7 — Location map (bonus)

`leaflet` + `react-leaflet` on OpenStreetMap tiles. Task 100's wording is
"click to set a point **or** draw a rectangle". Only the click path was built: a
click fixes the box and there is no resize handle. Drag-to-draw would be roughly
25 lines (`mousedown`/`mousemove`/`mouseup` in `useMapEvents`, then `clampBox`
on release) and is the obvious next step if it is wanted.

### The bug that made the map completely dead

Clicks did nothing. The four inputs stayed empty, and the cause was a circular
dependency I had written myself:

```jsx
{center && <ClickHandler onPick={onPick} />}
```

`ClickHandler` is what creates the **first** box. But it was gated on `center`,
which is `boxCenter(box)`, and `box` is null until a box exists:

```
inputs blank → box is null → center is null → ClickHandler not mounted
                ↑                                                  │
                └──── click lands on a map with no handler ─────────┘
```

The map could never bootstrap itself. `ClickHandler` is now mounted
unconditionally. The rectangle, marker and `Recentre` stay gated, because unlike
the click handler they genuinely have nothing to show before a box exists.

**Worth remembering:** gating a component on the state it produces is always
wrong, and it fails silently. Nothing errors; the control just does nothing.

### The zoom was wrong for a different reason

Two separate causes, which is why the symptom was confusing:

1. `zoom={center ? 8 : 2}` — the no-box case was zoom 2, the whole planet.
2. `MapContainer`'s `center` and `zoom` props are read **only on mount** in
   react-leaflet v5. The map lives inside `{mode === 'box' && …}`, so it
   mounts, unmounts and remounts whenever the location dropdown changes,
   discarding whatever zoom the user had scrolled to.

Now `DEFAULT_CENTER = [23.8, 90.4]`, `DEFAULT_ZOOM = 6` (South Asia, where
every preset region sits), `CHOSEN_ZOOM = 8` once a box exists. The remount
itself is still there — fixing it properly means lifting the map out of the
conditional, which was not worth doing without knowing whether it is noticed.

### Circle to rectangle

The first version drew a `Circle` with a rough degrees-to-metres constant. A
rectangle draws the actual bounds, so what appears on screen is literally what
gets sent. The marker sits at the centre point, which keeps the distinction
between "the area you selected" and "the single grid cell that was read"
visible rather than implied.

### The map is square on the map, not square in degrees

A click keeps a half-degree latitude span and widens longitude by
`0.5 / cos(latitude)`, because a degree of longitude covers progressively less
ground than a degree of latitude as you move away from the equator.

Two bounds apply. `cos` reaches zero at the poles, so the quotient is guarded.
And beyond roughly 60 degrees the correction would demand a span wider than the
2 degree limit, so it is **clamped at 1 degree per side**. A high-latitude box
is therefore square in degrees again, because it cannot afford to be anything
else. That is a documented limitation, and a test asserts the box is
*taller than wide* up there so the behaviour cannot change silently.

The centre is the click itself at every latitude, because both spans are applied
symmetrically. Widening for squareness never moves the queried point, and a test
checks that across eight latitudes.

**It is square to within 0.7%, not exactly.** A degree of longitude and a degree
of latitude are not quite the same length even at the equator — 111.32 km
against 110.574 km — and `cos` alone cannot remove that. The first version of the
test asserted ±0.05 km and failed; the tolerance is now 1%, which is honest about
what the formula delivers.

### Three more real bugs, all caught by the vitest suite

Installing vitest paid for itself immediately: 3 of the first 18 tests failed,
and every failure was a genuine defect in `clampBox`.

**Clamping squashed the span.** `min_lat: 89.6, max_lat: 90.6` came back 0.8
degrees tall instead of 1.0 — it clamped the bound rather than sliding the
window, so polar boxes came out narrower than the user asked for. Now the span
is preserved and only the position shifts. There is a test for the general case,
not just the 1-degree one.

**Non-finite input destroyed good data.** `clampBox({ min_lat: NaN, … })`
returned `[0, 0]` for the whole pair, discarding `max_lat` too. Each bound is
now repaired independently, so a bad value costs only itself.

**Longitude wrapping produced a 359-degree box.** At longitude −180 the first
implementation wrapped `min` past `max`, and the swap that restored the ordering
turned a 1-degree box into one spanning the entire globe. Rewritten to shift the
window back inside the range instead of wrapping.

### The 10,512-point sweep

`boxFromClick` is now covered by a permanent test walking the globe in 2.5-degree
steps and asserting every result passes `isWithinLimits`. This is the rule worth
protecting: **a click must never produce a request the backend rejects**. A single
bad corner of the world breaks the picker, and the failure only ever shows up on
screen.

Before vitest this was a throwaway node script that had to be re-derived by hand
each time — it found the antimeridian bug, but nothing stopped it coming back.

### Leaflet specifics worth recording

**`divIcon`, not the default pin.** Leaflet's default marker images live inside
the package and a bundler does not rewrite them, producing the classic
"marker-icon.png failed to load". A `divIcon` needs no asset and reuses the warm
accent from the theme.

**A fixed height on the container.** A Leaflet map inside an auto-height parent
collapses to zero before it can measure itself.

**Keyboard and screen readers.** Leaflet's container is a plain div with no role,
so it is invisible to assistive technology and unreachable by keyboard. The
container gets `tabIndex={0}` and `role="application"` with a descriptive label;
arrow keys then pan and plus/minus zoom natively.

**`z-0` on the container**, so wheel-zooming the map never scrolls the page.

### Dependency cost

Bundle went from 630 kB to **786 kB** (232 kB gzipped) — almost entirely Leaflet.
Worth knowing if it ever needs trimming; a dynamic import on the map splits it
out without touching any code above it.

`vitest` was added as a dev dependency. This is outside the brief's §2 list, and
worth flagging: the project had no frontend test runner at all, so the box maths
had no regression protection until now.