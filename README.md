# Climate Hazard Trend Analyzer — Frontend

React 19 + TypeScript + Vite dashboard for the heatwave trend analysis API.

## Requirements

- Node 20.19+ or 22.12+ (Node 22.19 was used in development)

## Setup

```bash
npm install
cp .env.example .env
```

`.env` points the app at the backend:

```
VITE_API_URL=http://localhost:8000
```

The backend repository is separate: <https://github.com/nasirmasud/climate-trend-backend>

## Scripts

| Command | Purpose |
|---|---|
| `npm run dev` | Dev server on <http://localhost:5173> |
| `npm run build` | Type-check with `tsc` then build to `dist/` |
| `npm run lint` | ESLint |
| `npm run format` | Prettier |

## How this connects to the backend

`src/types.ts` is a hand-maintained mirror of the backend's Pydantic response
models in `app/schemas.py`. The two must stay in step.

A backend test (`tests/test_contract.py`) reads this file and compares field
names against the Pydantic models, so drift fails the test suite rather than
surfacing as a runtime bug. Run those checks from the backend repo while the
`frontend/` directory sits alongside it.

Nullable fields are genuinely nullable: a year with no heatwave events has no
average duration, and the UI shows a dash rather than a misleading `0`.

## Current status

Phase 1 of the build plan. The page shell and the typed API client are in
place; analysis panels render placeholders until `/api/analysis` ships.