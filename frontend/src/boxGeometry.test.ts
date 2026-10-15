import { describe, expect, it } from 'vitest'

import {
  boxCenter,
  boxFromClick,
  clampBox,
  CLICK_BOX_HALF_DEG,
  isWithinLimits,
  longitudeHalfSpanFor,
} from './boxGeometry'
import { MAX_BOX_SPAN_DEG, round2 } from './validation'

/** Ground distance covered by a span, in kilometres. */
function northSouthKm(spanDeg: number): number {
  return spanDeg * 110.574
}
function eastWestKm(spanDeg: number, centreLat: number): number {
  return spanDeg * 111.32 * Math.cos((centreLat * Math.PI) / 180)
}

/** Target width-to-height of a click box, on the map. */
const TARGET_ASPECT = 4 / 3

function fixed(value: number, places: number): number {
  const factor = 10 ** places
  return Math.round(value * factor) / factor
}

describe('longitudeHalfSpanFor', () => {
  it('is the 4:3 half-height at the equator', () => {
    expect(longitudeHalfSpanFor(0)).toBeCloseTo(CLICK_BOX_HALF_DEG * TARGET_ASPECT, 9)
  })

  it('widens with latitude', () => {
    expect(longitudeHalfSpanFor(30)).toBeGreaterThan(longitudeHalfSpanFor(10))
    expect(longitudeHalfSpanFor(60)).toBeGreaterThan(longitudeHalfSpanFor(30))
  })

  it('follows 0.375 * (4/3) / cos(latitude) below the clamp', () => {
    for (const lat of [10, 25, 45, 59]) {
      const expected = (0.375 * (4 / 3)) / Math.cos((lat * Math.PI) / 180)
      expect(longitudeHalfSpanFor(lat)).toBeCloseTo(expected, 9)
    }
  })

  it('clamps where the correction would break the 2 degree limit', () => {
    // 0.5 / cos(60) is exactly 1.0; past that the span would exceed the limit.
    expect(longitudeHalfSpanFor(60)).toBeCloseTo(1.0, 9)
    expect(longitudeHalfSpanFor(75)).toBe(1)
    expect(longitudeHalfSpanFor(89)).toBe(1)
  })

  it('never divides by zero at the poles', () => {
    expect(Number.isFinite(longitudeHalfSpanFor(90))).toBe(true)
    expect(Number.isFinite(longitudeHalfSpanFor(-90))).toBe(true)
    expect(longitudeHalfSpanFor(90)).toBe(1)
  })

  it('is symmetric about the equator', () => {
    expect(longitudeHalfSpanFor(35)).toBeCloseTo(longitudeHalfSpanFor(-35), 12)
  })
})

