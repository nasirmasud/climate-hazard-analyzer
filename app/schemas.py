"""Request and response models.

This module is the contract. The frontend's `types.ts` mirrors it field for
field, so a change here must be made there too. Every response field below
comes straight from the brief's response shape; nothing is invented.

All input validation lives here rather than in the routes, so an invalid
request is rejected before any work (or any call to Open-Meteo) happens.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .regions import get_region

# Mirrors heatwave.BASELINE_START. Phase 2 imports the real constant from
# heatwave.py so there is only ever one definition of the baseline start.
EARLIEST_YEAR = 1990

# The brief caps a custom box at 2 degrees on each side.
MAX_BOX_SPAN_DEG = 2.0

# A trend needs enough years to mean anything.
MIN_YEAR_SPAN = 10

HazardName = Literal["heatwave"]


def last_complete_year() -> int:
    """The most recent calendar year that has fully finished.

    The current year is excluded because it would only be partway through the
    data, which would bias a trend upwards.
    """
    return date.today().year - 1


# --------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------


class AnalysisRequest(BaseModel):
    """Parameters for GET /api/analysis.

    Exactly one location must be given: a preset `region` id, a single
    `lat`/`lon` point, or a `min_lat`/`max_lat`/`min_lon`/`max_lon` box.
    """

    region: str | None = None

    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)

    min_lat: float | None = Field(default=None, ge=-90, le=90)
    max_lat: float | None = Field(default=None, ge=-90, le=90)
    min_lon: float | None = Field(default=None, ge=-180, le=180)
    max_lon: float | None = Field(default=None, ge=-180, le=180)

    start_year: int = Field(default=EARLIEST_YEAR, ge=EARLIEST_YEAR)
    end_year: int | None = None

    # A Literal makes any other hazard fail validation, which the API turns
    # into a 422 without any extra code.
    hazard: HazardName = "heatwave"

    @model_validator(mode="after")
    def check_location(self) -> "AnalysisRequest":
        has_point = self.lat is not None and self.lon is not None
        has_half_point = (self.lat is not None) != (self.lon is not None)
        box_fields = [self.min_lat, self.max_lat, self.min_lon, self.max_lon]
        has_some_box = any(f is not None for f in box_fields)
        has_full_box = all(f is not None for f in box_fields)

        given = [bool(self.region), has_point, has_some_box]
        if sum(given) != 1:
            raise ValueError(
                "Provide exactly one location: region=<id>, lat and lon, "
                "or all four of min_lat, max_lat, min_lon, max_lon."
            )

        if has_half_point:
            raise ValueError("lat and lon must be given together.")

        # Reject an unknown preset id here rather than deep in a route, so the
        # caller gets a 422 with a clear message.
        if self.region and get_region(self.region) is None:
            raise ValueError(f"Unknown region '{self.region}'.")

        if has_some_box:
            if not has_full_box:
                raise ValueError(
                    "A box needs all four of min_lat, max_lat, min_lon, max_lon."
                )
            if self.min_lat >= self.max_lat:
                raise ValueError("min_lat must be less than max_lat.")
            if self.min_lon >= self.max_lon:
                raise ValueError("min_lon must be less than max_lon.")
            # Reject oversized boxes so a "region" stays a region.
            if self.max_lat - self.min_lat > MAX_BOX_SPAN_DEG:
                raise ValueError(
                    f"Box is too tall: max_lat - min_lat must be "
                    f"{MAX_BOX_SPAN_DEG} degrees or less."
                )
            if self.max_lon - self.min_lon > MAX_BOX_SPAN_DEG:
                raise ValueError(
                    f"Box is too wide: max_lon - min_lon must be "
                    f"{MAX_BOX_SPAN_DEG} degrees or less."
                )

        return self

    @model_validator(mode="after")
    def check_years(self) -> "AnalysisRequest":
        if self.end_year is None:
            self.end_year = last_complete_year()

        if self.end_year > last_complete_year():
            raise ValueError(
                f"end_year must be {last_complete_year()} or earlier, "
                f"because {last_complete_year() + 1} is not a complete year yet."
            )
        if self.end_year < self.start_year:
            raise ValueError("end_year must be greater than or equal to start_year.")
        if self.end_year - self.start_year + 1 < MIN_YEAR_SPAN:
            raise ValueError(
                f"Select at least {MIN_YEAR_SPAN} years so a trend is meaningful."
            )
        return self

    @property
    def uses_box(self) -> bool:
        return self.min_lat is not None

    @property
    def center_lat(self) -> float | None:
        """ERA5 is a grid, so a box is queried at its center point only."""
        if self.min_lat is not None:
            return round((self.min_lat + self.max_lat) / 2, 2)
        return self.lat

    @property
    def center_lon(self) -> float | None:
        if self.min_lon is not None:
            return round((self.min_lon + self.max_lon) / 2, 2)
        return self.lon

    def resolve_location(self) -> tuple[str, float, float, str]:
        """Work out what to actually query and how to describe it in the UI.

        Returns (name, lat, lon, source_point). Coordinates are rounded to two
        decimals here, before any cache key is built, so that near-identical
        requests share one stored result.
        """
        if self.region:
            preset = get_region(self.region)
            # Unreachable in practice: the validator already rejected unknown
            # ids. Kept explicit so this function is safe to call on its own.
            if preset is None:
                raise ValueError(f"Unknown region '{self.region}'.")
            return preset.name, round(preset.lat, 2), round(preset.lon, 2), "preset"

        if self.uses_box:
            name = "Custom box"
            return (
                name,
                round((self.min_lat + self.max_lat) / 2, 2),
                round((self.min_lon + self.max_lon) / 2, 2),
                "center of box",
            )

        return "Custom point", round(self.lat, 2), round(self.lon, 2), "point"


# --------------------------------------------------------------------------
# Responses
# --------------------------------------------------------------------------


class RegionOut(BaseModel):
    name: str
    lat: float
    lon: float
    # Plain-language note on which point was actually queried.
    source_point: str


class PresetRegion(BaseModel):
    id: str
    name: str
    lat: float
    lon: float


class HealthOut(BaseModel):
    status: str


class PlaceNameOut(BaseModel):
    """
    A short name for a coordinate, or null when none could be found.

    Null is a normal, expected outcome: open water, unpopulated land, a lookup
    that timed out, or a rate limit. The frontend treats it as "use the plain
    label", so it is never an error.
    """

    name: str | None = None


class ParamsOut(BaseModel):
    start_year: int
    end_year: int
    hazard: HazardName


class ThresholdOut(BaseModel):
    percentile: int
    window_days: int
    min_run_days: int
    baseline_start: int
    baseline_end: int
    unit: str = "C"


class SummaryOut(BaseModel):
    total_events: int
    # Nullable because a range with no events has no meaningful average.
    avg_duration_days: float | None = None
    avg_intensity_c: float | None = None
    avg_max_temp_during_events_c: float | None = None
    events_per_year: float


class YearlyOut(BaseModel):
    year: int
    events: int
    heatwave_days: int
    avg_duration_days: float | None = None
    avg_intensity_c: float | None = None
    # Required by the brief's yearly aggregates, kept alongside avg_intensity_c
    # so the table can show both without a second request.
    avg_max_temp_during_events_c: float | None = None
    low_quality: bool = False


class EventOut(BaseModel):
    start: str
    end: str
    duration_days: int
    mean_intensity_c: float
    peak_temp_c: float


class TrendOut(BaseModel):
    direction: Literal["increasing", "decreasing", "no_clear_trend"]
    significant: bool
    mann_kendall_p: float
    sen_slope_per_year: float
    regression_slope_per_year: float
    # Fitted values the chart draws as the dashed trend line.
    fitted: list[float]


class PeriodStat(BaseModel):
    label: str
    events_per_year: float
    avg_duration_days: float | None = None


class PeriodComparisonOut(BaseModel):
    early: PeriodStat
    late: PeriodStat


class DataQualityOut(BaseModel):
    total_days: int
    missing_days: int
    interpolated_days: int
    low_quality_years: list[int]


class AnalysisResponse(BaseModel):
    region: RegionOut
    params: ParamsOut
    threshold: ThresholdOut
    summary: SummaryOut
    yearly: list[YearlyOut]
    events: list[EventOut]
    trend: TrendOut
    period_comparison: PeriodComparisonOut
    data_quality: DataQualityOut
    insights: list[str]
    limitations: list[str]
    # False on the request that computed and stored the analysis, True after.
    cached: bool


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody
