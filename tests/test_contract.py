"""Contract check: the response models and the frontend types must not drift.

The frontend lives in TypeScript, so this reads `frontend/src/types.ts` as text
and compares field names against the Pydantic models. It is the one test that
knows about both sides, which is exactly why it exists.

Backend and frontend are separate git repositories, so this file is NOT part of
the backend repo. Running the backend tests on its own will skip these checks
with a clear reason rather than failing on a missing path.

Run from `backend/`: pytest tests/test_contract.py
"""

import re
from pathlib import Path

import pytest

from app import schemas

# Sibling directory in the local monorepo, one level above `backend/`.
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
TYPES_TS = FRONTEND_DIR / "src" / "types.ts"

# Response models that must line up with an interface of the same name.
PAIRED_MODELS = {
    "RegionOut": "RegionOut",
    "ParamsOut": "ParamsOut",
    "ThresholdOut": "ThresholdOut",
    "SummaryOut": "SummaryOut",
    "YearlyOut": "YearlyOut",
    "EventOut": "EventOut",
    "TrendOut": "TrendOut",
    "PeriodStat": "PeriodStat",
    "PeriodComparisonOut": "PeriodComparisonOut",
    "DataQualityOut": "DataQualityOut",
    "AnalysisResponse": "AnalysisResponse",
    "PresetRegion": "PresetRegion",
    "ApiErrorBody": None,  # ApiErrorBody in TS has no Pydantic twin by that name
}


def parse_ts_interface(name: str, source: str) -> set[str]:
    """Return the property names declared inside a TypeScript interface."""
    match = re.search(rf"export interface {name}\s*\{{(.*?)\n\}}", source, re.S)
    if match is None:
        return set()
    body = match.group(1)
    # Strip block comments so commented-out fields do not count.
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    body = re.sub(r"//.*", "", body)
    return set(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*[?:]", body, re.M))


def parse_ts_type_alias(name: str, source: str) -> set[str]:
    """Return literal union members of a TypeScript string-literal alias."""
    match = re.search(rf"export type {name}\s*=\s*(.*)", source)
    if match is None:
        return set()
    return set(re.findall(r"'([^']+)'", match.group(1)))


# Skip cleanly when the sibling frontend checkout is not present, which is the
# normal case for the standalone backend repo and in CI.
needs_frontend = pytest.mark.skipif(
    not TYPES_TS.exists(),
    reason=f"frontend/src/types.ts not found at {TYPES_TS} (separate repo layout)",
)


@needs_frontend
def test_response_models_match_typescript() -> None:
    source = TYPES_TS.read_text(encoding="utf-8")
    missing: list[str] = []

    for model_name, ts_name in PAIRED_MODELS.items():
        if ts_name is None:
            continue
        model = getattr(schemas, model_name)
        py_fields = set(model.model_fields.keys())
        ts_fields = parse_ts_interface(ts_name, source)

        assert ts_fields, f"No TypeScript interface found for {model_name}"

        if py_fields != ts_fields:
            only_py = py_fields - ts_fields
            only_ts = ts_fields - py_fields
            if only_py:
                missing.append(f"{model_name}: missing in types.ts -> {sorted(only_py)}")
            if only_ts:
                missing.append(f"{model_name}: extra in types.ts -> {sorted(only_ts)}")

    assert not missing, "Contract drift:\n" + "\n".join(missing)


@needs_frontend
def test_trend_direction_labels_match() -> None:
    source = TYPES_TS.read_text(encoding="utf-8")
    py_labels = set(schemas.TrendOut.model_fields["direction"].annotation.__args__)
    ts_labels = parse_ts_type_alias("TrendDirection", source)

    assert py_labels == ts_labels, (
        f"Trend labels differ. backend={sorted(py_labels)} frontend={sorted(ts_labels)}"
    )


def test_yearly_has_both_aggregate_columns() -> None:
    """Both averages are required per the brief and must stay on every row."""
    fields = set(schemas.YearlyOut.model_fields.keys())
    assert "avg_intensity_c" in fields
    assert "avg_max_temp_during_events_c" in fields


def test_error_shape_is_consistent() -> None:
    """The UI reads error.error.code and error.error.message."""
    assert set(schemas.ErrorResponse.model_fields.keys()) == {"error"}
    assert set(schemas.ErrorBody.model_fields.keys()) == {"code", "message"}