describe('boxFromClick', () => {
  it('centres the box on the click', () => {
    const center = boxCenter(boxFromClick(23.81, 90.41))
    expect(center![0]).toBeCloseTo(23.81, 9)
    expect(center![1]).toBeCloseTo(90.41, 9)
  })

  // The point of the correction: the click is the centre whatever happens to
  // the span, so widening for squareness cannot move the queried point.
  it('keeps the centre on the click at every latitude', () => {
    for (const lat of [0, 15, 30, 45, 60, 72, 85, -45]) {
      const center = boxCenter(boxFromClick(lat, 12.5))!
      expect(center[0]).toBeCloseTo(lat, 9)
      expect(center[1]).toBeCloseTo(12.5, 9)
    }
  })

  it('reaches the 4:3 shape on the map below the clamp', () => {
    for (const lat of [0, 20, 40, 55]) {
      const box = boxFromClick(lat, 10)
      const ns = northSouthKm(box.max_lat - box.min_lat)
      const ew = eastWestKm(box.max_lon - box.min_lon, lat)

      // To within 1%. The correction is cos(latitude) alone, but a degree of
      // longitude and latitude differ in length even at the equator, leaving a
      // 0.7% residual that cos cannot remove.
      expect(ew / ns).toBeCloseTo(TARGET_ASPECT, 1)
      expect(Math.abs(ew / ns - TARGET_ASPECT) / TARGET_ASPECT).toBeLessThan(0.01)
    }
  })

  it('is wider than tall everywhere below the clamp', () => {
    for (const lat of [0, 15, 30, 45, 55]) {
      const box = boxFromClick(lat, 10)
      const ns = northSouthKm(box.max_lat - box.min_lat)
      const ew = eastWestKm(box.max_lon - box.min_lon, lat)
      expect(ew).toBeGreaterThan(ns)
    }
  })

  // Past ~60 degrees the correction is clamped by the 2 degree limit, so the
  // shape degrades towards square. Pinned so it cannot change quietly.
  it('cannot stay 4:3 above the clamp, and says so', () => {
    // Past ~68 degrees even a full 2 degree longitude span is not wide enough
    // to stay wider than the fixed 0.75 degree height.
    const tall = boxFromClick(78, 10)
    const nsTall = northSouthKm(tall.max_lat - tall.min_lat)
    const ewTall = eastWestKm(tall.max_lon - tall.min_lon, 78)
    expect(ewTall).toBeLessThan(nsTall)

    // Between the clamp and that point it is still landscape, just not 4:3.
    const wide = boxFromClick(65, 10)
    const nsWide = northSouthKm(wide.max_lat - wide.min_lat)
    const ewWide = eastWestKm(wide.max_lon - wide.min_lon, 65)
    expect(ewWide).toBeGreaterThan(nsWide)
    expect(ewWide / nsWide).toBeLessThan(TARGET_ASPECT)
  })

  it('keeps the latitude span fixed at 0.75 degrees at every latitude', () => {
    for (const lat of [0, 30, 60, 80, -45]) {
      const box = boxFromClick(lat, 10)
      expect(box.max_lat - box.min_lat).toBeCloseTo(2 * CLICK_BOX_HALF_DEG, 9)
    }
  })

  it('keeps bounds to three decimals so the centre survives rounding', () => {
    // The centre is the value that gets cached and queried, so the bounds may
    // carry a third decimal as long as the midpoint stays put.
    const box = boxFromClick(23.814567, 90.412345)
    for (const value of Object.values(box)) {
      expect(value).toBe(fixed(value, 3))
    }
    // The click snaps to two decimals; the bounds carry the half-span around it.
    const center = boxCenter(box)!
    expect(center[0]).toBeCloseTo(round2(23.814567), 9)
    expect(center[1]).toBeCloseTo(round2(90.412345), 9)
  })

  // Regression guard for a native-validation bug: the four inputs once carried
  // step="0.01" while a click produced three decimals, so the browser rejected
  // every click. The rule worth keeping is that bounds carry at most three
  // decimals, because that is the precision the centre calculation relies on;
  // raising it again would silently move the centre.
  it('never produces a bound with more precision than BOUND_PRECISION', () => {
    for (let lat = -90; lat <= 90; lat += 7.5) {
      for (let lon = -180; lon < 180; lon += 7.5) {
        for (const value of Object.values(boxFromClick(lat, lon))) {
          expect(value).toBe(fixed(value, 3))
        }
      }
    }
  })

  it('produces values that a two-decimal input would reject, which is why step is any', () => {
    // Documents the shape that triggered the step mismatch.
    const box = boxFromClick(22.78, 90.41)
    const hasThreeDecimals = Object.values(box).some((value) => value !== round2(value))
    expect(hasThreeDecimals).toBe(true)
  })

  // The rule this file exists to protect: a click must never produce a request
  // the backend rejects. A single bad corner of the world is enough to break
  // the location picker, and the failure only shows up on screen.
  it('never produces a box the backend would reject, anywhere on the globe', () => {
    const rejected: string[] = []

    for (let lat = -90; lat <= 90; lat += 2.5) {
      for (let lon = -180; lon < 180; lon += 2.5) {
        const box = boxFromClick(lat, lon)
        if (!isWithinLimits(box)) {
          rejected.push(`${lat},${lon} -> ${JSON.stringify(box)}`)
        }
      }
    }

    expect(rejected).toEqual([])
  })

  it('stays inside the 2 degree limit even where the correction wants more', () => {
    for (const lat of [61, 70, 80, 89]) {
      const box = boxFromClick(lat, 10)
      expect(box.max_lat - box.min_lat).toBeLessThanOrEqual(MAX_BOX_SPAN_DEG)
      expect(box.max_lon - box.min_lon).toBeLessThanOrEqual(MAX_BOX_SPAN_DEG)
    }
  })

  it('keeps a valid, narrow box across the antimeridian', () => {
    // A one degree box must not become a 359 degree one by wrapping past -180.
    // At the pole the span is the clamped maximum rather than the equatorial one.
    const box = boxFromClick(-90, -180)
    expect(box.max_lon - box.min_lon).toBeLessThanOrEqual(MAX_BOX_SPAN_DEG)
    expect(isWithinLimits(box)).toBe(true)
  })
})

