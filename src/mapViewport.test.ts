import { describe, expect, it, vi } from 'vitest'

import {
  FALLBACK_MIN_ZOOM,
  VIEWPORT_BAND,
  VIEWPORT_BOUNDS,
  enforceViewportBand,
  isWithinBand,
  minZoomForViewport,
} from './mapViewport'
import type { ViewportMap } from './mapViewport'

/**
 * The five presets from `backend/app/regions.py`, copied rather than imported.
 *
 * Duplicated deliberately: the frontend cannot import from the backend repo, and
 * this test is checking that the band the *frontend* chose can contain what the
 * *backend* will be asked for. Importing one from the other would make the test
 * tautological.
 */
const PRESETS = [
  { name: 'Dhaka', lat: 23.81, lon: 90.41 },
  { name: 'Chattogram', lat: 22.36, lon: 91.78 },
  { name: 'New Delhi', lat: 28.61, lon: 77.21 },
  { name: 'Mumbai', lat: 19.08, lon: 72.88 },
  { name: 'Karachi', lat: 24.86, lon: 67.01 },
]

/** A map that records what it was asked to do. */
function fakeMap(options: { boundsZoom?: number; minZoom?: number } = {}) {
  const boundsZoom = options.boundsZoom ?? 2
  const state = { minZoom: options.minZoom ?? 0 }

  const map = {
    getContainer: () => ({}) as HTMLElement,
    invalidateSize: vi.fn(),
    getMinZoom: vi.fn(() => state.minZoom),
    setMinZoom: vi.fn((zoom: number) => {
      state.minZoom = zoom
    }),
    getBoundsZoom: vi.fn(() => boundsZoom),
  }

  return { map: map as unknown as ViewportMap, raw: map, state }
}

/** Captures the handler so a test can fire resizes. */
function fakeSubscribe() {
  let handler: (() => void) | null = null
  const unsubscribe = vi.fn(() => {
    handler = null
  })

  const subscribe = (fn: () => void) => {
    handler = fn
    return unsubscribe
  }

  return {
    subscribe,
    unsubscribe,
    resize: () => {
      if (!handler) throw new Error('nothing subscribed')
      handler()
    },
    subscribed: () => handler !== null,
  }
}

describe('VIEWPORT_BAND', () => {
  it('runs from 58S to 72N, the band the map is limited to', () => {
    expect(VIEWPORT_BAND.south).toBe(-58)
    expect(VIEWPORT_BAND.north).toBe(72)
  })

  it('spans the full longitude, so noWrap is what stops the repeat', () => {
    expect(VIEWPORT_BAND.west).toBe(-180)
    expect(VIEWPORT_BAND.east).toBe(180)
  })

  it('stays inside the Mercator limit, which is where grey bands come from', () => {
    // Past roughly 85 degrees there are no tiles at any zoom. The band has to
    // stop short of that or the fill guarantee cannot hold.
    expect(VIEWPORT_BAND.south).toBeGreaterThan(-85)
    expect(VIEWPORT_BAND.north).toBeLessThan(85)
  })

  it('exposes the band as a Leaflet bounds literal in south-west / north-east order', () => {
    expect(VIEWPORT_BOUNDS).toEqual([
      [-58, -180],
      [72, 180],
    ])
  })
})

describe('isWithinBand', () => {
  it.each(PRESETS)('contains the $name preset', ({ lat, lon }) => {
    expect(isWithinBand(lat, lon)).toBe(true)
  })

  it('includes the edges themselves', () => {
    expect(isWithinBand(-58, -180)).toBe(true)
    expect(isWithinBand(72, 180)).toBe(true)
  })

  it('rejects latitudes outside the band', () => {
    expect(isWithinBand(-58.01, 0)).toBe(false)
    expect(isWithinBand(72.01, 0)).toBe(false)
    expect(isWithinBand(89, 0)).toBe(false)
    expect(isWithinBand(-85, 0)).toBe(false)
  })

  it('rejects non-finite coordinates rather than letting them through', () => {
    // An undefined latitude must not read as inside, because the fallback is
    // "the coordinate is fine".
    expect(isWithinBand(Number.NaN, 0)).toBe(false)
    expect(isWithinBand(0, Number.POSITIVE_INFINITY)).toBe(false)
  })
})

