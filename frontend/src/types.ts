/**
 * Mirrors backend/app/schemas.py field for field, including nullability. If a
 * field changes there, it changes here.
 *
 * Nullable fields are `| null` because the backend genuinely returns null: a
 * year with no heatwave events has no average duration or intensity, and
 * showing 0 there would read as a real measurement.
 */

export type HazardName = 'heatwave'

export type TrendDirection = 'increasing' | 'decreasing' | 'no_clear_trend'

/** One of the preset locations offered in the region dropdown. */
export interface PresetRegion {
  id: string
  name: string
  lat: number
  lon: number
}

/** Parameters the user submitted. The query runs from here, not per key. */
export interface AnalysisParams {
  region?: string
  lat?: number
  lon?: number
  min_lat?: number
  max_lat?: number
  min_lon?: number
  max_lon?: number
  start_year: number
  end_year: number
  hazard?: HazardName
}

export interface RegionOut {
  name: string
  lat: number
  lon: number
  /** Which point was queried: 'preset', 'point', or 'center of box'. */
  source_point: string
}

export interface ParamsOut {
  start_year: number
  end_year: number
  hazard: HazardName
}

export interface ThresholdOut {
  percentile: number
  window_days: number
  min_run_days: number
  baseline_start: number
  baseline_end: number
  unit: string
}

export interface SummaryOut {
  total_events: number
  avg_duration_days: number | null
  avg_intensity_c: number | null
  avg_max_temp_during_events_c: number | null
  events_per_year: number
}

export interface YearlyOut {
  year: number
  events: number
  heatwave_days: number
  avg_duration_days: number | null
  avg_intensity_c: number | null
  avg_max_temp_during_events_c: number | null
  low_quality: boolean
}

export interface EventOut {
  start: string
  end: string
  duration_days: number
  mean_intensity_c: number
  peak_temp_c: number
}

export interface TrendOut {
  direction: TrendDirection
  significant: boolean
  mann_kendall_p: number
  sen_slope_per_year: number
  regression_slope_per_year: number
  /** Fitted values the chart draws as the dashed trend line. */
  fitted: number[]
}

export interface PeriodStat {
  label: string
  events_per_year: number
  avg_duration_days: number | null
}

export interface PeriodComparisonOut {
  early: PeriodStat
  late: PeriodStat
}

export interface DataQualityOut {
  total_days: number
  missing_days: number
  interpolated_days: number
  low_quality_years: number[]
}

export interface AnalysisResponse {
  region: RegionOut
  params: ParamsOut
  threshold: ThresholdOut
  summary: SummaryOut
  yearly: YearlyOut[]
  events: EventOut[]
  trend: TrendOut
  period_comparison: PeriodComparisonOut
  data_quality: DataQualityOut
  insights: string[]
  limitations: string[]
  /** False on the request that computed and stored the result, true after. */
  cached: boolean
}

/** Every failure from the backend uses this one shape. */
export interface ApiErrorBody {
  error: {
    code: string
    message: string
  }
}
