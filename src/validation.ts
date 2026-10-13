/**
 * Client-side validation, mirroring the backend's rules in section 11.1.
 *
 * These are a convenience that saves a round trip, never the authority. The
 * backend validates every one of these again, so a client that skips this file
 * is slower but no less correct.
 *
 * The constants are deliberately duplicated from the backend rather than
 * fetched: they are part of the request contract, not results, and a validation
 * rule that arrives from the network cannot be trusted to block a request.
 */

export const MIN_YEAR_SPAN = 10
export const EARLIEST_YEAR = 1990
export const MAX_BOX_SPAN_DEG = 2.0

/**
 * The four bounds of a custom area.
 *
 * A named type rather than four loose parameters, because the same value is
 * passed between the map, the inputs and the submit handler, and a type makes
 * it impossible to pass the latitude bounds where the longitude bounds belong.
 */
export interface BoxBounds {
  min_lat: number
  max_lat: number
  min_lon: number
  max_lon: number
}

/**
 * The most recent fully finished calendar year.
 *
 * The current year is excluded because it is only partway through, and a
 * partial year would drag any average down and bias a trend downwards.
 */
export function lastCompleteYear(): number {
  return new Date().getFullYear() - 1
}

/** Returns a message describing what is wrong, or null when the range is fine. */
export function validateRange(
  startYear: number,
  endYear: number,
  lastYear: number,
): string | null {
  if (!Number.isFinite(startYear) || !Number.isFinite(endYear)) {
    return 'Enter a whole number for both years.'
  }
  if (startYear < EARLIEST_YEAR) {
    return `Start year must be ${EARLIEST_YEAR} or later.`
  }
  if (endYear > lastYear) {
    return `End year must be ${lastYear} or earlier, because ${lastYear + 1} is not a complete year yet.`
  }
  if (endYear < startYear) {
    return 'End year must be the same as or later than the start year.'
  }
  if (endYear - startYear + 1 < MIN_YEAR_SPAN) {
    return `Select at least ${MIN_YEAR_SPAN} years so a trend is meaningful.`
  }
  return null
}

/** Reject a box wider or taller than the brief allows. */
export function validateBox(box: BoxBounds): string | null {
  const { min_lat: minLat, max_lat: maxLat, min_lon: minLon, max_lon: maxLon } = box

  if (![minLat, maxLat, minLon, maxLon].every(Number.isFinite)) {
    return 'Enter all four bounds of the area.'
  }
  if (minLat >= maxLat || minLon >= maxLon) {
    return 'Minimum values must be smaller than maximum values.'
  }
  if (Math.abs(maxLat - minLat) > MAX_BOX_SPAN_DEG) {
    return `Area is too tall: the north-south span must be ${MAX_BOX_SPAN_DEG} degrees or less.`
  }
  if (Math.abs(maxLon - minLon) > MAX_BOX_SPAN_DEG) {
    return `Area is too wide: the east-west span must be ${MAX_BOX_SPAN_DEG} degrees or less.`
  }
  return null
}

/** Round to two decimals so near-identical requests share one cache entry. */
export function round2(value: number): number {
  return Math.round(value * 100) / 100
}
