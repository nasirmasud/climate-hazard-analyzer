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

Copy `.env.example` to `.env` and fill in `MONGODB_URI`:

```
MONGODB_URI=mongodb+srv://user:password@cluster.mongodb.net
MONGODB_DB=climate
FRONTEND_ORIGIN=http://localhost:5173
LOGIC_VERSION=v1
PORT=8000
```

`.env` is git-ignored and must never be committed.

Atlas needs Network Access set to allow your host, which on the free tier means
allowing all IPs.

## Run

```bash
uvicorn app.main:app --reload --port 8000
```

- API docs: <http://localhost:8000/docs>
- Health: <http://localhost:8000/api/health>

The frontend repository is separate: <https://github.com/nasirmasud/climate-trend>

## Tests

```bash
pytest
```

Two of these check the response contract against `frontend/src/types.ts`. They
skip with an explanatory message when the sibling `frontend/` directory is not
present, which is the normal case for this repository on its own.

## Modules

| File | Responsibility |
|---|---|
| `main.py` | App setup, CORS, error handling |
| `config.py` | Settings from environment variables |
| `routes.py` | API endpoints |
| `schemas.py` | Pydantic request and response models — the contract |
| `regions.py` | Preset regions |
| `ingest.py` | Open-Meteo fetch, cleaning, quality flags (Phase 2) |
| `heatwave.py` | Thresholds and event detection (Phase 2) |
| `trends.py` | Mann-Kendall and regression (Phase 4) |
| `insights.py` | Insight sentences from numbers (Phase 4) |
| `db.py` | MongoDB connection and queries (Phase 3) |

## Current status

Phase 1 of the build plan. Health and regions endpoints, plus the frozen
response contract. Analysis pipeline lands in Phase 2.