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

const BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

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

async function request<T>(path: string): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    headers: { Accept: 'application/json' },
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

export function getHealth() {
  return request<{ status: string }>('/api/health')
}

export function getRegions() {
  return request<PresetRegion[]>('/api/regions')
}

export function getAnalysis(params: AnalysisParams) {
  return request<AnalysisResponse>(`/api/analysis?${buildAnalysisQuery(params)}`)
}

/** URL for the CSV button. The browser downloads this directly, so it must be
 *  a plain URL rather than a fetch that would need blob handling. */
export function getExportCsvUrl(params: AnalysisParams): string {
  return `${BASE_URL}/api/analysis/export.csv?${buildAnalysisQuery(params)}`
}