describe('minZoomForViewport', () => {
  it('asks for the view to fit inside the band, not the band inside the view', () => {
    const { map, raw } = fakeMap({ boundsZoom: 2 })

    minZoomForViewport(map)

    // inside=false is the whole trick. With inside=true Leaflet returns the zoom
    // at which the band fits within the view, which leaves room outside the band
    // and therefore grey.
    expect(raw.getBoundsZoom).toHaveBeenCalledWith(VIEWPORT_BOUNDS, false)
  })

  it("returns Leaflet's own value, having already been rounded up", () => {
    const { map } = fakeMap({ boundsZoom: 1.66 })

    expect(minZoomForViewport(map)).toBe(1.66)
  })

  it('falls back rather than propagating a non-finite zoom', () => {
    const { map } = fakeMap({ boundsZoom: Number.NaN })

    expect(minZoomForViewport(map)).toBe(FALLBACK_MIN_ZOOM)
  })
})

describe('enforceViewportBand', () => {
  it('applies immediately, before any resize happens', () => {
    const { map, raw } = fakeMap({ boundsZoom: 2, minZoom: 0 })
    const observer = fakeSubscribe()

    enforceViewportBand(map, observer.subscribe)

    expect(raw.setMinZoom).toHaveBeenCalledWith(2)
  })

  it('recomputes on every resize, because a 2:1 map changes height with width', () => {
    const { map, raw } = fakeMap({ boundsZoom: 2, minZoom: 0 })
    const observer = fakeSubscribe()

    enforceViewportBand(map, observer.subscribe)
    raw.getBoundsZoom.mockReturnValue(3)
    observer.resize()

    expect(raw.setMinZoom).toHaveBeenLastCalledWith(3)
  })

  it('does not write minZoom back when it has not meaningfully changed', () => {
    // Leaflet rounds what it hands back, so a recompute can differ by a float
    // that no zoom level expresses. Writing that back would re-trigger the
    // resize that caused the recompute.
    const { map, raw } = fakeMap({ boundsZoom: 2, minZoom: 2 })
    const observer = fakeSubscribe()

    enforceViewportBand(map, observer.subscribe)
    observer.resize()

    expect(raw.setMinZoom).not.toHaveBeenCalled()
  })

  it('ignores a difference too small to be a zoom level', () => {
    const { map, raw } = fakeMap({ boundsZoom: 2.005, minZoom: 2 })
    const observer = fakeSubscribe()

    enforceViewportBand(map, observer.subscribe)

    expect(raw.setMinZoom).not.toHaveBeenCalled()
  })

  it('still writes when the difference is a whole zoom level', () => {
    const { map, raw } = fakeMap({ boundsZoom: 3, minZoom: 2 })
    const observer = fakeSubscribe()

    enforceViewportBand(map, observer.subscribe)

    expect(raw.setMinZoom).toHaveBeenCalledWith(3)
  })

  it('recomputes after a widening resize, which is the case that matters', () => {
    // Wider at a fixed 2:1 means taller, so more of the band fits and the
    // minimum zoom drops.
    const { map, raw, state } = fakeMap({ boundsZoom: 3, minZoom: 0 })
    const observer = fakeSubscribe()

    enforceViewportBand(map, observer.subscribe)
    expect(state.minZoom).toBe(3)

    raw.getBoundsZoom.mockReturnValue(2)
    observer.resize()

    expect(state.minZoom).toBe(2)
  })

  it('returns an unsubscribe and stops applying after it is called', () => {
    const { map, raw } = fakeMap({ boundsZoom: 2, minZoom: 0 })
    const observer = fakeSubscribe()

    const cleanup = enforceViewportBand(map, observer.subscribe)
    raw.setMinZoom.mockClear()

    cleanup()
    expect(observer.unsubscribe).toHaveBeenCalled()
    expect(observer.subscribed()).toBe(false)
  })
})