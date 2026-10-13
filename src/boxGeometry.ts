/**
 * Box maths for the location picker, kept out of the component file for two
 * reasons.
 *
 * The functions here are pure and worth testing directly. `boxFromClick` in
 * particular encodes a rule that must always hold — a click can never produce
 * a box the backend will reject — and that rule is much easier to check here
 * than through a click handler.
 *
 * It also keeps `RegionMap.tsx` exporting only a component, which is what
 * Fast Refresh needs to preserve state while editing.
 */

import type { BoxBounds } from './validation'
import { MAX_BOX_SPAN_DEG, round2 } from './validation'

/**
 * Half-height of the box a map click produces, in degrees of latitude.
 *
 * The box is deliberately a little taller than a quarter of a degree so that,
 * once the longitude span is widened to the 4:3 target, the rectangle reads as a
 * landscape area rather than a square or a sliver.
 */
export const CLICK_BOX_HALF_DEG = 0.375

/**
 * Target width-to-height ratio of the selected box, measured on the map.
 *
 * Slightly landscape rather than square, which matches how a region reads
 * better on a map and stops the selection looking like a plain click target.
 */
const TARGET_ASPECT = 4 / 3

/**
 * The largest half-span allowed on either axis.
 *
 * The brief caps a box at 2 degrees per side, so 2 / 2 = 1 degree either side of
 * the centre. This is the ceiling the longitude span is clamped to.
 */
const MAX_HALF_SPAN_DEG = MAX_BOX_SPAN_DEG / 2

const MAX_LAT = 90
const MAX_LON = 180

/**
 * Precision for box bounds, in decimal places.
 *
 * **Three, not two.** The latitude half-span is 0.375, so bounds sit on a third
 * decimal. Rounding them to two moved each bound by up to 0.005 in the
 * *opposite* direction, which silently shifted the centre: a click at 23.81
 * produced a box centred on 23.815.
 *
 * Cache sharing is unaffected, because the cache key is built from the *centre*
 * rounded to two decimals on both sides of the wire, not from the bounds.
 */
const BOUND_PRECISION = 3

/** Round to a fixed number of decimals. */
function fixed(value: number, places: number): number {
  const factor = 10 ** places
  return Math.round(value * factor) / factor
}

/**
 * Clamp a proposed box into the range the backend accepts.
 *
 * Both axes are handled the same way: if the box would run past an edge, it is
 * shifted back inside rather than wrapped.
 *
 * Longitude is the case that forces this. A box at longitude -180 wants
 * min -180.5 and max -179.5. Wrapping the first value to +179.5 and then
 * swapping to restore the ordering produces a box spanning 359 degrees, which
 * is the whole globe rather than one degree of it. Shifting instead keeps the
 * span intact and always yields a box the request model can express.
 *
 * Note that this makes an area straddling the antimeridian impossible to
 * select. The backend's `min_lon < max_lon` rule has no way to represent a
 * wrapped box, so such an area cannot be requested at all, and pretending
 * otherwise would produce a silently wrong centre point.
 */
export function clampBox(box: BoxBounds): BoxBounds {
  const clampPair = (low: number, high: number, max: number): [number, number] => {
    // Repair each bound independently. Returning a fixed pair when either value
    // was unusable would discard the good one along with the bad.
    const repaired = (value: number, fallback: number) =>
      Number.isFinite(value) ? value : fallback

    // A click never produces reversed bounds, but normalising first makes this
    // function total: whatever it is handed, it returns something ordered.
    const a = repaired(low, -CLICK_BOX_HALF_DEG)
    const b = repaired(high, CLICK_BOX_HALF_DEG)
    const [start, end] = a <= b ? [a, b] : [b, a]

    // The span is the quantity that must survive clamping. Slide the window
    // back inside the allowed range and leave it otherwise untouched, so a box
    // that overran a pole stays exactly as wide as the user asked for.
    const span = end - start

    let lo = start
    let hi = end
    if (hi > max) {
      lo = max - span
      hi = max
    }
    if (lo < -max) {
      lo = -max
      hi = -max + span
    }

    return [lo, hi]
  }

  const [minLat, maxLat] = clampPair(box.min_lat, box.max_lat, MAX_LAT)
  const [minLon, maxLon] = clampPair(box.min_lon, box.max_lon, MAX_LON)

  // Both bounds of a pair are rounded to the same precision, which keeps the
  // pair symmetric about its midpoint. Rounding to two would not: the 0.375
  // half-span puts bounds on a third decimal, and rounding a low bound up while
  // rounding a high bound down shifts the centre by up to 0.005.
  return {
    min_lat: fixed(minLat, BOUND_PRECISION),
    max_lat: fixed(maxLat, BOUND_PRECISION),
    min_lon: fixed(minLon, BOUND_PRECISION),
    max_lon: fixed(maxLon, BOUND_PRECISION),
  }
}