describe('clampBox', () => {
  it('leaves an ordinary box untouched', () => {
    const original = { min_lat: 23.4, max_lat: 24.2, min_lon: 89.9, max_lon: 90.9 }
    expect(clampBox(original)).toEqual(original)
  })

  it('preserves the span when a box overruns the north pole', () => {
    // Shifting, not clamping the bound: whatever span went in comes back out.
    const box = clampBox({ min_lat: 89.6, max_lat: 90.6, min_lon: 10, max_lon: 11 })
    expect(box.max_lat).toBe(90)
    expect(box.max_lat - box.min_lat).toBeCloseTo(1, 9)
    expect(isWithinLimits(box)).toBe(true)
  })

  it('handles the south pole the same way', () => {
    const box = clampBox({ min_lat: -90.6, max_lat: -89.6, min_lon: 10, max_lon: 11 })
    expect(box.min_lat).toBe(-90)
    expect(box.max_lat - box.min_lat).toBeCloseTo(1, 9)
    expect(isWithinLimits(box)).toBe(true)
  })

  it('preserves an arbitrary span rather than forcing it to a round number', () => {
    const box = clampBox({ min_lat: 89.2, max_lat: 90.6, min_lon: 10, max_lon: 11 })
    expect(box.max_lat - box.min_lat).toBeCloseTo(1.4, 9)
  })

  it('never wraps longitude across the globe', () => {
    const box = clampBox({ min_lat: 0, max_lat: 1, min_lon: 179.7, max_lon: 180.3 })
    expect(box.max_lon - box.min_lon).toBeCloseTo(0.6, 6)
    expect(box.min_lon).toBeGreaterThanOrEqual(-180)
    expect(box.max_lon).toBeLessThanOrEqual(180)
  })

  it('normalises reversed bounds so it is total', () => {
    const box = clampBox({ min_lat: 1, max_lat: 0, min_lon: 10, max_lon: 11 })
    expect(box.min_lat).toBeLessThan(box.max_lat)
  })

  it('returns a usable box for non-finite input rather than NaN', () => {
    const box = clampBox({ min_lat: NaN, max_lat: 1, min_lon: 10, max_lon: 11 })
    expect(Number.isFinite(box.min_lat)).toBe(true)
    expect(box.min_lat).toBeLessThan(box.max_lat)
  })
})

describe('isWithinLimits', () => {
  it('accepts a box exactly at the limit', () => {
    expect(isWithinLimits({ min_lat: 23, max_lat: 25, min_lon: 89, max_lon: 91 })).toBe(
      true,
    )
  })

  it('rejects a box wider than the limit', () => {
    expect(isWithinLimits({ min_lat: 23, max_lat: 26, min_lon: 89, max_lon: 91 })).toBe(
      false,
    )
  })

  it('rejects a box past either pole', () => {
    expect(isWithinLimits({ min_lat: 88, max_lat: 91, min_lon: 10, max_lon: 11 })).toBe(
      false,
    )
  })

  it('rejects a box with inverted latitude bounds', () => {
    expect(isWithinLimits({ min_lat: 25, max_lat: 23, min_lon: 89, max_lon: 91 })).toBe(
      false,
    )
  })

  it('agrees with the backend constant', () => {
    expect(MAX_BOX_SPAN_DEG).toBe(2)
  })
})

describe('boxCenter', () => {
  it('averages both axes, matching the backend rule', () => {
    const center = boxCenter({
      min_lat: 23.4,
      max_lat: 24.2,
      min_lon: 89.9,
      max_lon: 90.9,
    })
    expect(center![0]).toBeCloseTo(23.8, 9)
    expect(center![1]).toBeCloseTo(90.4, 9)
  })

  it('returns null when there is no box yet', () => {
    expect(boxCenter(null)).toBeNull()
  })
})
