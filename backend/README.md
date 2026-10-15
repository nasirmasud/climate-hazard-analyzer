# Heatwave Trend Analyzer — Backend

FastAPI service that fetches daily maximum temperature from Open-Meteo (ERA5),
detects heatwaves, analyses trends, and serves the results.

## Requirements

- Python 3.11+ (developed on 3.14.7)

## Setup

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt      # Windows
.venv\Scripts\activate
```

Create a `.env` file in this directory containing these five variables. There is
no template to copy from, deliberately: a tracked template is where a real
connection string ends up pasted by accident, which is exactly what happened
here once already. The names are listed here instead, with no values.

```
MONGODB_URI
MONGODB_DB
FRONTEND_ORIGIN
LOGIC_VERSION
PORT
```

`MONGODB_URI` is the only secret. The other four have safe defaults:
`MONGODB_DB=climate`, `FRONTEND_ORIGIN=http://localhost:5173`,
`LOGIC_VERSION=v1`, `PORT=8000`.

`.env` is git-ignored and must never be committed.

Atlas needs Network Access set to allow your host, which on the free tier means
allowing all IPs.

## Run

```bash
uvicorn app.main:app --reload --port 8000
```

- API docs: <http://localhost:8000/docs>
- Health: <http://localhost:8000/api/health>

The frontend is the sibling `frontend/` directory in this repository.

## Tests

```bash
pytest
```

Two of these check the response contract against `frontend/src/types.ts`. They
skip with an explanatory message when the sibling `frontend/` directory is not
present, which is the case when `backend/` is checked out on its own.

## Modules

| File | Responsibility |
|---|---|
| `main.py` | App setup, CORS, error handling |
| `config.py` | Settings from environment variables |
| `routes.py` | API endpoints |
| `schemas.py` | Pydantic request and response models — the contract |
| `regions.py` | Preset regions |
| `analysis.py` | Pipeline order: fetch, detect, trend, narrate (Phase 2) |
| `ingest.py` | Open-Meteo fetch, cleaning, quality flags (Phase 2) |
| `heatwave.py` | Thresholds and event detection (Phase 2) |
| `trends.py` | Mann-Kendall and regression (Phase 4) |
| `insights.py` | Insight sentences from numbers (Phase 4) |
| `db.py` | MongoDB connection and queries (Phase 3) |
| `ratelimit.py` | Two rate-limit budgets: general and upstream (Phase 6) |
| `places.py` | Reverse geocoding for the map's place names (Phase 7) |

## Current status

Build Phases 1 to 7 are complete and verified: the analysis pipeline, the
MongoDB cache, the trend quality guards, rate limiting, and the location map.
The suite is 136 tests, run against a real Atlas instance.

Nothing is deployed yet. What remains is Phase 8 (deployment) and Phase 9 (the
`§18.3` README deliverable and the final checks).