/**
 * The half-span in degrees of longitude that gives the box its 4:3 shape on the
 * map.
 *
 * Two corrections are needed to get there.
 *
 * **Aspect ratio.** The target is 4 units wide for every 3 tall, so the raw
 * half-width is the half-height times 4/3.
 *
 * **Latitude convergence.** A degree of longitude covers progressively less
 * ground than a degree of latitude as you move away from the equator, by a
 * factor of cos(latitude). Dividing by cos widens the longitude span until the
 * two sides cover the same distance, which is what makes the rectangle look
 * 4:3 on the map instead of only in the coordinate values.
 *
 * Two things bound the result. cos reaches zero at the poles, so the quotient is
 * guarded. And beyond roughly 60 degrees the correction would demand a span
 * wider than the 2 degree limit, so it is clamped there — a high-latitude box
 * is narrower relative to its height because it cannot afford to be anything
 * else.
 */
export function longitudeHalfSpanFor(lat: number): number {
  const cosLat = Math.cos((lat * Math.PI) / 180)

  // Near the poles cos is effectively zero and the quotient is meaningless.
  if (!Number.isFinite(cosLat) || Math.abs(cosLat) < 1e-6) {
    return MAX_HALF_SPAN_DEG
  }

  const desired = (CLICK_BOX_HALF_DEG * TARGET_ASPECT) / Math.abs(cosLat)

  return Math.min(desired, MAX_HALF_SPAN_DEG)
}

/**
 * The box a click at (lat, lon) produces.
 *
 * Latitude keeps a fixed half-span; longitude is widened to reach the 4:3 target
 * shape on the map. Both spans are applied symmetrically about the click, so
 * **the centre point is the click itself** and is unaffected by the widening.
 *
 * The click is snapped to two decimals *first*, then the bounds are built around
 * it. Snapping afterwards would move the centre, because rounding a low bound up
 * and a high bound down shifts the midpoint between them.
 */
export function boxFromClick(lat: number, lon: number): BoxBounds {
  // Snap the centre before deriving anything from it.
  const centreLat = round2(lat)
  const centreLon = round2(lon)

  const latHalf = CLICK_BOX_HALF_DEG
  const lonHalf = longitudeHalfSpanFor(centreLat)

  return clampBox({
    min_lat: centreLat - latHalf,
    max_lat: centreLat + latHalf,
    min_lon: centreLon - lonHalf,
    max_lon: centreLon + lonHalf,
  })
}

/** The centre of a box, or null when there is not one to show yet. */
export function boxCenter(box: BoxBounds | null): [number, number] | null {
  if (!box) return null
  return [(box.min_lat + box.max_lat) / 2, (box.min_lon + box.max_lon) / 2]
}

/** True when a box would pass `validateBox`. Used by the tests, not the UI. */
export function isWithinLimits(box: BoxBounds): boolean {
  return (
    box.min_lat >= -MAX_LAT &&
    box.max_lat <= MAX_LAT &&
    Math.abs(box.max_lat - box.min_lat) <= MAX_BOX_SPAN_DEG &&
    Math.abs(box.max_lon - box.min_lon) <= MAX_BOX_SPAN_DEG &&
    box.min_lat < box.max_lat &&
    box.min_lon < box.max_lon
  )
}
