/**
 * The only place the app talks to the network. Uses native fetch so there is
 * no HTTP library to learn about in the interview walkthrough.
 */

import type {
  AnalysisParams,
  AnalysisResponse,
  ApiErrorBody,
  PresetRegion,
} from './types'

/**
 * Where the backend lives. Read from `VITE_API_URL` so the same bundle can point
 * at a local backend in development and at the Render service in production
 * without a code change. Exported so an error message can name the URL that
 * actually failed, which is the first thing worth checking when the API is
 * unreachable.
 */
export const API_BASE_URL = (import.meta.env.VITE_API_URL ?? 'http://localhost:8000').replace(
  /\/+$/,
  '',
)

/** Thrown for any non-2xx response, carrying the backend's own message so the
 *  UI can show it verbatim instead of inventing its own wording. */
export class ApiError extends Error {
  status: number
  code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

/** Per-request options. Every field is optional, so a plain `request(path)`
 *  behaves exactly as it did before the timeout was added. */
interface RequestOptions {
  /** Cancels the request. Used to abandon a stale lookup. */
  signal?: AbortSignal
  /** Abort the request after this many milliseconds. */
  timeoutMs?: number
  /** Passed straight to fetch. `no-store` keeps a keep-alive ping honest. */
  cache?: RequestCache
}

/** The caller's cancellation and the timeout, whichever fires first. Either may
 *  be absent, and `undefined` means "no cancellation", which is what `fetch`
 *  expects. */
function combineSignals(signal: AbortSignal | undefined, timeout: AbortSignal | undefined) {
  if (signal === undefined) return timeout
  if (timeout === undefined) return signal
  return AbortSignal.any([signal, timeout])
}

/**
 * Perform one request.
 *
 * `timeoutMs` exists for the health check specifically. A sleeping free-tier
 * instance accepts the connection and then says nothing for the ~30 seconds it
 * needs to boot, so an unbounded fetch waits forever and the page looks broken
 * rather than busy. Other endpoints are deliberately left unbounded: a first
 * analysis for an unseen location fetches decades of daily history upstream and
 * a timeout there would abort legitimate work.
 *
 * The timeout is a manual `AbortController` rather than `AbortSignal.timeout`,
 * because `placeLookup.ts` already found that not every environment this bundle
 * has to run in provides it.
 */
async function request<T>(
  path: string,
  { signal, timeoutMs, cache }: RequestOptions = {},
): Promise<T> {
  const controller = timeoutMs === undefined ? null : new AbortController()
  let timer: ReturnType<typeof setTimeout> | undefined

  if (controller !== null) {
    timer = setTimeout(() => controller.abort(), timeoutMs)
  }

  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      headers: { Accept: 'application/json' },
      // Whichever signal fires first wins, so a timeout cannot be lost by the
      // caller also passing one.
      signal: combineSignals(signal, controller?.signal),
      cache,
    })

    if (!response.ok) {
      // The backend always returns the same error shape, so this is safe to
      // assume. Fall back to the status text if something upstream returns HTML.
      let code = 'http_error'
      let message = `Request failed (${response.status}).`
      try {
        const body = (await response.json()) as ApiErrorBody
        code = body.error.code
        message = body.error.message
      } catch {
        // Keep the generic message.
      }
      throw new ApiError(response.status, code, message)
    }

    return (await response.json()) as T
  } catch (error) {
    // A timeout is reported as a plain Error, never as the `AbortError` that
    // `fetch` throws. React Query reads an `AbortError` as "this query was
    // cancelled" and skips the retry counter, which would quietly turn a
    // twenty-attempt loop into a single attempt.
    if (controller !== null && controller.signal.aborted) {
      throw new Error(`No response within ${Math.round(timeoutMs! / 1000)} seconds.`, {
        cause: error,
      })
    }
    throw error
  } finally {
    if (timer !== undefined) clearTimeout(timer)
  }
}

/**
 * Build the analysis query string.
 *
 * Only numbers that have already passed validation are serialised, so a URL
 * can never be built from raw user text.
 */
export function buildAnalysisQuery(params: AnalysisParams): string {
  const query = new URLSearchParams()

  if (params.region) {
    query.set('region', params.region)
  }
  // Box and point share lat/lon keys, matching the backend.
  if (params.min_lat !== undefined && params.max_lat !== undefined) {
    query.set('min_lat', String(params.min_lat))
    query.set('max_lat', String(params.max_lat))
    query.set('min_lon', String(params.min_lon))
    query.set('max_lon', String(params.max_lon))
  } else if (params.lat !== undefined && params.lon !== undefined) {
    query.set('lat', String(params.lat))
    query.set('lon', String(params.lon))
  }

  query.set('start_year', String(params.start_year))
  query.set('end_year', String(params.end_year))
  query.set('hazard', params.hazard ?? 'heatwave')

  return query.toString()
}

/**
 * Liveness check.
 *
 * `timeoutMs` is a required argument rather than a default so the fifteen
 * second budget lives with the rest of the wake-up policy in `useBackendHealth`
 * instead of being duplicated here. The request is `no-store` so the ten-minute
 * keep-alive ping can never be answered from the browser cache and report a
 * backend that has since gone away.
 */
export function getHealth(timeoutMs: number) {
  return request<{ status: string }>('/api/health', { timeoutMs, cache: 'no-store' })
}

export function getRegions() {
  return request<PresetRegion[]>('/api/regions')
}

/**
 * A short name for a coordinate, or null.
 *
 * Reverse geocoding is a nicety, so this is allowed to fail: the caller treats
 * a null as "use the plain label". `signal` lets a stale lookup be abandoned
 * when the user has already clicked somewhere else.
 */
export function getPlaceName(lat: number, lon: number, signal?: AbortSignal) {
  const query = new URLSearchParams({ lat: String(lat), lon: String(lon) })

  return request<{ name: string | null }>(`/api/place-name?${query}`, { signal }).then(
    (body) => body.name ?? null,
  )
}

export function getAnalysis(params: AnalysisParams) {
  return request<AnalysisResponse>(`/api/analysis?${buildAnalysisQuery(params)}`)
}

/** URL for the CSV button. The browser downloads this directly, so it must be
 *  a plain URL rather than a fetch that would need blob handling. */
export function getExportCsvUrl(params: AnalysisParams): string {
  return `${API_BASE_URL}/api/analysis/export.csv?${buildAnalysisQuery(params)}`
}
