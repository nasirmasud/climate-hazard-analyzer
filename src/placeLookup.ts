/**
 * A place-name lookup that can never make the page wait.
 *
 * The four coordinate inputs must fill the instant a map is clicked. The name is
 * a nicety, so it is fetched afterwards and applied only if it is still the most
 * recent request: a slow lookup that lands after a newer click must not
 * overwrite the newer label.
 *
 * Nothing here rejects. A failed or slow lookup leaves the caller holding the
 * fallback label, which is a normal outcome rather than an error.
 */

import { getPlaceName } from './api'

const TIMEOUT_MS = 4000

export interface PlaceLookup {
  /** A short name, or null when none was found or the lookup failed. */
  promise: Promise<string | null>
  /** Abandons the request. Safe to call more than once. */
  cancel: () => void
}

/**
 * Start a reverse-geocoding lookup.
 *
 * `signal` cancellation is what stops an abandoned response from being applied.
 * A client-side timeout is layered on top because `AbortSignal.timeout` is not
 * available everywhere this bundle has to run.
 */
export function lookupPlace(lat: number, lon: number): PlaceLookup {
  const controller = new AbortController()

  let timer: ReturnType<typeof setTimeout> | undefined
  if (typeof setTimeout === 'function') {
    timer = setTimeout(() => controller.abort(), TIMEOUT_MS)
  }

  const promise = getPlaceName(lat, lon, controller.signal)
    .then((name) => {
      // An aborted request resolves to null, which is the fallback. That is the
      // intended behaviour, not a swallowed error.
      if (controller.signal.aborted) return null
      return name ?? null
    })
    .catch(() => null)
    .finally(() => {
      if (timer !== undefined) clearTimeout(timer)
    })

  return {
    promise,
    cancel: () => {
      if (timer !== undefined) clearTimeout(timer)
      controller.abort()
    },
  }
}
