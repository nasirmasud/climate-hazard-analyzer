/**
 * Viewport limits for the location map.
 *
 * Three Leaflet options have to agree, or fully zooming out shows one of two
 * defects. This module keeps the reasoning in one place so they cannot drift:
 *
 * - `maxBounds` (a Map option) keeps the view over the latitudes worth showing
 * - `noWrap` (a GridLayer option, so it goes on `TileLayer` rather than the map)
 *   stops the world repeating when panning east or west
 * - `minZoom` is the level at which the window exactly covers the band
 *
 * With all three, zooming out fills the window with map. With any one missing,
 * you get grey bands above and below (past the Mercator limit, where no tiles
 * exist at all) or two copies of the world side by side.
 *
 * The band is deliberately not the whole world. It runs from 58S to 72N: wide
 * enough for every preset and anywhere a heatwave is worth analysing, and narrow
 * enough that at minimum zoom the window is filled rather than mostly empty. It
 * also keeps the poles out, because Mercator tiles stop being meaningful well
 * before them — at 72N the scale factor is already past 3x.
 *
 * Kept out of the component for the same reason as `boxGeometry.ts`: the rule
 * that must always hold is checkable here rather than through a map interaction.
 */

import type { LatLngBoundsLiteral } from 'leaflet'

/** The latitude and longitude the view is allowed to cover. */
export const VIEWPORT_BAND = {
  south: -58,
  west: -180,
  north: 72,
  east: 180,
} as const

/**
 * As the `maxBounds` prop and as the argument to `getBoundsZoom`.
 *
 * Leaflet wants a `LatLngBoundsExpression`; a two-corner literal is the simplest
 * way to write one that survives TypeScript without a helper.
 */
export const VIEWPORT_BOUNDS: LatLngBoundsLiteral = [
  [VIEWPORT_BAND.south, VIEWPORT_BAND.west],
  [VIEWPORT_BAND.north, VIEWPORT_BAND.east],
]

/**
 * Used only when Leaflet cannot produce a usable zoom, which happens if the map
 * has not measured its container yet. Low rather than high on purpose: a
 * too-low minZoom shows grey bands, a too-high one merely starts zoomed in.
 */
export const FALLBACK_MIN_ZOOM = 1

/**
 * How far minZoom has to move before it is written back.
 *
 * Writing it unconditionally would be harmless in itself, but a written value
 * that Leaflet then rounds back up produces a resize, which recomputes, which
 * writes again. A tolerance below one zoom level breaks that loop by ignoring
 * differences no zoom level can express.
 */
const ZOOM_EPSILON = 0.01

/** The slice of Leaflet's Map this module actually depends on. */
export interface ViewportMap {
  getContainer(): HTMLElement
  invalidateSize(): void
  getMinZoom(): number
  setMinZoom(zoom: number): void
  getBoundsZoom(bounds: LatLngBoundsLiteral, inside: boolean): number
}

/** True when a coordinate lies inside the band, edges included. */
export function isWithinBand(lat: number, lon: number): boolean {
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return false
  return (
    lat >= VIEWPORT_BAND.south &&
    lat <= VIEWPORT_BAND.north &&
    lon >= VIEWPORT_BAND.west &&
    lon <= VIEWPORT_BAND.east
  )
}

/**
 * The lowest zoom at which the window still fits inside the band.
 *
 * **`inside: false` is the argument that matters.** Leaflet returns the zoom at
 * which the given bounds fit *within* the view when true, and at which the view
 * fits *within* the given bounds when false. We want the second: if the view
 * fitted inside the band there would be room to spare outside it, which is
 * exactly the grey band this is meant to prevent.
 *
 * Leaflet also rounds this result upward, so the answer is safe to use directly
 * rather than needing a correction of our own.
 */
export function minZoomForViewport(map: ViewportMap): number {
  const zoom = map.getBoundsZoom(VIEWPORT_BOUNDS, false)
  return Number.isFinite(zoom) ? zoom : FALLBACK_MIN_ZOOM
}

/**
 * Keeps the band applied to a live map, and returns a cleanup.
 *
 * Applied once immediately, because the map has already measured its container
 * by the time this runs but nothing has set a minimum yet. Then applied again
 * on every resize, because a 2:1 map changes height when its width changes —
 * the minimum zoom that fills the window is a function of the window.
 *
 * `subscribe` is passed in rather than imported so this stays testable without a
 * DOM: the caller decides how to observe size changes, and can be a
 * `ResizeObserver` in the browser and a plain callback in a test.
 */
export function enforceViewportBand(
  map: ViewportMap,
  subscribe: (handler: () => void) => () => void,
): () => void {
  const apply = () => {
    const next = minZoomForViewport(map)
    if (Math.abs(next - map.getMinZoom()) > ZOOM_EPSILON) {
      map.setMinZoom(next)
    }
  }

  apply()

  return subscribe(apply)
}